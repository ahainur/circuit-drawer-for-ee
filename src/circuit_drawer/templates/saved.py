"""Templates saved from a coordinate table (for example one a model wrote).

A saved template is a JSON file holding a validated table plus a name, title
and aliases.  It behaves like a hand-written template module: NAME, TITLE,
OPTIONS and ``build(options)``.  Its options are the generic ones that only
change symbols, never coordinates:

- ``switch=drawn|mosfet|igbt`` swaps every switch symbol in place
- ``currents=yes|no`` keeps or drops the current arrows

Files are read from the bundled ``saved_tables/`` folder and from the user folder
(``$CIRCUIT_DRAWER_TEMPLATES`` or ``~/.circuit_drawer/templates``).
"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path

from ..layout import PIN_NAMES, SWITCHES, Layout
from ..options import Option
from ..profiles import get_profile
from ..validate import check

BUNDLED = Path(__file__).with_name("saved_tables")
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_SWITCH_PINS = {k: PIN_NAMES[k] for k in SWITCHES}


def user_dir() -> Path:
    env = os.environ.get("CIRCUIT_DRAWER_TEMPLATES")
    return Path(env).expanduser() if env else Path.home() / ".circuit_drawer" / "templates"


def _swap_switches(d: dict, kind: str) -> None:
    for el in d["elements"]:
        if el["kind"] in _SWITCH_PINS and el["kind"] != kind:
            old, new = _SWITCH_PINS[el["kind"]], _SWITCH_PINS[kind]
            el["pins"] = {new[old.index(k)] if k in old else k: v
                          for k, v in el["pins"].items()}
            for o, n in zip(old, new):
                key = f"{el['id']}.{o}"
                if key in d.get("netlist", {}):
                    d["netlist"][f"{el['id']}.{n}"] = d["netlist"].pop(key)
            el["kind"] = kind


class SavedTemplate:
    def __init__(self, path: Path):
        data = json.loads(path.read_text(encoding="utf-8"))
        meta = data["template"]
        self.path = path
        self.NAME = meta["name"]
        self.TITLE = meta.get("title", self.NAME)
        self.ALIASES = tuple(meta.get("aliases", ()))
        self.DESCRIPTION = meta.get("description", "")
        self.DEFAULT_PROFILE = get_profile(
            meta.get("default_profile", meta.get("checked_profile", "ieee-single"))).name
        self.table = data["layout"]
        kinds = {e["kind"] for e in self.table["elements"]}
        has_arrows = any(a.get("arrow") for a in self.table.get("annotations", [])) or \
            any("current" in e.get("opts", {}) for e in self.table["elements"])
        self.OPTIONS: list[Option] = []
        if kinds & set(_SWITCH_PINS):
            self.OPTIONS.append(Option(
                "switch", "drawn", ("drawn", "mosfet", "igbt"),
                "switch symbol (drawn = as saved)",
                {"mosfet": "mosfet", "mos": "mosfet", "igbt": "igbt"}))
        if has_arrows:
            self.OPTIONS.append(Option(
                "currents", True, None, "keep the current reference arrows",
                {"currents": True, "电流": True, "无电流": False, "nocurrents": False}))

    def build(self, o: dict) -> Layout:
        d = copy.deepcopy(self.table)
        if o.get("switch", "drawn") != "drawn":
            _swap_switches(d, "nmos" if o["switch"] == "mosfet" else "igbt")
        if o.get("currents", True) is False:
            d["annotations"] = [a for a in d.get("annotations", []) if not a.get("arrow")]
            for el in d["elements"]:
                el.get("opts", {}).pop("current", None)
        d["name"] = self.NAME
        return Layout.from_dict(d)


def load_all() -> dict[str, SavedTemplate]:
    """User files override bundled ones of the same name."""
    found: dict[str, SavedTemplate] = {}
    for folder in (BUNDLED, user_dir()):
        if folder.is_dir():
            for f in sorted(folder.glob("*.json")):
                try:
                    t = SavedTemplate(f)
                except (OSError, ValueError, KeyError, TypeError):
                    continue    # a broken file must not break the built-ins
                found[t.NAME] = t
    return found


class TemplateError(ValueError):
    pass


def save_template(layout: Layout, name: str, title: str = "",
                  aliases: tuple[str, ...] = (), description: str = "",
                  profile: str = "ieee-single", directory: Path | None = None,
                  reserved: set[str] = frozenset(), force: bool = False) -> Path:
    """Write a validated table as a template.  Refuses tables that fail the
    validator for ``profile`` and names taken by hand-written templates."""
    name = name.strip().lower()
    if not _NAME.match(name):
        raise TemplateError(f"模板名 {name!r} 只能用小写字母、数字和下划线，并以字母开头")
    if name in reserved or set(a.lower() for a in aliases) & reserved:
        raise TemplateError(f"{name!r} 或其别名与内置模板重名")
    rep = check(layout, get_profile(profile))
    if not rep.ok:
        raise TemplateError("坐标表未通过校验，不能存为模板：\n" +
                            "\n".join(map(str, rep.errors)))
    folder = Path(directory) if directory else user_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.json"
    if path.exists() and not force:
        raise TemplateError(f"模板 {name} 已存在：{path}（覆盖请加 --force）")
    table = layout.to_dict()
    table["name"] = name
    meta = {"name": name, "title": title or name, "aliases": list(aliases),
            "description": description, "checked_profile": get_profile(profile).name}
    path.write_text(json.dumps({"template": meta, "layout": table}, indent=1,
                               ensure_ascii=False) + "\n", encoding="utf-8")
    return path
