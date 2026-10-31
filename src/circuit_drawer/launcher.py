"""Desktop entry point: run local editors without a terminal window.

The Windows gui_scripts executable uses the installed Python environment.
Settings belong to the user's app-data directory, never the circuit/repository.
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import json
import os
import socket
from pathlib import Path
import threading
import webbrowser
from http.server import ThreadingHTTPServer

from .localserver import LocalServer
from .editor.server import Editor
from .layout import Layout
from .pipeline import from_template
from . import templates
from .ai.settings import locked, validate_url, environment_key


def project_directory() -> Path:
    candidate = Path(__file__).resolve().parents[2]
    return candidate if (candidate / "pyproject.toml").is_file() else Path.cwd()


def settings_path() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".config"))) / "CircuitDrawer" / "launcher.json"


def protect(raw: bytes, *, decrypt=False) -> bytes:
    """Windows DPAPI, current user scope, with interactive prompts disabled."""
    if os.name != "nt":
        raise ValueError("记住 API Key 目前仅支持 Windows；其他平台可在本次会话使用")
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise OSError("Windows 无法读取或保护这份密钥，请在当前账户重新填写")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


class Settings:
    def __init__(self, path: Path | None = None):
        self.path = path or settings_path()
        self.values = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        if not isinstance(self.values, dict):
            raise ValueError("启动器配置不是 JSON 对象")
        self.revision = 0

    @locked
    def write(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # MSIX can redirect logical C: AppData to another drive. Resolve both
        # paths first so the atomic replace stays on the same physical volume.
        self.path = self.path.resolve()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.values, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        tmp.replace(self.path)

    @locked
    def remember_file(self, path: Path, profile: str, port: int | None = None):
        recent = [str(path.resolve())] + [p for p in self.values.get("recent", []) if p != str(path.resolve())]
        self.values.update(recent=recent[:10], profile=profile)
        if port is not None:
            self.values.setdefault("ports", {})[str(path.resolve())] = port
        self.write()

    def key(self) -> str:
        encoded = self.values.get("encrypted_key")
        return protect(base64.b64decode(encoded, validate=True), decrypt=True).decode("utf-8") if encoded else ""

    @locked
    def model_settings(self, provider, model, base_url, key, remember, ngspice,
                       *, plan_effort=None, draw_effort=None, expected_revision=None):
        from .ai.loop import EFFORT_CHOICES
        if expected_revision is not None and expected_revision != self.revision:
            raise ValueError("配置已在其他窗口更新，请关闭并重新打开设置后再修改")
        efforts = {"plan_effort": plan_effort, "draw_effort": draw_effort}
        for value in efforts.values():
            if value is not None and value not in EFFORT_CHOICES:
                raise ValueError("思考强度应选择服务商默认、low、medium、high、xhigh 或 max")
        if provider not in ("openai", "anthropic"):
            raise ValueError("请选择 OpenAI 兼容接口或 Anthropic 接口")
        validate_url(base_url)
        if ngspice and not Path(ngspice).is_file():
            raise ValueError("指定的 ngspice 程序不存在")
        encrypted = base64.b64encode(protect(key.encode())).decode() if remember and key else None
        updated = {**self.values, "provider": provider, "model": model, "base_url": base_url}
        if ngspice is not None:
            updated["ngspice"] = ngspice
        updated.update({k: v for k, v in efforts.items() if v is not None})
        updated.pop("encrypted_key", None)
        if encrypted:
            updated["encrypted_key"] = encrypted
        old = self.values
        self.values = updated
        try:
            self.write()
        except OSError:
            self.values = old
            raise
        self.apply(key, update_ngspice=ngspice is not None)
        self.revision += 1

    @locked
    def apply(self, key: str | None = None, *, update_ngspice=True):
        from .ai.loop import EFFORT_CHOICES, STAGE_EFFORT
        # Missing fields preserve legacy/environment behavior. Explicit default
        # must override an earlier high/low value, rather than merely deleting it.
        for stage, (variable, _) in STAGE_EFFORT.items():
            value = self.values.get(stage + "_effort")
            if value in EFFORT_CHOICES:
                os.environ[variable] = value
        provider = self.values.get("provider")
        if provider:
            os.environ["CIRCUIT_DRAWER_PROVIDER"] = provider
            prefix = "ANTHROPIC" if provider == "anthropic" else "OPENAI"
            for name, value in {"CIRCUIT_DRAWER_MODEL": self.values.get("model", ""),
                                prefix + "_BASE_URL": self.values.get("base_url", "")}.items():
                if value:
                    os.environ[name] = value
                else:
                    os.environ.pop(name, None)
            if key is None and self.values.get("encrypted_key"):
                key = self.key()
            if key is not None:
                if key:
                    os.environ[prefix + "_API_KEY"] = key
                else:
                    os.environ.pop(prefix + "_API_KEY", None)
                if prefix == "ANTHROPIC":
                    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
        if update_ngspice and self.values.get("ngspice"):
            os.environ["CIRCUIT_DRAWER_NGSPICE"] = self.values["ngspice"]
        elif update_ngspice and "ngspice" in self.values:
            os.environ.pop("CIRCUIT_DRAWER_NGSPICE", None)


class Session:
    def __init__(self, path: Path, profile: str, port: int = 0, *, settings=None):
        path = path.resolve()
        document = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(document, dict) and document.get("kind") == "control":
            from .control.editor import Editor as ControlEditor
            from .control.model import parse
            parse(document)
            self.editor = ControlEditor(path, profile, path.parent / "out", settings=settings)
        elif not isinstance(document, dict) or not isinstance(document.get("elements"), list):
            raise ValueError("请选择电路 JSON（含 elements）或控制框图 JSON（kind=control）")
        else:
            Layout.from_dict(document)  # Allow geometrical errors to be repaired in the editor.
            self.editor = Editor(path, profile, path.parent / "out", settings=settings)
        # Reuse the URL for browser preferences. If occupied, choose a free
        # localhost port; never kill or share an existing editor.
        try:
            self.server = LocalServer(("127.0.0.1", port), self.editor.handler())
        except OSError:
            if not port:
                raise
            self.server = LocalServer(("127.0.0.1", 0), self.editor.handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/"

    def close(self):
        for job in self.editor.jobs.values():
            job.cancel.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def create_circuit(template: str | None, path: Path):
    """Never replace an existing drawing when creating a new one."""
    layout = Layout(path.stem) if template is None else from_template(template)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(layout.to_dict(), stream, ensure_ascii=False, indent=1)
        stream.write("\n")


def create_control(template_name: str | None, path: Path):
    """Use the same launcher and settings for an ordinary control JSON file."""
    from .control import template
    table = template(template_name) if template_name else {
        "kind": "control", "name": path.stem, "profile": "ieee-double",
        "blocks": [], "links": [], "texts": [], "regions": []}
    with path.open("x", encoding="utf-8") as stream:
        json.dump(table, stream, ensure_ascii=False, indent=1)
        stream.write("\n")


class Launcher:
    def __init__(self, root, settings: Settings):
        import tkinter as tk
        from tkinter import ttk
        self.root, self.settings, self.sessions = root, settings, []
        self.directory = project_directory()
        self.status = tk.StringVar(value="普通画图、模态图和仿真无需 API；大模型功能在“API 与仿真设置”中配置。")
        self.profile = tk.StringVar(value=settings.values.get("profile", "ieee-single"))
        recent = settings.values.get("recent", [])
        self.file = tk.StringVar(value=recent[0] if recent else "")
        self.auto = tk.BooleanVar(value=settings.values.get("open_last", True))
        root.title("Circuit Drawer 启动器")
        root.geometry("900x480")
        root.minsize(760, 420)
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        root.option_add("*Font", "{Microsoft YaHei UI} 10" if os.name == "nt" else "TkDefaultFont")
        frame = ttk.Frame(root, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Circuit Drawer", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        ttk.Label(frame, text="启动器打开电路或控制框图，浏览器负责编辑；两者共用 API 设置。").pack(anchor="w", pady=(3, 16))
        row = ttk.Frame(frame); row.pack(fill="x")
        ttk.Label(row, text="图纸 / 最近文件").pack(side="left", padx=(0, 8))
        self.recent = ttk.Combobox(row, textvariable=self.file, values=recent)
        self.recent.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="浏览…", command=self.browse).pack(side="left", padx=(8, 0))
        row = ttk.Frame(frame); row.pack(fill="x", pady=12)
        ttk.Button(row, text="打开 / 继续编辑", command=self.open_selected).pack(side="left")
        ttk.Button(row, text="新建电路…", command=self.new_dialog).pack(side="left", padx=8)
        ttk.Button(row, text="新建控制框图…", command=lambda: self.new_dialog(control=True)).pack(side="left", padx=(0, 8))
        ttk.Button(row, text="API 与仿真设置…", command=self.settings_dialog).pack(side="left")
        ttk.Combobox(row, textvariable=self.profile, values=("ieee-single", "ieee-double"),
                     state="readonly", width=14).pack(side="right")
        ttk.Checkbutton(frame, text="下次双击启动器时，自动打开上次使用的图纸", variable=self.auto,
                        command=self.save_auto).pack(anchor="w")
        ttk.Label(frame, text="正在运行的编辑器").pack(anchor="w", pady=(16, 4))
        self.running = tk.Listbox(frame, height=5, exportselection=False)
        self.running.pack(fill="both", expand=True)
        self.running.bind("<Double-Button-1>", lambda event: self.reopen())
        row = ttk.Frame(frame); row.pack(fill="x", pady=8)
        ttk.Button(row, text="重新打开浏览器", command=self.reopen).pack(side="left")
        ttk.Button(row, text="停止所选编辑器", command=self.stop_selected).pack(side="left", padx=8)
        ttk.Label(frame, textvariable=self.status, wraplength=840).pack(anchor="w", pady=(8, 0))
        ttk.Label(frame, text="启动后可最小化此窗口；退出启动器会停止它启动的编辑器，请先在浏览器保存。").pack(anchor="w", pady=(4, 0))
        root.update_idletasks()
        # Chinese UI fonts at 125–200% DPI need more height than a fixed 480 px.
        height = max(480, frame.winfo_reqheight())
        root.geometry(f"900x{height}")
        root.minsize(760, height)
        self.recent.xview_moveto(1)
        root.protocol("WM_DELETE_WINDOW", self.close)
        try:
            settings.apply()
        except (OSError, ValueError):
            self.status.set("已保存的 API Key 无法读取，请在“API 与仿真设置”中重新填写。画图仍可使用。")

    def save_auto(self):
        self.settings.values["open_last"] = self.auto.get()
        self.settings.write()

    def browse(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(parent=self.root, title="打开电路或控制框图（JSON 保存文件）",
                                         initialdir=self.directory, filetypes=[("电路 / 控制框图 JSON", "*.json")])
        if path:
            self.file.set(path)
            self.open_selected()

    def open_selected(self):
        from tkinter import messagebox
        if not self.file.get().strip():
            self.browse()
            return
        try:
            path = Path(self.file.get().strip()).expanduser().resolve()
            session = next((s for s in self.sessions if s.editor.path.resolve() == path), None)
            if session is None:
                port = self.settings.values.get("ports", {}).get(str(path), 8765)
                session = Session(path, self.profile.get(), port, settings=self.settings)
                self.sessions.append(session)
            self.settings.remember_file(session.editor.path, session.editor.profile, session.server.server_port)
            self.recent.configure(values=self.settings.values["recent"])
            self.recent.xview_moveto(1)
            self.refresh()
            webbrowser.open(session.url)
            self.status.set(f"已打开 {session.editor.path.name}。保持启动器运行即可，API 设置可随时修改。")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            messagebox.showerror("无法打开电路", str(exc), parent=self.root)

    def refresh(self):
        self.running.delete(0, "end")
        for session in self.sessions:
            self.running.insert("end", f"{session.editor.path.name}   —   {session.url}")
        if self.sessions:
            self.running.selection_set(len(self.sessions)-1)

    def reopen(self):
        selection = self.running.curselection()
        if selection:
            webbrowser.open(self.sessions[selection[0]].url)

    def stop_selected(self):
        from tkinter import messagebox
        selection = self.running.curselection()
        if selection and messagebox.askyesno("停止编辑器", "请先在浏览器保存电路。现在停止所选编辑器？", parent=self.root):
            session = self.sessions[selection[0]]
            self.settings.remember_file(session.editor.path, session.editor.profile, session.server.server_port)
            self.sessions.pop(selection[0]).close()
            self.refresh()

    def close(self):
        from tkinter import messagebox
        if self.sessions and not messagebox.askyesno("退出启动器", "退出会停止上述编辑器。已经在浏览器保存，确定退出？", parent=self.root):
            return
        if self.sessions:
            selection = self.running.curselection()
            session = self.sessions[selection[0] if selection else -1]
            self.settings.remember_file(session.editor.path, session.editor.profile, session.server.server_port)
        for session in self.sessions:
            session.close()
        self.sessions.clear()
        self.root.destroy()

    def new_dialog(self, control=False):
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox
        window = tk.Toplevel(self.root); window.title("新建电路"); window.transient(self.root)
        window.grab_set()
        frame = ttk.Frame(window, padding=20); frame.pack(fill="both", expand=True)
        if control:
            from .control.render import available as control_templates
            available = control_templates()
            window.title("新建控制框图")
        else:
            available = templates.available()
        blank = "空白控制框图（自己绘制 / AI 生成）" if control else "空白电路（自己绘制 / AI 生成）"
        choices = {blank: None, **{name: name for name in available}}
        chosen = tk.StringVar(value=blank)
        ttk.Label(frame, text="可以从空白画布开始，也可以选一个基础拓扑修改。").pack(anchor="w", pady=(0, 8))
        ttk.Combobox(frame, textvariable=chosen, values=list(choices), state="readonly", width=42).pack(fill="x")
        ttk.Label(frame, text="控制框图打开后，点“描述新框图”或“AI 修改当前图”；模型配置与电路编辑器共用。" if control else "空白电路打开后，可从右侧元件库添加元件，或点“描述新拓扑”让 AI 生成。", wraplength=440).pack(anchor="w", pady=(10, 0))
        def create():
            template = choices[chosen.get()]
            noun = "控制框图" if control else "电路"
            path = filedialog.asksaveasfilename(parent=window, title="保存新" + noun, initialdir=self.directory,
                                               initialfile=(template or ("control" if control else "circuit"))+".json", defaultextension=".json",
                                               filetypes=[(noun + " JSON", "*.json")])
            if not path:
                return
            try:
                if Path(path).exists():
                    raise ValueError("这个文件已存在，请给新图另取名字；已有图可用“打开 / 继续编辑”。")
                (create_control if control else create_circuit)(template, Path(path))
                if control:
                    self.profile.set("ieee-double")
                elif template is not None:
                    self.profile.set(getattr(available[template], "DEFAULT_PROFILE", "ieee-single"))
                self.file.set(path); window.destroy(); self.open_selected()
            except (OSError, ValueError, KeyError) as exc:
                messagebox.showerror("无法新建", str(exc), parent=window)
        ttk.Button(frame, text="选择保存位置并创建", command=create).pack(pady=(12, 0))

    def settings_dialog(self):
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox
        from .ai.loop import EFFORT_CHOICES, resolve_provider, stage_effort
        revision = self.settings.revision
        window = tk.Toplevel(self.root); window.title("API 与仿真设置"); window.transient(self.root); window.grab_set()
        frame = ttk.Frame(window, padding=18); frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        provider = tk.StringVar(value=self.settings.values.get("provider") or resolve_provider())
        model = tk.StringVar(value=os.environ.get("CIRCUIT_DRAWER_MODEL", ""))
        base = tk.StringVar(value=os.environ.get("ANTHROPIC_BASE_URL" if provider.get()=="anthropic" else "OPENAI_BASE_URL", ""))
        key = tk.StringVar(value=environment_key(provider.get()))
        ngspice = tk.StringVar(value=os.environ.get("CIRCUIT_DRAWER_NGSPICE", ""))
        remember = tk.BooleanVar(value=bool(self.settings.values.get("encrypted_key")))
        default_label = "服务商默认（不额外指定）"
        effort_labels = (default_label, *EFFORT_CHOICES[1:])
        def effort_value(stage):
            value = stage_effort(stage, explicit_only=provider.get() == "openai")
            return value or default_label
        plan_effort = tk.StringVar(value=effort_value("plan"))
        draw_effort = tk.StringVar(value=effort_value("draw"))
        ttk.Label(frame, text="仅大模型功能需要 API。这里对应你原来在 PowerShell 中设置的参数。", wraplength=590).grid(row=0,column=0,columnspan=3,sticky="w",pady=(0,12))
        for row, title in enumerate(("接口类型", "模型 ID", "接口地址", "API Key", "ngspice 程序"), 1):
            ttk.Label(frame, text=title).grid(row=row,column=0,sticky="w",padx=(0,10),pady=6)
        provider_box = ttk.Combobox(frame,textvariable=provider,values=("openai","anthropic"),state="readonly",width=48)
        provider_box.grid(row=1,column=1,sticky="ew")
        def change_provider(event):
            key.set(environment_key(provider.get()))
            base.set(os.environ.get("ANTHROPIC_BASE_URL" if provider.get()=="anthropic" else "OPENAI_BASE_URL", ""))
            model.set("")
            plan_effort.set(default_label)
            draw_effort.set(default_label)
        provider_box.bind("<<ComboboxSelected>>", change_provider)
        for row, variable in ((2,model),(3,base),(4,key),(5,ngspice)):
            ttk.Entry(frame,textvariable=variable,width=52,show="•" if row==4 else "").grid(row=row,column=1,sticky="ew")
        def choose_ngspice():
            path=filedialog.askopenfilename(parent=window,title="选择 ngspice_con.exe",filetypes=[("应用程序","*.exe"),("所有文件","*")])
            if path:ngspice.set(path)
        ttk.Button(frame,text="浏览…",command=choose_ngspice).grid(row=5,column=2,padx=(8,0))
        for row, title, variable in ((6, "先看结构：思考强度", plan_effort),
                                     (7, "生成 / 修改：思考强度", draw_effort)):
            ttk.Label(frame, text=title).grid(row=row, column=0, sticky="w", padx=(0,10), pady=6)
            ttk.Combobox(frame, textvariable=variable, values=effort_labels,
                         state="readonly", width=48).grid(row=row, column=1, sticky="ew")
        ttk.Label(frame,text="openai 用于 OpenAI 兼容接口；anthropic 用于 Anthropic 兼容接口。档位支持取决于模型和服务商；不确定时选服务商默认。强度越高可能越慢、用量越大。千问等模型的独立思考开关与预算不由此设置控制。",wraplength=590).grid(row=8,column=0,columnspan=3,sticky="w",pady=8)
        box=ttk.Checkbutton(frame,text="在当前 Windows 账户中加密记住 API Key（下次不用再输）",variable=remember)
        box.grid(row=9,column=0,columnspan=3,sticky="w")
        if os.name!="nt":box.state(["disabled"])
        ttk.Label(frame,text="不勾选时密钥仅在本次启动器会话使用。设置不写入项目或电路文件。",wraplength=590).grid(row=10,column=0,columnspan=3,sticky="w",pady=6)
        def apply():
            try:
                self.settings.model_settings(provider.get(),model.get().strip(),base.get().strip(),key.get().strip(),remember.get(),ngspice.get().strip(),
                    plan_effort="default" if plan_effort.get() == default_label else plan_effort.get(),
                    draw_effort="default" if draw_effort.get() == default_label else draw_effort.get(),
                    expected_revision=revision)
                self.status.set("设置已应用，后续模型请求使用新设置；普通编辑不受影响。")
                window.destroy()
            except (OSError,ValueError) as exc:
                messagebox.showerror("无法保存设置",str(exc),parent=window)
        ttk.Button(frame,text="应用设置",command=apply).grid(row=11,column=1,sticky="e",pady=(12,0))


def main():
    import tkinter as tk
    from tkinter import messagebox
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", nargs="?", help="optional JSON circuit to open")
    parser.add_argument("--settings", type=Path, help="use another local settings file")
    parser.add_argument("--control", action="store_true", help="start by creating a control diagram (shared API settings)")
    args = parser.parse_args()
    mutex = None
    kernel = None
    if os.name == "nt":
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        identity = hashlib.sha256(str((args.settings or settings_path()).resolve()).encode()).hexdigest()[:20]
        mutex = kernel.CreateMutexW(None, False, "Local\\CircuitDrawerLauncher_"+identity)
        if mutex and ctypes.get_last_error() == 183:
            user = ctypes.WinDLL("user32")
            user.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
            user.FindWindowW.restype = wintypes.HWND
            user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            user.SetForegroundWindow.argtypes = [wintypes.HWND]
            hwnd = user.FindWindowW(None, "Circuit Drawer 启动器")
            if hwnd:
                user.ShowWindow(hwnd, 9); user.SetForegroundWindow(hwnd)
            kernel.CloseHandle(mutex)
            return 0
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (OSError, AttributeError):
            pass
    root = tk.Tk()
    try:
        app = Launcher(root, Settings(args.settings))
        root.report_callback_exception = lambda kind, value, trace: messagebox.showerror("启动器", str(value), parent=root)
        if args.file:
            app.file.set(args.file); root.after(150, app.open_selected)
        elif args.control:
            root.after(150, lambda: app.new_dialog(control=True))
        elif app.auto.get() and app.file.get() and Path(app.file.get()).is_file():
            root.after(150, app.open_selected)
        root.mainloop()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        messagebox.showerror("无法启动", str(exc), parent=root)
        root.destroy()
        return 1
    finally:
        if mutex:
            kernel.CloseHandle(mutex)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
