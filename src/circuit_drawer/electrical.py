"""Persistent electrical identities, independent of drawing order and geometry.

Legacy layouts remain opt-in. Reconciliation is an editor/AI transaction, never
a side effect of validation or SPICE export. Merges keep the oldest identity;
splits keep it on the first surviving anchor. Counters never reuse retired IDs.
"""
from copy import deepcopy
import re

from .layout import windings

NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,40}\Z")


def branch_refs(layout):
    for e in layout.elements:
        if e.kind == "port":
            continue
        if e.kind == "transformer":
            for winding, *_ in windings(e):
                yield f"{e.id}:{winding}"
        else:
            yield e.id


def reconcile(layout, nets):
    """Return a fresh identity section for the current graph; no layout mutation."""
    old = layout.extra.get("electrical", {})
    groups = {}
    for pin, net in nets.items():
        groups.setdefault(net, []).append(pin)
    remaining = {k: sorted(v) for k, v in groups.items()}
    nodes = []
    for node in old.get("nodes", []):
        matches = [net for pin in node["pins"] for net, refs in remaining.items() if pin in refs]
        if matches:
            net = matches[0]
            nodes.append({**deepcopy(node), "pins": remaining.pop(net)})
    next_node = old.get("next_node", 1)
    used = {n.get("name", n["id"]).lower() for n in old.get("nodes", [])}
    for refs in remaining.values():
        while f"n{next_node}" in used:
            next_node += 1
        nodes.append({"id": f"N{next_node}", "pins": refs})
        used.add(f"n{next_node}")
        next_node += 1
    branches, next_branch = {}, old.get("next_branch", 1)
    for ref in branch_refs(layout):
        if ref in old.get("branches", {}):
            branches[ref] = old["branches"][ref]
        else:
            branches[ref] = next_branch
            next_branch += 1
    return {"version": 1, "nodes": nodes, "branches": branches,
            "next_node": next_node, "next_branch": next_branch}


def pin_names(section):
    return {pin: n.get("name", n["id"]) for n in section["nodes"] for pin in n["pins"]}


def synchronize(layout, profile):
    """An explicit edit transaction, using full connectivity for module views."""
    from .layout import Layout
    from .profiles import get_profile
    from .validate import check
    bad = [message for code, message in problems(layout, {}, schema_only=True) if code == "bad-electrical"]
    if bad:
        raise ValueError("；".join(bad))
    graph = Layout.from_dict(layout.to_dict())
    graph.extra.pop("electrical", None)
    if "modules" in graph.extra:
        from .modules import checked
        assembly, report, _ = checked(graph, get_profile(profile))
        if assembly is None:
            raise ValueError("模块校验未通过，无法同步标号")
        graph = assembly.full
    else:
        report = check(graph, get_profile(profile))
    expected = {f"{e.id}.{pin}" for e in graph.elements for pin in e.pins}
    if expected - report.nets.keys():
        # Schema/geometry errors can stop validation before connectivity exists.
        # An absent graph does not mean the user deleted every electrical node.
        if "electrical" in layout.extra:
            return deepcopy(layout.extra["electrical"])
        raise ValueError("请先修正元件/导线的几何错误，再启用电气标号")
    graph.extra["electrical"] = layout.extra.get("electrical", {})
    section = reconcile(graph, report.nets)
    if "electrical" not in layout.extra:
        # Import names once; later edits retain the user's stable identities.
        candidates = []
        for node in section["nodes"]:
            names = {graph.netlist[p] for p in node["pins"] if p in graph.netlist}
            name = next(iter(names)) if len(names) == 1 else ""
            candidates.append(name if NAME.fullmatch(name) and not name.lower().startswith(("cd_", "ctl_")) else "")
        reserved = {n["id"].lower() for n in section["nodes"]}
        counts = [n.lower() for n in candidates]
        for node, name in zip(section["nodes"], candidates):
            if name and counts.count(name.lower()) == 1 and (name.lower() not in reserved or name.lower() == node["id"].lower()):
                node["name"] = name
    return section


