"""Transactional, graphical module authoring. No drawing is replaced until checked."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from copy import deepcopy

from .electrical import synchronize
from .layout import DECORATION_SECTIONS, Layout
from .modules import ModuleError, _name, assemble
from .modes import WireGraph, _k
from .profiles import get_profile
from .validate import check


def _valid(lay, profile, *, allow_open=False):
    report = check(lay, get_profile(profile))
    errors = [i for i in report.errors if not (allow_open and i.code == "open-pin")]
    if errors:
        raise ModuleError("请先修正电路：" + "；".join(i.message for i in errors[:8]))
    return report


def new_edit_errors(before, after):
    """Existing draft diagnostics stay visible; a transaction may not add errors."""
    remaining = Counter((i.code, i.message) for i in before.errors)
    errors = []
    for issue in after.errors:
        key = issue.code, issue.message
        if remaining[key] and not issue.code.startswith("bad-"):
            remaining[key] -= 1
        else:
            errors.append(issue)
    return errors


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


def _rename_electrical(raw, names):
    """Remap identities on the transaction's copy, before reconciliation."""
    if "electrical" not in raw:
        return
    section = raw["electrical"]
    for node in section["nodes"]:
        pins = []
        for pin in node["pins"]:
            eid, _, terminal = pin.rpartition(".")
            pins.append(names.get(eid, eid) + "." + terminal)
        node["pins"] = pins
    branches = {}
    for branch, number in section["branches"].items():
        eid, separator, winding = branch.partition(":")
        branches[names.get(eid, eid) + separator + winding] = number
    section["branches"] = branches


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
    _valid(source, profile, allow_open=True)
    if any(f"{e.id}.{p}" not in source.netlist for e in source.elements for p in e.pins):
        raise ModuleError("请先用“分析 → 以当前连接更新网表”记录已画出的连接，再设置模块端口")
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
    _rename_electrical(raw, {eid: instance + "/" + eid for eid in ids})
    result = direct_boundary_links(Layout.from_dict(raw))
    if "electrical" in result.extra:
        result.extra["electrical"] = synchronize(result, profile)
    _valid(result, profile, allow_open=True)
    if source.extra.get("simulation") and check(source, get_profile(profile)).ok:
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


