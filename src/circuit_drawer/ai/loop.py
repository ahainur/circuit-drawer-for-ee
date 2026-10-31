"""Description -> model-written coordinate table -> validator -> rework loop.

The model only writes coordinates.  Each table goes through the same
``check`` as every template; a rejected table is sent back with the report
until it passes or the round limit is reached.  Nothing here repairs a table
or places parts on its own.

Two providers: Anthropic (default) and OpenAI-compatible services (see
``openai_chat.py``).  Pick one with ``--provider`` or
``CIRCUIT_DRAWER_PROVIDER=anthropic|openai``; without either, a lone
``OPENAI_API_KEY`` selects OpenAI.  Anthropic is configured through:

- ``ANTHROPIC_API_KEY`` (or ``ANTHROPIC_AUTH_TOKEN``) - credentials
- ``ANTHROPIC_BASE_URL`` - optional proxy / gateway
- ``CIRCUIT_DRAWER_MODEL`` - model id, default ``claude-opus-5-5``
- ``CIRCUIT_DRAWER_TIMEOUT`` - seconds without any response before a request
  is abandoned (default 300; applies to both providers)
- ``CIRCUIT_DRAWER_MAX_TOKENS`` - positive output limit; default 64000 for
  Anthropic, omitted for OpenAI-compatible services unless explicitly set
- ``CIRCUIT_DRAWER_PLAN_EFFORT`` - reasoning effort for the structure step
  (default ``low``); ``CIRCUIT_DRAWER_EFFORT`` - for coordinate tables
  (default ``high``; OpenAI-compatible services get it only when it is set)
- ``CIRCUIT_DRAWER_FALLBACK=0`` - turn off server-side refusal fallback
  (some gateways reject the beta parameter)
"""

from __future__ import annotations

import json
import os
import re
import sys
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Callable, Protocol

from ..layout import PIN_NAMES, Layout
from ..profiles import get_profile
from ..validate import check
from .meter import Meter, describe
from .prompt import (PLAN_SYSTEM, first_message, identical_message, plan_message,
                     regression_message, rework_message, system_prompt, tweak_message)

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_ROUNDS = 4
# top-level sections the model draws along with the circuit (module blocks
# and arrows); every other extra section is carried over from the old table
DRAWN_SECTIONS = ("regions", "callouts", "blocks", "wave_symbols", "modules", "module_view", "no_connect", "model_mapping")

NO_KEY = (
    "没有找到大模型的 API 密钥。请先设置环境变量：\n"
    "  PowerShell: $env:ANTHROPIC_API_KEY=\"sk-ant-...\"\n"
    "  bash:       export ANTHROPIC_API_KEY=sk-ant-...\n"
    "使用代理或网关时再设 ANTHROPIC_BASE_URL；换模型用 CIRCUIT_DRAWER_MODEL 或 --model。\n"
    "想用 GPT 或其他 OpenAI 兼容接口：设 OPENAI_API_KEY，并加 --provider openai --model <模型名>。\n"
    "也可以在网页编辑器“AI → AI 接口设置…”或 Windows 启动器“API 与仿真设置…”里填写密钥，不必设环境变量。\n"
    "不用模型也能画：circuit-drawer export / edit 手工写坐标表。")
NO_SDK = ("调用大模型需要 anthropic 包：pip install 'circuit-drawer-for-ee[ai]'"
          "（或 pip install anthropic）")


class ModelError(RuntimeError):
    """Model unreachable or misconfigured; the message is meant for the user."""


