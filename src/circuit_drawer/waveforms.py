"""Measured/simulated CSV -> manually bound time intervals -> paper figures.

CSV samples are immutable inputs. The coordinate table owns all display and
binding settings. No operating state is inferred from current or voltage.
"""
from __future__ import annotations

from array import array
from bisect import bisect_left, bisect_right
from copy import deepcopy
import csv
from dataclasses import dataclass
import hashlib
import io
import json
import math
from pathlib import Path
import re
import shutil
import tempfile

from .compile import build
from .emit import _callout, _n, preamble
from .geometry import Scale, has_wide, text_size
from .layout import Layout
from .modes import modes_of
from .pipeline import LayoutRejected, render_modes
from .profiles import get_profile
from .regions import INKS, LINE_HEAD, Callout, ink_rgb, ink_tikz, valid_ink
from .validate import ARROW_TEXT, check

MAX_BYTES = 64 * 1024 * 1024
MAX_ROWS = 1_000_000
UNITS = {"s": 1.0, "ms": 1e-3, "us": 1e-6}
COLORS = ("#c8102e", "#2457d6", "#17845a", "#8753a0", "#b66b0a", "#237c89")
# "shade": alternate grey bands per interval, "Mode k" above the top channel.
# "lines": dashed boundaries and t0, t1, ... only (the original figure).
SLICE_STYLES = ("shade", "lines")
# How the combined figure places the chart and the mode diagrams:
# stack = all in one column, side = chart left / modes right (double column),
# grid = chart on top / modes below, two per row on a double column.
ARRANGEMENTS = ("stack", "side", "grid")
# Mode tags: below this interval width (cm) print "k" instead of "Mode k",
# below MIN_TAG_CM print nothing. 8 pt "Mode 3" is about 1.0 cm wide.
FULL_TAG_CM, MIN_TAG_CM = 1.25, .3
# Gap between the stacked mode diagrams of the side/grid layouts (pt).
MODE_GAP_PT = 8
# Printed chart rows (cm). The channels share one time axis, so only the
# bottom row carries tick numbers and the rows sit this close together.
ROW_CM, ROW_GAP_CM = 1.55, .3
# About this many labelled values on a value axis, at a 1/2/5 x 10^n step.
Y_TICKS = 3
# Space kept between neighbouring t0, t1, ... labels when they crowd (cm).
LABEL_GAP_CM = .05
# Manual notes (waveform.notes): text, an arrow, or a small open circle on a
# key point. Coordinates are [time in s, channel value], so a note stays on
# its sample when the window or the column width changes.
NOTE_KINDS = ("text", "arrow", "dot")
MAX_NOTES = 40
# Dragged label offsets (notes' "offset", waveform.label_offsets) in printed
# cm; further than this is off the figure.
MAX_OFFSET_CM = 3.0
DOT_CM = .06          # radius of a key-point circle, about the size of 8 pt "o"
# Note arrows are thin (IEEE line art), not the 1.6x circuit callout stroke.
NOTE_WEIGHT = .8
DOT_LABEL_OFFSET = (.08, .08)   # default label of a key point: up and right
# Physical sizes shared by the SVG editor and TeX. TeX points are 1/72.27 in.
PT_CM = 2.54 / 72.27
CHART_LEFT_CM, CHART_RIGHT_CM = 1.25, .3
CHART_TOP_CM, CHART_BOTTOM_CM = .65, .75
NOTE_LIMITS = {"font_pt": (5, 20), "line_pt": (.1, 2), "radius_cm": (.02, .3)}
NUDGE_CM = .01  # ordinary arrow key; Shift moves ten times as far


@dataclass
class Data:
    columns: dict[str, array]
    time: array


def parse_csv(raw: bytes, time_column: str | None = None, time_unit="s") -> Data:
    if len(raw) > MAX_BYTES:
        raise ValueError("CSV 最大支持 64 MiB")
    if time_unit not in UNITS:
        raise ValueError("时间单位请选择 s、ms 或 us")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("CSV 请保存为 UTF-8 编码") from exc
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    headers = [h.strip() for h in next(reader, [])]
    if not 2 <= len(headers) <= 32 or any(not h or len(h) > 120 for h in headers):
        raise ValueError("CSV 第一行需为 2～32 个非空列名（每列名最多 120 字）")
    if len(set(headers)) != len(headers):
        raise ValueError("CSV 列名不能重复")
    time_column = time_column or headers[0]
    if time_column not in headers:
        raise ValueError(f"CSV 没有时间列：{time_column}")
    columns = {h: array("d") for h in headers}
    count = 0
    for line, row in enumerate(reader, 2):
        if not row or all(not v.strip() for v in row):
            continue
        if len(row) != len(headers):
            raise ValueError(f"CSV 第 {line} 行列数不一致")
        try:
            values = [float(v) for v in row]
        except ValueError as exc:
            raise ValueError(f"CSV 第 {line} 行包含非数值；首行之后只能是数值") from exc
        if not all(math.isfinite(v) for v in values):
            raise ValueError(f"CSV 第 {line} 行包含 NaN 或无穷大")
        for key, value in zip(headers, values):
            columns[key].append(value)
        count += 1
        if count > MAX_ROWS:
            raise ValueError("CSV 最大支持 100 万行")
    if count < 2:
        raise ValueError("CSV 至少需要两行数值")
    time = array("d", (t * UNITS[time_unit] for t in columns[time_column]))
    if any(not math.isfinite(t) for t in time) or any(b <= a for a, b in zip(time, time[1:])):
        raise ValueError("时间列必须有限且严格递增，不能有重复时间")
    return Data(columns, time)


