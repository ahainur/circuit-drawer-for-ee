"""Text/form options: the user describes only how a figure differs from the
template's standard drawing, e.g. ``"switch=igbt, 全桥整流, currents"``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Option:
    name: str
    default: Any
    choices: tuple[Any, ...] | None = None   # None means free text
    help: str = ""
    # keyword -> value, so "igbt" alone means switch=igbt
    keywords: dict[str, Any] = field(default_factory=dict)


class OptionError(ValueError):
    pass


_SPLIT = re.compile(r"[,，;；\s]+")
_TRUE = {"1", "true", "yes", "on", "是", "开"}
_FALSE = {"0", "false", "no", "off", "否", "关"}


def _coerce(opt: Option, raw: str) -> Any:
    if isinstance(opt.default, bool):
        low = raw.lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
        raise OptionError(f"{opt.name} expects yes/no, got {raw!r}")
    if opt.choices is not None:
        norm = raw.lower().replace("-", "_")
        for c in opt.choices:
            if str(c).lower() == norm:
                return c
        if raw in opt.keywords:
            return opt.keywords[raw]
        raise OptionError(f"{opt.name} must be one of {list(opt.choices)}, got {raw!r}")
    return raw


def parse(text: str | None, spec: list[Option],
          given: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merge defaults, ``key=value``/keyword text, and an explicit dict."""
    by_name = {o.name: o for o in spec}
    values = {o.name: o.default for o in spec}
    keywords = {k.lower(): (o.name, v) for o in spec for k, v in o.keywords.items()}
    for tok in _SPLIT.split(text or ""):
        if not tok:
            continue
        if "=" in tok:
            key, raw = tok.split("=", 1)
            if key not in by_name:
                raise OptionError(f"unknown option {key!r}; known: {sorted(by_name)}")
            values[key] = _coerce(by_name[key], raw)
        elif tok.lower() in keywords:
            name, val = keywords[tok.lower()]
            values[name] = val
        else:
            known = sorted(keywords)
            raise OptionError(f"cannot understand {tok!r}; use key=value or one of "
                              f"{known}")
    for key, raw in (given or {}).items():
        if key not in by_name:
            raise OptionError(f"unknown option {key!r}; known: {sorted(by_name)}")
        values[key] = raw if not isinstance(raw, str) else _coerce(by_name[key], raw)
    return values
