"""DC operating-point output impedance from the existing averaged SPICE circuit.

The test current flows out of the selected voltage port. Zout = -dV/dI,
including the load already drawn in the table. Analysis settings are supplied
by the caller and recorded alongside the results, outside the coordinate table.
"""
from __future__ import annotations

import cmath
from copy import deepcopy
import csv
import json
import math
from pathlib import Path
import re
import tempfile
import uuid

from . import spice
from .closed_loop import controller, load, node
from .layout import Layout
from .waveforms import parse_csv

INPUTS = {
    "f_min": ("起始频率 (Hz)", "10"),
    "f_max": ("结束频率 (Hz)", "10k"),
    "points": ("每十倍频程点数", "80"),
    "amplitude": ("时域注入电流峰值 (A)", "10m"),
    "settle": ("初始稳定时间 (s)", "200m"),
}
NOTE = "直流工作点平均模型，保留图中负载。输出阻抗 Zout = −ΔV/ΔI，电流正向为从测量正端流向负端。"
SAMPLES_PER_CYCLE = 200  # resolve phase to well below a degree after linearize
MEASURE_CYCLES = 4
SETTLE_CYCLES = 12
MAX_SAMPLES = 500_000
STEADY_RTOL = 1e-3
STEADY_ATOL = 1e-6
COMPARISON_RTOL = 0.02  # engineering comparison tolerance, including finite injection amplitude
WINDOW_DRIFT_RTOL = 0.01  # adjacent whole-cycle windows must agree to 1 percent
AVERAGE_FREQUENCY_RATIO = 0.1  # keep measurements below switching dynamics omitted by the average model


def settings(raw=None):
    raw = {} if raw is None else raw
    if not isinstance(raw, dict) or set(raw) - set(INPUTS):
        raise ValueError("阻抗设置允许 f_min、f_max、points、amplitude、settle")
    cfg = {k: spice.number(raw.get(k, value), k) for k, (_, value) in INPUTS.items()}
    if not 0 < cfg["f_min"] < cfg["f_max"] or cfg["f_max"] / cfg["f_min"] > 1e6:
        raise ValueError("频率须满足 0 < f_min < f_max，范围最多六个十倍频程")
    if not cfg["points"].is_integer() or not 5 <= cfg["points"] <= 200:
        raise ValueError("每十倍频程点数须为 5～200 的整数")
    if cfg["amplitude"] <= 0 or not 0 < cfg["settle"] <= 10:
        raise ValueError("注入电流须大于 0，初始稳定时间须在 0～10 s 之间")
    if cfg["settle"] * cfg["f_max"] * 40 > MAX_SAMPLES:
        raise ValueError("稳定时间与最高频率的组合超过 50 万个采样点，请调整设置")
    return cfg


