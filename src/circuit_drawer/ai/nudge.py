"""Tiny, verified spacing fixes on a model-written table.

Only one thing is repaired: a two-terminal part whose pins are a hair too
close (``too-short`` by at most MAX_NUDGE units).  One pin is moved outward
along the part's own axis, together with the wire vertices sitting on it;
the whole table is then re-validated and the change is kept only if it
removes errors without adding any.  Everything else stays the model's job,
so this is not a layout engine - it never decides where a part goes.
"""

from __future__ import annotations

import copy
import math
import re

from ..layout import TWO_TERMINAL, Layout
from ..profiles import Profile
from ..validate import check

MAX_NUDGE = 0.1     # layout units
GRID = 0.05
_SHORT = re.compile(r"^(\S+): pin distance .* by at least ([\d.]+) units")


def _same(p, q) -> bool:
    return abs(p[0] - q[0]) < 1e-6 and abs(p[1] - q[1]) < 1e-6


def _shift(lay: Layout, pin_at, delta) -> bool:
    """Move every wire vertex at ``pin_at`` by ``delta``; a vertex whose next
    segment would turn diagonal drags its neighbour along.  False when
    another part's pin sits there (that part would move too)."""
    dx, dy = delta
    for w in lay.wires:
        for i, p in enumerate(w):
            if not _same(p, pin_at):
                continue
            w[i] = (round(p[0] + dx, 4), round(p[1] + dy, 4))
            for j in (i - 1, i + 1):
                if 0 <= j < len(w):
                    q = w[j]
                    # segment was perpendicular to the move: keep it orthogonal
                    if (dx and abs(q[0] - p[0]) < 1e-6) or (dy and abs(q[1] - p[1]) < 1e-6):
                        w[j] = (round(q[0] + dx, 4), round(q[1] + dy, 4))
    return True


def nudge(layout: Layout, prof: Profile) -> tuple[Layout, list[str]]:
    """Return a (possibly) improved copy and a note per applied fix."""
    lay = layout
    notes: list[str] = []
    for _ in range(10):                       # each pass fixes at most one part
        rep = check(lay, prof)
        errors = {str(i) for i in rep.errors}
        best = None
        for issue in rep.errors:
            m = _SHORT.match(issue.message) if issue.code == "too-short" else None
            if not m:
                continue
            need = float(m.group(2))
            if need > MAX_NUDGE + 1e-9:
                continue
            first = max(GRID, math.ceil(need / GRID - 1e-9) * GRID)
            el = lay.element(m.group(1))
            if el.kind not in TWO_TERMINAL:
                continue
            a, b = el.pins["a"], el.pins["b"]
            horizontal = abs(a[1] - b[1]) < 1e-6
            # the reported gap is rounded, so also try one grid step more
            for step, (pin, other) in ((st, po) for st in (first, first + GRID)
                                       for po in (("b", a), ("a", b))):
                p = el.pins[pin]
                if any(e is not el and any(_same(q, p) for q in e.pins.values())
                       for e in lay.elements):
                    continue
                sign = 1 if (p[0] - other[0] if horizontal else p[1] - other[1]) > 0 else -1
                delta = (sign * step, 0.0) if horizontal else (0.0, sign * step)
                cand = copy.deepcopy(lay)
                cand.element(el.id).pins[pin] = (round(p[0] + delta[0], 4),
                                                 round(p[1] + delta[1], 4))
                _shift(cand, p, delta)
                new = {str(i) for i in check(cand, prof).errors}
                if new < errors and (best is None or len(new) < len(best[1])):
                    best = (cand, new, f"{el.id}.{pin}: {p} -> {cand.element(el.id).pins[pin]}")
        if best is None:
            break
        lay = best[0]
        notes.append(best[2])
    return lay, notes
