"""Control block diagrams (PI loops, sums, limiters, A/B switches) drawn in
the circuit figures' style from a hand-written JSON table.

The table format is described in the user manual (control diagrams chapter).  ``check`` judges a
table, ``render`` draws one that passes; nothing is placed automatically.
"""

from .check import Report, check
from .model import ControlError, Diagram, parse
from .render import TEMPLATES, render, template

__all__ = ["ControlError", "Diagram", "Report", "TEMPLATES", "check", "parse", "render",
           "template"]
