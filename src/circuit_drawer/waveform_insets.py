"""Paper artwork on a time plot. Positions belong to a channel's axis, not its CSV."""
from __future__ import annotations

import base64
from bisect import bisect_left
import math
import os
from pathlib import Path
import re
import tempfile
from xml.etree import ElementTree

from . import figure_images, glyphs
from .geometry import text_size
from .regions import ink_tikz

KINDS = ("legend", "topology", "icon", "image")
DEFAULT_STYLE = {"color": "#9677b1", "tint": 17, "radius_pt": 6.4, "row_cm": 4.8, "font": "system"}
MAX_INSETS = 32
PDF_SOURCE = re.compile(r"figure-assets/([a-f0-9]{64})\.pdf")
SYSTEM_FONTS = "% CD_WAVE_SYSTEM_FONTS"
CJK_FONTS = "% CD_WAVE_CJK"


def number(value, low, high, name):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} 需为 {low:g}～{high:g} 的有限数值")


def vector(value, length, low, high, name):
    if not isinstance(value, list) or len(value) != length:
        raise ValueError(f"{name} 需要 {length} 个数值")
    for v in value:
        number(v, low, high, name)


def validate(cfg, columns=None):
    grid = cfg.get("grid", {})
    if not isinstance(grid, dict) or set(grid) - {"horizontal", "vertical"}:
        raise ValueError("waveform.grid 只接受 horizontal 与 vertical")
    if any(value not in ("none", "solid", "dashed") for value in grid.values()):
        raise ValueError("waveform.grid 的线型需为 none、solid 或 dashed")
    style = cfg.get("plot_style", {})
    if not isinstance(style, dict) or set(style) - DEFAULT_STYLE.keys():
        raise ValueError("waveform.plot_style 含有不支持的绘图区属性")
    if not re.fullmatch(r"#[\da-fA-F]{6}", str(style.get("color", "#ffffff"))):
        raise ValueError("waveform.plot_style.color 需为 #rrggbb")
    for name, low, high in (("tint", 0, 30), ("radius_pt", 0, 12), ("row_cm", 1.55, 12)):
        if name in style:
            number(style[name], low, high, "waveform.plot_style." + name)
    if style.get("font", "system") not in ("system", "journal"):
        raise ValueError("waveform.plot_style.font 需为 system 或 journal")
    items = cfg.get("insets", [])
    if not isinstance(items, list) or len(items) > MAX_INSETS:
        raise ValueError(f"waveform.insets 最多允许 {MAX_INSETS} 个插图")
    ids, legends = set(), set()
    for item in items:
        if not isinstance(item, dict) or item.get("kind") not in KINDS:
            raise ValueError("waveform.insets.kind 需为 legend、topology、icon 或 image")
        kind, identifier, channel = item["kind"], item.get("id"), item.get("channel")
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z][\w-]{0,63}", identifier) or identifier in ids:
            raise ValueError("波形插图 id 需要是唯一的标识")
        ids.add(identifier)
        if not isinstance(channel, str) or not channel or (columns is not None and channel not in columns):
            raise ValueError(f"{identifier}.channel 需要引用 CSV 数值列")
        vector(item.get("at"), 2, 0, 1, identifier + ".at")
        fields = {"id", "kind", "channel", "at", "background", "border", "shadow", "radius_pt"}
        if type(item.get("border", True)) is not bool:
            raise ValueError(f"{identifier} 的插图边框需为布尔值")
        if "radius_pt" in item:
            number(item["radius_pt"], 0, 12, identifier + ".radius_pt")
        if kind == "legend":
            if channel in legends:
                raise ValueError("同一通道只需一个可移动图例")
            legends.add(channel)
        else:
            fields.update(("width_cm", "label", "probe_s"))
            number(item.get("width_cm"), .35, 18, identifier + ".width_cm")
            if not isinstance(item.get("label", ""), str) or len(item.get("label", "")) > 60:
                raise ValueError("插图标题最多 60 字")
            if "probe_s" in item:
                number(item["probe_s"], -1e12, 1e12, identifier + ".probe_s")
        if item.get("background", "transparent") not in ("transparent", "white"):
            raise ValueError("插图底色需为 transparent 或 white")
        if type(item.get("shadow", False)) is not bool:
            raise ValueError("插图投影需为布尔值")
        if kind == "icon":
            fields.add("symbol")
            if not isinstance(item.get("symbol"), str) or item["symbol"] not in glyphs.KINDS:
                raise ValueError("请选择系统图标库中的符号")
        if kind in ("topology", "image"):
            fields.update(("source", "size"))
            vector(item.get("size"), 2, .001, 1e6, identifier + ".size")
            pattern = PDF_SOURCE if kind == "topology" else figure_images.SOURCE
            if not pattern.fullmatch(str(item.get("source", ""))):
                raise ValueError("插图来源必须引用工程内经过校验的素材")
        if kind == "image":
            fields.add("mono_source")
            if not figure_images.SOURCE.fullmatch(str(item.get("mono_source", ""))):
                raise ValueError("个人图片需要对应的黑白素材")
        if kind == "topology":
            fields.update(("mode", "crop", "mono_source"))
            if "mono_source" in item and not PDF_SOURCE.fullmatch(str(item["mono_source"])):
                raise ValueError("黑白拓扑需要工程内的 PDF 快照")
            if "mode" in item and (not isinstance(item["mode"], str) or not 0 < len(item["mode"]) <= 120):
                raise ValueError("请选择已有模态")
            if "crop" in item:
                vector(item["crop"], 4, 0, 1, identifier + ".crop")
                l, t, r, b = item["crop"]
                if r-l < .01 or b-t < .01:
                    raise ValueError("拓扑裁切区宽高均需达到原图的 1%")
        if set(item) - fields:
            raise ValueError(f"{identifier} 含有不支持的波形插图字段")


