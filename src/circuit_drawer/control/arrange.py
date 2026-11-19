"""Deterministic, explicit control-diagram placement (never a render side effect)."""
from __future__ import annotations

from collections import defaultdict

# Physical text clearance plus a routing lane, in table units. The existing
# checker performs the final scaled-page clearance test.
COLUMN_GAP = 0.9
LANE_GAP = 0.6
# A compact trial can keep fixed-size type legible on a double-column page.
LAYOUT_SPACING = (1.0, 0.8, 1.35, 1.8)
# Facing ports on parallel rows normally need at most one orthogonal offset.
PREFERRED_ARROW_BENDS = 2


def place(blocks, structure, prof, spacing=1.0, *, fixed_ports=()):
    """Place a directed forest in columns; keep return/auxiliary paths in lanes.

    A non-west input is not a forward rank constraint. A subtracting west input
    still belongs to the forward signal chain.
    Remaining graph cycles are cut deterministically for geometry only; every
    logical edge is retained and routed by the normal compiler.
    """
    if structure.get("algorithm"):
        from .algorithm import place as place_algorithm
        place_algorithm(blocks, spacing)
        return
    from copy import deepcopy
    from .check import AUTO_ROUTE_MIN_RUN, BLOCK_CLEARANCE, SIDE_DIR, check, geometry, port_point
    from .ai_structure import REGION_PAD, endpoint
    from .model import parse
    by_id = {b['id']: b for b in blocks}
    adj = {bid: [] for bid in by_id}
    incoming_count = defaultdict(int)
    for edge in structure['links']:
        incoming_count[endpoint(edge['to'])[0]] += 1
    for edge in sorted(structure['links'], key=lambda e: (e['from'], e['to'])):
        src, dst = edge['from'].split('.', 1)[0], edge['to'].split('.', 1)[0]
        port = edge['to'].split('.', 1)[1][0]
        if port == 'w' or (incoming_count[dst] == 1 and endpoint(edge['from'])[1] == 'e'
                           and by_id[dst]['type'] == 'block' and port in ('n', 's')):
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
    # A feed-forward preprocessing chain may drive several side ports on the
    # main path. Place the whole chain on the supply side, near its consumers.
    # Leave the opposite lane free for feedback when the receiving ports disagree.
    for block in blocks:
        if block['id'] not in fixed_ports:
            block.pop('port_sides', None)  # full placement recomputes the printed sides
    used_ports = defaultdict(set)
    for edge in structure['links']:
        for key in ('from','to'):
            bid, side, _ = endpoint(edge[key])
            used_ports[bid].add(side)
    for block in blocks:
        used_ports[block['id']].update(key[0] for key in block.get('port_labels', {}))
        used_ports[block['id']].update(block.get('signs', {}))

    def face_port(bid, name, side):
        block = by_id[bid]
        if bid in fixed_ports or block['type'] in ('switch', 'compare') or name not in SIDE_DIR:
            return False
        mapping = block.get('port_sides', {})
        old = mapping.get(name, name)
        occupant = next(n for n in SIDE_DIR if mapping.get(n, n) == side)
        if old == side:
            return True
        if occupant in used_ports[bid]:
            return False
        # Exchanging with an unused side keeps the logical interface one-to-one.
        mapping = dict(mapping)
        mapping[name], mapping[occupant] = side, old
        block['port_sides'] = {n: v for n, v in mapping.items() if n != v}
        return True

    _place_feeders(by_id, structure, parents, dag, depth, lanes, face_port)
    # For a forward transfer or a feedback return on the same row, turn an
    # available port toward the route. Signs and transfer inputs keep their names.
    for edge in structure['links']:
        src, exit_side, _ = endpoint(edge['from'])
        dst, enter_side, _ = endpoint(edge['to'])
        if lanes[src] != lanes[dst]:
            continue
        if depth[src] < depth[dst] and exit_side == 'e' and incoming_count[dst] == 1 and enter_side in ('n','s'):
            face_port(dst, enter_side, 'w')
        if depth[src] > depth[dst] + 1 and enter_side in ('n','s') and exit_side == 'e':
            if sum(e['from'] == edge['from'] for e in structure['links']) == 1:
                face_port(src, exit_side, by_id[dst].get('port_sides', {}).get(enter_side, enter_side))
    # A readout already leaving a vertical port belongs directly above/below
    # that block. This keeps it out of the return path of a horizontal loop.
    for edge in structure['links']:
        src, source_port, _ = endpoint(edge['from'])
        dst, target_port, _ = endpoint(edge['to'])
        side = by_id[src].get('port_sides', {}).get(source_port, source_port)
        if (side in ('n', 's') and by_id[dst]['type'] == 'tag' and dst in structure.get('outputs', ())
                and incoming_count[dst] == 1 and len(used_ports[dst]) == 1):
            lane = lanes[src] + (-1 if side == 'n' else 1)
            if (all(n == dst or (depth[n], lanes[n]) != (depth[src], lane) for n in by_id)
                    and face_port(dst, target_port, 's' if side == 'n' else 'n')):
                depth[dst], lanes[dst] = depth[src], lane
    # Alignment of two independent side inputs can claim the same grid cell.
    # Reserve all original cells before moving duplicates to the nearest free lane.
    occupied = {(depth[node], lanes[node]) for node in by_id}
    claimed = set()
    for node in sorted(by_id, key=lambda bid: (bid in auxiliary, bid)):
        col, lane = depth[node], lanes[node]
        if (col, lane) in claimed:
            step = -1 if auxiliary.get(node, (None, None))[1] == 'n' else 1
            while (col, lane) in occupied:
                lane += step
            lanes[node] = lane
            occupied.add((col, lane))
        claimed.add((col, lane))
    # Disjoint named regions describe separate parts of the figure. A feedback
    # branch must not pull another controller or the external plant into its frame.
    groups = [frozenset(r['members']) for r in structure.get('regions', []) if r.get('members')]
    band_starts = _separate_region_bands(groups, by_id, depth, lanes)
    # Attach a single-input tag to its consumer after separating group bands.
    # A free cell outside the frame can put both ports on one unobstructed line.
    outputs = defaultdict(list)
    for edge in structure['links']:
        src, dst = endpoint(edge['from']), endpoint(edge['to'])
        outputs[src[0]].append((src, dst))
    occupied = {(depth[n], lanes[n]) for n in by_id}
    for tag, edges in sorted(outputs.items()):
        if by_id[tag]['type'] != 'tag' or incoming[tag] or len(edges) != 1 or tag in fixed_ports:
            continue
        (_, outlet, offset), (dst, inlet, other_offset) = edges[0]
        if offset or other_offset:
            continue
        side = by_id[dst].get('port_sides', {}).get(inlet, inlet)
        row, col = lanes[dst], depth[tag]
        if side in ('n','s') and col == depth[dst]:
            if (lanes[tag] - row) * (-1 if side == 'n' else 1) > 0:
                face_port(tag, outlet, 's' if side == 'n' else 'n')
        elif side in ('e','w') and col == depth[dst] + (1 if side == 'e' else -1):
            if row != lanes[tag]:
                if (col, row) in occupied or any(tag in g or
                        (min(depth[n] for n in g) <= col <= max(depth[n] for n in g) and
                         min(lanes[n] for n in g) <= row <= max(lanes[n] for n in g)) for g in groups):
                    continue
                occupied.discard((col, lanes[tag]))
                lanes[tag] = row
                occupied.add((col, row))
            face_port(tag, outlet, 'w' if side == 'e' else 'e')
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
        # Both printed frames need their own padding and room for an outside title.
        gap = LANE_GAP + (2 * REGION_PAD + LANE_GAP if lane in band_starts else 0)
        ys[lane] = 0.0 if previous is None else ys[previous] - (below[previous] + gap + above[lane])*spacing
        previous = lane
    for bid, b in by_id.items():
        pos = [round(xs[depth[bid]], 6), round(ys[lanes[bid]], 6)]
        if b['type'] == 'switch':
            old = b.get('pole', [0, 0])
            b['throws'] = {k: [v[0]-old[0]+pos[0], v[1]-old[1]+pos[1]] for k,v in b['throws'].items()}
            b['pole'] = pos
        else:
            b['at'] = pos
    _align_fanouts(blocks, by_id, structure, groups, incoming, face_port, spacing)
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


