"""Compose checked vector figures from project sources without copying their geometry."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re

from .figure_style import paper_profile
from .layout import Layout
from .profiles import get_profile

PRESETS = ("vertical", "horizontal", "main_plus_two", "grid_two", "grid_three")
KINDS = ("circuit", "control", "detail", "waveform", "modes")
COMPOSITION_KEYS = {"id", "preset", "gap_mm", "align", "caption_position", "font_pt", "subfigures"}
PANEL_KEYS = {"kind", "source", "caption", "crop_cm", "channels", "window", "modes", "circuit_signature", "waveform"}


def _number(value, low, high, name):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be {low}..{high}")


def validate_compositions(items):
    if not isinstance(items, list):
        raise ValueError("figure.compositions must be a list")
    seen = set()
    for comp in items:
        if not isinstance(comp, dict) or set(comp) - COMPOSITION_KEYS:
            raise ValueError("invalid composition fields")
        identifier = comp.get("id")
        if not isinstance(identifier, str) or not re.fullmatch(r"[\w-]{1,80}", identifier) or identifier in seen:
            raise ValueError("composition id must be unique letters, digits, _ or -")
        seen.add(identifier)
        if comp.get("preset", "vertical") not in PRESETS or comp.get("align", "width") not in ("width", "height"):
            raise ValueError("invalid composition arrangement")
        if comp.get("caption_position", "below") not in ("above", "below"):
            raise ValueError("caption_position must be above or below")
        _number(comp.get("gap_mm", 2), 0, 30, "gap_mm")
        _number(comp.get("font_pt", 8), 5, 24, "font_pt")
        panels = comp.get("subfigures")
        if not isinstance(panels, list) or not 1 <= len(panels) <= 26:
            raise ValueError("a composition needs 1..26 subfigures")
        for panel in panels:
            if isinstance(panel, dict) and "waveform" in panel:
                if panel.get("kind") != "waveform" or not isinstance(panel["waveform"], dict):
                    raise ValueError("waveform settings require a waveform subfigure")
            if not isinstance(panel, dict) or set(panel) - PANEL_KEYS or panel.get("kind") not in KINDS:
                raise ValueError("invalid subfigure fields or kind")
            for key in ("source", "caption", "circuit_signature"):
                if key in panel and (not isinstance(panel[key], str) or len(panel[key]) > 1000):
                    raise ValueError(f"subfigure {key} must be text")
            for key in ("channels", "modes"):
                if key in panel and (not isinstance(panel[key], list) or not all(isinstance(v, str) for v in panel[key])):
                    raise ValueError(f"subfigure {key} must be a name list")
            if "crop_cm" in panel:
                box = panel["crop_cm"]
                if not isinstance(box, list) or len(box) != 4:
                    raise ValueError("crop_cm must be [left, bottom, right, top]")
                for number in box:
                    _number(number, -10000, 10000, "crop_cm")
                if box[0] >= box[2] or box[1] >= box[3]:
                    raise ValueError("crop_cm must have positive width and height")
            if "window" in panel:
                window = panel["window"]
                if not isinstance(window, list) or len(window) != 2:
                    raise ValueError("window must be [start, end] in seconds")
                for number in window:
                    _number(number, -1e9, 1e9, "window")
                if window[0] >= window[1]:
                    raise ValueError("window end must be after start")


def source_table(raw, path, panel):
    source = panel.get("source", "")
    target = Path(path)
    if source:
        root = target.resolve().parent
        target = (root / source).resolve()
        if not target.is_relative_to(root) or target.suffix.lower() != ".json":
            raise ValueError("子图来源需要是项目目录中的 JSON 图纸")
        raw = json.loads(target.read_text(encoding="utf-8"))
    if "waveform" in panel:
        raw = {**raw, "waveform": deepcopy(panel["waveform"])}
    return raw, target


def signature(raw, profile):
    """Keep the established simulation hash and also check actual pin partitions."""
    from .figure_candidates import _nets
    from .waveforms import circuit_signature
    lay = Layout.from_dict(raw)
    nets, _, _ = _nets(raw, profile)
    physical = [circuit_signature(lay), nets, raw.get("closed_loop"), raw.get("electrical")]
    return hashlib.sha256(json.dumps(physical, sort_keys=True).encode()).hexdigest()


def sources(raw, profile, path, comp, *, capture=False):
    validate_compositions([comp])
    comp = deepcopy(comp)
    rows = []
    for index, panel in enumerate(comp["subfigures"]):
        status, note = "current", "跟随当前图纸"
        try:
            data, source = source_table(raw, path, panel)
            is_control = data.get("kind") == "control"
            if panel["kind"] == "control" and not is_control or is_control and panel["kind"] not in ("control", "detail"):
                raise ValueError("子图类型与来源图纸不一致，请重新选择来源或类型")
            if panel["kind"] in ("waveform", "modes"):
                from .auto_modes import source_status
                lay = Layout.from_dict(data)
                current = signature(data, profile)
                provenance = source_status(lay, profile, source.parent)
                stale = (panel.get("circuit_signature", current) != current
                         or provenance["waveform_stale"]
                         or panel["kind"] == "modes" and provenance["stale_modes"])
                if stale:
                    status, note = "stale", "电路或参数已改变，需要重新仿真"
                elif capture:
                    panel["circuit_signature"] = current
                if panel["kind"] == "waveform" and not data.get("waveform"):
                    status, note = "missing", "请先在波形窗口关联仿真结果或 CSV"
                elif panel["kind"] == "waveform":
                    from .waveforms import WaveStore, validate_settings
                    cfg = data["waveform"]
                    validate_settings(lay, cfg, WaveStore(source).read(cfg))
                elif panel["kind"] == "modes" and not data.get("modes"):
                    status, note = "missing", "请先识别或添加模态"
        except (ValueError, OSError, KeyError, TypeError) as exc:
            status, note = "missing", str(exc)
        rows.append({"index": index, "source": panel.get("source") or str(path),
                     "kind": panel["kind"], "status": status, "note": note})
    return comp, rows


def waveform_settings(raw, panel):
    """Apply a panel's view to a copy, shared by publication and annotation editing."""
    cfg = deepcopy(raw.get("waveform", {}))
    if panel.get("channels"):
        available = {channel["column"] for channel in cfg["channels"]}
        missing = set(panel["channels"]) - available
        if missing:
            raise ValueError("波形通道已改变，请重新选择 " + ", ".join(sorted(missing)))
        cfg["channels"] = [channel for channel in cfg["channels"] if channel["column"] in panel["channels"]]
    if panel.get("window"):
        from bisect import bisect_right
        left, right = panel["window"]
        old_marks, old_intervals = cfg["boundaries"], cfg["intervals"]
        marks = [left] + [t for t in old_marks if left < t < right] + [right]
        intervals = []
        for start, end in zip(marks, marks[1:]):
            index = bisect_right(old_marks, (start + end)/2) - 1
            intervals.append(old_intervals[index] if 0 <= index < len(old_intervals) else None)
        cfg.update(window=[left, right], boundaries=marks, intervals=intervals)
    return cfg


