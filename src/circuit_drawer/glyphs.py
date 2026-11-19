"""Function marks inside a block: the table's ``glyphs`` section.

A glyph says what a collapsed or partly collapsed block contains, the way
papers draw a converter as a box.  ``acdc`` / ``dcdc`` / ``dcac`` are the
IEC 60617 / GB/T 4728 converter symbol: a diagonal across the box with the
two kinds of supply in opposite corners.  ``mosfet`` / ``igbt`` / ``diode``
are the common (not standardised) marks for a switching bridge or a
rectifier; say in the caption which bridge is meant.  ``battery``, ``pv``,
``rack``, ``capacitor``, ``ac`` and ``square`` are pictorial icons for
system-level block diagrams (storage, PV string, server rack / data-centre
load, DC link, AC grid or sinusoidal port, square-wave bridge port), drawn as
plain vector lines so they print like the rest of the figure.  ``python``,
``database``, ``script``, ``chip``, ``monitor`` and ``neural`` are software and
data icons for co-simulation and data-driven figures (a Python script, stored
simulation data, a code file, a DSP / MCU, a simulation tool or scope screen,
a neural network).  They are line art by default; a block's ``icon_color``
fills them with the colours of ``ICON_COLORS`` (greys in black-and-white
output).  A block can
carry any of these marks as its ``icon`` (blocks.py).  Glyphs never touch
connectivity.

    {"kind": "dcdc", "from": [x0, y0], "to": [x1, y1]}
"""
from __future__ import annotations

import math

from .layout import Layout

CONVERTERS = {"acdc": ("AC", "DC"), "dcdc": ("DC", "DC"), "dcac": ("DC", "AC")}
DEVICES = ("mosfet", "igbt", "diode")


def _wave(f, n: int = 24) -> list:
    """One period of f on [0.18, 0.82] across a circle of radius 0.34."""
    return [(0.18 + 0.64 * k / n, 0.5 + 0.16 * f(k / n)) for k in range(n + 1)]


def _svg(points) -> list:
    """Points drawn on a 100 x 100 sheet with y down -> the unit square, y up."""
    return [(round(x / 100, 4), round(1 - y / 100, 4)) for x, y in points]


def _arc(cx: float, cy: float, r: float, a0: float, a1: float, n: int = 6, ry: float | None = None) -> list:
    """Points of an arc on the 100 x 100 sheet (y down), angles in degrees."""
    ry = r if ry is None else ry
    return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * k / n)),
             cy + ry * math.sin(math.radians(a0 + (a1 - a0) * k / n))) for k in range(n + 1)]


def _ring(cx: float, cy: float, r: float, ry: float | None = None, n: int = 24) -> list:
    return _arc(cx, cy, r, 0, 360, n, ry)[:-1]


# One Python snake on the 100 x 100 sheet; the second is its point reflection.
_SNAKE = ([(42, 8)] + _arc(58, 18, 10, -90, 0) + [(68, 42)] + _arc(38, 48, 6, -90, -180)
          + [(32, 64)] + _arc(20, 54, 10, 90, 180) + _arc(20, 40, 10, 180, 270)
          + [(50, 30), (50, 24), (32, 24)] + _arc(42, 18, 10, 180, 270))


def _flip(points) -> list:
    return [(100 - x, 100 - y) for x, y in points]


def _sine(x0: float, x1: float, y: float, a: float, cycles: float, n: int = 32) -> list:
    return [(x0 + (x1 - x0) * k / n, y - a * math.sin(2 * math.pi * cycles * k / n)) for k in range(n + 1)]


_NN_LAYERS = ((18, (30, 50, 70)), (50, (18, 38, 62, 82)), (82, (40, 60)))


def _turn(points, deg: float, cx: float = 50, cy: float = 50) -> list:
    """``points`` given around (0, 0), turned by ``deg`` and moved to (cx, cy)."""
    c, s_ = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [(cx + x * c - y * s_, cy + x * s_ + y * c) for x, y in points]


def _rect(x0: float, y0: float, x1: float, y1: float) -> list:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _gear(cx: float, cy: float, r_out: float, r_in: float, teeth: int = 8) -> list:
    n = 4 * teeth
    return [(cx + (r_out if k % 4 in (0, 1) else r_in) * math.cos(2 * math.pi * (k + 0.5) / n),
             cy + (r_out if k % 4 in (0, 1) else r_in) * math.sin(2 * math.pi * (k + 0.5) / n)) for k in range(n)]


