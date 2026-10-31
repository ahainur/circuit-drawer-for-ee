"""Modal figures: one coordinate table, restyled once per operating mode.

A mode says which switches and diodes conduct and, usually, the current
loops as ordered lists of branches.  Nothing is moved: the conducting
elements and the wire runs between their pins are drawn heavy and in colour,
everything else light grey, and arrows mark the current direction.

Mode format (a dict, as stored in a coordinate table's ``modes`` list)::

    {"name": "Mode 1", "caption": "$t_0$--$t_1$",
     "on": ["S1", "S4", "D1"],                       # optional with loops
     "loops": [["Vin", "S1", "Cr", "Lr", "T:p|Lm", "S4"],
               ["T:s1", "D1", "Co|RL"]],
     "arrows": [{"at": [1.6, 2.5], "dir": "right"}]}  # optional, replaces auto arrows

Branch references in a loop:

- ``X``: element X.  ``S2:diode``: switch S2 conducting through its diode
  (drawn like an on switch; the name records the state).
- ``T:p`` / ``T:s``: primary / whole secondary winding of transformer T;
  ``T:s1`` (s1 to sct) and ``T:s2`` (sct to s2) for a centre tap.
- ``A|B``: A and B in parallel between the same two nets.

A loop is read in the order written: current leaves each branch into the
next one, and the last branch feeds the first.  When the order alone does
not fix the sense (a loop of two parallel parts), the first branch is taken
from its first pin to its second (a->b, d->s, c->e, p1->p2).

Without ``loops`` the conducting set is found from ``on``: every element
that lies on a closed path of passive parts, windings and on devices is lit.
That mode has no arrows, since a direction is not determined by switch
states alone.
"""

from __future__ import annotations

import heapq
import math
import re
from dataclasses import dataclass, field

from .geometry import EPS as CM_EPS
from .geometry import Box, centred, scaled
from .layout import SWITCHES, Layout, Point, branch_pins
from .profiles import Profile
from .validate import Report, footprints

TOL = 1e-6


class ModeError(ValueError):
    pass


@dataclass
class Mode:
    name: str
    caption: str = ""
    on: list[str] | None = None
    loops: list[list[str]] = field(default_factory=list)
    arrows: list[dict] | None = None
    condition: str = ""        # operating point the mode belongs to, e.g. "$f_s < f_r$"

    @classmethod
    def from_dict(cls, d: dict) -> "Mode":
        loops = d.get("loops") or []
        if isinstance(loops, list) and loops and isinstance(loops[0], str):
            loops = [loops]
        return cls(name=str(d.get("name", "mode")), caption=str(d.get("caption", "")),
                   on=list(d["on"]) if d.get("on") is not None else None,
                   loops=[list(l) for l in loops], arrows=d.get("arrows"),
                   condition=str(d.get("condition", "")))

    def to_dict(self) -> dict:
        d: dict = {"name": self.name}
        if self.caption:
            d["caption"] = self.caption
        if self.on is not None:
            d["on"] = list(self.on)
        if self.loops:
            d["loops"] = [list(l) for l in self.loops]
        if self.arrows is not None:
            d["arrows"] = self.arrows
        if self.condition:
            d["condition"] = self.condition
        return d


@dataclass
class Lit:
    """What one mode lights up."""
    parts: dict[str, set[str]] = field(default_factory=dict)  # element -> parts ("" = all)
    edges: set[int] = field(default_factory=set)               # WireGraph edge ids
    arrows: list[tuple[Point, float]] = field(default_factory=list)  # point, angle (deg)
    arrow_loops: list[int] = field(default_factory=list)       # loop index of each arrow
    channel: set[str] = field(default_factory=set)             # switches on through the channel
    diode: set[str] = field(default_factory=set)               # switches on through their diode
    problems: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# wire graph: every wire split at its corners, T joints and pins
# ---------------------------------------------------------------------------
def _k(p: Point) -> tuple[float, float]:
    return (round(p[0], 4), round(p[1], 4))


def _interior(pt: Point, p: Point, q: Point) -> bool:
    dx, dy = q[0] - p[0], q[1] - p[1]
    L2 = dx * dx + dy * dy
    if L2 < TOL:
        return False
    if abs((pt[0] - p[0]) * dy - (pt[1] - p[1]) * dx) / math.sqrt(L2) > 1e-4:
        return False
    t = ((pt[0] - p[0]) * dx + (pt[1] - p[1]) * dy) / L2
    return 1e-6 < t < 1 - 1e-6


