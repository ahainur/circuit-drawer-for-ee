"""Closed-loop simulation: a control table drives the circuit's PWM switches.

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
maps a controller output, read as a duty cycle 0..1, onto PWM switches; each
switch keeps its own frequency, edge times and start delay from
``simulation.components`` and its fixed ``duty`` is not used.

The controller is the control table's own structure and transfer functions,
simulated as a continuous-time model: each transfer function becomes a cascade
of first and second order sections, integrated on 1 F capacitors by
behavioural current sources, with states scaled by the section's frequency.
Output limits (``code.blocks.<id>.min / max``) clamp the output; while it is
clamped, back-calculation pulls the last integrator back so it does not wind up.  Sampling, computation delay
and fixed-point rounding are not modelled; ``codegen`` covers the discrete
controller.  Only ngspice built-in B sources are used (no XSPICE), so the deck
runs on every ngspice build.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

KEYS = ("control", "inputs", "outputs", "note")
INPUT_KEYS = ("probe", "value", "ramp", "filter")
OUTPUT_KEYS = ("switches", "complement", "dead_time", "model")
MODELS = ("switching", "average")
ROOT_ABS = 1e-6            # rad/s: a root this small is s = 0
DEFAULT_W = 2 * math.pi * 1e3
# A comparator edge as long as the switch's own rise/fall time: tanh crosses
# 10-90 % over about 2.2 / SHARPNESS of the carrier's 0..1 swing.
EDGE_SPAN = 2.2
MIN_PULSE_EDGES = 5        # a bridge leg's shortest on-time, in switch edge times
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
            "inputs": pl.inputs, "outputs": pl.outputs}


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
        if not isinstance(cfg, dict) or "switches" not in cfg or set(cfg) - set(OUTPUT_KEYS):
            raise ClosedLoopError(f"{what}：写成 {{\"switches\": [\"S1\"]}}，桥臂可加 "
                                  f"\"complement\": [\"S2\"] 和 \"dead_time\": 1e-6")
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
        model = cfg.get("model", "average" if comp else "switching")
        if model not in MODELS or (model == "average" and len(comp) != len(sws)):
            raise ClosedLoopError(f"{what}.model 须为 {list(MODELS)}；average 用于桥臂，"
                                  f"switches 与 complement 一一对应（上管、下管）")
        loop.outputs[bid] = {"switches": list(sws), "complement": list(comp), "dead_time": dead,
                             "model": model}

    for st in pl.steps:
        if st.kind in ("sos", "pi"):
            tf = spec.tf[st.id]
            loop.tf[st.id] = tf.zpk
            if tf.delay:
                loop.warnings.append(f"{st.id}：传递函数里的延迟 {tf.delay:g} s 未仿真")
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
          tr: dict) -> list[str]:
    """Controller, inputs and modulators; ``switches``: id -> (SPICE name, values)."""
    pl = loop.plan
    out = [f"* Closed loop: controller from control table {json.dumps(loop.name, ensure_ascii=True)}",
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
        x = [f"v({node(s)})" for s in st.srcs]
        if st.kind == "fn":
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
    for bid, cfg in loop.outputs.items():
        if cfg["model"] == "average":
            # the leg itself is written by spice.make_deck (average_leg);
            # the drive nodes keep drive probes working
            for sw in cfg["switches"]:
                name = switches[sw][0]
                out.append(f"Bctl_{name} ctl_{name} 0 V = min(max(v({node(bid)}), 0), 1)")
            for sw in cfg["complement"]:
                name = switches[sw][0]
                out.append(f"Bctl_{name} ctl_{name} 0 V = 1 - min(max(v({node(bid)}), 0), 1)")
            continue
        bridge = bool(cfg["complement"])
        for sw in cfg["switches"]:
            name, val = switches[sw]
            out += modulator(name, val, node(bid), sw, triangle=bridge, dead=cfg["dead_time"])
        for sw in cfg["complement"]:
            name, val = switches[sw]
            out += modulator(name, val, node(bid), sw, triangle=True, dead=cfg["dead_time"], low=True)
    return out


def modulator(name: str, val: dict, duty: str, label: str, *, triangle=False, dead=0.0,
              low=False) -> list[str]:
    """PWM from a carrier compare.  Alone, a switch is on from the sawtooth's
    reset until the carrier passes the duty (trailing edge).  In a bridge leg
    the carrier is a triangle (centre-aligned) so both edges are compares: the
    upper switch is on below duty - dead/T, the lower above duty + dead/T,
    which leaves ``dead`` seconds with both off at every edge.  A leg's duty
    is kept MIN_PULSE_EDGES edge times away from 0 and 1."""
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
