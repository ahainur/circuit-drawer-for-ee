"""Module libraries: the modules bundled with the program and the user's own.

A library entry is one module definition (the same object a table keeps in
``modules.definitions``) saved under a name. Placing an entry copies its
definition into the table and records where it came from (``source``), so a
saved table never depends on a library: it opens and simulates on another
computer. A later change in the library is only offered as an update; it
never edits a table by itself.

User entries are JSON files in the user's template folder
(``$CIRCUIT_DRAWER_TEMPLATES/modules`` or ``~/.circuit_drawer/templates/modules``),
next to the circuit and control templates.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from .layout import Layout
from .modules import LIBRARY, ModuleError, _name, library_names
from .templates.saved import user_dir as template_dir

KIND = "circuit-module"
FORMAT = 1
LIBRARIES = ("builtin", "user")
# names shown for the bundled modules (their tables keep the short ids), and
# the longer description the panel shows on hover
BUILTIN_TITLES = {"buck": "Buck", "boost": "Boost", "dab": "DAB", "llc_halfbridge": "LLC 半桥"}
BUILTIN_NOTES = {"buck": "降压变换器", "boost": "升压变换器", "dab": "双有源桥（两个全桥 + 变压器）",
                 "llc_halfbridge": "半桥 LLC 谐振变换器（含整流）"}


def user_module_dir() -> Path:
    return template_dir() / "modules"


def _canonical(value):
    """The value as JSON means it: a whole-valued float is that integer, since
    a trip through the browser writes 2.0 as 2 without changing anything."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_canonical(v) for v in value]
    return value


def revision(definition: dict) -> str:
    """A short fingerprint of a definition's content, its ``source`` left out:
    equal fingerprints mean the same circuit, ports, defaults and look."""
    body = _canonical({k: v for k, v in definition.items() if k != "source"})
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]


