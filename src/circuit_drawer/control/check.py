"""Control diagram checker: resolves ports and links at the printed scale and
judges the table.  The emitter draws from the same ``Geometry``, so what the
checker passed is exactly what gets printed.

Small symbols (sum circles, switch contacts, arrowheads, signs) and all text
have absolute printed sizes, like circuit symbols; boxes, link corners and
regions are in layout units and scale with the figure.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from ..geometry import (_CMD, EPS, WIDE_GLYPH, Box, Scale, centred, font_of, is_wide,
                        region_label_box, region_rect, text_size)
from ..profiles import Profile
from ..regions import LINE_HEAD
from .model import (BOX_TYPES, CIRCLE_TYPES, POLE, SIDES, TAG_STYLE, Block, Diagram, End, Link,
                    parse_lenient)

SUM_R = 0.16           # cm: radius of a sum / multiply / divide circle
CONTACT_R = 0.05       # cm: a switch contact's open circle
SIGN_GAP = 0.1         # cm beyond the circle to the centre of a + / - sign
SIGN_LIFT = 0.16       # cm off the incoming line, so the sign clears the arrow
SIGN_BOX = 0.2         # cm: a + / - glyph's square, smaller than text_size reads it
TEXT_PAD = 0.08        # cm of clear space inside a box left and right of its text
TEXT_PAD_Y = 0.05      # cm above and below: the height estimate already includes subscripts
LABEL_GAP = 0.08       # cm between a box or a line and its label
THROW_LABEL_GAP = 0.2  # cm from a contact centre to its letter
TOGGLE_FRAC = 0.55     # toggle arc radius as a fraction of the nearest throw's distance
BLOCK_CLEARANCE = 0.1  # cm two blocks must keep apart
MAX_SCALE = 1.0        # cm per layout unit: never stretch beyond the drawn size
FIT_ROUNDS = 6         # scale refinements to absorb the fixed-size symbols and text
# the run into an arrowhead must show the head (LINE_HEAD) plus some line
MIN_ARROW_RUN = LINE_HEAD + 0.1
PT_CM = 2.54 / 72.27
LEAD = 1.2             # baseline skip / font size, as the preamble sets it
SIDE_DIR = {"w": (-1.0, 0.0), "e": (1.0, 0.0), "n": (0.0, 1.0), "s": (0.0, -1.0)}


# Glyph widths (cm at 8 pt Times) for words in boxes, measured with
# \settowidth: "Controller" 1.16, "Modulation" 1.31, "Pulse Width" 1.37,
# "LPF" 0.48.  The circuit estimator (geometry.text_size) is tuned for short
# math labels and reads words about 60 % too wide.
GLYPH_CM = {"upper": 0.18, "lower": 0.12, "digit": 0.14, "space": 0.07, "other": 0.12}


# Inline \frac sets numerator and denominator in script size (6 pt under
# 8 pt type) stacked around the bar.  Checked against the typeset ink in the
# figure preamble: $G_{c}(s)=K_{p}+\frac{K_{i}}{s}$ prints 1.84 x 0.38 cm and
# reads 1.90 x 0.50 here (the plain estimator read it 2.49 cm wide).
FRAC_SCALE = 0.75
FRAC_H = 0.5           # cm at 8 pt: a fraction is taller than a subscripted symbol
# commands that put a mark over or around their argument without widening it
_ACCENT = re.compile(r"\\(hat|bar|dot|ddot|tilde|vec|overline|sqrt|left|right)\b")


def _braced(s: str, i: int) -> tuple[str, int]:
    """The group starting at s[i] == "{" and the index after it."""
    depth = 0
    for j in range(i, len(s)):
        depth += {"{": 1, "}": -1}.get(s[j], 0)
        if depth == 0:
            return s[i + 1:j], j + 1
    return s[i + 1:], len(s)


def math_size(tex: str, pt: float) -> tuple[float, float]:
    """Width and height (cm) of one formula, without the $ signs: the circuit
    estimator, with fractions measured as the wider of their two parts."""
    w, h, rest, i = 0.0, 0.0, [], 0
    while (k := tex.find("\\frac", i)) >= 0 and tex[k + 5:k + 6] == "{":
        rest.append(tex[i:k])
        num, j = _braced(tex, k + 5)
        den, j = _braced(tex, j) if tex[j:j + 1] == "{" else ("x", j)
        fw = max(math_size(num, pt)[0], math_size(den, pt)[0]) * FRAC_SCALE
        w, h, i = w + fw, max(h, FRAC_H * pt / 8), j
    rest.append(tex[i:])
    # an escaped underscore is one glyph, not a subscript
    plain = _ACCENT.sub("", "".join(rest)).replace(chr(92) + "_", "x").replace("\\|", "|")
    if plain.strip():
        mw, mh = text_size(f"${plain}$", pt)
        w, h = w + mw, max(h, mh)
    return w, h


def line_size(tex: str, pt: float) -> tuple[float, float]:
    """Width and height (cm) of one line: math parts by ``math_size``,
    plain words by GLYPH_CM."""
    w, h = 0.0, text_size("x", pt)[1]
    for i, part in enumerate(tex.split("$")):
        if i % 2:
            mw, mh = math_size(part, pt)
            w, h = w + mw, max(h, mh)
            continue
        part = _CMD.sub(lambda m: m.group(2), part)
        part = re.sub(r"\\[a-zA-Z]+\s*", "", part).replace("{", "").replace("}", "")
        for c in part:
            if is_wide(c):
                w += WIDE_GLYPH * pt / 8
                continue
            kind = ("upper" if c.isupper() else "lower" if c.islower() else
                    "digit" if c.isdigit() else "space" if c == " " else "other")
            w += GLYPH_CM[kind] * pt / 8
    return w, h


def text_box_size(text: str, pt: float) -> tuple[float, float]:
    """Printed width and height (cm) of text with ``\\\\`` line breaks."""
    lines = [ln.strip() for ln in text.split(r"\\")]
    sizes = [line_size(ln, pt) for ln in lines]
    w = max(s[0] for s in sizes)
    return w, (len(lines) - 1) * pt * LEAD * PT_CM + max(s[1] for s in sizes)


def anchored(at: tuple[float, float], anchor: str, w: float, h: float) -> Box:
    """Box (cm) of a TikZ node of size w x h placed at ``at`` by ``anchor``."""
    dx = w / 2 if "west" in anchor else -w / 2 if "east" in anchor else 0.0
    dy = h / 2 if "south" in anchor else -h / 2 if "north" in anchor else 0.0
    return centred(at[0] + dx, at[1] + dy, w, h)


@dataclass
class Label:
    """A piece of printed text: where it sits (cm) and what it belongs to."""
    box: Box
    text: str
    pt: float
    owner: str
    color: str | None = None


@dataclass
class Geometry:
    s: float
    bodies: dict[str, Box] = field(default_factory=dict)          # cm, by block id
    paths: list[list[tuple[float, float]] | None] = field(default_factory=list)  # units
    dots: list[tuple[float, float]] = field(default_factory=list)  # units
    labels: list[Label] = field(default_factory=list)
    signs: list[tuple[tuple[float, float], str]] = field(default_factory=list)  # cm
    errors: list[str] = field(default_factory=list)

    @property
    def scale(self) -> Scale:
        return Scale(self.s, self.s)


@dataclass
class Report:
    diagram: Diagram
    geometry: Geometry
    errors: list[str]
    warnings: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "scale_cm_per_unit": round(self.geometry.s, 4),
                "errors": self.errors, "warnings": self.warnings}


# ---- ports -------------------------------------------------------------------
def port_point(b: Block, port: str, offset: float, s: float) -> tuple[float, float]:
    """A port's position in layout units, or ControlError-style ValueError."""
    x, y = b.at
    if b.type == "switch":
        if offset:
            raise ValueError(f"switch ports take no offset ({b.id}.{port})")
        if port == POLE:
            return b.at
        if port in b.throws:
            return b.throws[port]
        raise ValueError(f"{b.id} has ports {[POLE] + list(b.throws)}, not {port!r}")
    if port not in SIDES:
        raise ValueError(f"{b.id} has ports {list(SIDES)}, not {port!r}")
    dx, dy = SIDE_DIR[port]
    if b.type in CIRCLE_TYPES:
        if offset:
            raise ValueError(f"{b.id}.{port}: a {b.type} port takes no offset")
        r = SUM_R / s
        return (x + dx * r, y + dy * r)
    w, h = b.size
    half = (h if dx else w) / 2
    if abs(offset) >= half - EPS:
        raise ValueError(f"{b.id}.{port}{offset:+g}: offset must stay inside the side "
                         f"(|offset| < {half:g})")
    if dx:
        return (x + dx * w / 2, y + offset)
    return (x + offset, y + dy * h / 2)