def validate_metadata(source, full=None):
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
    modes = meta.get("modes", [])
    if not isinstance(modes, list):
        raise ModuleError("module_authoring.modes 必须是模态对象列表")
    for index, mode in enumerate(modes):
        where = f"module_authoring.modes[{index}]"
        if not isinstance(mode, dict):
            raise ModuleError(f"{where} 必须是模态对象")
        on, loops = mode.get("on"), mode.get("loops")
        if on is not None and (not isinstance(on, list) or not all(isinstance(ref, str) for ref in on)):
            raise ModuleError(f"{where}.on 必须是支路名称列表")
        if loops is not None and (not isinstance(loops, list) or not (
                all(isinstance(ref, str) for ref in loops) or
                all(isinstance(loop, list) and all(isinstance(ref, str) for ref in loop) for loop in loops))):
            raise ModuleError(f"{where}.loops 必须是支路名称列表或回路列表")
    if full is None:
        return
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
    # Validate the shapes once, before pruning references to deleted parts.
    # The ordinary definition/assembly checks validate the surviving references.
    if not isinstance(definition, dict) or not isinstance(definition.get("layout"), dict):
        raise ModuleError(f"模块 {name}.layout 必须是坐标表对象")
    try:
        lay = Layout.from_dict(definition["layout"])
    except (TypeError, ValueError, KeyError, AttributeError, IndexError) as exc:
        raise ModuleError(f"模块 {name}.layout 无法读取：{exc}") from exc
    instances = raw["modules"].get("instances", [])
    if not isinstance(instances, list) or any(not isinstance(i, dict) or
            not isinstance(i.get("id"), str) or not isinstance(i.get("definition"), str) for i in instances):
        raise ModuleError("modules.instances 必须是含 id 和 definition 的实例对象列表")
    tables = [(f"模块 {name}.defaults", definition.get("defaults", {}))]
    tables.extend((f"实例 {i['id']}.overrides", i.get("overrides", {})) for i in instances)
    for where, values in tables:
        if not isinstance(values, dict) or any(not isinstance(eid, str) or not isinstance(params, dict)
                or not all(isinstance(key, str) for key in params) for eid, params in values.items()):
            raise ModuleError(f"{where} 必须是元件 ID 到参数对象的映射")
    editable = definition.get("editable")
    if "editable" in definition and (not isinstance(editable, list) or any(
            not isinstance(p, dict) or set(p) != {"element", "field"} or
            not all(isinstance(v, str) for v in p.values()) for p in editable)):
        raise ModuleError(f"模块 {name}.editable 必须是含 element、field 的参数对象列表")
    users = {i["id"] for i in instances if i["definition"] == name}
    meta = raw.get("module_authoring")
    if meta is not None:
        drawn = {e.id for e in lay.elements}
        removed = {k: v for k, v in meta["aliases"].items()
                   if k.partition("/")[0] in users and k.partition("/")[2] not in drawn}
        meta["aliases"] = {k: v for k, v in meta["aliases"].items() if k not in removed}
        deleted = set(removed) | set(removed.values())
        def touches(ref):
            return any(r.strip().partition(":")[0] in deleted for r in ref.split("|"))
        for mode in meta.get("modes", []):
            if mode.get("on") is not None:
                mode["on"] = [ref for ref in mode["on"] if not touches(ref)]
            loops = mode.get("loops") or []
            flat = bool(loops) and isinstance(loops[0], str)
            kept = [loop for loop in ([loops] if flat else loops) if not any(touches(ref) for ref in loop)]
            if len(kept) != (1 if flat else len(loops)):
                # Removing just one branch could invent a different current path.
                mode["loops"] = kept[0] if flat and kept else kept
                mode.pop("arrows", None)
        if deleted:
            meta["order"] = [eid for eid in meta["order"] if eid not in deleted]
    from .spice import FIELDS, OPTIONAL_FIELDS
    parts = {e.id: e.kind for e in lay.elements if e.kind != "port"}
    fields = {(eid, key) for eid, kind in parts.items()
              for key, _, _ in FIELDS.get(kind, []) + OPTIONAL_FIELDS.get(kind, [])}
    if "defaults" in definition:
        definition["defaults"] = {eid: v for eid, v in definition["defaults"].items() if eid in parts}
    if "editable" in definition:
        definition["editable"] = [p for p in definition["editable"]
                                  if (p["element"], p["field"]) in fields]
    editable = ({(p["element"], p["field"]) for p in definition["editable"]}
                if "editable" in definition else None)
    for inst in instances:
        if inst["definition"] != name or "overrides" not in inst:
            continue
        kept = {}
        for eid, values in inst["overrides"].items():
            if eid not in parts:
                continue
            values = {k: v for k, v in values.items() if editable is None or (eid, k) in editable}
            if not values:
                continue
            kept[eid] = values
        if kept:
            inst["overrides"] = kept
        else:
            inst.pop("overrides")


