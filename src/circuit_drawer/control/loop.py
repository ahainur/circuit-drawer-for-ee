"""Loop design on a control diagram: block transfer functions, loop gain,
closed-loop response, margins and Type I/II/III compensator suggestions.

The table's ``loop`` section gives the numbers; the drawing (blocks and
links) gives the structure.  Nothing here moves or adds anything to the
drawing: the signal graph is read from the checker's resolved link paths.

``loop`` section::

    params   {"Vin": 12, "L": "10e-6", ...}  numbers or formulas of earlier params
    tf       {block id: transfer function}   see TF_FORMS
    plant    [{"from": "pwm", "to": "vo", <transfer function>}, ...]
             the power stage the drawing leaves out: from a block's output
             to a measured-signal tag
    breaks   [block id | {"at": id, "label": ...}]  where the loop is cut to
             read the loop gain (default: the first plant entry's "from")
    closed   [{"from": ref tag, "to": block id, "label": ...}]
    freq     [f_min, f_max] in Hz for sweeps and the Bode plot

Every number may be a formula over ``params`` (``"1/VM"``, ``"2*pi*50"``).
"""

from __future__ import annotations

import ast
import cmath
import math
import operator
from dataclasses import dataclass, field
from typing import Any, Callable

from .lti import Zpk, pade, solve
from .model import BOX_TYPES, POLE, Diagram

TWO_PI = 2 * math.pi
TF_FORMS = ("gain", "num", "pi", "pid", "pr", "lpf", "comp", "model")
TF_EXTRA = ("delay", "port", "note")
MODELS = ("buck", "boost", "buckboost")
MODEL_OUT = ("v", "i")
LOOP_KEYS = ("params", "tf", "plant", "breaks", "closed", "freq", "note")
DEFAULT_FREQ = (10.0, 1e6)       # Hz: a switching converter's loop lives in here
SWEEP_PER_DECADE = 80            # sweep density; crossovers are then refined
BISECT_STEPS = 50
PM_TYPE2_MAX = 70.0              # deg: k-factor Type II boost above this needs huge k
PM_TYPE3_MAX = 150.0             # deg: likewise for Type III
DEFAULT_R1 = 10e3                # ohm: the op-amp compensator's input resistor


class LoopError(ValueError):
    pass


# ---- formulas ----------------------------------------------------------------
_FUNCS: dict[str, Callable[..., float]] = {
    "sqrt": math.sqrt, "exp": math.exp, "log": math.log, "log10": math.log10,
    "sin": math.sin, "cos": math.cos, "tan": math.tan, "atan": math.atan,
    "abs": abs, "min": min, "max": max}
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Pow: operator.pow}


def evaluate(v: Any, env: dict[str, float], what: str) -> float:
    """A number, or a formula string over ``env`` (arithmetic and _FUNCS only)."""
    if isinstance(v, bool):
        raise LoopError(f"{what} must be a number or a formula, not {v!r}")
    if isinstance(v, (int, float)):
        try:
            number = float(v)
        except OverflowError:
            raise LoopError(f"{what} must be finite") from None
        if not math.isfinite(number):
            raise LoopError(f"{what} must be finite")
        return number
    if not isinstance(v, str):
        raise LoopError(f"{what} must be a number or a formula, not {v!r}")
    try:
        tree = ast.parse(v.replace("^", "**"), mode="eval")
    except SyntaxError:
        raise LoopError(f"{what}: cannot read formula {v!r}") from None

    def ev(n: ast.AST) -> float:
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) \
                and not isinstance(n.value, bool):
            return float(n.value)
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            return -ev(n.operand) if isinstance(n.op, ast.USub) else ev(n.operand)
        if isinstance(n, ast.Name):
            if n.id in env:
                return env[n.id]
            if n.id == "pi":
                return math.pi
            raise LoopError(f"{what}: unknown name {n.id!r} in {v!r}; define it in loop.params")
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS \
                and not n.keywords:
            return float(_FUNCS[n.func.id](*[ev(a) for a in n.args]))
        raise LoopError(f"{what}: {v!r} may use numbers, params, + - * / ^ and "
                        f"{sorted(_FUNCS)} only")
    try:
        out = ev(tree)
        if not math.isfinite(out):
            raise LoopError(f"{what}: {v!r} is not finite")
    except (ZeroDivisionError, OverflowError, ValueError, TypeError) as exc:
        if isinstance(exc, LoopError):
            raise
        raise LoopError(f"{what}: {v!r} cannot be evaluated ({exc})") from None
    return out


# ---- transfer functions ------------------------------------------------------
@dataclass
class Tf:
    """A rational part and a pure delay: G(s) = zpk(s) * exp(-s delay)."""
    zpk: Zpk
    delay: float = 0.0
    port: str | None = None          # input side read by a multi-input block
    form: str = "gain"
    values: dict[str, Any] = field(default_factory=dict)   # evaluated numbers, for codegen

    def at(self, s: complex) -> complex:
        return self.zpk(s) * cmath.exp(-s * self.delay)

    def rational(self) -> Zpk:
        return self.zpk * pade(self.delay)


def _req(d: dict, key: str, env: dict, what: str) -> float:
    if key not in d:
        raise LoopError(f"{what} needs {key!r}")
    return evaluate(d[key], env, f"{what}.{key}")