def on_boundary(b: Block, p: tuple[float, float], s: float) -> str | None:
    """The port name a bare point sits on, if it touches this block."""
    if b.type == "switch":
        for name, q in [(POLE, b.at), *b.throws.items()]:
            if math.dist(p, q) < EPS:
                return name
        return None
    if b.type in CIRCLE_TYPES:
        for side in SIDES:
            if math.dist(p, port_point(b, side, 0, s)) < EPS:
                return side
        return None
    x0, y0, x1, y1 = b.rect()
    if y0 - EPS <= p[1] <= y1 + EPS:
        if abs(p[0] - x0) < EPS:
            return "w"
        if abs(p[0] - x1) < EPS:
            return "e"
    if x0 - EPS <= p[0] <= x1 + EPS:
        if abs(p[1] - y0) < EPS:
            return "s"
        if abs(p[1] - y1) < EPS:
            return "n"
    return None


def _on_path(p, path) -> bool:
    for a, b in zip(path, path[1:]):
        if (min(a[0], b[0]) - EPS <= p[0] <= max(a[0], b[0]) + EPS
                and min(a[1], b[1]) - EPS <= p[1] <= max(a[1], b[1]) + EPS
                and (abs(a[0] - b[0]) < EPS or abs(a[1] - b[1]) < EPS)):
            return True
    return False


