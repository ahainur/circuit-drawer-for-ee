"""Hard checks on a coordinate table.

A table that fails any error-level check is rejected and must be rewritten
(by hand or by the model that wrote it); there is no automatic repair.  The
messages name the element ids and coordinates involved so the writer can fix
exactly those entries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import combinations

from .geometry import (WINDING_GAP, BODY_FRAC, symbol_kind, EPS, MIN_LEAD, SW_HALF_SPAN, Box, sw_k, Footprint,
                       COIL_HALF_W, CORE_GAP, Scale, annotation_box, axis_sizes,
                       footprint, is_diagonal, scaled, transformer_frame, unscaled,
                       callout_box, callout_gap, callout_label_box, callout_shape, region_label_box,
                       region_rect, block_rect, block_text_box, isolation_bars, isolation_gap,
                       text_clearance)
from .layout import (EXTRA_WINDINGS, MAX_WINDINGS, OPTIONAL_PINS, PIN_NAMES, SWITCHES, TWO_TERMINAL,
                     Layout, Point, windings)
from .profiles import Profile
from .regions import ink_problems, problems as region_problems, safe_regions
from .schema import element_problems
from .glyphs import problems as glyph_problems
from .blocks import TEXT_PAD as BLOCK_TEXT_PAD, problems as block_problems, safe_blocks
from .source_waves import problems as wave_problems, symbols as wave_symbols
from .geometry import wave_symbol

BODY_CLEARANCE = 0.06    # cm between two symbol bodies
PARALLEL_MIN = 0.18      # cm between two parallel lines that run side by side


@dataclass
class Issue:
    code: str
    message: str
    level: str = "error"

    def __str__(self) -> str:
        return f"[{self.level}] {self.code}: {self.message}"


@dataclass
class Report:
    scale: Scale
    issues: list[Issue] = field(default_factory=list)
    nets: dict[str, int] = field(default_factory=dict)   # "El.pin" -> net id
    wire_nets: list[int] = field(default_factory=list)
    junctions: list[Point] = field(default_factory=list)  # in layout units

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {"ok": self.ok,
                "scale_cm_per_unit": [round(self.scale.x, 4), round(self.scale.y, 4)],
                "issues": [vars(i) for i in self.issues]}


@dataclass
class Seg:
    p: Point
    q: Point
    owner: str          # element id, or "wire[i]"
    kind: str           # "wire" | "lead"

    @property
    def horizontal(self) -> bool:
        return abs(self.p[1] - self.q[1]) < EPS

    @property
    def vertical(self) -> bool:
        return abs(self.p[0] - self.q[0]) < EPS

    @property
    def diagonal(self) -> bool:
        return not (self.horizontal or self.vertical)

    @property
    def hop_rank(self) -> int:
        """Which line of a crossing carries the hop arc: horizontal first,
        then a rising 45-degree line, then a falling one; never a vertical."""
        if self.horizontal:
            return 3
        if self.vertical:
            return 0
        rising = (self.q[0] - self.p[0]) * (self.q[1] - self.p[1]) > 0
        return 2 if rising else 1


def _fmt(p: Point, s: Scale) -> str:
    x, y = unscaled(p, s)
    return f"({round(x, 3):g}, {round(y, 3):g})"


def _key(p: Point) -> tuple[float, float]:
    return (round(p[0], 4), round(p[1], 4))


def _on_interior(pt: Point, seg: Seg) -> bool:
    (x0, y0), (x1, y1) = seg.p, seg.q
    if seg.horizontal:
        return abs(pt[1] - y0) < EPS and min(x0, x1) + EPS < pt[0] < max(x0, x1) - EPS
    if seg.vertical:
        return abs(pt[0] - x0) < EPS and min(y0, y1) + EPS < pt[1] < max(y0, y1) - EPS
    dx, dy = x1 - x0, y1 - y0
    L = (dx * dx + dy * dy) ** 0.5
    if abs((pt[0] - x0) * dy - (pt[1] - y0) * dx) / L > EPS:
        return False
    t = ((pt[0] - x0) * dx + (pt[1] - y0) * dy) / L
    return EPS < t < L - EPS


def _cross_point(a: Seg, b: Seg) -> Point | None:
    """Where two non-parallel lines meet, or None for parallel ones."""
    d1 = (a.q[0] - a.p[0], a.q[1] - a.p[1])
    d2 = (b.q[0] - b.p[0], b.q[1] - b.p[1])
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-9:
        return None
    t = ((b.p[0] - a.p[0]) * d2[1] - (b.p[1] - a.p[1]) * d2[0]) / den
    return (a.p[0] + t * d1[0], a.p[1] + t * d1[1])


def _parallel(a: Seg, b: Seg) -> tuple[float, float]:
    """Gap between two parallel lines and how far they run side by side."""
    dx, dy = a.q[0] - a.p[0], a.q[1] - a.p[1]
    L = (dx * dx + dy * dy) ** 0.5
    ux, uy = dx / L, dy / L
    gap = abs((b.p[0] - a.p[0]) * uy - (b.p[1] - a.p[1]) * ux)
    ta = sorted((0.0, L))
    tb = sorted(((b.p[0] - a.p[0]) * ux + (b.p[1] - a.p[1]) * uy,
                 (b.q[0] - a.p[0]) * ux + (b.q[1] - a.p[1]) * uy))
    return gap, min(ta[1], tb[1]) - max(ta[0], tb[0])


def _same_point(a: Point, b: Point) -> bool:
    return abs(a[0] - b[0]) < EPS and abs(a[1] - b[1]) < EPS


class _UF:
    def __init__(self) -> None:
        self.parent: dict = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b) -> None:
        self.parent[self.find(a)] = self.find(b)


# ---------------------------------------------------------------------------
def footprints(layout: Layout, s: Scale, prof: Profile) -> dict[str, Footprint]:
    return {el.id: footprint(el, s, prof) for el in layout.elements}


def validate(layout: Layout, s: Scale, prof: Profile, *, connections=()) -> Report:
    rep = Report(scale=s)
    add = rep.issues.append
    from .control_view import problems as control_view_problems
    for message in control_view_problems(layout):
        add(Issue("bad-control-view", message))
    # a system block diagram (boxes with icons, no parts) is a drawing too;
    # plain boxes alone are not (a control table also has "blocks")
    if not (layout.elements or layout.wires or layout.annotations or layout.extra.get("wave_symbols")
            or any(b.icon for b in safe_blocks(layout))):
        add(Issue("empty-drawing", "Add components or describe a new topology before exporting."))
        return rep

    # ---- 1. element schema ----------------------------------------------
    ids = [e.id for e in layout.elements]
    for dup in {i for i in ids if ids.count(i) > 1}:
        add(Issue("duplicate-id", f"element id {dup} used more than once"))
    schema_ok = True
    for el in layout.elements:
        need = PIN_NAMES.get(el.kind)
        if need is None:
            add(Issue("unknown-kind", f"{el.id}: unknown kind {el.kind!r}; "
                      f"known kinds: {sorted(PIN_NAMES)}"))
            schema_ok = False
            continue
        missing = [p for p in need if p not in el.pins]
        if el.kind == "transformer":
            # an extra winding needs both ends; any other pin name is a typo
            missing += [f"{w}{end}" for w in EXTRA_WINDINGS for end in "ab"
                        if f"{w}a" in el.pins or f"{w}b" in el.pins
                        if f"{w}{end}" not in el.pins]
            stray = sorted(set(el.pins) - set(need) - set(OPTIONAL_PINS["transformer"]))
            if stray:
                add(Issue("unknown-pin", f"{el.id} (transformer) has unknown pins {stray}; "
                          f"extra windings are w3a/w3b .. w{MAX_WINDINGS}a/w{MAX_WINDINGS}b"))
                schema_ok = False
        if missing:
            add(Issue("missing-pin", f"{el.id} ({el.kind}) lacks pins {missing}"))
            schema_ok = False
    for code, msg in element_problems(layout):
        add(Issue(code, msg))
        schema_ok = False
    for msg in region_problems(layout, s, prof):
        add(Issue("bad-region", msg))
    raw_regions = layout.extra.get("regions", [])
    for i, r in enumerate(raw_regions if isinstance(raw_regions, list) else []):
        if isinstance(r, dict) and r.get("fit") == "members" and ("from" in r or "to" in r):
            add(Issue("region-fit-overrides", f'regions[{i}]: fit="members" overrides from/to; '
                      "remove the explicit corners or use the editor fit button to freeze them", "warning"))
    _subscripts(layout, add)
    for msg in ink_problems(layout):
        add(Issue("bad-color", msg))
    for msg in wave_problems(layout):
        add(Issue("bad-source-wave", msg))
    for msg in glyph_problems(layout):
        add(Issue("bad-glyph", msg))
    for msg in block_problems(layout):
        add(Issue("bad-block", msg))
    if not schema_ok:
        return rep

    fps = footprints(layout, s, prof)
    def min_len(kind: str, z: float = 1.0) -> float:
        return prof.bipole_len * BODY_FRAC[kind] * z + 2 * MIN_LEAD

    for el in layout.elements:
        P = {k: scaled(v, s) for k, v in el.pins.items()}
        length, width = axis_sizes(el)
        if el.kind in TWO_TERMINAL:
            a, b = P["a"], P["b"]
            dx, dy = abs(a[0] - b[0]), abs(a[1] - b[1])
            min_two = min_len(symbol_kind(el), length)
            if dx > EPS and dy > EPS and el.kind == "scr":
                add(Issue("not-orthogonal", f"{el.id}: a thyristor's pins must be on one horizontal "
                          f"or vertical line (its gate lead is drawn across that axis)"))
            elif dx > EPS and dy > EPS and is_diagonal(el.pins["a"], el.pins["b"]):
                if (dx * dx + dy * dy) ** 0.5 < min_two - EPS:
                    add(Issue("too-short", f"{el.id}: 45-degree part shorter than "
                              f"{min_two:.2f} cm; move its pins apart"))
            elif dx > EPS and dy > EPS:
                add(Issue("not-orthogonal", f"{el.id}: pins {_fmt(a, s)} and "
                          f"{_fmt(b, s)} are not on one horizontal or vertical line "
                          f"(or an exact 45-degree one, |dx| = |dy|)"))
            elif max(dx, dy) < min_two - EPS:
                k = s.x if dx > dy else s.y
                add(Issue("too-short", f"{el.id}: pin distance {max(dx, dy):.2f} cm "
                          f"< {min_two:.2f} cm needed for the symbol and its leads; "
                          f"move the pins apart by at least "
                          f"{(min_two - max(dx, dy)) / k:.2f} units"))
        elif el.kind in SWITCHES:
            t, b = (P[p] for p in PIN_NAMES[el.kind])
            span = max(abs(t[0] - b[0]), abs(t[1] - b[1]))
            if abs(t[0] - b[0]) > EPS and abs(t[1] - b[1]) > EPS:
                add(Issue("not-orthogonal", f"{el.id}: a switch's two pins must be on one "
                          f"horizontal or vertical line"))
            elif span < 2 * SW_HALF_SPAN * sw_k(prof) * length - EPS:
                k = s.y if abs(t[0] - b[0]) < EPS else s.x
                add(Issue("too-short", f"{el.id}: its two pins must be at least "
                          f"{2 * SW_HALF_SPAN * sw_k(prof) * length / k:.2f} units apart"))
        elif el.kind == "pmsm":
            a, b, c = (P[k] for k in PIN_NAMES[el.kind])
            if (abs(a[0]-c[0]) > EPS and abs(a[1]-c[1]) > EPS
                    or any(abs(b[i]-(a[i]+c[i])/2) > EPS for i in (0, 1))):
                add(Issue("not-orthogonal", f"{el.id}: PMSM 的 a、b、c 引脚须水平或竖直等距排列，b 在 a 与 c 正中"))
            elif max(abs(a[0]-c[0]), abs(a[1]-c[1])) < 4*MIN_LEAD:
                add(Issue("too-short", f"{el.id}: PMSM 相邻引脚至少间隔 {2*MIN_LEAD:.2f} cm；请分开 a、b、c 引脚"))
        elif el.kind == "transformer":
            axis, _, P = transformer_frame(el, s)
            along, across = (s.y, s.x) if axis[0] == 0 else (s.x, s.y)
            xp, xs = P["p1"][0], P["s1"][0]
            sec = ["s1"] + (["sct"] if "sct" in P else []) + ["s2"]
            lo = max(WINDING_GAP[0], 2 * (COIL_HALF_W + CORE_GAP) * width + 0.04 + MIN_LEAD)
            hi = max(WINDING_GAP[1], lo)
            if abs(P["p2"][0] - xp) > EPS or any(abs(P[k][0] - xs) > EPS for k in sec):
                add(Issue("not-orthogonal", f"{el.id}: windings must be parallel and horizontal or vertical"))
            elif not lo - EPS <= xs - xp <= hi + EPS:
                add(Issue("winding-gap", f"{el.id}: secondary must sit "
                          f"{lo / across:.2f}-{hi / across:.2f} units from the primary "
                          f"along its local right axis (now {(xs - xp) / across:.2f})"))
            coils = [("p1", "p2")] + list(zip(sec, sec[1:]))
            min_two = min_len("L", length)
            for u, v in coils:
                if abs(P[u][1] - P[v][1]) < min_two - EPS:
                    add(Issue("too-short", f"{el.id}: winding {u}-{v} shorter than "
                              f"{min_two / along:.2f} units"))
                if P[u][1] < P[v][1]:
                    add(Issue("bad-order", f"{el.id}: pin {u} must precede {v} along the winding axis"))
            # extra windings: on the primary or the secondary axis, either
            # way round (the dot marks wka), long enough, and not overlapping
            # another winding on the same axis
            spans = {"p": [("p", P["p2"][1], P["p1"][1])], "s": [("s", P[sec[-1]][1], P["s1"][1])]}
            for name, a, b in windings(el):
                if name not in EXTRA_WINDINGS:
                    continue
                xa, xb = P[a][0], P[b][0]
                side = "p" if abs(xa - xp) < EPS else "s" if abs(xa - xs) < EPS else None
                if side is None or abs(xb - xa) > EPS:
                    add(Issue("not-orthogonal", f"{el.id}: winding {name} ({a}-{b}) must lie on the "
                              f"primary axis (with p1-p2) or the secondary axis (with s1-s2)"))
                    continue
                if abs(P[a][1] - P[b][1]) < min_two - EPS:
                    add(Issue("too-short", f"{el.id}: winding {a}-{b} shorter than "
                              f"{min_two / along:.2f} units"))
                lo_w, hi_w = sorted((P[a][1], P[b][1]))
                for other, o0, o1 in spans[side]:
                    if lo_w < o1 + EPS and o0 < hi_w + EPS:     # touching ends would join them
                        add(Issue("winding-overlap", f"{el.id}: winding {name} overlaps winding "
                                  f"{other} on the same axis; move {a}/{b} along the axis so the "
                                  f"two coils sit one after the other, with a gap between their ends"))
                spans[side].append((name, lo_w, hi_w))

    # ---- 2. wires ---------------------------------------------------------
    segs: list[Seg] = []
    # a 45-degree wire may only continue a 45-degree part along its own line
    diag_axes = [Seg(scaled(e.pins["a"], s), scaled(e.pins["b"], s), e.id, "lead")
                 for e in layout.elements if e.kind in TWO_TERMINAL
                 and is_diagonal(e.pins["a"], e.pins["b"])]

    def on_diag_axis(p: Point, q: Point) -> bool:
        def on_line(pt: Point, g: Seg) -> bool:
            dx, dy = g.q[0] - g.p[0], g.q[1] - g.p[1]
            return abs((pt[0] - g.p[0]) * dy - (pt[1] - g.p[1]) * dx) / (dx * dx + dy * dy) ** 0.5 < EPS
        return any(on_line(p, g) and on_line(q, g) for g in diag_axes)

    for i, w in enumerate(layout.wires):
        if len(w) < 2:
            add(Issue("short-wire", f"wire[{i}] needs at least two points"))
            continue
        pts = [scaled(p, s) for p in w]
        for p, q in zip(pts, pts[1:]):
            if _same_point(p, q):
                add(Issue("zero-length", f"wire[{i}] repeats point {_fmt(p, s)}"))
            elif abs(p[0] - q[0]) > EPS and abs(p[1] - q[1]) > EPS and \
                    not (is_diagonal(unscaled(p, s), unscaled(q, s))
                         and on_diag_axis(p, q)):
                add(Issue("not-orthogonal", f"wire[{i}] segment {_fmt(p, s)} -> "
                          f"{_fmt(q, s)} is diagonal; insert a corner point (a 45-degree "
                          f"wire is allowed only on the line of a 45-degree part)"))
            else:
                segs.append(Seg(p, q, f"wire[{i}]", "wire"))
    for el in layout.elements:
        for p, q in fps[el.id].leads:
            if abs(p[0] - q[0]) < EPS or abs(p[1] - q[1]) < EPS or \
                    is_diagonal(unscaled(p, s), unscaled(q, s)):
                segs.append(Seg(p, q, el.id, "lead"))
    if rep.errors:
        return rep

    # ---- 3. symbol bodies -------------------------------------------------
    bodies = [(el.id, b) for el in layout.elements for b in fps[el.id].bodies]
    bodies += [(f"wave of {eid}", fp.extra["source_wave"]["box"])
               for eid, fp in fps.items() if "source_wave" in fp.extra]
    bodies += [(f"wave_symbols[{i}]", wave_symbol(scaled(w["at"], s), w["wave"], w["scale"])[1])
               for i, w in enumerate(wave_symbols(layout)) if w is not None]
    for (ia, ba), (ib, bb) in combinations(bodies, 2):
        if ia != ib and ba.overlaps(bb, BODY_CLEARANCE):
            add(Issue("body-overlap", f"{ia} and {ib} symbols overlap or are "
                      f"closer than {BODY_CLEARANCE} cm"))
    on_wire = {f"wave_symbols[{i}]" for i, w in enumerate(wave_symbols(layout)) if w and w["on_wire"]}
    crossed: set[str] = set()
    for seg in segs:
        for eid, b in bodies:
            if seg.owner != eid and b.hits_segment(seg.p, seg.q):
                if eid in on_wire:
                    crossed.add(eid)
                    continue
                add(Issue("line-through-body", f"{seg.owner} line {_fmt(seg.p, s)} -> "
                          f"{_fmt(seg.q, s)} runs through the symbol of {eid}"))
    for eid in sorted(crossed):
        add(Issue("on-wire-allowed", f"{eid} sits on a wire on purpose (on_wire); the wire "
                  f"under it is hidden in the drawing and stays connected", "note"))

    # ---- 4. line against line --------------------------------------------
    hops = [scaled(h, s) for h in layout.hops]
    used_hops: set[int] = set()
    for a, b in combinations(segs, 2):
        if a.owner == b.owner and a.kind == "lead":
            continue
        if a.diagonal or b.diagonal:
            _slanted_pair(a, b, hops, used_hops, add, s)
            continue
        if a.horizontal == b.horizontal:
            fixed_a = a.p[1] if a.horizontal else a.p[0]
            fixed_b = b.p[1] if b.horizontal else b.p[0]
            ax = sorted((a.p[0], a.q[0]) if a.horizontal else (a.p[1], a.q[1]))
            bx = sorted((b.p[0], b.q[0]) if b.horizontal else (b.p[1], b.q[1]))
            overlap = min(ax[1], bx[1]) - max(ax[0], bx[0])
            gap = abs(fixed_a - fixed_b)
            if gap < EPS and overlap > EPS:
                add(Issue("collinear-overlap", f"{a.owner} and {b.owner} run on top "
                          f"of each other near {_fmt(a.p, s)}"))
            elif EPS < gap < PARALLEL_MIN and overlap > EPS:
                add(Issue("too-close", f"{a.owner} and {b.owner} run parallel only "
                          f"{gap:.2f} cm apart (min {PARALLEL_MIN} cm)"))
            continue
        h, v = (a, b) if a.horizontal else (b, a)
        x, y = v.p[0], h.p[1]
        cross = (x, y)
        if _on_interior(cross, h) and _on_interior(cross, v):
            k = next((i for i, hp in enumerate(hops) if _same_point(hp, cross)), None)
            if k is None:
                add(Issue("unmarked-crossing", f"{h.owner} crosses {v.owner} at "
                          f"{_fmt(cross, s)} without a junction; reroute or add "
                          f"this point to hops"))
            elif h.kind != "wire":
                add(Issue("bad-hop", f"hop at {_fmt(cross, s)} must sit on a "
                          f"horizontal wire, not on {h.owner}"))
            else:
                used_hops.add(k)
    for i, hp in enumerate(hops):
        if i not in used_hops:
            add(Issue("unused-hop", f"hop at {_fmt(hp, s)} is not on a crossing",
                      "warning"))

    # ---- 5. connectivity --------------------------------------------------
    uf = _UF()
    pin_at: dict[str, Point] = {}
    for el in layout.elements:
        for name, p in el.pins.items():
            pin_at[f"{el.id}.{name}"] = scaled(p, s)
    wire_segs = [g for g in segs if g.kind == "wire"]
    lead_segs = [g for g in segs if g.kind == "lead"]
    for g in wire_segs:
        uf.union(_key(g.p), _key(g.q))
    endpoints = [(g.p, g) for g in wire_segs] + [(g.q, g) for g in wire_segs]
    # T junctions: an end point landing inside another wire joins it
    on_lead: set = set()
    for pt, own in endpoints + [(p, None) for p in pin_at.values()]:
        for g in wire_segs:
            if g is not own and _on_interior(pt, g):
                uf.union(_key(pt), _key(g.p))
        for g in lead_segs:
            if _on_interior(pt, g):
                is_own_pin = any(_same_point(pt, pin_at[f"{g.owner}.{n}"])
                                 for n in layout.element(g.owner).pins)
                if not is_own_pin:
                    on_lead.add(_key(pt))
                    add(Issue("touches-lead", f"a connection at {_fmt(pt, s)} lands on "
                              f"the lead of {g.owner}; connect at its pins instead"))
    for el in layout.elements:
        names = list(el.pins)
        for u, v in combinations(names, 2):
            if _same_point(el.pins[u], el.pins[v]):
                add(Issue("shorted-pins", f"{el.id}.{u} and {el.id}.{v} coincide"))

    # incidence count at each point, to find dangling ends and junction dots
    # Zero-length port symbols also need the incidence of a wire through them.
    degree: dict[tuple[float, float], int] = {_key(p): 0 for p in pin_at.values()}
    for g in segs:
        for p in (g.p, g.q):
            degree[_key(p)] = degree.get(_key(p), 0) + 1
    for g in wire_segs:
        for pt in list(degree):
            if _on_interior(pt, g):
                degree[pt] += 2
    for el in layout.elements:
        if el.kind == "port":           # the circle ends the wire
            k = _key(pin_at[f"{el.id}.p"])
            degree[k] = degree.get(k, 0) + 1
    nc = layout.extra.get("no_connect", [])
    nc = {p for p in nc if isinstance(p, str)} if isinstance(nc, list) else set()
    # A pin in an explicit module-compiler connection is joined even with no
    # wire drawn to it (a folded instance's port, see Assembly.sheets).
    joined = {ref for refs in connections if len(refs) > 1 for ref in refs}
    for ref, p in pin_at.items():
        if ref in nc and degree.get(_key(p), 0) >= 2:
            add(Issue("bad-no-connect", f"{ref}: NoConn 只允许用于悬空引脚，请移除标记或断开连接"))
        if ref not in joined and degree.get(_key(p), 0) < 2 and ref not in nc:
            add(Issue("open-pin", f"{ref} at {_fmt(p, s)} is not connected"))
    for pt, g in endpoints:
        # an end on a lead is already reported as touches-lead
        if degree.get(_key(pt), 0) < 2 and _key(pt) not in on_lead:
            add(Issue("dangling-wire", f"{g.owner} ends at {_fmt(pt, s)} without "
                      f"meeting anything"))
    rep.junctions = sorted(tuple(round(v, 3) for v in unscaled(k, s))
                           for k, d in degree.items() if d >= 3)

    # Only the module compiler supplies these explicit, labelled port joins.
    # Ordinary JSON net names never imply a physical connection by themselves.
    for refs in connections:
        # A compiler-provided join may end in an omitted module. Its complete
        # circuit is checked separately; never join across the omitted device.
        refs = [ref for ref in refs if ref in pin_at]
        if refs:
            for ref in refs[1:]:
                uf.union(_key(pin_at[refs[0]]), _key(pin_at[ref]))
    roots: dict = {}
    for ref, p in pin_at.items():
        r = uf.find(_key(p))
        rep.nets[ref] = roots.setdefault(r, len(roots))

    for wire in layout.wires:
        root = uf.find(_key(scaled(wire[0], s)))
        rep.wire_nets.append(roots.setdefault(root, len(roots)))
    from .electrical import problems as electrical_problems
    from .model_mapping import problems as model_problems
    from .emi import problems as emi_problems
    for code, message in (*electrical_problems(layout, rep.nets), *model_problems(layout), *emi_problems(layout, rep.nets)):
        add(Issue(code, message))

    # ---- 6. drawing must match the declared netlist ----------------------
    if layout.netlist:
        for ref in layout.netlist:
            if ref not in rep.nets:
                add(Issue("netlist-unknown-pin", f"netlist names {ref}, which is not "
                          f"a pin in the drawing"))
        known = [r for r in layout.netlist if r in rep.nets]
        # one issue per declared net drawn in pieces, and one per pair of
        # declared nets the drawing joins, not one per pin pair
        declared: dict[str, list[str]] = {}
        drawn: dict[int, list[str]] = {}
        for r in known:
            declared.setdefault(layout.netlist[r], []).append(r)
            drawn.setdefault(rep.nets[r], []).append(r)
        for net, refs in declared.items():
            pieces: dict[int, list[str]] = {}
            for r in refs:
                pieces.setdefault(rep.nets[r], []).append(r)
            if len(pieces) > 1:
                groups = " | ".join(", ".join(g) for g in pieces.values())
                add(Issue("netlist-open", f"net {net} is drawn in {len(pieces)} separate "
                          f"pieces: {groups}; join them with a wire"))
        for refs in drawn.values():
            nets = sorted({layout.netlist[r] for r in refs})
            for a, b in combinations(nets, 2):
                pa = ", ".join(r for r in refs if layout.netlist[r] == a)
                pb = ", ".join(r for r in refs if layout.netlist[r] == b)
                add(Issue("netlist-short", f"the drawing joins net {a} ({pa}) "
                          f"and net {b} ({pb})"))
        for ref in rep.nets:
            if ref not in layout.netlist and not ref.endswith(".sct"):
                add(Issue("netlist-missing", f"{ref} is drawn but not declared in "
                          f"the netlist", "warning"))

    # ---- 7. regions and callouts ------------------------------------------
    regions, callouts = safe_regions(layout, s, prof)
    _check_regions(regions, callouts, bodies, s, add)
    _check_details(regions, callouts, [g for g in segs if g.kind == "wire"], s, add)
    _check_members(regions, fps, s, add)
    blocks = safe_blocks(layout)
    _check_blocks(blocks, callouts, bodies, segs, s, prof, add)

    # ---- 8. text --------------------------------------------------------
    texts: list[tuple[str, Box, float]] = []
    for el in layout.elements:
        texts += [(f"label of {el.id}", b, text_clearance(el.opts.get("label_size"), prof)) for b in fps[el.id].labels]
    for r in regions:
        if (box := region_label_box(r, s, prof)) is not None:
            texts.append((f"label of {r.name}", box, text_clearance(r.size, prof)))
    callout_lines = []
    for c in callouts:
        if (box := callout_label_box(c, s, prof)) is not None:
            gap = callout_gap(c, s, prof)
            if gap and any(gap.x0 <= p[0] <= gap.x1 and gap.y0 <= p[1] <= gap.y1
                           for p in (scaled(c.start, s), scaled(c.end, s))):
                add(Issue("inline-label-end", f"label of {c.name} covers an arrow endpoint; "
                          "lengthen the arrow, shorten the text or move label_at toward the middle"))
            texts.append((f"label of {c.name}", box, text_clearance(c.size, prof)))
            mark = isolation_gap(c, s)
            if mark is not None and gap is not None and mark.overlaps(gap):
                add(Issue("isolation-on-label", f"the isolation mark of {c.name} sits on its "
                          "inline label; move isolation or label_at apart"))
        callout_lines += [(c.name, p, q) for p, q in _outline(c, s)]
    for b in blocks:
        if (box := block_text_box(b, s, prof)) is not None:
            texts.append((f"text of {b.name}", box, text_clearance(b.size, prof)))
    for a in layout.annotations:
        texts.append((f"text {a.text!r}", annotation_box(a, s, prof), text_clearance(a.size, prof)))
        if ARROW_TEXT.fullmatch(a.text.strip()):
            add(Issue("arrow-as-text", f"text {a.text!r} draws an arrow out of a symbol; "
                      f"use a callout (\"callouts\": [{{\"from\": ..., \"to\": ...}}])"))
        if a.arrow and not any(_on_interior(scaled(a.at, s), g)
                               for g in segs if g.kind == "wire"):
            add(Issue("arrow-off-wire", f"current arrow {a.text!r} at "
                      f"{_fmt(scaled(a.at, s), s)} is not on a wire"))
    from .geometry import electrical_labels
    if not any(i.code == "bad-electrical" for i in rep.issues):
        for label in electrical_labels(layout, s, prof):
            texts.append((f"{label.kind} label {label.text} ({label.ref})", label.box, text_clearance(label.font_pt, prof)))
            if label.blocked:
                add(Issue("electrical-label-space", f"{label.ref}: no clear space for {label.text}; "
                          "move nearby elements/text or shorten the network name in the editor"))
    for name, box, clearance in texts:
        for eid, b in bodies:
            if box.overlaps(b, clearance) and not name.endswith(f"of {eid}"):
                add(Issue("text-on-symbol", f"{name} overlaps the symbol of {eid}"))
        for g in segs:
            if box.hits_segment(g.p, g.q):
                add(Issue("text-on-line", f"{name} is crossed by {g.owner} "
                          f"{_fmt(g.p, s)} -> {_fmt(g.q, s)}"))
        for b in blocks:
            if name != f"text of {b.name}" and box.overlaps(block_rect(b, s)):
                add(Issue("text-on-block", f"{name} overlaps {b.name}; move the text out of the box"))
        # Report once per arrow, not once for every sampled segment.
        allowed = {c.name for c in callouts if c.over_parts}
        for owner in dict.fromkeys(o for o, p, q in callout_lines
                                   if o not in allowed and box.hits_segment(p, q) and name != f"label of {o}"):
            add(Issue("text-on-callout", f"{name} is covered by {owner}"))
    for (na, ba, ca), (nb, bb, cb) in combinations(texts, 2):
        if ba.overlaps(bb, max(ca, cb)):
            add(Issue("text-overlap", f"{na} overlaps {nb}"))

    return rep


def _edges(b: Box) -> list[tuple[Point, Point]]:
    c = [(b.x0, b.y0), (b.x1, b.y0), (b.x1, b.y1), (b.x0, b.y1)]
    return list(zip(c, c[1:] + c[:1]))


def _contains(outer: Box, inner: Box) -> bool:
    return (outer.x0 - EPS <= inner.x0 and inner.x1 <= outer.x1 + EPS
            and outer.y0 - EPS <= inner.y0 and inner.y1 <= outer.y1 + EPS)


def _check_regions(regions, callouts, bodies, s: Scale, add) -> None:
    """A region's edge (or a panel behind it) must not cut a symbol in two,
    and a callout arrow must not cover one."""
    for r in regions:
        front = region_rect(r, s)
        hit: set[str] = set()
        for k in range(len(r.layers) + 1):
            rect = region_rect(r, s, k)
            for eid, b in bodies:
                if eid in hit:
                    continue
                if k and r.fill != "none" and _contains(front, b):
                    continue            # the opaque front panel hides that edge
                if any(b.hits_segment(p, q) for p, q in _edges(rect)):
                    hit.add(eid)
                    where = "edge" if k == 0 else f"stacked panel {k}"
                    add(Issue("region-cuts-symbol", f"the {where} of {r.name} runs through "
                              f"the symbol of {eid}; move the corner so the part is wholly "
                              f"inside or outside"))
    for c in callouts:
        pts, segs = callout_shape(c, s), _outline(c, s)
        if c.over_parts:
            add(Issue("callout-overlap-allowed", f"{c.name}: 已允许这支块箭头覆盖元件和文字（over_parts）；可在属性或问题栏撤回", "note"))
        for eid, b in bodies:
            covered = c.style == "block" and _inside_poly(b, pts)
            if not c.over_parts and (covered or any(b.hits_segment(p, q) for p, q in segs)):
                add(Issue("callout-on-symbol", f"{c.name} covers the symbol of {eid}; "
                          f"end the arrow before the part"))


def _check_blocks(blocks, callouts, bodies, segs, s: Scale, prof: Profile, add) -> None:
    """A function block is a box of its own: no symbol, wire or other block
    inside it, its text fits, and a signal arrow ends at its edge instead of
    running through it."""
    rects = [(b, block_rect(b, s)) for b in blocks]
    for b, r in rects:
        if (t := block_text_box(b, s, prof)) is not None and not b.icon and (
                t.x1 - t.x0 + 2 * BLOCK_TEXT_PAD > r.x1 - r.x0 + EPS
                or t.y1 - t.y0 + 2 * BLOCK_TEXT_PAD > r.y1 - r.y0 + EPS):
            add(Issue("block-text-overflow", f"the text of {b.name} does not fit its box; "
                      f"widen the box or shorten the text"))
        for eid, body in bodies:
            if r.overlaps(body):
                add(Issue("block-on-symbol", f"{b.name} overlaps the symbol of {eid}; "
                          f"move the box clear of the circuit"))
        for owner in dict.fromkeys(g.owner for g in segs if r.hits_segment(g.p, g.q)):
            add(Issue("line-through-block", f"{owner} runs through {b.name}; route it "
                      f"around the box or end it at the edge"))
        for c in callouts:
            if c.style == "line" and any(r.hits_segment(p, q) for p, q in _outline(c, s)):
                add(Issue("callout-through-block", f"{c.name} runs through {b.name}; end the "
                          f"arrow at the edge of the box or route it around with via"))
    for (a, ra), (b, rb) in combinations(rects, 2):
        if ra.overlaps(rb):
            add(Issue("block-overlap", f"{a.name} overlaps {b.name}"))


# a subscript of two or more characters written without braces: LaTeX lowers
# only the first one ($V_in$ prints V_i n); \_ is a literal underscore
SUBSCRIPT = re.compile(r"(?<!\\)_([A-Za-z0-9]{2,})")


def _subscripts(layout: Layout, add) -> None:
    texts = [(f"label of {e.id}", e.label) for e in layout.elements]
    texts += [(f"{k} of {e.id}", e.opts.get(k)) for e in layout.elements for k in ("current", "ratio")]
    texts += [(f"annotations[{i}]", a.text) for i, a in enumerate(layout.annotations)]
    for key, field in (("regions", "label"), ("callouts", "label"), ("blocks", "text")):
        raw = layout.extra.get(key)
        texts += [(f"{key}[{i}]", d.get(field)) for i, d in enumerate(raw if isinstance(raw, list) else [])
                  if isinstance(d, dict)]
    for owner, text in texts:
        if isinstance(text, str) and (m := SUBSCRIPT.search(text)):
            sub = m[1]
            add(Issue("subscript-braces", f"{owner}: {text!r} lowers only {sub[0]!r} of the subscript "
                      f"_{sub}; write _{{{sub}}}", "warning"))


# an annotation that is nothing but an arrow glyph (maybe coloured or bold)
ARROW_TEXT = re.compile(r"\$?(\\(color\w*|textcolor|boldsymbol|bm|mathbf|large|huge|"
                        r"Large|Huge)\b|\{[^{}]*\}|\[[^\]]*\]|\s)*\\(long)?(right|left|"
                        r"Right|Left|Long(right|left))arrow\}*\$?|\$?\\to\$?|[→⇒⟶⟹]+")
DETAIL_REACH = 0.5   # cm: an arrow ending this close to a region points at it


def _check_details(regions, callouts, wires, s: Scale, add) -> None:
    """The region an arrow points into (and starts outside) holds a zoomed
    detail: a separate drawing, so no wire may cross into it from the
    circuit it magnifies."""
    for c in callouts:
        a, b = scaled(c.start, s), scaled(c.end, s)
        targets = [(rect, r) for r in regions
                   if _inside(rect := region_rect(r, s), b, DETAIL_REACH)
                   and not _inside(rect, a)]
        if not targets:
            continue
        rect, r = min(targets, key=lambda t: (t[0].x1 - t[0].x0) * (t[0].y1 - t[0].y0))
        crossing = sorted({g.owner for g in wires if rect.hits_segment(g.p, g.q)
                           and not (_inside(rect, g.p) and _inside(rect, g.q))})
        if crossing and c.detail is False:
            add(Issue("detail-off", f"{c.name} only points at {r.name} (detail: false); the "
                      f"parts in it are the real circuit, so {', '.join(crossing)} may wire them", "note"))
        elif crossing:
            add(Issue("detail-wired", f"{c.name} points at the zoomed detail in {r.name}, "
                      f"but {', '.join(crossing)} wire it to the circuit outside; a detail "
                      f"is a separate drawing: its parts get their own node names and its "
                      f"terminals end in ports, the arrow is the only link"))


def _inside(b: Box, p: Point, grow: float = -EPS) -> bool:
    return b.x0 - grow < p[0] < b.x1 + grow and b.y0 - grow < p[1] < b.y1 + grow


def _check_members(regions, fps, s: Scale, add) -> None:
    """A region that lists its members is a module: every member's symbol
    and label sits wholly inside the front rectangle, and a part inside it
    that is not listed is pointed out (it may belong to the module)."""
    for r in regions:
        if not r.members:
            continue
        front = region_rect(r, s)
        for m in r.members:
            if m not in fps:
                add(Issue("bad-region", f"{r.name}: member {m} is not an element of the table"))
            elif not all(_contains(front, b) for b in fps[m].bodies + fps[m].labels):
                add(Issue("member-outside", f"{m} is listed in {r.name} but is not wholly "
                          f"inside it; move the region's corners or the part"))
        for eid, fp in fps.items():
            if eid not in r.members and fp.bodies and all(_contains(front, b) for b in fp.bodies):
                add(Issue("unlisted-member", f"{eid} sits inside {r.name} but is not in its "
                          f"members", "warning"))


def _outline(c, s: Scale) -> list[tuple[Point, Point]]:
    """Edges of a block callout's outline, or the one line of a line callout."""
    pts = callout_shape(c, s)
    if c.style == "block":
        return list(zip(pts, pts[1:] + pts[:1]))
    return list(zip(pts, pts[1:])) + isolation_bars(c, s)


