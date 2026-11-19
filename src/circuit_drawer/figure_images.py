"""Imported paper artwork, kept in content-addressed project PNG files."""
from __future__ import annotations

import base64
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import tempfile
import zlib

MAX_BYTES = 16 * 1024 * 1024
MAX_PIXELS = 16_777_216  # A 4096-square clipboard image fits this import budget.
MAX_IMAGES = 64
FIELDS = {"id", "name", "source", "mono_source", "pixel_size", "from", "to", "foreground"}
SOURCE = re.compile(r"figure-assets/([a-f0-9]{64})\.png")


def png_size(data):
    if len(data) > MAX_BYTES or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("图片需要是最多16 MiB的PNG")
    position, size, image_data = 8, None, False
    while position + 12 <= len(data):
        length, kind = struct.unpack(">I4s", data[position:position + 8])
        end = position + length + 12
        if end > len(data):
            break
        payload = data[position + 8:end - 4]
        checksum = struct.unpack(">I", data[end - 4:end])[0]
        if zlib.crc32(kind + payload) != checksum:
            raise ValueError("PNG内容校验失败，请重新导入")
        if position == 8:
            if kind != b"IHDR" or length != 13:
                break
            size = struct.unpack(">II", payload[:8])
            if min(size) < 1 or size[0] * size[1] > MAX_PIXELS:
                raise ValueError("图片像素过大，请缩小到4096×4096以内")
        image_data |= kind == b"IDAT"
        if kind == b"IEND" and not length and end == len(data) and image_data:
            return size
        position = end
    raise ValueError("PNG文件不完整，请重新导入")


def validate(items):
    if not isinstance(items, list) or len(items) > MAX_IMAGES:
        raise ValueError("images需要是最多64项的列表")
    seen = set()
    for item in items:
        if not isinstance(item, dict) or set(item) - FIELDS:
            raise ValueError("图片字段无效")
        identifier = item.get("id")
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier) or identifier in seen:
            raise ValueError("图片id需要是唯一的字母数字标识")
        seen.add(identifier)
        if not isinstance(item.get("name", ""), str) or len(item.get("name", "")) > 120:
            raise ValueError(f"{identifier} 图片名称过长")
        for key in ("source", "mono_source"):
            if not isinstance(item.get(key), str) or not SOURCE.fullmatch(item[key]):
                raise ValueError(f"{identifier}.{key}需要引用导入后的figure-assets PNG")
        pixels = item.get("pixel_size")
        if not isinstance(pixels, list) or len(pixels) != 2 or any(type(v) is not int or v < 1 for v in pixels) or math.prod(pixels) > MAX_PIXELS:
            raise ValueError(f"{identifier}.pixel_size需要是图片像素宽高")
        for key in ("from", "to"):
            pair = item.get(key)
            if not isinstance(pair, list) or len(pair) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) for v in pair):
                raise ValueError(f"{identifier}.{key}需要是两个有限坐标")
        if any(a >= b for a, b in zip(item["from"], item["to"])):
            raise ValueError(f"{identifier} 图片框需要正的宽高")
        if type(item.get("foreground", True)) is not bool:
            raise ValueError(f"{identifier}.foreground需要是布尔值")
    return items


def items(raw):
    return validate(raw.get("images", []))


def safe_items(raw):
    try:
        return items(raw)
    except ValueError:
        return []


def read(source, base_dir):
    match = SOURCE.fullmatch(source) if isinstance(source, str) else None
    if not match or base_dir is None:
        raise ValueError("图片需要与JSON同目录的figure-assets文件夹")
    root = Path(base_dir).resolve()
    path = (root / source).resolve()
    if not path.is_relative_to(root):
        raise ValueError("图片来源超出图纸目录")
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("图片超过16 MiB")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != match[1]:
        raise ValueError("图片文件已改变，请重新导入")
    png_size(data)
    return data


