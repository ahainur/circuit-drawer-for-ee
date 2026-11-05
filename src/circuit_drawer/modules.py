"""Explicit module instances; derived drawings never replace the source JSON.

Placements are translations supplied by the author, not automatic layout.
Declared terminal symbols join otherwise separate drawings by net name, and
``modules.links`` joins named ports ("Vin.p" <-> "M1.P").  A link stores no
coordinates: its orthogonal wire is derived from the two port positions on
every render, so collapsing or expanding an instance never leaves a stale wire.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from functools import lru_cache
from copy import deepcopy
from dataclasses import dataclass, field, replace
import json
import math
from pathlib import Path
import re
import threading

from .geometry import Scale
from .profiles import get_profile
from .glyphs import CONVERTERS, KINDS as GLYPH_KINDS
from .layout import DECORATION_SECTIONS, PIN_NAMES, SWITCHES, TWO_TERMINAL, Layout


class ModuleError(ValueError):
    pass


DISPLAYS = ("expanded", "collapsed", "partial")
SYMBOLS = ("text",) + GLYPH_KINDS
# Collapsed box, in layout units: a small near-square block as papers draw
# a converter, not the height of the expanded circuit.
COLLAPSED_SIZE = 2.0         # side of the box for up to two ports a side
PORT_PITCH = 1.0             # port spacing on a taller box
MIN_BOX = 0.8                # smallest side an author may give a collapsed box
TITLE_CHAR_WIDTH = 0.22      # rough width of one title character
TITLE_PAD = 1.4              # room left for the port labels on both sides
GLYPH_INSET = 0.25           # device mark kept this far inside the box
TITLE_GAP = 0.35             # name above a box that shows a mark
PARTIAL_TITLE_GAP = 0.35     # instance name above a partly collapsed module


def _object(value, required=(), optional=(), where="modules"):
    if not isinstance(value, dict):
        raise ModuleError(f"{where} 必须是对象")
    if set(required) - value.keys() or value.keys() - set(required) - set(optional):
        raise ModuleError(f"{where}: 必填 {list(required)}，可选 {list(optional)}；请检查缺少或未知字段")


def _name(value, where):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", value):
        raise ModuleError(f"{where} 必须以英文字母开头，只含字母、数字、下划线或短横线")
    return value


def _merge(base, diff):
    out = deepcopy(base)
    for key, value in diff.items():
        out[key] = _merge(out[key], value) if isinstance(out.get(key), dict) and isinstance(value, dict) else deepcopy(value)
    return out


def _terminal(lay, ref, where):
    if not isinstance(ref, str):
        raise ModuleError(f"{where} 必须指向 port 元件引脚，例如 IN.p")
    matches = [e for e in lay.elements if ref == f"{e.id}.p" and e.kind == "port"]
    if len(matches) != 1 or ref not in lay.netlist:
        raise ModuleError(f"{where}: {ref} 不是唯一、已声明网表的 port 引脚")
    return matches[0]


def _complete(lay, where):
    pins = {f"{e.id}.{p}" for e in lay.elements for p in e.pins}
    if not pins or set(lay.netlist) != pins or not all(isinstance(n, str) and n for n in lay.netlist.values()):
        raise ModuleError(f"{where} 需要完整网表（所有器件引脚逐一声明）")
    if len({e.id for e in lay.elements}) != len(lay.elements):
        raise ModuleError(f"{where} 存在重复器件 ID")


def _provisional_nets(lay):
    """A copy where every top-level pin missing from the netlist gets a net of
    its own, so a part just added in the editor can be drawn and wired before
    the netlist is updated; returns the copy and those pins. The validator
    then reports them as undeclared, exactly as in a table without modules."""
    pins = [f"{e.id}.{p}" for e in lay.elements for p in e.pins]
    missing = {r for r in pins if not (isinstance(lay.netlist.get(r), str) and lay.netlist.get(r))}
    if not missing:
        return lay, set()
    copy = Layout.from_dict(lay.to_dict())
    copy.netlist = {r: n for r, n in copy.netlist.items() if r not in missing}
    copy.netlist.update({r: "?" + r for r in missing})
    return copy, missing


APPEARANCE_KEYS = ("fill", "border", "border_color", "title")
BOX_BORDERS = ("solid", "dashed", "dotted", "none")
GROUP_PAD = 0.3      # layout units between a group's drawn instance and its panel


def _appearance(raw, name):
    """An instance's ``appearance``: its folded box's fill, border style and
    colour (region values) and the title written in it."""
    if not isinstance(raw, dict) or set(raw) - set(APPEARANCE_KEYS) or not all(isinstance(v, str) and v for v in raw.values()):
        raise ModuleError(f"实例 {name}.appearance 只接受 {' / '.join(APPEARANCE_KEYS)}，值为文字")
    if raw.get("border", "solid") not in BOX_BORDERS:
        raise ModuleError(f"实例 {name}.appearance.border 只接受 {' / '.join(BOX_BORDERS)}")
    if raw.get("border") == "none" and raw.get("fill", "none") == "none":
        raise ModuleError(f"实例 {name}.appearance：无边框时要选填充色，否则方框看不见")
    return dict(raw)


def _groups(instances, already_hidden):
    """``group`` on one instance: it is drawn for ``members`` (instances of the
    same definition), which stay in the circuit but are drawn only as panels
    stacked behind it."""
    defs = {i.get("id"): i.get("definition") for i in instances if isinstance(i, dict)}
    out, taken = {}, set()
    for inst in instances:
        if not isinstance(inst, dict) or "group" not in inst:
            continue
        name, spec = inst["id"], inst["group"]
        where = f"实例 {name}.group"
        if not isinstance(spec, dict) or set(spec) - {"members", "fill", "label", "label_pos"} or not isinstance(spec.get("members"), list):
            raise ModuleError(f"{where} 需要 members（同一定义的其他实例 ID 列表），可选 fill / label / label_pos")
        members = spec["members"]
        if not members or len(set(members)) != len(members):
            raise ModuleError(f"{where}.members 不能为空或重复")
        for m in members:
            if m == name or m not in defs:
                raise ModuleError(f"{where}.members 中的 {m!r} 不是另一个实例")
            if defs[m] != defs[name]:
                raise ModuleError(f"{where}: {m} 用的是 {defs[m]}，不是 {defs[name]}；一组只能是同一个模块")
            if m in taken or m in already_hidden:
                raise ModuleError(f"{where}: {m} 已经在别的组里")
        if name in taken:
            raise ModuleError(f"{where}: {name} 本身已被别的组代表")
        for key in ("fill", "label", "label_pos"):
            if key in spec and not isinstance(spec[key], str):
                raise ModuleError(f"{where}.{key} 须是文字")
        taken.update(members)
        out[name] = spec
    if set(out) & taken:
        raise ModuleError("代表一组的实例不能同时是别的组的成员")
    return out


def _translate(lay, prefix, offset, bindings):
    """Copy explicit geometry and namespace IDs/local nets without routing."""
    from .blocks import problems as block_problems
    from .regions import problems as region_problems
    from .source_waves import problems as wave_problems
    issues = region_problems(lay, schema_only=True) + list(wave_problems(lay)) + block_problems(lay)
    if issues:
        raise ModuleError(f"{prefix or '顶层'}绘图字段有误：" + "；".join(issues))
    raw = deepcopy(lay.to_dict())
    def pt(p):
        return [round(p[0] + offset[0], 4), round(p[1] + offset[1], 4)]
    for e in raw["elements"]:
        e["id"] = prefix + e["id"]
        e["pins"] = {k: pt(p) for k, p in e["pins"].items()}
    raw["wires"] = [[pt(p) for p in w] for w in raw["wires"]]
    raw["hops"] = [pt(p) for p in raw["hops"]]
    for a in raw["annotations"]:
        a["at"] = pt(a["at"])
    for key in DECORATION_SECTIONS:
        for obj in raw.get(key, []):
            for field in ("from", "to", "at"):
                if field in obj:
                    obj[field] = pt(obj[field])
            if "via" in obj:
                obj["via"] = [pt(p) for p in obj["via"]]
            if "id" in obj:
                obj["id"] = prefix + obj["id"]
            if "members" in obj:
                obj["members"] = [prefix + n for n in obj["members"]]
    raw["netlist"] = {prefix + ref: bindings.get(net, prefix + "local:" + net) for ref, net in raw["netlist"].items()}
    return Layout.from_dict(raw)


def _tex_name(name):
    return "$\\mathrm{" + name.replace("_", r"\_") + "}$"


class _UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        self.parent[self.find(a)] = self.find(b)


@dataclass
class _Links:
    pairs: list           # [(ref, ref, merged net)], e.g. ("Vin.p", "M1.P", "local:P")
    linked: set           # {(instance, port)} reached by a link
    linked_globals: set   # bound net names that a link also reaches
    root_nets: dict       # top-level net -> merged net name
    nets: dict            # (instance, port) -> merged net name

    def port_net(self, inst, port):
        return self.nets[(inst, port)]


def _link_nets(source, spec, instances, templates, root_ports):
    """Merge nets joined by ``modules.links``.  Names only, no geometry."""
    raw = spec.get("links", [])
    if not isinstance(raw, list):
        raise ModuleError("modules.links 必须是端口连接列表，例如 [[\"Vin.p\", \"M1.P\"]]")
    defs = {i["id"]: i["definition"] for i in instances
            if isinstance(i, dict) and isinstance(i.get("id"), str) and i.get("definition") in templates}
    pins = set(source.netlist)
    uf = _UnionFind()
    def node(ref, where):
        inst, _, port = ref.rpartition(".") if isinstance(ref, str) else ("", "", "")
        if inst in defs:
            ports = templates[defs[inst]][1]
            if port not in ports:
                raise ModuleError(f"{where}: 实例 {inst} 没有端口 {port}；可用端口 {list(ports)}")
            return ("p", inst, port)
        if ref in pins:
            return ("r", source.netlist[ref])
        raise ModuleError(f"{where}: {ref!r} 既不是实例端口（如 M1.P）也不是顶层器件引脚（如 Vin.p）")
    pairs, linked, touched = [], set(), set()
    for k, pair in enumerate(raw):
        where = f"modules.links 第 {k + 1} 条"
        if not isinstance(pair, list) or len(pair) != 2 or pair[0] == pair[1]:
            raise ModuleError(f"{where} 必须是两个不同的端口名，例如 [\"Vin.p\", \"M1.P\"]")
        a, b = node(pair[0], where), node(pair[1], where)
        uf.union(a, b)
        touched.update((a, b))
        linked.update(n[1:] for n in (a, b) if n[0] == "p")
        pairs.append((pair[0], pair[1], a))
    for ref, name in root_ports.items():
        if ref in pins and isinstance(name, str):
            uf.union(("r", source.netlist[ref]), ("g", name))
    for inst in instances:
        if not isinstance(inst, dict) or inst.get("id") not in defs:
            continue
        lay, ports, _ = templates[defs[inst["id"]]]
        for port, name in (inst.get("bindings") or {}).items():
            if port in ports and isinstance(name, str):
                uf.union(("p", inst["id"], port), ("g", name))
        by_net = defaultdict(list)
        for port, ref in ports.items():
            by_net[lay.netlist[ref]].append(("p", inst["id"], port))
        for group in by_net.values():
            for n in group[1:]:
                uf.union(group[0], n)
    classes = defaultdict(list)
    for n in list(uf.parent):
        classes[uf.find(n)].append(n)
    linked_roots = {uf.find(n) for n in touched}
    merged, linked_globals = {}, set()
    for root, members in classes.items():
        names = sorted(n[1] for n in members if n[0] == "g")
        if len(set(names)) > 1:
            raise ModuleError(f"连接把公共网络 {' 和 '.join(sorted(set(names)))} 短接；请检查 links / bindings")
        nets = sorted(n[1] for n in members if n[0] == "r")
        ports = sorted(f"{n[1]}.{n[2]}" for n in members if n[0] == "p")
        merged[root] = ("global:" + names[0] if names else "local:" + nets[0] if nets
                        else "link:" + ports[0])
        if root in linked_roots:
            linked_globals.update(names)
    root_nets = {n[1]: merged[uf.find(n)] for n in uf.parent if n[0] == "r" and uf.find(n) in linked_roots}
    nets = {n[1:]: merged[uf.find(n)] for n in uf.parent if n[0] == "p"}
    pairs = [(a, b, merged[uf.find(n)]) for a, b, n in pairs]
    return _Links(pairs, linked, linked_globals, root_nets, nets)


def _wire_contacts(source, view, ports, provisional):
    """Read ordinary wires at public pins, using the same junction graph as parts.

    The source wires remain the authored drawing. Only the all-expanded view
    needs derived links, since its module terminals are at different positions.
    """
    from .modes import WireGraph, _k
    graph_layout = Layout.from_dict(source.to_dict())
    graph_layout.elements = [e for e in view.elements if "/" not in e.id]
    insulated = {_k(p) for p in source.hops}
    public = {ref: _k(p) for ref, p in ports.items() if _k(p) not in insulated}
    graph_layout.wires = [_split_joins(w, set(public.values())) for w in source.wires]
    graph = WireGraph(graph_layout)
    endpoints = defaultdict(list)
    for element in graph_layout.elements:
        for pin, point in element.pins.items():
            endpoints[_k(point)].append(f"{element.id}.{pin}")
    for ref, point in public.items():
        endpoints[point].append(ref)
    visited, touched, removed, pairs = set(), {}, set(), []
    for ref, point in public.items():
        if point in visited:
            continue
        pending, points, edges = [point], {point}, set()
        while pending:
            current = pending.pop()
            for neighbour, edge in graph.adj.get(current, []):
                edges.add(edge)
                if neighbour not in points:
                    points.add(neighbour)
                    pending.append(neighbour)
        visited.update(points)
        refs = list(dict.fromkeys(r for p in sorted(points) for r in endpoints[p]))
        refs.sort(key=lambda r: r in public)
        if not edges and len(refs) < 2:
            continue
        # An existing disagreement between ordinary pins still belongs to the
        # validator. A module contact must never legitimise that short circuit.
        declared = {source.netlist[r] for r in refs if r in source.netlist and r not in provisional}
        touched.update((r, declared) for r in refs if r in public)
        if len(declared) > 1:
            continue
        roots = [p for p in points if any(r not in public for r in endpoints[p])]
        # Preserve the authored bus between ordinary pins. Only its free tails
        # are replaced by routes to the all-expanded module terminals.
        kept = graph.tree(roots) or set()
        removed.update(edges - kept)
        pairs.extend([refs[0], other] for other in refs[1:])
    wires = []
    for index, run in enumerate(graph.runs):
        if not removed.intersection(run):
            wires.append(source.wires[index])
            continue
        path = []
        for edge_index in run:
            edge = graph.edges[edge_index]
            if edge_index in removed:
                if path:
                    wires.append(path)
                    path = []
            else:
                if not path:
                    path.append(edge.a)
                path.append(edge.b)
        if path:
            wires.append(path)
    return pairs, wires, touched


@dataclass
class _Shape:
    """One instance, bound to its nets, ready to draw in any display."""
    lay: Layout            # full local drawing
    partial: Layout | None # local drawing with blocks, if the module has one
    prefix: str
    mapped: dict
    ports: dict
    faces: dict            # port -> (-1, 0) left edge, (1, 0) right edge
    at: list | None
    display: str
    title: str
    symbol: str
    size: dict | None = None   # author's collapsed box {"w", "h"}; default COLLAPSED_SIZE
    port_layout: dict = field(default_factory=dict)
    style: dict = field(default_factory=dict)   # instance appearance: folded box fill / border / border_color
    unconnected: tuple = ()  # ports deliberately left open: NoConn on a folded box


@dataclass
class _Drawn:
    layout: Layout
    ports: dict            # port -> position in this drawing
    frame: list            # [x0, y0, x1, y1] as drawn
    joins: list            # port refs on one internal net, not joined by a line


def _faces(lay, ports, s, prof):
    """Ports in the left half face left, the others right."""
    where = {p: next(e for e in lay.elements if e.id == ref[:-2]).pins["p"] for p, ref in ports.items()}
    x0, _, x1, _ = _extent(lay, s, prof)
    return {p: (-1, 0) if where[p][0] <= (x0 + x1) / 2 else (1, 0) for p in ports}


def _port_points(lay, prefix, ports):
    return {p: tuple(next(e for e in lay.elements if e.id == prefix + ref[:-2]).pins["p"])
            for p, ref in ports.items()}


def _bare_joins(lay):
    groups = defaultdict(list)
    for e in lay.elements:
        if e.kind == "port" and e.opts.get("block"):
            groups[(e.opts["block"], lay.netlist[f"{e.id}.p"])].append(f"{e.id}.p")
    return [g for g in groups.values() if len(g) > 1]


def _shown(sh, mode, at, s, prof):
    exp = _translate(sh.lay, sh.prefix, at, sh.mapped)
    if mode == "partial":
        lay = _translate(sh.partial, sh.prefix, at, sh.mapped)
        return _Drawn(lay, _port_points(lay, sh.prefix, sh.ports), _extent(lay, s, prof), _bare_joins(lay))
    if mode == "collapsed" or not sh.lay.elements:
        return _collapsed(exp, sh, s, prof, at)
    return _Drawn(exp, _port_points(exp, sh.prefix, sh.ports), _extent(exp, s, prof), [])


def _collapsed(part, sh, s, prof, at):
    """A small block with the module name or a function mark, and its ports
    evenly spaced on the left and right edges, top to bottom in the order the
    expanded drawing has them."""
    prefix, ports = sh.prefix, sh.ports
    where = _port_points(part, prefix, ports)
    left = sorted((p for p in ports if sh.faces[p][0] < 0), key=lambda p: -where[p][1])
    right = sorted((p for p in ports if p not in left), key=lambda p: -where[p][1])
    if sh.port_layout:
        left.sort(key=lambda p: sh.port_layout[p]["order"])
        right.sort(key=lambda p: sh.port_layout[p]["order"])
    n = max(len(left), len(right), 1)
    marked = sh.symbol != "text"
    if sh.size:
        width, height = sh.size["w"], sh.size["h"]
    else:
        height = max(COLLAPSED_SIZE, n * PORT_PITCH)
        width = COLLAPSED_SIZE if marked else max(COLLAPSED_SIZE, len(sh.title) * TITLE_CHAR_WIDTH + TITLE_PAD)
    if left:
        x0 = min(where[p][0] for p in left)
    elif right:
        x0 = max(where[p][0] for p in right) - width
    else:
        x0 = _extent(part, s, prof)[0] if part.elements else at[0]
    x1 = x0 + width
    top = _extent(part, s, prof)[3] if part.elements else at[1]
    y1 = max([where[p][1] for p in ports] or [top]) + (height / n) / 2
    y0 = y1 - height
    box = Layout(part.name)
    placed, by_net = {}, defaultdict(list)
    for side, x, inside in ((left, x0, "right"), (right, x1, "left")):
        for i, p in enumerate(side):
            placed[p] = (round(x, 4), round(y1 - (i + 0.5) * height / len(side), 4))
            ref = ports[p]
            el = box.add(prefix + ref[:-2], "port", label=None if marked else _tex_name(p),
                         label_pos=inside, p=placed[p])
            box.netlist[f"{el.id}.p"] = part.netlist[prefix + ref]
            by_net[box.netlist[f"{el.id}.p"]].append(f"{el.id}.p")
    frame = [round(v, 4) for v in (x0, y0, x1, y1)]
    style = sh.style or {}
    box.extra["regions"] = [{"id": prefix + "box", "from": frame[:2], "to": frame[2:], "fill": style.get("fill", "none"),
                             "border": style.get("border", "solid"),
                             **({} if style.get("border") == "none" else {"border_color": style.get("border_color", "#000000")})}]
    if not marked:
        box.text((round((x0 + x1) / 2, 4), round((y0 + y1) / 2, 4)), sh.title)
    else:
        k = 0 if sh.symbol in CONVERTERS else GLYPH_INSET
        box.extra["glyphs"] = [{"kind": sh.symbol, "from": [frame[0] + k, frame[1] + k],
                                "to": [frame[2] - k, frame[3] - k]}]
        box.text((round((x0 + x1) / 2, 4), round(y1 + TITLE_GAP, 4)), sh.title)
    top = y1 + (TITLE_GAP * 2 if marked else 0)
    return _Drawn(box, placed, [frame[0], frame[1], frame[2], round(top, 4)],
                  [g for g in by_net.values() if len(g) > 1])


def _link_order(pair, ends):
    """Module-to-module links first, so the shared buses exist before a
    source or load branches onto them."""
    a, b, _ = pair
    return sum(1 for r in (a, b) if r in ends and not ends[r][2])


def _root_ends(elements):
    """Top-level pins a link may end on, with the way the pin faces: outwards
    along the lead for a two-pin part, free for a terminal circle."""
    ends = {}
    for e in elements:
        pts = list(e.pins.values())
        cx, cy = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
        for pin, p in e.pins.items():
            dx, dy = p[0] - cx, p[1] - cy
            face = None if e.kind == "port" or (dx, dy) == (0, 0) else (
                (0, 1 if dy > 0 else -1) if abs(dy) > abs(dx) else (1 if dx > 0 else -1, 0))
            ends[f"{e.id}.{pin}"] = (tuple(p), face, False)
    return ends


# Router for module links, in layout units.  It only chooses among a few
# orthogonal shapes and never moves a part. If no legal route exists, report
# the layout problem instead of manufacturing an unchecked fallback wire.
ROUTE_STEP = 0.4      # spacing of the detours tried beside ports and parts
ROUTE_TRIES = 6       # detours tried on each side
# grid states the fallback router may expand before giving up: a route that
# exists is found long before this; an impossible one would otherwise search
# the whole grid on every scale pass (seconds each)
GRID_ROUTE_BUDGET = 40000
_ROUTING = threading.local()


@contextmanager
def route_budget(states):
    """For diagnostics that try many sizes, in this thread only: a smaller
    search, and a link that cannot be routed fails the drawing instead of
    getting a fallback line. The search order does not depend on the budget,
    so a route found under it is the one the full budget finds."""
    old = getattr(_ROUTING, "budget", None)
    _ROUTING.budget = states
    try:
        yield
    finally:
        _ROUTING.budget = old


def _frozen(value):
    return tuple(_frozen(v) for v in value) if isinstance(value, (list, tuple)) else value


@lru_cache(maxsize=64)
def _cached_grid_route(a_end, b_end, wires, pins, bodies, same, target_wires, budget):
    # A check and the editor's projection assemble the same drawing, so the
    # same (often failing) search comes twice; inputs are frozen tuples.
    path = _grid_route(a_end, b_end, wires, pins, bodies, same, target_wires, budget=budget)
    return None if path is None else tuple(path)


BODY_HALF = 0.3       # half-width kept clear around a part's pin line
SWITCH_HALF = 0.6     # a switch symbol reaches this far beside its pins
EPS = 1e-6
# Rough terminal-label box, enough to keep a link off a port name.
PORT_LABEL_GAP = 0.12
PORT_LABEL_HEIGHT = 0.4
PORT_LABEL_CHAR = 0.22


def _label_box(e):
    text = re.sub(r"\\[a-z]+|[${}]", "", e.label or "")
    if not text:
        return None
    (x, y), w, h = e.pins["p"], len(text) * PORT_LABEL_CHAR + 0.1, PORT_LABEL_HEIGHT
    d = PORT_LABEL_GAP
    cx, cy = {"left": (x - d - w / 2, y), "right": (x + d + w / 2, y),
              "above": (x, y + d + h / 2), "below": (x, y - d - h / 2)}[e.label_pos or "left"]
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def _bodies(lay, s, prof):
    out = []
    for e in lay.elements:
        if e.kind == "port":
            if (box := _label_box(e)) is not None:
                out.append(box)
            continue
        xs = [p[0] for p in e.pins.values()]; ys = [p[1] for p in e.pins.values()]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        half = SWITCH_HALF if e.kind in SWITCHES else BODY_HALF
        if x1 - x0 < EPS:
            x0, x1 = x0 - half, x1 + half
        if y1 - y0 < EPS:
            y0, y1 = y0 - half, y1 + half
        out.append((x0, y0, x1, y1))
    if lay.extra.get("module_authoring"):
        from .geometry import footprint
        for e in lay.elements:
            fp = footprint(e, s, prof)
            out.extend((b.x0, b.y0, b.x1, b.y1) for b in fp.labels)
    return out


def _segs(path):
    return list(zip(path, path[1:]))


def _on(p, a, b, interior=False):
    if abs(a[0] - b[0]) < EPS and abs(p[0] - a[0]) < EPS:
        lo, hi = sorted((a[1], b[1])); v = p[1]
    elif abs(a[1] - b[1]) < EPS and abs(p[1] - a[1]) < EPS:
        lo, hi = sorted((a[0], b[0])); v = p[0]
    else:
        return False
    return lo + EPS < v < hi - EPS if interior else lo - EPS <= v <= hi + EPS


def _cross(a, b, c, d):
    """Interior crossing point of a horizontal and a vertical segment."""
    if abs(a[1] - b[1]) < EPS and abs(c[0] - d[0]) < EPS:
        h, v = (a, b), (c, d)
    elif abs(a[0] - b[0]) < EPS and abs(c[1] - d[1]) < EPS:
        h, v = (c, d), (a, b)
    else:
        return None
    x, y = v[0][0], h[0][1]
    if _on((x, y), *h, interior=True) and _on((x, y), *v, interior=True):
        return (x, y)
    return None


def _overlap(a, b, c, d):
    if abs(a[1] - b[1]) < EPS and abs(c[1] - d[1]) < EPS and abs(a[1] - c[1]) < EPS:
        lo, hi = max(min(a[0], b[0]), min(c[0], d[0])), min(max(a[0], b[0]), max(c[0], d[0]))
    elif abs(a[0] - b[0]) < EPS and abs(c[0] - d[0]) < EPS and abs(a[0] - c[0]) < EPS:
        lo, hi = max(min(a[1], b[1]), min(c[1], d[1])), min(max(a[1], b[1]), max(c[1], d[1]))
    else:
        return False
    return hi - lo > EPS


def _through(a, b, box):
    x0, y0, x1, y1 = box
    if abs(a[1] - b[1]) < EPS:
        lo, hi = sorted((a[0], b[0]))
        return y0 + EPS < a[1] < y1 - EPS and min(hi, x1) - max(lo, x0) > EPS
    lo, hi = sorted((a[1], b[1]))
    return x0 + EPS < a[0] < x1 - EPS and min(hi, y1) - max(lo, y0) > EPS


def _route(a_end, b_end, lay, same=(), *, s, prof):
    """Return a clean path, insulated hop points and same-net junctions.
    Shapes tried: straight, one bend, and two-bend paths whose middle leg is
    centred or set ROUTE_STEP apart beside either end.  ``same`` holds link
    wires already drawn on this net: a path may run along one and branch off
    it, and the shared stretch is dropped so the branch meets it in a T."""
    (a, fa, sa), (b, fb, sb) = a_end, b_end
    a, b = (round(a[0], 4), round(a[1], 4)), (round(b[0], 4), round(b[1], 4))
    authored = bool(lay.extra.get("module_authoring"))
    if authored:
        from .modes import WireGraph
        if WireGraph(lay).tree([a, b]) is not None:
            return [], [], set()
    wires = [s for w in lay.wires for s in _segs([tuple(p) for p in w])]
    pins = [tuple(p) for e in lay.elements for p in e.pins.values()]
    bodies = _bodies(lay, s, prof)
    def offsets(u, v):
        # Half-step corridors are needed between a user-picked terminal and
        # an adjacent existing source; library ports usually have more room.
        fine = [p + sgn * ROUTE_STEP / 2 for p in (u, v) for sgn in (-1, 1)] if same else []
        return [u, v, (u + v) / 2] + fine + [u + k * ROUTE_STEP * sgn for k in range(1, ROUTE_TRIES + 1)
                                       for sgn in (1, -1)] + [v + k * ROUTE_STEP * sgn
                                       for k in range(1, ROUTE_TRIES + 1) for sgn in (1, -1)]
    shapes = [[a, b], [a, (a[0], b[1]), b], [a, (b[0], a[1]), b]]
    shapes += [[a, (x, a[1]), (x, b[1]), b] for x in offsets(a[0], b[0])]
    shapes += [[a, (a[0], y), (b[0], y), b] for y in offsets(a[1], b[1])]
    # Ports selected on an existing circuit may need to leave both faces before
    # turning around a body; the library's two-bend candidates cannot do that.
    if same:
        xmin, ymin, xmax, ymax = _extent(lay, s, prof)
        for y in (ymin - ROUTE_STEP, ymax + ROUTE_STEP):
            for k in (1, 2, 4):
                ax = a[0] + (fa[0] if fa else -1) * k * ROUTE_STEP
                bx = b[0] + (fb[0] if fb else 1) * k * ROUTE_STEP
                shapes.append([a, (ax, a[1]), (ax, y), (bx, y), (bx, b[1]), b])
    best = None
    index = _Index(wires, pins, bodies)   # every candidate asks about the same drawing
    for raw in shapes:
        path = [tuple(round(v, 4) for v in p) for p in raw]
        path = [p for i, p in enumerate(path) if i == 0 or p != path[i - 1]]
        if len(path) < 2 or any(abs(p[0] - q[0]) > EPS and abs(p[1] - q[1]) > EPS for p, q in _segs(path)):
            continue
        # merge straight runs so bends are counted honestly
        path = [p for i, p in enumerate(path) if i in (0, len(path) - 1)
                or not (_on(p, path[i - 1], path[i + 1]))]
        if not _leaves(path, a, fa, sa) or not _leaves(path, b, fb, sb):
            continue
        path = _trim(_trim(path, same)[::-1], same)[::-1]
        cost = _cost(path, (a, fa, sa), (b, fb, sb), wires, pins, bodies, same,
                     ROUTE_STEP * 0.75 if lay.extra.get("module_authoring") else 0, index)
        if cost is not None and (best is None or cost < best[0]):
            best = (cost, path)
    target_wires = []
    if best is None:
        # Join any point of the target's existing wire tree; forcing a new
        # path all the way to its pin would duplicate an occupied bus segment.
        from .modes import WireGraph
        graph = WireGraph(lay)
        reachable, pending = {b}, [b]
        while pending:
            for q, _ in graph.adj.get(pending.pop(), []):
                if q not in reachable:
                    reachable.add(q); pending.append(q)
        target_wires = [w for w in wires if w[0] in reachable and w[1] in reachable and _same_wire(w, same)]
    if best:
        path = best[1]
    else:
        path = _cached_grid_route(*map(_frozen, (a_end, b_end, wires, pins, bodies, same, target_wires)),
                                  getattr(_ROUTING, "budget", None) or GRID_ROUTE_BUDGET)
        path = path and list(path)
    if not path:
        raise ModuleError("该尺寸无法布线：请增大模块间距或调整方框尺寸，原连接不会改成无效折线")
    return _crossings(path, wires, same, authored)


def _crossings(path, wires, same, authored):
    """The path split at same-net junctions, its insulated hops, and the
    junction points, against the wire segments already drawn."""
    joins = set()
    hops = []
    for p, q in _segs(path):
        for w in wires:
            c = _cross(p, q, *w)
            if c:
                own = authored and _same_wire(w, same)
                if own:
                    joins.add(c)
                else:
                    hops.append(c)
    return _split_joins(path, joins), [tuple(h) for h in hops], joins


def _manual_route(a_end, b_end, via, lay, same=()):
    """A link drawn along the user's own bend points (``modules.routes``),
    or, with no points, the plain right-angle fallback for a link the router
    could not place. Each end leaves along its pin or port face; any bend
    the points leave out is added so every segment stays horizontal or
    vertical. Nothing is checked here: the validator reports what it hits."""
    (a, fa, _), (b, fb, _) = a_end, b_end
    pts = [tuple(round(v, 4) for v in p) for p in [a, *via, b]]
    path = [pts[0]]
    for i, q in enumerate(pts[1:], 1):
        p = path[-1]
        if abs(p[0] - q[0]) > EPS and abs(p[1] - q[1]) > EPS:
            if i == 1:
                horizontal = not fa or abs(fa[0]) > abs(fa[1])
            elif i == len(pts) - 1:
                horizontal = bool(fb) and abs(fb[1]) > abs(fb[0])
            else:
                horizontal = abs(q[0] - p[0]) >= abs(q[1] - p[1])
            path.append((q[0], p[1]) if horizontal else (p[0], q[1]))
        path.append(q)
    path = [p for i, p in enumerate(path) if i == 0 or p != path[i - 1]]
    path = [p for i, p in enumerate(path) if i in (0, len(path) - 1) or not _on(p, path[i - 1], path[i + 1])]
    # where it runs along a wire of its own net, it meets that wire in a T
    path = _trim(_trim(path, same)[::-1], same)[::-1]
    wires = [s for w in lay.wires for s in _segs([tuple(p) for p in w])]
    return _crossings(path, wires, same, bool(lay.extra.get("module_authoring")))


def _routes(spec):
    """``modules.routes``: hand-drawn link paths, keyed "A|B" by the two names
    of a link, each a list of bend points. A key no link uses is ignored, so
    renaming or removing a link simply falls back to automatic routing."""
    raw = spec.get("routes", {})
    if not isinstance(raw, dict):
        raise ModuleError("modules.routes 必须是对象：键为 \"端口A|端口B\"，值为拐点列表")
    out = {}
    for key, points in raw.items():
        a, sep, b = key.partition("|") if isinstance(key, str) else ("", "", "")
        if not sep or not a or not b:
            raise ModuleError(f"modules.routes 的键 {key!r} 须写成 \"端口A|端口B\"")
        if not isinstance(points, list) or not all(
                isinstance(p, list) and len(p) == 2 and all(isinstance(v, (int, float)) and math.isfinite(v) for v in p)
                for p in points):
            raise ModuleError(f"modules.routes[{key!r}] 须是 [x, y] 拐点列表")
        out[(a, b)] = [tuple(float(v) for v in p) for p in points]
    return out


def _split_joins(points, joins):
    result = []
    for p, q in _segs(points):
        result.append(tuple(p))
        result.extend(sorted((j for j in joins if _on(j, p, q, interior=True)),
                             key=lambda j: abs(j[0] - p[0]) + abs(j[1] - p[1])))
    return result + [tuple(points[-1])]


class _Index:
    """Spatial buckets over axis-aligned wires, pins, wire ends and bodies.

    ``_cost`` asks only about things within ``margin`` of one short segment,
    so a uniform grid of cells answers each query from a handful of entries
    instead of every wire in the drawing. Candidates are filtered by bounding
    box with the margin widened by EPS, then judged by the same predicates as
    before, so the router's result is unchanged; only the work shrinks.
    """
    CELL = 1.0

    def __init__(self, wires, pins, bodies):
        self.wires = list(wires)
        self.boxes = [(min(c[0], d[0]), min(c[1], d[1]), max(c[0], d[0]), max(c[1], d[1])) for c, d in self.wires]
        self._wire_cells = defaultdict(list)
        for i, box in enumerate(self.boxes):
            for cell in self._cells(box, 0):
                self._wire_cells[cell].append(i)
        self._at = {}
        self._pins = self._points(pins)
        self._ends = self._points([x for w in self.wires for x in w])
        self.bodies = list(bodies)
        self._body_cells = defaultdict(list)
        for i, box in enumerate(self.bodies):
            for cell in self._cells(box, 0):
                self._body_cells[cell].append(i)

    @classmethod
    def _cells(cls, box, margin):
        x0 = math.floor((box[0] - margin) / cls.CELL); x1 = math.floor((box[2] + margin) / cls.CELL)
        y0 = math.floor((box[1] - margin) / cls.CELL); y1 = math.floor((box[3] + margin) / cls.CELL)
        return [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]

    @classmethod
    def _points(cls, points):
        cells = defaultdict(list)
        for pt in points:
            cells[(math.floor(pt[0] / cls.CELL), math.floor(pt[1] / cls.CELL))].append(pt)
        return cells

    @staticmethod
    def _box(p, q):
        return (min(p[0], q[0]), min(p[1], q[1]), max(p[0], q[0]), max(p[1], q[1]))

    def _near(self, cells, items, boxes, box, margin):
        m = margin + 2 * EPS
        seen = set(); out = []
        for cell in self._cells(box, m):
            for i in cells.get(cell, ()):
                if i in seen:
                    continue
                seen.add(i)
                b = boxes[i]
                if b[0] <= box[2] + m and b[2] >= box[0] - m and b[1] <= box[3] + m and b[3] >= box[1] - m:
                    out.append(items[i])
        return out

    def wires_near(self, p, q, margin=0):
        return self._near(self._wire_cells, self.wires, self.boxes, self._box(p, q), margin)

    def wires_at(self, v):
        # Grid vertices are visited from every neighbour: remember each answer.
        hit = self._at.get(v)
        if hit is None:
            hit = self._at[v] = self._near(self._wire_cells, self.wires, self.boxes, (v[0], v[1], v[0], v[1]), 0)
        return hit

    def bodies_near(self, p, q):
        return self._near(self._body_cells, self.bodies, self.bodies, self._box(p, q), 0)

    def _points_near(self, cells, p, q):
        box = self._box(p, q); m = 2 * EPS
        out = []
        for cell in self._cells(box, m):
            for pt in cells.get(cell, ()):
                if box[0] - m <= pt[0] <= box[2] + m and box[1] - m <= pt[1] <= box[3] + m:
                    out.append(pt)
        return out

    def pins_near(self, p, q):
        return self._points_near(self._pins, p, q)

    def ends_near(self, p, q):
        return self._points_near(self._ends, p, q)


def _grid_route(a_end, b_end, wires, pins, bodies, same, target_wires=(), *, clearance=ROUTE_STEP * .75,
                budget=None):
    """Visibility-grid fallback for ports picked inside a user's old drawing.

    Unlike the library's two-bend paths, this can navigate around several
    existing components. It changes only derived module links, never source
    coordinates. All output still goes through the ordinary validator.
    Control AI passes its own printed-scale clearance; module callers retain
    the existing three-quarter-lane default.
    """
    import heapq
    a, fa, sa = a_end
    b, fb, sb = b_end
    a, b = tuple(a), tuple(b)
    coordinates = [a, b, *pins, *(p for w in wires for p in w)]
    xs = {p[0] for p in coordinates}
    ys = {p[1] for p in coordinates}
    for x0, y0, x1, y1 in bodies:
        xs.update((x0, x1)); ys.update((y0, y1))
    # The half-lane gives clearance around obstacles without depending on any
    # symbol-specific dimensions. Bound the grid to two lanes outside drawing.
    xs = sorted({round(x + k * ROUTE_STEP / 2, 4) for x in xs for k in (-4, -2, -1, 0, 1, 2, 4)})
    ys = sorted({round(y + k * ROUTE_STEP / 2, 4) for y in ys for k in (-4, -2, -1, 0, 1, 2, 4)})
    ix, iy = {x: i for i, x in enumerate(xs)}, {y: i for i, y in enumerate(ys)}
    start = (ix[a[0]], iy[a[1]], -1)
    index = _Index(wires, pins, bodies)
    queue, distance, previous, cache = [(0, start)], {start: 0}, {}, {}
    expanded = 0
    while queue:
        expanded += 1
        if budget is not None and expanded > budget:
            return None
        cost, state = heapq.heappop(queue)
        if cost != distance[state]:
            continue
        x, y, direction = state
        p = (xs[x], ys[y])
        if p == b or (p != a and any(_on(p, *w) for w in target_wires)):
            path = []
            while state != start:
                path.append((xs[state[0]], ys[state[1]])); state = previous[state]
            path.append(a); path.reverse()
            return [p for i, p in enumerate(path) if i in (0, len(path) - 1) or not _on(p, path[i - 1], path[i + 1])]
        for dx, dy, axis in ((1, 0, 0), (-1, 0, 0), (0, 1, 1), (0, -1, 1)):
            nx, ny = x + dx, y + dy
            # A hop must cross a wire inside a segment, never stop/turn on it.
            # Skip grid vertices on foreign wire interiors to permit that hop.
            while 0 <= nx < len(xs) and 0 <= ny < len(ys) and any(
                    _on((xs[nx], ys[ny]), *w, interior=True) and not _same_wire(w, same)
                    for w in index.wires_at((xs[nx], ys[ny]))):
                nx += dx; ny += dy
            if not (0 <= nx < len(xs) and 0 <= ny < len(ys)):
                continue
            q = (xs[nx], ys[ny])
            if p == a and not _leaves([p, q], a, fa, sa):
                continue
            if q == b and not _leaves([p, q], b, fb, sb):
                continue
            edge = (p, q)
            if edge not in cache:
                cache[edge] = _cost([p, q], a_end, b_end, wires, pins, bodies, same, clearance, index)
            extra = cache[edge]
            if extra is None:
                continue
            next_state = (nx, ny, axis)
            candidate = cost + extra + (1 if direction not in (-1, axis) else 0)
            if candidate < distance.get(next_state, float("inf")):
                distance[next_state] = candidate; previous[next_state] = state
                heapq.heappush(queue, (candidate, next_state))
    return None


def _leaves(path, end, face, strict):
    """Does the path leave ``end`` the way its pin faces?"""
    first = path[1] if path[0] == end else path[-2]
    d = (first[0] - end[0], first[1] - end[1])
    if face is None:
        return True
    along = d[0] * face[0] + d[1] * face[1]
    return along > EPS if strict else along > -EPS


def _trim(path, same):
    """Drop the start of ``path`` that runs along same-net wires."""
    while len(path) >= 2:
        p, q = path[0], path[1]
        cover = [w for w in same if _on(p, *w) and _overlap(p, q, *w)]
        if not cover:
            return path
        far = max((x for w in cover for x in (*w, q) if _on(x, p, q) and _on(x, *w)),
                  key=lambda x: abs(x[0] - p[0]) + abs(x[1] - p[1]))
        if far == q:
            path = path[1:]
        else:
            return [far] + path[1:]
    return path


def _cost(path, a_end, b_end, wires, pins, bodies, same=(), clearance=0, index=None):
    """``index``: an ``_Index`` over the same wires, pins and bodies; it only
    narrows each scan to nearby candidates, every predicate is unchanged."""
    (a, fa, sa), (b, fb, sb) = a_end, b_end
    if len(path) < 2:
        return None
    segs = _segs(path)
    ends = (path[0], path[-1])
    for p, q in segs:
        near = index.wires_near(p, q, clearance) if index else wires
        if clearance:
            # Reserve three quarters of a routing lane between parallel runs;
            # the final validator still checks actual printed-scale clearance.
            for c, d in near:
                horizontal = abs(p[1] - q[1]) < EPS and abs(c[1] - d[1]) < EPS
                vertical = abs(p[0] - q[0]) < EPS and abs(c[0] - d[0]) < EPS
                if horizontal or vertical:
                    along, across = (0, 1) if horizontal else (1, 0)
                    overlap = min(max(p[along], q[along]), max(c[along], d[along])) - max(min(p[along], q[along]), min(c[along], d[along]))
                    if overlap > EPS and EPS < abs(p[across] - c[across]) < clearance - EPS:
                        return None
        boxes = index.bodies_near(p, q) if index else bodies
        if any(_overlap(p, q, *w) for w in near) or any(_through(p, q, box) for box in boxes):
            return None
        if any(_on(x, p, q) for x in (index.pins_near(p, q) if index else pins) if x not in (a, b)
               and not (x in ends and any(_on(x, *w) for w in same))):
            return None
        if any(_on(x, p, q, interior=True) for x in (index.ends_near(p, q) if index else [x for w in wires for x in w])):
            return None
    for v in path:
        for w in (index.wires_at(v) if index else wires):
            if clearance and _on(v, *w) and not _same_wire(w, same):
                return None  # a foreign-net wire end is not a legal bend/junction
            # a branch may start on a wire of its own net: that is the junction
            if _on(v, *w, interior=True) and not (v in ends and _same_wire(w, same)):
                return None
    crossings = sum(1 for p, q in segs for w in (index.wires_near(p, q) if index else wires) if _cross(p, q, *w))
    length = sum(abs(p[0] - q[0]) + abs(p[1] - q[1]) for p, q in segs)
    soft = sum(0 if f is None or e not in ends or _leaves(path, e, f, True) else 1
               for e, f in ((a, fa), (b, fb)))
    return (len(segs) - 1) + 0.5 * crossings + 0.3 * soft + 0.05 * length


def _same_wire(wire, same):
    return any(_on(wire[0], *s) and _on(wire[1], *s) for s in same)


@dataclass
class Boxed:
    """A collapsed instance as the simulation exporters see it: its expanded
    drawing (ids prefixed, as placed in ``Assembly.full``), its public ports
    by name -> "Elem.p" ref in that drawing, and the box drawn for it in
    ``Assembly.exported`` [x0, y0, x1, y1] in layout units."""
    layout: Layout
    ports: dict
    box: list


@dataclass
class Assembly:
    full: Layout
    view: Layout
    links: list[list[str]]
    instances: list[str]
    hidden: list[str]
    joins: list[list[str]] = field(default_factory=list)   # collapsed ports on one internal net
    frames: dict = field(default_factory=dict)             # instance -> drawn [x0, y0, x1, y1]
    collapsed: list[str] = field(default_factory=list)
    partial: list[str] = field(default_factory=list)       # instances that can be partly collapsed
    # the drawing a simulation export lays its model out from when some
    # instance is collapsed: those as boxes, every other instance expanded,
    # nothing hidden; None when nothing is collapsed (the export uses ``full``)
    exported: Layout | None = None
    boxed: dict = field(default_factory=dict)              # collapsed instance -> Boxed
    # for the editor: which view wires draw which link (and whether by hand),
    # links the router could not place, and every instance port's position
    link_wires: list = field(default_factory=list)
    unrouted: list = field(default_factory=list)
    ports: dict = field(default_factory=dict)
    origins: dict = field(default_factory=dict)             # instance -> the ``at`` it is drawn at
    instance_wires: dict = field(default_factory=dict)      # instance -> [first, end) of its view wires
    wire_contacts: dict[str, set[str]] = field(default_factory=dict)  # public pin -> touched root net names


LIBRARY = Path(__file__).parent / "module_library"
AUTO_GAP = 1.0     # layout units between instances stacked without "at"
PART_GAP = 1.3     # layout units from a module port to a part placed beside it


def _partial(spec, full, ports, name):
    """The module drawn with some parts gathered into blocks.  Every part it
    still shows must match the full drawing pin for pin; ``hides`` lists the
    parts the blocks stand for, and together they must be the whole module."""
    where = f"模块 {name}.partial"
    _object(spec, ("layout", "hides"), (), where)
    try:
        lay = Layout.from_dict(spec["layout"])
    except (TypeError, ValueError, KeyError) as exc:
        raise ModuleError(f"{where}.layout 无法读取：{exc}") from exc
    _complete(lay, where)
    real = {e.id: e for e in full.elements if e.kind != "port"}
    shown = {e.id: e for e in lay.elements if e.kind != "port"}
    hides = spec["hides"]
    if not isinstance(hides, list) or set(hides) & set(shown) or set(hides) | set(shown) != set(real):
        raise ModuleError(f"{where}: 画出的器件加 hides 必须正好是全部器件 {sorted(real)}，且不重复")
    for i, e in shown.items():
        if e.kind != real[i].kind or {p: lay.netlist[f"{i}.{p}"] for p in e.pins} != {
                p: full.netlist[f"{i}.{p}"] for p in real[i].pins}:
            raise ModuleError(f"{where}: {i} 的类型或引脚网络与完整电路不一致")
    nets = set(full.netlist.values())
    for e in lay.elements:
        if e.kind == "port" and lay.netlist[f"{e.id}.p"] not in nets:
            raise ModuleError(f"{where}: 端子 {e.id} 的网络 {lay.netlist[f'{e.id}.p']} 在完整电路中不存在")
    for port, ref in ports.items():
        _terminal(lay, ref, f"{where} 端口 {port}")
    return lay


def library_names():
    return sorted(p.stem for p in LIBRARY.glob("*.json"))


def _library_for(instances, definitions):
    """Library definitions named by instances and not defined in the table."""
    wanted = {i.get("definition") for i in instances if isinstance(i, dict)}
    out = {}
    for name in wanted - set(definitions):
        if isinstance(name, str) and name in library_names():
            out[name] = json.loads((LIBRARY / f"{name}.json").read_text(encoding="utf-8"))
    return out


def _extent(lay, s, prof):
    xs = [q[0] for e in lay.elements for q in e.pins.values()] + [q[0] for w in lay.wires for q in w]
    ys = [q[1] for e in lay.elements for q in e.pins.values()] + [q[1] for w in lay.wires for q in w]
    from .regions import regions_of
    for r in regions_of(lay, s, prof):
        xs += [r.x0, r.x1]; ys += [r.y0, r.y1]
    return [min(xs, default=0), min(ys, default=0), max(xs, default=0), max(ys, default=0)]


def _stack_below(lay, placed, s, prof):
    """Where an instance without "at" goes: under the ones already placed,
    left edges aligned, AUTO_GAP apart."""
    if lay is None:
        return None
    x0, y0, _, y1 = _extent(lay, s, prof)
    top = min((_extent(p, s, prof)[1] for p in placed), default=None)
    dy = 0 if top is None else top - AUTO_GAP - y1
    return [0, round(dy, 4)]


def _with_parts(source, parts):
    """Add ``modules.parts`` (two-pin parts the program places, such as the
    source and the load) as top-level elements on their own nets."""
    if not parts:
        return source
    if not isinstance(parts, list):
        raise ModuleError("modules.parts 必须是列表，例如 [{\"id\":\"Vin\",\"kind\":\"V\"}]")
    out = Layout.from_dict(source.to_dict())
    taken = {e.id for e in out.elements}
    for k, part in enumerate(parts):
        _object(part, ("id", "kind"), ("label",), f"modules.parts 第 {k + 1} 项")
        name = _name(part["id"], "modules.parts 的 id")
        if name in taken or part["kind"] not in TWO_TERMINAL:
            raise ModuleError(f"modules.parts {name}: id 重复，或 kind 不是两端元件 {sorted(TWO_TERMINAL)}")
        taken.add(name)
        pins = PIN_NAMES[part["kind"]]
        out.add(name, part["kind"], label=part.get("label"), **{pins[0]: (0, 0), pins[1]: (0, 1)})
        out.netlist.update({f"{name}.{p}": f"part:{name}.{p}" for p in pins})
    return out


def _place_parts(out, parts, pairs, ports_at, faces):
    """Stand each part beside the module ports its two pins link to, as they
    are drawn now: on the left of left-edge ports, the right of right-edge
    ones, each pin level with its port so the link wires are straight."""
    for part in parts:
        el = next(e for e in out.elements if e.id == part["id"])
        spots = {}
        for a, b, _ in pairs:
            for mine, other in ((a, b), (b, a)):
                inst, _, port = other.rpartition(".")
                if mine.rpartition(".")[0] == el.id and (inst, port) in ports_at:
                    spots.setdefault(mine.rpartition(".")[2], (ports_at[(inst, port)], faces[(inst, port)][0]))
        if set(spots) != set(el.pins):
            raise ModuleError(f"自动放置的 {el.id} 每个引脚都要直接连到某个实例端口（modules.links）")
        (pa, (qa, fa)), (pb, (qb, fb)) = [(p, spots[p]) for p in el.pins]
        if fa != fb or abs(qa[1] - qb[1]) < 1e-6:
            raise ModuleError(f"自动放置的 {el.id} 两个引脚须连到同一侧、高度不同的端口")
        x = min(qa[0], qb[0]) - PART_GAP if fa < 0 else max(qa[0], qb[0]) + PART_GAP
        el.pins = {pa: (round(x, 4), qa[1]), pb: (round(x, 4), qb[1])}
        el.label_pos = el.label_pos or ("left" if fa < 0 else "right")


def _definitions(spec):
    definitions, instances = spec.get("definitions", {}), spec["instances"]
    if not isinstance(definitions, dict) or not isinstance(instances, list):
        raise ModuleError("modules.instances 必须是列表，definitions 必须是对象")
    definitions = {**_library_for(instances, definitions), **definitions}
    if not definitions:
        raise ModuleError(f"modules.definitions 为空；可直接引用模块库 {library_names()}")
    return definitions


def _read_definition(name, definition):
    _name(name, "模块定义 ID")
    _object(definition, ("layout", "ports"), ("defaults", "title", "symbol", "partial", "port_layout", "editable", "source"),
            f"模块 {name}")
    if "source" in definition:
        from .module_store import check_source
        check_source(definition["source"], f"模块 {name}")
    if not isinstance(definition["layout"], dict):
        raise ModuleError(f"模块 {name}.layout 必须是坐标表对象")
    allowed = {"name", "elements", "wires", "hops", "annotations", "netlist", *DECORATION_SECTIONS}
    if set(definition["layout"]) - allowed:
        raise ModuleError(f"模块 {name}.layout 有未知字段；嵌套模块、模态和仿真参数不放在这里")
    try:
        lay = Layout.from_dict(definition["layout"])
    except (TypeError, ValueError, KeyError) as exc:
        raise ModuleError(f"模块 {name}.layout 无法读取：{exc}") from exc
    if lay.elements:
        _complete(lay, f"模块 {name}")
        if not any(e.kind != "port" for e in lay.elements):
            raise ModuleError(f"模块 {name} 只有端口，没有实际器件；不能把占位框当作完整电路")
    elif ("elements" not in definition["layout"] or "netlist" not in definition["layout"]
          or lay.netlist or lay.wires or lay.hops or lay.annotations or lay.extra or definition["ports"]):
        raise ModuleError(f"模块 {name} 没有器件；空子系统不能包含导线、网表或端口")
    for e in lay.elements:
        _name(e.id, f"模块 {name} 器件 ID")
    ports = definition["ports"]
    if not isinstance(ports, dict) or not all(isinstance(v, str) for v in ports.values()) or len(set(ports.values())) != len(ports):
        raise ModuleError(f"模块 {name}.ports 必须是不重复的端口映射")
    for port, ref in ports.items():
        _name(port, "公开端口名")
        _terminal(lay, ref, f"模块 {name} 端口 {port}")
    port_layout = definition.get("port_layout", {})
    if "port_layout" in definition:
        if not isinstance(port_layout, dict) or set(port_layout) != set(ports):
            raise ModuleError(f"模块 {name}.port_layout 必须逐一列出端口 {list(ports)}")
        for p, placement in port_layout.items():
            _object(placement, ("side", "order"), (), f"端口 {name}.{p}")
            if placement["side"] not in ("left", "right") or type(placement["order"]) is not int or placement["order"] < 0:
                raise ModuleError(f"端口 {name}.{p}: side 只接受 left / right，order 为非负整数")
        if len({v["order"] for v in port_layout.values()}) != len(port_layout):
            raise ModuleError(f"模块 {name}.port_layout 的 order 不能重复")
    editable = definition.get("editable", [])
    from .spice import FIELDS, OPTIONAL_FIELDS
    allowed_parameters = {(e.id, k) for e in lay.elements
                          for k, _, _ in FIELDS.get(e.kind, []) + OPTIONAL_FIELDS.get(e.kind, [])}
    if not isinstance(editable, list):
        raise ModuleError(f"模块 {name}.editable 必须是参数列表")
    for param in editable:
        _object(param, ("element", "field"), (), f"模块 {name}.editable")
        if (param["element"], param["field"]) not in allowed_parameters:
            raise ModuleError(f"模块 {name}.editable 含未知元件参数")
    defaults = definition.get("defaults", {})
    if not isinstance(defaults, dict) or set(defaults) - {e.id for e in lay.elements if e.kind != "port"}:
        raise ModuleError(f"模块 {name}.defaults 含未知器件；这里只写各器件的仿真参数")
    title = definition.get("title", "")
    if not isinstance(title, str):
        raise ModuleError(f"模块 {name}.title 必须是文字，例如 \"LLC\"")
    symbol = definition.get("symbol", "text")
    if symbol not in SYMBOLS:
        raise ModuleError(f"模块 {name}.symbol 只接受 {' / '.join(SYMBOLS)}")
    partial = _partial(definition["partial"], lay, ports, name) if "partial" in definition else None
    return lay, ports, defaults, title, symbol, partial


def assemble(source: Layout, scale: Scale | None = None, prof=None) -> Assembly:
    scale = scale or Scale(1, 1)
    prof = prof or get_profile("double")
    # Older saved authoring projects may still contain boundary posts. Derive
    # direct pin links without rewriting their stored coordinate table.
    if isinstance(source.extra.get("module_authoring"), dict):
        from .module_authoring import direct_boundary_links
        source = direct_boundary_links(source)
    spec = source.extra.get("modules")
    _object(spec, ("version", "instances"), ("definitions", "root_ports", "links", "parts", "routes"))
    if type(spec["version"]) is not int or spec["version"] != 1:
        raise ModuleError("modules.version 当前只支持 1")
    instances = spec["instances"]
    definitions = _definitions(spec)
    source = _with_parts(source, spec.get("parts", []))
    view_mode = source.extra.get("module_view", "expanded")
    if view_mode not in ("expanded", "folded"):
        raise ModuleError("module_view 只接受 expanded / folded")
    if source.modes and instances:
        raise ModuleError("模块初版支持静态图和 SPICE；请在普通展开电路中编辑模态，不能忽略模块的跨端口回路")
    templates, titles, symbols, partials = {}, {}, {}, {}
    for name, definition in definitions.items():
        lay, ports, defaults, title, symbol, partial = _read_definition(name, definition)
        templates[name] = (lay, ports, defaults)
        titles[name], symbols[name] = title, symbol
        if partial is not None:
            partials[name] = partial
    root_ports = spec.get("root_ports", {})
    if not isinstance(root_ports, dict):
        raise ModuleError("modules.root_ports 必须是 port 引脚到公共网络名的映射")
    provisional = set()
    if source.elements:
        source, provisional = _provisional_nets(source)
        _complete(source, "顶层电路")
    resolve = _link_nets(source, spec, instances, templates, root_ports)
    routes = _routes(spec)
    links = defaultdict(list)
    def bind(lay, terminals, values, prefix):
        nets = {}
        for port, ref in terminals.items():
            name = _name(values[port], f"{prefix}{port} 的网络名")
            el = _terminal(lay, ref, f"{prefix}{port}")
            net = lay.netlist[ref]
            if net in nets and nets[net] != "global:" + name:
                raise ModuleError(f"{prefix}{port}: 同一内部网络绑定了不同公共网络")
            nets[net] = "global:" + name
            # Printed net labels make these non-geometric joins explicit.
            el.label = _tex_name(name)
            links[name].append(prefix + ref)
        return nets
    root = Layout.from_dict(source.to_dict())
    for key in ("modules", "module_view", "stack_usage", "simulation", "electrical"):
        root.extra.pop(key, None)
    root_nets = bind(root, {r: r for r in root_ports}, root_ports, "")
    root_nets.update(resolve.root_nets)
    root = _translate(root, "", (0, 0), root_nets)
    shapes = {}
    actual_ids = set(e.id for e in root.elements)
    names = []
    values = {}
    for inst in instances:
        _object(inst, ("id", "definition"), ("at", "bindings", "overrides", "label", "display", "symbol", "collapsed_size",
                                            "unconnected", "appearance", "group"), "模块实例")
        name = _name(inst["id"], "实例 ID")
        if name in names or not isinstance(inst["definition"], str) or inst["definition"] not in templates:
            raise ModuleError(f"实例 {name}: ID 重复或模块定义不存在")
        names.append(name)
        at = inst.get("at")
        if at is not None and (not isinstance(at, list) or len(at) != 2
                               or any(type(v) not in (int, float) or not math.isfinite(v) for v in at)):
            raise ModuleError(f"实例 {name}.at 必须是两个有限坐标；省略时由程序从上到下排列")
        original, ports, defaults = templates[inst["definition"]]
        cfg = inst.get("bindings", {})
        if not isinstance(cfg, dict) or set(cfg) - set(ports):
            raise ModuleError(f"实例 {name}.bindings 只能绑定公开端口 {list(ports)}")
        unconnected = inst.get("unconnected", [])
        if (not isinstance(unconnected, list) or any(not isinstance(p, str) or p not in ports for p in unconnected)
                or len(set(unconnected)) != len(unconnected)
                or any(p in cfg or (name, p) in resolve.linked for p in unconnected)):
            raise ModuleError(f"实例 {name}.unconnected 只能列出未连接的公开端口 {list(ports)}")
        for port in ports:
            if port not in cfg and (name, port) not in resolve.linked and port not in unconnected:
                raise ModuleError(f"实例 {name} 的端口 {port} 悬空：bindings 和 modules.links 都没有连接它")
        display = inst.get("display", "expanded")
        if display not in DISPLAYS:
            raise ModuleError(f"实例 {name}.display 只接受 {' / '.join(DISPLAYS)}")
        if display == "partial" and inst["definition"] not in partials:
            raise ModuleError(f"实例 {name}: 模块 {inst['definition']} 没有部分折叠画法（views.partial）；"
                              f"可用 partial 的模块：{sorted(partials) or '无'}")
        if display != "expanded" and cfg:
            raise ModuleError(f"实例 {name} 折叠显示时，端口 {list(cfg)} 须改用 modules.links 连接；"
                              "旧表可先用“转换为端口连接”")
        symbol = inst.get("symbol", symbols[inst["definition"]])
        if symbol not in SYMBOLS:
            raise ModuleError(f"实例 {name}.symbol 只接受 {' / '.join(SYMBOLS)}")
        size = inst.get("collapsed_size")
        if size is not None and (not isinstance(size, dict) or set(size) != {"w", "h"} or any(
                type(v) not in (int, float) or not math.isfinite(v) or v < MIN_BOX for v in size.values())):
            raise ModuleError(f"实例 {name}.collapsed_size 必须是 {{\"w\": 宽, \"h\": 高}}，每边至少 {MIN_BOX}")
        diff = inst.get("overrides", {})
        if not isinstance(diff, dict) or set(diff) - {e.id for e in original.elements if e.kind != "port"}:
            raise ModuleError(f"实例 {name}.overrides 含未知器件；这里只覆盖仿真参数")
        definition = definitions[inst["definition"]]
        if "editable" in definition:
            editable = {(p["element"], p["field"]) for p in definition["editable"]}
            if any(not isinstance(v, dict) or any((eid, k) not in editable for k in v) for eid, v in diff.items()):
                raise ModuleError(f"实例 {name}.overrides 只能修改模块勾选的 editable 参数")
        lay = Layout.from_dict(original.to_dict())
        views = [lay] + ([Layout.from_dict(partials[inst["definition"]].to_dict())]
                         if inst["definition"] in partials else [])
        if "label" in inst:
            label = inst["label"]
            _object(label, ("at", "text"), (), f"实例 {name}.label")
            pos = label["at"]
            if (not isinstance(label["text"], str) or not isinstance(pos, list) or len(pos) != 2
                    or any(type(v) not in (int, float) or not math.isfinite(v) for v in pos)):
                raise ModuleError(f"实例 {name}.label 需要文字和有限的局部坐标 at")
            lay.text(tuple(pos), label["text"])
        prefix = name + "/"
        mapped = bind(lay, {p: ports[p] for p in cfg}, cfg, prefix)
        faces = _faces(lay, ports, scale, prof)
        port_layout = definition.get("port_layout", {})
        if port_layout:
            faces = {p: (-1, 0) if port_layout[p]["side"] == "left" else (1, 0) for p in ports}
        for port, ref in ports.items():
            if port in cfg:
                continue
            if port in unconnected:
                continue
            net = resolve.port_net(name, port)
            if mapped.get(lay.netlist[ref], net) != net:
                raise ModuleError(f"实例 {name}.{port}: 同一内部网络连到了不同网络")
            mapped[lay.netlist[ref]] = net
            for view in views:
                el = _terminal(view, ref, f"{prefix}{port}")
                el.label = None if "editable" in definition else _tex_name(port)
                # a linked wire leaves on the port's outer side; keep the name off it
                if el.label_pos in (None, "left" if faces[port][0] < 0 else "right"):
                    el.label_pos = "above"
        if len(views) > 1 and "label" in inst:
            top = _extent(views[1], scale, prof)
            views[1].text(((top[0] + top[2]) / 2, top[3] + PARTIAL_TITLE_GAP), inst["label"]["text"])
        style = _appearance(inst.get("appearance", {}), name)
        title = style.pop("title", None) or (inst["label"]["text"] if "label" in inst else titles[inst["definition"]] or name)
        ids = {prefix + e.id for e in lay.elements}
        if actual_ids & ids:
            raise ModuleError(f"实例 {name} 展开后的器件 ID 与顶层重复")
        actual_ids.update(ids)
        values.update({prefix + k: v for k, v in _merge(defaults, diff).items()})
        shapes[name] = _Shape(views[0], views[1] if len(views) > 1 else None, prefix, mapped,
                              ports, faces, at, display, title, symbol, size, port_layout, style,
                              tuple(inst.get("unconnected", [])))
    for name, refs in links.items():
        if len(refs) < 2 and name not in resolve.linked_globals:
            raise ModuleError(f"公共网络 {name} 只有一个端口 {refs[0]}；请检查 bindings 拼写或补齐另一端")
    from .completeness import inspect_stacks
    completeness = inspect_stacks(source, resolved_instances=names)
    hidden = []
    if view_mode == "folded":
        if not completeness["ok"]:
            raise ModuleError("折叠前请核对叠面板用途：" + "；".join(completeness["issues"]))
        grouped = set()
        for usage in source.extra.get("stack_usage", {}).values():
            if usage.get("purpose") == "instances":
                group = usage["instances"]
                if grouped.intersection(group):
                    raise ModuleError("同一实例不能出现在多个折叠面板中")
                grouped.update(group)
                hidden.extend(group[1:])
        if not hidden:
            raise ModuleError("folded 需要 stack_usage 中声明实际实例组，第一项为保留的前面板")
    groups = _groups(instances, set(hidden))
    for group in groups.values():
        hidden.extend(group["members"])
    contact_pairs, contact_wires, touched_ports, contact_ports = [], source.wires, {}, {}
    def drawing(omit, folded, shown, decorated=True, routed=True, project_contacts=False):
        out = Layout.from_dict(root.to_dict())
        if project_contacts:
            out.wires = deepcopy(contact_wires)
        if not decorated:
            for key in DECORATION_SECTIONS:
                out.extra.pop(key, None)
            out.annotations = []
        usages = source.extra.get("stack_usage", {})
        usages = usages if isinstance(usages, dict) else {}
        if not folded:
            for region in out.extra.get("regions", []):
                usage = usages.get(region.get("id"))
                if isinstance(usage, dict) and usage.get("purpose") == "instances":
                    region.pop("stack", None)
        ports_at, faces, frames, joins, placed, drawings = {}, {}, {}, [], [], {}
        meta = {"link_wires": [], "unrouted": [], "ports": {}, "origins": {}, "instance_wires": {}}
        for name in names:
            if name in omit:
                continue
            sh = shapes[name]
            mode = shown(sh)
            origin = list(sh.at or [0, 0])
            drawn = _shown(sh, mode, origin, scale, prof)
            if sh.at is None and placed:
                # stack under the instances already drawn, as they are drawn now
                target = min(f[1] for f in placed) - AUTO_GAP
                origin = [0, round(target - drawn.frame[3], 4)]
                drawn = _shown(sh, mode, origin, scale, prof)
            meta["origins"][name] = origin
            placed.append(drawn.frame)
            frames[name] = drawn.frame
            if name in groups and all(m in omit for m in groups[name]["members"]):
                # one instance drawn for a group: the others are panels behind it
                gspec, f, pad = groups[name], drawn.frame, GROUP_PAD
                panel = {"id": f"{name}/group", "from": [round(f[0] - pad, 4), round(f[1] - pad, 4)],
                         "to": [round(f[2] + pad, 4), round(f[3] + pad, 4)], "fill": gspec.get("fill", "gray"),
                         "stack": {"count": len(gspec["members"]), "dx": 0.2, "dy": 0.2}}
                if gspec.get("label"):
                    panel.update(label=gspec["label"], label_pos=gspec.get("label_pos", "below"))
                out.extra.setdefault("regions", []).append(panel)
            joins += drawn.joins
            drawings[name] = drawn.layout
            ports_at.update({(name, p): q for p, q in drawn.ports.items()})
            faces.update({(name, p): f for p, f in sh.faces.items()})
            copy = drawn.layout
            meta["instance_wires"][name] = [len(out.wires), len(out.wires) + len(copy.wires)]
            out.elements.extend(copy.elements); out.wires.extend(copy.wires)
            out.hops.extend(copy.hops); out.annotations.extend(copy.annotations)
            out.netlist.update(copy.netlist)
            for key in DECORATION_SECTIONS:
                out.extra.setdefault(key, []).extend(copy.extra.get(key, []))
            if mode == "collapsed" and sh.unconnected:
                # a folded box's open port is a lone pin: mark it as meant to be open
                out.extra.setdefault("no_connect", []).extend(
                    sh.prefix + sh.ports[p] for p in sh.unconnected if f"{name}.{p}" not in touched_ports)
        # A hidden member's external circuit still needs an endpoint. Reuse a
        # visible port only on the SAME net; otherwise retain a named terminal.
        representatives = {member: lead for lead, group in groups.items() for member in group["members"]}
        for name in names:
            if name not in omit:
                continue
            sh = shapes[name]
            needed = [p for p in sh.ports if (name, p) in resolve.linked]
            if not needed:
                continue
            origin = list(sh.at or [0, 0])
            terminals = _shown(sh, "collapsed", origin, scale, prof)
            if sh.at is None and placed:
                origin[1] = round(min(f[1] for f in placed) - AUTO_GAP - terminals.frame[3], 4)
                terminals = _shown(sh, "collapsed", origin, scale, prof)
            retained = False
            for port in needed:
                ref = sh.prefix + sh.ports[port]
                net = terminals.layout.netlist[ref]
                lead = representatives.get(name)
                peer = (lead, port)
                contact = contact_ports.get(f"{name}.{port}") if f"{name}.{port}" in touched_ports else None
                if contact is None and peer in ports_at and out.netlist[shapes[lead].prefix + shapes[lead].ports[port]] == net:
                    ports_at[(name, port)], faces[(name, port)] = ports_at[peer], faces[peer]
                else:
                    el = terminals.layout.element(ref[:-2])
                    if contact is not None:
                        el.pins["p"] = tuple(contact)
                    elif sh.at is None and peer in ports_at:
                        el.pins["p"] = (ports_at[peer][0], el.pins["p"][1])
                    el.label = _tex_name(f"{name}.{port}")
                    out.elements.append(el)
                    out.netlist[ref] = net
                    ports_at[(name, port)], faces[(name, port)] = el.pins["p"], sh.faces[port]
                    retained = True
            if retained:
                placed.append(terminals.frame)
        _place_parts(out, spec.get("parts", []), resolve.pairs, ports_at, faces)
        ends = _root_ends([e for e in out.elements if "/" not in e.id])
        ends.update({f"{n}.{p}": (q, faces[(n, p)], True) for (n, p), q in ports_at.items()})
        drawn_nets = defaultdict(list)
        if source.extra.get("module_authoring") or (project_contacts and contact_pairs):
            # Reuse authored same-net buses rather than laying a duplicate link
            # over them. Pin names are already resolved by the module compiler.
            from .modes import WireGraph
            graph = WireGraph(out)
            net_at = {pt: out.netlist[f"{e.id}.{p}"] for e in out.elements for p, pt in e.pins.items()}
            pending = list(net_at)
            while pending:
                pt = pending.pop()
                for nxt, _ in graph.adj.get(pt, []):
                    if nxt not in net_at:
                        net_at[nxt] = net_at[pt]
                        pending.append(nxt)
            for w in out.wires:
                for a, b in _segs([tuple(p) for p in w]):
                    if a in net_at:
                        drawn_nets[net_at[a]].append((a, b))
        def route_order(pair):
            # Connect fixed source/load pins before buses can block their short
            # exit corridors; upper ports take the inner bus, lower ports outer.
            height = min((ends[r][0][1] for r in pair[:2] if r in ends), default=0)
            return (-_link_order(pair, ends), -height) if source.extra.get("module_authoring") else (_link_order(pair, ends), 0)
        meta["ports"] = {f"{n}.{p}": list(q) for (n, p), q in ports_at.items()}
        routed_pairs = resolve.pairs if project_contacts else resolve.pairs[:len(spec.get("links", []))]
        for a, b, net in sorted(routed_pairs, key=route_order):
            if a in ends and b in ends:
                if ends[a][0] == ends[b][0]:
                    continue  # extracted boundary terminals coincide while expanded
                if project_contacts and [a, b] in contact_pairs:
                    from .modes import WireGraph
                    if WireGraph(out).tree([ends[a][0], ends[b][0]]) is not None:
                        continue
                # hand-drawn routes belong to the picture they were drawn on
                same_view = routed and not any(r.rpartition(".")[0] in omit for r in (a, b))
                manual = routes.get((a, b)) if same_view else None
                if manual is None and same_view and (b, a) in routes:
                    manual = routes[(b, a)][::-1]
                if manual is not None:
                    path, hops, junctions = _manual_route(ends[a], ends[b], manual, out, drawn_nets[net])
                else:
                    try:
                        path, hops, junctions = _route(ends[a], ends[b], out, drawn_nets[net], s=scale, prof=prof)
                    except ModuleError:
                        if getattr(_ROUTING, "budget", None):
                            raise   # a size search only needs to know it fails
                        # draw a plain right-angle link and report it, so the
                        # figure stays editable and the user can drag it clear
                        path, hops, junctions = _manual_route(ends[a], ends[b], [], out, drawn_nets[net])
                        meta["unrouted"].append([a, b])
                if junctions:
                    out.wires = [_split_joins(w, junctions) for w in out.wires]
                    out.hops = [h for h in out.hops if tuple(h) not in junctions]
                if len(path) >= 2:
                    out.wires.append(path)
                    meta["link_wires"].append({"wire": len(out.wires) - 1, "link": [a, b], "manual": manual is not None})
                out.hops.extend(hops)
                drawn_nets[net].extend(_segs([tuple(p) for p in path]))
        return out, frames, joins, drawings, meta
    # Root regions, blocks, arrows and texts are placed for the drawn view.
    # When that view folds an instance, the expanded layout is a different
    # picture used only for connectivity and simulation, so it is checked
    # without them (they are checked on the view they were drawn for).
    differs = bool(hidden) or any(shapes[n].display != "expanded" for n in names)
    view, frames, joins, _, view_meta = drawing(hidden, view_mode == "folded", lambda sh: sh.display)
    contact_view, contact_meta = view, view_meta
    if hidden:
        contact_view, _, _, _, contact_meta = drawing([], False, lambda sh: sh.display)
    contact_ports = contact_meta["ports"]
    contact_pairs, contact_wires, touched_ports = _wire_contacts(source, contact_view, contact_ports, provisional)
    if touched_ports:
        resolve = _link_nets(source, {**spec, "links": spec.get("links", []) + contact_pairs},
                             instances, templates, root_ports)
        root.netlist = {ref: resolve.root_nets.get(net, root_nets.get(net, "local:" + net))
                        for ref, net in source.netlist.items()}
        for name, sh in shapes.items():
            for port, ref in sh.ports.items():
                if (name, port) in resolve.linked:
                    sh.mapped[sh.lay.netlist[ref]] = resolve.port_net(name, port)
        view, frames, joins, _, view_meta = drawing(hidden, view_mode == "folded", lambda sh: sh.display)
    full, _, _, expanded, _ = drawing([], False, lambda sh: "expanded", decorated=not differs,
                                       routed=not differs, project_contacts=differs)
    for ref in provisional:          # undeclared, as the editor's netlist panel shows them
        full.netlist.pop(ref, None); view.netlist.pop(ref, None)
    collapsed = {n for n in names if shapes[n].display != "expanded"}
    # The simulation exporters draw a collapsed instance as a subsystem box
    # and everything else expanded; the view already is that drawing unless
    # it folds stacks or shows a partly collapsed instance.
    exported, boxed = None, {}
    boxes = [n for n in names if shapes[n].display == "collapsed"]
    if boxes:
        exported = view
        if hidden or any(shapes[n].display == "partial" for n in names):
            exported = drawing([], False, lambda sh: "collapsed" if sh.display == "collapsed" else "expanded",
                               routed=False, project_contacts=True)[0]
        for n in boxes:
            region = next(r for r in exported.extra["regions"] if r.get("id") == shapes[n].prefix + "box")
            boxed[n] = Boxed(expanded[n], {p: shapes[n].prefix + ref for p, ref in shapes[n].ports.items()},
                             [*region["from"], *region["to"]])
    sim = deepcopy(source.extra.get("simulation", {}))
    if sim:
        if not isinstance(sim, dict) or not isinstance(sim.get("components", {}), dict):
            raise ModuleError("顶层 simulation.components 必须是对象")
        root_values = sim.get("components", {})
        if set(root_values) - {e.id for e in source.elements if e.kind != "port"}:
            raise ModuleError("顶层 simulation.components 只填顶层器件；实例参数写 defaults / overrides")
        sim["components"] = {**values, **root_values}
        full.extra["simulation"] = sim
    if "electrical" in source.extra:
        full.extra["electrical"] = deepcopy(source.extra["electrical"])
    from .module_authoring import validate_metadata
    validate_metadata(source, full)
    return Assembly(full, view, list(links.values()), names, hidden, joins, frames,
                    sorted(collapsed), [n for n in names if shapes[n].partial is not None],
                    exported, boxed, view_meta["link_wires"], view_meta["unrouted"], view_meta["ports"],
                    view_meta["origins"], view_meta["instance_wires"], touched_ports)


# Definition geometry is independent of the final figure's column width.
# The local fit still uses the requested profile's symbol/font dimensions.
DEFINITION_WIDTH_MARGIN = 1.0  # cm beyond the definition's largest tested extent
MODULE_SCALE_PASSES = 12      # bound the fit/assembly feedback for derived regions


def _definition_issues(issues):
    """A net split already names its open pins; keep their details on that issue."""
    from .validate import Issue
    grouped = list(issues)
    details = defaultdict(list)
    consumed = set()
    for index, issue in enumerate(issues):
        if issue.code != "open-pin":
            continue
        ref = issue.message.split(" at ", 1)[0]
        for other, split in enumerate(issues):
            if split.code == "netlist-open" and re.search(r"(?<![\w/.-])" + re.escape(ref) + r"(?![\w/.-])", split.message):
                details[other].append(issue.message)
                consumed.add(index)
                break
    return [Issue(i.code, i.message + ("；相关断线位置：" + "；".join(details[n]) if n in details else ""), i.level)
            for n, i in enumerate(grouped) if n not in consumed]


def check_definitions(source, prof):
    """Check each shared definition once, without imposing the figure's width."""
    from .validate import Issue, Report, check, extent
    report = Report(Scale(1, 1))
    spec = source.extra.get("modules")
    if not isinstance(spec, dict):
        raise ModuleError("modules 必须是对象")
    for name, definition in _definitions(spec).items():
        try:
            lay, _, _, _, _, partial = _read_definition(name, definition)
            if not lay.elements:
                if any(isinstance(i, dict) and i.get("definition") == name for i in spec["instances"]):
                    report.issues.append(Issue("empty-module", f"模块定义 {name} 是空子系统草稿；请进入定义搭好电路后再导出"))
                continue
            drawings = [("", lay, ())]
            if partial is not None:
                drawings.append(("（partial）", partial, _bare_joins(partial)))
            for suffix, drawing, joins in drawings:
                b = extent(drawing, Scale(prof.max_scale, prof.max_scale), prof)
                local_prof = replace(prof, width_cm=max(prof.width_cm, b.x1 - b.x0 + DEFINITION_WIDTH_MARGIN))
                rep = check(drawing, local_prof, connections=joins)
                report.issues.extend(Issue(i.code, f"模块定义 {name}{suffix}：{i.message}", i.level)
                                     for i in _definition_issues(rep.issues) if i.code != "too-cramped")
        except (ValueError, TypeError, KeyError) as exc:
            report.issues.append(Issue("bad-module", f"模块定义 {name}：{exc}"))
    return report