@dataclass
class Edge:
    wire: int
    a: tuple[float, float]
    b: tuple[float, float]
    length: float


class WireGraph:
    def __init__(self, layout: Layout):
        stops = {_k(p) for w in layout.wires for p in w}
        stops |= {_k(p) for el in layout.elements for p in el.pins.values()}
        self.edges: list[Edge] = []
        self.runs: list[list[int]] = []      # wire index -> its edge ids in order
        self.adj: dict[tuple, list[tuple[tuple, int]]] = {}
        for i, w in enumerate(layout.wires):
            run = []
            for p, q in zip(w, w[1:]):
                inner = [s for s in stops if _interior(s, p, q)]
                dx, dy = q[0] - p[0], q[1] - p[1]
                inner.sort(key=lambda s: (s[0] - p[0]) * dx + (s[1] - p[1]) * dy)
                pts = [_k(p)] + inner + [_k(q)]
                for a, b in zip(pts, pts[1:]):
                    if a == b:
                        continue
                    e = len(self.edges)
                    self.edges.append(Edge(i, a, b, math.dist(a, b)))
                    self.adj.setdefault(a, []).append((b, e))
                    self.adj.setdefault(b, []).append((a, e))
                    run.append(e)
            self.runs.append(run)

    def tree(self, terminals: list[Point]) -> set[int] | None:
        """Edges of a short tree joining all terminals (grown one nearest
        terminal at a time), or None when some terminal is not reachable."""
        want = list(dict.fromkeys(_k(t) for t in terminals))
        if len(want) < 2:
            return set()
        nodes = {want[0]}
        edges: set[int] = set()
        rest = set(want[1:])
        while rest:
            dist = {n: 0.0 for n in nodes}
            back: dict = {}
            heap = [(0.0, n) for n in nodes]
            heapq.heapify(heap)
            hit = None
            while heap:
                d, n = heapq.heappop(heap)
                if d > dist.get(n, math.inf):
                    continue
                if n in rest:
                    hit = n
                    break
                for m, e in self.adj.get(n, []):
                    nd = d + self.edges[e].length
                    if nd < dist.get(m, math.inf) - 1e-12:
                        dist[m] = nd
                        back[m] = (n, e)
                        heapq.heappush(heap, (nd, m))
            if hit is None:
                return None
            n = hit
            while n not in nodes:
                prev, e = back[n]
                edges.add(e)
                nodes.add(n)
                n = prev
            rest.discard(hit)
        return edges

    def orient(self, edges: set[int], sources: list[Point], sinks: list[Point]
               ) -> dict[int, int]:
        """For each tree edge, +1 if current runs a->b, -1 if b->a, judged by
        which side of the edge the sources and sinks are; edges with sources
        or sinks on both sides are left out."""
        src, snk = {_k(p) for p in sources}, {_k(p) for p in sinks}
        adj: dict = {}
        for e in edges:
            E = self.edges[e]
            adj.setdefault(E.a, []).append((E.b, e))
            adj.setdefault(E.b, []).append((E.a, e))
        out = {}
        for e in edges:
            E = self.edges[e]
            side, todo = {E.a}, [E.a]
            while todo:
                n = todo.pop()
                for m, f in adj.get(n, []):
                    if f != e and m not in side:
                        side.add(m)
                        todo.append(m)
            a_src, a_snk = bool(src & side), bool(snk & side)
            b_src, b_snk = bool(src - side), bool(snk - side)
            if a_src and b_snk and not a_snk and not b_src:
                out[e] = 1
            elif b_src and a_snk and not b_snk and not a_src:
                out[e] = -1
        return out


# ---------------------------------------------------------------------------
# branches
# ---------------------------------------------------------------------------
@dataclass
class Member:
    ref: str
    el: str
    part: str            # "" for the whole element, else a winding name
    pins: tuple[str, str]
    diode: bool = False


WINDINGS = {"p": ("p1", "p2"), "s": ("s1", "s2"), "s1": ("s1", "sct"), "s2": ("sct", "s2")}


