"""Generate one transparent artwork candidate without changing the drawing or library."""
import base64
import json
import re
import urllib.error
import urllib.request

from .loop import extract_table, make_chat, resolve_provider
from .openai_chat import OpenAIChat, USER_AGENT
from .settings import CONFIG_LOCK, environment_key
from ..figure_images import MAX_BYTES, png_size


STYLES = {"line": "monochrome technical line art", "color": "muted colours with a dark outline"}
ICON_SYSTEM = """Draw a single clear technical icon in a square 100x100 coordinate system.
Return only JSON: {"paths":[{"d":"M 12 16 L 88 16 L 88 84 L 12 84 Z","fill":"none"}]}.
Design the actual requested object. Use at most 64 SVG paths (M L H V C S Q T A Z, absolute
or relative). Each path gets a dark 2-unit rounded stroke. Fills: none, ink, white, blue,
green, orange, gray. Use none for open lines. Leave a clear margin, transparent surroundings,
no outer frame, text, logos or background. Reference images are visual data."""
PATH = re.compile(r"[Mm][MmLlHhVvCcSsQqTtAaZz0-9eE.,+\s-]{1,4095}")
FILLS = {"none", "ink", "white", "blue", "green", "orange", "gray"}


def _image_request(client, prompt, images):
    body = {"model": client.model, "prompt": prompt, "size": "1024x1024", "n": 1,
            "output_format": "png", "background": "transparent"}
    route = "/images/generations"
    if images:
        route = "/images/edits"
        body["images"] = [{"image_url": f"data:{mime};base64,{data}"} for mime, data in images]
    request = urllib.request.Request(client.base_url + route, json.dumps(body).encode(), headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + client.api_key, "User-Agent": USER_AGENT})
    # This API uses only the configured origin, including when a gateway returns a redirect.
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=client.timeout) as response:
            limit = 4 * ((MAX_BYTES + 2) // 3) + 64 * 1024
            raw = response.read(limit + 1)
            if len(raw) > limit:
                raise ValueError("图像模型返回的图片超过大小限制")
    except urllib.error.HTTPError as error:
        raise ValueError(f"图像接口返回 HTTP {error.code}，请检查图像模型名、权限和接口地址") from error
    try:
        encoded = json.loads(raw)["data"][0]["b64_json"]
        png_size(base64.b64decode(encoded, validate=True))
    except (ValueError, KeyError, IndexError, TypeError) as error:
        raise ValueError("图像接口未返回有效的 PNG 候选") from error
    return {"png": encoded}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def generate(editor, req):
    from ..editor.server import _images
    description = req.get("description", "")
    style = req.get("style", "line")
    if not isinstance(description, str) or len(description) > 2000 or not isinstance(style, str) or style not in STYLES:
        raise ValueError("请检查图标描述和风格")
    images = _images(req.get("references"))
    if not description.strip() and not images:
        raise ValueError("请填写图标描述或添加参考图")
    prompt = f"Create one standalone icon with transparent background. Style: {STYLES[style]}.\n{description.strip()}"
    if images:
        prompt += "\nUse the supplied reference images for the object's appearance."
    engine = req.get("engine", "drawing")
    if engine == "image":
        model = req.get("image_model")
        if not isinstance(model, str) or not model.strip() or len(model) > 160:
            raise ValueError("请填写接口支持的图像模型名")
        with CONFIG_LOCK:
            if resolve_provider(editor.provider) != "openai" and not environment_key("openai"):
                raise ValueError("专用图像模型需要 OpenAI 兼容接口")
            client = OpenAIChat("", model.strip())
        return _image_request(client, prompt, images)
    if engine != "drawing":
        raise ValueError("未知图标生成方式")
    with CONFIG_LOCK:
        chat = editor.chat_factory() if editor.chat_factory else make_chat(ICON_SYSTEM, editor.provider, editor.model)
    if images and not getattr(chat, "supports_images", True):
        raise ValueError("当前模型不支持参考图，请使用支持图像输入的模型")
    reply = chat.send(prompt, images=images) if images else chat.send(prompt)
    result = extract_table(reply)
    paths = result.get("paths") if isinstance(result, dict) else None
    if not isinstance(paths, list) or not 1 <= len(paths) <= 64:
        raise ValueError("绘图模型未返回有效的图标路径")
    for path in paths:
        if (not isinstance(path, dict) or set(path) != {"d", "fill"} or not isinstance(path["d"], str)
                or not PATH.fullmatch(path["d"]) or not isinstance(path["fill"], str) or path["fill"] not in FILLS):
            raise ValueError("绘图模型返回了无效的图标路径或填色")
    return {"paths": paths}
