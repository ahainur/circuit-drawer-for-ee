"""Prepare source geometry for a checked editor scaling transaction."""
from copy import deepcopy

from ..drawing_scale import transform
from ..geometry import FONT_RANGE, font_of
from ..layout import DECORATION_SECTIONS, Layout
from ..profiles import get_profile
from ..validate import check_with_assembly, extent
from .selection import contact_graph, selection_plan

SECTIONS = ("annotations", *DECORATION_SECTIONS)


def scale_modules(raw, assembly, scale, chosen, parts, factor, point, prof, text):
    from ..modules import _definitions, _faces, assemble, collapsed_dimensions, MIN_BOX
    spec = raw["modules"]
    definitions = _definitions(spec)
    changed = {}
    for name, definition in definitions.items():
        users = [i for i in spec["instances"] if i["definition"] == name]
        targets = [i for i in users if i["id"] in chosen]
        if not targets:
            continue
        key = name
        if len(targets) != len(users):
            key = name + "_scaled"
            n = 2
            while key in definitions or key in changed:
                key = f"{name}_scaled{n}"; n += 1
        copied = deepcopy(definition)
        for drawing in [copied["layout"]] + ([copied["partial"]["layout"]] if "partial" in copied else []):
            moved = transform(Layout.from_dict(drawing), factor, [0,0], prof, text=text).to_dict()
            drawing.clear(); drawing.update(moved)
        changed[key] = copied
        local = Layout.from_dict(definition["layout"])
        faces = _faces(local, definition["ports"], scale, prof)
        for p, placement in definition.get("port_layout", {}).items():
            faces[p] = (-1,0) if placement["side"] == "left" else (1,0)
        n = max(sum(face[0] < 0 for face in faces.values()), sum(face[0] >= 0 for face in faces.values()), 1)
        for inst in targets:
            inst["definition"] = key
            style = inst.get("appearance", {})
            title = style.get("title", inst.get("label", {}).get("text", definition.get("title") or inst["id"]))
            size = inst.get("collapsed_size") or collapsed_dimensions(n, title, inst.get("symbol",definition.get("symbol","text")))
            inst["collapsed_size"] = {axis:round(v*factor,4) for axis,v in size.items()}
            if min(inst["collapsed_size"].values()) < MIN_BOX:
                raise ValueError(f"模块 {inst['id']} 缩放后的外框小于 {MIN_BOX}；请增大倍率")
            if "label" in inst:
                inst["label"]["at"] = [round(v*factor,4) for v in inst["label"]["at"]]
            if "title_offset" in style:
                style["title_offset"] = [round(v*factor,4) for v in style["title_offset"]]
            if text:
                size = round(font_of(style.get("title_size"),prof)*factor,8)
                if not FONT_RANGE[0] <= size <= FONT_RANGE[1]:
                    raise ValueError(f"模块 {inst['id']} 标题字号超出 {FONT_RANGE[0]}–{FONT_RANGE[1]} pt")
                inst.setdefault("appearance", {})["title_size"] = size
    if changed:
        spec.setdefault("definitions", {}).update(changed)
    for inst in spec["instances"]:
        old = assembly.origins.get(inst["id"], inst.get("at"))
        if old is None:
            # Hidden automatic instances have no on-canvas position. Giving
            # them [0,0] would invent contacts with the representative module.
            continue
        if inst["id"] in chosen:
            inst["at"] = point(old)
        elif chosen and inst["id"] in assembly.origins:
            # A preceding resized module must not relocate an unselected one.
            inst["at"] = list(old)
    for key, route in spec.get("routes", {}).items():
        ends = [r.rpartition(".")[0] for r in key.split("|")]
        if all(name in chosen or name in parts for name in ends):
            spec["routes"][key] = [point(p) for p in route]
    for part in spec.get("parts", []):
        if part["id"] not in parts:
            continue
        old = assembly.view.element(part["id"])
        single = Layout("part", elements=[deepcopy(old)])
        part["opts"] = transform(single,factor,[0,0],prof,text=text).elements[0].opts
    if not any(p["id"] in parts for p in spec.get("parts",[])):
        return
    provisional = assemble(Layout.from_dict(raw), scale, prof)
    for part in spec.get("parts", []):
        if part["id"] not in parts:
            continue
        old = assembly.view.element(part["id"])
        actual = provisional.view.element(part["id"])
        offsets = [[point(p)[k]-actual.pins[pin][k] for k in range(2)] for pin,p in old.pins.items()]
        if any(abs(a-b)>1e-3 for a,b in zip(offsets[0], offsets[-1])):
            raise ValueError(f"外部元件 {part['id']} 的两端不能按同一倍率跟随；请同时选择相连模块")
        part["offset"] = [round(v+d,4) for v,d in zip(part.get("offset",[0,0]),offsets[0])]


