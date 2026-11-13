"""Two-axis voltage-injection identification in a fixed, grid-aligned dq frame."""
from copy import deepcopy
import csv
import json
import math
from pathlib import Path
import re
import tempfile
import uuid

from . import impedance, spice
from .closed_loop import controller, load
from .control.functions import expressions
from .waveforms import parse_csv

INPUTS = {
    "f_min": ("起始频率 (Hz)", "10"),
    "f_max": ("结束频率 (Hz)", "1k"),
    "points": ("每十倍频程点数", "3"),
    "amplitude": ("dq 注入电压峰值 (V)", "2"),
    "settle": ("初始稳定时间 (s)", "300m"),
    "max_step": ("最大采样与积分步长 (s)", "20u"),
}
NOTE = "固定电网同步坐标系，以 A 相源的基准相位定义 d 轴。电流正向为变流器流向电网，ΔVdq = −Zdq·ΔIdq。"
AXES = ("dd", "dq", "qd", "qq")
MAX_CONDITION = 1e6  # avoid inversion that magnifies measured errors by six orders
STEADY_ATOL = 1e-4  # V/A floor for the nominal dq port's steady-state check
ENTRY_SCALE_FLOOR = 1e-3  # near-zero cross terms use 0.1% of the matrix norm as their comparison scale
SOLVER_RTOL = 1e-6  # checked against the analytic dq model, including long PLL runs


def settings(raw=None):
    raw = {} if raw is None else raw
    if not isinstance(raw, dict) or set(raw) - set(INPUTS):
        raise ValueError("dq 设置允许 f_min、f_max、points、amplitude、settle、max_step")
    cfg = {key: spice.number(raw.get(key, default), key) for key, (_, default) in INPUTS.items()}
    if not 0 < cfg["f_min"] < cfg["f_max"] or cfg["f_max"] / cfg["f_min"] > 1000:
        raise ValueError("dq 频率须满足 0 < f_min < f_max，范围最多三个十倍频程")
    if not cfg["points"].is_integer() or not 1 <= cfg["points"] <= 10:
        raise ValueError("dq 每十倍频程点数须为 1～10 的整数")
    if cfg["amplitude"] <= 0 or not 0 < cfg["settle"] <= 10 or cfg["max_step"] <= 0:
        raise ValueError("dq 注入幅值和步长须大于 0，稳定时间须在 0～10 s 之间")
    count = math.ceil(math.log10(cfg["f_max"] / cfg["f_min"]) * cfg["points"])
    cfg["frequency_hz"] = [cfg["f_min"] * (cfg["f_max"] / cfg["f_min"]) ** (i / count) for i in range(count + 1)]
    return cfg