def point_along(path, t: float) -> tuple[tuple[float, float], tuple[float, float]]:
    """The point a fraction t along a polyline, and the direction there."""
    segs = [(a, b, math.dist(a, b)) for a, b in zip(path, path[1:])]
    total = sum(L for *_, L in segs) or 1.0
    left = t * total
    for a, b, L in segs:
        if left <= L + EPS or (a, b, L) == segs[-1]:
            k = min(left / L, 1.0) if L else 0.0
            u = ((b[0] - a[0]) / L, (b[1] - a[1]) / L) if L else (1.0, 0.0)
            return (a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k), u
        left -= L
    return path[-1], (1.0, 0.0)


def beside(at: tuple[float, float], pos: str, w: float, h: float, gap: float) -> Box:
    """Box (cm) of text of size w x h on one side of a point."""
    x, y = at
    return {"above": centred(x, y + gap + h / 2, w, h), "below": centred(x, y - gap - h / 2, w, h),
            "left": centred(x - gap - w / 2, y, w, h), "right": centred(x + gap + w / 2, y, w, h)}[pos]


# ---- geometry at one scale ---------------------------------------------------
def geometry(dia: Diagram, s: float, prof: Profile) -> Geometry:
    g = Geometry(s)
    blocks = {b.id: b for b in dia.blocks}
    for b in dia.blocks:
        _block_geometry(b, s, prof, g)
    for lk in dia.links:
        g.paths.append(_path(lk, blocks, s, g.errors))
    for lk, path in zip(dia.links, g.paths):
        if path is not None and lk.label:
            pt = font_of(lk.font, prof)
            w, h = text_box_size(lk.label, pt)
            (x, y), _ = point_along(path, lk.label_at)
            g.labels.append(Label(beside((x * s, y * s), lk.label_pos, w, h, LABEL_GAP),
                                  lk.label, pt, lk.name, lk.color))
    for t in dia.texts:
        pt = font_of(t.font, prof)
        w, h = text_box_size(t.text, pt)
        g.labels.append(Label(anchored((t.at[0] * s, t.at[1] * s), t.anchor, w, h),
                              t.text, pt, t.name, t.color))
    return g