def _opt(d: dict, key: str, env: dict, what: str, default: float) -> float:
    return evaluate(d[key], env, f"{what}.{key}") if key in d else default


def _hz_list(v: Any, env: dict, what: str) -> list[float]:
    vals = v if isinstance(v, list) else [v]
    out = [evaluate(x, env, what) for x in vals]
    if any(x <= 0 for x in out):
        raise LoopError(f"{what} must be positive frequencies in Hz")
    return out


def parse_tf(d: Any, env: dict[str, float], what: str) -> Tf:
    if isinstance(d, (int, float, str)) and not isinstance(d, bool):
        k = evaluate(d, env, what)
        return Tf(Zpk.const(k), values={"k": k})
    if not isinstance(d, dict):
        raise LoopError(f"{what} must be an object such as {{\"pi\": {{\"kp\": 0.1, \"ki\": 200}}}}")
    forms = [k for k in TF_FORMS if k in d]
    if "den" in d and "num" not in d:
        raise LoopError(f"{what}: den needs num")
    if len(forms) != 1:
        raise LoopError(f"{what} needs exactly one of {list(TF_FORMS)}, found {forms or 'none'}")
    form = forms[0]
    extra = {"den"} if form == "num" else {"out", *_MODEL_KEYS} if form == "model" else set()
    unknown = sorted(set(d) - {form} - set(TF_EXTRA) - extra)
    if unknown:
        raise LoopError(f"{what}: unknown keys {unknown}; a transfer function takes one of "
                        f"{list(TF_FORMS)} plus {list(TF_EXTRA[:2])}")
    delay = _opt(d, "delay", env, what, 0.0)
    if delay < 0:
        raise LoopError(f"{what}.delay must not be negative")
    port = d.get("port")
    if port is not None and not isinstance(port, str):
        raise LoopError(f"{what}.port must be a side such as \"w\"")
    zpk, vals = _form(form, d, env, f"{what}.{form}" if form not in ("num", "gain") else what)
    return Tf(zpk, delay, port, form, vals)


def _form(form: str, d: dict, env: dict, what: str) -> tuple[Zpk, dict[str, Any]]:
    if form == "gain":
        k = evaluate(d["gain"], env, f"{what}.gain")
        return Zpk.const(k), {"k": k}
    if form == "num":
        num, den = d["num"], d.get("den", [1])
        for name, p in (("num", num), ("den", den)):
            if not isinstance(p, list) or not p:
                raise LoopError(f"{what}.{name} must be a list of coefficients, highest power of s first")
        n = [evaluate(c, env, f"{what}.num") for c in num]
        m = [evaluate(c, env, f"{what}.den") for c in den]
        if all(c == 0 for c in m):
            raise LoopError(f"{what}.den is zero")
        return Zpk.from_poly(n, m), {"num": n, "den": m}
    if form == "model":
        return _model(d, env, what)
    p = d[form]
    if not isinstance(p, dict):
        raise LoopError(f"{what} must be an object of parameters")
    if form == "pi":
        kp, ki = _req(p, "kp", env, what), _req(p, "ki", env, what)
        return Zpk.from_poly([kp, ki], [1, 0]), {"kp": kp, "ki": ki}
    if form == "pid":
        kp, ki = _req(p, "kp", env, what), _req(p, "ki", env, what)
        kd, fd = _opt(p, "kd", env, what, 0.0), _opt(p, "fd", env, what, 0.0)
        g = Zpk.from_poly([kp, ki], [1, 0])
        if kd:
            g = g + (Zpk.from_poly([kd, 0], [1 / (TWO_PI * fd), 1]) if fd > 0
                     else Zpk.from_poly([kd, 0], [1]))
        return g, {"kp": kp, "ki": ki, "kd": kd, "fd": fd}
    if form == "pr":
        kp, kr, f0 = _req(p, "kp", env, what), _req(p, "kr", env, what), _req(p, "f0", env, what)
        fc = _opt(p, "fc", env, what, 0.0)
        w0, wc = TWO_PI * f0, TWO_PI * fc
        # Teodorescu et al.: ideal 2 kr s / (s^2 + w0^2); with fc > 0 the
        # non-ideal 2 kr wc s / (s^2 + 2 wc s + w0^2)
        res = (Zpk.from_poly([2 * kr * wc, 0], [1, 2 * wc, w0 * w0]) if fc > 0
               else Zpk.from_poly([2 * kr, 0], [1, 0, w0 * w0]))
        return Zpk.const(kp) + res, {"kp": kp, "kr": kr, "f0": f0, "fc": fc}
    if form == "lpf":
        fc = _req(p, "fc", env, what)
        order = p.get("order", 1)
        if order not in (1, 2):
            raise LoopError(f"{what}.order must be 1 or 2")
        if fc <= 0:
            raise LoopError(f"{what}.fc must be positive")
        w = TWO_PI * fc
        if order == 1:
            return Zpk.from_poly([w], [1, w]), {"fc": fc, "order": 1}
        q = _opt(p, "q", env, what, 1 / math.sqrt(2))
        return Zpk.from_poly([w * w], [1, w / q, w * w]), {"fc": fc, "order": 2, "q": q}
    if form == "comp":
        fi = _req(p, "fi", env, what)
        fz = _hz_list(p.get("fz", []), env, f"{what}.fz") if p.get("fz") is not None else []
        fp = _hz_list(p.get("fp", []), env, f"{what}.fp") if p.get("fp") is not None else []
        return comp_zpk(fi, fz, fp), {"fi": fi, "fz": fz, "fp": fp}
    raise LoopError(f"{what}: unknown form {form!r}")


