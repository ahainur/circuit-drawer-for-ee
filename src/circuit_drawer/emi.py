"""Two-line conducted-noise estimates from explicitly drawn circuits.

The LISNs, chassis path and filter are ordinary components, never injected
into a user's deck. FFT line amplitudes are not CISPR receiver readings.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from copy import deepcopy
import csv
import io
import json
import math
from pathlib import Path
import tempfile

from .layout import Layout
from .spice import _keys, number, export_spice
from .waveforms import Data, parse_csv

# Periodic Hann, tone-amplitude correction; not a 9 kHz IF/detector model.
MAX_FREQUENCY = 30e6
MAX_FFT = 262144
MIN_FFT = 256
SAMPLES_PER_CYCLE = 10  # margin beyond Nyquist; still requires a step-size comparison
EPSILON_0 = 8.8541878128e-12
REFERENCES = {"none": "不叠加限值", "cispr32_b_mains": "CISPR 32 B级交流电源端口（仅参考）"}
SOURCES = {"manual": "手动", "parallel_plate": "平行板估算", "measured": "实测", "q3d": "Q3D 导出值"}
CHANNELS = {"positive": "正线", "negative": "负线", "cm": "共模 CM", "dm": "差模 DM"}
INPUTS = {
    "positive": ("正线 LISN 电压通道", "lisn_p"),
    "negative": ("负线 LISN 电压通道", "lisn_n"),
    "start": ("分析开始 (s)", "400u"), "stop": ("分析结束 (s)", "1m"),
    "f_min": ("最低频率 (Hz)", "150k"), "f_max": ("最高频率 (Hz，≤30 MHz)", "30Meg"),
    "reference": ("参考曲线", "none"),
}
FILTER_PARTS = {"choke": ("共模电感（耦合绕组）", "transformer"),
                "dm_positive": ("正线差模电感", "L"), "dm_negative": ("负线差模电感", "L"),
                "x": ("跨线 X 电容", "C"), "y_positive": ("正线 Y 电容", "C"),
                "y_negative": ("负线 Y 电容", "C")}
FILTER_INPUTS = {"x_capacitance": ("选用 X 电容 (F)", "1u"),
                 "y_capacitance": ("每只 Y 电容 (F)", "2.2n"),
                 "coupling": ("共模电感耦合系数 k", "0.995"),
                 "margin_db": ("设计裕量 (dB)", "6")}
PARASITIC_FIELDS = ("element", "net", "source", "area_mm2", "gap_mm", "epsilon_r")
NOTE = ("工程估算：周期 Hann 窗、单边正弦 RMS 幅值，去直流；CM=(正线+负线)/2，"
        "DM=(正线−负线)/2，均为每线贡献。不是准峰值或平均值接收机，也不是合规结论。"
        "CISPR 32 曲线仅作交流电源端口参考，不据此判定直流端口合格。"
        "结果依赖开关边沿、器件寄生、布线和机壳回路；请用更小步长及实测复核。")


def field_reference():
    return ("emi: version=1; " + "; ".join(f"{k}: {v[0]}" for k, v in INPUTS.items())
            + "; reference=" + "|".join(REFERENCES)
            + "; parasitics[]: " + ", ".join(PARASITIC_FIELDS)
            + "; source=" + "|".join(SOURCES) + "; filter: "
            + ", ".join((*FILTER_PARTS, *FILTER_INPUTS))
            + "\nExample shape (existing probes required): "
            + json.dumps({"emi": {"version": 1, **{key: value[1] for key, value in INPUTS.items()}}}, ensure_ascii=False))


def settings(lay: Layout, nets=None) -> dict:
    raw = lay.extra.get("emi")
    _keys(raw, ("version", *INPUTS), ("parasitics", "filter"), "emi")
    if type(raw["version"]) is not int or raw["version"] != 1:
        raise ValueError("emi.version 必须为 1")
    cfg = deepcopy(raw)
    for key in ("start", "stop", "f_min", "f_max"):
        cfg[key] = number(cfg[key], f"emi.{key}")
    if not 0 <= cfg["start"] < cfg["stop"]:
        raise ValueError("emi 时间窗要求 0 ≤ start < stop")
    if not 150e3 <= cfg["f_min"] < cfg["f_max"] <= MAX_FREQUENCY:
        raise ValueError("emi 频带要求 150 kHz ≤ f_min < f_max ≤ 30 MHz")
    if not isinstance(cfg["reference"], str) or cfg["reference"] not in REFERENCES:
        raise ValueError("emi.reference 可用值：" + " / ".join(REFERENCES))
    sim = lay.extra.get("simulation")
    if not isinstance(sim, dict):
        raise ValueError("EMI 需要已有的 simulation 仿真参数")
    probes = sim.get("probes")
    if not isinstance(probes, list) or any(not isinstance(p, dict) for p in probes):
        raise ValueError("simulation.probes 必须是测量通道对象列表")
    voltage = {p["name"]: p for p in probes if p.get("kind") == "voltage" and isinstance(p.get("name"), str)}
    for key in ("positive", "negative"):
        if not isinstance(cfg[key], str) or cfg[key] not in voltage:
            raise ValueError(f"emi.{key} 请选择已配置的电压通道：" + " / ".join(voltage))
    if cfg["positive"] == cfg["negative"]:
        raise ValueError("emi 正负线必须是两个不同的 LISN 电压通道")
    nets = nets if nets is not None else lay.netlist
    ground = sim.get("ground")
    if not isinstance(ground, str) or ground not in nets:
        raise ValueError("EMI 仿真参考地需为图中实际机壳引脚")
    for key in ("positive", "negative"):
        p = voltage[cfg[key]]
        if any(not isinstance(p.get(end), str) or p[end] not in nets for end in ("positive", "negative")):
            raise ValueError(f"emi.{key} 电压通道需填写真实的正负端引脚")
        if nets[p["negative"]] != nets[ground]:
            raise ValueError(f"emi.{key} 的负端必须接同一仿真参考地（机壳），不能各自接电源回线")
    measurement_nets = [nets.get(voltage[cfg[key]].get("positive")) for key in ("positive", "negative")]
    if None in measurement_nets or len(set(measurement_nets + [nets[ground]])) != 3:
        raise ValueError("正负 LISN 测量端需为两个独立非地节点")
    components = sim.get("components")
    if not isinstance(components, dict):
        raise ValueError("EMI 需要已有的 simulation.components 参数表")
    for net in measurement_nets:
        terminations = [e for e in lay.elements if e.kind == "R" and
                        {nets.get(e.id + ".a"), nets.get(e.id + ".b")} == {net, nets[ground]}]
        if not any(isinstance(components.get(e.id), dict) and
                   math.isclose(number(components[e.id].get("value"), f"{e.id}.value"), 50, rel_tol=1e-9)
                   for e in terminations):
            raise ValueError("每个 LISN 测量端必须明确画出并设置对机壳的 50 Ω 接收电阻")
    ids = {e.id: e for e in lay.elements}
    parasitics = cfg.get("parasitics", [])
    if not isinstance(parasitics, list):
        raise ValueError("emi.parasitics 必须为列表")
    used = set()
    for i, item in enumerate(parasitics):
        _keys(item, PARASITIC_FIELDS[:3], PARASITIC_FIELDS[3:], f"emi.parasitics[{i}]")
        eid = item["element"]
        if not isinstance(eid, str) or eid not in ids or ids[eid].kind != "C" or eid in used:
            raise ValueError(f"emi.parasitics[{i}].element 需为不重复的已画电容 ID")
        used.add(eid)
        if not isinstance(item["source"], str) or item["source"] not in SOURCES:
            raise ValueError(f"{eid}.source 可用值：" + " / ".join(SOURCES))
        if not isinstance(item["net"], str) or item["net"] not in nets:
            raise ValueError(f"{eid}.net 需填写所关联导线上的实际引脚，例如 S1.s")
        if nets.get(eid + ".a") != nets[item["net"]] or nets.get(eid + ".b") != nets.get(ground):
            raise ValueError(f"{eid} 需明确画在关联导线与机壳之间：a 接 net，b 接仿真参考地")
        component = components.get(eid)
        if not isinstance(component, dict):
            raise ValueError(f"{eid} 需要电容仿真参数")
        actual = number(component.get("value"), f"{eid}.value")
        if not 0 < actual <= 1e-3:
            raise ValueError(f"{eid} 寄生电容需大于 0 且不超过 1 mF")
        if item["source"] == "parallel_plate":
            expected = plate_capacitance(item)
            if not math.isclose(actual, expected, rel_tol=1e-6):
                raise ValueError(f"{eid} 电容值与平行板公式不一致，请重新采用估算值或把来源改为手动")
        elif set(item) & set(PARASITIC_FIELDS[3:]):
            raise ValueError(f"{eid} 只有 parallel_plate 来源可填写几何估算参数")
    if "filter" in cfg:
        f = cfg["filter"]
        _keys(f, (*FILTER_PARTS, *FILTER_INPUTS), label="emi.filter")
        bound = []
        for key, (_, kind) in FILTER_PARTS.items():
            ref = f[key]
            if not isinstance(ref, str) or ref not in ids or ids[ref].kind != kind or ref in bound:
                raise ValueError(f"emi.filter.{key} 需选择未被重复使用的 {kind} 元件")
            if not isinstance(components.get(ref), dict):
                raise ValueError(f"{ref} 需要已有的滤波器仿真参数")
            bound.append(ref)
        if set(bound) & used:
            raise ValueError("滤波电容不能同时登记为对地寄生电容")
        for key in FILTER_INPUTS:
            f[key] = number(f[key], f"emi.filter.{key}")
        if not 0 < f["coupling"] < 1 or not 0 <= f["margin_db"] <= 40:
            raise ValueError("滤波器要求 0 < coupling < 1，裕量 0～40 dB")
        if not 0 < f["x_capacitance"] <= 1e-3 or not 0 < f["y_capacitance"] <= 1e-6:
            raise ValueError("设计输入要求 0 < X ≤ 1 mF、0 < 每只 Y ≤ 1 μF；实际漏电与安规由用户核定")
        _filter_connections(lay, f, nets, ground)
    return cfg


def _filter_connections(lay, f, nets, ground):
    n = lambda key, pin: nets.get(f[key] + "." + pin)
    # Two equal dotted windings first, one DM inductor per rail, capacitors at DUT.
    required = [(n("choke", "p2"), n("dm_positive", "a")),
                (n("choke", "s2"), n("dm_negative", "a")),
                (n("dm_positive", "b"), n("x", "a")), (n("dm_negative", "b"), n("x", "b")),
                (n("x", "a"), n("y_positive", "a")), (n("x", "b"), n("y_negative", "a")),
                (n("y_positive", "b"), nets.get(ground)), (n("y_negative", "b"), nets.get(ground))]
    distinct = {n("choke", "p1"), n("choke", "s1"), n("choke", "p2"), n("choke", "s2"),
                n("x", "a"), n("x", "b"), nets.get(ground)}
    if any(a is None or b is None or a != b for a, b in required) or None in distinct or len(distinct) != 7:
        raise ValueError("滤波器接法不符：共模绕组 p1/s1 朝 LISN，p2/s2 分别接差模电感 a，"
                         "差模电感 b 接 X 两端及各 Y 的 a，Y 的 b 接机壳；回线不可短接机壳")
    el = next(e for e in lay.elements if e.id == f["choke"])
    if any(k != "p1" and k != "p2" and k != "s1" and k != "s2" for k in el.pins) or el.opts.get("dots", "same") != "same":
        raise ValueError("共模电感需同侧同名端的双绕组，不支持中心抽头或第三、第四绕组")


def problems(lay, nets):
    if "emi" in lay.extra:
        try:
            settings(lay, nets)
        except ValueError as exc:
            yield "bad-emi", str(exc)


def plate_capacitance(item):
    values = {k: number(item.get(k), f"平行板.{k}") for k in PARASITIC_FIELDS[3:]}
    if any(v <= 0 for v in values.values()):
        raise ValueError("平行板面积、间距、相对介电常数都必须大于 0")
    return EPSILON_0 * values["epsilon_r"] * values["area_mm2"] * 1e-3 / values["gap_mm"]


def set_parasitic(lay, item, value=None):
    """Explicit adoption updates an existing drawn capacitor, never inserts one."""
    out = deepcopy(lay)
    _keys(item, PARASITIC_FIELDS[:3], PARASITIC_FIELDS[3:], "寄生参数")
    if not isinstance(item["element"], str):
        raise ValueError("寄生电容需填写已画元件 ID")
    sim = out.extra.get("simulation")
    if not isinstance(sim, dict) or not isinstance(sim.get("components"), dict):
        raise ValueError("请先配置 simulation.components 仿真参数")
    component = sim["components"].get(item["element"])
    if not isinstance(component, dict) or not any(e.id == item["element"] and e.kind == "C" for e in out.elements):
        raise ValueError("请先画出电容并配置 SPICE 参数，再关联寄生值")
    value = plate_capacitance(item) if item["source"] == "parallel_plate" else number(value, "寄生电容 (F)")
    if not 0 < value <= 1e-3:
        raise ValueError("寄生电容需大于 0 且不超过 1 mF")
    component["value"] = f"{value:.15g}"
    _keys(out.extra.get("emi"), ("version", *INPUTS), ("parasitics", "filter"), "emi")
    entries = out.extra["emi"].setdefault("parasitics", [])
    if not isinstance(entries, list):
        raise ValueError("emi.parasitics 必须为列表")
    for i, p in enumerate(entries):
        _keys(p, PARASITIC_FIELDS[:3], PARASITIC_FIELDS[3:], f"emi.parasitics[{i}]")
    entries[:] = [p for p in entries if p["element"] != item["element"]] + [deepcopy(item)]
    next(e for e in out.elements if e.id == item["element"]).opts["dash"] = "dashed"
    settings(out)
    return out


def import_parasitics(lay, raw: str, source: str):
    if source not in ("measured", "q3d"):
        raise ValueError("导入来源请选择 measured 或 q3d")
    if not isinstance(raw, str) or len(raw) > 65536:
        raise ValueError("寄生参数 CSV 最大 64 KiB")
    reader = csv.DictReader(io.StringIO(raw.lstrip("\ufeff")))
    if reader.fieldnames != ["element", "net", "capacitance_F"]:
        raise ValueError("CSV 表头需为 element,net,capacitance_F；Q3D 请先导出并换算为法拉 F")
    out, seen = deepcopy(lay), set()
    for row in reader:
        if set(row) != set(reader.fieldnames) or None in row.values() or row["element"] in seen:
            raise ValueError("寄生参数 CSV 列数不符或元件重复")
        seen.add(row["element"])
        out = set_parasitic(out, {"element": row["element"], "net": row["net"], "source": source}, row["capacitance_F"])
    if not seen:
        raise ValueError("寄生参数 CSV 没有数据")
    return out


def _fft(values):
    """In-place radix-2 FFT; bounded to MAX_FFT, keeping the base install dependency-free."""
    a = [complex(v) for v in values]
    n, j = len(a), 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j ^= bit
        if i < j:
            a[i], a[j] = a[j], a[i]
    size = 2
    while size <= n:
        half = size // 2
        angle = -2 * math.pi / size
        step = complex(math.cos(angle), math.sin(angle))
        for start in range(0, n, size):
            w = 1 + 0j
            for i in range(start, start + half):
                u, v = a[i], a[i + half] * w
                a[i], a[i + half] = u + v, u - v
                w *= step
        size *= 2
    return a


def reference_db(f):
    """TI SLYY202 table 5: CISPR 32 Class B AC mains, lower boundary value."""
    if not 150e3 <= f <= MAX_FREQUENCY:
        raise ValueError("参考曲线只覆盖 150 kHz～30 MHz")
    average = 56 - 10 * math.log(f / 150e3) / math.log(500e3 / 150e3) if f < 500e3 else 46 if f <= 5e6 else 50
    return average + 10, average


def spectrum(data: Data, cfg: dict) -> dict:
    t = data.time
    lo, hi = bisect_left(t, cfg["start"]), bisect_right(t, cfg["stop"])
    if t[0] > cfg["start"] or t[-1] < cfg["stop"] - max(1e-12, abs(cfg["stop"]) * 1e-8):
        raise ValueError("波形未覆盖整个 EMI 时间窗；不使用截断数据")
    count = hi - lo
    if count < MIN_FFT:
        raise ValueError(f"EMI 时间窗至少需要 {MIN_FFT} 个样本")
    n = min(MAX_FFT, 1 << (count.bit_length() - 1))
    lo = hi - n  # Explicitly report the actual last power-of-two window.
    dt = (t[hi - 1] - t[lo]) / (n - 1)
    if dt <= 0 or any(abs(t[i] - t[i - 1] - dt) > dt * 1e-4 for i in range(lo + 1, hi)):
        raise ValueError("FFT 需要等间隔采样；请导出均匀 CSV，不对不规则采样暗中插值")
    if 1 / dt < SAMPLES_PER_CYCLE * cfg["f_max"] * (1 - 1e-8):
        raise ValueError(f"采样率不足：最高分析频率需至少 {SAMPLES_PER_CYCLE} 点/周期；请缩小 SPICE 输出间隔和最大步长")
    df = 1 / (n * dt)
    if df > cfg["f_min"] / 10:
        raise ValueError("分析时间窗太短，最低频率需要至少 10 个周期")
    window = [0.5 - 0.5 * math.cos(2 * math.pi * i / n) for i in range(n)]
    transform = []
    for key in ("positive", "negative"):
        name = cfg[key] + "_V"
        if name not in data.columns:
            raise ValueError(f"波形缺少通道 {name}")
        values = data.columns[name][lo:hi]
        mean = sum(values) / n
        transform.append(_fft((v - mean) * w for v, w in zip(values, window)))
    scale = math.sqrt(2) / sum(window)
    bins = list(range(math.ceil(cfg["f_min"] / df), min(n // 2, math.floor(cfg["f_max"] / df)) + 1))
    if len(bins) < 2:
        raise ValueError("所选频带不足两个 FFT 频点，请扩大频带或延长时间窗")
    db = lambda z: 20 * math.log10(max(abs(z) * scale, 1e-18) / 1e-6)
    p, m = transform
    curves = {"positive": [db(p[k]) for k in bins], "negative": [db(m[k]) for k in bins],
              "cm": [db((p[k] + m[k]) / 2) for k in bins], "dm": [db((p[k] - m[k]) / 2) for k in bins]}
    freq = [k * df for k in bins]
    result = {"frequency_hz": freq, "dbuv": curves, "samples": n,
              "start_s": t[lo], "last_sample_s": t[hi - 1], "sample_rate_hz": 1 / dt,
              "bin_hz": df, "hann_enbw_hz": 1.5 * df, "note": NOTE}
    if cfg["reference"] != "none":
        result["reference"] = {"quasi_peak": [reference_db(f)[0] for f in freq],
                               "average": [reference_db(f)[1] for f in freq]}
    result["peaks"] = {key: {"frequency_hz": freq[max(range(len(freq)), key=values.__getitem__)], "dbuv": max(values)}
                       for key, values in curves.items()}
    return result


def recommend(lay: Layout, result: dict):
    cfg = settings(lay)
    if "filter" not in cfg:
        raise ValueError("请先画出滤波器并在 EMI 窗口选择对应器件")
    if cfg["reference"] == "none":
        raise ValueError("请明确选择工程参考曲线后再估算所需衰减")
    f = cfg["filter"]
    corners, attenuation = {}, {}
    for mode in ("cm", "dm"):
        # Each mode gets half the line-voltage budget: |CM±DM| ≤ |CM|+|DM|.
        required = [max(0, level - reference_db(hz)[1] + f["margin_db"] + 20 * math.log10(2))
                    for hz, level in zip(result["frequency_hz"], result["dbuv"][mode])]
        attenuation[mode] = max(required)
        corners[mode] = min(hz / 10 ** (a / 40) for hz, a in zip(result["frequency_hz"], required))
    cy, cx, k = f["y_capacitance"], f["x_capacitance"], f["coupling"]
    lc = 1 / ((2 * math.pi * corners["cm"]) ** 2 * cy * (1 + k))
    # Equal coupled windings: L+M for CM, L-M for DM; Cx+Cy/2 across rails.
    ld = max(1e-9, 1 / (2 * (2 * math.pi * corners["dm"]) ** 2 * (cx + cy / 2)) - lc * (1 - k))
    if lc > 1 or ld > 1:
        raise ValueError("所需电感超过 1 H，单级无损 LC 初估不适用；需重新选择电容或多级设计")
    candidate = deepcopy(lay)
    values = candidate.extra["simulation"]["components"]
    values[f["choke"]].update(model="ideal_transformer", ratio=f"{1 / k:.15g}", polarity="same",
                               magnetizing_inductance=f"{k*k*lc:.15g}", leakage_inductance=f"{(1-k*k)*lc:.15g}")
    for key, value in (("dm_positive", ld), ("dm_negative", ld), ("x", cx), ("y_positive", cy), ("y_negative", cy)):
        values[f[key]]["value"] = f"{value:.15g}"
    updates = {f[key]: values[f[key]] for key in FILTER_PARTS}
    return candidate, {"per_winding_h": lc, "dm_per_line_h": ld, "x_f": cx, "y_each_f": cy,
                       "corner_hz": corners, "required_db": attenuation, "updates": updates,
                       "note": "40 dB/dec 无损二阶渐近初估；耦合绕组按 L、k 换算已有变压器模型。"
                               "必须重跑开关仿真；谐振、阻尼、磁饱和、自谐振及 X/Y 电容漏电与额定值需另行核定。"}


def time_summary(data, start, stop):
    """Time-weighted means of both window halves expose startup/drift."""
    def window(values, a, b):
        integral, low, high = 0.0, math.inf, -math.inf
        for i in range(max(1, bisect_right(data.time, a)), min(len(data.time), bisect_left(data.time, b) + 1)):
            t0, t1 = data.time[i-1], data.time[i]
            left, right = max(a, t0), min(b, t1)
            v0, v1 = values[i-1], values[i]
            x = v0 + (v1-v0)*(left-t0)/(t1-t0)
            y = v0 + (v1-v0)*(right-t0)/(t1-t0)
            integral += (x+y)*(right-left)/2
            low, high = min(low,x,y), max(high,x,y)
        return {"mean": integral/(b-a), "min":low, "max":high}
    mid = (start+stop)/2
    return {name: {"first_half":window(v,start,mid), "second_half":window(v,mid,stop)}
            for name,v in data.columns.items() if name != "time_s"}


def spectrum_svg(result, compared=None):
    """Standalone plot: retain each display bucket's peak, never average dB."""
    w, h, left, top, cw, ch = 1000, 540, 75, 50, 890, 390
    freq = result["frequency_hz"]
    xf = lambda f: left + cw * math.log(f / freq[0]) / math.log(freq[-1] / freq[0])
    y = lambda d: top + ch * (140 - max(-40, min(140, d))) / 180
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="传导 EMI 估算频谱">',
           '<rect width="100%" height="100%" fill="white"/>',
           '<g font-family="sans-serif" font-size="13" fill="#222">',
           '<text x="75" y="24">传导 EMI 估算 · FFT 正弦 RMS 幅值（非接收机读数）</text>']
    for db in range(-40, 141, 20):
        out += [f'<path d="M {left} {y(db):.2f} h {cw}" stroke="#ddd"/>', f'<text x="20" y="{y(db)+4:.2f}">{db}</text>']
    for hz in (150e3, 300e3, 1e6, 3e6, 10e6, 30e6):
        if freq[0] <= hz <= freq[-1]:
            x = xf(hz)
            out += [f'<path d="M {x:.2f} {top} v {ch}" stroke="#ddd"/>', f'<text x="{x:.2f}" y="465" text-anchor="middle">{hz/1e6:g} MHz</text>']
    colors = {"positive": "#1b68b3", "negative": "#9a5932", "cm": "#c8213e", "dm": "#14855b"}
    for compare, data in enumerate([result] + ([compared] if compared else [])):
        for key, color in colors.items():
            buckets = {}
            for hz, level in zip(data["frequency_hz"], data["dbuv"][key]):
                bucket = int(xf(hz))
                if bucket not in buckets or level > buckets[bucket][1]:
                    buckets[bucket] = (hz, level)
            points = " ".join(f"{xf(hz):.2f},{y(level):.2f}" for hz, level in buckets.values())
            out.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="1.2"' + (' stroke-dasharray="5 3"' if compare else '') + '/>')
    for key, levels in result.get("reference", {}).items():
        points = " ".join(f"{xf(hz):.2f},{y(level):.2f}" for hz, level in zip(freq, levels))
        out.append(f'<polyline points="{points}" fill="none" stroke="#555" stroke-dasharray="2 5"/>')
    for i, (key, color) in enumerate(colors.items()):
        out.append(f'<text x="{75+i*150}" y="495" fill="{color}">{CHANNELS[key]}</text>')
    out += ['<text x="675" y="495">实线：原参数；虚线：建议参数</text>',
            '<text x="75" y="523">dBμV · ' + ('灰点线：CISPR 32 B级 AC端口参考（QP / AV），不判断直流端口合规' if 'reference' in result else '未叠加限值；不作合规判断') + '</text>', '</g></svg>']
    return "\n".join(out)