def _block_geometry(b: Block, s: float, prof: Profile, g: Geometry) -> None:
    cx, cy = b.at[0] * s, b.at[1] * s
    if b.type in BOX_TYPES:
        x0, y0, x1, y1 = b.rect()
        body = Box(x0 * s, y0 * s, x1 * s, y1 * s)
        g.bodies[b.id] = body
        if b.text and b.type != "limiter":
            pt = font_of(b.font, prof)
            w, h = text_box_size(b.text, pt)
            ink = b.color or (TAG_STYLE[b.style][2] if b.type == "tag" else None)
            g.labels.append(Label(centred(cx, cy, w, h), b.text, pt, b.id, ink))
        if b.label:
            pt = font_of(b.font, prof)
            w, h = text_box_size(b.label, pt)
            edge = {"above": (cx, body.y1), "below": (cx, body.y0),
                    "left": (body.x0, cy), "right": (body.x1, cy)}[b.label_pos]
            g.labels.append(Label(beside(edge, b.label_pos, w, h, LABEL_GAP), b.label, pt,
                                  f"{b.id} label", b.color))
    elif b.type in CIRCLE_TYPES:
        g.bodies[b.id] = centred(cx, cy, 2 * SUM_R, 2 * SUM_R)
        for side, sign in b.signs.items():
            (dx, dy) = SIDE_DIR[side]
            # beside the incoming arrow: above it for w/e inputs, left of it for n/s
            px, py = (0.0, SIGN_LIFT) if dx else (-SIGN_LIFT, 0.0)
            at = (cx + dx * (SUM_R + SIGN_GAP) + px, cy + dy * (SUM_R + SIGN_GAP) + py)
            g.signs.append((at, sign))
            g.labels.append(Label(centred(*at, SIGN_BOX, SIGN_BOX), sign, prof.font_pt, f"{b.id} sign", b.color))
        if b.label:
            pt = font_of(b.font, prof)
            w, h = text_box_size(b.label, pt)
            edge = {"above": (cx, cy + SUM_R), "below": (cx, cy - SUM_R),
                    "left": (cx - SUM_R, cy), "right": (cx + SUM_R, cy)}[b.label_pos]
            g.labels.append(Label(beside(edge, b.label_pos, w, h, LABEL_GAP), b.label, pt,
                                  f"{b.id} label", b.color))
    else:  # switch: contacts are the body; letters sit beyond each throw
        pts = [b.at, *b.throws.values()]
        xs, ys = [p[0] * s for p in pts], [p[1] * s for p in pts]
        g.bodies[b.id] = Box(min(xs) - CONTACT_R, min(ys) - CONTACT_R,
                             max(xs) + CONTACT_R, max(ys) + CONTACT_R)
        for name, q in b.throws.items():
            d = math.dist(b.at, q) or 1.0
            ux, uy = (q[0] - b.at[0]) / d, (q[1] - b.at[1]) / d
            at = (q[0] * s + ux * THROW_LABEL_GAP, q[1] * s + uy * THROW_LABEL_GAP)
            w, h = text_size(name, prof.font_pt)
            g.labels.append(Label(centred(*at, w, h), name, prof.font_pt, f"{b.id} throw",
                                  b.color))


def _path(lk: Link, blocks: dict[str, Block], s: float, errors: list[str]):
    pts = []
    for end, what in ((lk.start, "from"), (lk.end, "to")):
        if end.block is None:
            pts.append(end.point)
            continue
        b = blocks.get(end.block)
        if b is None:
            errors.append(f"{lk.name}: {what} names unknown block {end.block!r}")
            return None
        try:
            pts.append(port_point(b, end.port, end.offset, s))
        except ValueError as exc:
            errors.append(f"{lk.name}: {exc}")
            return None
    path = [pts[0], *lk.via, pts[1]]
    for a, b in zip(path, path[1:]):
        if math.dist(a, b) < EPS:
            errors.append(f"{lk.name}: two consecutive points are the same ({a[0]:g}, {a[1]:g})")
            return None
        if abs(a[0] - b[0]) > EPS and abs(a[1] - b[1]) > EPS:
            errors.append(f"{lk.name}: segment ({a[0]:g}, {a[1]:g}) -> ({b[0]:.4g}, {b[1]:.4g}) "
                          f"is neither horizontal nor vertical; add a corner in via")
            return None
    return path


