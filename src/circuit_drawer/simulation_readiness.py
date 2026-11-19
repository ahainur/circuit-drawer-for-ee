"""Explain the same operating point that the circuit simulator will use."""
import re

from . import closed_loop, pmsm, spice


def inspect(layout, profile, base_dir=None):
    issues = []

    def issue(target, message, device="", field=""):
        issues.append({"target": target, "device": device, "field": field, "message": str(message)})

    original = layout
    try:
        layout, report = spice.connected_layout(layout, profile)
        nets = report.nets
    except ValueError as exc:
        issue("drawing", exc)
        nets = {f"{e.id}.{p}": layout.netlist.get(f"{e.id}.{p}") for e in layout.elements for p in e.pins}
    sim = layout.extra.get("simulation")
    sim = sim if isinstance(sim, dict) else {}
    components = sim.get("components")
    components = components if isinstance(components, dict) else {}
    ids = {e.id: e for e in layout.elements if e.kind != "port"}
    if not isinstance(sim.get("ground"), str) or sim["ground"] not in nets:
        issue("ground", "请选择当前电路的参考地引脚")
    for key, element in ids.items():
        cfg = components.get(key)
        if element.kind not in spice.FIELDS:
            continue  # the connectivity check reports unsupported devices
        if not isinstance(cfg, dict):
            issue("component", f"{key} · 缺少器件参数", key)
            if element.kind in spice.PWM_SWITCHES and "g" not in element.pins:
                issue("drive", f"{key} · 缺少内部 PWM 驱动参数", key, "frequency")
            continue
        external = element.kind in spice.PWM_SWITCHES and cfg.get("gate_drive", "external" if "g" in element.pins else "internal") == "external"
        try:
            if element.kind == "pmsm":
                pmsm.parameters(cfg, key, spice.number)
            else:
                spice.component_values(element, cfg, external)
        except ValueError as exc:
            required, optional = spice.component_fields(element.kind, external)
            field = next((k for k in (*required, *optional) if f"{key}.{k}" in str(exc)), "")
            if not field:
                field = next((k for k in required if cfg.get(k) in ("", None)), "")
            if "占空比" in str(exc):
                field = "duty"
            elif "导通电阻 Ron" in str(exc):
                field = "ron"
            issue("drive" if field in spice.PWM_FIELDS or field == "gate_drive" else "component", exc, key, field)
    try:
        spice.transient_values(sim.get("transient", {}))
    except ValueError as exc:
        issue("transient", exc)
    probes = sim.get("probes")
    probes = probes if isinstance(probes, list) else []
    if not 1 <= len(probes) <= spice.MAX_PROBES:
        issue("probes", f"请选择 1–{spice.MAX_PROBES} 个测量通道")
    used = set()
    for i, probe in enumerate(probes):
        try:
            spice.probe_definition(probe, i, ids, nets, used)
        except ValueError as exc:
            issue("probes", f"#{i+1} · {exc}")
    loop = None
    if "closed_loop" in original.extra:
        try:
            loop = closed_loop.load(original.extra["closed_loop"], base_dir, components,
                                    {key: e.kind for key, e in ids.items()},
                                    [p.get("name") for p in probes if isinstance(p, dict)])
        except (ValueError, KeyError, TypeError) as exc:
            binding = re.search(r"closed_loop\.(inputs|outputs)\.([^\s：:]+)", str(exc))
            issue("closed_loop", exc, field=binding.group(0) if binding else "")
    if isinstance(sim.get("components"), dict):
        try:
            spice.gate_drives(layout, nets, loop, partial=True)
        except (ValueError, KeyError, TypeError) as exc:
            key = next((k for k in ids if str(exc).startswith((k + ":", k + "."))), "")
            issue("drive", exc, key, "gate_drive")
    if not issues:
        try:
            spice.make_deck(original, profile, base_dir)
        except (ValueError, KeyError, TypeError) as exc:
            msg = str(exc)
            key = next((k for k in ids if msg.startswith((k + ":", k + "."))), "")
            field = ""
            if "时间步长" in msg or "结束时间" in msg:
                target, key = "transient", ""
            elif key:
                target = "component"
                if ids[key].kind in spice.PWM_SWITCHES and any(s in msg for s in ("上升", "启动延迟", "移相")):
                    target, field = "drive", "rise" if "上升" in msg else "delay"
            else:
                target = "probes" if "probe" in msg or "通道" in msg else "closed_loop" if loop else "drawing"
            issue(target, msg, key, field)
    return {"ready": not issues, "issues": issues,
            "ground": sim.get("ground"), "transient": sim.get("transient", {}),
            "channels": [p["name"] for p in probes if isinstance(p, dict) and isinstance(p.get("name"), str)]}