def prepare(lay, probe, cfg, profile, base_dir):
    # A reference ramp describes startup. The AC operating point uses its final value.
    table = deepcopy(lay.to_dict())
    sim = table.get("simulation", {})
    if not isinstance(sim, dict):
        raise ValueError("请选择 simulation.probes 中的电压通道作为输出端口")
    probes = sim.get("probes", [])
    if not isinstance(probes, list) or any(not isinstance(p, dict) for p in probes):
        raise ValueError("请选择 simulation.probes 中的电压通道作为输出端口")
    port = next((p for p in probes if p.get("name") == probe and p.get("kind") == "voltage"), None)
    if port is None:
        raise ValueError("请选择 simulation.probes 中的电压通道作为输出端口")
    components = sim.get("components")
    if not isinstance(components, dict) or any(not isinstance(c, dict) for c in components.values()):
        raise ValueError("simulation.components 必须是元件参数对象")
    closed = table.get("closed_loop")
    if not closed:
        raise ValueError("输出阻抗需要 closed_loop 平均桥臂模型，可打开 examples/impedance/buck_output.json")
    loop = load(closed, base_dir, sim["components"], {e.id: e.kind for e in lay.elements},
                [p.get("name") for p in probes])
    if loop.digital:
        raise ValueError("digital 请使用离散环路分析与瞬态仿真，当前输出阻抗 AC 求解使用连续时间控制器")
    allowed = {"R", "L", "C", "V", "I", "nmos", "pmos", "igbt", "port"}
    if any(e.kind not in allowed for e in lay.elements):
        raise ValueError("本阶段支持直流 RLC 电路与平均桥臂，请使用同步 Buck 示例")
    switches = {e.id for e in lay.elements if e.kind in spice.GATED}
    averaged = set(loop.averaged) | {lo for lo, _ in loop.averaged.values()}
    if not averaged or switches != averaged:
        raise ValueError("全部开关须显式配置为 closed_loop 的 average 上下桥臂")
    if any(st.kind == "fn" for st in loop.plan.steps):
        raise ValueError("当前输出阻抗支持直流控制器，dq 旋转坐标模型请使用“dq 阻抗矩阵”分析")
    if any(c.get("wave") for c in sim["components"].values()):
        raise ValueError("电源须使用固定 dc 值，请先移除 simulation.components 中的 wave 激励")
    if any(cfg["f_max"] > spice.number(sim["components"][s]["frequency"], s) * AVERAGE_FREQUENCY_RATIO for s in switches):
        raise ValueError("平均模型最高分析频率须不超过最低开关频率的十分之一")
    for item in closed["inputs"].values():
        item.pop("ramp", None)
    deck = spice.make_deck(Layout.from_dict(table), profile, base_dir)
    positive, negative = (deck.pin_nodes[port[k]] for k in ("positive", "negative"))
    if positive == negative:
        raise ValueError("输出端口的正负引脚须属于不同电气节点")
    body = re.split(r"(?m)^\.tran\s", deck.text, maxsplit=1)[0]
    body = re.sub(r" IC=\S+", "", body)
    body = "\n".join(line for line in body.splitlines() if not line.startswith("* Startup")) + "\n"
    volt = " - ".join("0" if n == "0" else f"v({n})" for n in (positive, negative))
    nodes = sorted({n for line in body.splitlines()[1:] if re.match(r"^[RCLVBI]\S* ", line)
                    for n in line.split()[1:3]} - {"0"})
    return body, positive, negative, volt, nodes, loop


def _control(commands, *, reltol=1e-7):
    return "\n".join([f".options reltol={reltol:g} abstol=1e-12 vntol=1e-9", ".control",
                      "set noaskquit", "set wr_singlescale", "set wr_vecnames", "set numdgt=15",
                      *commands, "quit", ".endc", ".end", ""])


def _read(path, columns):
    lines = path.read_text(encoding="ascii").splitlines()[1:]
    rows = [[float(v) for v in line.split()] for line in lines if line.strip()]
    if not rows or any(len(row) != columns or not all(map(math.isfinite, row)) for row in rows):
        raise ValueError("ngspice 阻抗结果的列数或数值无效")
    return rows


def _steady(body, nodes, cfg, engine, stage):
    """Seed DC Newton iteration from a settled transient, then solve DC afresh."""
    step = min(cfg["settle"] / 2000, 1 / (40 * cfg["f_max"]))
    # Check adaptive solver points so uniform output sampling cannot hide oscillation.
    commands = [f"tran {step:.15g} {cfg['settle']:.15g} 0 {step:.15g} uic"]
    for i, n in enumerate(nodes):
        commands += [f"let seed{i} = v({n})[length(time)-1]",
                     f"let drift{i} = vecmax(v({n})[floor(length(time)*0.8),length(time)-1]) - "
                     f"vecmin(v({n})[floor(length(time)*0.8),length(time)-1])"]
    commands += ["wrdata equilibrium.dat " + " ".join(f"seed{i} drift{i}" for i in range(len(nodes)))]
    spice.run_deck(spice.Deck(body + _control(commands), [], step, cfg["settle"]), engine, stage, 120)
    values = _read(stage / "equilibrium.dat", 1 + 2 * len(nodes))[0][1:]
    seeds = dict(zip(nodes, values[::2]))
    if any(drift > STEADY_ATOL + STEADY_RTOL * abs(value) for value, drift in zip(values[::2], values[1::2])):
        raise ValueError("工作点仍在漂移，请增加初始稳定时间或核对闭环参数")
    return seeds, "".join(f".nodeset v({n})={v:.15g}\n" for n, v in seeds.items())


def phasor(values, frequency, step):
    """Cosine-reference phasor over an integer number of uniform sample cycles."""
    mean = sum(values) / len(values)
    return 2 / len(values) * sum((v - mean) * cmath.exp(-2j * math.pi * frequency * step * i)
                                 for i, v in enumerate(values))


