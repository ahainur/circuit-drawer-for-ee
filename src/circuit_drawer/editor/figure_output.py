"""Shared composition and checked publication export for both editors."""
from __future__ import annotations

import base64
from copy import deepcopy
from pathlib import Path
import shutil
import subprocess
import tempfile
import re
import threading

from ..compile import build, FORMATS
from ..figure_style import paper_profile
from ..profiles import get_profile

_PUBLISH_LOCK = threading.Lock()


def _document(raw, profile, path, mono, comp=None):
    if comp:
        from ..figure_composition import render
        return render(raw, profile, path, comp, mono=mono)[0]
    prof = paper_profile(get_profile(profile), raw)
    if raw.get("kind") == "control":
        from ..control.check import check
        from ..control.emit import emit
        report = check(raw, prof)
        if not report.ok:
            raise ValueError(" · ".join(report.errors))
        return emit(report, prof, mono=mono, base_dir=Path(path).parent)
    from ..layout import Layout
    from ..validate import check
    from ..emit import emit
    lay = Layout.from_dict(raw)
    report = check(lay, prof)
    if not report.ok:
        raise ValueError(" · ".join(i.message for i in report.errors))
    return emit(lay, prof, report, mono=mono, base_dir=Path(path).parent)


def embedded_fonts(pdf):
    from ..runtime import POPPLER_OFFLINE_FLAGS, find_tool, poppler_environment
    executable = find_tool("pdffonts")
    if not executable:
        return {"status": "unknown", "note": "未找到 pdffonts，字体嵌入状态待核对", "fonts": []}
    env = poppler_environment(executable)
    flags = POPPLER_OFFLINE_FLAGS if env else ()
    try:
        result = subprocess.run([executable, *flags, pdf.name], cwd=pdf.parent, capture_output=True,
                                env=env, timeout=30,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "unknown", "note": str(exc), "fonts": []}
    if result.returncode:
        return {"status": "unknown", "note": result.stderr.decode(errors="replace")[-300:], "fonts": []}
    lines = result.stdout.decode(errors="replace").splitlines()
    fonts = []
    for line in lines:
        fields = line.split()
        # The portable MiKTeX build can include process logs before and after the table.
        if (len(fields) >= 7 and all(value in ('yes', 'no') for value in fields[-5:-2])
                and all(value.isdecimal() for value in fields[-2:])):
            fonts.append({"name": fields[0], "embedded": fields[-5] == "yes"})
    return {"status": "pass" if all(f["embedded"] for f in fonts) else "warn",
            "note": "PDF 字体嵌入", "fonts": fonts}