_BLADE = [(0, -3), (34, -2.5), (37, 0), (0, 3)]          # wind-turbine blade from the hub
_FAN_BLADE = [(-3, -8), (6, -36), (20, -28), (6, -6)]     # cooling-fan blade from the hub
_CLOUD = (_arc(26, 58, 14, 90, 270) + _arc(44, 42, 18, 200, 330) + _arc(68, 50, 13, 245, 360)
          + _arc(76, 60, 12, -90, 90))
_BULB = _arc(50, 40, 26, 125, 415, 20) + [(62, 74), (38, 74)]
_THERMO = ([(44, 62), (44, 14)] + _arc(50, 14, 6, 180, 360) + [(56, 62)]
           + _arc(50, 74, 13, -62.5, 242.5, 16))


# Pictorial icons in a unit square (0..1 both ways, y up), as line work:
# ("line", [points]) open polyline, ("rect", p0, p1) closed white-filled box
# drawn in order (later ones hide earlier ones), ("circle", centre, r) and
# ("dot", centre, r) filled.  The editor draws the very same primitives.
# The software / data icons add ("poly", [points], fill) closed filled
# polygons and ("line", [points], colour) lines.  ``fill`` is "white", "ink"
# (solid in the line colour) or a key of ``ICON_COLORS``; a key paints white in
# line art, and a line's colour key paints in the line colour.
ICONS = {
    # battery: two cells, long plate +, leads to the box
    "battery": [("line", [(0.5, 0.92), (0.5, 0.66)]), ("line", [(0.2, 0.66), (0.8, 0.66)]),
                ("line", [(0.34, 0.56), (0.66, 0.56)]), ("line", [(0.2, 0.44), (0.8, 0.44)]),
                ("line", [(0.34, 0.34), (0.66, 0.34)]), ("line", [(0.5, 0.34), (0.5, 0.08)])],
    # PV string: the panel's two diagonals meeting at the bottom middle
    "pv": [("line", [(0.06, 0.94), (0.5, 0.1), (0.94, 0.94)])],
    # server rack: two cabinets behind, the front one with four slots
    "rack": [("rect", (0.3, 0.28), (0.94, 0.94)), ("rect", (0.18, 0.17), (0.82, 0.83)),
             ("rect", (0.06, 0.06), (0.7, 0.72))]
            + [("rect", (0.14, y), (0.62, y + 0.09)) for y in (0.15, 0.28, 0.41, 0.54)]
            + [("dot", (0.2, y + 0.045), 0.018) for y in (0.15, 0.28, 0.41, 0.54)],
    # DC-link capacitor with leads
    "capacitor": [("line", [(0.5, 0.92), (0.5, 0.56)]), ("line", [(0.22, 0.56), (0.78, 0.56)]),
                  ("line", [(0.22, 0.44), (0.78, 0.44)]), ("line", [(0.5, 0.44), (0.5, 0.08)])],
    # AC source / grid: sine in a circle
    "ac": [("circle", (0.5, 0.5), 0.34), ("line", _wave(lambda t: math.sin(2 * math.pi * t)))],
    # square-wave bridge port (DAB / QAB equivalent circuits)
    "square": [("circle", (0.5, 0.5), 0.34),
               ("line", [(0.24, 0.5), (0.24, 0.62), (0.5, 0.62), (0.5, 0.38), (0.76, 0.38), (0.76, 0.5)])],
    # Python script: two interlocking snakes, an eye on each head
    "python": [("poly", _svg(_SNAKE), "py_blue"), ("poly", _svg(_flip(_SNAKE)), "py_yellow"),
               ("poly", _svg(_ring(42, 17, 3.6, n=12)), "py_eye"),
               ("poly", _svg(_ring(58, 83, 3.6, n=12)), "py_eye")],
    # database / stored simulation data: a cylinder with two seams
    "database": [("poly", _svg([(14, 26), (14, 80)] + _arc(50, 80, 36, 180, 0, 16, 10) + [(86, 26)]), "db_body"),
                 ("poly", _svg(_ring(50, 26, 36, 10, 32)), "db_top"),
                 ("line", _svg(_arc(50, 44, 36, 180, 0, 16, 10)), "db_seam"),
                 ("line", _svg(_arc(50, 62, 36, 180, 0, 16, 10)), "db_seam")],
    # code file: a page with a folded corner and </>
    "script": [("poly", _svg([(22, 8), (62, 8), (80, 26), (80, 92), (22, 92)]), "page"),
               ("line", _svg([(62, 8), (62, 26), (80, 26)])),
               ("line", _svg([(42, 44), (32, 55), (42, 66)]), "code"),
               ("line", _svg([(60, 44), (70, 55), (60, 66)]), "code"),
               ("line", _svg([(55, 40), (47, 70)]), "code"),
               ("line", _svg([(32, 80), (70, 80)]))],
    # DSP / MCU: a package with four pins a side, the die and its pin-1 dot
    "chip": [("line", _svg([(x, 8), (x, 20)]), "pin") for x in (32, 44, 56, 68)]
            + [("line", _svg([(x, 80), (x, 92)]), "pin") for x in (32, 44, 56, 68)]
            + [("line", _svg([(8, y), (20, y)]), "pin") for y in (32, 44, 56, 68)]
            + [("line", _svg([(80, y), (92, y)]), "pin") for y in (32, 44, 56, 68)]
            + [("poly", _svg([(20, 20), (80, 20), (80, 80), (20, 80)]), "chip_body"),
               ("poly", _svg([(34, 34), (66, 34), (66, 66), (34, 66)]), "chip_die"),
               ("poly", _svg(_ring(28, 28, 2.6, n=10)), "ink")],
    # simulation tool / scope: a screen with a sine and a square trace, on a stand
    "monitor": [("poly", _svg([(8, 12), (92, 12), (92, 70), (8, 70)]), "screen"),
                ("line", _svg(_sine(14, 86, 40, 14, 2)), "trace1"),
                ("line", _svg([(14, 60), (32, 60), (32, 26), (50, 26), (50, 60), (68, 60), (68, 26), (86, 26)]), "trace2"),
                ("line", _svg([(50, 70), (50, 84)])), ("line", _svg([(32, 86), (68, 86)]))],
    # neural network: three layers of nodes, fully connected
    "neural": [("line", _svg([(x0, a), (x1, b)]), "edge")
               for (x0, ys0), (x1, ys1) in zip(_NN_LAYERS, _NN_LAYERS[1:]) for a in ys0 for b in ys1]
              + [("poly", _svg(_ring(x, y, 7, n=16)), f"nn{k}")
                 for k, (x, ys) in enumerate(_NN_LAYERS) for y in ys],
    # ---- energy and grid ----
    # wind turbine: tower, hub and three blades
    "wind": [("poly", _svg([(47, 40), (53, 40), (57, 94), (43, 94)]), "metal")]
            + [("poly", _svg(_turn(_BLADE, a, 50, 36)), "blade") for a in (-90, 30, 150)]
            + [("poly", _svg(_ring(50, 36, 4.5, n=12)), "ink")],
    # fuel cell: a stack of alternating plates between two end plates
    "fuelcell": [("poly", _svg(_rect(14, 22, 22, 86)), "plate_end"), ("poly", _svg(_rect(78, 22, 86, 86)), "plate_end")]
                + [("poly", _svg(_rect(22 + 7 * k, 28, 29 + 7 * k, 80)), "plate_a" if k % 2 else "plate_b") for k in range(8)]
                + [("line", _svg([(30, 22), (30, 10), (70, 10), (70, 22)]), "pipe")],
    # transmission pylon: lattice legs, cross arms and insulators
    "pylon": [("line", _svg([(28, 94), (45, 10), (55, 10), (72, 94)])),
              ("line", _svg([(20, 26), (80, 26)])), ("line", _svg([(26, 44), (74, 44)])),
              ("line", _svg([(41, 26), (59, 44), (39, 60), (62, 76), (34, 92)])),
              ("line", _svg([(59, 26), (41, 44), (61, 60), (38, 76), (66, 92)]))]
             + [("line", _svg([(x, y), (x, y + 8)])) for x, y in ((22, 26), (78, 26), (28, 44), (72, 44))],
    # power-system transformer: two overlapping windings with leads
    "xfmr": [("line", _svg(_ring(38, 50, 22, n=32) + [_ring(38, 50, 22, n=32)[0]]), "wind1"),
             ("line", _svg(_ring(62, 50, 22, n=32) + [_ring(62, 50, 22, n=32)[0]]), "wind2"),
             ("line", _svg([(4, 50), (16, 50)])), ("line", _svg([(84, 50), (96, 50)]))],
    # DC source: a circle with the DC sign (solid over dashed line)
    "dcsource": [("circle", (0.5, 0.5), 0.34), ("line", _svg([(30, 44), (70, 44)]))]
                + [("line", _svg([(x, 58), (x + 9, 58)])) for x in (30, 45.5, 61)],
    # ---- converters and components ----
    # magnetic core: a toroid with windings
    "toroid": [("poly", _svg(_ring(50, 50, 38, n=32)), "ferrite"), ("poly", _svg(_ring(50, 50, 20, n=24)), "white")]
              + [("line", _svg([(50 + 16 * math.cos(math.radians(a)), 50 + 16 * math.sin(math.radians(a))),
                                (50 + 42 * math.cos(math.radians(a)), 50 + 42 * math.sin(math.radians(a)))]), "copper")
                 for a in range(15, 360, 30)],
    # circuit breaker: housing, toggle lever and two terminals
    "breaker": [("poly", _svg(_rect(26, 8, 74, 92)), "housing"), ("poly", _svg(_rect(41, 36, 59, 62)), "lever"),
                ("circle", (0.5, 0.8), 0.04), ("circle", (0.5, 0.2), 0.04)],
    # heat sink: base plate and fins
    "heatsink": [("poly", _svg(_rect(10, 74, 90, 88)), "alu")]
                + [("poly", _svg(_rect(13 + 13 * k, 18, 20 + 13 * k, 74)), "alu") for k in range(6)],
    # cooling fan: frame, three blades and the hub
    "fan": [("poly", _svg(_rect(8, 8, 92, 92)), "frame"), ("poly", _svg(_ring(50, 50, 38, n=32)), "white")]
           + [("poly", _svg(_turn(_FAN_BLADE, a)), "fan_blade") for a in (0, 120, 240)]
           + [("poly", _svg(_ring(50, 50, 8, n=16)), "dark")],
    # ---- loads and applications ----
    # motor: a circle with M, shaft and feet
    "motor": [("line", _svg([(40, 74), (36, 84)])), ("line", _svg([(60, 74), (64, 84)])),
              ("line", _svg([(26, 84), (74, 84)])), ("line", _svg([(80, 46), (96, 46)])),
              ("poly", _svg(_ring(50, 46, 32, n=36)), "motor_body"),
              ("line", _svg([(36, 60), (36, 32), (50, 50), (64, 32), (64, 60)]))],
    # generator: a circle with G
    "generator": [("line", _svg([(4, 50), (18, 50)])), ("poly", _svg(_ring(50, 50, 32, n=36)), "gen_body"),
                  ("line", _svg(_arc(50, 50, 14, -40, -360, 18) + [(52, 50)]))],
    # electric vehicle: body, windows and wheels
    "ev": [("poly", _svg([(8, 64), (12, 50), (28, 46), (38, 30), (68, 30), (80, 46), (92, 50), (94, 64)]), "car"),
           ("poly", _svg([(41, 35), (53, 35), (53, 46), (32, 46)]), "glass"),
           ("poly", _svg([(57, 35), (66, 35), (75, 46), (57, 46)]), "glass")]
          + [("poly", _svg(_ring(x, 66, 10, n=20)), "tyre") for x in (28, 74)]
          + [("poly", _svg(_ring(x, 66, 4, n=12)), "white") for x in (28, 74)],
    # EV charging pile: body, screen, bolt, cable and plug
    "charger": [("poly", _svg(_rect(18, 10, 58, 90)), "pile"), ("poly", _svg(_rect(24, 18, 52, 38)), "glass"),
                ("poly", _svg([(42, 44), (30, 64), (38, 64), (34, 82), (48, 58), (40, 58), (46, 44)]), "bolt"),
                ("line", _svg([(58, 56), (72, 56), (78, 62), (78, 72)]), "cable"),
                ("poly", _svg(_rect(72, 72, 84, 86)), "dark"), ("line", _svg([(10, 90), (66, 90)]))],
    # house: residential load
    "home": [("poly", _svg([(10, 50), (50, 14), (90, 50), (80, 50), (80, 90), (20, 90), (20, 50)]), "wall"),
             ("poly", _svg(_rect(43, 62, 57, 90)), "door"), ("poly", _svg(_rect(27, 58, 37, 68)), "glass"),
             ("poly", _svg(_rect(63, 58, 73, 68)), "glass")],
    # factory: industrial load
    "factory": [("poly", _svg([(8, 90), (8, 52), (28, 40), (28, 52), (48, 40), (48, 52), (68, 40), (68, 16),
                               (82, 16), (82, 90)]), "plant")]
               + [("poly", _svg(_rect(14 + 18 * k, 64, 24 + 18 * k, 74)), "glass") for k in range(3)],
    # lamp: lighting load
    "bulb": [("poly", _svg(_BULB), "bulb_glass"), ("line", _svg([(44, 74), (44, 54), (47, 48), (50, 54), (53, 48), (56, 54), (56, 74)]), "filament"),
             ("poly", _svg(_rect(38, 76, 62, 88)), "metal"), ("line", _svg([(38, 82), (62, 82)]))],
    # phone: consumer electronics / adapter load
    "phone": [("poly", _svg(_rect(30, 6, 70, 94)), "dark"), ("poly", _svg(_rect(34, 14, 66, 80)), "glass"),
              ("poly", _svg(_ring(50, 87, 3, n=10)), "white")],
    # plug
    "plug": [("line", _svg([(41, 36), (41, 14)])), ("line", _svg([(59, 36), (59, 14)])),
             ("poly", _svg([(28, 36), (72, 36), (72, 64), (58, 78), (42, 78), (28, 64)]), "dark"),
             ("line", _svg([(50, 78), (50, 96)]), "cable")],
    # wireless power transfer: transmitter and receiver coils with the field between
    "wpt": [("line", _svg(_ring(50, y, rx, ry, 28) + [_ring(50, y, rx, ry, 28)[0]]), "copper")
            for y in (22, 78) for rx, ry in ((36, 8), (24, 5.5), (12, 3))]
           + [("line", _svg([(x + 3 * math.sin(math.pi * k / 4), 34 + 32 * k / 16) for k in range(17)]), "field")
              for x in (36, 50, 64)],
    # energy / lightning bolt
    "bolt": [("poly", _svg([(60, 6), (24, 54), (46, 54), (38, 94), (76, 40), (54, 40), (66, 6)]), "bolt")],
    # ---- measurement and control ----
    # meter: a dial with ticks and a needle
    "meter": [("poly", _svg(_arc(50, 66, 40, 180, 360, 24)), "dial")]
             + [("line", _svg([(50 + 40 * math.cos(math.radians(a)), 66 + 40 * math.sin(math.radians(a))),
                               (50 + 32 * math.cos(math.radians(a)), 66 + 32 * math.sin(math.radians(a)))]))
                for a in range(200, 341, 35)]
             + [("line", _svg([(50, 66), (72, 38)]), "needle"), ("poly", _svg(_ring(50, 66, 4, n=12)), "ink")],
    # thermometer: temperature sensing
    "thermometer": [("poly", _svg(_THERMO), "white"), ("line", _svg([(50, 66), (50, 28)]), "mercury"),
                    ("poly", _svg(_ring(50, 74, 7, n=14)), "mercury_bulb")]
                   + [("line", _svg([(60, y), (66, y)])) for y in (24, 34, 44, 54)],
    # ---- software and data (beside python ... neural) ----
    # cloud: remote monitoring / IoT
    "cloud": [("poly", _svg(_CLOUD), "cloud")],
    # gear: optimisation / mechanical system
    "gear": [("poly", _svg(_gear(50, 50, 40, 31)), "metal"), ("poly", _svg(_ring(50, 50, 12, n=20)), "white")],
}
# Fills of the coloured software / data icons (block ``icon_color``).  The
# Python blue / yellow are the language's own; the rest follow the region
# palette's hues, light enough for black lines on top.
ICON_COLORS = {"py_blue": "#3776ab", "py_yellow": "#ffd43b", "py_eye": "#ffffff",
               "db_body": "#8fca72", "db_top": "#c4e6b1", "db_seam": "#3d6b1e",
               "page": "#eef3f8", "code": "#2f6fae",
               "pin": "#555555", "chip_body": "#dbe8f5", "chip_die": "#2f6fae",
               "screen": "#10202c", "trace1": "#59e07a", "trace2": "#ffd43b",
               "edge": "#8a8a8a", "nn0": "#ffd43b", "nn1": "#8fca72", "nn2": "#6fa8dc",
               "metal": "#c9ced6", "blade": "#f2f6fb", "plate_end": "#6e6e6e", "plate_a": "#6fa8dc",
               "plate_b": "#dbe8f5", "pipe": "#2f6fae", "wind1": "#2f6fae", "wind2": "#c96a14",
               "ferrite": "#5b5f66", "copper": "#c96a14", "housing": "#e6e6e6", "lever": "#b03a3a",
               "alu": "#c9ced6", "frame": "#d9dde3", "fan_blade": "#6fa8dc", "dark": "#3b3f46",
               "motor_body": "#dbe8f5", "gen_body": "#e2efd9", "car": "#6fa8dc", "glass": "#e8f1fb",
               "tyre": "#333333", "pile": "#8fca72", "bolt": "#ffd43b", "cable": "#333333",
               "wall": "#fce6cf", "door": "#c96a14", "plant": "#d9d9d9", "bulb_glass": "#fff3b0",
               "filament": "#c96a14", "field": "#2f6fae", "dial": "#fbf3c9", "needle": "#b03a3a",
               "mercury": "#b03a3a", "mercury_bulb": "#b03a3a", "cloud": "#dbe8f5"}