def _member(layout: Layout, ref: str) -> Member:
    name, _, part = ref.strip().partition(":")
    try:
        el = layout.element(name)
    except KeyError:
        raise ModeError(f"{ref}: no element {name!r} in this figure") from None
    if el.kind == "transformer":
        if part not in WINDINGS:
            raise ModeError(f"{ref}: name a winding of {name}: {name}:p, {name}:s"
                            + (f", {name}:s1, {name}:s2" if "sct" in el.pins else ""))
        pins = WINDINGS[part]
        if any(p not in el.pins for p in pins):
            raise ModeError(f"{ref}: {name} has no centre tap; use {name}:s")
        return Member(ref, name, part, pins)
    if part not in ("", "diode"):
        raise ModeError(f"{ref}: unknown suffix :{part} (only :diode on a switch)")
    if part == "diode" and el.kind not in SWITCHES:
        raise ModeError(f"{ref}: :diode only applies to a switch")
    pins = branch_pins(el.kind)
    if pins is None:
        raise ModeError(f"{ref}: a {el.kind} carries no branch current")
    return Member(ref, name, "", pins, part == "diode")


def _edges_of(layout: Layout) -> list[Member]:
    """Every conducting path through an element: one per two-terminal part
    or switch, one per transformer winding (each half of a centre tap)."""
    out = []
    for el in layout.elements:
        if el.kind == "transformer":
            parts = ("p", "s1", "s2") if "sct" in el.pins else ("p", "s")
            out += [Member(f"{el.id}:{p}", el.id, p, WINDINGS[p]) for p in parts]
        elif branch_pins(el.kind):
            out.append(_member(layout, el.id))
    return out


# a thyristor conducts one way like a diode, once its gate has fired it
ONE_WAY = {"D", "scr"}
DEVICES = SWITCHES | ONE_WAY


def _parse_on(layout: Layout, on: list[str]) -> dict[str, str]:
    """``on`` list -> {device: "on" | "diode"}."""
    out: dict[str, str] = {}
    for ref in on:
        m = _member(layout, ref)
        if layout.element(m.el).kind not in DEVICES:
            raise ModeError(f"on: {ref} is not a switch or diode")
        out[m.el] = "diode" if m.diode else "on"
    return out


def _state_of(m: Member) -> str:
    return "diode" if m.diode else "on"