def _write(data, folder):
    identifier = hashlib.sha256(data).hexdigest()
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (identifier + ".png")
    with tempfile.NamedTemporaryFile(dir=folder, suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(data)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return f"figure-assets/{identifier}.png"


def library_dir():
    from .launcher import settings_path
    return settings_path().parent / "image-library"


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def icon_preferences(update=None):
    path = library_dir() / "settings" / "generation.json"
    defaults = {"engine":"drawing", "image_model":"gpt-image-2.5-flare"}
    if update is None:
        try:
            values = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return defaults
    else:
        values = update
    if (not isinstance(values, dict) or values.get("engine") not in ("drawing", "image") or
            not isinstance(values.get("image_model"), str) or not 1 <= len(values["image_model"].strip()) <= 160):
        if update is None:
            return defaults
        raise ValueError("请填写接口支持的图像模型名")
    choice = {"engine":values["engine"], "image_model":values["image_model"].strip()}
    if update is not None:
        _write_json(path, choice)
    return choice


def copy_assets(raw, source_dir, destination):
    """Copy every referenced image before publishing a template or a new project."""
    sources = {item[key] for item in items(raw) for key in ("source", "mono_source")}
    data = [read(source, source_dir) for source in sources]
    for content in data:
        _write(content, Path(destination) / "figure-assets")


def import_image(req, base_dir):
    if any(not isinstance(req.get(key), str) or len(req[key]) > 4 * ((MAX_BYTES + 2) // 3)
           for key in ("png", "mono_png")):
        raise ValueError("图片需要是最多16 MiB的PNG")
    data = base64.b64decode(req.get("png", ""), validate=True)
    mono = base64.b64decode(req.get("mono_png", ""), validate=True)
    size = png_size(data)
    if png_size(mono) != size:
        raise ValueError("彩色和黑白图片尺寸需要一致")
    name = req.get("name", "Image")
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise ValueError("请填写1到120字的图片名称")
    destination = library_dir() if base_dir is None else Path(base_dir)
    asset = {"name": name, "pixel_size": list(size),
             "source": _write(data, destination / "figure-assets"),
             "mono_source": _write(mono, destination / "figure-assets")}
    if base_dir is None or req.get("remember"):
        folder = library_dir()
        if destination != folder:
            _write(data, folder / "figure-assets")
            _write(mono, folder / "figure-assets")
        identifier = Path(asset["source"]).stem
        _write_json(folder / (identifier + ".json"), asset)
    return asset


def library():
    result = []
    for path in sorted(library_dir().glob("*.json")):
        try:
            asset = json.loads(path.read_text(encoding="utf-8"))
            result.append({"id": path.stem, "name": str(asset["name"])[:120]})
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return result


def use_library(identifier, base_dir):
    if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{64}", identifier):
        raise ValueError("请选择个人素材")
    folder = library_dir()
    asset = json.loads((folder / (identifier + ".json")).read_text(encoding="utf-8"))
    for key in ("source", "mono_source"):
        data = read(asset[key], folder)
        _write(data, Path(base_dir) / "figure-assets")
    asset["preview"] = base64.b64encode(read(asset["source"], folder)).decode()
    return asset


def tex(raw, scale, base_dir, *, mono=False, foreground=True):
    from .geometry import image_box
    output = []
    for item in items(raw):
        if item.get("foreground", True) != foreground:
            continue
        source = item["mono_source" if mono else "source"]
        data = read(source, base_dir)
        if list(png_size(data)) != item["pixel_size"]:
            raise ValueError(f"{item['id']} 图片尺寸与记录不符")
        filename = "cd_image_" + Path(source).name
        encoded = base64.b64encode(data).decode()
        # build() materializes these bytes beside the portable TeX and in its staging folder.
        output.append(f"% CD_IMAGE_BEGIN {filename}\n")
        output.extend("% " + encoded[i:i + 120] + "\n" for i in range(0, len(encoded), 120))
        output.append("% CD_IMAGE_END\n")
        box = image_box(item, scale)
        output.append(rf"\node[anchor=south west,inner sep=0pt,outer sep=0pt] at ({box.x0/scale.x:g},{box.y0/scale.y:g}) "
                      rf"{{\includegraphics[width={box.x1-box.x0:g}cm,height={box.y1-box.y0:g}cm]{{{filename}}}}};" + "\n")
    return "".join(output)


def unpack_tex(tex):
    assets = {}
    pattern = re.compile(r"^% CD_IMAGE_BEGIN (cd_image_[a-f0-9]{64}\.png)\n((?:% [A-Za-z0-9+/=]+\n)+)% CD_IMAGE_END\n", re.M)
    def unpack(match):
        data = base64.b64decode("".join(line[2:] for line in match[2].splitlines()), validate=True)
        png_size(data)
        if hashlib.sha256(data).hexdigest() != match[1][9:-4]:
            raise ValueError("图片导出内容校验失败")
        assets[match[1]] = data
        return ""
    return pattern.sub(unpack, tex), assets
