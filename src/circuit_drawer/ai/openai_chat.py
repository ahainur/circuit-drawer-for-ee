"""OpenAI Chat Completions, and any service that speaks the same protocol
(DeepSeek, Qwen/DashScope compatible mode, relays...).  Standard library
only, so no extra package is needed.

- ``OPENAI_API_KEY`` - credentials
- ``OPENAI_BASE_URL`` - default ``https://api.openai.com/v1``; point it at a
  compatible service, e.g. ``https://api.deepseek.com/v1``
- the model name is required (``--model`` or ``CIRCUIT_DRAWER_MODEL``)

Replies are streamed (``stream: true``) so the page can show text as it
arrives. The optional usage report in ``stream_options`` is dropped if the
service rejects it with 400. A refused reasoning effort is reported, never
silently removed. A service that ignores ``stream`` and
answers with one JSON body is read as before.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from copy import copy

from .. import __version__
from .meter import Meter

DEFAULT_BASE_URL = "https://api.openai.com/v1"
# Identify the application honestly; some API gateways reject Python-urllib.
USER_AGENT = f"CircuitDrawer/{__version__}"
# Keep provider error JSON intact long enough to extract its actual message,
# while bounding both the response read and the amount shown in the editor.
ERROR_BODY_LIMIT = 8192
ERROR_TEXT_LIMIT = 600


def service_error_detail(raw: str, secret: str) -> str:
    """A bounded provider explanation, with our credential removed before display."""
    try:
        data = json.loads(raw)
    except ValueError:
        data = raw
    if isinstance(data, dict):
        error = data.get("error", data)
        if isinstance(error, dict):
            message = error.get("message") or error.get("detail") or error.get("title")
            code = error.get("code") or error.get("error_code") or error.get("type")
            data = f"{code}: {message}" if code and message else message or code or raw
        else:
            data = error
    text = str(data)
    if secret:
        text = text.replace(secret, "[已隐藏密钥]")
    text = " ".join(text.split())
    return text[:ERROR_TEXT_LIMIT] + ("…" if len(text) > ERROR_TEXT_LIMIT else "")

NO_KEY = (
    "没有找到 OpenAI 兼容接口的密钥。请先设置环境变量：\n"
    "  PowerShell: $env:OPENAI_API_KEY=\"sk-...\"\n"
    "  bash:       export OPENAI_API_KEY=sk-...\n"
    "用 DeepSeek、中转等兼容服务时再设 OPENAI_BASE_URL（如 https://api.deepseek.com/v1）。\n"
    "也可以在网页编辑器“AI → AI 接口设置…”或 Windows 启动器“API 与仿真设置…”里填写密钥，不必设环境变量。")
NO_MODEL = ("用 OpenAI 兼容接口时请用 --model 或 CIRCUIT_DRAWER_MODEL 指定模型名，"
            "例如 OpenAI 账户里可用的 GPT 型号，或 DeepSeek 的 deepseek-chat。")


class OpenAIChat:
    """One conversation over /chat/completions; history is kept locally."""

    def __init__(self, system: str, model: str | None = None,
                 base_url: str | None = None, api_key: str | None = None,
                 timeout: float | None = None, effort: str | None = None):
        from .loop import ModelError, output_limit, request_timeout
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ModelError(NO_KEY)
        self.model = model or os.environ.get("CIRCUIT_DRAWER_MODEL")
        if not self.model:
            raise ModelError(NO_MODEL)
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL")
                         or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout or request_timeout()
        self.max_tokens = output_limit()
        self.messages: list[dict] = [{"role": "system", "content": system}]
        # optional fields, removed one by one when the service refuses them
        self.optional: dict = {"stream_options": {"include_usage": True}}
        if effort:
            self.optional["reasoning_effort"] = effort
        self.on_text = None
        self.stats: list[dict] = []

    def fork(self, system: str):
        """Fresh conversation without re-reading possibly changed API settings."""
        chat = copy(self)
        chat.messages = [{"role": "system", "content": system}]
        chat.optional = dict(self.optional)
        chat.stats, chat.on_text = [], None
        return chat

    def send(self, text: str, images: list[tuple[str, str]] | None = None) -> str:
        from .loop import ModelError, OutputTruncated
        content: str | list = text
        if images:
            content = [{"type": "image_url", "image_url": {"url": f"data:{mt};base64,{data}"}}
                       for mt, data in images] + [{"type": "text", "text": text}]
        self.messages.append({"role": "user", "content": content})
        while True:
            try:
                content, finish, usage, meter = self._post()
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read(ERROR_BODY_LIMIT).decode("utf-8", "replace")
                if self._drop_optional(exc.code, detail):
                    continue
                error = self._http_error(exc.code, detail)
                self.messages.pop()
                raise error from exc
            except (TimeoutError, OSError) as exc:
                self.messages.pop()
                if isinstance(exc, TimeoutError) or "timed out" in str(exc):
                    raise ModelError(f"等了 {self.timeout:.0f} 秒 {self.base_url} 没有返回结果；"
                                     "检查网络、代理（HTTPS_PROXY）或换一个可访问的 "
                                     "OPENAI_BASE_URL。复杂电路也可以调大 CIRCUIT_DRAWER_TIMEOUT。"
                                     ) from exc
                raise ModelError(f"连不上 {self.base_url}（{exc}）；检查网络、代理或 "
                                 "OPENAI_BASE_URL。") from exc
        usage = usage or {}
        self.stats.append(meter.done(usage.get("prompt_tokens"), usage.get("completion_tokens")))
        self.stats[-1].update(finish_reason=finish, text_chars=len(content),
                              requested_effort=self.optional.get("reasoning_effort"))
        if finish == "length":
            self.messages.pop()
            raise OutputTruncated("length", limit=self.max_tokens,
                                  output_tokens=usage.get("completion_tokens"), text_chars=len(content))
        self.messages.append({"role": "assistant", "content": content})
        return content

    def prepare_truncation_retry(self) -> bool:
        # Do not reintroduce a parameter already rejected by a compatible server.
        if "reasoning_effort" not in self.optional:
            return False
        changed = self.optional["reasoning_effort"] != "low"
        self.optional["reasoning_effort"] = "low"
        return changed

    def _drop_optional(self, code: int, detail: str) -> bool:
        """Usage reporting can be omitted; an explicitly sent effort cannot."""
        if code != 400 or "effort" in detail.lower() or "reasoning" in detail.lower():
            return False
        return self.optional.pop("stream_options", None) is not None

    def _http_error(self, code: int, detail: str):
        from .loop import ModelError
        detail = detail.replace(self.api_key, "[已隐藏密钥]")
        if code == 400 and "reasoning_effort" in self.optional and any(
                name in detail.lower() for name in ("reasoning", "effort")):
            return ModelError(f"接口拒绝了请求的思考强度 {self.optional['reasoning_effort']}（400）。"
                              "请在 API 与仿真设置中选择服务商支持的档位，或“服务商默认”。"
                              f"\n服务端说明：{detail}")
        msg = {401: "密钥被拒绝（401），请检查 OPENAI_API_KEY。",
               403: "接口或其网关拒绝访问（403），可能涉及权限或访问限制。",
               404: f"找不到模型 {self.model} 或接口地址不对（404）；"
                    f"检查 --model 和 OPENAI_BASE_URL（当前 {self.base_url}）。",
               429: "请求太频繁或额度已用完（429），稍后再试。"}.get(code)
        if code == 400 and any(isinstance(m.get("content"), list) for m in self.messages):
            msg = f"接口拒绝了请求（400），可能是模型 {self.model} 不支持图片输入；去掉参考图再试。"
        reason = service_error_detail(detail, self.api_key)
        if msg and code == 403 and reason:
            return ModelError(f"{msg}\n服务端说明：{reason}")
        return ModelError(msg or f"模型服务返回 {code}：{reason}")

    def _post(self) -> tuple[str, str | None, dict | None, Meter]:
        """One request; returns (text, finish reason, usage, meter)."""
        from .loop import ModelError
        body = json.dumps({"model": self.model, "messages": self.messages, "stream": True,
                           **({"max_tokens": self.max_tokens} if self.max_tokens else {}),
                           **self.optional}).encode()
        req = urllib.request.Request(
            self.base_url + "/chat/completions", body,
            {"Content-Type": "application/json", "Accept": "text/event-stream",
             "Authorization": f"Bearer {self.api_key}", "User-Agent": USER_AGENT})
        meter = Meter()
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            if "text/event-stream" not in (r.headers.get("Content-Type") or ""):
                data = json.loads(r.read())
                try:
                    choice = data["choices"][0]
                    text = choice["message"].get("content") or ""
                except (KeyError, IndexError, TypeError) as exc:
                    raise ModelError(f"接口返回的格式看不懂：{str(data)[:300]}") from exc
                if text:
                    meter.text()
                    if self.on_text:
                        self.on_text(text)
                return text, choice.get("finish_reason"), data.get("usage"), meter
            parts: list[str] = []
            finish = usage = None
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except ValueError:
                    continue
                if chunk.get("error"):
                    raise ModelError(f"模型服务出错：{str(chunk['error'])[:300]}")
                usage = chunk.get("usage") or usage
                for choice in chunk.get("choices") or []:
                    piece = (choice.get("delta") or {}).get("content")
                    if piece:
                        meter.text()
                        parts.append(piece)
                        if self.on_text:
                            self.on_text(piece)
                    finish = choice.get("finish_reason") or finish
            return "".join(parts), finish, usage, meter
