"""Saved paper settings and shared rectangular decoration options."""
from __future__ import annotations

from dataclasses import replace
import math

STYLE_LIMITS = {"font_pt": (5, 24), "line_pt": (0.1, 3), "margin_mm": (0, 20)}
BOX_FIELDS = {
    "corner_radius": "printed corner radius in cm, 0..1; default 0 (square)",
    "shadow": "boolean; default false; subtle lower-right vector shadow",
    "inner_shadow": "boolean; grey inset at the edges of the box",
    "border_width": "individual frame width in pt, 0.1..3; omit for the paper default",
    "font_weight": "normal or bold for the printed label",
}


def font_weight(raw):
    value = raw.get("font_weight", "normal")
    if value not in ("normal", "bold"):
        raise ValueError("font_weight must be normal or bold")
    return value


def box_style(raw):
    radius = raw.get("corner_radius", 0)
    if type(radius) not in (int, float) or not math.isfinite(radius) or not 0 <= radius <= 1:
        raise ValueError("corner_radius must be a finite number 0..1 cm")
    shadow = raw.get("shadow", False)
    if not isinstance(shadow, bool):
        raise ValueError("shadow must be true or false")
    inner = raw.get("inner_shadow", False)
    if not isinstance(inner, bool):
        raise ValueError("inner_shadow must be true or false")
    width = raw.get("border_width")
    if width is not None and (type(width) not in (int, float) or not 0.1 <= width <= 3):
        raise ValueError("border_width must be 0.1..3 pt")
    return float(radius), shadow, inner, width, font_weight(raw)


def settings(raw):
    figure = raw.get("figure", {})
    if not isinstance(figure, dict):
        raise ValueError("figure must be an object")
    unknown = set(figure) - STYLE_LIMITS.keys() - {"compositions"}
    if unknown:
        raise ValueError(f"figure has unknown fields {sorted(unknown)}")
    for key, (minimum, maximum) in STYLE_LIMITS.items():
        if key in figure:
            value = figure[key]
            if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
                raise ValueError(f"figure.{key} must be {minimum}..{maximum}")
    if "compositions" in figure:
        from .figure_composition import validate_compositions
        validate_compositions(figure["compositions"])
    return figure


def paper_profile(prof, raw):
    figure = settings(raw)
    overrides = {key: figure[key] for key in STYLE_LIMITS if key in figure}
    return replace(prof, **overrides) if overrides else prof