def style_of(cfg):
    return {**DEFAULT_STYLE, **cfg.get("plot_style", {})} if "plot_style" in cfg or cfg.get("insets") else {}


def grid_settings(cfg, *, xy=False):
    # Existing XY drawings use two solid grids; time plots use horizontal lines only.
    saved = cfg.get("grid", {})
    return {"horizontal": "solid", "vertical": "solid" if xy else "none", **saved,
            "color": "#bcbcbc" if saved else "#dfdfdf", "line_pt": .35 if saved else .25}


def tex_grid(grid, width, height, xs, ys):
    lines = []
    for direction, positions in (("vertical", xs), ("horizontal", ys)):
        style = grid[direction]
        if style == "none":
            continue
        ink = ink_tikz(grid["color"])
        dash = ",dash pattern=on 3pt off 2pt" if style == "dashed" else ""
        for pos in positions:
            a, b = ((pos, 0), (pos, height)) if direction == "vertical" else ((0, pos), (width, pos))
            lines.append(rf"\draw[draw={ink},line width={grid['line_pt']:g}pt{dash}] "
                         rf"({a[0]:.7f},{a[1]:.7f}) -- ({b[0]:.7f},{b[1]:.7f});")
    return lines


def new_style():
    from .runtime import find_tool
    fonts = Path(os.environ.get("WINDIR", "C:/Windows"))/"Fonts"
    system = find_tool("xelatex") and (fonts/"simsun.ttc").is_file() and (fonts/"times.ttf").is_file()
    return {**DEFAULT_STYLE, "font": "system" if system else "journal"}


def tint(style, mono=False):
    channels = [round(255+(int(style["color"][i:i+2], 16)-255)*style["tint"]/100) for i in (1, 3, 5)]
    if mono:
        channels = [round(sum(c*k for c, k in zip(channels, (.299, .587, .114))))]*3
    return "#" + "".join(f"{c:02x}" for c in channels)


def sample_value(data, column, t):
    if not data.time[0] <= t <= data.time[-1]:
        return None
    i = bisect_left(data.time, t)
    values = data.columns[column]
    if data.time[i] == t:
        return values[i]
    return values[i-1]+(values[i]-values[i-1])*(t-data.time[i-1])/(data.time[i]-data.time[i-1])


def objects(data, cfg, paper, channels):
    from .waveforms import PT_CM, UNITS, _text
    rows = {col: row for row in paper["rows"] for col in row["columns"]}
    by_col = {ch["column"]: ch for ch in channels}
    result = []
    width, height, font = paper["width_cm"], paper["row_cm"], paper["font_pt"]
    for item in cfg.get("insets", []):
        column = item["channel"]
        if column not in rows:
            continue
        row, kind = rows[column], item["kind"]
        obj = {"id": item["id"], "channel": column, "kind": kind}
        if kind == "legend":
            # If formerly separate axes are grouped, draw only the first frame on the shared row.
            if any(o["kind"] == "legend" and rows[o["channel"]] is row for o in result):
                continue
            entries = [by_col[c] for c in row["columns"]]
            keys = [{"text": e["label"], "color": e["color"], "line": e.get("line", "solid")} for e in entries]
            line_height = font*1.25*PT_CM
            w = min(width, max(text_size(_text(e["text"]), font)[0] for e in keys)+.78)
            h = min(height, len(keys)*line_height+.16)
            obj.update(keys=keys)
        else:
            sw, sh = item.get("size", (1, 1))
            l, t, r, b = item.get("crop", (0, 0, 1, 1))
            aspect = sw*(r-l)/(sh*(b-t))
            label = item.get("label", "")
            captions = [label] if label else []
            time = item.get("probe_s")
            if time is not None and cfg["window"][0] <= time <= cfg["window"][1]:
                value = sample_value(data, column, time)
                if value is not None:
                    obj["probe"] = [time, value]
                    unit = by_col[column]["unit"]
                    captions.append(f"{value:.4g} {unit} · {time/UNITS[cfg['display_unit']]:.4g} {cfg['display_unit']}")
            footer = len(captions)*font*1.18*PT_CM
            w = min(item["width_cm"], width, max(.06,height-footer-.12)*aspect+.12)
            ah = min(max(.06, height-footer-.12), max(.06, w-.12)/aspect)
            aw = min(max(.06, w-.12), ah*aspect)
            h = min(height, ah+footer+.12)
            obj.update(art=[(w-aw)/2, .06, aw, ah], captions=captions)
            if kind == "icon":
                obj["glyph"] = glyphs.icon_primitives(item["symbol"], (0, 0, 1, 1), colored=True)
        # at is the fraction of available travel, from the upper left of this axis.
        u, v = item["at"]
        obj.update(x=u*(width-w), y=v*(height-h), w=w, h=h)
        result.append(obj)
    return result