class OutputTruncated(ModelError):
    """An incomplete answer, distinct from a connection/authentication failure.

    Do not parse or export the partial table. Adapters remove the incomplete
    turn from their history so a bounded fresh rewrite cannot inherit broken JSON.
    """
    def __init__(self, reason: str, *, limit: int | None = None,
                 output_tokens: int | None = None, text_chars: int = 0):
        self.reason = reason
        detail = f"；请求上限 {limit} tokens" if limit else ""
        if output_tokens is not None:
            detail += f"；服务端报告输出 {output_tokens} tokens"
        super().__init__(f"模型本次回复达到输出上限（{reason}{detail}），"
                         f"已收到正文 {text_chars} 字，未采用不完整的坐标表。"
                         "可分步提出具体修改，或调整 CIRCUIT_DRAWER_MAX_TOKENS（须在服务支持范围内）。")


def output_limit(default: int | None = None) -> int | None:
    raw = os.environ.get("CIRCUIT_DRAWER_MAX_TOKENS", "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
        if value > 0:
            return value
    except ValueError:
        pass
    raise ModelError("CIRCUIT_DRAWER_MAX_TOKENS 必须是正整数。")


class Cancelled(ModelError):
    pass


def request_timeout() -> float:
    try:
        return max(10.0, float(os.environ.get("CIRCUIT_DRAWER_TIMEOUT", 300)))
    except ValueError:
        return 300.0


EFFORTS = ("low", "medium", "high", "xhigh", "max")
EFFORT_CHOICES = ("default", *EFFORTS)
STAGE_EFFORT = {"plan": ("CIRCUIT_DRAWER_PLAN_EFFORT", "low"),
                "draw": ("CIRCUIT_DRAWER_EFFORT", "high")}


def stage_effort(stage: str, explicit_only: bool = False) -> str | None:
    """Reasoning effort for a stage from the environment.  With
    ``explicit_only`` the default is None (the service decides)."""
    var, default = STAGE_EFFORT[stage]
    value = os.environ.get(var, "").strip().lower()
    if value == "default":
        return None
    if value in EFFORTS:
        return value
    return None if explicit_only and stage == "draw" else default


def _has_anthropic_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


class Chat(Protocol):
    """A conversation.  Real chats also have ``on_text`` (called with each
    piece of reply text as it streams in) and ``stats`` (one dict per
    request, see ``meter``); scripted test chats may have neither."""

    def send(self, text: str, images: list[tuple[str, str]] | None = None) -> str: ...


class ClaudeChat:
    """One conversation with Claude. Complete replies go back unchanged;
    truncated turns are discarded before any bounded retry."""

    def __init__(self, system: str, model: str | None = None,
                 fallback: bool | None = None, effort: str | None = "high"):
        try:
            import anthropic
        except ImportError as exc:
            raise ModelError(NO_SDK) from exc
        self._anthropic = anthropic
        self.model = model or os.environ.get("CIRCUIT_DRAWER_MODEL") or DEFAULT_MODEL
        if fallback is None:
            fallback = os.environ.get("CIRCUIT_DRAWER_FALLBACK", "1") not in ("0", "no", "off")
        self.fallback = fallback
        self.effort = effort
        self.max_tokens = output_limit(64000)
        self.system = [{"type": "text", "text": system,
                        "cache_control": {"type": "ephemeral"}}]
        self.messages: list[dict] = []
        self.on_text: Callable[[str], None] | None = None
        self.stats: list[dict] = []
        if not _has_anthropic_key():
            raise ModelError(NO_KEY)
        self.timeout = request_timeout()
        try:
            # read timeout: the stream sends events (and pings) while the model
            # thinks, so a long silence means the network, not the model
            self.client = anthropic.Anthropic(
                timeout=anthropic.Timeout(self.timeout, connect=20.0), max_retries=1)
        except TypeError as exc:
            raise ModelError(NO_KEY) from exc

    def send(self, text: str, images: list[tuple[str, str]] | None = None) -> str:
        a = self._anthropic
        content: str | list = text
        if images:
            content = [{"type": "image", "source": {"type": "base64", "media_type": mt,
                                                     "data": data}} for mt, data in images]
            content.append({"type": "text", "text": text})
        self.messages.append({"role": "user", "content": content})
        extra = {"betas": ["server-side-fallback-2026-07-01"],
                 "fallbacks": "default"} if self.fallback else {}
        try:
            reasoning = ({"thinking": {"type": "adaptive"},
                          "output_config": {"effort": self.effort}}
                         if self.effort is not None else {})
            with self.client.beta.messages.stream(
                    model=self.model, max_tokens=self.max_tokens, system=self.system,
                    messages=self.messages, **reasoning, **extra) as stream:
                meter = Meter()
                for event in stream:
                    if event.type == "content_block_delta" and event.delta.type == "text_delta":
                        meter.text()
                        if self.on_text:
                            self.on_text(event.delta.text)
                msg = stream.get_final_message()
        except TypeError as exc:
            if "authentication" in str(exc).lower():
                raise ModelError(NO_KEY) from exc
            raise
        except a.AuthenticationError as exc:
            raise ModelError("API 密钥被拒绝（401），请检查 ANTHROPIC_API_KEY。") from exc
        except a.PermissionDeniedError as exc:
            raise ModelError(f"这个密钥没有权限使用模型 {self.model}（403）。") from exc
        except a.NotFoundError as exc:
            raise ModelError(f"找不到模型 {self.model}；用 --model 或 "
                             f"CIRCUIT_DRAWER_MODEL 换一个。") from exc
        except a.RateLimitError as exc:
            raise ModelError("请求太频繁或额度已用完（429），稍后再试。") from exc
        except a.BadRequestError as exc:
            if self.effort is not None and "effort" in exc.message.lower():
                raise ModelError(f"接口拒绝了请求的思考强度 {self.effort}（400）：{exc.message}。"
                                 "请在 API 与仿真设置中选择服务商支持的档位，或“服务商默认”。") from exc
            hint = ("；网关不支持 fallback 参数时可设 CIRCUIT_DRAWER_FALLBACK=0"
                    if self.fallback else "")
            raise ModelError(f"请求被拒绝（400）：{exc.message}{hint}") from exc
        except a.APIStatusError as exc:
            raise ModelError(f"模型服务出错（{exc.status_code}），稍后再试。") from exc
        except a.APITimeoutError as exc:
            raise ModelError(f"等了 {self.timeout:.0f} 秒没有收到模型服务的响应。国内网络"
                             "通常需要代理或中转：设 HTTPS_PROXY，或用 ANTHROPIC_BASE_URL "
                             "指向可访问的网关；也可以改用 --provider openai 接国内兼容接口。"
                             ) from exc
        except a.APIConnectionError as exc:
            raise ModelError("连不上模型服务（api.anthropic.com 或 ANTHROPIC_BASE_URL）。"
                             "国内网络通常需要设 HTTPS_PROXY 或使用可访问的网关。") from exc
        u = msg.usage
        self.stats.append(meter.done(
            u.input_tokens + (getattr(u, "cache_creation_input_tokens", 0) or 0)
            + (getattr(u, "cache_read_input_tokens", 0) or 0), u.output_tokens))
        if msg.stop_reason == "refusal":
            self.messages.pop()
            raise ModelError("模型拒绝了这个请求，换个描述再试。")
        text = "".join(b.text for b in msg.content if b.type == "text")
        self.stats[-1].update(finish_reason=msg.stop_reason, text_chars=len(text),
                              requested_effort=self.effort)
        if msg.stop_reason == "max_tokens":
            self.messages.pop()
            raise OutputTruncated("max_tokens", limit=self.max_tokens,
                                  output_tokens=u.output_tokens, text_chars=len(text))
        self.messages.append({"role": "assistant", "content": msg.content})
        return text

    def prepare_truncation_retry(self) -> bool:
        """Reduce reasoning for this conversation, never increase the token budget."""
        if self.effort is None:
            return False
        changed = self.effort != "low"
        self.effort = "low"
        return changed


_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.S)


