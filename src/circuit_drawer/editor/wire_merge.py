"""Remove repeated source-wire coverage while keeping its junction vertices."""
from collections import Counter, defaultdict
from copy import deepcopy
from itertools import combinations
import re

from ..layout import Layout
from ..profiles import get_profile
from ..validate import check_with_assembly


def line_key(a, b):
    # Only exactly aligned source segments: nearby parallel wires stay separate.
    if a[1] == b[1] and a[0] != b[0]:
        return 0, a[1]
    if a[0] == b[0] and a[1] != b[1]:
        return 1, a[0]
    return None


def merged_wires(wires):
    lines = defaultdict(list)
    for i, wire in enumerate(wires):
        for a, b in zip(wire, wire[1:]):
            if (key := line_key(a, b)) is not None:
                lines[key].append((i, *sorted((a[key[0]], b[key[0]]))))
    affected = set()
    for runs in lines.values():
        for (i, lo, hi), (j, low, high) in combinations(runs, 2):
            if min(hi, high) > max(lo, low):
                affected.update((i, j))
    if not affected:
        return deepcopy(wires)
    cuts = {key: {v for _, lo, hi in runs for v in (lo, hi)} for key, runs in lines.items()}
    chunks, used = [], set()
    for i, wire in enumerate(wires):
        if i not in affected:
            chunks.append((list(wire), False))
            continue
        path = []

        def flush():
            if len(path) > 1:
                chunks.append((path.copy(), True))
            path.clear()

        for a, b in zip(wire, wire[1:]):
            key = line_key(a, b)
            points = [tuple(a), tuple(b)]
            if key is not None:
                axis, fixed = key
                values = sorted((v for v in cuts[key] if min(a[axis], b[axis]) <= v <= max(a[axis], b[axis])),
                                reverse=a[axis] > b[axis])
                points = [(v, fixed) if axis == 0 else (fixed, v) for v in values]
            for start, end in zip(points, points[1:]):
                edge = tuple(sorted((start, end)))
                if key is not None and edge in used:
                    flush()
                    continue
                used.add(edge)
                if not path:
                    path.append(start)
                path.append(end)
        flush()
    # Join the remaining straight continuations, retaining the common vertex.
    again = True
    while again:
        again = False
        for i, (a, touched) in enumerate(chunks):
            if not touched:
                continue
            for j in range(i + 1, len(chunks)):
                b, touched = chunks[j]
                if not touched:
                    continue
                for left, right in ((a,b), (a,list(reversed(b))), (list(reversed(a)),b), (list(reversed(a)),list(reversed(b)))):
                    if left[-1] != right[0]:
                        continue
                    key = line_key(left[-2], left[-1])
                    if key is not None and key == line_key(right[0], right[1]):
                        axis = key[0]
                        if (left[-1][axis]-left[-2][axis]) * (right[1][axis]-right[0][axis]) <= 0:
                            continue
                        chunks[i] = (left + right[1:], True)
                        chunks.pop(j)
                        again = True
                        break
                if again:
                    break
            if again:
                break
    return [wire for wire, _ in chunks]


def merge_overlaps(source, profile="double"):
    wires = merged_wires(source.wires)
    if wires == source.wires:
        return Layout.from_dict(source.to_dict())
    prof = get_profile(profile)
    before, original = check_with_assembly(source, prof)
    raw = deepcopy(source.to_dict())
    raw['wires'] = wires
    result = Layout.from_dict(raw)
    after, merged = check_with_assembly(result, prof)
    if 'modules' in source.extra and (original is None or merged is None):
        raise ValueError('模块结构无法解析，已保留原图；请先修正模块错误')

    def groups(report, assembly):
        nets = assembly.connectivity(prof, report.scale).nets if assembly else report.nets
        values = defaultdict(set)
        for pin, net in nets.items():
            values[net].add(pin)
        return {frozenset(pins) for pins in values.values()}

    if groups(before, original) != groups(after, merged):
        raise ValueError('合并会改变电气连接，已保留原图；请先确认分支节点和跨线')
    key = lambda issue: (issue.code, re.sub(r'wire\[\d+\]', 'wire', issue.message))
    remaining = Counter(key(issue) for issue in before.errors)
    errors = []
    for issue in after.errors:
        # Duplicate lines can hide an already free endpoint by inflating its degree.
        # Its geometry is unchanged; keep the newly visible dangling error in the report.
        if issue.code == 'dangling-wire':
            continue
        if remaining[key(issue)] and not issue.code.startswith('bad-'):
            remaining[key(issue)] -= 1
        else:
            errors.append(issue)
    if errors:
        raise ValueError('合并校验未通过，已保留原图：' + '；'.join(i.message for i in errors[:5]))
    return result
