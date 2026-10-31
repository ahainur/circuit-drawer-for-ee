"""Transactional, graphical module authoring. No drawing is replaced until checked."""
from __future__ import annotations

import re
from collections import defaultdict
from copy import deepcopy

from .layout import DECORATION_SECTIONS, Layout
from .modules import ModuleError, _name, assemble
from .modes import WireGraph
from .profiles import get_profile
from .validate import check


def _valid(lay, profile):
    report = check(lay, get_profile(profile))
    if not report.ok:
        raise ModuleError("请先修正电路：" + "；".join(i.message for i in report.errors[:8]))
    return report


def _assembles(lay, profile):
    """A new instance only has to make a valid circuit. Where it lands on
    the drawing (overlapping a part, a label on a wire) is reported on the
    canvas like any other drawing problem and fixed by dragging it away."""
    report = check(lay, get_profile(profile))
    broken = [i for i in report.errors if i.code.startswith("bad-")]
    if broken:
        raise ModuleError("无法放置：" + "；".join(i.message for i in broken[:4]))
    return report


def _unique(base, used):
    name, i = base, 1
    while name in used:
        name = f"{base}{i}"
        i += 1
    used.add(name)
    return name


def selection(source, ids, ports=(), profile="double"):
    """Split existing wire edges at explicit pins/vertices, never across a hop.

    The internal tree joins selected pins. Every attachment to the remaining
    drawing must be a marked boundary, including branches in the middle of a wire.
    """
    ids = set(ids)
    if not ids:
        raise ModuleError("请先框选或 Shift 点选要封装的元件，再存为模块")
    chosen = [e for e in source.elements if e.id in ids]
    if not chosen or len(chosen) != len(ids):
        raise ModuleError("请选择存在的普通元件；不能把展开的实例子元件再次封装")
    _valid(source, profile)
    # The root has explicit geometry even when other instances already exist.
    graph = WireGraph(source)
    by_net, at_net, outside = defaultdict(list), {}, set()
    for e in source.elements:
        for pin, pt in e.pins.items():
            net = source.netlist[f"{e.id}.{pin}"]
            at_net[pt] = net
            if e.id in ids:
                by_net[net].append(pt)
            else:
                outside.add(pt)
    # Propagate names along wires (never through a component).
    pending = list(at_net)
    while pending:
        pt = pending.pop()
        for nxt, _ in graph.adj.get(pt, []):
            if nxt not in at_net:
                at_net[nxt] = at_net[pt]
                pending.append(nxt)
    for p in ports:
        pt = tuple(p["at"])
        if pt not in at_net or at_net[pt] not in by_net:
            raise ModuleError("端口必须点击所选电路的节点")
        by_net[at_net[pt]].append(pt)
    internal = set()
    for net, points in by_net.items():
        tree = graph.tree(points)
        if tree is None:
            raise ModuleError(f"网络 {net} 的所选引脚不能沿现有导线连通")
        internal.update(tree)
        if not any(at_net.get(p) == net for p in outside):
            internal.update(i for i, edge in enumerate(graph.edges) if at_net.get(edge.a) == net)
    inside_pts = {p for pts in by_net.values() for p in pts}
    inside_pts.update(p for i in internal for p in (graph.edges[i].a, graph.edges[i].b))
    boundary = {p for p in inside_pts if p in outside or any(i not in internal for _, i in graph.adj.get(p, []))}
    # Existing name links can touch root pins without a geometric wire.
    for pair in source.extra.get("modules", {}).get("links", []):
        for ref in pair:
            eid, _, pin = ref.rpartition(".")
            if eid in ids:
                boundary.add(next(e for e in chosen if e.id == eid).pins[pin])
    marked = {tuple(p["at"]) for p in ports}
    from .spice import FIELDS, OPTIONAL_FIELDS
    params = source.extra.get("simulation", {}).get("components", {})
    parameters = [{"element": e.id, "field": key, "label": label,
                   "value": params.get(e.id, {}).get(key, "")}
                  for e in chosen for key, label, _ in FIELDS.get(e.kind, []) + OPTIONAL_FIELDS.get(e.kind, [])]
    nodes = [{"at": list(p), "net": at_net[p], "boundary": p in boundary}
             for p in sorted(at_net) if at_net[p] in by_net]
    return {"nodes": nodes, "missing": [list(p) for p in sorted(boundary - marked)],
            "parameters": parameters, "internal": sorted(internal), "graph": graph,
            "at_net": at_net, "chosen": chosen}


