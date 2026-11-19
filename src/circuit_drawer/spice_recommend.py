"""Reviewable simulation proposals; never edit geometry or save the drawing.

CCM estimates follow TI SLVA477 / SLVA372 with ideal duty and zero capacitor
ESR. These are a starting point for the existing simplified simulator, not a
component rating, loss estimate, or proof that a transient reached equilibrium.
"""
from __future__ import annotations

import csv
import json
import math
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from . import spice
from .ai.loop import Evaluation, extract_table
from .layout import Layout


# Demonstration operating points, explicitly shown when no requirements exist.
DEFAULTS = {"vin": 24, "power": 24, "frequency": 50000,
            "current_ripple_pct": 30, "voltage_ripple_pct": 1}
INPUTS = {"vin": "输入电压 (V)", "vout": "输出电压 (V)", "power": "输出功率 (W)",
          "frequency": "开关频率 (Hz)", "current_ripple_pct": "电感纹波峰峰值 / 平均值 (%)",
          "voltage_ripple_pct": "输出纹波峰峰值 / 输出电压 (%)"}
# Bound preview cost separately from the proposed full simulation. Keep the
# original integration/sample resolution, and report the actual checked window.
CHECK_SECONDS = 0.01
CHECK_POINTS = 100_000
CHECK_TIMEOUT = 20
SAMPLES_PER_PERIOD = 100
SETTLING_CYCLES = 400
RC_TIME_CONSTANTS = 10
# An explicitly labelled illustration, not an inferred device/model parameter.
EXAMPLE_DIODE_DROP = 0.7


def requirements(value):
    if not isinstance(value, dict) or value.keys() - INPUTS.keys():
        raise spice.SpiceError("parameter-requirements: 需求只支持界面所列的电压、功率、频率和纹波字段")
    result = {}
    for key, raw in value.items():
        if raw is None or raw == "":
            continue
        val = spice.number(raw, INPUTS[key])
        if val <= 0 or (key.endswith("_pct") and val > 100):
            raise spice.SpiceError(f"parameter-requirements: {INPUTS[key]} 必须大于 0；纹波比例不超过 100%")
        result[key] = val
    return result


def parameter_values(simulation):
    """Editable parameter paths, excluding the structural probe description."""
    if not isinstance(simulation, dict):
        return {}
    result = {"ground": simulation.get("ground")}
    def visit(obj, prefix):
        for key, value in obj.items():
            path = prefix + "." + key
            if isinstance(value, dict):
                visit(value, path)
            else:
                result[path] = value
    for section in ("components", "transient"):
        if isinstance(simulation.get(section), dict):
            visit(simulation[section], section)
    result["probes"] = simulation.get("probes")
    return result


def parameter_label(path, layout, simulation):
    """Reuse the parameter editor's field names and units in review tables."""
    keys = path.split(".")
    if keys[0] == "components" and len(keys) >= 3:
        element = next((e for e in layout.elements if e.id == keys[1]), None)
        if element:
            fields = spice.FIELDS.get(element.kind, []) + spice.OPTIONAL_FIELDS.get(element.kind, [])
            if keys[2] == "wave" and len(keys) == 4:
                wave = simulation["components"][element.id].get("wave", {})
                fields = spice.SOURCE_WAVE_FIELDS.get(wave.get("kind"), [])
            names = {key: label for key, label, _ in fields}
            names.update(model="仿真模型", polarity="绕组极性", gate_drive="栅极驱动", gate_source="驱动电压源", kind="激励类型")
            return element.id + " · " + names.get(keys[-1], keys[-1])
    if keys[0] == "transient" and len(keys) == 2:
        return next((label for key, label, _ in spice.TRANSIENT_FIELDS if key == keys[1]), path)
    return {"ground": "参考地", "probes": "测量通道"}.get(path, path)


def recognize(layout, profile):
    if layout.extra.get("model_mapping"):
        return None, None  # explicit advanced models need their own assumptions
    for topology in ("buck", "boost"):
        try:
            return topology, spice.example_settings(layout, topology, profile)
        except spice.SpiceError:
            pass
    return None, None


