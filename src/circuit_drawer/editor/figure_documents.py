"""Open project-owned algorithm drawings using the existing control editor."""
import json
import re
import threading

from ..control.algorithm import blank

_LOCK = threading.RLock()


def request(editor, req):
    if not isinstance(req, dict):
        raise ValueError("算法图请求需要是对象")
    root = editor.path.resolve().parent
    if req.get("action") == "list":
        files = []
        for path in sorted(root.glob("*.algorithm.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if raw.get("kind") == "control" and raw.get("algorithm") is True:
                    files.append(path.name)
            except (OSError, ValueError, AttributeError):
                continue
        return {"files": files}
    name = req.get("name")
    if not isinstance(name, str) or not name.strip() or not re.fullmatch(r"[\w -]{1,80}", name):
        raise ValueError("请使用1到80个汉字、字母、数字、空格或下划线命名")
    path = root / (name.strip() + ".algorithm.json")
    if not path.resolve().is_relative_to(root):
        raise ValueError("算法图需要位于当前项目目录")
    with _LOCK:
        if req.get("action") == "create":
            with path.open("x", encoding="utf-8") as stream:
                json.dump(blank(name.strip()), stream, ensure_ascii=False, indent=1)
                stream.write("\n")
        elif req.get("action") != "open":
            raise ValueError("未知算法图操作")
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("kind") != "control" or raw.get("algorithm") is not True:
            raise ValueError("请选择算法流程图")
        from ..launcher import Session
        sessions = getattr(editor, "figure_documents", {})
        if path not in sessions:
            sessions[path] = Session(path, raw.get("profile") or "ieee-single",
                                     settings=editor.web_settings.settings)
            sessions[path].editor.provider = editor.provider
            sessions[path].editor.model = editor.model
            sessions[path].editor.chat_factory = editor.chat_factory
            editor.figure_documents = sessions
        return {"file": str(path), "url": sessions[path].url}


def close(editor):
    with _LOCK:
        sessions = list(getattr(editor, "figure_documents", {}).values())
        editor.figure_documents = {}
    for session in sessions:
        session.close()
