"""Validated electrical graph shared by the Simscape and PLECS exporters.

Only explicit simulation data is translated; labels are never executable code.
The source drawing and its coordinates are not modified.
"""
from __future__ import annotations

import json
import hashlib
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .layout import Layout
from .spice import SpiceError, make_deck, number, resolved_layout, OPTIONAL_FIELDS

# A tangent at this *declared, adjustable* operating current approximates the
# SPICE diode at 27 deg C. Both target libraries use a piecewise-linear diode.
DEFAULT_DIODE_CURRENT = 1.0
DEFAULT_NUMERICAL_RESISTANCE = 1e-3
THERMAL_VOLTAGE = 1.380649e-23 * 300.15 / 1.602176634e-19
# Bound generated time tables independently of the solver's integration limit.
MAX_WAVE_POINTS = 200_000
PIXELS_PER_UNIT = 80


@dataclass
class Part:
    name: str
    kind: str
    nodes: list[str]
    params: dict
    position: tuple[float, float]
    source: str
    role: str = 'component'


@dataclass
class Signal:
    name: str
    kind: str
    params: dict
    position: tuple[float, float]
    points: list[tuple[float, float]] = field(default_factory=list)


@dataclass
class Subsystem:
    """A collapsed module instance, exported as one subsystem block.  Its
    electrical ports are the ports of the collapsed box, left side then
    right side, each top to bottom; gate drives enter and measurements leave
    through signal ports the emitters add."""
    name: str            # instance id, the block's name
    box: list            # [x0, y0, x1, y1] of the box on the top-level drawing (layout units)
    # {name, side: left | right, net, at: [x, y] on the top-level drawing,
    #  pin: the "Elem.p" of the port inside}
    ports: list
    parts: list          # names of the parts inside
    drawing: dict        # the interior, like Circuit.drawing


@dataclass
class Circuit:
    parts: list[Part]
    signals: list[Signal]
    outputs: list[dict]
    pin_nodes: dict[str, str]
    transient: dict[str, float]
    columns: list[str]
    notes: list[str]
    diode_current: float
    # the drawing the exporters lay the model out from (layout units):
    # element id -> [kind, {pin: [x, y]}], and the drawn wire polylines;
    # with subsystems, the collapsed instances are their port elements only
    drawing: dict = field(default_factory=dict)
    # the closed-loop controller as native control blocks (control_export.describe);
    # the Panel itself hangs on the instance as ``panel`` for the emitters
    control: dict | None = None
    subsystems: list = field(default_factory=list)


def _drawing(lay):
    return {'elements': {e.id: [e.kind, {k: list(v) for k, v in e.pins.items()}] for e in lay.elements},
            'wires': [[list(p) for p in w] for w in lay.wires]}


def _aliased(lay, aliases):
    """The drawing with its devices named as spice_identity names them (an
    authored module's aliases); the module ports stay, a subsystem needs them."""
    if not aliases:
        return lay
    out = Layout.from_dict(lay.to_dict())
    for e in out.elements:
        e.id = aliases.get(e.id, e.id)
    out.netlist = {aliases.get(r.rpartition('.')[0], r.rpartition('.')[0]) + '.' + r.rpartition('.')[2]: n
                   for r, n in out.netlist.items()}
    return out


def _subsystems(circuit, assembly, flat, aliases):
    """One Subsystem per collapsed instance; returns element id -> instance."""
    exported = _aliased(assembly.exported, aliases)
    at = {f'{e.id}.{pin}': xy for e in exported.elements for pin, xy in e.pins.items()}
    node_of = {flat.netlist[ref]: node for ref, node in circuit.pin_nodes.items() if ref in flat.netlist}
    owner = {}
    for name, b in assembly.boxed.items():
        inner = _aliased(b.layout, aliases)
        ports = []
        for port, ref in b.ports.items():
            x, y = at[ref]
            net = inner.netlist[ref]
            if net not in node_of:
                raise SpiceError(f'实例 {name} 的端口 {port} 没有接到任何器件，无法导出为子系统端子')
            ports.append({'name': port, 'side': 'left' if abs(x - b.box[0]) <= abs(x - b.box[2]) else 'right',
                          'net': node_of[net], 'at': [x, y], 'pin': ref})
        ports.sort(key=lambda p: (p['side'] != 'left', -p['at'][1]))
        for e in inner.elements:
            if e.kind != 'port':
                owner[e.id] = name
        circuit.subsystems.append(Subsystem(name, list(b.box), ports, [], _drawing(inner)))
    circuit.drawing = _drawing(exported)
    circuit.notes.append('折叠显示的模块实例导出为子系统方块（' + '、'.join(assembly.boxed) +
                         '）：方块放在折叠框的位置，端子按折叠框上的端口左右两侧、自上而下排列，'
                         '内部按模块定义的图排版；门极和源的驱动信号经子系统的信号输入端进入，'
                         '内部测量经信号输出端引出，不依赖跨层级的信号标签。')
    return owner


