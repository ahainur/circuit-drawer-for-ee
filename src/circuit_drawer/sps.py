"""Deterministic sampled SPS timing shared by native exporters and test oracle.

This is an ideal-edge *discrete* modulator, not an analogue PWM comparator.
The first period is blank. Commands (radians, positive = secondary lags) are
sampled on primary period boundaries and schedule the following cycle. A
quarter-period slew bound keeps consecutive secondary pulses separated even
for a full negative-to-positive command jump. Dead time delays turn-on only.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
import sys

FIELDS = ('frequency', 'phase_min', 'phase_max', 'samples_per_cycle')
DEFAULT_SAMPLES = 200  # resolution is explicit in exports; 50 kHz -> 100 ns
MIN_SAMPLES = 64
MAX_SAMPLES = 100000


@dataclass(frozen=True)
class Timing:
    frequency: float
    phase_min: float
    phase_max: float
    dead_time: float
    samples_per_cycle: int = DEFAULT_SAMPLES

    def __post_init__(self):
        vals = (self.frequency, self.phase_min, self.phase_max, self.dead_time)
        if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in vals):
            raise ValueError('SPS timing values must be finite numbers')
        if self.frequency <= 0 or not -math.pi/2 <= self.phase_min < self.phase_max <= math.pi/2:
            raise ValueError('SPS requires frequency > 0 and -pi/2 <= phase_min < phase_max <= pi/2 (radians)')
        n = self.samples_per_cycle
        if isinstance(n, bool) or not isinstance(n, int) or not MIN_SAMPLES <= n <= MAX_SAMPLES or n % 4:
            raise ValueError('SPS samples_per_cycle must be an integer multiple of 4 in 64..100000')
        if not math.isfinite(self.ts) or self.ts <= 0:
            raise ValueError('SPS tick duration must be finite and positive')
        ratio = self.dead_time / self.ts
        if self.dead_time <= 0 or not math.isfinite(ratio) or ratio >= n // 4 or self.dead_ticks >= n // 4:
            raise ValueError('SPS requires 0 < dead_time < quarter period after tick rounding')

    @property
    def ts(self):
        return 1 / (self.frequency * self.samples_per_cycle)

    @property
    def dead_ticks(self):
        # Round upward: quantization must never shorten requested dead time.
        return math.ceil(self.dead_time / self.ts)

    def offset(self, command):
        if not math.isfinite(command):
            raise ValueError('SPS phase command must be finite')
        phase = min(self.phase_max, max(self.phase_min, command))
        x = phase * self.samples_per_cycle / (2 * math.pi)
        return math.floor(x + .5) if x >= 0 else math.ceil(x - .5)

    def states(self):
        n = self.samples_per_cycle
        return dict(tick=0., offset=0., rise=-float(n), fall=-float(n),
                    pendingrise=float(n), pendingfall=1.5*n,
                    praw=-1., sraw=-1., pedge=0., sedge=0.)


def step(timing: Timing, state: dict, command: float):
    """Pure transition; repeated solver evaluations do not commit state.

    Returns four upper gates (primary left/right, secondary left/right),
    four corresponding lower gates, and next state. Adjacent entries are
    opposite bridge legs, not complementary devices of the same leg.
    """
    if not math.isfinite(command):
        raise ValueError('SPS phase command must be finite')
    s = dict(state)
    tick, n = int(s['tick']), timing.samples_per_cycle
    if tick and tick % n == 0:
        s['rise'], s['fall'] = s['pendingrise'], s['pendingfall']
        requested = timing.offset(command)
        offset = max(s['offset']-n//4, min(s['offset']+n//4, requested))
        s['offset'] = offset
        s['pendingrise'] = tick+n+offset
        s['pendingfall'] = tick+n+offset+n//2
    primary = int(tick % n < n//2)
    # A negative scheduled phase can lead into the preceding period. Keep
    # the entire first active period at zero phase, including its final quarter.
    secondary = primary if tick < 2*n else int(
        s['rise'] <= tick < s['fall'] or s['pendingrise'] <= tick < s['pendingfall'])
    upper, lower = [], []
    for bridge, raw in (('p', primary), ('s', secondary)):
        if tick < n:
            s[bridge+'raw'] = -1.
            hi = lo = 0
        else:
            if raw != s[bridge+'raw']:
                s[bridge+'edge'] = tick
            ready = tick-s[bridge+'edge'] >= timing.dead_ticks
            hi, lo = int(ready and raw == 1), int(ready and raw == 0)
            s[bridge+'raw'] = raw
        upper += [hi, lo]
        lower += [lo, hi]
    s['tick'] = tick+1
    return tuple(upper), tuple(lower), s


def c_spec(t: Timing):
    """Generated pure C99 transition consumed by existing native C exporters."""
    n, dead = t.samples_per_cycle, t.dead_ticks
    body = f'''
const double n = {n}, half = {n//2}, quarter = {n//4};
/* Simulink C Function supports these comparisons across its C runtimes. */
const double finite_limit = {sys.float_info.max!r};
double wanted, q, p, s, ready, ph=0, pl=0, sh=0, sl=0;
if (tick > 0 && fmod(tick, n) == 0) {{
    next_rise = pendingrise; next_fall = pendingfall;
    wanted = command > {t.phase_min!r} ? command : {t.phase_min!r};
    if (wanted > {t.phase_max!r}) wanted = {t.phase_max!r};
    q = wanted*n/{2*math.pi!r};
    q = q >= 0 ? floor(q+0.5) : ceil(q-0.5);
    next_offset = q;
    if (next_offset < offset-quarter) next_offset = offset-quarter;
    if (next_offset > offset+quarter) next_offset = offset+quarter;
    next_pendingrise = tick+n+next_offset;
    next_pendingfall = next_pendingrise+half;
}}
p = fmod(tick, n) < half;
s = tick < 2*n ? p : ((tick >= next_rise && tick < next_fall) ||
    (tick >= next_pendingrise && tick < next_pendingfall));
if (tick >= n && command >= -finite_limit && command <= finite_limit) {{
    if (p != praw) next_pedge = tick;
    if (s != sraw) next_sedge = tick;
    ready = tick-next_pedge >= {dead};
    ph = ready && p; pl = ready && !p;
    ready = tick-next_sedge >= {dead};
    sh = ready && s; sl = ready && !s;
    next_praw = p; next_sraw = s;
}} else {{ next_praw = -1; next_sraw = -1; }}
pLeft = ph; pRight = pl; sLeft = sh; sRight = sl;
pLeftLow = pl; pRightLow = ph; sLeftLow = sl; sRightLow = sh;
next_tick = tick+1;
'''
    return {'body': body, 'states': t.states()}

OUTPUTS = ('pLeft', 'pRight', 'sLeft', 'sRight', 'pLeftLow', 'pRightLow', 'sLeftLow', 'sRightLow')