# line art: a fill key paints white except these, which stay solid ink
LINE_ART_INK = frozenset({"py_eye", "mercury_bulb"})
# Pictorial icons by what they stand for, for the editors' grouped menus and
# the docs; every entry of ICONS is listed exactly once.
ICON_GROUPS = {
    "energy": ("ac", "pv", "wind", "battery", "fuelcell", "pylon", "xfmr", "dcsource", "square"),
    "components": ("capacitor", "toroid", "breaker", "heatsink", "fan"),
    "loads": ("motor", "generator", "ev", "charger", "home", "factory", "rack", "bulb", "phone", "plug", "wpt", "bolt"),
    "measurement": ("meter", "thermometer", "chip", "monitor"),
    "software": ("python", "database", "script", "neural", "cloud", "gear"),
}
ICON_INSET = 0.1        # fraction of the shorter box side kept clear around an icon
KINDS = tuple(CONVERTERS) + DEVICES + tuple(ICONS)
# What the editors' menus call each mark (Chinese source strings; the page's
# i18n table translates them) and how they group it.
LABELS = {"acdc": "AC/DC 变换器", "dcdc": "DC/DC 变换器", "dcac": "DC/AC 变换器", "mosfet": "MOSFET",
          "igbt": "IGBT", "diode": "二极管", "battery": "电池／储能", "pv": "光伏板", "rack": "服务器机柜",
          "capacitor": "电容", "ac": "交流源／电网", "square": "方波源", "python": "Python 脚本",
          "database": "数据库／仿真数据", "script": "代码文件", "chip": "DSP／MCU 芯片",
          "monitor": "仿真软件／示波器", "neural": "神经网络", "wind": "风机", "fuelcell": "燃料电池",
          "pylon": "输电塔", "xfmr": "变压器（系统图）", "dcsource": "直流源", "toroid": "磁环电感",
          "breaker": "断路器", "heatsink": "散热器", "fan": "风扇", "motor": "电动机", "generator": "发电机",
          "ev": "电动汽车", "charger": "充电桩", "home": "住宅负载", "factory": "工业负载", "bulb": "照明负载",
          "phone": "消费电子／适配器", "plug": "插头", "wpt": "无线充电线圈", "bolt": "电能／闪电",
          "meter": "仪表", "thermometer": "温度计", "cloud": "云平台", "gear": "齿轮／优化"}
