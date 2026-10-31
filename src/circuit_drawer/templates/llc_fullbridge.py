"""Full-bridge LLC resonant converter.

Standard drawing: DC source, MOSFET full bridge, series Cr-Lr, Lm across the
primary, 1:n transformer, centre-tapped secondary with two diodes, Co || RL.
"""

from __future__ import annotations

from ..layout import Layout
from ..options import Option
from .common import (SWITCH_OPTION, Y_BOT, Y_P, Y_Q, Y_TOP, input_full_bridge)

NAME = "llc_fullbridge"
TITLE = "Full-bridge LLC resonant converter"

OPTIONS = [
    SWITCH_OPTION,
    Option("rectifier", "center_tap", ("center_tap", "full_bridge"),
           "secondary rectifier",
           {"中心抽头": "center_tap", "全波": "center_tap", "centertap": "center_tap",
            "全桥整流": "full_bridge", "桥式整流": "full_bridge"}),
    Option("currents", False, None, "draw reference arrows for i_p",
           {"currents": True, "电流": True, "电流方向": True}),
    Option("lm", True, None, "draw the magnetising inductance Lm as a branch",
           {"no_lm": False, "不画lm": False}),
    Option("fs", "below", ("below", "resonance", "above"),
           "operating point of the built-in modes (modal figures only): "
           "fs < fr, fs = fr or fs > fr, continuous output current",
           {"低于谐振": "below", "谐振点": "resonance", "等于谐振": "resonance",
            "高于谐振": "above", "fs<fr": "below", "fs=fr": "resonance", "fs>fr": "above"}),
]


def build(o: dict) -> Layout:
    lay = Layout(NAME)
    (xa, _), (xb, _) = input_full_bridge(lay, o["switch"], r"$V_\mathrm{in}$")

    # resonant tank on line Y_P, return on line Y_Q
    x_cr, x_lr, x_p, x_w = 2.6, 3.35, 4.5, 5.15
    lay.wire((xa, Y_P), (x_cr, Y_P))
    lay.hop((xb, Y_P))
    if o["currents"]:
        lay.current((1.65, Y_P), "$i_p$")
    lay.add("Cr", "C", "$C_r$", a=(x_cr, Y_P), b=(x_lr, Y_P))
    lay.add("Lr", "L", "$L_r$", a=(x_lr, Y_P), b=(x_p, Y_P))
    lay.wire((x_p, Y_P), (x_w, Y_P))
    lay.wire((x_w, Y_Q), (xb, Y_Q))
    if o["lm"]:
        lay.add("Lm", "L", "$L_m$", "left", a=(x_p, Y_P), b=(x_p, Y_Q))
    lay.nets(NA=["Cr.a"], NX=["Cr.b", "Lr.a"], NP=["Lr.b", "T.p1"], NB=["T.p2"])
    if o["lm"]:
        lay.nets(NP=["Lm.a"], NB=["Lm.b"])

    x_s = 5.85
    if o["rectifier"] == "center_tap":
        y1, yc, y2 = 3.1, 1.85, 0.6
        lay.add("T", "transformer", p1=(x_w, Y_P), p2=(x_w, Y_Q),
                s1=(x_s, y1), sct=(x_s, yc), s2=(x_s, y2), ratio="$1:n$")
        x_d, x_co, x_rl, x_r = 6.75, 7.1, 8.15, 9.05
        lay.add("D1", "D", "$D_1$", a=(x_s, y1), b=(x_d, y1))
        lay.add("D2", "D", "$D_2$", "below", a=(x_s, y2), b=(x_d, y2))
        lay.wire((x_d, y1), (x_r, y1))
        lay.wire((x_d, y2), (x_r, y2), (x_r, y1))
        lay.wire((x_s, yc), (x_rl, yc))
        lay.add("Co", "C", "$C_o$", "left", a=(x_co, y1), b=(x_co, yc))
        lay.add("RL", "R", "$R_L$", "left", a=(x_rl, y1), b=(x_rl, yc))
        vx = x_rl + 0.45
        lay.nets(NT1=["T.s1", "D1.a"], NT2=["T.s2", "D2.a"],
                 OP=["D1.b", "D2.b", "Co.a", "RL.a"], ON=["T.sct", "Co.b", "RL.b"])
        ym = (y1 + yc) / 2
    else:
        lay.add("T", "transformer", p1=(x_w, Y_P), p2=(x_w, Y_Q),
                s1=(x_s, Y_P), s2=(x_s, Y_Q), ratio="$1:n$")
        x1, x2, x_co, x_rl = 6.55, 7.6, 8.65, 9.7
        y_mid2 = 1.5
        # leg 1: midpoint on Y_P; leg 2: midpoint on Y_Q, reached by hopping leg 1
        lay.add("D1", "D", "$D_1$", a=(x1, Y_P), b=(x1, Y_TOP))
        lay.add("D2", "D", "$D_2$", a=(x1, y_mid2), b=(x1, Y_P))
        lay.wire((x1, Y_BOT), (x1, y_mid2))
        lay.add("D3", "D", "$D_3$", a=(x2, Y_Q), b=(x2, 2.3))
        lay.wire((x2, 2.3), (x2, Y_TOP))
        lay.add("D4", "D", "$D_4$", a=(x2, Y_BOT), b=(x2, Y_Q))
        lay.wire((x_s, Y_P), (x1, Y_P))
        lay.wire((x_s, Y_Q), (x2, Y_Q))
        lay.hop((x1, Y_Q))
        lay.wire((x1, Y_TOP), (x_rl, Y_TOP))
        lay.wire((x1, Y_BOT), (x_rl, Y_BOT))
        lay.add("Co", "C", "$C_o$", a=(x_co, Y_TOP), b=(x_co, Y_BOT))
        lay.add("RL", "R", "$R_L$", a=(x_rl, Y_TOP), b=(x_rl, Y_BOT))
        vx = x_rl + 0.95
        lay.nets(NT1=["T.s1", "D1.a", "D2.b"], NT2=["T.s2", "D3.a", "D4.b"],
                 OP=["D1.b", "D3.b", "Co.a", "RL.a"],
                 ON=["D2.a", "D4.a", "Co.b", "RL.b"])
        ym = (Y_TOP + Y_BOT) / 2
    lay.text((vx, ym + 0.42), "$+$")
    lay.text((vx, ym), "$v_o$")
    lay.text((vx, ym - 0.42), "$-$")
    lay.modes = modes(o)
    return lay