def circuit_signature(lay: Layout) -> str:
    # Geometry/typography edits do not invalidate simulation data.
    d = lay.to_dict()
    physical = {"parts": sorted((e.id, e.kind, sorted(e.pins), e.opts.get("diode", True)) for e in lay.elements),
                "nets": sorted(sorted(p for p, n in lay.netlist.items() if n == net)
                               for net in set(lay.netlist.values())),
                "simulation": d.get("simulation", {})}
    return hashlib.sha256(json.dumps(physical, sort_keys=True).encode()).hexdigest()


def _number(value) -> float:
    if isinstance(value, bool):
        raise ValueError("时间必须是有限数值")
    try:
        value = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError("时间必须是有限数值") from exc
    if not math.isfinite(value):
        raise ValueError("时间必须是有限数值")
    return value


def validate_settings(lay: Layout, cfg: dict, data: Data, *, bound=False) -> dict:
    if not isinstance(cfg, dict):
        raise ValueError("waveform 需为设置对象")
    if cfg.get("version") != 1:
        raise ValueError("不支持的 waveform 版本")
    if cfg.get("display_unit") not in UNITS:
        raise ValueError("显示时间单位请选择 s、ms 或 us")
    if cfg.get("slice_style", "shade") not in SLICE_STYLES:
        raise ValueError(f"waveform.slice_style 只能是 {' / '.join(SLICE_STYLES)}："
                         f"{cfg.get('slice_style')!r}")
    if cfg.get("arrange", "stack") not in ARRANGEMENTS:
        raise ValueError(f"waveform.arrange 只能是 {' / '.join(ARRANGEMENTS)}："
                         f"{cfg.get('arrange')!r}")
    window = [_number(t) for t in cfg.get("window", [])]
    if len(window) != 2 or not data.time[0] <= window[0] < window[1] <= data.time[-1]:
        raise ValueError("显示范围必须位于 CSV 时间范围内，且起点小于终点")
    marks = [_number(t) for t in cfg.get("boundaries", [])]
    if not 2 <= len(marks) <= 25 or marks[0] != window[0] or marks[-1] != window[1]:
        raise ValueError("需要 2～25 条分界线，首尾必须与显示范围一致")
    if any(b <= a for a, b in zip(marks, marks[1:])):
        raise ValueError("时间分界线必须严格递增，不能重合")
    bindings = cfg.get("intervals", [])
    if not isinstance(bindings, list) or len(bindings) != len(marks) - 1:
        raise ValueError("每个时间区间都需对应一个模态选项")
    names = [m.get("name") for m in lay.modes]
    for name in bindings:
        if not bound and (name is None or isinstance(name, str)):
            continue
        if not isinstance(name, str) or names.count(name) != 1:
            raise ValueError(f"请为每个区间选择唯一且仍然存在的模态：{name or '尚未选择'}")
    channels = cfg.get("channels", [])
    if not isinstance(channels, list) or not 1 <= len(channels) <= 6:
        raise ValueError("请选择 1～6 个波形通道")
    seen = set()
    for c in channels:
        if not isinstance(c, dict):
            raise ValueError("每个通道需为设置对象")
        column = c.get("column")
        if column not in data.columns or column == cfg.get("time_column") or column in seen:
            raise ValueError(f"波形通道不存在、重复或等于时间列：{column}")
        seen.add(column)
        for key in ("label", "unit"):
            if not isinstance(c.get(key), str) or len(c[key]) > 60:
                raise ValueError("通道标签和单位需为最多 60 字的文字")
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", c.get("color", "")):
            raise ValueError("波形颜色需为 #rrggbb")
    # A note may sit on a hidden channel: it is kept, just not drawn.
    _check_notes(cfg, set(data.columns) - {cfg.get("time_column")})
    return cfg


def _pair(value, what: str) -> list[float]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{what} 需为两个数 [x, y]")
    return [_number(v) for v in value]


def _offset(value, what: str) -> list[float]:
    dx, dy = _pair(value, what)
    if max(abs(dx), abs(dy)) > MAX_OFFSET_CM:
        raise ValueError(f"{what} 偏移超过 {MAX_OFFSET_CM:g} cm，已拖出图外")
    return [dx, dy]


