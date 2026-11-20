"""Tiny, verified spacing fixes on a model-written table.

For ordinary drawings this repairs a two-terminal part whose pins are a hair too
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
from ..schema import LABEL_SIDES
from ..validate import check

MAX_NUDGE = 0.1     # layout units
GRID = 0.05
_SHORT = re.compile(r"^(\S+): pin distance .* by at least ([\d.]+) units")

# At most eight four-sided labels per model reply. Large drawings can still
# report the other conflicts for the next correction round.
PORT_LABEL_TRIALS = 32
_LABEL_COLLISIONS = {"text-on-line", "text-on-symbol", "text-overlap"}
_OWNER = re.compile(r"label of ([A-Za-z][A-Za-z0-9_/-]*)(?=\s|（|$)")
_DEFINITION = re.compile(r"^模块定义 ([A-Za-z][A-Za-z0-9_-]*)：")


def nudge_module_port_labels(layout, prof, report, should_stop):
    """Prefer a legible side for generated public ports when the drawing permits it."""
    spec = layout.extra["modules"]
    notes = []
    trials = 0
    while report.ok:
        collisions = [i for i in report.issues if i.code in _LABEL_COLLISIONS]
        if not collisions:
            break
        definitions = spec.get("definitions", {})
        instances = {inst["id"]: inst["definition"] for inst in spec["instances"]}
        targets = set()
        for issue in collisions:
            definition = _DEFINITION.match(issue.message)
            for ref in _OWNER.findall(issue.message):
                instance, sep, port = ref.partition("/")
                if sep and instance in instances:
                    targets.add((instances[instance], port))
                elif definition and not sep:
                    targets.add((definition[1], ref))
        previous = set(map(str, collisions))
        for name, port in sorted(targets):
            definition = definitions.get(name, {})
            if port + ".p" not in definition.get("ports", {}).values():
                continue
            elements = definition["layout"]["elements"]
            index = next(i for i, el in enumerate(elements) if el["id"] == port)
            for side in LABEL_SIDES:
                if side == elements[index].get("label_pos"):
                    continue
                if trials >= PORT_LABEL_TRIALS or should_stop():
                    return layout, report, notes
                trial = copy.deepcopy(layout)
                trial.extra["modules"]["definitions"][name]["layout"]["elements"][index]["label_pos"] = side
                trials += 1
                after = check(trial, prof)
                if after.ok and {str(i) for i in after.issues if i.code in _LABEL_COLLISIONS} < previous:
                    layout, report, spec = trial, after, trial.extra["modules"]
                    notes.append(f"{name}/{port}: label_pos → {side}")
                    break
            else:
                continue
            break
        else:
            break
    return layout, report, notes


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


def fit_candidate_labels(source, prof):
    """Offer a checked paper variant. Only label positions on flat source elements may change."""
    from ..geometry import footprint
    if source.get("modules"):
        return None
    lay = Layout.from_dict(source)
    report = check(lay, prof)
    best = copy.deepcopy(source)

    def conflicts(result):
        return [i for i in result.issues if i.level == "error" or i.code in _LABEL_COLLISIONS]

    # Limit interactive work to three labels and two lines of additional clearance per label.
    budget = 96
    for _ in range(3):
        issues = conflicts(report)
        if not issues:
            return best
        if any(i.code not in _LABEL_COLLISIONS | {"too-short", "too-cramped", "winding-gap"} for i in issues):
            return None
        owners = list(dict.fromkeys(ref for i in issues for ref in _OWNER.findall(i.message)))
        # A label outside the pin envelope can squeeze an entirely different component.
        xs = [p[0] * report.scale.x for e in lay.elements for p in e.pins.values()]
        if not xs:
            return None
        edges = []
        for e in lay.elements:
            if not e.label:
                continue
            for box in footprint(e, report.scale, prof).labels:
                edges.append((max(min(xs)-box.x0, box.x1-max(xs)), e.id))
        owners.extend(ref for margin, ref in sorted(edges, reverse=True) if margin > 0 and ref not in owners)

        def score(r):
            return ("labels" not in r.checked_stages, len(r.errors), len(conflicts(r)), -round(r.scale.x, 4))

        best_score, winner = score(report), None
        for ref in owners:
            for side, direction in (("below", (0,-1)), ("above", (0,1)), ("left", (-1,0)), ("right", (1,0))):
                for lines in (0, 1, 2):
                    if not budget:
                        break
                    trial = copy.deepcopy(best)
                    part = next((e for e in trial["elements"] if e["id"] == ref), None)
                    if not part:
                        break
                    opts = part.setdefault("opts", {})
                    part["label_pos"] = side
                    opts.pop("label_offset", None)
                    if lines:
                        height = float(opts.get("label_size", prof.font_pt)) * 2.54 / 72.27
                        opts["label_offset"] = [round(lines * d * height / scale, 3) for d,scale
                                                in zip(direction, (report.scale.x, report.scale.y))]
                    if not opts:
                        part.pop("opts", None)
                    budget -= 1
                    candidate = Layout.from_dict(trial)
                    tested = check(candidate, prof)
                    if not conflicts(tested):
                        return trial
                    if score(tested) < best_score:
                        winner, best_score = (trial, candidate, tested), score(tested)
            if winner:
                break
        if not winner:
            return None
        best, lay, report = winner
    return best if report.ok else None
