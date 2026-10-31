"""Drawing-only source glyphs. Simulation data stays in ``simulation``."""
from __future__ import annotations

import math

from .regions import valid_ink

WAVES = ("step-up", "step-down", "pulse")
POSITIONS = ("auto", "left", "right", "above", "below")
SCALE_RANGE = (0.5, 2.0)


def options(d: dict) -> dict:
    wave = d.get("wave")
    if wave not in WAVES:
        raise ValueError(f"wave must be one of {WAVES}")
    z = d.get("scale", 1)
    if (isinstance(z, bool) or not isinstance(z, (int, float))
            or not math.isfinite(z) or not SCALE_RANGE[0] <= z <= SCALE_RANGE[1]):
        raise ValueError("wave scale must be between 0.5 and 2")
    return {"wave": wave, "scale": z}


def source_options(el) -> dict | None:
    if "wave" not in el.opts:
        return None
    if el.kind not in ("V", "I"):
        raise ValueError("opts.wave is only supported on V / I sources")
    out = options({"wave": el.opts["wave"], "scale": el.opts.get("wave_scale", 1)})
    pos = el.opts.get("wave_pos", "auto")
    if pos not in POSITIONS:
        raise ValueError(f"wave_pos must be one of {POSITIONS}")
    return {**out, "pos": pos}


def parse_symbol(d) -> dict:
    if not isinstance(d, dict):
        raise ValueError("wave symbol must be an object")
    extra = d.keys() - {"at", "wave", "scale", "color", "on_wire"}
    if extra:
        raise ValueError(f"unknown wave symbol fields: {sorted(extra)}")
    p = d.get("at")
    if (not isinstance(p, (list, tuple)) or len(p) != 2
            or any(isinstance(v, bool) or not isinstance(v, (int, float))
                   or not math.isfinite(v) for v in p)):
        raise ValueError("wave symbol at must be [x, y] with finite coordinates")
    color = d.get("color", "blue")
    if not valid_ink(color):
        raise ValueError("wave symbol color must be an ink name or #rrggbb")
    # on_wire: the mark sits on a wire on purpose; the wire under it is hidden
    # in the drawing (never in the netlist) and may run through it
    on_wire = d.get("on_wire", False)
    if not isinstance(on_wire, bool):
        raise ValueError("wave symbol on_wire must be true or false")
    return {**options(d), "at": tuple(p), "color": color, "on_wire": on_wire}


def symbols(layout):
    """Keep indices stable for the editor even while invalid JSON is corrected."""
    raw = layout.extra.get("wave_symbols", [])
    if not isinstance(raw, list):
        return []
    out = []
    for d in raw:
        try:
            out.append(parse_symbol(d))
        except (ValueError, TypeError):
            out.append(None)
    return out


def problems(layout):
    for e in layout.elements:
        try:
            source_options(e)
        except (ValueError, TypeError) as exc:
            yield f"{e.id}: {exc}"
    raw = layout.extra.get("wave_symbols", [])
    if not isinstance(raw, list):
        yield "wave_symbols must be a list"
        return
    for i, d in enumerate(raw):
        try:
            parse_symbol(d)
        except (ValueError, TypeError) as exc:
            yield f"wave_symbols[{i}]: {exc}"
