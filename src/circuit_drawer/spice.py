"""Validated coordinate table -> ngspice transient deck -> optional CSV.

The drawing remains the connection source. No topology is recreated from its
name, no label is interpreted as a value, and no user-supplied SPICE is run.
The backend uses R/L/C, DC/step/pulse/sine V/I, generic diodes, PWM MOSFET/IGBT
approximations with separate antiparallel diodes and an optional linear output
capacitance, gate-fired thyristors that latch while they conduct forward, and
ideal transformers with optional primary-referred magnetising and leakage
inductance. It is not a semiconductor loss model.
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .layout import Layout
from .pipeline import LayoutRejected
from .profiles import get_profile
from .validate import check
from .completeness import simulation_completeness


class SpiceError(ValueError):
    pass


# A drawing (from a template, the AI or by hand) has no simulation section
# until its values are filled in; say where, not which keys are missing.
NO_SIMULATION = ("还没有仿真参数。打开“分析 → SPICE 仿真…”，填写元件值、参考地、仿真时间和测量通道，"
                 "填好后这里显示完整网表。")


# Keep the numerical transformer reference in the gigaohm range: it defines
# common mode with negligible loading, not a user-specified physical load.
MIN_TRANSFORMER_REFERENCE_OHM = 1e9
# Resolve a sinusoid well beyond Nyquist for usable phase/RMS comparisons.
MIN_SINE_SAMPLES_PER_PERIOD = 100
VOLTAGE_SOURCES = ("V", "vac")
PWM_SWITCHES = ("nmos", "igbt")
# parts with a gate pulse train: the PWM switches and the thyristor
GATED = (*PWM_SWITCHES, "scr")
CURRENT_PROBE_KINDS = ("D", "L", *VOLTAGE_SOURCES, "igbt", "scr", "transformer")
MAX_PROBES = 32  # Existing trace limit, shared with editor availability.
# A thyristor's latch is an ideal switch with hysteresis in a 1 V control
# loop: the gate drives its control to 1 (on), forward conduction holds it at
# 0.5 (between the thresholds: keeps its state), otherwise 0 (off). The RC
# (1 ohm, 1 nF: 1 ns) only smooths that control node for the solver.
SCR_LATCH = "SW(RON=1m ROFF=1G VT=0.5 VH=0.4)"
SCR_HOLD_RC = ("1", "1n")


# name, user-facing label, example value (SI or SPICE suffix).
FIELDS = {
    "V": [("dc", "直流值 / DC 工作点 (V，b−a)", "24")],
    "vac": [("dc", "DC 工作点 (V，b−a；瞬态由正弦参数指定)", "0")],
    "I": [("dc", "直流值 / DC 工作点 (A，a→b)", "1")],
    "R": [("value", "电阻 (Ω)", "10")],
    "L": [("value", "电感 (H)", "500u"), ("initial", "初始电流 a→b (A)", "0")],
    "C": [("value", "电容 (F)", "220u"), ("initial", "初始电压 a−b (V)", "0")],
    "transformer": [("ratio", "Ns/Np（副边全绕组匝比；抽头每半边为其一半）", "1"),
                    ("reference_resistance", "副边参考点到 p2 的数值参考电阻 (Ω)", "1T")],
    "D": [("is", "饱和电流 Is (A)", "1n"), ("n", "发射系数 N", "1"),
          ("rs", "串联电阻 (Ω)", "0.01")],
    "nmos": [("ron", "导通电阻 (Ω)", "0.01"), ("roff", "关断电阻 (Ω)", "1G"),
             ("frequency", "PWM 频率 (Hz)", "50k"), ("duty", "占空比 (0–1)", "0.5"),
             ("delay", "启动延迟 (s)", "0"), ("rise", "上升时间 (s)", "10n"),
             ("fall", "下降时间 (s)", "10n")],
}
FIELDS["igbt"] = [*FIELDS["nmos"], ("vce0", "正向压降截距 Vce0 (V，≥0；非固定 Vce(sat))", "1.5")]
# A thyristor's gate is a pulse train with the PWM fields: delay is the first
# firing instant (firing angle alpha at frequency f: alpha / 360 / f), duty the
# pulse width as a fraction of the period.
FIELDS["scr"] = [("ron", "导通电阻 (Ω)", "0.01"), ("roff", "关断电阻 (Ω)", "1G"),
                 ("vf", "正向压降 Vf (V，≥0)", "1"),
                 ("frequency", "触发脉冲频率 (Hz，通常等于电网频率)", "50"),
                 ("duty", "触发脉冲宽度 / 周期 (0–1)", "0.05"),
                 ("delay", "首次触发时刻 (s；触发角 α 为 α/360/f)", "3.333m"),
                 ("rise", "脉冲上升时间 (s)", "1u"), ("fall", "脉冲下降时间 (s)", "1u")]
# An optional field left out takes its listed default; every default except
# the body diode's reproduces the deck written before the field existed.
OPTIONAL_FIELDS = {
    "D": [("breakdown_voltage", "击穿电压 Vz@Izt (V，0 不建模；端电压另加 I×rs)", "0"),
          ("breakdown_current", "击穿测试电流 Izt (A，>0)", "10m"),
          ("zener_resistance", "击穿动态电阻 Zzt (Ω，>0；不含 rs)", "5")],
    "L": [("series_resistance", "电感串联电阻 (Ω，0 禁用)", "0")],
    # a source's internal impedance, in series with its + terminal b (a
    # battery's internal resistance, a grid's short-circuit impedance)
    "V": [("series_resistance", "内阻：串在 + 端 b 的电阻 (Ω，0 不建模)", "0"),
          ("series_inductance", "内感：串在 + 端 b 的电感 (H，0 不建模)", "0")],
    "transformer": [("magnetizing_inductance", "励磁电感 Lm，折算到原边、并在原边 (H，0 不建模)", "0"),
                    ("leakage_inductance", "漏感，折算到原边、串在 p1 (H，0 不建模)", "0")],
    "nmos": [("snubber_resistance", "跨开关串联 RC：电阻 (Ω，R/C 同时为 0 禁用)", "0"),
             ("snubber_capacitance", "跨开关串联 RC：电容 (F)", "0"),
             ("phase_shift", "附加移相 (°，正值滞后，−180 至 180)", "0"),
             ("phase_ramp", "从零移相线性爬升的时长 (s，0 为固定移相)", "0"),
             ("output_capacitance", "输出电容 Coss / Coes，线性、并在开关两端 (F，0 不建模)", "0"),
             ("diode_is", "体二极管 / 反并联二极管 Is (A)", "1n"),
             ("diode_n", "体二极管 / 反并联二极管发射系数 N", "1"),
             ("diode_rs", "体二极管 / 反并联二极管串联电阻 (Ω)", "0.01")],
}
# Optional fields that must be > 0 rather than >= 0.
POSITIVE_OPTIONAL = ("diode_is", "diode_n", "breakdown_current", "zener_resistance")
OPTIONAL_FIELDS["vac"] = OPTIONAL_FIELDS["V"]
# The antiparallel diode written before its parameters could be set: kept
# verbatim while they hold these defaults, so earlier decks stay byte-identical.
BODY_DIODE = "IS=1e-9 N=1 RS=0.01"
BODY_DIODE_VALUES = {"diode_is": 1e-9, "diode_n": 1.0, "diode_rs": 0.01}
OPTIONAL_FIELDS["igbt"] = OPTIONAL_FIELDS["nmos"]
# RC snubber and firing-angle phase shift / soft start, as on a PWM switch;
# holding / latching current (0: off at zero current, latched at once)
OPTIONAL_FIELDS["scr"] = [*OPTIONAL_FIELDS["nmos"][:4],
                          ("holding_current", "维持电流 IH (A，0 为电流过零才关断)", "0"),
                          ("latching_current", "擎住电流 IL (A，0 为不要求；不小于 IH)", "0"),
                          ("turn_off_time", "关断时间 tq (s，0 为不建模；反压不足 tq 时正压回来即重新导通)", "0")]
# Recovery fraction thresholds of the tq timer (1 = just turned off, 0 = recovered):
# refiring needs some reverse bias to have started and not finished
TQ_STARTED, TQ_DONE = 0.999, 0.001
TRANSIENT_FIELDS = [("step", "输出采样间隔 (s)", "200n"),
                    ("stop", "结束时间 (s)", "20m"),
                    ("max_step", "最大积分步长 (s)", "200n"),
                    ("node_capacitance", "数值稳定电容：每网络到仿真地 (F，0 禁用)", "0")]
SOURCE_WAVE_FIELDS = {
    "sine": [("offset", "正弦偏置 (V / A)", "0"), ("amplitude", "正弦峰值（非 RMS，V / A）", "24"),
             ("frequency", "正弦频率 (Hz)", "50"), ("delay", "正弦延迟 (s)", "0"),
             ("phase", "初相位 (°，−360 至 360；负值滞后)", "0")],
    "step": [("initial", "初始值 (V / A)", "0"), ("final", "最终值 (V / A)", "5"),
             ("delay", "跳变开始时间 (s)", "1m"), ("rise", "过渡时间 (s)", "1u")],
    "pulse": [("initial", "初始值 (V / A)", "0"), ("final", "脉冲值 (V / A)", "5"),
              ("delay", "启动延迟 (s)", "1m"), ("rise", "上升过渡时间 (s)", "1u"),
              ("fall", "返回过渡时间 (s)", "1u"), ("width", "脉冲平台宽度 (s)", "1m"),
              ("period", "重复周期 (s)", "2m")],
}
LIMIT_NOTE = ("简化模型：MOSFET 符号导出为 PWM 理想开关和通用反并联二极管；"
              "IGBT 为正向分段线性通道（Vce0 + Ron·I）和独立反并联二极管，反向不走通道；"
              "变压器为无损理想变比，可选填折算到原边的线性励磁电感和漏感，副边数值参考电阻需显式填写；"
              "开关可选填线性输出电容 Coss，只能定性观察 ZVS 换流过程，不含电压相关的 Coss 和栅极电荷；"
              "晶闸管为门极脉冲触发、正向导通（Vf + Ron）、电流将要反向时关断的简化模型；维持 / 擎住电流和关断时间 tq 是可选的简化判据，不含 dv/dt 误触发；"
              "数值稳定电容不是器件寄生模型；不包含反向恢复和磁饱和；闭环控制只在填写 closed_loop 时生成连续时间控制器模型；不能用于器件损耗结论。"
              "校验只检查连接、几何与参数格式，不证明工况正确。")


def number(value, label: str) -> float:
    """Only finite numbers and explicit engineering suffixes; never expressions."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise SpiceError(f"{label} 必须是数值")
    text = str(value).strip()
    match = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)(meg|[fpnuµmkgt])?",
                         text, re.I)
    if not match:
        raise SpiceError(f"{label}: 无效数值 {text!r}；例如 24、500u、50k；兆用 Meg")
    factors = {"": 1, "f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6,
               "µ": 1e-6, "m": 1e-3, "k": 1e3, "meg": 1e6, "g": 1e9, "t": 1e12}
    out = float(match[1]) * factors[(match[2] or "").lower()]
    if not math.isfinite(out):
        raise SpiceError(f"{label} 必须是有限数值")
    return out


