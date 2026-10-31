"""cg_lib.h: the small runtime the generated controllers call.

Header-only (static inline), C99, no libm in the fixed-point part.  Fixed
point keeps signals in int32 with the caller's Q, coefficients in int32 with
their own Q, and products in int64; results round to nearest and saturate.
Float (suffix f) and double (suffix d) versions do the same in floating point.

Besides what the code generator emits, the library carries the transforms a
motor or grid controller needs around its PI / PR blocks: Clarke, Park,
inverse Park and SVPWM (min-max zero-sequence injection, the same duty
cycles as seven-segment space-vector modulation).
"""

_FLOAT = r"""
/* ---- {T} ---------------------------------------------------------------- */
static inline {T} cg_clamp{S}({T} x, {T} lo, {T} hi) { return x < lo ? lo : x > hi ? hi : x; }
static inline {T} cg_mul{S}({T} a, {T} b) { return a * b; }
static inline {T} cg_div{S}({T} a, {T} b) { return b != 0 ? a / b : (a >= 0 ? CG_INF : -CG_INF); }

typedef struct { {T} i, e1; } cg_pi{S}_t;
/* u = kp e + i,  i += c0 e[k] + c1 e[k-1], i and u held inside [lo, hi] */
static inline {T} cg_pi{S}(cg_pi{S}_t *s, {T} kp, {T} c0, {T} c1, {T} e, {T} lo, {T} hi)
{
    s->i = cg_clamp{S}(s->i + c0 * e + c1 * s->e1, lo, hi);
    s->e1 = e;
    return cg_clamp{S}(kp * e + s->i, lo, hi);
}

typedef struct { {T} x1, x2, y1, y2; } cg_sos{S}_t;
/* second-order sections in direct form I; the last output is held in [lo, hi] */
static inline {T} cg_sos_chain{S}(cg_sos{S}_t *s, const {T} c[][5], int n, {T} x, {T} lo, {T} hi)
{
    for (int k = 0; k < n; k++) {
        {T} y = c[k][0] * x + c[k][1] * s[k].x1 + c[k][2] * s[k].x2
                - c[k][3] * s[k].y1 - c[k][4] * s[k].y2;
        if (k == n - 1) y = cg_clamp{S}(y, lo, hi);
        s[k].x2 = s[k].x1; s[k].x1 = x; s[k].y2 = s[k].y1; s[k].y1 = y;
        x = y;
    }
    return x;
}

static inline void cg_clarke{S}({T} ia, {T} ib, {T} *alpha, {T} *beta)
{
    *alpha = ia;
    *beta = (ia + 2 * ib) * ({T})CG_INV_SQRT3;
}

static inline void cg_park{S}({T} alpha, {T} beta, {T} sin_t, {T} cos_t, {T} *d, {T} *q)
{
    *d = alpha * cos_t + beta * sin_t;
    *q = -alpha * sin_t + beta * cos_t;
}

static inline void cg_ipark{S}({T} d, {T} q, {T} sin_t, {T} cos_t, {T} *alpha, {T} *beta)
{
    *alpha = d * cos_t - q * sin_t;
    *beta = d * sin_t + q * cos_t;
}

/* duty cycles 0..1 of phases a, b, c for the voltage vector (alpha, beta) */
static inline void cg_svpwm{S}({T} alpha, {T} beta, {T} vdc, {T} *da, {T} *db, {T} *dc)
{
    {T} va = alpha, vb = -alpha / 2 + ({T})CG_SQRT3_2 * beta, vc = -alpha / 2 - ({T})CG_SQRT3_2 * beta;
    {T} mx = va > vb ? (va > vc ? va : vc) : (vb > vc ? vb : vc);
    {T} mn = va < vb ? (va < vc ? va : vc) : (vb < vc ? vb : vc);
    {T} off = -(mx + mn) / 2;
    *da = cg_clamp{S}(({T})0.5 + (va + off) / vdc, 0, 1);
    *db = cg_clamp{S}(({T})0.5 + (vb + off) / vdc, 0, 1);
    *dc = cg_clamp{S}(({T})0.5 + (vc + off) / vdc, 0, 1);
}
"""

