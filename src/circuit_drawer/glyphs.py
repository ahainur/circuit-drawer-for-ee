"""Function marks inside a block: the table's ``glyphs`` section.

A glyph says what a collapsed or partly collapsed block contains, the way
papers draw a converter as a box.  ``acdc`` / ``dcdc`` / ``dcac`` are the
IEC 60617 / GB/T 4728 converter symbol: a diagonal across the box with the
two kinds of supply in opposite corners.  ``mosfet`` / ``igbt`` / ``diode``
are the common (not standardised) marks for a switching bridge or a
rectifier; say in the caption which bridge is meant.  ``battery``, ``pv``,
``rack``, ``capacitor``, ``ac`` and ``square`` are pictorial icons for
system-level block diagrams (storage, PV string, server rack / data-centre
load, DC link, AC grid or sinusoidal port, square-wave bridge port), drawn as
plain vector lines so they print like the rest of the figure.  A block can
carry any of these marks as its ``icon`` (blocks.py).  Glyphs never touch
connectivity.

    {"kind": "dcdc", "from": [x0, y0], "to": [x1, y1]}
"""
from __future__ import annotations

import math

from .layout import Layout

CONVERTERS = {"acdc": ("AC", "DC"), "dcdc": ("DC", "DC"), "dcac": ("DC", "AC")}
DEVICES = ("mosfet", "igbt", "diode")


def _wave(f, n: int = 24) -> list:
    """One period of f on [0.18, 0.82] across a circle of radius 0.34."""
    return [(0.18 + 0.64 * k / n, 0.5 + 0.16 * f(k / n)) for k in range(n + 1)]


# Pictorial icons in a unit square (0..1 both ways, y up), as line work:
# ("line", [points]) open polyline, ("rect", p0, p1) closed white-filled box
# drawn in order (later ones hide earlier ones), ("circle", centre, r) and
# ("dot", centre, r) filled.  The editor draws the very same primitives.
ICONS = {
    # battery: two cells, long plate +, leads to the box
    "battery": [("line", [(0.5, 0.92), (0.5, 0.66)]), ("line", [(0.2, 0.66), (0.8, 0.66)]),
                ("line", [(0.34, 0.56), (0.66, 0.56)]), ("line", [(0.2, 0.44), (0.8, 0.44)]),
                ("line", [(0.34, 0.34), (0.66, 0.34)]), ("line", [(0.5, 0.34), (0.5, 0.08)])],
    # PV string: the panel's two diagonals meeting at the bottom middle
    "pv": [("line", [(0.06, 0.94), (0.5, 0.1), (0.94, 0.94)])],
    # server rack: two cabinets behind, the front one with four slots
    "rack": [("rect", (0.3, 0.28), (0.94, 0.94)), ("rect", (0.18, 0.17), (0.82, 0.83)),
             ("rect", (0.06, 0.06), (0.7, 0.72))]
            + [("rect", (0.14, y), (0.62, y + 0.09)) for y in (0.15, 0.28, 0.41, 0.54)]
            + [("dot", (0.2, y + 0.045), 0.018) for y in (0.15, 0.28, 0.41, 0.54)],
    # DC-link capacitor with leads
    "capacitor": [("line", [(0.5, 0.92), (0.5, 0.56)]), ("line", [(0.22, 0.56), (0.78, 0.56)]),
                  ("line", [(0.22, 0.44), (0.78, 0.44)]), ("line", [(0.5, 0.44), (0.5, 0.08)])],
    # AC source / grid: sine in a circle
    "ac": [("circle", (0.5, 0.5), 0.34), ("line", _wave(lambda t: math.sin(2 * math.pi * t)))],
    # square-wave bridge port (DAB / QAB equivalent circuits)
    "square": [("circle", (0.5, 0.5), 0.34),
               ("line", [(0.24, 0.5), (0.24, 0.62), (0.5, 0.62), (0.5, 0.38), (0.76, 0.38), (0.76, 0.5)])],
}
ICON_INSET = 0.1        # fraction of the shorter box side kept clear around an icon
KINDS = tuple(CONVERTERS) + DEVICES + tuple(ICONS)


def icon_shapes(kind: str, box) -> list:
    """An icon's primitives in layout units, fitted square and centred in
    ``box`` = (x0, y0, x1, y1).  Radii scale with the square's side."""
    x0, y0, x1, y1 = box
    side = min(x1 - x0, y1 - y0) * (1 - 2 * ICON_INSET)
    ox, oy = (x0 + x1 - side) / 2, (y0 + y1 - side) / 2
    def p(q):
        return (round(ox + q[0] * side, 4), round(oy + q[1] * side, 4))
    out = []
    for prim in ICONS[kind]:
        if prim[0] == "line":
            out.append(("line", [p(q) for q in prim[1]]))
        elif prim[0] == "rect":
            out.append(("rect", p(prim[1]), p(prim[2])))
        else:
            out.append((prim[0], p(prim[1]), round(prim[2] * side, 4)))
    return out
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
    if g["kind"] in ICONS:
        out = []
        for prim in icon_shapes(g["kind"], (x0, y0, x1, y1)):
            if prim[0] == "line":
                out.append(rf"\draw[{color}, line join=round] " + " -- ".join(p(*q) for q in prim[1]) + ";")
            elif prim[0] == "rect":
                out.append(rf"\filldraw[{color}, fill=white] {p(*prim[1])} rectangle {p(*prim[2])};")
            elif prim[0] == "circle":
                out.append(rf"\draw[{color}] {p(*prim[1])} circle ({prim[2]:.4f});")
            else:
                out.append(rf"\fill[{color}] {p(*prim[1])} circle ({prim[2]:.4f});")
        return "\n".join(out) + "\n"
    shape = {"mosfet": "nmos, bodydiode", "igbt": "nigbt, bodydiode"}[g["kind"]]
    return rf"\node[{shape}, scale={DEVICE_SCALE}, {color}] at {p(cx, cy)} {{}};" "\n"
