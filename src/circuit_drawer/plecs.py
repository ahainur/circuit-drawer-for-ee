"""Native PLECS Standalone text writer (no vendor runtime needed to export)."""
from collections import defaultdict, deque
import json
import math

from .spice import SpiceError


def _check_state_dependencies(circuit):
    """Catch common source/state constraints before producing a native file.

    PLECS cannot integrate a capacitor voltage imposed by an ideal source or
    an inductor current imposed by an ideal source. Numerical capacitors have
    an explicit series resistance in this target and do not form ideal loops.
    The target compiler remains the final check for coupled constraints.
    """
    parents = {}
    def root(node):
        parents.setdefault(node, node)
        if parents[node] != node:
            parents[node] = root(parents[node])
        return parents[node]
    def join(a, b):
        parents[root(a)] = root(b)
    voltage_paths = defaultdict(list)
    def join_voltage_path(p):
        join(*p.nodes)
        a, b = p.nodes
        voltage_paths[a].append((b, p))
        voltage_paths[b].append((a, p))
    def constraint_path(a, b):
        # Recover one actual source/capacitor loop, not every capacitor in the
        # connected circuit (which can include unrelated dangling branches).
        previous, pending = {a: None}, deque([a])
        while pending:
            node = pending.popleft()
            if node == b:
                break
            for neighbor, part in voltage_paths[node]:
                if neighbor not in previous:
                    previous[neighbor] = (node, part)
                    pending.append(neighbor)
        path = []
        while b != a:
            b, part = previous[b]
            path.append(part)
        return path
    for p in circuit.parts:
        if p.kind == 'AM' or (p.kind == 'C' and p.role != 'numerical_capacitance'):
            join_voltage_path(p)
    for p in circuit.parts:
        if p.kind == 'V':
            if root(p.nodes[0]) == root(p.nodes[1]):
                path = [p, *constraint_path(*p.nodes)]
                sources = '、'.join(dict.fromkeys(q.source for q in path if q.kind == 'V'))
                capacitors = '、'.join(dict.fromkeys(q.source for q in path if q.kind == 'C'))
                if capacitors:
                    raise SpiceError(
                        f'PLECS：电压源 {sources} 与电容 {capacitors} 构成状态约束；'
                        '请先保存仿真参数并关闭窗口，进入“视图 → 全图器件模型…”（模型映射），'
                        '将“电容”设为“带寄生参数”，在“串联电阻 Ω”填写实际 ESR，点击“应用到全图”'
                        '（model_mapping.C：model=parasitic，series_resistance=ESR）；'
                        '此设置作用于全图所有电容，会同时影响 SPICE 仿真结果。'
                        '然后回到“分析 → SPICE 仿真…”重新导出。不会自动修改器件模型')
                raise SpiceError(f'PLECS：电压源 {sources} 构成理想电压源闭环；请显式加入实际串联阻抗，消除源/状态依赖，不会自动修改器件模型')
            join_voltage_path(p)
    parents.clear()
    for p in circuit.parts:
        if p.kind not in ('L', 'I', 'VM'):
            if p.kind == 'T':
                join(*p.nodes[:2]); join(*p.nodes[2:])
            else:
                join(*p.nodes)
    for p in circuit.parts:
        if p.kind == 'I' and root(p.nodes[0]) != root(p.nodes[1]):
            raise SpiceError(f'PLECS：{p.source} 的电流被强制流经电感或其他理想电流源；请显式加入实际并联通路，消除源/状态依赖')


# Terminal positions in px from a component's Position, for Direction right
# (terminal 1 usually points along Direction; CurrentSource points opposite).
# Read from the elbows PLECS
# 4.9.5 adds to diagonal wires when it saves a model natively: R, L, C, D,
# voltage source, MOSFET, ammeter and voltmeter along the axis, the source
# input and MOSFET gate across it. CurrentSource, Igbt, both sensor outputs and
# Transformer were measured in all four directions with GUI Save as in 4.9.5;
# the raw wires and projections were recorded from those saved models.
# Coordinates affect routing only; connections remain by terminal number.
# Thyristor: anode, cathode, gate, measured in all four directions like the
# others (PLECS 4.9.5).
THYRISTOR_TERMINALS = [(15, 0), (-15, 0), (-10, 30)]
TERMINALS = {
    'Resistor': [(20, 0), (-20, 0)],
    'Inductor': [(20, 0), (-20, 0)],
    'Capacitor': [(10, 0), (-10, 0)],
    'Diode': [(15, 0), (-15, 0)],
    'VoltageSource': [(20, 0), (-20, 0), (0, 25)],
    'CurrentSource': [(-20, 0), (20, 0), (0, 25)],
    'Mosfet': [(20, 0), (-20, 0), (0, -35)],
    'Igbt': [(20, 0), (-20, 0), (0, -35)],
    'Thyristor': THYRISTOR_TERMINALS,
    'Ammeter': [(20, 0), (-20, 0), (0, 15)],
    'Voltmeter': [(20, 0), (-20, 0), (0, 15)],
    'Transformer': [(10, -15), (10, 15), (-10, -15), (-10, 15)],
    'Ground': [(10, 0)],
    'Label': [(0, 0)],
}
KINDS = {'R': 'Resistor', 'L': 'Inductor', 'C': 'Capacitor', 'D': 'Diode', 'V': 'VoltageSource',
         'I': 'CurrentSource', 'S': 'Mosfet', 'Q': 'Igbt', 'SCR': 'Thyristor', 'T': 'Transformer',
         'VM': 'Voltmeter', 'AM': 'Ammeter'}
# parts with a gate input; PLECS's own Thyristor latches and turns off at zero current
GATED = ('S', 'Q', 'SCR')
# PLECS's Thyristor has no holding / latching current or turn-off time: such a thyristor becomes
# a gate-controlled forward-only Igbt (Vf, Ron) whose gate is the latch logic of
# the Simulink export, built from Function / SignalMux / Memory blocks.
LATCH_STEP = 70        # px between the latch logic blocks in its drive-panel row
SIGNAL_REACH = 50      # px from a gate / source input / sensor output to its tag block
ROW = 50               # px between rows in the panels
# Library blocks of the control panel, as saved by PLECS 4.9.5.  A Reference block is saved in its unrotated frame, Direction
# up, with its terminals listed; its terminal numbers are those of the sample.
PID_LIBRARY = 'Components/Control/Continuous/Continuous PID\\nController'
PID_FRAME = (-40, -25, 40, 25)
PID_TERMINALS = [('Input', (-40, 0), 'left'), ('Output', (44, 0), 'right'), ('Input', (-5, -25), 'up'),
                 ('Input', (5, -25), 'up'), ('Input', (15, -25), 'up'), ('Input', (-40, -15), 'left'),
                 ('Input', (-40, 10), 'left'), ('Input', (25, -25), 'up'), ('Input', (35, -25), 'up'),
                 ('Input', (-40, -20), 'left'), ('Input', (-40, -10), 'left'), ('Input', (-40, 20), 'left')]