# ---------------------------------------------------------------------------
# solving one mode
# ---------------------------------------------------------------------------
def solve(layout: Layout, mode: Mode, report: Report, graph: WireGraph | None = None,
          prof: Profile | None = None, max_arrows: int = 3) -> Lit:
    """Lit elements, wire edges and arrows of one mode.  Raises ModeError
    when the mode does not fit the circuit (unknown names, a loop that is
    not closed, a device listed off but used in a loop)."""
    graph = graph or WireGraph(layout)
    nets = report.nets
    pin_pt = {f"{el.id}.{n}": p for el in layout.elements for n, p in el.pins.items()}
    lit = Lit()

    def net(m: Member, i: int) -> int:
        return nets[f"{m.el}.{m.pins[i]}"]

    def light(m: Member) -> None:
        lit.parts.setdefault(m.el, set()).add(m.part)
        if layout.element(m.el).kind in SWITCHES:
            (lit.diode if m.diode else lit.channel).add(m.el)

    if mode.loops:
        oriented: dict[int, int] = {}
        clash: set[int] = set()
        per_loop: list[dict[int, int]] = []
        used: dict[str, str] = {}      # device -> state the loops use it in
        for li, loop in enumerate(mode.loops):
            if len(loop) < 2:
                raise ModeError(f"{mode.name}: loop {li + 1} needs at least two branches")
            branches = []
            for ref in loop:
                group = [_member(layout, r) for r in str(ref).split("|")]
                pairs = {frozenset((net(m, 0), net(m, 1))) for m in group}
                if len(pairs) != 1 or len(next(iter(pairs))) != 2:
                    raise ModeError(f"{mode.name}: {ref} is not one branch between two nets")
                branches.append(group)
            seq = _net_sequence(branches, net)
            if seq is None:
                raise ModeError(f"{mode.name}: loop {li + 1} ({' -> '.join(map(str, loop))}) "
                                f"is not closed: neighbouring branches must share a net and "
                                f"the last one must lead back to the first")
            for i, group in enumerate(branches):
                for m in group:
                    k = layout.element(m.el).kind
                    fwd = seq[i] == net(m, 0)          # entering at its first pin
                    if (k in ONE_WAY or k == "igbt" and not m.diode) and not fwd or \
                            k in SWITCHES and m.diode and fwd:
                        what = "its diode" if k in ONE_WAY or m.diode else "an IGBT channel"
                        lit.problems.append(f"{mode.name}: loop {li + 1} runs {m.el} against "
                                            f"the direction {what} can conduct")
            here: dict[int, int] = {}
            for i, group in enumerate(branches):
                for m in group:
                    light(m)
                    if layout.element(m.el).kind in DEVICES:
                        used[m.el] = _state_of(m)
                nxt = branches[(i + 1) % len(branches)]
                n = seq[i + 1]
                srcs = [pin_pt[f"{m.el}.{m.pins[0 if net(m, 0) == n else 1]}"] for m in group]
                snks = [pin_pt[f"{m.el}.{m.pins[0 if net(m, 0) == n else 1]}"] for m in nxt]
                tree = graph.tree(srcs + snks)
                if tree is None:
                    raise ModeError(f"{mode.name}: no wire joins {loop[i]} to "
                                    f"{loop[(i + 1) % len(loop)]}")
                lit.edges |= tree
                for e, sgn in graph.orient(tree, srcs, snks).items():
                    if oriented.get(e, sgn) != sgn:
                        clash.add(e)
                    oriented[e] = sgn
                    here[e] = sgn
            per_loop.append(here)
        if mode.on is not None:
            on = _parse_on(layout, mode.on)
            for d in sorted(used):
                if d not in on:
                    raise ModeError(f"{mode.name}: {d} carries a loop current but is not in 'on'")
                if on[d] != used[d]:
                    how = {"on": "its channel", "diode": "its diode"}
                    raise ModeError(f"{mode.name}: {d} conducts through {how[used[d]]} in a "
                                    f"loop but through {how[on[d]]} in 'on'")
            for d in sorted(set(on) - set(used)):
                lit.problems.append(f"{mode.name}: {d} is on but in no loop; drawn lit anyway")
                lit.parts.setdefault(d, set()).add("")
                if layout.element(d).kind in SWITCHES:
                    (lit.diode if on[d] == "diode" else lit.channel).add(d)
        if mode.arrows is None and prof is not None:
            placed = _place_arrows(layout, graph, report, prof, per_loop, clash, max_arrows)
            lit.arrows = [(p, a) for p, a, _ in placed]
            lit.arrow_loops = [i for _, _, i in placed]
    else:
        if mode.on is None:
            raise ModeError(f"{mode.name}: give 'loops', or at least 'on' (devices conducting)")
        keep = _conducting(layout, _parse_on(layout, mode.on), net)
        lit.problems.append(f"{mode.name}: no loops given; lit parts are every closed path "
                            f"the switch states allow (topology only), check them yourself")
        by_net: dict[int, list[Point]] = {}
        for m in keep:
            light(m)
            for i in (0, 1):
                by_net.setdefault(net(m, i), []).append(pin_pt[f"{m.el}.{m.pins[i]}"])
        for pts in by_net.values():
            tree = graph.tree(pts)
            if tree:
                lit.edges |= tree
    if mode.arrows:
        for a in mode.arrows:
            ang = {"right": 0.0, "up": 90.0, "left": 180.0, "down": 270.0}.get(
                str(a.get("dir", "right")))
            if ang is None:
                ang = float(a.get("dir"))
            lit.arrows.append(((float(a["at"][0]), float(a["at"][1])), ang))
    return lit


def _net_sequence(branches, net) -> list[int] | None:
    """Nets n0..nk (nk = n0) such that branch i runs from n_i to n_{i+1}.
    The first branch is tried forwards first."""
    m0 = branches[0][0]
    for start, nxt in ((net(m0, 0), net(m0, 1)), (net(m0, 1), net(m0, 0))):
        seq = [start, nxt]
        ok = True
        for group in branches[1:]:
            a, b = net(group[0], 0), net(group[0], 1)
            if seq[-1] == a:
                seq.append(b)
            elif seq[-1] == b:
                seq.append(a)
            else:
                ok = False
                break
        if ok and seq[-1] == seq[0]:
            return seq
    return None


