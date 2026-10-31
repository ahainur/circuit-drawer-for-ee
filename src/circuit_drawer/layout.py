"""Coordinate table: the single source of truth for one figure.

A layout is what a template (or a model) writes by hand: every element pin,
every wire vertex and every free annotation has an explicit coordinate.
Nothing in this package places parts automatically.  The validator decides
whether a table is acceptable; the emitter only translates it to CircuitikZ.

Coordinates are in abstract layout units.  The emitter maps one unit to
``scale`` cm, chosen to fit within the requested column width without
unnecessarily stretching a small circuit.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

Point = tuple[float, float]

# Element kinds and the pin names each one must define.
PIN_NAMES: dict[str, tuple[str, ...]] = {
    "R": ("a", "b"),
    "L": ("a", "b"),
    "C": ("a", "b"),
    "D": ("a", "b"),            # a = anode, b = cathode
    "scr": ("a", "b"),          # thyristor: a = anode, b = cathode; the gate is a drive signal
    "V": ("a", "b"),            # a = minus, b = plus
    "vac": ("a", "b"),          # sinusoidal source, a = reference end
    "I": ("a", "b"),            # current flows a -> b
    "nmos": ("d", "s"),
    "igbt": ("c", "e"),
    "transformer": ("p1", "p2", "s1", "s2"),  # optional "sct" centre tap
    "port": ("p",),             # terminal: an open circle where a wire ends
}

TWO_TERMINAL = {"R", "L", "C", "D", "scr", "V", "vac", "I"}
SWITCHES = {"nmos", "igbt"}


def branch_pins(kind: str) -> tuple[str, str] | None:
    """The two pins a branch current flows between, in PIN_NAMES order (for
    a switch: drain/collector first).  None for a kind that is not one
    branch: a terminal has one pin, a transformer has windings."""
    pins = PIN_NAMES.get(kind, ())
    return pins if len(pins) == 2 else None


def _pt(value: Any) -> Point:
    x, y = value
    return (round(float(x), 4), round(float(y), 4))


# Top-level sections that only decorate the drawing (never connectivity) and
# hold layout coordinates: modules move, rename and carry them as a group.
DECORATION_SECTIONS = ("regions", "callouts", "wave_symbols", "glyphs", "blocks")


@dataclass
class Element:
    id: str
    kind: str
    pins: dict[str, Point]
    label: str | None = None
    # above / below / left / right; None picks above (horizontal) or right (vertical)
    label_pos: str | None = None
    opts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"id": self.id, "kind": self.kind,
                             "pins": {k: list(v) for k, v in self.pins.items()}}
        if self.label is not None:
            d["label"] = self.label
        if self.label_pos is not None:
            d["label_pos"] = self.label_pos
        if self.opts:
            d["opts"] = self.opts
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Element":
        return cls(id=d["id"], kind=d["kind"],
                   pins={k: _pt(v) for k, v in d["pins"].items()},
                   label=d.get("label"), label_pos=d.get("label_pos"),
                   opts=dict(d.get("opts", {})))


@dataclass
class Annotation:
    """Free text such as polarity marks or an output voltage name.

    With ``arrow`` set (right/left/up/down) it is a reference current arrow
    drawn on a wire at ``at``, and ``text`` is placed beside it on ``side``.
    """
    at: Point
    text: str
    arrow: str | None = None
    side: str = "above"
    size: float | None = None     # font size in pt; None = the profile's 8 pt
    color: str | None = None      # ink name or #rrggbb for the text and its arrow

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"at": list(self.at), "text": self.text}
        if self.arrow:
            d["arrow"] = self.arrow
            d["side"] = self.side
        if self.size:
            d["size"] = self.size
        if self.color:
            d["color"] = self.color
        return d


# Top-level keys of the coordinate table that Layout models itself.
CORE_KEYS = ("name", "elements", "wires", "hops", "annotations", "netlist", "modes")


@dataclass
class Layout:
    name: str
    elements: list[Element] = field(default_factory=list)
    wires: list[list[Point]] = field(default_factory=list)
    hops: list[Point] = field(default_factory=list)
    annotations: list[Annotation] = field(default_factory=list)
    # Expected connectivity: "Elem.pin" -> net name.  Optional for hand-written
    # tables; templates always provide it so geometry is checked against it.
    netlist: dict[str, str] = field(default_factory=dict)
    # Operating modes for modal figures (see modes.py): which devices conduct
    # and the current loops.  They only restyle this same table.
    modes: list[dict[str, Any]] = field(default_factory=list)
    # Any other top-level section of the table (e.g. simulation settings),
    # kept verbatim so a feature can store its data here without a new field.
    # AI edits carry it over unchanged.  Only regions.py reads its "regions"
    # and "callouts" (background blocks and arrows, never connectivity).
    extra: dict[str, Any] = field(default_factory=dict)

    # ---- builder helpers used by templates -------------------------------
    def add(self, id: str, kind: str, label: str | None = None,
            label_pos: str | None = None, **pins_and_opts: Any) -> Element:
        pins = {k: _pt(v) for k, v in pins_and_opts.items()
                if k in PIN_NAMES.get(kind, ()) or k == "sct"}
        opts = {k: v for k, v in pins_and_opts.items() if k not in pins}
        el = Element(id, kind, pins, label, label_pos, opts)
        self.elements.append(el)
        return el

    def wire(self, *points: Point) -> None:
        self.wires.append([_pt(p) for p in points])

    def hop(self, p: Point) -> None:
        self.hops.append(_pt(p))

    def text(self, at: Point, text: str) -> None:
        self.annotations.append(Annotation(_pt(at), text))

    def current(self, at: Point, text: str, arrow: str = "right",
                side: str = "above", color: str | None = None) -> None:
        self.annotations.append(Annotation(_pt(at), text, arrow, side, color=color))

    def nets(self, **groups: list[str]) -> None:
        for net, pins in groups.items():
            for p in pins:
                self.netlist[p] = net

    def element(self, id: str) -> Element:
        for el in self.elements:
            if el.id == id:
                return el
        raise KeyError(id)

    # ---- JSON coordinate table ---------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        d = {
            "name": self.name,
            "elements": [e.to_dict() for e in self.elements],
            "wires": [[list(p) for p in w] for w in self.wires],
            "hops": [list(p) for p in self.hops],
            "annotations": [a.to_dict() for a in self.annotations],
            "netlist": dict(self.netlist),
        }
        if self.modes:
            d["modes"] = [dict(m) for m in self.modes]
        d.update(deepcopy(self.extra))
        return d

    def clean(self) -> int:
        """Drop repeated consecutive wire points, wires left with fewer than
        two points (what dragging a part onto its neighbour leaves behind)
        and hops on no wire (left when a crossing wire is moved away).
        Returns how many things were removed or shortened."""
        n, kept = 0, []
        for w in self.wires:
            pts = [p for i, p in enumerate(w) if i == 0 or p != w[i - 1]]
            n += len(pts) != len(w)
            if len(pts) >= 2:
                kept.append(pts)
            elif len(pts) == len(w):
                n += 1
        self.wires = kept

        def on_wire(h: Point) -> bool:
            for w in self.wires:
                for (x0, y0), (x1, y1) in zip(w, w[1:]):
                    dx, dy = x1 - x0, y1 - y0
                    t = ((h[0] - x0) * dx + (h[1] - y0) * dy) / (dx * dx + dy * dy)
                    if abs((h[0] - x0) * dy - (h[1] - y0) * dx) < 1e-6 and 1e-6 < t < 1 - 1e-6:
                        return True
            return False
        hops = [h for h in self.hops if on_wire(h)]
        n += len(self.hops) - len(hops)
        self.hops = hops
        return n

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Layout":
        lay = cls._from_dict(d)
        lay.clean()
        return lay

    @classmethod
    def _from_dict(cls, d: dict[str, Any]) -> "Layout":
        return cls(
            name=d.get("name", "figure"),
            elements=[Element.from_dict(e) for e in d.get("elements", [])],
            wires=[[_pt(p) for p in w] for w in d.get("wires", [])],
            hops=[_pt(p) for p in d.get("hops", [])],
            annotations=[Annotation(_pt(a["at"]), a["text"], a.get("arrow"),
                                    a.get("side", "above"), a.get("size"),
                                    a.get("color") or None)
                         for a in d.get("annotations", [])],
            netlist=dict(d.get("netlist", {})),
            modes=[dict(m) for m in d.get("modes", [])],
            extra={k: deepcopy(v) for k, v in d.items() if k not in CORE_KEYS},
        )

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=1,
                                         ensure_ascii=False) + "\n",
                              encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Layout":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