def _instance_issues(issues, source, assembly):
    """Attribute expanded IDs; combine identical internal problems across copies.

    Cross-instance/root interactions and non-element owners stay separate:
    equal-looking text must never hide a distinct wiring or placement error.
    """
    from .validate import Issue
    spec = source.extra["modules"]
    definitions = _definitions(spec)
    refs, offsets = {}, {}
    elements = {e.id: e for e in assembly.full.elements}
    for inst in spec["instances"]:
        name, definition = inst["id"], inst["definition"]
        for el in Layout.from_dict(definitions[definition]["layout"]).elements:
            ref = name + "/" + el.id
            refs[ref] = (name, definition, el.id)
            if ref in elements and el.pins:
                pin = next(iter(el.pins))
                p, q = elements[ref].pins[pin], el.pins[pin]
                offsets[name] = (p[0] - q[0], p[1] - q[1])
    ids = set(elements) | {e.id for e in assembly.view.elements}
    # Element IDs can share prefixes; a pin suffix is allowed but an ID suffix is not.
    pattern = re.compile(r"(?<![\w/-])(" + "|".join(re.escape(i) for i in sorted(ids, key=len, reverse=True)) + r")(?![\w/-])")
    groups, out = {}, []
    for issue in issues:
        hits = list(dict.fromkeys(pattern.findall(issue.message)))
        targets = [refs[h] for h in hits if h in refs]
        if not targets:
            out.append(issue)
            continue
        names = list(dict.fromkeys(n for n, _, _ in targets))
        detail = "；".join(f"实例 {n}，定义 {d} 的 {e}" for n, d, e in targets)
        annotated = Issue(issue.code, issue.message + f"（{detail}）", issue.level)
        internal = len(names) == 1 and all(h in refs for h in hits) and not re.search(r"wire\[|annotation\[|regions\[|callouts\[|\btext ", issue.message)
        if internal:
            name = names[0]
            canonical = issue.message.replace(name + "/", "")
            dx, dy = offsets[name]
            canonical = re.sub(r"\((-?[\d.]+), (-?[\d.]+)\)",
                               lambda m: f"({float(m[1])-dx:.3f}, {float(m[2])-dy:.3f})", canonical)
            key = (issue.code, issue.level, targets[0][1], canonical)
            if key in groups:
                index, affected, original = groups[key]
                if name not in affected:
                    affected.append(name)
                    out[index] = Issue(issue.code, original + "；受影响实例：" + "、".join(affected), issue.level)
                continue
            groups[key] = (len(out), [name], annotated.message)
        out.append(annotated)
    return out


