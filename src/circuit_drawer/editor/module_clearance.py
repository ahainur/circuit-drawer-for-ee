"""Make space for an explicitly requested module expansion in the source table."""
from copy import deepcopy
from collections import Counter
from math import ceil

from ..geometry import Box, annotation_box, footprint
from ..layout import DECORATION_SECTIONS, Layout
from ..module_authoring import _move_port_wires, new_edit_errors
from ..modules import assemble
from ..profiles import get_profile
from ..validate import PARALLEL_MIN, check_with_assembly, extent

# Use the validator's physical clearance and the editor's coordinate grid.
GRID = .05


def drawing_bounds(assembly, iid, scale, prof):
    frame = assembly.frames.get(iid)
    if frame is None:
        return None
    content = Layout(iid, elements=[e for e in assembly.view.elements if e.id.startswith(iid+'/')])
    first, end = assembly.instance_wires.get(iid, (0,0))
    # Extent also needs a seed when the module contains only port markers.
    content.wires = [*assembly.view.wires[first:end], [frame[:2],frame[2:]]]
    first, end = assembly.instance_decorations.get(iid,{}).get("annotations",(0,0))
    content.annotations = assembly.view.annotations[first:end]
    box = extent(content, scale, prof)
    return [min(frame[0],box.x0/scale.x), min(frame[1],box.y0/scale.y),
            max(frame[2],box.x1/scale.x), max(frame[3],box.y1/scale.y)]


def free_cuts(frame, boxes):
    """A strip must not split a neighbouring module's internal coordinates."""
    result = list(frame)
    for k in range(2):
        again = True
        while again:
            again = False
            for box in boxes:
                if box[k] < result[k+2]-1e-6 and box[k+2] > result[k]+1e-6:
                    lo, hi = min(result[k],box[k]), max(result[k+2],box[k+2])
                    if lo != result[k] or hi != result[k+2]:
                        result[k], result[k+2], again = lo, hi, True
    return result


def required_space(source, module_boxes, iid, frame, enlarged, scale, prof):
    boxes = [box for name, box in module_boxes.items() if name != iid]
    for element in source.elements:
        fp = footprint(element, scale, prof)
        shapes = fp.bodies + fp.labels
        shapes += [Box(min(p[0],q[0]),min(p[1],q[1]),max(p[0],q[0]),max(p[1],q[1])) for p,q in fp.leads]
        boxes += [[b.x0/scale.x,b.y0/scale.y,b.x1/scale.x,b.y1/scale.y] for b in shapes]
    for annotation in source.annotations:
        b = annotation_box(annotation, scale, prof)
        boxes.append([b.x0/scale.x,b.y0/scale.y,b.x1/scale.x,b.y1/scale.y])
    boxes += [[min(a[0],b[0]),min(a[1],b[1]),max(a[0],b[0]),max(a[1],b[1])]
              for wire in source.wires for a,b in zip(wire,wire[1:])]
    growth = []
    margin = PARALLEL_MIN / min(scale)
    for k in range(2):
        other = 1-k
        near = [b for b in boxes if b[other+2] >= enlarged[other]-margin
                and b[other] <= enlarged[other+2]+margin]
        lo = max([0] + [b[k+2]+margin-enlarged[k] for b in near if b[k+2] < frame[k]-1e-6])
        hi = max([0] + [enlarged[k+2]+margin-b[k] for b in near if b[k] > frame[k+2]+1e-6])
        growth.append((ceil(max(0,lo-1e-6)/GRID)*GRID if enlarged[k] < frame[k]-1e-6 else 0,
                       ceil(max(0,hi-1e-6)/GRID)*GRID if enlarged[k+2] > frame[k+2]+1e-6 else 0))
    return growth


