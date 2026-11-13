"""The closed-loop controller of a circuit table as native control blocks of
the Simulink and PLECS exports.

``build`` reads the ``Loop`` that ``closed_loop.load`` checked (the control
table's plan from ``control.codegen``, every block's continuous transfer
function, the ``inputs`` and ``outputs`` of the ``closed_loop`` section) and
turns it into a target-independent panel: a list of blocks, the links between
their ports, and the circuit signals the panel produces (each PWM switch's
gate).  ``simulink.py`` and ``plecs.py`` only map block kinds onto their own
library blocks and port numbers.

Two modes:

continuous   every transfer function as a continuous block (TransferFcn /
             TransferFunction, a limited PI as the PID block with clamping
             anti-windup), the same model ngspice simulates, for comparison.
discrete     the discretised controller the DSP code runs: ``code.fs`` and
             ``code.method`` of the control table, coefficients from
             ``control.codegen`` (DiscreteTransferFcn / DiscreteTransferFunction
             with Ts = 1/fs, a limited PI as the discrete PID block), and a
             unit delay where ``code.blocks.<id>.delay`` asks for one.

PWM is the same in both modes and the same as ngspice: a single switch
compares its duty against a sawtooth carrier (on while duty > carrier), a
bridge leg against a triangle, the upper switch on below duty - dead/T and
the lower above duty + dead/T.  An ``average`` leg has no averaged form in
the native libraries and is exported as this switching leg, said in a note.

Block kinds (``Block.kind``) and their ports, 1-based, inputs then outputs:

input     a circuit signal: ``source`` "probe" (``origin`` = the sensor part
          or drive signal, ``ref`` = the measurement name) or "signal" (a
          generator in the drive panel, ``ref`` = its name).  0 in, 1 out
const     ``value``.  0 in, 1 out
sum       ``signs`` "+-...".  n in, 1 out
gain      ``k``.  1 in, 1 out
product   ``ops`` "**" or "*/".  n in, 1 out
tf        ``num`` / ``den`` in descending powers of s.  1 in, 1 out
dtf       ``num`` / ``den`` in descending powers of z, equal length, ``ts``
pi        limited continuous PI: ``kp``, ``ki``, ``lo``, ``hi``
dpi       limited discrete PI: ``kp``, ``ki``, ``lo``, ``hi``, ``ts``, ``method``
sat       ``lo`` / ``hi`` (None = unbounded)
delay     one sample of ``ts``
carrier   0..1 ``shape`` "saw" or "tri" at ``f`` Hz, ``delay`` s.  0 in, 1 out
compare   output 1 while input 1 > input 2.  2 in, 1 out
step      0 before ``time``, 1 after.  0 in, 1 out
mux       n scalars to one vector (``dq``: a dq pair; Simulink pads dq0)
demux     one vector to n scalars (``dq``: a dq pair; Simulink drops the 0)
abc2dq    amplitude-invariant abc -> dq at the angle on input 2 (the native
          3ph->RRF / Park Transform block).  in 1 = abc, in 2 = theta
dq2abc    the inverse.  in 1 = dq, in 2 = theta
minmax    ``op`` "max" or "min" of n inputs
fcn       ``expr`` over ``{u1}``..``{un}`` with cos, sin, floor only
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from .closed_loop import CONTINUOUS_NOTE, MIN_PULSE_EDGES, ClosedLoopError, Loop

MODES = ("continuous", "discrete")
COLUMN = 170          # px between columns of control blocks (a PLECS PID block is 88 px wide)
ROW = 80              # px between rows (the tallest native block is 60 px)
TWO_PI = 2 * math.pi
_SAFE = re.compile(r"[^A-Za-z0-9_]")


@dataclass
class Block:
    name: str
    kind: str
    params: dict = field(default_factory=dict)
    inputs: int = 1
    outputs: int = 1
    label: str = ""                          # the control-table block it comes from
    pos: tuple[int, int] = (0, 0)            # px from the panel's top-left corner


@dataclass
class Link:
    src: tuple[str, int]                     # (block, output port)
    dst: tuple[str, int]                     # (block, input port)


@dataclass
class Panel:
    mode: str
    ts: float | None
    blocks: list[Block] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    exports: dict[str, tuple[str, int]] = field(default_factory=dict)   # circuit signal -> (block, port)
    warnings: list[str] = field(default_factory=list)
    size: tuple[int, int] = (0, 0)

    def block(self, name: str) -> Block:
        return next(b for b in self.blocks if b.name == name)

    def fed(self) -> set[tuple[str, int]]:
        """Input ports a link reaches."""
        return {lk.dst for lk in self.links}

    def used(self) -> set[tuple[str, int]]:
        """Output ports a link or an export reads."""
        return {lk.src for lk in self.links} | set(self.exports.values())

    def dangling(self) -> list[tuple[str, int]]:
        """Input ports nothing drives: a bug in the builder, never legal."""
        fed = self.fed()
        return [(b.name, i) for b in self.blocks for i in range(1, b.inputs + 1) if (b.name, i) not in fed]

    def unused_outputs(self) -> list[tuple[str, int]]:
        """Output ports nothing reads (a transform output the table does not use)."""
        used = self.used()
        return [(b.name, i) for b in self.blocks for i in range(1, b.outputs + 1) if (b.name, i) not in used]

    def kinds(self) -> set[str]:
        return {b.kind for b in self.blocks}


class _Builder:
    def __init__(self, panel: Panel):
        self.panel = panel
        self.names: set[str] = set()
        self.consts: dict[float, tuple[str, int]] = {}
        self.carriers: dict[tuple, tuple[str, int]] = {}

    def add(self, kind: str, base: str, params: dict | None = None, inputs: int = 1, outputs: int = 1,
            label: str = "") -> str:
        name = "ctl_" + _SAFE.sub("_", base)
        candidate, n = name, 1
        while candidate in self.names:
            n += 1
            candidate = f"{name}_{n}"
        self.names.add(candidate)
        self.panel.blocks.append(Block(candidate, kind, params or {}, inputs, outputs, label))
        return candidate

    def link(self, src: tuple[str, int], dst: tuple[str, int]) -> None:
        self.panel.links.append(Link(src, dst))

    def const(self, value: float) -> tuple[str, int]:
        if value not in self.consts:
            self.consts[value] = (self.add("const", f"k_{_num_name(value)}", {"value": float(value)}, 0, 1), 1)
        return self.consts[value]

    def one(self, kind: str, base: str, src: tuple[str, int], params: dict | None = None,
            label: str = "") -> tuple[str, int]:
        """A single-input, single-output block fed by ``src``."""
        name = self.add(kind, base, params, 1, 1, label)
        self.link(src, (name, 1))
        return name, 1

    def many(self, kind: str, base: str, srcs: list[tuple[str, int]], params: dict | None = None,
             label: str = "") -> tuple[str, int]:
        name = self.add(kind, base, params, len(srcs), 1, label)
        for i, s in enumerate(srcs, 1):
            self.link(s, (name, i))
        return name, 1

    def limited(self, src: tuple[str, int], lo, hi, base: str) -> tuple[str, int]:
        if lo is None and hi is None:
            return src
        return self.one("sat", base + "_sat", src, {"lo": lo, "hi": hi})

    def sum(self, base: str, terms: list[tuple[float, tuple[str, int]]], label: str = "") -> tuple[str, int]:
        """Signed sum; a single term is a gain of +-1."""
        if len(terms) == 1:
            sign, src = terms[0]
            return src if sign > 0 else self.one("gain", base, src, {"k": -1.0}, label)
        signs = "".join("+" if sign > 0 else "-" for sign, _ in terms)
        return self.many("sum", base, [src for _, src in terms], {"signs": signs}, label)

    def carrier(self, f: float, shape: str, delay: float) -> tuple[str, int]:
        key = (f, shape, delay)
        if key not in self.carriers:
            self.carriers[key] = (self.add("carrier", f"carrier_{len(self.carriers) + 1}",
                                           {"f": f, "shape": shape, "delay": delay}, 0, 1), 1)
        return self.carriers[key]


def _num_name(v: float) -> str:
    return _SAFE.sub("_", f"{v:g}")


# ---- transforms (amplitude invariant, the project's own conventions) -----------
def _vector(b: _Builder, base: str, srcs: list[tuple[str, int]], dq: bool) -> tuple[str, int]:
    return b.many("mux", base, srcs, {"n": len(srcs), "dq": dq})


def _scalars(b: _Builder, base: str, src: tuple[str, int], n: int, dq: bool) -> list[tuple[str, int]]:
    name = b.add("demux", base, {"n": n, "dq": dq}, 1, n)
    b.link(src, (name, 1))
    return [(name, i) for i in range(1, n + 1)]


def _abc_to_dq(b: _Builder, base: str, abc: tuple[str, int], theta: tuple[str, int]) -> tuple[str, int]:
    name = b.add("abc2dq", base, {}, 2, 1)
    b.link(abc, (name, 1))
    b.link(theta, (name, 2))
    return name, 1


def _dq_to_abc(b: _Builder, base: str, dq: tuple[str, int], theta: tuple[str, int]) -> tuple[str, int]:
    name = b.add("dq2abc", base, {}, 2, 1)
    b.link(dq, (name, 1))
    b.link(theta, (name, 2))
    return name, 1


def _function(b: _Builder, st, x: dict[str, tuple[str, int]]) -> dict[str, tuple[str, int]]:
    """Output name -> port of one function block, built from the native
    transform blocks: Clarke is 3ph->RRF at angle 0, Park is the inverse
    Clarke (RRF->3ph at 0) followed by 3ph->RRF at theta, and so on."""
    kind, sid = st.data["fn"], st.id
    zero = b.const(0.0)
    if kind == "clarke":
        c = x["c"] if "c" in x else b.sum(f"{sid}_c", [(-1.0, x["a"]), (-1.0, x["b"])])
        abc = _vector(b, f"{sid}_abc", [x["a"], x["b"], c], False)
        alpha, beta = _scalars(b, f"{sid}_out", _abc_to_dq(b, sid, abc, zero), 2, True)
        return {"alpha": alpha, "beta": beta}
    if kind == "park":
        ab = _vector(b, f"{sid}_ab", [x["alpha"], x["beta"]], True)
        abc = _dq_to_abc(b, f"{sid}_abc", ab, zero)
        d, q = _scalars(b, f"{sid}_out", _abc_to_dq(b, sid, abc, x["theta"]), 2, True)
        return {"d": d, "q": q}
    if kind == "ipark":
        dq = _vector(b, f"{sid}_dq", [x["d"], x["q"]], True)
        abc = _dq_to_abc(b, sid, dq, x["theta"])
        alpha, beta = _scalars(b, f"{sid}_out", _abc_to_dq(b, f"{sid}_ab", abc, zero), 2, True)
        return {"alpha": alpha, "beta": beta}
    if kind == "iclarke":
        ab = _vector(b, f"{sid}_ab", [x["alpha"], x["beta"]], True)
        a, bb, c = _scalars(b, f"{sid}_out", _dq_to_abc(b, sid, ab, zero), 3, False)
        return {"a": a, "b": bb, "c": c}
    if kind == "svpwm":
        ab = _vector(b, f"{sid}_ab", [x["alpha"], x["beta"]], True)
        phases = _scalars(b, f"{sid}_abc", _dq_to_abc(b, sid, ab, zero), 3, False)
        top = b.many("minmax", f"{sid}_max", phases, {"op": "max"})
        bottom = b.many("minmax", f"{sid}_min", phases, {"op": "min"})
        offset = b.one("gain", f"{sid}_offset", b.sum(f"{sid}_span", [(1.0, top), (1.0, bottom)]), {"k": 0.5})
        half = b.const(0.5)
        out = {}
        for name, ph in zip(("da", "db", "dc"), phases):
            centred = b.sum(f"{sid}_{name}_ac", [(1.0, ph), (-1.0, offset)])
            scaled = b.many("product", f"{sid}_{name}_pu", [centred, x["vdc"]], {"ops": "*/"})
            out[name] = b.limited(b.sum(f"{sid}_{name}", [(1.0, scaled), (1.0, half)]), 0.0, 1.0, f"{sid}_{name}")
        return out
    if kind == "wrap":
        expr = f"({{u1}}) - {TWO_PI!r}*floor(({{u1}})/{TWO_PI!r})"
        return {"y": b.one("fcn", sid, x["x"], {"expr": expr, "n": 1})}
    raise ClosedLoopError(f"{sid}：函数方块 {kind} 不能导出为原生控制方块")


# ---- the controller ---------------------------------------------------------------
def build(loop: Loop, mode: str, switches: dict[str, tuple[str, dict]], probes: dict[str, dict],
          references: dict[str, str]) -> Panel:
    """``switches``: switch id -> (its gate signal's name, its PWM values);
    ``probes``: measurement name -> the circuit output it is read from;
    ``references``: controller input -> the drive-panel generator of its
    fixed value."""
    if mode not in MODES:
        raise ClosedLoopError(f"闭环控制器导出模式须为 {list(MODES)}，不是 {mode!r}")
    if loop.digital:
        raise ClosedLoopError("digital 的逐拍量化模型请使用 ngspice 导出。Simulink/PLECS 的当前原生控制方块尚未实现该舍入与延迟模型")
    pl = loop.plan
    panel = Panel(mode, pl.ts if mode == "discrete" else None)
    b = _Builder(panel)
    sig: dict[str, tuple[str, int]] = {}
    for bid in pl.inputs:
        cfg = loop.inputs[bid]
        if "probe" in cfg:
            out = probes[cfg["probe"]]
            port = (b.add("input", bid, {"source": "probe", "ref": out["name"], "origin": out["origin"]},
                          0, 1, bid), 1)
            if cfg.get("filter"):
                port = b.one("tf", f"{bid}_filter", port, {"num": [1.0], "den": [cfg["filter"], 1.0]}, bid)
        else:
            port = (b.add("input", bid, {"source": "signal", "ref": references[bid]}, 0, 1, bid), 1)
        sig[bid] = port
    # a block read one sample late (code.blocks.<id>.delay) is read before it
    # is computed: its consumers link to the delay block, or in continuous
    # mode to a placeholder resolved once the block exists (as ngspice reads
    # the value directly)
    placeholders: dict[str, tuple[str, int]] = {}
    for st in pl.steps:
        if st.delay:
            if mode == "discrete":
                sig[st.id] = (b.add("delay", f"{st.id}_z", {"ts": pl.ts}, 1, 1, st.id), 1)
            else:
                sig[st.id] = placeholders[st.id] = (f"?{st.id}", 1)
                panel.warnings.append(f"{st.id}：code.blocks.{st.id}.delay 的一拍延时在连续模式下不建模（与 ngspice 相同）")
    for st in pl.steps:
        x = [sig[s] for s in st.srcs]
        if st.kind == "fn":
            if st.data["fn"] == "c":
                if mode != "discrete":
                    raise ClosedLoopError("C 代码方块需要离散控制器导出；请选择 discrete，采样率使用 code.fs")
                name = b.add("c",st.id,{**st.data,"ts":pl.ts},len(x),len(st.data["outs"]),st.id)
                for i, source in enumerate(x,1):
                    b.link(source,(name,i))
                for i, signal in enumerate(st.data["outs"],1):
                    sig[signal] = (name,i)
                continue
            outs = _function(b, st, dict(zip(st.data["names"], x)))
            for signal in st.data["outs"]:
                sig[signal] = outs[signal.split(".", 1)[1]]
            continue
        if st.kind == "copy":
            port = x[0]
        elif st.kind == "limit":
            port = b.limited(x[0], st.lo, st.hi, st.id)
        elif st.kind == "sum":
            port = b.limited(b.sum(st.id, list(zip(st.signs, x)), st.id), st.lo, st.hi, st.id)
        elif st.kind == "gain":
            port = b.limited(b.one("gain", st.id, x[0], {"k": st.data["k"]}, st.id), st.lo, st.hi, st.id)
        elif st.kind in ("mul", "div"):
            ops = "*" * len(x) if st.kind == "mul" else "*" + "/" * (len(x) - 1)
            port = b.limited(b.many("product", st.id, x, {"ops": ops}, st.id), st.lo, st.hi, st.id)
        elif st.kind == "pi":
            port = _pi(b, st, loop, mode, x[0])
        elif st.kind == "sos":
            port = _sos(b, st, loop, mode, x[0], panel.warnings)
        else:
            raise ClosedLoopError(f"{st.id}：无法导出的控制步骤 {st.kind}")
        if st.delay and mode == "discrete":
            b.link(port, (sig[st.id][0], 1))
            continue
        if st.delay:
            placeholders[st.id] = port
            continue
        sig[st.id] = port
    for bid, cfg in loop.outputs.items():
        duty = sig[bid]
        if cfg.get("modulation") == "vco":
            _vco(b, panel, bid, cfg, switches, duty)
            continue
        bridge = bool(cfg["complement"])
        if cfg["model"] == "average":
            panel.warnings.append(f"{bid}：桥臂 {cfg['switches'] + cfg['complement']} 的 average 模型在原生库里没有对应方块，"
                                  f"按逐开关模型导出（三角载波、死区 {cfg['dead_time']:g} s）")
        for sw in cfg["switches"]:
            _modulator(b, panel, sw, switches[sw], duty, bid, bridge, cfg["dead_time"], False)
        for sw in cfg["complement"]:
            _modulator(b, panel, sw, switches[sw], duty, bid, True, cfg["dead_time"], True)
    for lk in panel.links:
        if lk.src[0].startswith("?"):
            lk.src = placeholders[lk.src[0][1:]]
    panel.warnings += [w for w in loop.warnings if mode == "continuous" or w != CONTINUOUS_NOTE]
    _layout(panel)
    return panel


def _pi(b: _Builder, st, loop: Loop, mode: str, src: tuple[str, int]) -> tuple[str, int]:
    limited = st.lo is not None or st.hi is not None
    d, ts = st.data, loop.plan.ts
    kp, ki = d["kp"], (d["c0"] + d["c1"]) / ts          # c0 + c1 = ki Ts for every method
    if mode == "continuous":
        zpk = loop.tf[st.id]
        if not limited:
            return b.one("tf", st.id, src, {"num": zpk.num(), "den": zpk.den()}, st.id)
        return b.one("pi", st.id, src, {"kp": kp, "ki": ki, "lo": st.lo, "hi": st.hi}, st.id)
    if limited:
        return b.one("dpi", st.id, src, {"kp": kp, "ki": ki, "lo": st.lo, "hi": st.hi,
                                          "ts": ts, "method": d["method"]}, st.id)
    # y = kp e + I, I += c0 e[k] + c1 e[k-1]:  (kp + c0 + (c1 - kp) z^-1) / (1 - z^-1)
    return b.one("dtf", st.id, src, {"num": [d["kp"] + d["c0"], d["c1"] - d["kp"]], "den": [1.0, -1.0], "ts": ts},
                 st.id)


def _sos(b: _Builder, st, loop: Loop, mode: str, src: tuple[str, int], warnings: list[str]) -> tuple[str, int]:
    limited = st.lo is not None or st.hi is not None
    if mode == "continuous":
        zpk = loop.tf[st.id]
        if len(zpk.zeros) > len(zpk.poles):
            raise ClosedLoopError(f"{st.id}：零点多于极点（含纯微分），原生传递函数方块无法实现；给微分项加滤波（pid 的 fd）")
        port = b.one("tf", st.id, src, {"num": zpk.num(), "den": zpk.den()}, st.id)
        integrator = any(abs(p) < 1e-6 for p in zpk.poles)
    else:
        d = st.data["z"]
        port = b.one("dtf", st.id, src, {"num": d.num(), "den": d.den(), "ts": loop.plan.ts}, st.id)
        integrator = any(abs(p - 1) < 1e-9 for p in d.poles)
    if limited and integrator:
        warnings.append(f"{st.id}：传递函数后接限幅，积分环节没有抗饱和（ngspice 模型有反算抗饱和，DSP 代码钳位节状态）")
    return b.limited(port, st.lo, st.hi, st.id)


def _vco(b: _Builder, panel: Panel, out: str, cfg: dict,
         switches: dict[str, tuple[str, dict]], command: tuple[str, int]) -> None:
    """One continuously integrated phase per output, including sampled control.

    Delaying turn-on (min of each raw pulse and its time-delayed copy) keeps
    dead time in seconds when the command changes partway through a cycle.
    The complete SPICE validation has already checked group delays and timing.
    """
    frequency = b.limited(command, cfg["frequency_min"], cfg["frequency_max"], f"{out}_frequency")
    if panel.mode == "discrete":
        frequency = b.one("dtf", f"{out}_frequency_hold", frequency,
                          {"num": [1.0], "den": [1.0], "ts": panel.ts})
    delay = switches[cfg["switches"][0]][1]["delay"]
    enable = (b.add("step", f"{out}_enable", {"time": delay}, 0, 1), 1)
    rate = b.many("product", f"{out}_rate", [frequency, enable], {"ops": "**"})
    phase = b.one("tf", f"{out}_phase", rate, {"num": [1.0], "den": [1.0, 0.0]})
    wave = b.one("fcn", f"{out}_wave", phase, {"expr": f"sin({TWO_PI!r}*{{u1}})", "n": 1})
    zero = b.const(0.0)
    for suffix, group, inputs in (("high", cfg["switches"], [wave, zero]),
                                   ("low", cfg["complement"], [zero, wave])):
        if not group:
            continue
        raw = b.many("compare", f"{out}_{suffix}_raw", inputs)
        gate = b.many("product", f"{out}_{suffix}_enabled", [raw, enable], {"ops": "**"})
        if cfg["dead_time"]:
            delayed = b.one("pulse_delay", f"{out}_{suffix}_delay", gate, {"seconds": cfg["dead_time"]})
            gate = b.many("minmax", f"{out}_{suffix}_gate", [gate, delayed], {"op": "min"})
        for sw in group:
            panel.exports[switches[sw][0]] = gate
    panel.warnings.append(f"{out}：VCO 使用 Hz 限幅、共享连续相位和秒死区；原生开关采用理想边沿，"
                          "与 ngspice 的有限 rise/fall 边沿存在差别。")


def _modulator(b: _Builder, panel: Panel, sw: str, drive: tuple[str, dict], duty: tuple[str, int], out: str,
               triangle: bool, dead: float, low: bool) -> None:
    """PWM of one switch from the controller's duty (0..1), as closed_loop.modulator."""
    signal, val = drive
    period = 1 / val["frequency"]
    if not triangle:
        carrier = b.carrier(val["frequency"], "saw", val["delay"])
        gate = b.many("compare", f"{sw}_pwm", [duty, carrier], {}, sw)
    else:
        carrier = b.carrier(val["frequency"], "tri", val["delay"])
        margin = dead / period
        edge = (val["rise"] + val["fall"]) / 2
        lo = margin + MIN_PULSE_EDGES * 2 * edge / period
        clamped = b.limited(duty, lo, 1 - lo, f"{out}_{sw}")
        if margin:
            shifted = b.sum(f"{sw}_shift", [(1.0, clamped), ((1.0 if low else -1.0), b.const(margin))])
        else:
            shifted = clamped
        gate = b.many("compare", f"{sw}_pwm", [carrier, shifted] if low else [shifted, carrier], {}, sw)
    if val["delay"] > 0:
        enable = (b.add("step", f"{sw}_enable", {"time": val["delay"]}, 0, 1), 1)
        gate = b.many("product", f"{sw}_gate", [gate, enable], {"ops": "**"})
    panel.exports[signal] = gate


# ---- placement -------------------------------------------------------------------
def _layout(panel: Panel) -> None:
    """Columns by signal depth, left to right; a column's blocks stacked in
    creation order.  Back edges (a loop inside the controller) are ignored,
    so every block has a finite depth."""
    into: dict[str, list[str]] = {b.name: [] for b in panel.blocks}
    for lk in panel.links:
        into[lk.dst[0]].append(lk.src[0])
    depth: dict[str, int] = {}
    for blk in panel.blocks:
        feeders = [depth[s] for s in into[blk.name] if s in depth]
        depth[blk.name] = 1 + max(feeders) if feeders else 0
    rows: dict[int, int] = {}
    for blk in panel.blocks:
        col = depth[blk.name]
        row = rows.get(col, 0)
        # Simulink keeps custom C state in separate delays below the C Function.
        rows[col] = row + 1 + (len(blk.params['c']['states']) if blk.kind == 'c' else 0)
        blk.pos = (col * COLUMN, row * ROW)
    panel.size = ((max(depth.values(), default=0) + 1) * COLUMN, max(rows.values(), default=0) * ROW)


def describe(panel: Panel) -> dict[str, Any]:
    """The manifest's record of the controller: JSON only."""
    return {"mode": panel.mode, "ts": panel.ts,
            "blocks": [{"name": b.name, "kind": b.kind, "label": b.label, "params": b.params,
                        "inputs": b.inputs, "outputs": b.outputs, "pos": list(b.pos)} for b in panel.blocks],
            "links": [{"from": list(lk.src), "to": list(lk.dst)} for lk in panel.links],
            "exports": {k: list(v) for k, v in panel.exports.items()},
            "warnings": list(panel.warnings)}
