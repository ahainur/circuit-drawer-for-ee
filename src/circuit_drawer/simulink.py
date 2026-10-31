"""MATLAB builder for a native Simulink + Simscape Foundation model."""
from collections import defaultdict
from types import SimpleNamespace
import math
import re

from .spice import SpiceError

# Foundation diode and switch blocks require strictly positive leakage. Declare
# this target-specific regularization; never silently use library defaults.
MIN_CONDUCTANCE = 1e-12
MIN_FORWARD_DROP = 1e-12
BLOCK = 40             # px placement rectangle; native port leads may extend beyond it
# Simulink's Orientation names where a block faces: its left (LConn) ports sit
# on the opposite side.  The layout's direction names where terminal 1 (LConn)
# points, so the two are opposite. CurrentSource is the exception: its
# positive flow is tail (RConn(2)) -> head (LConn(1)), verified in R2024b.
LANE = 70              # px between parallel branches of one drawn element
PORT_GAP = 90          # px a subsystem's Connection Port sits outside its drawn pin (past a sensor's LEAD)
SIDE = 80              # px from a block to its signal converter
LATCH_GAP = 70         # px a thyristor latch's Memory blocks sit beside its Fcn / Mux
TIMER_GAP = 100        # px its turn-off timer sits on the other side (two 80 px Fcn blocks apart)
LATCH_SHIFT = 60       # px steps along the switch when its latch logic must dodge other blocks
LATCH_MARGIN = 10      # px kept clear around each latch block
ROW = 60               # px between rows of signal generators
ORIENTATION = {'right': 'left', 'left': 'right', 'up': 'down', 'down': 'up'}
# Control panel blocks, by control_export kind: library path and placement
# rectangle (w, h).  Paths, parameter names and port numbering follow
# the blocks as saved by MATLAB R2024b; Simulink numbers
# Inport and Outport separately.
CONTROL = {
    'input': ('simulink/Signal Routing/From', (60, 30)),
    'const': ('simulink/Sources/Constant', (50, 30)),
    'sum': ('simulink/Math Operations/Sum', (40, 40)),
    'gain': ('simulink/Math Operations/Gain', (50, 30)),
    'product': ('simulink/Math Operations/Product', (40, 40)),
    'tf': ('simulink/Continuous/Transfer Fcn', (80, 50)),
    'dtf': ('simulink/Discrete/Discrete Transfer Fcn', (80, 50)),
    'pi': ('simulink/Continuous/PID Controller', (70, 50)),
    'dpi': ('simulink/Continuous/PID Controller', (70, 50)),      # TimeDomain Discrete-time
    'sat': ('simulink/Discontinuities/Saturation', (50, 40)),
    'delay': ('simulink/Discrete/Unit Delay', (50, 40)),
    'carrier': ('simulink/Sources/Repeating Sequence', (60, 40)),
    'compare': ('simulink/Logic and Bit Operations/Relational Operator', (40, 50)),
    'step': ('simulink/Sources/Step', (50, 40)),
    'mux': ('simulink/Signal Routing/Mux', (10, 60)),
    'demux': ('simulink/Signal Routing/Demux', (10, 60)),
    'abc2dq': ('eeTransforms/Park Transform', (80, 60)),
    'dq2abc': ('eeTransforms/Inverse Park Transform', (80, 60)),
    'minmax': ('simulink/Math Operations/MinMax', (50, 60)),
    'fcn': ('simulink/User-Defined Functions/Fcn', (80, 40)),
}
INTEGRATOR_METHOD = {'tustin': 'Trapezoidal', 'backward': 'Backward Euler', 'forward': 'Forward Euler',
                     'zoh': 'Forward Euler'}       # codegen's zoh PI is forward Euler (c0 = 0, c1 = ki Ts)