def formula(layout, topology, preset, requested):
    req = {**DEFAULTS, **requested}
    req.setdefault("vout", req["vin"] / 2 if topology == "buck" else 2 * req["vin"])
    vin, vo, power, fs = (req[k] for k in ("vin", "vout", "power", "frequency"))
    if (topology == "buck" and vo >= vin) or (topology == "boost" and vo <= vin):
        relation = "低于" if topology == "buck" else "高于"
        fallback = f"；Vin 未填写，采用默认 {vin:g} V" if "vin" not in requested else ""
        raise spice.SpiceError(f"parameter-ratio: {topology.title()} 要求输出{relation}输入"
                               f"（Vin={vin:g} V，Vout={vo:g} V）{fallback}；请修改需求")
    n = sum(e.kind in ("nmos", "pmos") for e in layout.elements)
    duty = vo / vin if topology == "buck" else 1 - vin / vo
    diode_loss = (1 - duty) * EXAMPLE_DIODE_DROP if topology == "buck" else EXAMPLE_DIODE_DROP
    io = power / vo
    il = io / n if topology == "buck" else power / vin
    delta_i = il * req["current_ripple_pct"] / 100
    delta_v = vo * req["voltage_ripple_pct"] / 100
    inductance = ((vin - vo) if topology == "buck" else vin) * duty / (fs * delta_i)
    # Conservative sum of phase ripple: no claimed cancellation benefit.
    capacitance = n * delta_i / (8 * fs * delta_v) if topology == "buck" else io * duty / (fs * delta_v)
    resistance = vo * vo / power
    sim = deepcopy(preset)
    reasons = {key: "沿用现有示例的简化模型、初始条件或测量定义；请核对模型适用范围。"
               for key in parameter_values(sim)}
    def set_value(element, key, value, why):
        if not math.isfinite(value):
            raise spice.SpiceError("parameter-range: 需求导致非有限数值，请使用合理的电压、功率和频率")
        sim["components"][element.id][key] = f"{value:.12g}"
        reasons[f"components.{element.id}.{key}"] = why
    phase = 0
    for e in layout.elements:
        if e.kind == "V":
            set_value(e, "dc", vin, "采用需求中的输入电压；留空时使用列出的演示工况。")
        elif e.kind == "R":
            set_value(e, "value", resistance, f"R = Vout²/Pout = {resistance:g} Ω。")
        elif e.kind == "L":
            set_value(e, "value", inductance, ("L = (Vin−Vout)·D/(fs·ΔIL)" if topology == "buck" else
                      "L = Vin·D/(fs·ΔIL)") + f"；每相平均电流 {il:g} A，纹波 {delta_i:g} A。")
        elif e.kind == "C":
            set_value(e, "value", capacitance, ("C ≥ N·ΔIL/(8·fs·ΔVout)，按各相纹波相加保守估算" if topology == "buck" else
                      "C ≥ Iout·D/(fs·ΔVout)") + "；未计入 ESR 和负载瞬态余量。")
        elif e.kind in ("nmos", "pmos"):
            set_value(e, "duty", duty, "理想 CCM 占空比：" + ("D=Vout/Vin" if topology == "buck" else "D=1−Vin/Vout") +
                      f"；若 VF={EXAMPLE_DIODE_DROP:g} V（仅举例，并非当前模型的固定压降），"
                      + ("ΔVout≈(1−D)·VF" if topology == "buck" else "ΔVout≈VF") +
                      f"，仅此项使输出约低 {diode_loss:g} V（{100*diode_loss/vo:.2f}%）。"
                      "实际还受开关电阻与启动过程影响，请查看短仿真偏差；这里未自动补偿。")
            set_value(e, "frequency", fs, "采用需求中的开关频率。")
            set_value(e, "delay", phase / n / fs, f"第 {phase+1}/{n} 相，相位依次错开 360°/N。")
            for key in ("rise", "fall"):
                set_value(e, key, min(1e-8, min(duty, 1-duty) / fs / 20), "有限开关边沿；小于最短脉宽的 1/20。")
            phase += 1
    stop = max(SETTLING_CYCLES / fs, RC_TIME_CONSTANTS * resistance * capacitance)
    step = 1 / fs / SAMPLES_PER_PERIOD
    sim["transient"].update(step=f"{step:.12g}", max_step=f"{step:.12g}", stop=f"{stop:.12g}")
    for key in ("step", "max_step"):
        reasons[f"transient.{key}"] = f"每开关周期 {SAMPLES_PER_PERIOD} 个采样点，积分步长不大于采样间隔。"
    reasons["transient.stop"] = f"取 {SETTLING_CYCLES} 个周期与 {RC_TIME_CONSTANTS}RC 中较长者，仍需检查是否稳态。"
    assumptions = [f"未指定 {INPUTS[k]}，使用 {req[k]:g}。" for k in req if k not in requested and k != "vout"]
    if "vout" not in requested:
        assumptions.append(f"未指定输出电压，按 {'Vin/2' if topology == 'buck' else '2×Vin'} 推算，采用 {vo:g} V。")
    assumptions += ["理想 CCM 初稿：忽略损耗、ESR、额定值和控制器补偿；不自动优化器件。"]
    return {"simulation": sim, "requirements": req, "reasons": reasons, "assumptions": assumptions,
            "method": f"{n} 相 Buck 公式" if topology == "buck" else "Boost 公式"}