def _keys(obj, required, optional=(), label="simulation"):
    if not isinstance(obj, dict):
        raise SpiceError(f"{label} 必须是对象")
    missing, extra = set(required) - obj.keys(), obj.keys() - set(required) - set(optional)
    if missing or extra:
        parts = ([f"缺少字段 {sorted(missing)}"] if missing else []) + ([f"未知字段 {sorted(extra)}"] if extra else [])
        raise SpiceError(f"{label}: " + "；".join(parts))


_DRAWING_ONLY = frozenset({
    "bad-region", "bad-color", "bad-annotation", "bad-glyph", "bad-block",
    "text-on-symbol", "text-on-line", "text-on-block", "text-on-callout", "text-overlap",
    "region-cuts-symbol", "callout-on-symbol", "callout-through-block",
    "block-on-symbol", "line-through-block", "block-overlap", "block-text-overflow",
    "inline-label-end", "isolation-on-label", "arrow-as-text", "arrow-off-wire",
    "electrical-label-space", "too-cramped",
})


def _simulation_report(report):
    """Ignore only known drawing errors, preserving nets and electrical checks.

    Unknown codes fail closed. detail-wired and geometry shared by electrical
    objects are deliberately not exempt; completeness/model checks follow.
    """
    from dataclasses import replace
    return replace(report, issues=[replace(i, level="note")
                                   if i.code in _DRAWING_ONLY and i.level == "error" else i
                                   for i in report.issues])


def _checked_for_simulation(layout, profile, report=None, *, connections=()):
    rep = _simulation_report(report if report is not None else check(layout, profile, connections=connections))
    if rep.ok and not rep.nets and layout.elements:
        # Bad annotation syntax can stop the schema pass before nets are built.
        # Recheck the untouched electrical objects; an empty report is not proof
        # of connection. The user's table is never changed by this check.
        from copy import deepcopy
        from .regions import RegionError, parse_callout, parse_region
        drawing = deepcopy(layout)
        drawing.annotations = []
        # Retain valid regions/arrows so detail-wired is still checked, even
        # when a different decoration had a syntax/colour error.
        for key, parse in (("callouts", lambda d, i: parse_callout(d, i)),
                           ("regions", lambda d, i: parse_region(d, i, layout, rep.scale, profile))):
            valid = []
            raw = drawing.extra.get(key, [])
            for i, item in enumerate(raw if isinstance(raw, list) else []):
                try:
                    parse(item, i)
                except RegionError:
                    continue
                valid.append(item)
            if key in drawing.extra:
                drawing.extra[key] = valid
        if any(i.code == "bad-block" for i in rep.issues):
            drawing.extra.pop("blocks", None)
        if any(i.code == "bad-glyph" for i in rep.issues):
            drawing.extra.pop("glyphs", None)
        rep = _simulation_report(check(drawing, profile, connections=connections))
    return rep


def _connections(layout: Layout, profile: str, report=None):
    rep = _checked_for_simulation(layout, get_profile(profile), report)
    if not rep.ok:
        raise LayoutRejected(rep)
    completeness = simulation_completeness(layout)
    if not completeness["ok"]:
        raise SpiceError("仿真电路完整性检查未通过：\n" + "\n".join(completeness["issues"]))
    pins = {f"{e.id}.{p}" for e in layout.elements for p in e.pins}
    if not pins or set(layout.netlist) != pins:
        missing, extra = sorted(pins - set(layout.netlist)), sorted(set(layout.netlist) - pins)
        listed = lambda refs: "、".join(refs[:8]) + (f" 等 {len(refs)} 个" if len(refs) > 8 else "")
        raise SpiceError("SPICE 导出需要完整网表" + (f"：缺少 {listed(missing)}" if missing else "")
                         + (f"；多出已不存在的 {listed(extra)}" if extra else "")
                         + "。请先核对连接，再用“分析 → 以当前连接更新网表”更新")
    unsupported = [f"{e.id} ({e.kind})" for e in layout.elements if e.kind not in FIELDS and e.kind != "port"]
    if unsupported:
        raise SpiceError("第一版尚不支持这些器件：" + ", ".join(unsupported))
    return rep


