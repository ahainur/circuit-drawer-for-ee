"""Function blocks: nonlinear blocks with several inputs and outputs.

A drawn block becomes one with an entry in the table's ``functions`` section::

    "functions": {
      "park": {"fn": "park",
               "in":  {"alpha": "e+0.25", "beta": "e-0.25", "theta": "s"},
               "out": {"d": "w+0.25", "q": "w-0.25"}}
    }

``in`` and ``out`` name the port each signal uses: a side (``w``, ``e``, ``n``,
``s``) and, when several lines share a side, the offset the lines are drawn
at (``"e+0.25"``), exactly as the links write it.  Every other block reads one
output of a function block by the line it draws from that port; inside the
code and simulations that signal is called ``<block>.<output>``.

These blocks are for code generation and closed-loop simulation; the linear
loop analysis cannot pass through them (Park needs the angle, SVPWM is not
linear).  Generated C uses floating point for them.

Conventions (amplitude invariant):
  clarke   alpha = (2a - b - c)/3, beta = (b - c)/sqrt(3); with only a and b
           given the phases are taken to sum to zero (c = -a - b)
  iclarke  a = alpha, b = -alpha/2 + sqrt(3)/2 beta, c = -alpha/2 - sqrt(3)/2 beta
  park     d = alpha cos(theta) + beta sin(theta), q = -alpha sin(theta) + beta cos(theta)
  ipark    alpha = d cos(theta) - q sin(theta), beta = d sin(theta) + q cos(theta)
  svpwm    phase references from iclarke, with the min-max zero sequence
           added (the carrier-based equivalent of space vector modulation):
           da = 0.5 + (va - (max + min)/2) / vdc, limited to 0..1; likewise db, dc
  wrap     y = x - 2 pi floor(x / 2 pi), an angle kept in [0, 2 pi)
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

SQ3 = math.sqrt(3.0)
TWO_PI = 2 * math.pi
KEYS = ("fn", "in", "out", "c", "note")
PORT = re.compile(r"^([nsew])([+-]\d+(?:\.\d+)?)?$")
OFFSET_TOL = 1e-6


@dataclass(frozen=True)
class Kind:
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    optional: tuple[str, ...] = ()
    doc: str = ""


KINDS = {
    "clarke": Kind(("a", "b", "c"), ("alpha", "beta"), ("c",), "abc -> alpha beta"),
    "iclarke": Kind(("alpha", "beta"), ("a", "b", "c"), (), "alpha beta -> abc"),
    "park": Kind(("alpha", "beta", "theta"), ("d", "q"), (), "alpha beta -> dq at angle theta"),
    "ipark": Kind(("d", "q", "theta"), ("alpha", "beta"), (), "dq -> alpha beta at angle theta"),
    "svpwm": Kind(("alpha", "beta", "vdc"), ("da", "db", "dc"), (), "alpha beta, DC link -> phase duty cycles"),
    "wrap": Kind(("x",), ("y",), (), "angle -> [0, 2 pi)"),
    "c": Kind((), (), (), "custom scalar C step with explicit old/next state; native export only"),
}


class FunctionError(ValueError):
    pass


@dataclass
class Fn:
    block: str
    kind: str
    ins: dict[str, tuple[str, float]] = field(default_factory=dict)    # name -> (side, offset)
    outs: dict[str, tuple[str, float]] = field(default_factory=dict)
    c: dict | None = None

    def signal(self, name: str) -> str:
        return f"{self.block}.{name}"

    def output_at(self, side: str, offset: float) -> str | None:
        for name, (s, o) in self.outs.items():
            if s == side and abs(o - offset) < OFFSET_TOL:
                return self.signal(name)
        return None

    def input_at(self, side: str, offset: float) -> str | None:
        for name, (s, o) in self.ins.items():
            if s == side and abs(o - offset) < OFFSET_TOL:
                return name
        return None


def port(text: Any) -> tuple[str, float] | None:
    if not isinstance(text, str):
        return None
    m = PORT.match(text.strip())
    return (m[1], float(m[2] or 0.0)) if m else None


def parse(raw: Any, dia) -> tuple[dict[str, Fn], list[str]]:
    """The ``functions`` section: block id -> Fn, and schema errors."""
    if raw is None:
        return {}, []
    if not isinstance(raw, dict):
        return {}, ["functions must be an object of block id: {fn, in, out}"]
    ids = {b.id: b for b in dia.blocks}
    out, errors = {}, []
    for bid, d in raw.items():
        what = f"functions.{bid}"
        if bid not in ids:
            errors.append(f"{what}: no block {bid!r} in the drawing")
            continue
        if ids[bid].type != "block":
            errors.append(f"{what}: only a plain box (type block) can be a function block")
            continue
        if not isinstance(d, dict):
            errors.append(f"{what} must be an object such as {{\"fn\": \"park\", \"in\": {{...}}, \"out\": {{...}}}}")
            continue
        bad = sorted(set(d) - set(KEYS))
        if bad:
            errors.append(f"{what}: unknown keys {bad}; allowed are {list(KEYS)}")
        name = d.get("fn")
        kind = KINDS.get(name) if isinstance(name, str) else None
        if kind is None:
            errors.append(f"{what}.fn must be one of {list(KINDS)}")
            continue
        fn = Fn(bid, d["fn"])
        if fn.kind == "c":
            from .custom_c import parse as parse_c
            fn.c, c_errors = parse_c(d.get("c"), d.get("in"), d.get("out"), what + ".c")
            errors += c_errors
            if c_errors:
                continue
            kind = Kind(tuple(sorted(d["in"])), tuple(sorted(d["out"])), (), kind.doc)
        elif "c" in d:
            errors.append(f"{what}.c 仅用于 fn 为 c 的方块")
        for key, names, required, store in (("in", kind.inputs, set(kind.inputs) - set(kind.optional), fn.ins),
                                           ("out", kind.outputs, set(), fn.outs)):
            given = d.get(key) or {}
            if not isinstance(given, dict):
                errors.append(f"{what}.{key} must be an object of {list(names)} -> port such as \"w+0.3\"")
                continue
            unknown = sorted(set(given) - set(names))
            if unknown:
                errors.append(f"{what}.{key}: {unknown} are not {d['fn']} {key}puts; "
                              f"use {list(names)}")
            missing = sorted(required - set(given))
            if missing:
                errors.append(f"{what}.{key}: {d['fn']} needs {missing}")
            for name in names:
                if name in given:
                    p = port(given[name])
                    if p is None:
                        errors.append(f"{what}.{key}.{name}: {given[name]!r} is not a port; "
                                      f"write a side and offset such as \"e+0.25\" or \"s\"")
                    else:
                        store[name] = p
        if not fn.outs:
            errors.append(f"{what}.out: name at least one output port")
        used = list(fn.ins.values()) + list(fn.outs.values())
        if len(set(used)) != len(used):
            errors.append(f"{what}: two signals share one port")
        out[bid] = fn
    return out, errors


def wiring(fns: dict[str, Fn], links, errors: list[str]) -> None:
    """Every declared port must be where a line is drawn."""
    ends_in, ends_out = {}, {}
    for lk in links:
        for end, store in ((lk.end, ends_in), (lk.start, ends_out)):
            if end.block in fns:
                store.setdefault(end.block, []).append((end.port, end.offset))
    for bid, fn in fns.items():
        for key, ports, drawn in (("in", fn.ins, ends_in.get(bid, [])), ("out", fn.outs, ends_out.get(bid, []))):
            for name, (side, off) in ports.items():
                if not any(s == side and abs(o - off) < OFFSET_TOL for s, o in drawn):
                    errors.append(f"functions.{bid}.{key}.{name}: no line {'ends' if key == 'in' else 'starts'} "
                                  f"at {bid}.{side}{off:+g}" if off else
                                  f"functions.{bid}.{key}.{name}: no line {'ends' if key == 'in' else 'starts'} "
                                  f"at {bid}.{side}")
        for side, off in ends_in.get(bid, []):
            if fn.input_at(side, off) is None:
                errors.append(f"functions.{bid}: the line ending at {bid}.{side}{off:+g} is not one of its "
                              f"inputs; name it in functions.{bid}.in" if off else
                              f"functions.{bid}: the line ending at {bid}.{side} is not one of its inputs; "
                              f"name it in functions.{bid}.in")
        for side, off in ends_out.get(bid, []):
            if fn.output_at(side, off) is None:
                errors.append(f"functions.{bid}.out：连线起点 {side}{off:+g} 未声明为输出端口")


# ---- evaluation --------------------------------------------------------------------
def evaluate(kind: str, x: dict[str, float]) -> dict[str, float]:
    """Python reference, the same formulas as the C and SPICE forms."""
    if kind == "c":
        raise FunctionError("Python／ngspice 不执行 C 代码方块；请使用原生离散导出")
    if kind == "clarke":
        a, b = x["a"], x["b"]
        c = x["c"] if "c" in x else -a - b
        return {"alpha": (2 * a - b - c) / 3, "beta": (b - c) / SQ3}
    if kind == "iclarke":
        al, be = x["alpha"], x["beta"]
        return {"a": al, "b": -al / 2 + SQ3 / 2 * be, "c": -al / 2 - SQ3 / 2 * be}
    if kind == "park":
        c, s = math.cos(x["theta"]), math.sin(x["theta"])
        return {"d": x["alpha"] * c + x["beta"] * s, "q": -x["alpha"] * s + x["beta"] * c}
    if kind == "ipark":
        c, s = math.cos(x["theta"]), math.sin(x["theta"])
        return {"alpha": x["d"] * c - x["q"] * s, "beta": x["d"] * s + x["q"] * c}
    if kind == "svpwm":
        ph = evaluate("iclarke", x)
        off = (max(ph.values()) + min(ph.values())) / 2
        vdc = x["vdc"] if abs(x["vdc"]) > 1e-12 else 1e-12
        return {"d" + k: min(max(0.5 + (v - off) / vdc, 0.0), 1.0) for k, v in ph.items()}
    if kind == "wrap":
        return {"y": x["x"] - TWO_PI * math.floor(x["x"] / TWO_PI)}
    raise FunctionError(kind)


def expressions(kind: str, x: dict[str, str], lang: str) -> dict[str, str]:
    """Output name -> expression of the input expressions ``x``.  ``lang``:
    "c" (float), "cd" (double) or "spice" (an ngspice B source)."""
    if kind == "c":
        raise FunctionError("Python／ngspice 不执行 C 代码方块；请使用原生离散导出")
    f = "f" if lang == "c" else ""
    cos = (lambda a: f"cos{f}({a})")
    sin = (lambda a: f"sin{f}({a})")
    k = (lambda v: f"{v!r}{f}") if lang != "spice" else (lambda v: f"{v:.17g}")
    if kind == "clarke":
        c = x.get("c", f"(-({x['a']}) - ({x['b']}))")
        return {"alpha": f"(2*({x['a']}) - ({x['b']}) - ({c}))/{k(3.0)}",
                "beta": f"(({x['b']}) - ({c}))*{k(1 / SQ3)}"}
    if kind == "iclarke":
        al, be = x["alpha"], x["beta"]
        return {"a": f"({al})", "b": f"(-{k(0.5)}*({al}) + {k(SQ3 / 2)}*({be}))",
                "c": f"(-{k(0.5)}*({al}) - {k(SQ3 / 2)}*({be}))"}
    if kind == "park":
        al, be, th = x["alpha"], x["beta"], x["theta"]
        return {"d": f"(({al})*{cos(th)} + ({be})*{sin(th)})",
                "q": f"(-({al})*{sin(th)} + ({be})*{cos(th)})"}
    if kind == "ipark":
        d, q, th = x["d"], x["q"], x["theta"]
        return {"alpha": f"(({d})*{cos(th)} - ({q})*{sin(th)})",
                "beta": f"(({d})*{sin(th)} + ({q})*{cos(th)})"}
    if kind == "svpwm":
        ph = expressions("iclarke", x, lang)
        if lang == "spice":
            mx = f"max(max({ph['a']}, {ph['b']}), {ph['c']})"
            mn = f"min(min({ph['a']}, {ph['b']}), {ph['c']})"
            vdc = f"max(abs({x['vdc']}), 1e-12)"
            return {"d" + p: f"min(max(0.5 + (({e}) - ({mx} + {mn})/2)/{vdc}, 0), 1)" for p, e in ph.items()}
        mx = f"fmax{f}(fmax{f}({ph['a']}, {ph['b']}), {ph['c']})"
        mn = f"fmin{f}(fmin{f}({ph['a']}, {ph['b']}), {ph['c']})"
        vdc = f"fmax{f}(fabs{f}({x['vdc']}), {k(1e-12)})"
        return {"d" + p: f"fmin{f}(fmax{f}({k(0.5)} + (({e}) - ({mx} + {mn})*{k(0.5)})/{vdc}, {k(0.0)}), {k(1.0)})"
                for p, e in ph.items()}
    if kind == "wrap":
        if lang == "spice":
            return {"y": f"(({x['x']}) - {k(TWO_PI)}*floor(({x['x']})/{k(TWO_PI)}))"}
        return {"y": f"(({x['x']}) - {k(TWO_PI)}*floor{f}(({x['x']})/{k(TWO_PI)}))"}
    raise FunctionError(kind)
