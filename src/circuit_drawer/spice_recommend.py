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
    result = {"ground": simulation["ground"]}
    def visit(obj, prefix):
        for key, value in obj.items():
            path = prefix + "." + key
            if isinstance(value, dict):
                visit(value, path)
            else:
                result[path] = value
    visit(simulation["components"], "components")
    visit(simulation["transient"], "transient")
    result["probes"] = simulation["probes"]
    return result


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
    n = sum(e.kind == "nmos" for e in layout.elements)
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
        elif e.kind == "nmos":
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


def quick_check(layout, profile, base_dir=None):
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
                           timeout=CHECK_TIMEOUT, base_dir=base_dir)
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


class RecommendationWorkflow:
    document_kind = "仿真参数建议"
    max_rounds = 3

    def __init__(self, layout, profile="ieee-single", requested=None, description="", base_dir=None):
        self.layout = Layout.from_dict(layout.to_dict())
        self.profile, self.base_dir = profile, base_dir
        if "modules" in layout.extra or "closed_loop" in layout.extra:
            raise spice.SpiceError("parameter-scope: 本版参数推荐只处理展开的开环电路；模块 defaults/overrides 和闭环控制器需单独设计")
        spice.connected_layout(self.layout, profile)
        self.requested = requirements({} if requested is None else requested)
        if not isinstance(description, str) or len(description) > 8000:
            raise spice.SpiceError("parameter-description: 文字需求必须是最多 8000 字的文本")
        self.description = description.strip()
        self.topology, self.preset = recognize(self.layout, profile)
        self.use_ai = self.topology is None or bool(self.description)

    def system_prompt(self):
        from .ai.prompt import simulation_parameter_prompt
        return simulation_parameter_prompt(self.topology, INPUTS)

    def first_message(self, description):
        graph = {"elements": [{"id": e.id, "kind": e.kind, "pins": list(e.pins), "opts": e.opts}
                               for e in self.layout.elements], "netlist": self.layout.netlist,
                 "simulation": self.layout.extra.get("simulation"), "model_mapping": self.layout.extra.get("model_mapping")}
        return ("需求为空时按此拓扑常见的演示工况提议，明确列出假设。文字需求：" + (self.description or "未指定") +
                "\n已填写的固定要求（优先遵守）：" + json.dumps(self.requested, ensure_ascii=False) +
                "\n当前连接与参数：" + json.dumps(graph, ensure_ascii=False))

    def formula(self):
        return self._check(formula(self.layout, self.topology, self.preset, self.requested))

    def _check(self, data):
        if not isinstance(data, dict) or set(data) - {"simulation", "requirements", "reasons", "assumptions", "method"}:
            raise spice.SpiceError("parameter-fields: 只能返回 simulation、requirements、reasons、assumptions；不能修改坐标、接线、模型映射或控制器")
        req = requirements(data.get("requirements", {}))
        if any(k not in req or not math.isclose(v, req[k], rel_tol=1e-9) for k, v in self.requested.items()):
            raise spice.SpiceError("parameter-requirements: 返回的 requirements 必须保留所有固定要求")
        data = deepcopy(data)
        candidate = Layout.from_dict(self.layout.to_dict())
        candidate.extra["simulation"] = deepcopy(data.get("simulation"))
        spice.make_deck(candidate, self.profile, self.base_dir)
        values = parameter_values(data["simulation"])
        reasons = data.get("reasons")
        if not isinstance(reasons, dict) or any(not isinstance(reasons.get(k), str) or not reasons[k].strip() for k in values):
            raise spice.SpiceError("parameter-reasons: reasons 必须为每个参数路径提供非空理由，含 ground、probes、transient.*、components.<ID>.*")
        assumptions = data.get("assumptions")
        if not isinstance(assumptions, list) or not assumptions or any(not isinstance(x, str) or not x.strip() for x in assumptions):
            raise spice.SpiceError("parameter-assumptions: assumptions 必须列出至少一项明确的工况或模型假设")
        data["verification"] = quick_check(candidate, self.profile, self.base_dir)
        if "vout" in req and "v_out_V" in data["verification"].get("channels", {}):
            measured = data["verification"]["channels"]["v_out_V"]["mean"]
            data["verification"]["voltage_error_pct"] = 100 * (measured - req["vout"]) / req["vout"]
        before = self.layout.extra.get("simulation")
        try:
            old = parameter_values(before) if isinstance(before, dict) else {}
        except (KeyError, TypeError, AttributeError):
            old = {}  # an incomplete old configuration must not prevent repair
        data["parameters"] = [{"path": k, "before": old.get(k), "value": v, "reason": reasons[k]} for k, v in values.items()]
        return Proposal(data)

    def evaluate(self, reply):
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
        return (f"参数或短仿真未通过：{exc}\n按具体错误修正并返回完整 JSON；不能改动电路接线或模型映射。"
                "缺少理由时，为每个参数路径补上理由。")

    def feedback(self, result, best, improved, previous):
        return self.unreadable("；".join(result.errors))