def inspect_selection(source, ids, ports=(), profile="double"):
    info = selection(source, ids, ports, profile)
    return {k: info[k] for k in ("nodes", "missing", "parameters")}


def direct_boundary_links(source):
    """Replace synthetic boundary posts and their wire trees with pin links.

    Only authoring-owned ports are eligible; intentional user terminals and
    unrelated wiring remain ordinary geometry. A tree must reach a real root
    pin before it can be replaced, so a free boundary is never silently lost.
    """
    meta = source.extra.get("module_authoring", {})
    if not isinstance(meta, dict):
        return source
    terminals = meta.get("terminals", [])
    if not isinstance(terminals, list):
        return source
    anchors = {e.id for e in source.elements if e.kind == "port" and e.id in terminals}
    if not anchors:
        return source
    graph = WireGraph(source)
    refs = {}
    for e in source.elements:
        for pin, pt in e.pins.items():
            refs.setdefault(pt, []).append(f"{e.id}.{pin}")
    raw = deepcopy(source.to_dict())
    spec = raw["modules"]
    linked = {r for pair in spec.get("links", []) for r in pair}
    replacement, removed, visited, extra = {}, set(), set(), []
    for e in source.elements:
        if e.id not in anchors or f"{e.id}.p" not in linked:
            continue
        start = e.pins["p"]
        if start in visited:
            continue
        points, edges, pending = {start}, set(), [start]
        while pending:
            for q, index in graph.adj.get(pending.pop(), []):
                edges.add(index)
                if q not in points:
                    points.add(q); pending.append(q)
        visited.update(points)
        ends = sorted(r for p in points for r in refs.get(p, []) if r.rpartition(".")[0] not in anchors)
        posts = [r for p in points for r in refs.get(p, []) if r.rpartition(".")[0] in anchors]
        if len({source.netlist.get(r) for r in ends + posts}) != 1:
            continue  # never turn an existing short into a declared connection
        if not ends or any(r not in linked or r in spec.get("root_ports", {}) for r in posts):
            continue
        replacement.update({r: ends[0] for r in posts})
        extra.extend([ends[0], r] for r in ends[1:])
        removed.update(edges)
    if not replacement:
        return source
    deleted = {r.rpartition(".")[0] for r in replacement}
    raw["elements"] = [e for e in raw["elements"] if e["id"] not in deleted]
    raw["netlist"] = {r: n for r, n in raw["netlist"].items() if r not in replacement}
    raw["module_authoring"]["terminals"] = [r for r in terminals if r not in deleted]
    raw["wires"] = [[e.a, e.b] for i, e in enumerate(graph.edges) if i not in removed]
    from .modules import _on
    raw["hops"] = [h for h in raw["hops"] if not any(_on(tuple(h), graph.edges[i].a, graph.edges[i].b) for i in removed)]
    pairs = [[replacement.get(r, r) for r in pair] for pair in spec.get("links", [])] + extra
    spec["links"] = [list(p) for p in dict.fromkeys(tuple(p) for p in pairs if p[0] != p[1])]
    return Layout.from_dict(raw)