def _time_table(cfg, stop, *, pwm=False):
    """Exact linear edge times, including the inverse ramped-phase clock."""
    if not pwm and cfg['kind'] == 'step':
        candidates = [(0., cfg['initial']), (cfg['delay'], cfg['initial']),
                      (cfg['delay'] + cfg['rise'], cfg['final']), (stop, cfg['final'])]
    else:
        period = 1 / cfg['frequency'] if pwm else cfg['period']
        width = cfg['duty'] * period - (cfg['rise'] + cfg['fall']) / 2 if pwm else cfg['width']
        low, high = (0., 1.) if pwm else (cfg['initial'], cfg['final'])
        shift = cfg.get('phase_shift', 0) * period / 360 if pwm else 0
        ramp = cfg.get('phase_ramp', 0) if pwm else 0
        def time_at(u):
            if ramp:
                return u / (1 - shift / ramp) if u < ramp - shift else u + shift
            return u + shift
        delay = cfg['delay']
        count = math.ceil((stop + abs(shift) - delay) / period) + 2
        if 4 * count + 2 > MAX_WAVE_POINTS:
            raise SpiceError(f'导出时间表超过 {MAX_WAVE_POINTS} 点；请缩短仿真时间或降低 PWM/脉冲频率')
        candidates = [(0., low)]
        # The validated SPICE PWM never starts before zero. The extra final
        # cycle permits interpolation when stop falls inside a finite edge.
        for cycle in range(max(0, count)):
            edge = delay + cycle * period
            candidates.extend((time_at(edge + dt), value) for dt, value in
                              ((0, low), (cfg['rise'], high),
                               (cfg['rise'] + width, high),
                               (cfg['rise'] + width + cfg['fall'], low)))
        candidates.append((max(stop, candidates[-1][0]) + period, low))
    ordered = sorted(dict(candidates).items())
    result = []
    for t, value in ordered:
        if t < 0:
            raise SpiceError('导出波形不能在零时刻之前开始')
        if t > stop:
            previous_t, previous_y = result[-1]
            result.append((stop, previous_y + (value - previous_y) * (stop - previous_t) / (t - previous_t)))
            break
        result.append((t, value))
        if t == stop:
            break
    return result


