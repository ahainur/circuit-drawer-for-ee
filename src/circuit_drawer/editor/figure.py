"""Paper measurements and appearance-only editor transactions."""
from __future__ import annotations

import base64
from copy import deepcopy
import math
from pathlib import Path
import tempfile

from ..blocks import safe_blocks
from ..geometry import Scale, font_of, region_rect
from ..glyphs import glyphs
from ..layout import Layout, TWO_TERMINAL
from ..profiles import PROFILES, get_profile
from ..regions import PALETTE, _point, parse_callout, safe_regions
from ..schema import OPTS
from ..validate import check_with_assembly, extent
from ..figure_style import box_style, paper_profile, settings

# Adjustable manuscript advice, separate from the drawing validator's errors.
DEFAULT_ADVICE = {"height_cm": 12.0, "font_pt": 8.0, "line_pt": 0.5,
                  "gray_difference": 0.08}
NEAR_REGION_CM = 1.0
PAPER_BORDER_CM = 2.54 / 72.27  # the emitter's standalone border is 1 TeX pt


def _advice(value):
    if value is None:
        return dict(DEFAULT_ADVICE)
    if not isinstance(value, dict) or set(value) - DEFAULT_ADVICE.keys():
        raise ValueError("投稿建议设置无效")
    out = dict(DEFAULT_ADVICE)
    for key, number in value.items():
        upper = 1 if key == "gray_difference" else 100
        if isinstance(number, bool) or not isinstance(number, (float, int)) or not math.isfinite(number) or not 0 < number <= upper:
            raise ValueError("投稿建议数值超出范围")
        out[key] = float(number)
    return out


