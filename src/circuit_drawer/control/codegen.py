"""Control diagram -> embedded C: the controller part of a diagram, its
transfer functions discretised, as fixed-point (int32 Q format) or
floating-point C for a DSP or MCU.

Settings live in the table's ``code`` section::

    fs        sample rate in Hz (required)
    method    tustin (default) | zoh | backward | forward, for every block
    arith     fixed (default, int32 signals in Q``q``) | float | double
    q         fraction bits of a fixed-point signal (default 24: |x| < 128,
              so scale signals to per unit or volts/amps of that size)
    name      C identifier prefix (default: the diagram's name)
    external  drawn blocks that are not code (power stage, sensors); their
              outputs become inputs of the step function
    outputs   blocks whose values the step function returns (default: those
              read by an external block or a loop.plant entry, else the ends
              of open output lines)
    blocks    per-block settings: {"min", "max"} output limits (the limiter's
              range, a PI's anti-windup clamp), "method", "prewarp" (Hz),
              "num" (a divider's numerator port), and "delay": 1 to read the
              block's value from the previous sample, which breaks a loop
              inside the controller (a PLL's integrator feeding its own Park)

Function blocks (the table's ``functions`` section: Clarke, Park, SVPWM, ...;
see ``functions.py``) become one step with several outputs, named
``block.output`` in ``inputs`` / ``outputs`` / ``external`` and ``block_output``
in C.  They need floating point (``arith`` float or double).

The structure is the drawing's, read through ``loop.Network``: one value per
block output, evaluated in signal order.  ``simulate`` runs the same plan in
Python double precision; the tests compile the C and compare against it.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from .discrete import METHODS, DiscreteError, discretize, quantization_error, quantize, sections
from .loop import LoopError, Network, Tf, evaluate, parse_loop

CODE_KEYS = ("fs", "method", "arith", "q", "name", "external", "outputs", "blocks", "note")
BLOCK_KEYS = ("min", "max", "method", "prewarp", "num", "delay")
ARITHS = ("fixed", "float", "double")
DEFAULT_Q = 24
QUANT_WARN_DB = 0.1        # coefficient rounding worse than this is worth a warning
_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class CodeError(ValueError):
    pass


@dataclass
class Step:
    """One block's line of code: ``kind`` decides what ``data`` holds."""
    id: str
    kind: str                       # input | sum | gain | sos | pi | limit | copy | mul | div
    srcs: list[str] = field(default_factory=list)
    signs: list[float] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    lo: float | None = None
    hi: float | None = None
    note: str = ""
    delay: bool = False             # its value is read one sample late


@dataclass
class Plan:
    name: str
    fs: float
    method: str
    arith: str
    q: int
    inputs: list[str]
    outputs: list[str]
    steps: list[Step]
    warnings: list[str] = field(default_factory=list)
    source: str = ""

    @property
    def ts(self) -> float:
        return 1 / self.fs


