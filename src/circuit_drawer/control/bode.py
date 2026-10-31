"""Bode plot of the loop gains and closed-loop responses, as a standalone
TikZ figure in the circuit figures' style: the same preamble (Times, 8 pt),
line width and trace colours as the waveform charts.

Magnitude on top, phase below, one shared logarithmic frequency axis.  Each
loop gain's legend entry carries its crossover and margins, and a dashed
line marks the crossover in both panels.
"""

from __future__ import annotations

import math

from ..emit import preamble
from ..geometry import has_wide
from ..profiles import Profile
from ..waveforms import COLORS
from .loop import eng

MAG_H, PHASE_H, GAP = 2.6, 2.2, 0.3   # cm: panel heights and the gap between them
LEFT = 1.05                            # cm left of the axes for tick labels and the axis title
RIGHT = 0.25                           # cm right of the axes, for the last decade label
LEGEND_ROW = 0.36                      # cm per legend line (8 pt type plus leading)
MAG_CLIP = (-160.0, 160.0)             # dB: beyond this an integrator's sweep end is noise
MAG_STEPS = (10, 20, 40, 60)           # dB per grid line, the first giving at most MAX_TICKS
PHASE_STEPS = (30, 45, 90, 180)
MAX_TICKS = 7
# deg: a sampling delay keeps turning the phase; the margins live above this
PHASE_FLOOR = -450.0
# black-and-white print tells the traces apart by dash pattern
DASHES = ("solid", "dashed", "densely dotted", "dash dot", "loosely dashed", "dash dot dot")


def _n(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".") if abs(v) > 1e-9 else "0"


def _range(vals: list[float], steps: tuple[int, ...]) -> tuple[float, float, int]:
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        lo, hi = lo - 1, hi + 1
    for step in steps:
        a, b = math.floor(lo / step) * step, math.ceil(hi / step) * step
        if (b - a) / step <= MAX_TICKS:
            return a, b, step
    step = steps[-1]
    while (math.ceil(hi / step) - math.floor(lo / step)) > MAX_TICKS:
        step *= 2
    return math.floor(lo / step) * step, math.ceil(hi / step) * step, step


