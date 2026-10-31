"""Function marks inside a block: the table's ``glyphs`` section.

A glyph says what a collapsed or partly collapsed block contains, the way
papers draw a converter as a box.  ``acdc`` / ``dcdc`` / ``dcac`` are the
IEC 60617 / GB/T 4728 converter symbol: a diagonal across the box with the
two kinds of supply in opposite corners.  ``mosfet`` / ``igbt`` / ``diode``
are the common (not standardised) marks for a switching bridge or a
rectifier; say in the caption which bridge is meant.  Glyphs never touch
connectivity.

    {"kind": "dcdc", "from": [x0, y0], "to": [x1, y1]}
"""
from __future__ import annotations

import math

from .layout import Layout

CONVERTERS = {"acdc": ("AC", "DC"), "dcdc": ("DC", "DC"), "dcac": ("DC", "AC")}
DEVICES = ("mosfet", "igbt", "diode")
KINDS = tuple(CONVERTERS) + DEVICES
CORNER_INSET = 0.28     # fraction of the box from a corner to its converter text
DEVICE_SCALE = 0.8      # circuitikz symbol size inside a block
DIODE_LEAD = 0.05       # fraction of the box height for each diode lead


def _box(g):
    (ax, ay), (bx, by) = g["from"], g["to"]
    return min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)


def problems(layout: Layout) -> list[str]:
    raw = layout.extra.get("glyphs", [])
    if not isinstance(raw, list):
        return ["glyphs must be a list"]
    out = []
    for i, g in enumerate(raw):
        name = f"glyphs[{i}]"
        if not isinstance(g, dict) or set(g) - {"kind", "from", "to"} or g.get("kind") not in KINDS:
            out.append(f"{name}: needs kind (one of {list(KINDS)}), from and to")
            continue
        pts = [g.get("from"), g.get("to")]
        if not all(isinstance(p, list) and len(p) == 2 and all(
                type(v) in (int, float) and math.isfinite(v) for v in p) for p in pts):
            out.append(f"{name}: from / to must be [x, y]")
            continue
        x0, y0, x1, y1 = _box(g)
        if x1 - x0 < 0.2 or y1 - y0 < 0.2:
            out.append(f"{name}: the box must be at least 0.2 units on each side")
    return out


def glyphs(layout: Layout) -> list[dict]:
    return [] if problems(layout) else list(layout.extra.get("glyphs", []))


def tikz(g: dict, color: str = "black") -> str:
    x0, y0, x1, y1 = _box(g)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    def p(x, y):
        return f"({x:.4f},{y:.4f})"
    if g["kind"] in CONVERTERS:
        top, bottom = CONVERTERS[g["kind"]]
        k = CORNER_INSET
        return (rf"\draw[{color}] {p(x0, y0)} -- {p(x1, y1)};" "\n"
                rf"\node[{color}] at {p(x0 + k * (x1 - x0), y1 - k * (y1 - y0))} {{{top}}};" "\n"
                rf"\node[{color}] at {p(x1 - k * (x1 - x0), y0 + k * (y1 - y0))} {{{bottom}}};" "\n")
    if g["kind"] == "diode":
        # filled diode pointing up with short leads, as a rectifier mark
        lead = DIODE_LEAD * (y1 - y0)
        return (rf"\node[fulldiodeshape, rotate=90, scale={DEVICE_SCALE}, {color}] (glyph) at {p(cx, cy)} {{}};" "\n"
                rf"\draw[{color}] (glyph.left) -- ++(0,{-lead:.4f}) (glyph.right) -- ++(0,{lead:.4f});" "\n")
    shape = {"mosfet": "nmos, bodydiode", "igbt": "nigbt, bodydiode"}[g["kind"]]
    return rf"\node[{shape}, scale={DEVICE_SCALE}, {color}] at {p(cx, cy)} {{}};" "\n"
