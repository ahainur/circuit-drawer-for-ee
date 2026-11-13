"""Explicit, captured component data for a parameter batch's loss comparison."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from . import spice

FORMAT = "circuit-drawer.power-design/1"
MAX_FILE_BYTES = 10 * 1024 * 1024  # each local JSON input, never a manufacturer PDF
FIELDS = {"format", "source", "demo", "load", "devices", "magnetics", "capacitors",
          "auxiliary_watts", "current_threshold_a"}
MEASURED = {"fsw", "v_block", "i_rms", "i_avg", "i_on", "i_off", "i_rev", "t_rev"}
MATCH_RTOL = 1e-9  # unit conversion and floating-point range interpolation only


class PowerError(ValueError):
    pass


def positive_moments(times, values, threshold=0.):
    """Duration, charge and squared-current integral strictly above a threshold."""
    durations, charges, squares = [], [], []
    for ta, tb, a, b in zip(times, times[1:], values, values[1:]):
        if max(a, b) <= threshold:
            continue
        left = 0. if a > threshold else (threshold - a) / (b - a)
        right = 1. if b > threshold else (threshold - a) / (b - a)
        va, vb = a + (b - a) * left, a + (b - a) * right
        dt = (tb - ta) * (right - left)
        durations.append(dt)
        charges.append((va / 2 + vb / 2) * dt)
        squares.append((va * va + va * vb + vb * vb) / 3 * dt)
    return tuple(math.fsum(part) for part in (durations, charges, squares))


def switching_currents(times, data, ref, end, period):
    """First/last samples on the fully-on plateau of the last complete pulse."""
    gate, currents = data['gate:' + ref], data['current:' + ref]
    start, pulses = None, []
    for i, value in enumerate(gate):
        if value == 1.0 and start is None:
            start = i
        elif value != 1.0 and start is not None:
            if start > 0 and times[i - 1] <= end and times[start] >= end - 2 * period:
                pulses.append((start, i - 1))
            start = None
    if not pulses:
        raise PowerError(f"{ref} 没有完整的 0/1 PWM 导通平台，不能提取开关电流。")
    a, b = pulses[-1]
    return currents[a], currents[b], [times[a], times[b]]


class PowerDesign:
    def __init__(self, file, physical):
        try:
            from powerdesignkit import magnetics
            from powerdesignkit.device import validate
            from powerdesignkit.losses import models, operating
        except ImportError as exc:
            raise PowerError("请安装或更新 powerdesignkit，需包含磁件计算与器件快照接口。") from exc
        self.magnetics, self.validate, self.models, self.operating = magnetics, validate, models, operating
        self.files = {}
        self.path = Path(file).resolve()
        self.spec = self._read(self.path)
        d = self.spec
        if not isinstance(d, dict) or set(d) != FIELDS or d.get("format") != FORMAT:
            raise PowerError(f"损耗数据须使用 {FORMAT}，并填写：{', '.join(sorted(FIELDS))}。")
        if not isinstance(d['source'], str) or not d['source'].strip() or type(d['demo']) is not bool:
            raise PowerError("损耗数据需注明 source，并明确 demo 演示标识。")
        self.source = d['source']
        self.auxiliary = spice.number(d['auxiliary_watts'], 'auxiliary_watts')
        self.threshold = spice.number(d['current_threshold_a'], 'current_threshold_a')
        if self.auxiliary < 0 or self.threshold <= 0:
            raise PowerError("辅助损耗不能为负，导通电流阈值须大于零。")
        elements = {e.id: e for e in physical.elements if e.kind != 'port'}
        if any(e.kind not in {'V', 'R', 'L', 'C', 'D', 'nmos'} for e in elements.values()):
            raise PowerError("当前损耗映射支持 V、R、L、C、二极管与 nMOS；变压器、耦合电感及其他器件待接入。")
        if not isinstance(d['load'], str) or d['load'] not in elements or elements[d['load']].kind != 'R':
            raise PowerError("load 需要指定图纸中的一个电阻负载。")
        self.load = d['load']
        for section, kinds in [('devices', {'D', 'nmos'}), ('magnetics', {'L'}), ('capacitors', {'C'})]:
            want = {e.id for e in elements.values() if e.kind in kinds}
            if not isinstance(d[section], dict) or set(d[section]) != want:
                raise PowerError(f"{section} 必须逐一提供当前实际器件的数据：{', '.join(sorted(want))}。")
        self.devices, self.inductors, self.capacitors = {}, {}, {}
        for ref, item in d['devices'].items():
            if not isinstance(item, dict) or set(item) != {'file', 'operating'} or not isinstance(item['operating'], dict):
                raise PowerError(f"{ref} 需填写器件 file 与静态 operating 工况。")
            op = item['operating']
            if set(op) - operating.FIELDS.keys() or set(op) & (MEASURED | {'format', 'device', 'role', 'profile'}):
                raise PowerError(f"{ref} 的电流、电压与频率由本次仿真提取；operating 只填写温度、驱动、统计列与恢复假设。")
            if type(op.get('hard_recovery')) is not bool:
                raise PowerError(f"{ref} 必须明确填写 hard_recovery 假设。")
            raw = self._read_reference(item['file'])
            issues = validate.errors(validate.check(raw))
            if issues:
                raise PowerError(f"{ref} 器件文件未通过 powerdesignkit 校验：{issues[0]}")
            if raw.get('technology') == 'gan':
                raise PowerError("当前理想开关波形未建模 GaN 第三象限通路，不能用于此损耗映射。")
            self.devices[ref] = (raw, {'format': operating.FORMAT, 'device': item['file'],
                                     'role': 'switch' if elements[ref].kind == 'nmos' else 'diode', **op})
        for ref, files in d['magnetics'].items():
            if not isinstance(files, list) or not 1 <= len(files) <= 32:
                raise PowerError(f"{ref} 需提供 1–32 份对应不同电感值的明确设计文件。")
            designs = [self._read_reference(file) for file in files]
            for design in designs:
                errors = magnetics.check(design)
                if errors:
                    raise PowerError(f"{ref} 磁件设计错误：{errors[0]}")
                if design['demo'] and not d['demo']:
                    raise PowerError("磁件使用演示数据时，整组损耗数据也必须标为 demo。")
            self.inductors[ref] = designs
        for ref, item in d['capacitors'].items():
            if not isinstance(item, dict) or set(item) != {'esr_ohm', 'source'} or not isinstance(item['source'], str) or not item['source'].strip():
                raise PowerError(f"{ref} 须填写 esr_ohm 与对应频率、温度条件的 source。")
            esr = spice.number(item['esr_ohm'], ref + '.esr_ohm')
            if esr < 0:
                raise PowerError(f"{ref} 的 ESR 不能为负；采用理想电容时须明确填写 0 及来源说明。")
            self.capacitors[ref] = esr
        self.signature = self._signature()

    def _read(self, path):
        try:
            with path.open('rb') as stream:
                raw = stream.read(MAX_FILE_BYTES + 1)
            if len(raw) > MAX_FILE_BYTES:
                raise PowerError(f"数据文件超过 10 MiB：{path}")
            value = json.loads(raw)
        except (OSError, ValueError) as exc:
            raise PowerError(f"无法读取损耗数据 {path}：{exc}") from exc
        self.files[path] = raw
        return value

    def _read_reference(self, file):
        if not isinstance(file, str) or not file.strip():
            raise PowerError("器件和磁件文件路径必须为非空文字。")
        return self._read((self.path.parent / file).resolve())

    def _signature(self):
        return hashlib.sha256(json.dumps({str(p): hashlib.sha256(raw).hexdigest() for p, raw in self.files.items()}, sort_keys=True).encode()).hexdigest()

    def unchanged(self):
        try:
            for path, raw in self.files.items():
                with path.open('rb') as stream:
                    if stream.read(MAX_FILE_BYTES + 1) != raw:
                        return False
            return True
        except OSError:
            return False

    def save_sources(self, folder):
        folder.mkdir()
        manifest = {}
        for index, (path, raw) in enumerate(self.files.items(), 1):
            name = f'{index:03d}-{path.name}'
            (folder / name).write_bytes(raw)
            manifest[str(path)] = {'snapshot': name, 'sha256': hashlib.sha256(raw).hexdigest()}
        (folder / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')

    def vectors(self, deck, aliases):
        vectors = {}
        for ref, (_, op) in self.devices.items():
            pins = ('d', 's') if op['role'] == 'switch' else ('a', 'b')
            nodes = [deck.pin_nodes[f'{aliases.get(ref, ref)}.{pin}'] for pin in pins]
            vectors['power-voltage:' + ref] = ' - '.join('0' if n == '0' else f'v({n})' for n in nodes)
        return vectors

    def evaluate(self, physical, times, data, window):
        from .parameter_search import interval, mean
        begin, end = window
        period = end - begin
        columns = {name: interval(times, values, begin, end)[1] for name, values in data.items()}
        ts = interval(times, next(iter(data.values())), begin, end)[0]
        components = physical.extra['simulation']['components']
        output = spice.number(components[self.load]['value'], self.load) * mean(ts, columns['current:' + self.load], square=True)
        if output <= 0 or not math.isfinite(output):
            raise PowerError("所选电阻负载没有有限的正输出功率，不能比较效率。")
        loss, volume, items = self.auxiliary, 0., []
        for ref, (raw, settings) in self.devices.items():
            op = deepcopy(settings)
            op.update(fsw=1 / period, v_block=max(map(abs, columns['power-voltage:' + ref])))
            currents = columns['current:' + ref]
            if op['role'] == 'switch':
                op['i_rms'] = math.sqrt(mean(ts, currents, square=True))
                op['i_on'], op['i_off'], pulse = switching_currents(times, data, ref, end, period)
                duration, charge, _ = positive_moments(ts, columns.get('body:' + ref, [0.] * len(ts)), self.threshold)
                op.update(t_rev=duration, i_rev=charge / duration if duration else 0.)
            else:
                _, charge, square = positive_moments(ts, currents)
                op.update(i_avg=charge / period, i_rms=math.sqrt(square / period))
                pulse = None
            issues, device = self.operating.check(op, device_data=raw)
            errors = [issue for issue in issues if issue.level == 'error']
            if errors:
                raise PowerError(f'{ref} 工况未通过 powerdesignkit 校验：{errors[0]}')
            try:
                result = self.models.calculate(device, op)
            except self.models.Missing as exc:
                raise PowerError(f'{ref}：{exc}') from exc
            limit = device.get('tj_max')
            if not result.converged or (limit and result.tj > limit.value):
                raise PowerError(f'{ref} 结温计算未收敛或超过器件温度上限。')
            external = 0.
            for item in result.items:
                if 'driver_total_watts' in item.extra:
                    external += item.extra['driver_total_watts'] - item.watts
                if 'opposite_switch_watts' in item.extra:
                    external += item.extra['opposite_switch_watts']
                elif item.key == 'recovery' and device.kind == 'diode' and device.technology in {'schottky', 'sic'}:
                    raise PowerError(f'{ref} 缺少结电容电荷 Qc，不能把对侧开关附加损耗当成零。')
            loss += result.total + external
            items.append({'ref': ref, 'kind': 'semiconductor', 'result': result.to_json(),
                          'operating': op, 'pulse_s': pulse, 'external_watts': external})
        for ref, designs in self.inductors.items():
            value = spice.number(components[ref]['value'], ref)
            matching = [d for d in designs if math.isclose(d['inductance'], value, rel_tol=MATCH_RTOL, abs_tol=0)]
            if len(matching) != 1:
                raise PowerError(f'{ref}={value:g} H 需要且只能有一份匹配的磁件设计；不自动猜测匝数和体积。')
            result = self.magnetics.calculate(matching[0], ts, columns['current:' + ref])
            loss += result['total_watts']; volume += result['envelope_volume_m3']
            items.append({'ref': ref, 'kind': 'magnetic', 'result': result})
        for ref, esr in self.capacitors.items():
            watts = esr * mean(ts, columns['current:' + ref], square=True)
            loss += watts
            items.append({'ref': ref, 'kind': 'capacitor', 'watts': watts, **self.spec['capacitors'][ref]})
        for element in physical.elements:
            if element.kind == 'R' and element.id != self.load:
                watts = spice.number(components[element.id]['value'], element.id) * mean(ts, columns['current:' + element.id], square=True)
                loss += watts
                items.append({'ref': element.id, 'kind': 'resistor', 'watts': watts})
        efficiency = output / (output + loss) * 100
        if not all(math.isfinite(v) and v >= 0 for v in (loss, volume, efficiency)) or efficiency > 100:
            raise PowerError("损耗汇总出现无效数值，请核对模型与输入。")
        return {'source': self.source, 'demo': self.spec['demo'], 'output_watts': output, 'loss_watts': loss,
                'auxiliary_watts': self.auxiliary, 'efficiency_pct': efficiency, 'magnetic_volume_m3': volume,
                'items': items, 'notes': ['效率由电阻负载功率/(负载功率+所列模型损耗)估算，不叠加 SPICE 的开关导通损耗。',
                    '磁件体积仅包括给定磁件的整件包络，不代表变换器总体积。',
                    '开关电流取最后完整 PWM 导通平台首尾，RMS 与正向平均值按完整周期积分。',
                    '理想开关波形与手册模型未联合求解；损耗、磁件和温度假设须核对后使用。']}