def example_settings(layout: Layout, topology: str, profile="ieee-single") -> dict:
    """Explicit example preset, only after matching the actual device graph.

    ``buck`` also accepts an N-phase interleaved buck (one V, C, R; N switches,
    diodes and inductors sharing the input and output); phase k starts its PWM
    k/N of a switching period later, so the carriers are interleaved by 360°/N.
    """
    rep = _connections(layout, profile)
    if topology not in ("buck", "boost"):
        raise SpiceError("示例参数只提供 buck / boost")
    count = Counter(e.kind for e in layout.elements)
    phases = count["nmos"]
    single = Counter(("V", "R", "L", "C", "D", "nmos"))
    multi = topology == "buck" and phases > 1 and count == Counter(
        {"V": 1, "R": 1, "C": 1, "L": phases, "D": phases, "nmos": phases})
    if count != single and not multi:
        raise SpiceError("示例参数要求 V、L、C、R、D、nmos 各一个（Buck 也可为 N 相交错："
                         "V、C、R 各一个，nmos、D、L 各 N 个）；其他电路请手动填写")
    es = {e.kind: e for e in layout.elements if e.kind in ("V", "R", "C")}
    ref = lambda e, pin: f"{e.id}.{pin}"
    node = lambda e, pin: rep.nets[ref(e, pin)]
    ground, vin = node(es["V"], "a"), node(es["V"], "b")
    load = {node(es["R"], "a"), node(es["R"], "b")}
    if ground not in load or len(load) != 2:
        raise SpiceError("负载必须跨接输出与输入负母线")
    out = (load - {ground}).pop()
    ok = len({ground, vin, out}) == 3 and {node(es["C"], "a"), node(es["C"], "b")} == load
    # Pair every switch with the diode and inductor on its own switch node.
    legs, used_d, used_l = [], set(), set()
    for sw_el in [e for e in layout.elements if e.kind == "nmos"]:
        sw = node(sw_el, "s" if topology == "buck" else "d")
        d = [e for e in layout.elements if e.kind == "D" and e.id not in used_d
             and sw in (node(e, "a"), node(e, "b"))]
        ind = [e for e in layout.elements if e.kind == "L" and e.id not in used_l
               and sw in (node(e, "a"), node(e, "b"))]
        if len(d) != 1 or len(ind) != 1 or sw in {ground, vin, out}:
            ok = False
            break
        used_d.add(d[0].id)
        used_l.add(ind[0].id)
        legs.append((sw_el, d[0], ind[0], sw))
    ok &= len({leg[3] for leg in legs}) == len(legs)
    for s_el, d_el, l_el, sw in legs if ok else ():
        expect_l = {sw, out} if topology == "buck" else {vin, sw}
        ok &= {node(l_el, "a"), node(l_el, "b")} == expect_l
        if topology == "buck":
            ok &= node(s_el, "d") == vin and node(d_el, "a") == ground and node(d_el, "b") == sw
        else:
            ok &= node(s_el, "s") == ground and node(d_el, "a") == sw and node(d_el, "b") == out
    if not ok:
        name = f"{phases} 相交错 buck" if multi else f"基础 {topology}"
        raise SpiceError(f"当前连接不符合{name}；不会根据名称套用错误模型")
    values = {e.id: {k: value for k, _, value in FIELDS[e.kind]} for e in layout.elements}
    for k, (s_el, d_el, _, _) in enumerate(legs):
        values[s_el.id]["model"] = "ideal_switch"
        values[d_el.id]["model"] = "generic_diode"
        if multi:
            # Interleave with the start delay: phase_shift is limited to ±180°
            # and a negative shift would need a negative delay.
            period = 1 / number(values[s_el.id]["frequency"], f"{s_el.id}.frequency")
            values[s_el.id]["delay"] = f"{k * period / phases:.6g}"
    if topology == "boost":
        values[es["R"].id]["value"] = "30"
    positive = ref(es["R"], "a" if node(es["R"], "a") == out else "b")
    transient = {k: value for k, _, value in TRANSIENT_FIELDS}
    if topology == "boost":
        transient["stop"] = "80m"  # allow the startup oscillation to settle
    probes = [{"name": "v_out", "kind": "voltage", "positive": positive, "negative": ref(es["V"], "a")}]
    for k, (s_el, _, l_el, _) in enumerate(legs, 1):
        tag = str(k) if multi else ""
        probes.append({"name": "i_L" + tag, "kind": "current", "element": l_el.id})
    for k, (s_el, _, l_el, _) in enumerate(legs, 1):
        tag = str(k) if multi else ""
        probes.append({"name": "pwm" + tag, "kind": "drive", "element": s_el.id})
    return {"version": 1, "ground": ref(es["V"], "a"), "components": values,
            "transient": transient, "probes": probes}


@dataclass
class Deck:
    text: str
    columns: list[str]
    step: float
    stop: float
    notes: list[str] = field(default_factory=list)


def source_excitation(cfg: dict, label: str, tr: dict) -> str:
    """Only explicit simulation parameters drive SPICE; never drawing opts.wave."""
    dc = number(cfg["dc"], f"{label}.dc")
    text = f"DC {dc:.15g}"
    if "wave" not in cfg:
        return text
    wave = cfg["wave"]
    if (not isinstance(wave, dict) or not isinstance(wave.get("kind"), str)
            or wave["kind"] not in SOURCE_WAVE_FIELDS):
        raise SpiceError(f"{label}.wave.kind 必须是 sine、step 或 pulse")
    kind = wave["kind"]
    fields = [k for k, _, _ in SOURCE_WAVE_FIELDS[kind]]
    _keys(wave, ["kind", *fields], label=f"{label}.wave")
    v = {k: number(wave[k], f"{label}.wave.{k}") for k in fields}
    if kind == "sine":
        if v["amplitude"] <= 0 or v["frequency"] <= 0 or v["delay"] < 0 or not -360 <= v["phase"] <= 360:
            raise SpiceError(f"{label}: 正弦峰值、频率须 > 0，延迟须 ≥ 0，相位须在 −360 至 360 度")
        if not all(math.isfinite(v["offset"] + sign * v["amplitude"]) for sign in (-1, 1)):
            raise SpiceError(f"{label}: 正弦幅值加偏置超出有限数值范围")
        period = 1 / v["frequency"]
        if tr["step"] > period / MIN_SINE_SAMPLES_PER_PERIOD:
            raise SpiceError(f"{label}: 正弦每周期至少需要 {MIN_SINE_SAMPLES_PER_PERIOD} 个输出采样点")
        if tr["stop"] - v["delay"] < period:
            raise SpiceError(f"{label}: 结束时间须覆盖正弦延迟之后至少一个周期")
        return text + " SIN(" + " ".join(f"{v[k]:.15g}" for k in ("offset", "amplitude", "frequency", "delay")) + f" 0 {v['phase']:.15g})"
    if v["delay"] < 0 or v["rise"] <= 0 or v["initial"] == v["final"]:
        raise SpiceError(f"{label}: 延迟须 ≥ 0，过渡时间须 > 0，初值与终值须不同")
    end = v["delay"] + v["rise"]
    if end >= tr["stop"]:
        raise SpiceError(f"{label}: 仿真结束时间须晚于阶跃/脉冲上升沿结束")
    f = lambda k: f"{v[k]:.15g}"
    if kind == "step":
        pts = [(0, v["initial"])]
        if v["delay"] > 0:
            pts.append((v["delay"], v["initial"]))
        pts += [(end, v["final"]), (tr["stop"], v["final"])]
        return text + " PWL(" + " ".join(f"{t:.15g} {y:.15g}" for t, y in pts) + ")"
    off = v["period"] - v["rise"] - v["fall"] - v["width"]
    if min(v["width"], v["fall"], off) <= 0:
        raise SpiceError(f"{label}: 脉宽、返回时间须 > 0，周期须大于两段过渡时间与脉宽之和")
    if tr["step"] > min(v["width"], off) / 10:
        raise SpiceError(f"{label}: 时间步长过大；脉冲高低平台各需至少 10 个采样点")
    return text + " PULSE(" + " ".join(f(k) for k in fields) + ")"