def quick_check(layout, profile, base_dir=None, *, should_stop=None):
    """Run a disposable copy. Existing output files and JSON are untouched."""
    deck = spice.make_deck(layout, profile, base_dir)
    if not spice.executable():
        return {"status": "unavailable", "message": "未找到 ngspice，尚未做短仿真，不能采用。安装并配置后重新推荐。"}
    candidate = Layout.from_dict(layout.to_dict())
    stop = min(deck.stop, CHECK_SECONDS, deck.step * CHECK_POINTS)
    if stop <= deck.step:
        raise spice.SpiceError("parameter-window: 采样间隔太大，10 ms 内不足两个采样点；请缩短采样间隔")
    candidate.extra["simulation"]["transient"]["stop"] = f"{stop:.12g}"
    with tempfile.TemporaryDirectory(prefix="cd_recommend_") as folder:
        spice.export_spice(candidate, folder, "preview", profile=profile, run=True,
                           timeout=CHECK_TIMEOUT, base_dir=base_dir,
                           **({"should_stop": should_stop} if should_stop else {}))
        with (Path(folder) / "preview.csv").open(encoding="utf-8", newline="") as file:
            reader = csv.DictReader(file)
            columns = [c for c in reader.fieldnames if c != "time_s"]
            totals = {c: {"sum": 0., "min": math.inf, "max": -math.inf} for c in columns}
            count = 0
            for row in reader:
                if float(row["time_s"]) < stop * .8:
                    continue
                count += 1
                for c in columns:
                    val = float(row[c]); stats = totals[c]
                    stats["sum"] += val; stats["min"] = min(stats["min"], val); stats["max"] = max(stats["max"], val)
    if not count:
        raise spice.SpiceError("parameter-window: 短仿真没有末段样本，请缩短采样间隔")
    return {"status": "completed", "stop_s": stop, "window_start_s": stop * .8,
            "samples": count, "channels": {c: {"mean": s["sum"]/count, "min": s["min"], "max": s["max"]}
                                            for c, s in totals.items()},
            "message": "短仿真完成；以下是末段 20% 时窗统计，不代表已稳态或满足纹波、损耗和额定值要求。"}


@dataclass
class Proposal:
    data: dict

    def to_dict(self):
        return deepcopy(self.data)


