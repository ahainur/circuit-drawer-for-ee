"""Coordinate table -> standalone CircuitikZ document.

The emitter never moves anything.  It writes layout coordinates verbatim and
lets ``x``/``y`` units carry the scale, so the .tex stays easy to hand-edit.
"""

from __future__ import annotations

import math

from .geometry import (EPS, SW_LABEL_DX, Footprint, Scale, axis_sizes, block_text_box, callout_label_box,
                       callout_shape, callout_visible_paths, font_of, footprint, has_wide, isolation_bars,
                       region_label_box,
                       scaled, sw_k, unscaled)
from .layout import PIN_NAMES, SWITCHES, TWO_TERMINAL, Element, Layout, Point
from .profiles import Profile
from .regions import (LINE_HEAD, Callout, Region, ink_rgb, ink_tikz, rgb, safe_regions, tikz_rgb,
                      valid_ink)
from .validate import Report, extent
from .glyphs import glyphs, tikz as glyph_tikz
from .blocks import Block, fill_rgb as block_fill, safe_blocks
from .source_waves import symbols as wave_symbols
from .geometry import symbol_kind, wave_symbol

HOP_R = 0.12        # cm

CTIKZ_KIND = {"R": "R", "L": "L", "C": "C", "D": "D", "scr": "Ty", "V": "V", "vac": "sV", "I": "american current source",
              "battery": "battery1", "zener": "zD"}


def _n(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".") if abs(v) > 1e-9 else "0"


def _p(p: Point) -> str:
    return f"({_n(p[0])},{_n(p[1])})"


def preamble(prof: Profile, cjk: bool = False) -> str:
    """``cjk`` loads CJKutf8 for Chinese text; the caller closes the CJK
    environment before ``\\end{document}``."""
    lead = prof.font_pt * 1.2
    head = rf"""\documentclass[border=1pt]{{standalone}}
\usepackage{{mathptmx}}  % Times text and math
\usepackage{{circuitikz}}
\ctikzset{{bipoles/length={prof.bipole_len}cm, transistors/scale={prof.transistor_scale},
  inductors/coils=4, current/distance=.5, bipoles/diode/height=.32,
  bipoles/diode/width=.26}}
\begin{{document}}
\fontsize{{{_n(prof.font_pt)}}}{{{_n(lead)}}}\selectfont
"""
    if cjk:
        head = head.replace(r"\begin{document}", "\\usepackage{CJKutf8}\n\\begin{document}")
        head += "\\begin{CJK}{UTF8}{gbsn}\n"
    return head


def _texts(layout: Layout) -> list[str]:
    """Every piece of text the figure prints, to decide on CJK support."""
    out = [a.text for a in layout.annotations]
    for el in layout.elements:
        out += [el.label or ""] + [str(el.opts.get(k) or "") for k in ("current", "ratio")]
    regions, callouts = safe_regions(layout)
    out += [r.label or "" for r in regions] + [c.label or "" for c in callouts]
    out += [b.text for b in safe_blocks(layout)]
    return out