def prepare(lay, sources, cfg, profile, base_dir):
    if not isinstance(sources, (list, tuple)) or len(sources) != 3 or any(not isinstance(s, str) for s in sources) or len(set(sources)) != 3:
        raise ValueError("请按 A、B、C 顺序选择三个不同的电网正弦电压源")
    snapshot = deepcopy(lay)
    closed = snapshot.extra.get("closed_loop")
    if not isinstance(closed, dict):
        raise ValueError("dq 阻抗需要 closed_loop 平均桥臂与控制器，请载入 grid_dq_impedance 模板")
    closed["control"] = controller(closed.get("control"), base_dir)[0]
    deck = spice.make_deck(snapshot, profile, base_dir)
    power, _ = spice.connected_layout(snapshot, profile)
    sim = power.extra["simulation"]
    kinds = {e.id: e.kind for e in power.elements}
    loop = load(closed, base_dir, sim["components"], kinds, [p["name"] for p in sim["probes"]])
    if loop.digital:
        raise ValueError("digital 请使用离散环路分析与瞬态仿真，当前 dq 阻抗 AC 求解使用连续时间控制器")
    switches = {key for key, kind in kinds.items() if kind in spice.GATED}
    averaged = set(loop.averaged) | {lo for lo, _ in loop.averaged.values()}
    if not averaged or switches != averaged:
        raise ValueError("dq 阻抗要求全部开关显式采用 closed_loop 的 average 上下桥臂")
    if any(kind not in {"R", "L", "C", "V", "vac", "I", "nmos", "pmos", "igbt", "port"} for kind in kinds.values()):
        raise ValueError("dq 阻抗支持 RLC 滤波器、电源与平均桥臂")
    waves = []
    for source in sources:
        part = sim["components"].get(source, {})
        wave = part.get("wave", {})
        if kinds.get(source) not in spice.VOLTAGE_SOURCES or not isinstance(wave, dict) or wave.get("kind") != "sine":
            raise ValueError("dq 端口须选择明确配置 sine 激励的电压源")
        value = {key: spice.number(wave[key], source) for key in ("offset", "amplitude", "frequency", "delay", "phase")}
        if value["offset"] or value["delay"]:
            raise ValueError("dq 电网正弦源的 offset 和 delay 须为 0")
        waves.append(value)
    grid = waves[0]
    for i, wave in enumerate(waves):
        angle = math.radians(wave["phase"] - grid["phase"] + 120 * i)
        if (not math.isclose(wave["frequency"], grid["frequency"], rel_tol=1e-9)
                or not math.isclose(wave["amplitude"], grid["amplitude"], rel_tol=1e-9)
                or abs(math.sin(angle / 2)) > 1e-8):
            raise ValueError("dq 电网须为同频同幅的平衡 ABC 正序，B 相滞后 A 相 120°")
    neutral = {deck.pin_nodes[s + ".a"] for s in sources}
    terminals = {deck.pin_nodes[s + ".b"] for s in sources}
    if len(neutral) != 1 or len(terminals) != 3 or neutral & terminals:
        raise ValueError("dq 三相电压源须共用负端中性点，三个正端须为不同节点")
    if any(part.get("wave") for name, part in sim["components"].items() if name not in sources):
        raise ValueError("dq 扫频的其他独立电源须使用固定 dc 值")
    if cfg["amplitude"] > grid["amplitude"] * 0.05:
        raise ValueError("dq 注入电压峰值须不超过电网相电压峰值的 5%")
    if any(cfg["f_max"] > spice.number(sim["components"][key]["frequency"], key) * impedance.AVERAGE_FREQUENCY_RATIO for key in switches):
        raise ValueError("平均模型最高分析频率须不超过最低开关频率的十分之一")
    grid = {"frequency_hz": grid["frequency"], "peak_v": grid["amplitude"],
            "angle_rad": math.radians(grid["phase"]) - math.pi / 2}
    return snapshot, grid, deck.notes


def inverse(matrix):
    a, b, c, d = matrix
    determinant = a * d - b * c
    norm = math.sqrt(sum(abs(v) ** 2 for v in matrix))
    if not norm or abs(determinant) <= norm * norm / MAX_CONDITION:
        raise ValueError("dq 矩阵接近奇异，请核对端口、扰动幅值与测量频率")
    result = [d / determinant, -b / determinant, -c / determinant, a / determinant]
    condition = norm * math.sqrt(sum(abs(v) ** 2 for v in result))
    return result, condition


def matrix_from_columns(columns):
    # Each experiment measures both axes, including cross-coupling at the port.
    voltage = [columns[0]["vd"], columns[1]["vd"], columns[0]["vq"], columns[1]["vq"]]
    current = [columns[0]["id"], columns[1]["id"], columns[0]["iq"], columns[1]["iq"]]
    vi, vc = inverse(voltage)
    admittance = [-sum(current[row * 2 + k] * vi[k * 2 + col] for k in range(2))
                  for row in range(2) for col in range(2)]
    z, yc = inverse(admittance)
    return z, admittance, max(vc, yc)


def _timing(frequency, grid, cfg, refine):
    max_step = min(cfg["max_step"], 1 / (impedance.SAMPLES_PER_CYCLE * grid["frequency_hz"]))
    if max_step * frequency < 1 / impedance.MAX_SAMPLES:
        raise ValueError("dq 单次仿真超过 50 万采样点，请提高起始频率、缩短稳定时间或增大步长")
    per_cycle = max(impedance.SAMPLES_PER_CYCLE, math.ceil(1 / (max_step * frequency))) * refine
    step = 1 / (per_cycle * frequency)
    settle_cycles = math.ceil(max(cfg["settle"], impedance.SETTLE_CYCLES / frequency) * frequency)
    start_cycles = math.ceil(cfg["settle"] * frequency)
    stop = (start_cycles + settle_cycles + 2 * impedance.MEASURE_CYCLES) / frequency
    if stop / step > impedance.MAX_SAMPLES:
        raise ValueError("dq 单次仿真超过 50 万采样点，请提高起始频率、缩短稳定时间或增大步长")
    return step, stop, per_cycle * impedance.MEASURE_CYCLES, start_cycles / frequency


