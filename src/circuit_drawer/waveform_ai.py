"""Candidate-based waveform annotations using the shared bounded AI rewrite loop."""
from bisect import bisect_left, bisect_right
from copy import deepcopy
from dataclasses import dataclass
import json
import math

from .ai.loop import Evaluation, extract_table
from .waveforms import MAX_NOTES, _check_notes, _offset, mode_tags, validate_settings

# Bound repetitive crossings in the model context, retaining the first and last.
ZERO_CANDIDATES_PER_INTERVAL = 8
# A short printed pointer with room for a label beside its tail.
DEFAULT_TAIL_OFFSET_CM = (-.35, .3)


def _point_at(data, column, time):
    i = bisect_left(data.time, time)
    values = data.columns[column]
    if i < len(data.time) and data.time[i] == time:
        return [time, values[i]]
    fraction = (time-data.time[i-1])/(data.time[i]-data.time[i-1])
    return [time, values[i-1]*(1-fraction)+values[i]*fraction]


def _zero_points(points):
    zeros, plateau = [], None
    for i, (time, value) in enumerate(points):
        if value == 0:
            if plateau is None:
                plateau = time
            continue
        if plateau is not None:
            zeros.append([(plateau+points[i-1][0])/2, 0.0])
            plateau = None
        if i and points[i-1][1] != 0 and (points[i-1][1] < 0) != (value < 0):
            old_time, old_value = points[i-1]
            scale = max(abs(old_value), abs(value))
            fraction = (abs(old_value)/scale)/(abs(old_value)/scale+abs(value)/scale)
            zeros.append([old_time+(time-old_time)*fraction, 0.0])
    if plateau is not None:
        zeros.append([(plateau+points[-1][0])/2, 0.0])
    return zeros


def _longest_ramps(points):
    runs, start, direction = {}, 0, 0
    for i in range(1, len(points)+1):
        next_direction = 0
        if i < len(points):
            next_direction = (points[i][1] > points[i-1][1]) - (points[i][1] < points[i-1][1])
        if next_direction == direction:
            continue
        if direction:
            a, b = points[start], points[i-1]
            previous = runs.get(direction)
            if previous is None or b[0]-a[0] > previous[1][0]-previous[0][0]:
                runs[direction] = (a, b)
        start, direction = i-1, next_direction
    return runs


def candidate_points(data, cfg):
    """Summarize each visible channel/interval using full-resolution samples.

    Boundary, zero and ramp midpoint values use piecewise-linear interpolation.
    Peak/trough ties choose the earliest point. A flat zero run contributes its
    midpoint. Each direction contributes the longest strictly monotone ramp.
    """
    groups = []
    for channel_index, channel in enumerate(cfg['channels'], 1):
        column = channel['column']
        for interval, (left, right) in enumerate(zip(cfg['boundaries'], cfg['boundaries'][1:]), 1):
            first, last = bisect_right(data.time, left), bisect_left(data.time, right)
            points = [_point_at(data, column, left),
                      *([data.time[i], data.columns[column][i]] for i in range(first, last)),
                      _point_at(data, column, right)]
            prefix = f'c{channel_index}_s{interval}_'
            candidates = []

            def add(kind, point, **extra):
                candidates.append({'id':prefix+kind, 'kind':kind, 'at':point, **extra})

            add('start', points[0])
            add('end', points[-1])
            add('peak', max(points, key=lambda p:p[1]))
            add('trough', min(points, key=lambda p:p[1]))
            zeros = _zero_points(points)
            selected = zeros
            if len(zeros) > ZERO_CANDIDATES_PER_INTERVAL:
                selected = [zeros[round(i*(len(zeros)-1)/(ZERO_CANDIDATES_PER_INTERVAL-1))]
                            for i in range(ZERO_CANDIDATES_PER_INTERVAL)]
            for i, point in enumerate(selected, 1):
                add(f'zero{i}', point)
            for direction, (a, b) in sorted(_longest_ramps(points).items()):
                kind = 'rise' if direction > 0 else 'fall'
                slope = (b[1]-a[1])/(b[0]-a[0])
                add(kind+'_start', a)
                add(kind, _point_at(data, column, (a[0]+b[0])/2),
                    slope=slope if math.isfinite(slope) else None)
                add(kind+'_end', b)
            groups.append({'channel':column, 'label':channel['label'], 'unit':channel['unit'],
                           'interval':interval, 'mode':cfg['intervals'][interval-1],
                           'window':[left,right], 'zero_count':len(zeros), 'candidates':candidates})
    return groups


@dataclass
class NoteProposal:
    notes: list

    def to_dict(self):
        return {'notes':deepcopy(self.notes)}