def extract_table(text: str) -> dict:
    """The last fenced JSON block, or the outermost braces as a fallback."""
    blocks = _FENCE.findall(text)
    raw = blocks[-1] if blocks else text[text.find("{"):text.rfind("}") + 1]
    if not raw.strip():
        raise ValueError("回复里没有 JSON 坐标表")
    return json.loads(raw)


def _missing_netlist(lay: Layout) -> list[str]:
    """The netlist is how the validator knows the intended circuit, so a
    generated table must declare one for every pin."""
    if "modules" in lay.extra:
        return []  # module compiler checks every root/definition pin, including hidden instances
    if not lay.netlist:
        return ["netlist-missing: the table has no netlist; declare every pin's net"]
    out = []
    for el in lay.elements:
        for pin in PIN_NAMES.get(el.kind, ()):
            if f"{el.id}.{pin}" not in lay.netlist:
                out.append(f"netlist-missing: {el.id}.{pin} has no net")
    return out


def understanding(reply: str, limit: int = 1200) -> str:
    """What the model said before its table: its reading of the circuit."""
    text = _FENCE.sub("", reply)
    if "{" in text and not _FENCE.search(reply):
        text = text[:text.find("{")]
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "…"


@dataclass
class Attempt:
    round: int
    errors: list[str]
    layout: Layout | None = None
    reply: str = ""
    fixes: list[str] = field(default_factory=list)

    @property
    def note(self) -> str:
        return understanding(self.reply)

    @property
    def ok(self) -> bool:
        return self.layout is not None and not self.errors