# ---- scale -------------------------------------------------------------------
def extent(dia: Diagram, g: Geometry, prof: Profile) -> Box | None:
    """Printed bounding box (cm) of everything drawn."""
    boxes = list(g.bodies.values()) + [lb.box for lb in g.labels]
    for path in g.paths:
        if path:
            xs, ys = [p[0] * g.s for p in path], [p[1] * g.s for p in path]
            boxes.append(Box(min(xs), min(ys), max(xs), max(ys)))
    for r in dia.regions:
        boxes += [region_rect(r, g.scale, k) for k in range(len(r.layers) + 1)]
        lb = region_label_box(r, g.scale, prof)
        if lb is not None:
            boxes.append(lb)
    if not boxes:
        return None
    return Box(min(b.x0 for b in boxes), min(b.y0 for b in boxes),
               max(b.x1 for b in boxes), max(b.y1 for b in boxes))


def fit_scale(dia: Diagram, prof: Profile) -> float:
    """The largest scale (at most MAX_SCALE) at which the figure fits the column."""
    s = MAX_SCALE
    for _ in range(FIT_ROUNDS):
        box = extent(dia, geometry(dia, s, prof), prof)
        if box is None or box.x1 - box.x0 <= prof.width_cm + EPS:
            break
        # fixed-size parts do not shrink, so step a little past the ratio
        s *= prof.width_cm / (box.x1 - box.x0) * 0.995
    return s


# ---- the checker -------------------------------------------------------------
def check(raw: Any, prof: Profile) -> Report:
    dia, errors = parse_lenient(raw)
    warnings: list[str] = []
    s = fit_scale(dia, prof)
    g = geometry(dia, s, prof)
    errors += g.errors
    if s < prof.min_scale - EPS:
        errors.append(f"too wide for {prof.name}: it would print at {s:.2f} cm per unit, "
                      f"below the {prof.min_scale:g} limit; move blocks closer or use the "
                      f"double-column profile")
    box = extent(dia, g, prof)
    if box is not None and box.x1 - box.x0 > prof.width_cm + 0.05:
        errors.append(f"figure is {box.x1 - box.x0:.2f} cm wide, wider than the "
                      f"{prof.width_cm:.2f} cm column; the fixed-size symbols and text "
                      f"do not shrink, so move blocks closer")
    _connections(dia, g, errors, warnings)
    _overlaps(dia, g, errors, warnings)
    _fit_text(dia, g, prof, errors)
    _regions(dia, g, errors, warnings)
    if isinstance(raw, dict) and "loop" in raw:
        from .loop import parse_loop
        errors += parse_loop(raw["loop"], dia)[1]
    if isinstance(raw, dict) and "code" in raw:
        from .codegen import check_code_section
        errors += check_code_section(raw, dia)
    if isinstance(raw, dict) and "functions" in raw:
        from .functions import parse as parse_functions, wiring
        fns, ferr = parse_functions(raw["functions"], dia)
        wiring(fns, dia.links, ferr)
        errors += ferr
    return Report(dia, g, errors, warnings)


