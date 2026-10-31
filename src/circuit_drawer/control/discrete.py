"""Discretising controller transfer functions for a DSP or MCU.

``discretize`` maps a continuous ``Zpk`` to the z domain (Tustin with
optional pre-warping, ZOH, backward or forward Euler).  ``sections`` splits
the result into second-order sections, the form fixed-point code runs: one
section's coefficients stay well scaled where one high-order polynomial
would need far more bits.

A discrete transfer function is held as a ``Zpk`` in ``z`` (gain, zeros,
poles); ``delay`` counts extra samples of z^-1 so ``num`` / ``den`` in
powers of z^-1 are always causal.
"""

from __future__ import annotations

import cmath
import math
from dataclasses import dataclass, field

from .lti import ROOT_ABS, Zpk, from_roots, pmul, real_poly, roots, same_root

METHODS = ("tustin", "zoh", "backward", "forward")
SECTION_HEADROOM = 29     # bits: |coefficient| < 2^29 / 2^q keeps five int64 products clear


class DiscreteError(ValueError):
    pass


@dataclass
class Discrete:
    """H(z) = k z^-delay prod(1 - z_i z^-1) / prod(1 - p_i z^-1)."""
    k: float
    zeros: list[complex]
    poles: list[complex]
    ts: float
    method: str = "tustin"
    prewarp: float | None = None          # Hz, Tustin only
    delay: int = 0

    def num(self) -> list[float]:
        """Numerator coefficients in powers of z^-1, length len(den())."""
        b = [0.0] * self.delay + real_poly([self.k * c for c in from_roots(self.zeros)])
        n = len(self.den())
        return (b + [0.0] * n)[:max(n, len(b))]

    def den(self) -> list[float]:
        return real_poly(from_roots(self.poles))

    def at(self, f: float) -> complex:
        zinv = cmath.exp(-2j * math.pi * f * self.ts)
        b, a = self.num(), self.den()
        return (sum(c * zinv ** i for i, c in enumerate(b))
                / sum(c * zinv ** i for i, c in enumerate(a)))


def discretize(h: Zpk, ts: float, method: str = "tustin",
               prewarp: float | None = None) -> Discrete:
    if ts <= 0:
        raise DiscreteError("sample period must be positive")
    if method not in METHODS:
        raise DiscreteError(f"method must be one of {list(METHODS)}, not {method!r}")
    if len(h.zeros) > len(h.poles):
        raise DiscreteError("the transfer function is improper (more zeros than poles); "
                            "add a filter pole, e.g. pid.fd")
    if h.is_zero():
        return Discrete(0.0, [], [], ts, method)
    if method == "zoh":
        return _zoh(h, ts)
    if method == "tustin":
        if prewarp:
            w = 2 * math.pi * prewarp
            if w * ts / 2 >= math.pi / 2:
                raise DiscreteError(f"prewarp frequency {prewarp:g} Hz is not below fs/2")
            c = w / math.tan(w * ts / 2)
        else:
            c = 2 / ts
        # s = c (z - 1)/(z + 1):  s - r = (c - r)(z - (c + r)/(c - r)) / (z + 1)
        k = h.k
        zs, ps = [], []
        for r in h.zeros:
            k *= c - r
            zs.append((c + r) / (c - r))
        for r in h.poles:
            k /= c - r
            ps.append((c + r) / (c - r))
        zs += [-1 + 0j] * (len(h.poles) - len(h.zeros))
        return Discrete(_real(k), zs, ps, ts, method, prewarp if prewarp else None)
    if method == "backward":
        # s = (z - 1)/(T z):  s - r = ((1 - rT) z - 1) / (T z)
        k = h.k
        zs, ps = [], []
        for r in h.zeros:
            k *= (1 - r * ts) / ts
            zs.append(1 / (1 - r * ts))
        for r in h.poles:
            k /= (1 - r * ts) / ts
            ps.append(1 / (1 - r * ts))
        zs += [0j] * (len(h.poles) - len(h.zeros))
        return Discrete(_real(k), zs, ps, ts, method)
    # forward Euler: s = (z - 1)/T
    k = h.k * ts ** (len(h.poles) - len(h.zeros))
    zs = [1 + r * ts for r in h.zeros]
    ps = [1 + r * ts for r in h.poles]
    unstable = [p for p in ps if abs(p) >= 1 and abs(p - 1) > 1e-12]
    if unstable:
        raise DiscreteError("forward Euler maps a pole outside the unit circle at this sample "
                            "rate; use tustin or backward")
    return Discrete(_real(k), zs, ps, ts, method, delay=len(h.poles) - len(h.zeros))