def _compare(body, source, volt, frequency, expected, cfg, engine, stage):
    stage.mkdir()
    step = 1 / (SAMPLES_PER_CYCLE * frequency)
    settle = max(cfg["settle"], SETTLE_CYCLES / frequency)
    cycles = math.ceil(settle * frequency) + 2 * MEASURE_CYCLES
    stop = cycles / frequency
    if stop / step > MAX_SAMPLES:
        raise ValueError("时域对照超过 50 万个采样点，请缩短稳定时间或降低结束频率")
    amp = cfg["amplitude"]
    injection = f"{source} DC 0 SIN(0 {amp:.15g} {frequency:.15g})\n"
    commands = ["run", "linearize", f"let voltage = {volt}",
                f"let injection = {amp:.15g}*sin(2*pi*{frequency:.15g}*time)",
                "wrdata waveforms.dat voltage injection"]
    text = body + injection + f".tran {step:.15g} {stop:.15g} 0 {step:.15g}\n" + _control(commands)
    deck = spice.Deck(text, ["time_s", "voltage_V", "injection_A"], step, stop)
    spice._simulate(deck, engine, stage, 120)
    data = parse_csv((stage / "waveforms.csv").read_bytes())
    # Exclude the final endpoint so each window contains exactly N whole cycles.
    count = SAMPLES_PER_CYCLE * MEASURE_CYCLES
    voltage = data.columns["voltage_V"][-2 * count - 1:-1]
    current = data.columns["injection_A"][-2 * count - 1:-1]
    earlier = -phasor(voltage[:count], frequency, step) / phasor(current[:count], frequency, step)
    measured = -phasor(voltage[count:], frequency, step) / phasor(current[count:], frequency, step)
    drift = abs(measured - earlier) / max(abs(measured), 1e-15)
    error = abs(measured - expected) / max(abs(expected), 1e-15)
    return {"frequency_hz": frequency, "real_ohm": measured.real, "imag_ohm": measured.imag,
            "relative_error": error, "window_drift": drift,
            "consistent": error <= COMPARISON_RTOL and drift <= WINDOW_DRIFT_RTOL}