def _view_links(assembly):
    refs = assembly.view.netlist
    return [[r for r in group if r in refs] for group in assembly.links] + assembly.joins


def checked(source, prof, scale=None):
    from .validate import check, fit_scale, collapsed_box_issues
    definitions = check_definitions(source, prof)
    drawn_scale = scale or Scale(1, 1)
    try:
        assembly = assemble(source, drawn_scale, prof)
    except (ValueError, TypeError, KeyError):
        if not any(i.code != "empty-module" for i in definitions.errors):
            raise
        definitions.scale = drawn_scale
        return None, definitions, definitions
    if any(i.code != "empty-module" for i in definitions.errors):
        # Preserve a derived preview, but do not run assembled checks that would
        # repeat the shared error for every instance or obscure it with fallout.
        definitions.scale = drawn_scale
        return assembly, definitions, definitions
    if scale is None:
        tried = [drawn_scale]
        for _ in range(MODULE_SCALE_PASSES):
            actual = fit_scale(assembly.view, prof, connections=_view_links(assembly))
            if max(abs(a-b) for a, b in zip(actual, drawn_scale)) < 1e-8:
                break
            if assembly.unrouted:
                break   # already flagged for the user to route; do not re-search every pass
            drawn_scale = actual
            tried.append(actual)
            assembly = assemble(source, drawn_scale, prof)
        else:
            # Routing and labels depend on the scale and the fit can swing
            # between values. Draw at the smallest one tried: the narrowest
            # figure, assembled and checked at that same scale.
            drawn_scale = min(tried[1:], key=lambda sc: (sc[0], sc[1]))
            assembly = assemble(source, drawn_scale, prof)
    view_report = check(assembly.view, prof, drawn_scale, connections=_view_links(assembly))
    from .validate import Issue
    for a, b in assembly.unrouted:
        view_report.issues.append(Issue("module-route", f"连接 {a} — {b} 自动布线失败（该尺寸无法布线）：已先画一条直角线；"
                                        "拖动这条线改走向，或增大模块间距、调整方框尺寸"))
    view_report.issues.extend(collapsed_box_issues(assembly.view, assembly.collapsed, drawn_scale, prof))
    # Hidden instances are checked at the printed scale. Collapsed internals
    # retain their own expanded scale, as before.
    full_scale = None if assembly.collapsed else view_report.scale
    full_report = check(assembly.full, prof, full_scale, connections=assembly.links)
    keys = {(i.code, i.message) for i in view_report.issues}
    combined = definitions.issues + view_report.issues + [i for i in full_report.issues if (i.code, i.message) not in keys]
    view_report.issues = _instance_issues(combined, source, assembly)
    full_report.issues = _instance_issues(definitions.issues + full_report.issues, source, assembly)
    return assembly, full_report, view_report


