const ratioViews = {dc:{model:null, result:null}, dq:{model:null, result:null}};
let ratioRunning = false;

async function openImpedanceRatio(dq=false) {
  const view = ratioViews[dq ? 'dq' : 'dc'];
  const endpoint = dq ? 'dq-stability' : 'impedance-ratio';
  showDialog(dq ? 'dq 互联稳定性' : '源负载稳定性', '<div id="ratioPanel"><p role="status">读取设置…</p></div>');
  const panel = document.getElementById('ratioPanel');
  const active = () => panel.isConnected && document.getElementById('dlg').open;
  try {
    const info = await api('/api/' + endpoint + '-info', {});
    if (!active()) return;
    panel.innerHTML = `<div class="help-row"><span class="hint">载入示例或读取端口模型 JSON，再计算。</span>${helpTip(dq ? 'dq 互联说明' : '源负载稳定性说明', [esc(info.note), esc(info.description)])}</div>
      <details id="ratioSettings" open><summary>端口模型</summary><fieldset id="ratioFields">
      <label>示例 <select id="ratioExample">${info.labels.map((label, index) => `<option value="${index}">${esc(label)}</option>`).join('')}</select></label>
      <button id="ratioLoadExample">载入示例</button>
      <label>读取模型 JSON <input id="ratioFile" type="file" accept=".json,application/json"></label>
      <label>端口模型 JSON <textarea id="ratioModel" rows="14" spellcheck="false" style="display:block;width:100%;box-sizing:border-box;font-family:monospace;margin:8px 0"></textarea></label>
      <button id="ratioRun">计算阻抗比与极点</button></fieldset></details>
      <p id="ratioState" role="status"></p><div id="impedanceResult"></div>`;
    const $ = id => panel.querySelector('#' + id);
    const input = $('ratioModel');
    input.value = view.model ?? JSON.stringify(info.examples[0], null, 2);
    const state = (text, bad=false) => {
      if (active()) { $('ratioState').textContent = text; $('ratioState').className = bad ? 'bad' : 'hint'; }
    };
    input.oninput = () => {
      view.model = input.value;
      if (view.result) state('模型已编辑，显示上次计算结果。');
    };
    $('ratioLoadExample').onclick = () => {
      input.value = JSON.stringify(info.examples[Number($('ratioExample').value)], null, 2);
      input.oninput();
    };
    $('ratioFile').onchange = async () => {
      const file = $('ratioFile').files[0];
      if (!file) return;
      const before = input.value;
      try {
        const text = await file.text();
        if (active() && !ratioRunning && $('ratioFile').files[0] === file && input.value === before) {
          input.value = text.replace(/^\uFEFF/, ''); input.oninput();
        }
      } catch (error) { state(error.message, true); }
    };
    function showResult(result) {
      $('ratioSettings').open = false;
      const box = $('impedanceResult');
      box.replaceChildren();
      const title = document.createElement('h3');
      title.id = 'ratioVerdict';
      title.textContent = {stable:'所给模型渐近稳定', unstable:'所给模型不稳定', boundary:'所给模型位于临界或数值边界'}[result.verdict];
      // How the encirclement is counted is a method note: keep it behind "?".
      const head = document.createElement('div');
      head.className = 'help-row';
      head.append(title);
      head.insertAdjacentHTML('beforeend', helpTip('判据说明', [esc(dq
        ? '绕数按 det(I+T) 绕原点计算，P 与 Z 包含全部内部状态。行列式图显示所选频带的正、负频率轨迹，四项 T 曲线显示正频率。'
        : '绕数使用覆盖模型极点的完整右移轮廓。图中显示所选频带的正、负频率轨迹。')]));
      box.append(head);
      const count = document.createElement('p');
      count.textContent = result.nyquist
        ? `开环右半平面极点 P=${result.open_count.rhp}，顺时针绕数 N=${result.nyquist.clockwise}，闭环右半平面极点 Z=${result.closed_count.rhp}，Z=P+N。`
        : '闭环存在虚轴或近虚轴极点，请结合完整模型检查临界状态。';
      box.append(count);
      const image = document.createElement('img');
      image.alt = '阻抗比 Bode 与 Nyquist 图';
      image.style.width = '100%';
      image.plotSource = result.svg;
      box.append(image);
      if (dq) {
        const label = document.createElement('label');
        label.textContent = '曲线 ';
        const select = document.createElement('select');
        select.id = 'ratioPlot';
        select.setAttribute('aria-label', '曲线');
        const plots = {'det(I+T)':result.svg, ...result.plots};
        for (const name of Object.keys(plots)) select.add(new Option(name, name));
        select.onchange = () => { image.plotSource = plots[select.value]; localizeImpedancePlot(); };
        label.append(select);
        box.insertBefore(label, image);
      }
      localizeImpedancePlot();
      const distance = document.createElement('p');
      distance.textContent = dq
        ? `频带采样最小 |det(I+T)| = ${result.sampled_min_distance.toPrecision(5)}。`
        : `频带采样最小 |1+Tm| = ${result.sampled_min_distance.toPrecision(5)}。`;
      box.append(distance);
      const table = document.createElement('table');
      table.id = 'ratioPoles';
      table.style.borderSpacing = '12px 6px';
      table.innerHTML = '<thead><tr><th>闭环极点</th><th>实部 (rad/s)</th><th>虚部 (rad/s)</th></tr></thead>';
      const tbody = document.createElement('tbody');
      result.closed_poles.forEach((pole, index) => {
        const row = tbody.insertRow();
        [String(index+1), pole.real.toPrecision(7), pole.imag.toPrecision(7)].forEach(value => row.insertCell().textContent = value);
      });
      table.append(tbody);
      box.append(table);
      for (const [name, content, type] of [
        ['model.json', JSON.stringify(result.model, null, 2), 'application/json'],
        ['result.json', JSON.stringify({...result, svg:undefined, csv:undefined, plots:undefined}, null, 2), 'application/json'],
        [endpoint + '.csv', result.csv, 'text/csv'], [endpoint + '.svg', result.svg, 'image/svg+xml'],
        ...Object.entries(result.plots || {}).map(([name, svg]) => [name + '.svg', svg, 'image/svg+xml'])
      ]) {
        const link = document.createElement('a');
        link.textContent = name; link.download = name; link.style.marginRight = '1em';
        link.href = 'data:' + type + ';charset=utf-8,' + encodeURIComponent(content);
        box.append(link);
      }
    }
    $('ratioRun').onclick = async () => {
      if (ratioRunning) { state('已有阻抗比计算正在运行，完成后可重新打开查看。'); return; }
      view.model = input.value;
      ratioRunning = true;
      $('ratioFields').disabled = true;
      try {
        const model = JSON.parse(view.model);
        state('计算阻抗比与极点中…');
        view.result = await api('/api/' + endpoint, {model});
        if (active()) { showResult(view.result); state('计算完成，结果对应本次端口模型。'); }
        else flash(dq ? 'dq 互联计算完成，可重新打开 dq 互联稳定性窗口查看。' : '阻抗比计算完成，可重新打开源负载稳定性窗口查看。');
      } catch (error) {
        const message = error instanceof SyntaxError ? '模型 JSON 格式无效，请检查括号、逗号与引号' : error.message;
        if (active()) state(message, true); else flash(message, true);
      } finally {
        ratioRunning = false;
        if (active()) $('ratioFields').disabled = false;
      }
    };
    if (view.result) { showResult(view.result); state('显示上次端口模型的计算结果。'); }
    if (ratioRunning) state('已有阻抗比计算正在运行，完成后可重新打开查看。');
  } catch (error) { if (active()) panel.textContent = error.message; }
}
document.getElementById('impedanceRatio').onclick = () => openImpedanceRatio();
document.getElementById('dqStability').onclick = () => openImpedanceRatio(true);
