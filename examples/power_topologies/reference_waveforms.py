"""Generate ideal analytical examples in a new directory, not solver traces.

Run: python examples/power_topologies/reference_waveforms.py <new-directory>
Coordinates come unchanged from the bundled templates. This script computes
only explicitly assumed steady-state waveforms and their mode bindings.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path

from circuit_drawer.layout import Layout
from circuit_drawer.profiles import get_profile
from circuit_drawer.templates.saved import BUNDLED
from circuit_drawer.validate import check
from circuit_drawer.waveforms import circuit_signature

NAMES = (
    'synchronous_buck', 'synchronous_boost', 'inverting_buck_boost',
    'two_switch_buck_boost', 'four_switch_buck_boost', 'cuk', 'sepic', 'zeta',
    'interleaved_boost', 'interleaved_buck', 'two_switch_forward',
    'phase_shift_full_bridge', 'single_phase_full_bridge',
    'single_phase_half_bridge', 'single_phase_diode_bridge',
    'three_phase_diode_bridge', 'three_phase_thyristor_bridge',
)
VIN, L, LM = 12.0, 100e-6, 1e-3
PERIOD, LINE_PERIOD = 20e-6, .02
# Display samples include both sides of every ideal discontinuity; they are
# neither a numerical integration step nor a claim about switching rise time.
EDGE_EPS = 1e-8
COLORS = ('#c8102e', '#2457d6', '#17845a', '#8753a0', '#b66b0a')


def ripple(q, duty, voltage_on, mean=2.0, period=PERIOD):
    q %= 1.0
    delta = voltage_on * period * duty / L
    return mean - delta / 2 + delta * (q / duty if q < duty else (1 - q) / (1 - duty))


def signals(name):
    """Return period, normalized boundaries, columns and analytical functions."""
    if name.startswith('interleaved_'):
        duty = .3
        von = VIN if name.endswith('boost') else VIN * (1 - duty)
        return PERIOD, [0, duty, .5, .5 + duty, 1], ['g1_1', 'g2_1', 'i_L1_A', 'i_L2_A'], lambda q: [
            int(q % 1 < duty), int((q - .5) % 1 < duty),
            ripple(q, duty, von, 1.5), ripple(q - .5, duty, von, 1.5)]
    if name == 'two_switch_forward':
        duty = .3
        def forward(q):
            q %= 1
            magnetizing = VIN * PERIOD / LM * (q if q < duty else max(0, 2 * duty - q))
            return [int(q < duty), VIN if q < duty else -VIN if q < 2 * duty else 0,
                    ripple(q, duty, VIN * (1 - duty)), magnetizing]
        return PERIOD, [0, duty, 2 * duty, 1], ['g1_1', 'v_primary_V', 'i_Lo_A', 'i_m_A'], forward
    if name == 'phase_shift_full_bridge':
        def psfb(q):
            q %= 1
            primary = VIN if q < .3 else -VIN if .5 <= q < .8 else 0
            return [int(q < .3 or q >= .8), int(q >= .5), primary,
                    ripple(2 * q, .6, VIN * .4, period=PERIOD / 2)]
        return PERIOD, [0, .3, .5, .8, 1], ['g1_1', 'g3_1', 'v_primary_V', 'i_Lo_A'], psfb
    if name in ('single_phase_full_bridge', 'single_phase_half_bridge'):
        amplitude = VIN / 2 if name.endswith('half_bridge') else VIN
        return LINE_PERIOD, [0, .5, 1], ['g1_1', 'v_out_V', 'i_out_A'], lambda q: [
            int(q % 1 < .5), amplitude if q % 1 < .5 else -amplitude,
            amplitude / 6 if q % 1 < .5 else -amplitude / 6]
    if name == 'single_phase_diode_bridge':
        def single(q):
            voltage = VIN * math.sin(2 * math.pi * q)
            return [voltage, abs(voltage), abs(voltage) / 6]
        return LINE_PERIOD, [0, .5, 1], ['v_ac_V', 'v_out_V', 'i_out_A'], single
    if name.startswith('three_phase_'):
        scr = name == 'three_phase_thyristor_bridge'
        alpha = math.pi / 6 if scr else 0
        pairs = [(0, 1), (0, 2), (1, 2), (1, 0), (2, 0), (2, 1)]
        def three(q):
            q %= 1
            theta = 2 * math.pi * q + math.pi / 6 + alpha
            phase = [100 * math.sin(theta + p) for p in (0, -2 * math.pi / 3, 2 * math.pi / 3)]
            hi, lo = pairs[min(int(q * 6), 5)]
            return [phase[hi] - phase[lo], 2 if hi == 0 else -2 if lo == 0 else 0, int(hi == 0)]
        return LINE_PERIOD, [i / 6 for i in range(7)], ['v_dc_V', 'i_a_A', 'conduct_1_1'], three
    duty = .4
    von = VIN * (1 - duty) if name == 'synchronous_buck' else VIN
    return PERIOD, [0, duty, 1], ['g1_1', 'v_L1_V', 'i_L1_A'], lambda q: [
        int(q % 1 < duty), von if q % 1 < duty else -von * duty / (1 - duty), ripple(q, duty, von)]


def example(name):
    period, edges, columns, value = signals(name)
    # Equal spacing plus samples immediately before edges; finite output grid.
    points = sorted({i / 600 for i in range(601)} | set(edges) |
                    {edge - EDGE_EPS for edge in edges if edge > 0})
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, lineterminator='\n')
    writer.writerow(['time_s', *columns])
    for q in points:
        writer.writerow([format(q * period, '.17g'), *(format(v, '.17g') for v in value(q))])
    raw = stream.getvalue().encode('utf-8')
    table = json.loads((BUNDLED / (name + '.json')).read_text(encoding='utf-8'))['layout']
    lay = Layout.from_dict(table)
    assert len(lay.modes) == len(edges) - 1
    table['waveform'] = dict(version=1, source='waveforms-data/' + name + '.csv',
        sha256=hashlib.sha256(raw).hexdigest(), time_column='time_s', time_unit='s',
        display_unit='us' if period == PERIOD else 'ms', relative_time=True,
        slice_style='shade', arrange='grid' if check(lay, get_profile('single')).ok else 'stack', window=[0, period],
        boundaries=[q * period for q in edges], intervals=[m['name'] for m in lay.modes],
        channels=[dict(column=c, label=c.rsplit('_', 1)[0], unit=c.rsplit('_', 1)[1], color=COLORS[i])
                  for i, c in enumerate(columns)], circuit_signature=circuit_signature(lay),
        notes=[dict(kind='text', channel=columns[-1] if name.startswith('three_phase_') else columns[0],
                    at=[period * .7, 4 if name == 'single_phase_diode_bridge' else .65],
                    text='Ideal analytical reference')])
    return table, raw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    out = parser.parse_args().directory
    out.mkdir(parents=True, exist_ok=False)
    (out / 'waveforms-data').mkdir()
    for name in NAMES:
        table, raw = example(name)
        (out / (name + '.json')).write_text(json.dumps(table, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        (out / 'waveforms-data' / (name + '.csv')).write_bytes(raw)
    print(f'Created {len(NAMES)} ideal analytical examples in {out}')


if __name__ == '__main__':
    main()
