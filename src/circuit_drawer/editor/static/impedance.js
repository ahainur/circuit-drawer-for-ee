let impedanceRunning = false;
const impedanceViews = {dc:{options:{}, last:null}, dq:{options:{}, last:null}};

function localizeImpedancePlot() {
  const image = document.querySelector('#impedanceResult img');
  if (!image?.plotSource) return;
  const doc = new DOMParser().parseFromString(image.plotSource, 'image/svg+xml');
  doc.querySelectorAll('text').forEach(text => { text.textContent = cdI18n.translate(text.textContent); });
  image.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(new XMLSerializer().serializeToString(doc));
}
document.addEventListener('editor-language-change', localizeImpedancePlot);

async function openImpedance(dq = false) {
  const view = impedanceViews[dq ? 'dq' : 'dc'];
  const endpoint = dq ? 'dq-impedance' : 'impedance';
  showDialog(dq ? 'dq 阻抗矩阵' : '输出阻抗分析', '<div id="impedancePanel"><p role="status">读取设置…</p></div>');
  const panel = document.getElementById('impedancePanel');
  const snapshot = JSON.parse(JSON.stringify(layout));
  const active = () => panel.isConnected && document.getElementById('dlg').open;
  try {
    const info = await api('/api/' + endpoint + '-info', {layout:snapshot, profile});
    if (!active()) return;
    const probes = Array.isArray(snapshot.simulation?.probes) ? snapshot.simulation.probes.filter(probe => probe?.kind === 'voltage') : [];
    const defaults = info.defaults || [];
    const ports = dq ? ['A', 'B', 'C'].map((phase, index) => `<label class="export-row">${phase} <select id="impedanceSource${phase}" aria-label="${phase}">${info.sources.map(name =>
      `<option value="${esc(name)}" ${name === (view.sources?.[index] ?? defaults[index]) ? 'selected' : ''}>${esc(name)}</option>`).join('')}</select></label>`).join('')
      : `<label class="export-row">输出电压通道 <select id="impedanceProbe">${probes.map(probe => `<option value="${esc(probe.name)}">${esc(probe.name)}</option>`).join('')}</select></label>`;
    panel.innerHTML = `<div class="help-row"><span class="hint">${dq ? '按 A、B、C 顺序选择电网电压源，再运行扫频。' : '选择输出电压通道，再运行扫频。'}</span>${helpTip(dq ? 'dq 阻抗说明' : '输出阻抗说明', [esc(info.note),
        dq ? '可载入 grid_dq_impedance 模板。按 ABC 顺序选择电网电压源，分析设置随结果保存。' : '可载入 buck_impedance 模板。选择输出电压通道，分析设置随结果文件保存。'])}</div>
      <details id="impedanceSettings" open><summary>测量设置</summary>
      <fieldset id="impedanceFields"><legend>扫频与时域注入</legend>
      ${ports}
      ${Object.entries(info.inputs).map(([key,[label,value]]) => `<label class="export-row">${esc(label)} <input id="impedance_${key}" value="${esc(view.options[key] ?? value)}"></label>`).join('')}
      <button id="impedanceRun" title="${dq ? '逐频点注入 d、q 扰动，在时域中求阻抗矩阵' : '用 ngspice 做小信号 AC 扫频'}">${dq ? '运行 dq 时域扫频' : '运行 AC 扫频'}</button><button id="impedanceCompare" title="${dq ? '把注入幅值和步长减半重算，核对结果是否一致' : '在部分频点做时域注入，核对 AC 扫频结果'}">${dq ? '扫频并对照减半幅值与步长' : '扫频并对照时域注入'}</button>
      </fieldset></details><p id="impedanceState" role="status"></p><div id="impedanceResult"></div>`;
    const $ = id => panel.querySelector('#' + id);
    const state = (message, bad = false) => {
      if (!active()) return;
      $('impedanceState').textContent = message;
      $('impedanceState').className = bad ? 'bad' : 'hint';
    };
    function showResult(record) {
      const result = record.result;
      const box = $('impedanceResult');
      $('impedanceSettings').open = false;
      box.replaceChildren();
      const image = document.createElement('img');
      image.alt = '输出阻抗 Bode 与 Nyquist 图';
      image.style.width = '100%';
      image.plotSource = dq ? result.plots.dd : result.svg;
      if (dq) {
        const label = document.createElement('label');
        label.textContent = '矩阵项 ';
        const select = document.createElement('select');
        select.id = 'impedanceMatrixEntry';
        select.setAttribute('aria-label', '矩阵项');
        for (const axis of result.matrix_axes) select.add(new Option('Z' + axis, axis));
        select.onchange = () => { image.plotSource = result.plots[select.value]; localizeImpedancePlot(); };
        label.append(select);
        box.append(label);
      }
      box.append(image);
      localizeImpedancePlot();
      const summary = document.createElement('p');
      if (dq) {
        const op = result.records[0].operating;
        summary.textContent = `dq 工作点 vd=${op.vd.toPrecision(5)} V，vq=${op.vq.toPrecision(5)} V，id=${op.id.toPrecision(5)} A，iq=${op.iq.toPrecision(5)} A。`;
      } else summary.textContent = `测量通道 ${result.probe}，直流工作点 ${result.operating_voltage_v.toPrecision(6)} V。`;
      box.append(summary);
      for (const note of result.notes || []) {
        const detail = document.createElement('p');
        detail.textContent = note;
        box.append(detail);
      }
      if (result.comparison.length) {
        const table = document.createElement('table');
        table.style.borderSpacing = '12px 6px';
        table.style.textAlign = 'left';
        table.innerHTML = `<thead><tr><th>频率 (Hz)</th><th>${dq ? '最大分量相对差' : '相对误差'}</th><th>相邻窗口漂移</th><th>对照结果</th></tr></thead>`;
        const tbody = document.createElement('tbody');
        for (const item of result.comparison) {
          const row = tbody.insertRow();
          for (const value of [item.frequency_hz.toPrecision(5), (100 * item.relative_error).toFixed(3) + '%',
                              (100 * item.window_drift).toFixed(3) + '%', item.consistent ? '一致' : '请核对注入幅值、步长与稳定时间']) {
            row.insertCell().textContent = value;
          }
        }
        table.append(tbody);
        box.append(table);
      }
      const folder = document.createElement('p');
      folder.textContent = result.out_dir;
      folder.dataset.i18nSkip = '';
      box.append(folder);
      const plots = dq ? Object.entries(result.plots).map(([axis, svg]) => ['Z' + axis + '.svg', svg, 'image/svg+xml']) : [['impedance.svg', result.svg, 'image/svg+xml']];
      for (const [name, content, type] of [
        [endpoint + '.csv', result.csv, 'text/csv'], ...plots,
        ['result.json', JSON.stringify({...result, svg:undefined, plots:undefined}, null, 2), 'application/json']
      ]) {
        if (!content) continue;
        const link = document.createElement('a');
        link.download = name;
        link.href = 'data:' + type + ';charset=utf-8,' + encodeURIComponent(content);
        link.textContent = name;
        link.style.marginRight = '1em';
        box.append(link);
      }
    }
    async function calculate(compare) {
      if (impedanceRunning) {
        state('已有阻抗计算正在运行，完成后重新打开此窗口查看结果。');
        return;
      }
      view.options = Object.fromEntries(Object.keys(info.inputs).map(key => [key, $('impedance_' + key).value]));
      if (dq) view.sources = ['A', 'B', 'C'].map(phase => $('impedanceSource' + phase).value);
      const port = dq ? {sources:view.sources} : {probe:$('impedanceProbe').value};
      impedanceRunning = true;
      $('impedanceFields').disabled = true;
      state(dq ? 'ngspice 逐频点计算基线与 d/q 扰动中…关闭窗口后可重新打开查看结果。' : 'ngspice 计算中…关闭窗口后结果仍可在这里重新打开查看。');
      try {
        const result = await api('/api/' + endpoint + '-run', {layout:snapshot, profile, ...port,
          options:view.options, compare, out_dir:joinPath(defaultExportDir, endpoint)});
        view.last = {result, origin:editorTableSnapshot(snapshot)};
        if (active()) {
          showResult(view.last);
          state('计算完成，结果对应运行时的图纸快照。');
        } else flash(dq ? 'dq 阻抗计算完成，可重新打开“实验功能 → dq 阻抗矩阵…”查看。' : '阻抗计算完成，可重新打开“实验功能 → 输出阻抗分析…”查看。');
      } catch (error) {
        if (active()) state(error.message, true);
        else flash(error.message, true);
      } finally {
        impedanceRunning = false;
        if (active()) $('impedanceFields').disabled = false;
      }
    }
    $('impedanceRun').onclick = () => calculate(false);
    $('impedanceCompare').onclick = () => calculate(true);
    if (view.last) {
      showResult(view.last);
      state(view.last.origin === editorTableSnapshot(snapshot)
        ? '显示上次计算记录，修改参数后请重新计算。' : '图纸已变化，显示的是上次图纸快照的结果。');
    }
    if (impedanceRunning) state('已有阻抗计算正在运行，完成后重新打开此窗口查看结果。');
  } catch (error) {
    if (active()) panel.textContent = error.message;
  }
}
document.getElementById('impedance').onclick = () => openImpedance();
document.getElementById('dqImpedance').onclick = () => openImpedance(true);
