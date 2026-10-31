"""Dual active bridge (DAB) converter.

Standard drawing: input full bridge S1..S4, series inductor L, 1:n
transformer, output full bridge S5..S8, Co || RL (or a DC source V2).
"""

from __future__ import annotations

from ..layout import Layout
from ..options import Option
from .common import (SWITCH_OPTION, Y_BOT, Y_P, Y_Q, Y_TOP, bottom_pin,
                     half_bridge_leg, input_full_bridge, top_pin)

NAME = "dab"
TITLE = "Dual active bridge converter"

OPTIONS = [
    SWITCH_OPTION,
    Option("output", "load", ("load", "source"),
           "output side: Co with resistive load, or a DC source V2",
           {"负载": "load", "电阻负载": "load", "电压源": "source", "电池": "source",
            "battery": "source"}),
    Option("currents", False, None, "draw the reference arrow of the inductor current",
           {"currents": True, "电流": True, "电流方向": True}),
    Option("inductor", "$L$", None, "label of the series inductor, e.g. $L_k$"),
]


def build(o: dict) -> Layout:
    lay = Layout(NAME)
    kind = o["switch"]
    (xa, _), (xb, _) = input_full_bridge(lay, kind, "$V_1$")

    x_l, x_w = 2.6, 3.9
    lay.wire((xa, Y_P), (x_l, Y_P))
    lay.hop((xb, Y_P))
    if o["currents"]:
        lay.current((1.65, Y_P), "$i_L$")
    lay.add("L", "L", o["inductor"], a=(x_l, Y_P), b=(3.65, Y_P))
    lay.wire((3.65, Y_P), (x_w, Y_P))
    lay.wire((x_w, Y_Q), (xb, Y_Q))

    x_s, x5, x6 = 4.5, 5.2, 6.55
    lay.add("T", "transformer", p1=(x_w, Y_P), p2=(x_w, Y_Q),
            s1=(x_s, Y_P), s2=(x_s, Y_Q), ratio="$1:n$")
    # s1 runs to the far leg and hops the near one; s2 lands on the near leg
    lay.wire((x_s, Y_P), (x6, Y_P))
    lay.hop((x5, Y_P))
    lay.wire((x_s, Y_Q), (x5, Y_Q))
    half_bridge_leg(lay, x5, "S5", "S6", 5, 6, kind)
    half_bridge_leg(lay, x6, "S7", "S8", 7, 8, kind)

    x_end = 8.55
    lay.wire((x5, Y_TOP), (x_end, Y_TOP))
    lay.wire((x5, Y_BOT), (x_end, Y_BOT))
    lay.nets(NA=["L.a"], NP=["L.b", "T.p1"], NB=["T.p2"],
             NC=["T.s2", bottom_pin(lay, "S5"), top_pin(lay, "S6")],
             ND=["T.s1", bottom_pin(lay, "S7"), top_pin(lay, "S8")],
             OP=[top_pin(lay, "S5"), top_pin(lay, "S7")],
             ON=[bottom_pin(lay, "S6"), bottom_pin(lay, "S8")])
    if o["output"] == "load":
        x_co = 7.5
        lay.add("Co", "C", "$C_o$", "left", a=(x_co, Y_TOP), b=(x_co, Y_BOT))
        lay.add("RL", "R", "$R_L$", "left", a=(x_end, Y_TOP), b=(x_end, Y_BOT))
        lay.nets(OP=["Co.a", "RL.a"], ON=["Co.b", "RL.b"])
        vx, ym = x_end + 0.52, (Y_TOP + Y_BOT) / 2
        lay.text((vx, ym + 0.42), "$+$")
        lay.text((vx, ym), "$v_o$")
        lay.text((vx, ym - 0.42), "$-$")
    else:
        lay.add("V2", "V", "$V_2$", "left", a=(x_end, Y_BOT), b=(x_end, Y_TOP))
        lay.nets(OP=["V2.b"], ON=["V2.a"])
    lay.modes = modes(o)
    return lay


def modes(o: dict) -> list[dict]:
    """Single phase shift, primary bridge leading, one half period (the other
    half mirrors it), continuous current.  Conventions: transformer 1:n
    (primary:secondary); v_p = v(A) - v(B) is the primary bridge output and
    v_s = v(s1) - v(s2) the secondary winding voltage, so v_p = +-V_1 and
    v_s = +-V_2 while the bridges conduct (v_s / n referred to the primary).
    i_L > 0 runs from leg A through L into the dotted primary terminal.
    A gated switch carrying reverse current conducts through its channel
    (MOSFET, synchronous) or its anti-parallel diode (IGBT)."""
    def rev(sw: str) -> str:
        return sw if o["switch"] == "mosfet" else f"{sw}:diode"
    load = o["output"] == "load"
    out = "Co|RL" if load else "V2"          # absorbing power, + to -
    back = "Co" if load else "V2"            # giving it back, - to +
    extra = [["RL", "Co"]] if load else []   # Co keeps feeding RL
    m1 = ["L", rev("S1"), "Vin", rev("S4"), "T:p"]
    s1 = ["T:s", rev("S5"), out, rev("S8")]
    s3 = ["T:s", rev("S7"), out, rev("S6")]
    p4 = ["L", "T:p", rev("S3"), "Vin", rev("S2")]
    seq = [
        {"name": "Mode 1", "caption": "$[t_0, t_1]$: $v_p = V_1$, $v_s = -V_2$, $i_L < 0$",
         "on": [rev("S1"), rev("S4"), rev("S5"), rev("S8")], "loops": [m1, s1]},
        {"name": "Mode 2", "caption": "$[t_1, t_2]$: $v_p = V_1$, $v_s = -V_2$, $i_L > 0$",
         "on": ["S1", "S4", "S5", "S8"],
         "loops": [["Vin", "S1", "L", "T:p", "S4"], ["T:s", "S8", back, "S5"]] + extra},
        {"name": "Mode 3", "caption": "$[t_2, t_3]$: $v_p = V_1$, $v_s = V_2$, $i_L > 0$",
         "on": ["S1", "S4", rev("S6"), rev("S7")],
         "loops": [["Vin", "S1", "L", "T:p", "S4"], s3]},
        {"name": "Mode 4", "caption": "$[t_3, t_4]$: $v_p = -V_1$, $v_s = V_2$, $i_L > 0$",
         "on": [rev("S2"), rev("S3"), rev("S6"), rev("S7")], "loops": [p4, s3]},
    ]
    return [{**m, "condition": "single phase shift, primary leading"} for m in seq]
