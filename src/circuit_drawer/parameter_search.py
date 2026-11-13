"""Bounded parameter scans of disposable circuits, with explicit adoption.

The native trace and eight-period check are shared with mode identification.
Results describe the requested transient and declared limits; a short preview
is never substituted for a candidate's full simulation.
"""
from bisect import bisect_right, bisect_left
from copy import deepcopy
import csv
import gzip
import hashlib
import itertools
import json
import math
from pathlib import Path
import shutil
import tempfile
import uuid

from . import auto_modes as am, spice
from .layout import Layout
from .spice_recommend import apply_parameters, tuning_parameters

# Interactive batch budget, not an engineering feasibility threshold.
MAX_CASES = 32
CASE_TIMEOUT = 120
MAX_OUTPUT_SAMPLES = 1000000  # interactive batch storage budget, no truncated runs
# Equal target accuracy across cases prevents a nearly-off converter winning
# on low ripple. It is an editable, explicitly reported design constraint.
DEFAULT_LIMITS = {"error_pct": 5.0}
OBJECTIVES = ("ripple_pct", "overshoot_pct")
LIMITS = (*OBJECTIVES, "error_pct")


class SearchError(ValueError):
    pass


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def source_signature(layout, profile, base_dir):
    return _hash([layout.to_dict(), spice.make_deck(layout, profile, base_dir).text])


def range_values(spec):
    if not isinstance(spec, dict) or set(spec) - {"paths", "min", "max", "count", "spacing"}:
        raise SearchError("扫描范围只接受 paths、min、max、count、spacing。")
    if not {"paths", "min", "max", "count"} <= spec.keys():
        raise SearchError("每条扫描范围需填写参数、下限、上限和点数。")
    lo, hi = spice.number(spec["min"], "min"), spice.number(spec["max"], "max")
    count, spacing = spec["count"], spec.get("spacing", "linear")
    if type(count) is not int or not 2 <= count <= MAX_CASES or lo >= hi:
        raise SearchError(f"下限必须小于上限，点数须为 2–{MAX_CASES} 的整数。")
    if spacing not in ("linear", "log") or (spacing == "log" and lo <= 0):
        raise SearchError("间隔只支持 linear 或 log；对数范围必须大于零。")
    a, b = (math.log(lo), math.log(hi)) if spacing == "log" else (lo, hi)
    values = [(1 - i / (count - 1)) * a + i / (count - 1) * b for i in range(count)]
    if spacing == "log":
        values = [math.exp(v) for v in values]
    values[0], values[-1] = lo, hi
    if not all(map(math.isfinite, values)) or len(set(values)) != count:
        raise SearchError("范围的取样值不能重复或溢出，请调整范围和点数。")
    return values


def nondominated(points, objectives=OBJECTIVES):
    usable = [p for p in points if p.get("feasible") and all(
        isinstance(p.get("metrics", {}).get(k), (int, float))
        and math.isfinite(p["metrics"][k]) for k in objectives)]
    def dominates(a, b):
        return all(a["metrics"][k] <= b["metrics"][k] for k in objectives) and any(
            a["metrics"][k] < b["metrics"][k] for k in objectives)
    return [p["id"] for p in usable if not any(dominates(q, p) for q in usable)]


def interval(times, values, start, end):
    """Include exact interval boundaries without counting a sample twice."""
    if not times[0] <= start < end <= times[-1]:
        raise SearchError("测量数据没有覆盖完整周期。")
    a, b = bisect_right(times, start), bisect_left(times, end)
    return ([start, *times[a:b], end],
            [am._value(times, values, start), *values[a:b], am._value(times, values, end)])


def mean(times, values, *, square=False):
    duration = times[-1] - times[0]
    return math.fsum(((a * a + a * b + b * b) / 3 if square else a / 2 + b / 2) * ((tb - ta) / duration) for ta, tb, a, b in
                     zip(times, times[1:], values, values[1:]))


