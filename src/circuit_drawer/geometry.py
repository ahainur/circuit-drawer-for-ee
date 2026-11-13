"""Physical footprint of each element at a given layout scale.

Everything returned here is in printed centimetres: layout coordinates are
multiplied by ``scale``, symbol sizes are absolute.  The validator and the
emitter share these numbers, so what is checked is what gets drawn.
"""

from __future__ import annotations

# A NoConn cross is 1.6 mm wide on paper, independent of the diagram fit.
NO_CONNECT_HALF_CM = 0.08


def no_connect_mark(point, scale):
    x, y = point
    dx, dy = NO_CONNECT_HALF_CM / scale.x, NO_CONNECT_HALF_CM / scale.y
    return [((x-dx, y-dy), (x+dx, y+dy)),
            ((x-dx, y+dy), (x+dx, y-dy))]

import math
import re
from dataclasses import dataclass, field
from typing import NamedTuple

from .layout import PIN_NAMES, Element, Point, SWITCHES, TWO_TERMINAL, windings
from .profiles import Profile

EPS = 1e-4

# Half thickness of each two-terminal symbol across its axis (cm).
HALF_HEIGHT = {"R": 0.15, "L": 0.13, "C": 0.22, "D": 0.17, "scr": 0.215, "V": 0.25, "vac": 0.25, "I": 0.25,
               "battery": 0.23}
# Visible length of each symbol along its axis, as a fraction of
# bipoles/length (the capacitor and diode drawings are much shorter than the
# nominal bipole length).
BODY_FRAC = {"R": 0.8, "L": 0.9, "C": 0.35, "D": 0.5, "scr": 1.0, "V": 0.6, "vac": 0.6, "I": 0.6,
             "battery": 0.16}
# A battery (a V source with opts.battery) is CircuitikZ battery1: two plates
# 0.11 cm apart, the long one 0.45 cm across, at bipoles/length 0.75 cm.


HALF_HEIGHT["zener"] = HALF_HEIGHT["D"]
BODY_FRAC["zener"] = BODY_FRAC["D"]
# CircuitikZ's American resistor: width .8 and total height .3 of the
# default .75 cm bipole. Keep the electrical kind R and the existing pins.
HALF_HEIGHT["R_ic"], BODY_FRAC["R_ic"] = .1125, .8
RESISTOR_ZIGS = 3
# CircuitikZ emptyzdiode: cathode hook returns by 0.4 * half the triangle
# width, inside the ordinary diode body (pgfcircbipoles.tex, 2026/05/24).
ZENER_HOOK_FRAC = 0.2


def symbol_kind(el: Element) -> str:
    """Drawing variants keep their electrical kind and pin convention."""
    if el.kind == "R" and el.opts.get("symbol_style") == "ic":
        return "R_ic"
    if el.kind == "V" and el.opts.get("battery"):
        return "battery"
    return "zener" if el.kind == "D" and el.opts.get("zener") else el.kind


def body_diode_visible(el: Element, conducting: bool = False) -> bool:
    """A visual preset never removes a physical diode or its active mode path."""
    return el.opts.get("diode", True) is not False and (el.opts.get("symbol_style") != "ic" or conducting)
# The CircuitikZ thyristor (Ty) is a diode with a gate lead that leaves the
# cathode bar diagonally and turns up at the end of the symbol, on the left of
# the anode -> cathode path (the right with opts.mirror). Measured from
# CircuitikZ 2026/05/24 at bipoles/length 0.75 cm, as (along, across) from the
# centre in bipole lengths; it fills the whole bipole length, hence BODY_FRAC 1.
SCR_GATE = ((0.167, 0.0), (0.5, 0.267), (0.5, 0.547))
SCR_GATE_BOX = ((0.12, 0.04), (0.52, 0.56))     # (along, across) corners of its body
MIN_LEAD = 0.06          # visible straight lead on each side of a body
LABEL_GAP = 0.08         # gap between a symbol body and its label
# Switch footprint around its centre (cm), measured from CircuitikZ 2023 with
# transistors/scale=0.9: drain/source anchors at +-0.371, gate stub at -0.472.
# Other transistor scales multiply these by sw_k(profile).
SW_HALF_SPAN = 0.371
SW_LEFT, SW_RIGHT, SW_HALF_H = 0.50, 0.30, 0.37
SW_LABEL_DX = 0.33
# CircuitikZ PMOS gate dot: nodes width (.04) times bipoles/length, unscaled.
PMOS_DOT_RADIUS = 0.04
COIL_HALF_W = 0.13
CORE_GAP = 0.05          # half distance between the two core lines
WINDING_GAP = (0.5, 1.0)  # cm between primary and secondary winding axes
DOT_OFFSET, DOT_LIFT = 0.17, 0.28   # polarity dot position from the coil centre
# The motor circle encloses its outer phase leads, leaving 0.08 cm clearance.
PMSM_RADIUS, PMSM_LEAD, PMSM_EDGE = 0.42, 0.15, 0.08


@dataclass(frozen=True)
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    def contains(self, point: Point) -> bool:
        """Include the border, for marks that occupy a complete module frame."""
        return self.x0 - EPS <= point[0] <= self.x1 + EPS and self.y0 - EPS <= point[1] <= self.y1 + EPS

    def overlaps(self, other: "Box", clearance: float = 0.0) -> bool:
        if isinstance(other, DiagBox):
            return other.overlaps(self, clearance)
        return (self.x0 < other.x1 + clearance - EPS
                and other.x0 < self.x1 + clearance - EPS
                and self.y0 < other.y1 + clearance - EPS
                and other.y0 < self.y1 + clearance - EPS)

    def hits_segment(self, p: Point, q: Point) -> bool:
        """True if a segment enters the open box."""
        if abs(p[0] - q[0]) > EPS and abs(p[1] - q[1]) > EPS:   # 45-degree line
            return _sat_hit(_rect_of(self), _seg_rect(p, q))
        if abs(p[1] - q[1]) < EPS:  # horizontal
            y = p[1]
            lo, hi = sorted((p[0], q[0]))
            return (self.y0 + EPS < y < self.y1 - EPS
                    and lo < self.x1 - EPS and hi > self.x0 + EPS)
        x = p[0]
        lo, hi = sorted((p[1], q[1]))
        return (self.x0 + EPS < x < self.x1 - EPS
                and lo < self.y1 - EPS and hi > self.y0 + EPS)


@dataclass(frozen=True)
class DiagBox(Box):
    """Body of a part drawn at 45 degrees: a rectangle turned with the part,
    centred at ``c`` with half sizes ``hu`` along ``u`` and ``hv`` across it.
    x0..y1 hold its bounding box, for extents and the editor's hit area."""
    c: Point = (0.0, 0.0)
    u: Point = (1.0, 0.0)
    hu: float = 0.0
    hv: float = 0.0

    def overlaps(self, other: Box, clearance: float = 0.0) -> bool:
        return _sat_overlap(_rect_of(self), _rect_of(other), clearance)

    def hits_segment(self, p: Point, q: Point) -> bool:
        return _sat_hit(_rect_of(self), _seg_rect(p, q))


def diag_box(c: Point, u: Point, hu: float, hv: float) -> DiagBox:
    ex = abs(u[0]) * hu + abs(u[1]) * hv
    ey = abs(u[1]) * hu + abs(u[0]) * hv
    return DiagBox(c[0] - ex, c[1] - ey, c[0] + ex, c[1] + ey, c, u, hu, hv)