# ---- the code section ------------------------------------------------------------
def parse_code(raw: Any, dia, env: dict[str, float]) -> tuple[dict | None, list[str]]:
    """Schema of the ``code`` section: (settings, errors)."""
    if raw is None:
        return None, []
    if not isinstance(raw, dict):
        return None, ["code must be an object with fs, method, arith, q, blocks, ..."]
    errors = []
    unknown = sorted(set(raw) - set(CODE_KEYS))
    if unknown:
        errors.append(f"code: unknown keys {unknown}; allowed are {list(CODE_KEYS)}")
    ids = {b.id: b for b in dia.blocks}
    from .functions import parse as parse_functions
    fns, _ = parse_functions(dia.raw.get("functions") if isinstance(dia.raw, dict) else None, dia)
    signals = set(ids) | {f.signal(o) for f in fns.values() for o in f.outs}
    out: dict[str, Any] = {"blocks": {}}
    try:
        out["fs"] = evaluate(raw.get("fs"), env, "code.fs") if "fs" in raw else None
        if out["fs"] is None or out["fs"] <= 0:
            errors.append("code.fs: give the sample rate in Hz, e.g. \"fs\": 100e3")
    except LoopError as exc:
        errors.append(str(exc))
    out["method"] = raw.get("method", "tustin")
    if out["method"] not in METHODS:
        errors.append(f"code.method must be one of {list(METHODS)}")
    out["arith"] = raw.get("arith", "fixed")
    if out["arith"] not in ARITHS:
        errors.append(f"code.arith must be one of {list(ARITHS)}")
    q = raw.get("q", DEFAULT_Q)
    if isinstance(q, bool) or not isinstance(q, int) or not 4 <= q <= 30:
        errors.append("code.q must be a whole number of fraction bits from 4 to 30")
    out["q"] = q
    name = raw.get("name") or re.sub(r"\W", "_", dia.name) + "_ctl"
    if not isinstance(name, str) or not _ID.match(name):
        errors.append(f"code.name {name!r} is not a C identifier")
    out["name"] = name
    for key in ("external", "outputs"):
        v = raw.get(key) or []
        if not isinstance(v, list) or any(x not in signals for x in v):
            errors.append(f"code.{key} must list blocks in the drawing (a function block's "
                          f"output as block.output)")
            v = []
        out[key] = list(v)
    blocks = raw.get("blocks") or {}
    if not isinstance(blocks, dict):
        errors.append("code.blocks must be an object of block id: settings")
        blocks = {}
    for bid, d in blocks.items():
        what = f"code.blocks.{bid}"
        if bid not in ids:
            errors.append(f"{what}: no block {bid!r} in the drawing")
            continue
        if not isinstance(d, dict):
            errors.append(f"{what} must be an object such as {{\"min\": 0, \"max\": 0.95}}")
            continue
        bad = sorted(set(d) - set(BLOCK_KEYS))
        if bad:
            errors.append(f"{what}: unknown keys {bad}; allowed are {list(BLOCK_KEYS)}")
        s: dict[str, Any] = {}
        try:
            for k in ("min", "max", "prewarp"):
                if k in d:
                    s[k] = evaluate(d[k], env, f"{what}.{k}")
        except LoopError as exc:
            errors.append(str(exc))
        if "min" in s and "max" in s and s["min"] >= s["max"]:
            errors.append(f"{what}: min must be below max")
        if "method" in d:
            if d["method"] not in METHODS:
                errors.append(f"{what}.method must be one of {list(METHODS)}")
            s["method"] = d["method"]
        if "num" in d:
            s["num"] = d["num"]
        if "delay" in d:
            if d["delay"] not in (0, 1) or isinstance(d["delay"], bool):
                errors.append(f"{what}.delay must be 0 or 1 (samples)")
            s["delay"] = d["delay"]
        out["blocks"][bid] = s
    return out, errors