def _conducting(layout: Layout, on: dict[str, str], net) -> list[Member]:
    """Members on some closed path of passive parts, windings and on
    devices.  A winding counts only while another winding of its
    transformer also lies on a closed path (otherwise no current is
    coupled through it)."""
    edges = [m for m in _edges_of(layout)
             if layout.element(m.el).kind not in DEVICES or m.el in on]
    for m in edges:
        if on.get(m.el) == "diode":
            m.diode, m.ref = True, f"{m.el}:diode"
    while True:
        keep = _on_cycles(edges, net)
        live: dict[str, set[str]] = {}
        for m in keep:
            if m.part:
                live.setdefault(m.el, set()).add(m.part)
        dead = [m for m in edges if m.part
                and not (live.get(m.el, set()) - {m.part}
                         - ({"s1", "s2"} if m.part == "s" else set()))]
        if not dead:
            return keep
        edges = [m for m in edges if m not in dead]


def _on_cycles(edges: list[Member], net) -> list[Member]:
    """Members that are not bridges of the multigraph nets/edges, i.e. that
    lie on some closed path (Tarjan's bridge search)."""
    adj: dict[int, list[tuple[int, int]]] = {}
    for i, m in enumerate(edges):
        a, b = net(m, 0), net(m, 1)
        if a == b:
            continue
        adj.setdefault(a, []).append((b, i))
        adj.setdefault(b, []).append((a, i))
    disc: dict[int, int] = {}
    low: dict[int, int] = {}
    bridges: set[int] = set()
    t = [0]

    def dfs(root: int) -> None:
        # iterative DFS: (node, edge used to arrive, neighbour iterator)
        disc[root] = low[root] = t[0]
        t[0] += 1
        stack = [(root, -1, iter(adj.get(root, [])))]
        while stack:
            n, via, it = stack[-1]
            step = next(it, None)
            if step is None:
                stack.pop()
                if stack:
                    p = stack[-1][0]
                    low[p] = min(low[p], low[n])
                    if low[n] > disc[p]:
                        bridges.add(via)
                continue
            m, e = step
            if e == via:
                continue
            if m in disc:
                low[n] = min(low[n], disc[m])
            else:
                disc[m] = low[m] = t[0]
                t[0] += 1
                stack.append((m, e, iter(adj.get(m, []))))

    for n in adj:
        if n not in disc:
            dfs(n)
    return [m for i, m in enumerate(edges)
            if net(m, 0) != net(m, 1) and i not in bridges]


# ---------------------------------------------------------------------------
# arrows
# ---------------------------------------------------------------------------
ARROW_BOX = 0.24      # cm, square kept clear around an arrow
ARROW_MIN_RUN = 0.5   # cm, shortest wire piece that gets an arrow
ARROW_SPACING = 1.0   # cm between two arrows of one loop


def _place_arrows(layout: Layout, graph: WireGraph, report: Report, prof: Profile,
                  loops: list[dict[int, int]], clash: set[int], max_arrows: int
                  ) -> list[tuple[Point, float, int]]:
    s = report.scale
    fps = footprints(layout, s, prof)
    from .geometry import annotation_box
    blocked: list[Box] = [b for f in fps.values() for b in f.bodies + f.labels]
    blocked += [annotation_box(a, s, prof) for a in layout.annotations]
    for a in layout.annotations:
        if a.arrow:
            x, y = scaled(a.at, s)
            blocked.append(centred(x, y, ARROW_BOX, ARROW_BOX))
    hops = [scaled(h, s) for h in layout.hops]
    junctions = [scaled(j, s) for j in report.junctions]
    placed: list[tuple[Point, float, int]] = []
    taken: list[Point] = []
    for li, here in enumerate(loops):
        cands = []
        for e, sgn in here.items():
            if e in clash:
                continue
            E = graph.edges[e]
            a, b = scaled(E.a, s), scaled(E.b, s)
            L = math.dist(a, b)
            if L < ARROW_MIN_RUN:
                continue
            if sgn < 0:
                a, b = b, a
            mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            ang = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 360
            cands.append((L, mid, ang, e))
        cands.sort(key=lambda c: (-c[0], c[3]))
        mine: list[Point] = []
        for L, mid, ang, e in cands:
            if len(mine) >= max_arrows:
                break
            box = centred(mid[0], mid[1], ARROW_BOX, ARROW_BOX)
            if any(box.overlaps(b, 0.02) for b in blocked):
                continue
            if any(math.dist(mid, h) < 0.25 for h in hops + junctions):
                continue
            if any(math.dist(mid, p) < 0.3 for p in taken):
                continue
            if any(math.dist(mid, p) < ARROW_SPACING for p in mine):
                continue
            mine.append(mid)
            taken.append(mid)
            placed.append(((mid[0] / s.x, mid[1] / s.y), ang, li))
    return placed