def tuning_parameters(layout, profile, base_dir):
    """A whitelist of existing numbers, with destinations in the authored table."""
    from .modules import _definitions
    full, _, assembly = spice.resolved_layout(layout, profile)
    source = assembly.full if assembly else full
    catalog = {}
    def add(keys, value, label, name=None):
        path = name or ".".join(map(str, keys))
        catalog[path] = {"keys": keys, "value": value, "label": label}

    spec = layout.extra.get("modules", {})
    instances = {i["id"]: (n, i) for n, i in enumerate(spec.get("instances", []))}
    definitions = _definitions(spec) if assembly else {}
    for e in source.elements:
        values = source.extra.get("simulation", {}).get("components", {}).get(e.id, {})
        owner, _, local = e.id.partition("/")
        fields = [(k, label, (k,)) for k, label, _ in
                  [*spice.FIELDS.get(e.kind, []), *spice.OPTIONAL_FIELDS.get(e.kind, [])] if k in values]
        wave = values.get("wave", {})
        fields += [("wave", label, ("wave", k)) for k, label, _ in
                   spice.SOURCE_WAVE_FIELDS.get(wave.get("kind"), []) if k in wave]
        for field, label, tail in fields:
            value = values[tail[0]] if len(tail) == 1 else wave[tail[1]]
            if owner in instances and local:
                n, instance = instances[owner]
                definition = definitions[instance["definition"]]
                if "editable" in definition and not any(p["element"] == local and p["field"] == field
                                                        for p in definition["editable"]):
                    continue
                keys = ("modules", "instances", n, "overrides", local, *tail)
                name = ".".join(("modules", owner, local, *tail))
            else:
                keys = ("simulation", "components", e.id, *tail)
                name = None
            add(keys, value, f"{e.id} · {label}", name)
    for k, label, _ in spice.TRANSIENT_FIELDS:
        values = layout.extra["simulation"]["transient"]
        if k in values:
            add(("simulation", "transient", k), values[k], label)

    control = None
    if "closed_loop" in layout.extra:
        from .closed_loop import controller, INPUT_KEYS
        control, _, _ = controller(layout.extra["closed_loop"]["control"], base_dir)
        def numeric_leaves(value, keys):
            if isinstance(value, dict):
                for k, v in value.items():
                    if k not in ("model", "out", "port", "note"):
                        numeric_leaves(v, (*keys, k))
            elif isinstance(value, list):
                for i, v in enumerate(value):
                    numeric_leaves(v, (*keys, i))
            elif not isinstance(value, bool):
                try:
                    number = float(value)
                except (ValueError, TypeError):
                    return  # symbolic relationships remain formulas, not independent knobs
                if math.isfinite(number):
                    add(("closed_loop", "control", *keys), value, "控制器 · " + ".".join(map(str, keys)))
        for k in ("params", "tf"):
            numeric_leaves(control.get("loop", {}).get(k, {}), ("loop", k))
        numeric_leaves(control.get("code", {}).get("blocks", {}), ("code", "blocks"))
        if "fs" in control.get("code", {}):
            numeric_leaves(control["code"]["fs"], ("code", "fs"))
        for bid, cfg in layout.extra["closed_loop"]["inputs"].items():
            for k in INPUT_KEYS:
                if k != "probe" and k in cfg:
                    add(("closed_loop", "inputs", bid, k), cfg[k], f"控制输入 {bid} · {k}")
    return full, catalog, control


def apply_parameters(layout, catalog, updates, control=None):
    if not isinstance(updates, dict) or not updates or set(updates) - catalog.keys():
        raise spice.SpiceError("parameter-updates: updates 只允许当前 adjustable 清单中的参数路径，至少提供一项；不能修改模块定义、连线、模型或控制结构")
    raw, values = layout.to_dict(), {}
    for name, value in updates.items():
        p = catalog[name]
        value = spice.number(value, name)
        values[name] = value
        if value == spice.number(p["value"], name):
            continue  # keeping a default must not freeze it as an instance override
        keys = p["keys"]
        if keys[:2] == ("closed_loop", "control") and not isinstance(raw["closed_loop"]["control"], dict):
            raw["closed_loop"]["control"] = deepcopy(control)
        target = raw
        for key in keys[:-1]:
            target = target[key] if isinstance(target, list) else target.setdefault(key, {})
        target[keys[-1]] = value
    return Layout.from_dict(raw), values


