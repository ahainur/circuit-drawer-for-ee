"""Parameterised requirements, not pre-generated drawings or executable templates."""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import uuid

from .settings import CONFIG_LOCK

# Bounds protect the local editor from accidentally saving giant pasted documents.
MAX_TEXT = 12000
MAX_SAVED = 200


def _text(value, limit=MAX_TEXT):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"文本应不超过 {limit} 个字符")
    return value.strip()


def user_path():
    from ..launcher import settings_path
    return settings_path().with_name("prompt-library.json")


def _read(path):
    if not path.exists():
        return {"revision": 0, "items": []}
    if path.stat().st_size > MAX_SAVED * (MAX_TEXT + 2000):
        raise ValueError("个人提示词库过大；未修改原文件")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or type(data.get("revision")) is not int or not isinstance(data.get("items"), list):
        raise ValueError("个人提示词库格式无效；请保留原文件并修复后重试")
    if len(data["items"]) > MAX_SAVED or any(
        not isinstance(item, dict) or not isinstance(item.get("id"), str)
        or not item["id"].startswith("user_") or item.get("personal") is not True
        or not isinstance(item.get("body"), str) or len(item["body"]) > MAX_TEXT
        or not isinstance(item.get("title"), dict) or item.get("fields") != []
        or item.get("mode") not in ("create", "edit")
        or item.get("domains") not in (["circuit"], ["control"])
        for item in data["items"]
    ):
        raise ValueError("个人提示词库条目无效；未修改原文件")
    return data


@contextmanager
def _file_lock(path):
    # Editors can run in separate processes. Lock revision read + atomic replace.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a+b") as lock:
        lock.seek(0, 2)
        if lock.tell() == 0:
            lock.write(b"0"); lock.flush()
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def builtin():
    return json.loads(Path(__file__).with_name("presets").joinpath("builtin.json").read_text(encoding="utf-8"))


def explicit_context(table):
    """Return unambiguous explicit values only. Never infer a design from labels."""
    if not isinstance(table, dict):
        return {"values": {}, "sources": {}, "objects": [], "candidates": {}}
    values, sources = {}, {}
    loop = table.get("loop", {})
    params = loop.get("params", {}) if isinstance(loop, dict) else {}
    if not isinstance(params, dict):
        params = {}
    for section in ("blocks", "elements"):
        if not isinstance(table.get(section, []), list):
            raise ValueError("当前图对象列表格式无效")
    aliases = {"vin": ("Vin",), "vout": ("Vo", "Vout"), "inductance": ("L",),
               "capacitance": ("C",), "load": ("Rload",), "kp": ("Kp", "kp"),
               "ki": ("Ki", "ki"), "ratio": ("ratio",), "frequency": ("fsw",)}
    for key, names in aliases.items():
        candidates = [(n, params[n]) for n in names if n in params and isinstance(params[n], (str, int, float)) and not isinstance(params[n], bool)]
        if len(candidates) == 1:
            name, value = candidates[0]
            values[key], sources[key] = str(value), "loop.params." + name
    code = table.get("code", {})
    if isinstance(code, dict) and isinstance(code.get("fs"), (int, float)):
        values["sample_rate"], sources["sample_rate"] = str(code["fs"]), "code.fs"
    objects = [str(b["id"]) for section in ("blocks", "elements") for b in table.get(section, [])
               if isinstance(b, dict) and isinstance(b.get("id"), str)]
    # Distinguish sample rate from switching rate. Conflicting switch rates stay empty.
    from ..spice import PWM_SWITCHES
    switches = []
    candidates = {}
    simulation = table.get("simulation", {})
    components = simulation.get("components", {}) if isinstance(simulation, dict) else {}
    if not isinstance(components, dict):
        components = {}
    kinds = {b.get("id"): b.get("kind") for b in table.get("elements", []) if isinstance(b, dict)}
    for identifier, params in components.items():
        if not isinstance(params, dict):
            continue
        kind = kinds.get(identifier)
        key, field = ({"V": ("vin", "dc"), "L": ("inductance", "value"),
                       "C": ("capacitance", "value"), "R": ("load", "value"),
                       "transformer": ("ratio", "ratio")}.get(kind, (None, None)))
        if key and isinstance(params.get(field), (str, int, float)):
            candidates.setdefault(key, []).append({"value": str(params[field]), "source": f"simulation.components.{identifier}.{field}"})
        if kind in PWM_SWITCHES and isinstance(params.get("frequency"), (str, int, float)):
            switches.append((identifier, str(params["frequency"])))
    if switches and len({v for _, v in switches}) == 1 and "frequency" not in values:
        values["frequency"] = switches[0][1]
        sources["frequency"] = ", ".join(f"simulation.components.{id}.frequency" for id, _ in switches)
    probes = simulation.get("probes", []) if isinstance(simulation, dict) else []
    if isinstance(probes, list):
        objects.extend(str(p["name"]) for p in probes if isinstance(p, dict) and isinstance(p.get("name"), str))
    candidates["target"] = [{"value": name, "source": "current drawing"} for name in objects]
    return {"values": values, "sources": sources, "objects": objects, "candidates": candidates}


