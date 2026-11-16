"""Deterministic, explicit control-diagram placement (never a render side effect)."""
from __future__ import annotations

from collections import defaultdict

# Physical text clearance plus a routing lane, in table units. The existing
# checker performs the final scaled-page clearance test.
COLUMN_GAP = 0.9
LANE_GAP = 0.6


def place(blocks, structure, prof, spacing=1.0):
    """Place a directed forest in columns; keep return/auxiliary paths in lanes.

    A non-west input is not a forward rank constraint. A subtracting west input
    still belongs to the forward signal chain.
    Remaining graph cycles are cut deterministically for geometry only; every
    logical edge is retained and routed by the normal compiler.
    """
    from copy import deepcopy
    from .check import AUTO_ROUTE_MIN_RUN, BLOCK_CLEARANCE, SIDE_DIR, check, geometry, port_point
    from .ai_structure import endpoint
    from .model import parse
    by_id = {b['id']: b for b in blocks}
    adj = {bid: [] for bid in by_id}
    for edge in sorted(structure['links'], key=lambda e: (e['from'], e['to'])):
        src, dst = edge['from'].split('.', 1)[0], edge['to'].split('.', 1)[0]
        port = edge['to'].split('.', 1)[1][0]
        if port == 'w':
            adj[src].append(dst)
    # DFS back edges are removed from rank constraints, not from the document.
    colour, dag = {}, {bid: [] for bid in by_id}
    def visit(node):
        colour[node] = 1
        for nxt in sorted(set(adj[node])):
            if colour.get(nxt) == 1:
                continue
            if not colour.get(nxt):
                visit(nxt)
            dag[node].append(nxt)
        colour[node] = 2
    for node in sorted(by_id):
        if not colour.get(node):
            visit(node)
    parents = defaultdict(list)
    for node in dag:
        for nxt in dag[node]:
            parents[nxt].append(node)
    depth, span = {}, {}
    def rank(node):
        if node not in depth:
            depth[node] = max((rank(p) + 1 for p in parents[node]), default=0)
        return depth[node]
    def length(node):
        if node not in span:
            span[node] = 1 + max((length(n) for n in dag[node]), default=0)
        return span[node]
    for node in by_id:
        rank(node); length(node)
    roots = sorted((n for n in by_id if not parents[n]), key=lambda n: (-span[n], n))
    lanes = {}
    used = 0
    def assign(node, lane):
        nonlocal used
        if node in lanes:
            return
        lanes[node] = lane
        children = sorted(dag[node], key=lambda n: (-span[n], n))
        for i, nxt in enumerate(children):
            if i:
                used += 1
            assign(nxt, lane if i == 0 else used)
    for root in roots:
        if lanes:
            used += 1
        assign(root, used)
    # Place single-node side-input chains relative to their consumer. Iterate
    # from consumers outward; this also handles vertical two-stage sensors.
    auxiliary = {}
    side_chains = []
    for root in sorted(by_id):
        if dag[root]:
            continue
        targets = [(e['to'].split('.', 1)[0], e['to'].split('.', 1)[1][0])
                   for e in structure['links'] if e['from'].split('.', 1)[0] == root]
        if root in roots and len(targets) == 1 and targets[0][0] != root:
            auxiliary[root] = targets[0]
        elif len(targets) > 1 and len({(depth[dst], port) for dst, port in targets}) == 1 and targets[0][1] in ('n', 's'):
            # A shared side input belongs above/below its consumer column.
            # Leaving it among unrelated roots creates a return across the whole diagram.
            sign = 1 if targets[0][1] == 'n' else -1
            auxiliary[root] = min(targets, key=lambda target: (sign * lanes[target[0]], target[0]))
            chain, previous = [], root
            while len(parents[previous]) == 1:
                parent = parents[previous][0]
                if len(dag[parent]) != 1:
                    break
                chain.append(parent)
                previous = parent
            if chain:
                side_chains.append((root, chain, depth[root], lanes[root]))
    incoming = defaultdict(list)
    for edge in structure['links']:
        src, dst = edge['from'].split('.', 1)[0], edge['to'].split('.', 1)[0]
        incoming[dst].append(src)
    # A drawn return path can contain a plant and sensors. Anchor its two ends
    # to the forward chain, rather than stacking every block under the summing point.
    returns = []
    return_nodes = set()
    for node, (consumer, port) in sorted(auxiliary.items()):
        if consumer in auxiliary or port not in ('n', 's'):
            continue
        if sum(edge['from'].split('.', 1)[0] == node for edge in structure['links']) != 1:
            continue
        chain, source = [], node
        while source in auxiliary and source not in chain and len(incoming[source]) == 1:
            chain.append(source)
            source = incoming[source][0]
        if source in auxiliary or not chain or abs(depth[source] - depth[consumer]) < len(chain) - 1:
            continue
        returns.append((chain, source, consumer, port))
        return_nodes.update(chain)
    placed = set(return_nodes)
    def align(node, visiting):
        if node in placed or node not in auxiliary or node in visiting:
            return
        dst, port = auxiliary[node]
        align(dst, visiting | {node})
        depth[node] = depth[dst]
        lanes[node] = lanes[dst] + (-1 if port == 'n' else 1)
        placed.add(node)
    for node in sorted(auxiliary):
        align(node, set())
    for tip, chain, old_depth, old_lane in side_chains:
        for node in chain:
            depth[node] += depth[tip] - old_depth
            lanes[node] += lanes[tip] - old_lane
    for chain, source, consumer, port in returns:
        direction = -1 if port == 'n' else 1
        left, right = sorted((depth[source], depth[consumer]))
        occupied = [lanes[bid] for bid in by_id if bid not in return_nodes and left <= depth[bid] <= right]
        lane = (min(occupied) - 1) if direction < 0 else (max(occupied) + 1)
        for i, node in enumerate(chain):
            depth[node] = round(depth[consumer] + (depth[source] - depth[consumer]) * i / max(1, len(chain) - 1))
            lanes[node] = lane
    # Use the same body/label bounds as checking and rendering. Compact rows
    # need clearance around their own contents, not the tallest block in the diagram.
    shapes = deepcopy(blocks)
    for block in shapes:
        if block['type'] != 'switch':
            block.setdefault('at', [0, 0])
        else:
            block.setdefault('pole', [0, 0])
    geom = geometry(parse({'kind': 'control', 'name': 'placement', 'blocks': shapes, 'links': []}), 1.0, prof)
    extents = {bid: [box.x0, box.y0, box.x1, box.y1] for bid, box in geom.bodies.items()}
    for label in geom.labels:
        bid = label.owner.split()[0]
        if bid in extents:
            box, bounds = label.box, extents[bid]
            bounds[:] = [min(bounds[0], box.x0), min(bounds[1], box.y0), max(bounds[2], box.x1), max(bounds[3], box.y1)]
    widths, above, below = defaultdict(float), defaultdict(float), defaultdict(float)
    for bid, b in by_id.items():
        x, y = b.get('at', b.get('pole', [0, 0]))
        x0, y0, x1, y1 = extents[bid]
        widths[depth[bid]] = max(widths[depth[bid]], 2 * max(x - x0, x1 - x))
        above[lanes[bid]] = max(above[lanes[bid]], y1 - y)
        below[lanes[bid]] = max(below[lanes[bid]], y - y0)
    xs, x = {}, 0.0
    for col in range(min(depth.values(), default=0), max(depth.values(), default=0)+1):
        w = widths[col]
        xs[col] = x + w/2
        x += w + COLUMN_GAP*spacing
    ys, previous = {}, None
    for lane in sorted(above):
        ys[lane] = 0.0 if previous is None else ys[previous] - (below[previous] + LANE_GAP + above[lane])*spacing
        previous = lane
    for bid, b in by_id.items():
        pos = [round(xs[depth[bid]], 6), round(ys[lanes[bid]], 6)]
        if b['type'] == 'switch':
            old = b.get('pole', [0, 0])
            b['throws'] = {k: [v[0]-old[0]+pos[0], v[1]-old[1]+pos[1]] for k,v in b['throws'].items()}
            b['pole'] = pos
        else:
            b['at'] = pos
    # Align the return run with the receiving port. A side-exit measurement box
    # sits beside that run, leaving a full readable segment before the single bend.
    positioned = {'kind': 'control', 'name': 'placement', 'blocks': blocks, 'links': []}
    scale = check(positioned, prof).geometry.s
    diagram = parse(positioned)
    lead = (AUTO_ROUTE_MIN_RUN + BLOCK_CLEARANCE / 2) / scale
    for chain, _, consumer, _ in returns:
        node = chain[0]
        edge = next(edge for edge in structure['links']
                    if endpoint(edge['from'])[0] == node and endpoint(edge['to'])[0] == consumer)
        _, side, offset = endpoint(edge['from'])
        _, target_side, target_offset = endpoint(edge['to'])
        if side not in ('w', 'e'):
            continue
        source = port_point(diagram.block(node), side, offset, scale)
        target = port_point(diagram.block(consumer), target_side, target_offset, scale)
        by_id[node]['at'][0] = round(by_id[node]['at'][0] + target[0] - source[0] - SIDE_DIR[side][0] * lead, 6)