def extract(source, ids, name, ports, exposed, profile="double"):
    _name(name, "模块名")
    info = selection(source, ids, ports, profile)
    if info["missing"]:
        raise ModuleError("这些节点仍与外部相连，请标成端口：" + str(info["missing"]))
    if len({p["name"] for p in ports}) != len(ports) or len({tuple(p["at"]) for p in ports}) != len(ports):
        raise ModuleError("端口名和节点不能重复")
    for p in ports:
        _name(p["name"], "端口名")
        if p["side"] not in ("left", "right"):
            raise ModuleError("端口边只能是 left / right")
    raw = deepcopy(source.to_dict())
    spec = raw.setdefault("modules", {"version": 1, "definitions": {}, "instances": [], "links": []})
    definitions = spec.setdefault("definitions", {})
    if name in definitions:
        raise ModuleError(f"模块 {name} 已存在，请换名或编辑定义")
    ids = set(ids)
    instance = _unique("M", {i["id"] for i in spec["instances"]} | {e.id for e in source.elements})
    points = [p for e in info["chosen"] for p in e.pins.values()]
    origin = [min(p[0] for p in points), min(p[1] for p in points)]
    local = Layout(name)
    local.elements = deepcopy(info["chosen"])
    local.netlist = {r: n for r, n in source.netlist.items() if r.rpartition(".")[0] in ids}
    raw["elements"] = [e for e in raw["elements"] if e["id"] not in ids]
    raw["netlist"] = {r: n for r, n in raw["netlist"].items() if r.rpartition(".")[0] not in ids}
    internal = set(info["internal"])
    graph = info["graph"]
    local.wires = [(e.a, e.b) for i, e in enumerate(graph.edges) if i in internal]
    raw["wires"] = [[list(e.a), list(e.b)] for i, e in enumerate(graph.edges) if i not in internal]
    # A crossing marker moves into the module when every wire through it moved
    # there; one shared with a remaining wire stays at root and is checked on
    # the full drawing, where the instance sits at its authored place.
    from .modules import _on
    def through(h, wires):
        return any(_on(h, tuple(a), tuple(b)) for a, b in wires)
    local.hops = [tuple(h) for h in source.hops if through(tuple(h), local.wires) and not through(tuple(h), raw["wires"])]
    raw["hops"] = [h for h in raw.get("hops", []) if tuple(h) not in local.hops]
    local.annotations = []
    x0, y0 = origin
    x1, y1 = max(p[0] for p in points), max(p[1] for p in points)
    raw["annotations"] = []
    for a in source.annotations:
        if x0 <= a.at[0] <= x1 and y0 <= a.at[1] <= y1:
            local.annotations.append(deepcopy(a))
        else:
            raw["annotations"].append(a.to_dict())
    for key in DECORATION_SECTIONS:
        remaining, moved = [], []
        for obj in raw.get(key, []):
            members = set(obj.get("members", []))
            if members and members <= ids:
                moved.append(obj)
            else:
                if members:
                    obj["members"] = [f"{instance}/{m}" if m in ids else m for m in obj["members"]]
                remaining.append(obj)
        if key in raw:
            raw[key] = remaining
        if moved:
            local.extra[key] = moved
    author = raw.setdefault("module_authoring", {"aliases": {}, "order": [e.id for e in source.elements], "terminals": []})
    author["aliases"].update({f"{instance}/{e.id}": e.id for e in info["chosen"]})
    if raw.get("modes"):
        author["modes"] = raw.pop("modes")
    used = {e.id for e in local.elements}
    root_used = {e["id"] for e in raw["elements"]}
    port_map, display, unconnected = {}, {}, []
    # Recompute boundary after the user has extended a tree to another node.
    inside_pts = {p for e in info["chosen"] for p in e.pins.values()}
    inside_pts.update(p for i in internal for p in (graph.edges[i].a, graph.edges[i].b))
    outside_pts = {p for e in source.elements if e.id not in ids for p in e.pins.values()}
    boundary = {p for p in inside_pts if p in outside_pts or any(i not in internal for _, i in graph.adj.get(p, []))}
    for i, p in enumerate(ports):
        pt, pname = tuple(p["at"]), p["name"]
        eid = _unique("Port_" + pname, used)
        local.add(eid, "port", p=pt)
        local.netlist[eid + ".p"] = info["at_net"][pt]
        port_map[pname] = eid + ".p"
        display[pname] = {"side": p["side"], "order": i}
        author["terminals"].append(f"{instance}/{eid}")
        replaced = False
        for pair in spec.setdefault("links", []):
            for j, ref in enumerate(pair):
                rid, _, pin = ref.rpartition(".")
                if rid in ids and next(e for e in info["chosen"] if e.id == rid).pins[pin] == pt:
                    pair[j] = f"{instance}.{pname}"
                    replaced = True
        if pt in boundary:
            anchor = _unique("Anchor_" + pname, root_used)
            raw["elements"].append({"id": anchor, "kind": "port", "pins": {"p": list(pt)}})
            raw["netlist"][anchor + ".p"] = info["at_net"][pt]
            spec["links"].append([anchor + ".p", f"{instance}.{pname}"])
            author["terminals"].append(anchor)
        elif not replaced:
            unconnected.append(pname)
    # Local coordinates are an exact translation of the authored drawing.
    from .modules import _translate
    translated = _translate(local, "", [-x0, -y0], {n: n for n in local.netlist.values()})
    params = raw.get("simulation", {}).get("components", {})
    defaults = {eid: params.pop(eid) for eid in list(params) if eid in ids}
    allowed = {(p["element"], p["field"]) for p in info["parameters"]}
    if any((p["element"], p["field"]) not in allowed for p in exposed):
        raise ModuleError("公开参数含未知元件或字段")
    definitions[name] = {"layout": translated.to_dict(), "ports": port_map, "defaults": defaults,
                         "port_layout": display, "editable": exposed, "title": name}
    spec["instances"].append({"id": instance, "definition": name, "at": origin,
                              "unconnected": unconnected, "display": "expanded"})
    result = direct_boundary_links(Layout.from_dict(raw))
    _valid(result, profile)
    if source.extra.get("simulation"):
        from .spice import circuit_deck as make_deck
        if make_deck(source, profile).text != make_deck(result, profile).text:
            raise ModuleError("封装改变了网表；已取消保存，原图保持不变")
    return result