@dataclass
class Generation:
    description: str
    profile: str
    attempts: list[Attempt] = field(default_factory=list)
    passes_double: bool = False
    selected: Attempt | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.best_attempt is not None and self.best_attempt.ok

    @property
    def best_attempt(self) -> Attempt | None:
        """The passing table, else the parsed one with the fewest errors
        (latest wins a tie) so it can be finished in the editor."""
        if self.selected is not None:
            return self.selected
        parsed = [a for a in self.attempts if a.layout is not None]
        if not parsed:
            return None
        if self.passes_double:          # the run stopped on a double-column pass
            return parsed[-1]
        return min(reversed(parsed), key=lambda a: len(a.errors))

    @property
    def best(self) -> Layout | None:
        return self.best_attempt.layout if self.best_attempt else None


def changes(old: Layout, new: Layout) -> list[str]:
    """What a change did, element by element, for the user to check."""
    def fmt(p) -> str:
        return f"({p[0]:g}, {p[1]:g})"
    a = {e.id: e for e in old.elements}
    b = {e.id: e for e in new.elements}
    out = [f"新增 {i}（{b[i].kind}）" for i in b if i not in a]
    out += [f"删除 {i}" for i in a if i not in b]
    for i in a.keys() & b.keys():
        x, y = a[i], b[i]
        moved = [f"{k} {fmt(x.pins[k])} → {fmt(y.pins[k])}" for k in y.pins
                 if k in x.pins and x.pins[k] != y.pins[k]]
        if moved:
            out.append(f"移动 {i}：" + "，".join(moved))
        if x.kind != y.kind:
            out.append(f"{i} 换成 {y.kind}")
        if (x.label, x.label_pos) != (y.label, y.label_pos):
            out.append(f"{i} 标签：{x.label or ''} {x.label_pos or ''} → "
                       f"{y.label or ''} {y.label_pos or ''}".rstrip())
        if x.opts != y.opts:
            out.append(f"{i} 选项改为 {y.opts}")
    wa = {tuple(map(tuple, w)) for w in old.wires}
    wb = {tuple(map(tuple, w)) for w in new.wires}
    if wa != wb:
        out.append(f"导线：去掉 {len(wa - wb)} 条，加上 {len(wb - wa)} 条")
    if set(old.hops) != set(new.hops):
        out.append(f"跨线：{len(old.hops)} → {len(new.hops)} 处")
    ta = [(n.text, tuple(n.at)) for n in old.annotations]
    tb = [(n.text, tuple(n.at)) for n in new.annotations]
    if ta != tb:
        moved = [f"{t} {fmt(p)} → {fmt(q)}" for t, p in ta for u, q in tb if t == u and p != q]
        added = [t for t, _ in tb if t not in {u for u, _ in ta}]
        gone = [t for t, _ in ta if t not in {u for u, _ in tb}]
        out += [f"文字 {m}" for m in moved] + [f"新增文字 {t}" for t in added] + \
               [f"删除文字 {t}" for t in gone]
    return sorted(out, key=lambda s: (s.startswith(("导线", "跨线")), s)) or ["没有任何改动"]


