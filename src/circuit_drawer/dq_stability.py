"""Generalized Nyquist and internal poles for explicit two-axis port models."""
import cmath
from copy import deepcopy
import csv
from decimal import Decimal, localcontext
import io
import math

from .control import lti
from .control.loop import evaluate, sweep
from .impedance import plot_svg
from .impedance_ratio import MAX_ORDER, _polynomial_roots, _count, winding

MAX_FEEDTHROUGH_CONDITION = 1e10  # double precision algebraic feedback solve
IDENTITY_RTOL = 1e-7  # matrix return difference versus full-state determinant
IDENTITY_SCALE_FLOOR = 1e-12  # determinant scale when comparing a near-zero response
DECIMAL_PRECISION = 80  # retain characteristic coefficients before float rounding
DECIMAL_ROUNDOFF = Decimal('1e-70')  # allowance for up to sixteen state eliminations
REFINEMENT_RTOL = Decimal('1e-25')  # reserve digits for multiplicity reconstruction
RECONSTRUCTION_RTOL = Decimal('1e-18')  # compare the complete characteristic polynomial
REFINEMENT_ITERS = 80  # bounded Newton refinement of existing root estimates
NOTE = "同一固定 dq 坐标系，电流流入负载，Δv = −Zs·Δi，Δi = Yl·Δv。T = Zs·Yl，以 det(I+T) 绕原点的顺时针绕数核对内部极点。"


def example(power=1500):
    return {"params": {"V": 100, "P": power, "R": .2, "L": .002, "Cbus": .00047,
                       "omega": "2*pi*50", "G": "2*P/(3*V^2)"},
            "source_impedance": {
                "A": [["-R/L", "omega", "-1/L", 0], ["-omega", "-R/L", 0, "-1/L"],
                      ["1/Cbus", 0, 0, "omega"], [0, "1/Cbus", "-omega", 0]],
                "B": [[0, 0], [0, 0], ["-1/Cbus", 0], [0, "-1/Cbus"]],
                "C": [[0, 0, -1, 0], [0, 0, 0, -1]], "D": [[0, 0], [0, 0]]},
            "load_admittance": {"A": [], "B": [], "C": [[], []], "D": [["-G", 0], [0, "G"]]},
            "freq": [1, 10000], "points": 80}


def _matmul(a, b):
    return [[sum(x*y for x, y in zip(row, col)) for col in zip(*b)] for row in a]


def _det(matrix):
    """Pivoted determinant, including the empty state matrix."""
    a = [list(row) for row in matrix]
    result = 1 + 0j
    for j in range(len(a)):
        pivot = max(range(j, len(a)), key=lambda i: abs(a[i][j]))
        if a[pivot][j] == 0:
            return 0j
        if pivot != j:
            a[j], a[pivot] = a[pivot], a[j]
            result = -result
        value = a[j][j]
        result *= value
        for i in range(j+1, len(a)):
            factor = a[i][j] / value
            for k in range(j+1, len(a)):
                a[i][k] -= factor * a[j][k]
    return result


def _resolvent(a, s):
    return [[complex(s if i == j else 0) - value for j, value in enumerate(row)] for i, row in enumerate(a)]