def build_circuit(layout, profile='ieee-single', diode_current=DEFAULT_DIODE_CURRENT, *,
                  control='continuous', base_dir=None):
    """Run the existing complete SPICE validation, then translate its graph.
    ``control``: how a ``closed_loop`` controller is exported (control_export.MODES);
    ``base_dir``: where its control table path is read from (the circuit file's folder)."""
    from .control_export import MODES
    if control not in MODES:
        raise SpiceError(f'闭环控制器导出模式须为 {list(MODES)}，不是 {control!r}')
    deck = make_deck(layout, profile, base_dir)
    flat, report, assembly = resolved_layout(layout, profile)
    reference = number(diode_current, '二极管线性化参考电流')
    if reference <= 0:
        raise SpiceError('二极管线性化参考电流必须大于 0 A')
    mapping = flat.extra.get('model_mapping', {})
    if mapping.get('switch', {}).get('model') == 'mosfet':
        raise SpiceError('Simulink / PLECS 暂不支持 SPICE LEVEL=1 MOSFET 映射；请另存副本并显式选择 ideal_switch 或 igbt，不能自动降级')
    sim = flat.extra['simulation']
    tr = {k: number(v, k) for k, v in sim['transient'].items()}
    loop = None
    if 'closed_loop' in flat.extra:
        from .closed_loop import ClosedLoopError, load as load_loop
        try:
            loop = load_loop(flat.extra['closed_loop'], base_dir, sim['components'],
                             {e.id: e.kind for e in flat.elements}, [p['name'] for p in sim['probes']])
        except ClosedLoopError as exc:
            raise SpiceError(str(exc)) from None
    ground = report.nets[sim['ground']]
    node_names = {n: ('0' if n == ground else f'n{i}') for i, n in enumerate(sorted(set(report.nets.values())), 1)}
    if 'electrical' in flat.extra:
        from .electrical import pin_names
        stable = pin_names(flat.extra['electrical'])
        node_names = {n: ('0' if n == ground else stable[ref]) for ref, n in report.nets.items()}
    pin_nodes = {ref: node_names[n] for ref, n in report.nets.items()}
    notes = ['从已校验的 JSON 接线和 simulation 参数生成；没有猜测拓扑或读取文字标注数值。',
             '目标模型使用理想变压器（可带折算到原边的线性励磁电感、漏感）和简化开关（可带线性输出电容），'
             '不包含器件损耗、反向恢复、电压相关的 Coss 或磁饱和，不能据此判断损耗。',
             f'二极管采用 27 °C、{reference:g} A 处的指数模型切线：Vf + Ron·I，反向截止；与 ngspice 指数模型不完全等价。']
    circuit = Circuit([], [], [], pin_nodes, tr, deck.columns, notes, reference, _drawing(flat))
    owner = {}        # element id -> the collapsed instance (subsystem) it is inside
    if assembly is not None and assembly.boxed:
        owner = _subsystems(circuit, assembly, flat, (layout.extra.get('module_authoring') or {}).get('aliases', {}))
    entries, drives, driven = {}, {}, {}
    node_counter = 0
    occupied_nodes = set(pin_nodes.values())

    def internal():
        nonlocal node_counter
        node_counter += 1
        while f'cd_internal_{node_counter}' in occupied_nodes:
            node_counter += 1
        net = f'cd_internal_{node_counter}'
        occupied_nodes.add(net)
        return net

    def part(kind, nodes, params, position, source, role='component'):
        safe = re.sub(r'[^A-Za-z0-9_]', '_', source)[:35]
        p = Part(f'e{len(circuit.parts) + 1}_{safe}', kind, list(nodes), params, position, source, role)
        circuit.parts.append(p)
        return p

    def signal(cfg, pos, *, pwm=False):
        kind = 'pwl' if pwm or cfg.get('kind') in ('step', 'pulse') else cfg.get('kind', 'dc')
        s = Signal(f'signal_{len(circuit.signals) + 1}', kind, cfg, (pos[0], pos[1] + 100))
        if kind == 'pwl':
            s.points = _time_table(cfg, tr['stop'], pwm=pwm)
        circuit.signals.append(s)
        return s.name

    def diode(nodes, cfg, pos, source):
        thermal = cfg['n'] * THERMAL_VOLTAGE
        resistance = cfg['rs'] + thermal / (reference + cfg['is'])
        drop = thermal * (math.log1p(reference / cfg['is']) - reference / (reference + cfg['is']))
        if not math.isfinite(resistance + drop) or resistance <= 0:
            raise SpiceError(f'{source}: 二极管线性化超出数值范围')
        return part('D', nodes, {'vf': drop, 'ron': resistance}, pos, source)

    for e in flat.elements:
        if e.kind == 'port':
            continue
        cfg = sim['components'][e.id]
        values = {k: number(v, f'{e.id}.{k}') for k, v in cfg.items() if k not in ('model', 'polarity', 'wave')}
        pos = (sum(p[0] for p in e.pins.values()) / len(e.pins) * PIXELS_PER_UNIT,
               -sum(p[1] for p in e.pins.values()) / len(e.pins) * PIXELS_PER_UNIT)
        n = lambda pin: pin_nodes[f'{e.id}.{pin}']
        first = len(circuit.parts)
        if e.kind in ('V', 'vac', 'I'):
            excitation = {'kind': 'dc', 'dc': values['dc']}
            if 'wave' in cfg:
                excitation = {k: (v if k == 'kind' else number(v, k)) for k, v in cfg['wave'].items()}
            ports = [n('a'), n('b')] if e.kind == 'I' else [n('b'), n('a')]
            if e.kind != 'I' and (values.get('series_resistance', 0) or values.get('series_inductance', 0)):
                # the internal impedance runs from the EMF to the + terminal, as in ngspice
                emf = end = internal()
                if values.get('series_resistance', 0):
                    middle = internal() if values.get('series_inductance', 0) else ports[0]
                    part('R', [emf, middle], {'value': values['series_resistance']}, pos, e.id)
                    end = middle
                if values.get('series_inductance', 0):
                    part('L', [end, ports[0]], {'value': values['series_inductance'], 'initial': 0}, pos, e.id)
                ports[0] = emf
            p = part('I' if e.kind == 'I' else 'V', ports, {'signal': signal(excitation, pos)}, pos, e.id)
            entries[e.id, ''] = [(p, 0)]
        elif e.kind in ('R', 'L', 'C'):
            a, b = n('a'), n('b')
            parasitic = mapping.get(e.kind, {})
            if parasitic.get('model') == 'parasitic':
                v = {k: number(value, k) for k, value in parasitic.items() if k != 'model'}
                if e.kind in ('R', 'L'):
                    middle = internal()
                    kind, value = ('L', v['series_inductance']) if e.kind == 'R' else ('R', v['series_resistance'])
                    part(kind, [a, middle], {'value': value, 'initial': 0}, pos, e.id)
                    part('C', [a, b], {'value': v['parallel_capacitance'], 'initial': 0}, pos, e.id)
                    a = middle
                else:
                    middle, end = internal(), internal()
                    part('R', [a, middle], {'value': v['series_resistance']}, pos, e.id)
                    part('L', [middle, end], {'value': v['series_inductance'], 'initial': 0}, pos, e.id)
                    a = end
            elif e.kind == 'L' and values.get('series_resistance', 0):
                middle = internal()
                part('R', [middle, b], {'value': values['series_resistance']}, pos, e.id)
                b = middle
            p = part(e.kind, [a, b], {'value': values['value'], 'initial': values.get('initial', 0)}, pos, e.id)
            entries[e.id, ''] = [(p, 0)]
        elif e.kind == 'D':
            a, b = n('a'), n('b')
            diode([a, b], values, pos, e.id)
            for key, _, default in OPTIONAL_FIELDS['D']:
                values.setdefault(key, number(default, f'{e.id}.{key}'))
            if values['breakdown_voltage']:
                vz, izt, zzt = (values[k] for k in ('breakdown_voltage', 'breakdown_current', 'zener_resistance'))
                vf, ron = vz - zzt * izt, zzt + values['rs']
                if not math.isfinite(vf + ron) or vf < 0:
                    raise SpiceError(f'{e.id}: 击穿切线截距 Vz−Zzt×Izt 必须非负且有限，'
                                     '请核对击穿参数；不能静默夹到零')
                part('D', [b, a], {'vf': vf, 'ron': ron}, pos, e.id, 'breakdown')
                notes.append(f'{e.id}: 反向击穿以 Izt={izt:g} A 处切线近似：'
                             f'Vf={vf:g} V、Ron={ron:g} Ω；离开测试电流后与 ngspice 指数模型有偏差。')
            # A current probe measures the whole a->b terminal current, including
            # both parallel branches; reverse breakdown therefore reads negative.
            entries[e.id, ''] = [(p, i) for p in circuit.parts[first:] for i, net in enumerate(p.nodes) if net == a]
        elif e.kind == 'scr':
            # gate pulses as a PWM table; the targets latch it (see simulink / plecs)
            a, b = n('a'), n('b')
            drive = drives[e.id] = signal(values, pos, pwm=True)
            params = {'ron': values['ron'], 'roff': values['roff'], 'vf': values['vf'], 'signal': drive}
            if values.get('holding_current', 0) or values.get('latching_current', 0) or values.get('turn_off_time', 0):
                # holding / latching current as anode-cathode voltages, as in
                # ngspice; the targets build these latches with logic blocks
                from .spice import thyristor_thresholds
                params['v_hold'], params['v_latch'] = thyristor_thresholds(values)
                if values.get('turn_off_time', 0):
                    params['tq'] = values['turn_off_time']
            part('SCR', [a, b], params, pos, e.id)
            if values.get('snubber_resistance', 0):
                middle = internal()
                part('R', [a, middle], {'value': values['snubber_resistance']}, pos, e.id)
                part('C', [middle, b], {'value': values['snubber_capacitance'], 'initial': 0}, pos, e.id)
            entries[e.id, ''] = [(p, i) for p in circuit.parts[first:] for i, net in enumerate(p.nodes) if net == a]
        elif e.kind == 'transformer':
            sign = 1 if cfg['polarity'] == 'same' else -1
            windings = [('secondary', 's1', 's2')] if 'sct' not in e.pins else [('upper', 's1', 'sct'), ('lower', 'sct', 's2')]
            # as in spice._transformer_lines: leakage from p1 to the core,
            # magnetising inductance across the core's primary
            leakage, core = None, n('p1')
            if values.get('leakage_inductance', 0):
                core = internal()
                leakage = part('L', [n('p1'), core], {'value': values['leakage_inductance'], 'initial': 0}, pos, e.id)
            primary = []
            for winding, a, b in windings:
                p = part('T', [core, n('p2'), n(a), n(b)],
                         {'ratio': sign * values['ratio'] / len(windings)}, pos, e.id)
                primary.append((p, 0))
                entries[e.id, winding] = [(p, 2)]
            if values.get('magnetizing_inductance', 0):
                magnetizing = part('L', [core, n('p2')], {'value': values['magnetizing_inductance'], 'initial': 0}, pos, e.id)
                primary.append((magnetizing, 0))
            entries[e.id, 'primary'] = [(leakage, 0)] if leakage else primary
            ref = n('sct' if 'sct' in e.pins else 's2')
            if ref != n('p2'):
                part('R', [ref, n('p2')], {'value': values['reference_resistance']}, pos, e.id)
        else:
            a, b = (n('c'), n('e')) if e.kind == 'igbt' else (n('d'), n('s'))
            if loop and e.id in loop.driven:
                # the gate comes from the control panel's carrier compare
                s = Signal(f'signal_{len(circuit.signals) + 1}', 'control', {**values, 'switch': e.id}, (pos[0], pos[1] + 100))
                circuit.signals.append(s)
                drive = s.name
                driven[e.id] = (drive, values)
            else:
                drive = signal(values, pos, pwm=True)
            drives[e.id] = drive
            mapped = mapping.get('switch', {})
            kind = mapped.get('model', 'igbt' if e.kind == 'igbt' else 'ideal_switch')
            params = {'ron': values['ron'], 'roff': values['roff'], 'signal': drive}
            if kind == 'igbt':
                params['vce0'] = number(mapped.get('vce0', values.get('vce0', 0)), 'vce0')
            part('Q' if kind == 'igbt' else 'S', [a, b], params, pos, e.id)
            if e.opts.get('diode', True) is not False:
                diode([b, a], {'is': values.get('diode_is', 1e-9), 'n': values.get('diode_n', 1.),
                               'rs': values.get('diode_rs', .01)}, pos, e.id)
            if values.get('snubber_resistance', 0):
                middle = internal()
                part('R', [a, middle], {'value': values['snubber_resistance']}, pos, e.id)
                part('C', [middle, b], {'value': values['snubber_capacitance'], 'initial': 0}, pos, e.id)
            if values.get('output_capacitance', 0):
                part('C', [a, b], {'value': values['output_capacitance'], 'initial': 0}, pos, e.id)
            # IGBT probes measure total terminal current, including the reverse
            # diode, off-state leakage and snubber, as in the existing exporter.
            entries[e.id, ''] = [(p, i) for p in circuit.parts[first:] for i, net in enumerate(p.nodes) if net == a]
    # Which level a part sits on: a drawn element's parts go with it; a part
    # the drawing has no place for goes where its nets are.  Ground is a
    # reference block at any level, so net 0 counts as reachable everywhere.
    inside = {s.name: {port['net'] for port in s.ports} | {'0'} for s in circuit.subsystems}
    top = {'0'} | {net for s in circuit.subsystems for net in inside[s.name]}
    for p in circuit.parts:
        (inside[owner[p.source]] if p.source in owner else top).update(p.nodes)

    def level(nets, what):
        """The subsystem that holds every net, or None for the top level."""
        if set(nets) <= top:
            return None
        where = [name for name, nets_in in inside.items() if set(nets) <= nets_in]
        if not where:
            raise SpiceError(f'{what} 跨两个折叠模块内部的网络，子系统导出无法连接；'
                             '请把其中一个实例改为展开显示，或测量模块端口上的网络')
        return where[0]

    def place(p, where):
        if where is not None:
            next(s for s in circuit.subsystems if s.name == where).parts.append(p.name)
    for p in circuit.parts:
        place(p, owner.get(p.source))
    if any(p.kind == 'SCR' for p in circuit.parts):
        notes.append('晶闸管：门极脉冲触发，正向导通为 Vf + Ron，电流将要反向时关断（维持电流为 0），另保留 Roff 漏电。'
                     'Simulink 用开关、正向二极管和锁存逻辑（Fcn + Memory）搭成，PLECS 用原生 Thyristor。')
    if tr.get('node_capacitance', 0):
        for net in sorted(set(pin_nodes.values()) - {'0'}):
            place(part('C', [net, '0'], {'value': tr['node_capacitance'], 'initial': 0}, (0, 0),
                       f'numerical_{net}', 'numerical_capacitance'), level([net], f'网络 {net} 的数值稳定电容'))
        notes.append('已保留每个网络到地的显式数值稳定电容；它会影响快速瞬态，并非器件寄生模型。')
    measured = {}
    for index, probe in enumerate(sim['probes']):
        kind = probe['kind']
        if kind == 'drive':
            origin = drives[probe['element']]
        elif kind == 'voltage':
            p = part('VM', [pin_nodes[probe['positive']], pin_nodes[probe['negative']]], {}, (0, 0), probe['name'])
            place(p, level(p.nodes, f'电压测量 {probe["name"]}'))
            origin = p.name
        else:
            key = probe['element'], probe.get('winding', '')
            if key not in measured:
                terminals = entries[key]
                first, pin = terminals[0]
                original, middle = first.nodes[pin], internal()
                for component, port in terminals:
                    component.nodes[port] = middle
                sensor = part('AM', [original, middle], {}, first.position, probe['name'])
                place(sensor, owner.get(probe['element']))
                measured[key] = sensor.name
            origin = measured[key]
        circuit.outputs.append({'name': deck.columns[index + 1], 'origin': origin, 'kind': kind})
    if loop:
        _controller(circuit, loop, control, driven, {p['name']: o for p, o in zip(sim['probes'], circuit.outputs)}, tr)
    # Export-only sensors and numerical elements get separate side columns.
    # The original device positions are projected directly; no coordinates or
    # generated routes are installed in the source JSON.
    physical = [p for p in circuit.parts if p.kind not in ('AM', 'VM') and p.role == 'component']
    side = max(p.position[0] for p in physical) + 240
    top = min(p.position[1] for p in physical)
    for kind, offset in (('numerical_capacitance', 0), ('measurement', 180)):
        helpers = [p for p in circuit.parts if p.role == kind] if kind != 'measurement' else [p for p in circuit.parts if p.kind in ('AM', 'VM')]
        for index, helper in enumerate(helpers):
            helper.position = side + offset, top + index * 120
    return circuit


