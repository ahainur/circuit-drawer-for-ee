"""Where an exported simulation model puts each part and how its wires run.

The coordinate table already is a drawn schematic: every element has its pins
and every wire its corners.  A simulation export keeps that drawing instead of
inventing a new layout: each element's main part sits between its pins, turned
the same way; the extra parts one element becomes in the target (a switch's
reverse diode and off-state resistor, an inductor's series resistance, a
snubber) run as parallel or series branches beside it; a current sensor sits
in series at the pin it measures; and every net is wired along the drawn
wires, branching where the drawing branches.

Coordinates are target pixels with y pointing down, on a ``GRID`` px grid.
``direction`` is the side terminal 1 of a part points to.  What a target
knows about its own symbols comes in as ``terminal(part, index, direction)``:
a terminal's offset from the part's position, and ``signal_side(part,
direction)``: the offset of a switch's gate, whose side is kept free of
branches for the drive.

Parts the drawing gives no place for (voltmeters, numerical capacitors,
anything inside an element that is not a series/parallel branch) are listed in
``Plan.loose`` and placed in free space near their device: parts that share a net
nothing else uses are wired together, every other terminal is in
``Plan.tags`` and the target joins it by net name, the way a schematic uses
net labels.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from heapq import heapify, heappop, heappush
from math import ceil, floor

GRID = 5               # px: the target grid every point snaps to
MIN_SCALE = 80         # px per layout unit; the exporters' previous projection
MAX_SCALE = 200        # px per layout unit: beyond this the figure gets unwieldy
LANE = 40              # px between parallel branches of one drawn element
LEAD = 50              # px a series current sensor takes at a pin
PART_HALF = 20         # px a part reaches sideways from its own axis
CLEAR = 20             # px kept clear between neighbouring elements' branches
DRIVE_REACH = 70       # px a switch's gate drive (its signal tag) takes on its side
LOOSE_GAP = 160        # px from the drawing down to the rows of loose parts
LOOSE_STEP = 30        # px of wire between stacked loose parts
LOOSE_COLUMN = 110     # px between columns of loose parts
MIN_GAP = 10           # px of wire between parts in one branch
DIRS = {"right": (1, 0), "down": (0, 1), "left": (-1, 0), "up": (0, -1)}
# the part kind that is the drawn element itself, as the exporters name it
MAIN_KIND = {"nmos": ("S", "Q"), "pmos": ("S",), "igbt": ("Q", "S"), "vac": ("V",), "scr": ("SCR",)}
TWO_PIN = {"R": ("a", "b"), "L": ("a", "b"), "C": ("a", "b"), "D": ("a", "b"), "scr": ("a", "b"),
           "V": ("a", "b"), "vac": ("a", "b"), "I": ("a", "b"),
           "nmos": ("d", "s"), "pmos": ("s", "d"), "igbt": ("c", "e")}


def direction_of(vector) -> str:
    x, y = vector
    return ("right" if x > 0 else "left") if abs(x) >= abs(y) else ("down" if y > 0 else "up")


def snap(v: float) -> int:
    return int(round(v / GRID) * GRID)


def nearby(origin, size, occupied, clear=CLEAR):
    """Nearest free rectangle on the target grid; reserve it for the next item.
    Candidates touch obstacle edges, so a dense drawing cannot exhaust a
    fixed-radius search. Only newly created simulation helpers use this."""
    w, h = size[0] / 2, size[1] / 2
    xs = {ceil(max(w + clear, origin[0]) / GRID) * GRID}
    ys = {ceil(max(h + clear, origin[1]) / GRID) * GRID}
    for x0, y0, x1, y1 in occupied:
        xs.update((floor((x0 - w - clear) / GRID) * GRID, ceil((x1 + w + clear) / GRID) * GRID))
        ys.update((floor((y0 - h - clear) / GRID) * GRID, ceil((y1 + h + clear) / GRID) * GRID))
    ys = sorted((y for y in ys if y >= h + clear), key=lambda y: ((y - origin[1]) ** 2, y))
    distance = lambda x, y: (x - origin[0]) ** 2 + (y - origin[1]) ** 2
    candidates = [(distance(x, ys[0]), x, ys[0], 0) for x in xs if x >= w + clear]
    heapify(candidates)
    while candidates:
        _, x, y, index = heappop(candidates)
        box = (x - w, y - h, x + w, y + h)
        if all(box[2] + clear <= a or c + clear <= box[0] or box[3] + clear <= b or d + clear <= box[1]
               for a, b, c, d in occupied):
            occupied.append(box)
            return x, y
        if index + 1 < len(ys):
            yy = ys[index + 1]
            heappush(candidates, (distance(x, yy), x, yy, index + 1))
    raise ValueError("cannot find free space for simulation helper")


@dataclass
class Spot:
    pos: tuple[int, int]
    direction: str
    element: str | None = None        # drawn element it belongs to


@dataclass
class Plan:
    scale: float
    spots: dict[str, Spot] = field(default_factory=dict)
    # net -> undirected segments between points; a point is (x, y) or a
    # terminal ("T", part name, terminal index from 0) at its own position
    edges: dict[str, set] = field(default_factory=lambda: defaultdict(set))
    loose: list[str] = field(default_factory=list)
    tags: list[tuple[str, int]] = field(default_factory=list)   # (part, terminal) joined by net name
    box: tuple[int, int, int, int] = (0, 0, 0, 0)    # drawing extent: x0, y0, x1, y1
    pins: dict[str, tuple[int, int]] = field(default_factory=dict)
    offset: tuple[int, int] = (0, 0)                 # px subtracted from every scaled point
    occupied: list[tuple] = field(default_factory=list)

    def px(self, point):
        """A layout point in the plan's pixels, as ``pins`` are."""
        return snap(point[0] * self.scale) - self.offset[0], snap(-point[1] * self.scale) - self.offset[1]

    def under(self, point, boxes, reach=(0, 0)):
        """``point`` moved down to the bottom of any of ``boxes`` ((x0, y0),
        (x1, y1) px) that the span ``reach`` (px left and right of it) runs
        into: a ground and solver hung under a drawn point on a subsystem's
        edge go below the block, not into it."""
        x, y = point
        for (x0, _), (x1, y1) in boxes:
            if x + reach[0] <= x1 and x0 <= x + reach[1]:
                y = max(y, y1)
        return x, y


