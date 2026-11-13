"""Electrical meaning of stacked drawing panels, independent of geometry.

Legacy panels still render. Simulation requires an explicit declaration and
must never infer electrical copies from a label, colour, or layer count.
Only a module resolver may supply ``resolved_instances``; JSON cannot attest
that its own omitted circuit has been expanded.
"""
from __future__ import annotations

from collections import Counter

from .layout import Layout

INSTANCE_STYLES = ("stack", "ellipsis")


def inspect_stacks(layout: Layout, *, resolved_instances=()) -> dict:
    usage = layout.extra.get("stack_usage", {})
    issues, style_issues = [], []
    if not isinstance(usage, dict):
        issues.append("stack_usage 必须是按区域 ID 索引的对象")
        usage = {}
    regions = layout.extra.get("regions", [])
    regions = regions if isinstance(regions, list) else []
    ids = Counter(r["id"] for r in regions if isinstance(r, dict)
                  and isinstance(r.get("id"), str) and r["id"])
    known = set(resolved_instances)
    stacks = []
    for key, spec in usage.items():
        if key not in ids or ids[key] != 1:
            issues.append(f"叠面板用途 {key!r} 没有唯一对应的区域，请检查区域 ID")
        if not isinstance(spec, dict) or spec.get("purpose") not in ("decoration", "instances"):
            issues.append(f"区域 {key} 的 purpose 必须为 decoration 或 instances")
    for i, region in enumerate(regions):
        if not isinstance(region, dict):
            continue
        key = region.get("id")
        key = key if isinstance(key, str) else None
        spec = usage.get(key)
        has_stack = "stack" in region
        if not has_stack and spec is None:
            continue
        name = f"区域 {key}" if key else f"第 {i + 1} 个区域"
        item = {"index": i, "id": key, "name": name,
                "purpose": spec.get("purpose") if isinstance(spec, dict) else None}
        stacks.append(item)
        if not key or ids[key] != 1 or spec is None:
            issues.append(f"{name} 的叠面板用途尚未确认。请选中区域，声明仅装饰，或列出其代表的电路实例；叠层不会自动复制器件。")
            continue
        if not isinstance(spec, dict) or spec.get("purpose") not in ("decoration", "instances"):
            continue
        purpose = spec["purpose"]
        allowed = {"purpose"} | ({"instances", "style"} if purpose == "instances" else set())
        if set(spec) - allowed:
            target = style_issues if "style" in set(spec) - allowed else issues
            target.append(f"{name} 的用途含未知字段：{sorted(set(spec) - allowed)}")
        if purpose == "decoration":
            continue
        style = spec.get("style", "stack")
        if style not in INSTANCE_STYLES:
            style_issues.append(f"{name} 的 style 只接受 {' / '.join(INSTANCE_STYLES)}")
        names = spec.get("instances")
        if (not isinstance(names, list) or not names
                or not all(isinstance(n, str) and n.strip() == n and n for n in names)
                or len(names) != len(set(names))):
            issues.append(f"{name} 必须列出不重复、非空的实例 ID（包含前面板），例如 A、B、C")
            continue
        if style == "ellipsis" and len(names) < 3:
            style_issues.append(f"{name} 的 ellipsis 至少需要三个真实实例")
        st = region.get("stack")
        count = st.get("count", 2) if isinstance(st, dict) else None
        if style == "stack" and has_stack and type(count) is int and len(names) != count + 1:
            issues.append(f"{name} 连同前面板共表示 {count + 1} 份电路，但仅声明 {len(names)} 个实例")
        missing = [n for n in names if n not in known]
        if missing:
            issues.append(f"{name} 代表的电路未完整定义／展开，缺少实例：{'、'.join(missing)}。不能按完整电路导出 SPICE；仅有实例名称不等于真实器件和连接。")
    return {"ok": not (issues or style_issues), "issues": issues + style_issues,
            "style_issues": style_issues, "stacks": stacks,
            "element_count": len(layout.elements)}


def simulation_completeness(layout: Layout) -> dict:
    if "modules" in layout.extra:
        from .modules import assemble
        try:
            assembly = assemble(layout)
        except (ValueError, TypeError, KeyError) as exc:
            return {"ok": False, "issues": [f"模块定义不完整：{exc}"], "stacks": [], "element_count": 0}
        out = inspect_stacks(layout, resolved_instances=assembly.instances)
        out["element_count"] = sum(e.kind != "port" for e in assembly.full.elements)
        return out
    return inspect_stacks(layout)
