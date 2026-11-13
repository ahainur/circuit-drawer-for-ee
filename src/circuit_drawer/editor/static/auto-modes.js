/* Simulation proposals use the ordinary mode renderer and history transaction. */
window.openAutoModes = (settings=null) => {
  showDialog('从仿真识别模态', `<div id="autoModePanel">
    <div class="help-row"><span class="hint">先运行仿真，核对候选模态，再确认写入。</span><details class="help-tip"><summary aria-label="识别说明" title="识别说明">?</summary><div class="help-body"><ol>
      <li>支持普通电路及模块电路中的 Buck、Boost、LLC、DAB 定频开环理想开关与变压器模型。请核对完整周期的候选区间和方向后确认写入。</li>
      <li>区间以最后一个完整周期为基准。短区间完整保留，可逐项选择。</li>
      <li>电流正方向为 a→b、d→s。绕组 p 为 p1→p2，s 为 s1→s2，s1 为 s1→sct，s2 为 sct→s2，w3～w6 为 a→b。</li>
      <li>写入后可继续手动调整，一步撤销；点击编辑器“保存”后写入文件。</li>
    </ol></div></details></div>
    <fieldset id="autoModeSettings"><legend>识别设置</legend>
      <label>电流下限（A） <input id="amFloor" type="number" value="0.001" min="0" step="0.001"></label>
      <label>相对峰值阈值（%） <input id="amRelative" type="number" value="1" min="0" max="10" step="0.1"></label>
      <label>短区间标记（周期 %） <input id="amShort" type="number" value="0.5" min="0" max="5" step="0.1"></label>
      <p>按电感与绕组电流正、负、近零区分模态</p>
      <div>${(report.mode_currents || []).map(ref => `<label><input type="checkbox" data-am-inductor="${esc(ref)}" checked> <span translate="no">${esc(ref)}</span> </label>`).join('')}</div>
    </fieldset>
    <p><button id="amRun">运行仿真并识别</button> <button id="amCancel" hidden>取消识别</button></p>
    <p id="amStatus" role="status" aria-live="polite"></p>
    <fieldset id="amReview" style="border:0;padding:0"><div id="amResults"></div>
    <div id="amActions" hidden><p><label>写入方式 <select id="amMethod"><option value="append">追加</option><option value="replace">替换现有模态</option></select></label></p>
      <p><label><input type="checkbox" id="amBindWaveform"> 同时写入本次波形及区间绑定</label></p>
      <p id="amWaveReplaceNote" class="hint" hidden>现有波形设置和标注将被替换，可一次撤销。</p>
      <p class="hint">未勾选模态的区间会保留为空；绑定完整后可导出波形与模态总览。</p>
      <button id="amWavePreview">预览波形与分界</button><div id="amWaveImage"></div>
      <button id="amApply" title="写入后可一步撤销；点“保存”后才写入文件">确认写入选中模态</button></div></fieldset>
    </div>`);
  const host = document.getElementById('autoModePanel'), dialog = document.getElementById('dlg');
  const $ = id => host.querySelector('#' + id);
  if (settings && Array.isArray(settings.distinguish)) {
    $('amFloor').value = settings.current_floor;
    $('amRelative').value = settings.current_relative * 100;
    $('amShort').value = settings.short_fraction * 100;
    host.querySelectorAll('[data-am-inductor]').forEach(input => {
      input.checked = settings.distinguish.includes(input.dataset.amInductor);
    });
  }
  let jobId = null, result = null, snapshot = null, serial = 0;
  const alive = () => host.isConnected && dialog.open;
  const current = token => alive() && serial === token;
  const selection = () => Array.from(host.querySelectorAll('[data-am-id]'))
    .filter(row => row.querySelector('[data-am="selected"]').checked)
    .map(row => ({id:+row.dataset.amId, name:row.querySelector('[data-am="name"]').value,
      caption:row.querySelector('[data-am="caption"]').value}));
  const choice = () => JSON.stringify([selection(), $('amMethod').value]);
  const proposalRequest = waveform => ({layout:tableForSave(), profile, id:jobId, selection:selection(),
    replace:$('amMethod').value === 'replace', waveform});
  $('amReview').oninput = () => {
    $('amWaveImage').replaceChildren();
    $('amWaveReplaceNote').hidden = !layout.waveform || !$('amBindWaveform').checked;
  };
  function busy(value) {
    $('autoModeSettings').disabled = value;
    $('amRun').disabled = value;
    $('amCancel').hidden = !value;
  }
  async function cancel() {
    serial++;
    result = null;
    $('amActions').hidden = true;
    busy(false);
    $('amStatus').textContent = '已取消识别。正在执行的求解结束后会丢弃结果。';
    if (jobId) {
      try { await api('/api/cancel', {id:jobId}); }
      catch (error) { if (alive()) $('amStatus').textContent = error.message; }
    }
  }
  dialog.addEventListener('close', cancel, {once:true});
  $('amCancel').onclick = cancel;
  $('autoModeSettings').oninput = () => {
    result = null;
    $('amActions').hidden = true;
    $('amResults').replaceChildren();
    $('amStatus').textContent = '识别设置已修改，请重新运行。';
  };
  function show(proposal) {
    const begin = proposal.window[0], period = proposal.period_s;
    const fraction = time => ((time - begin) / period * 100).toFixed(3);
    const used = new Set((layout.modes || []).map(mode => mode.name));
    const stateNames = {on:'导通', off:'关断', diode:'体二极管导通'};
    let number = 1;
    $('amResults').innerHTML = `<details><summary>多周期重复性</summary><table><thead><tr><th>通道</th><th>漂移</th><th>容差</th></tr></thead>
      <tbody>${proposal.steady.map(c => `<tr><td translate="no">${esc(c.channel)}</td><td>${c.drift.toExponential(3)}</td><td>${c.tolerance.toExponential(3)}</td></tr>`).join('')}</tbody></table></details>
      ${proposal.short_variation ? '<p class="bad">短区间在不同周期有变化，请逐项核对。</p>' : ''}
      ${(proposal.previous_errors || []).map(error => `<p class="bad">${esc(zhMode(error))}</p>`).join('')}
      ${proposal.candidates.length > 12 ? '<p class="bad">候选超过 12 个，请仔细核对阈值与振荡。</p>' : ''}
      <ol id="amCandidates">${proposal.candidates.map(c => {
        while (used.has(`Mode ${number}`)) number++;
        const name = `Mode ${number++}`; used.add(name);
        return `<li data-am-id="${c.id}" style="padding:10px 0;border-bottom:1px solid var(--edge)">
          <label><input type="checkbox" data-am="selected" ${c.short ? '' : 'checked'}> 采用此模态</label>
          ${c.short ? '<strong>短区间（可选）</strong>' : ''} ${c.all_off ? '<strong>全部关断</strong>' : ''}
          <p><label>名称 <input data-am="name" value="${esc(name)}" maxlength="120"></label>
            <label>标题 <input data-am="caption" value="${esc(c.mode.caption || '')}" maxlength="2000"></label></p>
          <p><span>区间与占比</span> <span translate="no">${c.intervals.map(s => `${fraction(s.begin)}–${fraction(s.end)}% T`).join(' / ')} (${(c.fraction * 100).toFixed(3)}%)</span></p>
          <p><span>导通器件</span> <span translate="no">${esc(c.mode.on.join(', ') || '—')}</span></p>
          <p>${Object.entries(c.states).map(([id,state]) => `<span translate="no">${esc(id)}</span> <span>${stateNames[state]}</span>`).join(' / ')}</p>
          <p><span>电感与绕组电流符号</span> <span translate="no">${Object.entries(c.signs).map(([id,sign]) => `${esc(id)} ${sign > 0 ? '+' : sign < 0 ? '−' : '0'}`).join(', ')}</span></p>
          <p><span>导通集合相同的已有模态</span> <span translate="no">${esc(c.matches.join(', ') || '—')}</span></p>
          <p><span>回路方向相符的已有模态</span> <span translate="no">${esc(c.direction_matches.join(', ') || '—')}</span></p>
          ${c.uncertain.length ? `<p><span>接近电流阈值，请核对</span> <span translate="no">${esc(c.uncertain.join(', '))}</span></p>` : ''}
          ${c.notes.map(note => `<p class="bad">${esc(zhMode(note))}</p>`).join('')}
          <details><summary>各支路实测电流（A）</summary><p translate="no">${Object.entries(c.currents).map(([id,value]) => `${esc(id)} ${value.toPrecision(5)}`).join(' / ')}</p></details>
          <button data-am-preview="${c.id}">预览导通路径</button><div data-am-image="${c.id}"></div>
        </li>`;
      }).join('')}</ol>`;
    host.querySelectorAll('[data-am-preview]').forEach(button => {
      button.onclick = async () => {
        const candidate = proposal.candidates.find(c => c.id === +button.dataset.amPreview);
        const target = host.querySelector(`[data-am-image="${candidate.id}"]`);
        button.disabled = true; target.textContent = '编译中…';
        try {
          const preview = clone(snapshot); preview.modes = [candidate.mode];
          const response = await api('/api/preview', {layout:preview, profile, mode:0, grid:false});
          if (!target.isConnected || !alive()) return;
          const image = document.createElement('img');
          image.src = `data:image/png;base64,${response.png}`;
          image.alt = cdI18n.translate('候选模态导通路径');
          image.style.cssText = 'max-width:100%;max-height:180px;object-fit:contain';
          target.replaceChildren(image);
        } catch (error) { if (target.isConnected) target.textContent = error.message; }
        finally { button.disabled = false; }
      };
    });
  }
  $('amRun').onclick = async () => {
    const token = ++serial;
    result = null; jobId = null;
    snapshot = clone(tableForSave());
    $('amResults').replaceChildren(); $('amActions').hidden = true;
    $('amStatus').className = 'hint'; $('amStatus').textContent = '正在仿真并识别…';
    busy(true);
    const options = {current_floor:+$('amFloor').value, current_relative:+$('amRelative').value / 100,
      short_fraction:+$('amShort').value / 100,
      distinguish:Array.from(host.querySelectorAll('[data-am-inductor]:checked'), input => input.dataset.amInductor)};
    try {
      let job = await api('/api/auto-modes', {layout:snapshot, profile, options});
      if (!current(token)) { await api('/api/cancel', {id:job.id}); return; }
      jobId = job.id;
      while (job.state === 'running') {
        await new Promise(resolve => setTimeout(resolve, 500));
        if (!current(token)) return;
        job = await api('/api/job/' + jobId);
      }
      if (!current(token)) return;
      if (job.state !== 'done') throw new Error(job.error || '识别已取消。');
      result = job.result;
      show(result);
      const available = !!result.waveform_sha256;
      $('amBindWaveform').disabled = $('amWavePreview').disabled = !available;
      $('amBindWaveform').checked = available && !layout.waveform;
      $('amWaveReplaceNote').hidden = true;
      $('amStatus').textContent = result.reason || '请核对候选后确认写入。';
      $('amActions').hidden = !result.ready;
    } catch (error) {
      if (current(token)) { $('amStatus').className = 'bad'; $('amStatus').textContent = error.message; }
    } finally { if (current(token)) busy(false); }
  };
  $('amWavePreview').onclick = async () => {
    const token = serial, before = JSON.stringify(layout), chosen = choice();
    const target = $('amWaveImage'), button = $('amWavePreview');
    button.disabled = true; target.textContent = '编译中…';
    try {
      const request = proposalRequest(true);
      const response = await api('/api/auto-modes-apply', request);
      if (!current(token) || chosen !== choice() || before !== JSON.stringify(layout)) { target.replaceChildren(); return; }
      const preview = await api('/api/waveform-preview', {layout:{...request.layout, ...response}, profile});
      if (!current(token) || chosen !== choice() || before !== JSON.stringify(layout)) { target.replaceChildren(); return; }
      const image = document.createElement('img');
      image.src = `data:image/svg+xml;base64,${preview.svg}`;
      image.alt = cdI18n.translate('本次仿真的波形与模态分界');
      image.style.cssText = 'max-width:100%;max-height:360px;object-fit:contain';
      target.replaceChildren(image);
    } catch (error) { if (current(token) && chosen === choice()) target.textContent = error.message; }
    finally { button.disabled = false; }
  };
  $('amApply').onclick = async () => {
    if (!result?.ready) return;
    const token = serial, before = JSON.stringify(layout);
    $('amReview').disabled = true; $('autoModeSettings').disabled = true; $('amRun').disabled = true;
    try {
      const response = await api('/api/auto-modes-apply', proposalRequest($('amBindWaveform').checked));
      if (!current(token)) return;
      if (before !== JSON.stringify(layout)) throw new Error('图纸已修改，请核对后重新确认。');
      pushHistory(); layout.modes = response.modes; modeIdx = -1; modeLit = null;
      if (response.waveform) layout.waveform = response.waveform;
      changed(); dialog.close(); flash('已写入所选模态，可撤销。');
      if (response.waveform) window.openWaveforms();
    } catch (error) { if (current(token)) { $('amStatus').className = 'bad'; $('amStatus').textContent = error.message; } }
    finally { $('amReview').disabled = false; busy(false); }
  };
};
document.getElementById('autoModes').onclick = () => window.openAutoModes();

window.updateAutoModeStatus = () => {
  const notice = document.getElementById('simulationSourceNotice'), status = report?.auto_mode_status;
  if (!status) return;
  notice.hidden = !status.stale_modes.length && !status.waveform_stale;
  if (notice.hidden) return;
  const generated = status.stale_modes.length || layout.waveform?.mode_source;
  document.getElementById('simulationSourceText').textContent = generated
    ? (status.reason ? '当前图纸尚不能核对仿真来源，请先修正图纸或仿真设置。'
      : '电路或仿真参数已改变，已有仿真模态或绑定波形已过期。')
    : '电路或仿真参数已改变，已有波形已过期。';
  notice.title = status.reason || '';
  const button = document.getElementById('simulationSourceRefresh');
  button.textContent = generated ? '重新识别…' : '重新载入波形…';
  button.onclick = generated ? () => {
    const source = layout.modes[status.stale_modes[0]]?.source || layout.waveform?.mode_source;
    window.openAutoModes(source?.settings);
  } : async () => { await window.openWaveforms(); document.getElementById('waveImport').click(); };
};