# ---------------------------------------------------------------------------
# drawing style
# ---------------------------------------------------------------------------
_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


def tikz_color(text: str) -> str:
    """'#c8102e' -> an xcolor rgb spec; anything else is passed to xcolor."""
    m = _HEX.match(text.strip())
    if not m:
        return text.strip()
    h = m.group(1)
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"{{rgb,255:red,{r};green,{g};blue,{b}}}"


@dataclass
class Style:
    color: str = "#c8102e"     # lit parts; "black" for print-only figures
    off_color: str = "black!35"
    width: float = 2.0         # lit wire width as a multiple of the profile's
    symbol_width: float = 1.4  # lit symbols: their own strokes are already heavier
    off: str = "grey"          # "grey" or "dashed" (grey, wires dashed)
    arrows: int = 3            # automatic arrows per loop (0 = none)
    mono: bool = False         # black-and-white figure: regions in greys too

    @classmethod
    def mono(cls) -> "Style":
        return cls(color="black", off_color="black!30", width=2.2, symbol_width=1.5,
                   off="dashed", mono=True)


class Paint:
    """Hooks the emitter calls to restyle one mode.  Text stays black."""

    def __init__(self, layout: Layout, lit: Lit, graph: WireGraph, prof: Profile,
                 style: Style):
        self.lit, self.graph, self.style = lit, graph, style
        col = tikz_color(style.color)
        self.on = (f"color={col}, line width={style.width * prof.line_pt:.3g}pt, "
                   f"line cap=round, line join=round")
        self.on_symbol = f"color={col}, line width={style.symbol_width * prof.line_pt:.3g}pt"
        self.off = f"color={style.off_color}"
        self.off_wire = self.off + (", dash pattern=on 1.6pt off 1.2pt"
                                    if style.off == "dashed" else "")
        self.ink = "black"
        # pins where a lit element's current enters or leaves
        self.lit_pins: dict[tuple, int] = {}
        for el in layout.elements:
            parts = lit.parts.get(el.id, set())
            names = set(el.pins) if "" in parts else \
                {n for p in parts if p in WINDINGS for n in WINDINGS[p]}
            for n in names:
                k = _k(el.pins[n])
                self.lit_pins[k] = self.lit_pins.get(k, 0) + 1

    def element(self, el_id: str, part: str = "") -> str:
        parts = self.lit.parts.get(el_id, set())
        return self.on_symbol if ("" in parts or part in parts) else self.off

    def transformer(self, el_id: str) -> dict[str, str]:
        parts = self.lit.parts.get(el_id, set())
        sec = {"s1": "s1" in parts or "s" in parts, "s2": "s2" in parts or "s" in parts}
        on = self.on_symbol
        return {"p": on if "p" in parts else self.off,
                "s1": on if sec["s1"] else self.off,
                "s2": on if sec["s2"] else self.off,
                "core": f"color={tikz_color(self.style.color)}" if parts else self.off}

    def wire_runs(self, i: int, w: list[Point]) -> list[tuple[list[Point], str]]:
        """Wire i cut into runs of one style each."""
        runs: list[tuple[list[Point], str]] = []
        for e in self.graph.runs[i]:
            E = self.graph.edges[e]
            st = self.on if e in self.lit.edges else self.off_wire
            if runs and runs[-1][1] == st and runs[-1][0][-1] == E.a:
                runs[-1][0].append(E.b)
            else:
                runs.append(([E.a, E.b], st))
        return runs

    def dot(self, p: Point) -> str:
        k = _k(p)
        n = sum(1 for e in self.lit.edges
                if self.graph.edges[e].a == k or self.graph.edges[e].b == k)
        return self.on if n + self.lit_pins.get(k, 0) >= 2 else self.off

    def arrows(self) -> list[tuple[Point, float]]:
        return self.lit.arrows

    def switch(self, el_id: str) -> str:
        """off, on (channel), diode, or both."""
        ch, di = el_id in self.lit.channel, el_id in self.lit.diode
        return "both" if ch and di else "on" if ch else "diode" if di else "off"


