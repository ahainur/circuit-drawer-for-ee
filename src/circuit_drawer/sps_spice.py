"""Experimental SPS ngspice scheduler for the constrained continuous backend.

Shares Timing and the pure transition oracle with the native backend.  Unlike
DC alter at a stopped time, a one-tick-ahead PWL window never postpones turn-off
into the next solver step.  The finite numerical edge widens, not shortens,
dead time.  Digital/mixed-rate and native-software acceptance are separate requirements.
"""
from __future__ import annotations
import math
import re
from pathlib import Path

from .sps import Timing, step

MAX_TICKS = 20000  # Bound registered ngspice stop conditions and trace memory.
EDGE_FRACTION = .001  # Numerical transition, not a hardware gate rise/fall model.
MIN_BREAK_FRACTION = 1e-9  # Keep adjacent numerical edge breakpoints distinct.
_LANES = ('phi', 'plo', 'shi', 'slo', 'plo', 'phi', 'slo', 'shi')


def _number(x):
    return format(x, '.17g')


def _name(x):
    if not isinstance(x, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', x):
        raise ValueError('SPS SPICE identifiers must be simple ASCII names')
    return x


def tick_count(t: Timing, stop: float):
    if isinstance(stop, bool) or not isinstance(stop, (float, int)) or not math.isfinite(stop):
        raise ValueError('SPS stop must be finite')
    ticks = stop / t.ts
    if ticks < 3*t.samples_per_cycle or ticks > MAX_TICKS:
        raise ValueError(f'SPS experimental run needs 3 periods..{MAX_TICKS} ticks')
    return math.ceil(ticks-1e-10)-1


def sources(gates):
    """Eight ordered source/node names, matching the native eight output ports."""
    gates = tuple(map(_name, gates))
    if len(gates) != 8 or len(set(gates)) != 8:
        raise ValueError('SPS requires eight distinct gate nodes')
    return [f'Vsps_{g} {g} 0 PWL(0 0 1 0)' for g in gates]


def commands(t: Timing, phase_node: str, gates, stop: float):
    """Control commands for ONE continuous-controller SPS group.

    This intentionally is not a generic digital-controller/mixed-rate adapter.
    The returned script writes both command-state and adaptive gate evidence.
    No simulation result should be accepted without inspect().
    """
    gates = tuple(gates)
    sources(gates)  # validate before generating commands
    phase_node = _name(phase_node)
    count = tick_count(t, stop)
    n, dead = t.samples_per_cycle, t.dead_ticks
    ts, edge = _number(t.ts), _number(t.ts*EDGE_FRACTION)
    c = lambda k: 'const.cd_sps_'+k
    init = ['setplot const']
    def declare(k, expression='0'):
        # Initial qualified declarations create literal dotted names in 44.2.
        init.append(f'let cd_sps_{k} = {expression}')
    for k,v in t.states().items(): declare(k, _number(v))
    for k in ('i','index','command','q','p','s','phi','plo','shi','slo',
              'pt','st','peh','seh','phin','plon','shin','slon',
              *(f'old{i}' for i in range(8))): declare(k)
    declare('pwl','vector(12)')
    columns = ('time', 'command', *(f'g{i}' for i in range(8)))
    for k in columns: declare('trace_'+k, f'vector({count})')
    body=[]
    def let(k,expr): body.append(f'let {c(k)} = {expr}')
    let('tick',c('i'));let('index',c('i')+'-1')
    let('command',f'v({phase_node})[length(time)-1]')
    body.append(f'if ({c("tick")}-floor({c("tick")}/{n})*{n} eq 0)')
    let('rise',c('pendingrise'));let('fall',c('pendingfall'))
    let('q',f'min({_number(t.phase_max)},max({_number(t.phase_min)},{c("command")}))*{n}/{_number(2*math.pi)}')
    body.append(f'if {c("q")} ge 0');let('q',f'floor({c("q")}+0.5)')
    body.append('else');let('q',f'ceil({c("q")}-0.5)');body.append('end')
    let('offset',f'min({c("offset")}+{n//4},max({c("offset")}-{n//4},{c("q")}))')
    let('pendingrise',f'{c("tick")}+{n}+{c("offset")}')
    let('pendingfall',f'{c("pendingrise")}+{n//2}');body.append('end')
    def raw(k, at):
        if k=='p':return f'({at}-floor({at}/{n})*{n} lt {n//2})'
        scheduled = f'(({at} ge {c("rise")}) & ({at} lt {c("fall")})) | (({at} ge {c("pendingrise")}) & ({at} lt {c("pendingfall")}))'
        # Apply the startup window to both the current and look-ahead PWL tick.
        return f'(({at} lt {2*n}) & {raw("p", at)}) | (({at} ge {2*n}) & ({scheduled}))'
    for b in ('p','s'):
        let(b,raw(b,c('tick')));let(b+'hi','0');let(b+'lo','0')
        body.append(f'if {c("tick")} ge {n}')
        body.append(f'if {c(b)} ne {c(b+"raw")}');let(b+'edge',c('tick'));body.append('end')
        ready=f'(({c("tick")}-{c(b+"edge")}) ge {dead})'
        let(b+'hi',ready+' & '+c(b));let(b+'lo',ready+f' & ({c(b)} eq 0)')
        let(b+'raw',c(b));body.append('end')
    # At a period boundary the command only changes pulses >= .75 period
    # ahead. Thus immediate next-tick output is independent of the future input.
    future=f'({c("tick")}+1)'
    for b in ('p','s'):
        let(b+'t',raw(b,future));let(b+'eh',c(b+'edge'))
        body.append(f'if {c(b+"t")} ne {c(b+"raw")}');let(b+'eh',future);body.append('end')
        ready=f'(({future}-{c(b+"eh")}) ge {dead}) & ({future} ge {n})'
        let(b+'hin',ready+' & '+c(b+'t'));let(b+'lon',ready+f' & ({c(b+"t")} eq 0)')
    for i,(lane,g) in enumerate(zip(_LANES,gates)):
        v,vnext,old=c(lane),c(lane+'n'),c('old'+str(i))
        pairs=[('0',f'min({old},{v})'),(f'{c("tick")}*{ts}',f'min({old},{v})'),
               (f'{c("tick")}*{ts}+{edge}',v),(f'{future}*{ts}-{edge}',v),
               (f'{future}*{ts}',f'min({v},{vnext})'),(f'{future}*{ts}+{edge}',vnext)]
        for j,(time,value) in enumerate(pairs):
            body += [f'let {c("pwl")}[{2*j}] = {time}',f'let {c("pwl")}[{2*j+1}] = {value}']
        body.append(f'alter @Vsps_{g}[pwl] = {c("pwl")}');let('old'+str(i),v)
    values=['time[length(time)-1]',c('command'),*(c(k) for k in _LANES)]
    for col,value in zip(columns,values):body.append(f'let {c("trace_"+col)}[{c("index")}] = {value}')
    # Explicit right-side sampling: a step at the boundary must not wait a
    # whole extra cycle. A nominally exact stop can land one ulp before its
    # literal in ngspice. A positive numerical guard resolves that ambiguity;
    # four minbreak units prevent merging with an existing source breakpoint;
    # it is far smaller than the PWL edge and the unchanged trace tolerance.
    # Period boundaries use cycle/f, not an accumulated rounded Ts.
    stops=[]
    for i in range(1,count+1):
        nominal=(i//n)/t.frequency+(i%n)*t.ts
        guard=max(4*t.ts*MIN_BREAK_FRACTION,16*math.ulp(nominal))
        stops.append(f'stop when time ge {_number(nominal+guard)}')
    return ['set numdgt=16',f'option minbreak={_number(t.ts*MIN_BREAK_FRACTION)}',*init,*stops,'run','set cd_sps_plot = $curplot',
            f'let {c("i")} = 1',f'while {c("i")} le {count}',f'delete $&{c("i")}',
            *body,'resume',f'let {c("i")} = {c("i")}+1','end',
            'setplot const','setscale cd_sps_trace_time','set wr_singlescale','set wr_vecnames',
            'wrdata sps_samples.dat '+' '.join('cd_sps_trace_'+k for k in columns[1:]),
            'setplot $cd_sps_plot','wrdata sps_gates.dat '+' '.join(f'v({g})' for g in gates),
            'echo CD_SPS_COMPLETE']


def inspect(t: Timing, stop: float, stage: Path):
    """Reject missing/nonfinite/truncated evidence or incorrect gate transitions."""
    def read(name):
        lines=(stage/name).read_text(encoding='ascii').splitlines()
        rows=[[float(x) for x in line.split()] for line in lines[1:] if line.strip()]
        if not rows or any(len(r)!=10 or not all(map(math.isfinite,r)) for r in rows):
            raise ValueError('SPS command trace malformed or nonfinite')
        return lines[0].split(),rows
    names,rows=read('sps_samples.dat')
    if names != ['cd_sps_trace_time','cd_sps_trace_command',*(f'cd_sps_trace_g{i}' for i in range(8))] or len(rows)!=tick_count(t,stop):
        raise ValueError('SPS command trace incomplete or wrong columns')
    state=t.states();_,_,state=step(t,state,0.)
    for i,row in enumerate(rows,1):
        if not math.isclose(row[0],i*t.ts,rel_tol=0,abs_tol=t.ts*1e-7):
            raise ValueError('SPS tick timestamp mismatch')
        if i % t.samples_per_cycle == 0 and row[0] < (i//t.samples_per_cycle)/t.frequency:
            raise ValueError('SPS boundary sampled before the period edge')
        high,low,state=step(t,state,row[1])
        if tuple(row[2:])!=high+low: raise ValueError('SPS gate trace disagrees with timing oracle')
    # Electrical gate evidence is separate: a correct command trace alone is
    # insufficient (DC alter can delay turn-off and shorten physical dead time).
    lines=(stage/'sps_gates.dat').read_text(encoding='ascii').splitlines()
    gates=[[float(x) for x in line.split()] for line in lines[1:] if line.strip()]
    if not gates or any(len(r)!=9 or not all(map(math.isfinite,r)) for r in gates):
        raise ValueError('SPS electrical gate trace malformed')
    if gates[0][0]>t.ts or abs(gates[-1][0]-stop)>t.ts*1e-7:
        raise ValueError('SPS electrical gate trace incomplete')
    if any(b[0]<=a[0] for a,b in zip(gates,gates[1:])):
        raise ValueError('SPS electrical time must increase')
    # Away from the intentional finite-edge window, actual voltages must match
    # the oracle, not merely avoid overlap (an all-off run is not acceptance).
    for row in gates:
        tick=math.floor(row[0]/t.ts)
        distance=abs(row[0]/t.ts-round(row[0]/t.ts))
        if distance <= 2*EDGE_FRACTION or tick>=len(rows)+1: continue
        expected=(0,)*8 if tick==0 else tuple(rows[tick-1][2:])
        if any(abs(value-target)>1e-6 for value,target in zip(row[1:],expected)):
            raise ValueError('SPS electrical gates disagree with scheduled levels')
    minimum=[]
    for h,l in zip(range(1,5),range(5,9)):
        if any(r[h]>.5 and r[l]>.5 for r in gates):raise ValueError('SPS shoot-through detected')
        off={h:None,l:None};dead=[]
        for a,b in zip(gates,gates[1:]):
            for high,low in ((h,l),(l,h)):
                if a[high]>.5 and b[high]<=.5:off[high]=b[0]
                if a[low]<=.5 and b[low]>.5 and off[high] is not None:
                    # Crossing times are only bracketed by solver samples.
                    # Off may occur as late as b_off; on as early as a_on.
                    # Never use b_on: doing so can overstate physical dead time.
                    dead.append(a[0]-off[high])
        if not dead or min(dead)<t.dead_ticks*t.ts-t.ts*1e-7:
            raise ValueError('SPS electrical dead time lower bound is too short or unresolved')
        minimum.append(min(dead))
    boundary_offsets=[row[0]-(i//t.samples_per_cycle)/t.frequency
                      for i,row in enumerate(rows,1) if i % t.samples_per_cycle == 0]
    return dict(ticks=len(rows),solver_points=len(gates),minimum_dead_time_s=minimum,
                numerical_edge_s=t.ts*EDGE_FRACTION,
                dead_time_measurement='conservative_sample_bracket_lower_bound',
                boundary_sampling='right_side_with_positive_numerical_guard',
                maximum_requested_sample_guard_s=max(4*t.ts*MIN_BREAK_FRACTION,16*math.ulp(stop)),
                measured_boundary_sample_offset_s=[min(boundary_offsets),max(boundary_offsets)],
                sample_timestamp_tolerance_s=t.ts*1e-7,
                backend='ngspice-experimental')