def emit(layout: Layout, prof: Profile, report: Report, grid: bool = False,
         paint=None, mono: bool = False, node_labels: bool = False) -> str:
    """``grid`` overlays the layout-unit coordinate grid, for editing only.
    ``paint`` (a modes.Paint) restyles one operating mode: lit parts heavy and
    coloured, the rest grey, plus current arrows.  Coordinates do not change.
    ``mono`` draws regions and callouts in greys (always so for a
    black-and-white mode style)."""
    electrical = layout.extra.get("electrical")
    if "modules" in layout.extra:
        from .modules import checked
        assembly, _, report = checked(layout, prof)
        if not report.ok:
            raise ValueError("模块校验未通过，不能出图：" + "; ".join(map(str, report.errors)))
        layout = assembly.view
    s = report.scale
    mono = mono or bool(paint is not None and paint.style.mono)
    cjk = any(has_wide(t) for t in _texts(layout))
    regions, callouts = safe_regions(layout, s, prof)
    out: list[str] = [preamble(prof, cjk)]
    out.append(f"% {layout.name}: coordinates are layout units, 1 unit = "
               f"{_n(s.x)} cm across and {_n(s.y)} cm up ({prof.name})\n")
    out.append(rf"\begin{{circuitikz}}[x={_n(s.x)}cm, y={_n(s.y)}cm, "
               rf"line width={prof.line_pt}pt, cute inductors, "
               rf"european resistors, american voltages]" "\n")
    if grid:
        out.append(_grid(layout, prof, s))
    if regions or any(c.style == "block" and not c.foreground for c in callouts):
        out.append("% ---- regions (background) ----\n")
        out += [_region(r, s, prof, mono) for r in regions]
        out += [_callout(c, s, prof, mono) for c in callouts if c.style == "block" and not c.foreground]
    blocks = safe_blocks(layout)
    if blocks:
        out.append("% ---- function blocks (not electrical) ----\n")
        out += [_block(b, s, prof, mono) for b in blocks]
    out.append("% ---- wires ----\n")
    arcs: list[str] = []
    carriers = _hop_carriers(layout)
    for i, w in enumerate(layout.wires):
        if paint is None:
            out.append(_wire(w, layout.hops, s, arcs, carriers))
        else:
            for run, opt in paint.wire_runs(i, w):
                out.append(_wire(run, layout.hops, s, arcs, carriers, opt))
    for el in layout.elements:
        out.append(f"% ---- {el.id} ({el.kind}) ----\n")
        if paint is None:
            body = _element(el, s, prof)
            ink = el.opts.get("color")
            if ink and valid_ink(ink) and not mono:
                # the whole part, label and current arrow included, in one colour
                body = "\\begin{scope}[color=" + ink_tikz(ink) + "]\n" + body + "\\end{scope}\n"
            out.append(body)
        elif el.kind == "transformer":
            out.append(_element(el, s, prof, parts=paint.transformer(el.id), ink=paint.ink))
        elif el.kind in SWITCHES and paint.switch(el.id) in ("on", "diode"):
            out.append(_split_switch(el, s, prof, paint))
        else:
            out.append("\\begin{scope}[" + paint.element(el.id) + "]\n"
                       + _element(el, s, prof, ink=paint.ink) + "\\end{scope}\n")
    if layout.annotations:
        out.append("% ---- annotations ----\n")
        for a in layout.annotations:
            out.append(_annotation(a, prof, mono))
    if glyphs(layout):
        out.append("% ---- block function marks (not electrical) ----\n")
        for g in glyphs(layout):
            out.append(glyph_tikz(g))
    for w in wave_symbols(layout):
        if w is not None:
            points, box = wave_symbol(scaled(w["at"], s), w["wave"], w["scale"])
            color = "black" if mono else ink_tikz(w["color"])
            out.append("% ---- waveform mark (not an electrical connection) ----\n")
            if w["on_wire"]:
                out.append(_knockout(box, w["at"], regions, blocks, s, mono))
            out.append(_wave_path(points, s, color))
    if arcs:
        out.append("% ---- crossings without connection ----\n")
        out += arcs
    if report.junctions:
        out.append("% ---- junction dots ----\n")
        groups: dict[str, list[Point]] = {}
        for p in report.junctions:
            groups.setdefault(paint.dot(p) if paint else "", []).append(p)
        for opt, ps in groups.items():
            pts = ",".join(_p(p) for p in ps)
            fill = rf"\fill[{opt}]" if opt else r"\fill"
            k = paint.style.width ** 0.35 if paint and opt == paint.on else 1
            out.append(rf"\foreach \p in {{{pts}}} {fill} \p circle ({_n(prof.dot_pt * k)}pt);"
                       "\n")
    if paint is not None and paint.arrows():
        out.append("% ---- mode current arrows ----\n")
        for at, ang in paint.arrows():
            out.append(rf"\node[currarrow, {paint.on}, scale=1.25, rotate={_n(ang)}] "
                       rf"at {_p(at)} {{}};" "\n")
    if any(c.style != "block" for c in callouts):
        out.append("% ---- callouts ----\n")
        out += [_callout(c, s, prof, mono) for c in callouts if c.style != "block"]
    if node_labels and electrical:
        from .geometry import electrical_labels
        for label in electrical_labels(layout, s, prof, electrical):
            if label.kind != "node":
                continue
            if label.blocked:
                raise ValueError(f"{label.ref}: no clear space for {label.text}")
            name = label.text.replace("_", r"\_")
            font = rf"\fontsize{{{label.font_pt}}}{{{label.font_pt * 1.2}}}\sffamily\selectfont"
            out.append(rf"\node[inner sep=0pt,font={{{font}}}] at {_p(unscaled(label.centre, s))} {{{name}}};" + "\n")
    for ref in layout.extra.get("no_connect", []):
        for element in layout.elements:
            for pin, point in element.pins.items():
                if ref == f"{element.id}.{pin}":
                    from .geometry import no_connect_mark
                    for a, b in no_connect_mark(point, s):
                        out.append(rf"\draw {_p(a)} -- {_p(b)}; % NoConn" + "\n")
    if any(c.foreground for c in callouts):
        out.append("% ---- foreground block arrows (explicit) ----\n")
        out += [_callout(c, s, prof, mono) for c in callouts if c.foreground]
    out.append("\\end{circuitikz}\n" + ("\\end{CJK}\n" if cjk else "") + "\\end{document}\n")
    return "".join(out)