TRANSFORM_LIBRARY = {'abc2dq': 'Components/Control/Transformations/3ph->RRF',
                     'dq2abc': 'Components/Control/Transformations/RRF->3ph'}
TRANSFORM_FRAME = (-15, -15, 15, 15)
TRANSFORM_TERMINALS = [('Input', (-15, -5), 'left'), ('Output', (19, 0), 'right'), ('Input', (-15, 5), 'left')]
GREATER = 6            # RelationalOperator / ConstantRelationalOperator Operator value for >


def rotate(offset, direction):
    x, y = offset
    return {'right': (x, y), 'down': (-y, x), 'left': (-x, -y), 'up': (y, -x)}[direction]


def emit(circuit, name, numerical_resistance):
    from types import SimpleNamespace
    from .export_layout import DIRS, Spot, direction_of, plan
    _check_state_dependencies(circuit)
    if any(p.role == 'numerical_capacitance' for p in circuit.parts):
        circuit.notes.append(f'PLECS：每个数值稳定电容串联 {numerical_resistance:g} Ω，解除理想源/状态依赖；这会改变极快瞬态，请比较不同阻值及积分步长。实际器件电容不自动加 ESR。')
    if circuit.subsystems:
        circuit.notes.append('PLECS：子系统按 PLECS 文件格式的 Subsystem 写出，端子列表依次为电气端口（Electrical Port）、信号输入、'
                             '信号输出，内部端口块的编号与该顺序一致；连接按端子序号。PLECS 4.9.5 原生验收通过，'
                             '层内信号标签使用 Schematic 作用域，跨层信号只走 Input / Output 端口。')
    quoted = lambda v: json.dumps(str(v), ensure_ascii=False)

    def numeric(v):
        if isinstance(v, (float, int)):
            if not math.isfinite(v):
                raise SpiceError('PLECS 参数超出有限数值范围')
            return f'{v:.17g}'
        return str(v)

    # PLECS switches open ideally: a parallel resistor keeps Roff.  It is a
    # branch of the drawn switch like the reverse diode.  A part of a
    # collapsed module instance sits on that subsystem's sheet (``level``).
    owner = {part_name: s.name for s in circuit.subsystems for part_name in s.parts}
    parts = list(circuit.parts)
    latched = {}         # thyristor with a holding / latching current -> its gate signal
    for p in circuit.parts:
        p.level = owner.get(p.name)
        if p.kind in GATED:
            parts.append(SimpleNamespace(name=p.name + '_off', kind='R', nodes=p.nodes, source=p.source,
                                         role='leakage', params={'value': p.params['roff']}, level=p.level))
        if p.kind == 'SCR' and 'v_hold' in p.params:
            if p.level is not None:
                raise SpiceError(f'PLECS：{p.source} 在折叠模块内，带维持 / 擎住电流的晶闸管暂不支持子系统导出；'
                                 '请把该实例改为展开显示')
            latched[p.name] = p.params['signal']
            parts.append(SimpleNamespace(name=p.name + '_vak', kind='VM', nodes=p.nodes[:2], source=p.source,
                                         role='thyristor', params={}, level=None))
    if latched:
        by_part = {p.name: p for p in circuit.parts}
        if any('tq' in by_part[n].params for n in latched):
            circuit.notes.append('PLECS：设了维持 / 擎住电流或关断时间的晶闸管改用 Igbt（Vf、Ron，只能正向导通）加锁存逻辑'
                                 '（Function、SignalMux、Memory，读取阳极—阴极电压；关断时间用带限幅的 Integrator 计时），'
                                 '与 Simulink 导出相同；原生 Thyristor 没有这些参数。')
        else:
            circuit.notes.append('PLECS：设了维持 / 擎住电流的晶闸管改用 Igbt（Vf、Ron，只能正向导通）加锁存逻辑'
                                 '（Function、SignalMux、Memory，读取阳极—阴极电压），与 Simulink 导出相同；'
                                 '原生 Thyristor 没有这两个参数。')
    plecs_kind = lambda p: 'Igbt' if p.name in latched else KINDS[p.kind]
    occupied = {n for p in parts for n in p.nodes}
    for p in [p for p in parts if p.role == 'numerical_capacitance']:
        middle = f'{p.name}_numerical_internal'
        while middle in occupied:
            middle = '_' + middle
        occupied.add(middle)
        parts.append(SimpleNamespace(name=p.name + '_series', kind='R', nodes=[p.nodes[0], middle],
                                     source=p.source, role='numerical_resistance',
                                     params={'value': numerical_resistance}, level=p.level))
        p.plecs_nodes = [middle, p.nodes[1]]
    for p in parts:
        if p.kind == 'T' and p.params['ratio'] < 0:
            nodes = p.nodes[:]
            nodes[2], nodes[3] = nodes[3], nodes[2]
            p.plecs_nodes = nodes
    nodes_of = lambda p: getattr(p, 'plecs_nodes', p.nodes)
    def native_direction(p, direction):
        # The planner names terminal 1's side. Native CurrentSource instead
        # points its arrow toward terminal 2 (measured in all four directions).
        if p.kind == 'I':
            dx, dy = DIRS[direction]
            return direction_of((-dx, -dy))
        return direction
    terminal = lambda p, i, direction: rotate(TERMINALS[plecs_kind(p)][i], native_direction(p, direction))
    gate = lambda p, direction: rotate(TERMINALS[plecs_kind(p)][2], direction) if p.kind in GATED else None
    pin_nodes = dict(circuit.pin_nodes)
    for s in circuit.subsystems:
        pin_nodes.update({port['pin']: port['net'] for port in s.ports})
    by_name = {p.name: p for p in parts}
    labels = defaultdict(int)
    back = {'right': 'left', 'left': 'right', 'up': 'down', 'down': 'up'}

    def layout_of(level, drawing):
        elements = {k: (kind, {pin: tuple(xy) for pin, xy in pins.items()})
                    for k, (kind, pins) in drawing.get('elements', {}).items()}
        wires = [[tuple(p) for p in w] for w in drawing.get('wires', [])]
        mine = [p for p in parts if p.level == level]
        layout = plan([SimpleNamespace(name=p.name, kind=p.kind, nodes=nodes_of(p), source=p.source, role=p.role)
                       for p in mine], elements, wires, pin_nodes, terminal, gate)
        spots = dict(layout.spots)
        for p in mine:
            if p.kind == 'I':
                spot = spots[p.name]
                spots[p.name] = Spot(spot.pos, native_direction(p, spot.direction), spot.element)
        return mine, layout, spots

    class Sheet:
        """One schematic: the top level, or the inside of a subsystem."""
        def __init__(self, level, layout, spots):
            self.level, self.layout, self.spots = level, layout, spots
            self.components, self.connections, self.terminal_xy = [], [], {}
            self.edges = defaultdict(set, {n: set(e) for n, e in layout.edges.items()})
            self.signals, self.signal_junctions, self.label_names = defaultdict(list), {}, set()

        def block(self, kind, block_name, params, pos, direction='right', extra='', show=True, library=None, frame=None):
            """``library`` / ``frame``: a Reference block's SrcComponent (right after
            its Type) and Frame (before its parameters), in the order PLECS saves them."""
            if circuit.subsystems and kind in ('Goto', 'From'):
                # PLECS 4.9.5: 1 is Global, 2 is Schematic. Each sheet has its
                # own tags; only explicit Input / Output ports cross levels.
                params = dict(params, Visibility=2)
            fields = '\n'.join(f'Parameter {{ Variable {quoted(k)} Value {quoted(numeric(v))} Show off }}' for k, v in params.items())
            source = f'SrcComponent {quoted(library)}\n' if library else ''
            box = f'Frame [{frame[0]},{frame[1]};{frame[2]},{frame[3]}]\n' if frame else ''
            self.components.append(f'Component {{\nType {kind}\n{source}Name {quoted(block_name)}\nShow {"on" if show else "off"}\n'
                                   f'Position [{pos[0]},{pos[1]}]\nDirection {direction}\nFlipped off\nLabelPosition south\n{box}{fields}\n{extra}\n}}')

        def place(self, kind, block_name, spot):
            for i, offset in enumerate(TERMINALS.get(kind, [])):
                dx, dy = rotate(offset, spot.direction)
                self.terminal_xy[block_name, i] = (spot.pos[0] + dx, spot.pos[1] + dy)

        def label(self, net, pos):
            labels['n'] += 1
            block_name = f'net_{labels["n"]}'
            self.block('Label', block_name, {'Tag': net, 'Visibility': 1, 'NoMatchingCounterpartAction': 2}, pos, show=False)
            self.terminal_xy[block_name, 0] = pos
            self.label_names.add(block_name)
            return block_name

        def signal_wire(self, a, pa, b, pb, points=None, junction=None):
            path = '' if not points else ' Points [' + ';'.join(f'{x},{y}' for x, y in points) + ']'
            self.signals[a, pa].append(f'{path} DstComponent {quoted(b)} DstTerminal {pb}')
            if junction is not None:
                self.signal_junctions[a, pa] = junction

        def beside(self, block_name, terminal, reach=SIGNAL_REACH):
            """A point ``reach`` px out from a terminal, and the way out."""
            spot = self.spots[block_name]
            tx, ty = self.terminal_xy[block_name, terminal]
            way = direction_of((tx - spot.pos[0], ty - spot.pos[1]))
            dx, dy = DIRS[way]
            return (tx + dx * reach, ty + dy * reach), way

        def text(self):
            # PLECS permits only one Connection per output terminal. Repeated roots
            # lose their source on load, even when a graph reader can merge the edges.
            # Keep each route's Points inside its Branch, including existing subtrees.
            for (source, port), branches in self.signals.items():
                tree = branches[0]
                if len(branches) > 1:
                    # An omitted junction is drawn at (0, 0) by the native viewer.
                    x, y = self.signal_junctions[source, port]
                    tree = f'Points [{x},{y}]\n' + '\n'.join(
                        'Branch {\n' + branch + '\n}' for branch in branches)
                self.connections.append(f'Connection {{ Type Signal SrcComponent {quoted(source)} SrcTerminal {port}\n{tree}\n}}')
            return 'Schematic {\nLocation [0,0;1280,800]\nZoomFactor 1\n' + '\n'.join(self.components + self.connections) + '\n}'

    def devices(sheet, mine):
        """The level's parts as blocks, and net labels on terminals the drawing does not reach."""
        for p in mine:
            spot = sheet.spots[p.name]
            kind = plecs_kind(p)
            v = getattr(p, 'params', {})
            params = {
                'R': lambda: {'R': v.get('value')},
                'L': lambda: {'L': v.get('value'), 'i_init': v.get('initial')},
                'C': lambda: {'C': v.get('value'), 'v_init': v.get('initial')},
                'D': lambda: {'Vf': v.get('vf'), 'Ron': v.get('ron')},
            }.get(p.kind, dict)()
            if p.kind == 'T':
                params = {'Windings': '[1 1]', 'n': f"[1 {abs(v['ratio']):.17g}]", 'Polarity': '+', 'Lm': 'inf', 'im0': 0}
            if p.kind in GATED:
                # compensate Ron for a bidirectional switch's parallel leakage
                ron = v['ron'] / (1 - v['ron'] / v['roff']) if p.kind == 'S' else v['ron']
                params = {'Ron': ron, 's_init': 0}
                if p.kind in ('Q', 'SCR'):
                    params['Vf'] = v['vce0'] if p.kind == 'Q' else v['vf']
            sheet.block(kind, p.name, params, spot.pos, spot.direction)
            sheet.place(kind, p.name, spot)

    def tags(sheet):
        # Terminals the drawing does not reach carry a net label a little way out.
        for pname, i in sheet.layout.tags:
            spot = sheet.spots[pname]
            tx, ty = sheet.terminal_xy[pname, i]
            dx, dy = DIRS[direction_of((tx - spot.pos[0], ty - spot.pos[1]))]
            net = nodes_of(by_name[pname])[i]
            tag = sheet.label(net, (tx + dx * 20, ty + dy * 20))
            sheet.edges[net].add((('T', pname, i), ('T', tag, 0)))

    def wires(sheet, ground_name, boxes=()):
        """Nets: one wire tree per connected piece; pieces of one net that the
        drawing does not join (and nets with loose parts) meet through labels.
        Returns whether net 0 got its ground symbol (under the lowest drawn
        point of net 0, below any of ``boxes`` that point is on)."""
        edges, terminal_xy = sheet.edges, sheet.terminal_xy

        def coord(node):
            return terminal_xy[node[1], node[2]] if isinstance(node[0], str) and node[0] == 'T' else node

        # Nodes mix coordinate tuples and ('T', name, i) terminals, and str hashes
        # differ between processes: every walk below is in str order so the same
        # table gives the same file in any process (tests/test_export_layout.py checks two hash seeds).
        ground_done = False
        for net in sorted(edges, key=str):
            graph = defaultdict(set)
            for a, b in edges[net]:
                graph[a].add(b)
                graph[b].add(a)
            pieces, seen = [], set()
            for start in sorted(graph, key=str):
                if start in seen:
                    continue
                piece, stack = [], [start]
                seen.add(start)
                while stack:
                    n = stack.pop()
                    piece.append(n)
                    for m in sorted(graph[n], key=str):
                        if m not in seen:
                            seen.add(m)
                            stack.append(m)
                pieces.append(piece)
            pieces = [p for p in pieces if any(n[0] == 'T' for n in p if isinstance(n[0], str))]
            if net == '0' and pieces:
                # the ground symbol hangs under the lowest drawn point of net 0
                main = max(pieces, key=lambda piece: sum(not isinstance(n[0], str) for n in piece))
                points = [n for n in main if not isinstance(n[0], str)]
                if points:
                    low = max(points, key=lambda q: (q[1], -q[0]))
                    low = sheet.layout.under(low, boxes, (-10, 10))
                    spot = Spot((low[0], low[1] + 30), 'up')
                    sheet.block('Ground', ground_name, {}, spot.pos, 'up')
                    sheet.place('Ground', ground_name, spot)
                    graph[low].add(('T', ground_name, 0))
                    graph[('T', ground_name, 0)].add(low)
                    main.append(('T', ground_name, 0))
                    ground_done = True
            if len(pieces) > 1:
                for piece in pieces:
                    if any(_is_terminal(n) and n[1] in sheet.label_names for n in piece):
                        continue
                    points = [n for n in piece if not isinstance(n[0], str)] or [piece[0]]
                    anchor = min(points, key=lambda q: (coord(q)[1], coord(q)[0]))
                    ax, ay = coord(anchor)
                    tag = sheet.label(net, (ax, ay - 25))
                    graph[anchor].add(('T', tag, 0))
                    graph[('T', tag, 0)].add(anchor)
                    piece.append(('T', tag, 0))
            for piece in pieces:
                sheet.connections.extend(_tree(graph, piece, coord, quoted))
        return ground_done

    def loose_ground(sheet, ground_name, where):
        sheet.block('Ground', ground_name, {}, (where[0], where[1] + 30), 'up')
        tag = sheet.label('0', where)
        sheet.connections.append(f'Connection {{ Type Wire SrcComponent {quoted(ground_name)} SrcTerminal 1 DstComponent {quoted(tag)} DstTerminal 1 }}')

    def drives(sheet, mine):
        """Each gate / source input reads its tag from a From block beside it."""
        for p in mine:
            if p.kind not in (*GATED, 'V', 'I'):
                continue
            tag = p.name + '_latched' if p.name in latched else p.params['signal'] + ('_gate' if p.kind in GATED else '')
            port = 3 if p.kind in ('V', 'I') else 3
            near, way = sheet.beside(p.name, 2, 40)
            sheet.block('From', 'get_' + p.name, {'Tag': tag, 'Visibility': 1}, near, back[way], show=False)
            sheet.signal_wire('get_' + p.name, 1, p.name, port)

    def readings(sheet, sensors):
        """Each sensor (name, tag) sends its reading by tag."""
        for origin, tag in sensors:
            near, way = sheet.beside(origin, 2, 30)
            sheet.block('Goto', 'send_' + tag, {'Tag': tag, 'Visibility': 1}, near, way, show=False)
            sheet.signal_wire(origin, 3, 'send_' + tag, 1)

    def latch(sheet, p, at):
        """The thyristor's gate, as simulink's latch(): on with a gate pulse,
        held while latched and Vak > v_hold; latched once on with Vak above
        v_latch. Memory blocks hold the previous step's state, flag and Vak.
        SignalMux puts its output first, its inputs from terminal 2; a Memory,
        Function or Integrator block has its input at 1 and its output at 2.
        With a turn-off time a limited Integrator is the recovery timer (as in
        simulink's latch()), placed after the Goto in the same row."""
        x, y = at
        n, v = p.name, p.params
        timed = 'tq' in v
        from .spice import TQ_DONE, TQ_STARTED
        x_at = lambda k: x + LATCH_STEP * k
        for name, tag, k in ((n + '_get_gate', p.params['signal'] + '_gate', 0), (n + '_get_vak', n + '_vak', 1)):
            sheet.block('From', name, {'Tag': tag, 'Visibility': 1}, (x_at(k), y), show=False)
        for name, k in ((n + '_vak_previous', 2), (n + '_latch_state', 3), (n + '_latched_state', 4)):
            sheet.block('Memory', name, {'X0': 0}, (x_at(k), y), show=False)
        sheet.block('SignalMux', n + '_latch_inputs', {'Width': 5 if timed else 4}, (x_at(5), y), show=False)
        expr = f"u[1] > 0.5 || (u[2] > 0.5 && u[4] > 0.5 && u[3] > {numeric(v['v_hold'])})"
        if timed:
            expr += f" || (u[2] <= 0.5 && u[5] > {TQ_DONE:g} && u[5] < {TQ_STARTED:g} && u[3] > {numeric(v['vf'])})"
        sheet.block('Function', n + '_latch', {'f(u)': expr}, (x_at(6), y), show=False)
        sheet.block('SignalMux', n + '_latched_inputs', {'Width': 5 if timed else 4}, (x_at(7), y), show=False)
        # the previous Vak counts only if it was on then (as in simulink); a
        # refire (turn-off time not reached) counts as latched
        set_expr = f"(u[4] > 0.5 && u[3] > {numeric(v['v_latch'])})"
        if timed:
            set_expr += f" || (u[4] <= 0.5 && u[5] > {TQ_DONE:g} && u[5] < {TQ_STARTED:g} && u[3] > {numeric(v['vf'])})"
        sheet.block('Function', n + '_latched', {'f(u)': f"u[1] > 0.5 && (u[2] > 0.5 || {set_expr})"},
                    (x_at(8), y), show=False)
        sheet.block('Goto', 'send_' + n + '_latched', {'Tag': n + '_latched', 'Visibility': 1}, (x_at(9), y), show=False)
        below = lambda k: (x_at(k) + 30, y + 20)          # branch points under the row
        sheet.signal_wire(n + '_get_gate', 1, n + '_latch_inputs', 2)
        sheet.signal_wire(n + '_get_vak', 1, n + '_vak_previous', 1)
        sheet.signal_wire(n + '_vak_previous', 2, n + '_latch_inputs', 4, junction=below(2))
        sheet.signal_wire(n + '_vak_previous', 2, n + '_latched_inputs', 4, junction=below(2))
        sheet.signal_wire(n + '_latch_state', 2, n + '_latch_inputs', 3, junction=below(3))
        sheet.signal_wire(n + '_latch_state', 2, n + '_latched_inputs', 5, junction=below(3))
        sheet.signal_wire(n + '_latched_state', 2, n + '_latch_inputs', 5, junction=below(4))
        sheet.signal_wire(n + '_latched_state', 2, n + '_latched_inputs', 3, junction=below(4))
        sheet.signal_wire(n + '_latch_inputs', 1, n + '_latch', 1)
        for target, port in ((n + '_latch_state', 1), (n + '_latched_inputs', 2), ('send_' + n + '_latched', 1)):
            sheet.signal_wire(n + '_latch', 2, target, port, junction=below(6))
        sheet.signal_wire(n + '_latched_inputs', 1, n + '_latched', 1)
        sheet.signal_wire(n + '_latched', 2, n + '_latched_state', 1)
        if timed:
            # +1e9 /s while on (saturates at 1), -1/tq while off and reverse biased
            sheet.block('SignalMux', n + '_recovery_inputs', {'Width': 2}, (x_at(10), y), show=False)
            sheet.block('Function', n + '_recovery_rate',
                        {'f(u)': f"(u[1] > 0.5)*1e9 - (u[1] <= 0.5)*(u[2] < 0)*{numeric(1 / v['tq'])}"}, (x_at(11), y), show=False)
            sheet.block('Integrator', n + '_recovery', {'ExternalReset': 1, 'InitialConditionSource': 1, 'x0': 0,
                                                        'ShowStatePort': 1, 'EnableWrapping': 1, 'UpperLimit': 1,
                                                        'LowerLimit': 0}, (x_at(12), y), show=False)
            sheet.signal_wire(n + '_latch_state', 2, n + '_recovery_inputs', 2, junction=below(3))
            sheet.signal_wire(n + '_vak_previous', 2, n + '_recovery_inputs', 3, junction=below(2))
            sheet.signal_wire(n + '_recovery_inputs', 1, n + '_recovery_rate', 1)
            sheet.signal_wire(n + '_recovery_rate', 2, n + '_recovery', 1)
            sheet.signal_wire(n + '_recovery', 2, n + '_latch_inputs', 6, junction=(x_at(12) + 30, y + 20))
            sheet.signal_wire(n + '_recovery', 2, n + '_latched_inputs', 6, junction=(x_at(12) + 30, y + 20))

    # ---- the top level -------------------------------------------------
    mine, layout, spots = layout_of(None, circuit.drawing)
    mine_names = {p.name for p in mine}
    top = Sheet(None, layout, spots)
    x0, y0, x1, y1 = layout.box
    boxes = {s.name: (layout.px((s.box[0], s.box[3])), layout.px((s.box[2], s.box[1]))) for s in circuit.subsystems}
    # the measurement panel stands right of the drawing; loose rows keep clear of it
    meas_x = x1 + 260
    rows = len(circuit.outputs)
    layout.lower_loose([(meas_x - 20, y0 - 20, meas_x + 140, y0 + max(rows - 1, 0) * ROW + 20),
                        (meas_x + 200, y0 - 20, meas_x + 320, y0 + rows * ROW + 20)], 20, SIGNAL_REACH)
    lowest = max([y1] + [s.pos[1] + 40 for s in spots.values()] + [b[1][1] for b in boxes.values()])
    loose_y = lowest + 40
    devices(top, mine)
    # a sensor's reading travels under the tag of the first output it serves;
    # a subsystem's come out through its signal outputs, sent on by a Goto
    # at the terminal, so the measurement panel reads every tag on this sheet
    tag_of = {}
    for output in circuit.outputs:
        if output['origin'] in by_name:
            tag_of.setdefault(output['origin'], 'meas_' + output['name'])
    for s in circuit.subsystems:
        subsystem(s, top, boxes[s.name], layout_of, Sheet, devices, tags, wires, loose_ground, drives, readings, tag_of, quoted)
    tags(top)
    if not wires(top, 'ground', list(boxes.values())):
        loose_ground(top, 'ground', (x0, loose_y + 90))

    # Signals: generators in a drive panel under the drawing, sent by Goto
    # tags; each gate / source input / sensor output has its tag block beside it.
    drive_y = (loose_y + 160) if layout.loose else loose_y
    # a closed-loop gate (kind control) is made in the control panel below
    gens = [s for s in circuit.signals if s.kind != 'control']
    gates = {p.params['signal']: p.name for p in parts if p.kind in GATED}
    clock = any(s.kind != 'dc' for s in gens)
    if clock:
        top.block('Clock', 'time', {}, (x0, drive_y), show=False)
    for row, s in enumerate(gens):
        pos = (x0 + 120, drive_y + row * ROW)
        v = s.params
        if s.kind == 'dc':
            top.block('Constant', s.name, {'Value': v['dc']}, pos)
        elif s.kind == 'pwl':
            vector = lambda values: '[' + ' '.join(numeric(x) for x in values) + ']'
            # Lookup1D extrapolates its final slope. PWL sources (including
            # closed-loop reference ramps) hold their last value instead.
            points = list(s.points)
            if points[-1][0] < circuit.transient['stop']:
                points.append((circuit.transient['stop'], points[-1][1]))
            top.block('Lookup1D', s.name, {'x': vector(t for t, y in points),
                                           'f(x)': vector(y for t, y in points), 'ZeroCrossingSignals': 2}, pos)
        else:
            expression = (f"{v['offset']:.17g}+{v['amplitude']:.17g}*sin(2*pi*{v['frequency']:.17g}*"
                          f"max(u[1]-{v['delay']:.17g},0)+{v['phase']:.17g}*pi/180)")
            top.block('Function', s.name, {'f(u)': expression}, pos)
        out = 1 if s.kind == 'dc' else 2
        if s.kind != 'dc':
            top.signal_wire('time', 1, s.name, 1, [(x0 + 40, drive_y), (x0 + 40, pos[1])] if row else None,
                            junction=(x0 + 40, drive_y))
        top.block('Goto', 'send_' + s.name, {'Tag': s.name, 'Visibility': 1}, (pos[0] + 90, pos[1]), show=False)
        top.signal_wire(s.name, out, 'send_' + s.name, 1, junction=(pos[0] + 40, pos[1]))
        if s.name in gates:
            # the gate switches at the 50 % threshold, as the SPICE switch does;
            # a drive probe still reads the PWM waveform itself
            gate_name = gates[s.name] + '_gate'
            top.block('ConstantRelationalOperator', gate_name, {'Operator': 6, 'Threshold': .5}, (pos[0] + 180, pos[1]), show=False)
            # Go below the measurement tag instead of through its symbol.
            top.signal_wire(s.name, out, gate_name, 1,
                            [(pos[0] + 40, pos[1] + ROW // 2),
                             (pos[0] + 140, pos[1] + ROW // 2), (pos[0] + 140, pos[1])])
            top.block('Goto', 'send_' + s.name + '_gate', {'Tag': s.name + '_gate', 'Visibility': 1},
                      (pos[0] + 270, pos[1]), show=False)
            top.signal_wire(gate_name, 2, 'send_' + s.name + '_gate', 1)
        for scr in [name for name, signal in latched.items() if signal == s.name]:
            latch(top, by_name[scr], (pos[0] + 440, pos[1]))
    drives(top, mine)
    readings(top, [(name + '_vak', name + '_vak') for name in latched])

    # Measurements: each sensor sends its reading by tag; the measurement
    # panel reads it into an output port and the scope.
    terminals, axes = [], []
    sent, measurement_tags = set(), {}
    for i, output in enumerate(circuit.outputs, 1):
        origin = output['origin']
        if origin in by_name:
            tag = tag_of[origin]
            if origin not in sent and origin in mine_names:
                sent.add(origin)
                readings(top, [(origin, tag)])
        else:
            tag = origin                     # a drive signal: its generator's tag
        measurement_tags[output['name']] = tag
        y = y0 + (i - 1) * ROW
        out = 'out_' + output['name']
        top.block('From', 'read_' + output['name'], {'Tag': tag, 'Visibility': 1}, (meas_x, y), show=False)
        top.block('Output', out, {'Index': i, 'Width': 1}, (meas_x + 120, y))
        terminals.append(f'Terminal {{ Type Output Index "{i}" }}')
        top.signals['read_' + output['name'], 1].append(f'Points [{meas_x + 40},{y}]\n'
                       f'Branch {{ DstComponent {quoted(out)} DstTerminal 1 }}\n'
                       f'Branch {{ Points [{meas_x + 40},{y + ROW // 2}; {meas_x + 200},{y + ROW // 2}] DstComponent "Scope" DstTerminal {i} }}')
        axes.append(f'Axis {{ Name {quoted(output["name"])} AutoScale 1 MinValue 0 MaxValue 1 Signals {{}} AxisLabel {quoted(output["name"])} }}')
    top.block('Scope', 'Scope', {}, (meas_x + 260, y0 + len(axes) * ROW // 2),
              extra=f'Axes "{len(axes)}"\nOpen "0"\nTs "-1"\nXAxisLabel "Time / s"\n' + '\n'.join(axes))
    panel = getattr(circuit, 'panel', None)
    if panel:
        _control_panel(circuit, panel, (x0, drive_y + max(len(gens), 1) * ROW + 40), top.block, top.signal_wire,
                       measurement_tags)
    tr = circuit.transient
    return (f'Plecs {{\nName {quoted(name)}\nVersion "4.9"\nCircuitModel "ContStateSpace"\n'
            f'StartTime "0"\nTimeSpan "{tr["stop"]:.17g}"\nSolver "radau"\nMaxStep "{tr["max_step"]:.17g}"\n'
            'RelTol "1e-5"\nAbsTol "1e-9"\nExtendedMatrixPrecision "2"\nInitialState "1"\n' + '\n'.join(terminals) +
            '\n' + top.text() + '\n}\n')


# A subsystem's own terminal offsets are written in its Terminal list; the
# planner needs none.  Inside, an Electrical Port block stands ``PORT_GAP``
# px outside the port's pin (clear of a series current sensor on the lead),
# turned toward it; its terminal is assumed ``PORT_REACH`` px along its
# direction, as for the other one-terminal blocks, and a wire runs to the pin.
PORT_REACH = 10
PORT_GAP = 60
# Native screenshots showed signal tags overlapping electrical Port blocks.
# Outputs get a separate right column; inputs go below the drawing because
# moving their column farther left would put it outside PLECS's canvas.
SUBSYSTEM_SIGNAL_MARGIN = 4 * PORT_GAP
SUBSYSTEM_FIELDS = 'SampleTime "-1"\nCodeGenTarget "Generic"\nMaskIconFrame on\nMaskIconOpaque off\nMaskIconRotates on\n'


def subsystem(s, top, box, layout_of, Sheet, devices, tags, wires, loose_ground, drives, readings, tag_of, quoted):
    """One Subsystem component on the top sheet with its nested schematic:
    electrical ports on the box edges where the drawing has them, drive
    signals in through Input blocks on the top edge, readings out through
    Output blocks on the bottom edge under their tags (``tag_of``: sensor ->
    tag).  The Terminal list, and the index of every port block inside, run
    electrical ports, inputs, outputs; a connection names a terminal by its
    place in that list."""
    from .export_layout import DIRS
    mine, layout, spots = layout_of(s.name, s.drawing)
    sheet = Sheet(s.name, layout, spots)
    (bx0, by0), (bx1, by1) = box
    cx, cy = (bx0 + bx1) // 2, (by0 + by1) // 2
    width, height = bx1 - bx0, by1 - by0
    terminal_list, port_blocks = [], []
    # electrical ports: on the top sheet the subsystem's terminal sits on the
    # drawn port pin; inside, a Port block ends the module's own wire there
    for port in s.ports:
        px, py = top.layout.pins[port['pin']]
        k = len(terminal_list) + 1
        terminal_list.append(f'Terminal {{ Type Port Position [{px - cx},{py - cy}] Direction {port["side"]} }}')
        top.terminal_xy[s.name, k - 1] = (px, py)
        top.edges[port['net']].add((('T', s.name, k - 1), (px, py)))
        ix, iy = layout.pins[port['pin']]
        way = 'right' if port['side'] == 'left' else 'left'
        dx, dy = DIRS[way]
        block_name = f'{s.name}_port_{port["name"]}'
        port_blocks.append(('Port', block_name, {'Index': k}, (ix - dx * PORT_GAP, iy - dy * PORT_GAP), way))
        sheet.terminal_xy[block_name, 0] = (ix - dx * (PORT_GAP - PORT_REACH), iy - dy * (PORT_GAP - PORT_REACH))
        sheet.edges[port['net']].add((('T', block_name, 0), (ix, iy)))
    # drive signals: one Input per tag the parts inside read, a local Goto
    # beside it so each part keeps its From block; the top sheet feeds the
    # terminal from its own From of the same tag
    inputs = sorted({p.params['signal'] + ('_gate' if p.kind in GATED else '')
                     for p in mine if p.kind in (*GATED, 'V', 'I')})
    ix0, iy0, ix1, iy1 = layout.box
    input_y = max([iy1, *(spot.pos[1] for spot in spots.values())]) + 2 * ROW
    step_in = width // (len(inputs) + 1) if inputs else 0
    for i, tag in enumerate(inputs):
        k = len(terminal_list) + 1
        tx, ty = bx0 + step_in * (i + 1), by0
        terminal_list.append(f'Terminal {{ Type Input Position [{tx - cx},{ty - cy}] Direction up }}')
        top.block('From', f'get_{s.name}_{tag}', {'Tag': tag, 'Visibility': 1}, (tx, ty - 40), 'down', show=False)
        top.signal_wire(f'get_{s.name}_{tag}', 1, s.name, k)
        pos = (ix0, input_y + i * ROW)
        port_blocks.append(('Input', f'{s.name}_in_{tag}', {'Index': k, 'Width': 1}, pos, 'right'))
        sheet.block('Goto', f'send_{s.name}_{tag}', {'Tag': tag, 'Visibility': 1}, (pos[0] + 60, pos[1]), show=False)
        sheet.signal_wire(f'{s.name}_in_{tag}', 1, f'send_{s.name}_{tag}', 1)
    # readings: the sensors inside send their tag as on any sheet; an Output
    # block reads it out, and a Goto at the terminal sends it on up here
    sensors = sorted(p.name for p in mine if p.name in tag_of)
    step_out = width // (len(sensors) + 1) if sensors else 0
    for i, sensor in enumerate(sensors):
        k, tag = len(terminal_list) + 1, tag_of[sensor]
        tx, ty = bx0 + step_out * (i + 1), by1
        terminal_list.append(f'Terminal {{ Type Output Position [{tx - cx},{ty - cy + 4}] Direction down }}')
        top.block('Goto', f'send_{s.name}_{tag}', {'Tag': tag, 'Visibility': 1}, (tx, ty + 40), 'down', show=False)
        top.signal_wire(s.name, k, f'send_{s.name}_{tag}', 1)
        pos = (ix1 + SUBSYSTEM_SIGNAL_MARGIN, iy0 + i * ROW)
        port_blocks.append(('Output', f'{s.name}_out_{sensor}', {'Index': k, 'Width': 1}, pos, 'right'))
        sheet.block('From', f'read_{s.name}_{sensor}', {'Tag': tag, 'Visibility': 1}, (pos[0] - 60, pos[1]), show=False)
        sheet.signal_wire(f'read_{s.name}_{sensor}', 1, f'{s.name}_out_{sensor}', 1)
    for kind, block_name, params, pos, way in port_blocks:
        sheet.block(kind, block_name, params, pos, way)
    devices(sheet, mine)
    tags(sheet)
    if not wires(sheet, f'ground_{s.name}') and any(nodes == '0' for p in mine for nodes in p.nodes):
        loose_ground(sheet, f'ground_{s.name}', (ix0, iy1 + 120))
    drives(sheet, mine)
    readings(sheet, [(sensor, tag_of[sensor]) for sensor in sensors])
    top.components.append(f'Component {{\nType Subsystem\nName {quoted(s.name)}\nShow on\nPosition [{cx},{cy}]\n'
                          f'Direction up\nFlipped off\nLabelPosition south\n'
                          f'Frame [{bx0 - cx},{by0 - cy};{bx1 - cx},{by1 - cy}]\n{SUBSYSTEM_FIELDS}'
                          + '\n'.join(terminal_list) + '\n' + sheet.text() + '\n}')


def control_terminals(b):
    """([input terminals], [output terminals]) of a control_export block, each
    a (component, terminal number) as _control_panel writes it.  PLECS numbers
    a block's terminals together, inputs and outputs in the order the library
    defines; a block that needs two components (a discrete limited PI, a
    function of several inputs) spreads its ports over both."""
    name, n = b.name, b.inputs
    if b.kind in ('input', 'const', 'carrier'):
        return [], [(name, 1)]
    if b.kind in ('sum', 'product', 'mux'):
        # Native 4.9.5 Sum / Product / SignalMux put the output first;
        # inputs start at 2 (plecs-acceptance-74/math-ports-native.plecs).
        return [(name, i) for i in range(2, n + 2)], [(name, 1)]
    if b.kind == 'demux':
        return [(name, 1)], [(name, i + 1) for i in range(1, b.outputs + 1)]
    if b.kind == 'compare':
        return [(name, 1), (name, 2)], [(name, 3)]
    if b.kind == 'step':
        return [], [(name, 2)]
    if b.kind in ('abc2dq', 'dq2abc'):
        return [(name, 1), (name, 3)], [(name, 2)]
    if b.kind == 'dpi':
        return [(name, 1)], [(name + '_sat', 2)]
    if b.kind in ('fcn', 'minmax') and n > 1:
        return [(name + '_mux', i) for i in range(2, n + 2)], [(name, 2)]
    return [(name, 1)], [(name, 2)]       # gain, tf, dtf, pi, sat, delay, a one-input function


def _control_panel(circuit, panel, origin, block, signal_wire, measurement_tags):
    """The closed-loop controller under the drive panel.  Every block of the
    panel becomes one component (two where the library has no single block:
    a discrete limited PI, a function of several inputs); ``control_terminals``
    gives the terminal of each neutral port."""
    if 'dpi' in panel.kinds():
        circuit.notes.append('PLECS：离散限幅 PI 用离散传递函数加 Saturation 实现，不含抗饱和（离散 PID 库块未实测）。')
    ox, oy = origin
    vec = lambda values: '[' + ' '.join(f'{float(v):.17g}' for v in values) + ']'
    inf = lambda v, sign: 'inf' if v is None and sign > 0 else '-inf' if v is None else v
    ports = {b.name: control_terminals(b) for b in panel.blocks}

    def reference(name, library, frame, terminals, params, pos):
        extra = '\n'.join(f'Terminal {{ Type {kind} Position [{x},{y}] Direction {way} }}' for kind, (x, y), way in terminals)
        block('Reference', name, params, pos, 'up', extra=extra, library=library, frame=frame)

    def function(b, expr, pos):
        """A Function block over u[1..n]; a vector input from a Mux when n > 1."""
        u = {f'u{i}': f'u[{i}]' for i in range(1, b.inputs + 1)}
        block('Function', b.name, {'f(u)': expr.format(**u)}, pos)
        if b.inputs > 1:
            mux = b.name + '_mux'
            block('SignalMux', mux, {'Width': b.inputs}, (pos[0] - 60, pos[1]))
            signal_wire(mux, 1, b.name, 1)

    for b in panel.blocks:
        pos = (ox + b.pos[0], oy + b.pos[1])
        p, name = b.params, b.name
        if b.kind == 'input':
            tag = measurement_tags[p['ref']] if p['source'] == 'probe' else p['ref']
            block('From', name, {'Tag': tag, 'Visibility': 1}, pos, show=False)
        elif b.kind == 'const':
            block('Constant', name, {'Value': p['value']}, pos)
        elif b.kind == 'sum':
            block('Sum', name, {'IconShape': 2, 'Inputs': p['signs']}, pos)
        elif b.kind == 'gain':
            block('Gain', name, {'K': p['k'], 'Multiplication': 1}, pos)
        elif b.kind == 'product':
            block('Product', name, {'IconShape': 2, 'Inputs': p['ops']}, pos)
        elif b.kind == 'tf':
            block('TransferFunction', name, {'Numerator': vec(p['num']), 'Denominator': vec(p['den']), 'X0': 0}, pos)
        elif b.kind == 'dtf':
            block('DiscreteTransferFunction', name, {'Numerator': vec(p['num']), 'Denominator': vec(p['den']),
                                                     'Ts': p['ts'], 'X0': 0}, pos)
        elif b.kind == 'pi':
            # the native sample's parameter set: PI (cont_type 3), internal
            # parameters and limits, conditional-integration anti-windup
            params = {'cont_type': 3, 'par_source': 1, 'kp': p['kp'], 'ki': p['ki'], 'kd': 1, 'kf': 1,
                      'ex_reset': 1, 'x0_source': 1, 'x0': 0, 'ex_sat': 1, 'sat_lim': 1,
                      'up_limit': inf(p['hi'], 1), 'low_limit': inf(p['lo'], -1), 'aw_method': 3, 'kbc': 1}
            reference(name, PID_LIBRARY, PID_FRAME, PID_TERMINALS, params, pos)
        elif b.kind == 'dpi':
            # y = kp e + I, I += ki Ts (c0 e[k] + c1 e[k-1]) / (ki Ts), then the limit
            ts, kp, ki = p['ts'], p['kp'], p['ki']
            c0, c1 = {'tustin': (0.5, 0.5), 'backward': (1.0, 0.0), 'forward': (0.0, 1.0),
                      'zoh': (0.0, 1.0)}[p['method']]
            block('DiscreteTransferFunction', name, {'Numerator': vec([kp + ki * ts * c0, ki * ts * c1 - kp]),
                                                     'Denominator': vec([1, -1]), 'Ts': ts, 'X0': 0}, pos)
            block('Saturation', name + '_sat', {'UpperLimit': inf(p['hi'], 1), 'LowerLimit': inf(p['lo'], -1)},
                  (pos[0] + 80, pos[1]))
            signal_wire(name, 2, name + '_sat', 1)
        elif b.kind == 'sat':
            block('Saturation', name, {'UpperLimit': inf(p['hi'], 1), 'LowerLimit': inf(p['lo'], -1)}, pos)
        elif b.kind == 'delay':
            block('DiscreteTransferFunction', name, {'Numerator': '[1]', 'Denominator': '[1 0]', 'Ts': p['ts'], 'X0': 0}, pos)
        elif b.kind == 'carrier':
            block('TriangleGenerator', name, {'Min': 0, 'Max': 1, 'f': p['f'],
                                              'DutyCycle': 1 if p['shape'] == 'saw' else 0.5, 'Delay': p['delay']}, pos)
        elif b.kind == 'compare':
            block('RelationalOperator', name, {'Operator': GREATER}, pos)
        elif b.kind == 'step':
            block('Clock', name + '_clock', {}, (pos[0] - 60, pos[1]), show=False)
            block('ConstantRelationalOperator', name, {'Operator': GREATER, 'Threshold': p['time']}, pos, show=False)
            signal_wire(name + '_clock', 1, name, 1)
        elif b.kind == 'mux':
            block('SignalMux', name, {'Width': b.inputs}, pos)
        elif b.kind == 'demux':
            block('SignalDemux', name, {'Width': b.outputs}, pos)
        elif b.kind in ('abc2dq', 'dq2abc'):
            reference(name, TRANSFORM_LIBRARY[b.kind], TRANSFORM_FRAME, TRANSFORM_TERMINALS, {}, pos)
        elif b.kind == 'minmax':
            inner = '{u1}'
            for i in range(2, b.inputs + 1):
                inner = f"{p['op']}({inner}, {{u{i}}})"
            function(b, inner, pos)
        elif b.kind == 'fcn':
            function(b, p['expr'], pos)
        else:
            raise SpiceError(f'PLECS：无法导出的控制方块 {b.kind}')
    for lk in panel.links:
        (a, pa), (c, pc) = ports[lk.src[0]][1][lk.src[1] - 1], ports[lk.dst[0]][0][lk.dst[1] - 1]
        x, y = ox + panel.block(lk.src[0]).pos[0], oy + panel.block(lk.src[0]).pos[1]
        signal_wire(a, pa, c, pc, junction=(x + 50, y))
    # the gates: the switch reads <signal>_gate, a drive probe reads <signal>
    for signal, (src, port) in panel.exports.items():
        a, pa = ports[src][1][port - 1]
        x, y = ox + panel.block(src).pos[0], oy + panel.block(src).pos[1]
        for suffix, dy in (('', -15), ('_gate', 15)):
            block('Goto', 'send_' + signal + suffix, {'Tag': signal + suffix, 'Visibility': 1}, (x + 90, y + dy), show=False)
            signal_wire(a, pa, 'send_' + signal + suffix, 1, junction=(x + 50, y))


def _is_terminal(node) -> bool:
    return isinstance(node[0], str) and node[0] == 'T'


def _tree(graph, piece, coord, quoted):
    """PLECS wire trees for one connected piece of a net: a trunk from one
    terminal, branching where the drawing branches.  Cycles are cut and wire
    ends that reach no terminal are dropped."""
    terminals = [n for n in piece if _is_terminal(n)]
    if len(terminals) < 2:
        return []
    root = min(terminals, key=lambda n: (len(graph[n]) != 1, str(n)))
    parent, order, stack = {root: None}, [], [root]
    while stack:
        n = stack.pop()
        order.append(n)
        for m in sorted(graph[n], key=str):
            if m not in parent:
                parent[m] = n
                stack.append(m)
    children = defaultdict(list)
    for n in order[1:]:
        children[parent[n]].append(n)
    useful = {}
    for n in reversed(order):
        useful[n] = _is_terminal(n) or any(useful[c] for c in children[n])

    def points(pts, start, end=None):
        """Without repeats, the start/end themselves, or corners on a straight run."""
        out = []
        for p in pts:
            if p != (out[-1] if out else start):
                out.append(p)
        if end is not None and out and out[-1] == end:
            out.pop()
        full = [start, *out, *([end] if end is not None else [])]
        kept = [p for i, p in enumerate(out, 1)
                if i + 1 >= len(full) or not (full[i - 1][0] == p[0] == full[i + 1][0]
                                              or full[i - 1][1] == p[1] == full[i + 1][1])]
        return ('Points [' + '; '.join(f'{x},{y}' for x, y in kept) + ']\n') if kept else ''

    def walk(n, start):
        pts, here = [], n
        while True:
            kids = [c for c in children[here] if useful[c]]
            if _is_terminal(here) and not kids:
                return points(pts, start, coord(here)) + f'DstComponent {quoted(here[1])} DstTerminal {here[2] + 1}'
            pts.append(coord(here))
            if len(kids) == 1 and not _is_terminal(here):
                here = kids[0]
                continue
            junction = coord(here)
            branches = [walk(k, junction) for k in kids]
            if _is_terminal(here):
                branches.insert(0, f'DstComponent {quoted(here[1])} DstTerminal {here[2] + 1}')
            text = points(pts, start)
            if not text:
                text = f'Points [{junction[0]},{junction[1]}]\n'
            return text + '\n'.join('Branch {\n' + b + '\n}' for b in branches)

    kids = [c for c in children[root] if useful[c]]
    head = f'Connection {{\nType Wire\nSrcComponent {quoted(root[1])}\nSrcTerminal {root[2] + 1}\n'
    if len(kids) == 1:
        return [head + walk(kids[0], coord(root)) + '\n}']
    start = coord(root)
    return [head + f'Points [{start[0]},{start[1]}]\n' +
            '\n'.join('Branch {\n' + walk(k, start) + '\n}' for k in kids) + '\n}']