def _refine_roots(coefficients, scale):
    """Refine shared double-precision seeds against 80-digit coefficients."""
    roots = []
    for seed in lti.roots([float(c) for c in coefficients]):
        x, y = Decimal.from_float(seed.real), Decimal.from_float(seed.imag)
        for _ in range(REFINEMENT_ITERS):
            real, imag, dr, di = coefficients[0], Decimal(0), Decimal(0), Decimal(0)
            magnitude = (x*x+y*y).sqrt()
            bound = abs(real)
            for c in coefficients[1:]:
                dr, di = dr*x-di*y+real, dr*y+di*x+imag
                real, imag = real*x-imag*y+c, real*y+imag*x
                bound = bound*magnitude+abs(c)
            slope_squared = dr*dr+di*di
            uncertainty = (real*real+imag*imag).sqrt()+DECIMAL_ROUNDOFF*bound
            # Leave ample precision for the complete-coefficient check below.
            tolerance = REFINEMENT_RTOL*max(1/Decimal.from_float(scale),magnitude)
            if slope_squared and uncertainty < slope_squared.sqrt()*tolerance:
                break
            if not slope_squared:
                raise ValueError("重根或病态多项式的极点精度不足，请降低模型阶数或调整系数尺度")
            x, y = x-(real*dr+imag*di)/slope_squared, y-(imag*dr-real*di)/slope_squared
        else:
            raise ValueError("重根或病态多项式的极点精度不足，请降低模型阶数或调整系数尺度")
        roots.append((x,y))
    # Independent Newton iterations can converge to the same root. Reconstruct
    # every coefficient at high precision to check the complete multiplicity.
    rebuilt = [(Decimal(1),Decimal(0))]
    bounds = [Decimal(1)]
    for x,y in roots:
        product = [(Decimal(0),Decimal(0)) for _ in range(len(rebuilt)+1)]
        product_bounds = [Decimal(0)] * len(product)
        magnitude = (x*x+y*y).sqrt()
        for i,(real,imag) in enumerate(rebuilt):
            pr,pi = product[i]
            product[i] = pr+real,pi+imag
            pr,pi = product[i+1]
            product[i+1] = pr-real*x+imag*y,pi-real*y-imag*x
            product_bounds[i] += bounds[i]
            product_bounds[i+1] += bounds[i]*magnitude
        rebuilt = product
        bounds = product_bounds
    # Compare each coefficient at its own natural scale. A global monic scale
    # can hide lost slow modes when state units make the matrix norm enormous.
    if any(abs(real-c)+abs(imag) > RECONSTRUCTION_RTOL*max(abs(c),bound)
           for (real,imag),c,bound in zip(rebuilt,coefficients,bounds)):
        raise ValueError("重根或病态多项式的极点精度不足，请降低模型阶数或调整系数尺度")
    return [complex(float(x),float(y)) for x,y in roots]


def _poles(a):
    n = len(a)
    # Triangular state blocks keep repeated and unobservable modes explicitly.
    reach = [[i == j or a[i][j] != 0 for j in range(n)] for i in range(n)]
    for k in range(n):
        for i in range(n):
            for j in range(n):
                reach[i][j] = reach[i][j] or (reach[i][k] and reach[k][j])
    remaining, poles = set(range(n)), []
    while remaining:
        i = min(remaining)
        indices = sorted(j for j in remaining if reach[i][j] and reach[j][i])
        remaining.difference_update(indices)
        block = [[a[i][j] for j in indices] for i in indices]
        if len(block) == 1:
            poles.append(complex(block[0][0]))
            continue
        # Faddeev-LeVerrier on a scaled matrix. Extra decimal precision avoids
        # cancellation in characteristic coefficients before the shared solver.
        scale = max(abs(v) for row in block for v in row)
        with localcontext() as context:
            context.prec = DECIMAL_PRECISION
            size = len(block)
            matrix = [[Decimal.from_float(v) / Decimal.from_float(scale) for v in row] for row in block]
            b = [[Decimal(i == j) for j in range(size)] for i in range(size)]
            coefficients = [Decimal(1)]
            for k in range(1, size+1):
                b = _matmul(matrix, b)
                c = -sum(b[i][i] for i in range(size)) / k
                coefficient = float(c)
                if c and coefficient == 0:
                    raise ValueError("极点计算未收敛，请降低模型阶数或调整系数尺度")
                coefficients.append(c)
                for i in range(size):
                    b[i][i] += c
            if size == 2:
                # Repeated quadratic roots are available without iteration.
                trace, determinant = -float(coefficients[1]), float(coefficients[2])
                radical = cmath.sqrt(trace*trace-4*determinant)
                first = (trace + radical if trace >= 0 else trace - radical) / 2
                values = [first, determinant / first] if first else [0j, 0j]
            else:
                try:
                    values = _polynomial_roots([float(c) for c in coefficients], scale=scale)
                except ValueError:
                    values = _refine_roots(coefficients, scale)
        poles.extend(v * scale for v in values)
    if any(not math.isfinite(abs(p)) for p in poles):
        raise ValueError("极点计算未收敛，请降低模型阶数或调整系数尺度")
    return sorted(poles, key=lambda p: (p.real, p.imag))


