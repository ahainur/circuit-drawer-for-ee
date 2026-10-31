"""Read-only wiring plan for moving/copying a selection in the editor.

Use the same wire graph as module authoring. A drawing under repair need not
pass validation before it can be selected; names in its netlist are not used
to invent connections. The returned plan is transient, never a table field.
"""
from ..modes import WireGraph, _k
from ..modules import _on


def _include_parallel_paths(graph, internal):
    """Include every path through a wire cycle traversed by the selected tree.

    Biconnected edge blocks distinguish a parallel route between selected
    pins from an unrelated loop attached at just one external junction.
    Iterative DFS avoids a recursion limit on long, detailed wire routes.
    """
    order, low, edges = {}, {}, []
    for start in graph.adj:
        if start in order:
            continue
        order[start] = low[start] = len(order)
        walk = [(start, None, iter(graph.adj[start]))]
        while walk:
            point, parent_edge, neighbors = walk[-1]
            try:
                neighbor, edge = next(neighbors)
            except StopIteration:
                walk.pop()
                if not walk:
                    continue
                parent = walk[-1][0]
                low[parent] = min(low[parent], low[point])
                if low[point] >= order[parent]:
                    block = set()
                    while edges:
                        edge = edges.pop()
                        block.add(edge)
                        if edge == parent_edge:
                            break
                    if block & internal:
                        internal.update(block)
                continue
            if edge == parent_edge:
                continue
            if neighbor not in order:
                edges.append(edge)
                order[neighbor] = low[neighbor] = len(order)
                walk.append((neighbor, edge, iter(graph.adj[neighbor])))
            elif order[neighbor] < order[point]:
                edges.append(edge)
                low[point] = min(low[point], order[neighbor])


def selection_plan(layout, ids, wires=()):
    ids = set(ids)
    wires = set(wires)
    if any(type(i) is not int or not 0 <= i < len(layout.wires) for i in wires):
        raise ValueError("所选导线已不存在，请重新选择")
    chosen = [e for e in layout.elements if e.id in ids]
    if len(chosen) != len(ids):
        raise ValueError("请选择当前图中的普通元件；模块内部元件请进入定义后编辑")
    pins = {_k(p) for e in chosen for p in e.pins.values()}
    fixed = {_k(p) for e in layout.elements if e.id not in ids for p in e.pins.values()}
    graph = WireGraph(layout)
    internal, seen = set(), set()
    for start in sorted(pins):
        if start in seen:
            continue
        points, edges, pending = {start}, set(), [start]
        while pending:
            for q, i in graph.adj.get(pending.pop(), []):
                edges.add(i)
                if q not in points:
                    points.add(q)
                    pending.append(q)
        seen.update(points)
        if not points & fixed:
            internal.update(edges)
        else:
            internal.update(graph.tree(sorted(points & pins)) or ())
    _include_parallel_paths(graph, internal)
    internal.update(i for wi in wires for i in graph.runs[wi])
    moving = pins | {p for i in internal for p in (graph.edges[i].a, graph.edges[i].b)}
    runs = []
    for wi, indices in enumerate(graph.runs):
        if indices and all(i in internal for i in indices):
            runs.append({"wire": layout.wires[wi], "inside": True, "source": wi})
            continue
        # Completely unrelated polylines keep their authored vertices exactly.
        if not any(i in internal or graph.edges[i].a in moving or graph.edges[i].b in moving for i in indices):
            runs.append({"wire": layout.wires[wi], "inside": False, "source": wi})
            continue
        run = None
        for i in indices:
            edge, inside = graph.edges[i], i in internal
            # External T junctions and stationary pins are anchors. Split there
            # before reusing the editor's ordinary endpoint stretching rule.
            split = not inside and (edge.a in fixed or len(graph.adj.get(edge.a, [])) != 2 or edge.a in pins)
            if run is None or run["inside"] != inside or split:
                run = {"wire": [edge.a], "inside": inside, "source": wi}
                runs.append(run)
            run["wire"].append(edge.b)
    markers = []
    for h in layout.hops:
        owners = [i for i, run in enumerate(runs)
                  if any(_on(h, a, b) for a, b in zip(run["wire"], run["wire"][1:]))]
        markers.append({"at": h, "runs": owners})
    return {"runs": runs, "moving": sorted(moving), "fixed": sorted(fixed & moving), "hops": markers}