def _deck(snapshot, sources, grid, frequency, cfg, axis, amplitude, refine, profile, stage):
    stage.mkdir()
    step, stop, count, start = _timing(frequency, grid, cfg, refine)
    deck = spice.make_deck(snapshot, profile)
    body = re.split(r"(?m)^\.tran\s", deck.text, maxsplit=1)[0]
    theta = f"({2 * math.pi * grid['frequency_hz']:.17g}*time+({grid['angle_rad']:.17g}))"
    perturbation = f"(time < {start:.17g} ? 0 : {amplitude:.17g}*sin({2*math.pi*frequency:.17g}*(time-{start:.17g})))"
    rotating = {"d": perturbation if axis == "d" else "0", "q": perturbation if axis == "q" else "0", "theta": theta}
    abc = expressions("iclarke", expressions("ipark", rotating, "spice"), "spice")
    for phase, source in zip("abc", sources):
        name = deck.element_names[source]
        # Keep the source and its current-vector identity when adding a series injection.
        pattern = rf"(?m)^({re.escape(name)}) (\S+) (.*)$"
        body, changed = re.subn(pattern, lambda m: f"{m[1]} cd_dq_{phase} {m[3]}\nBcd_dq_{phase} {m[2]} cd_dq_{phase} V = {abc[phase]}", body)
        if changed != 1:
            raise ValueError("dq 注入端口的 SPICE 电源映射无效")
    vectors = {}
    for kind in "vi":
        phases = {}
        for phase, source in zip("abc", sources):
            a, b = (deck.pin_nodes[source + "." + pin] for pin in "ba")
            if kind == "v":
                phases[phase] = " - ".join("0" if node == "0" else f"v({node})" for node in (a, b))
            else:
                phases[phase] = f"i({deck.element_names[source]})"
        stationary = expressions("clarke", phases, "spice")
        vectors.update({kind + key: value for key, value in expressions("park", {**stationary, "theta": theta}, "spice").items()})
    commands = ["run", "linearize", *[f"let {name} = {expression}" for name, expression in vectors.items()],
                "wrdata waveforms.dat " + " ".join(vectors)]
    # Gear 2 avoids trapezoidal ringing at the average SVPWM common-mode corners.
    text = body + f".tran {step:.17g} {stop:.17g} 0 {step:.17g} uic\n.options method=gear maxord=2\n" + impedance._control(commands, reltol=SOLVER_RTOL)
    return spice.Deck(text, ["time_s", *vectors], step, stop, deck.notes), count


def _measure(snapshot, sources, grid, frequency, cfg, engine, profile, stage, refine=1):
    stage.mkdir()
    waves = []
    for axis in ("baseline", "d", "q"):
        folder = stage / axis
        deck, count = _deck(snapshot, sources, grid, frequency, cfg, axis, cfg["amplitude"] / refine, refine, profile, folder)
        spice._simulate(deck, engine, folder, 120)
        data = parse_csv((folder / "waveforms.csv").read_bytes())
        waves.append({name: values[-2 * count - 1:-1] for name, values in data.columns.items()})
    base = waves[0]
    operating = {name: sum(base[name]) / len(base[name]) for name in ("vd", "vq", "id", "iq")}
    # Scale each pair by its vector norm so a zero q component has the same tolerance after rotation.
    scale = {kind: math.hypot(operating[kind + "d"], operating[kind + "q"]) for kind in "vi"}
    if any(max(base[name]) - min(base[name]) > STEADY_ATOL + impedance.STEADY_RTOL * scale[name[0]] for name in operating):
        raise ValueError("dq 工作点仍有波动，请增加稳定时间或核对 PLL 与电流控制参数")
    windows = []
    for window in (slice(0, count), slice(count, 2 * count)):
        columns = []
        for wave in waves[1:]:
            columns.append({name: impedance.phasor([v - b for v, b in zip(wave[name][window], base[name][window])], frequency, deck.step)
                            for name in ("vd", "vq", "id", "iq")})
        windows.append(matrix_from_columns(columns))
    z, y, condition = windows[1]
    drift = relative_error(z, windows[0][0])
    if drift > impedance.WINDOW_DRIFT_RTOL:
        raise ValueError("dq 相邻窗口结果仍在漂移，请增加稳定时间或核对闭环参数")
    return {"frequency_hz": frequency, "z": z, "y": y, "window_drift": drift,
            "condition": condition, "operating": operating}