def update_definition(source, name, definition, profile="double", *, new_name=None):
    validate_metadata(source)
    raw = deepcopy(source.to_dict())
    if name not in raw.get("modules", {}).get("definitions", {}):
        raise ModuleError("只能编辑项目中保存的模块定义")
    new_name = name if new_name is None else _name(new_name, "模块名")
    from .modules import _definitions
    if new_name != name and new_name in _definitions(raw["modules"]):
        raise ModuleError(f"模块名 {new_name} 已存在，请换一个名称")
    old = raw["modules"]["definitions"][name]
    definition = deepcopy(definition)
    _prune_parameters(definition, raw, name)
    if new_name != name:
        raw["modules"]["definitions"][new_name] = raw["modules"]["definitions"].pop(name)
        for inst in raw["modules"]["instances"]:
            if inst["definition"] == name:
                inst["definition"] = new_name
    # Compare diagnostics using the new identity, including empty drafts.
    before = _assembles(source if new_name == name else Layout.from_dict(raw), profile)
    from .modules import _read_definition
    _read_definition(name, definition)
    if not set(old["ports"]) <= set(definition["ports"]):
        raise ModuleError("已有实例引用这些端口；请保留端口名，再调整节点、边或顺序")
    added = [p for p in definition["ports"] if p not in old["ports"]]
    for inst in raw["modules"]["instances"]:
        if inst["definition"] == new_name:
            if added:
                inst.setdefault("unconnected", []).extend(added)
    if new_name != name:
        definition["title"] = new_name
        definition["layout"]["name"] = new_name
    raw["modules"]["definitions"][new_name] = definition
    _move_port_wires(raw, assemble(source).ports, assemble(Layout.from_dict(raw)).ports)
    result = Layout.from_dict(raw)
    if "electrical" in result.extra:
        result.extra["electrical"] = synchronize(result, profile)
    errors = new_edit_errors(before, check(result, get_profile(profile)))
    if errors:
        raise ModuleError("定义未保存：" + "；".join(i.message for i in errors[:8]))
    if result.extra.get("simulation") and before.ok:
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


def connect_touching_ports(source, profile="double", instance_id=None):
    """Check a contact transaction; ordinary wires are the connection record."""
    result = Layout.from_dict(source.to_dict())
    if instance_id is not None and not any(i["id"] == instance_id for i in result.extra["modules"]["instances"]):
        raise ModuleError("模块实例不存在")
    contacts = assemble(result).wire_contacts
    if not contacts:
        return result
    if any(len(nets) > 1 for nets in contacts.values()):
        raise ModuleError("接触的导线与原网表不一致，请先确认原电路接法并更新网表")
    if "electrical" in result.extra:
        result.extra["electrical"] = synchronize(result, profile)
    _assembles(result, profile)
    return result


