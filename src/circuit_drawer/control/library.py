"""User control-diagram templates and snippets.

A template is a whole table that passed the checker, saved under a name so
``export`` / ``render`` / the editor can start from it like the bundled
examples.  A snippet is part of a table (a few blocks, the links between
them, nearby text and regions), saved so it can be pasted into another
diagram, e.g. one PI loop.  Both are plain JSON in the user's template
folder (``$CIRCUIT_DRAWER_TEMPLATES/control`` or
``~/.circuit_drawer/templates/control``); pasting only shifts the stored
coordinates and renames clashing ids, it never places anything.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from ..profiles import get_profile
from ..templates.saved import user_dir as circuit_template_dir
from .check import check
from .model import KIND, parse_end

SNIPPET_KIND = "control-snippet"
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
TEXT_MARGIN = 0.5      # layout units around the chosen blocks within which texts come along
# Table sections holding one entry per block id.  Copying a block carries its
# entries along, so a pasted PI keeps its transfer function and a pasted Park
# block stays a function block; pasting follows the block's new id.
PER_BLOCK = (("functions",), ("loop", "tf"), ("code", "blocks"))


class LibraryError(ValueError):
    pass


def user_dir() -> Path:
    return circuit_template_dir() / "control"


def snippet_dir(directory: Path | None = None) -> Path:
    return (Path(directory) if directory else user_dir()) / "snippets"


def _name(name: str) -> str:
    name = (name or "").strip().lower()
    if not _NAME.match(name):
        raise LibraryError(f"名字 {name!r} 只能用小写字母、数字和下划线，并以字母开头")
    return name


def _write(path: Path, data: dict, force: bool) -> Path:
    if path.exists() and not force:
        raise LibraryError(f"{path.stem} 已存在：{path}（网页上确认覆盖即可；命令行请加 --force）")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


# ---- templates ---------------------------------------------------------------
def save_template(raw: dict, name: str, title: str = "", profile: str | None = None,
                  directory: Path | None = None, reserved=frozenset(),
                  force: bool = False) -> Path:
    """Write a table that passes the checker as a named template."""
    name = _name(name)
    if name in reserved:
        raise LibraryError(f"{name!r} 与内置示例重名")
    prof = get_profile(profile or str(raw.get("profile") or "ieee-double"))
    rep = check(raw, prof)
    if not rep.ok:
        raise LibraryError("未通过校验，不能存为模板：\n" + "\n".join(rep.errors))
    table = copy.deepcopy(raw)
    table["name"], table["title"] = name, title or table.get("title") or name
    folder = Path(directory) if directory else user_dir()
    return _write(folder / f"{name}.json", table, force)


def user_templates(directory: Path | None = None) -> dict[str, tuple[str, Path]]:
    """name -> (title, path) of the saved templates that read as tables."""
    folder = Path(directory) if directory else user_dir()
    out = {}
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and d.get("kind") == KIND:
            out[path.stem] = (str(d.get("title") or path.stem), path)
    return out


# ---- snippets ----------------------------------------------------------------
def _ref_block(v: Any) -> str | None:
    if isinstance(v, str):
        try:
            return parse_end(v, "end").block
        except ValueError:
            return None
    return None


def _bounds(blocks: list[dict]) -> tuple[float, float, float, float]:
    xs, ys = [], []
    for b in blocks:
        if b.get("type") == "switch":
            pts = [b["pole"], *b.get("throws", {}).values()]
        else:
            (x, y), (w, h) = b["at"], b.get("size") or (0.4, 0.4)
            pts = [(x - w / 2, y - h / 2), (x + w / 2, y + h / 2)]
        xs += [p[0] for p in pts]
        ys += [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def extract(raw: dict, ids: list[str]) -> dict:
    """The chosen blocks with the links that touch only them, the texts near
    them and the regions around them, moved so their corner is at (0, 0)."""
    part, corner = _cut(raw, ids, None, False)
    return shift(part, -corner[0], -corner[1])


def copy_part(raw: dict, ids: list[str], texts: list[int]) -> tuple[dict, tuple[float, float]]:
    """What the editor's copy takes: exactly the selected blocks and texts,
    their links, regions and per-block data, plus where its corner was."""
    part, corner = _cut(raw, ids, texts, True)
    return shift(part, -corner[0], -corner[1]), corner


def _cut(raw: dict, ids: list[str], texts: list[int] | None, data: bool):
    """``texts`` None: the texts near the blocks (a snippet); a list of
    indices: exactly those, which may stand alone.  ``data`` adds the blocks'
    ``PER_BLOCK`` entries."""
    chosen = [copy.deepcopy(b) for b in raw.get("blocks") or [] if b.get("id") in ids]
    all_texts = raw.get("texts") or []
    if texts is not None:
        picked = [copy.deepcopy(all_texts[i]) for i in sorted(set(texts)) if 0 <= i < len(all_texts)]
        if not chosen and not picked:
            raise LibraryError("先选中要复制的方块或文字")
    elif not chosen:
        raise LibraryError("先选中要保存的方块")
    keep = {b["id"] for b in chosen}
    links = []
    for lk in raw.get("links") or []:
        ends = [_ref_block(lk.get("from")), _ref_block(lk.get("to"))]
        refs = [e for e in ends if e is not None]
        if refs and all(e in keep for e in refs):
            links.append(copy.deepcopy(lk))
    if texts is None:
        x0, y0, x1, y1 = _bounds(chosen)
        m = TEXT_MARGIN
        picked = [copy.deepcopy(t) for t in all_texts
                  if x0 - m <= t["at"][0] <= x1 + m and y0 - m <= t["at"][1] <= y1 + m]
    # the blocks' corner, as snippets always had; texts alone use their own
    corner = (_bounds(chosen)[:2] if chosen else
              (min(t["at"][0] for t in picked), min(t["at"][1] for t in picked)))
    regions = []
    for r in raw.get("regions") or []:
        members = r.get("members") or []
        if members and set(members) <= keep:
            regions.append(copy.deepcopy(r))
    part = {"blocks": chosen, "links": _own_branches(chosen, links) if chosen else [],
            "texts": picked, "regions": regions}
    if data:
        part["data"] = {}
        for path in PER_BLOCK:
            section = _section(raw, path)
            entries = {k: copy.deepcopy(v) for k, v in section.items() if k in keep}
            if entries:
                part["data"][".".join(path)] = entries
    return part, corner


def _section(raw: dict, path: tuple[str, ...]) -> dict:
    """The per-block entries at ``path``; empty when the table has none."""
    node: Any = raw
    for key in path:
        node = node.get(key) if isinstance(node, dict) else None
    return node if isinstance(node, dict) else {}


def _own_branches(blocks: list[dict], links: list[dict]) -> list[dict]:
    """Drop links that branch off a line left behind: their bare start would
    dangle in the snippet."""
    from .check import _on_path, geometry
    from .model import parse_lenient
    dia, _ = parse_lenient({"kind": KIND, "blocks": blocks, "links": links})
    g = geometry(dia, 1.0, get_profile("ieee-double"))
    keep = []
    for i, (lk, path) in enumerate(zip(links, g.paths)):
        if isinstance(lk.get("from"), list) and path:
            others = [q for j, q in enumerate(g.paths) if j != i and q]
            if not any(_on_path(path[0], q) for q in others):
                continue
        keep.append(lk)
    return keep


def _move(p, dx: float, dy: float):
    return [round(p[0] + dx, 4), round(p[1] + dy, 4)]


def shift(part: dict, dx: float, dy: float) -> dict:
    """Every hand-written coordinate in a table or snippet moved by (dx, dy)."""
    part = copy.deepcopy(part)
    for b in part.get("blocks") or []:
        for key in ("at", "pole"):
            if key in b:
                b[key] = _move(b[key], dx, dy)
        if isinstance(b.get("throws"), dict):
            b["throws"] = {k: _move(p, dx, dy) for k, p in b["throws"].items()}
    for lk in part.get("links") or []:
        for key in ("from", "to"):
            if isinstance(lk.get(key), list):
                lk[key] = _move(lk[key], dx, dy)
        lk["via"] = [_move(p, dx, dy) for p in lk.get("via") or []]
        if not lk["via"]:
            del lk["via"]
    for t in part.get("texts") or []:
        t["at"] = _move(t["at"], dx, dy)
    for r in part.get("regions") or []:
        r["from"], r["to"] = _move(r["from"], dx, dy), _move(r["to"], dx, dy)
    return part


# Snippets that ship with the package: name -> (title, bundled example, block ids).
# They are cut from the example tables with ``extract`` when asked for, so the
# coordinates live in one place and a snippet always matches its example.
BUILTIN_SNIPPETS = {
    "foc_speed_pi": ("转速 PI 外环", "foc_pmsm", ["wref", "sw", "piw"]),
    "foc_dq_current_pi": ("dq 轴电流 PI 内环（含虚线框）", "foc_pmsm",
                          ["sq", "idref", "sd", "piq", "pid"]),
    "foc_ipark_svpwm": ("反 Park 变换 + SVPWM", "foc_pmsm", ["ipark", "thi", "vdc", "svpwm"]),
    "foc_clarke_park": ("Clarke + Park 变换（电流反馈）", "foc_pmsm", ["clarke", "park"]),
    "mpc_fcs_core": ("FCS-MPC：预测模型、代价函数、最优开关选择", "mpc_fcs",
                     ["iref", "extrap", "cost", "minim", "pred", "vv", "vdc"]),
}


def builtin_snippet(name: str) -> dict:
    from .render import template
    title, source, ids = BUILTIN_SNIPPETS[name]
    return {"kind": SNIPPET_KIND, "name": name, "title": title,
            **extract(template(source), ids)}


def save_snippet(raw: dict, ids: list[str], name: str, title: str = "",
                 directory: Path | None = None, force: bool = False) -> Path:
    name = _name(name)
    if name in BUILTIN_SNIPPETS:
        raise LibraryError(f"{name!r} 与内置片段重名")
    part = extract(raw, ids)
    data = {"kind": SNIPPET_KIND, "name": name, "title": title or name, **part}
    return _write(snippet_dir(directory) / f"{name}.json", data, force)


def snippets(directory: Path | None = None) -> dict[str, tuple[str, Path]]:
    folder = snippet_dir(directory)
    out = {}
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and d.get("kind") == SNIPPET_KIND:
            out[path.stem] = (str(d.get("title") or path.stem), path)
    return out


def all_snippets(directory: Path | None = None) -> dict[str, str]:
    """Bundled and saved snippets: name -> title, bundled ones first."""
    out = {n: f"{t}  [内置]" for n, (t, _, _) in BUILTIN_SNIPPETS.items()}
    for n, (t, _) in snippets(directory).items():
        out.setdefault(n, t)
    return out


def load_snippet(name: str, directory: Path | None = None) -> dict:
    if name in BUILTIN_SNIPPETS:
        return builtin_snippet(name)
    found = snippets(directory).get(name)
    if found is None:
        raise LibraryError(f"没有片段 {name!r}")
    return json.loads(found[1].read_text(encoding="utf-8"))


GAP_BELOW = 1.0        # layout units between a table and a snippet pasted under it


def below(raw: dict, part: dict) -> tuple[float, float]:
    """Where to paste a snippet so it does not widen the figure: under the
    table's lowest block or region, aligned with its left edge."""
    blocks = raw.get("blocks") or []
    ys = [_bounds([b])[1] for b in blocks] + [min(r["from"][1], r["to"][1])
                                              for r in raw.get("regions") or []]
    xs = [_bounds([b])[0] for b in blocks] + [min(r["from"][0], r["to"][0])
                                              for r in raw.get("regions") or []]
    if not ys:
        return (0.0, 0.0)
    height = _bounds(part["blocks"])[3] if part.get("blocks") else 0.0
    return (round(min(xs), 4), round(min(ys) - GAP_BELOW - height, 4))