def _real(k: complex) -> float:
    return k.real if isinstance(k, complex) else float(k)


def _zoh(h: Zpk, ts: float) -> Discrete:
    """(1 - z^-1) Z{H(s)/s} by partial fractions; needs distinct poles."""
    poles = list(h.poles) + [0j]
    for i, p in enumerate(poles):
        if any(same_root(p, q) for q in poles[i + 1:]):
            raise DiscreteError("zoh needs distinct poles (an integrator or a repeated pole "
                                "is not supported); use tustin")
    num = [h.k * c for c in from_roots(h.zeros)]
    terms = []
    for i, p in enumerate(poles):
        rest = [q for j, q in enumerate(poles) if j != i]
        r = sum(c * p ** (len(num) - 1 - n) for n, c in enumerate(num))
        for q in rest:
            r /= p - q
        terms.append((r, cmath.exp(p * ts)))
    # common denominator prod(1 - e^{p T} z^-1), in powers of z^-1
    den = [1 + 0j]
    for _, e in terms:
        den = pmul(den, [1, -e])
    b = [0j] * len(den)
    for i, (r, _) in enumerate(terms):
        part = [r]
        for j, (_, e) in enumerate(terms):
            if j != i:
                part = pmul(part, [1, -e])
        for n, c in enumerate(part):
            b[n] += c
    # (1 - z^-1) cancels the factor of the pole at s = 0 (e^0 = 1)
    b = pmul(b, [1, -1])
    # back to z: b and den are polynomials in z^-1 of degrees nb, nd
    bz = _trim_tail(b)
    lead = next((i for i, c in enumerate(bz) if abs(c) > 1e-15 * max(abs(x) for x in bz)), 0)
    bz = bz[lead:]
    k = bz[0].real if bz else 0.0
    zs = roots(bz) if len(bz) > 1 else []
    ps = roots(den)
    d = Discrete(k, zs, ps, ts, "zoh", delay=lead)
    return _cancel(d)


def _trim_tail(p: list[complex]) -> list[complex]:
    scale = max((abs(c) for c in p), default=0) or 1.0
    while len(p) > 1 and abs(p[-1]) <= 1e-13 * scale:
        p = p[:-1]
    return p


def _cancel(d: Discrete) -> Discrete:
    z = Zpk(1.0, list(d.zeros), list(d.poles)).cancel()
    return Discrete(d.k, z.zeros, z.poles, d.ts, d.method, d.prewarp, d.delay)


# ---- second-order sections -----------------------------------------------------
@dataclass
class Section:
    b: list[float]                 # b0, b1, b2
    a: list[float]                 # 1, a1, a2

    def coeffs(self) -> list[float]:
        return [*self.b, self.a[1], self.a[2]]


@dataclass
class Sos:
    gain: float                    # only for a section-free transfer function
    sections: list[Section] = field(default_factory=list)


def _groups(rts: list[complex]) -> list[list[complex]]:
    """Conjugate pairs together, real roots alone."""
    left = list(rts)
    out = []
    while left:
        r = left.pop(0)
        if abs(r.imag) > 1e-9 * max(abs(r), ROOT_ABS):
            j = min(range(len(left)), key=lambda i: abs(left[i] - r.conjugate()))
            out.append([r, left.pop(j)])
        else:
            out.append([complex(r.real, 0)])
    return out