# ---- planning ------------------------------------------------------------------
def plan(rep) -> Plan:
    dia = rep.diagram
    raw_loop = dia.raw.get("loop") or {}
    spec, errors = parse_loop(raw_loop, dia)
    cfg, cerr = parse_code(dia.raw.get("code"), dia, spec.params if spec else {})
    if cfg is None:
        raise CodeError("表里没有 code 段，无法生成代码（the table has no code section）；请加 \"code\": {\"fs\": ...}")
    if errors or cerr:
        raise CodeError("; ".join(errors + cerr))
    from .functions import KINDS, parse as parse_functions, wiring
    fns, ferr = parse_functions(dia.raw.get("functions"), dia)
    wiring(fns, dia.links, ferr)
    if ferr:
        raise CodeError("; ".join(ferr))
    net = Network(rep, spec)
    blocks = net.blocks
    external = set(cfg["external"]) | {b for b, tf in spec.tf.items() if tf.form == "model"}
    code_ids = [b.id for b in dia.blocks if b.id not in external]

    def reads(bid: str) -> list[tuple[str, str]]:
        """(port, source) for a code block, the drawing's lines only."""
        return net.inputs.get(bid, [])

    inputs = [b for b in code_ids if blocks[b].type == "tag"
              or (not reads(b) and blocks[b].type not in ("sum", "switch"))]
    inputs += [b for b in dia.blocks if b.id in external and
               any(src == b.id for c in code_ids for _, src in reads(c))]
    inputs = [b if isinstance(b, str) else b.id for b in inputs]
    outputs = cfg["outputs"] or _default_outputs(rep, net, spec, code_ids, external)
    if fns and pl_arith_fixed(cfg):
        raise CodeError(f"function blocks {sorted(fns)} need floating point: set code.arith "
                        f"to \"float\" or \"double\"")
    if not outputs:
        raise CodeError("no outputs: list them in code.outputs")
    pl = Plan(cfg["name"], cfg["fs"], cfg["method"], cfg["arith"], cfg["q"], inputs, outputs, [])
    pl.source = dia.name
    steps: dict[str, Step] = {}
    for bid in code_ids:
        if bid in inputs:
            continue
        if bid in fns:
            steps[bid] = _fn_step(fns[bid], KINDS[fns[bid].kind], reads(bid))
            continue
        steps[bid] = _step(blocks[bid], reads(bid), spec.tf.get(bid), cfg, pl)
        if cfg["blocks"].get(bid, {}).get("delay"):
            steps[bid].delay = True
            steps[bid].note = (steps[bid].note + "; " if steps[bid].note else "") + \
                "read one sample late (code.blocks.delay)"
    pl.steps = _order(steps, set(inputs) | {b for b, st in steps.items() if st.delay})
    needed = _needed(pl, outputs)
    pl.steps = [s for s in pl.steps if s.id in needed]
    pl.inputs = [i for i in pl.inputs if i in needed]
    return pl


def pl_arith_fixed(cfg) -> bool:
    return cfg["arith"] == "fixed"


def _fn_step(fn, kind, ins: list[tuple[str, str]]) -> Step:
    """One function block: its inputs in the kind's order, one value per output."""
    given = {}
    for name, src in ins:
        if name not in kind.inputs:
            raise CodeError(f"{fn.block}: a line ends at port {name.lstrip('?')} that is not "
                            f"one of its inputs {list(fn.ins)}")
        if name in given:
            raise CodeError(f"{fn.block}: two lines feed its input {name}")
        given[name] = src
    names = [n for n in kind.inputs if n in fn.ins]
    missing = [n for n in names if n not in given]
    if missing:
        raise CodeError(f"{fn.block}: nothing drives its inputs {missing}")
    return Step(fn.block, "fn", [given[n] for n in names],
                data={"fn": fn.kind, "names": names, "outs": [fn.signal(o) for o in fn.outs]},
                note=f"{fn.kind}: {kind.doc}")


def _owner(signal: str) -> str:
    return signal.split(".", 1)[0]


def cid(signal: str) -> str:
    """A C identifier for a signal: park.d -> park_d."""
    return signal.replace(".", "_")


def _default_outputs(rep, net, spec, code_ids, external) -> list[str]:
    outs = [p.src for p in spec.plant if _owner(p.src) in code_ids]
    outs += [src for b in external for _, src in net.inputs.get(b, []) if _owner(src) in code_ids]
    if not outs:
        g = rep.geometry
        for lk, path in zip(rep.diagram.links, g.paths):
            if lk.open and path and lk.start.block in code_ids:
                outs.append(lk.start.block)
    return list(dict.fromkeys(outs))