def candidate(raw, profile='double', mode='layout'):
    """Return a checked candidate, without saving or mutating the source table."""
    from copy import deepcopy
    from .ai_structure import compile_structure, to_structure, StructureError
    from .check import check
    from ..profiles import get_profile
    if mode not in ('layout', 'routes'):
        raise ValueError('整理方式须为 layout 或 routes')
    if not raw.get('blocks'):
        raise ValueError('当前没有可整理的方块')
    structure = to_structure(raw, profile)
    failures = []
    # Three bounded local attempts avoid sending geometric failures back to AI.
    for spacing in ((1.0, 1.35, 1.8) if mode == 'layout' else (1.0,)):
        try:
            result = compile_structure(structure, deepcopy(raw), profile,
                                       arrange=mode == 'layout', reroute=True, spacing=spacing)
            if 'control_ai' not in raw:
                result.pop('control_ai', None)
            else:
                result['control_ai'] = deepcopy(raw['control_ai'])
            rep = check(result, get_profile(profile))
            if rep.errors:
                failures = list(rep.errors)
                continue
            before, after = to_structure(raw, profile), to_structure(result, profile)
            def signature(s):
                s = deepcopy(s)
                s['blocks'] = sorted(s['blocks'], key=lambda b: b['id'])
                s['links'] = sorted(s['links'], key=lambda l: (l['from'], l['to']))
                return s
            if signature(before) != signature(after):
                raise ValueError('整理结果改变了控制语义；当前图保持原样')
            return result
        except StructureError as exc:
            failures = [str(exc)]
    raise ValueError('无法完成整理；当前图保持原样：' + '; '.join(failures))