def comp_zpk(fi: float, fz: list[float], fp: list[float]) -> Zpk:
    """(wi/s) prod(1 + s/wz) / prod(1 + s/wp): a Type I/II/III compensator."""
    wz = [TWO_PI * f for f in fz]
    wp = [TWO_PI * f for f in fp]
    k = TWO_PI * fi * math.prod(1 / w for w in wz) * math.prod(wp)
    return Zpk(k, [complex(-w) for w in wz], [0j] + [complex(-w) for w in wp])


# ---- averaged converter models -----------------------------------------------
_MODEL_KEYS = ("vin", "l", "c", "r", "rc", "rl", "d")


def _model(d: dict, env: dict, what: str) -> tuple[Zpk, dict[str, Any]]:
    kind = d["model"]
    if kind not in MODELS:
        raise LoopError(f"{what}.model must be one of {list(MODELS)}, not {kind!r}")
    out = d.get("out", "v")
    if out not in MODEL_OUT:
        raise LoopError(f"{what}.out must be \"v\" (output voltage) or \"i\" (inductor current)")
    vin, l, c, r = (_req(d, k, env, what) for k in ("vin", "l", "c", "r"))
    rl, rc = _opt(d, "rl", env, what, 0.0), _opt(d, "rc", env, what, 0.0)
    if min(vin, l, c, r) <= 0 or rl < 0 or rc < 0:
        raise LoopError(f"{what}: vin, l, c, r must be positive and rl, rc not negative")
    vals = {"model": kind, "out": out, "vin": vin, "l": l, "c": c, "r": r, "rl": rl, "rc": rc}
    if kind == "buck":
        # d -> v and d -> i with Zo = R || (rc + 1/sC)
        den = [l * c * (r + rc), l + c * (rl * (r + rc) + r * rc), rl + r]
        if out == "v":
            return Zpk.from_poly([vin * r * rc * c, vin * r], den), vals
        return Zpk.from_poly([vin * c * (r + rc), vin], den), vals
    if rc:
        raise LoopError(f"{what}: rc (capacitor ESR) is modelled for the buck only")
    duty = _req(d, "d", env, what)
    if not 0 < duty < 1:
        raise LoopError(f"{what}.d must be between 0 and 1")
    dp = 1 - duty
    # ideal-switch averaged model, small signal around D:
    #   s L i = -rl i - D' v + Vx d,   s C v = D' i - v / R - I d
    vo = vin / dp if kind == "boost" else vin * duty / dp
    vx = vo if kind == "boost" else vin + vo
    il = vo / (r * dp)
    vals.update(d=duty, vo=vo, il=il)
    det = [l * c, l / r + rl * c, rl / r + dp * dp]
    if out == "v":
        return Zpk.from_poly([-l * il, dp * vx - rl * il], det), vals
    return Zpk.from_poly([vx * c, vx / r + dp * il], det), vals


# ---- the loop section --------------------------------------------------------
@dataclass
class Plant:
    src: str
    dst: str
    tf: Tf


@dataclass
class Probe:
    at: str
    label: str


@dataclass
class Closed:
    src: str
    dst: str
    label: str


@dataclass
class LoopSpec:
    params: dict[str, float] = field(default_factory=dict)
    tf: dict[str, Tf] = field(default_factory=dict)
    plant: list[Plant] = field(default_factory=list)
    breaks: list[Probe] = field(default_factory=list)
    closed: list[Closed] = field(default_factory=list)
    freq: tuple[float, float] = DEFAULT_FREQ