def spice_identity(source, flat, report):
    """Keep device names, pin comments, device order and node numbering stable.

    Electrical connectivity still comes exclusively from the checked full graph;
    this metadata cannot supply or override a net or a component parameter.
    """
    meta = source.extra.get("module_authoring")
    if not meta:
        return flat, report
    from dataclasses import replace
    flat = Layout.from_dict(flat.to_dict())
    aliases = meta["aliases"]
    terminals = set(meta["terminals"])
    def ref(r):
        eid, dot, pin = r.rpartition(".")
        return aliases.get(eid, eid) + dot + pin
    nets = {ref(r): n for r, n in report.nets.items() if r.rpartition(".")[0] not in terminals}
    flat.netlist = {ref(r): n for r, n in flat.netlist.items() if r.rpartition(".")[0] not in terminals}
    flat.elements = [e for e in flat.elements if e.id not in terminals]
    for e in flat.elements:
        e.id = aliases.get(e.id, e.id)
    order = {eid: i for i, eid in enumerate(meta["order"])}
    flat.elements.sort(key=lambda e: order.get(e.id, len(order)))
    renumber = {}
    nets = {f"{e.id}.{p}": renumber.setdefault(nets[f"{e.id}.{p}"], len(renumber))
            for e in flat.elements for p in e.pins}
    sim = flat.extra.get("simulation", {})
    sim["components"] = {aliases.get(k, k): v for k, v in sim.get("components", {}).items()}
    sim["ground"] = ref(sim.get("ground", ""))
    for p in sim.get("probes", []):
        for key in ("positive", "negative"):
            if key in p:
                p[key] = ref(p[key])
        if "element" in p:
            p["element"] = aliases.get(p["element"], p["element"])
    if "electrical" in flat.extra:
        identity = flat.extra["electrical"]
        for node in identity["nodes"]:
            node["pins"] = [ref(p) for p in node["pins"] if p.rpartition(".")[0] not in terminals]
        identity["nodes"] = [n for n in identity["nodes"] if n["pins"]]
        def branch(r):
            eid, sep, winding = r.rpartition(":")
            return aliases.get(eid, eid) + sep + winding if sep else aliases.get(r, r)
        identity["branches"] = {branch(r): n for r, n in identity["branches"].items()}
    return flat, replace(report, nets=nets)