def _segments(wires, points):
    """Drawn wire segments, split wherever a corner or pin lies inside one."""
    out = []
    for wire in wires:
        for p, q in zip(wire, wire[1:]):
            if p == q:
                continue
            inner = [r for r in points if r not in (p, q) and _between(p, q, r)]
            chain = [p, *sorted(inner, key=lambda r: abs(r[0] - p[0]) + abs(r[1] - p[1])), q]
            out += list(zip(chain, chain[1:]))
    return out


def _between(p, q, r) -> bool:
    if p[0] == q[0] == r[0]:
        return min(p[1], q[1]) < r[1] < max(p[1], q[1])
    if p[1] == q[1] == r[1]:
        return min(p[0], q[0]) < r[0] < max(p[0], q[0])
    return False


class _Union:
    def __init__(self):
        self.parent = {}

    def find(self, a):
        self.parent.setdefault(a, a)
        while self.parent[a] != a:
            self.parent[a] = self.parent[self.parent[a]]
            a = self.parent[a]
        return a

    def join(self, a, b):
        self.parent[self.find(a)] = self.find(b)


def _lanes(group, a_net, b_net):
    """Series chains from net A to net B, each a list of (part, flipped), or None."""
    touching = defaultdict(list)
    for p in group:
        for n in p.nodes:
            touching[n].append(p)
    lanes, used = [], set()
    for start in [p for p in group if a_net in p.nodes]:
        if start.name in used:
            continue
        chain, node, part = [], a_net, start
        while True:
            if len(part.nodes) != 2 or part.name in used:
                return None
            used.add(part.name)
            flipped = part.nodes[1] == node          # terminal 2 faces the A side
            chain.append((part, flipped))
            node = part.nodes[0] if flipped else part.nodes[1]
            if node == b_net:
                break
            if node == a_net:
                return None
            nxt = [q for q in touching[node] if q.name not in used]
            if len(nxt) != 1 or len(touching[node]) != 2:
                return None
            part = nxt[0]
        lanes.append(chain)
    if used != {p.name for p in group}:
        return None
    return lanes


