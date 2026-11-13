"""Apply a drawing preset to source parts without changing electrical options."""
from copy import deepcopy

from .layout import Layout
from .schema import OPTS


def apply_style(source: Layout, style: str) -> Layout:
    field = OPTS["symbol_style"]
    if style not in field.values:
        raise ValueError(f"symbol_style: expected {field.accepted()}")
    raw = deepcopy(source.to_dict())

    def parts(items):
        for part in items:
            if part["kind"] not in field.kinds:
                continue
            opts = part.setdefault("opts", {})
            if style == field.default:
                opts.pop(field.name, None)
            else:
                opts[field.name] = style
            if not opts:
                part.pop("opts", None)

    parts(raw["elements"])
    if "modules" in raw:
        from .modules import _definitions
        spec = raw["modules"]
        parts(spec.get("parts", []))
        for name, original in _definitions(spec).items():
            definition = deepcopy(original)
            parts(definition["layout"]["elements"])
            if "partial" in definition:
                parts(definition["partial"]["layout"]["elements"])
            # A preset belongs to this drawing; never rewrite the shared library.
            if definition != original:
                spec.setdefault("definitions", {})[name] = definition
    return Layout.from_dict(raw)