def parse_loop(raw: Any, dia: Diagram) -> tuple[LoopSpec | None, list[str]]:
    """The ``loop`` section, or (None, []) when the table has none."""
    if raw is None:
        return None, []
    errors: list[str] = []
    if not isinstance(raw, dict):
        return None, ["loop must be an object with params, tf, plant, breaks, closed, freq"]
    unknown = sorted(set(raw) - set(LOOP_KEYS))
    if unknown:
        errors.append(f"loop: unknown keys {unknown}; allowed are {list(LOOP_KEYS)}")
    spec = LoopSpec()
    params = raw.get("params") or {}
    if not isinstance(params, dict):
        errors.append("loop.params must be an object of name: value")
        params = {}
    for name, v in params.items():
        if not isinstance(name, str) or not name.isidentifier() or name == "pi":
            errors.append(f"loop.params: {name!r} is not a usable name")
            continue
        try:
            spec.params[name] = evaluate(v, spec.params, f"loop.params.{name}")
        except LoopError as exc:
            errors.append(str(exc))
    ids = {b.id: b for b in dia.blocks}
    tfs = raw.get("tf") or {}
    if not isinstance(tfs, dict):
        errors.append("loop.tf must be an object of block id: transfer function")
        tfs = {}
    for bid, d in tfs.items():
        what = f"loop.tf.{bid}"
        if bid not in ids:
            errors.append(f"{what}: no block {bid!r} in the drawing")
            continue
        if ids[bid].type in ("sum", "switch"):
            errors.append(f"{what}: a {ids[bid].type} has no transfer function")
            continue
        try:
            spec.tf[bid] = parse_tf(d, spec.params, what)
        except LoopError as exc:
            errors.append(str(exc))
    plants = raw.get("plant") or []
    if not isinstance(plants, list):
        errors.append("loop.plant must be a list")
        plants = []
    for i, d in enumerate(plants):
        what = f"loop.plant[{i}]"
        if not isinstance(d, dict):
            errors.append(f"{what} must be an object with from, to and a transfer function")
            continue
        src, dst = d.get("from"), d.get("to")
        bad = [k for k, v in (("from", src), ("to", dst)) if v not in ids]
        if bad:
            errors.append(f"{what}: {' and '.join(bad)} must name blocks in the drawing")
            continue
        if ids[dst].type != "tag":
            errors.append(f"{what}: to must be a measured-signal tag (type tag), "
                          f"{dst!r} is a {ids[dst].type}")
            continue
        body = {k: v for k, v in d.items() if k not in ("from", "to")}
        try:
            spec.plant.append(Plant(src, dst, parse_tf(body, spec.params, what)))
        except LoopError as exc:
            errors.append(str(exc))
    for i, d in enumerate(raw.get("breaks") or []):
        at, label = (d, None) if isinstance(d, str) else \
            (d.get("at"), d.get("label")) if isinstance(d, dict) else (None, None)
        if at not in ids:
            errors.append(f"loop.breaks[{i}]: {at!r} is not a block in the drawing")
            continue
        spec.breaks.append(Probe(at, label or f"$T_{{{len(spec.breaks) + 1}}}$"))
    for i, d in enumerate(raw.get("closed") or []):
        if not isinstance(d, dict) or d.get("from") not in ids or d.get("to") not in ids:
            errors.append(f"loop.closed[{i}]: needs from (an input tag) and to (a block), "
                          f"both blocks in the drawing")
            continue
        spec.closed.append(Closed(d["from"], d["to"], d.get("label") or
                                  f"{d['to']}/{d['from']}"))
    freq = raw.get("freq")
    if freq is not None:
        try:
            lo, hi = (evaluate(v, spec.params, "loop.freq") for v in freq)
            if not 0 < lo < hi:
                raise LoopError("loop.freq must be [f_min, f_max] with 0 < f_min < f_max")
            spec.freq = (lo, hi)
        except (TypeError, ValueError) as exc:
            errors.append(str(exc) if isinstance(exc, LoopError)
                          else "loop.freq must be [f_min, f_max] in Hz")
    if not spec.breaks and spec.plant:
        spec.breaks.append(Probe(spec.plant[0].src, "$T(s)$"))
    return spec, errors


# ---- the signal graph --------------------------------------------------------
@dataclass
class Node:
    """One block's output: y = sum(coef * input) (+ an external source)."""
    id: str
    terms: list[tuple[str, Tf | float]] = field(default_factory=list)   # (block, gain)
    source: bool = False