def _check_notes(cfg: dict, channels: set) -> None:
    offsets = cfg.get("label_offsets", {})
    if not isinstance(offsets, dict):
        raise ValueError("waveform.label_offsets 需为对象，如 {\"t1\": [0.2, 0]}")
    for key, value in offsets.items():
        if not re.fullmatch(r"(t|mode)\d{1,2}", key):
            raise ValueError(f"waveform.label_offsets 的键只能是 t0、t1… 或 mode0、mode1…：{key!r}")
        _offset(value, f"waveform.label_offsets.{key}")
    notes = cfg.get("notes", [])
    if not isinstance(notes, list) or len(notes) > MAX_NOTES:
        raise ValueError(f"waveform.notes 需为列表，最多 {MAX_NOTES} 条")
    for i, note in enumerate(notes):
        name = f"waveform.notes[{i}]"
        if not isinstance(note, dict):
            raise ValueError(f"{name} 需为对象")
        kind = note.get("kind")
        if kind not in NOTE_KINDS:
            raise ValueError(f"{name}.kind 只能是 {' / '.join(NOTE_KINDS)}：{kind!r}")
        if note.get("channel") not in channels:
            raise ValueError(f"{name}.channel 需为 CSV 中的通道列名：{note.get('channel')!r}")
        text = note.get("text", "")
        if not isinstance(text, str) or len(text) > 120:
            raise ValueError(f"{name}.text 需为最多 120 字的文字")
        if kind == "text" and not text.strip():
            raise ValueError(f"{name} 是文字标注，text 不能为空")
        if has_wide(text):
            raise ValueError(f"{name}.text 请用英文或数学记号（如 $i_L$ peak），论文图不放中文")
        if ARROW_TEXT.fullmatch(text.strip()):
            raise ValueError(f"{name}.text 把箭头写成了文字；请改用 kind: arrow，填 from 和 to")
        if text.count("$") % 2:
            raise ValueError(f"{name}.text 的 $ 不成对")
        if kind == "arrow":
            if "at" in note:
                raise ValueError(f"{name} 是箭头，用 from（箭尾）和 to（箭头尖），不用 at")
            a, b = _pair(note.get("from"), f"{name}.from"), _pair(note.get("to"), f"{name}.to")
            if a == b:
                raise ValueError(f"{name} 的 from 与 to 不能相同")
        else:
            _pair(note.get("at"), f"{name}.at")
        if not valid_ink(note.get("color", "black")):
            raise ValueError(f"{name}.color 需为 black、gray、red、blue 等颜色名或 #rrggbb")
        if "offset" in note:
            _offset(note["offset"], f"{name}.offset")
        allowed = {"kind", "channel", "text", "color", "offset", "font_pt"}
        allowed.update({"from", "to", "line_pt"} if kind == "arrow" else {"at"})
        if kind == "dot":
            allowed.update({"line_pt", "radius_cm"})
        unknown = set(note) - allowed
        if unknown:
            raise ValueError(f"{name} 不支持字段 {', '.join(sorted(unknown))}；允许：{', '.join(sorted(allowed))}")
        for field, (lo, hi) in NOTE_LIMITS.items():
            if field in note:
                value = note[field]
                if (isinstance(value, bool) or not isinstance(value, (int, float))
                        or not math.isfinite(value) or not lo <= value <= hi):
                    raise ValueError(f"{name}.{field} 需为 {lo:g}～{hi:g} 的有限数值")