def bode_tex(curves: list[dict], freq: tuple[float, float], prof: Profile,
             mono: bool = False) -> str:
    lo, hi = freq
    width = prof.width_cm - LEFT - RIGHT
    lx0, lx1 = math.log10(lo), math.log10(hi)
    x = lambda f: (math.log10(f) - lx0) / (lx1 - lx0) * width

    mags = [max(MAG_CLIP[0], min(MAG_CLIP[1], m)) for c in curves for m in c["mag"]]
    phases = [p for c in curves for p in c["phase"]]
    m0, m1, mstep = _range(mags + [0.0], MAG_STEPS)
    p0, p1, pstep = _range([max(PHASE_FLOOR, p) for p in phases], PHASE_STEPS)
    # traces beyond the axes run on and are cut by the panel's clip
    ym = lambda v: (max(2 * MAG_CLIP[0], min(2 * MAG_CLIP[1], v)) - m0) / (m1 - m0) * MAG_H
    yp = lambda v: (max(4 * PHASE_FLOOR, v) - p0) / (p1 - p0) * PHASE_H
    top = 0.0                                   # the magnitude panel spans [top, top - MAG_H]
    pbase = -(MAG_H + GAP + PHASE_H)            # phase panel bottom

    labels = [c["label"] for c in curves]
    cjk = any(has_wide(t) for t in labels)
    out = [preamble(prof, cjk)]
    out.append(f"% Bode plot: {len(curves)} curve(s), {eng(lo)}Hz .. {eng(hi)}Hz\n")
    out.append(rf"\begin{{tikzpicture}}[x=1cm, y=1cm, line width={prof.line_pt}pt, "
               r"line join=round]" "\n")
    for i, c in enumerate(curves):
        col = COLORS[i % len(COLORS)]
        out.append(rf"\definecolor{{trace{i}}}{{HTML}}{{{col[1:]}}}" "\n")

    def style(i: int) -> str:
        return f"black, {DASHES[i % len(DASHES)]}" if mono else f"trace{i}"

    for name, base, h, v0, v1, step, yv, title in (
            ("mag", -MAG_H, MAG_H, m0, m1, mstep, ym, "Magnitude (dB)"),
            ("phase", pbase, PHASE_H, p0, p1, pstep, yp, r"Phase ($^{\circ}$)")):
        out.append(rf"\begin{{scope}}[yshift={_n(base)}cm]" "\n")
        # decade and in-decade grid
        for dec in range(math.floor(lx0), math.ceil(lx1) + 1):
            for k in range(1, 10):
                f = k * 10 ** dec
                if lo <= f <= hi:
                    shade = "gray!30" if k == 1 else "gray!12"
                    out.append(rf"\draw[{shade}, line width=.25pt] ({_n(x(f))},0) -- "
                               rf"({_n(x(f))},{_n(h)});" "\n")
        v = v0
        while v <= v1 + 1e-9:
            y = yv(v)
            out.append(rf"\draw[gray!30, line width=.25pt] (0,{_n(y)}) -- ({_n(width)},{_n(y)});"
                       "\n")
            out.append(rf"\node[anchor=east, inner sep=1.5pt] at (0,{_n(y)}) {{{v:g}}};" "\n")
            v += step
        # the stability references: 0 dB and -180 deg (and its odd multiples)
        refs = [0.0] if name == "mag" else [-180.0 + 360 * k for k in range(-3, 4)]
        for r in refs:
            if v0 <= r <= v1:
                out.append(rf"\draw[black!60, densely dashed, line width=.35pt] (0,{_n(yv(r))}) -- "
                           rf"({_n(width)},{_n(yv(r))});" "\n")
        out.append(rf"\node[rotate=90, anchor=south] at (-{_n(LEFT - 0.12)},{_n(h / 2)}) "
                   rf"{{{title}}};" "\n")
        out.append(rf"\begin{{scope}}\clip (0,0) rectangle ({_n(width)},{_n(h)});" "\n")
        for i, c in enumerate(curves):
            key = "mag" if name == "mag" else "phase"
            pts = " -- ".join(f"({_n(x(f))},{_n(yv(val))})" for f, val in zip(c["f"], c[key]))
            out.append(rf"\draw[{style(i)}, line width={prof.line_pt}pt] {pts};" "\n")
        out.append(r"\end{scope}" "\n")
        for i, c in enumerate(curves):
            fc = c.get("fc")
            if c.get("loop") and fc and lo <= fc <= hi:
                col = "black" if mono else f"trace{i}"
                out.append(rf"\draw[{col}, densely dashed, line width=.35pt] ({_n(x(fc))},0) -- "
                           rf"({_n(x(fc))},{_n(h)});" "\n")
        out.append(rf"\draw[line width=.35pt] (0,0) rectangle ({_n(width)},{_n(h)});" "\n")
        out.append(r"\end{scope}" "\n")

    # crossover dots on the 0 dB line
    for i, c in enumerate(curves):
        fc = c.get("fc")
        if c.get("loop") and fc and lo <= fc <= hi and m0 <= 0 <= m1:
            col = "black" if mono else f"trace{i}"
            out.append(rf"\fill[{col}] ({_n(x(fc))},{_n(-MAG_H + ym(0))}) circle "
                       rf"({_n(prof.dot_pt)}pt);" "\n")
    # frequency ticks under the phase panel
    for dec in range(math.ceil(lx0 - 1e-9), math.floor(lx1 + 1e-9) + 1):
        f = 10 ** dec
        out.append(rf"\node[anchor=north, inner sep=2pt] at ({_n(x(f))},{_n(pbase)}) "
                   rf"{{$10^{{{dec}}}$}};" "\n")
    out.append(rf"\node[anchor=north] at ({_n(width / 2)},{_n(pbase - 0.38)}) "
               r"{Frequency (Hz)};" "\n")
    # legend above the magnitude panel, one entry per line
    for row, (i, c) in enumerate(reversed(list(enumerate(curves)))):
        y = top + 0.22 + row * LEGEND_ROW
        text = c["label"]
        if c.get("loop"):
            parts = []
            if c.get("fc"):
                parts.append(rf"$f_{{c}}$ = {eng(c['fc'])}Hz, PM = {c['pm']:.1f}$^{{\circ}}$")
            else:
                parts.append("no crossover")
            if c.get("gm") is not None:
                parts.append(f"GM = {c['gm']:.1f} dB")
            text += ": " + ", ".join(parts)
        out.append(rf"\draw[{style(i)}, line width={prof.line_pt}pt] (0.05,{_n(y)}) -- "
                   rf"(0.55,{_n(y)});" "\n")
        out.append(rf"\node[anchor=west, inner sep=1pt] at (0.62,{_n(y)}) {{{text}}};" "\n")
    out.append("\\end{tikzpicture}\n" + ("\\end{CJK}\n" if cjk else "") + "\\end{document}\n")
    return "".join(out)