PROVIDERS = ("anthropic", "openai")


def resolve_provider(provider: str | None = None) -> str:
    provider = (provider or os.environ.get("CIRCUIT_DRAWER_PROVIDER") or "").lower()
    if not provider:
        return "openai" if os.environ.get("OPENAI_API_KEY") and not _has_anthropic_key() \
            else "anthropic"
    if provider in ("openai", "gpt", "compatible"):
        return "openai"
    if provider in ("anthropic", "claude"):
        return "anthropic"
    raise ModelError(f"不认识的模型提供方 {provider!r}；可选 {', '.join(PROVIDERS)}")


def _sdk_installed() -> bool:
    """Whether the anthropic package can be imported, without importing it:
    a cold import takes several seconds and this answers every status poll."""
    import importlib.util
    if "anthropic" in sys.modules:
        return sys.modules["anthropic"] is not None
    try:
        return importlib.util.find_spec("anthropic") is not None
    except (ImportError, ValueError):
        return False


def provider_info(provider: str | None = None, model: str | None = None, *, check_sdk=True) -> dict:
    """Which provider, model and endpoint a run would use, and what is
    missing - without touching the network."""
    from .openai_chat import DEFAULT_BASE_URL, NO_KEY as NO_OPENAI_KEY, NO_MODEL
    try:
        name = resolve_provider(provider)
    except ModelError as exc:
        return {"provider": provider, "problem": str(exc)}
    model = model or os.environ.get("CIRCUIT_DRAWER_MODEL")
    info = {"provider": name, "timeout_s": request_timeout(), "problem": None,
            "plan_effort": stage_effort("plan", explicit_only=name == "openai"),
            "draw_effort": stage_effort("draw", explicit_only=name == "openai")}
    if name == "anthropic":
        info.update(model=model or DEFAULT_MODEL,
                    base_url=os.environ.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com",
                    key_set=_has_anthropic_key())
        if check_sdk and not _sdk_installed():
            info["problem"] = NO_SDK
        if not info["key_set"]:
            info["problem"] = NO_KEY
    else:
        info.update(model=model,
                    base_url=(os.environ.get("OPENAI_BASE_URL") or DEFAULT_BASE_URL).rstrip("/"),
                    key_set=bool(os.environ.get("OPENAI_API_KEY")))
        if not info["key_set"]:
            info["problem"] = NO_OPENAI_KEY
        elif not model:
            info["problem"] = NO_MODEL
    return info


def ping(base_url: str, timeout: float = 10.0) -> dict:
    """Can this machine reach the endpoint at all?  Any HTTP answer (even
    401/404) counts as reachable; proxies come from HTTPS_PROXY as usual."""
    import time
    import urllib.error
    import urllib.request
    from .openai_chat import USER_AGENT
    t0 = time.monotonic()
    try:
        urllib.request.urlopen(urllib.request.Request(
            base_url, method="GET", headers={"User-Agent": USER_AGENT}),
                               timeout=timeout).close()
        code = 200
    except urllib.error.HTTPError as exc:
        code = exc.code
    except Exception as exc:  # DNS, TLS, refused, timeout: all mean unreachable
        return {"reachable": False, "error": f"{type(exc).__name__}: {exc}",
                "seconds": round(time.monotonic() - t0, 1)}
    return {"reachable": True, "status": code, "seconds": round(time.monotonic() - t0, 1)}


