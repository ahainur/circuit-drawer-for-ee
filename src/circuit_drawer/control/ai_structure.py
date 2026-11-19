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

from ..ai.loop import Cancelled, Evaluation, extract_table
from ..ai.prompt import CONTROL_HINTS, control_system_prompt
from ..geometry import EPS, font_of
from ..profiles import get_profile
from . import library
from .check import (AUTO_ROUTE_MIN_RUN, BLOCK_CLEARANCE, MIN_ARROW_RUN, PORT_LABEL_GAP, PT_CM, SIDE_DIR, TEXT_PAD, TEXT_PAD_Y,
                    check, geometry, on_boundary, port_point, text_box_size, wire_clearance)
from .loop import evaluate as number, parse_tf
from .model import BLOCK_TYPES, BOX_TYPES, ControlError, parse, validate_appearance
from .render import available, template

MAX_BLOCKS = 64              # bound model output and routing work
NEW_COLUMNS = 4              # keep newly generated rows within a double column
BLOCK_GAP = 1.0              # room for arrowheads and routes between boxes
ROW_GAP = 2.0                # routing lanes between rows
REGION_PAD = 0.4             # clear block labels when framing a group
ROUTE_PASSES = 3             # circle ports depend on the final printed scale
STRICT_ROUTE_BUDGET = 18000   # cap a crossing-free search before trying a different source order
CONTROL_ROUTE_BUDGET = 120000  # bounded search, including edits through dense existing feedback wires
CROSSING_COST = 8.0          # prefer a short detour round a control loop to crossing a different signal
ARROW_MARGIN = 0.08          # cm above the checker minimum, allowing scale rounding
PORT_LANE_HALF = 0.04        # cm reserved beside an unused port's arrow approach
PORT_EXIT_LEAD = 0.08        # cm: edits of fixed geometry may have tightly spaced ports
# A five-and-a-half-em line keeps short Chinese phrases together at print size.
PAPER_CAPTION_EM = 5.5
_CAPTION_WORD = re.compile(r"\$[^$]*\$|[\u3400-\u9fff]|[^\s\u3400-\u9fff$]+|\s+")
_CJK = re.compile(r"[\u3400-\u9fff]")
BLOCK_FIELDS = {"id", "type", "text", "label", "label_pos", "signs", "style", "fill", "border", "color", "size_pt", "on", "toggle",
                "point", "port_labels", "select", "icon", "icon_color", "corner_radius", "shadow", "inner_shadow", "border_width", "font_weight"}
STRUCTURE_FIELDS = {"template", "snippets", "name", "title", "blocks", "links", "regions", "texts", "inputs", "outputs", "stateful", "dynamic_links", "remove", "loop", "code", "circuit"}
ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
REF = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z0-9_]+)([+-][0-9]*\.?[0-9]+)?$")


class StructureError(ValueError):
    def __init__(self, code, message):
        self.code, self.message = code, message
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
            connection = {k: deepcopy(v) for k, v in raw["links"][i].items() if k in ("label", "label_pos", "label_gap", "color", "dash", "size_pt", "font_weight")}
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
                        **{k: deepcopy(v) for k, v in region.items() if k in ("label", "fill", "border", "border_color", "label_pos", "corner_radius", "shadow", "inner_shadow", "border_width", "font_weight")}})
    metadata = raw.get("control_ai", {})
    return {"name": raw.get("name", "control_ai"), "title": raw.get("title", ""), "blocks": blocks,
            "texts": [{"index": i, "text": t["text"]} for i, t in enumerate(raw.get("texts", []))],
            "links": links, "inputs": metadata.get("inputs", inputs), "outputs": metadata.get("outputs", outputs),
            "stateful": metadata.get("stateful", []), "dynamic_links": metadata.get("dynamic_links", []), "regions": regions,
            **{key: deepcopy(raw[key]) for key in ("loop", "code", "circuit", "algorithm") if key in raw}}