def _pwm_lines(name, val, tr, label=None):
    """PULSE normally; explicit PWL edge times for a linear phase ramp.

    Invert u = t - phase_time * min(t/ramp, 1). Both bridge diagonals
    share this clock, preserving dead time and finite edges during startup.
    PWL covers startup through a complete falling edge after the ramp. A
    series PULSE source starts at the next rising edge; before then it is zero,
    and afterwards PWL holds zero. Both sources keep solver breakpoints without
    listing every steady-state cycle or switching a behavioral source mid-edge.
    """
    period = 1 / val["frequency"]
    label = label or name
    width = val["duty"] * period - (val["rise"] + val["fall"]) / 2
    shift = val.get("phase_shift", 0) * period / 360
    ramp = val.get("phase_ramp", 0)
    if abs(val.get("phase_shift", 0)) > 180:
        raise SpiceError(f"{label}.phase_shift 必须在 −180 至 180 度")
    if not ramp or not shift:
        delay = val["delay"] + shift
        if delay < 0 or delay + period > tr["stop"]:
            raise SpiceError(f"{label}: 移相后的启动延迟须 ≥ 0 且保留至少一个周期")
        return [f"Vctl_{name} ctl_{name} 0 PULSE(0 1 {delay:.15g} {val['rise']:.15g} "
                f"{val['fall']:.15g} {width:.15g} {period:.15g})"]
    if ramp <= abs(shift) or ramp + period > tr["stop"]:
        raise SpiceError(f"{label}.phase_ramp 须大于移相时间绝对值，且结束后至少保留一个周期")
    # Negative phase shortens the startup clock period: retain the same
    # sampling/step requirements used for ordinary PWM.
    clock_rate = max(1, 1 - shift / ramp)
    if (tr["step"] * clock_rate > period / 50
            or tr["max_step"] * clock_rate > min(width, period * (1-val["duty"])) / 10):
        raise SpiceError(f"{label}.phase_ramp 期间时间步长过大")
    def time_at(u):
        return u / (1-shift/ramp) if u < ramp-shift else u+shift
    points = [(0.0, 0)]
    for cycle in range(math.ceil((ramp + abs(shift)) / period) + 1):
        base = val["delay"] + cycle * period
        for offset, value in ((0, 0), (val["rise"], 1),
                              (val["rise"] + width, 1), (val["rise"] + width + val["fall"], 0)):
            t = time_at(base + offset)
            if t > points[-1][0]:
                points.append((t, value))
        if points[-1][0] >= ramp:
            break
    resume = val["delay"] + (cycle + 1) * period + shift
    steady = f"cd_{name}_steady"
    lines = [f"Vctl_{name} ctl_{name} {steady} PWL("]
    lines.extend("+ " + " ".join(f"{t:.15g} {v}" for t, v in points[i:i+4])
                 for i in range(0, len(points), 4))
    lines[-1] += ")"
    lines.append(f"Vsteady_{name} {steady} 0 PULSE(0 1 {resume:.15g} {val['rise']:.15g} "
                 f"{val['fall']:.15g} {width:.15g} {period:.15g})")
    return lines


def thyristor_thresholds(val):
    """Anode-cathode voltages of the holding and latching currents: the
    channel conducts Vf + Ron * I, so I > IH is Vak > Vf + Ron * IH. Shared
    with the Simulink / PLECS latch logic."""
    return (val["vf"] + val.get("holding_current", 0) * val["ron"],
            val["vf"] + val.get("latching_current", 0) * val["ron"])


def _thyristor_lines(name, anode, cathode, val):
    """Gate-fired, forward-only channel (Vf + Ron, like the IGBT's) with Roff
    leakage, held on by its latch while it conducts forward; it turns off when
    the anode-cathode voltage falls to Vf, i.e. when the current would reverse
    (an ideal holding current of zero). ``ctl_<name>`` is the gate pulse train.

    With a holding current IH it turns off when its current falls below IH;
    with a latching current IL the gate pulse must leave it carrying at least
    IL (a second latch, ``latched``, records that), or it turns off with the
    pulse. Both thresholds are anode-cathode voltages (thyristor_thresholds).

    With a turn-off time tq, a timer ``rec`` (1 = just off, 0 = recovered)
    runs down at 1/tq while it is off and reverse biased (Vak < 0). Forward
    voltage (Vak > Vf) while it has started but not finished recovering fires
    it again without a gate pulse: a commutation failure. A turn-off with no
    reverse bias after it (at the holding current, still forward biased) keeps
    the timer at 1 and does not refire."""
    a, lat, hold = f"cd_{name}_a", f"cd_{name}_lat", f"cd_{name}_hold"
    vak = f"v({a},{cathode})"
    f = lambda key: f"{val[key]:.15g}"
    r, c = SCR_HOLD_RC
    held = f"v({lat}) > 0.5 && {vak} > {f('vf')}"
    extra, refire, refired = [], "", ""
    if val.get("turn_off_time", 0):
        rec = f"cd_{name}_rec"
        refired = (f"v({lat}) < 0.5 && v({rec}) > {TQ_DONE:g} && v({rec}) < {TQ_STARTED:g} && "
                   f"{vak} > {f('vf')}")
        refire = f" + (({refired}) ? 1 : 0)"
        extra += [f"* {name}: turn-off time {f('turn_off_time')} s, counted under reverse bias",
                  # 1 nF: charged to 1 in about 1 ns while on, run down at 1/tq
                  f"Brec_{name} 0 {rec} I = v({lat}) > 0.5 ? (1 - v({rec})) : "
                  f"(({vak} < 0 && v({rec}) > 0) ? -{1e-9 / val['turn_off_time']:.15g} : 0)",
                  f"Crec_{name} {rec} 0 1n",
                  f"Rrec_{name} {rec} 0 1T"]
    if val.get("holding_current", 0) or val.get("latching_current", 0):
        v_hold, v_latch = thyristor_thresholds(val)
        flag, set_ = f"cd_{name}_latched", f"cd_{name}_set"
        set_on = f"v({lat}) > 0.5 ? ({vak} > {v_latch:.15g} ? 1 : 0.5) : 0"
        held = f"v({lat}) > 0.5 && v({flag}) > 0.5 && {vak} > {v_hold:.15g}"
        extra += [f"* {name}: holding {f('holding_current')} A / latching {f('latching_current')} A, as Vak thresholds",
                 # a refire (turn-off time not reached) counts as latched
                 f"Bset_{name} 0 {set_} I = " + (f"({refired}) ? 1 : ({set_on})" if refired else set_on),
                 f"Rset_{name} {set_} 0 {r}",
                 f"Cset_{name} {set_} 0 {c}",
                 f"Sflag_{name} cd_{name}_one {flag} {set_} 0 mlatch_{name}",
                 f"Rflag_{name} {flag} 0 1k"]
    return [f"* {name}: simplified thyristor; gate pulses fire it, forward conduction holds it",
            f"Vsense_{name} {anode} {a} 0",
            f"Bchannel_{name} {a} {cathode} I = {vak}/{f('roff')} + "
            f"(v({lat}) > 0.5 ? max({vak}-{f('vf')},0)/{f('ron')} : 0)",
            f"Bhold_{name} 0 {hold} I = v(ctl_{name}) + 0.5*(({held}) ? 1 : 0){refire}",
            f"Rhold_{name} {hold} 0 {r}",
            f"Chold_{name} {hold} 0 {c}",
            f"Vone_{name} cd_{name}_one 0 1",
            f"Slatch_{name} cd_{name}_one {lat} {hold} 0 mlatch_{name}",
            f"Rlatch_{name} {lat} 0 1k",
            *extra,
            f".model mlatch_{name} {SCR_LATCH}"]


