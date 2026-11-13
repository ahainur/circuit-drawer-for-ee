"""Simulation-backed candidates for fixed-frequency, ideal-switch RLC circuits.

Candidates stay outside the coordinate table until apply() is explicitly called.
Adaptive solver points, short intervals and convergence evidence are retained.
"""
from __future__ import annotations

from bisect import bisect_left
from collections import deque
from copy import deepcopy
import csv
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import shutil
import tempfile

from .layout import Layout, windings
from .modes import Mode, complete_loops, states, _member, _net_sequence, _edges_of, solve
from .profiles import get_profile
from .spice import make_deck, number, executable, _simulate

# Eight complete cycles catch slow envelope drift missed by adjacent edge times.
CHECK_CYCLES = 8
PHASE_SAMPLES = 256
# Require phase-matched drift below 5% of ripple, with a numerical noise floor.
RIPPLE_DRIFT = 0.05
RELATIVE_FLOOR = 1e-5
ABSOLUTE_FLOOR = 1e-7
EDGE_TOLERANCE = 0.02  # fraction of a switching period
PERIOD_MATCH_RTOL = 1e-4  # numerical period margin, in addition to measured edge brackets
# Multiple winding zero crossings add short states to each switching event.
# Bound review size while retaining the complete measured period.
MAX_CANDIDATES = 128
SUPPORTED = {"R", "L", "C", "V", "D", "nmos", "pmos", "port", "transformer"}
DEFAULTS = {"current_floor": 0.001, "current_relative": 0.01,
            "short_fraction": 0.005}
NOTE = "支持普通电路及模块电路中的 Buck、Boost、LLC、DAB 定频开环理想开关与变压器模型。请核对完整周期的候选区间和方向后确认写入。"


class AutoModeError(ValueError):
    pass


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode()).hexdigest()


def current_refs(layout):
    """Drawing references shared by recognition settings and winding probes."""
    if "modules" in layout.extra:
        from .modules import assemble
        try:
            layout = assemble(layout).sheets
        except (ValueError, KeyError, TypeError):
            return []  # Invalid drafts still need their normal validation report.
    return [m.ref for m in _edges_of(layout)
            if layout.element(m.el).kind in {"L", "transformer"}]