def validate_metadata(source, full):
    meta = source.extra.get("module_authoring")
    if meta is None:
        return
    from .modules import _object
    _object(meta, ("aliases", "order", "terminals"), ("modes",), "module_authoring")
    aliases, order, terminals = meta["aliases"], meta["order"], meta["terminals"]
    if (not isinstance(aliases, dict) or not all(isinstance(k, str) and isinstance(v, str) and v for k, v in aliases.items())
            or not isinstance(order, list) or not all(isinstance(i, str) for i in order) or len(set(order)) != len(order)
            or not isinstance(terminals, list) or not all(isinstance(i, str) for i in terminals)):
        raise ModuleError("module_authoring 需要唯一编号映射、元件顺序和辅助端口列表")
    actual_ids = {e.id for e in full.elements}
    port_ids = {e.id for e in full.elements if e.kind == "port"}
    if set(terminals) - port_ids:
        raise ModuleError("module_authoring.terminals 只能引用真实辅助 port，不能隐藏实际元件")
    if set(aliases) - actual_ids:
        raise ModuleError("module_authoring.aliases 引用了不存在的器件")
    mapped = [aliases.get(i, i) for i in actual_ids if i not in terminals]
    if len(set(mapped)) != len(mapped):
        raise ModuleError("module_authoring 编号映射冲突")


def _prune_parameters(definition, raw, name):
    """Parameters of parts no longer in the drawing go with them: the
    definition's ``defaults`` and ``editable``, every instance's
    ``overrides`` and the SPICE names kept for its parts keep only parts
    (and fields) the definition still has."""
    try:
        lay = Layout.from_dict(definition["layout"])
    except (TypeError, ValueError, KeyError, AttributeError):
        return  # the definition check below explains what is wrong
    instances = raw["modules"].get("instances", [])
    users = {i.get("id") for i in instances if isinstance(i, dict) and i.get("definition") == name}
    meta = raw.get("module_authoring")
    if isinstance(meta, dict) and isinstance(meta.get("aliases"), dict):
        drawn = {e.id for e in lay.elements}
        meta["aliases"] = {k: v for k, v in meta["aliases"].items()
                           if not (isinstance(k, str) and k.partition("/")[0] in users and k.partition("/")[2] not in drawn)}
    from .spice import FIELDS, OPTIONAL_FIELDS
    parts = {e.id: e.kind for e in lay.elements if e.kind != "port"}
    fields = {(eid, key) for eid, kind in parts.items()
              for key, _, _ in FIELDS.get(kind, []) + OPTIONAL_FIELDS.get(kind, [])}
    if isinstance(definition.get("defaults"), dict):
        definition["defaults"] = {eid: v for eid, v in definition["defaults"].items() if eid in parts}
    if isinstance(definition.get("editable"), list):
        definition["editable"] = [p for p in definition["editable"]
                                  if not isinstance(p, dict) or (p.get("element"), p.get("field")) in fields]
    editable = ({(p.get("element"), p.get("field")) for p in definition["editable"] if isinstance(p, dict)}
                if isinstance(definition.get("editable"), list) else None)
    for inst in instances:
        if not isinstance(inst, dict) or inst.get("definition") != name or not isinstance(inst.get("overrides"), dict):
            continue
        kept = {}
        for eid, values in inst["overrides"].items():
            if eid not in parts:
                continue
            if isinstance(values, dict):
                values = {k: v for k, v in values.items() if editable is None or (eid, k) in editable}
                if not values:
                    continue
            kept[eid] = values
        if kept:
            inst["overrides"] = kept
        else:
            inst.pop("overrides")


def update_definition(source, name, definition, profile="double"):
    raw = deepcopy(source.to_dict())
    if name not in raw.get("modules", {}).get("definitions", {}):
        raise ModuleError("只能编辑项目中保存的模块定义")
    old = raw["modules"]["definitions"][name]
    if set(old["ports"]) != set(definition["ports"]):
        raise ModuleError("已有实例引用这些端口；请保留端口名，再调整节点、边或顺序")
    definition = deepcopy(definition)
    _prune_parameters(definition, raw, name)
    raw["modules"]["definitions"][name] = definition
    result = Layout.from_dict(raw)
    _valid(result, profile)
    if result.extra.get("simulation"):
        from .spice import circuit_deck as make_deck
        make_deck(result, profile)
    return result