def make_chat(system: str, provider: str | None = None,
              model: str | None = None, stage: str = "draw") -> Chat:
    """``stage`` picks the reasoning effort: ``plan`` (the parts list) runs
    low by default, ``draw`` (coordinates) high."""
    if resolve_provider(provider) == "openai":
        from .openai_chat import OpenAIChat
        return OpenAIChat(system, model, effort=stage_effort(stage, explicit_only=True))
    return ClaudeChat(system, model, effort=stage_effort(stage))


def plan(description: str, profile: str = "ieee-single", chat: Chat | None = None,
         model: str | None = None, provider: str | None = None,
         images: list[tuple[str, str]] | None = None,
         on_text: Callable[[str], None] | None = None,
         log: Callable[[str], None] = lambda s: None) -> str:
    """Ask only for the model's reading of the circuit (topology, parts with
    their pins' nodes, assumptions), so the user can correct it before any
    coordinates.  It has its own short prompt and low reasoning effort; the
    coordinate rules only matter once drawing starts.  ``profile`` is kept
    for callers; the parts list does not depend on it."""
    if not description.strip():
        raise ValueError("描述为空")
    chat = chat or make_chat(PLAN_SYSTEM, provider, model, stage="plan")
    return _send(chat, plan_message(description), images, on_text, log, "结构").strip()


def _send(chat: Chat, text: str, images: list[tuple[str, str]] | None,
          on_text: Callable[[str], None] | None = None,
          log: Callable[[str], None] = lambda s: None, what: str = "") -> str:
    if on_text is not None and hasattr(chat, "on_text"):
        chat.on_text = on_text
    reply = chat.send(text, images=images) if images else chat.send(text)
    stats = getattr(chat, "stats", None)
    if stats:
        log(f"{what}请求用时：{describe(stats[-1])}")
    return reply


@dataclass
class Evaluation:
    """A domain adapter supplies validation; the bounded rewrite loop is shared."""
    layout: object
    errors: list[str]
    fixes: list[str] = field(default_factory=list)
    passes_double: bool = False
    report: object = None