class Network:
    """Linear signal graph of a diagram: one unknown per block output."""

    def __init__(self, rep, spec: LoopSpec):
        self.dia: Diagram = rep.diagram
        self.spec = spec
        self.blocks = {b.id: b for b in self.dia.blocks}
        self.inputs = _inputs(rep)                # block -> [(port, source block)]
        self.nodes: dict[str, Node] = {}
        self.problems: dict[str, str] = {}
        self.digital = None
        self.sample_plan = None
        self.digital_resolution = []
        for b in self.dia.blocks:
            self._node(b)

    def _node(self, b) -> None:
        ins = self.inputs.get(b.id, [])
        n = Node(b.id)
        tf = self.spec.tf.get(b.id)
        fn = (self.dia.raw.get("functions") or {}).get(b.id) if isinstance(self.dia.raw, dict) else None
        if isinstance(fn, dict):
            # a function block is nonlinear: the loop analysis stops at it
            why = (f"{b.id} is a function block ({fn.get('fn')}); the linear loop analysis "
                   f"cannot pass through it")
            self.problems[b.id] = why
            from .functions import KINDS
            outputs = fn.get("out", {}) if fn.get("fn") == "c" else getattr(KINDS.get(fn.get("fn")), "outputs", ())
            for name in outputs:
                self.nodes[f"{b.id}.{name}"] = Node(f"{b.id}.{name}")
                self.problems[f"{b.id}.{name}"] = why
                self.inputs.setdefault(f"{b.id}.{name}", list(ins))
            self.nodes[b.id] = n
            return
        if b.type == "sum":
            n.terms = [(src, 1.0 if b.signs.get(port) == "+" else -1.0) for port, src in ins]
        elif b.type == "switch":
            pole_in = [src for port, src in ins if port == POLE]
            on_in = [src for port, src in ins if port == b.on]
            n.terms = [(src, 1.0) for src in (on_in or pole_in)]
        elif b.type == "tag":
            n.terms = [(p.src, p.tf) for p in self.spec.plant if p.dst == b.id]
            n.terms += [(src, tf or 1.0) for port, src in ins]
            n.source = not n.terms
        else:
            if tf is not None and tf.port is not None:
                ins = [(p, s) for p, s in ins if p == tf.port]
                if not ins:
                    self.problems[b.id] = (f"{b.id}: loop.tf.{b.id}.port {tf.port!r} has no "
                                           f"line coming in")
            if not ins:
                n.source = True
            elif len(ins) > 1:
                self.problems[b.id] = (f"{b.id} has {len(ins)} inputs ({', '.join(p for p, _ in ins)}); "
                                       f"say which one carries the small signal with "
                                       f"loop.tf.{b.id}.port (the others are held constant)")
            elif tf is None and b.type == "limiter":
                n.terms = [(ins[0][1], 1.0)]          # inside its limits a limiter is a wire
            elif tf is None:
                self.problems[b.id] = (f"{b.id} ({b.type}) has no transfer function; "
                                       f"add loop.tf.{b.id}")
            else:
                n.terms = [(ins[0][1], tf)]
        self.nodes[b.id] = n

    def reads(self, bid: str) -> list[str]:
        """Every block ``bid`` depends on, including inputs of a block the
        loop cannot use yet (so its problem is reported, not a broken loop)."""
        return [src for src, _ in self.nodes[bid].terms] + \
            ([src for _, src in self.inputs.get(bid, [])] if bid in self.problems else [])

    def upstream(self, targets: list[str]) -> set[str]:
        seen: set[str] = set()
        stack = list(targets)
        while stack:
            bid = stack.pop()
            if bid in seen:
                continue
            seen.add(bid)
            stack += self.reads(bid)
        return seen

    def require(self, targets: list[str]) -> None:
        need = self.upstream(targets)
        bad = [msg for bid, msg in self.problems.items() if bid in need]
        if bad:
            raise LoopError("; ".join(bad))

    # one solve: unknowns are every non-source block in ``need``
    def _solve(self, need: set[str], drive: dict[str, float], cut: str | None, value):
        """``value(gain)`` maps a Tf or float into the field (complex or Zpk).
        ``drive`` sets sources (by block id) and, under key "#cut", the test
        signal that replaces block ``cut``'s output for everything reading it."""
        is_cplx = value(1.0).__class__ is complex
        zero = 0j if is_cplx else Zpk(0.0)
        isz = (lambda v: v == 0) if is_cplx else (lambda v: v.is_zero())
        order = [b for b in self.dia.blocks if b.id in need and not self.nodes[b.id].source]
        index = {b.id: i for i, b in enumerate(order)}
        n = len(order)
        a = [[zero for _ in range(n)] for _ in range(n)]
        rhs = [zero for _ in range(n)]
        for i, b in enumerate(order):
            a[i][i] = value(1.0)
            for src, g in self.nodes[b.id].terms:
                coef = value(g)
                if src == cut:
                    rhs[i] = rhs[i] + coef * drive.get("#cut", 0.0)
                elif src in index:
                    a[i][index[src]] = a[i][index[src]] - coef
                else:                                     # an external source
                    rhs[i] = rhs[i] + coef * drive.get(src, 0.0)
        try:
            y = solve(a, rhs, zero, isz)
        except ZeroDivisionError:
            raise LoopError("the diagram has an algebraic loop with gain 1 (no dynamics "
                            "between a block and itself); it cannot be solved") from None
        out = dict(zip(index, y))
        for bid in need:
            if bid not in out:
                out[bid] = value(drive.get(bid, 0.0))
        return out

    def loop_gain(self, at: str, value):
        """L = -(return at ``at``) for a unit test signal injected after ``at``."""
        consumers = [b for b in self.nodes if at in self.reads(b)]
        if not consumers:
            raise LoopError(f"loop break at {at!r}: nothing reads that block's output")
        need = self.upstream([at])
        self.require([at])
        if not any(c in need for c in consumers):
            raise LoopError(f"loop break at {at!r}: no feedback path returns to it; "
                            f"is the plant (loop.plant) entered?")
        y = self._solve(need, {"#cut": 1.0}, at, value)
        return -y[at]

    def closed(self, src: str, dst: str, value):
        need = self.upstream([dst])
        if src not in need:
            raise LoopError(f"closed loop {src} -> {dst}: {dst} does not depend on {src}")
        if not self.nodes[src].source:
            raise LoopError(f"closed loop from {src!r}: it must be an input (a tag or a block "
                            f"with no line coming in)")
        self.require([dst])
        return self._solve(need, {src: 1.0}, None, value)[dst]


def _inputs(rep) -> dict[str, list[tuple[str, str]]]:
    """block id -> [(input port, source)] from the resolved links.  A source
    is a block id, or ``block.output`` for an output of a function block; the
    port of a function block's input is that input's name."""
    from .check import _on_path, on_boundary
    from .functions import parse as parse_functions
    dia, g = rep.diagram, rep.geometry
    blocks = dia.blocks
    fns, _ = parse_functions(dia.raw.get("functions") if isinstance(dia.raw, dict) else None, dia)

    def block_at(end, p):
        if end.block is not None:
            return end.block, end.port
        for b in blocks:
            side = on_boundary(b, p, g.s)
            if side:
                return b.id, side
        return None, None

    def source(i: int, seen: set[int]) -> str | None:
        lk, path = dia.links[i], g.paths[i]
        bid, side = block_at(lk.start, path[0])
        if bid is not None:
            if bid in fns:
                offset = lk.start.offset if lk.start.block is not None else 0.0
                return fns[bid].output_at(side, offset) or f"{bid}.?{side}{offset:+g}"
            return bid
        for j, q in enumerate(g.paths):
            if j != i and j not in seen and q and _on_path(path[0], q):
                return source(j, seen | {i})
        return None

    out: dict[str, list[tuple[str, str]]] = {}
    for i, (lk, path) in enumerate(zip(dia.links, g.paths)):
        if not path:
            continue
        dst, port = block_at(lk.end, path[-1])
        src = source(i, set())
        if dst is None or src is None or dst == src or src.split(".")[0] == dst:
            continue
        if dst in fns:
            offset = lk.end.offset if lk.end.block is not None else 0.0
            port = fns[dst].input_at(port, offset) or f"?{port}{offset:+g}"
        out.setdefault(dst, []).append((port, src))
    return out