def topology(lay, profile, base_dir, mode, font):
    from .compile import build
    from .pipeline import render, render_modes
    from .modes import modes_of, Style
    if mode is not None:
        matches = [m for m in modes_of(lay) if m.name == mode]
        if len(matches) != 1:
            raise ValueError("请选择唯一且存在的模态")
    def drawing(mono):
        if mode is None:
            tex = render(lay, profile, base_dir=base_dir, mono=mono).tex
        else:
            tex = render_modes(lay, profile, modes=matches, sheet=False, base_dir=base_dir,
                               style=Style.mono() if mono else Style())[0].tex
        return tex.replace(r"\end{document}", SYSTEM_FONTS + "\n" + r"\end{document}", 1) if font == "system" else tex
    folder = Path(base_dir)/"figure-assets"
    with tempfile.TemporaryDirectory() as tmp:
        tex = drawing(False)
        files = build(tex, Path(tmp), "topology", ("svg", "pdf"))
        svg, pdf = files["svg"].read_bytes(), files["pdf"].read_bytes()
        mono = drawing(True)
        mono_pdf = build(mono, Path(tmp), "mono", ("pdf",))["pdf"].read_bytes() if mono != tex else pdf
    dims = [float(x) for x in ElementTree.fromstring(svg).attrib["viewBox"].split()]
    source = figure_images.write_asset(pdf, folder, ".pdf")
    mono_source = figure_images.write_asset(mono_pdf, folder, ".pdf") if mono_pdf != pdf else source
    return {"source": source, "mono_source": mono_source, "size": dims[2:], "svg": base64.b64encode(svg).decode()}


def preview_asset(source, base_dir):
    from .compile import convert_pdf
    if not PDF_SOURCE.fullmatch(str(source)):
        raise ValueError("请选择已保存的拓扑快照")
    pdf = figure_images.read_asset(source, base_dir, PDF_SOURCE)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)/"snapshot.pdf"
        path.write_bytes(pdf)
        convert_pdf(path, path.with_suffix(".svg"))
        return {"svg": base64.b64encode(path.with_suffix(".svg").read_bytes()).decode()}


LINE_STYLES = {"solid": "solid", "dashed": "dashed", "dashdot": "dash dot", "dotted": "dotted"}