def voltage_metrics(times, values, target, signed_peak):
    avg, ripple = mean(times, values), max(values) - min(values)
    metrics = {"mean_v": avg, "ripple_v": ripple, "ripple_pct": ripple / abs(target) * 100,
               "overshoot_pct": max(0., signed_peak - abs(target)) / abs(target) * 100,
               "error_pct": abs(avg - target) / abs(target) * 100}
    if not all(map(math.isfinite, metrics.values())):
        raise SearchError("测量结果溢出，请检查目标电压与仿真数值。")
    return metrics


class Search:
    def __init__(self, layout, config=None, *, profile="ieee-single", base_dir=None):
        self.layout = Layout.from_dict(layout.to_dict())
        self.profile, self.base_dir = profile, base_dir
        deck, scene, _, _, _ = am._prepare(self.layout, profile=profile, base_dir=base_dir, purpose="参数搜索")
        if deck.stop / deck.step > MAX_OUTPUT_SAMPLES:
            raise SearchError(f"每组输出最多 {MAX_OUTPUT_SAMPLES} 个采样点，请调整瞬态时长或输出步长；搜索不会截短仿真。")
        components = scene.layout.extra["simulation"]["components"]
        if any(e.kind in ("nmos", "pmos") and components[e.id].get("model", "ideal_switch") != "ideal_switch"
               for e in scene.layout.elements):
            raise SearchError("本阶段参数搜索使用理想 PWM 开关，请另存适用模型的副本。")
        _, catalog, self.control = tuning_parameters(self.layout, profile, base_dir)
        self.catalog = {}
        for name, item in catalog.items():
            # Numerical resolution and run duration stay identical across cases.
            if item["keys"][:2] == ("simulation", "transient"):
                continue
            try:
                spice.number(item["value"], name)
            except spice.SpiceError:
                continue
            self.catalog[name] = item
        self.voltages = [column for column in deck.columns[1:] if column.endswith("_V")]
        self.circuit_hash = source_signature(self.layout, profile, base_dir)
        self.source_hash = self.circuit_hash
        self.config = self._config(config) if config is not None else None
        self.power = None
        self.objectives = OBJECTIVES
        if self.config and self.config.get('power_file'):
            from .power_design import PowerDesign
            self.power = PowerDesign(Path(base_dir or '.') / self.config['power_file'], scene.layout)
            self.source_hash = _hash([self.circuit_hash, self.power.signature])
            self.objectives = ('loss_pct', 'magnetic_volume_cm3')

    def describe(self):
        return {"parameters": {k: {"value": v["value"], "label": v["label"]} for k, v in self.catalog.items()},
                "voltages": list(self.voltages), "max_cases": MAX_CASES, "limits": dict(DEFAULT_LIMITS),
                "source_hash": self.source_hash}

    def _config(self, config):
        if not isinstance(config, dict) or set(config) - {"ranges", "voltage", "target_v", "limits", "power_file"}:
            raise SearchError("搜索设置只接受 ranges、voltage、target_v、limits、power_file。")
        power_file = config.get('power_file', '')
        if not isinstance(power_file, str):
            raise SearchError("power_file 须为损耗与磁件数据文件路径。")
        voltage = config.get("voltage")
        if voltage not in self.voltages:
            raise SearchError("请选择当前图纸中的电压测量通道。")
        target = spice.number(config.get("target_v"), "target_v")
        if target == 0:
            raise SearchError("直流输出目标电压不能为零。")
        limits = config.get("limits", DEFAULT_LIMITS)
        if not isinstance(limits, dict) or set(limits) - set(LIMITS) or "error_pct" not in limits:
            raise SearchError("需填写输出误差上限；可另外限制纹波和过冲百分数。")
        limits = {k: spice.number(v, k) for k, v in limits.items()}
        if any(v < 0 for v in limits.values()):
            raise SearchError("误差、纹波和过冲的上限不能小于零。")
        ranges = config.get("ranges")
        if not isinstance(ranges, list) or not ranges:
            raise SearchError("请至少添加一条参数扫描范围。")
        seen, count, normalized = set(), 1, []
        for spec in ranges:
            values = range_values(spec)
            paths = spec["paths"]
            if not isinstance(paths, list) or not paths or any(not isinstance(p, str) for p in paths):
                raise SearchError("每条范围的 paths 需要非空参数名称列表。")
            if len(set(paths)) != len(paths) or seen.intersection(paths) or set(paths) - self.catalog.keys():
                raise SearchError("扫描参数不能重复，且只能来自当前可调参数清单。")
            seen.update(paths)
            count *= len(values)
            if count > MAX_CASES:
                raise SearchError(f"扫描组合超过 {MAX_CASES} 组，请减少范围点数。")
            normalized.append({"paths": list(paths), "min": values[0], "max": values[-1],
                               "count": len(values), "spacing": spec.get("spacing", "linear")})
        return {"ranges": normalized, "voltage": voltage, "target_v": target, "limits": limits,
                **({'power_file': power_file} if power_file else {})}

    def updates(self):
        if self.config is None:
            raise SearchError("尚未填写扫描范围。")
        ranges = self.config["ranges"]
        return [{path: value for spec, value in zip(ranges, values) for path in spec["paths"]}
                for values in itertools.product(*(range_values(spec) for spec in ranges))]

    def _evaluate(self, candidate, stage, engine, should_stop):
        deck, scene, _, period, _ = am._prepare(candidate, profile=self.profile, base_dir=self.base_dir,
                                               purpose="参数搜索")
        column = self.config["voltage"]
        trace_name = "measure:" + column
        aliases = candidate.extra.get("module_authoring", {}).get("aliases") or {}
        vectors = {trace_name: deck.probe_expressions[column]}
        if self.power:
            vectors.update(self.power.vectors(deck, aliases))
        measured, columns = am._instrument(scene.layout, deck, aliases, extra_vectors=vectors)
        spice._simulate(measured, engine, stage, CASE_TIMEOUT, should_stop=should_stop)
        index = columns.index(trace_name) + 1
        sign = math.copysign(1, self.config["target_v"])
        peak = {"value": -math.inf, "at_s": 0., "first_s": None, "samples": 0}
        def sample(row):
            if should_stop and should_stop():
                raise spice.SpiceCancelled("已取消。")
            peak["first_s"] = row[0] if peak["first_s"] is None else peak["first_s"]
            peak["samples"] += 1
            if sign * row[index] > peak["value"]:
                peak["value"], peak["at_s"] = sign * row[index], row[0]
        times, data = am._read_trace(stage / "device_states.dat", columns, deck.stop, period, on_sample=sample)
        if abs(peak["first_s"]) > deck.step * .01:
            raise SearchError("原始测量没有覆盖仿真起点，无法核对完整过冲。")
        edges = am._cycles(scene.layout, times, data, period)
        checks = am._steady(scene.layout, times, data, edges)
        checks.append(am._steady_channel(times, data[trace_name], edges, trace_name))
        ts, ys = interval(times, data[trace_name], edges[-2], edges[-1])
        metrics = voltage_metrics(ts, ys, self.config["target_v"], peak["value"])
        violations = [k for k, limit in self.config["limits"].items() if metrics[k] > limit]
        settled = all(c["ok"] for c in checks)
        with (stage / "checked_trace.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["time_s", *columns])
            writer.writerows(zip(times, *(data[k] for k in columns)))
        result = {"status": "passed" if settled and not violations else "rejected" if settled else "unsettled",
                "feasible": settled and not violations, "metrics": metrics, "limits_exceeded": violations,
                "settling": checks, "window_s": [edges[-2], edges[-1]],
                "checked_periods": len(edges) - 1, "period_s": period, "stop_s": deck.stop,
                "peak": peak, "waveform_sha256": hashlib.sha256((stage / "waveforms.csv").read_bytes()).hexdigest()}
        if self.power and settled:
            try:
                power = self.power.evaluate(scene.layout, times, data, result['window_s'])
                (stage / 'power.json').write_text(json.dumps(power, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
                result['power'] = power
                result['metrics'].update(efficiency_pct=power['efficiency_pct'], loss_pct=100 - power['efficiency_pct'],
                                         magnetic_volume_cm3=power['magnetic_volume_m3'] * 1e6)
            except ValueError as exc:
                result.update(status='failed', feasible=False, error=str(exc))
        return result

    def run(self, out_dir, *, ngspice=None, should_stop=None, on_progress=None):
        combinations = self.updates()
        if self.power and not self.power.unchanged():
            raise SearchError("损耗或磁件文件已改变，请重新开始搜索。")
        engine = spice.executable(ngspice)
        if not engine:
            raise SearchError("未找到 ngspice，请安装并设置 CIRCUIT_DRAWER_NGSPICE。")
        root = (Path(out_dir) / ("run-" + uuid.uuid4().hex)).resolve()
        root.mkdir(parents=True, exist_ok=False)
        self.layout.save(root / "source.json")
        if self.power:
            self.power.save_sources(root / 'power-data')
        result = {"version": 1, "source_hash": self.source_hash, "config": deepcopy(self.config),
                  "total": len(combinations), "points": [], "complete": False, "cancelled": False,
                  "directory": str(root), "objectives": list(self.objectives), "pareto_ids": []}
        for index, updates in enumerate(combinations, 1):
            if should_stop and should_stop():
                result["cancelled"] = True
                break
            destination = root / f"case-{index:03d}"
            destination.mkdir()
            point = {"id": index, "updates": updates, "feasible": False, "status": "failed"}
            candidate, _ = apply_parameters(self.layout, self.catalog, updates, self.control)
            candidate.save(destination / "input.json")
            with tempfile.TemporaryDirectory(prefix="cd_search_") as folder:
                stage = Path(folder)
                try:
                    point.update(self._evaluate(candidate, stage, engine, should_stop))
                except spice.SpiceCancelled as exc:
                    point.update(status="cancelled", error=str(exc))
                    result["cancelled"] = True
                except ValueError as exc:
                    point["error"] = str(exc)
                finally:
                    # Preserve each case's own evidence, including failed runs.
                    for name in ("circuit.cir", "simulation.log", "waveforms.csv", "checked_trace.csv", "power.json"):
                        if (stage / name).is_file():
                            shutil.copyfile(stage / name, destination / name)
                    raw = stage / 'device_states.dat'
                    if raw.is_file():
                        with raw.open('rb') as source, gzip.open(destination / 'device_states.dat.gz', 'wb', compresslevel=1) as target:
                            shutil.copyfileobj(source, target)
            point["directory"] = str(destination)
            result["points"].append(point)
            if on_progress:
                on_progress(deepcopy(point), len(combinations))
            if result["cancelled"]:
                break
        result["cancelled"] = result["cancelled"] or bool(should_stop and should_stop())
        result["complete"] = not result["cancelled"] and len(result["points"]) == len(combinations)
        result["pareto_ids"] = nondominated(result["points"], self.objectives)
        (root / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        return result

    def apply(self, current, result, selected):
        if source_signature(current, self.profile, self.base_dir) != self.circuit_hash or result.get("source_hash") != self.source_hash:
            raise SearchError("图纸或仿真设置已改变，请重新搜索。")
        if self.power and not self.power.unchanged():
            raise SearchError("损耗或磁件文件已改变，请重新搜索。")
        if not result.get("complete") or result.get("cancelled"):
            raise SearchError("搜索尚未完成或已取消，不能采用候选。")
        point = next((p for p in result["points"] if type(selected) is int and p["id"] == selected), None)
        if point is None or not point.get("feasible"):
            raise SearchError("请选择通过周期重复性检查及全部约束的候选。")
        candidate, _ = apply_parameters(current, self.catalog, point["updates"], self.control)
        spice.make_deck(candidate, self.profile, self.base_dir)
        return candidate