def _port(raw, env, name):
    if not isinstance(raw, dict) or set(raw) != {"A", "B", "C", "D"} or not isinstance(raw["A"], list):
        raise ValueError("dq 端口模型须包含 A、B、C、D 状态空间矩阵")
    n = len(raw["A"])
    if n > MAX_ORDER:
        raise ValueError("每个 dq 端口最多八个内部状态")
    dimensions = {"A": (n, n), "B": (n, 2), "C": (2, n), "D": (2, 2)}
    result = {}
    for key, (rows, columns) in dimensions.items():
        value = raw[key]
        if not isinstance(value, list) or len(value) != rows or any(not isinstance(row, list) or len(row) != columns for row in value):
            raise ValueError(f"{name}.{key} 的矩阵尺寸须为 {rows}×{columns}")
        result[key] = [[evaluate(v, env, f"{name}.{key}") for v in row] for row in value]
    return result


def _response(port, s):
    a = _resolvent(port["A"], s)
    result = deepcopy(port["D"])
    for j in range(2):
        x = lti.solve(a, [row[j] for row in port["B"]], 0j, lambda v: v == 0)
        for i in range(2):
            result[i][j] += sum(c*v for c, v in zip(port["C"][i], x))
    return result


def _closed_matrix(source, load):
    feedthrough = _matmul(source["D"], load["D"])
    for i in range(2):
        feedthrough[i][i] += 1
    determinant = _det(feedthrough).real
    norm = max(abs(v) for row in feedthrough for v in row)
    # Include formation of I+Ds*Dl, even when cancellation makes all of M small.
    product_bound = max(sum(abs(source["D"][i][k]*load["D"][k][j]) for k in range(2))
                        for i in range(2) for j in range(2))
    if not math.isfinite(determinant) or abs(determinant) <= norm*max(1, norm, product_bound)/MAX_FEEDTHROUGH_CONDITION:
        raise ValueError("I+Ds·Dl 接近奇异，请检查端口直接通道")
    ns, nl = len(source["A"]), len(load["A"])
    closed = [[0.0] * (ns+nl) for _ in range(ns+nl)]
    for k in range(ns+nl):
        ys = [source["C"][i][k] if k < ns else 0 for i in range(2)]
        yl = [load["C"][i][k-ns] if k >= ns else 0 for i in range(2)]
        right = [-ys[i] - sum(source["D"][i][j]*yl[j] for j in range(2)) for i in range(2)]
        voltage = lti.solve([[complex(v) for v in row] for row in feedthrough], right, 0j, lambda v: v == 0)
        current = [yl[i] + sum(load["D"][i][j]*voltage[j] for j in range(2)) for i in range(2)]
        for i in range(ns):
            closed[i][k] = ((source["A"][i][k] if k < ns else 0) + sum(source["B"][i][j]*current[j] for j in range(2))).real
        for i in range(nl):
            closed[ns+i][k] = ((load["A"][i][k-ns] if k >= ns else 0) + sum(load["B"][i][j]*voltage[j] for j in range(2))).real
    if any(not math.isfinite(v) for row in closed for v in row):
        raise ValueError("频率响应数值溢出，请调整模型尺度")
    return closed, determinant


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
    freq = raw.get("freq", [1, 10000])
    if not isinstance(freq, list) or len(freq) != 2:
        raise ValueError("freq 须为起始和结束频率，单位 Hz")
    lo, hi = [evaluate(v, env, "freq") for v in freq]
    points = evaluate(raw.get("points", 80), env, "points")
    if not 0 < lo < hi or hi/lo > 1e6 or not points.is_integer() or not 5 <= points <= 200:
        raise ValueError("频率须满足 0 < 起点 < 终点，最多六个十倍频程，每程 5～200 点")
    try:
        closed, direct = _closed_matrix(source, load)
        poles = _poles(source["A"]) + _poles(load["A"])
        closed_poles = _poles(closed)
    except ArithmeticError as error:
        raise ValueError("极点计算未收敛，请降低模型阶数或调整系数尺度") from error
    opened_count, closed_count = _count(poles), _count(closed_poles)
    def matrices(s):
        zs, yl = _response(source, s), _response(load, s)
        tm = _matmul(zs, yl)
        returned = [[v + (i == j) for j, v in enumerate(row)] for i, row in enumerate(tm)]
        value = _det(returned)
        if any(not math.isfinite(abs(v)) for matrix in (zs, yl, tm, [[value]]) for row in matrix for v in row):
            raise ValueError("频率响应数值溢出，请调整模型尺度")
        return zs, yl, tm, value
    nyquist = None
    if not closed_count["axis"]:
        nyquist = winding(lambda s: matrices(s)[3], poles, closed_poles)
        if opened_count["rhp"] + nyquist["clockwise"] != closed_count["rhp"]:
            raise ValueError("Nyquist 绕数与极点计数不一致，请检查模型的数值条件")
    fs, _ = sweep(lambda s: 0, lo, hi, int(points))
    values, records, entries = [], [], {name: [] for name in ("Tdd", "Tdq", "Tqd", "Tqq")}
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["frequency_hz", *[f"{name}_{axis}_{part}" for name in ("zs_ohm", "yl_siemens", "tm") for axis in ("dd", "dq", "qd", "qq") for part in ("real", "imag")], "det_real", "det_imag"])
    def packed(value):
        return {"real": value.real, "imag": value.imag}
    for f in fs:
        s = 2j*math.pi*f
        try:
            zs, yl, tm, value = matrices(s)
            independent = direct * _det(_resolvent(closed, s)) / (_det(_resolvent(source["A"], s)) * _det(_resolvent(load["A"], s)))
        except ArithmeticError as error:
            raise ValueError("显示频点落在模型极点上，请调整显示频率") from error
        if not math.isfinite(abs(independent)) or abs(value-independent) > IDENTITY_RTOL * max(abs(value), abs(independent), IDENTITY_SCALE_FLOOR):
            raise ValueError("dq 端口行列式与完整状态模型不一致，请检查数值尺度")
        values.append(value)
        for name, v in zip(entries, [v for row in tm for v in row]):
            entries[name].append(v)
        records.append({"frequency_hz": f, "source_impedance": [[packed(v) for v in row] for row in zs],
                        "load_admittance": [[packed(v) for v in row] for row in yl],
                        "ratio": [[packed(v) for v in row] for row in tm], "determinant": packed(value)})
        writer.writerow([f, *[part for matrix in (zs, yl, tm) for row in matrix for v in row for part in (v.real, v.imag)], value.real, value.imag])
    plots = {name: plot_svg(fs, vals, []).replace("Zout", name).replace("Output impedance", name).replace("dBΩ", "dB").replace(" / Ω", "")
             .replace("AC 扫频（蓝色） / 时域注入（橙色）", "完整矩阵中的单项频率响应") for name, vals in entries.items()}
    return {"model": deepcopy(raw), "evaluated": {"params": env, "source_impedance": source, "load_admittance": load},
            "note": NOTE, "verdict": "unstable" if closed_count["rhp"] else "boundary" if closed_count["axis"] else "stable",
            "open_count": opened_count, "closed_count": closed_count, "open_poles": list(map(packed, poles)),
            "closed_poles": list(map(packed, closed_poles)), "closed_state_matrix": closed, "direct_determinant": direct,
            "nyquist": nyquist, "records": records, "sampled_min_distance": min(map(abs, values)),
            "svg": plot_svg(fs, values, [], determinant=True), "plots": plots, "csv": stream.getvalue()}
