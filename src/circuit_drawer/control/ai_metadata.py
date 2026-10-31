"""Validate AI structural boundary/dynamics metadata without changing simulation."""


def metadata_errors(value, blocks):
    allowed = {"inputs", "outputs", "stateful", "dynamic_links"}
    if not isinstance(value, dict) or set(value) - allowed:
        return ["control-schema: control_ai 只允许 inputs/outputs/stateful/dynamic_links 列表"]
    by_id = {b.id: b for b in blocks}
    errors = []
    for key, ids in value.items():
        if key == "dynamic_links":
            if not isinstance(ids, list) or any(not isinstance(link, dict) or set(link) != {"from", "to"} or
                    any(not isinstance(ref, str) or "." not in ref or ref.split(".", 1)[0] not in by_id for ref in link.values()) for link in ids):
                errors.append("control-endpoint: control_ai.dynamic_links 必须是 from/to 端口引用列表")
            continue
        if not isinstance(ids, list) or any(not isinstance(i, str) or i not in by_id for i in ids):
            errors.append(f"control-block: control_ai.{key} 必须引用已存在的方块 id")
        elif key == "stateful" and any(by_id[i].type != "block" for i in ids):
            errors.append("control-type: stateful 只能声明有真实内部状态、无直通项的 block")
    return errors
