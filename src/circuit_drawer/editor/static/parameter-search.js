/* Each batch owns disposable simulations; adoption only edits the SPICE form. */
function initParameterSearch(currentDraft, accept, searchProfile) {
  const host = document.getElementById('spiceParameterSearch'), dialog = host.closest('dialog');
  host.innerHTML = `<summary>参数范围搜索…</summary>
    <div class="help-row"><span class="hint">填写范围和输出目标，比较完整仿真结果后采用一组参数。</span>
    <details class="help-tip"><summary aria-label="搜索说明" title="搜索说明">?</summary><div class="help-body"><ol>
      <li>支持定频开环理想开关电路，最多 32 组组合；每组使用窗口中的完整瞬态时长和积分步长。</li>
      <li>纹波取末尾完整周期的峰峰值；过冲取从启动到结束、沿目标电压方向的最大超出值。百分数以目标电压绝对值为基准。</li>
      <li>通过末尾八周期重复性检查及全部约束后才能采用。Pareto 候选表示没有另一组在所有指标上都不差，且至少一项更好。</li>
      <li>同一范围可选多个同步参数，例如全桥的全部开关频率。按 Ctrl 多选；各行范围取所有组合。</li>
      <li>填写损耗与磁件数据文件后，按约束筛选，再比较估算效率和磁件包络体积。需安装 powerdesignkit；数据文件路径相对于当前电路文件。</li>
    </ol></div></details></div>
    <fieldset id="psSettings" style="border:0;padding:0">
      <button type="button" id="psCatalog">读取当前可调参数</button>
      <div id="psConfig" hidden><div id="psRanges"></div><button type="button" id="psAdd">添加范围</button>
      <div class="spice-fields"><label>输出电压通道<select id="psVoltage"></select></label>
        <label>目标直流电压（V）<input id="psTarget" placeholder="例如 12"></label>
        <label>输出误差上限（%）<input id="psError" type="number" min="0" value="5"></label>
        <label>纹波上限（%，选填）<input id="psRipple" type="number" min="0"></label>
        <label>过冲上限（%，选填）<input id="psOvershoot" type="number" min="0"></label></div>
      <label>损耗与磁件数据文件（选填）<input id="psPowerFile" style="width:100%" placeholder="填写 power-data.json 路径"></label>
      <button type="button" id="psRun">运行范围搜索</button></div>
    </fieldset><button type="button" id="psCancel" hidden>取消搜索</button>
    <p id="psStatus" class="hint" role="status" aria-live="polite"></p>
    <div id="psResults"></div><button type="button" id="psApply" hidden disabled>采用选中参数到窗口</button>
    <p class="hint">采用后核对参数，再点击“保存参数”或导出。原仿真文件和图纸不会被搜索覆盖。</p>`;
  const $ = id => host.querySelector('#' + id), alive = () => host.isConnected && dialog.open;
  let catalog = null, result = null, jobId = null, selected = null, cancelled = false, serial = 0;
  let drawingSnapshot = null, draftSnapshot = null, configSnapshot = null;
  const present = token => alive() && serial === token;
  const fmt = value => Number.isFinite(value) ? value.toPrecision(5) : '—';
  function localizePlot() {
    for (const label of host.querySelectorAll('[data-ps-label]'))
      label.textContent = window.cdI18n?.translate(label.dataset.psLabel) || label.dataset.psLabel;
    for (const element of host.querySelectorAll('[data-ps-aria]'))
      element.setAttribute('aria-label', window.cdI18n?.translate(element.dataset.psAria) || element.dataset.psAria);
    for (const element of host.querySelectorAll('[data-ps-parameter]')) {
      const p = catalog.parameters[element.dataset.psParameter];
      element.textContent = p.label.split(' · ').map(s=>window.cdI18n?.translate(s) || s).join(' · ') +
        (element.tagName === 'OPTION' ? ' · ' + p.value : '');
    }
  }
  document.addEventListener('editor-language-change', localizePlot);
  dialog.addEventListener('close', () => document.removeEventListener('editor-language-change', localizePlot), {once:true});
  function invalidate() {
    result = null; selected = null; $('psApply').disabled = true; $('psApply').hidden = true;
    $('psResults').replaceChildren();
  }
  function busy(value, canCancel = true) {
    $('psSettings').disabled = value; $('psCancel').hidden = !value || !canCancel;
    $('psResults').inert = value;
    document.getElementById('spiceOptions').disabled = value;
    document.getElementById('spiceBuck').disabled = document.getElementById('spiceBoost').disabled = value;
    document.getElementById('spiceRecommendation').inert = value;
  }
  function fail(error) { $('psStatus').className = 'bad'; $('psStatus').textContent = error.message; }
  function addRange() {
    const row = document.createElement('div'); row.className = 'spice-fields'; row.dataset.psRange = '';
    const preferred = Object.keys(catalog.parameters).find(key => /\.L\w*\.value$/.test(key));
    row.innerHTML = `<label>参数（可多选）<select data-ps="paths" multiple size="3">${Object.entries(catalog.parameters)
      .map(([key,p],i)=>`<option value="${esc(key)}" translate="no" data-ps-parameter="${esc(key)}" ${(preferred ? key === preferred : i === 0) ? 'selected' : ''}>${esc(p.label)} · ${esc(String(p.value))}</option>`).join('')}</select></label>
      <label>下限<input data-ps="min" placeholder="例如 100u"></label><label>上限<input data-ps="max" placeholder="例如 1m"></label>
      <label>点数<input data-ps="count" type="number" min="2" max="32" value="3"></label>
      <label>间隔<select data-ps="spacing"><option value="linear">线性</option><option value="log">对数</option></select></label>
      <button type="button" data-ps="remove">移除此范围</button>`;
    row.querySelector('[data-ps="remove"]').onclick = () => { row.remove(); invalidate(); };
    $('psRanges').append(row); invalidate(); localizePlot();
  }
  function config() {
    const limits = {error_pct: $('psError').value};
    if ($('psRipple').value !== '') limits.ripple_pct = $('psRipple').value;
    if ($('psOvershoot').value !== '') limits.overshoot_pct = $('psOvershoot').value;
    return {ranges: [...host.querySelectorAll('[data-ps-range]')].map(row => {
      const field = name => row.querySelector(`[data-ps="${name}"]`);
      return {paths: [...field('paths').selectedOptions].map(o=>o.value), min:field('min').value,
        max:field('max').value, count:Number(field('count').value), spacing:field('spacing').value};
    }), voltage:$('psVoltage').value, target_v:$('psTarget').value, limits,
      ...($('psPowerFile').value.trim() ? {power_file:$('psPowerFile').value.trim()} : {})};
  }
  $('psSettings').oninput = () => { invalidate(); $('psStatus').textContent = '设置已修改，请重新运行搜索。'; };
  $('psAdd').onclick = addRange;
  $('psCatalog').onclick = async () => {
    const token = ++serial; invalidate(); $('psSettings').disabled = true;
    try {
      const draft = currentDraft(), snapshot = JSON.stringify(draft);
      const response = await api('/api/parameter-search-info', {layout:draft, profile:searchProfile});
      if (!present(token)) return;
      if (snapshot !== JSON.stringify(currentDraft())) throw new Error('窗口参数已修改，请重新读取可调参数。');
      catalog = response; $('psRanges').replaceChildren();
      $('psVoltage').innerHTML = catalog.voltages.map(v=>`<option translate="no">${esc(v)}</option>`).join('');
      $('psConfig').hidden = false; addRange();
      $('psStatus').className = 'hint'; $('psStatus').textContent = '请选择范围，并填写目标直流电压。';
    } catch (error) { if (present(token)) fail(error); }
    finally { if (present(token)) $('psSettings').disabled = false; }
  };
  function select(id) {
    selected = id;
    for (const input of host.querySelectorAll('[data-ps-choice]')) input.checked = Number(input.value) === id;
    $('psApply').disabled = !result?.complete || cancelled;
  }
  function show(points, completed) {
    const status = {passed:'通过', rejected:'超出约束', unsettled:'尚未周期稳定', failed:'仿真失败', cancelled:'已取消'};
    const pareto = new Set(result?.pareto_ids || []);
    const power = !!$('psPowerFile').value.trim();
    $('psResults').innerHTML = `<div id="psPlot"></div><div style="overflow:auto"><table style="width:100%;text-align:left">
      <thead><tr><th>选择</th><th>候选</th><th>状态</th><th>输出均值（V）</th><th>纹波（%）</th><th>过冲（%）</th>${power ? '<th>估算效率（%）</th><th>磁件包络体积（cm³）</th>' : ''}<th>详情</th></tr></thead><tbody>
      ${points.map(p=>`<tr data-ps-point="${p.id}"><td><input type="radio" name="psCandidate" data-ps-choice value="${p.id}" aria-label="${esc('候选 ' + p.id)}" ${completed && p.feasible ? '' : 'disabled'}></td>
        <td translate="no">${p.id}${pareto.has(p.id) ? ' · Pareto' : ''}</td><td>${status[p.status] || esc(p.status)}</td>
        <td translate="no">${fmt(p.metrics?.mean_v)}</td><td translate="no">${fmt(p.metrics?.ripple_pct)}</td><td translate="no">${fmt(p.metrics?.overshoot_pct)}</td>
        ${power ? `<td translate="no">${fmt(p.metrics?.efficiency_pct)}</td><td translate="no">${fmt(p.metrics?.magnetic_volume_cm3)}</td>` : ''}
        <td><details><summary>查看</summary><p>${esc(p.error || '')}</p>
          <div translate="no">${Object.entries(p.updates).map(([key,value])=>`<span data-ps-parameter="${esc(key)}">${esc(catalog.parameters[key]?.label || key)}</span>: ${esc(String(catalog.parameters[key]?.value))} → ${fmt(value)}`).join('<br>')}</div>
          ${p.metrics ? `<p><span>输出误差（%）</span> <span translate="no">${fmt(p.metrics.error_pct)}</span></p>` : ''}
          ${p.window_s ? `<p><span>测量区间（s）</span> <span translate="no">${p.window_s.map(fmt).join(' – ')} / T=${fmt(p.period_s)}</span></p><p><span>完整仿真时长（s）</span> <span translate="no">${fmt(p.stop_s)}</span></p>` : ''}
          ${(p.limits_exceeded || []).length ? `<p><span>超出约束</span> <span translate="no">${esc(p.limits_exceeded.join(', '))}</span></p>` : ''}
          ${(p.settling || []).filter(c=>!c.ok).map(c=>`<p translate="no">${esc(c.channel)}: ${fmt(c.drift)} &gt; ${fmt(c.tolerance)}</p>`).join('')}
          ${p.power ? `<p>${p.power.demo ? '<strong>合成演示数据</strong>' : ''} <span translate="no">${esc(p.power.source)}</span></p>
            <p><span>负载功率 / 所列总损耗 / 辅助损耗（W）</span> <span translate="no">${[p.power.output_watts,p.power.loss_watts,p.power.auxiliary_watts].map(fmt).join(' / ')}</span></p>
            <ol>${p.power.notes.map(note=>`<li>${esc(note)}</li>`).join('')}</ol>
            <table><thead><tr><th>器件</th><th>损耗（W）</th></tr></thead><tbody>${p.power.items.map(item=>`<tr><td translate="no">${esc(item.ref)}</td><td translate="no">${fmt(item.watts ?? (item.result.total_watts + (item.external_watts || 0)))}</td></tr>`).join('')}</tbody></table>` : ''}
          <p translate="no">${esc(p.directory || '')}</p></details></td></tr>`).join('')}</tbody></table></div>`;
    for (const input of host.querySelectorAll('[data-ps-choice]')) input.onchange = () => select(Number(input.value));
    localizePlot();
    if (!completed) return;
    const usable = points.filter(p=>p.feasible);
    if (!usable.length) { $('psPlot').textContent = '没有符合约束的候选，请查看每组原因。'; return; }
    const xkey = power ? 'magnetic_volume_cm3' : 'ripple_pct', ykey = power ? 'efficiency_pct' : 'overshoot_pct';
    const xmax = Math.max(...usable.map(p=>p.metrics[xkey]), 1e-12);
    const ymin = power ? Math.max(0, Math.min(...usable.map(p=>p.metrics[ykey])) - .1) : 0;
    const ymax = power ? Math.min(100, Math.max(...usable.map(p=>p.metrics[ykey])) + .1) : Math.max(...usable.map(p=>p.metrics[ykey]), 1e-12);
    const x = p => 55 + p.metrics[xkey] / xmax * 330, y = p => 215 - (p.metrics[ykey] - ymin) / (ymax - ymin) * 165;
    const xlabel = power ? '磁件包络体积（cm³）' : '纹波（%）', ylabel = power ? '估算效率（%）' : '过冲（%）';
    $('psPlot').innerHTML = `${points.some(p=>p.power?.demo) ? '<p><strong>合成演示数据</strong></p>' : ''}<p>蓝色为 Pareto 候选，灰色为其他合格候选。点击点或表格选择。</p><svg viewBox="0 0 430 260" style="width:100%;max-width:580px" role="group" data-ps-aria="${power ? '效率与磁件体积比较' : '纹波与过冲比较'}">
      <path d="M55 35V215H400" fill="none" stroke="currentColor"/><text x="130" y="252" data-ps-label="${xlabel}">${xlabel}</text><text x="8" y="22" data-ps-label="${ylabel}">${ylabel}</text>
      <text x="50" y="235">0</text><text x="350" y="235" translate="no">${fmt(xmax)}</text><text x="55" y="33" translate="no">${fmt(ymax)}</text>
      <text x="2" y="215" translate="no">${fmt(ymin)}</text>
      ${usable.map(p=>`<g data-ps-dot="${p.id}" tabindex="0" role="button" data-ps-aria="${esc('候选 ' + p.id)}" style="cursor:pointer">
        <circle cx="${x(p)}" cy="${y(p)}" r="6" fill="${pareto.has(p.id) ? '#2262bd' : '#808080'}"/><text x="${x(p)+8}" y="${y(p)-5}" translate="no">${p.id}</text>
        <title>${p.id}: ${fmt(p.metrics[xkey])}${power ? ' cm³' : '%'}, ${fmt(p.metrics[ykey])}%</title></g>`).join('')}</svg>`;
    localizePlot();
    for (const dot of host.querySelectorAll('[data-ps-dot]')) {
      dot.onclick = () => select(Number(dot.dataset.psDot));
      dot.onkeydown = event => { if (['Enter',' '].includes(event.key)) { event.preventDefault(); select(Number(dot.dataset.psDot)); } };
    }
  }
  async function requestCancel(id) {
    try { await api('/api/cancel', {id}); }
    catch (error) {
      const message = `结果不会采用，但取消请求未送达，后台任务可能仍在运行：${error.message}`;
      if (alive()) fail(new Error(message));
      flash(message, true);
    }
  }
  async function cancel() {
    cancelled = true; $('psApply').disabled = true;
    $('psStatus').textContent = '正在取消当前仿真…';
    if (jobId) await requestCancel(jobId);
  }
  $('psCancel').onclick = cancel;
  dialog.addEventListener('close', cancel, {once:true});
  $('psRun').onclick = async () => {
    const token = ++serial; invalidate(); cancelled = false; jobId = null; busy(true);
    $('psStatus').className = 'hint'; $('psStatus').textContent = '正在准备完整仿真…';
    try {
      const draft = currentDraft(), settings = config();
      drawingSnapshot = JSON.stringify(layout); draftSnapshot = JSON.stringify(draft); configSnapshot = JSON.stringify(settings);
      let job = await api('/api/parameter-search', {layout:draft, profile:searchProfile, config:settings});
      jobId = job.id;
      if (present(token)) { catalog = job.catalog; localizePlot(); }
      if (!present(token) || cancelled) await requestCancel(job.id);
      while (present(token) && job.state === 'running') {
        $('psStatus').textContent = cancelled ? '正在取消当前仿真…' : `已完成 ${job.points.length} / ${job.total} 组（${job.elapsed_s} 秒）`;
        show(job.points, false);
        await new Promise(resolve=>setTimeout(resolve,500));
        if (!present(token)) return;
        job = await api('/api/job/' + job.id);
      }
      if (!present(token)) return;
      if (job.state === 'error') { show(job.points, false); throw new Error(job.error); }
      const completed = job.state === 'done' && !cancelled;
      result = job.result; show(result?.points || job.points, completed);
      $('psApply').hidden = !completed;
      $('psStatus').textContent = completed ? '搜索完成。请核对候选和测量区间，再选择一组参数。' : '搜索已取消，结果不能采用。';
    } catch (error) { if (present(token)) fail(error); }
    finally { if (present(token)) busy(false); }
  };
  $('psApply').onclick = async () => {
    if (!result?.complete || cancelled || selected === null) return;
    const token = serial, before = JSON.stringify(currentDraft());
    $('psApply').disabled = true; busy(true, false);
    try {
      if (JSON.stringify(layout) !== drawingSnapshot || before !== draftSnapshot || JSON.stringify(config()) !== configSnapshot)
        throw new Error('图纸、参数或搜索设置已改变，请重新搜索。');
      const proposal = await api('/api/parameter-search-apply', {id:jobId, selected, layout:currentDraft(), profile:searchProfile});
      if (!present(token)) return;
      if (before !== JSON.stringify(currentDraft()) || drawingSnapshot !== JSON.stringify(layout))
        throw new Error('窗口参数已修改，请重新搜索。');
      accept(proposal); result = null; $('psApply').hidden = true;
      $('psStatus').textContent = '已采用到本窗口参数；核对后保存或导出。';
    } catch (error) { if (present(token)) fail(error); }
    finally { if (present(token)) busy(false); }
  };
}