def _transformer_lines(e, cfg, val, name, node):
    """VCVS + sensed-current feedback: Vs=n*Vp and Ip=-n*Is.

    A centre tap splits the end-to-end ratio into two equal, series-aiding
    halves. Optional primary-referred inductances: the leakage in series
    between p1's sensor and the ideal core, the magnetising inductance across
    the core's primary, so the primary probe reads the terminal current
    (magnetising current included). A coupled inductor L1, L2, k is the same
    circuit with Lm = k²·L1, leakage = (1−k²)·L1 and ratio = √(L2/L1)/k.
    See ngspice manual 4.2.2/4.2.3 (E/F dependent sources).
    """
    if cfg["model"] != "ideal_transformer":
        raise SpiceError(f"{e.id}: 仅支持 ideal_transformer；励磁电感、漏感用可选字段填写")
    polarity = cfg["polarity"]
    if polarity not in ("same", "opposite"):
        raise SpiceError(f"{e.id}.polarity 必须为 same 或 opposite")
    dots = e.opts.get("dots", "same")
    if dots is True:
        dots = "same"
    if dots not in (False, None, "same", "opposite") or (dots and dots != polarity):
        raise SpiceError(f"{e.id}: 仿真 polarity 与图中同名端 dots 不一致，请核对极性")
    # A positive explicit reference keeps isolated common-mode voltage defined;
    # never silently short the secondary to the primary return.
    if val["reference_resistance"] < MIN_TRANSFORMER_REFERENCE_OHM:
        raise SpiceError(f"{e.id}.reference_resistance 至少为 1G（数值参考，不是负载）")
    n = lambda pin: node(f"{e.id}.{pin}")
    gain = val["ratio"] * (1 if polarity == "same" else -1)
    primary = f"cd_{name}_p"
    lines = [f"* {name}: ideal transformer; ratio=Ns(total)/Np; polarity={polarity}"]
    if val["leakage_inductance"]:
        lines += [f"Vp_{name} {n('p1')} cd_{name}_pin 0",
                  f"* {name}: primary-referred leakage inductance in series with p1",
                  f"Lleak_{name} cd_{name}_pin {primary} {val['leakage_inductance']:.15g} IC=0"]
    else:
        lines.append(f"Vp_{name} {n('p1')} {primary} 0")
    if val["magnetizing_inductance"]:
        lines += [f"* {name}: primary-referred magnetising inductance across the ideal core",
                  f"Lmag_{name} {primary} {n('p2')} {val['magnetizing_inductance']:.15g} IC=0"]
    branches = [("secondary", "s1", "s2", gain)]
    if "sct" in e.pins:
        branches = [("upper", "s1", "sct", gain / 2), ("lower", "sct", "s2", gain / 2)]
    for winding, a, b, ratio in branches:
        internal, sense = f"cd_{name}_{winding}", f"V{winding}_{name}"
        lines += [f"{sense} {n(a)} {internal} 0",
                  f"E{winding}_{name} {internal} {n(b)} {primary} {n('p2')} {ratio:.15g}",
                  f"F{winding}_{name} {primary} {n('p2')} {sense} {-ratio:.15g}"]
    ref = n("sct" if "sct" in e.pins else "s2")
    if ref != n('p2'):
        lines += [f"* {name}: explicit numerical common-mode reference (not an isolation model)",
                  f"Rref_{name} {ref} {n('p2')} {val['reference_resistance']:.15g}"]
    return lines


def _element_names(devices):
    """Opt-in named decks use drawing IDs, never electrical branch numbers.

    SPICE's first letter selects the primitive. Preserve valid IDs where that
    letter is suitable; encode namespaced/unusual IDs injectively otherwise.
    Reserve all literal IDs first so an encoded name cannot shadow a real one.
    """
    prefixes = {"nmos": "S", "igbt": "Q", "vac": "V", "transformer": "T", "scr": "SCR"}
    names, used = {}, set()
    for e in devices:
        prefix = prefixes.get(e.kind, e.kind)
        suitable = e.id[:1].upper() == prefix or e.kind in ("igbt", "transformer", "scr")
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", e.id) and suitable:
            if e.id.lower() in used:
                raise SpiceError(f"{e.id}: SPICE 器件名忽略大小写后必须唯一，请修改元件 ID")
            names[e.id] = e.id
            used.add(e.id.lower())
    for e in sorted(devices, key=lambda e: e.id):
        if e.id in names:
            continue
        name = prefixes.get(e.kind, e.kind) + "_id_" + e.id.encode("utf-8").hex()
        while name.lower() in used:
            name += "_"
        names[e.id] = name
        used.add(name.lower())
    return names


def connected_layout(layout: Layout, profile="ieee-single"):
    """Resolve modules and stable identities for simulation exporters."""
    layout, rep, _ = resolved_layout(layout, profile)
    return layout, rep


def _module_simulation_source(layout):
    """Let the existing assembler handle modules, without malformed decorations.

    Only a private copy is cleaned. A region declaring stacked electrical
    instances is deliberately retained: its completeness must still be checked.
    Valid decoration geometry and all electrical/module data stay untouched.
    """
    from copy import deepcopy
    from .blocks import parse_block
    from .regions import RegionError, parse_callout, parse_region
    from .schema import element_problems
    raw = deepcopy(layout.to_dict())
    def clean(value):
        if not isinstance(value, dict):
            return  # the assembler reports malformed definitions itself
        for key, parse in (("callouts", parse_callout), ("blocks", parse_block),
                           ("regions", lambda d, i: parse_region(d, i, schema_only=True))):
            if key not in value:
                continue
            valid = []
            items = value[key]
            for i, item in enumerate(items if isinstance(items, list) else []):
                try:
                    parse(item, i)
                except RegionError:
                    usage = value.get("stack_usage", {})
                    if key != "regions" or not isinstance(item, dict) or not (
                            "stack" in item or (isinstance(usage, dict) and item.get("id") in usage)):
                        continue
                valid.append(item)
            value[key] = valid
        drawing = Layout.from_dict(value)
        if any(code == "bad-annotation" for code, _ in element_problems(drawing)):
            value["annotations"] = []
    clean(raw)
    spec = raw.get("modules")
    definitions = spec.get("definitions", {}) if isinstance(spec, dict) else {}
    for definition in definitions.values() if isinstance(definitions, dict) else ():
        if isinstance(definition, dict):
            clean(definition.get("layout"))
            partial = definition.get("partial")
            if isinstance(partial, dict):
                clean(partial.get("layout"))
    return Layout.from_dict(raw)


def resolved_layout(layout: Layout, profile="ieee-single"):
    """``connected_layout`` plus, for a table with modules, its Assembly: the
    Simulink / PLECS exporters draw its collapsed instances as subsystems."""
    rep, assembly = None, None
    if "modules" in layout.extra:
        from .modules import checked
        layout = _module_simulation_source(layout)
        completeness = simulation_completeness(layout)
        if not completeness["ok"]:
            raise SpiceError("仿真电路完整性检查未通过：" + "；".join(completeness["issues"]))
        assembly, rep, visible_report = checked(layout, get_profile(profile))
        visible_report = _simulation_report(visible_report)
        if not visible_report.ok:
            raise LayoutRejected(visible_report)
        rep = _checked_for_simulation(assembly.full, get_profile(profile), rep, connections=assembly.links)
        from .module_authoring import spice_identity
        layout, rep = spice_identity(layout, assembly.full, rep)
    rep = _connections(layout, profile, rep)
    return layout, rep, assembly


def circuit_deck(layout: Layout, profile="ieee-single") -> Deck:
    """The power circuit alone, without closed_loop: for checks that compare or
    validate the circuit and do not know where the control table lives."""
    if "closed_loop" in layout.extra:
        layout = Layout.from_dict({k: v for k, v in layout.to_dict().items() if k != "closed_loop"})
    return make_deck(layout, profile)