MENU_GROUPS = (("能源与电网", ICON_GROUPS["energy"]),
               ("变换器与器件", tuple(CONVERTERS) + DEVICES + ICON_GROUPS["components"]),
               ("负载与应用", ICON_GROUPS["loads"]),
               ("测量与控制", ICON_GROUPS["measurement"]),
               ("软件与数据", ICON_GROUPS["software"]))
# icons with a colour version (a block's ``icon_color``)
COLORED = tuple(k for k, prims in ICONS.items()
                if any(len(p) > 2 and p[0] in ("line", "poly") and p[2] not in ("white", "ink") for p in prims))


def icon_shapes(kind: str, box) -> list:
    """An icon's primitives in layout units, fitted square and centred in
    ``box`` = (x0, y0, x1, y1).  Radii scale with the square's side."""
    x0, y0, x1, y1 = box
    side = min(x1 - x0, y1 - y0) * (1 - 2 * ICON_INSET)
    ox, oy = (x0 + x1 - side) / 2, (y0 + y1 - side) / 2
    def p(q):
        return (round(ox + q[0] * side, 4), round(oy + q[1] * side, 4))
    out = []
    for prim in ICONS[kind]:
        if prim[0] in ("line", "poly"):
            out.append((prim[0], [p(q) for q in prim[1]], *prim[2:]))
        elif prim[0] == "rect":
            out.append(("rect", p(prim[1]), p(prim[2])))
        else:
            out.append((prim[0], p(prim[1]), round(prim[2] * side, 4)))
    return out