def _instance_port(spec, ref):
    """(instance, port) when ``ref`` names a port of an instance, else None."""
    from .modules import _definitions
    iid, _, port = ref.rpartition(".") if isinstance(ref, str) else ("", "", "")
    inst = next((i for i in spec.get("instances", []) if isinstance(i, dict) and i.get("id") == iid), None)
    if inst is None:
        return None
    definition = _definitions(spec).get(inst.get("definition"))
    return (inst, port) if isinstance(definition, dict) and port in definition.get("ports", {}) else None


def _sync_unconnected(spec, joined=(), released=()):
    """Keep ``unconnected`` true at both ends of a link: a port a new link
    reaches is no longer open on purpose; a port whose last link went away
    is now open on purpose (it would otherwise be reported as dangling)."""
    linked = {r for pair in spec.get("links", []) for r in pair}
    for ref in joined:
        hit = _instance_port(spec, ref)
        if hit and hit[1] in hit[0].get("unconnected", []):
            hit[0]["unconnected"] = [p for p in hit[0]["unconnected"] if p != hit[1]]
    for ref in released:
        hit = _instance_port(spec, ref)
        if not hit or ref in linked:
            continue
        inst, port = hit
        if port not in (inst.get("bindings") or {}) and port not in inst.get("unconnected", []):
            inst["unconnected"] = [*inst.get("unconnected", []), port]


def _drop_routes(spec, pairs):
    routes = spec.get("routes")
    if not isinstance(routes, dict):
        return
    for a, b in pairs:
        routes.pop(f"{a}|{b}", None)
        routes.pop(f"{b}|{a}", None)
    if not routes:
        spec.pop("routes")


def set_connections(source, iid, connections, profile="double"):
    """One connection per port, as the port dialog shows it: the first link
    of each port. A port left empty loses all its links; an unchanged choice
    keeps every link it has (a port may also feed further instances)."""
    from .modules import _definitions
    raw = deepcopy(source.to_dict())
    spec = raw["modules"]
    inst = next(i for i in spec["instances"] if i["id"] == iid)
    definition = _definitions(spec).get(inst["definition"])
    if not isinstance(definition, dict):
        raise ModuleError(f"实例 {iid} 的模块定义 {inst['definition']} 不存在")
    if set(connections) != set(definition["ports"]):
        raise ModuleError(f"请逐一填写端口 {list(definition['ports'])}")
    links = [list(pair) for pair in spec.get("links", [])]
    joined, released, removed = [], [], []
    for port, ref in connections.items():
        me = f"{iid}.{port}"
        mine = [k for k, pair in enumerate(links) if me in pair]
        ends = [links[k][1] if links[k][0] == me else links[k][0] for k in mine]
        if not ref:
            released += ends
            removed += [links[k] for k in mine]
            links = [pair for k, pair in enumerate(links) if k not in mine]
        elif ref in ends:
            continue
        elif mine:
            released.append(ends[0])
            removed.append(links[mine[0]])
            links[mine[0]] = [me, ref]
            joined.append(ref)
        else:
            links.append([me, ref])
            joined.append(ref)
    spec["links"] = links
    _drop_routes(spec, removed)
    inst.pop("bindings", None)
    inst["unconnected"] = [p for p in definition["ports"] if not any(f"{iid}.{p}" in pair for pair in links)]
    _sync_unconnected(spec, joined, released)
    result = Layout.from_dict(raw)
    _valid(result, profile)
    return result


