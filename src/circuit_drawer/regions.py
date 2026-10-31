"""Background regions and callout arrows: the table's ``regions`` and
``callouts`` sections.

A region is a light coloured rectangle drawn behind the circuit, used to
group one module of a multi-module converter ("双有源桥" under a green
block), or a dashed box around a few parts.  ``stack`` repeats it as panels
offset behind the front one, the usual way to show three identical phases.
A callout is an arrow from one point to another, either a broad shaded
block (pointing at a zoomed-in submodule) or a thin coloured line.

Both sections are stored as plain top-level lists (``Layout.extra``) and
never touch connectivity. Explicit corners stay as written; fit=members
derives display bounds from member footprints without changing the table.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .layout import Layout, Point

# Print-friendly tints: light enough that black lines and 8 pt text on top
# stay crisp on paper, distinct enough to tell neighbouring modules apart.
# name -> (fill, edge, grey level of the fill in black-and-white figures).
# The grey levels are spread out by hand so that neighbours stay distinct in
# print even where the colours have nearly the same lightness.
PALETTE: dict[str, tuple[str, str, float]] = {
    "green":  ("#e2efd9", "#5e8c31", 0.93),
    "blue":   ("#dbe8f5", "#2f6fae", 0.86),
    "orange": ("#fce6cf", "#c96a14", 0.90),
    "red":    ("#f7d9d9", "#b03a3a", 0.82),
    "purple": ("#e7def2", "#6f4a9c", 0.84),
    "yellow": ("#fbf3c9", "#a88a12", 0.96),
    "teal":   ("#d6efed", "#23807b", 0.80),
    "gray":   ("#ececec", "#6e6e6e", 0.88),
}
FILLS = tuple(PALETTE) + ("none",)
# Other names models and people write for the same tints; read as the
# palette colour they mean rather than rejected.
COLOR_ALIASES: dict[str, str] = {
    "grey": "gray", "lightgray": "gray", "lightgrey": "gray", "silver": "gray",
    "cyan": "teal", "aqua": "teal", "lightcyan": "teal", "turquoise": "teal",
    "lightblue": "blue", "skyblue": "blue", "lightgreen": "green", "lime": "green",
    "pink": "red", "lightred": "red", "salmon": "red", "violet": "purple",
    "lavender": "purple", "magenta": "purple", "gold": "yellow", "amber": "orange",
    "transparent": "none",
    "绿": "green", "绿色": "green", "浅绿": "green", "蓝": "blue", "蓝色": "blue",
    "浅蓝": "blue", "青": "teal", "青色": "teal", "青绿": "teal", "青绿色": "teal",
    "橙": "orange", "橙色": "orange",
    "红": "red", "红色": "red", "粉": "red", "粉色": "red", "紫": "purple",
    "紫色": "purple", "黄": "yellow", "黄色": "yellow", "灰": "gray", "灰色": "gray",
    "浅灰": "gray", "无": "none", "透明": "none",
}
BORDERS = ("none", "solid", "dashed", "dotted")
REGION_LABEL_POS = ("below", "above", "left", "right", "inside")
CALLOUT_STYLES = ("block", "line", "wave")
CALLOUT_LABEL_POS = ("above", "below", "left", "right", "inline")
# Signal lines of a block diagram are line callouts: dashed for a feedback or
# sampled signal, a head at either end or none, and an isolation mark.
CALLOUT_DASHES = ("solid", "dashed", "dotted")
CALLOUT_HEADS = ("end", "start", "both", "none")
# Printed outline widths, in pt; bounded to keep the outline smaller than the
# block shaft. None keeps the historical profile-dependent width.
CALLOUT_BORDER_WIDTH = (0.1, 3.0)
CALLOUT_APPEARANCE_FIELDS = {
    "border_color": "ink name or #rrggbb or none; omitted: darker fill colour",
    "border_width": f"{CALLOUT_BORDER_WIDTH[0]}..{CALLOUT_BORDER_WIDTH[1]} pt; omitted: profile line width × 0.6",
    "fill_opacity": "0..1; default 1 (opaque fill; outline and label stay opaque)",
    "foreground": "boolean; default false (background); true draws above the circuit",
    "over_parts": "boolean; default false; true explicitly allows this block arrow to cover symbols/text",
}
CALLOUT_KEYS = frozenset({"id", "from", "to", "style", "color", "label", "label_pos", "size",
                         "bend", "wave_amplitude", "wave_cycles", "via", "label_at", "dash",
                         "head", "isolation", "detail"}) | CALLOUT_APPEARANCE_FIELDS.keys()
TRANSPARENCY_NOTE = "块箭头使用了透明填充：PDF 保留透明度；转 EPS 或部分印刷流程会拍平透明度，请核对转换后的遮挡效果。"

STACK_MAX = 6               # more panels than this reads as noise
STACK_OFFSET = 0.25         # layout units each panel steps up and right by default
STACK_SHADE = 0.06          # each panel further back is this much darker
MONO_RANGE = (0.72, 0.97)   # grey of a custom fill in black-and-white figures
MIN_FILL_CHANNEL = 0.8      # custom fills are lightened to at least this in every channel
CUSTOM_EDGE = 0.55          # edge of a custom #rrggbb fill: the same hue, this much darker
MIN_SIDE = 0.1              # layout units: smaller regions are almost surely typos
MIN_CALLOUT = 0.2           # layout units
BLOCK_WIDTH = 0.32          # cm: shaft of a block callout
BLOCK_HEAD = (0.36, 0.62)   # cm: head length and width of a block callout
LINE_HEAD = 0.14            # cm: head length of a line callout
MAX_BEND = 120              # degrees a line callout may leave its chord by
WAVE_AMPLITUDE = 0.06      # cm: small pointer ripple, independent of column scaling
WAVE_CYCLES = 2
MAX_WAVE_CYCLES = 8        # keep a short pointer readable and geometry bounded
CALLOUT_LABEL_GAP = 0.08    # cm between a callout's midpoint and its label
# Isolation mark across a signal line: two short bars with the line cut
# between them, the way papers mark an isolated (optocoupler, isolated ADC)
# feedback path.  Sizes in printed cm, independent of the column scaling.
ISOLATION_GAP = 0.07        # between the two bars
ISOLATION_BAR = 0.28        # length of each bar


class RegionError(ValueError):
    pass


def _hex(c: str) -> tuple[int, int, int] | None:
    c = c.strip()
    if len(c) == 7 and c[0] == "#":
        try:
            return int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)
        except ValueError:
            return None
    return None


def canon(c: Any) -> Any:
    """The palette name for an alias ("cyan", "青", "Grey"); anything else as is."""
    if not isinstance(c, str):
        return c
    k = c.strip().lower().replace(" ", "").replace("_", "").replace("-", "")
    return COLOR_ALIASES.get(k, k if k in PALETTE or k == "none" else c.strip())


def valid_color(c: Any, allow_none: bool = True) -> bool:
    if not isinstance(c, str):
        return False
    c = canon(c)
    return c in PALETTE or (allow_none and c == "none") or _hex(c) is not None


def _num(v: Any) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise RegionError(f"{v!r} is not a number")
    return float(v)


def _point(v: Any, what: str) -> Point:
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        raise RegionError(f"{what} must be [x, y]")
    return (_num(v[0]), _num(v[1]))


@dataclass
class Region:
    x0: float
    y0: float
    x1: float
    y1: float
    fill: str = "gray"
    border: str = "none"
    border_color: str | None = None
    label: str | None = None
    label_pos: str = "below"
    size: float | None = None
    # panels behind the front one: [(dx, dy, fill)], nearest first
    layers: list[tuple[float, float, str]] = field(default_factory=list)
    name: str = "region"
    # element ids that make up this module: the validator keeps them inside,
    # and the model and later exports read the module split from here
    members: list[str] = field(default_factory=list)

    def rect(self, k: int = 0) -> tuple[float, float, float, float]:
        """The front rectangle (k = 0) or the k-th panel behind it."""
        dx, dy = (0.0, 0.0) if k == 0 else self.layers[k - 1][:2]
        return (self.x0 + dx, self.y0 + dy, self.x1 + dx, self.y1 + dy)


@dataclass
class Callout:
    start: Point
    end: Point
    style: str = "block"
    color: str = "gray"
    label: str | None = None
    label_pos: str = "above"
    size: float | None = None
    name: str = "callout"
    bend: float = 0.0
    wave_amplitude: float = WAVE_AMPLITUDE
    wave_cycles: int = WAVE_CYCLES
    via: list[Point] = field(default_factory=list)
    label_at: float = 0.5
    dash: str = "solid"
    head: str = "end"
    isolation: float | None = None      # fraction of the path length
    # does the arrow point at a zoomed detail (a redrawn copy that may not be
    # wired to the circuit)?  None: inferred from where it points; False: it
    # only points, the region it ends in holds the real circuit
    detail: bool | None = None
    border_color: str | None = None
    border_width: float | None = None
    fill_opacity: float = 1.0
    foreground: bool = False
    over_parts: bool = False


def parse_region(d: Any, i: int, layout=None, s=None, prof=None, *, schema_only=False) -> Region:
    name = f"regions[{i}]" + (f" ({d['id']})" if isinstance(d, dict) and d.get("id") else "")
    if not isinstance(d, dict):
        raise RegionError(f"{name} must be an object")
    try:
        if "fit" in d:
            if d["fit"] != "members":
                raise RegionError('fit only accepts "members"')
            members = d.get("members")
            if not isinstance(members, list) or not members or not all(isinstance(m, str) for m in members):
                raise RegionError('fit="members" requires a non-empty members list of element ids')
            if layout is None and not schema_only:
                raise RegionError('fit="members" needs a circuit layout to resolve member geometry')
            if schema_only:
                # Schema-only parsing, before module IDs have been expanded.
                ax, ay, bx, by = 0, 0, 1, 1
            else:
                from .geometry import Scale, region_fit_box
                from .profiles import get_profile
                box = region_fit_box(layout, members, s or Scale(1, 1), prof or get_profile("ieee-double"))
                ax, ay, bx, by = box.x0, box.y0, box.x1, box.y1
        else:
            (ax, ay), (bx, by) = _point(d.get("from"), "from"), _point(d.get("to"), "to")
    except RegionError as exc:
        raise RegionError(f"{name}: {exc}") from None
    x0, x1, y0, y1 = min(ax, bx), max(ax, bx), min(ay, by), max(ay, by)
    if x1 - x0 < MIN_SIDE or y1 - y0 < MIN_SIDE:
        raise RegionError(f"{name}: each side must be at least {MIN_SIDE} units")
    fill = canon(d.get("fill", "gray"))
    if not valid_color(fill):
        raise RegionError(f"{name}: fill must be one of {list(FILLS)} or #rrggbb")
    border = d.get("border", "dashed" if fill == "none" else "none")
    if border not in BORDERS:
        raise RegionError(f"{name}: border must be one of {list(BORDERS)}")
    if fill == "none" and border == "none":
        raise RegionError(f"{name}: with no fill and no border nothing would be drawn")
    bc = canon(d.get("border_color"))
    if bc is not None and not valid_color(bc, allow_none=False):
        raise RegionError(f"{name}: border_color must be a palette name or #rrggbb")
    pos = d.get("label_pos", "below")
    if pos not in REGION_LABEL_POS:
        raise RegionError(f"{name}: label_pos must be one of {list(REGION_LABEL_POS)}")
    label = d.get("label") or None
    if label is not None and not isinstance(label, str):
        raise RegionError(f"{name}: label must be text")
    size = d.get("size")
    if size is not None:
        size = _num(size)
    members = d.get("members") or []
    if not isinstance(members, list) or not all(isinstance(m, str) for m in members):
        raise RegionError(f"{name}: members must list element ids")
    layers = []
    st = d.get("stack")
    if st is not None:
        if not isinstance(st, dict):
            raise RegionError(f"{name}: stack must be an object")
        n = st.get("count", 2)
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= STACK_MAX:
            raise RegionError(f"{name}: stack.count must be a whole number 1-{STACK_MAX}")
        try:
            dx, dy = _num(st.get("dx", STACK_OFFSET)), _num(st.get("dy", STACK_OFFSET))
        except RegionError as exc:
            raise RegionError(f"{name}: stack {exc}") from None
        if math.hypot(dx, dy) < 0.05:
            raise RegionError(f"{name}: stack offset (dx, dy) is too small to see")
        fills = st.get("fills") or []
        if isinstance(fills, list):
            fills = [canon(f) for f in fills]
        if not isinstance(fills, list) or not all(valid_color(f, False) for f in fills):
            raise RegionError(f"{name}: stack.fills must list palette names or #rrggbb")
        base = fill if fill != "none" else "gray"
        for k in range(1, n + 1):
            f = fills[k - 1] if k - 1 < len(fills) else base
            layers.append((dx * k, dy * k, f if k - 1 < len(fills) else f"{base}@{k}"))
    return Region(x0, y0, x1, y1, fill, border, bc, label, pos, size, layers, name,
                  list(members))


def parse_callout(d: Any, i: int) -> Callout:
    name = f"callouts[{i}]" + (f" ({d['id']})" if isinstance(d, dict) and d.get("id") else "")
    if not isinstance(d, dict):
        raise RegionError(f"{name} must be an object")
    if unknown := set(d) - CALLOUT_KEYS:
        raise RegionError(f"{name}: 未知项 {sorted(unknown)}；可用 {sorted(CALLOUT_KEYS)}")
    try:
        a, b = _point(d.get("from"), "from"), _point(d.get("to"), "to")
    except RegionError as exc:
        raise RegionError(f"{name}: {exc}") from None
    if math.dist(a, b) < MIN_CALLOUT:
        raise RegionError(f"{name}: from and to must be at least {MIN_CALLOUT} units apart")
    style = d.get("style", "block")
    if style not in CALLOUT_STYLES:
        raise RegionError(f"{name}: style must be one of {list(CALLOUT_STYLES)}")
    color = (canon if style == "block" else ink_canon)(d.get("color", "gray" if style == "block" else "red"))
    if not (valid_color(color, allow_none=False) if style == "block" else valid_ink(color)):
        raise RegionError(f"{name}: color must be a palette name or #rrggbb")
    pos = d.get("label_pos", "above")
    if pos not in CALLOUT_LABEL_POS:
        raise RegionError(f"{name}: label_pos must be one of {list(CALLOUT_LABEL_POS)}")
    label = d.get("label") or None
    if label is not None and not isinstance(label, str):
        raise RegionError(f"{name}: label must be text")
    size = d.get("size")
    try:
        bend = _num(d.get("bend", 0))
    except RegionError as exc:
        raise RegionError(f"{name}: bend {exc}") from None
    if abs(bend) > MAX_BEND:
        raise RegionError(f"{name}: bend must be between -{MAX_BEND} and {MAX_BEND} degrees")
    if bend and style != "line":
        raise RegionError(f"{name}: only a line callout can bend; set style=line")
    amplitude = _num(d.get("wave_amplitude", WAVE_AMPLITUDE))
    cycles = _num(d.get("wave_cycles", WAVE_CYCLES))
    if amplitude <= 0:
        raise RegionError(f"{name}: wave_amplitude must be positive (cm)")
    if not cycles.is_integer() or not 1 <= cycles <= MAX_WAVE_CYCLES:
        raise RegionError(f"{name}: wave_cycles must be an integer from 1 to {MAX_WAVE_CYCLES}")
    if style != "wave" and ("wave_amplitude" in d or "wave_cycles" in d):
        raise RegionError(f"{name}: wave options require style=wave")
    if style == "wave" and d.get("bend", 0):
        raise RegionError(f"{name}: wave arrows do not take bend; change their endpoints instead")
    via = d.get("via", [])
    if not isinstance(via, list):
        raise RegionError(f"{name}: via must be an array of corner points")
    via = [_point(p, "via point") for p in via]
    if via and (style != "line" or d.get("bend", 0)):
        raise RegionError(f"{name}: via requires a straight line style without bend")
    points = [a, *via, b]
    if any(math.dist(p, q) < 1e-6 for p, q in zip(points, points[1:])):
        raise RegionError(f"{name}: consecutive arrow points must be different")
    label_at = _num(d.get("label_at", 0.5))
    if not 0 < label_at < 1:
        raise RegionError(f"{name}: label_at must be between 0 and 1 (exclusive)")
    if style == "block" and pos == "inline":
        raise RegionError(f"{name}: inline labels are for line or wave arrows")
    dash, head = d.get("dash", "solid"), d.get("head", "end")
    if dash not in CALLOUT_DASHES:
        raise RegionError(f"{name}: dash must be one of {list(CALLOUT_DASHES)}")
    if head not in CALLOUT_HEADS:
        raise RegionError(f"{name}: head must be one of {list(CALLOUT_HEADS)}")
    if (dash != "solid" or head != "end") and style != "line":
        raise RegionError(f"{name}: dash and head are options of a line arrow; set style=line")
    isolation = d.get("isolation")
    if isolation is not None:
        isolation = _num(isolation)
        if not 0 < isolation < 1:
            raise RegionError(f"{name}: isolation must be between 0 and 1 (fraction of the path length)")
        if style != "line" or bend:
            raise RegionError(f"{name}: isolation marks a straight line arrow (via allowed, no bend)")
    detail = d.get("detail")
    if detail is not None and not isinstance(detail, bool):
        raise RegionError(f"{name}: detail must be true or false (false: the arrow only points, "
                          f"it does not lead to a zoomed detail)")
    appearance = {}
    for key in CALLOUT_APPEARANCE_FIELDS:
        if key not in d:
            continue
        if style != "block":
            raise RegionError(f"{name}: {key} 只适用于块箭头（style=block），细箭头／波浪箭头请移除此项")
        value = d[key]
        if key == "border_color":
            value = "none" if canon(value) == "none" else ink_canon(value)
            if value != "none" and not valid_ink(value):
                raise RegionError(f"{name}: border_color 可用 {list(INKS)}、#rrggbb 或 none")
        elif key in ("border_width", "fill_opacity"):
            try:
                value = _num(value)
            except RegionError as exc:
                raise RegionError(f"{name}: {key} 必须是有限数值") from exc
            low, high = CALLOUT_BORDER_WIDTH if key == "border_width" else (0, 1)
            if not low <= value <= high:
                raise RegionError(f"{name}: {key} 可用范围 {low}–{high}" + (" pt" if key == "border_width" else ""))
        elif not isinstance(value, bool):
            raise RegionError(f"{name}: {key} 必须是 true 或 false")
        appearance[key] = value
    return Callout(a, b, style, color, label, pos, _num(size) if size is not None else None, name,
                   bend=bend, wave_amplitude=amplitude, wave_cycles=int(cycles), via=via, label_at=label_at,
                   dash=dash, head=head, isolation=isolation, detail=detail, **appearance)


def _sections(layout: Layout, key: str) -> list:
    v = layout.extra.get(key) or []
    if not isinstance(v, list):
        raise RegionError(f"{key} must be a list")
    return v


def regions_of(layout: Layout, s=None, prof=None) -> list[Region]:
    return [parse_region(d, i, layout, s, prof) for i, d in enumerate(_sections(layout, "regions"))]


def callouts_of(layout: Layout) -> list[Callout]:
    return [parse_callout(d, i) for i, d in enumerate(_sections(layout, "callouts"))]


def problems(layout: Layout, s=None, prof=None, *, schema_only=False) -> list[str]:
    """Schema errors of both sections, one message each."""
    out = []
    for key, parse in (("regions", parse_region), ("callouts", parse_callout)):
        try:
            items = _sections(layout, key)
        except RegionError as exc:
            out.append(str(exc))
            continue
        for i, d in enumerate(items):
            try:
                if key == "regions":
                    parse(d, i, layout, s, prof, schema_only=schema_only)
                else:
                    parse(d, i)
            except RegionError as exc:
                out.append(str(exc))
    return out


def safe_regions(layout: Layout, s=None, prof=None) -> tuple[list[Region], list[Callout]]:
    """Both sections without the entries that fail their schema (those are
    reported by the validator), so extents and previews still work."""
    regs, calls = [], []
    for key, parse, dst in (("regions", parse_region, regs), ("callouts", parse_callout, calls)):
        v = layout.extra.get(key)
        for i, d in enumerate(v if isinstance(v, list) else []):
            try:
                dst.append(parse(d, i, layout, s, prof) if key == "regions" else parse(d, i))
            except RegionError:
                pass
    return regs, calls


# ---- colours -----------------------------------------------------------------
def rgb(color: str, mono: bool = False, edge: bool = False) -> tuple[int, int, int]:
    """Fill (or edge) colour as 0-255 RGB.  ``name@k`` is the palette colour
    k panels back in a stack: a little darker for each step."""
    shade = 0
    if "@" in color:
        color, k = color.split("@")
        shade = int(k)
    if color in PALETTE:
        fill, line, grey = PALETTE[color]
        if mono:
            g = 0.35 if edge else grey
            c = (round(255 * g),) * 3
        else:
            c = _hex(line if edge else fill)
    else:
        c = _hex(color)
        if not edge:
            c = _light(c)
        if edge and not mono:
            c = tuple(round(v * CUSTOM_EDGE) for v in c)
        if mono:
            lum = (0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]) / 255
            g = 0.35 if edge else min(max(lum, MONO_RANGE[0]), MONO_RANGE[1])
            c = (round(255 * g),) * 3
    k = 1 - STACK_SHADE * shade
    return tuple(max(0, round(v * k)) for v in c)


def _light(c: tuple[int, int, int]) -> tuple[int, int, int]:
    """A custom fill mixed with white until every channel is at least
    MIN_FILL_CHANNEL, so black lines on it stay crisp (#00ffff becomes a
    pale cyan; tints already that pale are kept)."""
    lo = min(c) / 255
    if lo >= MIN_FILL_CHANNEL:
        return c
    k = (MIN_FILL_CHANNEL - lo) / (1 - lo)
    return tuple(round(v + (255 - v) * k) for v in c)


def callout_rgb(c: "Callout", mono: bool = False, edge: bool = True) -> tuple[int, int, int]:
    """Colour of a callout's outline (``edge``) or fill: a block callout uses
    the tints above, a line callout an ink."""
    if c.style == "block":
        if edge and c.border_color not in (None, "none"):
            return (0, 0, 0) if mono else ink_rgb(c.border_color)
        return rgb(c.color, mono, edge)
    return (round(255 * 0.35),) * 3 if mono else ink_rgb(c.color)


def transparency_notes(layout: Layout) -> list[str]:
    """A conversion reminder, without changing formats or flattening the PDF."""
    return [TRANSPARENCY_NOTE] if any(c.fill_opacity < 1 for c in safe_regions(layout)[1]) else []


def tikz_rgb(color: str, mono: bool = False, edge: bool = False) -> str:
    r, g, b = rgb(color, mono, edge)
    return f"{{rgb,255:red,{r};green,{g};blue,{b}}}"


def css_rgb(color: str, mono: bool = False, edge: bool = False) -> str:
    return "#%02x%02x%02x" % rgb(color, mono, edge)


# ---- ink: coloured parts and text ---------------------------------------------
# Saturated colours for lines and text drawn on white, as review papers mark
# one device or one current in magenta or blue.  Unlike the fills above they
# are not lightened; in black-and-white figures every ink prints black.
INKS: dict[str, str] = {
    "magenta": "#e5007e",
    "blue":    "#1f4fd8",
    "red":     "#d0231c",
    "green":   "#1f9a3a",
    "orange":  "#e07000",
    "purple":  "#7a3fb2",
    "teal":    "#00838f",
    "gray":    "#707070",
    "black":   "#000000",
}
INK_ALIASES: dict[str, str] = {
    "pink": "magenta", "fuchsia": "magenta", "grey": "gray", "cyan": "teal",
    "violet": "purple", "品红": "magenta", "洋红": "magenta", "粉": "magenta",
    "粉色": "magenta", "粉红": "magenta", "紫红": "magenta", "蓝": "blue", "蓝色": "blue",
    "红": "red", "红色": "red", "绿": "green", "绿色": "green", "橙": "orange",
    "橙色": "orange", "紫": "purple", "紫色": "purple", "青": "teal", "青色": "teal",
    "青绿": "teal", "青绿色": "teal", "灰": "gray", "灰色": "gray", "黑": "black", "黑色": "black",
}


def ink_canon(c: Any) -> Any:
    """The ink name for an alias ("品红", "Pink"); anything else as is."""
    if not isinstance(c, str):
        return c
    k = c.strip().lower().replace(" ", "").replace("_", "").replace("-", "")
    return INK_ALIASES.get(k, k if k in INKS else c.strip())


def valid_ink(c: Any) -> bool:
    c = ink_canon(c)
    return isinstance(c, str) and (c in INKS or _hex(c) is not None)


def ink_rgb(color: str, mono: bool = False) -> tuple[int, int, int]:
    """An ink as 0-255 RGB, black when printing in greys."""
    if mono:
        return (0, 0, 0)
    c = ink_canon(color)
    return _hex(INKS.get(c, c)) or (0, 0, 0)


def ink_tikz(color: str, mono: bool = False) -> str:
    r, g, b = ink_rgb(color, mono)
    return f"{{rgb,255:red,{r};green,{g};blue,{b}}}"


def ink_problems(layout: Layout) -> list[str]:
    """Unknown colours on parts (``opts.color``) and free text (``color``)."""
    out = []
    for el in layout.elements:
        c = el.opts.get("color")
        if c is not None and not valid_ink(c):
            out.append(f"{el.id}: color must be one of {list(INKS)} or #rrggbb, not {c!r}")
    for i, a in enumerate(layout.annotations):
        if a.color is not None and not valid_ink(a.color):
            out.append(f"annotations[{i}] ({a.text!r}): color must be one of "
                       f"{list(INKS)} or #rrggbb, not {a.color!r}")
    return out