def plan(parts, elements, wires, pin_nodes, terminal, signal_side=lambda part, direction: None,
         lane=LANE, drive_reach=DRIVE_REACH, column=LOOSE_COLUMN, boxes=()) -> Plan:
    """``parts``: objects with name, kind, nodes, source, role.  ``elements``:
    drawn element id -> (kind, {pin: (x, y) in layout units}).  ``wires``:
    drawn polylines in layout units.  ``pin_nodes``: "Elem.pin" -> net.
    ``lane``: px between parallel branches; ``drive_reach``: px the signal
    side of a part needs (gate or converter)."""
    def span(part):
        (x0, y0), (x1, y1) = terminal(part, 0, "right"), terminal(part, 1, "right")
        return abs(x1 - x0) + abs(y1 - y0)
    by_source = defaultdict(list)
    for p in parts:
        by_source[p.source].append(p)
    sensors = [p for p in parts if p.kind == "AM"]
    groups = {}
    for eid, (kind, pins) in elements.items():
        if kind not in TWO_PIN or eid not in by_source:
            continue
        pa, pb = TWO_PIN[kind]
        na, nb = pin_nodes[f"{eid}.{pa}"], pin_nodes[f"{eid}.{pb}"]
        group = [p for p in by_source[eid] if p.kind != "AM"]
        nodes = {n for p in group for n in p.nodes}
        lead = {}
        for side, net in (("a", na), ("b", nb)):
            s = next((s for s in sensors if s.nodes[0] == net and s.nodes[1] in nodes), None)
            if s:
                lead[side] = s
        bus_a = lead["a"].nodes[1] if "a" in lead else na
        bus_b = lead["b"].nodes[1] if "b" in lead else nb
        lanes = _lanes(group, bus_a, bus_b) if bus_a != bus_b else None
        x0, y0 = pins[pa]
        x1, y1 = pins[pb]
        if lanes is None or (x0 != x1 and y0 != y1):
            continue
        own = MAIN_KIND.get(kind, (kind,))
        main = next((i for i, lane in enumerate(lanes) for p, _ in lane if p.kind in own), 0)
        lanes.insert(0, lanes.pop(main))
        length = max(sum(span(p) for p, _ in lane) + MIN_GAP * (len(lane) + 1) for lane in lanes)
        length += LEAD * len(lead)
        main_part, main_flip = lanes[0][0]
        drive = signal_side(main_part, "right") is not None
        groups[eid] = dict(pa=pa, pb=pb, lanes=lanes, lead=lead, bus=(bus_a, bus_b), need=length, lane=lane,
                           reach=lane * (len(lanes) - 1 if drive else len(lanes) // 2) + PART_HALF
                           + (drive_reach if drive else 0))
    scale = _scale(groups, elements)

    def px(point):
        return snap(point[0] * scale), snap(-point[1] * scale)

    pins_px = {f"{eid}.{pin}": px(xy) for eid, (_, pins) in elements.items() for pin, xy in pins.items()}
    drawn = [[px(p) for p in w] for w in wires]
    xs = [x for x, _ in pins_px.values()] + [x for w in drawn for x, _ in w]
    ys = [y for _, y in pins_px.values()] + [y for w in drawn for _, y in w]
    left, top = min(xs, default=0) - 120, min(ys, default=0) - 120
    shift = lambda p: (p[0] - left, p[1] - top)
    pins_px = {k: shift(v) for k, v in pins_px.items()}
    drawn = [[shift(p) for p in w] for w in drawn]
    out = Plan(scale, pins=pins_px, offset=(left, top))
    out.box = (min(x for x, _ in pins_px.values()), min(y for _, y in pins_px.values()),
               max(x for x, _ in pins_px.values()), max(y for _, y in pins_px.values()))

    # Drawn wires: the net of each connected piece comes from the pins on it.
    segs = _segments(drawn, set(pins_px.values()) | {p for w in drawn for p in w})
    union = _Union()
    for p, q in segs:
        union.join(p, q)
    piece_net = {}
    for ref, xy in pins_px.items():
        if ref in pin_nodes:
            piece_net.setdefault(union.find(xy), pin_nodes[ref])
    for p, q in segs:
        net = piece_net.get(union.find(p))
        if net is not None:
            out.edges[net].add((p, q))

    placed = set()
    for eid, g in groups.items():
        placed |= _place_element(out, eid, g, span, terminal, signal_side)
    for eid, (kind, pins) in elements.items():
        if kind == "transformer":
            placed |= _place_transformer(out, eid, pins, [p for p in by_source[eid] if p.kind == "T"],
                                         pin_nodes, terminal, sensors)
        elif kind == "pmsm":
            placed |= _place_motor(out, eid, pins, by_source[eid], terminal)
    # Keep helpers clear of the original wires, devices and collapsed modules.
    by_name = {p.name: p for p in parts}

    def endpoint(node):
        if node[0] != 'T':
            return node
        _, name, index = node
        spot = out.spots[name]
        dx, dy = terminal(by_name[name], index, spot.direction)
        return spot.pos[0] + dx, spot.pos[1] + dy

    for edges in out.edges.values():
        for start, end in sorted(edges, key=str):
            a, b = endpoint(start), endpoint(end)
            out.occupied.append((min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])))
    for box in boxes:
        a, b = out.px((box[0], box[3])), out.px((box[2], box[1]))
        out.occupied.append((*a, *b))
    for p in parts:
        if p.name not in placed:
            continue
        sp = out.spots[p.name]
        side = signal_side(p, sp.direction)
        reach = drive_reach if side is not None or p.kind in ('VM', 'AM', 'PMSM') else 0
        x, y = sp.pos
        out.occupied.append((x - PART_HALF - reach, y - PART_HALF - reach,
                             x + PART_HALF + reach, y + PART_HALF + reach))
    _place_loose(out, [p for p in parts if p.name not in placed], terminal, span, column, parts)
    # a placed part's terminal no drawn wire reaches (a sensor's inner net at a
    # transformer winding) is joined by net name too
    reached = {n for e in out.edges.values() for pair in e for n in pair if n[0] == "T"}
    for p in parts:
        if p.name in placed:
            out.tags += [(p.name, i) for i in range(len(p.nodes)) if ("T", p.name, i) not in reached]
    return out