def checks(raw, profile, path, advice=None):
    from .figure import _advice
    limits = _advice(advice)
    prof = paper_profile(get_profile(profile), raw)
    rows, fixes = [], []
    if raw.get("kind") == "control":
        from ..control.check import check, extent
        report = check(raw, prof)
        bounds = extent(report.diagram, report.geometry, prof)
        sizes = [bounds.x1-bounds.x0, bounds.y1-bounds.y0] if bounds else None
        warnings = report.warnings
        font = min([prof.font_pt] + [label.pt for label in report.geometry.labels])
        from ..regions import rgb
        from ..geometry import region_rect
        from .figure import NEAR_REGION_CM
        pairs = []
        regions = report.diagram.regions
        for index, first in enumerate(regions):
            for second in regions[index+1:]:
                if first.fill == 'none' or second.fill == 'none':
                    continue
                a, b = region_rect(first, report.geometry.scale), region_rect(second, report.geometry.scale)
                gap = max(a.x0-b.x1, b.x0-a.x1, a.y0-b.y1, b.y0-a.y1, 0)
                if gap <= NEAR_REGION_CM and abs(rgb(first.fill, mono=True)[0]-rgb(second.fill, mono=True)[0])/255 < limits["gray_difference"]:
                    pairs.append((first.name, second.name))
    else:
        from ..layout import Layout
        from .server import analyse
        from .figure import inspect
        lay = Layout.from_dict(raw)
        analysis = analyse(lay, profile, Path(path).parent)
        data = inspect(lay, profile, analysis, limits)
        sizes, font, pairs = data["size_cm"], data["font_pt"], data["gray_pairs"]
        warnings = [i["message"] for i in analysis["issues"] if i["level"] != "note"]
        from ..regions import rgb, PALETTE
        regions = raw.get("regions", [])
        # Offer one reversible edit per named root region. Nested definitions
        # stay in the ordinary appearance editor where all instances are visible.
        for pair in pairs:
            index = next((i for i,r in enumerate(regions) if r.get("id") == pair["second"]), None)
            first = next((r for r in regions if r.get("id") == pair["first"]), None)
            if index is None or first is None:
                continue
            color = max(PALETTE, key=lambda name: abs(rgb(name, mono=True)[0]-rgb(first["fill"], mono=True)[0]))
            if not any(f.get("index") == index for f in fixes):
                fixes.append({"kind": "region", "index": index, "fill": color, "label": "调整色块灰度"})
    if font is not None and font < limits["font_pt"] and prof.font_pt < limits["font_pt"] <= 24:
        fixes.append({"kind":"paper", "settings":{"font_pt":limits["font_pt"]}, "label":"调整正文字号"})
    if prof.line_pt < limits["line_pt"] <= 3:
        fixes.append({"kind":"paper", "settings":{"line_pt":limits["line_pt"]}, "label":"调整线宽"})
    for label, value, target, okay in (
        ("图宽", f"{sizes[0]:.2f} cm" if sizes else "—", f"≤ {prof.width_cm:g} cm", sizes and sizes[0] <= prof.width_cm+.05),
        ("图高", f"{sizes[1]:.2f} cm" if sizes else "—", f"≤ {limits['height_cm']:g} cm", sizes and sizes[1] <= limits["height_cm"]),
        ("正文字号", f"{font:g} pt" if font else "—", f"≥ {limits['font_pt']:g} pt", font and font >= limits["font_pt"]),
        ("线宽", f"{prof.line_pt:g} pt", f"≥ {limits['line_pt']:g} pt", prof.line_pt >= limits["line_pt"]),
        ("相邻色块灰度", str(len(pairs)), "0", not pairs),
        ("图纸检查", " · ".join(str(w) for w in warnings) or "通过", "", not warnings),
    ):
        rows.append({"label":label, "value":value, "target":target, "status":"pass" if okay else "warn"})
    return {"checks":rows, "fixes":fixes}