def _controller(circuit, loop, mode, driven, probe_outputs, tr):
    """Fixed references as drive-panel generators, then the control panel."""
    from .control_export import build, describe
    references = {}
    for bid, cfg in loop.inputs.items():
        if 'value' not in cfg:
            continue
        name = f'signal_{len(circuit.signals) + 1}'
        if cfg['ramp'] > 0:
            # ramp from 0, then hold: the drive panel's time table (PWL) does it
            points = [(0., 0.), (cfg['ramp'], cfg['value'])] if cfg['ramp'] < tr['stop'] else \
                [(0., 0.), (tr['stop'], cfg['value'] * tr['stop'] / cfg['ramp'])]
            s = Signal(name, 'pwl', {'kind': 'ramp', 'value': cfg['value'], 'ramp': cfg['ramp'], 'input': bid}, (0, 0), points)
        else:
            s = Signal(name, 'dc', {'kind': 'dc', 'dc': cfg['value'], 'input': bid}, (0, 0))
        circuit.signals.append(s)
        references[bid] = name
    panel = build(loop, mode, driven, probe_outputs, references)
    circuit.panel = panel
    circuit.control = describe(panel)
    pl = loop.plan
    how = ('连续时间模型（与 ngspice 闭环一致，用于对照）' if mode == 'continuous' else
           f'离散模型（code.fs = {pl.fs:g} Hz，Ts = {pl.ts:g} s，{pl.method}；系数与 codegen 生成的 DSP 代码相同，按双精度，不含定点舍入）')
    circuit.notes.append(f'闭环控制：控制表 {loop.name} 的控制器按{how}导出为原生控制方块，排在图下方的控制面板；'
                         f'被驱动开关的门极由占空比与载波比较生成，simulation 里的固定 duty 不再使用。')
    circuit.notes.extend('闭环控制：' + w for w in panel.warnings)


