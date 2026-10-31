"""Control diagram -> standalone TikZ document, in the circuit figures' style:
the same preamble (Times, 8 pt), line width, arrowheads, dots and region
tints.  Coordinates are written verbatim in layout units; ``x``/``y``
carry the scale.  Everything is drawn from the checker's ``Geometry``.
"""

from __future__ import annotations

import math

from ..emit import _region, preamble
from ..geometry import has_wide, unscaled
from ..profiles import Profile
from ..regions import LINE_HEAD, ink_tikz
from .check import CONTACT_R, SUM_R, TOGGLE_FRAC, Label, Report
from .model import CIRCLE_TYPES, TAG_STYLE, Block, Diagram, block_fill

CROSS = 0.62           # half-size of the x in a multiplier, as a fraction of the radius
DIV_BAR = 0.6          # half-length of the divider's bar, fraction of the radius
DIV_DOT = (0.45, 0.09)  # divider dots: distance from centre and dot radius, fractions
LIMIT_AXES = 0.36      # limiter axes reach this fraction of the box from its centre
LIMIT_KNEE = (0.16, 0.24)   # limiter knee (x, y) as fractions of the box size
LIMIT_FLAT = 0.34      # limiter flat runs end this fraction of the box from the centre
TAG_FRAME = 0.6        # tag frame width, as a fraction of the line width


def _n(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".") if abs(v) > 1e-9 else "0"


def _p(p) -> str:
    return f"({_n(p[0])},{_n(p[1])})"


def _rgb(hex_: str) -> str:
    r, g, b = (int(hex_[i:i + 2], 16) for i in (1, 3, 5))
    return f"{{rgb,255:red,{r};green,{g};blue,{b}}}"


def _font(pt: float) -> str:
    return rf"\fontsize{{{_n(pt)}}}{{{_n(pt * 1.2)}}}\selectfont"


def _texts(dia: Diagram) -> list[str]:
    out = [b.text or "" for b in dia.blocks] + [b.label or "" for b in dia.blocks]
    out += [lk.label or "" for lk in dia.links] + [t.text for t in dia.texts]
    out += [r.label or "" for r in dia.regions]
    return out


def emit(rep: Report, prof: Profile, mono: bool = False, grid: bool = False) -> str:
    dia, g = rep.diagram, rep.geometry
    s = g.scale
    cjk = any(has_wide(t) for t in _texts(dia))
    out = [preamble(prof, cjk).replace(r"\usepackage{circuitikz}",
                                       "\\usepackage{circuitikz}\n\\usetikzlibrary{arrows.meta}")]
    out.append(f"% {dia.name}: control diagram, 1 layout unit = {_n(g.s)} cm ({prof.name})\n")
    out.append(rf"\begin{{tikzpicture}}[x={_n(g.s)}cm, y={_n(g.s)}cm, "
               rf"line width={prof.line_pt}pt, line join=round]" "\n")
    if grid:
        out.append(_grid(rep))
    if dia.regions:
        out.append("% ---- regions (background) ----\n")
        out += [_region(r, s, prof, mono) for r in dia.regions]
    out.append("% ---- links ----\n")
    blocks = {b.id: b for b in dia.blocks}
    for lk, path in zip(dia.links, g.paths):
        out.append(_link(lk, path, blocks, g.s, mono))
    if g.dots:
        out.append("% ---- branch dots ----\n")
        out += [rf"\fill {_p(p)} circle ({_n(prof.dot_pt)}pt);" "\n" for p in g.dots]
    for b in dia.blocks:
        out.append(f"% ---- {b.id} ({b.type}) ----\n")
        out.append(_block(b, g.s, prof, mono))
    if g.signs:
        out.append("% ---- signs ----\n")
        sign_labels = (lb for lb in g.labels if lb.owner.endswith(" sign"))
        for (at, sign), lb in zip(g.signs, sign_labels):
            col = f", text={ink_tikz(lb.color, mono)}" if lb.color else ""
            out.append(rf"\node[inner sep=0pt{col}] at {_p(unscaled(at, s))} "
                       rf"{{${'-' if sign == '-' else '+'}$}};" "\n")
    out.append("% ---- text ----\n")
    out += [_label(lb, g.s, mono) for lb in g.labels if not lb.owner.endswith(" sign")]
    out.append("\\end{tikzpicture}\n" + ("\\end{CJK}\n" if cjk else "") + "\\end{document}\n")
    return "".join(out)