MONO_FLOOR = 0.75      # least grey of a coloured icon fill in black-and-white output (as control fills)


def paint(key: str | None, colored: bool, mono: bool = False, line: bool = False) -> str:
    """What a fill or line-colour key of a software / data icon prints as:
    "white", "ink" (the icon's line colour) or "#rrggbb".  Line art uses
    white fills and ink lines; colour output uses ``ICON_COLORS``, turned into
    greys (fills no darker than ``MONO_FLOOR``, lines ink) for black and white."""
    if key in (None, "ink") or (line and (mono or not colored)):
        return "ink"
    if key == "white":
        return "white"
    if not colored:
        return "ink" if key in LINE_ART_INK else "white"
    hex_ = ICON_COLORS[key]
    if not mono:
        return hex_
    r, g, b = (int(hex_[i:i + 2], 16) for i in (1, 3, 5))
    v = round(255 * max(MONO_FLOOR, (0.299 * r + 0.587 * g + 0.114 * b) / 255))
    return f"#{v:02x}{v:02x}{v:02x}"


def _rgb_tex(hex_: str) -> str:
    r, g, b = (int(hex_[i:i + 2], 16) for i in (1, 3, 5))
    return "{rgb,255:red,%d;green,%d;blue,%d}" % (r, g, b)