# ---- evaluation helpers ------------------------------------------------------
def at_freq(s: complex):
    def value(g):
        return g.at(s) if isinstance(g, Tf) else complex(g)
    return value


def rational(g):
    return g.rational() if isinstance(g, Tf) else Zpk.const(g)


def sweep(fn: Callable[[complex], complex], lo: float, hi: float,
          per_decade: int = SWEEP_PER_DECADE) -> tuple[list[float], list[complex]]:
    n = max(2, int(math.ceil(math.log10(hi / lo) * per_decade)) + 1)
    fs = [lo * (hi / lo) ** (k / (n - 1)) for k in range(n)]
    return fs, [fn(TWO_PI * f * 1j) for f in fs]


def unwrap(vals: list[complex]) -> list[float]:
    """Phase in degrees, continuous, the first point in (-270, 90]."""
    out: list[float] = []
    for v in vals:
        ph = math.degrees(cmath.phase(v)) if v != 0 else (out[-1] if out else 0.0)
        if out:
            ph += 360 * round((out[-1] - ph) / 360)
        out.append(ph)
    if out:
        shift = 360 * math.floor((90 - out[0]) / 360)
        out = [p + shift for p in out]
    return out


def db(v: complex) -> float:
    return 20 * math.log10(abs(v)) if v != 0 else -400.0


@dataclass
class Margins:
    crossovers: list[tuple[float, float]]      # (f_hz, phase margin deg)
    phase_crossovers: list[tuple[float, float]]  # (f_hz, gain margin dB)

    @property
    def fc(self) -> float | None:
        """The crossover with the smallest phase margin (as MATLAB's margin)."""
        return min(self.crossovers, key=lambda c: c[1])[0] if self.crossovers else None

    @property
    def pm(self) -> float | None:
        return min(pm for _, pm in self.crossovers) if self.crossovers else None

    @property
    def gm(self) -> float | None:
        return min(g for _, g in self.phase_crossovers) if self.phase_crossovers else None


def _bisect(g: Callable[[float], float], a: float, b: float) -> float:
    ga = g(a)
    for _ in range(BISECT_STEPS):
        m = math.sqrt(a * b)
        gm = g(m)
        if (gm > 0) == (ga > 0):
            a, ga = m, gm
        else:
            b = m
    return math.sqrt(a * b)


def margins(fn: Callable[[complex], complex], lo: float, hi: float) -> Margins:
    fs, vals = sweep(fn, lo, hi)
    mag = [db(v) for v in vals]
    ph = unwrap(vals)
    cross, pcross = [], []
    for k in range(len(fs) - 1):
        if (mag[k] > 0) != (mag[k + 1] > 0):
            f = _bisect(lambda f: db(fn(TWO_PI * f * 1j)), fs[k], fs[k + 1])
            phase = ph[k] + (ph[k + 1] - ph[k]) * math.log(f / fs[k]) / math.log(fs[k + 1] / fs[k])
            pm = (phase + 180 + 180) % 360 - 180
            cross.append((f, pm))
        # phase crossing an odd multiple of -180
        for m in range(math.floor(min(ph[k], ph[k + 1]) / 360) - 1,
                       math.ceil(max(ph[k], ph[k + 1]) / 360) + 1):
            line = -180 + 360 * m
            if (ph[k] - line) * (ph[k + 1] - line) < 0 or ph[k + 1] == line:
                t = (line - ph[k]) / (ph[k + 1] - ph[k])
                f = fs[k] * (fs[k + 1] / fs[k]) ** t
                pcross.append((f, -db(fn(TWO_PI * f * 1j))))
    return Margins(cross, pcross)


def describe(roots: list[complex]) -> list[str]:
    """Roots in engineering words: Hz, damping, RHP."""
    out, skip = [], set()
    for i, r in enumerate(roots):
        if i in skip:
            continue
        if abs(r) == 0:
            out.append("s = 0")
            continue
        f = abs(r) / TWO_PI
        if abs(r.imag) > 1e-9 * abs(r):
            for j in range(i + 1, len(roots)):
                if j not in skip and abs(roots[j] - r.conjugate()) <= 1e-6 * abs(r):
                    skip.add(j)
                    break
            zeta = -r.real / abs(r)
            out.append(f"{eng(f)}Hz pair, zeta {zeta:.3g}" + (" (RHP)" if r.real > 0 else ""))
        else:
            out.append(f"{eng(f)}Hz" + (" (RHP)" if r.real > 0 else ""))
    return out