def _label(lb: Label, s: float, mono: bool) -> str:
    c = ((lb.box.x0 + lb.box.x1) / 2 / s, (lb.box.y0 + lb.box.y1) / 2 / s)
    col = f", text={ink_tikz(lb.color, mono)}" if lb.color else ""
    return (rf"\node[inner sep=0pt, align=center, font={_font(lb.pt)}{col}] at {_p(c)} "
            rf"{{{lb.text}}};" "\n")


def _link(lk, path, blocks: dict[str, Block], s: float, mono: bool = False) -> str:
    if not path:
        return ""
    path = list(path)
    # a line meets a switch contact at its circle, not its centre
    for idx, end, nxt in ((0, lk.start, 1), (-1, lk.end, -2)):
        b = blocks.get(end.block) if end.block else None
        if b is not None and b.type == "switch":
            p, q = path[idx], path[nxt]
            d = math.dist(p, q)
            k = CONTACT_R / s / d
            path[idx] = (p[0] + (q[0] - p[0]) * k, p[1] + (q[1] - p[1]) * k)
    tip = rf"Stealth[length={_n(LINE_HEAD)}cm]"
    arrow = {"end": f"-{{{tip}}}", "start": f"{{{tip}}}-", "both": f"{{{tip}}}-{{{tip}}}",
             "none": ""}[lk.arrow]
    opts = [arrow] if arrow else []
    if lk.color:
        opts.append(f"draw={ink_tikz(lk.color, mono)}")
    if lk.dash != "solid":
        opts.append(lk.dash)
    opt = f"[{', '.join(opts)}]" if opts else ""
    return rf"\draw{opt} " + " -- ".join(_p(p) for p in path) + ";\n"


def _fill_tex(value: str, mono: bool) -> str:
    color = block_fill(value, mono)
    return "none" if color == "none" else _rgb(color)


def _block(b: Block, s: float, prof: Profile, mono: bool) -> str:
    fill = _fill_tex(b.fill, mono) if b.fill is not None else "white"
    edge = f", draw={ink_tikz(b.border, mono)}" if b.border else ""
    stroke = f"[{edge[2:]}]" if edge else ""
    if b.type in CIRCLE_TYPES:
        c, r = _p(b.at), f"{_n(SUM_R)}cm"
        out = rf"\draw[fill={fill}{edge}] {c} circle ({r});" "\n"
        if b.type == "mul":
            k = SUM_R * CROSS * math.cos(math.pi / 4)
            out += (rf"\draw{stroke} {c} ++(-{_n(k)}cm,-{_n(k)}cm) -- ++({_n(2 * k)}cm,{_n(2 * k)}cm) "
                    rf"{c} ++(-{_n(k)}cm,{_n(k)}cm) -- ++({_n(2 * k)}cm,-{_n(2 * k)}cm);" "\n")
        elif b.type == "div":
            bar, (dd, dr) = SUM_R * DIV_BAR, DIV_DOT
            dots = f"[fill={ink_tikz(b.border, mono)}]" if b.border else ""
            out += (rf"\draw{stroke} {c} ++(-{_n(bar)}cm,0) -- ++({_n(2 * bar)}cm,0);" "\n"
                    rf"\fill{dots} {c} ++(0,{_n(SUM_R * dd)}cm) circle ({_n(SUM_R * dr)}cm) "
                    rf"{c} ++(0,-{_n(SUM_R * dd)}cm) circle ({_n(SUM_R * dr)}cm);" "\n")
        return out
    if b.type == "switch":
        return _switch(b, s, prof, mono)
    x0, y0, x1, y1 = b.rect()
    rect = f"{_p((x0, y0))} rectangle {_p((x1, y1))}"
    if b.type == "tag":
        fill, border, _ = TAG_STYLE[b.style]
        fill = b.fill or fill
        border = b.border or border
        fill_tex = _fill_tex(fill, mono)
        return (rf"\filldraw[fill={fill_tex}, draw={ink_tikz(border, mono)}, "
                rf"line width={_n(prof.line_pt * TAG_FRAME)}pt] {rect};" "\n")
    out = rf"\draw[fill={fill}{edge}] {rect};" "\n"
    if b.type == "limiter":
        (cx, cy), (w, h) = b.at, b.size
        ax, ay = w * LIMIT_AXES, h * LIMIT_AXES
        thin = f"line width={_n(prof.line_pt * 0.5)}pt"
        out += (rf"\draw[{thin}{edge}] {_p((cx - ax, cy))} -- {_p((cx + ax, cy))} "
                rf"{_p((cx, cy - ay))} -- {_p((cx, cy + ay))};" "\n")
        kx, ky = w * LIMIT_KNEE[0], h * LIMIT_KNEE[1]
        fx = w * LIMIT_FLAT
        out += (rf"\draw{stroke} {_p((cx - fx, cy - ky))} -- {_p((cx - kx, cy - ky))} -- "
                rf"{_p((cx + kx, cy + ky))} -- {_p((cx + fx, cy + ky))};" "\n")
    return out