def _move_port_wires(raw, old_ports, new_ports):
    """Keep authored wire contacts when a definition or its display changes."""
    from .modules import _on
    wires = deepcopy(raw["wires"])
    fixed = [tuple(p) for e in raw["elements"] for p in e["pins"].values()]
    stops = fixed + [tuple(p) for p in old_ports.values()] + [tuple(p) for w in wires for p in (w[0], w[-1])]
    for ref, old in old_ports.items():
        if ref not in new_ports or _k(old) == _k(new_ports[ref]):
            continue
        new = list(new_ports[ref])
        attached = [(i, j) for i, w in enumerate(wires) for j, p in enumerate(w) if _k(p) == _k(old)]
        tee = (tuple(old) in fixed or any(_on(old, a, b, interior=True) for w in wires for a, b in zip(w, w[1:]))
               or any(0 < j < len(wires[i]) - 1 and _on(old, wires[i][j-1], wires[i][j+1], interior=True)
                      for i, j in attached)
               or any(j in (0, len(wires[i])-1) and any(_on(p, old, wires[i][1 if j == 0 else -2], interior=True)
                      for p in stops) for i, j in attached)
               or any(other != ref and _k(p) == _k(old) for other, p in old_ports.items()))
        if tee:
            if tuple(old) in fixed or attached or any(_on(old, a, b) for w in wires for a, b in zip(w, w[1:])):
                path = [list(old), [old[0], new[1]], new]
                if path not in raw["wires"]:
                    raw["wires"].append(path)
            continue
        for i, j in sorted(attached, reverse=True):
            path = raw["wires"][i]
            target = len(path) - 1 if j == len(wires[i]) - 1 else j
            if len(path) == 2:
                other = path[1 - target]
                axis = 0 if abs(old[1] - other[1]) < 1e-6 else 1
                if abs(new[1 - axis] - other[1 - axis]) > 1e-6:
                    a, b = list(other), new.copy()
                    a[axis] = b[axis] = (new[axis] + other[axis]) / 2
                    path = [list(other), a, b, new.copy()]
                    raw["wires"][i] = path if target else path[::-1]
                    continue
            path[target] = new.copy()
            for k in (target + 1, target - 1):
                if not 0 <= k < len(path):
                    continue
                horizontal = abs(old[1] - path[k][1]) < 1e-6
                if 0 < k < len(path) - 1:
                    path[k][1 if horizontal else 0] = new[1 if horizontal else 0]
                else:
                    elbow = [new[0], path[k][1]] if horizontal else [path[k][0], new[1]]
                    path.insert(min(k, target) + 1, elbow)
    cleaned = []
    for index, path in enumerate(raw["wires"]):
        if index < len(wires) and path == wires[index]:
            cleaned.append(path)
            continue
        path = [p for j, p in enumerate(path) if j == 0 or _k(p) != _k(path[j - 1])]
        kept = []
        for j, point in enumerate(path):
            if j in (0, len(path) - 1) or tuple(point) in stops:
                kept.append(point)
                continue
            previous, following = path[j - 1], path[j + 1]
            vertical = abs(previous[0] - point[0]) < 1e-6 and abs(following[0] - point[0]) < 1e-6
            horizontal = abs(previous[1] - point[1]) < 1e-6 and abs(following[1] - point[1]) < 1e-6
            if not (vertical or horizontal):
                kept.append(point)
        if len(kept) > 1:
            cleaned.append(kept)
    raw["wires"] = cleaned


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
    if "electrical" in result.extra:
        result.extra["electrical"] = synchronize(result, profile)
    _valid(result, profile)
    return result


def create_empty(source, at, profile="double"):
    """A real, saved draft instance. Export still requires actual circuitry."""
    _assembles(source, profile)
    raw = deepcopy(source.to_dict())
    spec = raw.setdefault("modules", {"version": 1, "definitions": {}, "instances": [], "links": []})
    definitions = spec.setdefault("definitions", {})
    name = _unique("Subsystem", set(definitions))
    definitions[name] = {"layout": Layout(name).to_dict(), "ports": {}, "editable": [], "title": name}
    if raw.get("modes"):
        meta = raw.setdefault("module_authoring", {"aliases": {}, "order": [e.id for e in source.elements], "terminals": []})
        meta["modes"] = raw.pop("modes")
    return add_instance(Layout.from_dict(raw), name, at, {}, profile)


def add_instance(source, name, at, connections, profile="double"):
    raw = deepcopy(source.to_dict())
    spec = raw["modules"]
    definition = spec["definitions"][name]
    if set(connections) != set(definition["ports"]):
        raise ModuleError("请逐一选择端口连接，或明确留空")
    iid = _unique("M", {i["id"] for i in spec["instances"]} | {e.id for e in source.elements})
    inst = {"id": iid, "definition": name, "at": at, "display": "expanded" if definition["layout"]["elements"] else "collapsed",
            "unconnected": [p for p, ref in connections.items() if not ref]}
    spec["instances"].append(inst)
    spec.setdefault("links", []).extend([f"{iid}.{p}", ref] for p, ref in connections.items() if ref)
    _sync_unconnected(spec, joined=[ref for ref in connections.values() if ref])
    if "module_authoring" in raw:
        raw["module_authoring"]["terminals"].extend(f"{iid}/{ref[:-2]}" for ref in definition["ports"].values())
    result = Layout.from_dict(raw)
    if "electrical" in result.extra:
        result.extra["electrical"] = synchronize(result, profile)
    _assembles(result, profile)
    return result


