"""Measured Agent results, shared with MCP and the two editors."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .layout import Layout

# Twenty stored points per cycle bound interpolation gaps for the display check.
MIN_PERIOD_SAMPLES = 20


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def file_record(path, source):
    path = Path(path)
    return {"path": str(path), "format": path.suffix.lstrip("."), "source": source,
            "exists": path.is_file(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None}


def save_run_record(result, record):
    """Keep the downloadable record and its checksum in sync with measured results."""
    result["files"] = [item for item in result["files"] if item["path"] != str(record)]
    temporary = record.with_suffix(".pending")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(record)
    result["files"].append(file_record(record, result["run_id"]))


def engine_version(engine):
    result = subprocess.run([engine, "--version"], capture_output=True, timeout=10,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    lines = result.stdout.decode("utf-8", errors="replace").splitlines()
    return next((line.strip(" *") for line in lines if "ngspice-" in line.lower()), "")


def waveform_result(path, run_id, window=None, signals=None, power_quality=None):
    """Integrate full-resolution data. Decimation applies only to display samples.

    ``power_quality`` ({voltage, current, frequency, harmonics?, current_sign?})
    adds PF, displacement factor, mean power and THD over whole fundamental
    cycles ending at the window's end (see power_quality.py).
    """
    from bisect import bisect_left, bisect_right
    from .waveforms import column_defaults, parse_csv, samples

    data = parse_csv(Path(path).read_bytes())
    if len(data.time) < 2:
        raise ValueError("波形至少需要两个采样点")
    # The final 10% is an explicit sampling window, not a claim of steady state.
    if window is None:
        window = [data.time[0] + .9 * (data.time[-1] - data.time[0]), data.time[-1]]
    if (not isinstance(window, list) or len(window) != 2 or
            any(type(t) not in (int, float) or not math.isfinite(t) for t in window) or
            window[0] >= window[1]):
        raise ValueError("取样区间须在波形时间范围内，且起点小于终点")
    # ngspice's decimal CSV endpoint can differ from the requested stop by roundoff.
    tolerance = max(1e-15, (data.time[-1] - data.time[0]) * 1e-9)
    if window[0] < data.time[0] - tolerance or window[1] > data.time[-1] + tolerance:
        raise ValueError("取样区间须在波形时间范围内，且起点小于终点")
    window = [max(data.time[0], window[0]), min(data.time[-1], window[1])]
    if window[0] >= window[1]:
        raise ValueError("取样区间没有有效时长")
    channels = list(data.columns)[1:]
    signals = channels if signals is None else signals
    if not isinstance(signals, list) or not signals or any(not isinstance(s, str) for s in signals):
        raise ValueError("请选择运行记录中实际存在的波形通道")
    resolved = []
    for signal in signals:
        # Probe IDs omit the unit suffix that the existing CSV exporter adds.
        matches = [signal] if signal in channels else [c for c in channels if column_defaults(c)["label"] == signal]
        if len(matches) != 1:
            raise ValueError(f"波形通道 {signal} 无法唯一对应；实际通道为 {', '.join(channels)}")
        resolved.append(matches[0])
    metrics, waveforms = [], []
    for signal in dict.fromkeys(resolved):
        values = data.columns[signal]

        def interpolate(t):
            i = bisect_left(data.time, t)
            if i == 0 or data.time[i] == t:
                return values[i]
            ratio = (t - data.time[i - 1]) / (data.time[i] - data.time[i - 1])
            return values[i - 1] + ratio * (values[i] - values[i - 1])

        first, last = bisect_right(data.time, window[0]), bisect_left(data.time, window[1])
        times = [window[0], *data.time[first:last], window[1]]
        points = [interpolate(window[0]), *values[first:last], interpolate(window[1])]
        duration = window[1] - window[0]
        area = square = 0.0
        for a, b, x, y in zip(times, times[1:], points, points[1:]):
            area += (b - a) * (x + y) / 2
            square += (b - a) * (x * x + x * y + y * y) / 3
        labels = column_defaults(signal)
        for name, value in (("mean", area / duration), ("rms", math.sqrt(max(0, square / duration))),
                            ("min", min(points)), ("max", max(points)),
                            ("peak_to_peak", max(points) - min(points))):
            metrics.append({"name": name, "signal": signal, "value": value, "unit": labels["unit"],
                            "window": {"from": window[0], "to": window[1], "unit": "s"},
                            "note": "sample_window", "flag": None, "run_id": run_id})
        waveforms.append({"signal": signal, **labels, "run_id": run_id,
                          "t_range": [data.time[0], data.time[-1]], "window": window,
                          "data_ref": {"file": str(path), "column": signal},
                          "samples": samples(data, signal, [data.time[0], data.time[-1]], budget=1200),
                          "window_samples": [[window[0], points[0]],
                              *[p for p in samples(data, signal, window, budget=1200) if window[0] < p[0] < window[1]],
                              [window[1], points[-1]]]})
    out = {"metrics": metrics, "waveforms": waveforms}
    if power_quality is not None:
        from .power_quality import metrics as pq_metrics
        extra, detail = pq_metrics(data, power_quality, window, run_id)
        metrics.extend(extra)
        out["power_quality"] = detail
    return out


def assess_waveforms(result, table, requirements):
    """Compare recorded channels and declared fixed-PWM cycles without claiming stability."""
    from .auto_modes import CHECK_CYCLES, _steady_channel
    from .spice import GATED, number
    from .waveforms import parse_csv

    result["requirements"] = requirements
    result["assessment"] = {"targets": [], "periodicity": []}
    target = requirements.get("vout")
    if target is not None:
        target = number(target, "vout")
        for metric in result["metrics"]:
            if metric["name"] == "mean" and metric["signal"] == "v_out_V":
                result["assessment"]["targets"].append({"signal": metric["signal"], "target": target,
                    "measured": metric["value"], "unit": metric["unit"], "window": metric["window"],
                    "error_pct": 100 * (metric["value"] - target) / target if target else None})
    configs = table.get("simulation", {}).get("components", {})
    switches = []
    for element in table.get("elements", []):
        if element["kind"] in GATED:
            config = configs.get(element["id"], {})
            mode = config.get("gate_drive", "external" if "g" in element.get("pins", {}) else "internal")
            switches.append({**config, "gate_drive": mode})
    frequency = None
    if switches and not table.get("closed_loop") and not table.get("modules"):
        try:
            frequencies = {number(c["frequency"], "frequency") for c in switches}
            internal = all(c.get("gate_drive", "internal") == "internal" and
                           not number(c.get("phase_ramp", 0), "phase_ramp") for c in switches)
            if internal and len(frequencies) == 1 and min(frequencies) > 0:
                frequency = frequencies.pop()
        except (KeyError, ValueError):
            pass
    cache = {}
    for wave in result["waveforms"]:
        item = {"signal": wave["signal"], "state": "unavailable", "unit": wave["unit"], "cycles": CHECK_CYCLES}
        result["assessment"]["periodicity"].append(item)
        if frequency is None:
            item["reason"] = "fixed_pwm_required"
            continue
        a, b = wave["window"]
        period = 1 / frequency
        if b - a < CHECK_CYCLES * period * (1 - 1e-9):
            item["reason"] = "short_window"
            continue
        path = wave["data_ref"]["file"]
        if path not in cache:
            cache[path] = parse_csv(Path(path).read_bytes())
        data = cache[path]
        begin = b - CHECK_CYCLES * period
        from bisect import bisect_left
        first = max(0, bisect_left(data.time, begin) - 1)
        last = min(len(data.time) - 1, bisect_left(data.time, b))
        if any(tb - ta > period / MIN_PERIOD_SAMPLES * (1 + 1e-9)
               for ta, tb in zip(data.time[first:last], data.time[first + 1:last + 1])):
            item["reason"] = "coarse_samples"
            continue
        edges = [begin + i * period for i in range(CHECK_CYCLES + 1)]
        measured = _steady_channel(data.time, data.columns[wave["signal"]], edges, wave["signal"])
        item.update(measured, state="repeatable" if measured["ok"] else "changing",
                    window=[begin, b], frequency=frequency)


def run_candidate(workspace, candidate_id, tool, *, options=None, should_stop=None, drawing_version=None, reviewed_circuit=None):
    """Each result belongs to one immutable candidate and one unique directory."""
    from .spice import SpiceCancelled

    candidate = workspace._get(candidate_id)
    report = workspace.validate_candidate(candidate_id)
    if not report["ok"]:
        raise ValueError("校验未通过，请先修正候选")
    options = options or {}
    run_id = uuid4().hex
    output = workspace._path(workspace.output_dir / ("run-" + run_id))
    result = {"run_id": run_id, "candidate_id": candidate_id, "revision": candidate.revision,
              "drawing_version": drawing_version,
              "started_at": utc_now(), "engine_status": "done", "metrics": [], "waveforms": [],
              "modes": [], "files": [], "suggestions": []}
    started = time.monotonic()
    circuit_table, base_dir = candidate.table, candidate.base_dir
    circuit_profile = candidate.profile
    binding_notes = []
    if candidate.kind == "control" and tool in ("simulate", "export_simulation"):
        from .control.circuit import resolve
        circuit_table, source, binding_notes = resolve(candidate.table, candidate.base_dir)
        if reviewed_circuit:
            from .agent_tools import _digest, _json
            if _digest(_json(circuit_table)) != reviewed_circuit:
                raise ValueError("关联电路在确认工况后已改变，请重新确认参数")
        base_dir = source.parent
        circuit_profile = circuit_table.get("profile") or "ieee-double"
    if tool in ("simulate", "modes"):
        from .spice import executable, export_spice, make_deck
        if candidate.kind != "circuit" and (tool != "simulate" or not candidate.table.get("circuit")):
            raise ValueError("ngspice 仿真需要电路图")
        engine = executable()
        if not engine:
            raise ValueError("未找到 ngspice，请在 API 与仿真设置中指定程序")
        layout = Layout.from_dict(circuit_table)
        deck = make_deck(layout, circuit_profile, base_dir)
        result.update(engine={"name": "ngspice", "version": engine_version(engine), "where": "local"},
                      sim_time={"stop": deck.stop, "max_step": deck.step})
        if tool == "simulate":
            export = export_spice(layout, output, profile=circuit_profile, run=True, ngspice=engine,
                                  base_dir=base_dir, should_stop=should_stop)
            result["notes"] = export["notes"]
        else:
            from .auto_modes import run
            export = run(layout, output, profile=candidate.profile, base_dir=candidate.base_dir,
                         ngspice=engine, should_stop=should_stop)
            result["mode_analysis"] = export
            result["modes"] = [{"index": item["id"], "name": item["mode"]["name"],
                                "intervals": item["intervals"], "conducting": item["mode"]["on"],
                                "run_id": run_id} for item in export["candidates"]]
            result["notes"] = [export.get("reason", "")]
        result["files"] = [file_record(path, run_id) for path in export["files"]]
        csv = next(path for path in export["files"] if Path(path).suffix == ".csv"
                   and Path(path).name != "device_states.csv")
        result.update(waveform_result(csv, run_id, options.get("window"), options.get("signals"),
                                      options.get("power_quality")))
        if tool == "modes" and export.get("ready"):
            from .modes import Mode
            from .pipeline import render_modes
            modes = [Mode.from_dict(item["mode"]) for item in export["candidates"]]
            formats = ("svg",) if "svg" in workspace.capabilities()["available_formats"] else ("tex",)
            figures = render_modes(layout, candidate.profile, output / "figures", "modes", formats,
                                   modes=modes, sheet=False, base_dir=candidate.base_dir)
            for mode, figure in zip(result["modes"], figures):
                paths = list(figure.files.values())
                mode["figure_ref"] = str(paths[0]) if paths else None
                result["files"].extend(file_record(path, run_id) for path in paths)
    elif tool == "export_simulation":
        from .simulation_export import export_simulation
        target = options.get("target", "simulink")
        if target not in ("simulink", "plecs"):
            raise ValueError("请选择 simulink 或 plecs 导出目标")
        exported = export_simulation(Layout.from_dict(circuit_table), output,
                                     stem=circuit_table.get("name") or "circuit", target=target,
                                     profile=circuit_profile, base_dir=base_dir,
                                     control=options.get("control", "continuous"))
        result.update(engine={"name": target, "version": "", "where": "export"},
                      notes=exported["notes"], exported=True, ran=False,
                      files=[file_record(path, run_id) for path in exported["files"]])
    elif tool == "loop":
        from .control.check import check
        from .control.loop import analyse
        from .control.render import bode
        from .profiles import get_profile
        if candidate.kind != "control":
            raise ValueError("环路分析需要控制框图")
        analysis = analyse(check(candidate.table, get_profile(candidate.profile)))
        result.update(engine={"name": "Circuit Drawer", "version": "", "where": "local"}, loop=analysis)
        for loop in analysis["loops"]:
            for name, unit in (("fc_hz", "Hz"), ("pm_deg", "°"), ("gm_db", "dB")):
                result["metrics"].append({"name": name, "signal": loop["label"], "value": loop[name],
                                          "unit": unit, "window": None, "run_id": run_id, "flag": None})
        rendered = bode(candidate.table, candidate.profile, output, "bode", ("svg", "pdf"))
        result["files"] = [file_record(path, run_id) for path in rendered.files.values()]
    else:
        raise ValueError("Unknown result tool")
    if should_stop and should_stop():
        raise SpiceCancelled("已取消")
    output.mkdir(parents=True, exist_ok=True)
    if tool in ("simulate", "export_simulation"):
        snapshot = output / "circuit-input.json"
        snapshot.write_text(json.dumps(circuit_table, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        result["circuit_snapshot"] = str(snapshot)
        result["files"].append(file_record(snapshot, run_id))
        result.setdefault("notes", []).extend(binding_notes)
    result.update(finished_at=utc_now(), duration_ms=round((time.monotonic() - started) * 1000))
    record = output / "run.json"
    save_run_record(result, record)
    return result
