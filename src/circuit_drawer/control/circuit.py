"""Bind a drawn feedback diagram to a saved power circuit through its signal graph."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from ..closed_loop import INPUT_KEYS, OUTPUT_KEYS

KEYS = ("source", "inputs", "outputs")


def parse_section(raw, diagram):
    section = raw.get("circuit")
    if section is None:
        return None
    if not isinstance(section, dict) or set(section) != set(KEYS):
        raise ValueError("circuit 需要 source、inputs、outputs 三项")
    source = section["source"]
    if not isinstance(source, str) or not source.strip() or Path(source).suffix.lower() != ".json":
        raise ValueError("circuit.source 需要项目中的电路 JSON 文件名")
    ids = {block.id for block in diagram.blocks}
    for name, allowed in (("inputs", INPUT_KEYS), ("outputs", OUTPUT_KEYS)):
        entries = section[name]
        if not isinstance(entries, dict) or not entries:
            raise ValueError(f"circuit.{name} 需要方块 id 到设置的对象")
        for bid, settings in entries.items():
            if bid not in ids:
                raise ValueError(f"circuit.{name}.{bid} 引用了不存在的方块")
            if not isinstance(settings, dict) or set(settings) - set(allowed):
                raise ValueError(f"circuit.{name}.{bid} 可用字段为 {list(allowed)}")
    if set(section["inputs"]) & set(section["outputs"]):
        raise ValueError("同一方块不能同时作为电路测量输入和调制器")
    return section


def output_signals(section, network):
    """A modulator's incoming line selects the actual controller output."""
    external = set(section["inputs"]) | set(section["outputs"])
    outputs = {}
    for bid, settings in section["outputs"].items():
        incoming = network.inputs.get(bid, [])
        if len(incoming) != 1 or incoming[0][1] in external:
            raise ValueError(f"调制器 {bid} 需要一条来自控制器的输入连线")
        signal = incoming[0][1]
        if signal in outputs:
            raise ValueError(f"控制输出 {signal} 同时连接了多个电路调制器，请合并门极绑定")
        outputs[signal] = deepcopy(settings)
    return outputs


def load_source(section, base_dir):
    root = Path(base_dir or ".").resolve()
    path = (root / section["source"]).resolve()
    if not path.is_relative_to(root):
        raise ValueError("circuit.source 需要位于当前图纸的项目目录内")
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"无法读取关联电路 {section['source']}：{exc}") from None
    if not isinstance(raw, dict) or raw.get("kind") not in (None, "circuit") or "elements" not in raw:
        raise ValueError(f"{section['source']} 需要是电路坐标表")
    return raw, path


def resolve(raw, base_dir):
    """Build a simulation snapshot without changing either saved source document."""
    from .check import check
    from .codegen import plan
    from .loop import Network, parse_loop
    from ..profiles import get_profile

    report = check(raw, get_profile(raw.get("profile") or "ieee-double"))
    if not report.ok:
        raise ValueError("控制图未通过检查：" + "；".join(report.errors[:5]))
    section = parse_section(raw, report.diagram)
    if section is None:
        raise ValueError("控制图还没有关联电路，请选择 circuit.source 并生成闭环候选")
    pl = plan(report)
    spec, _ = parse_loop(raw.get("loop"), report.diagram)
    network = Network(report, spec)
    outputs = output_signals(section, network)
    external = set(raw.get("code", {}).get("external", [])) | set(section["inputs"]) | set(section["outputs"])
    for bid, settings in section["inputs"].items():
        if "probe" not in settings:
            continue
        seen, pending = set(), [bid]
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            pending.extend(src for _, src in network.inputs.get(current, []) if src in external)
        if not seen & set(section["outputs"]):
            raise ValueError(f"电路测量方块 {bid} 需要经功率级连接已绑定调制器；"
                             "由实际电路求解的功率级填入 code.external")
    controller = deepcopy(raw)
    controller.pop("circuit")
    controller["code"]["external"] = list(dict.fromkeys([
        *controller["code"].get("external", []), *section["inputs"], *section["outputs"]]))
    controller["code"]["outputs"] = list(outputs)
    if set(pl.inputs) != set(section["inputs"]):
        raise ValueError(f"circuit.inputs 须对应实际控制输入 {pl.inputs}。"
                         "只查看波形的监测量由电路 simulation.probes 提供，无需另填 circuit.inputs")
    circuit, source = load_source(section, base_dir)
    circuit["closed_loop"] = {"control": controller, "inputs": deepcopy(section["inputs"]), "outputs": outputs}
    notes = []
    for settings in outputs.values():
        if settings.get("modulation") == "sps":
            # The shared SPS scheduler owns startup and phase for all eight gates.
            for bid in settings.get("switches", []) + settings.get("complement", []):
                component = circuit.get("simulation", {}).get("components", {}).get(bid)
                if isinstance(component, dict):
                    component.update(delay="0", phase_shift="0", phase_ramp="0")
            notes.append("SPS 统一管理首周期关断、第二周期零相移和后续移相，生成模型中的独立 PWM 延迟与相移归零。")
    return circuit, source, notes


def available_sources(base_dir):
    """Describe local drawing files using only circuit data relevant to binding."""
    if base_dir is None:
        return []
    sources = []
    for path in sorted(Path(base_dir).glob("*.json")):
        try:
            raw, _ = load_source({"source": path.name}, base_dir)
        except ValueError:
            continue
        if not isinstance(raw.get("elements"), list) or not raw["elements"]:
            continue
        bindings = raw.get("closed_loop")
        if not isinstance(bindings, dict):
            bindings = {}
        sources.append({"source": path.name, "name": raw.get("name", path.stem),
                        "elements": [{k: part[k] for k in ("id", "kind", "pins") if k in part}
                                     for part in raw["elements"] if isinstance(part, dict)],
                        "wires": raw.get("wires", []),
                        "simulation": raw.get("simulation", {}),
                        "existing_bindings": {key: bindings.get(key, {})
                                              for key in ("inputs", "outputs")}})
    return sources