def eng(v: float) -> str:
    """3 significant digits with an SI prefix: 1.2 k, 470 m."""
    if v == 0:
        return "0 "
    exp = int(math.floor(math.log10(abs(v)) / 3))
    exp = max(-4, min(3, exp))
    prefix = {-4: "p", -3: "n", -2: "u", -1: "m", 0: "", 1: "k", 2: "M", 3: "G"}[exp]
    return f"{v / 10 ** (3 * exp):.3g} {prefix}"


# ---- analysis ----------------------------------------------------------------
def network(rep, raw_loop=None) -> tuple[Network, LoopSpec]:
    raw = raw_loop if raw_loop is not None else rep.diagram.raw.get("loop")
    if raw is None:
        raise LoopError("表里没有 loop 段，无法做环路分析（the table has no loop section）；请加 \"loop\": {\"tf\": ..., \"plant\": ...}")
    spec, errors = parse_loop(raw, rep.diagram)
    if errors:
        raise LoopError("; ".join(errors))
    net = Network(rep, spec)
    if "digital" in rep.diagram.raw:
        from .digital import network as digital_network
        net.digital, net.sample_plan = digital_network(rep, net, spec)
    return net, spec


def analyse(rep) -> dict[str, Any]:
    """Loop gains with margins, closed-loop poles and closed-loop responses."""
    net, spec = network(rep)
    lo, hi = spec.freq
    loops = []
    for probe in spec.breaks:
        fn = lambda s, at=probe.at: net.loop_gain(at, at_freq(s))
        m = margins(fn, lo, hi)
        lz = net.loop_gain(probe.at, rational)
        char = lz + 1.0
        poles = char.zeros if not char.is_zero() else []
        loops.append({
            "break": probe.at, "label": probe.label,
            "fc_hz": _r(m.fc), "pm_deg": _r(m.pm), "gm_db": _r(m.gm),
            "crossovers": [{"f_hz": _r(f), "pm_deg": _r(p)} for f, p in m.crossovers],
            "phase_crossovers": [{"f_hz": _r(f), "gm_db": _r(g)} for f, g in m.phase_crossovers],
            "loop_gain": {"k": _r(lz.k), "zeros": _roots_text(lz.zeros, net.digital), "poles": _roots_text(lz.poles, net.digital)},
            "closed_loop_poles": _roots_text(poles, net.digital),
            "stable": all(abs(p) < 1 - 1e-9 for p in poles) if net.digital else not [p for p in poles if p.real > 0],
        })
    closed = []
    for c in spec.closed:
        fn = lambda s, c=c: net.closed(c.src, c.dst, at_freq(s))
        dc = abs(fn(TWO_PI * lo * 1j))
        fs, vals = sweep(fn, lo, hi)
        bw = next((f for f, v in zip(fs, vals) if dc and abs(v) < dc / math.sqrt(2)), None)
        closed.append({"from": c.src, "to": c.dst, "label": c.label,
                       "gain_at_fmin": _r(dc), "bandwidth_hz": _r(bw)})
    blocks = {bid: _tf_text(tf) for bid, tf in spec.tf.items()}
    result = {"params": spec.params, "blocks": blocks, "loops": loops, "closed": closed,
              "stable": all(lp["stable"] for lp in loops)}
    if net.digital:
        from .digital import LINEAR_NOTE
        result["digital"] = {"fs_hz": net.sample_plan.fs, "delay_samples": net.digital.delay_samples,
                             "freq_hz": list(spec.freq), "resolution": net.digital_resolution,
                             "word_bits": net.digital.word_bits, "fraction_bits": net.digital.fraction_bits,
                             "terms": {bid: [{"from": src, "num_z": gain.zpk.num(), "den_z": gain.zpk.den()}
                                             for src, gain in nd.terms] for bid, nd in net.nodes.items()},
                             "note": LINEAR_NOTE}
    return result


def _roots_text(values, digital):
    if digital:
        return [f"z = {p.real:.6g}{p.imag:+.6g}j, |z| = {abs(p):.6g}" for p in values]
    return describe(values)


def _tf_text(tf: Tf) -> dict[str, Any]:
    z = tf.zpk
    d: dict[str, Any] = {"form": tf.form, "k": _r(z.k), "zeros": describe(z.zeros),
                         "poles": describe(z.poles)}
    if tf.delay:
        d["delay_s"] = _r(tf.delay)
    return d


def _r(v):
    if v is None:
        return None
    return float(f"{v:.5g}")


def curves(rep, mono: bool = False) -> tuple[list[dict], tuple[float, float]]:
    """What the Bode plot draws: each loop gain, then each closed-loop response."""
    net, spec = network(rep)
    lo, hi = spec.freq
    out = []
    for probe in spec.breaks:
        fn = lambda s, at=probe.at: net.loop_gain(at, at_freq(s))
        fs, vals = sweep(fn, lo, hi, per_decade=120)
        m = margins(fn, lo, hi)
        out.append({"label": probe.label, "f": fs, "mag": [db(v) for v in vals],
                    "phase": unwrap(vals), "fc": m.fc, "pm": m.pm, "gm": m.gm, "loop": True})
    for c in spec.closed:
        fn = lambda s, c=c: net.closed(c.src, c.dst, at_freq(s))
        fs, vals = sweep(fn, lo, hi, per_decade=120)
        out.append({"label": c.label, "f": fs, "mag": [db(v) for v in vals],
                    "phase": unwrap(vals), "loop": False})
    return out, (lo, hi)