def expand(source, displays, profile="double"):
    """Insert strips around the old frame; never rewrite a shared definition."""
    prof = get_profile(profile)
    before, assembly = check_with_assembly(source, prof)
    if not assembly:
        raise ValueError("模块结构无法解析，请先修正问题栏中的模块错误")
    raw = deepcopy(source.to_dict())
    instances = {i["id"]: i for i in raw["modules"]["instances"]}
    if not isinstance(displays, dict) or not displays or set(displays) - instances.keys():
        raise ValueError("所选模块已不存在，请重新选择")
    if any(not isinstance(display,str) or display not in {"expanded", "collapsed", "partial"} for display in displays.values()):
        raise ValueError("模块显示须为 expanded、collapsed 或 partial")
    original_nets = assembly.connectivity(prof, before.scale).nets
    changing = [iid for iid, display in displays.items() if instances[iid].get("display","expanded") != display]
    if not changing:
        return Layout.from_dict(raw)
    # Calculate each display once; inserting space only translates module bounds.
    # Reassembling/routing the whole project for every instance made Expand All slow.
    for inst in instances.values():
        if inst["id"] in assembly.origins:
            inst["at"] = list(assembly.origins[inst["id"]])
    boxes = {name:drawing_bounds(assembly,name,before.scale,prof) for name in assembly.frames}
    previous_ports = assembly.contact_pins
    for iid, display in displays.items():
        instances[iid]["display"] = display
    proposed = assemble(Layout.from_dict(raw), before.scale, prof)
    targets = {name:drawing_bounds(proposed,name,before.scale,prof) for name in proposed.frames}
    for iid in changing:
        if iid not in boxes or iid not in targets:
            continue
        delta = [v-proposed.origins[iid][k] for k,v in enumerate(instances[iid]["at"])]
        enlarged = [v+delta[k%2] for k,v in enumerate(targets[iid])]
        frame = free_cuts(boxes[iid], boxes.values())
        growth = required_space(Layout.from_dict(raw), boxes, iid, frame, enlarged, before.scale, prof)

        def point(p):
            return [round(v - growth[k][0] if v < frame[k]-1e-6
                          else v + growth[k][1] if v > frame[k+2]+1e-6 else v, 4)
                    for k, v in enumerate(p)]

        if any(lo or hi for lo, hi in growth):
            for element in raw["elements"]:
                element["pins"] = {name: point(p) for name, p in element["pins"].items()}
            raw["wires"] = [[point(p) for p in w] for w in raw["wires"]]
            raw["hops"] = [point(p) for p in raw["hops"]]
            for section in ("annotations", *DECORATION_SECTIONS):
                for item in raw.get(section, []):
                    for key in ("at", "from", "to"):
                        if key in item:
                            item[key] = point(item[key])
                    if "via" in item:
                        item["via"] = [point(p) for p in item["via"]]
            for key, route in raw["modules"].get("routes", {}).items():
                raw["modules"]["routes"][key] = [point(p) for p in route]
            for other, inst in instances.items():
                if other == iid or other not in boxes:
                    continue
                box = boxes[other]
                delta = [-growth[k][0] if box[k+2] <= frame[k]+1e-6
                         else growth[k][1] if box[k] >= frame[k+2]-1e-6 else 0 for k in range(2)]
                inst["at"] = [round(v+delta[k], 4) for k, v in enumerate(inst["at"])]
                boxes[other] = [v+delta[k%2] for k,v in enumerate(box)]
            previous_ports = {ref: point(p) for ref, p in previous_ports.items()}
        boxes[iid] = enlarged
    assembly = assemble(Layout.from_dict(raw), before.scale, prof)
    _move_port_wires(raw, previous_ports, assembly.contact_pins)
    result = Layout.from_dict(raw)
    after, assembly = check_with_assembly(result, prof)
    if not assembly:
        raise ValueError("展开后的模块结构无法解析，已保留原图")
    def groups(nets):
        values = {}
        for pin, net in nets.items():
            values.setdefault(net,set()).add(pin)
        return {frozenset(pins) for pins in values.values()}
    if groups(original_nets) != groups(assembly.connectivity(prof, after.scale).nets):
        raise ValueError("展开会改变电气连接，已保留原图；请先增大模块间距")
    # A wider drawing remains editable; the existing export gate reports its paper limit.
    errors = [i for i in new_edit_errors(before, after) if i.code != "drawing-scale-width"]
    # A free tail stays reported after moving; its coordinates and wire index change.
    old_counts, new_counts = Counter(i.code for i in before.errors), Counter(i.code for i in after.errors)
    if new_counts['dangling-wire'] <= old_counts['dangling-wire']:
        errors = [i for i in errors if i.code != 'dangling-wire']
    if errors:
        raise ValueError("无法自动留出足够空间，已保留原图：" + "；".join(i.message for i in errors[:5]))
    return result