def _step(b, ins: list[tuple[str, str]], tf: Tf | None, cfg: dict, pl: Plan) -> Step:
    s = cfg["blocks"].get(b.id, {})
    lo, hi = s.get("min"), s.get("max")
    if b.type == "sum":
        return Step(b.id, "sum", [src for _, src in ins],
                    [1.0 if b.signs.get(p) == "+" else -1.0 for p, _ in ins], lo=lo, hi=hi)
    if b.type == "switch":
        src = [x for p, x in ins if p == b.on] or [x for p, x in ins if p == "c"]
        if len(src) != 1:
            raise CodeError(f"{b.id}: the switch's active contact {b.on!r} has no input")
        return Step(b.id, "copy", src, note=f"switch on {b.on}")
    if b.type == "mul":
        return Step(b.id, "mul", [src for _, src in ins], lo=lo, hi=hi)
    if b.type == "div":
        num = s.get("num")
        tops = [x for p, x in ins if p == num]
        if len(tops) != 1:
            raise CodeError(f"{b.id}: say which input is the numerator with "
                            f"code.blocks.{b.id}.num (one of {[p for p, _ in ins]})")
        return Step(b.id, "div", tops + [x for p, x in ins if p != num], lo=lo, hi=hi)
    if tf is not None and tf.port is not None:
        ins = [(p, x) for p, x in ins if p == tf.port]
    if len(ins) != 1:
        raise CodeError(f"{b.id} has {len(ins)} inputs; code needs one (loop.tf.{b.id}.port "
                        f"picks it)")
    src = [ins[0][1]]
    if b.type == "limiter" and tf is None:
        if lo is None or hi is None:
            raise CodeError(f"{b.id}: a limiter needs its range in code.blocks.{b.id} "
                            f"(\"min\" and \"max\")")
        return Step(b.id, "limit", src, lo=lo, hi=hi)
    if tf is None:
        raise CodeError(f"{b.id} has no transfer function; add loop.tf.{b.id}")
    note = ""
    if tf.delay:
        note = f"delay {tf.delay:g} s not generated (it models sampling/PWM delay)"
        pl.warnings.append(f"{b.id}: {note}")
    if not tf.zpk.poles and not tf.zpk.zeros:
        return Step(b.id, "gain", src, data={"k": tf.zpk.k}, lo=lo, hi=hi, note=note)
    method = s.get("method", cfg["method"])
    prewarp = s.get("prewarp")
    if prewarp is None and tf.form == "pr" and method == "tustin":
        prewarp = tf.values["f0"]        # keep the resonance exactly at f0
    if tf.form == "pi" and "prewarp" not in s:
        kp, ki, ts = tf.values["kp"], tf.values["ki"], pl.ts
        c0, c1 = {"tustin": (ki * ts / 2, ki * ts / 2), "backward": (ki * ts, 0.0),
                  "forward": (0.0, ki * ts), "zoh": (0.0, ki * ts)}[method]
        return Step(b.id, "pi", src, data={"kp": kp, "c0": c0, "c1": c1, "method": method},
                    lo=lo, hi=hi, note=note)
    try:
        d = discretize(tf.zpk, pl.ts, method, prewarp)
        sos = sections(d)
    except DiscreteError as exc:
        raise CodeError(f"{b.id}: {exc}") from None
    data = {"sos": sos, "method": method, "prewarp": prewarp, "z": d}
    if pl.arith == "fixed":
        try:
            ints, qsos = quantize(sos)
        except DiscreteError as exc:
            raise CodeError(f"{b.id}: {exc}") from None
        err_db, err_deg = quantization_error(sos, qsos, pl.ts, pl.fs * 1e-5)
        data.update(ints=ints, err_db=err_db, err_deg=err_deg)
        if err_db > QUANT_WARN_DB:
            pl.warnings.append(f"{b.id}: fixed-point coefficients change the response by up "
                               f"to {err_db:.2f} dB; try a lower fs or tustin")
    return Step(b.id, "sos", src, data=data, lo=lo, hi=hi, note=note)


def _order(steps: dict[str, Step], known: set[str]) -> list[Step]:
    out, done = [], set(known)
    pending = dict(steps)
    while pending:
        ready = [bid for bid, st in pending.items() if all(s in done for s in st.srcs)]
        if not ready:
            raise CodeError("the controller has an algebraic loop through "
                            f"{sorted(pending)}; a loop inside the code needs a block with "
                            f"dynamics or a delay")
        for bid in ready:
            st = pending.pop(bid)
            out.append(st)
            done.add(bid)
            done.update(st.data.get("outs", []) if st.kind == "fn" else [])
    return out


def _needed(pl: Plan, outputs: list[str]) -> set[str]:
    by_id = {s.id: s for s in pl.steps}
    need, stack = set(), list(outputs)
    while stack:
        b = stack.pop()
        if b in need:
            continue
        need.add(b)
        if b not in by_id and _owner(b) in by_id:
            stack.append(_owner(b))
        if b in by_id:
            stack += by_id[b].srcs
    return need


