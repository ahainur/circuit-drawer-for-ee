"""Scalar DC source/load stability for explicit continuous-time port models."""
import cmath
from copy import deepcopy
import csv
import io
import json
import math
from pathlib import Path
import tempfile
import uuid

from .control import lti
from .control.loop import evaluate, sweep
from .impedance import plot_svg

MAX_ORDER = 8  # bounded polynomial root problem for each supplied port
POLE_RTOL = 1e-7  # rad/s relative to each pole, with a 1 rad/s absolute scale
ROOT_RESIDUAL = 1e-8  # backward error accepted for the shared polynomial solver
ROOT_ROUNDOFF = 16 * math.ulp(1.0)  # Horner roundoff allowance, up to degree 16
MAX_PHASE_STEP = math.pi / 6  # resolve each turn with at least twelve segments
NOTE = "端口电流流入负载，Tm = Zs·Yl = Zs/Zl，特征方程为 1 + Tm = 0。结论针对所给连续时间端口模型。"


def example(power=60):
    return {"params": {"V": 48, "R": .2, "L": .002, "C": .00047, "P": power},
            "source_impedance": {"num": ["L", "R"], "den": ["L*C", "R*C", 1]},
            "load_admittance": {"num": ["-P/V^2"], "den": [1]},
            "freq": [1, 10000], "points": 80}


def _polynomial_roots(coefficients, *, scale=1):
    if not all(math.isfinite(c) for c in coefficients) or not any(coefficients):
        raise ValueError("模型多项式为零或数值溢出，请调整系数尺度")
    try:
        roots = lti.roots(coefficients)
    except ArithmeticError as error:
        raise ValueError("极点计算未收敛，请降低模型阶数或调整系数尺度") from error
    degree = len(coefficients) - 1
    derivative = [c * (degree - i) for i, c in enumerate(coefficients[:-1])]
    for root in roots:
        scale = lti.peval([abs(c) for c in coefficients], abs(root)).real
        residual = abs(lti.peval(coefficients, root))
        if not all(math.isfinite(v) for v in (abs(root), scale, residual)) or residual > ROOT_RESIDUAL * max(scale, 1e-300):
            raise ValueError("极点计算未收敛，请降低模型阶数或调整系数尺度")
        if root == 0 and coefficients[-1] == 0:
            continue
        # A small residual alone can hide repeated imaginary-axis roots split
        # into both half-planes. Refuse a pole estimate below the count precision.
        slope = abs(lti.peval(derivative, root))
        uncertainty = degree * (residual + ROOT_ROUNDOFF * scale)
        if uncertainty > slope * POLE_RTOL * max(1 / scale, abs(root)):
            raise ValueError("重根或病态多项式的极点精度不足，请降低模型阶数或调整系数尺度")
    return roots


def _port(raw, env, name):
    if not isinstance(raw, dict) or set(raw) != {"num", "den"}:
        raise ValueError("端口模型须包含 num、den 两组降幂系数")
    for values in raw.values():
        if not isinstance(values, list) or not 1 <= len(values) <= MAX_ORDER + 1:
            raise ValueError("每个端口的 num、den 须为非空数组，最高八阶")
    # Keep the supplied polynomials, including cancelled internal modes.
    num, den = (lti.trim([evaluate(c, env, f"{name}.{key}") for c in raw[key]]) for key in ("num", "den"))
    if den == [0]:
        raise ValueError("模型多项式为零或数值溢出，请调整系数尺度")
    if len(num) > len(den):
        raise ValueError("端口须为正则有理模型，分子阶数不能高于分母")
    return {"num": [c / den[0] for c in num], "den": [c / den[0] for c in den]}


def _count(roots):
    tolerance = [POLE_RTOL * max(1, abs(r)) for r in roots]
    return {"rhp": sum(r.real > t for r, t in zip(roots, tolerance)),
            "axis": sum(abs(r.real) <= t for r, t in zip(roots, tolerance))}


def _nyquist(num, den, poles, closed):
    return winding(lambda s: 1 + lti.peval(num, s) / lti.peval(den, s), poles, closed)


def winding(response, poles, closed):
    """Clockwise winding of a return difference on a complete RHP contour."""
    all_roots = poles + closed
    radius = 10 * max([1, *map(abs, all_roots)])
    positive = [r.real for r in all_roots if r.real > POLE_RTOL * max(1, abs(r))]
    near_axis = [r.real for r in poles if abs(r.real) <= POLE_RTOL * max(1, abs(r))]
    shift = max(1e-8, 2 * max([0, *near_axis]))
    if positive and shift >= min(positive):
        raise ValueError("Nyquist 轮廓接近奇点，请检查模型系数与临界极点")
    # The clockwise rectangle encloses every strictly RHP root. The positive
    # shift excludes imaginary-axis open poles, as a right-sided limiting contour.
    levels = {-radius, 0, radius}
    for root in all_roots:
        width = max(abs(root.real - shift), shift)
        for factor in (-100, -10, -5, -2, -1, -.5, -.1, 0, .1, .5, 1, 2, 5, 10, 100):
            levels.add(max(-radius, min(radius, root.imag + width * factor)))
    corners = [complex(shift, y) for y in sorted(levels)]
    corners += [complex(radius, radius), complex(radius, -radius), corners[0]]
    previous = None
    for subdivisions in (8, 16, 32, 64, 128):
        path = [a + (b - a) * i / subdivisions for a, b in zip(corners, corners[1:]) for i in range(subdivisions)]
        path.append(path[0])
        try:
            values = [response(s) for s in path]
        except ArithmeticError as error:
            raise ValueError("Nyquist 轮廓接近奇点，请检查模型系数与临界极点") from error
        if any(not math.isfinite(abs(v)) or abs(v) < 1e-15 for v in values):
            raise ValueError("Nyquist 轮廓接近奇点，请检查模型系数与临界极点")
        angles = [cmath.phase(b / a) for a, b in zip(values, values[1:])]
        winding = -sum(angles) / (2 * math.pi)
        integer = round(winding)
        if (integer == previous and abs(winding - integer) < 1e-6
                and max(map(abs, angles)) < MAX_PHASE_STEP):
            return {"clockwise": integer, "shift_rad_s": shift, "radius_rad_s": radius,
                    "samples": len(path), "max_phase_step_rad": max(map(abs, angles))}
        previous = integer
    raise ValueError("Nyquist 绕数未收敛，请检查模型尺度与临界极点")