def sections(d: Discrete) -> Sos:
    """Split H(z) into sections; the gain goes in the first section, and the
    section holding a pole at z = 1 (an integrator) runs last so its output
    limit also stops the integrator winding up."""
    if not d.poles:
        if d.zeros or d.delay:
            raise DiscreteError("an FIR controller is not supported; give it a pole")
        return Sos(d.k)
    pgroups = _groups(d.poles)
    # pair real poles two to a section, largest magnitude first
    reals = sorted([g[0] for g in pgroups if len(g) == 1], key=lambda p: -abs(p))
    plist = [g for g in pgroups if len(g) == 2] + \
        [reals[i:i + 2] for i in range(0, len(reals), 2)]
    zgroups = _groups(d.zeros)
    zreal = [g[0] for g in zgroups if len(g) == 1]
    zpairs = [g for g in zgroups if len(g) == 2]
    delays = d.delay
    secs = []
    for ps in plist:
        need = len(ps)
        zs: list[complex] = []
        if need == 2 and zpairs:
            # the conjugate zero pair closest to these poles
            j = min(range(len(zpairs)), key=lambda i: abs(zpairs[i][0] - ps[0]))
            zs = zpairs.pop(j)
        while len(zs) < need and zreal:
            j = min(range(len(zreal)), key=lambda i: abs(zreal[i] - ps[0]))
            zs.append(zreal.pop(j))
        num = [1 + 0j]
        for z in zs:
            num = pmul(num, [1, -z])
        while len(num) - 1 < need and delays:
            num = pmul(num, [0, 1])
            delays -= 1
        den = from_roots(ps)
        b = real_poly(num) + [0.0] * (3 - len(num))
        a = real_poly(den) + [0.0] * (3 - len(den))
        secs.append(Section(b[:3], a[:3]))
    if zpairs or zreal or delays:
        raise DiscreteError("could not pair the zeros with the poles")
    integ = [i for i, s in enumerate(secs) if abs(sum(s.a)) < 1e-9]
    if integ:
        secs.append(secs.pop(integ[0]))
    secs[0].b = [c * d.k for c in secs[0].b]
    return Sos(1.0, secs)


def sos_at(sos: Sos, f: float, ts: float) -> complex:
    zinv = cmath.exp(-2j * math.pi * f * ts)
    v = complex(sos.gain)
    for s in sos.sections:
        v *= (s.b[0] + s.b[1] * zinv + s.b[2] * zinv ** 2) / (1 + s.a[1] * zinv + s.a[2] * zinv ** 2)
    return v


def coef_q(values: list[float]) -> int:
    """Fraction bits for a section's coefficients: as many as fit with headroom."""
    big = max((abs(v) for v in values), default=0.0)
    q = SECTION_HEADROOM
    if big > 0:
        q = min(q, SECTION_HEADROOM - max(0, math.ceil(math.log2(big + 1e-300) + 1e-12)))
    if q < 4:
        raise DiscreteError(f"a coefficient of {big:g} leaves fewer than 4 fraction bits; "
                            f"rescale the signals")
    return q


def quantize(sos: Sos) -> tuple[list[tuple[list[int], int]], Sos]:
    """Integer coefficients and Q per section, and the transfer function they realise."""
    out, real = [], []
    for s in sos.sections:
        c = s.coeffs()
        q = coef_q(c)
        ints = [int(round(v * (1 << q))) for v in c]
        out.append((ints, q))
        f = [i / (1 << q) for i in ints]
        real.append(Section(f[:3], [1.0, f[3], f[4]]))
    return out, Sos(sos.gain, real)


def quantization_error(exact: Sos, quant: Sos, ts: float, f_lo: float) -> tuple[float, float]:
    """Largest magnitude (dB) and phase (deg) difference from f_lo to fs/2."""
    f_hi = 0.45 / ts
    worst_db = worst_deg = 0.0
    n = 200
    for i in range(n + 1):
        f = f_lo * (f_hi / f_lo) ** (i / n)
        a, b = sos_at(exact, f, ts), sos_at(quant, f, ts)
        if a == 0 or b == 0:
            continue
        worst_db = max(worst_db, abs(20 * math.log10(abs(b) / abs(a))))
        worst_deg = max(worst_deg, abs(math.degrees(cmath.phase(b / a))))
    return worst_db, worst_deg