# ---- reference simulation (double precision) ------------------------------------
def simulate(pl: Plan, inputs: dict[str, list[float]]) -> dict[str, list[float]]:
    n = len(next(iter(inputs.values()))) if inputs else 0
    state: dict[str, Any] = {}
    out: dict[str, list[float]] = {o: [] for o in pl.outputs}
    for k in range(n):
        v = {i: inputs[i][k] for i in pl.inputs}
        v.update({st.id: state.get(st.id + "#z", 0.0) for st in pl.steps if st.delay})
        for st in pl.steps:
            if st.kind == "fn":
                from .functions import evaluate
                res = evaluate(st.data["fn"], {n: v[s] for n, s in zip(st.data["names"], st.srcs)})
                v.update({f"{st.id}.{k}": res[k] for k in res})
                continue
            if st.delay:
                state[st.id + "#z"] = _run(st, v, state)
                continue
            v[st.id] = _run(st, v, state)
        for o in pl.outputs:
            out[o].append(v[o])
    return out


def _clamp(x: float, lo, hi) -> float:
    if lo is not None and x < lo:
        return lo
    if hi is not None and x > hi:
        return hi
    return x


def _run(st: Step, v: dict, state: dict) -> float:
    x = [v[s] for s in st.srcs]
    if st.kind == "sum":
        return _clamp(sum(g * a for g, a in zip(st.signs, x)), st.lo, st.hi)
    if st.kind == "copy":
        return x[0]
    if st.kind == "mul":
        return _clamp(math.prod(x), st.lo, st.hi)
    if st.kind == "div":
        den = math.prod(x[1:])
        return _clamp(x[0] / den if den else 0.0, st.lo, st.hi)
    if st.kind == "limit":
        return _clamp(x[0], st.lo, st.hi)
    if st.kind == "gain":
        return _clamp(st.data["k"] * x[0], st.lo, st.hi)
    if st.kind == "pi":
        s = state.setdefault(st.id, {"i": 0.0, "e1": 0.0})
        d = st.data
        s["i"] = _clamp(s["i"] + d["c0"] * x[0] + d["c1"] * s["e1"], st.lo, st.hi)
        s["e1"] = x[0]
        return _clamp(d["kp"] * x[0] + s["i"], st.lo, st.hi)
    secs = st.data["sos"].sections
    s = state.setdefault(st.id, [[0.0] * 4 for _ in secs])
    u = x[0] * st.data["sos"].gain
    for i, (sec, w) in enumerate(zip(secs, s)):
        y = (sec.b[0] * u + sec.b[1] * w[0] + sec.b[2] * w[1]
             - sec.a[1] * w[2] - sec.a[2] * w[3])
        if i == len(secs) - 1:
            y = _clamp(y, st.lo, st.hi)
        w[1], w[0], w[3], w[2] = w[0], u, w[2], y
        u = y
    return u


# ---- C ---------------------------------------------------------------------------
def _f(v: float) -> str:
    return repr(float(v))


def _ctype(pl: Plan) -> str:
    return {"fixed": "int32_t", "float": "float", "double": "double"}[pl.arith]


def _ints(vals: list[float]) -> tuple[list[int], int]:
    """Coefficients as integers sharing one Q (see ``discrete.coef_q``)."""
    from .discrete import coef_q
    q = coef_q(vals)
    return [int(round(v * (1 << q))) for v in vals], q


