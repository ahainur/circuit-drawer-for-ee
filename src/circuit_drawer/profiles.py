"""Paper style profiles: font, target width, stroke and symbol sizes.

All symbol dimensions are absolute (cm) and do not change with the layout
scale, so the printed figure keeps the journal's 8 pt type and symbol sizes
whatever the circuit size.  Only the spacing between parts scales.
"""

from __future__ import annotations

from dataclasses import dataclass

CM_PER_IN = 2.54


@dataclass(frozen=True)
class Profile:
    name: str
    width_cm: float          # target figure width
    font_pt: float = 8.0
    line_pt: float = 0.55
    bipole_len: float = 0.75  # cm, CircuitikZ bipoles/length
    transistor_scale: float = 0.9
    dot_pt: float = 1.3
    min_scale: float = 0.55   # below this the figure is declared too cramped
    max_scale: float = 1.4   # wide profiles stop stretching here
    y_min_scale: float = 1.0  # height is not limited by the column
    y_max_scale: float = 1.2


# IEEE Transactions: single column 3.5 in, double column 7.16 in.  Leave a
# small margin so the PDF bounding box never exceeds \columnwidth.
PROFILES = {
    "ieee-single": Profile("ieee-single", width_cm=3.5 * CM_PER_IN - 0.1,
                           transistor_scale=0.8),
    "ieee-double": Profile("ieee-double", width_cm=7.16 * CM_PER_IN - 0.2),
}

ALIASES = {"single": "ieee-single", "double": "ieee-double",
           "单栏": "ieee-single", "双栏": "ieee-double"}


def get_profile(name: str) -> Profile:
    key = ALIASES.get(name, name)
    if key not in PROFILES:
        raise KeyError(f"unknown profile {name!r}; choose from "
                       f"{sorted(PROFILES) + sorted(ALIASES)}")
    return PROFILES[key]