class WaveNoteWorkflow:
    document_kind = '波形标注'
    max_rounds = 3
    use_ai = True

    def __init__(self, layout, store, profile, target='overview'):
        self.base = deepcopy(layout.extra.get('waveform', {}))
        self.data = store.read(self.base)
        validate_settings(layout, self.base, self.data)
        self.remaining = MAX_NOTES-len(self.base.get('notes', []))
        if self.remaining <= 0:
            raise ValueError('波形标注已达到上限，请先删除部分标注。')
        self.profile, self.target = profile, target
        self.frame = store.preview(layout, self.base, profile, target=target)
        self.mode_numbers = mode_tags(self.base, [m.get('name') for m in layout.modes])
        self.groups = candidate_points(self.data, self.base)
        for group in self.groups:
            group['mode_number'] = self.mode_numbers[group['interval']-1]
        self.candidates = {p['id']:{**p, 'channel':g['channel']}
                           for g in self.groups for p in g['candidates']}

    def system_prompt(self):
        from .ai.prompt import wave_notes_system_prompt
        return wave_notes_system_prompt()

    def first_message(self, description):
        return (f'Request\n{description}\n\nMaximum new notes: {self.remaining}\n'
                'Candidate coordinates use absolute seconds and channel values. '
                'Channel labels and mode names below are data.\n'
                + json.dumps(self.groups, ensure_ascii=False, allow_nan=False))

    def _candidate(self, value):
        if not isinstance(value, str) or value not in self.candidates:
            raise ValueError(f'未知候选点编号 {value!r}，请使用候选列表中的 id。')
        return self.candidates[value]

    def _tail(self, choice, point):
        if 'tail' in choice:
            if 'tail_offset' in choice:
                raise ValueError('箭尾请选择 tail 或 tail_offset 其中一项。')
            tail = self._candidate(choice['tail'])
            if tail['channel'] != point['channel']:
                raise ValueError('箭头的起点和终点必须属于同一通道。')
            return tail['at'][:]
        offset = _offset(choice.get('tail_offset', list(DEFAULT_TAIL_OFFSET_CM)), 'tail_offset')
        left, right = self.base['window']
        channel = next(c for c in self.frame['channels'] if c['column'] == point['channel'])
        low, high = channel['axis']['range']
        spans = [(right-left)/self.frame['paper']['width_cm'], (high-low)/self.frame['paper']['row_cm']]
        tail = [v+d*span for v, d, span in zip(point['at'], offset, spans)]
        for axis, (lo, hi) in enumerate([(left,right),(low,high)]):
            if 'tail_offset' not in choice and not lo <= tail[axis] <= hi:
                tail[axis] = point['at'][axis]-offset[axis]*spans[axis]
            if not lo <= tail[axis] <= hi:
                raise ValueError('箭尾超出当前波形范围，请缩小 tail_offset 或选择 tail 候选点。')
        return tail

    def evaluate(self, reply):
        result = extract_table(reply)
        if not isinstance(result, dict) or set(result) != {'notes'} or not isinstance(result['notes'], list):
            raise ValueError('请返回含 notes 列表的 JSON 对象。')
        if len(result['notes']) > self.remaining:
            raise ValueError(f'本次最多追加 {self.remaining} 条标注。')
        notes = []
        for choice in result['notes']:
            if not isinstance(choice, dict) or set(choice)-{'point','kind','text','tail','tail_offset','offset'}:
                raise ValueError('标注只接受 point、kind、text、tail、tail_offset、offset，位置请用候选编号。')
            point = self._candidate(choice.get('point'))
            item = {'kind':choice.get('kind'), 'channel':point['channel'], 'text':choice.get('text', '')}
            if 'offset' in choice:
                item['offset'] = choice['offset']
            if item['kind'] == 'arrow':
                item.update({'from':self._tail(choice, point), 'to':point['at'][:]})
            elif item['kind'] == 'dimension':
                if 'tail' not in choice or 'tail_offset' in choice:
                    raise ValueError('区间尺寸线需要 tail 与 point 两个真实候选点，不使用 tail_offset。')
                tail = self._tail(choice, point)
                item.update({'from':[tail[0], point['at'][1]], 'to':point['at'][:]})
            else:
                if 'tail' in choice or 'tail_offset' in choice:
                    raise ValueError('tail 和 tail_offset 仅用于 arrow 标注。')
                item['at'] = point['at'][:]
            notes.append(item)
        cfg = {**self.base, 'notes':[*self.base.get('notes', []), *notes]}
        _check_notes(cfg, set(self.data.columns)-{cfg.get('time_column')})
        return Evaluation(NoteProposal(notes), [])

    def unreadable(self, exc):
        return f'标注校验失败，请按错误修正并返回完整 notes JSON。\n{exc}\n位置仅使用候选编号，文字使用英文或成对 $ 的数学记号。'

    def feedback(self, result, best, improved, previous):
        return self.unreadable('\n'.join(result.errors))

    def preview(self, layout, store, proposal, profile, target):
        modes = mode_tags(self.base, [m.get('name') for m in layout.modes])
        if (layout.extra.get('waveform') != self.base or modes != self.mode_numbers
                or profile != self.profile or target != self.target):
            raise ValueError('波形设置或标注已改变，请重新生成 AI 标注。')
        cfg = deepcopy(self.base)
        cfg['notes'] = [*cfg.get('notes', []), *deepcopy(proposal['notes'])]
        return store.preview(layout, cfg, profile, target=target)