def analyse(raw):
    if not isinstance(raw, dict) or set(raw) - {"params", "source_impedance", "load_admittance", "freq", "points"}:
        raise ValueError("阻抗比模型允许 params、source_impedance、load_admittance、freq、points")
    params = raw.get("params", {})
    if not isinstance(params, dict) or any(not isinstance(k, str) or not k.isidentifier() for k in params):
        raise ValueError("params 须使用有效参数名与数值或公式")
    env = {}
    for name, value in params.items():
        env[name] = evaluate(value, env, name)
    source = _port(raw.get("source_impedance"), env, "source_impedance")
    load = _port(raw.get("load_admittance"), env, "load_admittance")
    frequency = raw.get("freq", [1, 10000])
    if not isinstance(frequency, list) or len(frequency) != 2:
        raise ValueError("freq 须为起始和结束频率，单位 Hz")
    lo, hi = [evaluate(v, env, "freq") for v in frequency]
    points = evaluate(raw.get("points", 80), env, "points")
    if not 0 < lo < hi or hi / lo > 1e6 or not points.is_integer() or not 5 <= points <= 200:
        raise ValueError("频率须满足 0 < 起点 < 终点，最多六个十倍频程，每程 5～200 点")
    num = lti.real_poly(lti.pmul(source["num"], load["num"]))
    den = lti.real_poly(lti.pmul(source["den"], load["den"]))
    characteristic = lti.real_poly(lti.padd(den, num))
    if abs(characteristic[0]) <= 1e-10 * (1 + abs(num[0]) if len(num) == len(den) else 1):
        raise ValueError("高频直接通道使互联接近奇异，请检查 1 + Zs·Yl 的最高阶系数")
    poles = _polynomial_roots(den)
    closed = _polynomial_roots(characteristic)
    opened_count, closed_count = _count(poles), _count(closed)
    verdict = "unstable" if closed_count["rhp"] else "boundary" if closed_count["axis"] else "stable"
    nyquist = None
    if not closed_count["axis"]:
        nyquist = _nyquist(num, den, poles, closed)
        if opened_count["rhp"] + nyquist["clockwise"] != closed_count["rhp"]:
            raise ValueError("Nyquist 绕数与极点计数不一致，请检查模型的数值条件")
    def response(port, s):
        denominator = lti.peval(port["den"], s)
        if denominator == 0:
            raise ValueError("显示频点落在模型极点上，请调整显示频率")
        value = lti.peval(port["num"], s) / denominator
        if not math.isfinite(abs(value)):
            raise ValueError("频率响应数值溢出，请调整模型尺度")
        return value
    fs, values = sweep(lambda s: response(source, s) * response(load, s), lo, hi, int(points))
    if any(not math.isfinite(abs(v)) for v in values):
        raise ValueError("频率响应数值溢出，请调整模型尺度")
    records = [{"frequency_hz": f, "source_impedance": response(source, 2j * math.pi * f),
                "load_admittance": response(load, 2j * math.pi * f), "ratio": value}
               for f, value in zip(fs, values)]
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["frequency_hz", "zs_real_ohm", "zs_imag_ohm", "yl_real_siemens", "yl_imag_siemens", "tm_real", "tm_imag"])
    for record in records:
        writer.writerow([record["frequency_hz"], *[part for key in ("source_impedance", "load_admittance", "ratio")
                                                for part in (record[key].real, record[key].imag)]])
        for key in ("source_impedance", "load_admittance", "ratio"):
            record[key] = {"real": record[key].real, "imag": record[key].imag}
    return {"model": deepcopy(raw), "evaluated": {"params": env, "source_impedance": source, "load_admittance": load},
            "note": NOTE, "verdict": verdict, "open_count": opened_count, "closed_count": closed_count,
            "open_poles": [{"real": p.real, "imag": p.imag} for p in poles],
            "closed_poles": [{"real": p.real, "imag": p.imag} for p in closed],
            "characteristic": characteristic, "nyquist": nyquist, "records": records,
            "sampled_min_distance": min(abs(1 + v) for v in values),
            "svg": plot_svg(fs, values, [], ratio=True), "csv": output.getvalue()}


def export(result, out_dir, *, prefix="impedance-ratio"):
    destination = Path(out_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".impedance-ratio-", dir=destination) as temp:
        stage = Path(temp)
        (stage / "model.json").write_text(json.dumps(result["model"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (stage / "result.json").write_text(json.dumps({k: v for k, v in result.items() if k not in ("svg", "csv", "plots")}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (stage / (prefix + ".csv")).write_text(result["csv"], encoding="utf-8", newline="")
        (stage / (prefix + ".svg")).write_text(result["svg"], encoding="utf-8")
        for name, svg in result.get("plots", {}).items():
            (stage / (name + ".svg")).write_text(svg, encoding="utf-8")
        final = destination / ("run-" + uuid.uuid4().hex[:12])
        stage.rename(final)
    return final