def dissolve(source, instance_id, profile="double"):
    """Materialize just one instance; the project library and others survive."""
    source = direct_boundary_links(source)
    before = _assembles(source, profile)
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
        elements = Layout.from_dict(definitions[other["definition"]]["layout"]).elements
        if not elements:
            return assembly.origins[other["id"]]
        first = elements[0]
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
    if not local.elements:
        raise ModuleError("空子系统还没有可解散的元件；可进入定义搭电路，或删除实例")
    prefix = instance_id + "/"
    targets = {f"{instance_id}.{p}": full[prefix + ref[:-2]].pins["p"] for p, ref in definition["ports"].items()}
    _move_port_wires(raw, assembly.ports, targets)
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
        for obj in raw.get(key, []):
            if "members" in obj:
                obj["members"] = [rename.get(e, e) for e in obj["members"]]
        for obj in part.get(key, []):
            if "members" in obj:
                obj["members"] = [rename.get(e, e) for e in obj["members"]]
        raw.setdefault(key, []).extend(part.get(key, []))
    # A dissolved instance is now ordinary parts, not an omitted electrical copy.
    for region in raw.get("regions", []):
        usage = raw.get("stack_usage", {}).get(region.get("id"), {})
        if usage.get("purpose") == "instances" and instance_id in usage.get("instances", []):
            region.pop("stack", None)
            raw["stack_usage"].pop(region["id"])
            raw["module_view"] = "expanded"
    if "stack_usage" in raw and not raw["stack_usage"]:
        raw.pop("stack_usage")
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
    # Deliberately unused module ports remain deliberately open after flattening.
    open_points = {full[prefix + definition["ports"][p][:-2]].pins["p"] for p in inst.get("unconnected", [])}
    _rename_electrical(raw, rename)
    result = Layout.from_dict(raw)
    if "electrical" in result.extra:
        result.extra["electrical"] = synchronize(result, profile)
    report = check(result, get_profile(profile))
    open_pins = {i.message.split(" at ", 1)[0] for i in report.errors if i.code == "open-pin"}
    preserved = [f"{e.id}.{p}" for e in result.elements if e.id in rename.values()
                 for p, point in e.pins.items() if point in open_points and f"{e.id}.{p}" in open_pins]
    if preserved:
        result.extra.setdefault("no_connect", []).extend(preserved)
        report = check(result, get_profile(profile))
    errors = new_edit_errors(before, report)
    if errors:
        hit = [o["id"] for o in others
               if any(re.search(rf"(?<![\w/-]){re.escape(o['id'])}/", i.message) for i in errors)]
        detail = "；".join(i.message for i in errors[:4])
        if hit:
            raise ModuleError(f"解散 {instance_id} 后，展开的元件会与实例 {'、'.join(hit)} 重叠或相交，已取消；"
                              f"请先把 {instance_id} 或 {'、'.join(hit)} 拖开（留出 {instance_id} 展开后的位置）再解散。详情：{detail}")
        raise ModuleError(f"解散 {instance_id} 后电路未通过校验，已取消：{detail}")
    # Compare actual connectivity even when unrelated unfinished parts prevent SPICE export.
    after = assemble(result)
    groups = []
    for graph, links, rename_pin in ((assembly.full, assembly.links, ref), (after.full, after.links, lambda p: p)):
        nets = check(graph, get_profile(profile), connections=links).nets
        grouped = defaultdict(set)
        for element in graph.elements:
            if element.kind != "port":
                for pin in element.pins:
                    key = f"{element.id}.{pin}"
                    if key not in nets:
                        raise ModuleError("解散后的连接未能完整校验，已取消")
                    grouped[nets[key]].add(rename_pin(key))
        groups.append({frozenset(g) for g in grouped.values()})
    if groups[0] != groups[1]:
        raise ModuleError("解散改变了电气连接，已取消")
    if source.extra.get("simulation") and before.ok:
        from .spice import circuit_deck as make_deck
        if make_deck(source, profile).text != make_deck(result, profile).text:
            raise ModuleError("解散改变了网表，已取消")
    return result