_FIXED = r"""
/* ---- fixed point: int32 signals, int64 products ---------------------------- */
/* real -> Q n, rounded to nearest (constants only: evaluated by the compiler) */
#define CG_QN(x, n) ((int32_t)((x) * (double)((int64_t)1 << (n)) + ((x) >= 0 ? 0.5 : -0.5)))

static inline int32_t cg_sat32(int64_t v)
{
    return v > INT32_MAX ? INT32_MAX : v < INT32_MIN ? INT32_MIN : (int32_t)v;
}
/* arithmetic shift right with rounding to nearest */
static inline int64_t cg_rshift(int64_t v, int n) { return n > 0 ? (v + ((int64_t)1 << (n - 1))) >> n : v; }
static inline int32_t cg_clamp(int32_t x, int32_t lo, int32_t hi) { return x < lo ? lo : x > hi ? hi : x; }
/* x * k with k in Q qk */
static inline int32_t cg_gain(int32_t x, int32_t k, int qk) { return cg_sat32(cg_rshift((int64_t)x * k, qk)); }
/* a * b and a / b, all in Q q */
static inline int32_t cg_mul(int32_t a, int32_t b, int q) { return cg_sat32(cg_rshift((int64_t)a * b, q)); }
static inline int32_t cg_div(int32_t a, int32_t b, int q)
{
    if (b == 0) return a >= 0 ? INT32_MAX : INT32_MIN;
    return cg_sat32(((int64_t)a * ((int64_t)1 << q)) / b);
}

/* PI with the integrator kept in Q(q + qk) in 64 bits, so a small ki Ts
 * still integrates; kp, c0, c1 share the Q qk. */
typedef struct { int64_t i; int32_t e1; } cg_pi_t;
static inline int32_t cg_pi(cg_pi_t *s, int32_t kp, int32_t c0, int32_t c1, int qk, int32_t e,
                            int32_t lo, int32_t hi)
{
    int64_t lo_i = (int64_t)lo * ((int64_t)1 << qk), hi_i = (int64_t)hi * ((int64_t)1 << qk);
    int64_t i = s->i + (int64_t)c0 * e + (int64_t)c1 * s->e1;
    s->i = i < lo_i ? lo_i : i > hi_i ? hi_i : i;
    s->e1 = e;
    return cg_clamp(cg_sat32(cg_rshift((int64_t)kp * e + s->i, qk)), lo, hi);
}

/* second-order sections, direct form I, section k's coefficients in Q qc[k];
 * every coefficient is below 2^29 / 2^qc[k], so the five int64 products
 * cannot overflow */
typedef struct { int32_t x1, x2, y1, y2; } cg_sos_t;
static inline int32_t cg_sos_chain(cg_sos_t *s, const int32_t c[][5], const int qc[], int n,
                                   int32_t x, int32_t lo, int32_t hi)
{
    for (int k = 0; k < n; k++) {
        int64_t acc = (int64_t)c[k][0] * x + (int64_t)c[k][1] * s[k].x1
                    + (int64_t)c[k][2] * s[k].x2 - (int64_t)c[k][3] * s[k].y1
                    - (int64_t)c[k][4] * s[k].y2;
        int32_t y = cg_sat32(cg_rshift(acc, qc[k]));
        if (k == n - 1) y = cg_clamp(y, lo, hi);
        s[k].x2 = s[k].x1; s[k].x1 = x; s[k].y2 = s[k].y1; s[k].y1 = y;
        x = y;
    }
    return x;
}

/* transforms in Q q; sin_t, cos_t in the same Q */
static inline void cg_clarke(int32_t ia, int32_t ib, int32_t *alpha, int32_t *beta)
{
    *alpha = ia;
    *beta = cg_sat32(cg_rshift(((int64_t)ia + 2 * (int64_t)ib) * CG_QN(CG_INV_SQRT3, 30), 30));
}
static inline void cg_park(int32_t alpha, int32_t beta, int32_t sin_t, int32_t cos_t, int q,
                           int32_t *d, int32_t *qo)
{
    *d = cg_sat32(cg_rshift((int64_t)alpha * cos_t + (int64_t)beta * sin_t, q));
    *qo = cg_sat32(cg_rshift(-(int64_t)alpha * sin_t + (int64_t)beta * cos_t, q));
}
static inline void cg_ipark(int32_t d, int32_t qi, int32_t sin_t, int32_t cos_t, int q,
                            int32_t *alpha, int32_t *beta)
{
    *alpha = cg_sat32(cg_rshift((int64_t)d * cos_t - (int64_t)qi * sin_t, q));
    *beta = cg_sat32(cg_rshift((int64_t)d * sin_t + (int64_t)qi * cos_t, q));
}
static inline void cg_svpwm(int32_t alpha, int32_t beta, int32_t vdc, int q,
                            int32_t *da, int32_t *db, int32_t *dc)
{
    int32_t half = (int32_t)1 << (q - 1), one = (int32_t)1 << q;
    int32_t bb = cg_sat32(cg_rshift((int64_t)beta * CG_QN(CG_SQRT3_2, 30), 30));
    int32_t va = alpha, vb = -alpha / 2 + bb, vc = -alpha / 2 - bb;
    int32_t mx = va > vb ? (va > vc ? va : vc) : (vb > vc ? vb : vc);
    int32_t mn = va < vb ? (va < vc ? va : vc) : (vb < vc ? vb : vc);
    int32_t off = -((mx + mn) / 2);
    *da = cg_clamp(half + cg_div(va + off, vdc, q), 0, one);
    *db = cg_clamp(half + cg_div(vb + off, vdc, q), 0, one);
    *dc = cg_clamp(half + cg_div(vc + off, vdc, q), 0, one);
}
"""

LIB = ("/* cg_lib.h: runtime for controllers generated by circuit-drawer-control.\n"
       " * Header-only, C99.  Generated file: do not edit. */\n"
       "#ifndef CG_LIB_H\n#define CG_LIB_H\n\n#include <stdint.h>\n\n"
       "#define CG_INF (1.0e30)\n"
       "#define CG_INV_SQRT3 0.57735026918962576451\n"
       "#define CG_SQRT3_2 0.86602540378443864676\n"
       + _FIXED
       + _FLOAT.replace("{T}", "float").replace("{S}", "f")
       + _FLOAT.replace("{T}", "double").replace("{S}", "d")
       + "\n#endif /* CG_LIB_H */\n")
