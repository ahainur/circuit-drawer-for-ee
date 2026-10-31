"""AI-only structure adapter; ordinary control tables remain the saved truth.

Templates/snippets keep their authored geometry. New blocks are placed by this
adapter, and new links use the existing module visibility-grid router. Neither
the editor's manual operations nor the control analysis/codegen engines change.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import re

from ..ai.loop import Evaluation, extract_table
from ..ai.prompt import CONTROL_HINTS, control_system_prompt
from ..profiles import get_profile
from . import library
from .check import (BLOCK_CLEARANCE, MIN_ARROW_RUN, SIDE_DIR, TEXT_PAD, TEXT_PAD_Y,
                    check, geometry, on_boundary, port_point, text_box_size)
from .loop import evaluate as number, parse_tf
from .model import BLOCK_TYPES, BOX_TYPES, ControlError, parse, validate_appearance
from .render import available, template

MAX_BLOCKS = 64              # bound model output and routing work
NEW_COLUMNS = 4              # keep newly generated rows within a double column
BLOCK_GAP = 1.0              # room for arrowheads and routes between boxes
ROW_GAP = 2.0                # routing lanes between rows
REGION_PAD = 0.4             # clear block labels when framing a group
ROUTE_PASSES = 3             # circle ports depend on the final printed scale
ARROW_MARGIN = 0.08          # cm above the checker minimum, allowing scale rounding
PORT_LANE_HALF = 0.04        # cm reserved beside an unused port's arrow approach
PORT_EXIT_LEAD = 0.08        # cm: a source has no arrowhead, so can turn earlier
BLOCK_FIELDS = {"id", "type", "text", "label", "label_pos", "signs", "style", "fill", "border", "color", "size_pt", "on", "toggle"}
STRUCTURE_FIELDS = {"template", "snippets", "name", "title", "blocks", "links", "regions", "inputs", "outputs", "stateful", "dynamic_links", "remove", "loop"}
ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
REF = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z0-9_]+)([+-][0-9]*\.?[0-9]+)?$")


class StructureError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(f"{code}: {message} — {CONTROL_HINTS[code]}")


def fail(code, message):
    raise StructureError(code, message)


def array(value, what):
    if not isinstance(value, list):
        fail("control-schema", f"{what} 必须是列表")
    return value


def endpoint(value):
    match = REF.fullmatch(value) if isinstance(value, str) else None
    if not match:
        fail("control-endpoint", f"连接端点 {value!r} 不是 block.port")
    return match[1], match[2], float(match[3] or 0)


def structural_links(raw, profile):
    """Resolve authored branch points to their source port before prompting AI."""
    from .check import _on_path
    rep = check(raw, get_profile(profile))
    dia, geom = rep.diagram, rep.geometry

    def ref(end, point):
        if end.block is not None:
            return end.block + "." + end.port + (f"{end.offset:+g}" if end.offset else "")
        for block in dia.blocks:
            side = on_boundary(block, point, geom.s)
            if side:
                return block.id + "." + side
        return None

    def source(i, seen):
        path = geom.paths[i]
        if not path:
            return None
        direct = ref(dia.links[i].start, path[0])
        if direct:
            return direct
        for j, other in enumerate(geom.paths):
            if j != i and j not in seen and other and _on_path(path[0], other):
                found = source(j, seen | {i})
                if found:
                    return found
        return None

    result, native, inputs, outputs = [], {}, set(), set()
    for i, (link, path) in enumerate(zip(dia.links, geom.paths)):
        if not path:
            fail("control-layout", f"当前图的连接 {i + 1} 无法解析；请先修正当前图")
        src, dst = source(i, set()), ref(link.end, path[-1])
        if src and dst:
            connection = {k: deepcopy(v) for k, v in raw["links"][i].items() if k in ("label", "label_pos", "color", "dash", "size_pt")}
            connection.update({"from": src, "to": dst})
            result.append(connection)
            native[(src, dst)] = deepcopy(raw["links"][i])
        elif src and link.open:
            outputs.add(endpoint(src)[0])
        elif dst and link.open:
            inputs.add(endpoint(dst)[0])
        else:
            fail("control-dangling", f"当前图的连接 {i + 1} 悬空")
    incoming = {endpoint(l["to"])[0] for l in result}
    outgoing = {endpoint(l["from"])[0] for l in result}
    inputs.update(b["id"] for b in raw.get("blocks", []) if b["id"] not in incoming and b["id"] in outgoing)
    outputs.update(b["id"] for b in raw.get("blocks", []) if b["id"] in incoming and b["id"] not in outgoing)
    return result, native, sorted(inputs), sorted(outputs)


def to_structure(raw, profile="double"):
    links, _, inputs, outputs = structural_links(raw, profile)
    blocks = []
    for block in raw.get("blocks", []):
        item = {k: deepcopy(v) for k, v in block.items() if k in BLOCK_FIELDS}
        if block.get("type") == "switch":
            item["throws"] = list(block.get("throws", {}))
        if block["id"] in raw.get("loop", {}).get("tf", {}):
            item["tf"] = deepcopy(raw["loop"]["tf"][block["id"]])
        blocks.append(item)
    regions = []
    for i, region in enumerate(raw.get("regions", [])):
        members = region.get("members")
        if members is None:
            x0, y0 = region["from"]; x1, y1 = region["to"]
            members = [b["id"] for b in raw.get("blocks", []) if x0 <= b.get("at", b.get("pole"))[0] <= x1 and y0 <= b.get("at", b.get("pole"))[1] <= y1]
        regions.append({"id": f"region_{i}", "members": members,
                        **{k: deepcopy(v) for k, v in region.items() if k in ("label", "fill", "border", "border_color", "label_pos")}})
    metadata = raw.get("control_ai", {})
    return {"name": raw.get("name", "control_ai"), "title": raw.get("title", ""), "blocks": blocks,
            "links": links, "inputs": metadata.get("inputs", inputs), "outputs": metadata.get("outputs", outputs),
            "stateful": metadata.get("stateful", []), "dynamic_links": metadata.get("dynamic_links", []), "regions": regions,
            **({"loop": deepcopy(raw["loop"])} if "loop" in raw else {})}


def _merge_objects(base, patch):
    result = deepcopy(base)
    for key, value in patch.items():
        result[key] = _merge_objects(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else deepcopy(value)
    return result


def expand(proposal, current, profile, directory=None):
    if not isinstance(proposal, dict) or set(proposal) - STRUCTURE_FIELDS:
        fail("control-schema", f"顶层字段只允许 {sorted(STRUCTURE_FIELDS)}")
    base = deepcopy(current) if current else {"kind": "control", "name": "control_ai", "blocks": [], "links": [], "regions": [], "texts": []}
    if "template" in proposal:
        try:
            base = template(proposal["template"], directory)
        except (KeyError, TypeError):
            fail("control-template", f"未知模板 {proposal['template']!r}")
    for name in array(proposal.get("snippets", []), "snippets"):
        try:
            part = library.load_snippet(name, directory)
            base, _ = library.insert(base, part, library.below(base, part))
        except (library.LibraryError, KeyError, TypeError):
            fail("control-template", f"未知片段 {name!r}")
    structure = to_structure(base, profile)
    # These annotations describe legacy power-line measurement taps, whose
    # drawing differs from a pure signal graph. Trust authored annotations,
    # never let a model declare arbitrary edges dynamic to evade cycle checks.
    for link in array(proposal.get("dynamic_links", []), "dynamic_links"):
        if link not in structure["dynamic_links"]:
            fail("control-tf", "AI 不能新增 dynamic_links 豁免；保留模板已有注释，或用真实动态方块及传递函数表达反馈")
    blocks = {b["id"]: b for b in structure["blocks"]}
    remove = array(proposal.get("remove", []), "remove")
    if any(not isinstance(bid, str) for bid in remove):
        fail("control-block", "remove 必须是方块 id 列表")
    removed = set(remove)
    for key in removed:
        if key not in blocks:
            fail("control-block", f"remove 引用了不存在的方块 {key}")
        del blocks[key]
    seen = set()
    for block in array(proposal.get("blocks", []), "blocks"):
        if not isinstance(block, dict) or not isinstance(block.get("id"), str) or not ID.fullmatch(block["id"]):
            fail("control-block", "每个方块必须有合法 id")
        if block["id"] in seen:
            fail("control-block", f"重复方块 {block['id']}")
        seen.add(block["id"])
        if set(block) - (BLOCK_FIELDS | {"tf", "throws"}):
            fail("control-coordinates", f"方块 {block['id']} 含不支持的字段 {sorted(set(block) - (BLOCK_FIELDS | {'tf', 'throws'}))}")
        old = blocks.get(block["id"], {})
        blocks[block["id"]] = {**old, **deepcopy(block)}
    structure["blocks"] = list(blocks.values())
    for key in ("links", "regions", "inputs", "outputs", "stateful", "dynamic_links", "name", "title"):
        if key in proposal:
            previous = structure.get(key, [])
            structure[key] = deepcopy(proposal[key])
            if key == "regions" and isinstance(structure[key], list):
                for region in structure[key]:
                    if not isinstance(region, dict):
                        continue
                    old = next((r for r in previous if r.get("id") == region.get("id")), {})
                    for field in ("fill", "border", "border_color"):
                        if field in old and field not in region:
                            region[field] = deepcopy(old[field])
    if "loop" in proposal:
        if not isinstance(proposal["loop"], dict):
            fail("control-tf", "loop 必须是对象")
        for key in ("tf", "params"):
            if key in proposal["loop"] and not isinstance(proposal["loop"][key], dict):
                fail("control-tf", f"loop.{key} 必须是对象")
        structure["loop"] = _merge_objects(structure.get("loop", {}), proposal["loop"])
        # A TF is one complete form: changing PI to PR must not merge the two
        # mutually exclusive forms into the same object.
        if "tf" in proposal["loop"]:
            structure["loop"]["tf"].update(deepcopy(proposal["loop"]["tf"]))
    explicit_tf = {b["id"] for b in proposal.get("blocks", []) if "tf" in b}
    patched_tf = proposal.get("loop", {}).get("tf", {})
    for block in structure["blocks"]:
        if block["id"] in patched_tf and block["id"] not in explicit_tf:
            block["tf"] = deepcopy(patched_tf[block["id"]])
        if "tf" in block:
            structure.setdefault("loop", {}).setdefault("tf", {})[block["id"]] = deepcopy(block["tf"])
    # Explicit removals remove their old data references, never arbitrary unknown ids.
    for mapping in (structure.get("loop", {}).get("tf", {}), base.get("code", {}).get("blocks", {}),
                    base.get("functions", {})):
        for bid in removed:
            mapping.pop(bid, None)
    return structure, base


def validate_structure(s):
    blocks_list = array(s.get("blocks"), "blocks")
    if not 1 <= len(blocks_list) <= MAX_BLOCKS:
        fail("control-block", f"方块数量必须为 1–{MAX_BLOCKS}")
    blocks = {b["id"]: b for b in blocks_list}
    for b in blocks_list:
        try:
            validate_appearance(b, "blocks", f"方块 {b['id']}")
        except ControlError as exc:
            fail("control-appearance", str(exc))
        if not isinstance(b.get("type"), str) or b["type"] not in BLOCK_TYPES:
            fail("control-type", f"{b['id']} 的类型 {b.get('type')!r} 不存在")
        for field in ("text", "label"):
            if field in b and b[field] is not None and not isinstance(b[field], str):
                fail("control-block", f"{b['id']}.{field} 必须是文字或公式字符串")
        if b["type"] == "switch" and (not isinstance(b.get("throws"), list) or not b["throws"] or any(not isinstance(p, str) or not ID.fullmatch(p) for p in b["throws"])):
            fail("control-coordinates", f"{b['id']}.throws 必须是接点名列表，不含坐标")
    declared = {}
    for name in ("inputs", "outputs", "stateful"):
        values = array(s.get(name, []), name)
        if any(not isinstance(v, str) or v not in blocks for v in values):
            fail("control-block", f"{name} 引用了不存在的方块")
        declared[name] = set(values)
    if any(blocks[bid]["type"] != "block" for bid in declared["stateful"]):
        fail("control-type", "stateful 只能声明有真实状态且无直通项的 block")
    ins, outs = {k: set() for k in blocks}, {k: set() for k in blocks}
    edges = []
    dynamic_links = array(s.get("dynamic_links", []), "dynamic_links")
    pairs = {(l.get("from"), l.get("to")) for l in array(s.get("links", []), "links") if isinstance(l, dict) and isinstance(l.get("from"), str) and isinstance(l.get("to"), str)}
    for link in dynamic_links:
        if not isinstance(link, dict) or set(link) != {"from", "to"} or any(not isinstance(ref, str) for ref in link.values()) or (link["from"], link["to"]) not in pairs:
            fail("control-endpoint", "dynamic_links 必须引用一条实际存在的信号连线")
    for i, link in enumerate(array(s.get("links", []), "links")):
        if not isinstance(link, dict) or set(link) - {"from", "to", "label", "label_pos", "color", "dash", "size_pt"}:
            fail("control-coordinates", "links 只接收端口和标签，不接收 via 或坐标")
        try:
            validate_appearance(link, "links", f"links[{i}]")
        except ControlError as exc:
            fail("control-appearance", str(exc))
        ends = [endpoint(link.get(k)) for k in ("from", "to")]
        for bid, port, offset in ends:
            if bid not in blocks:
                fail("control-endpoint", f"端点引用未知方块 {bid}")
            allowed = ["c", *blocks[bid]["throws"]] if blocks[bid]["type"] == "switch" else list(SIDE_DIR)
            if port not in allowed:
                fail("control-endpoint", f"{bid}.{port} 不存在，可用 {allowed}")
        (src, sp, so), (dst, dp, do) = ends
        sp += f"{so:+g}" if so else ""
        dp += f"{do:+g}" if do else ""
        if dp in ins[dst]:
            fail("control-input", f"{dst}.{dp} 收到多个输入")
        ins[dst].add(dp); outs[src].add(sp)
        if not any(link["from"] == d["from"] and link["to"] == d["to"] for d in dynamic_links):
            edges.append((src, dst))
    for bid, block in blocks.items():
        if not ins[bid] and bid not in declared["inputs"]:
            fail("control-dangling", f"{bid} 没有输入")
        if not outs[bid] and bid not in declared["outputs"]:
            fail("control-dangling", f"{bid} 没有输出")
        if ins[bid] & outs[bid]:
            fail("control-input", f"{bid} 的同一个端口同时用作输入和输出")
        if block["type"] == "sum":
            signs = block.get("signs")
            if not isinstance(signs, dict) or len(signs) < 2 or set(signs) != ins[bid] or any(v not in ("+", "-") for v in signs.values()):
                fail("control-sign", f"{bid} 的 signs 必须逐一对应所有输入，且至少两个")
        if block["type"] in ("mul", "div") and len(ins[bid]) < 2:
            fail("control-input", f"{bid} 需要两个输入")
    for region in array(s.get("regions", []), "regions"):
        if not isinstance(region, dict) or set(region) - {"id", "members", "label", "fill", "border", "border_color", "label_pos"}:
            fail("control-coordinates", "分区只接收成员、标签和样式")
        members = array(region.get("members"), "region.members")
        if not members or any(not isinstance(m, str) or m not in blocks for m in members):
            fail("control-block", "分区成员必须是已定义的方块")
    _algebraic_cycles(s, edges)


def _algebraic_cycles(s, edges):
    """Reject cycles made entirely of direct-feedthrough blocks, including plant edges."""
    loop = s.get("loop", {})
    env = {}
    try:
        for name, value in loop.get("params", {}).items():
            env[name] = number(value, env, name)
        dynamic = set(s.get("stateful", []))
        for bid, tf in loop.get("tf", {}).items():
            parsed = parse_tf(tf, env, bid)
            if parsed.delay > 0 or len(parsed.zpk.poles) > len(parsed.zpk.zeros):
                dynamic.add(bid)
            elif bid in dynamic:
                fail("control-tf", f"{bid} 的数值传递函数有直通项，与 stateful 声明矛盾")
        graph = {b["id"]: [] for b in s["blocks"]}
        for src, dst in edges:
            if dst not in dynamic:
                graph[src].append(dst)
        for plant in loop.get("plant", []):
            parsed = parse_tf({k: v for k, v in plant.items() if k not in ("from", "to", "label")}, env, "plant")
            if parsed.delay == 0 and len(parsed.zpk.poles) <= len(parsed.zpk.zeros):
                if plant.get("from") in graph and plant.get("to") in graph:
                    graph[plant["from"]].append(plant["to"])
    except (ValueError, TypeError, AttributeError, ZeroDivisionError) as exc:
        fail("control-tf", str(exc))
    visiting, visited = set(), set()
    def visit(node):
        if node in visiting:
            fail("control-algebraic-loop", f"{node} 位于没有动态环节的反馈环中")
        if node in visited:
            return
        visiting.add(node)
        for child in graph[node]:
            visit(child)
        visiting.remove(node); visited.add(node)
    for node in graph:
        visit(node)


def _new_blocks(structure, base, prof):
    old = {b["id"]: b for b in base.get("blocks", [])}
    result, fresh = [], []
    for b in structure["blocks"]:
        block = {**deepcopy(old.get(b["id"], {})), **{k: deepcopy(v) for k, v in b.items() if k in BLOCK_FIELDS}}
        if b["id"] not in old or b["type"] != old[b["id"]]["type"]:
            for key in ("at", "size", "pole", "throws"):
                block.pop(key, None)
            fresh.append(block)
        if block["type"] in BOX_TYPES and "size" not in block:
            w, h = text_box_size(block.get("text", ""), prof.font_pt)
            block["size"] = [max(0.8, w + 2 * TEXT_PAD + .1), max(.6, h + 2 * TEXT_PAD_Y + .1)]
        if block["type"] == "switch" and "throws" not in block:
            block["throws"] = {name: [0, 0] for name in b["throws"]}
        result.append(block)
    # Reuse the snippet placement convention for the next row; existing blocks stay put.
    anchor = (0, 0)
    if base.get("blocks") and fresh:
        ys = [b.get("at", b.get("pole"))[1] - b.get("size", [0, 0])[1] / 2 for b in base["blocks"]]
        anchor = (0, min(ys) - ROW_GAP)
    width = max([b.get("size", [1, 1])[0] for b in fresh] or [1]) + BLOCK_GAP
    height = max([b.get("size", [1, 1])[1] for b in fresh] or [1]) + ROW_GAP
    for i, block in enumerate(fresh):
        x, y = anchor[0] + (i % NEW_COLUMNS) * width, anchor[1] - (i // NEW_COLUMNS) * height
        # Keep additions near their existing signal consumer/producer. In an
        # edit, a new summing point should sit below its consumer, rather than
        # making every return wire cross the entire original controller.
        consumers = [old[endpoint(l["to"])[0]] for l in structure["links"] if endpoint(l["from"])[0] == block["id"] and endpoint(l["to"])[0] in old]
        if consumers:
            x = min(b.get("at", b.get("pole"))[0] - b.get("size", [0, 0])[0] / 2 for b in consumers) - BLOCK_GAP
        elif base.get("blocks"):
            producers = [old[endpoint(l["from"])[0]] for l in structure["links"] if endpoint(l["to"])[0] == block["id"] and endpoint(l["from"])[0] in old]
            if producers:
                x = max(b.get("at", b.get("pole"))[0] + b.get("size", [0, 0])[0] / 2 for b in producers) + BLOCK_GAP
        for previous in fresh[:i]:
            px, py = previous.get("at", previous.get("pole"))
            if abs(py-y) < height / 2 and abs(px-x) < width:
                x = px + width
        if block["type"] == "switch":
            block["pole"] = [x, y]
            block["throws"] = {name: [x - .6, y + (len(block["throws"]) - 1 - 2 * j) * .4] for j, name in enumerate(block["throws"])}
        else:
            block["at"] = [x, y]
    return result


def compile_structure(s, base, profile):
    """Build a conventional control table; never let AI-provided coordinates through."""
    prof = get_profile(profile)
    out = deepcopy(base)
    out.update(kind="control", name=s["name"], title=s["title"], profile=prof.name)
    out["blocks"] = _new_blocks(s, base, prof)
    out["control_ai"] = {key: deepcopy(s[key]) for key in ("inputs", "outputs", "stateful", "dynamic_links")}
    if "loop" in s:
        out["loop"] = deepcopy(s["loop"])
    _, native, _, base_outputs = structural_links(base, profile)
    same_geometry = {b["id"] for b in out["blocks"] for old in base["blocks"] if b["id"] == old["id"] and all(b.get(k) == old.get(k) for k in ("type", "at", "size", "pole", "throws"))}
    kept, pending = [], []
    for link in s["links"]:
        key = (link["from"], link["to"])
        # Rerouting changes geometry, not a logical connection's appearance.
        link = {**{k: v for k, v in native.get(key, {}).items() if k in ("color", "dash")}, **link}
        if key in native and all(endpoint(ref)[0] in same_geometry for ref in key):
            kept.append({**native[key], **{k: v for k, v in link.items() if k not in ("from", "to")}})
        else:
            pending.append(deepcopy(link))
    ids = {b["id"] for b in out["blocks"]}
    # Keep explicit template input/output stubs and their captions.
    kept += [deepcopy(l) for l in base["links"] if l.get("open") and any(isinstance(l.get(k), str) and endpoint(l[k])[0] in ids and endpoint(l[k])[0] in s[boundary] for k, boundary in (("from", "outputs"), ("to", "inputs")))]
    # Keep original order as well as geometry, including interspersed output stubs.
    def route_key(link):
        return json.dumps([link.get(k) for k in ("from", "via", "to")], sort_keys=True)
    order = {route_key(link): i for i, link in enumerate(base["links"])}
    kept.sort(key=lambda link: order.get(route_key(link), len(order)))
    for bid in s["outputs"]:
        if bid in base_outputs:
            continue
        if not any(isinstance(l.get("from"), str) and endpoint(l["from"])[0] == bid for l in kept + pending):
            b = next(b for b in out["blocks"] if b["id"] == bid)
            if b["type"] != "tag":
                x, y = b.get("at", b.get("pole")); w = b.get("size", [0, 0])[0]
                kept.append({"from": bid + ".e", "to": [x + w / 2 + BLOCK_GAP, y], "open": True})
    old_regions = to_structure(base, profile)["regions"]
    out["regions"] = []
    for region in s["regions"]:
        if region in old_regions and set(region["members"]) <= same_geometry:
            out["regions"].append(deepcopy(base["regions"][old_regions.index(region)]))
        else:
            members = [b for b in out["blocks"] if b["id"] in region["members"]]
            bounds = library._bounds(members)
            out["regions"].append({k: deepcopy(v) for k, v in region.items() if k != "id"} | {
                "from": [bounds[0] - REGION_PAD, bounds[1] - REGION_PAD],
                "to": [bounds[2] + REGION_PAD, bounds[3] + REGION_PAD]})
    out["links"] = kept
    if pending:
        _route_links(out, kept, pending, prof)
    return out


def _route_links(out, kept, pending, prof):
    from ..modules import _grid_route, _on, _segs, _trim
    # Use the same visibility-grid router as module wiring, with control geometry.
    scale = check(out, prof).geometry.s
    for _ in range(ROUTE_PASSES):
        out["links"] = deepcopy(kept)
        dia = parse(out)
        geom = geometry(dia, scale, prof)
        # Router vertices are rounded to four decimals; box edges must use the
        # same precision so a circle port is not mistaken for an interior point.
        boxes = [tuple(round(v / scale, 4) for v in (b.x0, b.y0, b.x1, b.y1)) for b in geom.bodies.values()]
        wires = [segment for path in geom.paths if path for segment in _segs(path)]
        branches = {}
        for native, path in zip(kept, geom.paths):
            if isinstance(native["from"], str) and path:
                branches.setdefault(native["from"], []).extend(_segs(path))
        pins = []
        reservations = {}
        for link in kept + pending:
            for key in ("from", "to"):
                if isinstance(link.get(key), str):
                    bid, side, offset = endpoint(link[key])
                    pins.append(tuple(round(v, 4) for v in port_point(dia.block(bid), side, offset, scale)))
                    if link in pending and side in SIDE_DIR:
                        point = pins[-1]
                        distance = MIN_ARROW_RUN + ARROW_MARGIN if key == "to" else PORT_EXIT_LEAD
                        lead = tuple(x + d * distance / scale for x, d in zip(point, SIDE_DIR[side]))
                        half = PORT_LANE_HALF / scale
                        reservations[link[key]] = (min(point[0], lead[0])-half, min(point[1], lead[1])-half,
                                                   max(point[0], lead[0])+half, max(point[1], lead[1])+half)
        for link in pending:
            ends = []
            for key in ("from", "to"):
                bid, side, offset = endpoint(link[key])
                block = dia.block(bid)
                point = tuple(round(v, 4) for v in port_point(block, side, offset, scale))
                face = SIDE_DIR.get(side)
                ends.append((point, face, True))
            same = branches.get(link["from"], [])
            route_boxes = boxes + [box for ref, box in reservations.items() if ref not in (link["from"], link["to"])]
            starts = [] if same else [ends[0]]
            for a, b in same:
                middle = tuple(round((x + y) / 2, 4) for x, y in zip(a, b))
                starts.append((middle, None, False))
            path = None
            for start in starts:
                path = _grid_route(start, ends[1], wires, pins, route_boxes, same, clearance=BLOCK_CLEARANCE / scale)
                if path and len(path) > 1 and sum(abs(x-y) for x,y in zip(path[-2], path[-1])) * scale < MIN_ARROW_RUN + ARROW_MARGIN:
                    target, face, _ = ends[1]
                    if face:
                        lead = tuple(round(x + d * (MIN_ARROW_RUN + ARROW_MARGIN) / scale, 4) for x, d in zip(target, face))
                        path = _grid_route(start, (lead, face, False), wires, pins, route_boxes, same, clearance=BLOCK_CLEARANCE / scale)
                        if path:
                            path.append(target)
                            path = [p for i, p in enumerate(path) if i in (0, len(path)-1) or not _on(p, path[i-1], path[i+1])]
                if path:
                    path = _trim(path, same)
                    if len(path) > 1:
                        break
            if not path or len(path) < 2:
                fail("control-layout", f"{link['from']} → {link['to']} 没有可用连线路径")
            draw = deepcopy(link)
            if path[0] != ends[0][0]:
                draw["from"] = list(path[0])
            if len(path) > 2:
                draw["via"] = [list(p) for p in path[1:-1]]
            out["links"].append(draw)
            wires.extend(_segs(path)); branches.setdefault(link["from"], []).extend(_segs(path))
        new_scale = check(out, prof).geometry.s
        if abs(scale - new_scale) < 1e-6:
            break
        scale = new_scale


@dataclass
class ControlDocument:
    table: dict
    structure: dict

    def to_dict(self):
        return deepcopy(self.table)


class ControlWorkflow:
    document_kind = "框图结构"
    max_rounds = 3

    def __init__(self, profile="double", current=None, directory=None):
        self.profile, self.current, self.directory = profile, deepcopy(current), directory

    def system_prompt(self):
        # Catalogs contain structural examples, never coordinates or API credentials.
        catalog = {name: to_structure(template(name, self.directory), self.profile) for name in available(self.directory)}
        snippets = {name: to_structure(library.load_snippet(name, self.directory), self.profile) for name in library.all_snippets(self.directory)}
        return control_system_prompt(catalog, snippets)

    def first_message(self, description):
        if self.current is None:
            return "创建控制框图，只输出结构 JSON。需求：\n" + description
        return ("只修改要求涉及的结构，保留其他方块、文字、连接、分区及传递函数参数。"
                "未要求改配色时保留 fill/border/color/dash；重新布线也要保留连线颜色和线型。省略的字段保留；"
                "links/regions/inputs/outputs 若提供则完整替换。不要输出坐标或 code。\n要求：" + description +
                "\n当前结构：\n" + json.dumps(to_structure(self.current, self.profile), ensure_ascii=False))

    def evaluate(self, reply):
        structure, base = expand(extract_table(reply), self.current, self.profile, self.directory)
        validate_structure(structure)
        table = compile_structure(structure, base, self.profile)
        report = check(table, get_profile(self.profile))
        errors = ["control-layout: " + error + " — " + CONTROL_HINTS["control-layout"] for error in report.errors]
        return Evaluation(ControlDocument(table, structure), errors)

    def unreadable(self, exc):
        return f"结构被拒绝：{exc}\n按错误码和提示修正，返回完整的结构 JSON；不要用坐标修复。"

    def feedback(self, result, best, improved, previous):
        return "\n".join(result.errors) + "\n请修正结构并完整重写；不得输出坐标。此前结构：\n" + json.dumps(result.layout.structure, ensure_ascii=False)

    def changes(self, document):
        old = {b["id"]: b for b in (self.current or {}).get("blocks", [])}
        new = {b["id"]: b for b in document.table["blocks"]}
        return ([f"新增方块 {bid}" for bid in new if bid not in old] +
                [f"删除方块 {bid}" for bid in old if bid not in new] +
                [f"更新方块 {bid}" for bid in new if bid in old and new[bid] != old[bid]] +
                (["连接或分区已更新"] if self.current and any(document.table.get(k) != self.current.get(k) for k in ("links", "regions")) else []) +
                (["传递函数参数已更新，环路分析可直接读取"] if self.current and document.table.get("loop") != self.current.get("loop") else []))
