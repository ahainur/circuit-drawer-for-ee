"""What a coordinate table may contain: one declaration per field.

The validator checks element fields and annotations against these
declarations, and the AI prompt's field reference is generated from them, so
a field is declared once and the model, the validator and the docs cannot
drift apart.  Values that already have a home (palette, inks, callout styles,
source waves, simulation fields, model mappings) are imported from that
module rather than copied; the module that owns them also validates them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .geometry import FONT_RANGE, SIZE_RANGE
from .layout import PIN_NAMES, SWITCHES, TWO_TERMINAL
from .regions import CALLOUT_DASHES
from .source_waves import POSITIONS as WAVE_POSITIONS, SCALE_RANGE as WAVE_SCALE, WAVES

ALL_KINDS = frozenset(PIN_NAMES)


@dataclass(frozen=True)
class Field:
    name: str
    type: str                                # text | bool | number | enum | ink | point
    doc: str
    values: tuple[Any, ...] = ()             # enum: the accepted literals
    range: tuple[float, float] | None = None  # number: inclusive bounds
    default: Any = None
    kinds: frozenset[str] = ALL_KINDS        # element kinds an option applies to
    owner: str | None = None                 # issue code of the module that checks the value

    def accepted(self) -> str:
        """The accepted values as the prompt and error messages state them."""
        if self.type == "enum":
            return "|".join("false" if v is False else str(v) for v in self.values)
        if self.type == "number" and self.range:
            return f"{self.range[0]:g}..{self.range[1]:g}"
        return {"bool": "true|false", "ink": "ink name or #rrggbb", "point": "[dx, dy]（两个有限数值，图纸坐标单位）"}.get(self.type, "text")

    def problem(self, value: Any) -> str | None:
        """Why ``value`` is not accepted, or None; owned values are checked elsewhere."""
        if self.owner:
            return None
        if self.type == "bool":
            ok = isinstance(value, bool)
        elif self.type == "text":
            ok = isinstance(value, str)
        elif self.type == "point":
            ok = (isinstance(value, (list, tuple)) and len(value) == 2
                  and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                          and math.isfinite(v) for v in value))
        elif self.type == "number":
            ok = (isinstance(value, (int, float)) and not isinstance(value, bool)
                  and math.isfinite(value)
                  and (self.range is None or self.range[0] <= value <= self.range[1]))
        else:
            ok = any(value is v if isinstance(v, bool) else value == v for v in self.values)
        return None if ok else f"{self.name} must be {self.accepted()}, got {value!r}"


LABEL_SIDES = ("above", "below", "left", "right")

LABEL_POS = Field("label_pos", "enum", "page side of the label; default above for horizontal "
                  "parts, right for vertical ones (switches: the diode side; transformer name: below; "
                  "PMSM: below for a vertical phase row, right for a horizontal one)", LABEL_SIDES)

# opts keys of an element, by the kinds they apply to.
ELEMENT_OPTS: tuple[Field, ...] = (
    Field("symbol_style", "enum", "drawing preset only; ic uses a zigzag resistor and simplified MOSFETs "
          "with an open PMOS gate circle; physical body diodes remain and are shown when conducting in a mode",
          ("power", "ic"), default="power", kinds=frozenset({"R", "nmos", "pmos"})),
    Field("dash", "enum", "line pattern for an explicitly drawn parasitic R/L/C; drawing only",
          CALLOUT_DASHES, default="solid", kinds=frozenset({"R", "L", "C"})),
    Field("label_offset", "point", "user-dragged label offset [dx, dy] in layout units, "
          "page right/up positive, relative to label_pos; transformer: ratio text only; "
          "does not move pins, wires, current arrows or polarity dots", default=[0, 0]),
    Field("color", "ink", "draw the symbol and its label in this ink; only when the user "
          "asks for coloured marks", owner="bad-color"),
    Field("scale_length", "number", "symbol size along the pin axis (PMSM: phase lead stand-off); pin spacing and "
          "font unchanged", range=SIZE_RANGE, default=1),
    Field("scale_width", "number", "symbol size across the pin axis (PMSM: circle size, at least enclosing the phase pins)", range=SIZE_RANGE, default=1),
    Field("scale", "number", "legacy uniform symbol size; scale_length/scale_width override it",
          range=SIZE_RANGE, default=1),
    Field("label_size", "number", "font size in pt of the label, current and ratio text; "
          "default the profile font", range=FONT_RANGE),
    Field("current", "text", 'current arrow on the lead, e.g. "$i_L$"', kinds=frozenset(TWO_TERMINAL)),
    Field("current_side", "enum", "side of the current arrow text: ^ left of travel a->b, _ right",
          ("^", "_"), default="^", kinds=frozenset(TWO_TERMINAL)),
    Field("invert", "bool", "draw the symbol reversed between its pins; drawing only, SPICE "
          "ignores it: swap the pins to reverse a real source or diode", default=False,
          kinds=frozenset(TWO_TERMINAL)),
    Field("wave", "enum", "small source waveform mark beside the source (drawing only)",
          WAVES, kinds=frozenset({"V", "I"}), owner="bad-source-wave"),
    Field("wave_pos", "enum", "where the waveform mark sits; auto = opposite the label",
          WAVE_POSITIONS, default="auto",
          kinds=frozenset({"V", "I"}), owner="bad-source-wave"),
    Field("wave_scale", "number", "size of the waveform mark", range=WAVE_SCALE, default=1,
          kinds=frozenset({"V", "I"}), owner="bad-source-wave"),
    Field("mirror", "bool", "mirror the gate side (a thyristor's gate lead to the right of anode->cathode)",
          default=False, kinds=frozenset(SWITCHES | {"scr"})),
    Field("battery", "bool", "draw the voltage source as a battery: long plate at + (b), short "
          "plate at - (a); same pins and simulation fields", default=False, kinds=frozenset({"V"})),
    Field("zener", "bool", "draw D as a Zener diode (zD); a is anode, b cathode; simulation "
          "requires explicit breakdown_voltage > 0, never inferred from the symbol", default=False,
          kinds=frozenset({"D"})),
    Field("diode", "bool", "body / antiparallel diode; false removes it from drawing and SPICE",
          default=True, kinds=frozenset(SWITCHES)),
    Field("ratio", "text", 'turns ratio text, display only, e.g. "$1:n$"',
          kinds=frozenset({"transformer"})),
    Field("dots", "enum", "polarity dots: same = both at the top, opposite = flyback, "
          "false = none; must match the simulation polarity", ("same", "opposite", False),
          default="same", kinds=frozenset({"transformer"})),
    Field("core", "bool", "core lines between the windings; false draws a coupled inductor "
          "(same pins and simulation fields)", default=True, kinds=frozenset({"transformer"})),
    Field("bare", "bool", "no open circle: a terminal where a wire meets a block edge",
          default=False, kinds=frozenset({"port"})),
    Field("block", "text", "library-module bookkeeping: bare terminals of the same block and "
          "net are joined; do not write it yourself", kinds=frozenset({"port"})),
)
OPTS = {f.name: f for f in ELEMENT_OPTS}

ANNOTATION_FIELDS: tuple[Field, ...] = (
    Field("arrow", "enum", "makes it a reference current arrow on a wire at at",
          ("right", "left", "up", "down")),
    Field("side", "enum", "side of an arrow's text", LABEL_SIDES, default="above"),
    Field("size", "number", "font size in pt", range=FONT_RANGE),
    Field("color", "ink", "ink of the text and its arrow", owner="bad-color"),
)
ANNOTATION = {f.name: f for f in ANNOTATION_FIELDS}


def control_appearance_fields() -> dict[str, tuple[Field, ...]]:
    """Control-only reference; values remain owned by model/regions.

    Imports are lazy so the circuit schema does not depend on control parsing
    at import time. The same declarations feed the prompt and link validator.
    """
    from .control.model import BLOCK_TYPES, fill_names
    from .regions import BORDERS, CALLOUT_DASHES, FILLS, INKS, REGION_BORDER_COLORS
    blocks = frozenset(BLOCK_TYPES)
    return {
        "blocks": (
            Field("fill", "fill", "block/contact fill; transparent also allowed", fill_names(),
                  default="white; tag: TAG_STYLE", kinds=blocks, owner="control-appearance"),
            Field("border", "ink", "outline and inner symbol strokes", tuple(INKS),
                  default="black; tag: TAG_STYLE", kinds=blocks, owner="control-appearance"),
            Field("color", "ink", "text/sign ink; switch toggle arrow defaults to red", tuple(INKS),
                  default="black; tag: TAG_STYLE", kinds=blocks, owner="control-appearance")),
        "links": (
            Field("color", "ink", "line, arrowhead and label ink", tuple(INKS), default="black",
                  kinds=frozenset({"link"}), owner="control-appearance"),
            Field("dash", "enum", "signal line pattern", CALLOUT_DASHES, default="solid",
                  kinds=frozenset({"link"}))),
        "texts": (Field("color", "ink", "free text ink", tuple(INKS), default="black",
                        kinds=frozenset({"text"}), owner="control-appearance"),),
        "regions": (
            Field("fill", "fill", "background tint", FILLS, default="gray",
                  kinds=frozenset({"region"}), owner="bad-region"),
            Field("border", "enum", "region outline", BORDERS, default="none; transparent: dashed",
                  kinds=frozenset({"region"})),
            Field("border_color", "fill", "palette edge or ink colour (none is forbidden)", REGION_BORDER_COLORS,
                  default="fill; transparent: gray", kinds=frozenset({"region"}), owner="bad-region"))}


def control_appearance_reference() -> str:
    from .control.model import fill_aliases
    from .regions import COLOR_ALIASES, INK_ALIASES
    lines = []
    for section, fields in control_appearance_fields().items():
        for f in fields:
            values = "|".join(f.values)
            if f.type in ("fill", "ink"):
                aliases = COLOR_ALIASES if f.type == "fill" else INK_ALIASES
                if section == "blocks" and f.name == "fill":
                    aliases = fill_aliases()
                values += "|#rrggbb; aliases: " + ", ".join(
                    f"{k}={v}" for k, v in aliases.items() if k not in f.values and v in f.values)
            lines.append(f"{section}.{f.name} ({f.type}; {values}); default: {f.default}. {f.doc}")
    return "\n".join(lines)


def opts_for(kind: str) -> list[Field]:
    return [f for f in ELEMENT_OPTS if kind in f.kinds]


def element_problems(layout) -> list[tuple[str, str]]:
    """(code, message) for every element field or annotation the schema rejects.

    bad-option and bad-annotation stop validation: geometry looks label
    sides up directly and would fail on an unknown one.
    """
    out = []
    from .modes import ModeError, validate_source
    for mode in layout.modes:
        if isinstance(mode, dict) and "source" in mode:
            try:
                validate_source(mode["source"])
            except ModeError as exc:
                out.append(("bad-mode-source", f"{mode.get('name', 'mode')}: {exc}"))
    for el in layout.elements:
        if el.label_pos is not None and (msg := LABEL_POS.problem(el.label_pos)):
            out.append(("bad-option", f"{el.id}: {msg}"))
        if not isinstance(el.opts, dict):
            out.append(("bad-option", f"{el.id}: opts must be an object"))
            continue
        allowed = {f.name: f for f in opts_for(el.kind)}
        for key, value in el.opts.items():
            if key not in allowed:
                reason = f"is not an option of {el.kind}" if key in OPTS else "is not a known option"
                out.append(("bad-option", f"{el.id}: opts.{key} {reason}; {el.kind} accepts "
                            f"{', '.join(sorted(allowed))}"))
            elif msg := allowed[key].problem(value):
                out.append(("bad-option", f"{el.id}: opts.{msg}"))
    for i, ann in enumerate(layout.annotations):
        for name in ("arrow", "side", "size"):
            value = getattr(ann, name)
            if name == "side" and not ann.arrow:
                continue
            if value is not None and (msg := ANNOTATION[name].problem(value)):
                out.append(("bad-annotation", f"annotations[{i}] {ann.text!r}: {msg}"))
    return out