def make_deck(layout: Layout, profile="ieee-single", base_dir=None) -> Deck:
    """``base_dir``: where a closed_loop.control path is read from (the circuit file's folder)."""
    closed = layout.extra.get("closed_loop")
    layout, rep = connected_layout(layout, profile)
    # Terminals name nodes; they have no SPICE device or independent parameters.
    devices = [e for e in layout.elements if e.kind != "port"]
    sim = layout.extra.get("simulation", {})
    if not sim:
        raise SpiceError(NO_SIMULATION)
    _keys(sim, ("version", "ground", "components", "transient", "probes"))
    if type(sim["version"]) is not int or sim["version"] != 1:
        raise SpiceError("不支持的 simulation.version（当前为 1）")
    if not isinstance(sim["ground"], str) or sim["ground"] not in rep.nets:
        raise SpiceError("ground 必须指向当前电路的一个引脚，例如 Vin.a")
    ids = {e.id: e for e in devices}
    _keys(sim["components"], ids, label="components（必须与元件逐一对应）")
    _keys(sim["transient"], ("step", "stop", "max_step"), optional=("node_capacitance",), label="transient")
    tr = {k: number(v, f"transient.{k}") for k, v in sim["transient"].items()}
    if tr.get("node_capacitance", 0) < 0:
        raise SpiceError("transient.node_capacitance 必须 ≥ 0；0 表示禁用数值稳定电容")
    if not 0 < tr["max_step"] <= tr["step"] < tr["stop"]:
        raise SpiceError("时间设置要求 0 < 最大积分步长 ≤ 输出采样间隔 < 结束时间")
    if tr["stop"] / tr["max_step"] > 1_000_000:
        raise SpiceError("本次仿真超过 100 万个积分步长；请缩短时长或增大步长")
    loop = None
    if closed is not None:
        from .closed_loop import ClosedLoopError, load
        names_ = [p.get("name") for p in sim["probes"] if isinstance(p, dict)] \
            if isinstance(sim["probes"], list) else []
        try:
            loop = load(closed, base_dir, sim["components"],
                        {e.id: e.kind for e in devices}, names_)
        except ClosedLoopError as exc:
            raise SpiceError(str(exc)) from None
    driven = {}
    ground = rep.nets[sim["ground"]]
    nodes = {n: ("0" if n == ground else f"n{i}") for i, n in enumerate(sorted(set(rep.nets.values())), 1)}
    if "electrical" in layout.extra:
        from .electrical import pin_names
        stable = pin_names(layout.extra["electrical"])
        nodes = {net: ("0" if net == ground else stable[ref]) for ref, net in rep.nets.items()}
        # ngspice treats a node called gnd (any case) as ground: a net that is
        # not the simulation ground must not use that name in the deck
        taken = {name.lower() for name in nodes.values()}
        for net, name in nodes.items():
            if name != "0" and name.lower() == "gnd":
                alias = next(f"gnd_net{k or ''}" for k in range(len(nodes) + 1) if f"gnd_net{k or ''}" not in taken)
                nodes[net] = alias; taken.add(alias)
    node = lambda ref: nodes[rep.nets[ref]]
    mapping = layout.extra.get("model_mapping", {})
    prefixes = {"nmos": "S", "igbt": "Q", "vac": "V", "transformer": "T", "scr": "SCR"}
    names = {e.id: f"{prefixes.get(e.kind, e.kind)}{i}" for i, e in enumerate(devices, 1)}
    if "electrical" in layout.extra:
        branch_ids = layout.extra["electrical"]["branches"]
        names = _element_names(devices)
    lines = ["Circuit Drawer - ngspice simplified switching model",
             "* Generated from the validated JSON table. Do not use for device losses or ZVS.",
             "* Startup uses UIC with explicit L/C initial conditions; all units are SI.",
             "* PWM control is normalized 0/1, not a physical gate-voltage model."]
    for ref in sorted(rep.nets):
        lines.append(f"* pin {json.dumps(ref, ensure_ascii=True)} -> {node(ref)}")
    for e in devices:
        cfg = sim["components"][e.id]
        fields = [k for k, _, _ in FIELDS[e.kind]]
        defaults = {k: d for k, _, d in OPTIONAL_FIELDS.get(e.kind, [])}
        optional = list(defaults)
        extra = ["model", "polarity"] if e.kind == "transformer" else ["model"] if e.kind in ("D", *GATED) else []
        _keys(cfg, fields + extra,
              optional=[*optional, *(["wave"] if e.kind in (*VOLTAGE_SOURCES, "I") else [])], label=e.id)
        val = {k: number(cfg[k], f"{e.id}.{k}") for k in fields}
        for k in optional:
            val[k] = number(cfg.get(k, defaults[k]), f"{e.id}.{k}")
            if k in POSITIVE_OPTIONAL and val[k] <= 0:
                raise SpiceError(f"{e.id}.{k} 必须大于 0")
            if k != "phase_shift" and val[k] < 0:
                raise SpiceError(f"{e.id}.{k} 不能小于 0")
        body = (BODY_DIODE if all(val.get(k, v) == v for k, v in BODY_DIODE_VALUES.items()) else
                f"IS={val['diode_is']:.15g} N={val['diode_n']:.15g} RS={val['diode_rs']:.15g}")
        for k, v in val.items():
            if k in optional:
                continue
            if k not in ("initial", "dc", "delay", "rs", "vce0", "vf") and v <= 0:
                raise SpiceError(f"{e.id}.{k} 必须大于 0")
            if k in ("delay", "rs", "vce0", "vf") and v < 0:
                raise SpiceError(f"{e.id}.{k} 不能小于 0")
        n = lambda pin: node(f"{e.id}.{pin}")
        s = lambda key: f"{val[key]:.15g}"
        name = names[e.id]
        lines.append(f"* element {json.dumps(e.id, ensure_ascii=True)} -> {name}")
        if "electrical" in layout.extra:
            for ref, branch in branch_ids.items():
                if ref == e.id or (e.kind == "transformer" and ref.startswith(e.id + ":")):
                    lines.append(f"* branch {branch}: {json.dumps(ref, ensure_ascii=True)} -> {name}")
        if e.kind in (*VOLTAGE_SOURCES, "I"):
            if e.kind == "vac" and (not isinstance(cfg.get("wave"), dict) or cfg["wave"].get("kind") != "sine"):
                raise SpiceError(f"{e.id}: vac 必须显式填写 wave.kind=sine 及正弦参数，不能由图标猜测激励")
            pins = ("b", "a") if e.kind in VOLTAGE_SOURCES else ("a", "b")
            top, inner = n(pins[0]), []
            if e.kind in VOLTAGE_SOURCES and (val["series_resistance"] or val["series_inductance"]):
                # the internal impedance runs from the EMF to the + terminal
                top, end = f"cd_{name}_emf", n(pins[0])
                if val["series_inductance"]:
                    middle = f"cd_{name}_series" if val["series_resistance"] else top
                    inner.append(f"Lseries_{name} {middle} {end} {s('series_inductance')} IC=0")
                    end = middle
                if val["series_resistance"]:
                    inner.append(f"Rseries_{name} {top} {end} {s('series_resistance')}")
            lines.append(f"{name} {top} {n(pins[1])} {source_excitation(cfg, e.id, tr)}")
            lines += inner
        elif e.kind in ("R", "L", "C"):
            if mapping.get(e.kind, {}).get("model") == "parasitic":
                from .model_mapping import passive_lines
                lines.extend(passive_lines(e.kind, mapping[e.kind], name, n('a'), n('b'), s('value'), s('initial') if e.kind != 'R' else None))
                continue
            end = n('b')
            if e.kind == "L" and val["series_resistance"]:
                end = f"cd_{name}_series"
                lines.append(f"Rseries_{name} {end} {n('b')} {s('series_resistance')}")
            line = f"{name} {n('a')} {end} {s('value')}"
            lines.append(line + (f" IC={s('initial')}" if e.kind != "R" else ""))
        elif e.kind == "D":
            if cfg["model"] != "generic_diode":
                raise SpiceError(f"{e.id}: 仅支持 generic_diode 模型")
            if e.opts.get("zener") and not val["breakdown_voltage"]:
                raise SpiceError(f"{e.id}: 已画成稳压管，请在“分析 → SPICE 仿真…”填写大于 0 的 "
                                 "breakdown_voltage（击穿电压 Vz）；图标不会自动生成击穿参数")
            breakdown = ""
            if val["breakdown_voltage"]:
                # Share the exporter's 27 °C operating temperature; import at
                # call time because that exporter also uses make_deck validation.
                from .simulation_export import THERMAL_VOLTAGE
                nbv = val["zener_resistance"] * val["breakdown_current"] / THERMAL_VOLTAGE
                if not math.isfinite(nbv) or nbv <= 0:
                    raise SpiceError(f"{e.id}: 击穿系数超出数值范围，请核对 Izt 和 Zzt")
                breakdown = f" BV={s('breakdown_voltage')} IBV={s('breakdown_current')} NBV={nbv:.15g}"
            lines += [f"{name} {n('a')} {n('b')} md_{name}",
                      f".model md_{name} D(IS={s('is')} N={s('n')} RS={s('rs')}{breakdown})"]
        elif e.kind == "transformer":
            lines.extend(_transformer_lines(e, cfg, val, name, node))
        else:
            model = {"igbt": "simplified_igbt", "scr": "simplified_thyristor"}.get(e.kind, "ideal_switch")
            if cfg["model"] != model:
                raise SpiceError(f"{e.id}: {e.kind} 仅支持 {model} 模型")
            period = 1 / val["frequency"]
            # Duty is measured between the 50% rising/falling threshold crossings.
            width = val["duty"] * period - (val["rise"] + val["fall"]) / 2
            if not 0 < val["duty"] < 1:
                raise SpiceError(f"{e.id}: 占空比 {val['duty']:g} 超出范围，应大于 0、小于 1")
            if val["ron"] >= val["roff"]:
                raise SpiceError(f"{e.id}: 导通电阻 Ron = {val['ron']:g} Ω 必须小于关断电阻 Roff = {val['roff']:g} Ω")
            if not (width > 0 and width + val["rise"] + val["fall"] < period):
                raise SpiceError(f"{e.id}: 上升 / 下降时间（{val['rise']:g} s / {val['fall']:g} s）相对开关周期 "
                                 f"{period:g} s 和占空比 {val['duty']:g} 过长，导通或关断段放不下")
            if tr["step"] > period / 50 or tr["max_step"] > min(width, period * (1-val["duty"])) / 10:
                raise SpiceError(f"{e.id}: 时间步长过大；每周期至少 50 个采样点，导通/关断段至少 10 步")
            if val["delay"] + period > tr["stop"]:
                raise SpiceError(f"{e.id}: 结束时间须覆盖启动延迟之后至少一个周期")
            if e.kind == "scr":
                if loop and e.id in loop.driven:
                    raise SpiceError(f"{e.id}: 晶闸管暂不支持闭环驱动；用 delay 设触发时刻，phase_ramp 做触发角软启动")
                if val["latching_current"] and val["latching_current"] < val["holding_current"]:
                    raise SpiceError(f"{e.id}: latching_current 不能小于 holding_current（0 表示不要求擎住电流）")
                lines.extend(_pwm_lines(name, val, tr, e.id))
                lines.extend(_thyristor_lines(name, n('a'), n('b'), val))
                sr, sc = val["snubber_resistance"], val["snubber_capacitance"]
                if bool(sr) != bool(sc):
                    raise SpiceError(f"{e.id}: snubber_resistance / snubber_capacitance 须同时 > 0，或同时为 0 禁用")
                if sr:
                    # behind the anode sensor, so the terminal current includes it
                    lines += [f"Rsnub_{name} cd_{name}_a cd_{name}_snub {sr:.15g}",
                              f"Csnub_{name} cd_{name}_snub {n('b')} {sc:.15g} IC=0"]
                continue
            if loop and e.id in loop.driven:
                if val.get("phase_shift") or val.get("phase_ramp"):
                    raise SpiceError(f"{e.id}: 闭环驱动的开关不支持 phase_shift / phase_ramp")
                driven[e.id] = (name, val)
                averaged = {lo: up for up, (lo, _) in loop.averaged.items()}
                if e.id in loop.averaged or e.id in averaged:
                    if e.id in loop.averaged:
                        lower, out = loop.averaged[e.id]
                        pins = ("c", "e") if e.kind == "igbt" else ("d", "s")
                        low = ids[lower]
                        lpins = ("c", "e") if low.kind == "igbt" else ("d", "s")
                        if node(f"{e.id}.{pins[1]}") != node(f"{lower}.{lpins[0]}"):
                            raise SpiceError(f"{e.id} / {lower}: 不是同一桥臂（上管的 {pins[1]} 须接下管的 "
                                             f"{lpins[0]}），不能按 average 模型仿真")
                        from .closed_loop import average_leg, node as loop_node
                        lines.extend(average_leg(name, loop_node(out), n(pins[0]), n(pins[1]),
                                                 node(f"{lower}.{lpins[1]}")))
                    continue
            else:
                lines.extend(_pwm_lines(name, val, tr, e.id))
            sr, sc = val["snubber_resistance"], val["snubber_capacitance"]
            if bool(sr) != bool(sc):
                raise SpiceError(f"{e.id}: snubber_resistance / snubber_capacitance 须同时 > 0，或同时为 0 禁用")
            if "switch" in mapping:
                from .model_mapping import switch_lines
                positive, negative = (n('c'), n('e')) if e.kind == "igbt" else (n('d'), n('s'))
                lines.extend(switch_lines(mapping["switch"], e, name, positive, negative, val, body))
                a, b = negative, f"cd_{name}_channel"
            elif e.kind == "igbt":
                collector, emitter = f"cd_{name}_c", n('e')
                vce = f"v({collector},{emitter})"
                # A closed-loop drive is a smooth compare (closed_loop.modulator)
                # without solver breakpoints: the channel then follows it
                # continuously instead of switching at a threshold.
                on = (f"v(ctl_{name})" if e.id in driven else f"(v(ctl_{name}) > 0.5 ? 1 : 0)")
                lines += [f"* {name}: forward-only channel; separate e-to-c diode; finite bidirectional leakage",
                          f"Vsense_{name} {n('c')} {collector} 0",
                          f"Bchannel_{name} {collector} {emitter} I = {vce}/{s('roff')} + "
                          f"{on}*max({vce}-{s('vce0')},0)/{s('ron')}" if e.id in driven else
                          f"Bchannel_{name} {collector} {emitter} I = {vce}/{s('roff')} + "
                          f"(v(ctl_{name}) > 0.5 ? max({vce}-{s('vce0')},0)/{s('ron')} : 0)"]
                a, b = emitter, collector
            elif e.id in driven:
                vds = f"v({n('d')},{n('s')})"
                lines += [f"* {name}: conductance follows the closed-loop drive (0..1) continuously",
                          f"Bsw_{name} {n('d')} {n('s')} I = {vds}*({1 / val['ron']:.15g}*v(ctl_{name}) + "
                          f"{1 / val['roff']:.15g}*(1 - v(ctl_{name})))"]
                a, b = n('s'), n('d')
            else:
                lines += [f"{name} {n('d')} {n('s')} ctl_{name} 0 ms_{name}",
                          f".model ms_{name} SW(RON={s('ron')} ROFF={s('roff')} VT=0.5 VH=0)"]
                a, b = n('s'), n('d')
            if "switch" not in mapping and e.opts.get("diode", True) is not False:
                lines += [f"Dbody_{name} {a} {b} mbody_{name}",
                          f".model mbody_{name} D({body})"]
            if sr:
                # Keep the IGBT's zero-volt sensor outside every parallel
                # branch, so its terminal current includes the snubber too.
                lines += [f"Rsnub_{name} {b} cd_{name}_snub {sr:.15g}",
                          f"Csnub_{name} cd_{name}_snub {a} {sc:.15g} IC=0"]
            if val["output_capacitance"]:
                # linear Coss behind the sensor too, like the snubber
                lines.append(f"Coss_{name} {b} {a} {s('output_capacitance')} IC=0")
    if tr.get("node_capacitance", 0):
        lines.append("* Explicit numerical node capacitance; NOT device parasitics. Changes fast transients.")
        lines.extend(f"Cnum_{n} {n} 0 {tr['node_capacitance']:.15g}" for n in nodes.values() if n != "0")
    probes = sim["probes"]
    if not isinstance(probes, list) or not 1 <= len(probes) <= MAX_PROBES:
        raise SpiceError(f"probes 需要 1–{MAX_PROBES} 个测量通道")
    commands, vectors, columns, used, exprs = [], [], ["time_s"], set(), {}
    diode_currents, diode_feedback = [], {}
    for i, probe in enumerate(probes):
        if not isinstance(probe, dict):
            raise SpiceError("每个 probe 必须是对象")
        kind = probe.get("kind")
        fields = ("positive", "negative") if kind == "voltage" else ("element",)
        target = ids.get(probe.get("element")) if isinstance(probe.get("element"), str) else None
        winding = ("winding",) if kind == "current" and target and target.kind == "transformer" else ()
        _keys(probe, ("name", "kind", *fields, *winding), label=f"probe[{i}]")
        label = probe["name"]
        if not isinstance(label, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,40}", label) or label.lower() in used:
            raise SpiceError("通道名称必须是互不重复的英文字母/数字/下划线，以字母开头")
        used.add(label.lower())
        if kind == "voltage":
            if any(not isinstance(probe[k], str) or probe[k] not in rep.nets for k in fields):
                raise SpiceError(f"{label}: 测量引脚已不存在，请重新选择")
            a, b = node(probe["positive"]), node(probe["negative"])
            expr = " - ".join("0" if x == "0" else f"v({x})" for x in (a, b))
            unit = "V"
        elif kind in ("current", "drive"):
            e = ids.get(probe["element"]) if isinstance(probe["element"], str) else None
            if e is None or (kind == "current" and e.kind not in CURRENT_PROBE_KINDS) or (kind == "drive" and e.kind not in GATED):
                raise SpiceError(f"{label}: 电流通道支持 D/L/V/vac/igbt/scr/transformer；驱动通道支持 nmos/igbt/scr")
            expr = f"i({names[e.id]})" if kind == "current" else f"v(ctl_{names[e.id]})"
            if loop and kind == "current" and e.id in {*loop.averaged, *(lo for lo, _ in loop.averaged.values())}:
                raise SpiceError(f"{label}: {e.id} 按闭环 average 模型仿真，没有单管电流；改测桥臂所接电感的电流")
            if kind == "current" and e.kind in ("igbt", "scr"):
                expr = f"i(Vsense_{names[e.id]})"
            if kind == "current" and e.kind == "D":
                expr = f"@{names[e.id].lower()}[id]"
                diode_currents.append(expr)
                # @device[id] is a saved vector, not a B-source expression.
                # ngspice resolves i(D...) when a controller senses the diode.
                diode_feedback[label] = f"i({names[e.id]})"
            if e.kind == "transformer":
                accepted = ("primary", "upper", "lower") if "sct" in e.pins else ("primary", "secondary")
                if probe["winding"] not in accepted:
                    raise SpiceError(f"{label}.winding 必须为 {accepted}")
                part = "p" if probe["winding"] == "primary" else probe["winding"]
                expr = f"i(V{part}_{names[e.id]})"
            unit = "A" if kind == "current" else "1"
        else:
            raise SpiceError(f"不支持的 probe.kind: {kind}")
        exprs[label] = expr
        vector = f"cd_probe_{i}"
        commands.append(f"let {vector} = 0*time + ({expr})")
        vectors.append(vector)
        columns.append(f"{label}_{unit}")
    if loop:
        from .closed_loop import lines as loop_lines
        lines += loop_lines(loop, {**exprs, **diode_feedback}, driven, tr)
    if diode_currents:
        # Save device currents before transient analysis, without adding a
        # sensing source or changing decks that have no diode current probes.
        lines.append(".save all " + " ".join(dict.fromkeys(diode_currents)))
    lines += [f".tran {tr['step']:.15g} {tr['stop']:.15g} 0 {tr['max_step']:.15g} uic",
              ".options reltol=1e-5 abstol=1e-9 vntol=1e-6", ".control", "set noaskquit",
              "run", "linearize", *commands, "set wr_singlescale", "set wr_vecnames",
              "set numdgt=15", "wrdata waveforms.dat " + " ".join(vectors), "quit", ".endc", ".end"]
    return Deck("\n".join(lines) + "\n", columns, tr["step"], tr["stop"], loop.warnings if loop else [])