def recognition_settings(options, inductors):
    options = {} if options is None else options
    if not isinstance(options, dict) or set(options) - {*DEFAULTS, "distinguish"}:
        raise AutoModeError("模态识别设置包含未知字段。")
    settings = {**DEFAULTS, **options}
    for key, upper in (("current_floor", 1e6), ("current_relative", 0.1), ("short_fraction", 0.05)):
        value = settings[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= upper:
            raise AutoModeError(f"{key} 须为大于 0 且不超过 {upper:g} 的数值。")
    settings.setdefault("distinguish", inductors)
    chosen = settings["distinguish"]
    if not isinstance(chosen, list) or any(not isinstance(e, str) or e not in inductors for e in chosen) or len(set(chosen)) != len(chosen):
        raise AutoModeError("区分量必须是不重复的电感或绕组编号。")
    return settings


def physical_signature(deck):
    # Keep the original signature format so existing provenance remains valid.
    physical = [s for s in deck.text.split(".control\n", 1)[0].splitlines()[1:]
                if not s.startswith(("*", ".save"))]
    return _hash({"deck": physical, "pins": deck.pin_nodes, "elements": deck.element_names})


def source_status(layout, profile="ieee-single", base_dir=None):
    """Check provenance without running the solver or changing stored results."""
    from .waveforms import circuit_signature
    sources = [(i, m["source"]) for i, m in enumerate(layout.modes)
               if isinstance(m, dict) and isinstance(m.get("source"), dict) and m["source"].get("kind") == "simulation"]
    waveform = layout.extra.get("waveform")
    waveform = waveform if isinstance(waveform, dict) else {}
    wave_source = waveform.get("mode_source")
    signature, reason = None, ""
    if sources or wave_source:
        try:
            signature = physical_signature(make_deck(layout, profile, base_dir))
        except (ValueError, KeyError, TypeError, OSError) as exc:
            reason = str(exc)
    simulation = layout.extra.get("simulation", {})
    transient_hash = _hash(simulation.get("transient", {}) if isinstance(simulation, dict) else simulation)
    def stale(source):
        return (not isinstance(source, dict) or source.get("signature") != signature or signature is None
                or source.get("transient_hash", transient_hash) != transient_hash)
    # The simulation signature preserves device aliases across encapsulation.
    return {"stale_modes": [i for i, source in sources if stale(source)],
            "waveform_stale": bool(waveform) and (stale(wave_source) if wave_source else
                                                   waveform.get("circuit_signature") != circuit_signature(layout)),
            "reason": reason}


def prepare(layout: Layout, options=None, profile="ieee-single", base_dir=None):
    deck, scene, settings, period, signature = _prepare(layout, options, profile, base_dir)
    return deck, scene.report, settings, period, signature


def _prepare(layout, options=None, profile="ieee-single", base_dir=None, *, purpose="模态识别"):
    from .mode_scene import ModeScene
    if "closed_loop" in layout.extra:
        raise AutoModeError(f"本阶段支持定频开环驱动；闭环和平均模型的{purpose}待接入。")
    try:
        deck = make_deck(layout, profile, base_dir)
        scene = ModeScene(layout, get_profile(profile))
    except ValueError as exc:
        raise AutoModeError(str(exc)) from exc
    signature = physical_signature(deck)
    layout = scene.layout
    unsupported = [e.id for e in layout.elements if e.kind not in SUPPORTED]
    if unsupported:
        raise AutoModeError(f"当前{purpose}不支持这些器件模型：" + "、".join(unsupported))
    mapping = layout.extra.get("model_mapping", {})
    if not isinstance(mapping, dict) or "switch" in mapping or any(
            not isinstance(value, dict) or value.get("model") != "ideal" for value in mapping.values()):
        raise AutoModeError(f"当前{purpose}需要默认理想开关与理想 RLC 映射，请另存适用模型的副本。")
    if not scene.report.ok:
        raise AutoModeError("图纸校验未通过，请先修正问题栏后预览模态。")
    switches = [e for e in layout.elements if e.kind in {"nmos", "pmos"}]
    inductors = current_refs(layout)
    if not switches or not inductors:
        raise AutoModeError(f"{purpose}需要至少一个 PWM 开关和一个电感或变压器。")
    sim = layout.extra["simulation"]
    frequencies = [number(sim["components"][e.id]["frequency"], e.id) for e in switches]
    if any(not math.isclose(f, frequencies[0], rel_tol=1e-9) for f in frequencies):
        raise AutoModeError(f"当前{purpose}要求所有开关使用相同频率。")
    period = 1 / frequencies[0]
    for e in layout.elements:
        cfg = sim["components"].get(e.id, {})
        if number(cfg.get("phase_ramp", 0), e.id) and number(cfg.get("phase_shift", 0), e.id):
            # _pwm_lines resumes fixed PULSE drives after the ramp and one cycle.
            settled = max(number(cfg["phase_ramp"], e.id), number(cfg["delay"], e.id)) + 2 * period
            if settled > deck.stop - (CHECK_CYCLES + 3) * period:
                raise AutoModeError("移相软启动尚未结束，分析窗口需要固定相位：" + e.id)
        if number(cfg.get("breakdown_voltage", 0), e.id):
            raise AutoModeError(f"当前{purpose}需要正向二极管模型：" + e.id)
    settings = recognition_settings(options, inductors)
    if number(sim["transient"]["max_step"], "max_step") > EDGE_TOLERANCE * period:
        raise AutoModeError(f"{purpose}要求最大积分步长不超过周期的 2%，请减小 max_step。")
    if deck.stop < (CHECK_CYCLES + 2) * period:
        raise AutoModeError("仿真时长至少需要十个开关周期。")
    return deck, scene, settings, period, signature


def _instrument(layout, deck, aliases=None, *, extra_vectors=None):
    """Probe actual branches before linearize without changing the circuit."""
    vectors = {}
    saved = []
    aliases = aliases or {}

    def voltage(eid, pins):
        nodes = [deck.pin_nodes[f"{aliases.get(eid, eid)}.{pin}"] for pin in pins]
        return " - ".join("0" if n == "0" else f"v({n})" for n in nodes)

    def device(key, expression):
        vectors[key] = expression
        saved.append(expression)

    for e in layout.elements:
        if e.kind == "port":
            continue
        name = deck.element_names[aliases.get(e.id, e.id)]
        if e.kind in {"nmos", "pmos"}:
            vectors[f"gate:{e.id}"] = f"v(ctl_{name})"
            device(f"current:{e.id}", f"@{name.lower()}[i]")
            if e.opts.get("diode", True) is not False:
                device(f"body:{e.id}", f"@dbody_{name.lower()}[id]")
        elif e.kind == "D":
            device(f"current:{e.id}", f"@{name.lower()}[id]")
        elif e.kind == "C":
            vectors[f"voltage:{e.id}"] = voltage(e.id, ("a", "b"))
            device(f"current:{e.id}", f"@{name.lower()}[i]")
        elif e.kind == "L":
            vectors[f"current:{e.id}"] = f"i({name})"
        elif e.kind == "transformer":
            # These are the same terminal-current sensors used by SPICE probes.
            sensors = {"s": "secondary", "s1": "upper", "s2": "lower"}
            for part, *_ in windings(e):
                sensor = sensors.get(part, part)
                vectors[f"current:{e.id}:{part}"] = f"i(V{sensor}_{name})"
            cfg = layout.extra["simulation"]["components"][e.id]
            if number(cfg.get("magnetizing_inductance", 0), e.id):
                vectors[f"magnetizing:{e.id}"] = f"i(Lmag_{name})"
        elif e.kind == "V":
            # The drawing's a->b direction is opposite the SPICE source branch.
            vectors[f"current:{e.id}"] = f"-i({name})"
        elif e.kind == "R":
            resistance = number(layout.extra["simulation"]["components"][e.id]["value"], e.id)
            vectors[f"current:{e.id}"] = f"({voltage(e.id, ('a', 'b'))})/{resistance:.15g}"
    if extra_vectors:
        if vectors.keys() & extra_vectors.keys():
            raise AutoModeError("额外探针名称与器件探针重复。")
        vectors.update(extra_vectors)
    # Round-trip double precision preserves adjacent adaptive times at an edge.
    # The ordinary probe export sets its own precision after linearize.
    commands = ["set wr_singlescale", "set wr_vecnames", "set numdgt=17"]
    commands += [f"let cd_mode_{i} = 0*time + ({expr})" for i, expr in enumerate(vectors.values())]
    commands.append("wrdata device_states.dat " + " ".join(f"cd_mode_{i}" for i in range(len(vectors))))
    text = deck.text.replace(".control\n", ".save all " + " ".join(saved) + "\n.control\n")
    text = text.replace("\nlinearize\n", "\n" + "\n".join(commands) + "\nlinearize\n")
    return replace(deck, text=text), list(vectors)


def _read_trace(path, columns, stop, period, *, on_sample=None):
    rows = deque()
    cutoff = stop - (CHECK_CYCLES + 3) * period
    previous = -math.inf
    with path.open(encoding="ascii") as stream:
        header = next(stream, "").split()
        if len(header) != len(columns) + 1:
            raise AutoModeError("模态探针数据列数无效。")
        for line in stream:
            try:
                row = [float(v) for v in line.split()]
            except ValueError:
                raise AutoModeError("模态探针包含无效数值。") from None
            if len(row) != len(header) or not all(map(math.isfinite, row)) or row[0] <= previous:
                raise AutoModeError("模态探针数值或时间顺序无效。")
            if on_sample is not None:
                on_sample(row)
            previous = row[0]
            rows.append(row)
            if row[0] < cutoff and len(rows) > 1:
                rows.popleft()
            if len(rows) > 1_000_000:
                raise AutoModeError("模态分析窗口超过一百万个积分点，请检查仿真设置。")
    if len(rows) < 2 or rows[0][0] > cutoff + period or not math.isclose(previous, stop, rel_tol=1e-9, abs_tol=1e-15):
        raise AutoModeError("模态探针未覆盖分析窗口。")
    values = list(zip(*rows))
    return values[0], dict(zip(columns, values[1:]))


def _value(times, values, time):
    index = bisect_left(times, time)
    if index == 0:
        return values[0]
    if index == len(times):
        return values[-1]
    ratio = (time - times[index - 1]) / (times[index] - times[index - 1])
    return values[index - 1] + ratio * (values[index] - values[index - 1])


def _crossings(times, values, threshold):
    result = []
    for i in range(1, len(times)):
        a, b = values[i - 1], values[i]
        if (a > threshold) != (b > threshold):
            result.append(times[i - 1] + (threshold - a) / (b - a) * (times[i] - times[i - 1]))
    return result


def _cycles(layout, times, data, period):
    reference = next(e.id for e in layout.elements if e.kind in {"nmos", "pmos"})
    gate = data[f"gate:{reference}"]
    edges, brackets = [], []
    for ta, tb, a, b in zip(times, times[1:], gate, gate[1:]):
        if a <= 0.5 < b:
            edges.append(ta + (0.5 - a) / (b - a) * (tb - ta))
            brackets.append((ta, tb))
    if len(edges) < CHECK_CYCLES + 1:
        raise AutoModeError("缺少八个完整 PWM 周期，请延长仿真时间。")
    edges = edges[-(CHECK_CYCLES + 1):]
    brackets = brackets[-(CHECK_CYCLES + 1):]
    # Adaptive points may straddle an entire PWM ramp. Its crossing cannot
    # be timed more accurately than that bracket; do not invent extra samples.
    margin = period * PERIOD_MATCH_RTOL
    if any(period + margin < b[0] - a[1] or period - margin > b[1] - a[0]
           for a, b in zip(brackets, brackets[1:])):
        raise AutoModeError("末尾 PWM 周期不一致，请检查驱动设置。")
    return edges


def _steady(layout, times, data, edges):
    channels = [f"current:{ref}" for ref in current_refs(layout)]
    channels += [f"voltage:{e.id}" for e in layout.elements if e.kind == "C"]
    channels += [key for key in data if key.startswith("magnetizing:")]
    return [_steady_channel(times, data[key], edges, key) for key in channels]


def _steady_channel(times, values, edges, name):
    cycles = [[_value(times, values, a + (b - a) * (k + 0.5) / PHASE_SAMPLES)
               for k in range(PHASE_SAMPLES)] for a, b in zip(edges, edges[1:])]
    last = cycles[-1]
    ripple = max(last) - min(last)
    drift = max(abs(x - y) for cycle in cycles[:-1] for x, y in zip(cycle, last))
    tolerance = max(RIPPLE_DRIFT * ripple, RELATIVE_FLOOR * max(map(abs, last)), ABSOLUTE_FLOOR)
    return {"channel": name, "drift": drift, "ripple": ripple, "tolerance": tolerance,
            "ok": drift <= tolerance}


def _segments(layout, times, data, begin, end, thresholds, distinguish):
    cuts = {begin, end}
    for key, values in data.items():
        levels = []
        if key.startswith("gate:"):
            levels = [0.5]
        elif key in thresholds:
            levels = [thresholds[key]]
            if key.removeprefix("current:") in distinguish:
                levels.append(-thresholds[key])
        for level in levels:
            cuts.update(t for t in _crossings(times, values, level) if begin < t < end)
    cuts = sorted(cuts)
    segments = []
    for a, b in zip(cuts, cuts[1:]):
        middle = (a + b) / 2
        sample = {key: _value(times, values, middle) for key, values in data.items()}
        on = []
        for e in layout.elements:
            if e.kind in {"nmos", "pmos"}:
                if sample[f"gate:{e.id}"] > 0.5:
                    on.append(e.id)
                elif sample.get(f"body:{e.id}", 0) > thresholds.get(f"body:{e.id}", math.inf):
                    on.append(e.id + ":diode")
            elif e.kind == "D" and sample[f"current:{e.id}"] > thresholds[f"current:{e.id}"]:
                on.append(e.id)
        signs = {eid: (1 if sample[f"current:{eid}"] > thresholds[f"current:{eid}"] else
                       -1 if sample[f"current:{eid}"] < -thresholds[f"current:{eid}"] else 0)
                 for eid in distinguish}
        if segments and segments[-1]["on"] == on and segments[-1]["signs"] == signs:
            segments[-1]["end"] = b
        else:
            segments.append({"begin": a, "end": b, "on": on, "signs": signs})
    return segments


def _orient(layout, report, loops, currents):
    """Orient using measured device/inductor current, then passive branch flow."""
    def net(member, i):
        return report.nets[f"{member.el}.{member.pins[i]}"]
    result = []
    for loop in loops:
        groups = [[_member(layout, ref) for ref in branch.split("|")] for branch in loop]
        sequence = _net_sequence(groups, net)
        evidence = []
        for i, group in enumerate(groups):
            for member in group:
                kind = layout.element(member.el).kind
                rank = 3 if kind in {"nmos", "pmos", "D"} else 2 if kind in {"L", "transformer"} else 1
                current = currents.get(member.ref, 0)
                if abs(current) > 1e-9:
                    evidence.append((rank, abs(current), current * (1 if sequence[i] == net(member, 0) else -1)))
        flip = evidence and max(evidence)[2] < 0
        result.append(list(reversed(loop)) if flip else list(loop))
    return result


def _opposing_magnetic_currents(layout, report, loops, currents, thresholds):
    """A grouped parallel path cannot represent opposing winding currents."""
    net = lambda m, i: report.nets[f"{m.el}.{m.pins[i]}"]
    opposite = set()
    for loop in loops:
        groups = [[_member(layout, ref) for ref in branch.split("|")] for branch in loop]
        sequence = _net_sequence(groups, net)
        for i, group in enumerate(groups):
            for member in group:
                if layout.element(member.el).kind not in {"L", "transformer"}:
                    continue
                current = currents.get(member.ref, 0) * (1 if sequence[i] == net(member, 0) else -1)
                if current < -thresholds[f"current:{member.ref}"]:
                    opposite.add(member.ref)
    return sorted(opposite)


def analyse(layout, report, settings, period, signature, times, data, graph=None):
    edges = _cycles(layout, times, data, period)
    steady = _steady(layout, times, data, edges)
    result = {"version": 1, "note": NOTE, "signature": signature, "settings": settings,
              "transient_hash": _hash(layout.extra["simulation"]["transient"]),
              "settings_hash": _hash(settings), "period_s": period, "window": edges[-2:],
              "steady": steady, "candidates": [], "ready": False, "reason": ""}
    if not all(c["ok"] for c in steady):
        result["reason"] = "末尾八个周期的电流或电压仍在漂移，请延长仿真或核对初始条件后重新识别。"
        return result
    start = bisect_left(times, edges[0])
    thresholds = {key: max(settings["current_floor"], settings["current_relative"] * max(map(abs, values[start:])))
                  for key, values in data.items() if key.startswith(("current:", "body:"))}
    cycles = [_segments(layout, times, data, a, b, thresholds, settings["distinguish"])
              for a, b in zip(edges, edges[1:])]
    segments = cycles[-1]
    result["thresholds"] = thresholds
    result["segments"] = segments
    result["cycles"] = cycles
    signatures = [[(s["on"], s["signs"]) for s in cycle] for cycle in cycles]
    result["short_variation"] = any(sequence != signatures[-1] for sequence in signatures[:-1])
    # Gate ramps set the resolution floor; retain every event regardless of its
    # short flag. A tiny relative margin stabilises comparisons at exactly 0.5%.
    edge = max(number(layout.extra["simulation"]["components"][e.id]["rise"], e.id)
               for e in layout.elements if e.kind in {"nmos", "pmos"})
    short = max(settings["short_fraction"] * period, 2 * edge) + period * 1e-6
    # All short states remain visible. Compare the substantial sequence across
    # all eight cycles so threshold chatter cannot masquerade as a stable mode.
    major = [[s for s in cycle if s["end"] - s["begin"] >= short] for cycle in cycles]
    reference = [(s["on"], s["signs"]) for s in major[-1]]
    repeated = all([(s["on"], s["signs"]) for s in cycle] == reference for cycle in major)
    if repeated:
        repeated = all(abs((s["begin"] - edges[i]) - (r["begin"] - edges[-2])) <= EDGE_TOLERANCE * period
                       for i, cycle in enumerate(major) for s, r in zip(cycle, major[-1]))
    if not repeated:
        result["reason"] = "末尾八个周期的状态顺序或分界时刻不一致，请延长仿真后重新识别。"
        return result
    groups = {}
    for index, segment in enumerate(segments):
        segment["index"] = index
        key = (tuple(segment["on"]), tuple(segment["signs"].items()))
        groups.setdefault(key, []).append(segment)
    if len(groups) > MAX_CANDIDATES:
        result["reason"] = "候选模态过多，请检查阈值与振荡后重新识别。"
        return result
    old = []
    result["previous_errors"] = []
    for raw in layout.modes:
        try:
            previous = Mode.from_dict(raw)
            prior_state = states(layout, previous)
            solve(layout, previous, report, graph)
            old.append((previous, prior_state))
        except (ValueError, KeyError, TypeError) as exc:
            result["previous_errors"].append(str(exc))
    magnetic = current_refs(layout)
    for i, ((on, signs), intervals) in enumerate(groups.items()):
        longest = max(intervals, key=lambda s: s["end"] - s["begin"])
        middle = (longest["begin"] + longest["end"]) / 2
        currents = {key.removeprefix("current:"): _value(times, values, middle)
                    for key, values in data.items() if key.startswith("current:")}
        for e in layout.elements:
            key = f"body:{e.id}"
            if key in data:
                currents[e.id + ":diode"] = _value(times, data[key], middle) * (1 if e.kind == "pmos" else -1)
        state = {ref.split(":")[0]: "diode" if ref.endswith(":diode") else "on" for ref in on}
        loops = _orient(layout, report, complete_loops(layout, report, state, []), currents)
        caption = ", ".join(f"$t_{{{s['index']}}}$--$t_{{{s['index'] + 1}}}$" for s in intervals)
        mode = Mode(f"Mode {i + 1}", caption=caption, on=list(on), loops=loops)
        mode.source = {"kind": "simulation", "signature": signature, "settings_hash": result["settings_hash"],
                       "settings": deepcopy(settings), "transient_hash": _hash(layout.extra["simulation"]["transient"])}
        lit = solve(layout, mode, report, graph)
        opposite = _opposing_magnetic_currents(layout, report, loops, currents, thresholds)
        if opposite:
            mode.arrows = []
            lit.problems.append("这些电感或绕组的电流与回路方向相反，已隐藏方向箭头，请按实测电流核对：" + "、".join(opposite))
        covered = {ref for loop in loops for branch in loop for ref in branch.split("|")}
        uncovered = [ref for ref in magnetic if ref not in covered
                     and abs(currents[ref]) > thresholds[f"current:{ref}"]]
        if uncovered:
            lit.problems.append("这些支路仍有电流，模态图的闭合回路未覆盖，请核对换流区间：" + "、".join(uncovered))
        small = all(s["end"] - s["begin"] < short for s in intervals)
        matches = [m.name for m, prior in old if prior == state]
        directions = [m.name for m, prior in old if prior == state and m.loops and m.arrows is None
                      and _orient(layout, report, m.loops, currents) == m.loops
                      and not _opposing_magnetic_currents(layout, report, m.loops, currents, thresholds)]
        result["candidates"].append({"id": i, "mode": mode.to_dict(), "intervals": intervals,
                                     "fraction": sum(s["end"] - s["begin"] for s in intervals) / period,
                                     "signs": dict(signs), "currents": currents, "short": small,
                                     "all_off": not on, "matches": matches, "direction_matches": directions,
                                     "states": {e.id: state.get(e.id, "off") for e in layout.elements
                                                if e.kind in {"nmos", "pmos", "D"}}, "notes": lit.problems,
                                     "uncertain": [eid for eid, value in currents.items()
                                                   if 0.3 * thresholds.get(f"current:{eid}", math.inf) <= abs(value)
                                                   <= 3 * thresholds.get(f"current:{eid}", -math.inf)]})
    result["ready"] = True
    return result


def run(layout, out_dir=None, *, options=None, profile="ieee-single", base_dir=None, ngspice=None, timeout=120):
    deck, scene, settings, period, signature = _prepare(layout, options, profile, base_dir)
    engine = executable(ngspice)
    if not engine:
        raise AutoModeError("未找到 ngspice，请安装并设置 CIRCUIT_DRAWER_NGSPICE。")
    measured, columns = _instrument(scene.layout, deck, layout.extra.get("module_authoring", {}).get("aliases"))
    with tempfile.TemporaryDirectory(prefix="cd_modes_") as folder:
        stage = Path(folder)
        _simulate(measured, engine, stage, timeout)
        times, data = _read_trace(stage / "device_states.dat", columns, deck.stop, period)
        result = analyse(scene.layout, scene.report, settings, period, signature, times, data, scene.graph)
        result["waveform_sha256"] = hashlib.sha256((stage / "waveforms.csv").read_bytes()).hexdigest()
        if out_dir is not None:
            # Publish only after simulation, parsing and recognition all finish.
            (stage / "candidates.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            with (stage / "device_states.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["time_s", *columns])
                writer.writerows(zip(times, *(data[key] for key in columns)))
            destination = Path(out_dir).resolve()
            destination.mkdir(parents=True, exist_ok=True)
            files = ["candidates.json", "circuit.cir", "simulation.log", "waveforms.csv", "device_states.csv"]
            # Publish one complete run atomically. Independent runs use separate
            # folders, including concurrent CLI calls to the same destination.
            with tempfile.TemporaryDirectory(prefix=".modes-", dir=destination) as publishing:
                staged = Path(publishing)
                completed = destination / ("run-" + staged.name.removeprefix(".modes-"))
                for name in files:
                    shutil.copyfile(stage / name, staged / name)
                staged.rename(completed)
            result["files"] = [str(completed / name) for name in files]
    return result


def apply(layout, result, selection, *, replace_modes=False, profile="ieee-single", base_dir=None,
          bind_waveform=False, wave_store=None):
    """Validate a whole selection before returning the new, unsaved table."""
    if not result.get("ready"):
        raise AutoModeError("本次识别没有可采用的候选。")
    _, scene, _, _, signature = _prepare(layout, result["settings"], profile, base_dir)
    if (signature != result["signature"] or result.get("transient_hash", _hash(layout.extra["simulation"]["transient"]))
            != _hash(layout.extra["simulation"]["transient"])):
        raise AutoModeError("电路或仿真参数已改变，请重新识别后再写入。")
    if not isinstance(selection, list) or not selection:
        raise AutoModeError("请至少选择一个候选模态。")
    by_id = {c["id"]: c for c in result["candidates"]}
    modes = [] if replace_modes else deepcopy(layout.modes)
    names = {m["name"] for m in modes}
    selected = {}
    for item in selection:
        if not isinstance(item, dict) or set(item) - {"id", "name", "caption"}:
            raise AutoModeError("候选选择包含无效字段。")
        index = item.get("id")
        if type(index) is not int or index not in by_id or index in selected:
            raise AutoModeError("候选编号无效或重复。")
        candidate = deepcopy(by_id[index]["mode"])
        name = item.get("name", candidate["name"])
        caption = item.get("caption", candidate.get("caption", ""))
        if not isinstance(name, str) or not name.strip() or len(name) > 120 or not isinstance(caption, str) or len(caption) > 2000:
            raise AutoModeError("模态名称须为 1–120 字，标题最多 2000 字。")
        name = name.strip()
        if name in names:
            raise AutoModeError("模态名称重复，请改名后采用：" + name)
        names.add(name)
        selected[index] = name
        candidate.update(name=name, caption=caption)
        solve(scene.layout, Mode.from_dict(candidate), scene.report, scene.graph)
        modes.append(candidate)
    bound = layout.extra.get("waveform", {}).get("intervals", [])
    if replace_modes and any(bound) and not bind_waveform:
        raise AutoModeError("已有波形引用了将被替换的模态，请使用追加或先调整波形绑定。")
    updated = deepcopy(layout)
    updated.modes = modes
    if bind_waveform:
        from .waveforms import MAX_BOUNDARIES, validate_settings
        files = [Path(p) for p in result.get("files", []) if Path(p).name == "waveforms.csv"]
        if wave_store is None or len(files) != 1:
            raise AutoModeError("本次候选没有可用的波形文件，请重新运行仿真并识别。")
        raw = wave_store._bytes(files[0])
        if hashlib.sha256(raw).hexdigest() != result.get("waveform_sha256"):
            raise AutoModeError("本次识别的波形文件已改变，请重新运行仿真并识别。")
        segments = result["segments"]
        marks = [segments[0]["begin"], *(s["end"] for s in segments)]
        if len(marks) > MAX_BOUNDARIES:
            raise AutoModeError("本周期的分界过多，不能自动绑定；请核对阈值与振荡后重新识别。")
        owners = {s["index"]: c["id"] for c in result["candidates"] for s in c["intervals"]}
        # Explicit replacement starts fresh; old labels/notes refer to old samples.
        updated.extra.pop("waveform", None)
        cfg = wave_store.import_csv(updated, {"text": raw.decode("utf-8-sig"), "filename": "auto-modes.csv"}, profile)["config"]
        cfg.update(window=list(result["window"]), boundaries=marks,
                   intervals=[selected.get(owners[s["index"]]) for s in segments],
                   mode_source=deepcopy(next(m["source"] for m in modes if m["name"] in selected.values())))
        validate_settings(updated, cfg, wave_store.read(cfg))
        updated.extra["waveform"] = cfg
    return updated
