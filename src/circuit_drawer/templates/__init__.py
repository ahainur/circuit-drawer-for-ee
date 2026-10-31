"""Template registry.  A template is a module with NAME, TITLE, OPTIONS and
``build(options) -> Layout``; its coordinates are written by hand.  Saved
templates (JSON tables, see ``saved.py``) join the registry with the same
interface and are re-read on every lookup, so a newly saved one is usable
at once."""

from __future__ import annotations

import re
from types import ModuleType

from . import dab, flyback, llc_fullbridge, vienna, zsource
from .saved import SavedTemplate, TemplateError, load_all, save_template

TEMPLATES: dict[str, ModuleType] = {m.NAME: m for m in (llc_fullbridge, dab, flyback, zsource, vienna)}
ALIASES = {"llc": "llc_fullbridge", "全桥llc": "llc_fullbridge", "反激": "flyback",
           "zsource": "zsource_inverter", "z源": "zsource_inverter",
           "z源逆变器": "zsource_inverter", "阻抗源逆变器": "zsource_inverter",
           "阻抗源网络三相逆变器": "zsource_inverter",
           "vienna": "vienna_rectifier", "维也纳": "vienna_rectifier",
           "维也纳整流": "vienna_rectifier", "维也纳整流器": "vienna_rectifier",
           "viennapfc": "vienna_rectifier", "维也纳pfc": "vienna_rectifier"}
RESERVED = set(TEMPLATES) | set(ALIASES)


def available() -> dict[str, ModuleType | SavedTemplate]:
    out: dict[str, ModuleType | SavedTemplate] = {
        k: v for k, v in load_all().items() if k not in RESERVED}
    out.update(TEMPLATES)
    return out


def get(name: str) -> ModuleType | SavedTemplate:
    key = ALIASES.get(name.lower(), name.lower())
    if key in TEMPLATES:
        return TEMPLATES[key]
    saved = available()
    if key in saved:
        return saved[key]
    for t in saved.values():
        if name.lower() in (a.lower() for a in getattr(t, "ALIASES", ())):
            return t
    raise KeyError(f"unknown template {name!r}; available: {sorted(saved)}")


# Words that make a description a different circuit from a plain template:
# "四开关 Buck-Boost" is not the Boost template, "三电平 DAB" is not the DAB one.
VARIANTS = ("四开关", "four-switch", "交错", "interleav", "两相", "多相", "multiphase", "无桥", "bridgeless",
            "图腾柱", "totem", "三电平", "three-level", "3-level", "3level", "anpc", "有源中点", "反相", "inverting",
            "同步整流", "synchronous", "隔离", "isolated")


def match(description: str) -> str | None:
    """A template whose name or alias appears in a free-text description,
    longest match first, so "阻抗源网络三相逆变器" finds zsource_inverter.
    An ASCII name must stand as a word ("buck-boost" holds no "boost"), and a
    variant word the template's own names do not carry rules the match out."""
    # names are compared without spaces; a removed space still separates words
    text, cuts = "", set()
    for ch in description.lower():
        if ch == " ":
            cuts.add(len(text))
        else:
            text += ch
    names = [(k, v) for k, v in ALIASES.items()] + [(k, k) for k in TEMPLATES]
    for t in available().values():
        names += [(a.lower(), t.NAME) for a in getattr(t, "ALIASES", ())]
        names.append((t.NAME, t.NAME))
    names = [(k.lower().replace(" ", ""), v) for k, v in names]

    def found(k):
        if not k.isascii():
            return k in text
        word = re.compile(r"[a-z0-9\-]")
        for m in re.finditer(re.escape(k), text):
            a, b = m.start(), m.end()
            if (a == 0 or a in cuts or not word.match(text[a - 1])) and \
               (b == len(text) or b in cuts or not word.match(text[b])):
                return True
        return False

    hits = sorted(((len(k), v) for k, v in names
                   if (len(k) >= 3 or not k.isascii()) and len(k) >= 2 and found(k)),
                  reverse=True)
    if not hits:
        return None
    hit = hits[0][1]
    own = " ".join(k for k, v in names if v == hit) + " " + str(getattr(get(hit), "TITLE", "")).lower().replace(" ", "")
    if any(w in text and w not in own for w in VARIANTS):
        return None
    return hit


def save(layout, name: str, **kw):
    return save_template(layout, name, reserved=RESERVED, **kw)


__all__ = ["TEMPLATES", "ALIASES", "available", "get", "match", "save", "SavedTemplate",
           "TemplateError"]
