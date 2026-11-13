"""Solve on the full electrical circuit, then project paths onto its drawn view."""
from collections import defaultdict
from dataclasses import replace

from .geometry import Box
from .glyphs import _box
from .modes import ModeError, WireGraph, _k, _place_arrows, solve


def _pins(layout):
    return {f"{e.id}.{n}": _k(p) for e in layout.elements for n, p in e.pins.items()}


class ModeScene:
    def __init__(self, source, prof, report=None, assembly=None):
        self.prof, self.assembly = prof, assembly
        if "modules" in source.extra:
            from .modules import checked, _view_links
            if assembly is None:
                assembly, physical, report = checked(source, prof)
            else:
                physical = assembly.connectivity(prof, report.scale)
            if not report.ok or not physical.ok:
                raise ModeError("模块校验未通过：" + "; ".join(map(str, report.errors + physical.errors)))
            self.assembly = assembly
            self.layout, self.report = assembly.sheets, physical
            self.view, self.view_report = assembly.view, report
            if "electrical" in source.extra:
                self.view = replace(self.view, extra={**self.view.extra, "electrical": source.extra["electrical"]})
            self.graph = WireGraph(self.layout, assembly.sheet_links)
            self.view_graph = WireGraph(self.view, _view_links(assembly))
            self.points, self.boxes = self._projection(source)
            represented = {e.id for e in self.view.elements} | set().union(*self.boxes.values())
            self.unmapped = {e.id for e in self.layout.elements if e.kind != "port" and e.id not in represented
                             and e.id.split("/", 1)[0] not in assembly.hidden}
        else:
            from .validate import check
            self.layout = self.view = source
            self.report = self.view_report = report or check(source, prof)
            self.graph = self.view_graph = WireGraph(source)

    def _projection(self, source):
        a = self.assembly
        physical, visible = _pins(self.layout), _pins(self.view)
        points = defaultdict(set)
        for ref in physical.keys() & visible.keys():
            points[physical[ref]].add(visible[ref])
        for public, ref in a.port_refs.items():
            if public in a.ports:
                points[physical[ref]].add(_k(a.ports[public]))

        # A partial block's terminals name its internal nets. Associate only
        # hidden devices whose pin nets belong to exactly one such block.
        terminals = defaultdict(list)
        for e in self.view.elements:
            if e.kind == "port" and e.opts.get("block"):
                owner = e.id.split("/", 1)[0]
                terminals[owner + "/" + e.opts["block"]].append(f"{e.id}.p")
        boxes = {block: set() for block in terminals}
        nets = {block: {self.view.netlist[r] for r in refs} for block, refs in terminals.items()}
        visible_ids = {e.id for e in self.view.elements}
        for e in self.layout.elements:
            if e.kind == "port" or e.id in visible_ids:
                continue
            owner = e.id.split("/", 1)[0]
            candidates = [block for block in terminals if block.startswith(owner + "/")
                          and {self.layout.netlist[f"{e.id}.{p}"] for p in e.pins} <= nets[block]]
            if len(candidates) != 1:
                continue
            block = candidates[0]
            boxes[block].add(e.id)
            for ref in terminals[block]:
                for pin in e.pins:
                    real = f"{e.id}.{pin}"
                    if self.layout.netlist[real] == self.view.netlist[ref]:
                        points[physical[real]].add(visible[ref])

        region_ids = {r["id"] for r in self.view.extra.get("regions", []) if isinstance(r.get("id"), str)}
        for name in a.instances:
            if name + "/box" in region_ids:
                boxes[name + "/box"] = {e.id for e in self.layout.elements if e.id.startswith(name + "/")}
        for ref in region_ids:
            if ref and ref.endswith("/group"):
                owner = ref.removesuffix("/group")
                members = {p.split(".")[0] for p, representative in a.port_owners.items() if representative == owner}
                boxes[ref] = {e.id for e in self.layout.elements if e.id.split("/", 1)[0] in members}
        usages = source.extra.get("stack_usage", {})
        for ref, usage in (usages if isinstance(usages, dict) else {}).items():
            if ref in region_ids and usage.get("purpose") == "instances" and a.hidden:
                boxes[ref] = {e.id for e in self.layout.elements
                              if e.id.split("/", 1)[0] in usage.get("instances", [])}
        return points, boxes

    def draw(self, mode, max_arrows=3):
        if self.assembly is None:
            return solve(self.layout, mode, self.report, self.graph, self.prof if max_arrows else None, max_arrows)
        physical = solve(self.layout, mode, self.report, self.graph)
        if self.unmapped & physical.parts.keys():
            raise ModeError("部分折叠图不能唯一对应隐藏器件，请展开实例后查看模态。")
        lit = replace(physical, edges=set(), arrows=[], arrow_loops=[], traces=[])
        oriented, clash = {}, set()
        loops = [{} for _ in mode.loops]
        for trace in physical.traces:
            adj = defaultdict(set)
            nodes = {_k(p) for p in trace.sources + trace.sinks}
            for edge in trace.edges:
                e = self.graph.edges[edge]
                nodes.update((e.a, e.b))
                adj[e.a].add(e.b)
                adj[e.b].add(e.a)
            mapped = set().union(*(self.points[p] for p in nodes))
            if len(mapped) < 2:
                continue
            tree = self.view_graph.tree(sorted(mapped))
            if tree is None:
                raise ModeError(f"{mode.name}: 可见模块端口之间没有完整导线，请检查部分折叠图的端口网络")
            lit.edges |= tree
            if trace.loop is None:
                continue

            def frontier(terminals):
                result, seen, todo = set(), set(), list(map(_k, terminals))
                while todo:
                    p = todo.pop()
                    if p in seen:
                        continue
                    seen.add(p)
                    if self.points[p]:
                        result.update(self.points[p])
                    else:
                        todo.extend(adj[p] - seen)
                return list(result)

            for edge, sign in self.view_graph.orient(tree, frontier(trace.sources), frontier(trace.sinks)).items():
                if oriented.get(edge, sign) != sign:
                    clash.add(edge)
                oriented[edge] = sign
                loops[trace.loop][edge] = sign
        lit.regions = {ref: bool(members & physical.parts.keys()) for ref, members in self.boxes.items()}
        for i, glyph in enumerate(self.view.extra.get("glyphs", [])):
            for region in self.view.extra.get("regions", []):
                ref = region.get("id")
                if not isinstance(ref, str) or ref not in lit.regions or "from" not in region or "to" not in region:
                    continue
                if all(Box(*_box(region)).contains(p) for p in (glyph["from"], glyph["to"])):
                    lit.glyphs[i] = lit.regions[ref]
        if mode.arrows is None and max_arrows:
            placed = _place_arrows(self.view, self.view_graph, self.view_report, self.prof, loops, clash, max_arrows)
            lit.arrows = [(p, angle) for p, angle, _ in placed]
            lit.arrow_loops = [i for _, _, i in placed]
        elif mode.arrows is not None:
            lit.arrows = physical.arrows
        return lit