def relative_error(a, b):
    return math.sqrt(sum(abs(x - y) ** 2 for x, y in zip(a, b)) / max(sum(abs(v) ** 2 for v in b), 1e-30))


def run(lay, out_dir, *, sources, options=None, compare=False, profile="double", ngspice=None, base_dir=None):
    cfg = settings(options)
    snapshot, grid, notes = prepare(lay, sources, cfg, profile, base_dir)
    for frequency in cfg["frequency_hz"]:
        _timing(frequency, grid, cfg, 2 if compare else 1)
    engine = spice.executable(ngspice)
    if not engine:
        raise ValueError("未找到 ngspice，请安装并设置 CIRCUIT_DRAWER_NGSPICE")
    destination = Path(out_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".dq-impedance-", dir=destination) as temp:
        stage = Path(temp)
        records, comparisons = [], []
        selected = {0, len(cfg["frequency_hz"]) // 2, len(cfg["frequency_hz"]) - 1}
        for i, frequency in enumerate(cfg["frequency_hz"]):
            record = _measure(snapshot, sources, grid, frequency, cfg, engine, profile, stage / f"frequency-{i}")
            records.append(record)
            if compare and i in selected:
                refined = _measure(snapshot, sources, grid, frequency, cfg, engine, profile, stage / f"comparison-{i}", 2)
                floor = ENTRY_SCALE_FLOOR * math.sqrt(sum(abs(z) ** 2 for z in record["z"]))
                errors = {axis: abs(a - b) / max(abs(b), floor) for axis, a, b in zip(AXES, refined["z"], record["z"])}
                error = max(errors.values())
                comparisons.append({**refined, "relative_error": error, "entry_relative_error": errors,
                                    "matrix_relative_error": relative_error(refined["z"], record["z"]),
                                    "consistent": error <= impedance.COMPARISON_RTOL})
        plots = {axis: impedance.plot_svg(cfg["frequency_hz"], [r["z"][i] for r in records],
                      [{**r, "real_ohm": r["z"][i].real, "imag_ohm": r["z"][i].imag} for r in comparisons])
                      .replace("Zout", "Z" + axis).replace("Output impedance", "dq impedance")
                      .replace("AC 扫频（蓝色） / 时域注入（橙色）", "时域扫频（蓝色） / 减半幅值与步长（橙色）")
                 for i, axis in enumerate(AXES)}
        for record in [*records, *comparisons]:
            for key in ("z", "y"):
                record[key] = {axis: {"real": value.real, "imag": value.imag} for axis, value in zip(AXES, record[key])}
        result = {"sources": list(sources), "settings": cfg, "grid": grid, "note": NOTE, "notes": notes,
                  "records": records, "comparison": comparisons, "matrix_axes": list(AXES)}
        with (stage / "dq-impedance.csv").open("w", encoding="utf-8", newline="") as target:
            writer = csv.writer(target)
            writer.writerow(["frequency_hz", *[f"{kind}_{axis}_{part}" for kind in ("z_ohm", "y_siemens") for axis in AXES for part in ("real", "imag")]])
            writer.writerows([r["frequency_hz"], *[r[kind][axis][part] for kind in ("z", "y") for axis in AXES for part in ("real", "imag")]] for r in records)
        for axis, svg in plots.items():
            (stage / f"Z{axis}.svg").write_text(svg, encoding="utf-8")
        (stage / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        snapshot.save(stage / "source.json")
        final = destination / ("run-" + uuid.uuid4().hex[:12])
        stage.rename(final)
    return {**result, "plots": plots, "csv": (final / "dq-impedance.csv").read_text(encoding="utf-8"),
            "out_dir": str(final), "files": [str(final / name) for name in ("dq-impedance.csv", "result.json", "source.json", *[f"Z{axis}.svg" for axis in AXES])]}
