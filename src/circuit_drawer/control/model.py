"""Control block diagram table: parsing and the data classes.

A control diagram is its own JSON file (``"kind": "control"``), separate
from the circuit coordinate table.  Like the circuit table it is the only
source of truth: every block centre, every link corner and every text is
written by hand.  Nothing here places or routes anything; the checker only
reads the table and judges it.

Sections::

    blocks   PI boxes, sums, multipliers, limiters, input tags, A/B switches
    links    signal lines between ports, with hand-written corners
    texts    free text
    regions  background panels and dashed boxes, in the circuit table's
             ``regions`` format (parsed and drawn by ``circuit_drawer.regions``)
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..layout import Point
from ..regions import (COLOR_ALIASES, INKS, PALETTE, Region,
                       RegionError, canon, parse_region, valid_ink)

KIND = "control"
BOX_TYPES = ("block", "limiter", "tag")
CIRCLE_TYPES = ("sum", "mul", "div")
BLOCK_TYPES = BOX_TYPES + CIRCLE_TYPES + ("switch",)
SIDES = ("w", "e", "n", "s")
SIGNS = ("+", "-")
POLE = "c"                 # the switch's common contact
ARROWS = ("end", "none", "start", "both")
LABEL_POS = ("above", "below", "left", "right")
ANCHORS = ("center", "west", "east", "north", "south",
           "north west", "north east", "south west", "south east")
TAG_STYLES = ("ref", "meas", "plain")

# Input tags as in published control figures: references on a cyan tag,
# measured signals on a yellow tag with a red frame, both in red type.
# style -> (fill, border ink, text ink)
TAG_STYLE = {"ref": ("cyan", "black", "red"), "meas": ("yellow", "red", "red"),
             "plain": ("white", "black", "black")}
# name -> (fill, grey level in black-and-white figures; the yellow measured-signal
# tag prints white so it stands out from a grey region behind it)
TAG_FILLS = {"cyan": ("#3fe0ee", 0.82), "yellow": ("#fff35c", 1.0),
             "green": ("#bfe8a0", 0.88), "orange": ("#ffcf8a", 0.86),
             "white": ("#ffffff", 1.0), "gray": ("#d9d9d9", 0.85)}
# Block fills include the bright reference-tag cyan, so 青 means cyan here;
# regions/inks have no cyan and keep 青 = teal. 青绿 is teal everywhere.
BLOCK_FILL_ALIASES = {"青": "cyan", "青色": "cyan"}

MIN_BOX = 0.2              # layout units: a smaller box is almost surely a typo
SECTIONS = ("blocks", "links", "texts", "regions")
# sections that carry numbers, not drawing: ``loop`` (transfer functions,
# plant, loop breaks) is read by ``loop.py``, ``code`` (sample rate, number
# format, limits) by ``codegen.py``; neither is drawn
DATA_SECTIONS = ("loop", "code", "functions", "control_ai")

_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PORT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z0-9_]+)([+-][0-9]*\.?[0-9]+)?$")


class ControlError(ValueError):
    pass


def _num(v: Any, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ControlError(f"{what} must be a number, not {v!r}")
    return float(v)


def _point(v: Any, what: str) -> Point:
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        raise ControlError(f"{what} must be [x, y]")
    return (_num(v[0], what), _num(v[1], what))


def _text(v: Any, what: str) -> str | None:
    if v is None or v == "":
        return None
    if not isinstance(v, str):
        raise ControlError(f"{what} must be text")
    return v


def _font(v: Any, what: str) -> float | None:
    return None if v is None else _num(v, what)


def fill_names() -> tuple[str, ...]:
    """Tag names retain their historical colours when palettes overlap."""
    return tuple(dict.fromkeys((*TAG_FILLS, *PALETTE, "none")))


def block_fill_name(value: Any) -> Any:
    """Canonical block-fill name, without changing region or ink aliases."""
    name = value.strip().lower() if isinstance(value, str) else None
    return name if name in TAG_FILLS else BLOCK_FILL_ALIASES.get(name, canon(value))


def fill_aliases() -> dict[str, str]:
    """Describe the actual parser rules for the generated prompt and UI."""
    return {name: block_fill_name(name) for name in (*COLOR_ALIASES, *BLOCK_FILL_ALIASES)}


def block_fill(value: Any, mono: bool = False) -> str:
    """Resolve every block fill once, for validation, TikZ and the canvas.

    Tag names and their block-specific aliases win over the region palette.
    Other aliases use the shared parser. Custom fills retain their exact RGB;
    monochrome keeps the original tag greys and minimum 0.75 luminance.
    """
    name = block_fill_name(value)
    if name == "none":
        return "none"
    grey = None
    if isinstance(name, str) and name in TAG_FILLS:
        hex_, grey = TAG_FILLS[name]
    elif isinstance(name, str) and name in PALETTE:
        hex_ = PALETTE[name][0]
    elif isinstance(name, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", name):
        hex_ = name
    else:
        raise ControlError(f"填充颜色 {value!r} 无效；可用 {', '.join(fill_names())}、"
                           "对应中文别名或 #rrggbb")
    if mono:
        if grey is None:
            r, g, b = (int(hex_[i:i + 2], 16) for i in (1, 3, 5))
            grey = max(0.75, (0.299 * r + 0.587 * g + 0.114 * b) / 255)
        v = round(255 * grey)
        return f"#{v:02x}{v:02x}{v:02x}"
    return hex_


def fill_options() -> dict[str, str]:
    return {name: block_fill(name) for name in (*fill_names(), *fill_aliases())}


def tag_fill_ok(v: Any) -> bool:
    try:
        block_fill(v)
        return True
    except ControlError:
        return False


def _ink(v: Any, what: str) -> str | None:
    if v is None:
        return None
    if not valid_ink(v):
        raise ControlError(f"{what}: 墨色 {v!r} 无效；可用 {', '.join(INKS)}、对应中文别名或 #rrggbb")
    return v


def validate_appearance(obj: dict, section: str, name: str) -> None:
    """Validate coordinate-free AI styles with the ordinary table rules."""
    from ..schema import control_appearance_fields
    for f in control_appearance_fields()[section]:
        if f.name not in obj:
            continue
        value = obj[f.name]
        if f.type == "ink":
            _ink(value, f"{name}: {f.name}")
        elif f.type == "fill" and value is not None:
            try:
                block_fill(value)
            except ControlError as exc:
                raise ControlError(f"{name}: {f.name}: {exc}") from exc
        elif f.type == "enum" and f.problem(value):
            raise ControlError(f"{name}: {f.name} 线型 {value!r} 无效；可用 {', '.join(f.values)}")


@dataclass
class Block:
    id: str
    type: str
    at: Point                          # centre; the pole for a switch
    size: tuple[float, float] = (0.0, 0.0)   # layout units, box types only
    text: str | None = None
    font: float | None = None          # pt; None = profile size
    label: str | None = None           # under/over the box (limiter range)
    label_pos: str = "below"
    signs: dict[str, str] = field(default_factory=dict)      # sum: side -> + / -
    style: str = "ref"                 # tag
    fill: str | None = None            # all symbols: palette/tag colour, #rrggbb or none
    border: str | None = None          # outline and internal symbol ink
    color: str | None = None           # text ink
    throws: dict[str, Point] = field(default_factory=dict)   # switch contacts
    on: str | None = None              # switch: the throw the blade touches
    toggle: bool = False               # switch: curved arrow between throws

    @property
    def is_box(self) -> bool:
        return self.type in BOX_TYPES

    def rect(self) -> tuple[float, float, float, float]:
        (x, y), (w, h) = self.at, self.size
        return (x - w / 2, y - h / 2, x + w / 2, y + h / 2)


@dataclass(frozen=True)
class End:
    """One end of a link: a port reference or a bare point."""
    block: str | None = None
    port: str | None = None
    offset: float = 0.0
    point: Point | None = None

    def __str__(self) -> str:
        if self.block is None:
            return f"[{self.point[0]:g}, {self.point[1]:g}]"
        off = f"{self.offset:+g}" if self.offset else ""
        return f"{self.block}.{self.port}{off}"


@dataclass
class Link:
    start: End
    end: End
    via: list[Point] = field(default_factory=list)
    arrow: str = "end"
    label: str | None = None
    label_pos: str = "above"
    label_at: float = 0.5
    font: float | None = None
    open: bool = False                 # a bare end may touch nothing (an output stub)
    name: str = "link"
    color: str | None = None
    dash: str = "solid"


@dataclass
class Text:
    at: Point
    text: str
    anchor: str = "center"
    font: float | None = None
    color: str | None = None
    name: str = "text"


@dataclass
class Diagram:
    name: str = "control"
    title: str = ""
    profile: str = "ieee-double"
    blocks: list[Block] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    texts: list[Text] = field(default_factory=list)
    regions: list[Region] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def block(self, bid: str) -> Block:
        for b in self.blocks:
            if b.id == bid:
                return b
        raise KeyError(bid)

    @classmethod
    def load(cls, path: str | Path) -> "Diagram":
        return parse(json.loads(Path(path).read_text(encoding="utf-8")))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.raw, indent=1, ensure_ascii=False) + "\n",
                              encoding="utf-8")


# ---- parsing -----------------------------------------------------------------
def parse_end(v: Any, what: str) -> End:
    if isinstance(v, str):
        m = _PORT.match(v.strip())
        if not m:
            raise ControlError(f"{what}: {v!r} is not a port such as \"pi.e\" or \"pwm.n-0.3\"")
        return End(m.group(1), m.group(2), float(m.group(3) or 0))
    return End(point=_point(v, what))


def parse_block(d: Any, i: int) -> Block:
    name = f"blocks[{i}]" + (f" ({d['id']})" if isinstance(d, dict) and d.get("id") else "")
    if not isinstance(d, dict):
        raise ControlError(f"{name} must be an object")
    bid, kind = d.get("id"), d.get("type")
    if not isinstance(bid, str) or not _ID.match(bid):
        raise ControlError(f"{name}: id must be letters, digits and _ (not starting with a digit)")
    if kind not in BLOCK_TYPES:
        raise ControlError(f"{name}: type must be one of {list(BLOCK_TYPES)}")
    at = _point(d.get("pole") if kind == "switch" else d.get("at"),
                f"{name}: {'pole' if kind == 'switch' else 'at'}")
    b = Block(bid, kind, at, text=_text(d.get("text"), f"{name}: text"),
              font=_font(d.get("size_pt"), f"{name}: size_pt"),
              label=_text(d.get("label"), f"{name}: label"),
              color=_ink(d.get("color"), f"{name}: color"),
              fill=d.get("fill"), border=_ink(d.get("border"), f"{name}: border"))
    if b.fill is not None:
        try:
            block_fill(b.fill)
        except ControlError as exc:
            raise ControlError(f"{name}: fill: {exc}") from exc
    if kind in BOX_TYPES:
        w, h = _point(d.get("size"), f"{name}: size")
        if w < MIN_BOX or h < MIN_BOX:
            raise ControlError(f"{name}: size must be at least {MIN_BOX} units each way")
        b.size = (w, h)
    pos = d.get("label_pos", "below")
    if pos not in LABEL_POS:
        raise ControlError(f"{name}: label_pos must be one of {list(LABEL_POS)}")
    b.label_pos = pos
    if kind == "sum":
        signs = d.get("signs")
        if not isinstance(signs, dict) or not signs:
            raise ControlError(f"{name}: a sum needs signs, e.g. {{\"w\": \"+\", \"s\": \"-\"}}")
        for side, sign in signs.items():
            if side not in SIDES or sign not in SIGNS:
                raise ControlError(f"{name}: signs map a side {list(SIDES)} to + or -, "
                                   f"not {side!r}: {sign!r}")
        b.signs = dict(signs)
    elif "signs" in d:
        raise ControlError(f"{name}: only a sum takes signs")
    if kind == "tag":
        style = d.get("style", "ref")
        if style not in TAG_STYLES:
            raise ControlError(f"{name}: style must be one of {list(TAG_STYLES)}")
        b.style = style
    if kind == "switch":
        throws = d.get("throws")
        if not isinstance(throws, dict) or len(throws) < 2:
            raise ControlError(f"{name}: a switch needs at least two throws, "
                               f"e.g. {{\"A\": [x, y], \"B\": [x, y]}}")
        for k, p in throws.items():
            if not isinstance(k, str) or not _ID.match(k) or k == POLE:
                raise ControlError(f"{name}: throw names are letters and digits other than {POLE!r}")
            b.throws[k] = _point(p, f"{name}: throws.{k}")
        on = d.get("on", next(iter(throws)))
        if on not in b.throws:
            raise ControlError(f"{name}: on must name one of the throws {list(b.throws)}")
        b.on = on
        b.toggle = bool(d.get("toggle", False))
    return b


def parse_link(d: Any, i: int) -> Link:
    name = f"links[{i}]" + (f" ({d['id']})" if isinstance(d, dict) and d.get("id") else "")
    if not isinstance(d, dict):
        raise ControlError(f"{name} must be an object")
    a, b = parse_end(d.get("from"), f"{name}: from"), parse_end(d.get("to"), f"{name}: to")
    via = d.get("via", [])
    if not isinstance(via, list):
        raise ControlError(f"{name}: via must be a list of corner points")
    arrow = d.get("arrow", "end")
    if arrow not in ARROWS:
        raise ControlError(f"{name}: arrow must be one of {list(ARROWS)}")
    pos = d.get("label_pos", "above")
    if pos not in LABEL_POS:
        raise ControlError(f"{name}: label_pos must be one of {list(LABEL_POS)}")
    at = _num(d.get("label_at", 0.5), f"{name}: label_at")
    if not 0 <= at <= 1:
        raise ControlError(f"{name}: label_at must be between 0 and 1")
    dash = d.get("dash", "solid")
    validate_appearance(d, "links", name)
    return Link(a, b, [_point(p, f"{name}: via point") for p in via], arrow,
                _text(d.get("label"), f"{name}: label"), pos, at,
                _font(d.get("size_pt"), f"{name}: size_pt"), bool(d.get("open", False)), name,
                _ink(d.get("color"), f"{name}: color"), dash)


def parse_text(d: Any, i: int) -> Text:
    name = f"texts[{i}]"
    if not isinstance(d, dict):
        raise ControlError(f"{name} must be an object")
    text = _text(d.get("text"), f"{name}: text")
    if text is None:
        raise ControlError(f"{name}: text is empty")
    anchor = d.get("anchor", "center")
    if anchor not in ANCHORS:
        raise ControlError(f"{name}: anchor must be one of {list(ANCHORS)}")
    return Text(_point(d.get("at"), f"{name}: at"), text, anchor,
                _font(d.get("size_pt"), f"{name}: size_pt"),
                _ink(d.get("color"), f"{name}: color"), f"{name} ({text!r})")


def parse(raw: Any) -> Diagram:
    """The whole table, or ControlError listing every schema problem."""
    dia, errors = parse_lenient(raw)
    if errors:
        raise ControlError("; ".join(errors))
    return dia


def parse_lenient(raw: Any) -> tuple[Diagram, list[str]]:
    """The entries that parse, and one message for each one that does not."""
    if not isinstance(raw, dict):
        return Diagram(), ["a control diagram must be a JSON object"]
    errors = []
    if raw.get("kind") != KIND:
        errors.append(f"kind must be {KIND!r} (a control diagram, not a circuit table)")
    dia = Diagram(name=str(raw.get("name") or "control"), title=str(raw.get("title") or ""),
                  profile=str(raw.get("profile") or "ieee-double"), raw=raw)
    parsers = {"blocks": (parse_block, dia.blocks), "links": (parse_link, dia.links),
               "texts": (parse_text, dia.texts), "regions": (parse_region, dia.regions)}
    for key, (fn, dst) in parsers.items():
        items = raw.get(key) or []
        if not isinstance(items, list):
            errors.append(f"{key} must be a list")
            continue
        for i, d in enumerate(items):
            try:
                dst.append(fn(d, i))
            except (ControlError, RegionError) as exc:
                errors.append(str(exc))
    unknown = sorted(set(raw) - set(SECTIONS) - set(DATA_SECTIONS)
                     - {"kind", "name", "title", "profile", "note"})
    if unknown:
        errors.append(f"unknown top-level keys {unknown}; sections are "
                      f"{list(SECTIONS + DATA_SECTIONS)}")
    seen: set[str] = set()
    if "control_ai" in raw:
        from .ai_metadata import metadata_errors
        errors.extend(metadata_errors(raw["control_ai"], dia.blocks))
    for b in dia.blocks:
        if b.id in seen:
            errors.append(f"block id {b.id!r} is used twice")
        seen.add(b.id)
    return dia, errors