def _merge_objects(base, patch):
    result = deepcopy(base)
    for key, value in patch.items():
        result[key] = _merge_objects(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else deepcopy(value)
    return result


def expand(proposal, current, profile, directory=None, *, asset_dir=None):
    if not isinstance(proposal, dict) or set(proposal) - STRUCTURE_FIELDS:
        fail("control-schema", f"顶层字段只允许 {sorted(STRUCTURE_FIELDS)}")
    proposal = deepcopy(proposal)
    for section in ("blocks", "links", "regions", "texts"):
        for item in array(proposal.get(section, []), section):
            if isinstance(item, dict):
                for field in ("text", "label"):
                    if isinstance(item.get(field), str):
                        item[field] = item[field].replace("\r\n", "\n").replace("\r", "\n").replace("\n", r"\\")
    base = deepcopy(current) if current else {"kind": "control", "name": "control_ai", "blocks": [], "links": [], "regions": [], "texts": []}
    if "template" in proposal:
        try:
            base = template(proposal["template"], directory, asset_dir=asset_dir)
        except (KeyError, TypeError):
            fail("control-template", f"未知模板 {proposal['template']!r}")
        if current and current.get("algorithm") and not base.get("algorithm"):
            fail("control-template", "请选择算法流程图模板")
    for name in array(proposal.get("snippets", []), "snippets"):
        try:
            part = library.load_snippet(name, directory)
            base, _ = library.insert(base, part, library.below(base, part))
        except (library.LibraryError, KeyError, TypeError):
            fail("control-template", f"未知片段 {name!r}")
    structure = to_structure(base, profile)
    seen_texts = set()
    for patch in proposal.get("texts", []):
        if not isinstance(patch, dict) or set(patch) != {"index", "text"}:
            fail("control-schema", "texts 只接受已有文字的 index 和 text")
        index = patch["index"]
        if type(index) is not int or not 0 <= index < len(structure["texts"]) or index in seen_texts:
            fail("control-schema", "texts.index 必须是当前文字列表中未重复的索引")
        if not isinstance(patch["text"], str):
            fail("control-schema", "texts.text 必须是文字或公式字符串")
        seen_texts.add(index)
        structure["texts"][index]["text"] = patch["text"]
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
                    for field in ("fill", "border", "border_color", "corner_radius", "shadow", "inner_shadow", "border_width", "font_weight"):
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
    for key in ("code", "circuit"):
        if key in proposal:
            if not isinstance(proposal[key], dict):
                fail("control-schema", f"{key} 必须是对象")
            structure[key] = _merge_objects(structure.get(key, {}), proposal[key])
            if key == "circuit":
                for field in ("inputs", "outputs"):
                    if field in proposal[key]:
                        structure[key][field] = deepcopy(proposal[key][field])
    explicit_tf = {b["id"] for b in proposal.get("blocks", []) if "tf" in b}
    patched_tf = proposal.get("loop", {}).get("tf", {})
    for block in structure["blocks"]:
        if block["id"] in patched_tf and block["id"] not in explicit_tf:
            block["tf"] = deepcopy(patched_tf[block["id"]])
        if "tf" in block:
            structure.setdefault("loop", {}).setdefault("tf", {})[block["id"]] = deepcopy(block["tf"])
    # Explicit removals remove their old data references, never arbitrary unknown ids.
    for mapping in (structure.get("loop", {}).get("tf", {}), structure.get("code", {}).get("blocks", {}),
                    base.get("code", {}).get("blocks", {}),
                    base.get("functions", {}), base.get("digital", {}).get("adc", {})):
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
        if not isinstance(link, dict) or set(link) - {"from", "to", "label", "label_pos", "label_gap", "color", "dash", "size_pt", "font_weight"}:
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
            if blocks[bid]["type"] == "switch" and blocks[bid].get("select"):
                allowed.append("sel")
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
        if not isinstance(region, dict) or set(region) - {"id", "members", "label", "fill", "border", "border_color", "label_pos", "inner_shadow", "shadow", "corner_radius", "font_weight", "border_width"}:
            fail("control-coordinates", "分区只接收成员、标签和样式")
        members = array(region.get("members"), "region.members")
        if not members or any(not isinstance(m, str) or m not in blocks for m in members):
            fail("control-block", "分区成员必须是已定义的方块")
    if not s.get("algorithm"):
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


def _new_blocks(structure, base, prof, arrange=False, spacing=1.0, fit_scale=None):
    old = {b["id"]: b for b in base.get("blocks", [])}
    result, fresh = [], []
    for b in structure["blocks"]:
        block = {**deepcopy(old.get(b["id"], {})), **{k: deepcopy(v) for k, v in b.items() if k in BLOCK_FIELDS}}
        if b["id"] not in old or b["type"] != old[b["id"]]["type"]:
            for key in ("at", "size", "pole", "throws", "port_sides"):
                block.pop(key, None)
            fresh.append(block)
        if block["type"] in BOX_TYPES and "size" not in block:
            w, h = text_box_size(block.get("text", ""), font_of(block.get("size_pt"), prof), block.get("font_weight", "normal"))
            block["size"] = [max(0.8, w + 2 * TEXT_PAD + .1), max(.6, h + 2 * TEXT_PAD_Y + .1)]
        if block["type"] == "switch" and "throws" not in block:
            block["throws"] = {name: [0, 0] for name in b["throws"]}
        result.append(block)
    if (not old or arrange) and result:
        from .arrange import place
        fixed_ports = {endpoint(link[k])[0] for link in base.get('links', []) if link.get('open')
                       for k in ('from', 'to') if isinstance(link.get(k), str)}
        place(result, structure, prof, spacing, fixed_ports=fixed_ports)
        # Fonts keep their physical size. Grow boxes only when their text no longer fits.
        for _ in range(ROUTE_PASSES):
            scale = check({'kind': 'control', 'name': 'placement', 'blocks': result, 'links': []}, prof).geometry.s
            if fit_scale is not None:
                scale = min(scale, fit_scale)
            resized = False
            for block in result:
                if block['type'] not in ('block', 'tag') or not block.get('text'):
                    continue
                pt, weight = font_of(block.get('size_pt'), prof), block.get('font_weight', 'normal')
                w, h = text_box_size(block['text'], pt, weight)
                # Reserve an inner strip on either side of the centred title.
                labels = [(block.get('port_sides', {}).get(key[0], key[0]), text_box_size(label, pt, weight))
                          for key, label in block.get('port_labels', {}).items()]
                margin_x = max((size[0] + PORT_LABEL_GAP for side, size in labels if side in 'we'), default=0)
                margin_y = max((size[1] + PORT_LABEL_GAP for side, size in labels if side in 'ns'), default=0)
                required = [(w + 2 * (TEXT_PAD + margin_x)) / scale,
                            (h + 2 * (TEXT_PAD_Y + margin_y)) / scale]
                size = list(block['size'])
                for axis, needed in enumerate(required):
                    if block['id'] not in old or needed > size[axis] + 1e-4:
                        size[axis] = max(size[axis], needed + .1 / scale)
                if any(value > old_size + 1e-4 for value, old_size in zip(size, block['size'])):
                    block['size'], resized = size, True
            if not resized:
                break
            place(result, structure, prof, spacing, fixed_ports=fixed_ports)
        return result
    # Reuse the snippet placement convention for the next row; existing blocks stay put.
    anchor = (0, 0)
    if base.get("blocks") and fresh:
        ys = [b.get("at", b.get("pole"))[1] - b.get("size", [0, 0])[1] / 2 for b in base["blocks"]]
        anchor = (0, min(ys) - ROW_GAP)
    # An output stub also occupies the next block's approach lane.
    width = max([b.get("size", [1, 1])[0] +
                 (BLOCK_GAP if b["id"] in structure["outputs"] and b["type"] != "tag" else 0)
                 for b in fresh] or [1]) + BLOCK_GAP
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


def compile_structure(s, base, profile, *, arrange=False, reroute=False, spacing=1.0, check_cancelled=None):
    """Fit actual printed text after frames and feedback wires set the final page width."""
    fit_scale = None
    for _ in range(ROUTE_PASSES):
        out = _compile_once(s, base, profile, arrange, reroute, spacing, check_cancelled, fit_scale)
        if not arrange and base.get('blocks'):
            break
        report = check(out, get_profile(profile))
        if not any(' needs about ' in error and 'at this scale' in error for error in report.errors):
            break
        # Fixed-point fitting needs a little room when enlarged boxes widen the page again.
        fit_scale = report.geometry.s * .94
    return out


def _paper_variants(structure, prof, compact):
    yield structure
    if not compact:
        return
    wrapped = deepcopy(structure)
    for block in wrapped["blocks"]:
        text = block.get("text")
        if not text or block["type"] not in ("block", "tag"):
            continue
        pt = font_of(block.get("size_pt"), prof)
        max_width = PAPER_CAPTION_EM * pt * PT_CM
        weight = block.get("font_weight", "normal")
        rows = []
        for line in text.split(r"\\"):
            # A math span stays intact. Leave free-form TeX and its braces alone.
            if re.search(r"[{}$\\]", re.sub(r"\$[^$]*\$", "", line)):
                rows.append(line)
                continue
            lines = []
            row = ""
            for word in _CAPTION_WORD.findall(line):
                if row.strip() and word.strip() and text_box_size(row + word, pt, weight)[0] > max_width:
                    lines.append(row.rstrip())
                    row = ""
                row += word
            row = row.rstrip()
            # Do not leave a single Chinese character stranded on its own line.
            if lines and _CJK.fullmatch(row) and len(lines[-1]) >= 3 and _CJK.fullmatch(lines[-1][-1]):
                row = lines[-1][-1] + row
                lines[-1] = lines[-1][:-1]
            rows.extend([*lines, row])
        block["text"] = r"\\".join(rows)
    if wrapped != structure:
        yield wrapped


def _compile_once(s, base, profile, arrange, reroute, spacing, check_cancelled, fit_scale):
    from ..figure_style import paper_profile
    prof = paper_profile(get_profile(profile), base)
    out = deepcopy(base)
    out.update(kind="control", name=s["name"], title=s["title"], profile=prof.name)
    for key in ("code", "circuit"):
        if key in s:
            out[key] = deepcopy(s[key])
    out["blocks"] = _new_blocks(s, base, prof, arrange, spacing, fit_scale)
    for text in s["texts"]:
        out["texts"][text["index"]]["text"] = text["text"]
    if arrange and base.get("blocks"):
        old_by_id = {b["id"]: b for b in base["blocks"]}
        new_by_id = {b["id"]: b for b in out["blocks"]}
        for text in out.get("texts", []):
            at = text.get("at")
            if not at:
                continue
            owner = min(old_by_id, key=lambda bid: (sum((a-b)**2 for a,b in zip(at, old_by_id[bid].get("at", old_by_id[bid].get("pole")))), bid))
            old, new = old_by_id[owner], new_by_id[owner]
            a, b = old.get("at", old.get("pole")), new.get("at", new.get("pole"))
            text["at"] = [at[0]+b[0]-a[0], at[1]+b[1]-a[1]]
    out["control_ai"] = {key: deepcopy(s[key]) for key in ("inputs", "outputs", "stateful", "dynamic_links")}
    if "loop" in s:
        out["loop"] = deepcopy(s["loop"])
    _, native, _, base_outputs = structural_links(base, profile)
    same_geometry = {b["id"] for b in out["blocks"] for old in base["blocks"] if b["id"] == old["id"] and all(b.get(k) == old.get(k) for k in ("type", "at", "size", "pole", "throws", "port_sides"))}
    if reroute or arrange:
        same_geometry = set()
    kept, pending = [], []
    for link in sorted(s["links"], key=lambda link: (link["from"], link["to"])):
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
    if arrange:
        # Open input/output stubs are geometry anchored to their owning block.
        old_blocks = {b["id"]: b for b in base["blocks"]}
        new_blocks = {b["id"]: b for b in out["blocks"]}
        for link in kept:
            if not link.get("open"):
                continue
            ref = next((link[k] for k in ("from", "to") if isinstance(link.get(k), str)), None)
            if ref is None:
                continue
            bid = endpoint(ref)[0]
            old, new = old_blocks[bid], new_blocks[bid]
            a, b = old.get("at", old.get("pole")), new.get("at", new.get("pole"))
            def shift(p):
                return [p[0]+b[0]-a[0], p[1]+b[1]-a[1]]
            for key in ("from", "to"):
                if isinstance(link.get(key), list):
                    link[key] = shift(link[key])
            if "via" in link:
                link["via"] = [shift(p) for p in link["via"]]
    # Keep original order as well as geometry, including interspersed output stubs.
    def route_key(link):
        return json.dumps([link.get(k) for k in ("from", "via", "to")], sort_keys=True)
    order = {route_key(link): i for i, link in enumerate(base["links"])}
    kept.sort(key=lambda link: order.get(route_key(link), len(order)))
    for bid in ([] if s.get("algorithm") else s["outputs"]):
        if bid in base_outputs:
            continue
        if not any(isinstance(l.get("from"), str) and endpoint(l["from"])[0] == bid for l in kept + pending):
            b = next(b for b in out["blocks"] if b["id"] == bid)
            if b["type"] != "tag":
                x, y = b.get("at", b.get("pole")); w = b.get("size", [0, 0])[0]
                kept.append({"from": bid + ".e", "to": [x + w / 2 + BLOCK_GAP, y], "open": True})
    old_regions = to_structure(base, profile)["regions"]
    out["regions"] = []
    out["links"] = kept
    member_geometry = geometry(parse(out), 1.0, prof)
    same_labels = {b["id"] for b in out["blocks"] for old in base["blocks"]
                   if b["id"] == old["id"] and all(b.get(k) == old.get(k) for k in ("label", "label_pos", "size_pt", "font_weight"))}
    for region in s["regions"]:
        if region in old_regions and set(region["members"]) <= same_geometry & same_labels:
            out["regions"].append(deepcopy(base["regions"][old_regions.index(region)]))
        else:
            members = set(region["members"])
            boxes = [box for bid, box in member_geometry.bodies.items() if bid in members]
            boxes += [label.box for label in member_geometry.labels if label.owner.split()[0] in members]
            bounds = (min(b.x0 for b in boxes), min(b.y0 for b in boxes),
                      max(b.x1 for b in boxes), max(b.y1 for b in boxes))
            out["regions"].append({k: deepcopy(v) for k, v in region.items() if k != "id"} | {
                "from": [bounds[0] - REGION_PAD, bounds[1] - REGION_PAD],
                "to": [bounds[2] + REGION_PAD, bounds[3] + REGION_PAD]})
    if (arrange or not base.get('blocks')) and len(out['regions']) > 1:
        from .arrange import fit_nested_frames
        fit_nested_frames(out, prof)
    if pending:
        min_run = AUTO_ROUTE_MIN_RUN if arrange or reroute or not base.get('blocks') else 0.0
        if not base.get('blocks') and check(out, prof).geometry.s < prof.min_scale - EPS:
            fail("control-layout", "方块与标签已经超过当前栏宽，请缩短或分行文字")
        _route_links(out, kept, pending, prof, min_run=min_run, check_cancelled=check_cancelled,
                     compare_orders=bool(min_run))
    if (arrange or not base.get("blocks")) and any(link.get("label") for link in out["links"]):
        from .arrange import place_link_labels
        place_link_labels(out, prof)
    return out


def _route_links(out, kept, pending, prof, *, min_run=0.0, check_cancelled=None, compare_orders=False):
    from .arrange import route_quality
    best, best_score, failure = None, None, None
    from ..modules import ROUTE_CROSSING_COST
    trials = [('sources', ROUTE_CROSSING_COST)]
    if compare_orders:
        # A layout with crossings gets a second chance with crossings forbidden.
        # Try completing the graph in different orders before accepting a detour.
        trials = [('local', CROSSING_COST), ('sources', ROUTE_CROSSING_COST), ('returns', CROSSING_COST),
                  ('local', None), ('returns', None), ('sources', None)]
    for order, crossing_cost in trials:
        out['links'] = deepcopy(kept)
        try:
            _route_order(out, kept, pending, prof, min_run, check_cancelled, order, crossing_cost)
        except StructureError as exc:
            failure = exc
            continue
        if not compare_orders:
            return
        score = route_quality(out, prof, per_link=True)
        if best_score is None or score < best_score:
            best, best_score = out['links'], score
        if best_score[:3] == (0, 0, 0) and (best_score[3] == 0 or order != 'local'):
            break
    if best is None:
        raise failure
    out['links'] = best


def _route_order(out, kept, pending, prof, min_run, check_cancelled, order, crossing_cost):
    from ..modules import _grid_route, _on, _segs, _trim
    # Use the same visibility-grid router as module wiring, with control geometry.
    scale = check(out, prof).geometry.s
    for _ in range(ROUTE_PASSES):
        out["links"] = deepcopy(kept)
        dia = parse(out)
        geom = geometry(dia, scale, prof)
        # Router vertices are rounded to four decimals; box edges must use the
        # same precision so a circle port is not mistaken for an interior point.
        def obstacle(body, padding):
            return tuple(round(v / scale, 4) for v in
                         (body.x0 - padding, body.y0 - padding, body.x1 + padding, body.y1 + padding))
        # Open only the two true port approaches through the block clearance.
        # A small margin absorbs visibility-grid rounding and final page fitting.
        bodies = {bid: obstacle(body, wire_clearance(dia.block(bid), prof) + .01 if min_run else 0)
                  for bid, body in geom.bodies.items()}
        captions = [obstacle(label.box, BLOCK_CLEARANCE / 2) for label in geom.labels
                    if min_run and label.owner.endswith(' label')]
        wires = [segment for path in geom.paths if path for segment in _segs(path)]
        branches = {}
        for native, path in zip(kept, geom.paths):
            if isinstance(native["from"], str) and path:
                branches.setdefault(native["from"], []).extend(_segs(path))
        pins = []
        ports = {}
        reservations = {}
        for link in kept + pending:
            for key in ("from", "to"):
                if isinstance(link.get(key), str):
                    bid, side, offset = endpoint(link[key])
                    pins.append(tuple(round(v, 4) for v in port_point(dia.block(bid), side, offset, scale)))
                    ports[link[key]] = pins[-1]
                    if link in pending and side in SIDE_DIR:
                        point = pins[-1]
                        distance = min_run or (MIN_ARROW_RUN + ARROW_MARGIN if key == 'to' else PORT_EXIT_LEAD)
                        lead = tuple(x + d * distance / scale for x, d in zip(point, SIDE_DIR[dia.block(bid).port_side(side)]))
                        half = PORT_LANE_HALF / scale
                        reservations[link[key]] = (min(point[0], lead[0])-half, min(point[1], lead[1])-half,
                                                   max(point[0], lead[0])+half, max(point[1], lead[1])+half)
        # Route the nearest sink first, so a fan-out grows from its short trunk
        # instead of trapping that sink behind a long detour to another row.
        def distance(link):
            return sum(abs(a-b) for a, b in zip(ports[link['from']], ports[link['to']]))
        # Keep fan-outs together, but route short local connections or long return
        # paths first in alternate trials. Never choose an unchecked crossing count.
        source_length = {}
        for link in pending:
            src = link['from']
            source_length[src] = min(distance(link), source_length.get(src, float('inf')))
        def priority(link):
            lead = source_length[link['from']]
            return (lead if order == 'local' else -lead if order == 'returns' else 0,
                    link['from'], distance(link), link['to'])
        ordered = sorted(pending, key=priority)
        for link in ordered:
            ends = []
            for key in ("from", "to"):
                bid, side, offset = endpoint(link[key])
                block = dia.block(bid)
                point = tuple(round(v, 4) for v in port_point(block, side, offset, scale))
                face = SIDE_DIR.get(block.port_side(side))
                ends.append((point, face, True))
            same = branches.get(link["from"], [])
            port_exits = {}
            route_bodies = dict(bodies)
            for key, (point, face, _) in zip(('from','to'), ends):
                bid = endpoint(link[key])[0]
                if face:
                    port_exits.setdefault(bodies[bid], []).append((point, face))
                else:
                    # Movable switch contacts may enter from either side.
                    route_bodies[bid] = obstacle(geom.bodies[bid], 0)
            route_boxes = [*route_bodies.values(), *captions,
                           *(box for ref, box in reservations.items() if ref not in (link['from'], link['to']))]
            path = _grid_route(ends[0], ends[1], wires, pins, route_boxes, same,
                               clearance=BLOCK_CLEARANCE / scale, min_run=min_run / scale,
                               budget=CONTROL_ROUTE_BUDGET if crossing_cost is not None else STRICT_ROUTE_BUDGET,
                               check_cancelled=check_cancelled, source_wires=same, crossing_cost=crossing_cost, port_exits=port_exits)
            if not min_run and path and len(path) > 1 and sum(abs(x-y) for x,y in zip(path[-2], path[-1])) * scale < MIN_ARROW_RUN + ARROW_MARGIN:
                target, face, _ = ends[1]
                if face:
                    lead = tuple(round(x + d * (MIN_ARROW_RUN + ARROW_MARGIN) / scale, 4) for x, d in zip(target, face))
                    path = _grid_route(ends[0], (lead, face, False), wires, pins, route_boxes, same,
                                       clearance=BLOCK_CLEARANCE / scale, budget=CONTROL_ROUTE_BUDGET,
                                       check_cancelled=check_cancelled, source_wires=same, crossing_cost=crossing_cost, port_exits=port_exits)
                    if path:
                        path.append(target)
                        path = [p for i, p in enumerate(path) if i in (0, len(path)-1) or not _on(p, path[i-1], path[i+1])]
            if path:
                path = _trim(path, same)
                if any(sum(abs(x-y) for x, y in zip(a, b)) * scale < min_run - 1e-4 for a, b in _segs(path)):
                    path = None
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

    def __init__(self, profile="double", current=None, directory=None, description="", base_dir=None,
                 *, selection=(), original=False):
        self.profile, self.current, self.directory = profile, deepcopy(current), directory
        self.description = description
        self.base_dir = base_dir
        self.selection, self.original = list(selection), original
        self.review_questions = []
        self.draft_ids = set()

    def system_prompt(self):
        if self.selection:
            return ("Edit only the named targets. Return JSON {edits:[{target,set:{...},move,distance_cm,reroute}]}. "
                    "All fields after target are optional. set changes only printed text, fill, border, font_weight "
                    "(normal/bold), border_width (pt) or inner_shadow. move is right/left/up/down, distance_cm is "
                    "a positive printed distance (default 0.6). For a selected link use reroute:true. The program "
                    "preserves other objects and reconnects incident wires. Never return a whole drawing or coordinates.")
        if self.current and self.current.get("algorithm"):
            from .algorithm import PROMPT
            return control_system_prompt({}, {}) + "\n" + PROMPT
        # Catalogs contain structural examples, never coordinates or API credentials.
        from ..ai.catalog_select import select_examples
        snippets = {}
        for name in library.all_snippets(self.directory):
            part = library.load_snippet(name, self.directory)
            snippets[name] = {**to_structure(part, self.profile), "interface": part.get("interface", {}),
                              "title": part.get("title", name)}
        snippets = select_examples(snippets, self.description) | {
            k: v for k, v in snippets.items() if k not in library.BUILTIN_SNIPPETS}
        if self.original:
            return (control_system_prompt({}, snippets) +
                    "\nDesign the requested structure from the user's description. Never choose a whole template. "
                    "Reuse a matching local snippet only when it has the required function and ports. "
                    "For a paper about a research method, show the user's new decisions, dependencies and feedback. "
                    "When they ask for a simplified plant or background controller, represent that stage as one "
                    "named block. Keep explicitly requested stages separate. Start with short labels; leave "
                    "implementation details for a local edit. Planner suggestions are alternatives unless the "
                    "user accepts them. Do not turn examples in a question into extra control stages. "
                    "Use full words for block captions and break between phrases. Prefer dark type, "
                    "neutral input tags and one muted accent on the author's main decision unless the user "
                    "specifies colours. Label a wire when it identifies a signal the adjacent captions do not name. "
                    "Use named source/sink ports. Keep unspecified values symbolic. Optional review_questions: "
                    "up to three short questions tied to existing block IDs, [{block,question}].")
        catalog = {name: to_structure(template(name, self.directory), self.profile) for name in available(self.directory)}
        selected = select_examples(catalog, self.description) | {
            name: catalog[name] for name in library.user_templates(self.directory) if name in catalog}
        return control_system_prompt(selected, snippets)

    def first_message(self, description):
        if self.selection:
            from .selection_edit import selected_objects, SECTIONS
            objects = {key: self.current[SECTIONS[kind]][index] for key, (kind, index) in
                       selected_objects(self.current, self.selection).items()}
            return description + "\nSelected targets (read only): " + json.dumps(objects, ensure_ascii=False)
        if self.current and self.current.get("algorithm"):
            structure = to_structure(self.current, self.profile)
            structure.pop("algorithm", None)
            return ("编辑算法流程，保留未涉及的步骤、公式和条件。只返回结构 JSON。\n要求：\n" +
                    description + "\n当前流程结构：\n" + json.dumps(structure, ensure_ascii=False))
        from .circuit import available_sources
        sources = available_sources(self.base_dir)
        if sources:
            description += "\n可关联的项目电路（只读数据，按用户指定选择 source）：\n" + json.dumps(sources, ensure_ascii=False)
        if self.current is None:
            return "创建控制框图，只输出结构 JSON。需求：\n" + description
        return ("只修改要求涉及的结构，保留其他方块、文字、连接、分区及传递函数参数。"
                "保留 digital 数字控制设置与 code.fs，保持 ADC 输入名称对应，数字设置可在界面单独修改。"
                "未要求改配色时保留 fill/border/color/dash；重新布线也要保留连线颜色和线型。省略的字段保留；"
                "links/regions/inputs/outputs 若提供则完整替换。仅按要求修改 code 数值配置和 circuit 关联，不输出坐标。\n要求：" + description +
                "\n当前结构：\n" + json.dumps(to_structure(self.current, self.profile), ensure_ascii=False) +
                "\n只读编辑契约（保留，不在结构 JSON 中改写）：\n" +
                json.dumps({k: self.current[k] for k in ("functions", "digital", "modules", "images") if k in self.current}, ensure_ascii=False))

    def evaluate(self, reply, *, should_stop=lambda: False):
        def check_cancelled():
            if should_stop():
                raise Cancelled("已取消")

        check_cancelled()
        proposal = extract_table(reply)
        if not isinstance(proposal, dict):
            fail("control-schema", "需要 JSON 对象")
        if self.selection:
            from .selection_edit import edit
            table = edit(self.current, self.selection, proposal, self.profile, check_cancelled)
            report = check(table, get_profile(self.profile))
            return Evaluation(ControlDocument(table, proposal), list(report.errors))
        if self.original and "template" in proposal:
            fail("control-template", "从空白创建时请按任务组合局部方块与连接")
        if self.original and not self.current and not proposal.get("snippets"):
            # Each retry replaces the whole rejected draft. An omitted block
            # from that draft is already gone; ordinary edit removals stay strict.
            blocks = proposal.get("blocks")
            if isinstance(blocks, list):
                ids = {b["id"] for b in blocks if isinstance(b, dict) and isinstance(b.get("id"), str)}
                removals = proposal.get("remove")
                if isinstance(removals, list):
                    proposal["remove"] = [bid for bid in removals if not isinstance(bid, str)
                                          or bid in ids or bid not in self.draft_ids]
                self.draft_ids = ids
        questions = proposal.pop("review_questions", [])
        structure, base = expand(proposal, self.current, self.profile, self.directory, asset_dir=self.base_dir)
        validate_structure(structure)
        from .arrange import LAYOUT_SPACING, layout_issues
        table, issues, last_failure = None, [], None
        # Only wholly new candidates may be rearranged automatically. Existing
        # manual geometry remains authoritative for local AI edits.
        prof = get_profile(self.profile)
        fresh = not base.get("blocks")
        if fresh and self.original:
            for block in structure['blocks']:
                if block['type'] == 'tag':
                    block.setdefault('style', 'plain')
        compact = fresh and self.original and not any(key in structure for key in ("loop", "code", "circuit"))
        for variant in _paper_variants(structure, prof, compact):
            for spacing in (LAYOUT_SPACING if fresh else (1.0,)):
                check_cancelled()
                try:
                    candidate = compile_structure(variant, base, self.profile, spacing=spacing, check_cancelled=check_cancelled)
                except StructureError as exc:
                    last_failure = exc
                    continue
                problems = layout_issues(check(candidate, prof), prof, automatic=fresh)
                if table is None or len(problems) < len(issues):
                    table, issues, structure = candidate, problems, variant
                if not issues:
                    break
            if table is not None and not issues:
                break
        if table is None:
            if last_failure:
                raise last_failure
            fail("control-layout", "本地布局尝试失败")
        errors = ["control-layout: " + error + " — " + CONTROL_HINTS["control-layout"] for error in issues]
        if not errors and "circuit" in table:
            from .circuit import resolve
            from ..simulation_export import build_circuit
            from ..layout import Layout
            try:
                circuit, source, _ = resolve(table, self.base_dir)
                build_circuit(Layout.from_dict(circuit), circuit.get("profile") or "ieee-double",
                              base_dir=source.parent, control="continuous")
            except (ValueError, TypeError, KeyError) as exc:
                errors.append("control-schema: " + str(exc))
        ids = {b["id"] for b in table["blocks"]}
        if not isinstance(questions, list) or len(questions) > 3 or any(
                not isinstance(q, dict) or not isinstance(q.get("block"), str) or q["block"] not in ids
                or not isinstance(q.get("question"), str) or not 0 < len(q["question"].strip()) <= 300 for q in questions):
            fail("control-block", "review_questions 须定位到已有方块并给出简短问题，最多三项")
        self.review_questions = [{"block": q["block"], "question": q["question"]} for q in questions]
        return Evaluation(ControlDocument(table, structure), errors)

    def unreadable(self, exc):
        if self.original and isinstance(exc, StructureError) and exc.code == 'control-layout':
            return (f"control-layout: 本轮首稿无法排入纸面，{exc.message}。保留用户指定的环节、判据和反馈。"
                    "把未要求展开的背景环节收成有输入输出的方块，缩短或分行框中文字，完整重写结构 JSON。不要给坐标。")
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
                (["独立文字已更新"] if self.current and document.table.get("texts") != self.current.get("texts") else []) +
                (["传递函数参数已更新，环路分析可直接读取"] if self.current and document.table.get("loop") != self.current.get("loop") else []))
