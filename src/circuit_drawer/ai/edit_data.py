"""Preserve physical values while reconciling an AI edit's device references."""
from copy import deepcopy

from ..electrical import instruction_targets, problems, requested_simulation


def simulation_edit(old, new, proposed, description):
    """Return carried data and whether the resulting circuit needs a deck check.

    Only an unambiguous same-kind/same-pin-position pair is treated as a rename.
    Moving and renaming indistinguishable devices together must be split into
    separate edits, rather than guessing which physical values belong to each.
    """
    if not isinstance(proposed, dict):
        raise ValueError("AI 提议的仿真数据必须是对象")
    errors = [message for code, message in problems(old, {}, schema_only=True) if code == "bad-electrical"]
    if errors:
        raise ValueError("；".join(errors))
    carried = deepcopy(old.extra)
    sim = carried.get("simulation")
    if "simulation" not in carried:
        return carried, False
    if not isinstance(sim, dict):
        raise ValueError("simulation 必须是仿真参数对象")
    if "electrical" in carried and any(instruction_targets(description, carried["electrical"])):
        sim = carried["simulation"] = requested_simulation(
            description, old.extra, proposed.get("simulation"), new)
    before = {e.id: e for e in old.elements}
    after = {e.id: e for e in new.elements}
    if "modules" in old.extra or "modules" in new.extra:
        from ..modules import assemble
        before = {e.id: e for e in (assemble(old).full if "modules" in old.extra else old).elements}
        after = {e.id: e for e in (assemble(new).full if "modules" in new.extra else new).elements}
    signature = lambda parts: {i: (e.kind, set(e.pins)) for i, e in parts.items()}
    structural = signature(before) != signature(after)
    if not structural:
        return carried, False

    removed = [e for i, e in before.items() if i not in after]
    added = [e for i, e in after.items() if i not in before]
    renames = {}
    for e in removed:
        candidates = [n for n in added if n.kind == e.kind]
        matches = [n for n in candidates if n.pins == e.pins]
        if candidates:
            if len(matches) != 1 or sum(p.kind == e.kind and p.pins == e.pins for p in removed) != 1:
                raise ValueError(f"{e.id}：无法唯一确认更名后对应哪个元件；请把更名与移动／替换分开操作，保留原参数")
            renames[e.id] = matches[0].id

    def pin(ref):
        if not isinstance(ref, str) or "." not in ref:
            return ref
        ident, suffix = ref.rsplit(".", 1)
        return renames.get(ident, ident) + "." + suffix

    original = sim.get("components", {})
    if not isinstance(original, dict):
        raise ValueError("simulation.components 必须是元件参数表")
    supplied = proposed.get("simulation", {})
    supplied = supplied if isinstance(supplied, dict) else {}
    configs = supplied.get("components", {})
    configs = configs if isinstance(configs, dict) else {}
    kept = {renames.get(i, i): deepcopy(cfg) for i, cfg in original.items()
            if i in renames or (i in before and i in after and before[i].kind == after[i].kind)}
    for i, e in after.items():
        if e.kind != "port" and i not in kept and i in configs:
            kept[i] = deepcopy(configs[i])
    sim["components"] = kept
    pins = {f"{e.id}.{p}" for e in after.values() for p in e.pins}
    sim["ground"] = pin(sim.get("ground"))
    if sim["ground"] not in pins and "ground" in supplied:
        sim["ground"] = supplied["ground"]
    offered = supplied.get("probes", [])
    offered = {p.get("name"): p for p in offered if isinstance(p, dict)} if isinstance(offered, list) else {}
    probes = sim.get("probes", [])
    if not isinstance(probes, list) or any(not isinstance(p, dict) for p in probes):
        raise ValueError("simulation.probes 必须是测量通道对象列表")
    for probe in probes:
        replacement = offered.get(probe.get("name"), {})
        for key in ("positive", "negative", "element"):
            if key not in probe:
                continue
            probe[key] = pin(probe[key]) if key != "element" else renames.get(probe[key], probe[key])
            valid = probe[key] in pins if key != "element" else probe[key] in after
            if not valid and replacement.get("kind") == probe.get("kind") and key in replacement:
                probe[key] = replacement[key]
    ledger = carried.get("electrical")
    if ledger is not None:
        for node in ledger["nodes"]:
            node["pins"] = [pin(p) for p in node["pins"]]
        def branch(ref):
            ident, sep, winding = ref.partition(":")
            return renames.get(ident, ident) + sep + winding
        ledger["branches"] = {branch(ref): n for ref, n in ledger["branches"].items()}
    closed = carried.get("closed_loop")
    if "emi" in carried:
        from ..emi import FILTER_PARTS, settings
        settings(old)
        emi = carried["emi"]
        for item in emi.get("parasitics", []):
            item["element"] = renames.get(item["element"], item["element"])
            item["net"] = pin(item["net"])
            if item["element"] not in after or item["net"] not in pins:
                raise ValueError("EMI 寄生参数仍引用待删除的器件，请先调整 EMI 绑定")
        if "filter" in emi:
            for key in FILTER_PARTS:
                ref = emi["filter"][key]
                emi["filter"][key] = renames.get(ref, ref)
                if emi["filter"][key] not in after:
                    raise ValueError("EMI 滤波器仍引用待删除的器件，请先调整 EMI 绑定")
    if isinstance(closed, dict):
        for output, cfg in closed.get("outputs", {}).items():
            for key in ("switches", "complement"):
                if key in cfg:
                    cfg[key] = [renames.get(i, i) for i in cfg[key]]
                    if any(i not in after or after[i].kind not in ("nmos", "pmos", "igbt") for i in cfg[key]):
                        raise ValueError(f"closed_loop.outputs.{output}.{key} 引用了已删除或不再是开关的元件；请先调整闭环绑定")
    return carried, True
