"""Function blocks: the table's ``blocks`` section.

A block is a labelled box of a block diagram drawn into the circuit figure:
the controller, PWM and driver boxes papers put under or beside a power stage
("Resonant Driver", "PWM", "$f_r$ calculation").  Signal arrows between blocks
and to the circuit are line callouts (``dash``, ``head`` and ``isolation``
options in regions.py); a grey controller area behind them is a region.
Blocks never touch connectivity: they have no pins and no netlist entries.

    {"id": "pwm", "from": [x0, y0], "to": [x1, y1], "text": "PWM"}

Like regions, blocks are stored as a plain top-level list (``Layout.extra``)
and the corners stay exactly as written.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .layout import Layout
from .regions import INKS, PALETTE, RegionError, _point, canon, ink_canon, valid_color, valid_ink

# "white" hides a grey region behind the box, as block diagrams print it;
# "none" leaves only the outline; palette tints as for regions.
FILLS = ("white", "none") + tuple(PALETTE)
BORDERS = ("solid", "dashed", "dotted")
KEYS = ("id", "from", "to", "text", "fill", "border", "color", "size")
MIN_SIDE = 0.2           # layout units: a smaller box cannot hold a word
TEXT_PAD = 0.06          # printed cm of clear space between the text and each edge


class BlockError(RegionError):
    pass


@dataclass
class Block:
    id: str
    x0: float
    y0: float
    x1: float
    y1: float
    text: str = ""
    fill: str = "white"
    border: str = "solid"
    color: str = "black"      # ink of the outline and the text
    size: float | None = None
    name: str = "block"


def parse_block(d: Any, i: int) -> Block:
    name = f"blocks[{i}]" + (f" ({d['id']})" if isinstance(d, dict) and isinstance(d.get("id"), str) else "")
    if not isinstance(d, dict):
        raise BlockError(f"{name} must be an object")
    unknown = sorted(set(d) - set(KEYS))
    if unknown:
        raise BlockError(f"{name}: unknown key {', '.join(unknown)}; a block accepts {', '.join(KEYS)}")
    bid = d.get("id")
    if not isinstance(bid, str) or not bid.strip():
        raise BlockError(f"{name}: id must be a non-empty name, e.g. \"pwm\"")
    try:
        (ax, ay), (bx, by) = _point(d.get("from"), "from"), _point(d.get("to"), "to")
    except RegionError as exc:
        raise BlockError(f"{name}: {exc}") from None
    x0, x1, y0, y1 = min(ax, bx), max(ax, bx), min(ay, by), max(ay, by)
    if x1 - x0 < MIN_SIDE or y1 - y0 < MIN_SIDE:
        raise BlockError(f"{name}: each side must be at least {MIN_SIDE} units")
    text = d.get("text", "")
    if not isinstance(text, str):
        raise BlockError(f"{name}: text must be text")
    if "\n" in text or "\\\\" in text:
        raise BlockError(f"{name}: text is one line; widen the box or use two blocks")
    fill = d.get("fill", "white")
    fill = fill if fill in ("white", "none") else canon(fill)
    if fill not in ("white", "none") and not valid_color(fill, allow_none=False):
        raise BlockError(f"{name}: fill must be one of {list(FILLS)} or #rrggbb")
    border = d.get("border", "solid")
    if border not in BORDERS:
        raise BlockError(f"{name}: border must be one of {list(BORDERS)}")
    color = ink_canon(d.get("color", "black"))
    if not valid_ink(color):
        raise BlockError(f"{name}: color must be one of {list(INKS)} or #rrggbb")
    size = d.get("size")
    if size is not None and (isinstance(size, bool) or not isinstance(size, (int, float))
                             or not math.isfinite(size)):
        raise BlockError(f"{name}: size must be a font size in pt")
    return Block(bid.strip(), x0, y0, x1, y1, text, fill, border, color,
                 float(size) if size is not None else None, name)


def _items(layout: Layout) -> list:
    v = layout.extra.get("blocks") or []
    if not isinstance(v, list):
        raise BlockError("blocks must be a list")
    return v


def problems(layout: Layout) -> list[str]:
    """Schema errors, one message each, plus repeated ids."""
    try:
        items = _items(layout)
    except BlockError as exc:
        return [str(exc)]
    out, seen = [], set()
    for i, d in enumerate(items):
        try:
            b = parse_block(d, i)
        except BlockError as exc:
            out.append(str(exc))
            continue
        if b.id in seen:
            out.append(f"{b.name}: id {b.id} is used by another block; ids must be unique")
        seen.add(b.id)
    return out


def safe_blocks(layout: Layout) -> list[Block]:
    """Every block that passes its schema (failures are reported by the
    validator), so extents and previews still work."""
    out = []
    try:
        items = _items(layout)
    except BlockError:
        return out
    for i, d in enumerate(items):
        try:
            out.append(parse_block(d, i))
        except BlockError:
            pass
    return out


def fill_rgb(b: Block, mono: bool = False) -> tuple[int, int, int] | None:
    """The box fill as 0-255 RGB, or None for no fill."""
    from .regions import rgb
    if b.fill == "none":
        return None
    if b.fill == "white":
        return (255, 255, 255)
    return rgb(b.fill, mono)