def _place_feeders(by_id, structure, parents, dag, depth, lanes, face_port):
    from .ai_structure import endpoint
    edges = [(endpoint(e['from']), endpoint(e['to'])) for e in structure['links']]
    remaining = set(by_id)
    while remaining:
        component, stack = set(), [min(remaining)]
        while stack:
            node = stack.pop()
            if node in component:
                continue
            component.add(node)
            stack.extend(parents[node] + dag[node])
        remaining -= component
        if len(component) < 2:
            continue
        external = [(src, dst) for src, dst in edges if (src[0] in component) != (dst[0] in component)]
        if any(src[0] not in component or dst[1] not in ('n','s') for src, dst in external):
            continue
        taps = {dst[0] for _, dst in external}
        # Aligned consumers on one side already share the trunk placed above.
        if len(taps) < 2 or len({(depth[dst[0]], dst[1]) for _, dst in external}) == 1:
            continue
        sides = {dst[1] for _, dst in external}
        returns_to = [dst[1] for src, dst in edges if src[0] not in component and dst[0] not in component
                      and depth[src[0]] > depth[dst[0]]]
        above_main = sides == {'n'} or (len(sides) > 1 and returns_to.count('s') >= returns_to.count('n'))
        # Bring a side input to its source-facing edge. Its logical name, sum
        # sign and controller binding are unchanged. Do not displace another
        # vertical signal or a symbol with fixed physical contacts.
        for target in taps:
            sink_ports = {dst[1] for _, dst in external if dst[0] == target}
            used = {side for edge in edges for node, side, _ in edge if node == target and side in ('n', 's')}
            block = by_id[target]
            if len(sink_ports) == 1 and used == sink_ports and block['type'] not in ('switch', 'compare'):
                face_port(target, next(iter(sink_ports)), 'n' if above_main else 's')
        others = [lanes[n] for n in by_id.keys() - component]
        shift_y = (min(others) - 1 - max(lanes[n] for n in component) if above_main else
                   max(others) + 1 - min(lanes[n] for n in component))
        offsets = sorted(depth[dst[0]] - depth[src[0]] for src, dst in external)
        shift_x = offsets[len(offsets)//2]
        for node in component:
            depth[node] += shift_x
            lanes[node] += shift_y


def _separate_region_bands(groups, by_id, depth, lanes):
    band_starts = set()
    def pack_siblings(members):
        children = [group for group in dict.fromkeys(groups) if group < members and
                    not any(group < other < members for other in groups)]
        if not children or any(a & b for i, a in enumerate(children) for b in children[i + 1:]):
            return
        for child in children:
            pack_siblings(child)
        def bounds(group):
            return (min(depth[n] for n in group), min(lanes[n] for n in group),
                    max(depth[n] for n in group), max(lanes[n] for n in group))
        frames = [bounds(group) for group in children]
        occupied = any(x0 <= depth[n] <= x1 and y0 <= lanes[n] <= y1
                       for group, (x0,y0,x1,y1) in zip(children, frames) for n in members - group)
        intersect = any(max(a[0], b[0]) <= min(a[2], b[2]) and max(a[1], b[1]) <= min(a[3], b[3])
                        for i, a in enumerate(frames) for b in frames[i + 1:])
        if occupied or intersect:
            first = min(lanes[n] for n in members)
            for group in [*children, members.difference(*children)]:
                if not group:
                    continue
                old_rows = sorted({lanes[n] for n in group})
                # Keep any bands established inside a nested region.
                offset = first - old_rows[0]
                for n in group:
                    lanes[n] += offset
                first = old_rows[-1] + offset + 2
    if groups:
        pack_siblings(frozenset(by_id))
        # Keep space for both outlines and their outside titles between disjoint groups.
        for group in groups:
            first = min(lanes[n] for n in group)
            if any(other.isdisjoint(group) and max(lanes[n] for n in other) < first for other in groups):
                band_starts.add(first)
    return band_starts


def _align_fanouts(blocks, by_id, structure, groups, incoming, face_port, spacing):
    from .ai_structure import endpoint
    # A horizontal fan-out trunk can feed a side port with a single straight
    # drop. Leave the source to one side of the drop instead of centring a
    # block directly on top of its destination and forcing a boxed-in dogleg.
    links_by_source = defaultdict(list)
    for link in structure['links']:
        links_by_source[link['from']].append(endpoint(link['to']))
    for ref, destinations in links_by_source.items():
        source_id, face, _ = endpoint(ref)
        source = by_id[source_id]
        if face != 'e' or source['type'] not in ('tag','block') or incoming[source_id]:
            continue
        x, y = source['at']
        straight = [by_id[dst] for dst, side, _ in destinations if side == 'w' and
                    by_id[dst].get('at', [None, None])[1] == y and by_id[dst]['at'][0] > x]
        drops = [by_id[dst]['at'][0] + off for dst, side, off in destinations
                 if side in ('n','s') and by_id[dst].get('at', [None, None])[1] != y and 'at' in by_id[dst]]
        if not straight or not drops:
            continue
        right = min(b['at'][0] - b.get('size', [0])[0] / 2 for b in straight)
        targets = [t for t in drops if x <= t < right - COLUMN_GAP / 2]
        if targets:
            desired = min(targets) - source['size'][0] / 2 - COLUMN_GAP * spacing
            others = [b for b in blocks if b is not source and b.get('at', [None, None])[1] == y and b['at'][0] < x]
            floor = max((b['at'][0] + b.get('size', [0])[0]/2 + COLUMN_GAP + source['size'][0]/2
                         for b in others), default=desired)
            source['at'][0] = round(min(x, max(floor, desired)), 6)
    # A terminal readout branching off a through signal fits opposite its T.
    # Keeping it beneath the trunk removes the little elbow beside the tag.
    def groups_for(bid):
        return {i for i, group in enumerate(groups) if bid in group}
    for ref, destinations in links_by_source.items():
        src, face, off = endpoint(ref)
        source = by_id[src]
        if face != 'e' or len(destinations) < 2 or 'at' not in source:
            continue
        x, y = source['at']
        straight = [by_id[dst] for dst, side, _ in destinations if side == 'w' and
                    by_id[dst].get('at',[None,None])[1] == y and by_id[dst]['at'][0] > x]
        if not straight:
            continue
        left = x + source.get('size', (0,))[0] / 2
        right = min(b['at'][0] - b.get('size', (0,))[0]/2 for b in straight)
        mid = (left + right) / 2
        for dst, side, offset in destinations:
            tag = by_id[dst]
            if (tag['type'] != 'tag' or dst not in structure.get('outputs', ())
                    or tag['at'][1] == y or groups_for(src) != groups_for(dst)):
                continue
            row = tag['at'][1]
            if any(b is not tag and 'at' in b and b['at'][1] == row and
                   abs(b['at'][0] - mid) < (b.get('size', [0])[0] + tag['size'][0])/2 + LANE_GAP for b in blocks):
                continue
            if face_port(dst, side, 'n' if row < y else 's'):
                tag['at'][0] = round(mid - offset, 6)


def fit_nested_frames(table, prof):
    """Leave enough paper inside a parent for a child frame and its caption."""
    from ..geometry import region_label_box, region_rect
    from .ai_structure import REGION_PAD
    from .check import check
    from .model import parse
    regions = table['regions']
    members = [set(r.get('members', [])) for r in regions]
    order = sorted(range(len(regions)), key=lambda i: len(members[i]))
    scale = check(table, prof).geometry.scale
    for i in order:
        children = [j for j in order if members[j] and members[j] < members[i]]
        if not children:
            continue
        parsed = parse(table).regions
        for j in children:
            for box in (region_rect(parsed[j], scale), region_label_box(parsed[j], scale, prof)):
                if box is None:
                    continue
                a, b = regions[i]['from'], regions[i]['to']
                regions[i]['from'] = [min(a[0], (box.x0-REGION_PAD)/scale.x), min(a[1], (box.y0-REGION_PAD)/scale.y)]
                regions[i]['to'] = [max(b[0], (box.x1+REGION_PAD)/scale.x), max(b[1], (box.y1+REGION_PAD)/scale.y)]


def route_quality(table, prof, *, per_link=False):
    """Rank complete routes by validity, interior crossings, then bends and length."""
    from itertools import combinations
    from ..modules import _cross, _segs
    from .check import check
    report = check(table, prof)
    paths = [_segs(path) for path in report.geometry.paths if path]
    crossings = sum(bool(_cross(a, b, c, d)) for first, second in combinations(paths, 2)
                    for a, b in first for c, d in second)
    length = sum(abs(a[0]-b[0]) + abs(a[1]-b[1]) for path in paths for a,b in path)
    # Link labels are fitted after routing. Electrical, port and geometry errors
    # take precedence at this stage; the final check includes all labels.
    errors = sum(not error.startswith('text ') for error in report.errors)
    bends = [len(path)-1 for path in paths]
    score = (errors, crossings)
    if per_link:
        score += (sum(max(0, n-PREFERRED_ARROW_BENDS) for n in bends),)
    return (*score, sum(bends), length)


def place_link_labels(table, prof):
    """Keep labels on their generated signal paths and clear of blocks and other labels."""
    from ..geometry import font_of, region_label_box
    from itertools import product
    from .check import beside, check, point_along, text_box_size
    from .model import MAX_LABEL_GAP
    report = check(table, prof)
    geom, diagram = report.geometry, report.diagram
    labels = {link.name for link in diagram.links}
    occupied = [label.box for label in geom.labels if label.owner not in labels]
    occupied.extend(box for region in diagram.regions
                    if (box := region_label_box(region, geom.scale, prof)) is not None)
    obstacles = list(geom.bodies.values())
    strokes = [((x * geom.s, y * geom.s), (u * geom.s, v * geom.s))
               for path in geom.paths if path for (x,y), (u,v) in zip(path, path[1:])]
    def free(box):
        return (not any(box.overlaps(other, .04) for other in obstacles + occupied)
                and not any(box.hits_segment(a, b) for a, b in strokes))
    for raw, link, path in zip(table['links'], diagram.links, geom.paths):
        if not link.label or not path:
            continue
        w, h = text_box_size(link.label, font_of(link.font, prof), link.font_weight)
        def placement(at, side, gap):
            (x, y), _ = point_along(path, at)
            return beside((x * geom.s, y * geom.s), side, w, h, gap)
        # Prefer the requested position whenever it fits. If no clear option
        # exists, preserve it so the normal checker reports the unresolved issue.
        box = placement(link.label_at, link.label_pos, link.label_gap)
        if not free(box):
            # Search along the wire first, then raise the text by a line height.
            gaps = dict.fromkeys(min(MAX_LABEL_GAP, link.label_gap + i*h) for i in range(4))
            sides = dict.fromkeys((link.label_pos, 'above', 'below', 'left', 'right'))
            for gap, at, side in product(gaps, (link.label_at, .35, .65, .2, .8, .1, .9), sides):
                trial = placement(at, side, gap)
                if free(trial):
                    raw.update(label_at=at, label_pos=side)
                    if gap != link.label_gap:
                        raw['label_gap'] = round(gap, 5)
                    box = trial
                    break
        occupied.append(box)


def wire_gap_issues(report, prof):
    from ..geometry import Box
    from ..figure_style import paper_profile
    from .check import on_boundary, wire_clearance
    prof = paper_profile(prof, report.diagram.raw)
    issues = []
    geometry = report.geometry
    for bid, body in geometry.bodies.items():
        block = report.diagram.block(bid)
        gap = wire_clearance(block, prof)
        outside = Box(body.x0-gap, body.y0-gap, body.x1+gap, body.y1+gap)
        for link, path in zip(report.diagram.links, geometry.paths):
            if not path:
                continue
            for i, (a,b) in enumerate(zip(path, path[1:])):
                # Only the segment that actually meets this port is exempt.
                if i == 0 and (link.start.block == bid or
                               link.start.block is None and on_boundary(block, a, geometry.s)):
                    continue
                if i == len(path)-2 and (link.end.block == bid or
                                        link.end.block is None and on_boundary(block, b, geometry.s)):
                    continue
                p, q = [(x * geometry.s, y * geometry.s) for x,y in (a,b)]
                if outside.hits_segment(p,q):
                    issues.append(f"{link.name}: less than 2 mm of clearance to block {bid}")
                    break
    return issues


def layout_issues(report, prof, automatic=True):
    """Automatic full layout must keep independent frames and text apart."""
    prefixes = ('texts ', 'independent regions ') if automatic else ('texts ',)
    issues = list(report.errors) + [warning for warning in report.warnings
                                    if warning.startswith(prefixes) and warning.endswith(' overlap')]
    return issues or (wire_gap_issues(report, prof) if automatic else [])


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
    for spacing in (LAYOUT_SPACING if mode == 'layout' else (1.0,)):
        try:
            result = compile_structure(structure, deepcopy(raw), profile,
                                       arrange=mode == 'layout', reroute=True, spacing=spacing)
            if 'control_ai' not in raw:
                result.pop('control_ai', None)
            else:
                result['control_ai'] = deepcopy(raw['control_ai'])
            prof = get_profile(profile)
            rep = check(result, prof)
            issues = layout_issues(rep, prof) if mode == 'layout' else list(rep.errors) + wire_gap_issues(rep, prof)
            if issues:
                failures = list(issues)
                continue
            before, after = to_structure(raw, profile), to_structure(result, profile)
            def signature(s):
                s = deepcopy(s)
                s['blocks'] = sorted(s['blocks'], key=lambda b: b['id'])
                for link in s['links']:
                    link.pop('label_pos', None)
                    link.pop('label_gap', None)
                s['links'] = sorted(s['links'], key=lambda l: (l['from'], l['to']))
                return s
            if signature(before) != signature(after):
                raise ValueError('整理结果改变了控制语义；当前图保持原样')
            return result
        except StructureError as exc:
            failures = [str(exc)]
    raise ValueError('无法完成整理；当前图保持原样：' + '; '.join(failures))