def _composition_checks(raw, profile, path, comp, panels, pdf, advice=None):
    """Report the scaled panels and actual PDF page, including its margins."""
    from .figure import _advice
    limits = _advice(advice)
    from ..figure_composition import source_table
    from ..runtime import POPPLER_OFFLINE_FLAGS, find_tool, poppler_environment
    smallest_font, smallest_line = comp.get("font_pt", 8), float("inf")
    warnings = []
    for panel, measured in zip(comp["subfigures"], panels):
        source, source_path = source_table(raw, path, panel)
        prof = paper_profile(get_profile(profile), source)
        rows = checks(source, profile, source_path, limits)["checks"]
        font = float(rows[2]["value"].split()[0]) if rows[2]["value"] != '—' else prof.font_pt
        line = prof.line_pt
        if panel["kind"] == "waveform":
            from ..waveforms import paper
            dimensions = paper(source["waveform"], profile, "standalone")
            font, line = dimensions["font_pt"], dimensions["line_pt"]
        smallest_font = min(smallest_font, font*measured["scale"])
        smallest_line = min(smallest_line, line*measured["scale"])
        warnings += [row for row in rows[4:] if row["status"] != "pass"]
    result = []
    executable = find_tool("pdfinfo")
    size = None
    if executable:
        env = poppler_environment(executable)
        flags = POPPLER_OFFLINE_FLAGS if env else ()
        try:
            output = subprocess.run([executable, *flags, pdf.name], cwd=pdf.parent, capture_output=True,
                env=env, timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            match = re.search(rb"Page size:\s+([\d.]+) x ([\d.]+) pts", output.stdout)
            if match:
                size = [float(match[i])*2.54/72 for i in (1, 2)]
        except (OSError, subprocess.TimeoutExpired):
            pass
    for label, value, limit in (("图宽", size[0] if size else None, get_profile(profile).width_cm),
                                ("图高", size[1] if size else None, limits["height_cm"])):
        result.append({"label":label, "value":f"{value:.2f} cm" if value else "待核对", "target":f"≤ {limit:g} cm",
                       "status":"unknown" if value is None else "pass" if value <= limit+.05 else "warn"})
    for label, value, limit in (("缩放后最小字号",smallest_font,limits["font_pt"]), ("缩放后线宽",smallest_line,limits["line_pt"])):
        result.append({"label":label, "value":f"{value:.2f} pt", "target":f"≥ {limit:g} pt", "status":"pass" if value >= limit else "warn"})
    result += warnings
    return {"checks":result, "fixes":[]}


def publish_files(files, destination, before_publish=lambda: None):
    """Stage the whole set, restoring previous exports if replacing a file fails."""
    with _PUBLISH_LOCK:
        return _publish_files(files, destination, before_publish)


def _publish_files(files, destination, before_publish):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for file in files:
        target = destination/file.name
        if target.exists() and not target.is_file():
            raise ValueError(f'Export target is not a file {target}')
    stage = Path(tempfile.mkdtemp(prefix='.figure-publish-', dir=destination))
    backups, installed = [], []
    cleanup = True
    try:
        for file in files:
            shutil.copyfile(file, stage/file.name)
        before_publish()
        for file in files:
            target = destination/file.name
            if target.exists():
                backup = stage/(file.name + '.previous')
                target.replace(backup)
                backups.append((backup, target))
            (stage/file.name).replace(target)
            installed.append(target)
    except Exception:
        try:
            for target in installed:
                target.unlink()
            for backup, target in backups:
                backup.replace(target)
        except OSError as exc:
            cleanup = False
            raise OSError(f'Could not restore all exports. Previous files are retained in {stage}') from exc
        raise
    finally:
        if cleanup:
            shutil.rmtree(stage)
    return [str(destination/file.name) for file in files]


def request(raw, profile, path, output_dir, req, *, before_publish=lambda: None):
    action, comp = req.get("action", "inspect"), req.get("composition")
    if action in ("import-image", "remember-image", "image-library", "icon-preferences", "use-image", "image-preview", "save-image", "remove-image"):
        from .. import figure_images
        root = Path(path).parent
        if action == "import-image":
            return {"asset": figure_images.import_image(req, root)}
        if action == "remember-image":
            return {"asset": figure_images.import_image(req, None)}
        if action == "image-library":
            return {"items": figure_images.library(), "preferences": figure_images.icon_preferences()}
        if action == "icon-preferences":
            return {"preferences": figure_images.icon_preferences(req)}
        if action == "use-image":
            return {"asset": figure_images.use_library(req.get("id"), root)}
        if action == "image-preview":
            return {"png": base64.b64encode(figure_images.read(req.get("source", ""), root)).decode()}
        table = deepcopy(raw)
        images = table.setdefault("images", [])
        identifier = req.get("id")
        index = next((i for i, image in enumerate(images) if image["id"] == identifier), len(images))
        if action == "remove-image":
            if index == len(images):
                raise ValueError("图片已不存在")
            images.pop(index)
        else:
            item = req.get("image")
            figure_images.validate([item])
            if item["id"] != identifier:
                raise ValueError("图片标识已改变")
            for key in ("source", "mono_source"):
                data = figure_images.read(item[key], root)
                if list(figure_images.png_size(data)) != item["pixel_size"]:
                    raise ValueError("图片像素尺寸与来源不符")
            if index == len(images):
                images.append(item)
            else:
                images[index] = item
        figure_images.items(table)
        return {"table": table}
    if action == "waveform-view":
        from ..figure_composition import source_table, validate_compositions, waveform_settings
        from ..layout import Layout
        from ..waveforms import WaveStore, validate_settings
        panel = req.get("panel")
        validate_compositions([{"id": "edit", "subfigures": [panel]}])
        if panel["kind"] != "waveform" or panel.get("source"):
            raise ValueError("请在来源图纸中编辑外部波形标注")
        table, source = source_table(raw, path, panel)
        cfg = waveform_settings(table, panel)
        validate_settings(Layout.from_dict(table), cfg, WaveStore(source).read(cfg))
        return {"config": cfg}
    if action == "style":
        from ..figure_candidates import propose
        candidate = propose(raw, profile, {"changes": req.get("edits", [])})
        if not candidate["passed"]:
            raise ValueError(" · ".join(e["message"] for e in candidate["errors"]))
        return {"table": candidate["preview"]}
    if action in ("sources", "save-composition"):
        from ..figure_composition import sources
        comp, rows = sources(raw, profile, path, comp, capture=action == "save-composition")
        if action == "sources":
            return {"sources":rows}
        if any(row["status"] in ("missing", "stale") for row in rows):
            raise ValueError(" · ".join(row["note"] for row in rows if row["status"] in ("missing", "stale")))
        table = deepcopy(raw)
        compositions = table.setdefault("figure", {}).setdefault("compositions", [])
        index = next((i for i,old in enumerate(compositions) if old["id"] == comp["id"]), len(compositions))
        if index == len(compositions):
            compositions.append(comp)
        else:
            compositions[index] = comp
        return {"table":table, "sources":rows}
    if action == "fix":
        current = checks(raw, profile, path, req.get("advice"))["fixes"]
        fix = req.get("fix")
        if fix not in current:
            raise ValueError("检查结果已改变，请重新检查")
        table = deepcopy(raw)
        if fix["kind"] == "paper":
            table.setdefault("figure", {}).update(fix["settings"])
        else:
            table["regions"][fix["index"]]["fill"] = fix["fill"]
        _document(table, profile, path, False)
        return {"table":table}
    if action not in ("inspect", "preview", "export"):
        raise ValueError("未知出图操作")
    panels = None
    if comp:
        from ..figure_composition import render
        tex, panels = render(raw, profile, path, comp, mono=bool(req.get("mono")))
    else:
        tex = _document(raw, profile, path, bool(req.get("mono")))
    if action == "export":
        formats = req.get("formats", ["pdf", "svg"])
        dpi = req.get("png_dpi", 600)
        if not isinstance(formats, list) or not formats or any(f not in FORMATS for f in formats):
            raise ValueError("请选择 PDF、SVG、PNG 或 TeX")
        if type(dpi) is not int or not 72 <= dpi <= 2400:
            raise ValueError("PNG 分辨率需要是 72..2400 dpi 的整数")
        destination = Path(req.get("out_dir") or output_dir).expanduser()
        if not destination.is_absolute():
            destination = Path(path).parent / destination
        stem = Path(path).stem.removesuffix(".layout") + ("_" + comp["id"] if comp else "")
        with tempfile.TemporaryDirectory(prefix="figure-export-") as directory:
            files = build(tex, Path(directory), stem, tuple(dict.fromkeys(formats)), png_dpi=dpi)
            paths = publish_files(list(files.values()), destination, before_publish)
        return {"files":paths, "out_dir":str(destination)}
    result = checks(raw, profile, path, req.get("advice")) if action == "inspect" else {}
    with tempfile.TemporaryDirectory(prefix="figure-check-") as directory:
        stage = Path(directory)
        files = build(tex, stage, "color", ("pdf", "png"), png_dpi=120)
        result["png"] = base64.b64encode(files["png"].read_bytes()).decode()
        if action == "inspect":
            if comp:
                result.update(_composition_checks(raw, profile, path, comp, panels, files["pdf"], req.get("advice")))
            result["fonts"] = embedded_fonts(files["pdf"])
            mono = build(_document(raw, profile, path, True, comp), stage, "mono", ("png",), png_dpi=120)
            result["mono_png"] = base64.b64encode(mono["png"].read_bytes()).decode()
    return result
