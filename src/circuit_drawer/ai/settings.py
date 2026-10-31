"""Shared launcher/web configuration access; secrets never go back to the page."""
from functools import wraps
import os
import secrets
import threading
from urllib.parse import urlsplit

CONFIG_LOCK = threading.RLock()


def locked(function):
    @wraps(function)
    def call(*args, **kwargs):
        with CONFIG_LOCK:
            return function(*args, **kwargs)
    return call


def environment_key(provider):
    return ((os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN", ""))
            if provider == "anthropic" else os.environ.get("OPENAI_API_KEY", ""))


def validate_url(value):
    if not value:
        return
    try:
        url = urlsplit(value)
        valid = url.scheme in ("http", "https") and url.hostname and not (
            url.username or url.password or url.query or url.fragment)
        url.port  # reject malformed ports without echoing the input
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("接口地址应为完整的 http(s) 地址，不含账号、密码、查询参数或片段；密钥请填入 API Key")


class WebSettings:
    def __init__(self, editor, settings=None):
        self.editor, self.settings = editor, settings
        self.token = secrets.token_urlsafe(32)

    def config(self):
        if self.settings is None:
            from ..launcher import Settings
            self.settings = Settings()
        return self.settings

    @locked
    def read(self):
        from .loop import EFFORT_CHOICES, PROVIDERS, provider_info
        settings = self.config()
        info = provider_info(self.editor.provider, self.editor.model, check_sdk=False)
        return {"provider": info["provider"], "model": info.get("model") or "",
                "base_url": info.get("base_url") or "", "key_set": bool(info.get("key_set")),
                "remember": bool(settings.values.get("encrypted_key")), "can_remember": os.name == "nt",
                "plan_effort": info.get("plan_effort") or "default",
                "draw_effort": info.get("draw_effort") or "default",
                "providers": list(PROVIDERS), "efforts": list(EFFORT_CHOICES),
                "revision": settings.revision, "token": self.token}

    @locked
    def update(self, req):
        fields = {"provider", "model", "base_url", "key", "key_action", "remember",
                  "plan_effort", "draw_effort", "revision"}
        if not isinstance(req, dict) or set(req) != fields:
            raise ValueError("设置字段不完整，请重新打开 AI 接口设置")
        for key in fields - {"remember", "revision"}:
            if not isinstance(req[key], str) or len(req[key]) > 4096:
                raise ValueError("设置项应为不超过 4096 字符的文本")
        if type(req["remember"]) is not bool or type(req["revision"]) is not int:
            raise ValueError("记住密钥与配置版本格式不正确")
        if req["remember"] and os.name != "nt":
            raise ValueError("加密记住 API Key 目前仅支持 Windows")
        settings, before = self.config(), self.read()
        action = req["key_action"]
        if action == "keep":
            if req["key"]:
                raise ValueError("输入新密钥时请选择“替换密钥”")
            if req["provider"] != before["provider"] or req["base_url"].strip().rstrip("/") != before["base_url"].rstrip("/"):
                raise ValueError("接口类型或地址已改变，请填写新接口的密钥，不会沿用旧密钥")
            key = environment_key(req["provider"])
            if not key:
                raise ValueError("当前接口没有可保留的密钥，请选择替换或清除")
        elif action == "replace":
            key = req["key"].strip()
            if not key:
                raise ValueError("请填写新密钥；不使用密钥时请选择“清除密钥”")
        elif action == "clear":
            key = ""
        else:
            raise ValueError("请选择保留、替换或清除密钥")
        settings.model_settings(req["provider"], req["model"].strip(), req["base_url"].strip(), key,
                                req["remember"], None,
                                plan_effort=req["plan_effort"], draw_effort=req["draw_effort"],
                                expected_revision=req["revision"])
        # Explicit command-line model flags apply until the user changes them here.
        self.editor.provider = self.editor.model = None
        return self.read()


class SettingsRoutes:
    """Both editor handlers share the same local-origin and token checks."""
    def _settings_guard(self, write=False):
        host = self.headers.get("Host", "")
        allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        origin = self.headers.get("Origin")
        if host not in allowed or (origin is not None and origin != "http://" + host):
            raise ValueError("请从本机编辑器页面打开 AI 接口设置")
        if write and (self.headers.get_content_type() != "application/json" or
                      not secrets.compare_digest(self.headers.get("X-CircuitDrawer-Settings", ""), self.web_settings.token)):
            raise ValueError("设置请求已失效，请重新打开 AI 接口设置")

    def _get_ai_settings(self):
        self._settings_guard()
        return self.web_settings.read()

    def _set_ai_settings(self, req):
        self._settings_guard(write=True)
        return self.web_settings.update(req)

    def _set_ai_settings_route(self, req):
        try:
            self._json(self._set_ai_settings(req))
        except (ValueError, OSError) as exc:
            self._json({"error": str(exc)}, 400)