class CircuitWorkflow:
    """Existing circuit parsing, carry-over, nudging and correction messages."""
    document_kind = "坐标表"

    def __init__(self, profile, current=None, structure=None):
        self.prof = get_profile(profile)
        self.current, self.structure = current, structure
        self.extra = Layout.from_dict(current).extra if current else {}
        self.description = ""

    def system_prompt(self):
        return system_prompt(self.prof.name)

    def first_message(self, description):
        self.description = description
        if self.current is not None:
            from ..electrical import validate_instruction
            validate_instruction(description, Layout.from_dict(self.current))
            report = check(Layout.from_dict(self.current), self.prof)
            return tweak_message(description, self.current,
                                 [f"{i.code}: {i.message}" for i in report.errors], self.prof.name)
        return first_message(description, self.structure)

    def evaluate(self, reply):
        from .nudge import nudge
        lay = Layout.from_dict(extract_table(reply))
        drawn = {k: lay.extra[k] for k in DRAWN_SECTIONS if k in lay.extra}
        if self.current is None and "stack_usage" in lay.extra:
            drawn["stack_usage"] = lay.extra["stack_usage"]
        # Drawing edits may change module geometry/port bindings, but cannot
        # quietly change physical parameters held in defaults/overrides.
        previous_modules = self.extra.get("modules")
        proposed = drawn.get("modules")
        if isinstance(previous_modules, dict) and isinstance(proposed, dict):
            old_defs, new_defs = previous_modules.get("definitions", {}), proposed.get("definitions", {})
            if isinstance(old_defs, dict) and isinstance(new_defs, dict):
                # Project modules can be referenced by name alone. Retain
                # the saved library when a response lists only instances.
                proposed["definitions"] = new_defs = {**deepcopy(old_defs), **new_defs}
                for key, definition in new_defs.items():
                    if isinstance(definition, dict) and isinstance(old_defs.get(key), dict):
                        definition["defaults"] = deepcopy(old_defs[key].get("defaults", {}))
                        for field in ("editable", "port_layout"):
                            if field in old_defs[key]:
                                definition[field] = deepcopy(old_defs[key][field])
            old_instances = previous_modules.get("instances", [])
            old_instances = {i["id"]: i for i in old_instances if isinstance(i, dict) and isinstance(i.get("id"), str)} if isinstance(old_instances, list) else {}
            if isinstance(proposed.get("instances"), list):
                for instance in proposed["instances"]:
                    if isinstance(instance, dict) and isinstance(instance.get("id"), str) and instance["id"] in old_instances:
                        instance["overrides"] = deepcopy(old_instances[instance["id"]].get("overrides", {}))
        from ..electrical import instruction_targets, requested_simulation
        if (self.current and "electrical" in self.extra
                and any(instruction_targets(self.description, self.extra["electrical"]))
                and "simulation" in lay.extra):
            drawn["simulation"] = requested_simulation(self.description, self.extra, lay.extra["simulation"], lay)
        lay.extra = {**deepcopy(self.extra), **drawn}
        # Geometry repair runs before program-owned identities are reconciled.
        # Never let a model-supplied ledger block that repair or replace saved IDs.
        lay.extra.pop("electrical", None)
        prof = self.prof
        lay, fixes = (lay, []) if "modules" in lay.extra else nudge(lay, prof)
        if "electrical" in self.extra:
            from ..electrical import synchronize
            lay.extra["electrical"] = deepcopy(self.extra["electrical"])
            lay.extra["electrical"] = synchronize(lay, prof.name)
        rep = check(lay, prof)
        errors = [f"{i.code}: {i.message}" for i in rep.errors] + _missing_netlist(lay)
        double = get_profile("ieee-double")
        passes_double = (bool(errors) and prof.name != double.name and
                         not _missing_netlist(lay) and check(lay, double).ok)
        return Evaluation(lay, errors, fixes, passes_double, rep)

    def unreadable(self, exc):
        return ("Your reply could not be read as a coordinate table: "
                f"{exc}.  Return the whole table in one ```json block.")

    def feedback(self, result, best, improved, previous):
        errors, lay, rep = result.errors, result.layout, result.report
        if previous is not None and errors == previous:
            return identical_message(errors, best.layout.to_dict(), best.errors)
        if not improved:
            return regression_message(errors, best.errors, best.layout.to_dict())
        return rework_message(errors, (rep.scale.x, rep.scale.y), self.prof.name,
                              lay.to_dict() if result.fixes else None)


