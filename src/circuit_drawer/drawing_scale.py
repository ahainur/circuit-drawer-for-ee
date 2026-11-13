"""An explicit paper scale keeps a user's circuit resize from being refitted."""
import math
from copy import deepcopy

from .geometry import FONT_RANGE, SIZE_RANGE, Scale, axis_sizes, font_of
from .layout import DECORATION_SECTIONS, Layout

# Printed centimetres per layout unit. This is a paper conversion, not a
# component size factor; normal fitted profiles use roughly 0.55–1.4.
SCALE_RANGE = (0.05, 10.0)


def read(layout):
    if "drawing_scale" not in layout.extra:
        return None
    value = layout.extra["drawing_scale"]
    if (not isinstance(value, list) or len(value) != 2
            or any(type(v) not in (int, float) or not math.isfinite(v)
                   or not SCALE_RANGE[0] <= v <= SCALE_RANGE[1] for v in value)):
        raise ValueError(f"drawing_scale 须为横／纵两个 cm/布局单位倍率，范围 {SCALE_RANGE[0]:g}–{SCALE_RANGE[1]:g}；自动适配请删除此字段")
    return Scale(*value)


def width_issue(layout, scale, profile):
    from .validate import Issue, extent
    if not (layout.elements or layout.wires or layout.annotations
            or any(layout.extra.get(k) for k in DECORATION_SECTIONS)):
        return None
    box = extent(layout, scale, profile)
    width = box.x1 - box.x0
    if width > profile.width_cm + 1e-6:
        return Issue("drawing-scale-width", f"固定图纸比例后宽 {width:.2f} cm，超过当前栏宽 {profile.width_cm:.2f} cm；请缩小电路、换双栏或恢复自动适配")
    return None


def transform(source, factor, anchor, profile, *, text=False):
    """Scale one flat source drawing; callers choose the selection/instance.

    Parameters, graph identities and waveform samples stay verbatim. Module
    callers transform their local view, retaining the source definition.
    """
    if type(factor) not in (int, float) or not math.isfinite(factor) or factor <= 0:
        raise ValueError("缩放倍率必须为正数")
    if not isinstance(text, bool):
        raise ValueError("同步字号必须为布尔值")
    if (not isinstance(anchor, (list, tuple)) or len(anchor) != 2
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in anchor)):
        raise ValueError("缩放中心须为两个有限坐标")
    if "modules" in source.extra:
        raise ValueError("模块须按实例缩放，不能直接改写展开坐标")
    raw = deepcopy(source.to_dict())

    def point(p):
        return [round(c + (v-c)*factor, 4) for v,c in zip(p,anchor)]

    def bounded(value, bounds, name):
        result = round(value*factor, 8)
        if not bounds[0] <= result <= bounds[1]:
            raise ValueError(f"{name} 缩放后为 {result:g}，允许范围 {bounds[0]:g}–{bounds[1]:g}；请调整倍率")
        return result

    def font(item, key, name):
        if text:
            item[key] = bounded(font_of(item.get(key), profile), FONT_RANGE, name)

    for el, original in zip(raw["elements"], source.elements):
        el["pins"] = {name: point(p) for name,p in el["pins"].items()}
        opts = el.setdefault("opts", {})
        length, width = axis_sizes(original)
        opts["scale_length"] = bounded(length, SIZE_RANGE, f"{el['id']} 长度倍率")
        opts["scale_width"] = bounded(width, SIZE_RANGE, f"{el['id']} 宽度倍率")
        opts.pop("scale", None)
        if "label_offset" in opts:
            opts["label_offset"] = [round(v*factor,4) for v in opts["label_offset"]]
        if "wave" in opts:
            from .schema import OPTS
            field = OPTS["wave_scale"]
            opts["wave_scale"] = bounded(opts.get("wave_scale",field.default), field.range, f"{el['id']} 波形大小")
        font(opts,"label_size",f"{el['id']} 标签字号")
    raw["wires"] = [[point(p) for p in wire] for wire in raw["wires"]]
    raw["hops"] = [point(p) for p in raw["hops"]]
    for annotation in raw["annotations"]:
        annotation["at"] = point(annotation["at"])
        font(annotation,"size","文字字号")
    for section in DECORATION_SECTIONS:
        for item in raw.get(section, []):
            for key in ("at", "from", "to"):
                if key in item:
                    item[key] = point(item[key])
            if "via" in item:
                item["via"] = [point(p) for p in item["via"]]
            if "stack" in item:
                from .regions import STACK_OFFSET
                for axis in ("dx", "dy"):
                    item["stack"][axis] = round(item["stack"].get(axis,STACK_OFFSET)*factor,4)
            if section == "wave_symbols":
                from .source_waves import SCALE_RANGE as WAVE_RANGE
                item["scale"] = bounded(item.get("scale",1),WAVE_RANGE,"波形标记大小")
            elif section != "glyphs":
                font(item,"size","标注字号")
    return Layout.from_dict(raw)