def emit(circuit, name):
    circuit.notes.append('Simscape Foundation 二极管采用 1e-12 S 反向电导；IGBT 串联开关也保留 1e-12 S 数值电导，零压降截距用 1e-12 V。')
    if any(p.role == 'numerical_capacitance' for p in circuit.parts):
        circuit.notes.append('Simscape 数值稳定电容的初值使用低优先级，服从理想电压源的电压约束；实际器件的 L/C 初值仍为高优先级。')
    if circuit.subsystems:
        circuit.notes.append('Simulink：子系统的电气端子是 Simscape Connection Port（Side 按折叠框的左右，Port 全子系统连续编号，'
                             '左侧在前）；驱动信号经子系统 Inport 进入，内部传感器的读数经 Outport 引出。已在 MATLAB R2024b 上实机验收（llc_single 折叠与展开两版稳态一致）。')
    q = lambda value: "'" + str(value).replace("'", "''") + "'"
    def num(value):
        if not math.isfinite(value):
            raise SpiceError('Simulink 参数超出有限数值范围')
        return f'{value:.17g}'
    tr = circuit.transient
    lines = [f'function model = build_{name}(overwrite)',
             '% Generated from checked Circuit Drawer JSON. See the adjacent README/manifest.',
             '% Requires MATLAB, Simulink and Simscape Foundation Library (R2024b+).',
             'if nargin < 1, overwrite = false; end',
             f'model = {q(name)};',
             "destination = fullfile(fileparts(mfilename('fullpath')), [model '.slx']);",
             "if bdIsLoaded(model), error('CircuitDrawer:OpenModel', 'Model is already open; close it before rebuilding.'); end",
             "if isfile(destination) && ~overwrite, error('CircuitDrawer:ExistingModel', 'SLX already exists; move it or call this builder with true to replace it.'); end",
             "load_system('simulink'); load_system('fl_lib'); load_system('nesl_utility');",
             'new_system(model);', 'try',
             f"set_param(model, 'SolverType', 'Variable-step', 'Solver', 'ode23t', 'StopTime', {q(num(tr['stop']))}, 'MaxStep', {q(num(tr['max_step']))}, 'RelTol', '1e-5', 'AbsTol', '1e-9');",
             "set_param(model, 'SaveOutput', 'on', 'OutputSaveName', 'yout', 'SaveFormat', 'Dataset', 'SaveTime', 'on', 'TimeSaveName', 'tout', 'ReturnWorkspaceOutputs', 'on');",
             f"set_param(model, 'OutputOption', 'SpecifiedOutputTimes', 'OutputTimes', {q('unique([0:'+num(tr['step'])+':'+num(tr['stop'])+' '+num(tr['stop'])+'])')});",
             "workspace = get_param(model, 'ModelWorkspace');"]
    links, outputs, derivatives = [], {}, {}
    electrical = 'fl_lib/Electrical/'
    # The drawing decides where each Simscape block sits and which way it
    # turns (see export_layout); an IGBT is a switch and a forward diode in
    # series with an off-state resistor across them, as built below.  A
    # collapsed module instance is a subsystem: its parts sit on their own
    # sheet (``level``), the others on the top level (None).
    owner = {part_name: s.name for s in circuit.subsystems for part_name in s.parts}
    parts = []
    for p in circuit.parts:
        level = owner.get(p.name)
        if p.kind in ('Q', 'SCR'):
            joint = p.name + '_igbt_internal'
            parts += [SimpleNamespace(name=p.name, kind=p.kind, nodes=[p.nodes[0], joint], source=p.source, role=p.role, level=level),
                      SimpleNamespace(name=p.name + '_forward', kind='D', nodes=[joint, p.nodes[1]], source=p.source, role='igbt', level=level),
                      SimpleNamespace(name=p.name + '_off', kind='R', nodes=p.nodes[:2], source=p.source, role='igbt', level=level)]
            if p.kind == 'SCR':
                # the latch reads the anode-cathode voltage (see latch() below)
                parts.append(SimpleNamespace(name=p.name + '_vak', kind='VM', nodes=p.nodes[:2], source=p.source,
                                             role='thyristor', level=level))
        else:
            parts.append(SimpleNamespace(name=p.name, kind=p.kind, nodes=p.nodes, source=p.source, role=p.role, level=level))
    from .export_layout import DIRS, plan
    half = BLOCK // 2

    def terminal(part, index, direction):
        """Approximate planning footprint; actual wiring uses native port handles."""
        dx, dy = DIRS[direction]
        sign = 1 if index % 2 == 0 else -1
        return dx * half * sign, dy * half * sign
    def signal_side(part, direction):
        """Sources and switches take their converter across from the axis."""
        if part.kind not in ('V', 'I', 'S', 'Q', 'SCR'):
            return None
        dx, dy = DIRS[direction]
        if part.kind == 'I':
            dx, dy = -dx, -dy
        return -dy * SIDE, dx * SIDE

    def layout_of(level, drawing, pin_nodes):
        elements = {k: (kind, {pin: tuple(xy) for pin, xy in pins.items()})
                    for k, (kind, pins) in drawing.get('elements', {}).items()}
        return plan([p for p in parts if p.level == level], elements, [[tuple(p) for p in w] for w in drawing.get('wires', [])],
                    pin_nodes, terminal, signal_side, lane=LANE, drive_reach=SIDE + half, column=SIDE + 2 * BLOCK + 20)

    placed = defaultdict(list)     # system -> rectangles of the blocks added so far

    def add(system, path, block_name, pos, params=None, direction=None, size=(BLOCK, BLOCK)):
        x, y = round(pos[0]), round(pos[1])
        w, h = size[0] // 2, size[1] // 2
        placed[system].append((x - w, y - h, x + w, y + h))
        arguments = ''.join(f', {q(k)}, {q(num(v) if isinstance(v, (int, float)) else v)}' for k, v in (params or {}).items())
        if direction:
            arguments += f", 'Orientation', {q(ORIENTATION[direction])}"
        lines.append(f"add_block({q(path)}, [{system} '/{block_name}'], 'Position', "
                     f"[{x - w} {y - h} {x + w} {y + h}]{arguments});")
        var = 'h_' + _ident(block_name)
        lines.append(f"{var} = get_param([{system} '/{block_name}'], 'PortHandles');")
        return var

    def connect(system, a, b):
        links.append(f"add_line({system}, {a}, {b}, 'autorouting', 'smart');")

    pin_nodes = dict(circuit.pin_nodes)
    for s in circuit.subsystems:
        pin_nodes.update({port['pin']: port['net'] for port in s.ports})
    layout = layout_of(None, circuit.drawing, pin_nodes)
    spots = layout.spots
    x0, y0, x1, y1 = layout.box
    boxes = {s.name: (layout.px((s.box[0], s.box[3])), layout.px((s.box[2], s.box[1]))) for s in circuit.subsystems}
    # the output ports and scope stand right of the drawing; loose rows keep clear of them
    out_x = x1 + 300
    panel_rects = [(out_x - half, y0 + k * 70 - half, out_x + half, y0 + k * 70 + half) for k in range(len(circuit.outputs))]
    panel_rects.append((out_x + 140 - half, y0 + len(circuit.outputs) * 35 - half, out_x + 140 + half, y0 + len(circuit.outputs) * 35 + half))
    layout.lower_loose(panel_rects, half, SIDE)
    bottom = max([y1] + [sp.pos[1] for sp in spots.values()] + [b[1][1] for b in boxes.values()]) + 120

    # signal generators: a drive panel under the drawing, one row each; a
    # closed-loop gate (kind control) comes from the control panel below it
    panel = getattr(circuit, 'panel', None)
    generators = [s for s in circuit.signals if s.kind != 'control']
    for row, s in enumerate(generators):
        s.position = (x0 + 160, bottom + 120 + row * 2 * ROW)       # a derivative row under each
    if panel:
        for signal_name, (block_name, port) in panel.exports.items():
            outputs[signal_name] = _source(panel, block_name, port)

    def table(signal_name, points, pos, interpolate=True):
        rows = ';'.join(f'{num(t)} {num(y)}' for t, y in points)
        lines.append(f"assignin(workspace, {q(signal_name+'_data')}, [{rows}]);")
        handle = add('model', 'simulink/Sources/From Workspace', signal_name, pos,
                     {'VariableName': signal_name + '_data', 'SampleTime': '0',
                      'Interpolate': 'on' if interpolate else 'off', 'ZeroCross': 'on',
                      'OutputAfterFinalValue': 'Holding final value'})
        return handle + '.Outport(1)'

    if any(s.kind == 'sine' for s in generators):
        clock = add('model', 'simulink/Sources/Clock', 'time', (x0 + 40, bottom + 120)) + '.Outport(1)'
    # Supply analytic first derivatives to physical source converters; using
    # their default zero derivative would give a wrong current in a capacitor
    # driven directly by a sine or ramp voltage source.
    derivative_signals = {p.params['signal'] for p in circuit.parts if p.kind in ('V', 'I')}
    for s in generators:
        v, pos = s.params, s.position
        needs_derivative = s.name in derivative_signals
        derivative_pos = pos[0], pos[1] + ROW
        derivative_name = s.name + '_derivative'
        if s.kind == 'dc':
            outputs[s.name] = add('model', 'simulink/Sources/Constant', s.name, pos, {'Value': v['dc']}) + '.Outport(1)'
            if needs_derivative:
                derivatives[s.name] = add('model', 'simulink/Sources/Constant', derivative_name, derivative_pos, {'Value': 0}) + '.Outport(1)'
        elif s.kind == 'pwl':
            outputs[s.name] = table(s.name, s.points, pos)
            if needs_derivative:
                slopes = [(t, (b - a) / (end - t)) for (t, a), (end, b) in zip(s.points, s.points[1:])]
                slopes.append((s.points[-1][0], 0.))
                derivatives[s.name] = table(derivative_name, slopes, derivative_pos, interpolate=False)
        else:
            # Fcn supports relational operators, but not MATLAB's max().
            elapsed = f"((u-{num(v['delay'])})*(u>{num(v['delay'])}))"
            phase = f"(2*pi*{num(v['frequency'])}*{elapsed}+{num(v['phase'])}*pi/180)"
            expression = f"{num(v['offset'])}+{num(v['amplitude'])}*sin{phase}"
            derivative = f"{num(v['amplitude'])}*2*pi*{num(v['frequency'])}*cos{phase}*(u>={num(v['delay'])})"
            functions = [(s.name, expression, pos, outputs)]
            if needs_derivative:
                functions.append((derivative_name, derivative, derivative_pos, derivatives))
            for block_name, formula, position, destination in functions:
                handle = add('model', 'simulink/User-Defined Functions/Fcn', block_name, position, {'Expr': formula})
                connect('model', clock, handle + '.Inport(1)')
                destination[s.name] = handle + '.Outport(1)'

    # The top level always has the Electrical Reference and the one Solver
    # Configuration; a subsystem gets a reference when net 0 has terminals
    # on its sheet, and the solver instead when the top level has none.
    at_top = {n for p in circuit.parts if p.name not in owner for n in p.nodes}
    at_top |= {port['net'] for s in circuit.subsystems for port in s.ports}
    solver_level = None
    if '0' not in at_top:
        solver_level = next(s.name for s in circuit.subsystems
                            if any('0' in p.nodes for p in circuit.parts if p.name in s.parts))

    def sheet(system, level, layout, signals, slopes, readings, terminals):
        """Place one level's parts and their converters.  ``signals`` /
        ``slopes``: port handles of the drive signals and their derivatives on
        this level; ``readings``: sensor name -> converter output handle,
        filled here; ``terminals``: net -> (handle, (block, index)) this
        level's own ports add.  Returns the nets, net -> [(handle, terminal)]."""
        spots = layout.spots
        x0, y0, x1, y1 = layout.box
        nets = defaultdict(list)
        for net, endpoints in terminals.items():
            nets[net].extend(endpoints)
        centre = ((x0 + x1) / 2, (y0 + y1) / 2)
        converters = []          # where the converters placed so far stand
        latches, senses = [], {}  # thyristors and their anode-cathode voltage readings

        def beside(spot, outward=False):
            """Across the block from its axis: the side the layout kept free for a
            source or switch, the side away from the drawing's middle for a sensor."""
            dx, dy = DIRS[spot.direction]
            sx, sy = -dy * SIDE, dx * SIDE
            if outward and (sx * (spot.pos[0] - centre[0]) + sy * (spot.pos[1] - centre[1])) < 0:
                sx, sy = -sx, -sy
            if outward and any(abs(spot.pos[0] + sx - ox) < BLOCK and abs(spot.pos[1] + sy - oy) < BLOCK
                               for ox, oy in [o.pos for o in spots.values()] + converters):
                # A loose sensor can sit one converter-width from a drawn part,
                # or a sensor's outward side can hold a switch's converter.
                # Keep the converter adjacent, using the sensor's other side.
                sx, sy = -sx, -sy
            return spot.pos[0] + sx, spot.pos[1] + sy

        def control(p, handle, pos):
            unit = 'V' if p.kind == 'V' else 'A' if p.kind == 'I' else '1'
            params = {'Unit': unit, 'FilteringAndDerivatives': 'provide', 'UdotUserProvided': '1' if p.kind in ('V', 'I') else '0'}
            converter = add(system, 'nesl_utility/Simulink-PS Converter', p.name + '_input', pos, params)
            converters.append(pos)
            connect(system, signals[p.params['signal']], converter + '.Inport(1)')
            if p.kind in ('V', 'I'):
                connect(system, slopes[p.params['signal']], converter + '.Inport(2)')
            connect(system, converter + '.RConn(1)', handle + '.RConn(1)')

        def clear_of(system, spot, shape):
            """``at(k, side)`` for a thyristor's latch logic (``shape``: k
            outward, offset along the switch and size of each block). On the
            switch's free side as always; where that would overlap blocks
            already placed (the next leg of a bridge), the other side, then
            shifted along the switch in LATCH_SHIFT steps, the nearest clear
            one. With nothing clear it stays on the free side."""
            bx, by = beside(spot)
            sx, sy = bx - spot.pos[0], by - spot.pos[1]

            def place(sign, shift):
                ox, oy = (bx, by) if sign == 1 else (spot.pos[0] - sx, spot.pos[1] - sy)
                ux, uy = sign * sx / SIDE, sign * sy / SIDE
                return lambda k, side=0: (ox + ux * SIDE * k - uy * (side + shift),
                                          oy + uy * SIDE * k + ux * (side + shift))

            def clear(at):
                for k, side, (w, h) in shape:
                    x, y = at(k, side)
                    x0, y0 = x - w / 2 - LATCH_MARGIN, y - h / 2 - LATCH_MARGIN
                    x1, y1 = x + w / 2 + LATCH_MARGIN, y + h / 2 + LATCH_MARGIN
                    if any(x0 < b[2] and b[0] < x1 and y0 < b[3] and b[1] < y1 for b in placed[system]):
                        return False
                return True

            for shift in (0, *(s * LATCH_SHIFT * i for i in range(1, 9) for s in (1, -1))):
                for sign in (1, -1):
                    if clear(at := place(sign, shift)):
                        return at
            return place(1, 0)

        def latch(p, handle, spot, vak):
            """A thyristor's gate: on with a gate pulse, held while Vak > Vf (it
            conducts forward), off otherwise. Memory blocks hold the previous
            step's state and Vak, so the loop through the network is not algebraic.
            The logic runs out from the switch on its free side, one block per
            ``SIDE``: converter, Fcn, Mux, with the two Memory blocks beside them.
            With a holding / latching current (``v_hold`` / ``v_latch``, as in
            spice._thyristor_lines) it is held only above ``v_hold`` and only
            once latched: a second Fcn / Mux / Memory keeps that flag. With a
            turn-off time (``tq``) a limited Integrator is the recovery timer of
            spice._thyristor_lines: 1 while on, run down at 1/tq while off and
            reverse biased; forward voltage while it is between the two
            thresholds fires it again."""
            flagged, timed = 'v_hold' in p.params, 'tq' in p.params
            shape = [(0, 0, (BLOCK, BLOCK)), (1.2, 0, (80, 40)), (2.4, 0, (10, 60)),
                     (1.2, LATCH_GAP, (30, 30)), (2.4, LATCH_GAP, (30, 30))]
            if flagged:
                shape += [(3.6, 0, (80, 40)), (4.8, 0, (10, 60)), (3.6, LATCH_GAP, (30, 30))]
            if timed:
                shape += [(3.6, -TIMER_GAP, (80, 40)), (4.8, -TIMER_GAP, (10, 40)), (2.4, -TIMER_GAP, (30, 30))]
            at = clear_of(system, spot, shape)
            converter = add(system, 'nesl_utility/Simulink-PS Converter', p.name + '_input', at(0),
                            {'Unit': '1', 'FilteringAndDerivatives': 'provide', 'UdotUserProvided': '0'})
            expr = (f"u(1) > 0.5 || (u(2) > 0.5 && u(4) > 0.5 && u(3) > {num(p.params['v_hold'])})" if flagged
                    else f"u(1) > 0.5 || (u(2) > 0.5 && u(3) > {num(p.params['vf'])})")
            if timed:
                from .spice import TQ_DONE, TQ_STARTED
                expr += (f" || (u(2) <= 0.5 && u(5) > {TQ_DONE:g} && u(5) < {TQ_STARTED:g} && "
                         f"u(3) > {num(p.params['vf'])})")
            fcn = add(system, 'simulink/User-Defined Functions/Fcn', p.name + '_latch', at(1.2), {'Expr': expr}, size=(80, 40))
            mux = add(system, 'simulink/Signal Routing/Mux', p.name + '_latch_inputs', at(2.4),
                      {'Inputs': str(3 + flagged + timed)}, size=(10, 60))
            state = add(system, 'simulink/Discrete/Memory', p.name + '_latch_state', at(1.2, LATCH_GAP), size=(30, 30))
            previous = add(system, 'simulink/Discrete/Memory', p.name + '_vak_previous', at(2.4, LATCH_GAP), size=(30, 30))
            converters.extend([at(0), at(1.2), at(2.4)])
            connect(system, signals[p.params['signal']], mux + '.Inport(1)')
            connect(system, state + '.Outport(1)', mux + '.Inport(2)')
            connect(system, vak, previous + '.Inport(1)')
            connect(system, previous + '.Outport(1)', mux + '.Inport(3)')
            connect(system, mux + '.Outport(1)', fcn + '.Inport(1)')
            connect(system, fcn + '.Outport(1)', state + '.Inport(1)')
            connect(system, fcn + '.Outport(1)', converter + '.Inport(1)')
            connect(system, converter + '.RConn(1)', handle + '.RConn(1)')
            if flagged:
                # latched once on with Vak above v_latch, cleared when off. The
                # previous Vak counts only if it was on then: at the firing step
                # it still holds the blocking voltage.
                set_expr = f"(u(4) > 0.5 && u(3) > {num(p.params['v_latch'])})"
                if timed:
                    # a refire (turn-off time not reached) counts as latched
                    set_expr += (f" || (u(4) <= 0.5 && u(5) > {TQ_DONE:g} && u(5) < {TQ_STARTED:g} && "
                                 f"u(3) > {num(p.params['vf'])})")
                set_fcn = add(system, 'simulink/User-Defined Functions/Fcn', p.name + '_latched', at(3.6),
                              {'Expr': f"u(1) > 0.5 && (u(2) > 0.5 || {set_expr})"}, size=(80, 40))
                set_mux = add(system, 'simulink/Signal Routing/Mux', p.name + '_latched_inputs', at(4.8),
                              {'Inputs': '5' if timed else '4'}, size=(10, 60))
                flag = add(system, 'simulink/Discrete/Memory', p.name + '_latched_state', at(3.6, LATCH_GAP), size=(30, 30))
                converters.extend([at(3.6), at(4.8)])
                connect(system, fcn + '.Outport(1)', set_mux + '.Inport(1)')
                connect(system, flag + '.Outport(1)', set_mux + '.Inport(2)')
                connect(system, previous + '.Outport(1)', set_mux + '.Inport(3)')
                connect(system, state + '.Outport(1)', set_mux + '.Inport(4)')
                connect(system, set_mux + '.Outport(1)', set_fcn + '.Inport(1)')
                connect(system, set_fcn + '.Outport(1)', flag + '.Inport(1)')
                connect(system, flag + '.Outport(1)', mux + '.Inport(4)')
            if timed:
                # the recovery timer: +1e9 /s (saturates at 1 within 1 ns) while
                # on, -1/tq while off and reverse biased, held otherwise
                rate = add(system, 'simulink/User-Defined Functions/Fcn', p.name + '_recovery_rate', at(3.6, -TIMER_GAP),
                           {'Expr': f"(u(1) > 0.5)*1e9 - (u(1) <= 0.5)*(u(2) < 0)*{num(1 / p.params['tq'])}"}, size=(80, 40))
                rate_mux = add(system, 'simulink/Signal Routing/Mux', p.name + '_recovery_inputs', at(4.8, -TIMER_GAP),
                               {'Inputs': '2'}, size=(10, 40))
                timer = add(system, 'simulink/Continuous/Integrator', p.name + '_recovery', at(2.4, -TIMER_GAP),
                            {'InitialCondition': '0', 'LimitOutput': 'on', 'UpperSaturationLimit': '1',
                             'LowerSaturationLimit': '0'}, size=(30, 30))
                connect(system, state + '.Outport(1)', rate_mux + '.Inport(1)')
                connect(system, previous + '.Outport(1)', rate_mux + '.Inport(2)')
                connect(system, rate_mux + '.Outport(1)', rate + '.Inport(1)')
                connect(system, rate + '.Outport(1)', timer + '.Inport(1)')
                connect(system, timer + '.Outport(1)', mux + f'.Inport({4 + flagged})')
                if flagged:
                    connect(system, timer + '.Outport(1)', set_mux + '.Inport(5)')

        for p in circuit.parts:
            if owner.get(p.name) != level:
                continue
            spot = spots[p.name]
            pos, turn = spot.pos, spot.direction
            v, nodes = p.params, p.nodes[:]
            params = {}
            if p.kind in ('R', 'L', 'C'):
                block = {'R': 'Resistor', 'L': 'Inductor', 'C': 'Capacitor'}[p.kind]
                params = {{'R': 'R', 'L': 'l', 'C': 'c'}[p.kind]: v['value']}
                if p.kind in ('L', 'C'):
                    state = 'i_L' if p.kind == 'L' else 'vc'
                    params.update({'r': 0, 'g': 0, state: v['initial'], state + '_specify': 'on', state + '_priority': 'High'})
                    if p.role == 'numerical_capacitance':
                        params[state + '_priority'] = 'Low'
                path = electrical + 'Electrical Elements/' + block
            elif p.kind == 'D':
                path = electrical + 'Electrical Elements/Diode'
                params = {'Vf': max(v['vf'], MIN_FORWARD_DROP), 'Ron': v['ron'], 'Goff': MIN_CONDUCTANCE}
            elif p.kind in ('S', 'Q', 'SCR'):
                path = electrical + 'Electrical Elements/Switch'
                params = {'R_closed': v['ron'], 'G_open': 1 / v['roff'], 'Threshold': .5}
                if p.kind in ('Q', 'SCR'):
                    # Forward-only channel plus a separate, bidirectional Roff.
                    joint = p.name + '_igbt_internal'
                    fwd, off = spots[p.name + '_forward'], spots[p.name + '_off']
                    d = add(system, electrical + 'Electrical Elements/Diode', p.name + '_forward',
                            fwd.pos, {'Vf': max(v['vce0'] if p.kind == 'Q' else v['vf'], MIN_FORWARD_DROP),
                                      'Ron': v['ron'] / 2, 'Goff': MIN_CONDUCTANCE},
                            fwd.direction)
                    nets[joint].append((d + '.LConn(1)', (p.name + '_forward', 0)))
                    nets[nodes[1]].append((d + '.RConn(1)', (p.name + '_forward', 1)))
                    r = add(system, electrical + 'Electrical Elements/Resistor', p.name + '_off', off.pos, {'R': v['roff']},
                            off.direction)
                    nets[nodes[0]].append((r + '.LConn(1)', (p.name + '_off', 0)))
                    nets[nodes[1]].append((r + '.RConn(1)', (p.name + '_off', 1)))
                    if p.kind == 'SCR':
                        sense = spots[p.name + '_vak']
                        vm = add(system, electrical + 'Electrical Sensors/Voltage Sensor', p.name + '_vak', sense.pos, {},
                                 sense.direction)
                        nets[nodes[0]].append((vm + '.LConn(1)', (p.name + '_vak', 0)))
                        nets[nodes[1]].append((vm + '.RConn(2)', (p.name + '_vak', 1)))
                        reading = add(system, 'nesl_utility/PS-Simulink Converter', p.name + '_vak_measurement',
                                      beside(sense, True), {'Unit': 'V'})
                        connect(system, vm + '.RConn(1)', reading + '.LConn(1)')
                        senses[p.name + '_vak'] = reading + '.Outport(1)'
                    nodes[1] = joint
                    params.update(R_closed=v['ron'] / 2, G_open=MIN_CONDUCTANCE)
            elif p.kind == 'T':
                path = electrical + 'Electrical Elements/Ideal Transformer'
                params = {'n': 1 / abs(v['ratio'])}  # Foundation uses Np/Ns.
                if v['ratio'] < 0:
                    nodes[2], nodes[3] = nodes[3], nodes[2]
            elif p.kind in ('V', 'I'):
                path = electrical + 'Electrical Sources/Controlled ' + ('Voltage' if p.kind == 'V' else 'Current') + ' Source'
            else:
                path = electrical + 'Electrical Sensors/' + ('Voltage' if p.kind == 'VM' else 'Current') + ' Sensor'
            facing = ORIENTATION[turn] if p.kind == 'I' else turn
            h = add(system, path, p.name, pos, params, facing)
            if p.kind == 'T':
                ports = [h + '.' + port for port in ('LConn(1)', 'LConn(2)', 'RConn(1)', 'RConn(2)')]
            else:
                # Sources, switches and sensors expose their physical signal at
                # RConn(1), and their negative electrical terminal at RConn(2).
                n = 2 if p.kind in ('V', 'I', 'S', 'Q', 'SCR', 'VM', 'AM') else 1
                ports = [h + '.LConn(1)', h + f'.RConn({n})']
                if p.kind == 'I':
                    # IR/SPICE current flows nodes[0] -> nodes[1]; Simscape's
                    # controlled_current.ssc declares i: tail.i -> head.i.
                    ports.reverse()
            for i, (node, port) in enumerate(zip(nodes, ports)):
                nets[node].append((port, (p.name, i)))
            if p.kind in ('V', 'I', 'S', 'Q'):
                control(p, h, beside(SimpleNamespace(pos=pos, direction=facing)))
            elif p.kind == 'SCR':
                latches.append((p, h, SimpleNamespace(pos=pos, direction=facing)))
            elif p.kind in ('VM', 'AM'):
                unit = 'V' if p.kind == 'VM' else 'A'
                converter = add(system, 'nesl_utility/PS-Simulink Converter', p.name + '_measurement', beside(spot, True), {'Unit': unit})
                connect(system, h + '.RConn(1)', converter + '.LConn(1)')
                readings[p.name] = converter + '.Outport(1)'
        for p, h, pos in latches:
            latch(p, h, pos, senses[p.name + '_vak'])
        return nets

    def ground(system, level, layout, nets):
        """Ground and solver hang under the lowest drawn point of net 0."""
        if level is not None and not nets['0']:
            return
        x0, y0, x1, y1 = layout.box
        zero = [q for a, b in layout.edges.get('0', ()) for q in (a, b) if not isinstance(q[0], str)]
        low = max(zero, key=lambda q: (q[1], -q[0])) if zero else (x0, y1)
        if level is None:
            low = layout.under(low, boxes.values(), (-half, 90 + half))     # the solver sits 90 px right

        def blocked(y):
            """Would the ground (or the solver 90 px right of it) 60 px under y
            overlap a block already placed, e.g. a source's converter?"""
            spans = [low[0]] + ([low[0] + 90] if level == solver_level else [])
            return any(x - half < b[2] and b[0] < x + half and y + 60 - half < b[3] and b[1] < y + 60 + half
                       for x in spans for b in placed[system])
        for _ in range(40):
            if not blocked(low[1]):
                break
            low = (low[0], low[1] + half)
        suffix = '' if level is None else '_' + _ident(level)
        g = add(system, electrical + 'Electrical Elements/Electrical Reference', 'ground' + suffix, (low[0], low[1] + 60))
        if level == solver_level:
            solver = add(system, 'nesl_utility/Solver Configuration', 'solver', (low[0] + 90, low[1] + 60),
                         {'DoDC': 'off', 'AutomaticFiltering': 'off'})
            nets['0'].insert(0, (solver + '.RConn(1)', None))
        # The reference has a single physical terminal (orientation-independent).
        lines.append(f'ground_port{suffix} = [{g}.LConn {g}.RConn];')
        nets['0'].insert(0, (f'ground_port{suffix}(1)', None))

    def wire(system, layout, nets):
        for net, endpoints in nets.items():
            # join the ports of a net in the order the drawn wires reach them
            order = _drawn_order(layout.edges.get(net, ()), [t for _, t in endpoints])
            ranked = sorted(endpoints, key=lambda e: order.get(e[1], len(order)))
            for (a, _), (b, _) in zip(ranked, ranked[1:]):
                connect(system, a, b)

    # subsystems first: a subsystem's port handles exist once its ports do
    top_terminals = defaultdict(list)
    for s in circuit.subsystems:
        system, block = 'sys_' + _ident(s.name), 'h_' + _ident(s.name)
        (bx0, by0), (bx1, by1) = boxes[s.name]
        lines.append(f"add_block('built-in/Subsystem', [model '/{s.name}'], 'Position', [{bx0} {by0} {bx1} {by1}]);")
        lines.append(f"{system} = [model '/{s.name}'];")
        inner = layout_of(s.name, s.drawing, pin_nodes)
        ix0, iy0, ix1, iy1 = inner.box
        terminals = defaultdict(list)
        side_count = defaultdict(int)
        for k, port in enumerate(s.ports, 1):
            side = port['side'].capitalize()
            side_count[side] += 1
            conn = 'Left' if side == 'Left' else 'Right'
            # outside the pin, clear of a series current sensor on its lead.
            # 'Port' must equal the creation order: giving a port a number another
            # port holds swaps the two, Side included (R2024b).
            px, py = inner.pins[port['pin']]
            var = add(system, 'nesl_utility/Connection Port', _ident(s.name) + '_' + port['name'],
                      (px - PORT_GAP if conn == 'Left' else px + PORT_GAP, py),
                      {'Side': conn, 'Port': str(k)}, size=(30, 20))
            lines.append(f'{var}_conn = [{var}.LConn {var}.RConn];')
            terminals[port['net']].append((f'{var}_conn(1)', (var, 0)))
            inner.edges[port['net']].add(((('T', var, 0)), inner.pins[port['pin']]))
            outer = f"{block}.{'L' if conn == 'Left' else 'R'}Conn({side_count[side]})"
            top_terminals[port['net']].append((outer, (s.name, k)))
            layout.edges[port['net']].add((('T', s.name, k), layout.pins[port['pin']]))
        # drive signals enter through Inports down the left, readings leave
        # through Outports down the right
        signals, slopes, readings = {}, {}, {}
        inputs = sorted({(p.params['signal'], p.kind in ('V', 'I')) for p in circuit.parts
                         if p.name in s.parts and p.kind in ('V', 'I', 'S', 'Q', 'SCR')})
        for k, (signal_name, with_slope) in enumerate(inputs):
            y = iy0 + 2 * k * ROW
            signals[signal_name] = add(system, 'simulink/Sources/In1', 'in_' + signal_name, (ix0 - 160, y),
                                       {'Port': str(len(signals) + len(slopes) + 1)}, size=(30, 16)) + '.Outport(1)'
            connect('model', outputs[signal_name], f'{block}.Inport({len(signals) + len(slopes)})')
            if with_slope:
                slopes[signal_name] = add(system, 'simulink/Sources/In1', 'in_' + signal_name + '_derivative', (ix0 - 160, y + ROW),
                                          {'Port': str(len(signals) + len(slopes) + 1)}, size=(30, 16)) + '.Outport(1)'
                connect('model', derivatives[signal_name], f'{block}.Inport({len(signals) + len(slopes)})')
        nets = sheet(system, s.name, inner, signals, slopes, readings, terminals)
        for k, sensor in enumerate(sorted(readings), 1):
            out = add(system, 'simulink/Sinks/Out1', 'out_' + sensor, (ix1 + 160, iy0 + (k - 1) * ROW), {'Port': str(k)}, size=(30, 16))
            connect(system, readings[sensor], out + '.Inport(1)')
            outputs[sensor] = f'{block}.Outport({k})'
        ground(system, s.name, inner, nets)
        wire(system, inner, nets)
        lines.append(f"{block} = get_param([model '/{s.name}'], 'PortHandles');")
    nets = sheet('model', None, layout, outputs, derivatives, outputs, top_terminals)
    if panel:
        _control_panel(circuit, panel, lambda *a, **k: add('model', *a, **k), lambda a, b: connect('model', a, b),
                       outputs, lines, (x0 + 100, bottom + 120 + max(len(generators), 1) * 2 * ROW + 60))
    ground('model', None, layout, nets)
    scope = add('model', 'simulink/Sinks/Scope', 'Scope', (out_x + 140, y0 + len(circuit.outputs) * 35), {'NumInputPorts': str(len(circuit.outputs))})
    for index, output in enumerate(circuit.outputs, 1):
        h = add('model', 'simulink/Sinks/Out1', 'out_' + output['name'], (out_x, y0 + (index - 1) * 70), {'Port': index})
        origin = outputs[output['origin']]
        connect('model', origin, h + '.Inport(1)')
        connect('model', origin, scope + f'.Inport({index})')
    wire('model', layout, nets)
    lines += links
    lines += ["set_param(model, 'SimulationCommand', 'update');", 'save_system(model, destination);',
              'catch exception', 'close_system(model, 0);', 'rethrow(exception);', 'end',
              "if usejava('desktop'), open_system(model); end", 'end']
    return '\n'.join(lines) + '\n'