# ---- compensator design ------------------------------------------------------
def design(rep, block: str, fc: float, pm: float, kind: int | None = None,
           at: str | None = None, r1: float = DEFAULT_R1) -> dict[str, Any]:
    """K-factor (Venable) Type I/II/III compensator for ``block``, placing the
    loop's crossover at ``fc`` Hz with ``pm`` degrees of phase margin."""
    net, spec = network(rep)
    if net.digital:
        raise LoopError("数字模式请调整 loop.tf 后重新计算。k 因子补偿器设计使用连续时间模型")
    if block not in net.blocks:
        raise LoopError(f"no block {block!r}")
    probe = at or (spec.breaks[0].at if spec.breaks else None)
    if probe is None:
        raise LoopError("say where to cut the loop: loop.breaks or --break")
    if not 0 < pm < 180:
        raise LoopError("phase margin must be between 0 and 180 degrees")
    if fc <= 0:
        raise LoopError("crossover frequency must be positive")
    old = spec.tf.get(block)
    port = old.port if old else None
    spec.tf[block] = Tf(Zpk.const(1.0), port=port)       # the uncompensated loop
    net = Network(rep, spec)
    if block not in net.upstream([probe]):
        raise LoopError(f"{block} is not inside the loop cut at {probe}")
    fn = lambda s: net.loop_gain(probe, at_freq(s))
    lo = min(spec.freq[0], fc / 100)
    _, vals = sweep(fn, lo, fc)
    phase = unwrap(vals)[-1]
    gain = abs(vals[-1])
    if gain == 0:
        raise LoopError(f"the loop without {block} has zero gain at {eng(fc)}Hz")
    boost = pm - 90 - phase
    if kind is None:
        kind = 1 if boost <= 0 else 2 if boost <= PM_TYPE2_MAX else 3
    if kind == 2 and not 0 < boost < 90:
        raise LoopError(f"Type II needs a phase boost between 0 and 90 deg, this loop needs "
                        f"{boost:.1f} deg; use Type III or a lower crossover")
    if kind == 3 and not 0 < boost < 180:
        raise LoopError(f"Type III gives at most 180 deg of boost, this loop needs {boost:.1f} "
                        f"deg; lower the crossover or the phase margin")
    if kind == 1:
        k, fz, fp = 1.0, [], []
    elif kind == 2:
        k = math.tan(math.radians(boost / 2 + 45))
        fz, fp = [fc / k], [fc * k]
    else:
        k = math.tan(math.radians(boost / 4 + 45)) ** 2
        fz, fp = [fc / math.sqrt(k)] * 2, [fc * math.sqrt(k)] * 2
    unit = comp_zpk(1.0, fz, fp)
    fi = 1.0 / (abs(unit(TWO_PI * fc * 1j)) * gain)
    comp = {"fi": _r(fi), "fz": [_r(f) for f in fz], "fp": [_r(f) for f in fp]}
    if kind == 1:
        del comp["fz"], comp["fp"]
    spec.tf[block] = Tf(comp_zpk(fi, fz, fp), old.delay if old else 0.0, port, "comp")
    check_net = Network(rep, spec)
    got = margins(lambda s: check_net.loop_gain(probe, at_freq(s)), spec.freq[0], spec.freq[1])
    return {"block": block, "type": kind, "break": probe, "fc_target_hz": fc,
            "pm_target_deg": pm, "plant_phase_deg": _r(phase), "plant_gain_db": _r(db(gain)),
            "boost_deg": _r(boost), "k": _r(k), "tf": {"comp": comp},
            "achieved": {"fc_hz": _r(got.fc), "pm_deg": _r(got.pm), "gm_db": _r(got.gm)},
            "opamp": opamp(kind, fi, fz, fp, r1)}


def opamp(kind: int, fi: float, fz: list[float], fp: list[float], r1: float) -> dict[str, Any]:
    """Inverting op-amp realisation (Venable).  Type II: R2 in series with C2,
    C1 across both.  Type III adds R3 in series with C3 across R1."""
    wi = TWO_PI * fi
    ct = 1 / (r1 * wi)
    if kind == 1:
        return {"R1": _r(r1), "C1": _r(ct), "note": "C1 from the output to the inverting input"}
    wz1, wp1 = TWO_PI * fz[0], TWO_PI * fp[0]
    c2 = ct * wz1 / wp1 if kind == 3 else None
    if kind == 2:
        c1 = ct * wz1 / wp1           # the small capacitor across the network
        c2 = ct - c1
        return {"R1": _r(r1), "R2": _r(1 / (wz1 * c2)), "C1": _r(c1), "C2": _r(c2),
                "note": "R2 in series with C2 from the output to the inverting input, C1 across them"}
    c1 = ct - c2
    wz2, wp2 = TWO_PI * fz[1], TWO_PI * fp[1]
    r3 = r1 / (wp2 / wz2 - 1)
    return {"R1": _r(r1), "R2": _r(1 / (wz1 * c1)), "R3": _r(r3), "C1": _r(c1), "C2": _r(c2),
            "C3": _r(1 / (wp2 * r3)),
            "note": "R2 in series with C1 in the feedback, C2 across them; R3 in series with "
                    "C3 across R1"}