def _union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def inspect(lay, profile, report, advice=None):
    """Use the editor's checked geometry, including its compiled module view."""
    prof = paper_profile(get_profile(profile), lay.extra)
    limits = _advice(advice)
    view = Layout.from_dict(report.get("module_info", {}).get("view", lay.to_dict()))
    scale = Scale(*report["scale_cm_per_unit"])
    shapes = []
    from ..figure_images import safe_items
    from ..geometry import image_box
    images = []
    for item in safe_items(lay.extra):
        box = image_box(item, scale)
        images.append({"id": item["id"], "bounds": [box.x0/scale.x, box.y0/scale.y,
                                                    box.x1/scale.x, box.y1/scale.y]})
    for element in lay.elements:
        geometry = report.get("geometry", {}).get(element.id, {})
        boxes = geometry.get("bodies", []) + geometry.get("labels", [])
        if boxes:
            shapes.append({"type": "element", "id": element.id, "bounds": _union(boxes),
                           "labels": geometry.get("labels", [])})
    for identifier, box in report.get("module_info", {}).get("frames", {}).items():
        shapes.append({"type": "module", "id": identifier, "bounds": list(box)})
    for block in safe_blocks(lay):
        shapes.append({"type": "block", "id": block.id,
                       "bounds": [block.x0, block.y0, block.x1, block.y1]})
    for index, glyph in enumerate(glyphs(lay)):
        xs, ys = zip(glyph["from"], glyph["to"])
        shapes.append({"type": "glyph", "id": str(index),
                       "bounds": [min(xs), min(ys), max(xs), max(ys)]})
    root_map = report.get("module_info", {}).get("root_decorations", {}).get("regions")
    for index, region in enumerate(report.get("regions", [])):
        source_index = root_map.get(str(index), root_map.get(index)) if root_map is not None else index
        if region and source_index is not None:
            shapes.append({"type": "region", "id": str(source_index), "bounds": _union(region["rects"])})
    for kind, section in (("annotation","annotations"), ("callout","callouts")):
        root_map = report.get("module_info", {}).get("root_decorations", {}).get(section)
        for i, entry in enumerate(report.get(section, [])):
            index = root_map.get(str(i), root_map.get(i)) if root_map is not None else i
            if entry is None or index is None:
                continue
            if kind == "callout":
                # The selectable arrow exists even when it has no caption.
                if not entry or not entry.get("shape"):
                    continue
                xs, ys = zip(*entry["shape"])
                bounds = [min(xs), min(ys), max(xs), max(ys)]
                if entry.get("label"):
                    bounds = _union([bounds, entry["label"]])
                shapes.append({"type": kind, "id": str(index), "bounds": bounds, "geometry_index": i})
            else:
                shapes.append({"type": kind, "id": str(index), "bounds": entry})
    regions, callouts = safe_regions(view, scale, prof)
    fonts = [font_of(e.opts.get("label_size"), prof) for e in view.elements if e.label]
    fonts += [font_of(a.size, prof) for a in view.annotations if a.text]
    fonts += [font_of(r.size, prof) for r in [*regions, *callouts] if r.label]
    fonts += [font_of(b.size, prof) for b in safe_blocks(view) if b.text]
    bounds = None
    try:
        box = extent(view, scale, prof)
        bounds = [box.x0, box.y0, box.x1, box.y1]
    except (ValueError, KeyError, TypeError):
        pass  # Invalid/empty drawings still have a usable workspace and issues.
    size = [bounds[2] - bounds[0], bounds[3] - bounds[1]] if bounds else None
    pairs = []
    same_colors = []
    gray_checked = 0
    from ..regions import rgb
    for index, first in enumerate(regions):
        if first.fill == "none":
            continue
        a = region_rect(first, scale)
        for second in regions[index + 1:]:
            if second.fill == "none":
                continue
            b = region_rect(second, scale)
            gap = math.hypot(max(a.x0 - b.x1, b.x0 - a.x1, 0), max(a.y0 - b.y1, b.y0 - a.y1, 0))
            if gap >= NEAR_REGION_CM:
                continue
            gray_checked += 1
            if rgb(first.fill) == rgb(second.fill):
                same_colors.append([first.id or str(index + 1), second.id or second.label])
            gray_gap = abs(rgb(first.fill, mono=True)[0] - rgb(second.fill, mono=True)[0]) / 255
            if gray_gap + 1e-9 < limits["gray_difference"]:
                pairs.append({"first": first.id or str(index + 1), "second": second.id or second.label,
                              "difference": gray_gap})
    waveform = lay.extra.get("waveform", {})
    signature = waveform.get("circuit_signature") if isinstance(waveform, dict) else None
    from ..waveforms import circuit_signature
    style_field = OPTS["symbol_style"]
    styles = {e.opts.get("symbol_style", style_field.default)
              for e in view.elements if e.kind in style_field.kinds}
    symbol_style = next(iter(styles)) if len(styles) == 1 else "mixed" if styles else style_field.default
    return {"profile": prof.name, "bounds_cm": bounds, "size_cm": size, "font_pt": min(fonts) if fonts else None,
            "symbol_style": symbol_style,
            "line_pt": prof.line_pt, "advice": limits, "gray_pairs": pairs, "gray_checked": gray_checked,
            "same_colors": same_colors,
            "profiles": {name: {"width_cm": p.width_cm, "font_pt": p.font_pt, "line_pt": p.line_pt}
                         for name, p in PROFILES.items()},
            "palette": {name: {"color": color, "gray": gray} for name, (color, _, gray) in PALETTE.items()},
            "shapes": shapes, "images": images, "scale": [scale.x, scale.y],
            "simulation_matches": circuit_signature(lay) == signature if signature else None}