def model_stem(value):
    """Deterministic ASCII identifier, safe as filename, MATLAB name and path."""
    original = str(value)
    safe = re.sub(r'[^A-Za-z0-9_]', '_', original).strip('_')[:40]
    suffix = '_' + hashlib.sha256(original.encode('utf-8')).hexdigest()[:8] if safe != original else ''
    return 'cd_' + (safe or 'circuit') + suffix


def export_simulation(layout, out_dir, stem='circuit', *, target, profile='ieee-single',
                      diode_current=DEFAULT_DIODE_CURRENT,
                      numerical_resistance=DEFAULT_NUMERICAL_RESISTANCE,
                      control='continuous', base_dir=None):
    """``control``: continuous or discrete controller for a ``closed_loop`` table;
    ``base_dir``: the circuit file's folder, where its control table path is read from."""
    if target not in ('simulink', 'plecs'):
        raise SpiceError('仿真导出目标必须为 simulink 或 plecs')
    circuit = build_circuit(layout, profile, diode_current, control=control, base_dir=base_dir)
    name = model_stem(stem)
    if target == 'simulink':
        from .simulink import emit
        filename = f'build_{name}.m'
        requirements = 'MATLAB + Simulink + Simscape（Foundation Library）；已使用 R2024b 验证。'
        how = f'在 MATLAB 中切换到本目录，执行 model = build_{name}; 然后 sim(model)。脚本保存 {name}.slx。'
        content = emit(circuit, name)
        options = {}
    else:
        from .plecs import emit
        filename = name + '.plecs'
        requirements = 'PLECS Standalone 独立版 4.9 或以上；不需要 PLECS Blockset。'
        how = f'用 PLECS Standalone 打开 {filename}，运行仿真；测量量连接到 Scope 和顶层输出端口。'
        resistance = number(numerical_resistance, 'PLECS 数值电容串联电阻')
        if resistance <= 0:
            raise SpiceError('PLECS 数值电容串联电阻必须大于 0 Ω；只作用于显式启用的 node_capacitance')
        content = emit(circuit, name, resistance)
        options = {'numerical_capacitor_series_resistance_ohm': resistance}
    if circuit.control:
        options['closed_loop_controller'] = control
    manifest = {'format_version': 1, 'target': target, 'model': name, 'target_options': options, **asdict(circuit)}
    if not circuit.subsystems:
        del manifest['subsystems']       # a table without collapsed instances exports as before, byte for byte
    readme = '\n'.join([requirements, how, '', *circuit.notes, '',
                        '测量通道（按顺序）：' + ', '.join(circuit.columns),
                        '原始器件、引脚到网络和目标方块的对应关系见同目录 manifest.json。',
                        '同名导出文件会更新。导出器不改动原坐标；编辑器按钮会像 SPICE 入口一样保存本窗口的仿真参数。']) + '\n'
    # Generate and validate all content before creating any destination files.
    destination = Path(out_dir).resolve()
    outputs = {filename: content,
               f'{name}_{target}.manifest.json': json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
               f'{name}_{target}.README.txt': readme}
    destination.mkdir(parents=True, exist_ok=True)
    for basename, text in outputs.items():
        (destination / basename).write_text(text, encoding='utf-8')
    return {'files': [str(destination / f) for f in outputs], 'out_dir': str(destination),
            'columns': circuit.columns, 'ran': False, 'target': target, 'notes': circuit.notes,
            'control': control if circuit.control else None}
