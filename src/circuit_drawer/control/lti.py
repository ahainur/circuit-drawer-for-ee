"""Small linear-systems toolkit for loop analysis, in plain Python.

Polynomials are lists of coefficients in descending powers of ``s`` (as a
user writes ``num``/``den``).  A transfer function is held as zeros, poles
and a gain (``Zpk``): products then never grow the degree, and a sum keeps
only the poles the two terms do not share, so the eliminations in
``loop.py`` stay at the degree of the real system.
"""

from __future__ import annotations

import cmath
import math
from dataclasses import dataclass, field

# Two roots closer than this (relative to their size) are the same root.
# Simple roots come out of the Aberth iteration good to ~1e-10 relative, but
# a repeated root (a Type III double zero) spreads by ~1e-5, so the tolerance
# sits above that; a distinct pole and zero this close cancel anyway.
ROOT_TOL = 1e-4
ROOT_ABS = 1e-9        # rad/s: below this a root is taken as s = 0
ABERTH_ITERS = 500


# ---- polynomials -------------------------------------------------------------
def trim(p: list[complex]) -> list[complex]:
    """Drop leading zeros; the zero polynomial is [0]."""
    i = 0
    while i < len(p) - 1 and p[i] == 0:
        i += 1
    return list(p[i:]) or [0.0]


def pmul(a: list, b: list) -> list:
    out = [0j] * (len(a) + len(b) - 1)
    for i, x in enumerate(a):
        for j, y in enumerate(b):
            out[i + j] += x * y
    return out


def padd(a: list, b: list) -> list:
    n = max(len(a), len(b))
    a = [0] * (n - len(a)) + list(a)
    b = [0] * (n - len(b)) + list(b)
    return [x + y for x, y in zip(a, b)]


def peval(p: list, s: complex) -> complex:
    acc = 0j
    for c in p:
        acc = acc * s + c
    return acc


def from_roots(roots: list[complex]) -> list[complex]:
    p: list[complex] = [1 + 0j]
    for r in roots:
        p = pmul(p, [1, -r])
    return p


def real_poly(p: list[complex]) -> list[float]:
    return [c.real if isinstance(c, complex) else float(c) for c in p]


def roots(p: list) -> list[complex]:
    """All roots of a polynomial (Aberth-Ehrlich, on a rescaled variable)."""
    p = [complex(c) for c in trim(list(p))]
    if len(p) <= 1:
        return []
    out: list[complex] = []
    while len(p) > 1 and p[-1] == 0:      # exact roots at s = 0
        out.append(0j)
        p.pop()
    n = len(p) - 1
    if n == 0:
        return out
    # rescale s = r t so the roots of the new polynomial are near the unit
    # circle; converter coefficients span 1e-12 .. 1e6 otherwise
    r = abs(p[-1] / p[0]) ** (1 / n) or 1.0
    q = [c / p[0] / r ** k for k, c in enumerate(p)]
    dq = [c * (n - k) for k, c in enumerate(q[:-1])]
    z = [cmath.exp(2j * math.pi * (k + 0.25) / n) * 1.1 for k in range(n)]
    for _ in range(ABERTH_ITERS):
        worst = 0.0
        for i in range(n):
            f = peval(q, z[i])
            if f == 0:
                continue
            ratio = f / peval(dq, z[i]) if peval(dq, z[i]) != 0 else f
            rep = sum(1 / (z[i] - z[j]) for j in range(n) if j != i and z[i] != z[j])
            step = ratio / (1 - ratio * rep)
            z[i] -= step
            worst = max(worst, abs(step) / max(abs(z[i]), 1e-30))
        if worst < 1e-14:
            break
    res = []
    for t in z:
        s = t * r
        # a real coefficient polynomial: snap near-real roots onto the axis
        if abs(s.imag) <= 1e-9 * max(abs(s), ROOT_ABS):
            s = complex(s.real, 0)
        if abs(s) < ROOT_ABS:
            s = 0j
        res.append(s)
    return out + sorted(res, key=lambda c: (abs(c), c.imag))


def same_root(a: complex, b: complex, tolerance=ROOT_TOL) -> bool:
    return abs(a - b) <= tolerance * max(abs(a), abs(b)) + min(ROOT_ABS, tolerance)


def _take(pool: list[complex], r: complex, tolerance=ROOT_TOL) -> bool:
    for i, q in enumerate(pool):
        if same_root(q, r, tolerance):
            del pool[i]
            return True
    return False