def run(lay, out_dir, *, profile="double", compare=False, ngspice=None, base_dir=None):
    """Stage both runs; never save a partial comparison or mutate the open drawing."""
    cfg = settings(lay)
    tr = lay.extra.get("simulation", {}).get("transient", {})
    if not isinstance(tr, dict):
        raise ValueError("simulation.transient 必须是时间参数对象")
    for key in ("step", "max_step"):
        if number(tr.get(key), f"transient.{key}") > 1 / (SAMPLES_PER_CYCLE * cfg["f_max"]):
            raise ValueError("EMI 采样与最大积分步长均需满足最高频率至少 10 点/周期；请先调整 SPICE 参数")
    with tempfile.TemporaryDirectory(prefix="cd_emi_") as temp:
        stage = Path(temp)
        result = {"note": NOTE}
        export_spice(lay, stage, "before", profile=profile, run=True, ngspice=ngspice, base_dir=base_dir)
        data = parse_csv((stage / "before.csv").read_bytes())
        result["before"] = spectrum(data, cfg)
        result["before"]["time_summary"] = time_summary(data, result["before"]["start_s"], result["before"]["last_sample_s"])
        if compare:
            candidate, proposal = recommend(lay, result["before"])
            export_spice(candidate, stage, "after", profile=profile, run=True, ngspice=ngspice, base_dir=base_dir)
            data = parse_csv((stage / "after.csv").read_bytes())
            result.update(after=spectrum(data, cfg), proposal=proposal)
            result["after"]["time_summary"] = time_summary(data, result["after"]["start_s"], result["after"]["last_sample_s"])
            result["peak_reduction_db"] = {mode: result["before"]["peaks"][mode]["dbuv"] - result["after"]["peaks"][mode]["dbuv"]
                                           for mode in result["before"]["peaks"]}
            candidate.save(stage / "candidate.json")
            result["candidate"] = candidate.to_dict()
        for name in ("before", "after"):
            if name in result and cfg["reference"] != "none":
                r = result[name]
                # This is a numeric design gap, not a pass/fail decision.
                r["max_above_average_reference_db"] = max(
                    level - limit for mode in ("positive", "negative")
                    for level, limit in zip(r["dbuv"][mode], r["reference"]["average"]))
        (stage / "spectrum.svg").write_text(spectrum_svg(result["before"], result.get("after")), encoding="utf-8")
        (stage / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        with (stage / "spectrum.csv").open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            names = [key for key in ("before", "after") if key in result]
            writer.writerow(["frequency_Hz", *(f"{name}_{mode}_dBuV" for name in names for mode in result[name]["dbuv"])])
            for i, freq in enumerate(result["before"]["frequency_hz"]):
                writer.writerow([freq, *(values[i] for name in names for values in result[name]["dbuv"].values())])
        destination = Path(out_dir)
        destination.mkdir(parents=True, exist_ok=True)
        import shutil
        files = list(stage.iterdir())
        for path in files:
            shutil.copyfile(path, destination / path.name)
        result["files"] = [str((destination / p.name).resolve()) for p in files]
        result["svg"] = (stage / "spectrum.svg").read_text(encoding="utf-8")
        return result
