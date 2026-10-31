"""Z-source (impedance-source) three-phase inverter.

Standard drawing: DC source and input diode, the symmetric X-shaped
impedance network (L1 on the top line, L2 on the bottom line, C1 and C2
crossing between them), a six-switch bridge and a star-connected load.

x=diagonal (default) draws the textbook X: C1 runs at 45 degrees from the
top-left node to the bottom-right one, C2 from the top-right node to the
bottom-left one, each capacitor on the upper half of its line; the two
lines cross in the middle with a hop.  x=orthogonal keeps every part
horizontal or vertical: C1 hangs under the top-left node and its return
runs to the bottom-right node, C2 likewise, and the two returns cross once.
"""

from __future__ import annotations

from ..layout import Layout
from ..options import Option
from .common import SWITCH_OPTION, Y_BOT, Y_TOP, bottom_pin, half_bridge_leg, top_pin

NAME = "zsource_inverter"
DEFAULT_PROFILE = "ieee-double"     # six switches and a three-phase load
TITLE = "Z-source three-phase inverter"

OPTIONS = [
    SWITCH_OPTION,
    Option("diode", True, None, "input blocking diode D1",
           {"无二极管": False, "nodiode": False}),
    Option("load", "r", ("r", "rl"), "star load per phase: R, or R in series with L",
           {"电阻负载": "r", "阻感负载": "rl", "rl": "rl"}),
    Option("x", "diagonal", ("diagonal", "orthogonal"),
           "capacitor X: 45-degree capacitors, or the orthogonal drawing",
           {"斜放": "diagonal", "45°": "diagonal", "正交": "orthogonal",
            "横平竖直": "orthogonal"}),
]


def build(o: dict) -> Layout:
    lay = Layout(NAME)
    kind = o["switch"]
    yt, yb = Y_TOP, Y_BOT

    lay.add("Vdc", "V", r"$V_\mathrm{dc}$", "left", a=(0, yb), b=(0, yt))
    if o["diode"]:
        lay.add("D1", "D", "$D_1$", a=(0.35, yt), b=(1.15, yt))
        lay.wire((0, yt), (0.35, yt))
        lay.wire((1.15, yt), (1.6, yt))
    else:
        lay.wire((0, yt), (1.6, yt))

    # nodes: A = top-left, PB = top-right, N = bottom-left, NB = bottom-right
    xa, xp = 1.6, 3.4
    lay.add("L1", "L", "$L_1$", a=(xa, yt), b=(xp, yt))
    lay.add("L2", "L", "$L_2$", "below", a=(xa, yb), b=(xp, yb))
    lay.wire((0, yb), (xa, yb))
    if o["x"] == "diagonal":
        # a square of side xp - xa centred between the rails
        h = (xp - xa) / 2
        cx, cy = (xa + xp) / 2, (yt + yb) / 2
        top, bot = cy + h, cy - h
        k = 0.6                                 # capacitor length along x and y
        lay.wire((xa, yt), (xa, top))
        lay.add("C1", "C", "$C_1$", "left", a=(xa, top), b=(xa + k, top - k))
        lay.wire((xa + k, top - k), (xp, bot), (xp, yb))        # C1 -> NB
        lay.wire((xp, yt), (xp, top))
        lay.add("C2", "C", "$C_2$", "right", a=(xp, top), b=(xp - k, top - k))
        lay.wire((xp - k, top - k), (xa, bot), (xa, yb))        # C2 -> N
        lay.hop((cx, cy))
        x_nb = xp
    else:
        y1, y2 = 1.9, 1.5                       # levels of the two crossing returns
        lay.wire((xa, yt), (xa, 3.2))
        lay.add("C1", "C", "$C_1$", "left", a=(xa, 3.2), b=(xa, 2.4))
        lay.wire((xa, 2.4), (xa, y1), (3.7, y1), (3.7, yb))        # C1 -> NB
        lay.hop((xp, y1))
        lay.wire((xp, yt), (xp, 3.2))
        lay.add("C2", "C", "$C_2$", "right", a=(xp, 3.2), b=(xp, 2.4))
        lay.wire((xp, 2.4), (xp, y2), (1.3, y2), (1.3, yb))        # C2 -> N
        x_nb = 3.7

    # three-phase bridge; phase lines leave the leg midpoints to the right
    legs = [(4.9, "S1", "S4", 1, 4), (6.3, "S3", "S6", 3, 6), (7.7, "S5", "S2", 5, 2)]
    lay.wire((xp, yt), (legs[-1][0], yt))
    lay.wire((x_nb, yb), (legs[-1][0], yb))
    if x_nb != xp:
        lay.wire((xp, yb), (x_nb, yb))
    for x, up, low, nu, nl in legs:
        half_bridge_leg(lay, x, up, low, nu, nl, kind)
    levels = (2.5, 2.0, 1.5)
    loads = (10.1, 9.4, 8.7)                   # a, b, c from the right
    for i, ((x, *_), y, xl) in enumerate(zip(legs, levels, loads)):
        lay.wire((x, y), (xl, y))
        for xo, *_ in legs[i + 1:]:
            lay.hop((xo, y))

    y_star = 0.4 if o["load"] == "r" else -0.4
    for ph, y, xl in zip("abc", levels, loads):
        if o["load"] == "r":
            lay.add(f"R{ph}", "R", f"$R_{ph}$", "left", a=(xl, y), b=(xl, y_star))
        else:
            lay.add(f"R{ph}", "R", f"$R_{ph}$", "left", a=(xl, y), b=(xl, 0.6))
            lay.add(f"L{ph}", "L", f"$L_{ph}$", "left", a=(xl, 0.6), b=(xl, y_star))
    lay.wire((loads[-1], y_star), (loads[0], y_star))

    lay.nets(DCP=["Vdc.b"], DCN=["Vdc.a", "L2.a", "C2.b"],
             A=["L1.a", "C1.a"], PB=["L1.b", "C2.a"], NB=["L2.b", "C1.b"])
    if o["diode"]:
        lay.nets(DCP=["Vdc.b", "D1.a"], A=["D1.b", "L1.a", "C1.a"])
    else:
        lay.nets(A=["Vdc.b", "L1.a", "C1.a"])
    for (x, up, low, *_), ph in zip(legs, "abc"):
        lay.nets(PB=[top_pin(lay, up)], NB=[bottom_pin(lay, low)])
        lay.nets(**{f"P{ph}": [bottom_pin(lay, up), top_pin(lay, low), f"R{ph}.a"]})
        if o["load"] == "rl":
            lay.nets(**{f"M{ph}": [f"R{ph}.b", f"L{ph}.a"]})
    star = [f"{'L' if o['load'] == 'rl' else 'R'}{ph}.b" for ph in "abc"]
    lay.nets(STAR=star)
    if not o["diode"]:
        lay.netlist.pop("Vdc.b", None)
        lay.nets(A=["Vdc.b"])
    return lay