def _ident(name):
    """A module instance id as a MATLAB identifier part."""
    return re.sub(r'[^A-Za-z0-9_]', '_', name)


def _source(panel, block_name, port):
    """The handle of a control block's output port; a comparator's boolean
    output is read through its Data Type Conversion (see ``_control_panel``)."""
    if panel.block(block_name).kind == 'compare':
        return f'h_{block_name}_dbl.Outport(1)'
    return f'h_{block_name}.Outport({port})'


def _control_panel(circuit, panel, add, connect, outputs, lines, origin):
    """The closed-loop controller as native control blocks under the drive
    panel: a From for every circuit signal it reads (its Goto sits beside),
    one block per control_export block, Terminators on outputs the table
    does not use, and the links between ports."""
    if panel.kinds() & {'abc2dq', 'dq2abc'}:
        lines.append("load_system('eeTransforms');  % Simscape Electrical: Park transforms")
    if 'dpi' in panel.kinds():
        circuit.notes.append('Simulink：离散限幅 PI 用 Discrete PID Controller（钳位抗饱和、积分方法随离散化方法）实现。')
    ox, oy = origin
    vec = lambda values: '[' + ' '.join(f'{float(v):.17g}' for v in values) + ']'
    inf = lambda v, sign: 'inf' if v is None and sign > 0 else '-inf' if v is None else v
    sent = {}
    for b in panel.blocks:
        x, y = ox + b.pos[0], oy + b.pos[1]
        path, size = CONTROL[b.kind]
        p, params = b.params, {}
        if b.kind == 'input':
            source = outputs[p['origin']] if p['source'] == 'probe' else outputs[p['ref']]
            tag = 'cl_' + p['ref']
            if tag not in sent:
                sent[tag] = add('simulink/Signal Routing/Goto', 'send_' + tag, (x - 90, y),
                                {'GotoTag': tag, 'TagVisibility': 'local'}, size=(60, 30))
                connect(source, sent[tag] + '.Inport(1)')
            params = {'GotoTag': tag}
        elif b.kind == 'const':
            params = {'Value': p['value']}
        elif b.kind == 'sum':
            params = {'Inputs': p['signs'], 'IconShape': 'rectangular'}
        elif b.kind == 'gain':
            params = {'Gain': p['k']}
        elif b.kind == 'product':
            params = {'Inputs': p['ops']}
        elif b.kind == 'tf':
            params = {'Numerator': vec(p['num']), 'Denominator': vec(p['den'])}
        elif b.kind == 'dtf':
            params = {'Numerator': vec(p['num']), 'Denominator': vec(p['den']), 'SampleTime': p['ts']}
        elif b.kind in ('pi', 'dpi'):
            params = {'Controller': 'PI', 'Form': 'Parallel', 'P': p['kp'], 'I': p['ki'], 'UseKiTs': 'off',
                      'LimitOutput': 'on', 'UpperSaturationLimit': inf(p['hi'], 1),
                      'LowerSaturationLimit': inf(p['lo'], -1), 'AntiWindupMode': 'clamping'}
            if b.kind == 'pi':
                params.update({'TimeDomain': 'Continuous-time', 'SampleTime': '-1'})
            else:
                params.update({'TimeDomain': 'Discrete-time', 'SampleTime': p['ts'],
                               'IntegratorMethod': INTEGRATOR_METHOD[p['method']]})
        elif b.kind == 'sat':
            params = {'UpperLimit': inf(p['hi'], 1), 'LowerLimit': inf(p['lo'], -1)}
        elif b.kind == 'delay':
            params = {'SampleTime': p['ts']}
        elif b.kind == 'carrier':
            period = 1 / p['f']
            if p['shape'] == 'saw':
                params = {'rep_seq_t': vec([0, period]), 'rep_seq_y': vec([0, 1])}
            else:
                params = {'rep_seq_t': vec([0, period / 2, period]), 'rep_seq_y': vec([0, 1, 0])}
        elif b.kind == 'compare':
            params = {'Operator': '>', 'OutDataTypeStr': 'boolean'}
        elif b.kind == 'step':
            params = {'Time': p['time'], 'Before': 0, 'After': 1, 'SampleTime': 0}
        elif b.kind == 'mux':
            # the native Inverse Park Transform takes dq0: a dq pair gets a zero
            params = {'Inputs': str(3 if p['dq'] else p['n'])}
        elif b.kind == 'demux':
            params = {'Outputs': str(3 if p['dq'] else p['n'])}
        elif b.kind in ('abc2dq', 'dq2abc'):
            params = {'AxisAlignment': 'D-axis', 'PowerInvariant': 'off'}
        elif b.kind == 'minmax':
            params = {'Function': p['op'], 'Inputs': str(b.inputs)}
        elif b.kind == 'fcn':
            u = {f'u{i}': ('u' if b.inputs == 1 else f'u({i})') for i in range(1, b.inputs + 1)}
            params = {'Expr': p['expr'].format(**u)}
        h = add(path, b.name, (x, y), params, size=size)
        if b.kind == 'compare':
            # the native Relational Operator only outputs boolean (R2024b rejects
            # 'double'); the converter and a Product with the Step want double
            cast = add('simulink/Signal Attributes/Data Type Conversion', b.name + '_dbl', (x + 60, y),
                       {'OutDataTypeStr': 'double'}, size=(40, 24))
            connect(h + '.Outport(1)', cast + '.Inport(1)')
        if b.kind == 'mux' and p['dq']:
            zero = add('simulink/Sources/Constant', b.name + '_zero', (x - 50, y + 35), {'Value': 0}, size=(30, 20))
            connect(zero + '.Outport(1)', h + '.Inport(3)')
        if b.kind == 'demux' and p['dq']:
            # after the slots the unused-output Terminators take (one per output)
            end = add('simulink/Sinks/Terminator', b.name + '_zero', (x + 30 + 25 * b.outputs, y + 35), size=(20, 20))
            connect(h + '.Outport(3)', end + '.Inport(1)')
    for src, i in panel.unused_outputs():
        b = panel.block(src)
        end = add('simulink/Sinks/Terminator', f'{src}_end{i}', (ox + b.pos[0] + 30 + 25 * (i - 1), oy + b.pos[1] + 35), size=(20, 20))
        connect(f'h_{src}.Outport({i})', end + '.Inport(1)')
    for lk in panel.links:
        connect(_source(panel, *lk.src), f'h_{lk.dst[0]}.Inport({lk.dst[1]})')


def _drawn_order(edges, terminals):
    """Terminal -> rank in a walk along the drawn wires, so that each port is
    joined to its neighbour on the drawing rather than all to one port."""
    graph = defaultdict(set)
    for a, b in edges:
        graph[a].add(b)
        graph[b].add(a)
    rank, seen = {}, set()
    for start in [('T', *t) for t in terminals if t] + sorted(graph, key=str):
        if start not in graph or start in seen:
            continue
        stack = [start]
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            if isinstance(n[0], str) and n[0] == 'T':
                rank.setdefault((n[1], n[2]), len(rank))
            stack.extend(sorted(graph[n] - seen, key=str, reverse=True))
    return rank