def modes_of(layout: Layout) -> list[Mode]:
    return [Mode.from_dict(m) for m in layout.modes]


def overlay(layout: Layout, index: int, report: Report, prof: Profile,
            max_arrows: int = 3) -> dict:
    """What the editor needs to show mode ``index`` on its canvas: lit
    elements (with lit winding parts), lit wire pieces and arrows."""
    mode = modes_of(layout)[index]
    graph = WireGraph(layout)
    lit = solve(layout, mode, report, graph, prof, max_arrows)
    return {"name": mode.name, "caption": mode.caption,
            "parts": {k: sorted(v) for k, v in lit.parts.items()},
            "segments": [[list(graph.edges[e].a), list(graph.edges[e].b)]
                         for e in sorted(lit.edges)],
            "arrows": [[at[0], at[1], ang, li] for (at, ang), li
                       in zip(lit.arrows, lit.arrow_loops or [None] * len(lit.arrows))],
            "channel": sorted(lit.channel), "diode": sorted(lit.diode),
            "notes": lit.problems}


def check_modes(layout: Layout, report: Report) -> list[dict]:
    """Every mode of the table against the current drawing: after the main
    figure is edited, a mode that names a removed part or no longer closes
    is reported here instead of failing at export."""
    out = []
    graph = WireGraph(layout)
    for m in modes_of(layout):
        try:
            lit = solve(layout, m, report, graph)
            out.append({"name": m.name, "ok": True, "notes": lit.problems})
        except ModeError as exc:
            out.append({"name": m.name, "ok": False, "error": str(exc)})
    return out


# ---------------------------------------------------------------------------
# editing a mode by clicking parts (the editor's mode tools)
# ---------------------------------------------------------------------------
def states(layout: Layout, mode: Mode) -> dict[str, str]:
    """{device: "on" | "diode"} of a mode, from 'on' or else from its loops."""
    if mode.on is not None:
        return _parse_on(layout, mode.on)
    out = {}
    for loop in mode.loops:
        for ref in loop:
            for r in str(ref).split("|"):
                m = _member(layout, r)
                if layout.element(m.el).kind in DEVICES:
                    out[m.el] = _state_of(m)
    return out


def next_state(layout: Layout, el_id: str, now: str | None) -> str | None:
    """Click cycle: switch off -> channel -> diode -> off; diode off <-> on."""
    if layout.element(el_id).kind in ONE_WAY:
        return None if now else "on"
    return {None: "on", "on": "diode", "diode": None}[now]


def set_state(layout: Layout, report: Report, mode: Mode, el_id: str,
              state: str | None) -> Mode:
    """The mode with one device switched to ``state`` (None = off).  Loops
    that relied on the old state are dropped; every conducting device left
    outside all loops gets the shortest closed path through it as a new
    loop, run in its conducting direction (d->s for a channel, s->d for a
    body diode, a->b for a diode).  Arrows can then be flipped per loop."""
    el = layout.element(el_id)
    if el.kind not in DEVICES:
        raise ModeError(f"{el_id} is not a switch or diode; passive parts light up with "
                        f"the loops they are in")
    if state == "diode" and el.kind not in SWITCHES:
        raise ModeError(f"{el_id}: only a switch has a body diode")
    on = states(layout, mode)
    if state:
        on[el_id] = state
    else:
        on.pop(el_id, None)
    ref = {d: d + (":diode" if st == "diode" else "") for d, st in on.items()}

    def fits(loop) -> bool:
        for b in loop:
            for r in str(b).split("|"):
                m = _member(layout, r)
                if layout.element(m.el).kind in DEVICES and ref.get(m.el) != m.ref.strip():
                    return False
        return True
    loops = [list(l) for l in mode.loops if fits(l)]
    loops = complete_loops(layout, report, on, loops)
    order = [d for d in (mode.on or []) if _member(layout, d).el in on]
    order = [ref[_member(layout, d).el] for d in order]
    order += [ref[d] for d in on if ref[d] not in order]
    return Mode(mode.name, mode.caption, order, loops,
                mode.arrows if loops == mode.loops else None, mode.condition)