def add_instance(source, name, at, connections, profile="double"):
    raw = deepcopy(source.to_dict())
    spec = raw["modules"]
    definition = spec["definitions"][name]
    if set(connections) != set(definition["ports"]):
        raise ModuleError("请逐一选择端口连接，或明确留空")
    iid = _unique("M", {i["id"] for i in spec["instances"]} | {e.id for e in source.elements})
    inst = {"id": iid, "definition": name, "at": at, "display": "expanded",
            "unconnected": [p for p, ref in connections.items() if not ref]}
    spec["instances"].append(inst)
    spec.setdefault("links", []).extend([f"{iid}.{p}", ref] for p, ref in connections.items() if ref)
    _sync_unconnected(spec, joined=[ref for ref in connections.values() if ref])
    if "module_authoring" in raw:
        raw["module_authoring"]["terminals"].extend(f"{iid}/{ref[:-2]}" for ref in definition["ports"].values())
    result = Layout.from_dict(raw)
    _assembles(result, profile)
    return result


def dissolve(source, instance_id, profile="double"):
    """Materialize just one instance; the project library and others survive."""
    source = direct_boundary_links(source)
    _valid(source, profile)
    assembly = assemble(source)
    raw = deepcopy(source.to_dict())
    spec = raw["modules"]
    inst = next((i for i in spec["instances"] if i["id"] == instance_id), None)
    if inst is None:
        raise ModuleError("实例不存在")
    from .modules import _library_for, _merge, _translate
    definitions = {**_library_for(spec["instances"], spec.get("definitions", {})), **spec.get("definitions", {})}
    full = {e.id: e for e in assembly.full.elements}

    def origin(other):
        """Where an instance sits in the full (all expanded) drawing."""
        first = Layout.from_dict(definitions[other["definition"]]["layout"]).elements[0]
        pin = next(iter(first.pins))
        return [round(full[other["id"] + "/" + first.id].pins[pin][j] - first.pins[pin][j], 4) for j in range(2)]

    others = [i for i in spec["instances"] if i is not inst]
    # The dissolved parts take the instance's place in the full drawing. The
    # other instances placed without "at" were stacked around it there; they
    # keep that place, which the full drawing has already checked.
    for other in others:
        if other.get("at") is None:
            other["at"] = origin(other)
        group = other.get("group")
        if isinstance(group, dict) and instance_id in group.get("members", []):
            group["members"] = [m for m in group["members"] if m != instance_id]
            if not group["members"]:
                other.pop("group")
    # An automatic external part is placed beside the instance ports it links
    # to. One linked to this instance loses that reference: it becomes an
    # ordinary part where the full drawing has it. The others stay automatic.
    parts = spec.pop("parts", [])
    kept = [p for p in parts if others and not any(
        any(r.rpartition(".")[0] == p["id"] for r in pair) and any(r.rpartition(".")[0] == instance_id for r in pair)
        for pair in spec.get("links", []))]
    if kept:
        spec["parts"] = kept
    for part in parts:
        if part in kept:
            continue
        element = assembly.full.element(part["id"])
        raw["elements"].append(element.to_dict())
        raw["netlist"].update({f"{element.id}.{p}": assembly.full.netlist[f"{element.id}.{p}"] for p in element.pins})
    definition = definitions[inst["definition"]]
    local = Layout.from_dict(definition["layout"])
    prefix = instance_id + "/"
    first = local.elements[0]
    pin = next(iter(first.pins))
    at = [full[prefix + first.id].pins[pin][j] - first.pins[pin][j] for j in range(2)]
    nets = {net: assembly.full.netlist[prefix + ref] for ref, net in local.netlist.items()}
    part = _translate(local, prefix, at, nets).to_dict()
    meta = raw.setdefault("module_authoring", {"aliases": {}, "order": [e.id for e in assembly.full.elements], "terminals": []})
    used = {e["id"] for e in raw["elements"]} | {p["id"] for p in kept}
    rename = {e["id"]: _unique(meta["aliases"].get(e["id"], e["id"].replace("/", "_")), used) for e in part["elements"]}
    def ref(r):
        eid, _, pin = r.rpartition(".")
        return rename.get(eid, eid) + "." + pin
    for e in part["elements"]:
        old = e["id"]
        e["id"] = rename[old]
        if old in meta["aliases"]:
            original = meta["aliases"].pop(old)
            if original != e["id"]:
                meta["aliases"][e["id"]] = original
        elif e["kind"] != "port" or old not in meta["terminals"]:
            # a module's own terminal (a library module's IN / OUT) keeps the
            # name the netlist listed it under, like any other part
            meta["aliases"][e["id"]] = old
    meta["terminals"] = [rename.get(e, e) for e in meta["terminals"]]
    raw["elements"].extend(part["elements"])
    # Use resolved net names for every root pin, so renamed public nets agree.
    raw["netlist"] = {r: assembly.full.netlist[r] for r in raw["netlist"]}
    raw["netlist"].update({ref(r): n for r, n in part["netlist"].items()})
    for key in ("wires", "hops", "annotations"):
        raw.setdefault(key, []).extend(part[key])
    for key in DECORATION_SECTIONS:
        for obj in part.get(key, []):
            if "members" in obj:
                obj["members"] = [rename.get(e, e) for e in obj["members"]]
        raw.setdefault(key, []).extend(part.get(key, []))
    for pair in spec.get("links", []):
        for j, endpoint in enumerate(pair):
            iid, _, port = endpoint.rpartition(".")
            if iid == instance_id:
                pair[j] = ref(prefix + definition["ports"][port])
    if inst.get("bindings"):
        for p, net in inst["bindings"].items():
            spec.setdefault("root_ports", {})[ref(prefix + definition["ports"][p])] = net
    spec["instances"].remove(inst)
    if raw.get("simulation"):
        sim = raw["simulation"]
        sim["components"].update({rename[prefix + k]: v for k, v in _merge(definition.get("defaults", {}), inst.get("overrides", {})).items()})
        sim["ground"] = ref(sim["ground"])
        for probe in sim["probes"]:
            for k in ("positive", "negative"):
                if k in probe:
                    probe[k] = ref(probe[k])
            if "element" in probe:
                probe["element"] = rename.get(probe["element"], probe["element"])
    if not spec["instances"]:
        if not spec.get("definitions"):
            # A bundled module referenced by name only: keep a copy, as the
            # table's library, so it still reads as a module table.
            from .module_store import revision
            spec["definitions"] = {inst["definition"]: {**deepcopy(definition), "source": {
                "library": "builtin", "name": inst["definition"], "revision": revision(definition)}}}
        # The final full projection contains all external routes. Remove only
        # synthetic terminals, keeping real user-authored terminals intact.
        interim = Layout.from_dict(raw)
        full_lay = assemble(interim).full
        raw["wires"] = full_lay.to_dict()["wires"]
        raw["hops"] = full_lay.to_dict()["hops"]
        terminals = set(meta["terminals"])
        raw["elements"] = [e for e in raw["elements"] if e["id"] not in terminals]
        raw["netlist"] = {r: n for r, n in raw["netlist"].items() if r.rpartition(".")[0] not in terminals}
        order = {eid: i for i, eid in enumerate(meta["order"])}
        raw["elements"].sort(key=lambda e: order.get(e["id"], len(order)))
        spec["links"] = []
        spec.pop("root_ports", None)
        if meta.get("modes"):
            raw["modes"] = meta["modes"]
        meta["terminals"] = []
    result = Layout.from_dict(raw)
    report = check(result, get_profile(profile))
    if not report.ok:
        errors = report.errors
        hit = [o["id"] for o in others
               if any(re.search(rf"(?<![\w/-]){re.escape(o['id'])}/", i.message) for i in errors)]
        detail = "；".join(i.message for i in errors[:4])
        if hit:
            raise ModuleError(f"解散 {instance_id} 后，展开的元件会与实例 {'、'.join(hit)} 重叠或相交，已取消；"
                              f"请先把 {instance_id} 或 {'、'.join(hit)} 拖开（留出 {instance_id} 展开后的位置）再解散。详情：{detail}")
        raise ModuleError(f"解散 {instance_id} 后电路未通过校验，已取消：{detail}")
    if source.extra.get("simulation"):
        from .spice import circuit_deck as make_deck
        if make_deck(source, profile).text != make_deck(result, profile).text:
            raise ModuleError("解散改变了网表，已取消")
    return result