BORDER_STYLE = {"solid": "", "dashed": ", dash pattern=on 2.4pt off 1.6pt",
                "dotted": ", dash pattern=on 0.6pt off 1.2pt"}


def _label_node(box, text: str, size, s: Scale, prof: Profile) -> str:
    at = unscaled(((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2), s)
    pt = font_of(size, prof) if size else None
    return rf"\node[inner sep=0pt] at {_p(at)} {{{sized(text, pt)}}};" "\n"


def _region(r: Region, s: Scale, prof: Profile, mono: bool) -> str:
    """Panels behind first (farthest back first), then the front one."""
    lines = []
    width = f"line width={_n(prof.line_pt * 0.8)}pt"
    for k in range(len(r.layers), 0, -1):
        x0, y0, x1, y1 = r.rect(k)
        fill = r.layers[k - 1][2]
        lines.append(rf"\filldraw[fill={tikz_rgb(fill, mono)}, "
                     rf"draw={tikz_rgb(fill, mono, edge=True)}, {width}] "
                     rf"{_p((x0, y0))} rectangle {_p((x1, y1))};")
    x0, y0, x1, y1 = r.rect()
    opts = []
    if r.fill != "none":
        opts.append(f"fill={tikz_rgb(r.fill, mono)}")
    if r.border != "none":
        edge = r.border_color or (r.fill if r.fill != "none" else "gray")
        opts.append(f"draw={tikz_rgb(edge, mono, edge=True)}, {width}{BORDER_STYLE[r.border]}")
    cmd = "\\filldraw" if r.fill != "none" and r.border != "none" else (
        "\\fill" if r.fill != "none" else "\\draw")
    lines.append(rf"{cmd}[{', '.join(opts)}] {_p((x0, y0))} rectangle {_p((x1, y1))};")
    out = "\n".join(lines) + "\n"
    box = region_label_box(r, s, prof)
    if box is not None:
        out += _label_node(box, r.label, r.size, s, prof)
    return out


def _block(b: Block, s: Scale, prof: Profile, mono: bool) -> str:
    """A function block: box in the circuit's line width, centred text."""
    ink = "{rgb,255:red,%d;green,%d;blue,%d}" % ink_rgb(b.color, mono)
    fill = block_fill(b, mono)
    opts = [f"draw={ink}", f"line width={_n(prof.line_pt)}pt" + BORDER_STYLE[b.border]]
    if fill is not None:
        opts.insert(0, "fill={rgb,255:red,%d;green,%d;blue,%d}" % fill)
    out = (rf"\{'filldraw' if fill is not None else 'draw'}[{', '.join(opts)}] "
           rf"{_p((b.x0, b.y0))} rectangle {_p((b.x1, b.y1))};" "\n")
    box = block_text_box(b, s, prof)
    if box is not None:
        text = b.text if mono or b.color == "black" else \
            r"\textcolor[RGB]{%d,%d,%d}{%s}" % (*ink_rgb(b.color), b.text)
        out += _label_node(box, text, b.size, s, prof)
    return out


def _callout(c: Callout, s: Scale, prof: Profile, mono: bool, weight: float = 1.6) -> str:
    """``weight`` scales the stroke of line and wave arrows (x line_pt)."""
    pts = [unscaled(p, s) for p in callout_shape(c, s)]
    if c.style == "block":
        from .regions import ink_tikz
        edge = (tikz_rgb(c.color, mono, edge=True) if c.border_color is None else
                "none" if c.border_color == "none" else ink_tikz(c.border_color, mono))
        width = c.border_width if c.border_width is not None else prof.line_pt * 0.6
        opacity = f", fill opacity={_n(c.fill_opacity)}" if c.fill_opacity != 1 else ""
        out = (rf"\filldraw[fill={tikz_rgb(c.color, mono)}, draw={edge}, "
               rf"line width={_n(width)}pt, line join=round{opacity}] "
               + " -- ".join(_p(p) for p in pts) + " -- cycle;\n")
    else:
        color = (89, 89, 89) if mono else ink_rgb(c.color)
        col = "{rgb,255:red,%d;green,%d;blue,%d}" % color
        if (c.style == "line" and not c.via and not c.bend and c.label_pos != "inline"
                and c.isolation is None):
            paths = [(f"{_p(pts[0])} -- {_p(pts[1])}", True, True)]
        else:
            paths = []
            for curve in callout_visible_paths(c, s, prof):
                a, c1, c2, b = (unscaled(p, s) for p in curve)
                paths.append((f"{_p(a)} .. controls {_p(c1)} and {_p(c2)} .. {_p(b)}",
                              math.dist(a, c.start) < EPS, math.dist(b, c.end) < EPS))
        tip = rf"{{Stealth[length={_n(LINE_HEAD)}cm]}}"
        width = rf"line width={_n(prof.line_pt * weight)}pt"
        dash = BORDER_STYLE[c.dash]
        out = ""
        for path, first, last in paths:
            start = first and c.head in ("start", "both")
            end = last and c.head in ("end", "both")
            # arrows.meta draws the tips solid on a dashed line
            head = f"{tip if start else ''}-{tip if end else ''}, " if start or end else ""
            out += (rf"\draw[{head}draw={col}, line cap=round, line join=round, "
                    rf"{width}{dash}] {path};" "\n")
        for a, b in isolation_bars(c, s):
            out += rf"\draw[draw={col}, {width}] {_p(unscaled(a, s))} -- {_p(unscaled(b, s))};" "\n"
    box = callout_label_box(c, s, prof)
    if box is not None:
        text = c.label if mono or c.style == "block" else \
            r"\textcolor[RGB]{%d,%d,%d}{%s}" % (*ink_rgb(c.color), c.label)
        out += _label_node(box, text, c.size, s, prof)
    return out


def _rank(p: Point, q: Point) -> int:
    """Same order as the validator: horizontal, rising, falling, vertical."""
    if abs(p[1] - q[1]) < EPS:
        return 3
    if abs(p[0] - q[0]) < EPS:
        return 0
    return 2 if (q[0] - p[0]) * (q[1] - p[1]) > 0 else 1


def _inside(h: Point, p: Point, q: Point) -> bool:
    dx, dy = q[0] - p[0], q[1] - p[1]
    L = math.hypot(dx, dy)
    if abs((h[0] - p[0]) * dy - (h[1] - p[1]) * dx) / L > EPS:
        return False
    t = ((h[0] - p[0]) * dx + (h[1] - p[1]) * dy) / L
    return EPS < t < L - EPS


def _hop_carriers(layout: Layout) -> dict:
    """For each hop, the rank of the wire that draws its arc: the highest
    ranked wire segment through it, so the other line stays whole."""
    best: dict = {}
    for w in layout.wires:
        for p, q in zip(w, w[1:]):
            for h in layout.hops:
                if _inside(h, p, q):
                    best[h] = max(best.get(h, 0), _rank(p, q))
    return best


def _wire(w: list[Point], hops: list[Point], s: Scale, arcs: list[str],
          carriers: dict | None = None, opt: str = "") -> str:
    r = HOP_R / s.x
    d = f"[{opt}]" if opt else ""
    pieces: list[list[Point]] = [[w[0]]]
    for p, q in zip(w, w[1:]):
        rank = _rank(p, q)
        if rank in (1, 2):           # 45 degrees: carries a hop it outranks
            L = math.hypot(q[0] - p[0], q[1] - p[1])
            u = ((q[0] - p[0]) / L, (q[1] - p[1]) / L)
            on = sorted((h for h in hops if _inside(h, p, q)
                         and (carriers or {}).get(h, rank) == rank),
                        key=lambda h: (h[0] - p[0]) * u[0] + (h[1] - p[1]) * u[1])
            phi = math.degrees(math.atan2(u[1], u[0]))
            # bulge to the upper side, like the horizontal hops
            a0, a1 = (phi + 180, phi) if u[0] > 0 else (phi + 180, phi + 360)
            for h in on:
                start = (h[0] - u[0] * r, h[1] - u[1] * r)
                pieces[-1].append(start)
                arcs.append(rf"\draw{d} {_p(start)} arc[start angle={_n(a0)},"
                            rf"end angle={_n(a1)},radius={HOP_R}cm];" "\n")
                pieces.append([(h[0] + u[0] * r, h[1] + u[1] * r)])
        elif abs(p[1] - q[1]) < EPS:   # horizontal: may carry hops
            sign = 1 if q[0] > p[0] else -1
            on = sorted((h for h in hops if abs(h[1] - p[1]) < EPS
                         and min(p[0], q[0]) + EPS < h[0] < max(p[0], q[0]) - EPS),
                        key=lambda h: sign * h[0])
            for h in on:
                start, end = (h[0] - sign * r, h[1]), (h[0] + sign * r, h[1])
                pieces[-1].append(start)
                a0, a1 = (180, 0) if sign > 0 else (0, 180)
                # unfilled arc, no white halo: the crossed line stays whole
                arcs.append(rf"\draw{d} "
                            rf"{_p(start)} arc[start angle={a0},end angle={a1},"
                            rf"radius={HOP_R}cm];" "\n")
                pieces.append([end])
        pieces[-1].append(q)
    return "".join(rf"\draw{d} " + " -- ".join(_p(p) for p in pc) + ";\n"
                   for pc in pieces if len(pc) > 1)


def _element(el: Element, s: Scale, prof: Profile, parts: dict | None = None,
             ink: str | None = None) -> str:
    """``parts`` styles the windings and core of a transformer separately and
    ``ink`` keeps labels in that colour when the symbol itself is restyled."""
    z, width = axis_sizes(el)
    body = _element_body(el, s, prof, parts, ink)
    if el.opts.get("dash", "solid") != "solid":
        body = "\\begin{scope}[" + el.opts["dash"] + "]\n" + body + "\\end{scope}\n"
    if abs(z - 1) < 1e-9 or el.kind in ("transformer", "pmsm"):
        return body
    if el.kind in TWO_TERMINAL and abs(z - width) > 1e-9:
        return body                         # the local frame applies both axes
    # a resized symbol: local settings inside a group, nothing else changes
    keys = (f"transistors/scale={_n(prof.transistor_scale * z)}" if el.kind in SWITCHES
            else f"bipoles/length={_n(prof.bipole_len * z)}cm")
    return "{\\ctikzset{" + keys + "}\n" + body + "}\n"


def _bipole_path(kind: str, a: Point, b: Point, s: Scale, prof: Profile,
                 length: float, width: float, style: str = "", invert: bool = False) -> str:
    """Stretch only the symbol, with inverse-transformed endpoints keeping
    its pins fixed. Text is emitted outside this frame, at its checked position.
    """
    opt = kind + (", invert" if invert else "")
    if abs(length - width) < 1e-9:
        line = rf"\draw{style} {_p(a)} to[{opt}] {_p(b)};" "\n"
        if abs(length - 1) < 1e-9:
            return line
        return ("{\\ctikzset{bipoles/length=" + _n(prof.bipole_len * length)
                + "cm}\n" + line + "}\n")
    ap, bp = scaled(a, s), scaled(b, s)
    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    angle = math.degrees(math.atan2(bp[1] - ap[1], bp[0] - ap[0]))
    half = math.dist(ap, bp) / (2 * length)
    return (rf"\begin{{scope}}[shift={{{_p(mid)}}}]" "\n"
            rf"\begin{{scope}}[x=1cm, y=1cm, rotate={_n(angle)}, "
            rf"xscale={_n(length)}, yscale={_n(width)}, transform shape]" "\n"
            rf"\draw{style} ({_n(-half)},0) to[{opt}] ({_n(half)},0);" "\n"
            "\\end{scope}\n\\end{scope}\n")


def _two_terminal(el: Element, fp: Footprint, s: Scale, prof: Profile) -> str:
    """One path for every two-terminal part.  CircuitikZ places the label
    itself on a plain straight part; a 45-degree part or a symbol stretched on
    one axis gets its label where the validator checked it instead."""
    length, width = axis_sizes(el)
    if el.kind == "scr" and el.opts.get("mirror"):
        width = -width                  # the gate lead on the other side of the path
    stretched = abs(length - width) > 1e-9
    # a thyristor's label sits where the validator put it, clear of the gate lead
    own_label = el.label and fp.labels and (stretched or "diag" in fp.extra or el.kind == "scr" or "label_offset" in fp.extra)
    a, b = el.pins["a"], el.pins["b"]
    side = fp.label_side
    if el.kind == "V":
        # CircuitikZ puts the minus sign at the end of the path; draw from
        # + to - and flip the label side to match the reversed travel.
        a, b = b, a
        side = "_" if side == "^" else "^"
    current = (f"i>{el.opts.get('current_side', '^')}={el.opts['current']}"
               if el.opts.get("current") else None)
    if stretched:
        # the local frame would stretch text too: the arrow rides an open path
        line = _bipole_path(CTIKZ_KIND[symbol_kind(el)], a, b, s, prof, length, width,
                            invert=bool(el.opts.get("invert")))
    else:
        opts = [CTIKZ_KIND[symbol_kind(el)]]
        if el.label and not own_label:
            opts.append(f"l{side}={el.label}")
        if current:
            opts.append(current)
        if el.opts.get("invert"):
            opts.append("invert")
        line = rf"\draw {_p(a)} to[{', '.join(opts)}] {_p(b)};" "\n"
    if own_label:
        box = fp.labels[0]
        at = unscaled(((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2), s)
        line += rf"\node at {_p(at)} {{{el.label}}};" "\n"
    if stretched and current:
        line += rf"\draw {_p(a)} to[open, {current}] {_p(b)};" "\n"
    if "source_wave" in fp.extra:
        line += _wave_path(fp.extra["source_wave"]["points"], s)
    return line


# cm of background left around a mark that sits on a wire, so the wire stops
# just short of the mark instead of touching its strokes
KNOCKOUT_PAD = 0.04


def backdrop(at: Point, regions: list[Region], blocks: list[Block], mono: bool) -> tuple[int, int, int]:
    """The colour drawn behind a point: the topmost filled block or region
    panel there (blocks are drawn over regions), else the white page."""
    for b in reversed(blocks):
        if b.x0 <= at[0] <= b.x1 and b.y0 <= at[1] <= b.y1 and (fill := block_fill(b, mono)) is not None:
            return fill
    for r in reversed(regions):
        panels = [(r.rect(), r.fill)] + [(r.rect(k), r.layers[k - 1][2]) for k in range(1, len(r.layers) + 1)]
        for (x0, y0, x1, y1), fill in panels:
            if x0 <= at[0] <= x1 and y0 <= at[1] <= y1 and fill != "none":
                return rgb(fill, mono)
    return (255, 255, 255)


def _knockout(box, at: Point, regions, blocks, s: Scale, mono: bool) -> str:
    """Hide the wire under an on_wire mark with the colour behind it."""
    fill = "{rgb,255:red,%d;green,%d;blue,%d}" % backdrop(at, regions, blocks, mono)
    a = unscaled((box.x0 - KNOCKOUT_PAD, box.y0 - KNOCKOUT_PAD), s)
    b = unscaled((box.x1 + KNOCKOUT_PAD, box.y1 + KNOCKOUT_PAD), s)
    return rf"\fill[fill={fill}] {_p(a)} rectangle {_p(b)};" "\n"


def _wave_path(points, s: Scale, color: str | None = None) -> str:
    opt = "line cap=round, line join=round" + (f", color={color}" if color else "")
    return rf"\draw[{opt}] " + " -- ".join(_p(unscaled(p, s)) for p in points) + ";\n"


def _element_body(el: Element, s: Scale, prof: Profile, parts: dict | None = None,
                  ink: str | None = None) -> str:
    fp = footprint(el, s, prof)
    if ink:
        def inked(t):
            return rf"\textcolor{{{ink}}}{{{t}}}" if t else t
        el = Element(el.id, el.kind, el.pins, inked(el.label), el.label_pos,
                     {**el.opts, **{k: inked(el.opts[k]) for k in ("current", "ratio")
                                    if el.opts.get(k)}})
    if el.opts.get("label_size"):
        # same footprint (it was measured at this size), sized text in the output
        pt = font_of(el.opts["label_size"], prof)
        el = Element(el.id, el.kind, el.pins, sized(el.label, pt) if el.label else el.label,
                     el.label_pos, {**el.opts,
                                    **({"current": sized(el.opts["current"], pt)} if el.opts.get("current") else {}),
                                    **({"ratio": sized(el.opts["ratio"], pt)} if el.opts.get("ratio") else {})})
    if el.kind in TWO_TERMINAL:
        return _two_terminal(el, fp, s, prof)
    if el.kind == "pmsm":
        lines = [rf"\draw {_p(unscaled(a, s))} -- {_p(unscaled(b, s))};" for a, b in fp.leads]
        lines.append(rf"\draw {_p(unscaled(fp.centre, s))} circle[radius={_n(fp.extra['radius'])}cm];")
        for text, at, height in fp.extra["motor_marks"]:
            lines.append(rf"\node at {_p(unscaled(at, s))} {{{sized(text, height*72.27/2.54)}}};")
        if el.label:
            lines.append(rf"\node at {_p(unscaled(fp.extra['label_at'], s))} {{{el.label}}};")
        return "\n".join(lines) + "\n"
    if el.kind == "port":
        # a block terminal is where a wire meets a block's edge: no circle
        line = "" if el.opts.get("bare") else rf"\node[ocirc] at {_p(el.pins['p'])} {{}};" "\n"
        if el.label and fp.labels:
            box = fp.labels[0]
            at = unscaled(((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2), s)
            line += rf"\node at {_p(at)} {{{el.label}}};" "\n"
        return line
    if el.kind in SWITCHES:
        top, bot = ("s", "d") if el.kind == "pmos" else PIN_NAMES[el.kind]
        anchors = (top.upper(), bot.upper())
        shape = "nigbt" if el.kind == "igbt" else el.kind
        diode = ",bodydiode" if el.opts.get("diode", True) else ""
        c = unscaled(fp.centre, s)
        u, side = fp.extra["u"], fp.extra["side"]
        length, width = axis_sizes(el)
        stretch = width / length
        shape_scale = f", xscale={_n(stretch)}" if abs(stretch - 1) > 1e-9 else ""
        if (u, side) != ((0.0, 1.0), (1.0, 0.0)):
            # turned and/or mirrored: mirror in the switch's own frame, then rotate
            rot = {(0.0, 1.0): 0, (-1.0, 0.0): 90, (0.0, -1.0): 180, (1.0, 0.0): -90}[u]
            mirror = f", xscale={_n(-stretch)}" if el.opts.get("mirror") else shape_scale
            lines = [rf"\node[{shape}{diode}, rotate={rot}{mirror}] ({el.id}) at {_p(c)} {{}};",
                     rf"\draw {_p(el.pins[top])} -- ({el.id}.{anchors[0]}) "
                     rf"({el.id}.{anchors[1]}) -- {_p(el.pins[bot])};"]
            if el.label:
                lines.append(rf"\node at {_p(unscaled(fp.extra['label_at'], s))} {{{el.label}}};")
            return "\n".join(lines) + "\n"
        lines = [rf"\node[{shape}{diode}{shape_scale}] ({el.id}) at {_p(c)} {{}};",
                 rf"\draw {_p(el.pins[top])} -- ({el.id}.{anchors[0]}) "
                 rf"({el.id}.{anchors[1]}) -- {_p(el.pins[bot])};"]
        if el.label:
            if el.label_pos or "label_offset" in fp.extra:
                lines.append(rf"\node at {_p(unscaled(fp.extra['label_at'], s))} {{{el.label}}};")
            else:
                lines.append(rf"\node[anchor=west] at ([xshift={_n(SW_LABEL_DX * sw_k(prof) * width)}cm]{el.id}.center) "
                             rf"{{{el.label}}};")
        return "\n".join(lines) + "\n"
    if el.kind == "transformer":
        ex = {k: v for k, v in fp.extra.items()}

        def u(p: Point) -> Point:   # printed cm back to layout units
            return unscaled(p, s)

        pri, sec = [u(p) for p in ex["pri"]], [u(p) for p in ex["sec"]]

        def st(part: str) -> str:
            return f"[{parts[part]}]" if parts else ""
        length, width = axis_sizes(el)
        lines = [_bipole_path("L", pri[0], pri[1], s, prof, length, width, st('p'))]
        # secondary drawn upwards so its turns face the core
        halves = ["s1", "s2"] if len(sec) == 3 else ["s1"]
        for (a, b), h in zip(zip(sec, sec[1:]), halves):
            lines.append(_bipole_path("L", b, a, s, prof, length, width, st(h)))
        for a, b in ex["core_lines"]:
            lines.append(rf"\draw{st('core')} {_p(u(a))} -- {_p(u(b))};")
        for dot in ex.get("dots", []):
            lines.append(rf"\fill {_p(u(dot))} circle (1.2pt);")
        if "label_at" in ex:
            lines.append(rf"\node at {_p(u(ex['label_at']))} {{{el.label}}};")
        if "ratio_at" in ex:
            lines.append(rf"\node at {_p(u(ex['ratio_at']))} {{{el.opts['ratio']}}};")
        return "\n".join(lines) + "\n"
    raise ValueError(el.kind)


DIODE_SPAN = 0.223        # cm from the centre at transistors/scale 0.9, nmos and nigbt


def _split_switch(el: Element, s: Scale, prof: Profile, paint) -> str:
    """A switch conducting through only one of its two paths: drawn grey,
    then drawn again lit, clipped so that only that path (with the leads)
    shows.  "on" hides the diode, "diode" hides the channel and gate."""
    fp = footprint(el, s, prof)
    (cx, cy), u, side = fp.centre, fp.extra["u"], fp.extra["side"]
    length, width = axis_sizes(el)
    kl, kw = sw_k(prof) * length, sw_k(prof) * width
    # where the diode branch leaves the d-s line (measured, CircuitikZ 2023;
    # nearer the centre than the drain/source anchors)
    h, eps, big = DIODE_SPAN * kl, 0.03 * kw, 3.0

    def pt(a: float, b: float) -> str:      # (side, axis) offsets in cm
        return _p(unscaled((cx + side[0] * a + u[0] * b, cy + side[1] * a + u[1] * b), s))
    if paint.switch(el.id) == "on":         # hide the diode beside the d-s line
        a0, a1, b0, b1 = eps, big, -h - 0.05 * kl, h + 0.05 * kl
    else:                                   # hide the channel and the gate
        a0, a1, b0, b1 = -big, eps, -h + 0.02 * kl, h - 0.02 * kl
    # outer box one way round, the hidden box the other: a hole by the nonzero rule
    hole = (rf"\clip {pt(-big, -big)} -- {pt(big, -big)} -- {pt(big, big)} -- "
            rf"{pt(-big, big)} -- cycle {pt(a0, b0)} -- {pt(a0, b1)} -- {pt(a1, b1)} -- "
            rf"{pt(a1, b0)} -- cycle;" "\n")
    body = _element(el, s, prof, ink=paint.ink)
    return ("\\begin{scope}[" + paint.off + "]\n" + body + "\\end{scope}\n"
            # the grey copy already fixes the bounding box; the big clip must not
            + "\\begin{pgfinterruptboundingbox}\\begin{scope}\n" + hole
            + "\\begin{scope}[" + paint.element(el.id) + "]\n"
            + body + "\\end{scope}\n\\end{scope}\\end{pgfinterruptboundingbox}\n")


ARROW_ROT = {"right": 0, "up": 90, "left": 180, "down": 270}
TEXT_ANCHOR = {"above": "south", "below": "north", "left": "east", "right": "west"}


def sized(text: str, pt: float | None) -> str:
    """Text at its own font size (a local group), or as is at the default."""
    if not pt:
        return text
    return rf"{{\fontsize{{{_n(pt)}}}{{{_n(pt * 1.2)}}}\selectfont {text}}}"


def _annotation(a, prof: Profile, mono: bool = False) -> str:
    pt = font_of(a.size, prof) if a.size else None
    text = sized(a.text, pt)
    col = (", color=" + ink_tikz(a.color, mono)) if a.color and valid_ink(a.color) else ""
    if not a.arrow:
        return rf"\node[{col[2:]}] at {_p(a.at)} {{{text}}};" "\n" if col else \
            rf"\node at {_p(a.at)} {{{text}}};" "\n"
    shift = {"above": "yshift=.1cm", "below": "yshift=-.1cm",
             "left": "xshift=-.1cm", "right": "xshift=.1cm"}[a.side]
    return (rf"\node[currarrow, rotate={ARROW_ROT[a.arrow]}{col}] at {_p(a.at)} {{}};" "\n"
            rf"\node[anchor={TEXT_ANCHOR[a.side]}, inner sep=0pt{col}] at "
            rf"([{shift}]{_n(a.at[0])},{_n(a.at[1])}) {{{text}}};" "\n")


def _grid(layout: Layout, prof: Profile, s: Scale) -> str:
    """Light 0.1-unit grid, darker every 0.5, numbered every 0.5 along the
    bottom and left edges.  Editing aid only; never part of a paper figure."""
    import math
    b = extent(layout, s, prof)
    x0 = math.floor(b.x0 / s.x * 2) / 2 - 0.5
    x1 = math.ceil(b.x1 / s.x * 2) / 2 + 0.5
    y0 = math.floor(b.y0 / s.y * 2) / 2 - 0.5
    y1 = math.ceil(b.y1 / s.y * 2) / 2 + 0.5
    rect = f"({_n(x0)},{_n(y0)}) grid ({_n(x1)},{_n(y1)})"
    lines = ["% ---- coordinate grid (editing aid) ----",
             rf"\draw[black!8, line width=0.2pt, step=0.1] {rect};",
             rf"\draw[black!25, line width=0.3pt, step=0.5] {rect};"]
    n = round((x1 - x0) * 2)
    for i in range(n + 1):
        x = x0 + i / 2
        lines.append(rf"\node[black!55, font=\tiny, below] at ({_n(x)},{_n(y0)}) {{{_n(x)}}};")
    n = round((y1 - y0) * 2)
    for i in range(n + 1):
        y = y0 + i / 2
        lines.append(rf"\node[black!55, font=\tiny, left] at ({_n(x0)},{_n(y)}) {{{_n(y)}}};")
    return "\n".join(lines) + "\n"