# ---- transfer functions ------------------------------------------------------
@dataclass
class Zpk:
    """k * prod(s - z) / prod(s - p)."""
    k: float = 0.0
    zeros: list[complex] = field(default_factory=list)
    poles: list[complex] = field(default_factory=list)
    root_tol: float = ROOT_TOL

    @classmethod
    def const(cls, k: float) -> "Zpk":
        return cls(float(k))

    @classmethod
    def from_poly(cls, num: list, den: list, *, root_tol=ROOT_TOL) -> "Zpk":
        num, den = trim([complex(c) for c in num]), trim([complex(c) for c in den])
        if den == [0]:
            raise ZeroDivisionError("denominator is zero")
        if num == [0]:
            return cls(0.0)
        return cls((num[0] / den[0]).real, roots(num), roots(den), root_tol).cancel()

    def cancel(self) -> "Zpk":
        if self.k == 0:
            return Zpk(0.0)
        zs, ps = list(self.zeros), list(self.poles)
        keep = []
        for z in zs:
            if not _take(ps, z, self.root_tol):
                keep.append(z)
        return Zpk(self.k, keep, ps, self.root_tol)

    def num(self) -> list[float]:
        return real_poly([self.k * c for c in from_roots(self.zeros)])

    def den(self) -> list[float]:
        return real_poly(from_roots(self.poles))

    def __call__(self, s: complex) -> complex:
        v = complex(self.k)
        for z in self.zeros:
            v *= s - z
        for p in self.poles:
            v /= s - p
        return v

    def is_zero(self) -> bool:
        return self.k == 0

    def __mul__(self, o: "Zpk | float") -> "Zpk":
        o = _zpk(o)
        if self.is_zero() or o.is_zero():
            return Zpk(0.0)
        return Zpk(self.k * o.k, self.zeros + o.zeros, self.poles + o.poles,
                   min(self.root_tol, o.root_tol)).cancel()

    __rmul__ = __mul__

    def __neg__(self) -> "Zpk":
        return Zpk(-self.k, list(self.zeros), list(self.poles), self.root_tol)

    def inverse(self) -> "Zpk":
        if self.is_zero():
            raise ZeroDivisionError("inverse of zero")
        return Zpk(1 / self.k, list(self.poles), list(self.zeros), self.root_tol)

    def __truediv__(self, o: "Zpk | float") -> "Zpk":
        return self * _zpk(o).inverse()

    def __add__(self, o: "Zpk | float") -> "Zpk":
        o = _zpk(o)
        tolerance = min(self.root_tol, o.root_tol)
        if o.is_zero():
            return Zpk(self.k, list(self.zeros), list(self.poles), tolerance)
        if self.is_zero():
            return Zpk(o.k, list(o.zeros), list(o.poles), tolerance)
        # common denominator: the shared poles once, then each side's own
        mine, theirs, shared = list(self.poles), list(o.poles), []
        for p in list(mine):
            if _take(theirs, p, tolerance):
                mine.remove(p)
                shared.append(p)
        a = [self.k * c for c in pmul(from_roots(self.zeros), from_roots(theirs))]
        b = [o.k * c for c in pmul(from_roots(o.zeros), from_roots(mine))]
        num = padd(a, b)
        size = padd([abs(c) for c in a], [abs(c) for c in b])
        # a coefficient where the two terms cancel is round-off: drop it
        num = [0 if abs(c) <= 1e-12 * m else c for c, m in zip(num, size)]
        num = trim(num)
        if num == [0]:
            return Zpk(0.0)
        return Zpk(num[0].real, roots(num), shared + mine + theirs, tolerance).cancel()

    __radd__ = __add__

    def __sub__(self, o: "Zpk | float") -> "Zpk":
        return self + (-_zpk(o))

    def __rsub__(self, o: "Zpk | float") -> "Zpk":
        return _zpk(o) - self


def _zpk(v) -> Zpk:
    return v if isinstance(v, Zpk) else Zpk.const(v)


def pade(delay: float, order: int = 2) -> Zpk:
    """Rational stand-in for exp(-s T); the frequency response uses the exact delay."""
    if delay <= 0:
        return Zpk.const(1.0)
    t = delay
    if order == 1:
        return Zpk.from_poly([-t / 2, 1], [t / 2, 1])
    return Zpk.from_poly([t * t / 12, -t / 2, 1], [t * t / 12, t / 2, 1])


# ---- linear solve over any field (complex numbers or Zpk) ---------------------
def solve(a: list[list], b: list, zero, is_zero) -> list:
    """Gaussian elimination with the first non-zero pivot.  ``a`` and ``b``
    are copied.  Raises ZeroDivisionError for a singular system (an
    algebraic loop with unit gain)."""
    n = len(a)
    m = [list(row) + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = None
        best = -1.0
        for r in range(col, n):
            v = m[r][col]
            if is_zero(v):
                continue
            size = abs(v) if isinstance(v, complex) else 1.0
            if size > best:
                piv, best = r, size
            if not isinstance(v, complex):
                break
        if piv is None:
            raise ZeroDivisionError("singular system")
        m[col], m[piv] = m[piv], m[col]
        p = m[col][col]
        for r in range(n):
            if r == col or is_zero(m[r][col]):
                continue
            f = m[r][col] / p
            m[r] = [x - f * y if not is_zero(y) else x for x, y in zip(m[r], m[col])]
            m[r][col] = zero
    return [m[i][n] / m[i][i] for i in range(n)]
