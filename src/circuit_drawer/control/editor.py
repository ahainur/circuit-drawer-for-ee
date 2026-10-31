"""Local editor for control diagrams: drag blocks and link corners, check
live, preview with LaTeX, export, save templates and snippets.

A page of its own (``static/editor.html``) served by a small standard-library
server, separate from the circuit editor.  The page edits the same JSON
table the command line uses; every change is judged by the same checker and
every export goes through ``render``, which refuses a failing table.
"""

from __future__ import annotations

import base64
import json
import shutil
import tempfile
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ..compile import FORMATS, CompileError
from ..ai.settings import SettingsRoutes, WebSettings
from ..geometry import region_label_box
from ..profiles import PROFILES, get_profile
from . import library
from ..regions import (BORDERS, CALLOUT_DASHES, COLOR_ALIASES, INKS, INK_ALIASES,
                       PALETTE, css_rgb, ink_rgb)
from .check import CONTACT_R, PT_CM, SUM_R, check, port_point
from .model import (BOX_TYPES, CIRCLE_TYPES, POLE, SIDES, TAG_FILLS, TAG_STYLE,
                    block_fill, fill_names, fill_options)
from .render import TEMPLATES, Rejected, available, bode, render, template

STATIC = Path(__file__).with_name("static")
MENU_STATIC = Path(__file__).parents[1] / "editor" / "static"
PREVIEW_DPI = 200
TEX_PREVIEW_DPI = 400  # one formula on its own is small; read it at twice the figure preview


def appearance_constants() -> dict:
    """UI values and aliases come from the same owners as the validator."""
    return {"fills": {k: block_fill(k) for k in fill_names()}, "fill_colors": fill_options(),
            "inks": INKS, "ink_aliases": INK_ALIASES, "color_aliases": COLOR_ALIASES,
            "palette": {k: css_rgb(k) for k in PALETTE},
            "palette_edges": {k: css_rgb(k, edge=True) for k in PALETTE},
            "borders": BORDERS, "dashes": CALLOUT_DASHES}


def _ink_css(value: str) -> str:
    return "#%02x%02x%02x" % ink_rgb(value)


def _r(v: float) -> float:
    return round(v, 4)


def _box(b, s: float) -> list[float]:
    return [_r(b.x0 / s), _r(b.y0 / s), _r(b.x1 / s), _r(b.y1 / s)]


def _profile(raw: dict, profile: str | None):
    return get_profile(profile or str(raw.get("profile") or "ieee-double"))


def analyse(raw: dict, profile: str | None = None) -> dict:
    """The checker's verdict plus everything the page draws, in layout units."""
    prof = _profile(raw, profile)
    rep = check(raw, prof)
    dia, g = rep.diagram, rep.geometry
    s = g.s
    blocks = []
    for b in dia.blocks:
        d: dict[str, Any] = {"id": b.id, "type": b.type, "box": _box(g.bodies[b.id], s)}
        if b.type in BOX_TYPES or b.type in CIRCLE_TYPES:
            d["ports"] = {side: list(map(_r, port_point(b, side, 0, s))) for side in SIDES}
        fill, border, ink = TAG_STYLE[b.style] if b.type == "tag" else ("white", "black", "black")
        d.update(fill=block_fill(b.fill or fill), border=_ink_css(b.border or border),
                 ink=_ink_css(b.color or ink))
        if b.type == "switch":
            d["ports"] = {POLE: list(b.at), **{k: list(p) for k, p in b.throws.items()}}
            d["on"] = b.on
        blocks.append(d)
    regions = []
    for r in dia.regions:
        lb = region_label_box(r, g.scale, prof)
        regions.append({"rect": [r.x0, r.y0, r.x1, r.y1], "fill": css_rgb(r.fill) if r.fill != "none"
                        else None, "border": r.border,
                        "edge": css_rgb(r.border_color or (r.fill if r.fill != "none" else "gray"),
                                        edge=True),
                        "label": r.label, "label_box": _box(lb, s) if lb else None})
    return {**rep.to_dict(), "blocks": blocks, "constants": appearance_constants(),
            "paths": [[list(map(_r, p)) for p in path] if path else None for path in g.paths],
            "dots": [list(map(_r, p)) for p in g.dots],
            "labels": [{"box": _box(lb.box, s), "text": lb.text, "owner": lb.owner,
                        "color": _ink_css(lb.color) if lb.color else None}
                       for lb in g.labels],
            "regions": regions}