class RecommendationWorkflow:
    document_kind = "仿真参数建议"
    max_rounds = 3

    def __init__(self, layout, profile="ieee-single", requested=None, description="", base_dir=None,
                 *, use_formula=True, should_stop=None, preview_simulation=True, example_mode=False, example_case=None):
        self.layout = Layout.from_dict(layout.to_dict())
        self.original = layout
        self.example_mode = example_mode
        self.example_name = None
        if example_mode:
            from .simulation_examples import match, overlay
            matched = match(self.layout, profile, example_case)
            if matched:
                self.example_name, settings = matched
                for key, value in settings.items():
                    self.layout.extra[key] = overlay(value, self.layout.extra.get(key, {}))
        self.profile, self.base_dir = profile, base_dir
        self.tuning = "modules" in self.layout.extra or "closed_loop" in self.layout.extra
        if example_mode and self.tuning and "modules" not in self.layout.extra:
            try:
                spice.make_deck(self.layout, profile, base_dir)
            except ValueError:
                self.tuning = False  # a partial operating point needs a complete parameter proposal
        self.repair_bindings = (example_mode and not self.tuning and
                                isinstance(self.layout.extra.get("closed_loop"), dict) and "control" in self.layout.extra["closed_loop"])
        self.catalog, self.control = {}, None
        if self.tuning:
            # Tuning starts from a runnable operating point; it never invents bindings or probes.
            spice.make_deck(self.layout, profile, base_dir)
            self.full, self.catalog, self.control = tuning_parameters(self.layout, profile, base_dir)
        else:
            spice.connected_layout(self.layout, profile)
            self.full = self.layout
            if "simulation" in self.layout.extra:
                try:
                    spice.make_deck(self.layout, profile, base_dir)
                    _, self.catalog, _ = tuning_parameters(self.layout, profile, base_dir)
                except spice.SpiceError:
                    pass  # incomplete configurations still need a full proposal
        self.requested = requirements({} if requested is None else requested)
        if not isinstance(description, str) or len(description) > 8000:
            raise spice.SpiceError("parameter-description: 文字需求必须是最多 8000 字的文本")
        self.description = description.strip()
        self.topology, self.preset = (None, None) if self.tuning or not use_formula else recognize(self.layout, profile)
        self.should_stop = should_stop
        self.preview_simulation = preview_simulation
        self.use_ai = self.topology is None or bool(self.description)

    def system_prompt(self):
        from .ai.prompt import simulation_parameter_prompt
        prompt = simulation_parameter_prompt(self.topology, INPUTS, tuning=self.tuning,
                                            incremental=bool(self.catalog),
                                            kinds={e.kind for e in self.full.elements})
        if self.example_name:
            prompt += (f"\n已按实际器件和连接匹配示例工况 {self.example_name}，当前值已叠加用户填写的值。"
                       "复用适用值，只为用户目标或缺项提出修改。示例完全适用时返回 updates:{}、"
                       "requirements、reasons:{} 及非空 assumptions，仍要交给用户采用。")
        if self.repair_bindings:
            prompt += ("\n现有闭环工况不完整。随完整 simulation 可提供 bindings:{inputs:{...},outputs:{...}} 修正测量与驱动映射，"
                       "两项均需完整。不得更换控制器。在 reasons.bindings 说明绑定依据。")
        if self.example_mode and self.catalog and not self.example_name:
            prompt += "\n已填写的工况完全适用时，可以返回 updates:{}、reasons:{}、requirements 和非空 assumptions 供用户确认。"
        return prompt

    def first_message(self, description):
        graph = {"elements": [{"id": e.id, "kind": e.kind, "pins": list(e.pins), "opts": e.opts}
                               for e in self.full.elements], "netlist": self.full.netlist,
                 "simulation": self.full.extra.get("simulation"), "model_mapping": self.layout.extra.get("model_mapping")}
        graph["component_ids"] = [e.id for e in self.full.elements if e.kind != "port"]
        if self.repair_bindings:
            graph["closed_loop"] = self.layout.extra["closed_loop"]
        if not self.tuning:
            graph["component_fields"] = self.component_contract(self.full.extra.get("simulation") or {})
        if self.catalog:
            graph["adjustable"] = {k: {"value": v["value"], "label": v["label"]} for k, v in self.catalog.items()}
            if self.control is not None:
                graph["closed_loop"] = {**self.layout.extra["closed_loop"], "control": self.control}
        basis = "需求为空时以当前工况为基准调参，保留或调整均说明理由。" if self.tuning else "需求为空时按此拓扑常见的演示工况提议，明确列出假设。"
        return (basis + "文字需求：" + (self.description or "未指定") +
                "\n已填写的固定要求（优先遵守）：" + json.dumps(self.requested, ensure_ascii=False) +
                "\n当前连接与参数：" + json.dumps(graph, ensure_ascii=False))

    def component_contract(self, simulation):
        result = {}
        components = simulation.get("components", {}) if isinstance(simulation, dict) else {}
        if not isinstance(components, dict):
            components = {}
        for element in self.full.elements:
            if element.kind == "port":
                continue
            config = components.get(element.id, {})
            mode = config.get("gate_drive", "external" if "g" in element.pins else "internal") if isinstance(config, dict) else "internal"
            required, optional = spice.component_fields(element.kind, mode == "external" and element.kind in spice.PWM_SWITCHES)
            result[element.id] = {"required": required, "optional": optional}
            if element.kind in spice.MODELS:
                result[element.id]["model"] = spice.MODELS[element.kind]
        return result

    def formula(self):
        return self._check(formula(self.layout, self.topology, self.preset, self.requested))

    def _check(self, data):
        patch = self.tuning or isinstance(data, dict) and "updates" in data
        allowed = {"updates" if patch else "simulation", "requirements", "reasons", "assumptions", "method"}
        if self.repair_bindings and not patch:
            allowed.add("bindings")
        if not isinstance(data, dict) or set(data) - allowed:
            raise spice.SpiceError(f"parameter-fields: 只能返回 {sorted(allowed)}；不能修改坐标、接线、模型映射或控制结构")
        req = requirements(data.get("requirements", {}))
        if any(k not in req or not math.isclose(v, req[k], rel_tol=1e-9) for k, v in self.requested.items()):
            raise spice.SpiceError("parameter-requirements: 返回的 requirements 必须保留所有固定要求")
        data = deepcopy(data)
        data["requirements"] = req
        candidate = Layout.from_dict(self.layout.to_dict())
        if patch:
            if self.example_mode and self.catalog and data.get("updates") == {}:
                values = {}
            else:
                candidate, values = self._tuned(data.get("updates"))
        else:
            if "bindings" in data:
                bindings = data["bindings"]
                spice._keys(bindings, ("inputs", "outputs"), label="bindings")
                candidate.extra["closed_loop"].update(deepcopy(bindings))
                data["closed_loop"] = deepcopy(candidate.extra["closed_loop"])
            candidate.extra["simulation"] = deepcopy(data.get("simulation"))
            simulation = candidate.extra["simulation"]
            if isinstance(simulation, dict) and isinstance(simulation.get("components"), dict):
                contract = self.component_contract(simulation)
                errors = []
                for key, fields in contract.items():
                    try:
                        spice._keys(simulation["components"].get(key), fields["required"], fields["optional"],
                                    label="components." + key)
                    except spice.SpiceError as exc:
                        errors.append(str(exc))
                extra = simulation["components"].keys() - contract.keys()
                if extra:
                    errors.append("components 含多余 ID：" + ", ".join(sorted(extra)))
                if errors:
                    raise spice.SpiceError("\n".join(errors))
        spice.make_deck(candidate, self.profile, self.base_dir)
        if not patch:
            values = parameter_values(data["simulation"])
        reasons = data.get("reasons")
        missing = [k for k in values if not isinstance(reasons, dict) or
                   not isinstance(reasons.get(k), str) or not reasons[k].strip()]
        if "bindings" in data and not (isinstance(reasons, dict) and isinstance(reasons.get("bindings"), str) and reasons["bindings"].strip()):
            missing.append("bindings")
        if missing:
            raise spice.SpiceError("parameter-reasons: 缺少以下 reasons 键的非空理由：" + ", ".join(missing))
        assumptions = data.get("assumptions")
        if not isinstance(assumptions, list) or not assumptions or any(not isinstance(x, str) or not x.strip() for x in assumptions):
            raise spice.SpiceError("parameter-assumptions: assumptions 必须列出至少一项明确的工况或模型假设")
        if self.example_name:
            data["assumptions"].insert(0, f"匹配示例工况 {self.example_name}，复用其连接对应的参数与测量通道。")
        if self.preview_simulation:
            data["verification"] = quick_check(candidate, self.profile, self.base_dir,
                                               **({"should_stop": self.should_stop} if self.should_stop else {}))
        else:
            # Agent tasks run the requested simulation after candidate adoption.
            data["verification"] = {"status": "not_run", "message": "配置已校验，采用后由仿真步骤运行。"}
        if patch:
            for key in ("simulation", "modules", "closed_loop"):
                if key in candidate.extra:
                    data[key] = deepcopy(candidate.extra[key])
            if "modules" in candidate.extra:
                _, _, assembly = spice.resolved_layout(candidate, self.profile)
                data["module_values"] = assembly.full.extra["simulation"]["components"]
            data["parameters"] = [{"path": k, "label": self.catalog[k]["label"],
                                   "before": self.catalog[k]["value"], "value": v, "reason": reasons[k]}
                                  for k, v in values.items()]
            if self.control is not None:
                from .closed_loop import controller
                from .control.check import check
                from .control.loop import analyse, LoopError
                from .profiles import get_profile
                control, _, _ = controller(candidate.extra["closed_loop"]["control"], self.base_dir)
                try:
                    data["verification"]["loop"] = analyse(check(control, get_profile(control.get("profile", "double"))))
                except (LoopError, ValueError, ArithmeticError) as exc:
                    data["verification"]["loop_note"] = f"现有环路模型无法给出裕度：{exc}；短仿真不证明稳定性。"
                if isinstance(self.layout.extra["closed_loop"]["control"], str) and isinstance(candidate.extra["closed_loop"]["control"], dict):
                    data["assumptions"].append("控制器调参保存为当前电路内的控制表副本，原外部控制表文件保持不变。")
        if "vout" in req and "v_out_V" in data["verification"].get("channels", {}):
            measured = data["verification"]["channels"]["v_out_V"]["mean"]
            data["verification"]["voltage_error_pct"] = 100 * (measured - req["vout"]) / req["vout"]
        if self.example_mode:
            for key in ("simulation", "closed_loop"):
                if key in candidate.extra:
                    data[key] = deepcopy(candidate.extra[key])
            old = parameter_values(self.original.extra.get("simulation"))
            reasons = reasons or {}
            tuned = [p for p in data.get("parameters", []) if not p["path"].startswith("simulation.")]
            rows = []
            for path, value in parameter_values(data["simulation"]).items():
                reason = reasons.get("simulation." + path) or reasons.get(path)
                if not reason:
                    reason = "沿用当前值" if old.get(path) == value else "沿用匹配工况"
                rows.append({"path": path, "label": parameter_label(path, self.layout, data["simulation"]),
                             "before": old.get(path), "value": value, "reason": reason})
            original_loop = self.original.extra.get("closed_loop")
            if not isinstance(original_loop, dict):
                original_loop = {}
            for param in tuned:
                if param["path"].startswith("closed_loop.") and not original_loop:
                    param["before"] = None
            rows.extend(tuned)
            loop = data.get("closed_loop") or {}
            if loop != original_loop:
                def control_name(section):
                    control = section.get("control")
                    return control.get("name", "内嵌控制表") if isinstance(control, dict) else control

                reason = reasons.get("bindings") or "采用与此电路匹配的控制表及测量、驱动绑定"
                if control_name(loop) != control_name(original_loop):
                    rows.append({"path": "closed_loop.control", "label": "闭环控制表",
                                 "before": control_name(original_loop), "value": control_name(loop), "reason": reason})
                for section, label in (("inputs","控制输入"), ("outputs","控制输出")):
                    previous, following = original_loop.get(section), loop.get(section) or {}
                    if not isinstance(previous, dict):
                        previous = {}
                    for key in dict.fromkeys([*previous, *following]):
                        if previous.get(key) != following.get(key):
                            rows.append({"path": f"closed_loop.{section}.{key}", "label": f"{label} · {key}",
                                         "before": previous.get(key), "value": following.get(key), "reason": reason})
            data["parameters"] = rows
            return Proposal(data)
        if patch:
            return Proposal(data)
        before = self.layout.extra.get("simulation")
        old = parameter_values(before)
        data["parameters"] = [{"path": k, "label": parameter_label(k, self.layout, data["simulation"]),
                               "before": old.get(k), "value": v, "reason": reasons[k]} for k, v in values.items()]
        return Proposal(data)

    def _tuned(self, updates):
        return apply_parameters(self.layout, self.catalog, updates, self.control)

    def evaluate(self, reply, *, should_stop=lambda: False):
        data = extract_table(reply)
        if self.topology:
            if not isinstance(data, dict) or set(data) != {"requirements"}:
                raise spice.SpiceError("parameter-fields: 已识别 Buck/Boost，只返回 requirements；数值由程序按公式计算")
            inferred = requirements(data["requirements"])
            data = formula(self.layout, self.topology, self.preset, {**inferred, **self.requested})
        else:
            if isinstance(data, dict):
                data = {**data, "method": "AI 参数提议"}
        return Evaluation(self._check(data), [])

    def unreadable(self, exc):
        if isinstance(exc, json.JSONDecodeError):
            return (f"JSON 语法错误：{exc}。请检查此处的逗号、引号及括号，返回一个完整 JSON 对象。"
                    "字符串内反斜杠须转义，数字字段使用 SI 或 SPICE 后缀，禁止注释及省略号。")
        return (f"参数或短仿真未通过：{exc}\n按具体错误修正并返回完整 JSON；不能改动电路接线或模型映射。"
                "保留已经正确的参数；缺少理由时只补报错列出的完整 reasons 键。")

    def feedback(self, result, best, improved, previous):
        return self.unreadable("；".join(result.errors))
