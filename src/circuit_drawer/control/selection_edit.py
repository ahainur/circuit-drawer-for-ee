"""Scoped publication edits. Geometry is written only into an unadopted candidate."""
from copy import deepcopy

from ..profiles import get_profile
from . import library
from .check import AUTO_ROUTE_MIN_RUN, check

SECTIONS = {"block": "blocks", "link": "links", "region": "regions", "text": "texts"}
STYLES = {"block": {"text", "label", "fill", "border", "color", "font_weight", "size_pt",
                    "corner_radius", "shadow", "inner_shadow", "border_width", "label_pos", "icon_color"},
          "region": {"fill", "border", "border_color", "label", "label_pos", "font_weight", "size",
                     "corner_radius", "shadow", "inner_shadow", "border_width"},
          "link": {"label", "label_pos", "label_gap", "color", "dash", "font_weight", "size_pt"},
          "text": {"text", "anchor", "color", "font_weight", "size_pt"}}
DIRECTIONS = {"right": (1, 0), "left": (-1, 0), "up": (0, 1), "down": (0, -1)}


def selected_objects(table, keys):
    if not isinstance(keys, list) or not 1 <= len(keys) <= 128:
        raise ValueError("请在图上选择要修改的对象")
    result = {}
    for key in keys:
        if not isinstance(key, str):
            raise ValueError("选区无效")
        kind, _, name = key.partition(":")
        objects = table.get(SECTIONS.get(kind), [])
        if kind == "block":
            index = next((i for i, obj in enumerate(objects) if obj["id"] == name), -1)
        else:
            index = int(name) if kind in SECTIONS and name.isascii() and name.isdecimal() else -1
        if not 0 <= index < len(objects):
            raise ValueError(f"选区中的 {key} 已不存在")
        result[key] = (kind, index)
    return result


def edit(table, selection, proposal, profile, cancelled):
    from .ai_structure import _route_links, endpoint, structural_links
    targets = selected_objects(table, selection)
    ops = proposal.get("edits")
    if set(proposal) != {"edits"} or not isinstance(ops, list) or not 1 <= len(ops) <= 128:
        raise ValueError("请返回仅含 edits 的局部修改清单")
    out = deepcopy(table)
    prof = get_profile(profile)
    scale = check(table, prof).geometry.s
    moves, routes = {}, set()
    for op in ops:
        if not isinstance(op, dict) or set(op) - {"target", "set", "move", "distance_cm", "reroute"}:
            raise ValueError("局部编辑只接受 target、set、move、distance_cm、reroute")
        target = op.get("target")
        if not isinstance(target, str) or target not in targets:
            raise ValueError(f"不能修改选区外的对象 {target}")
        kind, index = targets[target]
        section = SECTIONS[kind]
        obj = out[section][index]
        fields = op.get("set", {})
        if not isinstance(fields, dict) or set(fields) - STYLES[kind]:
            raise ValueError(f"{target} 可修改的字段为 {sorted(STYLES[kind])}")
        obj.update(deepcopy(fields))
        if "move" in op or "distance_cm" in op:
            direction, distance = op.get("move"), op.get("distance_cm", .6)
            if kind == "link" or not isinstance(direction, str) or direction not in DIRECTIONS or type(distance) not in (int, float) or not 0 < distance <= 10:
                raise ValueError("移动需要方向及 0..10 cm 范围内的正数")
            dx, dy = [v * distance / scale for v in DIRECTIONS[direction]]
            out[section][index] = library.shift({section: [obj]}, dx, dy)[section][0]
            if kind == "block":
                old = moves.get(obj["id"], (0, 0))
                moves[obj["id"]] = (old[0] + dx, old[1] + dy)
        if "reroute" in op:
            if kind != "link" or op["reroute"] is not True:
                raise ValueError("reroute 仅适用于选中的信号线")
            routes.add(index)
    if not moves and not routes:
        return out
    logical, native, _, _ = structural_links(table, profile)
    # A branch can start partway down a trunk. Rebuild the affected output's
    # fan-out together so that no branch stays attached to the old trunk.
    source_for = {}
    for link in logical:
        pair = (link["from"], link["to"])
        source_for[table["links"].index(native[pair])] = pair
    affected = {src for i, (src, dst) in source_for.items()
                if i in routes or endpoint(src)[0] in moves or endpoint(dst)[0] in moves}
    kept, pending, rerouted = [], [], set()
    for i, raw in enumerate(out.get("links", [])):
        pair = source_for.get(i)
        if not pair or pair[0] not in affected:
            if raw.get("open"):
                owners = {endpoint(raw[k])[0] for k in ("from", "to") if isinstance(raw[k], str)}
                delta = next((moves[bid] for bid in owners if bid in moves), None)
                if delta:
                    raw = library.shift({"links": [raw]}, *delta)["links"][0]
                if i in routes:
                    raise ValueError("独立端口引线随所属方块移动")
            kept.append(raw)
            continue
        draw = {k: v for k, v in raw.items() if k != "via"}
        draw.update(zip(("from", "to"), pair))
        pending.append(draw)
        rerouted.add(pair)
    if pending:
        _route_links(out, kept, pending, prof, min_run=AUTO_ROUTE_MIN_RUN, check_cancelled=cancelled)
        after, rebuilt, _, _ = structural_links(out, profile)
        if set(rebuilt) != set(native) or len(after) != len(logical):
            raise ValueError("调整改变了信号连接，请缩小移动距离")
        # Keep the indices used by selections, links and display metadata.
        ordered = iter(kept)
        out["links"] = [deepcopy(rebuilt[source_for[i]]) if i in source_for and source_for[i] in rerouted
                        else next(ordered) for i in range(len(table["links"]))]
    else:
        out["links"] = kept
    return out