def _connections(dia: Diagram, g: Geometry, errors: list[str], warnings: list[str]) -> None:
    """Bare ends, port use, and what each block type needs."""
    s = g.s
    ins: dict[tuple[str, str], list[str]] = {}
    outs: dict[tuple[str, str], list[str]] = {}

    def touch(end: End, p, lk: Link, incoming: bool, others) -> None:
        if end.block is not None:
            key = (end.block, end.port + (f"{end.offset:+g}" if end.offset else ""))
        else:
            hit = [(b.id, side) for b in dia.blocks if (side := on_boundary(b, p, s))]
            if hit:
                key = hit[0]
            elif not incoming and any(_on_path(p, q) for q in others):
                if not any(math.dist(p, d) < EPS for d in g.dots):
                    g.dots.append(p)
                return
            elif incoming and any(_on_path(p, q) for q in others):
                errors.append(f"{lk.name}: ends on another line at ({p[0]:g}, {p[1]:g}); "
                              f"signals do not merge, end it at a port or a sum")
                return
            elif lk.open:
                return
            else:
                errors.append(f"{lk.name}: {'to' if incoming else 'from'} "
                              f"({p[0]:g}, {p[1]:g}) touches no port and no line; "
                              f"connect it, or set \"open\": true for an output stub")
                return
        (ins if incoming else outs).setdefault(key, []).append(lk.name)

    for i, (lk, path) in enumerate(zip(dia.links, g.paths)):
        if path is None:
            continue
        others = [q for j, q in enumerate(g.paths) if j != i and q]
        touch(lk.start, path[0], lk, False, others)
        touch(lk.end, path[-1], lk, True, others)
        if lk.arrow in ("end", "both") and math.dist(path[-1], path[-2]) * s < MIN_ARROW_RUN - EPS:
            errors.append(f"{lk.name}: the last segment is {math.dist(path[-1], path[-2]) * s:.2f} cm, "
                          f"too short for its arrowhead (at least {MIN_ARROW_RUN:.2f} cm)")
        if lk.arrow in ("start", "both") and math.dist(path[0], path[1]) * s < MIN_ARROW_RUN - EPS:
            errors.append(f"{lk.name}: the first segment is too short for its arrowhead")
    for key, names in ins.items():
        if len(names) > 1:
            errors.append(f"{key[0]}.{key[1]} receives {len(names)} lines ({', '.join(names)}); "
                          f"combine signals with a sum, multiplier or divider")
    for key, names in outs.items():
        if len(names) > 1:
            warnings.append(f"{key[0]}.{key[1]} starts {len(names)} lines; draw one and "
                            f"branch the others from it so the junction gets a dot")
    for b in dia.blocks:
        bin_ = [k[1] for k in ins if k[0] == b.id]
        bout = [k[1] for k in outs if k[0] == b.id]
        both = set(bin_) & set(bout)
        if both:
            errors.append(f"{b.id}: port {sorted(both)[0]} is used as an input and an output")
        if b.type == "sum":
            for side in b.signs:
                if side not in bin_:
                    errors.append(f"{b.id}: side {side} has sign {b.signs[side]} but no "
                                  f"line comes in there")
            for side in bin_:
                if side not in b.signs:
                    errors.append(f"{b.id}: a line comes in at side {side}, which has no "
                                  f"sign; add it to signs")
            if len(b.signs) < 2:
                errors.append(f"{b.id}: a sum needs at least two signed inputs")
        if b.type in ("mul", "div") and len(bin_) < 2:
            errors.append(f"{b.id}: a {'multiplier' if b.type == 'mul' else 'divider'} "
                          f"needs two inputs, it has {len(bin_)}")
        if b.type in CIRCLE_TYPES and not bout:
            errors.append(f"{b.id}: no line leaves this {b.type}")
        if b.type == "switch":
            missing = [p for p in [POLE, *b.throws] if p not in bin_ + bout]
            if missing:
                warnings.append(f"{b.id}: contacts {missing} are not connected")
        elif not bin_ and not bout:
            warnings.append(f"{b.id}: no line touches this block")


def _seg_cm(path, s):
    return [((a[0] * s, a[1] * s), (b[0] * s, b[1] * s)) for a, b in zip(path, path[1:])]