def generate(description: str, profile: str = "ieee-single",
             rounds: int = DEFAULT_ROUNDS, chat: Chat | None = None,
             model: str | None = None, provider: str | None = None,
             log: Callable[[str], None] = lambda s: None,
             on_attempt: Callable[[Attempt], None] = lambda a: None,
             should_stop: Callable[[], bool] = lambda: False,
             structure: str | None = None,
             images: list[tuple[str, str]] | None = None,
             patience: int = 2, current: dict | None = None,
             on_text: Callable[[str], None] | None = None, workflow=None) -> Generation:
    """Ask the model for a table and loop on validator reports, at most
    ``rounds`` model replies.  Raises ModelError for configuration problems
    and Cancelled when ``should_stop`` turns true between rounds; a table
    that still fails is returned in ``Generation.best``.

    - ``structure``: a parts list the user confirmed (see ``plan``).
    - ``images``: reference figures as (media type, base64) pairs.
    - ``on_text``: called with reply text as it streams in.
    - ``current``: an existing table to change; ``description`` is then the
      change to make ("move D to the right"), not a circuit to draw.
    - Pins a hair too close are fixed by ``nudge`` and re-validated.
    - A single-column table that already passes in double column stops
      the loop (``Generation.passes_double``) instead of reworking.
    - A rewrite worse than the best so far is discarded; after ``patience``
      rounds without progress the loop stops early.
    - A reply cut off at the output limit is never parsed; it gets one
      compact rewrite inside ``rounds``, else the best table so far is kept.
    """
    if not description.strip():
        raise ValueError("描述为空")
    prof = get_profile(profile)
    workflow = workflow or CircuitWorkflow(prof.name, current, structure)
    rounds = min(rounds, getattr(workflow, "max_rounds", rounds))
    chat = chat or make_chat(workflow.system_prompt(), provider, model)
    gen = Generation(description, prof.name)
    text = workflow.first_message(description)
    pics = images
    best: Attempt | None = None
    stale = 0
    previous: list[str] | None = None
    truncation_retried = False
    for n in range(1, max(1, rounds) + 1):
        if should_stop():
            raise Cancelled("已取消")
        log(f"第 {n} 轮：等待模型写{workflow.document_kind}…")
        try:
            reply = _send(chat, text, pics, on_text, log, f"第 {n} 轮")
        except OutputTruncated as exc:
            att = Attempt(n, ["output-truncated: " + str(exc)])
            gen.attempts.append(att)
            on_attempt(att)
            if not truncation_retried and n < max(1, rounds):
                truncation_retried = True
                prepare = getattr(chat, "prepare_truncation_retry", None)
                reduced = bool(prepare and prepare())
                log("回复被截断；" + ("本次会话降低推理强度，" if reduced else "") +
                    "在剩余轮数内尝试一次紧凑重写，不提高输出上限。")
                text += ("\n\nThe previous request hit its output limit. Start a fresh complete "
                         "answer, not a continuation of partial JSON. Keep commentary to one "
                         "sentence and emit compact JSON (one element or wire per line). "
                         "Do not repeat the task, examples or lengthy analysis. Preserve the "
                         "requested circuit and all required fields; do not omit devices or wires.")
                continue
            if gen.best is not None:
                gen.notes.append("回复被截断且没有继续重试，已保留此前可读取的坐标表；请检查剩余校验问题。")
                break
            raise
        pics = None
        if should_stop():
            raise Cancelled("已取消")
        try:
            result = workflow.evaluate(reply)
            lay, errors, fixes = result.layout, result.errors, result.fixes
        except (ValueError, KeyError, TypeError) as exc:
            att = Attempt(n, [str(exc) if getattr(exc, "code", None) else f"bad-json: {exc}"], None, reply)
            gen.attempts.append(att)
            on_attempt(att)
            log(f"第 {n} 轮：回复不是合法{workflow.document_kind}（{exc}）")
            text = workflow.unreadable(exc)
            continue
        att = Attempt(n, errors, lay, reply, fixes)
        gen.attempts.append(att)
        if fixes:
            log(f"第 {n} 轮：代码微调了 {len(fixes)} 处间距：" + "；".join(fixes))
        passes_double = result.passes_double
        if not errors or passes_double:
            gen.selected, gen.passes_double = att, passes_double
            on_attempt(att)
            log(f"第 {n} 轮：" + ("单栏放不下，但双栏校验通过，停止返工" if passes_double
                                  else "校验通过"))
            break
        on_attempt(att)
        log(f"第 {n} 轮：校验未通过，{len(errors)} 个问题")
        improved = best is None or len(errors) < len(best.errors)
        if improved:
            best, stale = att, 0
        else:
            stale += 1
            if stale >= patience:
                log(f"连续 {stale} 轮没有进展，提前结束")
                break
        text = workflow.feedback(result, best, improved, previous)
        previous = errors
    return gen