def icon_primitives(kind: str, box, colored: bool = False) -> list[dict] | None:
    """``icon_shapes`` as JSON for the editors' canvas sketch, colours
    resolved with ``paint`` ("white", "ink" or "#rrggbb"). Converter and
    device previews share their line/text marks; unknown kinds return None."""
    if kind in CONVERTERS:
        x0, y0, x1, y1 = box
        top, bottom = CONVERTERS[kind]
        k = CORNER_INSET
        return [{"kind": "line", "points": [[x0, y0], [x1, y1]]},
                {"kind": "text", "at": [x0+k*(x1-x0), y1-k*(y1-y0)], "text": top},
                {"kind": "text", "at": [x1-k*(x1-x0), y0+k*(y1-y0)], "text": bottom}]
    if kind in DEVICES:
        x0, y0, x1, y1 = box
        return [{"kind": "text", "at": [(x0+x1)/2, (y0+y1)/2],
                 "text": {"mosfet": "MOSFET", "igbt": "IGBT", "diode": "▲"}[kind]}]
    if kind not in ICONS:
        return None
    out = []
    for p in icon_shapes(kind, box):
        if p[0] == "line":
            d = {"kind": "line", "points": p[1]}
            ink = paint(p[2] if len(p) > 2 else None, colored, line=True)
            if ink != "ink":
                d["color"] = ink
        elif p[0] == "poly":
            d = {"kind": "poly", "points": p[1], "fill": paint(p[2], colored)}
        elif p[0] == "rect":
            d = {"kind": "rect", "from": p[1], "to": p[2]}
        else:
            d = {"kind": p[0], "at": p[1], "r": p[2]}
        out.append(d)
    return out