def _content(raw, profile, path, panel, mono, *, waveform_width=None):
    from .emit import emit, preamble
    from .validate import check_with_assembly, extent
    from .waveforms import WaveStore, wave_tex, paper, validate_settings, _body, _mode_preamble
    raw, path = source_table(raw, path, panel)
    prof = paper_profile(get_profile(profile), raw)
    kind = panel["kind"]
    if raw.get("kind") == "control" or kind == "control":
        from .control.check import check, extent as control_extent
        from .control.emit import emit as control_emit
        rep = check(raw, prof)
        if not rep.ok:
            raise ValueError("控制子图校验未通过 " + " · ".join(rep.errors))
        box = control_extent(rep.diagram, rep.geometry, prof)
        tex = control_emit(rep, prof, mono=mono, base_dir=path.parent)
    else:
        lay = Layout.from_dict(raw)
        rep, assembly = check_with_assembly(lay, prof)
        if not rep.ok:
            raise ValueError("子图校验未通过 " + " · ".join(i.message for i in rep.errors))
        box = extent(assembly.view if assembly else lay, rep.scale, prof)
        if kind == "waveform":
            cfg = waveform_settings(raw, panel)
            data = WaveStore(path).read(cfg)
            if mono:
                for channel in cfg["channels"]:
                    channel["color"] = "#333333"
                for note in cfg.get("notes", []):
                    note["color"] = "black"
            validate_settings(lay, cfg, data)
            from .geometry import has_wide
            cjk = any(has_wide(item.get("label", item.get("text", "")))
                      for item in cfg["channels"] + cfg.get("notes", []))
            tex = preamble(get_profile(profile), cjk) + wave_tex(data, cfg, profile, [m["name"] for m in lay.modes],
                                                               width_cm=waveform_width, base_dir=path.parent, mono=mono)
            tex += ("\n\\end{CJK}" if cjk else "") + "\n\\end{document}\n"
            dimensions = paper(cfg, profile, target="standalone", names=[m["name"] for m in lay.modes], width_cm=waveform_width)
            return tex, [dimensions["total_width_cm"], dimensions["total_height_cm"]]
        if kind == "modes":
            if box is None:
                raise ValueError("模态子图为空")
            selected = panel.get("modes") or [m["name"] for m in lay.modes]
            from .pipeline import render_modes
            from .modes import modes_of, Style
            mode_map = {mode.name: mode for mode in modes_of(lay)}
            if any(name not in mode_map for name in selected):
                raise ValueError("模态名称已改变，请重新选择子图")
            results = render_modes(lay, profile, modes=[mode_map[name] for name in selected],
                                   sheet=False, base_dir=path.parent, style=Style.mono() if mono else Style())
            if not results:
                raise ValueError("未选择模态")
            # Reuse the existing streamed-vector wrapper for large mode sets.
            tex = _mode_preamble(prof, [r.tex for r in results]).replace(r"\newcommand{\CDmodepicture}", r"\providecommand{\CDmodepicture}") + r"\begin{tabular}{@{}c@{}}"
            tex += r"\\[4pt]".join(_body(result.tex) for result in results)
            tex += "\\end{tabular}\n\\end{document}\n"
            return tex, [box.x1-box.x0, (box.y1-box.y0)*len(results)]
        tex = emit(lay, prof, rep, mono=mono, base_dir=path.parent)
    if box is None:
        raise ValueError("子图为空")
    bounds = panel.get("crop_cm") if kind == "detail" else None
    if bounds:
        sx, sy = (rep.geometry.s, rep.geometry.s) if raw.get("kind") == "control" else (rep.scale.x, rep.scale.y)
        begin, end = (r"\begin{tikzpicture}", r"\end{tikzpicture}") if raw.get("kind") == "control" else (r"\begin{circuitikz}", r"\end{circuitikz}")
        start = tex.index("]", tex.index(begin)) + 1
        rect = f"({bounds[0]/sx:g},{bounds[1]/sy:g}) rectangle ({bounds[2]/sx:g},{bounds[3]/sy:g})"
        tex = tex[:start] + "\n\\begin{scope}\n\\clip " + rect + ";\n" + tex[start:]
        tex = tex.replace(end, "\\end{scope}\n\\pgfresetboundingbox\n\\path[use as bounding box] " + rect + ";\n" + end)
        return tex, [bounds[2]-bounds[0], bounds[3]-bounds[1]]
    return tex, [box.x1-box.x0, box.y1-box.y0]