def problems(layout, nets, *, schema_only=False):
    """Strict schema and reference errors, including typical model mistakes."""
    pins = {f"{e.id}.{p}" for e in layout.elements for p in e.pins}
    nc = layout.extra.get("no_connect", [])
    if not isinstance(nc, list) or any(not isinstance(p, str) or p not in pins for p in nc):
        yield "bad-no-connect", "no_connect 必须是现有引脚名称列表，例如 [\"R1.b\"]"
    elif len(nc) != len(set(nc)):
        yield "bad-no-connect", "no_connect 引脚不能重复"
    if "electrical" not in layout.extra:
        return
    section = layout.extra["electrical"]
    try:
        if not isinstance(section, dict) or set(section) != {"version", "nodes", "branches", "next_node", "next_branch"}:
            raise ValueError("允许字段 version, nodes, branches, next_node, next_branch")
        if type(section["version"]) is not int or section["version"] != 1:
            raise ValueError("version 必须为 1")
        for key in ("next_node", "next_branch"):
            if type(section[key]) is not int or section[key] < 1:
                raise ValueError(f"{key} 必须是正整数")
        if not isinstance(section["nodes"], list) or not isinstance(section["branches"], dict):
            raise ValueError("nodes 必须为列表，branches 必须为对象")
        ids, names, refs, graph = set(), set(), set(), set()
        for node in section["nodes"]:
            if not isinstance(node, dict) or set(node) - {"id", "name", "pins"} or not {"id", "pins"} <= node.keys():
                raise ValueError("节点允许 id, name, pins；id 和 pins 必填")
            ident, name = node["id"], node.get("name", node["id"])
            if not isinstance(ident, str) or not re.fullmatch(r"N[1-9][0-9]*", ident) or ident in ids:
                raise ValueError(f"节点 id {ident!r} 必须是唯一的 N1、N2…")
            if int(ident[1:]) >= section["next_node"]:
                raise ValueError(f"{ident}: next_node 必须大于已分配编号")
            if not isinstance(name, str) or not NAME.fullmatch(name):
                raise ValueError(f"{ident}: 网络名 {name!r} 须以英文字母开头，只用字母、数字和下划线，最长 41 个字符")
            if name.lower() in names:
                raise ValueError(f"{ident}: 网络名 {name} 已被另一个网络使用（不区分大小写）")
            # Reserve internal SPICE namespaces to avoid hidden electrical shorts.
            if name.lower().startswith(("cd_", "ctl_")):
                raise ValueError(f"{ident}: 网络名不能使用内部前缀 cd_ / ctl_")
            members = node["pins"]
            if not isinstance(members, list) or not members or any(not isinstance(p, str) or (not schema_only and p not in pins) or p in refs for p in members) or len(set(members)) != len(members):
                raise ValueError(f"{ident}: pins 必须是现有且不重复的引脚名称: {members!r}")
            netids = {nets[p] for p in members if p in nets}
            if not schema_only and (len(netids) != 1 or graph & netids):
                raise ValueError(f"{ident} ({name}): 节点标号与连接不一致: {', '.join(members)}")
            ids.add(ident); names.add(name.lower()); refs.update(members); graph.update(netids)
        if not schema_only and refs != pins:
            raise ValueError(f"节点缺少引脚: {', '.join(sorted(pins - refs))}")
        branches = section["branches"]
        if not schema_only and set(branches) != set(branch_refs(layout)):
            raise ValueError("branches 必须逐一对应现有支路（变压器使用 T:p、T:s / T:s1、T:s2）")
        values = list(branches.values())
        if any(type(v) is not int or not 0 < v < section["next_branch"] for v in values) or len(set(values)) != len(values):
            raise ValueError("支路编号必须唯一、为正整数且小于 next_branch")
    except (ValueError, TypeError, KeyError) as exc:
        yield "bad-electrical", f"electrical: {exc}"


_SEPARATOR = r"\s*(?:[、,，和&]|\band\b)\s*"
_NODE_WORDS = re.compile(r"(?:节点|\bnodes?\b)\s*([A-Za-z][A-Za-z0-9_]*(?:" + _SEPARATOR
                         + r"[A-Za-z][A-Za-z0-9_]*)*)", re.I)
_BRANCH_NUMBERS = re.compile(r"(?:支路|\bbranch)\s*(\d+)", re.I)


def instruction_targets(instruction, section=None):
    """Node names and branch numbers an instruction explicitly refers to.

    A word after "节点"/"node" is a label reference only if it is an existing
    name or looks like one (contains a capital, digit or underscore: OP, N3,
    Vout). Plain lowercase prose such as "node between R1 and C1" or
    "节点 label" is ordinary wording, not a target.
    """
    names = {n.get("name", n["id"]).lower() for n in section["nodes"]} if section else set()
    nodes = [word for match in _NODE_WORDS.finditer(instruction)
             for word in re.split(_SEPARATOR, match[1], flags=re.I)
             if word.lower() in names or not (word.isalpha() and word.islower())]
    return nodes, [int(b) for b in _BRANCH_NUMBERS.findall(instruction)]


def validate_instruction(instruction, layout):
    """Reject explicit missing targets before contacting the model."""
    bad = [message for code, message in problems(layout, {}, schema_only=True) if code == "bad-electrical"]
    if bad:
        raise ValueError("；".join(bad))
    section = layout.extra.get("electrical")
    nodes, branches = instruction_targets(instruction, section)
    if section is None:
        # Without the ledger node words stay ordinary drawing language for the
        # model; branch numbers exist only once labels are enabled.
        if branches:
            raise ValueError("请先启用节点/支路标号，再按支路号修改")
        return
    names = {n.get("name", n["id"]).lower() for n in section["nodes"]}
    missing = [n for n in nodes if n.lower() not in names]
    missing += [f"支路 {b}" for b in branches if b not in section["branches"].values()]
    if missing:
        raise ValueError("标号不存在: " + "、".join(missing))


def requested_simulation(instruction, previous, proposed, layout):
    """Only named branches/new devices may receive AI-written physical values."""
    old = previous.get("simulation")
    if not isinstance(old, dict) or not isinstance(proposed, dict):
        return deepcopy(old)
    result = deepcopy(old)
    nodes, wanted = instruction_targets(instruction, previous["electrical"])
    targets = {r.split(":")[0] for r, n in previous["electrical"]["branches"].items() if n in wanted}
    # A node-targeted insertion adds simulation data only for the new devices.
    if nodes:
        targets.update(e.id for e in layout.elements if e.id not in old.get("components", {}))
    supplied = proposed.get("components", {})
    if isinstance(supplied, dict):
        for ref in targets:
            if ref in supplied:
                result.setdefault("components", {})[ref] = deepcopy(supplied[ref])
    return result


def display(layout, section):
    pins = {f"{e.id}.{k}": list(p) for e in layout.elements for k, p in e.pins.items()}
    names = pin_names(section)
    return {"nodes": [{"name": n.get("name", n["id"]), "id": n["id"], "at": pins[next(p for p in n["pins"] if p in pins)]}
                      for n in section["nodes"] if any(p in pins for p in n["pins"])],
            "lines": [f"{names.get(p, '?')}: " + ", ".join(n["pins"])
                      for n in section["nodes"] for p in n["pins"][:1]],
            "branches": section["branches"]}
