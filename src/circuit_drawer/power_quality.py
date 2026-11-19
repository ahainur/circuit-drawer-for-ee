"""Power factor and harmonic distortion of recorded waveforms.

The measurement uses a whole number of fundamental cycles that ends at the
end of the requested window (normally the last part of a run, where the
circuit should be closest to steady state). Recorded points can be unevenly
spaced: the waveform between points is taken as a straight line, the same
assumption the mean/RMS metrics use, and resampled uniformly for the Fourier
series.

1. Power factor PF = P / (V_rms * I_rms), with P the mean of v(t)·i(t) and
   both RMS values including DC and every harmonic. P is positive when the
   recorded current flows in the direction the probe defines and the recorded
   voltage is positive across it; the sign of PF follows P.
2. THD = sqrt(sum of harmonic 2..H RMS squared) / fundamental RMS, DC excluded.
3. Displacement factor cos(phi_1) is the cosine of the angle between the
   voltage and current fundamentals.
"""
from __future__ import annotations

import math
from bisect import bisect_left

DEFAULT_HARMONICS = 40
SPEC_KEYS = ("voltage", "current", "frequency", "harmonics", "current_sign")
POINTS_PER_HARMONIC = 16      # uniform resampling points per cycle of the highest harmonic
MIN_POINTS_PER_CYCLE = 256
FUNDAMENTAL_FLOOR = 1e-6      # fundamental RMS below this fraction of total RMS is "near zero"
CYCLE_TOL = 1e-9
MAX_FOURIER_TERMS = 10_000_000  # Bound the per-channel DFT work of user-selected windows/frequencies.


class PowerQualityError(ValueError):
    pass


def _interpolate(times, values, t):
    i = bisect_left(times, t)
    if i < len(times) and times[i] == t:
        return values[i]
    if i == 0 or i == len(times):
        raise PowerQualityError("取样区间超出波形时间范围")
    ratio = (t - times[i - 1]) / (times[i] - times[i - 1])
    return values[i - 1] + ratio * (values[i] - values[i - 1])


def _series(samples, harmonics, cycles):
    """RMS and phase of DC and harmonics 1..H from uniform samples over ``cycles`` periods."""
    n = len(samples)
    dc = sum(samples) / n
    out = []
    for h in range(1, harmonics + 1):
        k = h * cycles
        re = sum(x * math.cos(2 * math.pi * k * j / n) for j, x in enumerate(samples)) * 2 / n
        im = sum(x * math.sin(2 * math.pi * k * j / n) for j, x in enumerate(samples)) * 2 / n
        out.append((math.hypot(re, im) / math.sqrt(2), math.atan2(-im, re)))
    return dc, out


def measure(times, voltage, current, *, frequency, window, harmonics=DEFAULT_HARMONICS):
    """Measured values and conditions; ``status`` explains a result that is missing."""
    if not (isinstance(frequency, (int, float)) and math.isfinite(frequency) and frequency > 0):
        raise PowerQualityError("基波频率须为正数（Hz）")
    if isinstance(harmonics, bool) or not isinstance(harmonics, int) or not 2 <= harmonics <= 200:
        raise PowerQualityError("谐波上限须为 2 到 200 的整数")
    a, b = window
    period = 1 / frequency
    conditions = {"fundamental_hz": frequency, "harmonics": harmonics, "requested_window_s": [a, b],
                  "method": "linear interpolation between recorded points, uniform resampling, DFT over whole cycles",
                  "power_sign": "mean of v*i as recorded; positive when the probes' reference directions deliver power"}
    cycle_count = (b - a) / period
    if not math.isfinite(cycle_count):
        return {"status": "insufficient_data", "conditions": conditions,
                "reason": "指定基波频率与取样区间超出计算范围"}
    cycles = math.floor(cycle_count + CYCLE_TOL)
    if cycles < 1:
        return {"status": "short_window", "conditions": conditions,
                "reason": f"取样区间 {b - a:g} s 不足一个基波周期 {period:g} s"}
    # The final ngspice timestamp can round just below an exact number of periods.
    start = max(a, b - cycles * period)
    conditions.update(window_s=[start, b], cycles=cycles)
    first, last = bisect_left(times, start), bisect_left(times, b)
    inside = times[max(0, first - 1):min(len(times), last + 1)]
    gaps = [y - x for x, y in zip(inside, inside[1:])]
    if len(inside) < 4 or not gaps:
        return {"status": "insufficient_data", "conditions": conditions, "reason": "取样区间内的记录点不足"}
    widest = max(gaps)
    resolvable = math.floor(period / (2 * widest)) if widest > 0 else harmonics
    conditions["widest_point_gap_s"] = widest
    per_cycle = max(MIN_POINTS_PER_CYCLE, POINTS_PER_HARMONIC * harmonics)
    n = cycles * per_cycle
    if resolvable < 1:
        return {"status": "insufficient_data", "conditions": conditions,
                "reason": "记录点间隔无法解析指定基波频率，请降低输出间隔或核对频率"}
    if n * harmonics > MAX_FOURIER_TERMS:
        return {"status": "insufficient_data", "conditions": conditions,
                "reason": "所选周期数和谐波数超出计算范围，请缩短取样区间或降低谐波上限"}
    grid = [start + (b - start) * j / n for j in range(n)]
    v = [_interpolate(times, voltage, t) for t in grid]
    i = [_interpolate(times, current, t) for t in grid]
    if not all(map(math.isfinite, v + i)):
        return {"status": "insufficient_data", "conditions": conditions, "reason": "取样区间内含非有限数值"}
    power = sum(x * y for x, y in zip(v, i)) / n
    v_rms = math.sqrt(sum(x * x for x in v) / n)
    i_rms = math.sqrt(sum(y * y for y in i) / n)
    v_dc, v_h = _series(v, harmonics, cycles)
    i_dc, i_h = _series(i, harmonics, cycles)
    result = {"status": "ok", "conditions": conditions, "power_w": power, "v_rms": v_rms, "i_rms": i_rms,
              "v_dc": v_dc, "i_dc": i_dc, "v_fundamental_rms": v_h[0][0], "i_fundamental_rms": i_h[0][0],
              "harmonics_v_rms": [x for x, _ in v_h], "harmonics_i_rms": [x for x, _ in i_h]}
    result["pf"] = power / (v_rms * i_rms) if v_rms > 0 and i_rms > 0 else None
    for key, total, series in (("thd_v", v_rms, v_h), ("thd_i", i_rms, i_h)):
        fundamental = series[0][0]
        if fundamental <= FUNDAMENTAL_FLOOR * max(total, 1e-300):
            result[key] = None
            result.setdefault("flags", []).append(f"{key}_fundamental_near_zero")
        else:
            result[key] = math.sqrt(sum(x * x for x, _ in series[1:])) / fundamental
    if result["thd_v"] is not None and result["thd_i"] is not None:
        result["displacement_factor"] = math.cos(v_h[0][1] - i_h[0][1])
    else:
        result["displacement_factor"] = None
    if resolvable < harmonics:
        result.setdefault("flags", []).append("coarse_samples")
        result["resolvable_harmonics"] = resolvable
    if result["pf"] is None:
        result["status"] = "zero_rms"
    return result