def emit_c(pl: Plan) -> dict[str, str]:
    """File name -> text: <name>.h, <name>.c and the shared cg_lib.h."""
    from .clib import LIB
    n, N, t = pl.name, pl.name.upper(), _ctype(pl)
    fixed = pl.arith == "fixed"
    sfx = "" if fixed else ("f" if pl.arith == "float" else "d")
    lit = "f" if pl.arith == "float" else ""

    def bound(v: float | None, high: bool) -> str:
        if v is None:
            return ("INT32_MAX" if high else "INT32_MIN") if fixed else \
                ("CG_INF" if high else "-CG_INF")
        return f"{N}_Q({_f(v)})" if fixed else _f(v) + lit

    head = [f"/* {n}: generated by circuit-drawer-control from '{pl.source}'.",
            f" * fs = {pl.fs:g} Hz (Ts = {pl.ts:.6g} s), {pl.method}; "
            + (f"fixed point, int32 signals in Q{pl.q} (|x| < {2 ** (31 - pl.q)})."
               if fixed else f"{pl.arith} arithmetic."),
            " * Do not edit: change the diagram's loop / code sections and generate again. */"]
    h = head + ["", f"#ifndef {N}_H", f"#define {N}_H", "", '#include "cg_lib.h"', "",
                f"#define {N}_FS {_f(pl.fs)}"]
    if fixed:
        h += [f"#define {N}_QBITS {pl.q}",
              f"/* real value <-> Q{pl.q} */",
              f"#define {N}_Q(x) CG_QN((x), {N}_QBITS)",
              f"#define {N}_REAL(x) ((double)(x) / (double)((int64_t)1 << {N}_QBITS))"]
    h += ["", "typedef struct {"] + [f"    {t} {cid(i)};" for i in pl.inputs] + [f"}} {n}_in_t;", ""]
    h += ["typedef struct {"] + [f"    {t} {cid(o)};" for o in pl.outputs] + [f"}} {n}_out_t;", ""]
    state = []
    for st in pl.steps:
        if st.kind == "pi":
            state.append(f"    cg_pi{sfx}_t {st.id};")
        elif st.kind == "sos":
            state.append(f"    cg_sos{sfx}_t {st.id}[{len(st.data['sos'].sections)}];")
        if st.delay:
            state.append(f"    {t} {st.id}_z;            /* last sample's {st.id} */")
    h += ["typedef struct {"] + (state or ["    int unused;"]) + [f"}} {n}_t;", ""]
    h += [f"void {n}_init({n}_t *s);",
          f"void {n}_step({n}_t *s, const {n}_in_t *in, {n}_out_t *out);", "",
          f"#endif /* {N}_H */", ""]

    c = head + ["", f'#include "{n}.h"', "#include <string.h>"]
    c += ["#include <math.h>", ""] if any(st.kind == "fn" for st in pl.steps) else [""]
    for st in pl.steps:
        if st.kind != "sos":
            continue
        secs = st.data["sos"].sections
        what = f"{st.id}: {st.data['method']}" + \
            (f", prewarp {st.data['prewarp']:g} Hz" if st.data.get("prewarp") else "")
        if fixed:
            rows = ", ".join("{" + ", ".join(str(v) for v in ints) + "}"
                             for ints, _ in st.data["ints"])
            qs = ", ".join(str(q) for _, q in st.data["ints"])
            c += [f"/* {what}.  Per section b0 b1 b2 a1 a2 in Q{{{qs}}}; rounding moves the "
                  f"response by at most {st.data['err_db']:.2g} dB / {st.data['err_deg']:.2g} deg */",
                  f"static const int32_t {st.id.upper()}_C[{len(secs)}][5] = {{{rows}}};",
                  f"static const int {st.id.upper()}_QC[{len(secs)}] = {{{qs}}};", ""]
        else:
            rows = ", ".join("{" + ", ".join(_f(v) + lit for v in sec.coeffs()) + "}"
                             for sec in secs)
            c += [f"/* {what}.  Per section b0 b1 b2 a1 a2 */",
                  f"static const {t} {st.id.upper()}_C[{len(secs)}][5] = {{{rows}}};", ""]
    c += [f"void {n}_init({n}_t *s)", "{", "    memset(s, 0, sizeof *s);", "}", "",
          f"void {n}_step({n}_t *s, const {n}_in_t *in, {n}_out_t *out)", "{"]
    if not state:
        c.append("    (void)s;")
    ref = {i: f"in->{cid(i)}" for i in pl.inputs}
    for st in pl.steps:
        if st.delay:
            c.append(f"    const {t} {st.id} = s->{st.id}_z;   /* last sample */")
            ref[st.id] = st.id
    for st in pl.steps:
        x = [ref[src] for src in st.srcs]
        if st.kind == "fn":
            from .functions import expressions
            c.append(f"    /* {st.id}: {st.note} */")
            exprs = expressions(st.data["fn"], dict(zip(st.data["names"], x)),
                                "c" if pl.arith == "float" else "cd")
            for out in st.data["outs"]:
                c.append(f"    const {t} {cid(out)} = {exprs[out.split('.', 1)[1]]};")
                ref[out] = cid(out)
            continue
        lo, hi = bound(st.lo, False), bound(st.hi, True)
        if st.note:
            c.append(f"    /* {st.id}: {st.note} */")
        lhs = f"    s->{st.id}_z =" if st.delay else f"    const {t} {st.id} ="

        def clamp(expr: str) -> str:
            if st.lo is None and st.hi is None:
                return expr
            return f"cg_clamp{sfx}({expr}, {lo}, {hi})"
        if st.kind == "sum":
            cast = "(int64_t)" if fixed else ""
            terms = " ".join(("+ " if g > 0 else "- ") + cast + a for g, a in zip(st.signs, x))
            terms = terms[2:] if terms.startswith("+ ") else "-" + terms[2:]
            expr = f"cg_sat32({terms})" if fixed else f"({terms})"
            c.append(f"{lhs} {clamp(expr)};")
        elif st.kind == "copy":
            c.append(f"{lhs} {x[0]};")
        elif st.kind == "limit":
            c.append(f"{lhs} cg_clamp{sfx}({x[0]}, {lo}, {hi});")
        elif st.kind == "gain":
            kv = st.data["k"]
            if fixed:
                (ki,), qk = _ints([kv])
                expr = f"cg_gain({x[0]}, {ki}, {qk})"
            else:
                expr = f"{_f(kv)}{lit} * {x[0]}"
            c.append(f"{lhs} {clamp(expr)};   /* x {kv:g} */")
        elif st.kind in ("mul", "div"):
            qarg = f", {N}_QBITS" if fixed else ""
            den = x[1] if st.kind == "div" else None
            if st.kind == "div":
                for a in x[2:]:
                    den = f"cg_mul{sfx}({den}, {a}{qarg})"
                expr = f"cg_div{sfx}({x[0]}, {den}{qarg})"
            else:
                expr = x[0]
                for a in x[1:]:
                    expr = f"cg_mul{sfx}({expr}, {a}{qarg})"
            c.append(f"{lhs} {clamp(expr)};")
        elif st.kind == "pi":
            d = st.data
            c.append(f"    /* {st.id}: PI, kp = {d['kp']:g}, integrator gains {d['c0']:.6g} "
                     f"(e[k]) + {d['c1']:.6g} (e[k-1]), {d['method']} */")
            if fixed:
                (kp, c0, c1), qk = _ints([d["kp"], d["c0"], d["c1"]])
                args = f"{kp}, {c0}, {c1}, {qk}"
            else:
                args = ", ".join(_f(d[k]) + lit for k in ("kp", "c0", "c1"))
            c.append(f"{lhs} cg_pi{sfx}(&s->{st.id}, {args}, {x[0]}, {lo}, {hi});")
        elif st.kind == "sos":
            nsec = len(st.data["sos"].sections)
            qc = f", {st.id.upper()}_QC" if fixed else ""
            c.append(f"{lhs} cg_sos_chain{sfx}(s->{st.id}, {st.id.upper()}_C{qc}, {nsec}, "
                     f"{x[0]}, {lo}, {hi});")
        ref[st.id] = st.id
    c += [f"    out->{cid(o)} = {ref[o]};" for o in pl.outputs] + ["}", ""]
    return {f"{n}.h": "\n".join(h), f"{n}.c": "\n".join(c), "cg_lib.h": LIB}