CORNER_INSET = 0.28     # fraction of the box from a corner to its converter text
DEVICE_SCALE = 0.8      # circuitikz symbol size inside a block
DIODE_LEAD = 0.05       # fraction of the box height for each diode lead


def _box(g):
    (ax, ay), (bx, by) = g["from"], g["to"]
    return min(ax, bx), min(ay, by), max(ax, bx), max(ay, by)


def problems(layout: Layout) -> list[str]:
    raw = layout.extra.get("glyphs", [])
    if not isinstance(raw, list):
        return ["glyphs must be a list"]
    out = []
    for i, g in enumerate(raw):
        name = f"glyphs[{i}]"
        if not isinstance(g, dict) or set(g) - {"kind", "from", "to"} or g.get("kind") not in KINDS:
            out.append(f"{name}: needs kind (one of {list(KINDS)}), from and to")
            continue
        pts = [g.get("from"), g.get("to")]
        if not all(isinstance(p, list) and len(p) == 2 and all(
                type(v) in (int, float) and math.isfinite(v) for v in p) for p in pts):
            out.append(f"{name}: from / to must be [x, y]")
            continue
        x0, y0, x1, y1 = _box(g)
        if x1 - x0 < 0.2 or y1 - y0 < 0.2:
            out.append(f"{name}: the box must be at least 0.2 units on each side")
    return out