def metrics(data, spec, window, run_id):
    """Shared result metrics for the channels ``spec`` names; ``data`` from waveforms.parse_csv."""
    from .waveforms import column_defaults
    if not isinstance(spec, dict) or set(spec) - set(SPEC_KEYS) or not {"voltage", "current", "frequency"} <= set(spec):
        raise PowerQualityError(f"功率因数／THD 设置须为对象，字段 {', '.join(SPEC_KEYS)}；voltage、current、frequency 必填")
    sign = spec.get("current_sign", 1)
    if sign not in (1, -1) or isinstance(sign, bool):
        raise PowerQualityError("current_sign 须为 1 或 -1：-1 表示把记录的电流取反后再算功率")
    frequency = spec["frequency"]
    if isinstance(frequency, bool) or not isinstance(frequency, (int, float)):
        raise PowerQualityError("frequency 须为基波频率数值（Hz）")
    channels = list(data.columns)[1:]

    def resolve(name):
        matches = [name] if name in channels else [c for c in channels if column_defaults(c)["label"] == name]
        if len(matches) != 1:
            raise PowerQualityError(f"波形通道 {name} 无法唯一对应；实际通道为 {', '.join(channels)}")
        return matches[0]

    v_name, i_name = resolve(spec["voltage"]), resolve(spec["current"])
    current = data.columns[i_name] if sign == 1 else [-x for x in data.columns[i_name]]
    result = measure(data.time, data.columns[v_name], current, frequency=float(frequency),
                     window=window, harmonics=spec.get("harmonics", DEFAULT_HARMONICS))
    result["conditions"].update(voltage_channel=v_name, current_channel=i_name, current_sign=sign)
    signal = f"{v_name}|{i_name}"
    window_record = {"from": result["conditions"].get("window_s", window)[0],
                     "to": result["conditions"].get("window_s", window)[1], "unit": "s"}
    flag = None if result["status"] == "ok" and not result.get("flags") else \
        ",".join([result["status"]] * (result["status"] != "ok") + result.get("flags", []))
    out = []
    for name, unit, sig in (("pf", "", signal), ("displacement_factor", "", signal), ("power", "W", signal),
                            ("thd", "", v_name), ("thd", "", i_name)):
        key = {"pf": "pf", "displacement_factor": "displacement_factor", "power": "power_w"}.get(name)
        if name == "thd":
            key = "thd_v" if sig == v_name else "thd_i"
        out.append({"name": name, "signal": sig, "value": result.get(key), "unit": unit, "window": window_record,
                    "note": "power_quality", "flag": flag, "run_id": run_id,
                    "conditions": {**result["conditions"], "status": result["status"],
                                   **({"reason": result["reason"]} if "reason" in result else {}),
                                   **({"resolvable_harmonics": result["resolvable_harmonics"]}
                                      if "resolvable_harmonics" in result else {})}})
    return out, result