def prepare(source, selection, factor, profile, *, text=False):
    prof = get_profile(profile)
    raw = deepcopy(source.to_dict())
    before, assembly = check_with_assembly(source,prof)
    invalid = [i.message for i in before.errors if i.code.startswith("bad-") or i.code in {"unknown-kind","missing-pin"}]
    if invalid:
        raise ValueError("；".join(invalid))
    keys = {"elements", "wires", *SECTIONS}
    all_objects = selection is None
    instances = {i["id"] for i in raw.get("modules",{}).get("instances",[])}
    part_ids = {p["id"] for p in raw.get("modules",{}).get("parts",[])}
    if all_objects:
        selection = {key:list(range(len(raw.get(key, [])))) for key in keys if key != "elements"}
        selection["elements"] = [e.id for e in source.elements]
        selection.update(instances=sorted(instances),parts=sorted(part_ids))
    if not isinstance(selection, dict) or set(selection) - keys - {"instances","parts"}:
        raise ValueError("缩放选区含未知对象类型")
    for key, values in selection.items():
        if not isinstance(values, list):
            raise ValueError(f"缩放选区 {key} 须为列表")
        if key in {"elements","instances","parts"} and any(not isinstance(i,str) for i in values):
            raise ValueError(f"缩放选区 {key} 须使用对象 ID")
        if key not in {"elements","instances","parts"} and any(type(i) is not int or not 0 <= i < len(raw.get(key, [])) for i in values):
            raise ValueError(f"缩放选区 {key} 含已不存在的对象")
    ids = selection.get("elements", [])
    chosen, parts = set(selection.get("instances",[])), set(selection.get("parts",[]))
    if chosen - instances or parts - part_ids:
        raise ValueError("所选模块或自动元件已不存在")
    # Transient only: expose public contacts to the existing selection graph.
    graph = Layout.from_dict(source.to_dict())
    graph.extra.pop("modules",None)
    moving = list(ids)
    if assembly:
        for inst in raw["modules"]["instances"]:
            if inst["id"] in chosen:
                chosen.update(inst.get("group",{}).get("members",[]))
        if all_objects:
            chosen = instances
        for p in raw["modules"].get("parts",[]):
            peers = [b.rpartition(".")[0] for pair in raw["modules"].get("links",[])
                     for a,b in (pair,pair[::-1]) if a.rpartition(".")[0] == p["id"]]
            if peers and all(n in chosen or n in ids for n in peers):
                parts.add(p["id"])
        graph, moving = contact_graph(source, assembly, ids, chosen | parts)
    plan = selection_plan(graph, moving, selection.get("wires", []))
    subset = {"name":source.name,"elements":[e for e in raw["elements"] if e["id"] in ids],
              "wires":[r["wire"] for r in plan["runs"] if r["inside"]],"hops":[],"netlist":{}}
    for key in SECTIONS:
        subset[key] = [raw[key][i] for i in sorted(set(selection.get(key, [])))]
    measure = deepcopy(subset)
    if assembly:
        measure['elements'] += [e.to_dict() for e in assembly.view.elements if e.id.split('/')[0] in chosen or e.id in parts]
        if all_objects:
            measure['wires'] = deepcopy(assembly.view.wires)
        else:
            for name in chosen:
                start,end = assembly.instance_wires.get(name,(0,0))
                measure['wires'].extend(assembly.view.wires[start:end])
        for name in chosen:
            for section,(start,end) in assembly.instance_decorations.get(name,{}).items():
                if section in SECTIONS:
                    data = assembly.view.to_dict().get(section,[])
                    measure.setdefault(section,[]).extend(data[start:end])
    if not any(measure[k] for k in ("elements","wires",*SECTIONS)):
        raise ValueError("请先选中要缩放的电路")
    drawing = Layout.from_dict(subset)
    box = extent(Layout.from_dict(measure),before.scale,prof)
    anchor = [(box.x0+box.x1)/2/before.scale.x,(box.y0+box.y1)/2/before.scale.y]
    moved = transform(drawing,factor,anchor,prof,text=text).to_dict()
    if factor != 1:
        by_id = {e["id"]:e for e in moved["elements"]}
        raw["elements"] = [by_id.get(e["id"],e) for e in raw["elements"]]
        for key in SECTIONS:
            for i, item in zip(sorted(set(selection.get(key, []))), moved.get(key, [])):
                raw[key][i] = item
        raw["drawing_scale"] = list(before.scale)
        if assembly:
            point = lambda p: [round(c+(v-c)*factor,4) for c,v in zip(anchor,p)]
            scale_modules(raw,assembly,before.scale,chosen,parts | set(ids),factor,point,prof,text)
    return {"layout":raw,"plan":plan,"anchor":anchor,"factor":factor,"all":all_objects,"before_nets":before.nets}
