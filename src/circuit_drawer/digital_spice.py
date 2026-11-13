"""Execute a discrete controller at ngspice time breakpoints and inspect samples.

The electrical solver keeps its capacitor/inductor states across stop/resume.
Only controller voltage sources are altered, implementing a zero-order hold.
"""
from __future__ import annotations

import csv
import math
from pathlib import Path

from .closed_loop import ClosedLoopError, duty_node, node

MAX_SAMPLES = 20000  # ngspice keeps the scheduled breakpoints for the whole run.
MIN_CYCLES = 4       # Repetition alone is evidence for a candidate, not its cause.
MAX_PERIOD = 256     # samples, includes oscillations well below the switching rate
TIME_TOL = 1e-9      # Ts fraction, admits merged coincident solver breakpoints.
COMMAND_REL_TOL = 1e-10  # Ignore roundoff relative to the command's unit scale (duty or Hz).


def sample_count(loop, stop: float) -> int:
    count = stop * loop.plan.fs
    if not math.isfinite(count) or count > MAX_SAMPLES + 1:
        raise ClosedLoopError(f"数字闭环最多 {MAX_SAMPLES} 拍，请缩短瞬态时间或降低 code.fs")
    # The final point belongs to the completed run, not a further controller step.
    count = math.ceil(count - 1e-10) - 1
    if count < 1:
        raise ClosedLoopError("数字闭环的瞬态时间须大于一个采样周期")
    return count


def _number(value: float) -> str:
    return f"{value:.17g}"


def _clamp(expr: str, lo, hi) -> str:
    if lo is not None:
        expr = f"max({expr}, {_number(lo)})"
    if hi is not None:
        expr = f"min({expr}, {_number(hi)})"
    return expr


def drive_column(loop, bid):
    return ('frequency_Hz_' if loop.outputs[bid].get('modulation') == 'vco' else 'duty_') + bid


def commands(loop, stop: float) -> list[str]:
    """The control-script commands replacing a single uninterrupted ``run``."""
    pl, cfg = loop.plan, loop.digital
    count = sample_count(loop, stop)
    init, step = [], []
    names = {bid: f"const.cd_v{i}" for i, bid in enumerate([*pl.inputs, *(st.id for st in pl.steps)])}

    def declare(name, value=0):
        init.append(f"let {name} = {_number(value)}")
        return name

    def rounding(expr):
        if cfg.word_bits is None:
            return expr
        scale = 2 ** cfg.fraction_bits
        expr = f"floor(({expr})*{scale} + 0.5)/{scale}"
        return _clamp(expr, *cfg.limits)

    def assign(name, expr, lo=None, hi=None):
        step.append(f"let {name} = {rounding(_clamp(expr, lo, hi))}")

    for name in names.values():
        declare(name)
    trace, columns = ["const.cd_time"], ["time_s"]
    for i, bid in enumerate(pl.inputs):
        raw = declare(f"const.cd_raw{i}")
        step.append(f"let {raw} = v({node(bid)})[length(time)-1]")
        expr = raw
        if bid in cfg.adc:
            adc = cfg.adc[bid]
            quantum = (adc["max"] - adc["min"]) / 2 ** adc["bits"]
            code = _clamp(f"floor(({raw} - {_number(adc['min'])})/{_number(quantum)} + 0.5)",
                          0, 2 ** adc["bits"] - 1)
            expr = f"{_number(adc['min'])} + ({code})*{_number(quantum)}"
        assign(names[bid], expr)
        trace += [raw, names[bid]]
        columns += [f"raw_{bid}", f"sample_{bid}"]

    delayed = []
    for i, st in enumerate(pl.steps):
        x, target = [names[s] for s in st.srcs], names[st.id]
        if st.delay:
            target = declare(f"const.cd_next{i}")
            delayed.append((names[st.id], target))
        if st.kind == "pi":
            integ, prev = declare(f"const.cd_i{i}"), declare(f"const.cd_e{i}")
            d = st.data
            assign(integ, f"{integ} + {_number(d['c0'])}*{x[0]} + {_number(d['c1'])}*{prev}", st.lo, st.hi)
            step.append(f"let {prev} = {x[0]}")
            expr = f"{_number(d['kp'])}*{x[0]} + {integ}"
        elif st.kind == "sos":
            u = declare(f"const.cd_u{i}")
            y = declare(f"const.cd_y{i}")
            assign(u, f"{x[0]}*{_number(st.data['sos'].gain)}")
            sections = st.data["sos"].sections
            for j, sec in enumerate(sections):
                a, b, c, d = [declare(f"const.cd_s{i}_{j}_{k}") for k in range(4)]
                values = zip(sec.coeffs(), [u, a, b, c, d], [1, 1, 1, -1, -1])
                expr = " + ".join(f"{_number(coef * sign)}*{value}" for coef, value, sign in values)
                assign(y, expr, st.lo if j == len(sections) - 1 else None,
                       st.hi if j == len(sections) - 1 else None)
                step += [f"let {b} = {a}", f"let {a} = {u}", f"let {d} = {c}",
                         f"let {c} = {y}", f"let {u} = {y}"]
            expr = u
        elif st.kind == "gain":
            expr = f"{_number(st.data['k'])}*{x[0]}"
        elif st.kind == "sum":
            expr = " + ".join(f"{_number(g)}*{v}" for g, v in zip(st.signs, x))
        elif st.kind == "mul":
            expr = "*".join(x)
        elif st.kind == "div":
            den = "*".join(x[1:])
            expr = f"(({den}) <> 0 ? {x[0]}/({den}) : 0)"
        else:
            expr = x[0]
        assign(target, expr, st.lo, st.hi)

    for st in pl.steps:
        step.append(f"alter V{node(st.id)} = {names[st.id]}")
    for i, bid in enumerate(loop.outputs):
        output = loop.outputs[bid]
        bounds = (output['frequency_min'], output['frequency_max']) if output.get('modulation') == 'vco' else (0, 1)
        applied = declare(f"const.cd_duty{i}", bounds[0])
        value = _clamp(names[bid], *bounds)
        if cfg.pwm_bits:
            scale = 2 ** cfg.pwm_bits
            value = f"floor(({value})*{scale} + 0.5)/{scale}"
        queue = [declare(f"const.cd_q{i}_{j}", bounds[0]) for j in range(cfg.delay_samples)]
        step.append(f"let {applied} = {queue[-1] if queue else value}")
        for j in range(len(queue) - 1, 0, -1):
            step.append(f"let {queue[j]} = {queue[j-1]}")
        if queue:
            step.append(f"let {queue[0]} = {value}")
        step.append(f"alter V{duty_node(loop, bid)} = {applied}")
        trace.append(applied)
        columns.append(drive_column(loop, bid))
    step += [f"let {name} = {value}" for name, value in delayed]
    for column, value in zip(columns, trace):
        init.append(f"let const.cd_trace_{column} = vector({count})")
        step.append(f"let const.cd_trace_{column}[const.cd_index] = {value}")
    # $& substitution formats scalars with only six significant digits. Keep
    # times as literals and use vector expressions for alter and recorded data.
    # '=' tests exact doubles and misses a time merged with a PWM edge. The
    # 'ge' installs a time breakpoint and tolerates that merge.
    stops = [f"stop when time ge {_number((i - TIME_TOL) * pl.ts)}" for i in range(1, count + 1)]
    vectors = " ".join(f"cd_trace_{c}" for c in columns[1:])
    return ["set numdgt=16", *init, *stops, "run", "set cd_tranplot = $curplot",
            "let const.cd_index = 0", f"while const.cd_index < {count}",
            "let const.cd_break = const.cd_index + 1", "delete $&const.cd_break",
            "let const.cd_time = time[length(time)-1]", *step,
            "resume", "let const.cd_index = const.cd_index + 1", "end",
            "setplot const", "setscale cd_trace_time_s", "set wr_singlescale", "set wr_vecnames",
            "wrdata digital_samples.dat " + vectors, "setplot $cd_tranplot", "echo CD_DIGITAL_COMPLETE"]


