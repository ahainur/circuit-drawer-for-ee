"""Hand-drawn building blocks shared by several converter templates.

Every coordinate here was placed by hand on a 0.1-unit grid.  Blocks return
the points other blocks attach to; they never compute positions from the
circuit structure.
"""

from __future__ import annotations

from ..layout import PIN_NAMES, Layout, Point
from ..options import Option

# Standard vertical levels of a two-rail power stage
Y_TOP, Y_BOT = 3.9, 0.0          # DC rails
Y_P, Y_Q = 2.5, 1.2              # ac lines leaving a bridge
SW_DROP = 1.0                    # switch drawn between rail and rail -/+ 1.0

SWITCH_OPTION = Option(
    "switch", "mosfet", ("mosfet", "igbt"),
    "power switch symbol (MOSFET with body diode, or IGBT with anti-parallel diode)",
    {"mosfet": "mosfet", "igbt": "igbt", "mos": "mosfet"})


def switch(lay: Layout, name: str, label: str, x: float, top: float,
           kind: str) -> None:
    pins = {"d": (x, top), "s": (x, top - SW_DROP)} if kind == "mosfet" \
        else {"c": (x, top), "e": (x, top - SW_DROP)}
    lay.add(name, "nmos" if kind == "mosfet" else "igbt", label, **pins)


def top_pin(lay: Layout, name: str) -> str:
    return f"{name}.{PIN_NAMES[lay.element(name).kind][0]}"


def bottom_pin(lay: Layout, name: str) -> str:
    return f"{name}.{PIN_NAMES[lay.element(name).kind][1]}"


def half_bridge_leg(lay: Layout, x: float, upper: str, lower: str,
                    n_up: int, n_low: int, kind: str) -> None:
    """Two switches between the rails with a plain wire between them."""
    switch(lay, upper, f"$S_{{{n_up}}}$", x, Y_TOP, kind)
    lay.wire((x, Y_TOP - SW_DROP), (x, Y_BOT + SW_DROP))
    switch(lay, lower, f"$S_{{{n_low}}}$", x, Y_BOT + SW_DROP, kind)


def input_full_bridge(lay: Layout, kind: str, vin_label: str,
                      x_src: float = 0.0, x_a: float = 1.0,
                      x_b: float = 2.35) -> tuple[Point, Point]:
    """DC source + S1..S4.  Returns (A, B): leg A midpoint on line Y_P and leg
    B midpoint on line Y_Q.  A line leaving A to the right must hop leg B at
    (x_b, Y_P)."""
    lay.add("Vin", "V", vin_label, "left", a=(x_src, Y_BOT), b=(x_src, Y_TOP))
    lay.wire((x_src, Y_TOP), (x_b, Y_TOP))
    lay.wire((x_src, Y_BOT), (x_b, Y_BOT))
    half_bridge_leg(lay, x_a, "S1", "S2", 1, 2, kind)
    half_bridge_leg(lay, x_b, "S3", "S4", 3, 4, kind)
    lay.nets(DCP=["Vin.b", top_pin(lay, "S1"), top_pin(lay, "S3")],
             DCN=["Vin.a", bottom_pin(lay, "S2"), bottom_pin(lay, "S4")],
             NA=[bottom_pin(lay, "S1"), top_pin(lay, "S2")],
             NB=[bottom_pin(lay, "S3"), top_pin(lay, "S4")])
    return (x_a, Y_P), (x_b, Y_Q)