def tex_row(items, cfg, row, paper, data_axis, base_dir, mono):
    from .waveforms import _text, PT_CM
    by_id = {i["id"]: i for i in cfg.get("insets", [])}
    row_height = paper["row_cm"]
    output = []
    for obj in items:
        if obj["channel"] not in row["columns"]:
            continue
        item = by_id[obj["id"]]
        x, top, w, h = obj["x"], row_height-obj["y"], obj["w"], obj["h"]
        if "probe" in obj:
            t, value = obj["probe"]
            t0, t1 = cfg["window"]
            lo, hi = data_axis["range"]
            px, py = (t-t0)/(t1-t0)*paper["width_cm"], (value-lo)/(hi-lo)*row_height
            tx, ty = max(x, min(x+w, px)), max(top-h, min(top, py))
            output.append(rf"\draw[gray,line width=.4pt] ({px:g},{py:g}) -- ({tx:g},{ty:g});")
            output.append(rf"\draw[fill=white] ({px:g},{py:g}) circle[radius=.045cm];")
        radius = min(item.get("radius_pt", paper["plot_style"]["radius_pt"]), w/2/PT_CM, h/2/PT_CM)
        rounded = rf"rounded corners={radius:g}pt"
        white = item.get("background", "white" if item["kind"] == "legend" else "transparent") == "white"
        border = item.get("border", True)
        if border and item.get("shadow", item["kind"] == "legend"):
            if not white:
                output.extend((rf"\begin{{scope}}[even odd rule,{rounded}]",
                               rf"\clip ({x-.1:g},{top+.1:g}) rectangle ({x+w+.1:g},{top-h-.1:g}) "
                               rf"({x:g},{top:g}) rectangle ({x+w:g},{top-h:g});"))
            output.append(rf"\fill[black!28,{rounded}] ({x+.035:g},{top-.035:g}) rectangle ({x+w+.035:g},{top-h-.035:g});")
            if not white:
                output.append(r"\end{scope}")
        bounds = rf"({x:g},{top:g}) rectangle ({x+w:g},{top-h:g});"
        if border:
            fill = ",fill=white" if white else ""
            output.append(rf"\draw[gray,line width=.4pt,{rounded}{fill}] {bounds}")
        elif white:
            output.append(rf"\fill[white,{rounded}] {bounds}")
        def text_node(txt, anchor, nx, ny, limit):
            fitted = rf"\begingroup\setbox0=\hbox{{{_text(txt)}}}\ifdim\wd0>{limit:g}cm\resizebox{{{limit:g}cm}}{{!}}{{\box0}}\else\box0\fi\endgroup"
            return rf"\node[inner sep=0pt,anchor={anchor}] at ({nx:g},{ny:g}) {{{fitted}}};"
        if item["kind"] == "legend":
            for i, entry in enumerate(obj["keys"]):
                y = top-(i+.5)*h/len(obj["keys"])
                color = ink_tikz(entry["color"])
                output.append(rf"\draw[draw={color},{LINE_STYLES[entry['line']]}] ({x+.08:g},{y:g}) -- ({x+.39:g},{y:g});")
                output.append(text_node(entry["text"], "west", x+.48, y, max(.05, w-.55)))
            continue
        ax, ay, aw, ah = obj["art"]
        ax, ay = x+ax, top-ay-ah
        if item["kind"] == "icon":
            output.append(glyphs.tikz({"kind":item["symbol"], "from":[ax,ay], "to":[ax+aw,ay+ah]}, colored=True, mono=mono))
        else:
            source = item.get("mono_source", item["source"]) if mono else item["source"]
            if item["kind"] == "image":
                content = figure_images.read(source, base_dir)
            else:
                content = figure_images.read_asset(source, base_dir, PDF_SOURCE)
            filename = "cd_image_" + Path(source).name
            output.append(figure_images.pack_tex(filename, content))
            trim = ""
            if "crop" in item:
                l, t, r, b = item["crop"]
                sw, sh = item["size"]
                trim = f"trim={l*sw:g}bp {(1-b)*sh:g}bp {(1-r)*sw:g}bp {t*sh:g}bp,clip,"
            output.append(rf"\node[anchor=south west,inner sep=0pt,outer sep=0pt] at ({ax:g},{ay:g}) "
                          rf"{{\includegraphics[{trim}width={aw:g}cm,height={ah:g}cm]{{{filename}}}}};")
        for i, cap in enumerate(obj["captions"]):
            ny = ay-(i+.5)*paper["font_pt"]*1.18*PT_CM
            output.append(text_node(cap, "center", x+w/2, ny, max(.05,w-.09)))
    return output


def print_fonts(tex):
    """Select system fonts for styled figures. Journal figures retain the portable pdfTeX route."""
    if SYSTEM_FONTS in tex:
        tex = tex.replace(r"\usepackage{CJKutf8}", "").replace(r"\begin{CJK}{UTF8}{gbsn}", "").replace(r"\end{CJK}", "")
        setup = (r"\usepackage{mathspec}" "\n" r"\setmainfont{Times New Roman}" "\n"
                 r"\setmathsfont(Digits,Latin){Times New Roman}" "\n" r"\setmathrm{Times New Roman}" "\n"
                 r"\usepackage{xeCJK}" "\n" r"\setCJKmainfont{SimSun}" "\n")
        return "% !TEX program = xelatex\n" + tex.replace(r"\begin{document}", setup + r"\begin{document}", 1)
    if CJK_FONTS in tex:
        if r"\usepackage{CJKutf8}" not in tex:
            tex = tex.replace(r"\begin{document}", "\\usepackage{CJKutf8}\n\\begin{document}", 1)
        if not tex.partition(r"\begin{document}")[2].lstrip().startswith(r"\begin{CJK}"):
            tex = tex.replace(r"\begin{document}", "\\begin{document}\n\\begin{CJK}{UTF8}{gbsn}", 1)
            tex = tex.replace(r"\end{document}", "\\end{CJK}\n\\end{document}")
    return tex