CONDITION = {"below": "$f_s < f_r$", "resonance": "$f_s = f_r$", "above": "$f_s > f_r$"}


def modes(o: dict) -> list[dict]:
    """One switching period at the chosen operating point (TI SLUP263 and
    the usual LLC mode analyses; the load is heavy enough that the output
    current is continuous).  i_p > 0 runs from leg A through Cr and Lr into
    the dotted primary terminal.

    - fs < fr: power delivery; i_r = i_m, rectifier off and Lm joining the
      resonance; dead time on the body diodes; the same for the other half.
    - fs = fr: as below resonance without the i_r = i_m interval.
    - fs > fr: the rectifier current is still flowing at each switching
      edge, so it commutates after the bridge does: reverse current through
      the newly gated pair, power delivery, dead time with the rectifier
      still conducting.
    A gated switch carrying reverse current conducts through its channel
    (MOSFET) or its anti-parallel diode (IGBT)."""
    def rev(sw: str) -> str:
        return sw if o["switch"] == "mosfet" else f"{sw}:diode"
    tp = "T:p|Lm" if o["lm"] else "T:p"
    lm = "Lm" if o["lm"] else "T:p"
    if o["rectifier"] == "center_tap":
        pos, neg = ["T:s1", "D1", "Co|RL"], ["T:s2", "D2", "Co|RL"]
        dpos, dneg = ["D1"], ["D2"]
    else:
        pos, neg = ["T:s", "D1", "Co|RL", "D4"], ["T:s", "D3", "Co|RL", "D2"]
        dpos, dneg = ["D1", "D4"], ["D2", "D3"]
    idle = ["RL", "Co"]          # Co alone feeds the load
    fwd14 = ["Vin", "S1", "Cr", "Lr", tp, "S4"]          # i_p > 0 from the source
    fwd23 = ["Vin", "S3", tp, "Lr", "Cr", "S2"]          # i_p < 0 from the source
    dead23 = ["Cr", "Lr", tp, "S3:diode", "Vin", "S2:diode"]   # i_p > 0 back to Vin
    dead14 = [tp, "Lr", "Cr", "S1:diode", "Vin", "S4:diode"]   # i_p < 0 back to Vin
    power14 = {"caption": "$S_1$, $S_4$ on, power to the load",
               "on": ["S1", "S4"] + dpos, "loops": [fwd14, pos]}
    power23 = {"caption": "$S_2$, $S_3$ on, power to the load",
               "on": ["S2", "S3"] + dneg, "loops": [fwd23, neg]}
    if o["fs"] == "above":
        seq = [
            {"caption": "$S_1$, $S_4$ gated, $i_p < 0$, $D_2$ still conducting",
             "on": [rev("S1"), rev("S4")] + dneg,
             "loops": [[tp, "Lr", "Cr", rev("S1"), "Vin", rev("S4")], neg]},
            power14,
            {"caption": "dead time, body diodes of $S_2$, $S_3$, $D_1$ still conducting",
             "on": ["S2:diode", "S3:diode"] + dpos, "loops": [dead23, pos]},
            {"caption": "$S_2$, $S_3$ gated, $i_p > 0$, $D_1$ still conducting",
             "on": [rev("S2"), rev("S3")] + dpos,
             "loops": [["Cr", "Lr", tp, rev("S3"), "Vin", rev("S2")], pos]},
            power23,
            {"caption": "dead time, body diodes of $S_1$, $S_4$, $D_2$ still conducting",
             "on": ["S1:diode", "S4:diode"] + dneg, "loops": [dead14, neg]},
        ]
    else:
        lm14 = {"caption": "$i_r = i_m$, rectifier off", "on": ["S1", "S4"],
                "loops": [["Vin", "S1", "Cr", "Lr", lm, "S4"], idle]}
        lm23 = {"caption": "$i_r = i_m$, rectifier off", "on": ["S2", "S3"],
                "loops": [["Vin", "S3", lm, "Lr", "Cr", "S2"], idle]}
        d23 = {"caption": "dead time, body diodes of $S_2$, $S_3$",
               "on": ["S2:diode", "S3:diode"] + dneg, "loops": [dead23, neg]}
        d14 = {"caption": "dead time, body diodes of $S_1$, $S_4$",
               "on": ["S1:diode", "S4:diode"] + dpos, "loops": [dead14, pos]}
        seq = [power14, lm14, d23, power23, lm23, d14] if o["fs"] == "below" \
            else [power14, d23, power23, d14]
    cond = CONDITION[o["fs"]]
    return [{"name": f"Mode {i}", "condition": cond,
             "caption": f"$[t_{{{i - 1}}}, t_{{{i}}}]$: {m['caption']}",
             "on": m["on"], "loops": m["loops"]} for i, m in enumerate(seq, 1)]