def plot_svg(frequency, values, compared, *, ratio=False, determinant=False):
    """Bode magnitude/phase and the positive-frequency complex impedance locus."""
    dimensionless = ratio or determinant
    critical = 0 if determinant else -1
    magnitudes = [20 * math.log10(max(abs(z), 1e-30)) for z in values]
    phases = []
    for z in values:
        phase = math.degrees(cmath.phase(z))
        if phases:
            phase += 360 * round((phases[-1] - phase) / 360)
        phases.append(phase)
    panels = [("幅值 (dBΩ)", [math.log10(f) for f in frequency], magnitudes, 55, 45, 470, 210),
              ("相位 (°)", [math.log10(f) for f in frequency], phases, 55, 320, 470, 210),
              ("Zout Nyquist · 正频率", [z.real for z in values], [z.imag for z in values], 620, 85, 400, 400)]
    out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1100 590" role="img" aria-label="Output impedance Bode and Nyquist">',
           '<rect width="1100" height="590" fill="white"/><g font-family="sans-serif" font-size="13" fill="#222">']
    for index, (title, xs, ys, left, top, width, height) in enumerate(panels):
        markers = []
        for record in compared:
            z = complex(record["real_ohm"], record["imag_ohm"])
            x = math.log10(record["frequency_hz"]) if index < 2 else z.real
            y = z.imag
            if index == 0:
                y = 20 * math.log10(max(abs(z), 1e-30))
            if index == 1:
                nearest = min(range(len(frequency)), key=lambda i: abs(frequency[i] - record["frequency_hz"]))
                y = math.degrees(cmath.phase(z))
                y += 360 * round((phases[nearest] - y) / 360)
            markers.append((x, y))
        bounds_x = [*xs, *(x for x, _ in markers)]
        bounds_y = [*ys, *(y for _, y in markers)]
        if dimensionless and index == 2:
            bounds_x.append(critical)
            bounds_y.extend([-y for y in ys] + [0])
        xmin, xmax, ymin, ymax = min(bounds_x), max(bounds_x), min(bounds_y), max(bounds_y)
        if index == 2:
            span = max(xmax - xmin, ymax - ymin, 1e-12) * 1.1
            midx, midy = (xmin + xmax) / 2, (ymin + ymax) / 2
            xmin, xmax, ymin, ymax = midx - span / 2, midx + span / 2, midy - span / 2, midy + span / 2
        else:
            pad = max((ymax - ymin) * 0.08, 1)
            ymin, ymax = ymin - pad, ymax + pad
        xf = lambda x: left + (x - xmin) / (xmax - xmin) * width
        yf = lambda y: top + height - (y - ymin) / (ymax - ymin) * height
        out.append(f'<text x="{left}" y="{top-16}">{title}</text>')
        for tick in range(5):
            x, y = xmin + (xmax - xmin) * tick / 4, ymin + (ymax - ymin) * tick / 4
            label = f"{10**x:.3g} Hz" if index < 2 else f"{x:.3g}"
            out += [f'<path d="M {xf(x):.2f} {top} v {height} M {left} {yf(y):.2f} h {width}" stroke="#ddd"/>',
                    f'<text x="{xf(x):.2f}" y="{top+height+20}" text-anchor="middle">{label}</text>',
                    f'<text x="{left-8}" y="{yf(y)+4:.2f}" text-anchor="end">{y:.3g}</text>']
        points = " ".join(f"{xf(x):.2f},{yf(y):.2f}" for x, y in zip(xs, ys))
        out.append(f'<polyline points="{points}" fill="none" stroke="#176ca4" stroke-width="1.8"/>')
        if dimensionless and index == 2:
            mirror = " ".join(f"{xf(x):.2f},{yf(-y):.2f}" for x, y in zip(xs, ys))
            out.append(f'<polyline points="{mirror}" fill="none" stroke="#176ca4" stroke-width="1.8" stroke-dasharray="5 4"/>')
            out.append(f'<path d="M {xf(critical)-5:.2f} {yf(0)-5:.2f} l 10 10 m -10 0 l 10 -10" stroke="#b12d31" stroke-width="2"/>')
            out.append(f'<text x="{xf(critical)+8:.2f}" y="{yf(0)-8:.2f}">{"0" if determinant else "−1"}</text>')
        for x, y in markers:
            out.append(f'<circle cx="{xf(x):.2f}" cy="{yf(y):.2f}" r="4" fill="#ca5e26"/>')
        if index == 2:
            out += [f'<text x="{left+width/2}" y="{top+height+45}" text-anchor="middle">Re(Zout) / Ω</text>',
                    f'<text x="{left}" y="{top-36}">Im(Zout) / Ω</text>',
                    f'<circle cx="{xf(xs[0]):.2f}" cy="{yf(ys[0]):.2f}" r="4" fill="#176ca4"/>',
                    f'<text x="{left}" y="560">起点 {frequency[0]:g} Hz → 终点 {frequency[-1]:g} Hz</text>']
    out += ['<text x="55" y="580">AC 扫频（蓝色） / 时域注入（橙色）</text>', '</g></svg>']
    svg = "\n".join(out)
    if dimensionless:
        svg = (svg.replace("Zout", "Tm").replace("dBΩ", "dB").replace(" / Ω", "")
               .replace("Output impedance", "Impedance ratio")
               .replace("Tm Nyquist · 正频率", "Tm Nyquist · 正负频率")
               .replace("AC 扫频（蓝色） / 时域注入（橙色）", "实线正频率 / 虚线负频率 / 红叉 −1，显示范围见横轴"))
    if determinant:
        svg = svg.replace("Tm", "det(I+T)").replace("红叉 −1", "红叉 0").replace("Impedance ratio", "Return determinant")
    return svg


