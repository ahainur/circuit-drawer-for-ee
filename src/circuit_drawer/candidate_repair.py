"""Protect the rest of a circuit while reviewing a repair to one issue."""
import re

from .figure_candidates import _nets, _protected
from .layout import Layout
from .modes import WireGraph


def pin_groups(table, profile):
    if table.get("modules"):
        groups, _, _ = _nets(table, profile)
        return {pin: set(peers) for pin, peers in groups.items()}
    # A spacing error can stop paper validation before it visits connectivity.
    # Use the same physical wire graph used to trace operating modes.
    layout = Layout.from_dict(table)
    graph = WireGraph(layout)
    at = {f"{e.id}.{key}": tuple(round(v, 4) for v in p)
          for e in layout.elements for key, p in e.pins.items()}
    roots = {}
    for p in at.values():
        if p in roots:
            continue
        roots[p] = p
        todo = [p]
        while todo:
            for other, _ in graph.adj.get(todo.pop(), ()):
                if other not in roots:
                    roots[other] = p
                    todo.append(other)
    groups = {}
    for pin, point in at.items():
        groups.setdefault(roots[point], set()).add(pin)
    return {pin: groups[roots[point]] - {pin} for pin, point in at.items()}


def preserves_circuit(before, after, profile, issue):
    try:
        layouts = [Layout.from_dict(table).to_dict() for table in (before, after)]
        if _protected(layouts[0]) != _protected(layouts[1]):
            return False
        a, b = (pin_groups(table, profile) for table in layouts)
    except (ValueError, KeyError, TypeError, AttributeError, IndexError):
        return False
    if a.keys() != b.keys():
        return False
    declared = before.get("netlist", {})
    relevant = a.keys() & set(re.findall(r"[\w/-]+\.[\w-]+", issue.get("message", "")))
    for pin in a:
        for other in a[pin] ^ b[pin]:
            if not {pin, other} & relevant:
                return False
            # Repair only joins declared members of the same network or splits two named nets.
            if other in b[pin]:
                if issue["code"] not in ("netlist-open", "open-pin", "dangling-wire") or not (
                        declared.get(pin) and declared.get(pin) == declared.get(other)):
                    return False
            elif issue["code"] not in ("netlist-short", "shorted-pins") or declared.get(pin) == declared.get(other):
                return False
    return True