# oriented rectangle: (centre, unit axis, half size along it, half size across)
def _rect_of(b: Box):
    if isinstance(b, DiagBox):
        return (b.c, b.u, b.hu, b.hv)
    return (((b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2), (1.0, 0.0),
            (b.x1 - b.x0) / 2, (b.y1 - b.y0) / 2)


def _seg_rect(p: Point, q: Point):
    L = math.hypot(q[0] - p[0], q[1] - p[1])
    return (((p[0] + q[0]) / 2, (p[1] + q[1]) / 2),
            ((q[0] - p[0]) / L, (q[1] - p[1]) / L), L / 2, 0.0)


def _interval(r, n: Point) -> tuple[float, float]:
    (cx, cy), (ux, uy), hu, hv = r
    c = cx * n[0] + cy * n[1]
    e = hu * abs(ux * n[0] + uy * n[1]) + hv * abs(-uy * n[0] + ux * n[1])
    return c - e, c + e


def _axes(*rects) -> list[Point]:
    out = []
    for _, (ux, uy), _, _ in rects:
        out += [(ux, uy), (-uy, ux)]
    return out


def _sat_overlap(a, b, clearance: float) -> bool:
    """Separating-axis test: closer than ``clearance`` on every axis."""
    for n in _axes(a, b):
        (a0, a1), (b0, b1) = _interval(a, n), _interval(b, n)
        if max(a0, b0) - min(a1, b1) >= clearance - EPS:
            return False
    return True


def _sat_hit(box, seg) -> bool:
    """A segment enters the open rectangle: its projection lies strictly
    inside the box's on every axis (a point projection must be inside)."""
    for n in _axes(box, seg):
        (b0, b1), (s0, s1) = _interval(box, n), _interval(seg, n)
        if s1 - s0 < EPS:
            if not b0 + EPS < s0 < b1 - EPS:
                return False
        elif min(b1, s1) - max(b0, s0) <= EPS:
            return False
    return True


def centred(cx: float, cy: float, w: float, h: float) -> Box:
    return Box(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


# ---------------------------------------------------------------------------
# text size estimate (8 pt Times)
# ---------------------------------------------------------------------------
_CMD = re.compile(r"\\(mathrm|mathit|text|mathbf)\{([^{}]*)\}")


# A CJK character is one em wide: 8 pt = 0.282 cm, plus a little for the
# inter-character spacing CJKutf8 adds.
WIDE_GLYPH = 0.29


def is_wide(c: str) -> bool:
    """CJK ideographs, kana, hangul and full-width punctuation."""
    o = ord(c)
    return (0x2E80 <= o <= 0x9FFF or 0xAC00 <= o <= 0xD7AF or 0xF900 <= o <= 0xFAFF
            or 0xFF00 <= o <= 0xFFEF or 0x3000 <= o <= 0x303F)


def has_wide(text: str | None) -> bool:
    return bool(text) and any(is_wide(c) for c in text)


def text_size(tex: str, font_pt: float = 8.0) -> tuple[float, float]:
    """Rough width/height in cm of a short math label such as ``$L_{m}$``."""
    if tex in (r"$\vdots$", r"$\cdots$"):
        # Conservative 8 pt math-glyph bounds, including breathing room.
        width, height = (0.23, 0.40) if tex == r"$\vdots$" else (0.45, 0.30)
        return width * font_pt / 8, height * font_pt / 8
    s = tex.replace("$", "")
    s = _CMD.sub(lambda m: m.group(2), s)
    s = re.sub(r"\\[a-zA-Z]+", "x", s)          # \sigma, \alpha -> one glyph
    base, sub, wide, has_sub = 0, 0, 0, False
    i = 0
    while i < len(s):
        c = s[i]
        if c in "_^":
            has_sub = True
            i += 1
            if i < len(s) and s[i] == "{":
                j = s.index("}", i)
                sub += len(s[i + 1:j].replace(" ", ""))
                i = j + 1
            else:
                sub += 1
                i += 1
            continue
        if is_wide(c):
            wide += 1
        elif c not in "{} ":
            base += 1
        i += 1
    k = font_pt / 8.0
    w = (0.19 * base + WIDE_GLYPH * wide + 0.13 * sub + 0.04) * k
    h = (0.36 if has_sub else 0.30) * k
    return w, h


# ---------------------------------------------------------------------------
# element footprints
# ---------------------------------------------------------------------------
@dataclass
class Footprint:
    leads: list[tuple[Point, Point]] = field(default_factory=list)
    bodies: list[Box] = field(default_factory=list)
    labels: list[Box] = field(default_factory=list)
    # emitter hints
    label_side: str = "^"          # CircuitikZ l^ / l_ for two-terminal parts
    centre: Point | None = None
    extra: dict = field(default_factory=dict)


class Scale(NamedTuple):
    """cm per layout unit, horizontally and vertically.  The width of a paper
    column fixes ``x``; height is free, so ``y`` need not shrink with it."""
    x: float
    y: float


def scaled(p: Point, s: Scale) -> Point:
    return (p[0] * s.x, p[1] * s.y)


def unscaled(p: Point, s: Scale) -> Point:
    return (p[0] / s.x, p[1] / s.y)


def _axis(a: Point, b: Point) -> str | None:
    if abs(a[1] - b[1]) < EPS and abs(a[0] - b[0]) > EPS:
        return "h"
    if abs(a[0] - b[0]) < EPS and abs(a[1] - b[1]) > EPS:
        return "v"
    return None


def is_diagonal(a: Point, b: Point) -> bool:
    """Exactly 45 degrees in layout units (the scale is square whenever a
    layout has such a part, so it is 45 degrees on paper too)."""
    dx, dy = abs(a[0] - b[0]), abs(a[1] - b[1])
    return dx > EPS and abs(dx - dy) < 1e-3


def default_label_pos(axis: str) -> str:
    return "above" if axis == "h" else "right"


def _side_for(a: Point, b: Point, pos: str) -> str:
    """Translate above/below/left/right into CircuitikZ ^ (left of travel) or _."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    left = (-dy, dx)  # normal to the left of the drawing direction
    want = {"above": (0, 1), "below": (0, -1),
            "left": (-1, 0), "right": (1, 0)}[pos]
    return "^" if left[0] * want[0] + left[1] * want[1] > 0 else "_"


SIZE_RANGE = (0.5, 2.0)
FONT_RANGE = (5.0, 14.0)    # pt
TEXT_CLEARANCE = 0.06      # cm at 8 pt, proportional like text_size's bounds


def font_of(value, prof: Profile) -> float:
    """A text's size in pt: its own setting within FONT_RANGE, else the
    profile's (8 pt for IEEE)."""
    try:
        return min(max(float(value), FONT_RANGE[0]), FONT_RANGE[1]) if value else prof.font_pt
    except (TypeError, ValueError):
        return prof.font_pt


def text_clearance(value, prof: Profile) -> float:
    return TEXT_CLEARANCE * font_of(value, prof) / 8


def _size(value, default: float = 1.0) -> float:
    try:
        z = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(z, SIZE_RANGE[0]), SIZE_RANGE[1]) if math.isfinite(z) else default


def size_of(el: Element) -> float:
    """Symbol size factor of one part (opts.scale, 1 = the profile's size);
    only the symbol grows or shrinks, the type stays the journal's 8 pt."""
    return _size(el.opts.get("scale", 1.0))


def axis_sizes(el: Element) -> tuple[float, float]:
    """Length along the pins, width across them (in the part's own frame).

    Each axis overrides the legacy uniform scale independently. For a
    transformer these are the winding length and width; pin positions stay put.
    """
    z = size_of(el)
    return (_size(el.opts.get("scale_length", z), z),
            _size(el.opts.get("scale_width", z), z))


def footprint(el: Element, s: Scale, prof: Profile) -> Footprint:
    fp = _base_footprint(el, s, prof)
    offset = el.opts.get("label_offset")
    if offset is not None:
        # Malformed JSON still gets a usable editor sketch; the schema reports it.
        from .schema import OPTS
        if OPTS["label_offset"].problem(offset) is None and any(offset):
            index = -1 if el.kind == "transformer" else 0
            if fp.labels and (el.opts.get("ratio") if el.kind == "transformer" else el.label):
                fp.extra["label_offset"] = list(offset)
                dx, dy = scaled(offset, s)
                b = fp.labels[index]
                fp.labels[index] = Box(b.x0 + dx, b.y0 + dy, b.x1 + dx, b.y1 + dy)
                for key in (("ratio_at",) if el.kind == "transformer" else ("label_at",)):
                    if key in fp.extra:
                        x, y = fp.extra[key]
                        fp.extra[key] = (x + dx, y + dy)
    return fp


def _base_footprint(el: Element, s: Scale, prof: Profile) -> Footprint:
    if el.kind in TWO_TERMINAL:
        fp = _two_terminal(el, s, prof)
        if symbol_kind(el) == "R_ic" and fp.bodies:
            a, b = scaled(el.pins["a"], s), scaled(el.pins["b"], s)
            length, width = axis_sizes(el)
            span = prof.bipole_len * BODY_FRAC["R_ic"] * length
            half = HALF_HEIGHT["R_ic"] * width
            distance = math.dist(a, b)
            if distance <= EPS:
                return fp
            u = ((b[0]-a[0])/distance, (b[1]-a[1])/distance)
            c = ((a[0]+b[0])/2, (a[1]+b[1])/2)
            def at(x, y):
                return (c[0]+x*u[0]-y*u[1], c[1]+x*u[1]+y*u[0])
            # Six alternating peaks with a half-step at each end, as in
            # CircuitikZ's three-zig resistor; all rotations share this path.
            peaks = [at(-span/2+(2*i+1)*span/(4*RESISTOR_ZIGS), (-1)**i*half)
                     for i in range(2*RESISTOR_ZIGS)]
            fp.extra["resistor_path"] = [a, at(-span/2, 0), *peaks, at(span/2, 0), b]
        if symbol_kind(el) == "zener":
            fp.extra["zener_hook"] = ZENER_HOOK_FRAC
        _source_wave(el, fp)
        if el.kind == "I" and fp.bodies:
            a, b = scaled(el.pins["a"], s), scaled(el.pins["b"], s)
            d = math.dist(a, b)
            if d > EPS:
                u = ((b[0]-a[0])/d, (b[1]-a[1])/d)
                if el.opts.get("invert"):
                    u = (-u[0], -u[1])
                c = ((a[0]+b[0])/2, (a[1]+b[1])/2)
                length, width = axis_sizes(el)
                radius = prof.bipole_len * BODY_FRAC["I"] * length / 2
                # Match the arrow within CircuitikZ's American current source.
                def p(x, y=0):
                    return (c[0]+x*u[0]-y*u[1], c[1]+x*u[1]+y*u[0])
                tip, half = .7*radius, .25*HALF_HEIGHT["I"]*width
                fp.extra["current_arrow"] = {"shaft": [p(-tip), p(tip)],
                                              "head": [p(tip), p(.25*radius, half), p(.25*radius, -half)]}
        return fp
    if el.kind in SWITCHES:
        return _switch(el, s, prof)
    if el.kind == "transformer":
        return _transformer(el, s, prof)
    if el.kind == "pmsm":
        return _pmsm(el, s, prof)
    if el.kind == "port":
        return _port(el, s, prof)
    raise ValueError(f"unknown element kind {el.kind!r}")


# A small waveform beside a 0.5 cm source circle, in printed centimetres.
# It stays upright when the electrical source is rotated or reversed.
WAVE_WIDTH, WAVE_HEIGHT, WAVE_GAP = 0.42, 0.28, 0.10


def wave_symbol(at: Point, wave: str, z: float = 1) -> tuple[list[Point], Box]:
    path = [(0, 0), (.5, 0), (.5, 1), (1, 1)]
    if wave == "step-down":
        path = [(x, 1-y) for x, y in path]
    elif wave == "pulse":
        path = [(0, 0), (.25, 0), (.25, 1), (.75, 1), (.75, 0), (1, 0)]
    w, h = WAVE_WIDTH * z, WAVE_HEIGHT * z
    return ([(at[0] + (x-.5)*w, at[1] + (y-.5)*h) for x, y in path],
            centred(*at, w, h))


def _source_wave(el: Element, fp: Footprint) -> None:
    from .source_waves import source_options
    try:
        opt = source_options(el)
    except (ValueError, TypeError):
        return                         # schema reports the invalid option
    if opt is None or not fp.bodies:
        return
    b = fp.bodies[0]
    mx, my = (b.x0+b.x1)/2, (b.y0+b.y1)/2
    pos, z = opt["pos"], opt["scale"]
    if pos == "auto":
        axis = _axis(el.pins["a"], el.pins["b"])
        lab = el.label_pos or (default_label_pos(axis) if axis else "left")
        pos = {"left": "right", "right": "left", "above": "below", "below": "above"}[lab]
    at = {"left": (b.x0-WAVE_GAP-WAVE_WIDTH*z/2, my),
          "right": (b.x1+WAVE_GAP+WAVE_WIDTH*z/2, my),
          "above": (mx, b.y1+WAVE_GAP+WAVE_HEIGHT*z/2),
          "below": (mx, b.y0-WAVE_GAP-WAVE_HEIGHT*z/2)}[pos]
    pts, box = wave_symbol(at, opt["wave"], z)
    fp.extra["source_wave"] = {"points": pts, "box": box}


def _two_terminal(el: Element, s: Scale, prof: Profile) -> Footprint:
    a, b = scaled(el.pins["a"], s), scaled(el.pins["b"], s)
    fp = Footprint(leads=[(a, b)])
    axis = _axis(a, b)
    if axis is None:
        if is_diagonal(el.pins["a"], el.pins["b"]):
            _diagonal(el, a, b, fp, prof)
        return fp
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    length, width = axis_sizes(el)
    hh = HALF_HEIGHT[symbol_kind(el)] * width
    L = prof.bipole_len * BODY_FRAC[symbol_kind(el)] * length
    body = centred(mx, my, L, 2 * hh) if axis == "h" else centred(mx, my, 2 * hh, L)
    fp.bodies.append(body)
    pos = el.label_pos or default_label_pos(axis)
    outer = body
    if el.kind == "scr":
        gate_side = _scr_gate(el, a, b, (mx, my), fp, prof)
        pos = el.label_pos or {(0, 1): "below", (0, -1): "above", (-1, 0): "right", (1, 0): "left"}[gate_side]
        if {"above": (0, 1), "below": (0, -1), "left": (-1, 0), "right": (1, 0)}[pos] == gate_side:
            outer = fp.bodies[1]            # a label on the gate side clears the gate lead
    fp.label_side = _side_for(a, b, pos)
    if el.label:
        w, h = text_size(el.label, font_of(el.opts.get("label_size"), prof))
        cx, cy = {"above": (mx, max(body.y1, outer.y1) + LABEL_GAP + h / 2),
                  "below": (mx, min(body.y0, outer.y0) - LABEL_GAP - h / 2),
                  "left": (min(body.x0, outer.x0) - LABEL_GAP - w / 2, my),
                  "right": (max(body.x1, outer.x1) + LABEL_GAP + w / 2, my)}[pos]
        fp.labels.append(centred(cx, cy, w, h))
    return fp


def _scr_gate(el: Element, a: Point, b: Point, c: Point, fp: Footprint, prof: Profile) -> tuple[int, int]:
    """The thyristor's gate lead (``extra["gate"]``, printed cm) and its body;
    returns the page direction the lead points to."""
    length, width = axis_sizes(el)
    d = math.hypot(b[0] - a[0], b[1] - a[1])
    u = ((b[0] - a[0]) / d, (b[1] - a[1]) / d)
    n = (-u[1], u[0]) if not el.opts.get("mirror") else (u[1], -u[0])
    along, across = prof.bipole_len * length, prof.bipole_len * width
    at = lambda p, q: (c[0] + u[0] * p * along + n[0] * q * across, c[1] + u[1] * p * along + n[1] * q * across)
    fp.extra["gate"] = [at(p, q) for p, q in SCR_GATE]
    (p0, q0), (p1, q1) = SCR_GATE_BOX
    corners = [at(p0, q0), at(p1, q1)]
    fp.bodies.append(Box(min(x for x, _ in corners), min(y for _, y in corners),
                         max(x for x, _ in corners), max(y for _, y in corners)))
    return round(n[0]), round(n[1])


def _diagonal(el: Element, a: Point, b: Point, fp: Footprint, prof: Profile) -> None:
    L = math.hypot(b[0] - a[0], b[1] - a[1])
    u = ((b[0] - a[0]) / L, (b[1] - a[1]) / L)
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    length, width = axis_sizes(el)
    hh = HALF_HEIGHT[symbol_kind(el)] * width
    fp.bodies.append(diag_box((mx, my), u, prof.bipole_len * BODY_FRAC[symbol_kind(el)] * length / 2, hh))
    fp.extra["diag"] = {"u": u, "half_len": prof.bipole_len * BODY_FRAC[symbol_kind(el)] * length / 2,
                        "half_h": hh}
    if el.label:
        # beside the body, on the side of the normal that points to label_pos
        want = {"above": (0, 1), "below": (0, -1), "left": (-1, 0),
                "right": (1, 0)}[el.label_pos or "left"]
        n = (-u[1], u[0])
        if n[0] * want[0] + n[1] * want[1] < 0:
            n = (-n[0], -n[1])
        w, h = text_size(el.label, font_of(el.opts.get("label_size"), prof))
        d = hh + LABEL_GAP + abs(n[0]) * w / 2 + abs(n[1]) * h / 2
        fp.labels.append(centred(mx + n[0] * d, my + n[1] * d, w, h))
        fp.extra["label_at"] = (mx + n[0] * d, my + n[1] * d)


def sw_k(prof: Profile) -> float:
    return prof.transistor_scale / 0.9


def switch_frame(el: Element) -> tuple[Point, Point] | None:
    """(u, side) of a switch: u points from the bottom pin (source/emitter)
    to the top pin, side is where its default label goes; the gate is on the other
    side.  Upright and unmirrored: u = (0, 1), side = (1, 0).  None when the
    pins are not on one horizontal or vertical line."""
    # CircuitikZ's PMOS is source-up; electrical current still references d->s.
    names = ("s", "d") if el.kind == "pmos" else PIN_NAMES[el.kind]
    (tx, ty), (bx, by) = el.pins[names[0]], el.pins[names[1]]
    if abs(tx - bx) > EPS and abs(ty - by) > EPS or (abs(tx - bx) < EPS and abs(ty - by) < EPS):
        return None
    u = (0.0, 1.0 if ty > by else -1.0) if abs(tx - bx) < EPS else (1.0 if tx > bx else -1.0, 0.0)
    side = (u[1], -u[0])
    if el.opts.get("mirror"):
        side = (-side[0], -side[1])
    return u, side


def _switch(el: Element, s: Scale, prof: Profile) -> Footprint:
    names = PIN_NAMES[el.kind]
    top, bot = scaled(el.pins[names[0]], s), scaled(el.pins[names[1]], s)
    fp = Footprint(leads=[(top, bot)])
    cx, cy = (top[0] + bot[0]) / 2, (top[1] + bot[1]) / 2
    fp.centre = (cx, cy)
    length, width = axis_sizes(el)
    kl, kw = sw_k(prof) * length, sw_k(prof) * width
    frame = switch_frame(el) or ((0.0, 1.0), (1.0, 0.0))
    u, side = frame
    fp.extra.update(u=u, side=side)
    # Keep the physical diode's envelope for every MOS style: a conducting
    # diode is visible in a mode, with no scale or label jump when switching views.
    # local box: SW_RIGHT towards the label side, SW_LEFT towards the gate
    xs = [cx + side[0] * a + u[0] * b for a in (-SW_LEFT * kw, SW_RIGHT * kw)
          for b in (-SW_HALF_H * kl, SW_HALF_H * kl)]
    ys = [cy + side[1] * a + u[1] * b for a in (-SW_LEFT * kw, SW_RIGHT * kw)
          for b in (-SW_HALF_H * kl, SW_HALF_H * kl)]
    fp.bodies.append(Box(min(xs), min(ys), max(xs), max(ys)))
    if el.label:
        w, h = text_size(el.label, font_of(el.opts.get("label_size"), prof))
        d = SW_LABEL_DX * kw + abs(side[0]) * w / 2 + abs(side[1]) * h / 2
        lx, ly = cx + side[0] * d, cy + side[1] * d
        # Explicit positions are page directions. Along the pin axis, retain
        # the sideways offset so text does not sit on its own drain/source
        # lead. This is a fixed symbol anchor, not a search for empty space.
        default = {(1, 0): "right", (-1, 0): "left", (0, 1): "above", (0, -1): "below"}[side]
        body = fp.bodies[0]
        pos = el.label_pos
        if pos and pos != default:
            if pos == "above":
                lx, ly = (lx if u[1] else cx), body.y1 + LABEL_GAP + h / 2
            elif pos == "below":
                lx, ly = (lx if u[1] else cx), body.y0 - LABEL_GAP - h / 2
            elif pos == "left":
                lx, ly = body.x0 - LABEL_GAP - w / 2, (ly if u[0] else cy)
            elif pos == "right":
                lx, ly = body.x1 + LABEL_GAP + w / 2, (ly if u[0] else cy)
        fp.labels.append(centred(lx, ly, w, h))
        fp.extra["label_at"] = (lx, ly)
    return fp


PORT_R = 0.06      # cm, radius of a terminal's open circle (CircuitikZ ocirc)


def _port(el: Element, s: Scale, prof: Profile) -> Footprint:
    """A terminal: no body to route around, only its label beside the circle
    (left unless label_pos says otherwise)."""
    fp = Footprint(leads=[])
    if el.label:
        x, y = scaled(el.pins["p"], s)
        w, h = text_size(el.label, font_of(el.opts.get("label_size"), prof))
        d = PORT_R + LABEL_GAP
        cx, cy = {"left": (x - d - w / 2, y), "right": (x + d + w / 2, y),
                  "above": (x, y + d + h / 2), "below": (x, y - d - h / 2)}[el.label_pos or "left"]
        fp.labels.append(centred(cx, cy, w, h))
    return fp


def pmsm_frame(el: Element, s: Scale):
    """Phase a to c fixes the cardinal axis; the motor is to its local right."""
    points = {k: scaled(p, s) for k, p in el.pins.items()}
    a, c = points["a"], points["c"]
    dx, dy = a[0] - c[0], a[1] - c[1]
    u = ((1.0 if dx >= 0 else -1.0), 0.0) if abs(dx) > abs(dy) else (0.0, 1.0 if dy >= 0 else -1.0)
    return u, (u[1], -u[0]), points


def _pmsm(el: Element, s: Scale, prof: Profile) -> Footprint:
    if any(k not in el.pins for k in PIN_NAMES["pmsm"]):
        return Footprint()  # Fit still runs before schema validation reports missing-pin.
    u, v, points = pmsm_frame(el, s)
    length, width = axis_sizes(el)
    b = points["b"]
    offsets = {k: (p[0]-b[0])*u[0] + (p[1]-b[1])*u[1] for k, p in points.items()}
    radius = max(PMSM_RADIUS * width, max(abs(d) for d in offsets.values()) + PMSM_EDGE)
    centre = tuple(b[i] + v[i] * (radius + PMSM_LEAD * length) for i in (0, 1))
    leads = []
    for k in PIN_NAMES["pmsm"]:
        d = offsets[k]
        reach = math.sqrt(max(0, radius*radius-d*d))
        end = tuple(centre[i] + u[i]*d - v[i]*reach for i in (0, 1))
        leads.append((points[k], end))
    body = centred(*centre, 2*radius, 2*radius)
    fp = Footprint(leads=leads, bodies=[body], centre=centre,
                   extra={"radius": radius, "u": u, "side": v,
                          "motor_marks": [("M", (centre[0], centre[1]+radius*.23), radius*.7),
                                          (r"$3\sim$", (centre[0], centre[1]-radius*.4), radius*.38)]})
    if el.label:
        w, h = text_size(el.label, font_of(el.opts.get("label_size"), prof))
        at = {"above": (centre[0], body.y1+LABEL_GAP+h/2),
              "below": (centre[0], body.y0-LABEL_GAP-h/2),
              "left": (body.x0-LABEL_GAP-w/2, centre[1]),
              "right": (body.x1+LABEL_GAP+w/2, centre[1])}[el.label_pos or ("right" if u[0] else "below")]
        fp.labels.append(centred(*at, w, h))
        fp.extra["label_at"] = at
    return fp


def transformer_frame(el: Element, s: Scale):
    """Cardinal winding axis and perpendicular axis, inferred from JSON pins.

    Project even malformed windings so validation can report them normally.
    No separate rotation flag or generated coordinate table is stored.
    """
    points = {k: scaled(p, s) for k, p in el.pins.items()}
    a, b = points["p1"], points["p2"]
    dx, dy = a[0] - b[0], a[1] - b[1]
    u = ((1.0 if dx >= 0 else -1.0), 0.0) if abs(dx) > abs(dy) else (0.0, 1.0 if dy >= 0 else -1.0)
    v = (u[1], -u[0])
    local = {k: (p[0] * v[0] + p[1] * v[1], p[0] * u[0] + p[1] * u[1])
             for k, p in points.items()}
    return u, v, local


def extra_windings(el: Element, P: dict) -> list[tuple[str, str, str, str]]:
    """(name, top pin, bottom pin, column) of each complete extra winding
    w3..w6 in a transformer's local frame; column is "p" when it shares the
    primary's axis, else "s".  The validator rejects a winding that lies on
    neither axis, so the column here is only a drawing hint."""
    out = []
    for name, a, b in windings(el)[2 if "sct" not in el.pins else 3:]:
        if a not in P or b not in P:
            continue                   # the validator reports the half winding
        top, bot = (a, b) if P[a][1] >= P[b][1] else (b, a)
        side = "p" if abs(P[a][0] - P["p1"][0]) <= abs(P[a][0] - P["s1"][0]) else "s"
        out.append((name, top, bot, side))
    return out


def _transformer(el: Element, s: Scale, prof: Profile) -> Footprint:
    """Windings are drawn on their pins, so a vertical wire leaving a pin
    never jogs; the validator keeps the primary-secondary gap within
    WINDING_GAP so the core does not drift away from the coils.  Extra
    windings w3..w6 stand on the primary or the secondary axis and share
    the one core, which spans every winding."""
    u, v, P = transformer_frame(el, s)
    xcp, xcs = P["p1"][0], P["s1"][0]
    xm = (xcp + xcs) / 2
    sec_names = ["s1"] + (["sct"] if "sct" in P else []) + ["s2"]
    pri = [P["p1"], P["p2"]]
    sec = [P[k] for k in sec_names]
    extras = extra_windings(el, P)
    coils = [(pri[0], pri[1])] + list(zip(sec, sec[1:])) + [(P[t], P[b]) for _, t, b, _ in extras]
    # which side of the core each coil faces, in coil order (editor and emitter)
    sides = ["p"] + ["s"] * (len(sec) - 1) + [side for *_, side in extras]
    fp = Footprint(leads=list(coils))
    length, width = axis_sizes(el)
    L = prof.bipole_len * BODY_FRAC["L"] * length
    # A newly added coil leaves two minimum leads between adjacent windings.
    fp.extra["winding_gap"] = 2 * MIN_LEAD * length
    for a, b in coils:
        fp.bodies.append(centred(a[0], (a[1] + b[1]) / 2, 2 * COIL_HALF_W * width, L))
    ys = [p[1] for p in P.values()]
    y0, y1 = min(ys), max(ys)
    core_gap = CORE_GAP * width
    core = Box(xm - core_gap - 0.02, y0, xm + core_gap + 0.02, y1)
    fp.bodies.append(core)
    # every point in extra is turned back to world coordinates below
    fp.extra.update(coils=coils, pri=pri, sec=sec, coil_sides=sides,
                    extra_coils=[(name, P[t], P[b], side) for name, t, b, side in extras])
    # core: false draws a coupled inductor, two windings without core lines;
    # the core box stays so nothing is routed between the windings
    fp.extra["core_lines"] = ([((xm + dx, y0), (xm + dx, y1)) for dx in (-core_gap, core_gap)]
                              if el.opts.get("core", True) is not False else [])
    # polarity dots: "same" puts both at the top of their winding, "opposite"
    # (flyback) puts the secondary one at the bottom of its last coil; an
    # extra winding's dot is always at its wka end, on the outer side
    dots = el.opts.get("dots", "same")
    if dots is True:
        dots = "same"
    if dots:
        pc = (pri[0][1] + pri[1][1]) / 2
        if dots == "opposite":
            sy = (sec[-2][1] + sec[-1][1]) / 2 - DOT_LIFT * length
        else:
            sy = (sec[0][1] + sec[1][1]) / 2 + DOT_LIFT * length
        fp.extra["dots"] = [(xcp - DOT_OFFSET * width, pc + DOT_LIFT * length),
                            (xcs + DOT_OFFSET * width, sy)]
        for name, t, b, side in extras:
            mid = (P[t][1] + P[b][1]) / 2
            lift = DOT_LIFT * length if t == f"{name}a" else -DOT_LIFT * length
            x = P[t][0] + (-DOT_OFFSET if side == "p" else DOT_OFFSET) * width
            fp.extra["dots"].append((x, mid + lift))
        fp.labels += [centred(x, y, 0.09, 0.09) for x, y in fp.extra["dots"]]
    ratio = el.opts.get("ratio")
    if ratio:
        w, h = text_size(ratio, font_of(el.opts.get("label_size"), prof))
        offset = (h if u[0] == 0 else w) / 2
        fp.labels.append(centred(xm, y1 + 0.08 + offset, w, h))
        fp.extra["ratio_at"] = (xm, y1 + 0.08 + offset)
    def world(p):
        return (p[0] * v[0] + p[1] * u[0], p[0] * v[1] + p[1] * u[1])

    fp.leads = [(world(a), world(b)) for a, b in fp.leads]
    bodies = []
    for b in fp.bodies:
        corners = [world((x, y)) for x in (b.x0, b.x1) for y in (b.y0, b.y1)]
        bodies.append(Box(min(p[0] for p in corners), min(p[1] for p in corners),
                          max(p[0] for p in corners), max(p[1] for p in corners)))
    fp.bodies = bodies
    # Labels move with the transformer but remain upright and keep their size.
    fp.labels = [centred(*world(((b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2)),
                        b.x1 - b.x0, b.y1 - b.y0) for b in fp.labels]
    for key in ("pri", "sec", "dots"):
        if key in fp.extra:
            fp.extra[key] = [world(p) for p in fp.extra[key]]
    for key in ("coils", "core_lines"):
        fp.extra[key] = [(world(a), world(b)) for a, b in fp.extra[key]]
    fp.extra["extra_coils"] = [(n, world(a), world(b), side) for n, a, b, side in fp.extra["extra_coils"]]
    if "ratio_at" in fp.extra:
        fp.extra["ratio_at"] = world(fp.extra["ratio_at"])
    if el.label:
        w, h = text_size(el.label, font_of(el.opts.get("label_size"), prof))
        boxes = fp.bodies + fp.labels
        x0, x1 = min(b.x0 for b in boxes), max(b.x1 for b in boxes)
        y0, y1 = min(b.y0 for b in boxes), max(b.y1 for b in boxes)
        # The name defaults below the windings, clear of the existing ratio.
        at = {"above": ((x0+x1)/2, y1 + LABEL_GAP + h/2),
              "below": ((x0+x1)/2, y0 - LABEL_GAP - h/2),
              "left": (x0 - LABEL_GAP - w/2, (y0+y1)/2),
              "right": (x1 + LABEL_GAP + w/2, (y0+y1)/2)}[el.label_pos or "below"]
        fp.extra["label_at"] = at
        fp.labels.insert(len(fp.labels) - bool(ratio), centred(*at, w, h))
    return fp


ARROW_GAP = 0.1   # cm between a current arrow and its label


def module_ellipsis(first, last, scale):
    """Text and midpoint of the clear gap between two module frames (layout units)."""
    vertical = max(first[1] - last[3], last[1] - first[3]) * scale.y
    horizontal = max(first[0] - last[2], last[0] - first[2]) * scale.x
    if max(vertical, horizontal) <= 0:
        raise ValueError("省略显示的首尾外框之间需要留出间隔")
    if vertical >= horizontal:
        low, high = sorted((first, last), key=lambda f: f[1])
        at = [(first[0] + first[2] + last[0] + last[2]) / 4, (low[3] + high[1]) / 2]
        return at, r"$\vdots$"
    left, right = sorted((first, last), key=lambda f: f[0])
    return [(left[2] + right[0]) / 2, (first[1] + first[3] + last[1] + last[3]) / 4], r"$\cdots$"


def annotation_box(ann, s: Scale, prof: Profile) -> Box:
    w, h = text_size(ann.text, font_of(ann.size, prof))
    x, y = scaled(ann.at, s)
    if ann.arrow:
        d = ARROW_GAP
        x, y = {"above": (x, y + d + h / 2), "below": (x, y - d - h / 2),
                "left": (x - d - w / 2, y), "right": (x + d + w / 2, y)}[ann.side]
    return centred(x, y, w, h)


# ---------------------------------------------------------------------------
# regions and callouts (see regions.py)
# ---------------------------------------------------------------------------
REGION_LABEL_GAP = 0.08   # cm between a region's edge and its label
REGION_FIT_PAD = 0.3      # layout units: breathing room around member symbols/text
REGION_FIT_GRID = 0.05    # layout units: round outward, never clip a member


def region_fit_box(layout, members, s: Scale, prof: Profile) -> Box:
    """Member symbols and labels plus padding, in layout units; never mutate source."""
    from .regions import RegionError
    if not isinstance(members, list) or not members or not all(isinstance(m, str) for m in members):
        raise RegionError('fit="members" requires a non-empty members list of element ids')
    elements = {e.id: e for e in layout.elements}
    boxes = []
    for name in members:
        if name not in elements:
            raise RegionError(f"member {name} is not an element of the table")
        try:
            fp = footprint(elements[name], s, prof)
        except (KeyError, ValueError) as exc:
            raise RegionError(f"member {name}: cannot measure symbol ({exc})") from None
        boxes.extend(fp.bodies + fp.labels)
        if "source_wave" in fp.extra:
            boxes.append(fp.extra["source_wave"]["box"])
    if not boxes:
        raise RegionError("members have no visible symbols or labels to fit")
    def snap(v, upper=False):
        # Floating point noise at an exact grid edge must not add a whole cell.
        n = math.ceil(v / REGION_FIT_GRID - 1e-9) if upper else math.floor(v / REGION_FIT_GRID + 1e-9)
        return round(n * REGION_FIT_GRID, 10)
    return Box(snap(min(b.x0 for b in boxes) / s.x - REGION_FIT_PAD),
               snap(min(b.y0 for b in boxes) / s.y - REGION_FIT_PAD),
               snap(max(b.x1 for b in boxes) / s.x + REGION_FIT_PAD, True),
               snap(max(b.y1 for b in boxes) / s.y + REGION_FIT_PAD, True))
REGION_INSET = 0.06       # cm from the top edge to an "inside" label


def region_rect(r, s: Scale, k: int = 0) -> Box:
    """Front rectangle (k = 0) or the k-th panel behind it, in cm."""
    x0, y0, x1, y1 = r.rect(k)
    return Box(x0 * s.x, y0 * s.y, x1 * s.x, y1 * s.y)


def region_label_box(r, s: Scale, prof: Profile) -> Box | None:
    if not r.label:
        return None
    w, h = text_size(r.label, font_of(r.size, prof))
    b, g = region_rect(r, s), REGION_LABEL_GAP
    cx, cy = (b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2
    x, y = {"below": (cx, b.y0 - g - h / 2), "above": (cx, b.y1 + g + h / 2),
            "left": (b.x0 - g - w / 2, cy), "right": (b.x1 + g + w / 2, cy),
            "inside": (cx, b.y1 - REGION_INSET - h / 2)}[r.label_pos]
    if r.label_pos == "above" and r.layers:
        # clear the panels stacked above the front one
        y = max(region_rect(r, s, k).y1 for k in range(len(r.layers) + 1)) + g + h / 2
    return centred(x, y, w, h)


def block_rect(b, s: Scale) -> Box:
    """A function block's box in printed cm."""
    return Box(b.x0 * s.x, b.y0 * s.y, b.x1 * s.x, b.y1 * s.y)


def control_view_origin(b, s: Scale, content: Box) -> Point:
    """Translation in printed cm; the referenced table's coordinates stay intact."""
    frame = block_rect(b, s)
    return ((frame.x0 + frame.x1 - content.x0 - content.x1) / 2,
            (frame.y0 + frame.y1 - content.y0 - content.y1) / 2)


def block_text_box(b, s: Scale, prof: Profile) -> Box | None:
    """The block's text, centred in the box (cm), or under it when the
    block carries an icon."""
    if not b.text:
        return None
    w, h = text_size(b.text, font_of(b.size, prof))
    r = block_rect(b, s)
    if getattr(b, "icon", None):
        from .blocks import CAPTION_GAP      # an icon fills the box: caption underneath
        return centred((r.x0 + r.x1) / 2, r.y0 - CAPTION_GAP - h / 2, w, h)
    return centred((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2, w, h)


def callout_shape(c, s: Scale) -> list[Point]:
    """Outline of a block callout in cm (shaft then head, tip at ``end``);
    for a line callout just its two ends."""
    from .regions import BLOCK_HEAD, BLOCK_WIDTH
    a, b = scaled(c.start, s), scaled(c.end, s)
    if c.style == "wave":
        out = [a]
        for curve in wave_curves(c, s):
            # Subdivide each exact cubic for collision checks, not for drawing.
            for i in range(1, 9):
                t = i / 8
                weights = ((1-t)**3, 3*(1-t)**2*t, 3*(1-t)*t*t, t**3)
                out.append(tuple(sum(w*p[j] for w, p in zip(weights, curve)) for j in (0, 1)))
        return out
    if c.style != "block":
        if c.bend:
            curve = callout_curve(c, s)
            return [_bezier_at(curve, i/BEND_SAMPLES) for i in range(BEND_SAMPLES+1)]
        if c.corner == "round" and c.via:
            return [a, *(_bezier_at(curve, i/BEND_SAMPLES)
                         for curve in callout_paths(c, s) for i in range(1, BEND_SAMPLES+1))]
        return [a, *(scaled(p, s) for p in c.via), b]
    L = math.dist(a, b)
    u = ((b[0] - a[0]) / L, (b[1] - a[1]) / L)
    n = (-u[1], u[0])
    hl, hw = min(BLOCK_HEAD[0], 0.6 * L), BLOCK_HEAD[1] / 2
    sw = BLOCK_WIDTH / 2
    base = (b[0] - u[0] * hl, b[1] - u[1] * hl)

    def off(p: Point, d: float) -> Point:
        return (p[0] + n[0] * d, p[1] + n[1] * d)
    return [off(a, sw), off(base, sw), off(base, hw), b, off(base, -hw),
            off(base, -sw), off(a, -sw)]


BEND_SAMPLES = 16      # points along a bent callout, for checks and the editor


def callout_curve(c, s: Scale) -> tuple[Point, Point, Point, Point]:
    """A bent line callout as one cubic Bezier in cm (start, two control
    points, tip): the circular arc that leaves the chord by ``bend`` degrees."""
    a, b = scaled(c.start, s), scaled(c.end, s)
    th = math.radians(c.bend)
    chord = math.atan2(b[1] - a[1], b[0] - a[0])
    # Equivalent to 4/3 * tan(|bend|/2) * radius, but also stable at 0:
    # the straight arrow is a cubic with its controls at one and two thirds.
    k = 2 * math.dist(a, b) / (3 * (1 + math.cos(th)))
    ua = (math.cos(chord + th), math.sin(chord + th))
    ub = (math.cos(chord - th), math.sin(chord - th))
    return (a, (a[0] + k * ua[0], a[1] + k * ua[1]),
            (b[0] - k * ub[0], b[1] - k * ub[1]), b)


def callout_handle(c, s: Scale) -> Point:
    """Midpoint of the cubic, used as the editor's bend handle (cm)."""
    curve = callout_curve(c, s)
    return tuple(sum(w * p[j] for w, p in zip((1, 3, 3, 1), curve)) / 8
                 for j in (0, 1))


def callout_bend_at(c, s: Scale, point: Point) -> float:
    """Bend whose sagitta reaches a user's handle, projected onto the normal.

    Work in printed cm so diagonal arrows and unequal layout scales agree
    with both the emitted curve and its visible handle. No coordinates move.
    """
    from .regions import MAX_BEND
    a, b, p = scaled(c.start, s), scaled(c.end, s), scaled(point, s)
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    height = ((p[0] - (a[0] + b[0]) / 2) * -dy
              + (p[1] - (a[1] + b[1]) / 2) * dx) / length
    angle = math.degrees(2 * math.atan2(2 * height, length))
    return round(max(-MAX_BEND, min(MAX_BEND, angle)), 2)


def callout_head(c, s: Scale) -> list[Point]:
    """Line-arrow tip aligned to the exact cubic tangent, in printed cm."""
    from .regions import LINE_HEAD
    _, _, control, tip = callout_curve(c, s)
    length = math.dist(control, tip)
    ux, uy = (tip[0] - control[0]) / length, (tip[1] - control[1]) / length
    # Same narrow pointer proportions as the editor's existing line arrow.
    half_width = LINE_HEAD * 0.45
    base = (tip[0] - ux * LINE_HEAD, tip[1] - uy * LINE_HEAD)
    return [tip, (base[0] - uy * half_width, base[1] + ux * half_width),
            (base[0] + uy * half_width, base[1] - ux * half_width)]


def wave_curves(c, s: Scale) -> list[tuple[Point, Point, Point, Point]]:
    """A chain of tangent-continuous cubics for a wavy annotation arrow.

    The envelope goes to zero with zero slope at each end, leaving the tip
    aimed along the chord. Quarter-period resolution is doubled to keep the
    sinusoid smooth; both SVG and TeX draw these very same cubic segments.
    """
    a, b = scaled(c.start, s), scaled(c.end, s)
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    nx, ny = -dy / length, dx / length
    omega = 2 * math.pi * c.wave_cycles
    count = 8 * c.wave_cycles

    def at(t):
        env = 16 * t*t * (1-t)**2
        denv = 32 * t * (1-t) * (1-2*t)
        offset = c.wave_amplitude * env * math.sin(omega*t)
        slope = c.wave_amplitude * (denv * math.sin(omega*t) + env * omega * math.cos(omega*t))
        return ((a[0] + dx*t + nx*offset, a[1] + dy*t + ny*offset),
                (dx + nx*slope, dy + ny*slope))

    curves = []
    for i in range(count):
        p, dp = at(i / count)
        q, dq = at((i+1) / count)
        curves.append((p, (p[0] + dp[0]/(3*count), p[1] + dp[1]/(3*count)),
                       (q[0] - dq[0]/(3*count), q[1] - dq[1]/(3*count)), q))
    return curves


def wave_head(c, s: Scale) -> list[Point]:
    from .regions import LINE_HEAD
    a, b = scaled(c.via[-1] if c.via else c.start, s), scaled(c.end, s)
    length = math.dist(a, b)
    ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
    base = (b[0] - LINE_HEAD*ux, b[1] - LINE_HEAD*uy)
    half = LINE_HEAD * 0.45
    return [b, (base[0] - uy*half, base[1] + ux*half),
            (base[0] + uy*half, base[1] - ux*half)]


def callout_box(c, s: Scale) -> Box:
    pts = callout_shape(c, s)
    return Box(min(p[0] for p in pts), min(p[1] for p in pts),
               max(p[0] for p in pts), max(p[1] for p in pts))


def callout_label_box(c, s: Scale, prof: Profile) -> Box | None:
    from .regions import CALLOUT_LABEL_GAP
    if not c.label:
        return None
    w, h = text_size(c.label, font_of(c.size, prof))
    if c.label_pos == "inline":
        pts = callout_shape(c, s)
        lengths = [math.dist(p, q) for p, q in zip(pts, pts[1:])]
        remaining = sum(lengths) * c.label_at
        for p, q, length in zip(pts, pts[1:], lengths):
            if remaining <= length:
                t = remaining / length
                return centred(p[0] + (q[0] - p[0])*t, p[1] + (q[1] - p[1])*t, w, h)
            remaining -= length
    b, g = callout_box(c, s), CALLOUT_LABEL_GAP
    cx, cy = (b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2
    x, y = {"above": (cx, b.y1 + g + h / 2), "below": (cx, b.y0 - g - h / 2),
            "left": (b.x0 - g - w / 2, cy), "right": (b.x1 + g + w / 2, cy)}[c.label_pos]
    return centred(x, y, w, h)


CALLOUT_TEXT_GAP = 0.04     # printed cm of clear space on each side of an inline label


def callout_paths(c, s: Scale) -> list[tuple[Point, Point, Point, Point]]:
    """Exact cubics shared by the vector editor and the exporter."""
    if c.style == "wave":
        return wave_curves(c, s)
    if c.bend:
        return [callout_curve(c, s)]
    pts = [scaled(c.start, s), *(scaled(p, s) for p in c.via), scaled(c.end, s)]

    def straight(a, b):
        return (a, ((2*a[0]+b[0])/3, (2*a[1]+b[1])/3),
                ((a[0]+2*b[0])/3, (a[1]+2*b[1])/3), b)

    if c.corner != "round":
        return [straight(a, b) for a, b in zip(pts, pts[1:])]
    from .regions import CALLOUT_CORNER_CUT
    paths = []
    start = pts[0]
    for a, vertex, b in zip(pts, pts[1:], pts[2:]):
        before, after = math.dist(a, vertex), math.dist(vertex, b)
        u = ((vertex[0]-a[0])/before, (vertex[1]-a[1])/before)
        v = ((b[0]-vertex[0])/after, (b[1]-vertex[1])/after)
        if abs(u[0]*v[1] - u[1]*v[0]) < 1e-9:
            paths.append(straight(start, vertex))
            start = vertex
            continue
        # Half-length caps keep neighbouring rounds from overlapping on short legs.
        cut = min(CALLOUT_CORNER_CUT, before/2, after/2)
        entry = (vertex[0]-cut*u[0], vertex[1]-cut*u[1])
        exit = (vertex[0]+cut*v[0], vertex[1]+cut*v[1])
        if math.dist(start, entry) > 1e-9:
            paths.append(straight(start, entry))
        # Quadratic corner expressed as a cubic, tangent to both adjoining legs.
        controls = [tuple((p[j]+2*vertex[j])/3 for j in (0, 1)) for p in (entry, exit)]
        paths.append((entry, *controls, exit))
        start = exit
    paths.append(straight(start, pts[-1]))
    return paths


def callout_gap(c, s: Scale, prof: Profile) -> Box | None:
    box = callout_label_box(c, s, prof) if c.label_pos == "inline" else None
    if box is None:
        return None
    return Box(box.x0-CALLOUT_TEXT_GAP, box.y0-CALLOUT_TEXT_GAP,
               box.x1+CALLOUT_TEXT_GAP, box.y1+CALLOUT_TEXT_GAP)


def callout_visible_paths(c, s: Scale, prof: Profile):
    """Split the shaft around the label and the isolation mark; never cover
    them with a white rectangle."""
    curves = callout_paths(c, s)
    for gap in (callout_gap(c, s, prof), isolation_gap(c, s)):
        if gap is not None:
            curves = [piece for curve in curves for piece in _outside_box(curve, gap)]
    return curves


def _along(pts: list[Point], t: float) -> tuple[Point, Point]:
    """Point at fraction ``t`` of a polyline's length and the unit direction there."""
    lengths = [math.dist(p, q) for p, q in zip(pts, pts[1:])]
    remaining = sum(lengths) * t
    for p, q, length in zip(pts, pts[1:], lengths):
        if remaining <= length or q is pts[-1]:
            k = min(remaining / length, 1.0)
            u = ((q[0] - p[0]) / length, (q[1] - p[1]) / length)
            return (p[0] + (q[0] - p[0]) * k, p[1] + (q[1] - p[1]) * k), u
        remaining -= length
    raise ValueError("empty path")


def isolation_bars(c, s: Scale) -> list[tuple[Point, Point]]:
    """The two bars of a callout's isolation mark, across the line (cm)."""
    from .regions import ISOLATION_BAR, ISOLATION_GAP
    if c.isolation is None:
        return []
    (x, y), (ux, uy) = _along(callout_shape(c, s), c.isolation)
    nx, ny, h = -uy, ux, ISOLATION_BAR / 2
    out = []
    for d in (-ISOLATION_GAP / 2, ISOLATION_GAP / 2):
        cx, cy = x + ux * d, y + uy * d
        out.append(((cx - nx * h, cy - ny * h), (cx + nx * h, cy + ny * h)))
    return out


def isolation_gap(c, s: Scale) -> Box | None:
    """Where the line is cut between the isolation bars (cm)."""
    from .regions import ISOLATION_GAP
    if c.isolation is None:
        return None
    (x, y), _ = _along(callout_shape(c, s), c.isolation)
    return centred(x, y, ISOLATION_GAP, ISOLATION_GAP)


def callout_heads(c, s: Scale) -> list[list[Point]]:
    """Arrow-head triangles of a line or wave callout, one per drawn end (cm)."""
    from .regions import LINE_HEAD
    if c.style == "block":
        return []
    if c.bend:
        curve = callout_curve(c, s)
        ends = {"end": (curve[3], curve[2]), "start": (curve[0], curve[1])}
    else:
        pts = [scaled(c.start, s), *(scaled(p, s) for p in c.via), scaled(c.end, s)]
        ends = {"end": (pts[-1], pts[-2]), "start": (pts[0], pts[1])}
    which = {"end": ("end",), "start": ("start",), "both": ("start", "end"), "none": ()}[c.head]
    out = []
    for key in which:
        tip, back = ends[key]
        length = math.dist(tip, back)
        ux, uy = (tip[0] - back[0]) / length, (tip[1] - back[1]) / length
        base = (tip[0] - ux * LINE_HEAD, tip[1] - uy * LINE_HEAD)
        half = LINE_HEAD * 0.45      # same narrow pointer as the end head
        out.append([tip, (base[0] - uy * half, base[1] + ux * half),
                    (base[0] + uy * half, base[1] - ux * half)])
    return out


def _bezier_at(curve, t):
    weights = ((1-t)**3, 3*(1-t)**2*t, 3*(1-t)*t*t, t**3)
    return tuple(sum(w*p[j] for w, p in zip(weights, curve)) for j in (0, 1))


def _split_curve(curve, t):
    levels = [list(curve)]
    while len(levels[-1]) > 1:
        levels.append([((1-t)*a[0]+t*b[0], (1-t)*a[1]+t*b[1])
                       for a, b in zip(levels[-1], levels[-1][1:])])
    return tuple(level[0] for level in levels), tuple(level[-1] for level in reversed(levels))


def _outside_box(curve, box):
    # Coordinate cubics are monotone between their derivative's real roots.
    # Bracket each box-edge crossing there, then keep only exterior intervals.
    cuts = [0.0, 1.0]
    for axis, bounds in ((0, (box.x0, box.x1)), (1, (box.y0, box.y1))):
        p0, p1, p2, p3 = (p[axis] for p in curve)
        a, b, cc = -p0+3*p1-3*p2+p3, 3*p0-6*p1+3*p2, -3*p0+3*p1
        critical = [0.0, 1.0]
        if abs(a) > 1e-12:
            disc = 4*b*b - 12*a*cc
            if disc >= 0:
                critical += [t for t in ((-2*b-math.sqrt(disc))/(6*a),
                                         (-2*b+math.sqrt(disc))/(6*a)) if 0 < t < 1]
        elif abs(b) > 1e-12 and 0 < -cc/(2*b) < 1:
            critical.append(-cc/(2*b))
        critical.sort()
        for bound in bounds:
            def f(t):
                return ((a*t+b)*t+cc)*t+p0-bound
            for lo, hi in zip(critical, critical[1:]):
                if abs(f(lo)) < 1e-10:
                    cuts.append(lo)
                if f(lo)*f(hi) >= 0:
                    continue
                sign = f(lo)
                for _ in range(45):
                    mid = (lo+hi)/2
                    if sign*f(mid) > 0:
                        lo = mid
                    else:
                        hi = mid
                cuts.append((lo+hi)/2)
    cuts = sorted(set(round(t, 12) for t in cuts))
    intervals = []
    for lo, hi in zip(cuts, cuts[1:]):
        if hi-lo < 1e-10:
            continue
        x, y = _bezier_at(curve, (lo+hi)/2)
        if box.x0 <= x <= box.x1 and box.y0 <= y <= box.y1:
            continue
        if intervals and abs(intervals[-1][1] - lo) < 1e-10:
            intervals[-1] = (intervals[-1][0], hi)
        else:
            intervals.append((lo, hi))
    out = []
    for lo, hi in intervals:
        left, _ = _split_curve(curve, hi)
        _, piece = _split_curve(left, lo/hi)
        out.append(piece)
    return out


# Electrical captions are auxiliary text, not part of a component footprint.
# 6.5 pt distinguishes them from the default 8 pt component labels. The 0.08 cm
# gap exceeds the validator's 0.06 cm text clearance; search stays within 1.2 cm.
ELECTRICAL_FONT_PT = 6.5
ELECTRICAL_LABEL_GAP = 0.08
ELECTRICAL_LABEL_STEP = 0.10
ELECTRICAL_LABEL_RINGS = 12
POINT_TO_CM = 2.54 / 72.27


@dataclass(frozen=True)
class ElectricalLabel:
    kind: str
    ref: str
    text: str
    box: Box
    blocked: bool = False
    font_pt: float = ELECTRICAL_FONT_PT

    @property
    def centre(self):
        b = self.box
        return ((b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2)


def electrical_labels(layout, s: Scale, prof: Profile, section=None):
    """Place only derived captions, keeping parts/wires and saved JSON intact.

    The validator, editor and TeX emitter consume these same printed-cm boxes.
    All symbols, existing text, wires/leads, connection points and NoConn marks
    are obstacles. Labels are never added to footprint().bodies, so module
    symbol/body-overlap checks cannot confuse decoration with a component.
    """
    section = layout.extra.get("electrical") if section is None else section
    if not isinstance(section, dict) or not isinstance(section.get("nodes"), list) or not isinstance(section.get("branches"), dict):
        return []
    from .regions import safe_regions
    from .source_waves import symbols
    fps, elements, pins = {}, {}, {}
    obstacles, segments = [], []
    for e in layout.elements:
        try:
            fp = footprint(e, s, prof)
        except (ValueError, KeyError):
            continue  # ordinary validation supplies the schema error
        fps[e.id], elements[e.id] = fp, e
        pins.update({f"{e.id}.{k}": scaled(p, s) for k, p in e.pins.items()})
        obstacles += fp.bodies + fp.labels
        segments += fp.leads
        if "source_wave" in fp.extra:
            obstacles.append(fp.extra["source_wave"]["box"])
    points = list(pins.values())
    for wire in layout.wires:
        pts = [scaled(p, s) for p in wire]
        points += pts
        segments += list(zip(pts, pts[1:]))
    dot_radius = max(prof.dot_pt * POINT_TO_CM, NO_CONNECT_HALF_CM)
    obstacles += [centred(x, y, dot_radius * 2, dot_radius * 2) for x, y in points]
    obstacles += [annotation_box(a, s, prof) for a in layout.annotations]
    for symbol in symbols(layout):
        if symbol is not None:
            obstacles.append(wave_symbol(scaled(symbol["at"], s), symbol["wave"], symbol["scale"])[1])
    regions, callouts = safe_regions(layout, s, prof)
    for region in regions:
        if (b := region_label_box(region, s, prof)) is not None:
            obstacles.append(b)
        for index in range(len(region.layers) + 1):
            b = region_rect(region, s, index)
            corners = [(b.x0,b.y0),(b.x1,b.y0),(b.x1,b.y1),(b.x0,b.y1)]
            segments += list(zip(corners, corners[1:] + corners[:1]))
    for callout in callouts:
        obstacles.append(callout_box(callout, s))
        if (b := callout_label_box(callout, s, prof)) is not None:
            obstacles.append(b)
    requests = []
    for node in section["nodes"]:
        if not isinstance(node, dict) or not isinstance(node.get("pins"), list):
            continue
        anchors = [centred(*pins[p], dot_radius * 2, dot_radius * 2)
                   for p in node["pins"] if isinstance(p, str) and p in pins]
        if anchors and isinstance(node.get("id"), str) and isinstance(node.get("name", node["id"]), str):
            requests.append(("node", node["id"], node.get("name", node["id"]), anchors))
    for ref, number in section["branches"].items():
        if not isinstance(ref, str) or type(number) is not int:
            continue
        eid = ref if ref in elements else ref.rpartition(":")[0]
        if eid not in fps or not fps[eid].bodies:
            continue
        boxes = fps[eid].bodies
        anchor = Box(min(b.x0 for b in boxes), min(b.y0 for b in boxes),
                     max(b.x1 for b in boxes), max(b.y1 for b in boxes))
        requests.append(("branch", ref, f"B{number}", [anchor]))
    labels = []
    for kind, ref, text, anchors in requests:
        # Underscores in a network name are literal, never TeX subscripts.
        width, height = text_size(text.replace("_", "x"), ELECTRICAL_FONT_PT)
        first = chosen = None
        for ring in range(ELECTRICAL_LABEL_RINGS):
            gap = ELECTRICAL_LABEL_GAP + ring * ELECTRICAL_LABEL_STEP
            for anchor in anchors:
                cx, cy = (anchor.x0 + anchor.x1)/2, (anchor.y0 + anchor.y1)/2
                left, right = anchor.x0-gap-width/2, anchor.x1+gap+width/2
                bottom, top = anchor.y0-gap-height/2, anchor.y1+gap+height/2
                positions = ([(right,cy),(left,cy),(cx,bottom),(cx,top)] if kind == "branch"
                             else [(left,top),(right,top),(left,bottom),(right,bottom)])
                positions += [(left,top),(right,top),(left,bottom),(right,bottom),(cx,top),(cx,bottom)]
                for x, y in positions:
                    box = centred(x, y, width, height)
                    first = first or box
                    padded = Box(box.x0-ELECTRICAL_LABEL_GAP, box.y0-ELECTRICAL_LABEL_GAP,
                                 box.x1+ELECTRICAL_LABEL_GAP, box.y1+ELECTRICAL_LABEL_GAP)
                    if any(box.overlaps(b, ELECTRICAL_LABEL_GAP) for b in obstacles):
                        continue
                    if any(padded.hits_segment(a,b) for a,b in segments):
                        continue
                    chosen = box
                    break
                if chosen:
                    break
            if chosen:
                break
        box = chosen or first
        if box is not None:
            labels.append(ElectricalLabel(kind, ref, text, box, chosen is None))
            obstacles.append(box)
    return labels
