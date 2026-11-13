"""Run bundled grid-forming cases and record their actual controller signals.

From the source tree, after installing circuit-drawer-for-ee and ngspice:
    python examples/grid_forming/run.py --case parallel --out out/grid-forming

The standard editor/CLI simulation records the circuit probes. This companion
also records P, Q, oscillator frequency and voltage command from the same
ngspice solve. CSV files can be opened by the existing waveform editor.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import replace
import json
import math
from pathlib import Path
import shutil
import tempfile

from circuit_drawer import spice
from circuit_drawer.closed_loop import node
from circuit_drawer.layout import Layout

CASES = ('droop', 'vsm', 'parallel')


def recorded_deck(layout):
    """Add read-only records for the named signals in these authored examples."""
    deck = spice.make_deck(layout, 'double')
    units = ['u1', 'u2'] if 'u2_omega' in {
        b['id'] for b in layout.extra['closed_loop']['control']['blocks']
    } else ['u1']
    expressions = {}
    for unit in units:
        expressions.update({
            f'{unit}_P_W': f'v({node(unit + "_p")})',
            f'{unit}_Q_var': f'v({node(unit + "_q")})',
            f'{unit}_f_Hz': f'v({node(unit + "_omega")})/{2 * math.pi:.17g}',
            f'{unit}_E_V': f'v({node(unit + "_em")})',
        })
    commands = [f'let gfm_{i} = 0*time + ({value})' for i, value in enumerate(expressions.values())]
    lines = deck.text.splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith('wrdata waveforms.dat '))
    lines[index] += ' ' + ' '.join(f'gfm_{i}' for i in range(len(expressions)))
    lines[index:index] = commands
    return replace(deck, text='\n'.join(lines) + '\n', columns=deck.columns + list(expressions))


def summaries(rows):
    """Time-weighted means before and during the load pulse, in seconds."""
    columns = [key for key in rows[0] if key.endswith(('_P_W', '_Q_var', '_f_Hz', '_E_V'))]
    result = {}
    for label, begin, end in [('before_load', .4, .48), ('loaded', 1.0, 1.08)]:
        selected = [row for row in rows if begin <= row['time_s'] <= end]
        if len(selected) < 2:
            raise ValueError(f'The run does not cover the {label} measurement window')
        duration = selected[-1]['time_s'] - selected[0]['time_s']
        result[label] = {'window_s': [selected[0]['time_s'], selected[-1]['time_s']], 'mean': {
            key: sum((a[key] + b[key]) * (b['time_s'] - a['time_s']) / 2
                     for a, b in zip(selected, selected[1:])) / duration
            for key in columns
        }}
    return result


def run_case(case, out, *, max_step=None, timeout=120):
    """Keep existing results until the new complete solve and summary succeed."""
    if case not in CASES:
        raise ValueError(f'Choose a case from {CASES}')
    layout = Layout.load(Path(__file__).with_name('gfm_' + case + '.json'))
    if max_step is not None:
        layout.extra['simulation']['transient']['max_step'] = max_step
    deck = recorded_deck(layout)
    engine = spice.executable()
    if not engine:
        raise spice.SpiceError('Install ngspice or set CIRCUIT_DRAWER_NGSPICE to its executable')
    destination = Path(out) / case
    with tempfile.TemporaryDirectory(prefix='cd_gfm_') as folder:
        stage = Path(folder)
        spice._simulate(deck, engine, stage, timeout)
        with (stage / 'waveforms.csv').open(encoding='utf-8') as stream:
            rows = [{key: float(value) for key, value in row.items()} for row in csv.DictReader(stream)]
        summary = summaries(rows)
        (stage / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
        (stage / 'control.json').write_text(json.dumps(layout.extra['closed_loop']['control'], ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        (stage / 'circuit.json').write_text(json.dumps(layout.to_dict(), ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        destination.mkdir(parents=True, exist_ok=True)
        for name in ('circuit.cir', 'circuit.json', 'control.json', 'simulation.log', 'waveforms.csv', 'summary.json'):
            shutil.copyfile(stage / name, destination / name)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=(*CASES, 'all'), default='parallel')
    parser.add_argument('--out', type=Path, default=Path('out/grid-forming'))
    parser.add_argument('--max-step', help='Maximum ngspice step, e.g. 10u for a convergence comparison')
    args = parser.parse_args(argv)
    for case in CASES if args.case == 'all' else [args.case]:
        summary = run_case(case, args.out, max_step=args.max_step)
        print(case, json.dumps(summary, ensure_ascii=True))
        print((args.out / case / 'waveforms.csv').resolve())


if __name__ == '__main__':
    main()