def _user_entry(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("kind") != KIND or not isinstance(data.get("definition"), dict):
        raise ModuleError(f"{path} 不是模块库条目")
    return data


def library_definition(library: str, name: str) -> dict:
    """A fresh copy of a library entry's definition."""
    if library == "builtin":
        if name not in library_names():
            raise ModuleError(f"内置模块库里没有 {name}；可选 {', '.join(library_names())}")
        return json.loads((LIBRARY / f"{name}.json").read_text(encoding="utf-8"))
    if library == "user":
        _name(name, "模块库名")
        path = user_module_dir() / f"{name}.json"
        if not path.exists():
            raise ModuleError(f"我的模块库里没有 {name}")
        return _user_entry(path)["definition"]
    raise ModuleError(f"模块库只有 {' / '.join(LIBRARIES)}")


def _box(definition: dict) -> list | None:
    """The drawing's extent in its own coordinates (pins and wires): placed
    at a point, an instance covers that point plus this box."""
    drawing = definition.get("layout") or {}
    pts = [p for e in drawing.get("elements", []) for p in (e.get("pins") or {}).values()]
    pts += [p for w in drawing.get("wires", []) for p in w]
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


def _summary(library: str, name: str, definition: dict, title: str | None = None) -> dict:
    layout = definition.get("port_layout") or {}
    return {"library": library, "name": name, "title": title or definition.get("title") or name,
            "ports": list(definition.get("ports", {})),
            "sides": {p: (layout.get(p) or {}).get("side") for p in definition.get("ports", {})},
            "symbol": definition.get("symbol"), "revision": revision(definition), "box": _box(definition)}


def catalogue(lay: Layout | None = None) -> dict:
    """Everything the component panel lists: the bundled modules, the user's
    library (a broken file is skipped, never fatal) and this table's own
    definitions, each with whether its library has changed since."""
    builtin = [{**_summary("builtin", n, library_definition("builtin", n), BUILTIN_TITLES.get(n)), "note": BUILTIN_NOTES.get(n, "")}
               for n in library_names()]
    user, folder = [], user_module_dir()
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            data = _user_entry(path)
            user.append({**_summary("user", path.stem, data["definition"], data.get("title")), "saved": data.get("saved")})
        except (ValueError, KeyError, ModuleError):
            continue
    project = []
    spec = lay.extra.get("modules") if lay is not None else None
    for name, definition in ((spec or {}).get("definitions") or {}).items():
        if not isinstance(definition, dict):
            continue
        entry = _summary("project", name, definition)
        source = definition.get("source")
        if isinstance(source, dict):
            try:
                current = revision(library_definition(source.get("library"), source.get("name")))
            except ModuleError:
                current = None          # removed from the library: the table's copy still works
            entry.update(source=source, update=current is not None and current != source.get("revision"),
                         edited=revision(definition) != source.get("revision"))
        project.append(entry)
    return {"builtin": builtin, "user": user, "project": project, "folder": str(folder)}


def save_to_user(lay: Layout, definition_name: str, library_name: str | None = None,
                 force: bool = False) -> tuple[Path, Layout]:
    """Save one of this table's definitions into the user's library. The
    table's copy then records the entry it matches."""
    definitions = (lay.extra.get("modules") or {}).get("definitions") or {}
    if definition_name not in definitions:
        raise ModuleError(f"本文件没有模块定义 {definition_name}（内置模块本来就在库里）")
    name = _name(library_name or definition_name, "模块库名")
    definition = deepcopy(definitions[definition_name])
    definition.pop("source", None)
    path = user_module_dir() / f"{name}.json"
    if path.exists() and not force:
        raise ModuleError(f"我的模块库里已有 {name}；确认覆盖后再存")
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"kind": KIND, "format": FORMAT, "name": name, "title": definition.get("title") or name,
             "saved": datetime.now(timezone.utc).isoformat(timespec="seconds"), "definition": definition}
    path.write_text(json.dumps(entry, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    raw = deepcopy(lay.to_dict())
    raw["modules"]["definitions"][definition_name]["source"] = {"library": "user", "name": name,
                                                               "revision": revision(definition)}
    return path, Layout.from_dict(raw)


def delete_from_user(name: str) -> None:
    path = user_module_dir() / f"{_name(name, '模块库名')}.json"
    if not path.exists():
        raise ModuleError(f"我的模块库里没有 {name}")
    path.unlink()


def place(lay: Layout, library: str, name: str, at, connections: dict,
          profile: str = "double") -> tuple[Layout, str]:
    """Copy a library module into the table (once per revision) and place an
    instance of it with the given port connections."""
    from .module_authoring import add_instance
    definition = deepcopy(library_definition(library, name))
    definition.pop("source", None)
    rev = revision(definition)
    raw = deepcopy(lay.to_dict())
    spec = raw.setdefault("modules", {"version": 1})
    if spec.get("instances") and "links" not in spec:
        raise ModuleError("这张图的模块按网络名连接（旧写法），请先点“转换为端口连接”再从模块库放置")
    spec.setdefault("instances", [])
    spec.setdefault("links", [])
    if raw.get("modes"):
        # as when saving a selection as a module: operating modes cannot cross
        # module ports yet, so they wait here and return when the last
        # instance is dissolved
        author = raw.setdefault("module_authoring", {"aliases": {}, "order": [e["id"] for e in raw["elements"]],
                                                     "terminals": []})
        author["modes"] = raw.pop("modes")
    definitions = spec.setdefault("definitions", {})
    target = next((d for d, existing in definitions.items()
                   if isinstance(existing, dict) and existing.get("source") == {"library": library, "name": name, "revision": rev}
                   and revision(existing) == rev), None)
    if target is None:
        target, n = name, 2
        while target in definitions:
            target, n = f"{name}_{n}", n + 1
        definitions[target] = {**definition, "source": {"library": library, "name": name, "revision": rev}}
    return add_instance(Layout.from_dict(raw), target, at, connections, profile), target


def update_from_library(lay: Layout, definition_name: str, profile: str = "double") -> Layout:
    """Replace this table's copy with its library entry's current content;
    every instance of it follows, as after editing the definition."""
    from .module_authoring import update_definition
    definition = ((lay.extra.get("modules") or {}).get("definitions") or {}).get(definition_name)
    source = definition.get("source") if isinstance(definition, dict) else None
    if not isinstance(source, dict):
        raise ModuleError(f"模块 {definition_name} 不是从模块库放进来的")
    fresh = deepcopy(library_definition(source["library"], source["name"]))
    fresh.pop("source", None)
    if set(fresh["ports"]) != set(definition["ports"]):
        raise ModuleError("模块库里的新版本端口名不同，已有实例无法直接更新；请另放一个新实例")
    fresh["source"] = {**source, "revision": revision(fresh)}
    return update_definition(lay, definition_name, fresh, profile)


def check_source(source, where: str) -> None:
    """``source`` on a definition: where it was copied from."""
    if not isinstance(source, dict) or set(source) != {"library", "name", "revision"} \
            or source["library"] not in LIBRARIES or not all(isinstance(v, str) and v for v in source.values()):
        raise ModuleError(f"{where}.source 须为 {{library: builtin / user, name, revision}}")