def _inside_poly(b: Box, pts: list[Point]) -> bool:
    """The box centre lies inside the polygon (even-odd rule)."""
    x, y = (b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2
    inside = False
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            inside = not inside
    return inside


def _slanted_pair(a: Seg, b: Seg, hops, used_hops, add, s: Scale) -> None:
    """Line-against-line checks when one of the two runs at 45 degrees."""
    cross = _cross_point(a, b)
    if cross is None:
        gap, overlap = _parallel(a, b)
        if gap < EPS and overlap > EPS:
            add(Issue("collinear-overlap", f"{a.owner} and {b.owner} run on top "
                      f"of each other near {_fmt(a.p, s)}"))
        elif EPS < gap < PARALLEL_MIN and overlap > EPS:
            add(Issue("too-close", f"{a.owner} and {b.owner} run parallel only "
                      f"{gap:.2f} cm apart (min {PARALLEL_MIN} cm)"))
        return
    if not (_on_interior(cross, a) and _on_interior(cross, b)):
        return
    k = next((i for i, hp in enumerate(hops) if _same_point(hp, cross)), None)
    if k is None:
        add(Issue("unmarked-crossing", f"{a.owner} crosses {b.owner} at "
                  f"{_fmt(cross, s)} without a junction; reroute or add "
                  f"this point to hops"))
        return
    wires = [g for g in (a, b) if g.kind == "wire" and g.hop_rank > 0]
    if not wires:
        add(Issue("bad-hop", f"hop at {_fmt(cross, s)} must sit on a horizontal "
                  f"or 45-degree wire, not on {a.owner} / {b.owner}"))
    else:
        used_hops.add(k)


# ---------------------------------------------------------------------------
def extent(layout: Layout, s: Scale, prof: Profile) -> Box:
    """Bounding box of everything drawn, in cm."""
    boxes: list[Box] = []
    for el in layout.elements:
        fp = footprint(el, s, prof)
        boxes += fp.bodies + fp.labels
        if "source_wave" in fp.extra:
            boxes.append(fp.extra["source_wave"]["box"])
        for p, q in fp.leads:
            boxes.append(Box(min(p[0], q[0]), min(p[1], q[1]),
                             max(p[0], q[0]), max(p[1], q[1])))
    for w in layout.wires:
        for p in w:
            x, y = scaled(p, s)
            boxes.append(Box(x, y, x, y))
    for a in layout.annotations:
        boxes.append(annotation_box(a, s, prof))
    for w in wave_symbols(layout):
        if w is not None:
            boxes.append(wave_symbol(scaled(w["at"], s), w["wave"], w["scale"])[1])
    regions, callouts = safe_regions(layout, s, prof)
    for r in regions:
        boxes += [region_rect(r, s, k) for k in range(len(r.layers) + 1)]
        boxes += [b for b in (region_label_box(r, s, prof),) if b is not None]
    for c in callouts:
        boxes += [b for b in (callout_box(c, s), callout_label_box(c, s, prof)) if b is not None]
    boxes += [block_rect(b, s) for b in safe_blocks(layout)]
    from .glyphs import glyphs
    for glyph in glyphs(layout):
        a, b = scaled(glyph["from"], s), scaled(glyph["to"], s)
        boxes.append(Box(min(a[0],b[0]),min(a[1],b[1]),max(a[0],b[0]),max(a[1],b[1])))
    return Box(min(b.x0 for b in boxes), min(b.y0 for b in boxes),
               max(b.x1 for b in boxes), max(b.y1 for b in boxes))


def fit_scale(layout: Layout, prof: Profile, *, connections=()) -> Scale:
    """Fit the column limit without stretching small circuits unnecessarily.

    Prefer one printed centimetre per coordinate unit when that fits and
    passes validation. If symbols/labels need more room, try progressively
    wider spacing up to the original column fit. Never move JSON coordinates.
    """
    if not (layout.elements or layout.wires or layout.annotations or any(wave_symbols(layout))):
        return Scale(1.0, 1.0)

    def width(sx: float) -> float:
        b = extent(layout, Scale(sx, sx), prof)
        return b.x1 - b.x0

    lo, hi = 0.05, prof.max_scale
    for _ in range(50):
        mid = (lo + hi) / 2
        if width(mid) > prof.width_cm:
            hi = mid
        else:
            lo = mid
    diagonal = any(e.kind in TWO_TERMINAL and is_diagonal(e.pins["a"], e.pins["b"])
                   for e in layout.elements)

    def at(x):
        if diagonal:
            return Scale(min(x, prof.y_max_scale), min(x, prof.y_max_scale))
        return Scale(x, min(max(x, prof.y_min_scale), prof.y_max_scale))

    # Fixed physical symbol sizes may require a little extra lead/label space.
    # This bounded scale search is not a placement or routing algorithm.
    natural = min(1.0, lo)
    for step in range(9):
        x = natural + 0.05 * step
        if x >= lo - EPS:
            break
        candidate = at(x)
        if validate(layout, candidate, prof, connections=connections).ok:
            return candidate
    return at(lo)


def collapsed_box_issues(layout: Layout, instances, s: Scale, prof: Profile) -> list[Issue]:
    """Solid module envelopes, excluding their own ports and hidden internals.

    Ordinary decorative regions are deliberately not component envelopes.
    Use the rendered region rectangles and component footprints in printed cm.
    Only interior overlap counts: a lead or pin may meet the frame boundary.
    NoConn crosses and electrical captions are decorations, not device ink.
    """
    from .regions import parse_region
    box_ids = {f"{iid}/box" for iid in instances}
    boxes = {r["id"][:-4]: region_rect(parse_region(r, i, layout, s, prof), s)
             for i, r in enumerate(layout.extra.get("regions", []))
             if r.get("id") in box_ids}
    issues = []
    for (a, ba), (b, bb) in combinations(boxes.items(), 2):
        if ba.overlaps(bb):
            issues.append(Issue("module-box-overlap", f"折叠框 {a} 与 {b} 重叠；请缩小方框或调整实例位置"))
    for iid, box in boxes.items():
        for el in layout.elements:
            if el.id.startswith(iid + "/"):
                continue
            fp = footprint(el, s, prof)
            covered = []
            if any(box.overlaps(body) for body in fp.bodies):
                covered.append("符号")
            if any(box.hits_segment(p, q) for p, q in fp.leads):
                covered.append("引线")
            if any(box.overlaps(label) for label in fp.labels):
                covered.append("标签")
            if any(_inside(box, scaled(p, s)) for p in el.pins.values()):
                covered.append("引脚")
            if covered:
                issues.append(Issue("module-box-overlap", f"折叠框 {iid} 压到元件 {el.id}（{'、'.join(covered)}）；请缩小方框或调整位置"))
    return issues


def check(layout: Layout, prof: Profile, scale: Scale | None = None, *, connections=()) -> Report:
    return check_with_assembly(layout, prof, scale, connections=connections)[0]


def check_with_assembly(layout: Layout, prof: Profile, scale: Scale | None = None, *, connections=()):
    """``check()`` and, for a table with modules, the assembly it was drawn
    from (None otherwise or when it could not be built), so a caller that
    also needs the drawn view does not assemble the table a second time."""
    # Report schema mistakes before fitting the scale: measuring a label
    # looks its side up directly and would fail on a misspelt one.
    from .drawing_scale import read as drawing_scale, width_issue
    try:
        requested = drawing_scale(layout)
    except ValueError as exc:
        return Report(scale or Scale(1, 1), [Issue("bad-drawing-scale", str(exc))]), None
    locked = scale is None and requested is not None
    if locked:
        scale = requested
    early = element_problems(layout)
    if early:
        return Report(scale or Scale(1, 1), [Issue(code, msg) for code, msg in early]), None
    if "modules" in layout.extra:
        from .modules import checked
        try:
            assembly, _, report = checked(layout, prof, scale)
            if locked and assembly is not None:
                issue = width_issue(assembly.view, report.scale, prof)
                if issue:
                    report.issues.append(issue)
            return report, assembly
        except (ValueError, TypeError, KeyError) as exc:
            return Report(scale or Scale(1, 1), [Issue("bad-module", str(exc))]), None
    s = fit_scale(layout, prof, connections=connections) if scale is None else scale
    rep = validate(layout, s, prof, connections=connections)
    if locked:
        issue = width_issue(layout, s, prof)
        if issue:
            rep.issues.append(issue)
    if s.x < prof.min_scale:
        rep.issues.append(Issue("too-cramped", f"the circuit only fits "
                                f"{prof.name} at scale {s.x:.2f} < {prof.min_scale}; "
                                f"use a wider profile or a more compact layout"))
    return rep, None
