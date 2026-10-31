"""Three-phase Vienna rectifier (three-level boost PFC), the textbook drawing.

The three phases are horizontal rows: input terminal a/b/c (ports), boost inductor
with its current arrow, then the phase line u/v/w.  Three diode columns
stand between the P rail on top and the N rail at the bottom: column k has
its upper diode in the band under P and its lower diode in the band above
N, and joins phase k's row in between (crossing the other two rows with
hops).  Each row ends in the bidirectional switch, two switches back to
back with joined sources, and all three switches meet the vertical
midpoint bus O, which goes to the junction of the split capacitors C1/C2.
"""

from __future__ import annotations

from ..layout import PIN_NAMES, Layout
from .common import SWITCH_OPTION, bottom_pin, top_pin

NAME = "vienna_rectifier"
DEFAULT_PROFILE = "ieee-single"
TITLE = "Three-phase Vienna rectifier"

OPTIONS = [SWITCH_OPTION]

Y_N, Y_P = 0.0, 6.4
ROWS = {"a": 4.8, "b": 3.2, "c": 1.6}       # phase rows u, v, w
NODE = {"a": "u", "b": "v", "c": "w"}
X_COLS = {"a": 2.8, "b": 3.9, "c": 5.0}     # diode columns
X_SW, X_O, X_CAP = 6.0, 8.8, 9.8            # switch pair start, O bus, capacitors


def build(o: dict) -> Layout:
    lay = Layout(NAME)
    kind = "nmos" if o["switch"] == "mosfet" else "igbt"
    hi, lo = PIN_NAMES[kind]
    y_o = ROWS["b"]
    for i, (ph, y) in enumerate(ROWS.items()):
        x = X_COLS[ph]
        n = i + 1
        lay.add(f"T{ph}", "port", f"${ph}$", p=(0, y))
        lay.add(f"L{n}", "L", f"$L_{n}$", "below", a=(0.3, y), b=(1.7, y),
                current=f"$i_{NODE[ph]}$")
        lay.wire((0, y), (0.3, y))
        lay.wire((1.7, y), (X_SW, y))
        lay.text((x + 0.2, y - 0.25), f"${NODE[ph]}$")
        # diode column: P -> upper diode -> phase row -> lower diode -> N
        up, dn = f"D{n}", f"D{n + 3}"
        lay.add(up, "D", f"$D_{n}$", "right", a=(x, 5.2), b=(x, 6.0))
        lay.wire((x, 6.0), (x, Y_P))
        lay.wire((x, 5.2), (x, y))
        lay.add(dn, "D", f"$D_{n + 3}$", "right", a=(x, 0.4), b=(x, 1.2))
        lay.wire((x, Y_N), (x, 0.4))
        lay.wire((x, 1.2), (x, y))
        for other, yo in ROWS.items():
            if other != ph:
                lay.hop((x, yo))
        # bidirectional switch, gates down: phase side drain, sources joined
        sa, sb = f"S{ph}1", f"S{ph}2"
        lay.add(sa, kind, f"$S_{{{ph}1}}$", **{hi: (X_SW, y), lo: (X_SW + 1.0, y)})
        lay.add(sb, kind, f"$S_{{{ph}2}}$", mirror=True,
                **{lo: (X_SW + 1.2, y), hi: (X_SW + 2.2, y)})
        lay.wire((X_SW + 1.0, y), (X_SW + 1.2, y))
        lay.wire((X_SW + 2.2, y), (X_O, y))
        lay.nets(**{f"IN{ph}": [f"T{ph}.p", f"L{n}.a"],
                    NODE[ph].upper(): [f"L{n}.b", f"{up}.a", f"{dn}.b", top_pin(lay, sa)],
                    f"K{ph}": [bottom_pin(lay, sa), bottom_pin(lay, sb)],
                    "P": [f"{up}.b"], "N": [f"{dn}.a"], "O": [top_pin(lay, sb)]})
    lay.wire((X_O, ROWS["a"]), (X_O, ROWS["c"]))
    lay.wire((X_O, y_o), (X_CAP + 0.6, y_o))
    xs = list(X_COLS.values())
    lay.wire(*[(x, Y_P) for x in xs], (X_CAP, Y_P), (X_CAP + 0.6, Y_P))
    lay.wire(*[(x, Y_N) for x in xs], (X_CAP, Y_N), (X_CAP + 0.6, Y_N))
    lay.add("C1", "C", "$C_1$", "right", a=(X_CAP, Y_P), b=(X_CAP, y_o))
    lay.add("C2", "C", "$C_2$", "right", a=(X_CAP, y_o), b=(X_CAP, Y_N))
    for name, y in (("P", Y_P), ("O", y_o), ("N", Y_N)):
        lay.add(f"T{name}", "port", f"${name}$", "right", p=(X_CAP + 0.6, y))
    lay.nets(P=["C1.a", "TP.p"], O=["C1.b", "C2.a", "TO.p"], N=["C2.b", "TN.p"])
    return lay