def insert(raw: dict, part: dict, at: tuple[float, float]) -> tuple[dict, list[str]]:
    """``raw`` with the snippet pasted at ``at`` (its lower-left corner).
    Ids already in the table get a numeric suffix; links follow the renames."""
    raw = copy.deepcopy(raw)
    part = shift(part, at[0], at[1])
    taken = {b.get("id") for b in raw.get("blocks") or []}
    rename = {}
    for b in part["blocks"]:
        new, k = b["id"], 2
        while new in taken:
            new, k = f"{b['id']}_{k}", k + 1
        rename[b["id"]] = new
        taken.add(new)
        b["id"] = new

    def ref(v):
        blk = _ref_block(v)
        if blk is None or blk not in rename:
            return v
        return rename[blk] + v[len(blk):]

    for lk in part["links"]:
        lk["from"], lk["to"] = ref(lk["from"]), ref(lk["to"])
    for r in part["regions"]:
        if r.get("members"):
            r["members"] = [rename.get(m, m) for m in r["members"]]
    for key in ("blocks", "links", "texts", "regions"):
        if part.get(key):
            raw.setdefault(key, []).extend(part[key])
    for dotted, entries in (part.get("data") or {}).items():
        node = raw
        for key in dotted.split("."):
            node = node.setdefault(key, {})
        node.update({rename.get(k, k): v for k, v in entries.items()})
    return raw, [b["id"] for b in part["blocks"]]