def harness(pl: Plan) -> str:
    """A PC test program: reads one line of real input values per sample
    (in the order of the input struct) and prints the outputs as reals."""
    n, N, fixed = pl.name, pl.name.upper(), pl.arith == "fixed"
    conv = (lambda v: f"{N}_Q({v})") if fixed else (lambda v: v)
    back = (lambda v: f"{N}_REAL({v})") if fixed else (lambda v: f"(double){v}")
    reads = "".join(f" %lf" for _ in pl.inputs).strip()
    args = ", ".join(f"&v[{i}]" for i in range(len(pl.inputs)))
    lines = [f"/* {n}_test: feeds stdin samples to {n}_step and prints the outputs. */",
             "#include <stdio.h>", f'#include "{n}.h"', "", "int main(void)", "{",
             f"    {n}_t s; {n}_in_t in; {n}_out_t out;",
             f"    double v[{max(1, len(pl.inputs))}];", f"    {n}_init(&s);"]
    if pl.inputs:
        lines.append(f'    while (scanf("{reads}", {args}) == {len(pl.inputs)}) {{')
    else:
        lines.append("    for (int k = 0; k < 1000; k++) {")
    lines += [f"        in.{cid(i)} = {conv(f'v[{k}]')};" for k, i in enumerate(pl.inputs)]
    lines.append(f"        {n}_step(&s, &in, &out);")
    lines.append('        printf("' + " ".join("%.9g" for _ in pl.outputs) + '\\n", '
                 + ", ".join(back(f"out.{cid(o)}") for o in pl.outputs) + ");")
    lines += ["    }", "    return 0;", "}", ""]
    return "\n".join(lines)