def inspect(loop, stop: float, stage: Path) -> dict:
    """Check the complete sample log and report short periodic output candidates."""
    path = stage / "digital_samples.dat"
    if not path.is_file():
        raise ClosedLoopError("数字控制采样记录缺失")
    lines = path.read_text(encoding="ascii").splitlines()
    columns = ["time_s"]
    for bid in loop.plan.inputs:
        columns += [f"raw_{bid}", f"sample_{bid}"]
    columns += [drive_column(loop, bid) for bid in loop.outputs]
    if not lines or lines[0].split() != [f"cd_trace_{c}" for c in columns]:
        raise ClosedLoopError("数字控制采样记录列名错误")
    try:
        rows = [[float(v) for v in line.split()] for line in lines[1:] if line.strip()]
    except ValueError:
        raise ClosedLoopError("数字控制采样记录包含非数值") from None
    if len(rows) != sample_count(loop, stop):
        raise ClosedLoopError("数字控制采样记录未覆盖完整时间范围")
    for i, row in enumerate(rows, 1):
        if (len(row) != len(columns) or not all(map(math.isfinite, row))
                or not math.isclose(row[0], i * loop.plan.ts, rel_tol=1e-10, abs_tol=loop.plan.ts * 1e-8)):
            raise ClosedLoopError("数字控制采样记录的数值或采样时刻错误")
    with (stage / "digital.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(rows)
    candidates = []
    cfg = loop.digital
    for bid in loop.outputs:
        values = [r[columns.index(drive_column(loop, bid))] for r in rows]
        tolerance = (0.25 / 2 ** cfg.pwm_bits if cfg.pwm_bits else
                     COMMAND_REL_TOL * max(1, max(map(abs, values))))
        for period in range(2, min(MAX_PERIOD, len(rows) // MIN_CYCLES) + 1):
            tail = values[-MIN_CYCLES * period:]
            if max(tail) - min(tail) <= tolerance:
                continue
            if any(abs(tail[k] - tail[k-period]) > tolerance for k in range(period, len(tail))):
                continue
            # Require the physical input to repeat too, excluding an output
            # stuck against a periodic quantizer while startup still drifts.
            repeated = True
            for inp in loop.plan.inputs:
                signal = [r[columns.index(f"raw_{inp}")] for r in rows[-MIN_CYCLES * period:]]
                adc = cfg.adc.get(inp)
                eps = (adc["max"] - adc["min"]) / 2 ** adc["bits"] / 4 if adc else 1e-7 * max(1, max(map(abs, signal)))
                repeated &= all(abs(signal[k] - signal[k-period]) <= eps for k in range(period, len(signal)))
            if repeated:
                measure = 'frequency_peak_to_peak_hz' if loop.outputs[bid].get('modulation') == 'vco' else 'duty_peak_to_peak'
                candidates.append({"output": bid, "period_samples": period,
                                   "frequency_hz": loop.plan.fs / period,
                                   measure: max(tail) - min(tail)})
                break
    messages = [f"{c['output']} 出现周期 {c['period_samples']} 拍的重复振荡，疑似量化极限环。请增加采样量化分辨率或定点字长并比较尾段波形。"
                for c in candidates]
    return {"samples": len(rows), "fs_hz": loop.plan.fs, "delay_samples": cfg.delay_samples,
            "candidate_cycles": candidates, "messages": messages}