def samples(data: Data, column: str, window: list[float], budget=2400) -> list[list[float]]:
    """Time-ordered first/min/max/last per bucket. Never average away spikes.

    Include neighbours of the view edges, then clip at render time. Keep each
    channel's own extrema (not the indices from some other channel).
    """
    start = max(0, bisect_left(data.time, window[0]) - 1)
    stop = min(len(data.time), bisect_right(data.time, window[1]) + 1)
    values = data.columns[column]
    step = max(1, math.ceil((stop - start) / max(1, budget // 4)))
    kept = []
    for first in range(start, stop, step):
        last = min(first + step, stop)
        ids = {first, last - 1, min(range(first, last), key=values.__getitem__),
               max(range(first, last), key=values.__getitem__)}
        kept.extend(sorted(ids))
    return [[data.time[i], values[i]] for i in kept]


def last_period(data: Data, column: str) -> list[float]:
    if column not in data.columns:
        raise ValueError("请选择 PWM 参考通道")
    values = data.columns[column]
    low, high = min(values), max(values)
    if high - low <= 1e-12:
        raise ValueError("该通道没有开关跳变，请手动填写时间范围")
    threshold = (low + high) / 2
    rises, falls = [], []
    for i in range(1, len(values)):
        a, b = values[i - 1], values[i]
        if (a < threshold <= b) or (b < threshold <= a):
            t = data.time[i - 1] + (data.time[i] - data.time[i - 1]) * (threshold - a) / (b - a)
            (rises if b > a else falls).append(t)
    if len(rises) < 2:
        raise ValueError("至少需要两个 PWM 上升沿才能选出完整周期")
    begin, end = rises[-2:]
    middle = [t for t in falls if begin < t < end]
    if len(middle) != 1:
        raise ValueError("该区间不是单脉冲周期，请手动设置分界线")
    return [begin, middle[0], end]


class WaveStore:
    def __init__(self, layout_path: Path):
        self.path = layout_path
        self.cache = {}

    def _resolve(self, name: str) -> Path:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("请指定 CSV 文件")
        path = Path(name).expanduser()
        return (path if path.is_absolute() else self.path.parent / path).resolve()

    def read(self, cfg: dict) -> Data:
        if not isinstance(cfg, dict):
            raise ValueError("waveform 需为设置对象")
        path = self._resolve(cfg.get("source"))
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size, cfg.get("sha256"),
               cfg.get("time_column"), cfg.get("time_unit"))
        if key not in self.cache:
            raw = self._bytes(path)
            if hashlib.sha256(raw).hexdigest() != cfg.get("sha256"):
                raise ValueError("CSV 内容已变化，请重新载入并核对时间区间")
            data = parse_csv(raw, cfg.get("time_column"), cfg.get("time_unit"))
            if len(self.cache) >= 3:
                self.cache.clear()
            self.cache[key] = data
        return self.cache[key]

    @staticmethod
    def _bytes(path):
        if path.stat().st_size > MAX_BYTES:
            raise ValueError("CSV 最大支持 64 MiB")
        return path.read_bytes()

    def import_csv(self, lay: Layout, req: dict, profile: str = "ieee-single", *, target="overview") -> dict:
        if "text" in req:
            if not isinstance(req["text"], str):
                raise ValueError("CSV 内容需为文本")
            raw = req["text"].encode("utf-8")
            name = Path(req.get("filename", "waveform.csv")).stem
        else:
            source = self._resolve(req.get("source"))
            raw, name = self._bytes(source), source.stem
        data = parse_csv(raw, req.get("time_column"), req.get("time_unit", "s"))
        sha = hashlib.sha256(raw).hexdigest()
        name = re.sub(r"[^\w-]+", "_", name).strip("_")[:60] or "waveform"
        destination = self.path.parent / "waveforms-data" / f"{name}_{sha[:16]}.csv"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if hashlib.sha256(destination.read_bytes()).hexdigest() != sha:
                raise ValueError("CSV 快照文件内容不一致，请保留现场并重新命名导入文件")
        else:
            with destination.open("xb") as f:
                f.write(raw)
        time_column = req.get("time_column") or next(iter(data.columns))
        channels = []
        for key in data.columns:
            if key == time_column:
                continue
            label, sep, unit = key.rpartition("_")
            if not sep or unit not in ("V", "A", "1"):
                label, unit = key, ""
            channels.append({"column": key, "label": label, "unit": unit,
                             "color": COLORS[len(channels)]})
            if len(channels) == 3:
                break
        window = [data.time[0], data.time[-1]]
        cfg = {"version": 1, "source": destination.relative_to(self.path.parent).as_posix(),
               "sha256": sha, "time_column": time_column,
               "time_unit": req.get("time_unit", "s"),
               "display_unit": "us" if window[1] - window[0] < .001 else "ms",
               "relative_time": True,
               "window": window, "boundaries": window[:], "intervals": [None],
               "channels": channels, "circuit_signature": circuit_signature(lay)}
        previous = lay.extra.get("waveform", {})
        # A repeated run may reuse confirmed ranges/bindings, but only when the
        # columns and new time span still support them. Never invent a binding.
        if previous:
            candidate = {**cfg, **{k: deepcopy(previous[k]) for k in
                         ("window", "boundaries", "intervals", "channels", "display_unit", "relative_time",
                          "period_column", "slice_style", "arrange", "notes", "label_offsets")
                          if k in previous}}
            try:
                if candidate.get("period_column"):
                    marks = last_period(data, candidate["period_column"])
                    candidate.update(boundaries=marks, window=[marks[0], marks[-1]])
                validate_settings(lay, candidate, data)
                cfg = candidate
            except (ValueError, TypeError, KeyError):
                pass
        return self.preview(lay, cfg, profile, target=target)

    def preview(self, lay: Layout, cfg: dict, profile: str = "ieee-single", *, target="overview") -> dict:
        data = self.read(cfg)
        validate_settings(lay, cfg, data)
        channels = []
        for c in cfg["channels"]:
            points = samples(data, c["column"], cfg["window"])
            channels.append({**c, "points": points, "axis": axis(c, points, cfg["window"])})
        return {"config": cfg, "columns": list(data.columns), "rows": len(data.time),
                "paper": paper(cfg, profile, target, [m.get("name") for m in lay.modes]),
                "extent": [data.time[0], data.time[-1]], "channels": channels,
                "stale": cfg.get("circuit_signature") != circuit_signature(lay),
                "binding_errors": [i for i, name in enumerate(cfg["intervals"])
                                   if name is not None and sum(m.get("name") == name for m in lay.modes) != 1]}


def _text(value: str) -> str:
    # CSV headings are data, not executable TeX. Simple i_L / v_out labels
    # receive a subscript; all other text is escaped.
    if re.fullmatch(r"[A-Za-z]+_[A-Za-z0-9]+", value):
        a, b = value.split("_")
        return rf"${a}_{{\mathrm{{{b}}}}}$"
    escapes = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "$": r"\$",
               "&": r"\&", "#": r"\#", "%": r"\%", "_": r"\_",
               "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
    return "".join(escapes.get(c, c) for c in value)


def mode_tags(cfg: dict, names: list) -> list[int | None]:
    """1-based table index of each interval's mode; None when unbound."""
    return [names.index(n) + 1 if n in names else None for n in cfg["intervals"]]


def nice_ticks(lo: float, hi: float, count: int = Y_TICKS) -> list[float]:
    """Round values inside [lo, hi] at a 1/2/5 x 10^n step, at least two."""
    raw = (hi - lo) / max(1, count - 1)
    mag = 10 ** math.floor(math.log10(raw))
    steps = [m * mag for m in (.1, .2, .5, 1, 2, 5, 10)]
    k = next(i for i, step in enumerate(steps) if step >= raw * (1 - 1e-9))
    while True:
        step = steps[k]
        # Tolerance for window ends like 19.999999995 us read from the CSV.
        first = math.ceil(lo / step - 1e-6)
        ticks = [round(n * step, 12) for n in range(first, math.floor(hi / step + 1e-6) + 1)]
        if len(ticks) >= 2 or k == 0:
            return ticks
        k -= 1


def tick_label(value: float, ticks: list[float]) -> str:
    step = abs(ticks[1] - ticks[0]) if len(ticks) > 1 else abs(value) or 1
    places = max(0, -math.floor(math.log10(step) + 1e-9))
    text = f"{value:.{places}f}"
    return "0" if float(text) == 0 else text


def is_binary(values) -> bool:
    """A PWM / gate channel: every shown sample is exactly 0 or 1. The unit is
    no guide, since normalised voltages and currents are dimensionless too."""
    return set(values) <= {0.0, 1.0}


def axis(channel: dict, points: list, window: list[float]) -> dict:
    """Value range and labelled ticks of one row, shared by editor and export."""
    left, right = window
    visible = [v for t, v in points if left <= t <= right] or [v for t, v in points]
    if is_binary(visible):
        return {"range": [-.15, 1.15], "ticks": [0.0, 1.0], "labels": ["0", "1"]}
    lo, hi = min(visible), max(visible)
    pad = (hi - lo) * .08 if hi != lo else max(abs(hi) * .05, .05)
    lo, hi = lo - pad, hi + pad
    ticks = nice_ticks(lo, hi)
    return {"range": [lo, hi], "ticks": ticks, "labels": [tick_label(v, ticks) for v in ticks]}


def axis_title(channel: dict) -> str:
    # A dimensionless channel (PWM, unit "1") gets no "[1]".
    unit = channel.get("unit", "")
    return _text(channel["label"]) + (f" [{_text(unit)}]" if unit not in ("", "1") else "")


def spread(centres: list[float], widths: list[float], gap: float) -> list[float]:
    """Push crowding labels right so neighbours keep ``gap`` between them."""
    out, edge = [], -math.inf
    for c, w in zip(centres, widths):
        c = max(c, edge + gap + w / 2)
        out.append(c)
        edge = c + w / 2
    return out


def chart_profile(cfg: dict, profile: str, target="overview") -> str:
    if target not in ("overview", "standalone"):
        raise ValueError("波形预览目标只能是 overview 或 standalone")
    return "ieee-single" if target == "overview" and cfg.get("arrange") == "side" else profile


def chart_labels(cfg: dict, width: float, font_pt: float, names: list) -> list[dict]:
    """Label anchors in paper cm, before manual offsets; both renderers use these."""
    left, right = cfg["window"]
    xs = [(t - left) / (right - left) * width for t in cfg["boundaries"]]
    texts = [f"$t_{{{i}}}$" for i in range(len(xs))]
    centres = spread(xs, [text_size(t, font_pt)[0] for t in texts], LABEL_GAP_CM)
    labels = [{"key": f"t{i}", "at": [cx, ROW_CM + .02], "text": text}
              for i, (cx, text) in enumerate(zip(centres, texts))]
    if cfg.get("slice_style", "shade") == "shade":
        for i, k in enumerate(mode_tags(cfg, names)):
            span = xs[i + 1] - xs[i]
            if k is not None and span >= MIN_TAG_CM:
                labels.append({"key": f"mode{i}", "at": [(xs[i] + xs[i+1])/2, ROW_CM + .3],
                               "text": f"Mode {k}" if span >= FULL_TAG_CM else str(k)})
    return labels


def paper(cfg: dict, profile: str, target="overview", names=None) -> dict:
    """Printed geometry and defaults. Screen zoom never changes these values."""
    prof = get_profile(chart_profile(cfg, profile, target))
    width = prof.width_cm - 1.6
    height = len(cfg["channels"]) * ROW_CM + (len(cfg["channels"]) - 1) * ROW_GAP_CM
    return {"profile": prof.name, "width_cm": width, "row_cm": ROW_CM, "gap_cm": ROW_GAP_CM,
            "left_cm": CHART_LEFT_CM, "right_cm": CHART_RIGHT_CM,
            "top_cm": CHART_TOP_CM, "bottom_cm": CHART_BOTTOM_CM,
            "total_width_cm": width + CHART_LEFT_CM + CHART_RIGHT_CM,
            "total_height_cm": height + CHART_TOP_CM + CHART_BOTTOM_CM,
            "font_pt": prof.font_pt, "line_pt": prof.line_pt, "pt_cm": PT_CM,
            "arrow_head_cm": LINE_HEAD, "arrow_weight": NOTE_WEIGHT, "radius_cm": DOT_CM,
            "note_limits": NOTE_LIMITS, "nudge_cm": NUDGE_CM, "max_offset_cm": MAX_OFFSET_CM,
            "labels": chart_labels(cfg, width, prof.font_pt, names or []),
            "dot_label_offset": list(DOT_LABEL_OFFSET),
            "inks": {k: "#%02x%02x%02x" % ink_rgb(k) for k in INKS}}


def _note_text(text: str) -> str:
    # Notes are the author's own TeX when they use $...$; plain words are escaped.
    return text if "$" in text else _text(text)


def note_anchor(dx: float, dy: float) -> str:
    """Label side of an arrow's tail: away from where the arrow points."""
    if abs(dx) >= abs(dy):
        return "east" if dx > 0 else "west"
    return "north" if dy > 0 else "south"


def _notes_tex(notes: list, x, y, prof) -> list[str]:
    out = []
    for note in notes:
        col = ink_tikz(note.get("color", "black"))
        dx, dy = note.get("offset", [0, 0])
        text = note.get("text", "").strip()
        if note["kind"] == "arrow":
            (ta, va), (tb, vb) = note["from"], note["to"]
            a, b = (x(ta), y(va)), (x(tb), y(vb))
            arrow = Callout(a, b, style="line", color=note.get("color", "black"))
            weight = note.get("line_pt", prof.line_pt * NOTE_WEIGHT) / prof.line_pt
            out.append(_callout(arrow, Scale(1, 1), prof, False, weight=weight).rstrip())
            at, anchor = a, note_anchor(b[0] - a[0], b[1] - a[1])
        else:
            at = (x(note["at"][0]), y(note["at"][1]))
            anchor = "center"
            if note["kind"] == "dot":
                line = note.get("line_pt", prof.line_pt)
                radius = note.get("radius_cm", DOT_CM)
                out.append(rf"\draw[draw={col},line width={line:g}pt,fill=white] "
                           rf"({at[0]:.5f},{at[1]:.5f}) circle[radius={radius:g}];")
                anchor = "south west"
                if "offset" not in note:
                    dx, dy = DOT_LABEL_OFFSET
        if text:
            font = note.get("font_pt", prof.font_pt)
            out.append(rf"\node[anchor={anchor},inner sep=1pt,text={col},font=\fontsize{{{font:g}}}{{{font*1.2:g}}}\selectfont] at "
                       rf"({at[0] + dx:.5f},{at[1] + dy:.5f}) {{{_note_text(text)}}};")
    return out


def wave_tex(data: Data, cfg: dict, profile: str, names: list | None = None) -> str:
    """``names`` is the table's mode order; with slice_style "shade" each
    interval gets a band and its "Mode k" tag, k as in the mode files."""
    prof = get_profile(profile)
    sizes = paper(cfg, profile, "standalone", names)
    width, height, gap = sizes["width_cm"], sizes["row_cm"], sizes["gap_cm"]
    left, right = cfg["window"]
    factor = UNITS[cfg["display_unit"]]
    origin = left if cfg.get("relative_time", False) else 0
    time_unit = r"$\mu$s" if cfg["display_unit"] == "us" else cfg["display_unit"]
    rows = len(cfg["channels"])
    bottom = (rows - 1) * (height + gap)
    out = [r"\begin{tikzpicture}[x=1cm,y=1cm]" + "\n"]
    # Fix the chart width so individual figures and a composed sheet agree.
    out.append(rf"\path[use as bounding box] (-{CHART_LEFT_CM:g},-{bottom+CHART_BOTTOM_CM:g}) rectangle ({width+CHART_RIGHT_CM:g},{height+CHART_TOP_CM:g});")
    x = lambda t: (t - left) / (right - left) * width
    shade = cfg.get("slice_style", "shade") == "shade"
    marks = cfg["boundaries"]
    offsets = cfg.get("label_offsets", {})
    notes = [n for n in cfg.get("notes", [])
             if all(left <= p[0] <= right for p in (n.get("at"), n.get("from"), n.get("to")) if p)]
    times = nice_ticks((left - origin) / factor, (right - origin) / factor, 5)
    for row, channel in enumerate(cfg["channels"]):
        points = samples(data, channel["column"], cfg["window"])
        ax = axis(channel, points, cfg["window"])
        lo, hi = ax["range"]
        y = lambda v, lo=lo, hi=hi: (v - lo) / (hi - lo) * height
        out.append(rf"\begin{{scope}}[yshift=-{row*(height+gap):g}cm]")
        out.append(rf"\definecolor{{trace}}{{HTML}}{{{channel['color'][1:]}}}")
        if shade:
            # Every other interval, so neighbours stay apart in grey print.
            for i in range(0, len(marks) - 1, 2):
                out.append(rf"\fill[black!7] ({x(marks[i]):.7f},0) rectangle ({x(marks[i+1]):.7f},{height:g});")
        for v, label in zip(ax["ticks"], ax["labels"]):
            out.append(rf"\draw[gray!25,line width=.25pt] (0,{y(v):.6f}) -- ({width:g},{y(v):.6f});")
            out.append(rf"\node[anchor=east] at (-.1,{y(v):.6f}) {{{label}}};")
        out.append(rf"\node[rotate=90] at (-1.05,{height/2:g}) {{{axis_title(channel)}}};")
        out.append(r"\begin{scope}" + rf"\clip (0,0) rectangle ({width:g},{height:g});")
        coords = " -- ".join(f"({x(t):.7f},{y(v):.7f})" for t, v in points)
        out.append(rf"\draw[trace,line width={prof.line_pt:g}pt,line join=round] {coords};")
        out.append(r"\end{scope}")
        for t in marks:
            out.append(rf"\draw[gray,dashed,line width=.3pt] ({x(t):.7f},0) -- ({x(t):.7f},{height:g});")
        if row == 0:
            for label in sizes["labels"]:
                cx, cy = label["at"]
                dx, dy = offsets.get(label["key"], [0, 0])
                out.append(rf"\node[anchor=south,inner sep=1pt] at ({cx + dx:.7f},{cy + dy:g}) {{{label['text']}}};")
        out.append(rf"\draw[line width=.35pt] (0,{height:g}) -- (0,0) -- ({width:g},0);")
        # One shared time axis: tick marks on every row, numbers on the bottom one.
        for t in times:
            tx = x(t * factor + origin)
            out.append(rf"\draw[line width=.35pt] ({tx:.7f},0) -- ({tx:.7f},.06);")
            if row == rows - 1:
                out.append(rf"\node[anchor=north] at ({tx:.7f},-.04) {{{tick_label(t, times)}}};")
        out += _notes_tex([n for n in notes if n["channel"] == channel["column"]], x, y, prof)
        out.append(r"\end{scope}")
    title = r"Time $t-t_0$" if cfg.get("relative_time", False) else "Time"
    out.append(rf"\node[anchor=north] at ({width/2:g},-{bottom+.42:g}) {{{title} [{time_unit}]}};")
    out.append(r"\end{tikzpicture}")
    return "\n".join(out)


def export_waveforms(lay: Layout, store: WaveStore, out_dir: Path, stem: str,
                     profile="ieee-single", formats=("pdf", "svg", "png"),
                     include_modes=True) -> dict:
    if not isinstance(formats, (list, tuple)) or not formats or any(f not in ("pdf", "svg", "png", "tex") for f in formats):
        raise ValueError("请选择 PDF、SVG、PNG 或 TeX 导出格式")
    if not re.fullmatch(r"[\w-]+", stem):
        raise ValueError("文件名只能包含文字、数字、下划线与连字符")
    prof = get_profile(profile)
    report = check(lay, prof)
    if not report.ok:
        raise ValueError("电路校验未通过，不能导出波形与模态图")
    cfg = lay.extra.get("waveform", {})
    data = store.read(cfg)
    validate_settings(lay, cfg, data, bound=include_modes)
    if cfg.get("circuit_signature") != circuit_signature(lay):
        raise ValueError("电路连接或仿真参数已改变，请重新载入对应工况的 CSV")
    names = [m.get("name") for m in lay.modes]
    chart = wave_tex(data, cfg, profile, names)
    docs = {f"{stem}_waveforms": preamble(prof) + chart + "\n\\end{document}\n"}
    if include_modes:
        arrange = cfg.get("arrange", "stack")
        if arrange == "side" and prof.name != "ieee-double":
            raise ValueError("并排排版（arrange: side）需要双栏 ieee-double；单栏请用 stack 或 grid")
        selected = list(dict.fromkeys(cfg["intervals"]))
        bodies = {}
        for result in _mode_results(lay, profile, selected):
            name = result.mode.name
            docs[f"{stem}_mode{names.index(name) + 1}"] = result.tex
            bodies[name] = _body(result.tex)
        if arrange == "stack":
            docs[f"{stem}_waveform_modes"] = _stack(prof, chart, cfg, names, bodies)
        else:
            docs[f"{stem}_waveform_modes"] = _sliced(lay, data, cfg, prof, chart, names, selected)
    made = []
    # Publish only after every figure compiles; no temporary PDF/TeX leaks.
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        for name, tex in docs.items():
            made.extend(build(tex, stage, name, tuple(dict.fromkeys(formats))).values())
        out_dir.mkdir(parents=True, exist_ok=True)
        for source in made:
            shutil.copyfile(source, out_dir / source.name)
    return {"files": [str(out_dir / p.name) for p in made], "out_dir": str(out_dir),
            "note": "时间与模态的对应由使用者指定；校验不承诺工况正确。"}


def _body(tex: str) -> str:
    return tex.split(r"\begin{document}", 1)[1].rsplit(r"\end{document}", 1)[0]


def _mode_results(lay: Layout, profile: str, selected: list):
    results = render_modes(lay, profile, modes=[m for m in modes_of(lay) if m.name in selected],
                           sheet=False)
    return [next(r for r in results if r.mode.name == name) for name in selected]


def _stack(prof, chart: str, cfg: dict, names: list, bodies: dict) -> str:
    rows = [chart]
    for i, name in enumerate(cfg["intervals"]):
        # Numeric references avoid importing an obsolete template's time
        # caption into a newly chosen period. Mode names remain in JSON/UI.
        cap = rf"$[t_{{{i}}},t_{{{i+1}}})$ — Mode {names.index(name)+1}"
        rows.append(bodies[name] + r"\\[2pt] " + cap)
    combined = preamble(prof) + r"\begin{tabular}{@{}c@{}}" + "\n"
    combined += "\\\\[10pt]\n".join(rows) + "\n\\end{tabular}\n\\end{document}\n"
    return combined


def _sliced(lay: Layout, data: Data, cfg: dict, prof, chart: str, names: list,
            selected: list) -> str:
    """One diagram per distinct mode, captioned with every interval it covers,
    so the "Mode k" tags on the chart point at exactly one picture.

    The diagrams are drawn at single-column width (not scaled down), so the
    8 pt type matches the chart when two share a double-column line."""
    cell_prof = get_profile("ieee-single")
    try:
        results = _mode_results(lay, cell_prof.name, selected)
    except LayoutRejected as exc:
        raise ValueError("电路在单栏宽度下校验不通过，不能按模态并排；请改用 stack 排版") from exc
    cells = []
    for result in results:
        name = result.mode.name
        spans = ", ".join(rf"$[t_{{{i}}},t_{{{i+1}}})$"
                          for i, n in enumerate(cfg["intervals"]) if n == name)
        cap = f"Mode {names.index(name) + 1}: {spans}"
        cells.append(rf"\begin{{minipage}}[b]{{{cell_prof.width_cm:g}cm}}\centering "
                     + _body(result.tex) + r"\\[2pt] " + cap + r"\end{minipage}")
    head = preamble(prof)
    if cfg.get("arrange") == "side":
        # Chart and mode column are each one IEEE column wide; the rest of
        # the double column is the gutter between them.
        gap = prof.width_cm - 2 * cell_prof.width_cm
        left = wave_tex(data, cfg, cell_prof.name, names)
        right = f"\\\\[{MODE_GAP_PT}pt]\n".join(cells)
        body = (rf"\begin{{tabular}}{{@{{}}c@{{\hspace{{{gap:.3f}cm}}}}c@{{}}}}" + "\n"
                + rf"\begin{{minipage}}[c]{{{cell_prof.width_cm:g}cm}}\centering {left}\end{{minipage}} &"
                + "\n" + rf"\begin{{minipage}}[c]{{{cell_prof.width_cm:g}cm}}\centering {right}\end{{minipage}}"
                + "\n\\end{tabular}")
    else:
        cols = max(1, int(prof.width_cm // cell_prof.width_cm))
        gap = (prof.width_cm - cols * cell_prof.width_cm) / max(1, cols - 1)
        rows = [" & ".join(cells[i:i + cols]) for i in range(0, len(cells), cols)]
        spec = "@{}" + rf"c@{{\hspace{{{gap:.3f}cm}}}}" * (cols - 1) + "c@{}"
        grid = (rf"\begin{{tabular}}{{{spec}}}" + "\n"
                + f"\\\\[{MODE_GAP_PT}pt]\n".join(rows) + "\n\\end{tabular}")
        body = r"\begin{tabular}{@{}c@{}}" + "\n" + chart + r"\\[10pt]" + "\n" + grid + "\n\\end{tabular}"
    return head + body + "\n\\end{document}\n"
