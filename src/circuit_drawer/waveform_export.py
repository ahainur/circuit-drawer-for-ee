"""Native result scripts produce the numeric CSV used by the waveform editor."""
from __future__ import annotations

import csv
import io

from .waveforms import MAX_BYTES, MAX_ROWS


def _writer(columns):
    header = io.StringIO()
    csv.writer(header, lineterminator="").writerow(columns)
    text = header.getvalue().replace("'", "''")
    return f"""if size(data, 1) < 2 || size(data, 2) ~= {len(columns)} || any(~isfinite(data(:)))
    error('CircuitDrawer:WaveformData', 'Expected finite scalar output channels and at least two samples.');
end
if size(data, 1) > {MAX_ROWS}
    error('CircuitDrawer:WaveformRows', 'CSV exceeds the waveform editor row limit. Select a shorter interval.');
end
if any(diff(data(:, 1)) <= 0)
    error('CircuitDrawer:WaveformTime', 'Time must increase strictly.');
end
folder = fileparts(destination);
if isempty(folder), folder = pwd; end
temporary = tempname(folder);
fid = fopen(temporary, 'w');
if fid < 0, error('CircuitDrawer:WaveformFile', 'Cannot create CSV.'); end
try
    fprintf(fid, '%s\\n', '{text}');
    fprintf(fid, ['%.17g' repmat(',%.17g', 1, size(data, 2)-1) '\\n'], data.');
    [message, number] = ferror(fid);
    status = fclose(fid); fid = -1;
    if number || status, error('CircuitDrawer:WaveformWrite', '%s', message); end
    info = dir(temporary);
    if info.bytes > {MAX_BYTES}
        error('CircuitDrawer:WaveformSize', 'CSV exceeds the waveform editor file limit. Select a shorter interval.');
    end
    [ok, message] = movefile(temporary, destination, 'f');
    if ~ok, error('CircuitDrawer:WaveformPublish', '%s', message); end
catch failure
    if fid >= 0, fclose(fid); end
    if exist(temporary, 'file'), delete(temporary); end
    rethrow(failure);
end
disp(['Import this CSV in Circuit Drawer > Waveform publication: ' destination]);
"""


def simulink_script(circuit, name):
    """Accept a completed SimulationOutput; never start another simulation."""
    methods = ["previous" if out["kind"] == "drive" else "linear" for out in circuit.outputs]
    return f"""function destination = export_{name}_waveforms(result, destination)
% Pass the completed sim(model) output, for example export_{name}_waveforms(out).
% Duplicate event times use the last value. Multirate signals use the union
% of logged times, with linear analog interpolation and held drive signals.
if nargin < 2
    destination = fullfile(fileparts(mfilename('fullpath')), '{name}_simulink_waveforms.csv');
end
signals = result.yout;
if ~isa(signals, 'Simulink.SimulationData.Dataset') || signals.numElements ~= {len(methods)}
    error('CircuitDrawer:WaveformChannels', 'Output Dataset does not match this model.');
end
times = cell(1, signals.numElements);
values = cell(1, signals.numElements);
for k = 1:signals.numElements
    signal = signals.getElement(k).Values;
    if ~isa(signal, 'timeseries') || numel(signal.Data) ~= numel(signal.Time)
        error('CircuitDrawer:WaveformScalar', 'Expected scalar timeseries outputs.');
    end
    original = signal.Data(:);
    if any(~isfinite(signal.Time(:))) || any(~isfinite(original)) || any(diff(signal.Time(:)) < 0)
        error('CircuitDrawer:WaveformFinite', 'Expected finite values and chronological times.');
    end
    [times{{k}}, indices] = unique(signal.Time(:), 'last');
    values{{k}} = original(indices);
    if isempty(times{{k}}) || any(~isfinite(times{{k}})) || any(~isfinite(values{{k}}))
        error('CircuitDrawer:WaveformFinite', 'Output contains empty or nonfinite samples.');
    end
end
start = max(cellfun(@(t) t(1), times));
stop = min(cellfun(@(t) t(end), times));
time = unique(vertcat(times{{:}}));
time = time(time >= start & time <= stop);
data = zeros(numel(time), signals.numElements + 1);
data(:, 1) = time;
methods = {{{', '.join(repr(method) for method in methods)}}};
for k = 1:signals.numElements
    if numel(times{{k}}) < 2
        error('CircuitDrawer:WaveformSpan', 'Each channel needs at least two logged times.');
    end
    data(:, k+1) = interp1(times{{k}}, values{{k}}, time, methods{{k}});
end
{_writer(circuit.columns)}end
"""


def plecs_script(circuit, name):
    """Run from the model's Simulation Scripts menu, using top-level outputs."""
    tr = circuit.transient
    return f"""% Run this script from PLECS Simulation > Simulation scripts.
% The CSV path is printed on completion. Change destination to choose a folder.
destination = fullfile(pwd, '{name}_plecs_waveforms.csv');
options = struct('SolverOpts', struct('OutputTimes', unique([0:{tr['step']:.17g}:{tr['stop']:.17g} {tr['stop']:.17g}])));
result = plecs('simulate', options);
data = [result.Time(:), result.Values.'];
if any(~isfinite(data(:))) || any(diff(data(:, 1)) < 0)
    error('CircuitDrawer:WaveformFinite', 'Expected finite values and chronological times.');
end
% At an event with repeated timestamps, retain the value after the event.
[~, indices] = unique(data(:, 1), 'last');
data = data(indices, :);
{_writer(circuit.columns)}"""
