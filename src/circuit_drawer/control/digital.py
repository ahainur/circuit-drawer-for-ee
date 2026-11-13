"""Sampled controller settings shared by loop analysis and transient simulation.

``code`` supplies the sample rate and discretisation method. ``digital`` adds
whole-sample computation latency, ADC ranges, PWM resolution and an optional
finite-word model. Arithmetic uses a wide accumulator, rounding each stored
state and block output to the selected signed format.
"""
from __future__ import annotations

import cmath
import math
from copy import deepcopy
from dataclasses import dataclass, field

from .loop import LoopError, Tf, evaluate
from .lti import Zpk
from .discrete import Z_ROOT_TOL

KEYS = ("delay_samples", "adc", "pwm_bits", "word_bits", "fraction_bits", "note")
ADC_KEYS = ("bits", "min", "max")
MAX_DELAY = 16  # Bounds the delay polynomial and the transient history queue.
MAX_BITS = 24   # Products of two codes remain exactly representable in a double.
NOTE = "数字控制按 code.fs 逐拍采样和保持，计算延迟按整拍计入。"
LINEAR_NOTE = "离散环路采用零阶保持功率级和量化后的控制器系数。ADC、PWM 和状态舍入按单位小信号增益处理，极限环请结合时域波形判断。"


def field_reference() -> str:
    return (f"Control-table digital keys: {', '.join(KEYS)}. Omit digital for continuous simulation.\n"
            f"code.fs supplies the sampling frequency in Hz. digital.delay_samples is an integer 0..{MAX_DELAY}.\n"
            f"digital.adc maps controller input ids to exactly {', '.join(ADC_KEYS)}. "
            f"bits and pwm_bits are integers 1..{MAX_BITS}. ADC min/max are finite values or loop.params expressions, min < max.\n"
            f"word_bits (8..{MAX_BITS}) and fraction_bits (0..word_bits-2) are supplied together or both omitted. "
            "Signed states and outputs saturate and round to this format, coefficients use per-section scaling.\n"
            "These saved settings are preserved through AI structural edits. Configure them in the digital-control dialog.\n"
            'Example saved data: {"digital":{"delay_samples":1,"adc":{"plant":{"bits":12,"min":0,"max":24}},'
            '"pwm_bits":14,"word_bits":24,"fraction_bits":18}}\n')


@dataclass
class Digital:
    delay_samples: int = 0
    adc: dict[str, dict] = field(default_factory=dict)
    pwm_bits: int | None = None
    word_bits: int | None = None
    fraction_bits: int | None = None

    @property
    def limits(self):
        if self.word_bits is None:
            return None, None
        scale = 2 ** self.fraction_bits
        return -2 ** (self.word_bits - 1) / scale, (2 ** (self.word_bits - 1) - 1) / scale

    def round(self, value: float) -> float:
        if self.word_bits is None:
            return value
        lo, hi = self.limits
        scale = 2 ** self.fraction_bits
        return min(hi, max(lo, math.floor(value * scale + 0.5) / scale))

    def coefficients(self, values: list[float]) -> list[float]:
        if not all(map(math.isfinite, values)):
            raise LoopError("digital 控制器系数须为有限值，请检查采样率和传递函数参数")
        if self.word_bits is None:
            return list(values)
        # Three guard bits cover the sum of five SOS products.
        big = max(map(abs, values), default=0.0)
        shift = max(0, math.ceil(math.log2(big + 1e-300) + 1e-12)) if big else 0
        q = self.word_bits - 3 - shift
        if q < 0:
            raise LoopError("digital.word_bits 太小，控制器系数超出范围，请增加字长或缩放控制信号")
        return [math.floor(v * 2 ** q + 0.5) / 2 ** q for v in values]


def _integer(value, what: str, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise LoopError(f"{what} 须为 {lo} 到 {hi} 的整数")
    return value


def parse(raw, pl, env: dict) -> Digital | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) - set(KEYS):
        raise LoopError(f"digital 须为对象，允许字段 {list(KEYS)}")
    cfg = Digital(delay_samples=_integer(raw.get("delay_samples", 0), "digital.delay_samples", 0, MAX_DELAY))
    if "pwm_bits" in raw:
        cfg.pwm_bits = _integer(raw["pwm_bits"], "digital.pwm_bits", 1, MAX_BITS)
    if "word_bits" in raw or "fraction_bits" in raw:
        cfg.word_bits = _integer(raw.get("word_bits"), "digital.word_bits", 8, MAX_BITS)
        cfg.fraction_bits = _integer(raw.get("fraction_bits"), "digital.fraction_bits", 0, cfg.word_bits - 2)
    adc = raw.get("adc", {})
    if not isinstance(adc, dict):
        raise LoopError("digital.adc 须为对象，键为控制器输入名称")
    for bid, settings in adc.items():
        what = f"digital.adc.{bid}"
        if bid not in pl.inputs:
            raise LoopError(f"{what} 须指定控制器输入 {pl.inputs}")
        if not isinstance(settings, dict) or set(settings) != set(ADC_KEYS):
            raise LoopError(f"{what} 需要 bits、min、max 三个字段")
        bits = _integer(settings["bits"], f"{what}.bits", 1, MAX_BITS)
        lo = evaluate(settings["min"], env, f"{what}.min")
        hi = evaluate(settings["max"], env, f"{what}.max")
        if hi <= lo or not math.isfinite(hi - lo):
            raise LoopError(f"{what}.max 须大于 min，量程差须为有限值")
        step = (hi - lo) / 2 ** bits
        if step == 0 or lo + step == lo:
            raise LoopError(f"{what} 的量化步长过小，请调整量程或位数")
        cfg.adc[bid] = {"bits": bits, "min": lo, "max": hi}
    return cfg


