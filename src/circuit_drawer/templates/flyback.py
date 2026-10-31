"""Flyback converter.

Standard drawing: DC source, primary winding in series with a low-side
MOSFET, coupled inductor with opposite polarity dots, secondary diode on the
top line, Co || RL.
"""

from __future__ import annotations

from ..layout import Layout
from ..options import Option
from .common import SWITCH_OPTION, switch

NAME = "flyback"
TITLE = "Flyback converter"

OPTIONS = [
    SWITCH_OPTION,
    Option("currents", False, None, "draw reference arrows for i_p and i_D",
           {"currents": True, "电流": True, "电流方向": True}),
    Option("diode", "top", ("top", "bottom"),
           "output diode on the positive line or in the return line",
           {"二极管在下": "bottom", "二极管下置": "bottom", "低边二极管": "bottom",
            "二极管在上": "top"}),
]


def build(o: dict) -> Layout:
    lay = Layout(NAME)
    y_top, y_sw, y_bot = 3.0, 1.6, 0.0
    x_p, x_s = 1.3, 1.9

    lay.add("Vin", "V", r"$V_\mathrm{in}$", "left", a=(0, y_bot), b=(0, y_top))
    lay.wire((0, y_top), (x_p, y_top))
    lay.add("T", "transformer", p1=(x_p, y_top), p2=(x_p, y_sw),
            s1=(x_s, y_top), s2=(x_s, y_sw), ratio="$1:n$", dots="opposite")
    switch(lay, "S", "$S$", x_p, y_sw, o["switch"])
    lay.wire((x_p, y_sw - 1.0), (x_p, y_bot), (0, y_bot))

    x_d, x_co, x_rl = 3.0, 3.4, 4.3
    if o["diode"] == "top":
        lay.add("D", "D", "$D$", a=(x_s, y_top), b=(x_d, y_top))
        lay.wire((x_d, y_top), (x_rl, y_top))
        lay.wire((x_s, y_sw), (x_s, y_bot), (x_rl, y_bot))
    else:
        # return current flows from the load back into the dotted end
        lay.wire((x_s, y_top), (x_rl, y_top))
        lay.wire((x_s, y_sw), (x_s, y_bot))
        lay.add("D", "D", "$D$", "below", a=(x_d, y_bot), b=(x_s, y_bot))
        lay.wire((x_d, y_bot), (x_rl, y_bot))
    lay.add("Co", "C", "$C_o$", "left", a=(x_co, y_top), b=(x_co, y_bot))
    lay.add("RL", "R", "$R_L$", "left", a=(x_rl, y_top), b=(x_rl, y_bot))
    if o["currents"]:
        lay.current((0.65, y_top), "$i_p$")
        if o["diode"] == "top":
            lay.current((3.2, y_top), "$i_D$", side="below")
        else:
            lay.current((3.2, y_bot), "$i_D$", "left")

    sw = lay.element("S")
    top, bot = ("S.d", "S.s") if sw.kind == "nmos" else ("S.c", "S.e")
    lay.nets(DCP=["Vin.b", "T.p1"], SW=["T.p2", top], DCN=[bot, "Vin.a"])
    if o["diode"] == "top":
        lay.nets(NS=["T.s1", "D.a"], OP=["D.b", "Co.a", "RL.a"],
                 ON=["T.s2", "Co.b", "RL.b"])
    else:
        lay.nets(NS=["T.s2", "D.b"], OP=["T.s1", "Co.a", "RL.a"],
                 ON=["D.a", "Co.b", "RL.b"])

    vx, ym = x_rl + 0.5, (y_top + y_bot) / 2
    lay.text((vx, ym + 0.42), "$+$")
    lay.text((vx, ym), "$v_o$")
    lay.text((vx, ym - 0.42), "$-$")
    lay.modes = modes(o)
    return lay


def modes(o: dict) -> list[dict]:
    """Switch on (magnetising), switch off (demagnetising through D), and the
    idle interval of discontinuous conduction."""
    sec = ["T:s", "D", "Co|RL"] if o["diode"] == "top" else ["T:s", "Co|RL", "D"]
    return [
        {"name": "Mode 1", "caption": "$S$ on: $L_m$ stores energy, $C_o$ feeds the load",
         "condition": "CCM or DCM", "on": ["S"], "loops": [["Vin", "T:p", "S"], ["RL", "Co"]]},
        {"name": "Mode 2", "caption": "$S$ off: energy released through $D$",
         "condition": "CCM or DCM", "on": ["D"], "loops": [sec]},
        {"name": "Mode 3", "caption": "DCM idle: $S$ and $D$ off",
         "condition": "DCM only", "on": [], "loops": [["RL", "Co"]]},
    ]