def complete_loops(layout: Layout, report: Report, on: dict[str, str],
                   loops: list[list[str]]) -> list[list[str]]:
    nets = report.nets

    def net(m: Member, i: int) -> int:
        return nets[f"{m.el}.{m.pins[i]}"]
    active = _conducting(layout, on, net)
    loops = [_regroup(layout, l, active, net) for l in loops]

    def els(loop) -> set[str]:
        return {_member(layout, r).el for b in loop for r in str(b).split("|")}

    def has_device(loop) -> bool:
        return any(layout.element(e).kind in DEVICES for e in els(loop))
    # device loops first; a loop of passive parts only (Co feeding RL) stays
    # only while some of its parts are in no device loop
    for m in active:
        dev_cov = set().union(*[els(l) for l in loops if has_device(l)]) if loops else set()
        if layout.element(m.el).kind in DEVICES and m.el not in dev_cov:
            loop = _shortest_loop(m, active, net)
            if loop:
                loops.append(loop)
    dev_cov = set().union(*[els(l) for l in loops if has_device(l)]) if loops else set()
    loops = [l for l in loops if has_device(l) or not els(l) <= dev_cov]
    covered = set().union(*[els(l) for l in loops]) if loops else set()
    # devices first, then loads (a resistor run a->b makes a capacitor beside
    # it discharge into it), then whatever else still carries no loop
    rank = {"R": 1, "I": 2, "V": 3, "vac": 3, "L": 4, "C": 5}
    for m in sorted(active, key=lambda m: (0 if layout.element(m.el).kind in DEVICES
                                           else rank.get(layout.element(m.el).kind, 6), m.ref)):
        if m.el in covered:
            continue
        loop = _shortest_loop(m, active, net)
        if loop:
            loops.append(loop)
            covered |= {_member(layout, r).el for b in loop for r in b.split("|")}
    return loops


def _regroup(layout: Layout, loop: list[str], active: list[Member], net) -> list[str]:
    """Branches of a kept loop brought up to date: parallel parts that now
    conduct join their branch, parts that no longer conduct leave it."""
    live = {m.ref: m for m in active}
    named = {r.strip() for b in loop for r in str(b).split("|")}
    out = []
    for b in loop:
        refs = [r.strip() for r in str(b).split("|")]
        keep = [r for r in refs if r in live or layout.element(_member(layout, r).el).kind
                in DEVICES] or refs
        m0 = _member(layout, keep[0])
        pair = {net(m0, 0), net(m0, 1)}
        extra = sorted(m.ref for m in active if m.ref not in named
                       and {net(m, 0), net(m, 1)} == pair)
        out.append("|".join(keep + extra))
        named |= set(extra)
    return out


def _shortest_loop(dev: Member, active: list[Member], net) -> list[str] | None:
    """Fewest-branch closed path through ``dev`` leaving it in its
    conducting direction; parallel parts between the same two nets are
    written as one A|B branch."""
    start, goal = (net(dev, 0), net(dev, 1)) if not dev.diode else (net(dev, 1), net(dev, 0))
    # leave from goal (the net the current enters after dev), come back to start
    adj: dict[int, list[tuple[int, Member]]] = {}
    for m in active:
        if m.el == dev.el:
            continue
        a, b = net(m, 0), net(m, 1)
        adj.setdefault(a, []).append((b, m))
        adj.setdefault(b, []).append((a, m))
    back = {goal: None}
    todo = [goal]
    while todo and start not in back:
        nxt = []
        for n in todo:
            for k, m in sorted(adj.get(n, []), key=lambda t: t[1].ref):
                if k not in back:
                    back[k] = (n, m)
                    nxt.append(k)
        todo = nxt
    if start not in back:
        return None
    path, n = [], start
    while back[n] is not None:
        prev, m = back[n]
        path.append((prev, n, m))
        n = prev
    path.reverse()
    out = [dev.ref]
    for a, b, m in path:
        group = sorted({p.ref for p in active if p.el != dev.el
                        and {net(p, 0), net(p, 1)} == {a, b}},
                       key=lambda r: (r != m.ref, r))
        out.append("|".join(group))
    return out


def reverse_loop(mode: Mode, index: int) -> Mode:
    loops = [list(l) for l in mode.loops]
    loops[index] = loops[index][::-1]
    return Mode(mode.name, mode.caption, mode.on, loops, None, mode.condition)