def run(lay, out_dir, *, probe, options=None, compare=False, profile="double", ngspice=None, base_dir=None):
    cfg = settings(options)
    if compare and (math.ceil(cfg["settle"] * cfg["f_max"]) + 2 * MEASURE_CYCLES) * SAMPLES_PER_CYCLE > MAX_SAMPLES:
        raise ValueError("时域对照超过 50 万个采样点，请缩短稳定时间或降低结束频率")
    snapshot = deepcopy(lay)
    closed = snapshot.extra.get("closed_loop")
    # Resolve a referenced controller once, before solving, for reproducible results.
    if isinstance(closed, dict) and isinstance(closed.get("control"), str):
        closed["control"] = controller(closed["control"], base_dir)[0]
    body, positive, negative, volt, nodes, loop = prepare(snapshot, probe, cfg, profile, base_dir)
    engine = spice.executable(ngspice)
    if not engine:
        raise ValueError("未找到 ngspice，请安装并设置 CIRCUIT_DRAWER_NGSPICE")
    destination = Path(out_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".impedance-", dir=destination) as temp:
        stage = Path(temp)
        warmup = stage / "warmup"
        warmup.mkdir()
        seeds, initial = _steady(body, nodes, cfg, engine, warmup)
        body += initial
        source = f"I_cd_impedance {positive} {negative}"
        ac = stage / "ac"
        ac.mkdir()
        measurements = [f"let zr = real(-({volt}))", f"let zi = imag(-({volt}))",
                        "wrdata impedance.dat zr zi"]
        sweep = [f"ac dec {int(cfg['points'])} {cfg['f_min']:.15g} {cfg['f_max']:.15g}", *measurements]
        if math.log10(cfg['f_max'] / cfg['f_min']) * cfg['points'] < 1:
            # ngspice can omit the second endpoint of a very narrow LIN 2 sweep.
            sweep = [f"ac lin 1 {cfg['f_min']:.17g} {cfg['f_min']:.17g}", *measurements,
                     "set appendwrite", "unset wr_vecnames",
                     f"ac lin 1 {cfg['f_max']:.17g} {cfg['f_max']:.17g}", *measurements]
        commands = ["op", *[f"let op{i} = v({n})" for i, n in enumerate(nodes)],
                    "wrdata operating-point.dat " + " ".join(f"op{i}" for i in range(len(nodes))),
                    *sweep]
        spice.run_deck(spice.Deck(body + source + " DC 0 AC 1\n" + _control(commands), [], 0, 0), engine, ac, 120)
        if "Transient op" in (ac / "simulation.log").read_text(encoding="utf-8"):
            raise ValueError("直流工作点求解失败，请核对闭环参数与初始稳定时间")
        operating = dict(zip(nodes, _read(ac / "operating-point.dat", 1 + len(nodes))[0][1:]))
        dc_voltage = operating.get(positive, 0) - operating.get(negative, 0)
        observed = {positive, negative, *(node(key) for key in loop.outputs)} - {"0"}
        if any(abs(operating[n] - seeds[n]) > STEADY_ATOL + STEADY_RTOL * abs(operating[n]) for n in observed):
            raise ValueError("直流工作点与时域稳定值不一致，请核对闭环参数")
        rows = _read(ac / "impedance.dat", 3)
        freq = [row[0] for row in rows]
        if (len(freq) < 2 or any(a >= b for a, b in zip(freq, freq[1:]))
                or not math.isclose(freq[0], cfg["f_min"], rel_tol=1e-6)
                or not cfg["f_max"] / 10 ** (1 / cfg["points"]) * (1 - 1e-6) <= freq[-1] <= cfg["f_max"] * (1 + 1e-6)):
            raise ValueError("ngspice 扫频的频率轴无效")
        values = [complex(*row[1:]) for row in rows]
        comparisons = []
        if compare:
            for index in sorted({0, len(rows) // 2, len(rows) - 1}):
                comparisons.append(_compare(body, source, volt, freq[index], values[index], cfg, engine,
                                            stage / f"injection-{index}"))
        result = {"probe": probe, "settings": cfg, "note": NOTE, "notes": loop.warnings,
                  "operating_voltage_v": dc_voltage,
                  "duty": {key: operating[node(key)] for key in loop.outputs},
                  "frequency_hz": freq, "real_ohm": [v.real for v in values],
                  "imag_ohm": [v.imag for v in values], "comparison": comparisons}
        with (stage / "impedance.csv").open("w", encoding="utf-8", newline="") as target:
            writer = csv.writer(target)
            writer.writerow(["frequency_hz", "real_ohm", "imag_ohm", "magnitude_db_ohm", "phase_deg"])
            writer.writerows([f, z.real, z.imag, 20 * math.log10(max(abs(z), 1e-30)), math.degrees(cmath.phase(z))]
                             for f, z in zip(freq, values))
        svg = plot_svg(freq, values, comparisons)
        (stage / "impedance.svg").write_text(svg, encoding="utf-8")
        (stage / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        snapshot.save(stage / "source.json")
        final = destination / ("run-" + uuid.uuid4().hex[:12])
        stage.rename(final)
    return {**result, "svg": svg, "csv": (final / "impedance.csv").read_text(encoding="utf-8"), "out_dir": str(final),
            "files": [str(final / name) for name in ("impedance.csv", "impedance.svg", "result.json", "source.json")]}