def preview_png(raw: dict, profile: str | None, grid: bool, mono: bool,
                dpi: int = PREVIEW_DPI) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        res = render(raw, profile, tmp, "preview", ("pdf",), mono=mono, grid=grid)
        from ..compile import _tool
        _tool(["pdftocairo", "-png", "-singlefile", "-r", str(dpi),
               str(res.files["pdf"]), str(Path(tmp) / "preview")])
        return base64.b64encode((Path(tmp) / "preview.png").read_bytes()).decode()


def tex_preview(text: str, size_pt: float | None, profile: str | None) -> str:
    """One text typeset alone by the figure's own preamble and font, so the
    formula box in the page shows exactly what the export will print."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("没有可预览的文字")
    item: dict[str, Any] = {"at": [0, 0], "text": text}
    if size_pt is not None:
        item["size_pt"] = size_pt
    raw = {"kind": "control", "name": "formula", "texts": [item]}
    try:
        return preview_png(raw, profile, False, False, TEX_PREVIEW_DPI)
    except CompileError as exc:
        raise ValueError(f"LaTeX 排不出来，检查命令拼写和 $、{{}} 是否成对：{exc}") from exc


BODE_PROFILE = "ieee-single"   # the loop dialog previews and exports single-column plots


def _pdf_png(pdf: Path, dpi: int) -> str:
    from ..compile import _tool
    stem = pdf.with_suffix("")
    _tool(["pdftocairo", "-png", "-singlefile", "-r", str(dpi), str(pdf), str(stem)])
    return base64.b64encode(stem.with_suffix(".png").read_bytes()).decode()


def loop_report(raw: dict, profile: str | None, mono: bool = False) -> dict:
    """Margins and poles (``loop.analyse``) plus the Bode plot as a PNG."""
    from .loop import analyse as loop_analyse
    prof = _profile(raw, profile)
    rep = check(raw, prof)
    if not rep.ok:
        raise Rejected(rep)
    report = loop_analyse(rep)
    with tempfile.TemporaryDirectory() as tmp:
        res = bode(raw, BODE_PROFILE, tmp, "bode", ("pdf",), mono)
        return {"report": report, "png": _pdf_png(res.files["pdf"], PREVIEW_DPI)}


def loop_design(raw: dict, profile: str | None, req: dict) -> dict:
    """A compensator suggestion and the table with it written into loop.tf."""
    from .loop import design
    rep = check(raw, _profile(raw, profile))
    if not rep.ok:
        raise Rejected(rep)
    kind = req.get("type")
    res = design(rep, str(req.get("block") or ""), float(req.get("fc") or 0),
                 float(req.get("pm") or 60), int(kind) if kind else None,
                 req.get("break") or None)
    table = json.loads(json.dumps(raw))
    old = table["loop"].setdefault("tf", {}).get(res["block"])
    keep = {k: v for k, v in old.items() if k in ("delay", "port")} if isinstance(old, dict) else {}
    table["loop"]["tf"][res["block"]] = {**res["tf"], **keep}
    return {"design": res, "table": table}


class Editor:
    def __init__(self, path: Path, profile: str | None, out_dir: Path,
                 library_dir: Path | None = None, chat_factory=None,
                 provider: str | None = None, model: str | None = None, settings=None):
        self.path, self.profile, self.out_dir = path, profile, out_dir
        self.library_dir = library_dir     # tests point this at a temporary folder
        self.chat_factory, self.provider, self.model = chat_factory, provider, model
        self.web_settings = WebSettings(self, settings)
        self.jobs = {}

    def state(self) -> dict:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return {"table": raw, "file": str(self.path), "out_dir": str(self.out_dir),
                "profile": self.profile or raw.get("profile") or "ieee-double",
                "profiles": sorted(PROFILES), "formats": list(FORMATS),
                "constants": {"sum_r_cm": SUM_R, "contact_cm": CONTACT_R, "font_cm": 8 * PT_CM,
                              "tag_style": TAG_STYLE,
                              "tag_fills": {k: v[0] for k, v in TAG_FILLS.items()},
                              **appearance_constants()},
                **self.lists()}

    def lists(self) -> dict:
        return {"templates": available(self.library_dir),
                "snippets": library.all_snippets(self.library_dir)}

    def save(self, raw: dict) -> dict:
        self.path.write_text(json.dumps(raw, indent=1, ensure_ascii=False) + "\n",
                             encoding="utf-8")
        return {"saved": str(self.path)}

    def export(self, raw: dict, req: dict) -> dict:
        formats = req.get("formats") or list(FORMATS)
        if not isinstance(formats, list) or any(f not in FORMATS for f in formats):
            raise ValueError("请选择至少一种导出格式：PDF、SVG、PNG、TeX")
        out = Path(str(req.get("out_dir") or self.out_dir)).expanduser()
        out = out if out.is_absolute() else self.path.parent / out
        stem = self.path.stem
        with tempfile.TemporaryDirectory() as tmp:
            res = render(raw, req.get("profile") or self.profile, tmp, stem,
                         tuple(dict.fromkeys(formats)), mono=bool(req.get("mono")))
            out.mkdir(parents=True, exist_ok=True)
            files = []
            for src in res.files.values():
                shutil.copyfile(src, out / src.name)
                files.append(str(out / src.name))
        self.save(raw)
        return {"files": files, "warnings": res.report.warnings}

    def export_bode(self, raw: dict, req: dict) -> dict:
        formats = req.get("formats") or ["pdf", "png"]
        if not isinstance(formats, list) or any(f not in FORMATS for f in formats):
            raise ValueError("请选择至少一种导出格式：PDF、SVG、PNG、TeX")
        out = Path(str(req.get("out_dir") or self.out_dir)).expanduser()
        out = out if out.is_absolute() else self.path.parent / out
        res = bode(raw, BODE_PROFILE, out, f"{self.path.stem}_bode",
                   tuple(dict.fromkeys(formats)), bool(req.get("mono")))
        return {"files": [str(p) for p in res.files.values()]}

    def export_code(self, raw: dict, req: dict) -> dict:
        from .codegen import write_code
        out = Path(str(req.get("out_dir") or self.out_dir)).expanduser()
        out = out if out.is_absolute() else self.path.parent / out
        res = write_code(raw, out / f"{self.path.stem}_c", with_harness=True)
        return {"files": res["files"], "warnings": res["warnings"]}

    def handler(self):
        editor = self

        class H(SettingsRoutes, BaseHTTPRequestHandler):
            web_settings = editor.web_settings
            def log_message(self, *args):
                pass

            def _send(self, code: int, body: bytes, ctype: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _json(self, obj, code: int = 200) -> None:
                self._send(code, json.dumps(obj, ensure_ascii=False).encode(),
                           "application/json; charset=utf-8")

            def do_GET(self):
                route = AI_GET_ROUTES.get("/api/job/" if self.path.startswith("/api/job/") else self.path)
                if route:
                    try:
                        self._json(getattr(self, route)())
                    except (KeyError, ValueError, OSError) as exc:
                        self._json({"error": str(exc)}, 404)
                    return
                if self.path in ("/", "/index.html"):
                    self._send(200, (STATIC / "editor.html").read_bytes(),
                               "text/html; charset=utf-8")
                elif self.path in ("/menu.js", "/menu.css", "/ai-settings.js"):
                    self._send(200, (MENU_STATIC / self.path[1:]).read_bytes(),
                               "text/javascript; charset=utf-8" if self.path.endswith(".js") else "text/css; charset=utf-8")
                elif self.path == "/api/state":
                    self._json(editor.state())
                else:
                    self._send(404, b"not found", "text/plain")

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                route = ROUTES.get(self.path)
                if route is None:
                    self._send(404, b"not found", "text/plain")
                    return
                try:
                    req = json.loads(self.rfile.read(n) or b"{}")
                    self._json(getattr(self, route)(req))
                except Rejected as exc:
                    self._json({"error": "校验未通过，先修正标红的问题",
                                "errors": exc.report.errors}, 400)
                except (library.LibraryError, CompileError, KeyError, ValueError, OSError) as exc:
                    self._json({"error": str(exc)}, 400)

            def _check(self, req: dict) -> dict:
                return analyse(req["table"], req.get("profile") or editor.profile)

            def _save(self, req: dict) -> dict:
                return editor.save(req["table"])

            def _preview(self, req: dict) -> dict:
                return {"png": preview_png(req["table"], req.get("profile") or editor.profile,
                                           bool(req.get("grid")), bool(req.get("mono")))}

            def _export(self, req: dict) -> dict:
                return editor.export(req["table"], req)

            def _save_template(self, req: dict) -> dict:
                path = library.save_template(req["table"], req.get("name", ""),
                                             req.get("title", ""),
                                             req.get("profile") or editor.profile,
                                             editor.library_dir, set(TEMPLATES),
                                             bool(req.get("force")))
                return {"saved": str(path), **editor.lists()}

            def _save_snippet(self, req: dict) -> dict:
                path = library.save_snippet(req["table"], list(req.get("ids") or []),
                                            req.get("name", ""), req.get("title", ""),
                                            editor.library_dir, bool(req.get("force")))
                return {"saved": str(path), **editor.lists()}

            def _insert_snippet(self, req: dict) -> dict:
                part = library.load_snippet(req.get("name", ""), editor.library_dir)
                at = req.get("at") or library.below(req["table"], part)
                table, ids = library.insert(req["table"], part, (float(at[0]), float(at[1])))
                return {"table": table, "ids": ids}

            def _copy(self, req: dict) -> dict:
                part, corner = library.copy_part(req["table"], list(req.get("ids") or []),
                                                 [int(i) for i in req.get("texts") or []])
                return {"part": part, "corner": list(corner)}

            def _paste(self, req: dict) -> dict:
                at = req["at"]
                table, ids = library.insert(req["table"], req["part"], (float(at[0]), float(at[1])))
                return {"table": table, "ids": ids}

            def _tex_preview(self, req: dict) -> dict:
                return {"png": tex_preview(req.get("text", ""), req.get("size_pt"),
                                           req.get("profile") or editor.profile)}

            def _loop(self, req: dict) -> dict:
                return loop_report(req["table"], req.get("profile") or editor.profile,
                                   bool(req.get("mono")))

            def _design(self, req: dict) -> dict:
                return loop_design(req["table"], req.get("profile") or editor.profile, req)

            def _export_bode(self, req: dict) -> dict:
                return editor.export_bode(req["table"], req)

            def _export_code(self, req: dict) -> dict:
                return editor.export_code(req["table"], req)

            def _template(self, req: dict) -> dict:
                return {"table": template(req.get("name", ""), editor.library_dir)}

            def _ai_status(self) -> dict:
                from ..ai import provider_info
                return ({"provider": "scripted", "model": "-", "base_url": "-", "problem": None}
                        if editor.chat_factory else provider_info(editor.provider, editor.model))

            def _ai_job(self) -> dict:
                job = editor.jobs.get(self.path.rsplit("/", 1)[-1])
                if job is None:
                    raise ValueError("没有这个任务")
                return job.status()

            def _ai_generate(self, req: dict) -> dict:
                from ..ai import provider_info
                from ..editor.server import Job
                from .ai_structure import ControlWorkflow, to_structure
                if not isinstance(req, dict):
                    raise ValueError("AI 请求必须是对象")
                description = req.get("description")
                if not isinstance(description, str) or not description.strip():
                    raise ValueError("描述为空")
                if any(job.state == "running" for job in editor.jobs.values()):
                    raise ValueError("已有 AI 任务进行中，请先等待或取消")
                profile = req.get("profile") or editor.profile or "ieee-double"
                profile = get_profile(profile).name
                current = req.get("current")
                if current is not None:
                    if not isinstance(current, dict) or current.get("kind") != "control":
                        raise ValueError("当前图必须是控制框图")
                    to_structure(current, profile)
                chat = editor.chat_factory() if editor.chat_factory else None
                if chat is None:
                    info = provider_info(editor.provider, editor.model)
                    if info.get("problem"):
                        raise ValueError(info["problem"])
                requested_rounds = req.get("rounds", 3)
                if not isinstance(requested_rounds, int) or isinstance(requested_rounds, bool):
                    raise ValueError("rounds 必须是整数，最多 3 轮")
                rounds = max(1, min(3, requested_rounds))
                workflow = ControlWorkflow(profile, current, editor.library_dir)
                job = Job(description.strip(), profile, rounds, chat, editor.provider, editor.model,
                          "tweak" if current is not None else "draw", current=current, workflow=workflow)
                editor.jobs[job.id] = job
                return job.status()

            def _ai_cancel(self, req: dict) -> dict:
                job = editor.jobs.get(req.get("id", ""))
                if job:
                    job.cancel.set()
                return {"cancelled": bool(job)}

        return H


# POST path -> handler method; each takes the request body and returns the answer
ROUTES = {
    "/api/ai-settings": "_set_ai_settings",
    "/api/generate": "_ai_generate",
    "/api/cancel": "_ai_cancel",
    "/api/check": "_check",
    "/api/save": "_save",
    "/api/preview": "_preview",
    "/api/export": "_export",
    "/api/save-template": "_save_template",
    "/api/save-snippet": "_save_snippet",
    "/api/insert-snippet": "_insert_snippet",
    "/api/copy": "_copy",
    "/api/paste": "_paste",
    "/api/template": "_template",
    "/api/tex-preview": "_tex_preview",
    "/api/loop": "_loop",
    "/api/design": "_design",
    "/api/export-bode": "_export_bode",
    "/api/export-code": "_export_code",
}

AI_GET_ROUTES = {"/api/ai-status": "_ai_status", "/api/job/": "_ai_job", "/api/ai-settings": "_get_ai_settings"}


def serve(path: Path, profile: str | None = None, port: int = 8766,
          out_dir: Path | None = None, open_browser: bool = True) -> None:
    path = path.resolve()
    editor = Editor(path, profile, (out_dir or path.parent).resolve())
    from ..localserver import bind
    httpd = bind(port, editor.handler())
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"editing control diagram {path} at {url}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