def _switch(b: Block, s: float, prof: Profile, mono: bool) -> str:
    fill = _fill_tex(b.fill, mono) if b.fill is not None else "white"
    edge = f", draw={ink_tikz(b.border, mono)}" if b.border else ""
    stroke = f"[{edge[2:]}]" if edge else ""
    on = b.throws[b.on]
    d = math.dist(b.at, on)
    k = CONTACT_R / s / d
    a = (b.at[0] + (on[0] - b.at[0]) * k, b.at[1] + (on[1] - b.at[1]) * k)
    z = (on[0] - (on[0] - b.at[0]) * k, on[1] - (on[1] - b.at[1]) * k)
    out = rf"\draw{stroke} {_p(a)} -- {_p(z)};" "\n"
    if b.toggle:
        angles = [math.degrees(math.atan2(q[1] - b.at[1], q[0] - b.at[0]))
                  for q in b.throws.values()]
        a0, a1 = angles[0], angles[1]
        if a1 - a0 > 180:
            a1 -= 360
        elif a0 - a1 > 180:
            a1 += 360
        r = TOGGLE_FRAC * min(math.dist(b.at, q) for q in b.throws.values()) * s
        col = ink_tikz(b.color or "red", mono)
        tip = rf"Stealth[length={_n(LINE_HEAD * 0.8)}cm]"
        out += (rf"\draw[{{{tip}}}-{{{tip}}}, draw={col}, line width={_n(prof.line_pt * 0.8)}pt] "
                rf"{_p(b.at)} ++({_n(a0)}:{_n(r)}cm) arc[start angle={_n(a0)}, "
                rf"end angle={_n(a1)}, radius={_n(r)}cm];" "\n")
    for p in [b.at, *b.throws.values()]:
        out += rf"\draw[fill={fill}{edge}] {_p(p)} circle ({_n(CONTACT_R)}cm);" "\n"
    return out


def _grid(rep: Report) -> str:
    """Light 0.1-unit grid, numbered every 0.5: an editing aid only."""
    g = rep.geometry
    pts = [p for path in g.paths if path for p in path]
    pts += [(bx.x0 / g.s, bx.y0 / g.s) for bx in g.bodies.values()]
    pts += [(bx.x1 / g.s, bx.y1 / g.s) for bx in g.bodies.values()]
    if not pts:
        return ""
    x0 = math.floor(min(p[0] for p in pts) * 2) / 2 - 0.5
    x1 = math.ceil(max(p[0] for p in pts) * 2) / 2 + 0.5
    y0 = math.floor(min(p[1] for p in pts) * 2) / 2 - 0.5
    y1 = math.ceil(max(p[1] for p in pts) * 2) / 2 + 0.5
    rect = f"({_n(x0)},{_n(y0)}) grid ({_n(x1)},{_n(y1)})"
    lines = ["% ---- coordinate grid (editing aid) ----",
             rf"\draw[black!8, line width=0.2pt, step=0.1] {rect};",
             rf"\draw[black!25, line width=0.3pt, step=0.5] {rect};"]
    for i in range(round((x1 - x0) * 2) + 1):
        x = x0 + i / 2
        lines.append(rf"\node[black!55, font=\tiny, below] at ({_n(x)},{_n(y0)}) {{{_n(x)}}};")
    for i in range(round((y1 - y0) * 2) + 1):
        y = y0 + i / 2
        lines.append(rf"\node[black!55, font=\tiny, left] at ({_n(x0)},{_n(y)}) {{{_n(y)}}};")
    return "\n".join(lines) + "\n"