def executable(value=None) -> str | None:
    requested = value or os.environ.get("CIRCUIT_DRAWER_NGSPICE")
    if requested:
        return shutil.which(str(requested))
    return shutil.which("ngspice_con") or shutil.which("ngspice")


def _convergence_hint(deck: Deck, log: str) -> str:
    """Point at the documented startup checks; never change the deck itself."""
    if not re.search(r"(?i)timestep too small", log):
        return ""
    text = "\n提示：积分步长过小通常来自浮空节点或开关不连续。先核对初始条件、电源上升沿和 PWM 启动延迟"
    if "Cnum_" not in deck.text:
        text += "；也可在瞬态设置中填写数值稳定电容（如 100p–1n），并比较不同取值下关注的指标是否一致"
    return text + "。详见使用说明书 SPICE 仿真一章的收敛建议。"


def _simulate(deck: Deck, engine: str, stage: Path, timeout: float) -> None:
    (stage / "circuit.cir").write_text(deck.text, encoding="ascii")
    try:
        proc = subprocess.run([engine, "-n", "-b", "circuit.cir"], cwd=stage,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        raise SpiceError(f"ngspice 超过 {timeout:g} 秒，已停止；请缩短仿真时间") from exc
    log = proc.stdout.decode("utf-8", errors="replace")
    (stage / "simulation.log").write_text(log, encoding="utf-8")
    if proc.returncode or re.search(r"(?im)^\s*(?:error\b|fatal\b)|timestep too small|simulation interrupted", log):
        raise SpiceError("ngspice 仿真失败：\n" + log[-3500:] + _convergence_hint(deck, log))
    raw = stage / "waveforms.dat"
    if not raw.exists():
        raise SpiceError("ngspice 未生成波形：\n" + log[-3500:])
    with raw.open(encoding="ascii") as source, (stage / "waveforms.csv").open("w", encoding="utf-8", newline="") as target:
        writer = csv.writer(target)
        writer.writerow(deck.columns)
        next(source, None)  # wr_vecnames header
        count, previous, first = 0, -math.inf, None
        for line in source:
            parts = line.split()
            if not parts:
                continue
            try:
                values = [float(v) for v in parts]
            except ValueError as exc:
                raise SpiceError("ngspice 波形包含非数值数据") from exc
            if len(values) != len(deck.columns) or not all(map(math.isfinite, values)) or values[0] <= previous:
                raise SpiceError("ngspice 波形列数、有限值或时间顺序无效")
            if count and not math.isclose(values[0] - previous, deck.step, rel_tol=1e-5, abs_tol=deck.step*1e-5):
                raise SpiceError("ngspice 输出时间轴不是设置的均匀采样")
            first = values[0] if first is None else first
            previous = values[0]
            count += 1
            writer.writerow(parts)
        if (count < 2 or abs(first) > deck.step * 0.01
                or not deck.stop - deck.step * 1.01 <= previous <= deck.stop + deck.step * 0.01):
            raise SpiceError("ngspice 仿真未覆盖设置的完整时间范围")


def export_spice(layout: Layout, out_dir: str | Path, stem="circuit", *,
                 profile="ieee-single", run=False, ngspice=None, timeout=120, base_dir=None) -> dict:
    """Validate everything before creating output; stage runs to prevent stale CSV."""
    deck = make_deck(layout, profile, base_dir)
    if not isinstance(stem, str) or not re.fullmatch(r"[\w-]+", stem):
        raise SpiceError("输出名称只能包含字母、数字、下划线或连字符")
    engine = executable(ngspice) if run else None
    if run and not engine:
        raise SpiceError("未找到 ngspice。先导出 .cir，或安装后设置 CIRCUIT_DRAWER_NGSPICE 为可执行文件路径")
    destination = Path(out_dir).resolve()
    with tempfile.TemporaryDirectory(prefix="cd_spice_") as temp:
        stage = Path(temp)
        (stage / "circuit.cir").write_text(deck.text, encoding="ascii")
        sources = {"cir": stage / "circuit.cir"}
        if run:
            _simulate(deck, engine, stage, timeout)
            sources.update(csv=stage / "waveforms.csv", log=stage / "simulation.log")
        destination.mkdir(parents=True, exist_ok=True)
        files = []
        for ext, source in sources.items():
            target = destination / f"{stem}.{ext}"
            shutil.copyfile(source, target)
            files.append(str(target))
    return {"files": files, "out_dir": str(destination), "columns": deck.columns,
            "ran": bool(run), "notes": [LIMIT_NOTE, *deck.notes]}