def _overlaps(dia: Diagram, g: Geometry, errors: list[str], warnings: list[str]) -> None:
    ids = list(g.bodies)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if g.bodies[a].overlaps(g.bodies[b], BLOCK_CLEARANCE):
                errors.append(f"blocks {a} and {b} overlap or are closer than "
                              f"{BLOCK_CLEARANCE:g} cm")
    segs = [(lk.name, _seg_cm(p, g.s)) for lk, p in zip(dia.links, g.paths) if p]
    for name, ss in segs:
        for p, q in ss:
            for bid, body in g.bodies.items():
                if dia.block(bid).type != "switch" and body.hits_segment(p, q):
                    errors.append(f"{name}: passes through block {bid}")
                    break
    for i, (na, sa) in enumerate(segs):
        for nb, sb in segs[i + 1:]:
            if any(_collinear(p, q) for p in sa for q in sb):
                errors.append(f"{na} and {nb} run on top of each other; move one aside")
    texts = g.labels
    for lb in texts:
        own = lb.owner.split()[0]
        for bid, body in g.bodies.items():
            if bid == own:
                continue
            if lb.box.overlaps(body):
                errors.append(f"text {lb.text!r} ({lb.owner}) overlaps block {bid}")
        for name, ss in segs:
            if lb.owner == name:
                continue
            if any(lb.box.hits_segment(p, q) for p, q in ss):
                warnings.append(f"text {lb.text!r} ({lb.owner}) sits on {name}")
    for i, a in enumerate(texts):
        for b in texts[i + 1:]:
            if a.owner != b.owner and a.box.overlaps(b.box):
                warnings.append(f"texts {a.text!r} and {b.text!r} overlap")


def _collinear(a, b) -> bool:
    (p0, p1), (q0, q1) = a, b
    if abs(p0[1] - p1[1]) < EPS and abs(q0[1] - q1[1]) < EPS and abs(p0[1] - q0[1]) < EPS:
        lo, hi = max(min(p0[0], p1[0]), min(q0[0], q1[0])), min(max(p0[0], p1[0]), max(q0[0], q1[0]))
        return hi - lo > EPS
    if abs(p0[0] - p1[0]) < EPS and abs(q0[0] - q1[0]) < EPS and abs(p0[0] - q0[0]) < EPS:
        lo, hi = max(min(p0[1], p1[1]), min(q0[1], q1[1])), min(max(p0[1], p1[1]), max(q0[1], q1[1]))
        return hi - lo > EPS
    return False


def _fit_text(dia: Diagram, g: Geometry, prof: Profile, errors: list[str]) -> None:
    for b in dia.blocks:
        if b.type not in ("block", "tag") or not b.text:
            continue
        body = g.bodies[b.id]
        w, h = text_box_size(b.text, font_of(b.font, prof))
        need_w, need_h = w + 2 * TEXT_PAD, h + 2 * TEXT_PAD_Y
        if need_w > body.x1 - body.x0 + EPS or need_h > body.y1 - body.y0 + EPS:
            errors.append(f"{b.id}: text {b.text!r} needs about {need_w / g.s:.2f} x "
                          f"{need_h / g.s:.2f} units at this scale, the box is "
                          f"{b.size[0]:g} x {b.size[1]:g}")


def _regions(dia: Diagram, g: Geometry, errors: list[str], warnings: list[str]) -> None:
    ids = {b.id for b in dia.blocks}
    for r in dia.regions:
        rect = region_rect(r, g.scale)
        if r.border != "none":
            edges = [((rect.x0, rect.y0), (rect.x1, rect.y0)), ((rect.x0, rect.y1), (rect.x1, rect.y1)),
                     ((rect.x0, rect.y0), (rect.x0, rect.y1)), ((rect.x1, rect.y0), (rect.x1, rect.y1))]
            for lb in g.labels:
                if any(lb.box.hits_segment(p, q) for p, q in edges):
                    warnings.append(f"text {lb.text!r} ({lb.owner}) crosses the border of {r.name}")
        for m in r.members:
            if m not in ids:
                errors.append(f"{r.name}: member {m!r} is not a block")
                continue
            body = g.bodies[m]
            if not (rect.x0 - EPS <= body.x0 and body.x1 <= rect.x1 + EPS
                    and rect.y0 - EPS <= body.y0 and body.y1 <= rect.y1 + EPS):
                errors.append(f"{r.name}: member {m} sticks out of the region")