def glyphs(layout: Layout) -> list[dict]:
    return [] if problems(layout) else list(layout.extra.get("glyphs", []))


def tikz(g: dict, color: str = "black", colored: bool = False, mono: bool = False) -> str:
    """``colored`` paints a software / data icon with ``ICON_COLORS``
    (``mono`` turns those into greys); other marks ignore both."""
    x0, y0, x1, y1 = _box(g)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    def p(x, y):
        return f"({x:.4f},{y:.4f})"
    if g["kind"] in CONVERTERS:
        top, bottom = CONVERTERS[g["kind"]]
        k = CORNER_INSET
        return (rf"\draw[{color}] {p(x0, y0)} -- {p(x1, y1)};" "\n"
                rf"\node[{color}] at {p(x0 + k * (x1 - x0), y1 - k * (y1 - y0))} {{{top}}};" "\n"
                rf"\node[{color}] at {p(x1 - k * (x1 - x0), y0 + k * (y1 - y0))} {{{bottom}}};" "\n")
    if g["kind"] == "diode":
        # filled diode pointing up with short leads, as a rectifier mark
        lead = DIODE_LEAD * (y1 - y0)
        return (rf"\node[fulldiodeshape, rotate=90, scale={DEVICE_SCALE}, {color}] (glyph) at {p(cx, cy)} {{}};" "\n"
                rf"\draw[{color}] (glyph.left) -- ++(0,{-lead:.4f}) (glyph.right) -- ++(0,{lead:.4f});" "\n")
    if g["kind"] in ICONS:
        out = []
        for prim in icon_shapes(g["kind"], (x0, y0, x1, y1)):
            if prim[0] == "line":
                ink = paint(prim[2] if len(prim) > 2 else None, colored, mono, line=True)
                ink = color if ink == "ink" else "color=" + _rgb_tex(ink)
                out.append(rf"\draw[{ink}, line join=round] " + " -- ".join(p(*q) for q in prim[1]) + ";")
            elif prim[0] == "poly":
                fill = paint(prim[2], colored, mono)
                fill = "" if fill == "ink" else ", fill=" + ("white" if fill == "white" else _rgb_tex(fill))
                out.append(rf"\filldraw[{color}{fill}, line join=round] "
                           + " -- ".join(p(*q) for q in prim[1]) + " -- cycle;")
            elif prim[0] == "rect":
                out.append(rf"\filldraw[{color}, fill=white] {p(*prim[1])} rectangle {p(*prim[2])};")
            elif prim[0] == "circle":
                out.append(rf"\draw[{color}] {p(*prim[1])} circle ({prim[2]:.4f});")
            else:
                out.append(rf"\fill[{color}] {p(*prim[1])} circle ({prim[2]:.4f});")
        return "\n".join(out) + "\n"
    shape = {"mosfet": "nmos, bodydiode", "igbt": "nigbt, bodydiode"}[g["kind"]]
    return rf"\node[{shape}, scale={DEVICE_SCALE}, {color}] at {p(cx, cy)} {{}};" "\n"