def prepare(pl, cfg: Digital):
    """Copy the existing discrete plan, applying the selected coefficient format."""
    pl = deepcopy(pl)
    for st in pl.steps:
        if st.kind == "fn":
            raise LoopError(f"digital 暂支持标量控制器，{st.id} 的函数块需要连续模式")
        keys = {"pi": ("kp", "c0", "c1"), "gain": ("k",)}.get(st.kind, ())
        st.data.update(zip(keys, cfg.coefficients([st.data[k] for k in keys])))
        if st.kind == "sos":
            st.data["sos"].gain = cfg.coefficients([st.data["sos"].gain])[0]
            for sec in st.data["sos"].sections:
                c = cfg.coefficients(sec.coeffs())
                sec.b, sec.a = c[:3], [1.0, *c[3:]]
    return pl


def adc_value(value: float, cfg: dict) -> float:
    step = (cfg["max"] - cfg["min"]) / 2 ** cfg["bits"]
    code = min(2 ** cfg["bits"] - 1, max(0, math.floor((value - cfg["min"]) / step + 0.5)))
    return cfg["min"] + code * step


def pwm_value(value: float, bits: int | None) -> float:
    value = min(1.0, max(0.0, value))
    return math.floor(value * 2 ** bits + 0.5) / 2 ** bits if bits else value


def zpk(num: list[float], den: list[float]) -> Zpk:
    """z^-1 coefficients -> a rational function in z, retaining causal delays."""
    n = max(len(num), len(den))
    return Zpk.from_poly(num + [0.0] * (n - len(num)), den + [0.0] * (n - len(den)), root_tol=Z_ROOT_TOL)


@dataclass
class SampledTf(Tf):
    ts: float = 1.0

    def at(self, s: complex) -> complex:
        return self.zpk(cmath.exp(s * self.ts))

    def rational(self) -> Zpk:
        return self.zpk


def network(rep, net, spec):
    """Replace scalar graph coefficients with the matching sampled model."""
    from .codegen import plan
    from .discrete import DiscreteError, discretize
    pl = plan(rep)
    cfg = parse(rep.diagram.raw["digital"], pl, spec.params)
    pl = prepare(pl, cfg)
    steps = {st.id: st for st in pl.steps}
    delay = Zpk(1.0, [], [0j] * cfg.delay_samples)
    for bid, nd in net.nodes.items():
        st = steps.get(bid)
        terms = []
        for src, gain in nd.terms:
            if st is None and not nd.source:
                if src not in pl.outputs or bid not in pl.inputs or not isinstance(gain, Tf):
                    raise LoopError(f"digital 的模拟功率级 {src} → {bid} 须直接连接控制器输出与采样输入")
                if gain.delay:
                    raise LoopError(f"digital 功率级 {bid} 请用 digital.delay_samples 设置整拍延迟")
                if bid in cfg.adc and cfg.pwm_bits:
                    adc = cfg.adc[bid]
                    quantum = (adc["max"] - adc["min"]) / 2 ** adc["bits"]
                    try:
                        pwm_step = abs(gain.zpk(0j)) / 2 ** cfg.pwm_bits
                    except ZeroDivisionError:
                        pwm_step = math.inf
                    if math.isfinite(pwm_step):
                        net.digital_resolution.append({"input": bid, "output": src, "adc_step": quantum,
                                                       "pwm_step_at_input": pwm_step, "ratio": pwm_step / quantum,
                                                       "coarse_pwm": pwm_step > quantum})
                try:
                    d = discretize(gain.zpk, pl.ts, "zoh")
                except DiscreteError:
                    raise LoopError(f"digital 功率级 {bid} 的零阶保持等效需要互异的非零极点，传递函数分子阶数须不高于分母") from None
                result = zpk(d.num(), d.den()) * delay
            elif st and st.kind == "pi":
                d = st.data
                result = zpk([d["kp"] + d["c0"], d["c1"] - d["kp"]], [1.0, -1.0])
            elif st and st.kind == "sos":
                sos = st.data["sos"]
                result = Zpk.const(sos.gain)
                for sec in sos.sections:
                    result *= zpk(sec.b, sec.a)
            elif st and st.kind == "gain":
                result = Zpk.const(st.data["k"])
            elif st and st.kind in ("mul", "div"):
                raise LoopError(f"digital 环路分析需要 {bid} 的线性小信号模型")
            else:
                result = gain.zpk if isinstance(gain, Tf) else Zpk.const(gain)
            if isinstance(gain, Tf) and gain.delay:
                raise LoopError(f"digital 控制器 {bid} 请用 digital.delay_samples 设置整拍延迟")
            if st and st.delay:
                result *= Zpk(1.0, [], [0j])
            terms.append((src, SampledTf(result, ts=pl.ts)))
        nd.terms = terms
    lo, hi = spec.freq
    # Nyquist itself may put a pole on z = -1, so leave a small open interval.
    spec.freq = (lo, min(hi, pl.fs * 0.499))
    if spec.freq[1] <= lo:
        raise LoopError("digital 扫频起点须低于 code.fs / 2")
    return cfg, pl