def _place_motor(out, eid, pins, parts, terminal):
    """Project the shared symbol centre; join each phase to its own drawn pin."""
    from .geometry import Scale, footprint
    from .layout import Element
    from .profiles import get_profile
    fp = footprint(Element(eid, 'pmsm', pins), Scale(1, 1), get_profile('double'))
    side = fp.extra['side']
    direction = direction_of((-side[0], side[1]))
    placed = set()
    for p in parts:
        if p.kind != 'PMSM':
            continue
        pos = out.px(fp.centre)
        out.spots[p.name] = Spot(pos, direction, eid)
        horizontal = direction in ('left', 'right')
        for i, pin in enumerate(('a', 'b', 'c')):
            target = out.pins[f'{eid}.{pin}']
            dx, dy = terminal(p, i, direction)
            corner = (target[0], pos[1] + dy) if horizontal else (pos[0] + dx, target[1])
            out.edges[p.nodes[i]].add((('T', p.name, i), corner))
            out.edges[p.nodes[i]].add((corner, target))
        placed.add(p.name)
    return placed


def _scale(groups, elements) -> float:
    """Enough room for every element's branches along and across its axis."""
    scale = MIN_SCALE
    axes = {}
    for eid, g in groups.items():
        (x0, y0), (x1, y1) = elements[eid][1][g["pa"]], elements[eid][1][g["pb"]]
        length = abs(x1 - x0) + abs(y1 - y0)
        if length:
            scale = max(scale, g["need"] / length)
        reach = g["reach"]
        axes[eid] = ("v" if x0 == x1 else "h", (x0 + x1) / 2, (y0 + y1) / 2,
                     sorted((x0, x1) if y0 == y1 else (y0, y1)), reach)
    items = list(axes.values())
    for i, (a, ax, ay, ra, wa) in enumerate(items):
        for b, bx, by, rb, wb in items[i + 1:]:
            if a != b or ra[1] <= rb[0] or rb[1] <= ra[0]:
                continue
            d = abs(ax - bx) if a == "v" else abs(ay - by)
            if d:
                scale = max(scale, (wa + wb + CLEAR) / d)
    return min(scale, MAX_SCALE)