def upgrade_links(source: Layout) -> Layout:
    """Rewrite an older source whose instances meet by shared net names
    (``bindings`` / ``root_ports``) into ``modules.links``.  Each named net
    becomes a chain of port-to-port links in the order the ports are listed;
    the old open-terminal symbols stay as link ends.  A source that already
    uses links is returned unchanged.  The legacy stacked view is dropped:
    collapse instances with ``display`` instead."""
    raw = deepcopy(source.to_dict())
    spec = raw.get("modules")
    if not isinstance(spec, dict) or "links" in spec:
        return Layout.from_dict(raw)
    ends = defaultdict(list)
    for ref, name in spec.get("root_ports", {}).items():
        ends[name].append(ref)
    for inst in spec.get("instances", []):
        for port, name in inst.pop("bindings", {}).items():
            ends[name].append(f"{inst['id']}.{port}")
    spec.pop("root_ports", None)
    spec["links"] = [[a, b] for refs in ends.values() for a, b in zip(refs, refs[1:])]
    for key in ("module_view", "stack_usage"):
        raw.pop(key, None)
    raw["regions"] = [r for r in raw.get("regions", []) if not set(r.get("members", ())) & {
        f"{i['id']}/{m}" for i in spec.get("instances", []) for m in
        (e["id"] for e in spec["definitions"][i["definition"]]["layout"]["elements"])}]
    if not raw["regions"]:
        raw.pop("regions")
    assemble(Layout.from_dict(raw))   # refuse a conversion that does not resolve
    return Layout.from_dict(raw)