def preview(lay, profile, mono=False, base_dir=None):
    """Real TeX preview with an explicit paper frame for selection coordinates."""
    from ..emit import emit
    from ..compile import build
    prof = paper_profile(get_profile(profile), lay.extra)
    report, assembly = check_with_assembly(lay, prof)
    if not report.ok:
        raise ValueError("校验未通过，先修正问题栏中的错误")
    box = extent(assembly.view if assembly else lay, report.scale, prof)
    tex = emit(lay, prof, report, mono=mono, base_dir=base_dir)
    # Keep the common renderer. Only its page frame is made explicit so clicks
    # on the paper can use the exact same geometry as the existing editor.
    frame = ("\\pgfresetboundingbox\n\\path[use as bounding box] "
             f"({box.x0 / report.scale.x:.8f},{box.y0 / report.scale.y:.8f}) rectangle "
             f"({box.x1 / report.scale.x:.8f},{box.y1 / report.scale.y:.8f});\n")
    tex = tex.replace(r"\end{circuitikz}", frame + r"\end{circuitikz}")
    with tempfile.TemporaryDirectory(prefix="figure-preview-") as directory:
        files = build(tex, Path(directory), "figure", ("png",), png_dpi=300)
        png = base64.b64encode(files["png"].read_bytes()).decode("ascii")
    border = prof.margin_mm / 10
    return {"png": png, "bounds_cm": [box.x0 - border, box.y0 - border,
                                       box.x1 + border, box.y1 + border]}