def _place_element(out: Plan, eid, g, span, terminal, signal_side) -> set[str]:
    pa, pb = out.pins[f"{eid}.{g['pa']}"], out.pins[f"{eid}.{g['pb']}"]
    length = abs(pb[0] - pa[0]) + abs(pb[1] - pa[1])
    if not length:
        return set()
    u = ((pb[0] - pa[0]) // length, (pb[1] - pa[1]) // length)
    v = (-u[1], u[0])
    at = lambda base, t, o=0: (snap(base[0] + u[0] * t + v[0] * o), snap(base[1] + u[1] * t + v[1] * o))
    toward_a, toward_b = direction_of((-u[0], -u[1])), direction_of(u)
    placed = set()
    ends = {}
    for side, pin, sign in (("a", pa, 1), ("b", pb, -1)):
        s = g["lead"].get(side)
        if s is None:
            ends[side] = pin
            continue
        half = span(s) / 2
        centre = at(pin, sign * (half + (LEAD - 2 * half) / 2))
        # terminal 1 (the original net) faces the pin
        out.spots[s.name] = Spot(centre, toward_a if side == "a" else toward_b, eid)
        out.edges[s.nodes[0]].add((("T", s.name, 0), pin))
        ends[side] = at(pin, sign * LEAD)
        out.edges[s.nodes[1]].add((("T", s.name, 1), ends[side]))
        placed.add(s.name)
    ba, bb = ends["a"], ends["b"]
    inner = abs(bb[0] - ba[0]) + abs(bb[1] - ba[1])
    main, flipped = g["lanes"][0][0]
    gate = signal_side(main, toward_b if flipped else toward_a)
    # branches keep off the gate's side; elsewhere they alternate sides
    away = -1 if gate and gate[0] * v[0] + gate[1] * v[1] > 0 else 1
    for k, lane in enumerate(g["lanes"]):
        step = g["lane"]
        offset = step * k * away if gate else step * ((k + 1) // 2) * (1 if k % 2 else -1)
        start, finish = at(ba, 0, offset), at(bb, 0, offset)
        if offset:
            out.edges[g["bus"][0]].add((ba, start))
            out.edges[g["bus"][1]].add((bb, finish))
        total = sum(span(p) for p, _ in lane)
        gap = max(inner - total, 0) / (len(lane) + 1)
        cursor, previous = 0.0, (start, g["bus"][0])
        for part, flipped in lane:
            s = span(part)
            cursor += gap + s / 2
            centre = at(ba, cursor, offset)
            cursor += s / 2
            # the terminal facing the A side is 1 unless the part is flipped
            out.spots[part.name] = Spot(centre, toward_b if flipped else toward_a, eid)
            near, far = (1, 0) if flipped else (0, 1)
            point, net = previous
            out.edges[net].add((point, ("T", part.name, near)))
            previous = (("T", part.name, far), part.nodes[far])
            placed.add(part.name)
        point, net = previous
        out.edges[net].add((point, finish))
    return placed


def _place_transformer(out: Plan, eid, pins, parts, pin_nodes, terminal, sensors) -> set[str]:
    """Each winding pair sits between the primary and secondary pins, primary
    terminals toward the primary side; terminals reach their pins at a right
    angle, and a winding's current sensor sits on that leg."""
    if not parts:
        return set()
    px = {pin: out.pins[f"{eid}.{pin}"] for pin in pins}
    prim = [px["p1"], px["p2"]]
    sec = [px[k] for k in ("s1", "s2", "sct") if k in px]
    cx = (sum(x for x, _ in prim) / 2 + sum(x for x, _ in sec) / len(sec)) / 2
    cy = (sum(y for _, y in prim + sec)) / len(prim + sec)
    side = (sum(x for x, _ in prim) / 2 - sum(x for x, _ in sec) / len(sec),
            sum(y for _, y in prim) / 2 - sum(y for _, y in sec) / len(sec))
    direction = direction_of(side)
    horizontal = direction in ("left", "right")
    def span(part):
        (x0, y0), (x1, y1) = terminal(part, 0, "right"), terminal(part, 1, "right")
        return abs(x1 - x0) + abs(y1 - y0)
    placed = set()
    for k, part in enumerate(parts):
        shift = (k - (len(parts) - 1) / 2) * 2 * LANE
        pos = (snap(cx + (0 if horizontal else shift)), snap(cy + (shift if horizontal else 0)))
        out.spots[part.name] = Spot(pos, direction, eid)
        for i, net in enumerate(part.nodes):
            sensor = next((s for s in sensors if s.nodes[1] == net and s.name not in placed
                           and any(pin_nodes.get(f"{eid}.{pin}") == s.nodes[0] for pin in px)), None)
            outer = sensor.nodes[0] if sensor else net
            target = next((xy for pin, xy in px.items() if pin_nodes.get(f"{eid}.{pin}") == outer), None)
            if target is None:
                continue
            dx, dy = terminal(part, i, direction)
            t = (pos[0] + dx, pos[1] + dy)
            corner = (target[0], t[1]) if horizontal else (t[0], target[1])
            out.edges[net].add((("T", part.name, i), corner))
            leg = abs(target[0] - corner[0]) + abs(target[1] - corner[1])
            if sensor and leg >= span(sensor) + 2 * MIN_GAP:
                # the sensor straddles the middle of the leg, terminal 1 toward the pin
                mid = (snap((corner[0] + target[0]) / 2), snap((corner[1] + target[1]) / 2))
                way = direction_of((target[0] - corner[0], target[1] - corner[1]))
                out.spots[sensor.name] = Spot(mid, way, eid)
                out.edges[net].add((corner, ("T", sensor.name, 1)))
                out.edges[outer].add((("T", sensor.name, 0), target))
                placed.add(sensor.name)
            elif not sensor:
                out.edges[net].add((corner, target))
        placed.add(part.name)
    return placed


def _place_loose(out: Plan, parts, terminal, span, column, all_parts):
    """Near the owning device. Parts joined by nets nothing else uses form a
    chain, stacked top to bottom and wired; other terminals become tags."""
    if not parts:
        return
    users = defaultdict(list)
    for p in parts:
        for n in p.nodes:
            users[n].append(p)
    drawn = set(out.edges) | {n for n, e in out.edges.items() if e}
    private = {n for n, ps in users.items() if n not in drawn and len(ps) == 2}
    union = _Union()
    for p in parts:
        union.find(p.name)
        for n in p.nodes:
            if n in private:
                union.join(p.name, users[n][0].name)
    clusters = defaultdict(list)
    for p in parts:
        clusters[union.find(p.name)].append(p)
    x0, _, _, y1 = out.box
    x, top = x0, y1 + LOOSE_GAP
    for group in clusters.values():
        chain = _chain(group, private)
        sources = {p.source for p in group}
        nets = {n for p in group for n in p.nodes if n != '0'}
        anchors = [p for p in all_parts if p.name in out.spots and out.spots[p.name].element and p.source in sources]
        if not anchors:
            anchors = [p for p in all_parts if p.name in out.spots and nets.intersection(p.nodes)]
        if anchors:
            # Prefer the component sharing the most measured terminals, then
            # file order. A voltage probe need not have the component's id.
            anchor = max(anchors, key=lambda p: len(nets.intersection(p.nodes)))
            origin = out.spots[anchor.name].pos
        else:
            origin = (x, top)
        height = sum(span(p) + LOOSE_STEP for p, _ in chain) - LOOSE_STEP
        cx, cy = nearby(origin, (column, height + 2 * CLEAR), out.occupied)
        x, top_of_group = cx, cy - height / 2
        y = top_of_group
        for part, flipped in chain:
            s = span(part)
            direction = "down" if flipped else "up"      # terminal facing up first
            pos = (x, snap(y + s / 2))
            out.spots[part.name] = Spot(pos, direction)
            y += s + LOOSE_STEP
        for (a, fa), (b, fb) in zip(chain, chain[1:]):
            near_a = 0 if fa else 1
            near_b = 1 if fb else 0
            out.edges[a.nodes[near_a]].add((("T", a.name, near_a), ("T", b.name, near_b)))
        for part, _ in chain:
            for i, n in enumerate(part.nodes):
                if n not in private:
                    out.tags.append((part.name, i))
        out.loose += [p.name for p, _ in chain]
        x += column


def _chain(group, private):
    """A cluster in series order: (part, flipped), flipped when terminal 1 faces down."""
    if len(group) == 1 or any(len(p.nodes) != 2 for p in group):
        return [(p, False) for p in group]
    ends = [p for p in group if sum(n in private for n in p.nodes) == 1]
    if len(ends) != 2:
        return [(p, False) for p in group]
    out, done, part, came = [], set(), ends[0], None
    while part is not None:
        if came is None:
            link = next(n for n in part.nodes if n in private)
            flipped = part.nodes[0] == link           # the link to the next part faces down
        else:
            flipped = part.nodes[1] == came           # the link from the last part faces up
        out.append((part, flipped))
        done.add(part.name)
        came = next((n for n in part.nodes if n in private and n != came), None)
        part = next((q for q in group if q.name not in done and came in q.nodes), None) if came else None
    return out if len(out) == len(group) else [(p, False) for p in group]