def report(pl: Plan) -> dict[str, Any]:
    """What was generated, per block: the discrete coefficients and Q formats."""
    blocks = {}
    for st in pl.steps:
        d: dict[str, Any] = {"kind": st.kind, "inputs": st.srcs}
        if st.lo is not None or st.hi is not None:
            d["limits"] = [st.lo, st.hi]
        if st.kind == "gain":
            d["k"] = st.data["k"]
        if st.kind == "fn":
            d.update(fn=st.data["fn"], outputs=st.data["outs"])
        if st.kind == "pi":
            d.update({k: st.data[k] for k in ("kp", "c0", "c1", "method")})
        if st.kind == "sos":
            dz = st.data["z"]
            d.update(method=st.data["method"], prewarp_hz=st.data["prewarp"],
                     num=[round(v, 12) for v in dz.num()], den=[round(v, 12) for v in dz.den()],
                     sections=[s.coeffs() for s in st.data["sos"].sections])
            if "ints" in st.data:
                d.update(fixed=[{"c": ints, "q": q} for ints, q in st.data["ints"]],
                         rounding_db=st.data["err_db"], rounding_deg=st.data["err_deg"])
        if st.note:
            d["note"] = st.note
        if st.delay:
            d["delay"] = 1
        blocks[st.id] = d
    return {"name": pl.name, "fs": pl.fs, "method": pl.method, "arith": pl.arith,
            "q": pl.q if pl.arith == "fixed" else None, "inputs": pl.inputs,
            "outputs": pl.outputs, "blocks": blocks, "warnings": pl.warnings}


def check_code_section(raw: dict, dia) -> list[str]:
    """Schema errors of ``code`` for the checker."""
    spec, _ = parse_loop(raw.get("loop") or {}, dia)
    return parse_code(raw.get("code"), dia, spec.params if spec else {})[1]


def write_code(raw: dict, out_dir, with_harness: bool = False) -> dict[str, Any]:
    """Check the table, plan and write the C files plus a JSON report."""
    import json
    from pathlib import Path
    from ..profiles import get_profile
    from .check import check
    from .render import Rejected
    rep = check(raw, get_profile(str(raw.get("profile") or "ieee-double")))
    if not rep.ok:
        raise Rejected(rep)
    pl = plan(rep)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = emit_c(pl)
    if with_harness:
        files[f"{pl.name}_test.c"] = harness(pl)
    files[f"{pl.name}_report.json"] = json.dumps(report(pl), indent=1, ensure_ascii=False) + "\n"
    written = []
    for name, text in files.items():
        (out / name).write_text(text, encoding="utf-8")
        written.append(str(out / name))
    return {"files": written, "warnings": pl.warnings, "report": report(pl)}