def apply(lay, profile, req):
    """Finite local actions cannot write electrical or control settings."""
    raw = deepcopy(lay.to_dict())
    action = req.get("action")
    if action == "paper":
        changes = req.get("settings")
        if not isinstance(changes, dict):
            raise ValueError("纸面设置无效")
        raw.setdefault("figure", {}).update(changes)
        settings(raw)
        return raw
    if action == "symbol":
        from ..blocks import parse_block
        identifier, serial = "symbol", 1
        blocks = raw.setdefault("blocks", [])
        while any(b.get("id") == identifier for b in blocks):
            serial += 1
            identifier = f"symbol{serial}"
        block = {"id": identifier, "from": req.get("from"), "to": req.get("to"),
                 "icon": req.get("icon"), "corner_radius": 0.12}
        parse_block(block, len(blocks))
        blocks.append(block)
        return raw
    if action == "callout":
        arrow = {"from": req.get("from"), "to": req.get("to"), "style": "block"}
        for key in ("from_anchor", "to_anchor"):
            if key in req:
                arrow.update(style="line", color="black")
                arrow[key] = deepcopy(req[key])
        parse_callout(arrow, len(raw.get("callouts", [])), Layout.from_dict(raw))
        raw.setdefault("callouts", []).append(arrow)
        return raw
    ids = req.get("ids", [])
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
        raise ValueError("请先选择对象")
    ids = list(dict.fromkeys(ids))
    elements = {e["id"]: e for e in raw["elements"]}
    instances = {i["id"]: i for i in raw.get("modules", {}).get("instances", [])}
    kind = req.get("kind", "element")
    targets = instances if kind == "module" else elements
    if action in ("move-decoration", "move-region", "move-end"):
        if len(ids) != 1 or kind not in ("annotation", "block", "glyph", "callout", "region"):
            raise ValueError("请先选择对象")
        items = raw.get(kind + "s", [])
        if kind == "block":
            item = next((b for b in items if b["id"] == ids[0]), None)
        else:
            index = int(ids[0]) if ids[0].isascii() and ids[0].isdigit() else -1
            item = items[index] if 0 <= index < len(items) else None
        if item is None:
            raise ValueError("所选对象已改变，请重新选择")
        if action == "move-end":
            end = req.get("end")
            if kind != "callout" or end not in ("from", "to"):
                raise ValueError("请先选择箭头端点")
            item[end] = list(_point(req.get("at"), end))
            if req.get("anchor") is None:
                item.pop(end + "_anchor", None)
            else:
                item[end + "_anchor"] = deepcopy(req["anchor"])
        else:
            if action == "move-region" and kind != "region":
                raise ValueError("请先选择对象")
            if kind == "region" and item.get("fit") == "members":
                raise ValueError("此色块跟随成员位置，可修改配色与名称。")
            delta = _point(req.get("delta"), "delta")

            def moved(point):
                return [v + d for v, d in zip(point, delta)]

            for key in (("at",) if kind == "annotation" else ("from", "to")):
                if key + "_anchor" not in item:
                    item[key] = moved(item[key])
            if kind == "callout" and item.get("via"):
                item["via"] = [moved(p) for p in item["via"]]
        if kind == "callout":
            parse_callout(item, int(ids[0]), lay)
        return raw
    if kind not in ("module", "element", "region") or (kind != "region" and not set(ids) <= targets.keys()):
        raise ValueError("所选对象已改变，请重新选择")
    if action == "display" and kind == "module":
        display = req.get("display")
        if display not in ("expanded", "collapsed", "partial"):
            raise ValueError("未知模块显示方式")
        from .module_clearance import expand
        return expand(lay, {identifier: display for identifier in ids}, profile).to_dict()
    if action == "move-label" and kind == "element" and len(ids) == 1:
        offset = req.get("offset")
        if (not isinstance(offset, list) or len(offset) != 2
                or any(isinstance(n, bool) or not isinstance(n, (int, float))
                       or not math.isfinite(n) for n in offset)):
            raise ValueError("标签偏移必须是两个有限数值")
        elements[ids[0]].setdefault("opts", {})["label_offset"] = offset
        return raw
    if action == "region":
        radius, shadow, *_ = box_style(req)
        appearance = {key: req[key] for key in ("inner_shadow", "border_width", "font_weight") if key in req}
        color = req.get("color", "green")
        border, position = req.get("border", "none"), req.get("label_pos", "below")
        if color not in PALETTE or border not in ("none", "solid", "dashed") or position not in ("below", "above", "inside"):
            raise ValueError("色块选项无效")
        label = req.get("label", "")
        if not isinstance(label, str) or len(label) > 500:
            raise ValueError("区域名称过长")
        label_mode = req.get("label_mode", "module")
        if label_mode not in ("none", "module", "custom"):
            raise ValueError("色块选项无效")
        if label_mode == "none":
            label = ""
        regions = raw.setdefault("regions", [])
        if kind == "region":
            if len(ids) != 1 or not ids[0].isdigit() or not 0 <= int(ids[0]) < len(regions):
                raise ValueError("区域已改变，请重新选择")
            regions[int(ids[0])].update(fill=color, border=border, label=label, label_pos=position,
                                      corner_radius=radius, shadow=shadow, **appearance)
        else:
            groups = [[identifier] for identifier in ids] if kind == "module" and req.get("separate", True) else [ids]
            for group in groups:
                identifier = "figure_region"
                serial = 1
                while any(r.get("id") == identifier for r in regions):
                    serial += 1
                    identifier = f"figure_region_{serial}"
                regions.append({"id": identifier, "fit": "members", "members": group, "fill": color,
                                "border": border,
                                "label": ", ".join(group) if label_mode == "module" and kind == "module" else label,
                                "label_pos": position, "corner_radius": radius, "shadow": shadow, **appearance})
    elif action == "label" and kind == "element":
        if len(ids) != 1:
            raise ValueError("请只选择一个器件")
        text = req.get("text", "")
        position = req.get("position", "auto")
        if not isinstance(text, str) or len(text) > 500 or position not in ("auto", "left", "right", "above", "below"):
            raise ValueError("标签选项无效")
        element = elements[ids[0]]
        element["label"] = text
        if position == "auto":
            element.pop("label_pos", None)
        else:
            element["label_pos"] = position
    elif action == "current" and kind == "element":
        if len(ids) != 1 or elements[ids[0]]["kind"] not in TWO_TERMINAL:
            raise ValueError("请只选择一个两端器件")
        text = req.get("text", "")
        if not isinstance(text, str) or len(text) > 500:
            raise ValueError("电流标签过长")
        options = elements[ids[0]].setdefault("opts", {})
        side = req.get("side", options.get("current_side", "^"))
        if side not in ("^", "_"):
            raise ValueError("电流标签位置无效")
        if text:
            options["current"] = text
            options["current_side"] = side
        else:
            options.pop("current", None)
            options.pop("current_side", None)
    else:
        raise ValueError("此选区不支持这项外观操作")
    return raw