def render(raw, profile, path, comp, *, mono=False):
    from .compile import _panel_label
    from .waveforms import _body, _mode_preamble
    comp, rows = sources(raw, profile, path, comp)
    if any(row["status"] in ("stale", "missing") for row in rows):
        raise ValueError(" · ".join(row["note"] for row in rows if row["status"] in ("stale", "missing")))
    prof = paper_profile(get_profile(profile), raw)
    panels = [None] * len(comp["subfigures"])
    preset, gap = comp.get("preset", "vertical"), comp.get("gap_mm", 2)/10
    if preset == "vertical":
        groups = [[i] for i in range(len(panels))]
    elif preset == "horizontal":
        groups = [list(range(len(panels)))]
    elif preset in ("grid_two", "grid_three"):
        columns = 2 if preset == "grid_two" else 3
        groups = [list(range(i, min(i+columns, len(panels)))) for i in range(0, len(panels), columns)]
    else:
        groups = [[0]] + [list(range(i, min(i+2, len(panels)))) for i in range(1, len(panels), 2)]
    rows_tex = []
    for group in groups:
        count = max(map(len, groups)) if preset.startswith("grid_") else len(group)
        width = (prof.width_cm - 2*prof.margin_mm/10 - gap*(count-1))/count
        if width <= 0:
            raise ValueError("子图间距超过纸面宽度")
        for index in group:
            panels[index] = _content(raw, profile, path, comp["subfigures"][index], mono, waveform_width=width)
        height = min(width*panels[i][1][1]/panels[i][1][0] for i in group)
        cells = []
        for index in group:
            tex, _ = panels[index]
            scaled = (rf"\resizebox{{!}}{{{height:g}cm}}{{" if comp.get("align") == "height"
                      else rf"\resizebox{{{width:g}cm}}{{!}}{{") + _body(tex) + "}"
            caption = comp["subfigures"][index].get("caption", "")
            # Captions are plain text; circuit/control labels retain their LaTeX semantics.
            caption = "".join({"\\":r"\textbackslash{}", "&":r"\&", "%":r"\%", "$":r"\$",
                               "#":r"\#", "_":r"\_", "{":r"\{", "}":r"\}", "~":r"\textasciitilde{}",
                               "^":r"\textasciicircum{}"}.get(c, c) for c in caption)
            caption = rf"\parbox{{{width:g}cm}}{{\centering ({_panel_label(index)}) {caption}}}"
            contents = [caption, scaled] if comp.get("caption_position") == "above" else [scaled, caption]
            cells.append(rf"\begin{{minipage}}[t]{{{width:g}cm}}\vspace{{0pt}}\centering "
                         + r"\par\vspace{2pt}".join(contents) + r"\end{minipage}")
            rows[index]["scale"] = height/panels[index][1][1] if comp.get("align") == "height" else width/panels[index][1][0]
        rows_tex.append((rf"\hspace{{{gap:g}cm}}").join(cells))
    head = _mode_preamble(prof, [p[0] for p in panels])
    from .geometry import has_wide
    if any(has_wide(p.get("caption", "")) for p in comp["subfigures"]) and r"\usepackage{CJKutf8}" not in head:
        head = head.replace(r"\begin{document}", "\\usepackage{CJKutf8}\n\\begin{document}")
    cjk = r"\usepackage{CJKutf8}" in head
    font = comp.get("font_pt", 8)
    tex = head + ("\\begin{CJK}{UTF8}{gbsn}\n" if cjk else "")
    tex += rf"\fontsize{{{font:g}}}{{{font*1.2:g}}}\selectfont" + "\n\\begin{tabular}{@{}c@{}}\n"
    tex += (rf"\\[{gap:g}cm]" + "\n").join(rows_tex) + "\n\\end{tabular}\n"
    tex += ("\\end{CJK}\n" if cjk else "") + "\\end{document}\n"
    return tex, rows