def _entry(items, identifier):
    return next((x for x in items if x["id"] == identifier), None)


def render(entry, values, domain):
    if not isinstance(values, dict):
        raise ValueError("参数必须是对象")
    if entry.get("personal"):
        return {"text": entry["body"], "missing": [], "values": {}}
    fields = entry.get("fields", [])
    unknown = set(values) - {f["key"] for f in fields}
    if unknown:
        raise ValueError("未知参数：" + ", ".join(sorted(unknown)))
    chosen, missing, lines = {}, [], []
    for field in fields:
        key = field["key"]
        value = _text(str(values.get(key, "")), 500)
        chosen[key] = value
        if not value:
            missing.append(key)
        lines.append(f"{field['label']['zh-CN']}：{value or '待填写（保留符号，勿猜测数值）'} {field.get('unit', '')}".rstrip())
    text = entry["body"] + ("\n" + "\n".join(lines) if lines else "")
    if domain == "control":
        text += "\n本窗口只生成控制结构；功率级为明确的外部接口，不把电力电路当控制方块伪造。"
    else:
        text += "\n本窗口生成电路；配套控制图需通过控制编辑器生成并明确绑定，不把控制框图画成电路元件。"
    text += "\n未提供的设计值保持符号并列为待填写；数值分析或原生求解前必须补齐并校验，不声称已经整定或仿真通过。"
    return {"text": text, "missing": missing, "values": chosen}


def request(req, path=None):
    """Shared route for both editors; the caller cannot choose a filesystem path."""
    if not isinstance(req, dict):
        raise ValueError("提示词请求必须是对象")
    action = req.get("action", "list")
    domain = req.get("domain", "circuit")
    if domain not in ("circuit", "control"):
        raise ValueError("未知编辑器类型")
    path = Path(path) if path else user_path()
    with CONFIG_LOCK, _file_lock(path):
        saved = _read(path)
        entries = builtin() + saved["items"]
        if action == "list":
            query = _text(req.get("query", ""), 200).casefold()
            mode = req.get("mode")
            items = [deepcopy(x) for x in entries if domain in x["domains"] and (not mode or x["mode"] == mode)
                     and (not query or query in json.dumps(x, ensure_ascii=False).casefold())]
            return {"items": items, "revision": saved["revision"], "context": explicit_context(req.get("table"))}
        if action == "render":
            entry = _entry(entries, req.get("id"))
            if entry is None or domain not in entry["domains"]:
                raise ValueError("提示词不存在，请刷新后重试")
            return render(entry, req.get("values", {}), domain)
        if action not in ("save", "delete"):
            raise ValueError("未知提示词操作")
        if type(req.get("revision")) is not int or req["revision"] != saved["revision"]:
            raise ValueError("提示词库已变化，请刷新后重试；未覆盖已有内容")
        identifier = req.get("id")
        old = _entry(saved["items"], identifier)
        if identifier is not None and old is None:
            raise ValueError("只能修改个人提示词，内置条目不可覆盖")
        if action == "delete":
            if old is None:
                raise ValueError("个人提示词不存在")
            saved["items"].remove(old)
        else:
            title, body = _text(req.get("title"), 120), _text(req.get("text"))
            if not title or not body:
                raise ValueError("请填写名称和需求文字")
            if old is None and len(saved["items"]) >= MAX_SAVED:
                raise ValueError("个人提示词已达上限，请先删除不再使用的条目")
            mode = req.get("mode", "create")
            if mode not in ("create", "edit"):
                raise ValueError("未知需求类型")
            # Save the actual editable requirement, including filled parameters.
            item = {"id": identifier or "user_" + uuid.uuid4().hex, "title": {k: title for k in ("zh-CN", "zh-TW", "en")},
                    "body": body, "mode": mode, "domains": [domain], "fields": [], "personal": True,
                    "capability": {k: "" for k in ("zh-CN", "zh-TW", "en")}}
            if old:
                saved["items"][saved["items"].index(old)] = item
            else:
                saved["items"].append(item)
            identifier = item["id"]
        saved["revision"] += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="prompt-library-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump(saved, out, ensure_ascii=False, indent=2)
                out.write("\n")
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        return {"id": identifier, "revision": saved["revision"]}
