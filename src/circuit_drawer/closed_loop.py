"""Closed-loop control: PWM/VCO simulation and sampled SPS export.

The circuit table's ``closed_loop`` section links the two tables::

    "closed_loop": {
      "control": "buck_vmode.control.json",          # path (relative to the
                                                     # circuit file) or the table
      "inputs":  {"ref":   {"value": 1.65, "ramp": 5e-4},
                  "plant": {"probe": "v_out"}},
      "outputs": {"pwm":   {"switches": ["S1"]}}
    }

``inputs`` names every input of the controller (``codegen.plan``): a fixed
reference (``value``, optionally ramped from 0 over ``ramp`` seconds) or a
measured signal, by the name of a ``simulation.probes`` channel.  ``outputs``
maps a controller output, read as a duty cycle 0..1, onto PWM switches.
Unpaired switches keep their frequency, edge times and start delay from
``simulation.components``. Each complementary pair shares the upper switch's
frequency and delay, with the longer rise/fall times of the pair. Fixed
open-loop duty values are replaced by the controller output.
With ``modulation: vco``, the command instead denotes Hz, clamped to explicit
frequency bounds and integrated into a shared phase for the switch group.

The default controller uses the control table's structure and transfer functions
as a continuous-time model: each transfer function becomes a cascade
of first and second order sections, integrated on 1 F capacitors by
behavioural current sources, with states scaled by the section's frequency.
Output limits (``code.blocks.<id>.min / max``) clamp the output; while it is
clamped, back-calculation pulls the last integrator back so it does not wind up.
An optional control-table ``digital`` section selects the shared discrete plan,
sampled at code.fs with ADC/PWM quantization, delay and finite-word states.
The ngspice control script updates held sources while preserving circuit state.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .sps import DEFAULT_SAMPLES, FIELDS as SPS_FIELDS, Timing as SpsTiming

KEYS = ("control", "inputs", "outputs", "note")
INPUT_KEYS = ("probe", "value", "ramp", "filter")
MODULATIONS = ("pwm", "vco", "sps")
VCO_FIELDS = ("frequency_min", "frequency_max")  # Hz, explicit positive ordered limits
OUTPUT_KEYS = ("switches", "complement", "dead_time", "model", "modulation", *VCO_FIELDS, *SPS_FIELDS)
MODELS = ("switching", "average")
ROOT_ABS = 1e-6            # rad/s: a root this small is s = 0
DEFAULT_W = 2 * math.pi * 1e3
# A comparator edge as long as the switch's own rise/fall time: tanh crosses
# 10-90 % over about 2.2 / SHARPNESS of the carrier's 0..1 swing.
EDGE_SPAN = 2.2
MIN_PULSE_EDGES = 5        # a bridge leg's shortest on-time, in switch edge times
DELAY_MATCH_ULPS = 8       # equivalent SI spellings can round a few float steps apart
# ngspice's PULSE misreads a zero plateau when rise + fall fill the whole
# period (it holds the top for half a period instead), so each carrier gets a
# plateau of this fraction of the period, taken from its slopes.
PLATEAU = 1e-6
CONTINUOUS_NOTE = "控制器按连续时间模型仿真：不含采样、计算延迟和定点舍入"


class ClosedLoopError(ValueError):
    pass


@dataclass
class Loop:
    name: str
    plan: Any                                   # codegen.Plan
    tf: dict[str, Any]                          # block -> continuous Zpk
    inputs: dict[str, dict]                     # control input -> settings
    outputs: dict[str, dict]                    # control output -> switches, complement, dead_time
    warnings: list[str] = field(default_factory=list)
    digital: Any = None                         # control.digital.Digital

    @property
    def averaged(self) -> dict[str, tuple[str, str]]:
        """Upper switch -> (lower switch, control output) of each averaged leg."""
        return {u: (lo, out) for out, cfg in self.outputs.items() if cfg["model"] == "average"
                for u, lo in zip(cfg["switches"], cfg["complement"])}

    @property
    def driven(self) -> dict[str, str]:
        """Switch id -> the control output that sets its duty."""
        return {s: out for out, cfg in self.outputs.items() for s in cfg["switches"] + cfg["complement"]}


def node(signal: str) -> str:
    """The SPICE node of a controller signal; park.d -> cl_park__d."""
    return "cl_" + signal.replace(".", "__")


def duty_node(loop: Loop, signal: str) -> str:
    if not loop.digital:
        return node(signal)
    name = node(signal) + "__duty"
    occupied = {node(bid) for bid in loop.plan.inputs} | {node(st.id) for st in loop.plan.steps}
    while name in occupied:
        name += "_"
    return name


def _table(raw: Any, base_dir: str | Path | None) -> dict:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        raise ClosedLoopError("closed_loop.control 填控制框图表的路径（相对于电路文件）或表本身")
    path = Path(raw)
    if not path.is_absolute():
        path = Path(base_dir or ".") / path
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ClosedLoopError(f"closed_loop.control：找不到控制表 {path}") from None
    except (OSError, ValueError) as exc:
        raise ClosedLoopError(f"closed_loop.control：无法读取控制表 {path}：{exc}") from None
    if not isinstance(data, dict):
        raise ClosedLoopError(f"closed_loop.control：{path} 不是控制框图表")
    return data


def controller(control: Any, base_dir: str | Path | None):
    """(table, plan, loop spec) of a control table that passes its own check."""
    from .control.check import check
    from .control.codegen import CodeError, plan
    from .control.loop import LoopError, parse_loop
    from .profiles import get_profile
    raw = _table(control, base_dir)
    if raw.get("kind", "control") != "control":
        raise ClosedLoopError("closed_loop.control 必须是控制框图表（kind = control）")
    rep = check(raw, get_profile(str(raw.get("profile") or "ieee-double")))
    if not rep.ok:
        raise ClosedLoopError("控制表未通过检查：" + "；".join(rep.errors[:5]))
    try:
        pl = plan(rep)
    except (CodeError, LoopError) as exc:
        raise ClosedLoopError(f"控制表无法生成控制器：{exc}") from None
    spec, _ = parse_loop(raw.get("loop"), rep.diagram)
    return raw, pl, spec


def describe(control: Any, base_dir: str | Path | None) -> dict:
    """What the editor needs to map a control table onto the circuit."""
    raw, pl, _ = controller(control, base_dir)
    return {"name": str(raw.get("name") or "control"), "title": str(raw.get("title") or ""),
            "inputs": pl.inputs, "outputs": pl.outputs, "table": raw, "modulations": list(MODULATIONS)}


def load(section: Any, base_dir: str | Path | None, components: dict, kinds: dict[str, str],
         probes: list[str]) -> Loop:
    """Check the section against the control table and the circuit."""
    if not isinstance(section, dict):
        raise ClosedLoopError("closed_loop 必须是对象：control、inputs、outputs")
    bad = sorted(set(section) - set(KEYS))
    missing = [k for k in ("control", "inputs", "outputs") if k not in section]
    if bad or missing:
        raise ClosedLoopError(f"closed_loop：未知字段 {bad}，缺少字段 {missing}；"
                              f"允许 {list(KEYS)}")
    raw, pl, spec = controller(section["control"], base_dir)
    loop = Loop(str(raw.get("name") or "control"), pl, {}, {}, {})
    loop.warnings += pl.warnings

    ins = section["inputs"]
    if not isinstance(ins, dict):
        raise ClosedLoopError("closed_loop.inputs 必须是对象：控制表输入框 → 来源")
    for bid in sorted(set(ins) - set(pl.inputs)):
        raise ClosedLoopError(f"closed_loop.inputs.{bid}：不是控制器的输入；输入为 {pl.inputs}")
    for bid in pl.inputs:
        cfg = ins.get(bid)
        what = f"closed_loop.inputs.{bid}"
        if not isinstance(cfg, dict):
            raise ClosedLoopError(f"{what}：控制器输入 {bid} 需要来源，"
                                  f"如 {{\"probe\": \"v_out\"}} 或 {{\"value\": 1.65}}")
        bad = sorted(set(cfg) - set(INPUT_KEYS))
        if bad or ("probe" in cfg) == ("value" in cfg) or ("ramp" in cfg and "value" not in cfg) \
                or ("filter" in cfg and "probe" not in cfg):
            raise ClosedLoopError(f"{what}：填 probe（测量通道名）加可选 filter（秒），"
                                  f"或 value（定值）加可选 ramp（秒）")
        if "probe" in cfg and cfg["probe"] not in probes:
            raise ClosedLoopError(f"{what}.probe：没有名为 {cfg['probe']!r} 的测量通道；"
                                  f"现有 {probes}")
        if "value" in cfg:
            cfg = {"value": _num(cfg["value"], f"{what}.value"),
                   "ramp": _num(cfg.get("ramp", 0), f"{what}.ramp")}
            if cfg["ramp"] < 0:
                raise ClosedLoopError(f"{what}.ramp 不能小于 0")
        elif "filter" in cfg:
            cfg = {"probe": cfg["probe"], "filter": _num(cfg["filter"], f"{what}.filter")}
            if cfg["filter"] < 0:
                raise ClosedLoopError(f"{what}.filter 不能小于 0")
        loop.inputs[bid] = dict(cfg)

    outs = section["outputs"]
    if not isinstance(outs, dict) or not outs:
        raise ClosedLoopError("closed_loop.outputs 必须是对象：控制器输出 → {\"switches\": [...]}")
    seen: set[str] = set()
    for bid, cfg in outs.items():
        what = f"closed_loop.outputs.{bid}"
        if bid not in pl.outputs:
            raise ClosedLoopError(f"{what}：不是控制器的输出；输出为 {pl.outputs}")
        if not isinstance(cfg, dict) or "switches" not in cfg:
            raise ClosedLoopError(f"{what}：写成 {{\"switches\": [\"S1\"]}}，桥臂可加 "
                                  f"\"complement\": [\"S2\"] 和 \"dead_time\": 1e-6")
        if unknown := sorted(set(cfg) - set(OUTPUT_KEYS)):
            raise ClosedLoopError(f"{what}：未知字段 {unknown}；可用字段 {list(OUTPUT_KEYS)}")
        sws, comp = cfg["switches"], cfg.get("complement", [])
        if not isinstance(sws, list) or not sws or not isinstance(comp, list):
            raise ClosedLoopError(f"{what}.switches 需要至少一个开关元件；complement 是开关列表")
        for key, group in (("switches", sws), ("complement", comp)):
            for sw in group:
                if kinds.get(sw) not in ("nmos", "pmos", "igbt") or sw not in components:
                    raise ClosedLoopError(f"{what}.{key}：{sw!r} 不是电路里的 MOSFET / IGBT")
                if sw in seen:
                    raise ClosedLoopError(f"{what}.{key}：开关 {sw} 被两个输出同时驱动，或在一个桥臂里列了两次")
                seen.add(sw)
        dead = _num(cfg.get("dead_time", 0), f"{what}.dead_time")
        if dead < 0 or (dead and not comp):
            raise ClosedLoopError(f"{what}.dead_time 须 ≥ 0，且只用于有 complement 的桥臂")
        modulation = cfg.get("modulation", "pwm")
        if modulation not in MODULATIONS:
            raise ClosedLoopError(f"{what}.modulation 须为 {list(MODULATIONS)}")
        if modulation == "pwm" and comp and len(comp) != len(sws):
            raise ClosedLoopError(f"{what}：PWM 的 switches 与 complement 须一一对应（上管、下管）")
        bounds = {}
        if modulation == "vco":
            bounds = {k: _num(cfg.get(k), f"{what}.{k}") for k in VCO_FIELDS}
            if not 0 < bounds['frequency_min'] < bounds['frequency_max']:
                raise ClosedLoopError(f"{what}：要求 0 < frequency_min < frequency_max，单位 Hz")
        elif set(cfg).intersection(VCO_FIELDS):
            raise ClosedLoopError(f"{what}：{', '.join(VCO_FIELDS)} 仅用于 VCO 变频调制")
        if modulation == "sps":
            if len(sws) != 4 or len(comp) != 4:
                raise ClosedLoopError(f"{what}：SPS 的 switches/complement 各需四项，按原A、原B、副A、副B桥臂排列上/下管")
            try:
                timing = SpsTiming(_num(cfg.get('frequency'), what+'.frequency'),
                    _num(cfg.get('phase_min'), what+'.phase_min'),
                    _num(cfg.get('phase_max'), what+'.phase_max'), dead,
                    cfg.get('samples_per_cycle', DEFAULT_SAMPLES))
            except ValueError as exc:
                raise ClosedLoopError(f"{what}：{exc}") from None
            bounds = {k: getattr(timing, k) for k in SPS_FIELDS}
        elif set(cfg).intersection(SPS_FIELDS):
            raise ClosedLoopError(f"{what}：{', '.join(SPS_FIELDS)} 仅用于 SPS 相移调制")
        model = cfg.get("model", "average" if comp and modulation == "pwm" else "switching")
        if modulation == "vco" and model != "switching":
            raise ClosedLoopError(f"{what}：VCO 变频调制只支持 switching 模型")
        if modulation == "sps" and model != "switching":
            raise ClosedLoopError(f"{what}：SPS 调制只支持 switching 模型")
        if model not in MODELS or (model == "average" and len(comp) != len(sws)):
            raise ClosedLoopError(f"{what}.model 须为 {list(MODELS)}；average 用于桥臂，"
                                  f"switches 与 complement 一一对应（上管、下管）")
        loop.outputs[bid] = {"switches": list(sws), "complement": list(comp), "dead_time": dead,
                             "model": model, **({'modulation': modulation, **bounds} if bounds else {})}

    for st in pl.steps:
        if st.kind in ("sos", "pi"):
            tf = spec.tf[st.id]
            loop.tf[st.id] = tf.zpk
            if tf.delay:
                loop.warnings.append(f"{st.id}：传递函数里的延迟 {tf.delay:g} s 未仿真")
    if "digital" in raw:
        from .control.custom_c import is_step
        if any(is_step(st) for st in pl.steps):
            raise ClosedLoopError("C 代码方块使用 code.fs 固定采样；暂不支持 digital 延迟与量化设置")
        from .control.digital import NOTE, parse, prepare
        loop.digital = parse(raw["digital"], pl, spec.params)
        if loop.digital.pwm_bits and any(cfg.get('modulation') == 'vco' for cfg in loop.outputs.values()):
            raise ClosedLoopError("VCO 的输出单位为 Hz，不能使用占空比 digital.pwm_bits；请移除此设置")
        if loop.digital.pwm_bits and any(cfg.get('modulation') == 'sps' for cfg in loop.outputs.values()):
            raise ClosedLoopError("SPS 的输出单位为 rad，不能使用占空比 digital.pwm_bits；请移除此设置")
        loop.plan = prepare(pl, loop.digital)
        loop.warnings = [NOTE]
    else:
        loop.warnings.append(CONTINUOUS_NOTE)
    return loop


def _num(v: Any, what: str) -> float:
    """A number, or a SPICE-style string such as "500u", as simulation fields take."""
    from .spice import SpiceError, number
    try:
        return number(v, what)
    except SpiceError as exc:
        raise ClosedLoopError(str(exc)) from None


# ---- transfer function -> cascade of sections ------------------------------------
@dataclass
class Section:
    w: float                     # rad/s time scale: s = w p
    poles: list[complex]
    zeros: list[complex] = field(default_factory=list)

    def coeffs(self) -> tuple[list[float], list[float]]:
        """Numerator and monic denominator in p, both padded to the order."""
        from .control.lti import from_roots, real_poly
        n = len(self.poles)
        den = real_poly(from_roots([p / self.w for p in self.poles]))
        num = real_poly(from_roots([z / self.w for z in self.zeros]))
        return [0.0] * (n + 1 - len(num)) + num, den


def _split(roots: list[complex]) -> tuple[list[complex], list[list[complex]]]:
    """Real roots, and complex roots as conjugate pairs."""
    real, pairs, rest = [], [], []
    for r in roots:
        if abs(r) < ROOT_ABS:
            real.append(0.0)
        elif abs(r.imag) <= 1e-9 * abs(r):
            real.append(r.real)
        elif r.imag > 0:
            pairs.append(r)
        else:
            rest.append(r)
    out = [[p, p.conjugate()] for p in pairs]
    if len(rest) != len(pairs):
        raise ClosedLoopError("传递函数的复数根不成共轭对")
    return sorted(real, key=abs), out


def sections(zpk, label: str) -> tuple[float, list[Section]]:
    """Overall gain and sections, s = 0 poles first.  Each section is proper."""
    if len(zpk.zeros) > len(zpk.poles):
        raise ClosedLoopError(f"{label}：零点多于极点（含纯微分），连续模型无法实现；"
                              f"给微分项加滤波（pid 的 fd）")
    preal, ppairs = _split(list(zpk.poles))
    zreal, zpairs = _split(list(zpk.zeros))
    secs = [Section(0, [p]) for p in preal] + [Section(0, pair) for pair in ppairs]
    for pair in zpairs:
        home = next((s for s in secs if len(s.poles) == 2 and not s.zeros), None)
        if home is None:
            ones = [s for s in secs if len(s.poles) == 1 and not s.zeros]
            if len(ones) < 2:
                raise ClosedLoopError(f"{label}：无法给复数零点配对极点")
            ones[0].poles += ones[1].poles
            secs.remove(ones[1])
            home = ones[0]
        home.zeros = list(pair)
    for z in zreal:
        home = next(s for s in secs if len(s.zeros) < len(s.poles))
        home.zeros.append(z)
    nonzero = [abs(r) for r in [*zpk.poles, *zpk.zeros] if abs(r) >= ROOT_ABS]
    gain = zpk.k
    for s in secs:
        roots = [abs(r) for r in [*s.poles, *s.zeros] if abs(r) >= ROOT_ABS]
        s.w = max(roots) if roots else max(nonzero, default=DEFAULT_W)
        gain *= s.w ** (len(s.zeros) - len(s.poles))
    return gain, secs


# ---- netlist --------------------------------------------------------------------
def _g(v: float) -> str:
    return f"{v:.15g}"


def _clamp(expr: str, lo, hi) -> str:
    if lo is not None:
        expr = f"max({expr}, {_g(lo)})"
    if hi is not None:
        expr = f"min({expr}, {_g(hi)})"
    return expr


def _sum(terms: list[tuple[float, str]]) -> str:
    out = ""
    for c, x in terms:
        if c == 0:
            continue
        sign = "-" if c < 0 else "+"
        mag = "" if abs(c) == 1 else f"{_g(abs(c))}*"
        out += f" {sign} {mag}{x}"
    out = out.strip()
    if not out:
        return "0"
    return out[2:] if out.startswith("+ ") else "-" + out[2:]


def _anti_windup(secs: list[Section]) -> tuple[int, float] | None:
    """Back-calculation into the last integrator: (section, gain from its state to the
    output at low frequency), or None when that integrator is not a first-order section."""
    ints = [i for i, sec in enumerate(secs) if any(abs(p) < ROOT_ABS for p in sec.poles)]
    if not ints or len(secs[ints[-1]].poles) != 1:
        return None
    i = ints[-1]
    num, _ = secs[i].coeffs()
    c = num[1]                                  # y = b0 x + b1 u, a0 = 0
    for sec in secs[i + 1:]:
        n, d = sec.coeffs()
        c *= n[-1] / d[-1]
    return (i, c) if c else None


def _tf_lines(sid: str, zpk, u: str, lo, hi, warnings: list[str]) -> list[str]:
    gain, secs = sections(zpk, sid)
    lines = [f"* {sid}: {len(secs)} section(s), states on 1 F capacitors, s = w*p"]
    node = f"cl_{sid}"
    limited = lo is not None or hi is not None
    raw = f"{node}__raw" if limited else node
    aw = None
    if limited and any(abs(p) < ROOT_ABS for p in zpk.poles):
        aw = _anti_windup(secs)
        if aw is None:
            warnings.append(f"{sid}：积分环节不是一阶节，限幅时未做抗饱和")
        # Back-calculation: while clamped, pull the output back to the limit at
        # the integrator's own rate (no faster than the sections after it).
        else:
            rate = min([secs[aw[0]].w] + [0.5 * sec.w for sec in secs[aw[0] + 1:]])
            aw = (aw[0], rate / aw[1])
    x_in = f"{_g(gain)}*({u})"
    for i, sec in enumerate(secs):
        num, den = sec.coeffs()
        xs = [f"cl_{sid}__{i}x{j}" for j in range(len(den) - 1)]
        for x in xs:
            lines += [f"C{x} {x} 0 1 IC=0", f"R{x} {x} 0 1e12"]
        w = _g(sec.w)
        if len(xs) == 1:
            a0 = den[1]
            b1, b0 = num
            back = f" + {_g(aw[1])}*(v({node}) - v({raw}))" if aw and aw[0] == i else ""
            lines.append(f"B{xs[0]} 0 {xs[0]} I = {w}*({_sum([(-a0, f'v({xs[0]})'), (1, x_in)])}){back}")
            y = _sum([(b0 - b1 * a0, f"v({xs[0]})"), (b1, f"({x_in})")])
        else:
            a1, a0 = den[1], den[2]
            b2, b1, b0 = num
            lines += [f"B{xs[0]} 0 {xs[0]} I = {w}*v({xs[1]})",
                      f"B{xs[1]} 0 {xs[1]} I = {w}*("
                      + _sum([(-a0, f"v({xs[0]})"), (-a1, f"v({xs[1]})"), (1, x_in)]) + ")"]
            y = _sum([(b0 - b2 * a0, f"v({xs[0]})"), (b1 - b2 * a1, f"v({xs[1]})"),
                      (b2, f"({x_in})")])
        out = raw if i == len(secs) - 1 else f"cl_{sid}__{i}y"
        lines.append(f"B{out} {out} 0 V = {y}")
        x_in = f"v({out})"
    if limited:
        lines.append(f"B{node} {node} 0 V = {_clamp(f'v({raw})', lo, hi)}")
    return lines


def lines(loop: Loop, probe_exprs: dict[str, str], switches: dict[str, tuple[str, dict]],
          tr: dict, *, native=False) -> list[str]:
    """Controller, inputs and modulators; ``switches``: id -> (SPICE name, values)."""
    pl = loop.plan
    out = [f"* Closed loop: controller from control table {json.dumps(loop.name, ensure_ascii=True)}",
           "* Sampled controller updated by the control script." if loop.digital else
           "* Continuous-time model of the controller; no sampling or quantisation."]
    for bid in pl.inputs:
        cfg = loop.inputs[bid]
        if "probe" in cfg and cfg.get("filter"):
            # a first-order measurement filter: the controller reads a state,
            # not the switched circuit itself, within one integration step
            raw, tau = node(bid) + "__m", _g(cfg["filter"])
            out += [f"B{raw} {raw} 0 V = {probe_exprs[cfg['probe']]}",
                    f"C{node(bid)} {node(bid)} 0 1 IC=0", f"R{node(bid)} {node(bid)} 0 1e12",
                    f"B{node(bid)} 0 {node(bid)} I = (v({raw}) - v({node(bid)}))/{tau}"]
        elif "probe" in cfg:
            out.append(f"Bcl_{bid} {node(bid)} 0 V = {probe_exprs[cfg['probe']]}")
        elif cfg["ramp"] > 0:
            out.append(f"Vcl_{bid} {node(bid)} 0 PWL(0 0 {_g(cfg['ramp'])} {_g(cfg['value'])})")
        else:
            out.append(f"Vcl_{bid} {node(bid)} 0 DC {_g(cfg['value'])}")
    for st in pl.steps:
        if loop.digital:
            out.append(f"V{node(st.id)} {node(st.id)} 0 DC 0")
            continue
        x = [f"v({node(s)})" for s in st.srcs]
        if st.kind == "fn":
            if st.data["fn"] == "c":
                if not native:
                    raise ClosedLoopError("ngspice 不支持 C 代码方块；请选择 PLECS／Simulink 离散控制器导出")
                continue  # native validation still checks the remaining controller and modulators
            from .control.functions import expressions
            exprs = expressions(st.data["fn"], dict(zip(st.data["names"], x)), "spice")
            out.append(f"* {st.id}: {st.note}")
            for sig in st.data["outs"]:
                out.append(f"B{node(sig)} {node(sig)} 0 V = {exprs[sig.split('.', 1)[1]]}")
            continue
        if st.kind in ("sos", "pi"):
            out += _tf_lines(st.id, loop.tf[st.id], x[0], st.lo, st.hi, loop.warnings)
            continue
        if st.kind == "sum":
            expr = _sum(list(zip(st.signs, x)))
        elif st.kind == "gain":
            expr = f"{_g(st.data['k'])}*{x[0]}"
        elif st.kind == "mul":
            expr = "*".join(x)
        elif st.kind == "div":
            den = "*".join(x[1:])
            expr = f"(abs({den}) > 1e-30 ? {x[0]}/({den}) : 0)"
        else:                                   # copy, limit
            expr = x[0]
        out.append(f"Bcl_{st.id} cl_{st.id} 0 V = {_clamp(expr, st.lo, st.hi)}")
    for index, (bid, cfg) in enumerate(loop.outputs.items()):
        duty = duty_node(loop, bid)
        if loop.digital:
            out.append(f"V{duty} {duty} 0 DC {_g(cfg.get('frequency_min', 0))}")
        if cfg.get('modulation') == 'sps':
            timing = validate_sps(cfg, switches, tr)
            if native:
                out.append('* SPS: native sampled gate scheduler')
            else:
                from .sps_spice import sources, tick_count
                if loop.digital or len(loop.outputs) != 1:
                    raise ClosedLoopError('ngspice SPS 暂仅支持连续控制器与单一 SPS 输出组；数字、多速率或混合输出尚未验收')
                if tr.get('node_capacitance', 0) <= 0:
                    raise ClosedLoopError('ngspice SPS 要求显式设置正的 transient.node_capacitance 处理浮置网络；不会自动接地或修改电路，须核对该数值电容的敏感性')
                try:
                    tick_count(timing, tr['stop'])
                except ValueError as exc:
                    raise ClosedLoopError(str(exc)) from exc
                out += sources(['ctl_' + switches[sw][0] for sw in cfg['switches'] + cfg['complement']])
                loop.warnings.append('ngspice SPS 实验模式：连续控制、单组、前瞻 PWL；数值边沿为时间格的 0.001，死区不缩短。必须复核阶跃、步长及显式节点电容敏感性；不代表已整定或原生验收。')
            continue
        if cfg.get('modulation') == 'vco':
            out += vco_lines(index, cfg, switches, duty, tr)
            continue
        if cfg["model"] == "average":
            # the leg itself is written by spice.make_deck (average_leg);
            # the drive nodes keep drive probes working
            for sw in cfg["switches"]:
                name = switches[sw][0]
                out.append(f"Bctl_{name} ctl_{name} 0 V = min(max(v({duty}), 0), 1)")
            for sw in cfg["complement"]:
                name = switches[sw][0]
                out.append(f"Bctl_{name} ctl_{name} 0 V = 1 - min(max(v({duty}), 0), 1)")
            continue
        bridge = bool(cfg["complement"])
        for sw, (name, val), low in pwm_drives(cfg, switches):
            if bridge:
                from .spice import check_pwm_timing
                check_pwm_timing(sw, val, tr)
            out += modulator(name, val, duty, sw, triangle=bridge, dead=cfg["dead_time"], low=low)
    return out


def pwm_drives(cfg, switches):
    """Pairwise carrier timing shared by ngspice and both native exporters."""
    if not cfg["complement"]:
        return [(sw, switches[sw], False) for sw in cfg["switches"]]
    drives = []
    for upper, lower in zip(cfg["switches"], cfg["complement"]):
        high, low = switches[upper][1], switches[lower][1]
        timing = {"frequency": high["frequency"], "delay": high["delay"],
                  "rise": max(high["rise"], low["rise"]), "fall": max(high["fall"], low["fall"])}
        edge = (timing["rise"] + timing["fall"]) / 2
        if cfg["dead_time"] + MIN_PULSE_EDGES * 2 * edge >= .5 / timing["frequency"]:
            raise ClosedLoopError(f"{upper}/{lower}：PWM 死区和边沿过长，半周期内没有足够导通时间")
        for sw, is_lower in ((upper, False), (lower, True)):
            name, values = switches[sw]
            drives.append((sw, (name, {**values, **timing}), is_lower))
    return drives


def vco_lines(index, cfg, switches, signal, tr):
    """One integrated phase for the entire group; transport delays only turn-on.

    The matched lossless line has unity gain and a delay in seconds (ngspice
    manual 6.1). min(raw, delayed) turns off immediately and turns on after
    dead_time, including frequency steps. A phase offset proportional to the
    instantaneous frequency would not preserve this time interval.
    """
    group = [switches[sw] for sw in cfg['switches'] + cfg['complement']]
    delay = group[0][1]['delay']
    if any(abs(val['delay'] - delay) > DELAY_MATCH_ULPS * math.ulp(max(val['delay'], delay)) for _, val in group):
        raise ClosedLoopError("同一 VCO 输出的所有开关须使用相同 delay，由共享相位产生互补驱动")
    edge = max(max(val['rise'], val['fall']) for _, val in group)
    fmin, fmax, dead = cfg['frequency_min'], cfg['frequency_max'], cfg['dead_time']
    if dead + MIN_PULSE_EDGES * edge >= .5 / fmax:
        raise ClosedLoopError("VCO：dead_time 和开关边沿过长，最高频率的半周期内没有足够导通时间")
    if tr['step'] > 1 / fmax / 50 or tr['max_step'] > (.5 / fmax - dead) / 10:
        raise ClosedLoopError("VCO：时间步长过大；最高频率每周期至少 50 个采样点，导通段至少 10 步")
    if delay + 1 / fmin > tr['stop']:
        raise ClosedLoopError("VCO：结束时间须覆盖启动延迟之后至少一个最低频率周期")
    prefix = f'cd_vco_{index}'
    freq, phase = prefix + '_frequency', prefix + '_phase'
    enable = prefix + '_enable'
    out = [f'* VCO: controller Hz clamped to {_g(fmin)}..{_g(fmax)}, shared phase in cycles',
           f'B{freq} {freq} 0 V = {_clamp(f"v({signal})", fmin, fmax)}',
           f'V{enable} {enable} 0 PWL(0 0 ' + (f'{_g(delay)} 0 ' if delay else '') + f'{_g(delay + edge)} 1)',
           f'C{phase} {phase} 0 {_g(1/fmax)} IC=0', f'R{phase} {phase} 0 1e12',
           f'B{phase} 0 {phase} I = v({enable})*v({freq})/{_g(fmax)}']
    wave = f'0.5*(1 + tanh({_g(EDGE_SPAN / (2 * math.pi * edge))}/v({freq})*sin({_g(2 * math.pi)}*v({phase}))))'
    for polarity, members in (('high', cfg['switches']), ('low', cfg['complement'])):
        if not members:
            continue
        raw = prefix + '_' + polarity
        expr = wave if polarity == 'high' else f'1 - ({wave})'
        out.append(f'B{raw} {raw} 0 V = v({enable})*({expr})')
        drive = f'v({raw})'
        if dead:
            delayed = raw + '_delayed'
            out += [f'T{raw} {raw} 0 {delayed} 0 Z0=1 TD={_g(dead)} IC=0,0,0,0',
                    f'R{delayed} {delayed} 0 1']
            # Delay-line interpolation can undershoot at a sharp edge; a
            # normalized gate must never produce negative conductance.
            drive = f'min(v({raw}), max(0, v({delayed})))'
        for sw in members:
            name = switches[sw][0]
            out.append(f'Bctl_{name} ctl_{name} 0 V = {drive}')
    return out


def modulator(name: str, val: dict, duty: str, label: str, *, triangle=False, dead=0.0,
              low=False) -> list[str]:
    """PWM from a carrier compare.  Alone, a switch is on from the sawtooth's
    reset until the carrier passes the duty (trailing edge).  In a bridge leg
    the carrier is a triangle (centre-aligned) so both edges are compares: the
    upper switch is on below duty - dead/T, the lower above duty + dead/T,
    giving a nominal ``dead`` gap at constant duty. Continuous duty changes
    affect that gap. A leg's duty is kept MIN_PULSE_EDGES edge times away
    from 0 and 1."""
    period = 1 / val["frequency"]
    edge = (val["rise"] + val["fall"]) / 2
    gate = f"(time >= {_g(val['delay'])} ? 1 : 0)*" if val["delay"] > 0 else ""
    if not triangle:
        sharp = EDGE_SPAN * period / edge
        return [f"* {label}: duty from v({duty}), sawtooth carrier at {_g(val['frequency'])} Hz",
                f"Vcar_{name} car_{name} 0 PULSE(0 1 {_g(val['delay'])} "
                f"{_g(period - val['rise'] - PLATEAU * period)} {_g(val['rise'])} {_g(PLATEAU * period)} "
                f"{_g(period)})",
                f"Bctl_{name} ctl_{name} 0 V = {gate}0.5*(1 + tanh({_g(sharp)}*(v({duty}) - v(car_{name}))))"]
    sharp = EDGE_SPAN * period / (2 * edge)          # the triangle rises 0..1 in half a period
    margin = dead / period
    # Minimum pulse: keep the duty where each switch's on-time still spans
    # several edge times, as a gate driver's minimum pulse width does; a
    # sliver at the carrier's peak would make diode commutations shorter
    # than the solver can follow.
    lo = margin + MIN_PULSE_EDGES * 2 * edge / period
    clamped = f"min(max(v({duty}), {_g(lo)}), {_g(1 - lo)})"
    compare = (f"v(car_{name}) - {clamped} - {_g(margin)}" if low
               else f"{clamped} - v(car_{name}) - {_g(margin)}")
    return [f"* {label}: {'lower' if low else 'upper'} switch of a leg, duty from v({duty}), "
            f"triangle carrier at {_g(val['frequency'])} Hz, dead time {_g(dead)} s",
            f"Vcar_{name} car_{name} 0 PULSE(0 1 {_g(val['delay'])} {_g(period * (1 - PLATEAU) / 2)} "
            f"{_g(period * (1 - PLATEAU) / 2)} {_g(PLATEAU * period)} {_g(period)})",
            f"Bctl_{name} ctl_{name} 0 V = {gate}0.5*(1 + tanh({_g(sharp)}*({compare})))"]


def average_leg(upper: str, duty: str, p: str, mid: str, n: str) -> list[str]:
    """A bridge leg as its switching-period average: the midpoint follows
    duty x the DC link, and the DC link supplies duty x the leg current, so
    power balances.  Dead time, ripple and diode conduction are not modelled."""
    d = f"min(max(v({duty}), 0), 1)"
    return [f"* leg of {upper}: averaged over a switching period (closed_loop model average)",
            f"Bavg_{upper} cd_{upper}_avg {n} V = {d}*v({p},{n})",
            f"Vavg_{upper} cd_{upper}_avg {mid} 0",
            f"Bdc_{upper} {p} {n} I = {d}*i(Vavg_{upper})"]


def validate_sps(cfg, switches, tr):
    """Native and internal export front doors must share timing checks."""
    t = SpsTiming(cfg['frequency'], cfg['phase_min'], cfg['phase_max'], cfg['dead_time'], cfg['samples_per_cycle'])
    group = [switches[sw][1] for sw in cfg['switches'] + cfg['complement']]
    if any(v['delay'] != 0 for v in group):
        raise ClosedLoopError('SPS 使用共享启动：首周期全关断；所有开关 delay 须为 0')
    if any(tr[key] > t.ts + 8*math.ulp(t.ts) for key in ('max_step', 'step')):
        raise ClosedLoopError(f'SPS 时间步长过大：step 和 max_step 均须 ≤ {t.ts:g} s')
    if tr['stop'] < 3 / t.frequency:
        raise ClosedLoopError('SPS 结束时间须覆盖至少三个开关周期（含启动和命令流水延迟）')
    return t
