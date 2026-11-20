/* Composition keeps references to source drawings and exports through the common renderer. */
(() => {
  const tr = (text) => window.cdI18n?.translate(text) || text;
  const esc = (value) =>
    String(value ?? '').replace(
      /[&<>"']/g,
      (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
    );
  const snapshot = () => window.CircuitAgentEditor.snapshot();
  const documentKey = () => JSON.stringify(snapshot());
  const button = (action, text) =>
    `<button type="button" data-action="${action}">${esc(tr(text))}</button>`;
  const dialog = document.createElement('dialog');
  dialog.id = 'figureOutput';
  document.body.append(dialog);
  let busy = false,
    composition = null,
    inspection = null,
    page = 'checks';
  let inspectKey = '';
  let exportHandler = null;
  const element = (name) => dialog.querySelector(`[data-field="${name}"]`);
  const message = (text) => {
    dialog.querySelector('[role=status]').textContent = tr(text);
  };
  function shell(title, content) {
    dialog.innerHTML = `<header><h2>${esc(tr(title))}</h2>${button('close', '关闭')}</header>${content}<p role="status"></p>`;
    dialog.onclick = (event) => {
      const action = event.target.closest('[data-action]')?.dataset.action;
      if (action) handle(action, event.target.closest('[data-index]'));
    };
    window.cdI18n?.apply?.(dialog);
    if (!dialog.open) dialog.showModal();
  }
  dialog.addEventListener('cancel', (event) => {
    if (busy) event.preventDefault();
  });
  async function request(action, extra = {}) {
    const current = snapshot();
    const key = documentKey();
    let advice;
    try { advice = JSON.parse(localStorage.getItem('circuitDrawer.beautify.advice')); } catch {}
    const result = await api('/api/figure-output', {
      advice,
      action,
      layout: current.table,
      table: current.table,
      profile: current.profile,
      ...extra,
    });
    if (documentKey() !== key) throw new Error(tr('等待操作时图纸已改变，请重试。'));
    return result;
  }
  async function run(action) {
    if (busy) return;
    busy = true;
    dialog.setAttribute('aria-busy', 'true');
    dialog.querySelectorAll('button').forEach((item) => (item.disabled = true));
    message('处理中…');
    try {
      await action();
      return true;
    } catch (error) {
      message(error.message);
      return false;
    } finally {
      busy = false;
      dialog.removeAttribute('aria-busy');
      dialog.querySelectorAll('button').forEach((item) => (item.disabled = false));
      updateExportButtons();
    }
  }
  function install(table, label) {
    window.CircuitFigure.install(table, tr(label));
  }
  function preferences() {
    try {
      return JSON.parse(localStorage.getItem('circuitDrawer.export:' + snapshot().file)) || {};
    } catch {
      return {};
    }
  }
  function openChecks(comp = null, options = {}) {
    if (busy) return;
    page = 'checks';
    composition = comp;
    exportHandler = options.onExport || null;
    inspection = null;
    inspectKey = '';
    const pref = preferences();
    const formats = options.formats || pref.formats || ['pdf', 'svg'];
    shell(
      '导出检查',
      `<div class="figure-export-checks"></div>
      <div class="figure-export-previews"><figure><figcaption>${tr('彩色')}</figcaption><img data-color alt="${tr('彩色预览')}"></figure>
        <figure><figcaption>${tr('黑白打印')}</figcaption><img data-mono alt="${tr('黑白打印预览')}"></figure></div>
      <fieldset><legend>${tr('导出设置')} ${editorHint("figureExport")}</legend><label>${tr('目标文件夹')} <input data-field="folder" value="${esc(pref.out_dir || 'figures')}"></label>
      <div>${['pdf', 'svg', 'png', 'tex'].map((format) => `<label><input type="checkbox" data-format="${format}" ${formats.includes(format) ? 'checked' : ''}> ${format.toUpperCase()}</label>`).join(' ')}</div>
      <label>PNG DPI <input data-field="dpi" aria-describedby="hint-figureExport" type="number" min="72" max="2400" step="1" value="${pref.png_dpi || 600}"></label>
      <label><input data-field="remember" type="checkbox" checked> ${tr('导出成功后记住')}</label></fieldset>
      <footer>${button('inspect', '重新检查')}${button('fix-export', '修正后导出')}${button('export', '导出')}${button('close', '取消')}</footer>`,
    );
    run(inspect);
  }
  function updateExportButtons() {
    const ready = !busy && inspection && inspectKey === documentKey();
    const exportButton = dialog.querySelector('[data-action=export]');
    if (exportButton) exportButton.disabled = !ready;
    const fixButton = dialog.querySelector('[data-action=fix-export]');
    if (fixButton) {
      fixButton.hidden = !inspection?.fixes.length;
      fixButton.disabled = !ready;
    }
  }
  async function inspect() {
    inspection = null;
    inspectKey = '';
    const result = await request('inspect', { composition });
    inspection = result;
    inspectKey = documentKey();
    const rows = [
      ...result.checks,
      {
        label: '字体嵌入',
        value: result.fonts.note,
        target: 'PDF 字体全部嵌入',
        status: result.fonts.status,
      },
    ];
    dialog.querySelector('.figure-export-checks').innerHTML =
      `<table><thead><tr>${['状态', '检查项', '这张图', '要求'].map((t) => `<th>${tr(t)}</th>`).join('')}</tr></thead><tbody>${rows
        .map(
          (row) =>
            `<tr data-status="${row.status}"><td>${row.status === 'pass' ? '✓' : row.status === 'warn' ? '!' : '?'}</td><th>${esc(tr(row.label))}</th><td>${esc(tr(row.value))}</td><td>${esc(tr(row.target))}</td></tr>`,
        )
        .join('')}</tbody></table>`;
    dialog.querySelector('[data-color]').src = 'data:image/png;base64,' + result.png;
    dialog.querySelector('[data-mono]').src = 'data:image/png;base64,' + result.mono_png;
    dialog.querySelector('[data-action=export]').textContent = tr(
      rows.some((r) => r.status !== 'pass') ? '仍然导出' : '导出',
    );
    dialog.querySelector('[data-action=fix-export]').textContent =
      tr('修正后导出') + ` (${result.fixes.length})`;
    message('检查完成');
  }
  async function exportFiles(fix) {
    if (!inspection || inspectKey !== documentKey()) throw new Error(tr('图纸已改变，请重新检查'));
    const formats = [...dialog.querySelectorAll('[data-format]:checked')].map(
      (item) => item.dataset.format,
    );
    const out_dir = element('folder').value.trim(),
      png_dpi = Number(element('dpi').value);
    if (!formats.length || !out_dir) throw new Error(tr('请选择格式并填写目标文件夹'));
    if (fix) {
      for (const item of inspection.fixes) {
        const result = await request('fix', { fix: item });
        install(result.table, item.label);
      }
      await inspect();
    }
    const result = exportHandler
      ? await exportHandler({formats, out_dir, png_dpi, checked_current: true})
      : await request('export', { composition, formats, out_dir, png_dpi });
    if (element('remember').checked) {
      try {
        localStorage.setItem(
          'circuitDrawer.export:' + snapshot().file,
          JSON.stringify({ formats, out_dir: result?.out_dir || out_dir, png_dpi }),
        );
      } catch {
        /* Storage is optional. */
      }
    }
    if (exportHandler) dialog.close();
    else message(tr('导出完成') + '\n' + result.files.join('\n'));
  }
  function openComposition(comp = null) {
    if (busy) return;
    page = 'composition';
    inspection = null;
    const current = snapshot().table,
      saved = current.figure?.compositions || [];
    composition = JSON.parse(
      JSON.stringify(
        comp ||
          saved.at(-1) || {
            id: 'composition-' + (saved.length + 1),
            preset: 'main_plus_two',
            gap_mm: 2,
            align: 'width',
            caption_position: 'below',
            font_pt: 8,
            subfigures: [{ kind: current.kind === 'control' ? 'control' : 'circuit', caption: '' }],
          },
      ),
    );
    shell(
      '组合出图',
      `<div class="figure-compose-grid"><aside>
      <label>${tr('已有组合')} <select data-field="saved"><option value="">${tr('新建组合')}</option>${saved.map((c) => `<option value="${esc(c.id)}" ${c.id === composition.id ? 'selected' : ''}>${esc(c.id)}</option>`).join('')}</select></label>
      <label>${tr('名称')} <input data-field="id" value="${esc(composition.id)}" maxlength="80"></label>
      <label>${tr('版式')} <select data-field="preset" aria-describedby="hint-figureComposition"><option value="vertical">${tr('上下')}</option><option value="horizontal">${tr('左右')}</option><option value="main_plus_two">${tr('主图与两小图')}</option><option value="grid_two">${tr('两列网格')}</option><option value="grid_three">${tr('三列网格')}</option></select></label>
      <label>${tr('对齐')} <select data-field="align"><option value="width">${tr('同排等宽')}</option><option value="height">${tr('同排等高')}</option></select></label>
      <label>${tr('间距')} mm <input data-field="gap" type="number" min="0" max="30" step="0.5"></label>
      <label>${tr('图注位置')} <select data-field="caption"><option value="below">${tr('下方')}</option><option value="above">${tr('上方')}</option></select></label>
      <label>${tr('图注字号')} pt <input data-field="font" type="number" min="5" max="24" step="0.5"></label>
      <div data-panels></div>${button('add', '添加子图')} ${editorHint("figureComposition")}</aside>
      <section class="figure-composition-paper"><img data-preview alt="${tr('组合图预览')}" hidden><p data-sources></p></section></div>
      <footer>${button('preview', '预览组合')}${button('save', '保存组合')}${button('compose-export', '导出检查')}${button('close', '返回图纸')}</footer>`,
    );
    element('preset').value = composition.preset;
    element('align').value = composition.align || 'width';
    element('gap').value = composition.gap_mm ?? 2;
    element('caption').value = composition.caption_position || 'below';
    element('font').value = composition.font_pt ?? 8;
    element('saved').onchange = () => {
      const existing = saved.find((c) => c.id === element('saved').value);
      openComposition(
        existing || {
          id: 'composition-' + (saved.length + 1),
          preset: 'vertical',
          subfigures: [{ kind: current.kind === 'control' ? 'control' : 'circuit' }],
        },
      );
    };
    renderPanels();
    message('选择已有图纸、模态或波形作为子图来源');
  }
  const kinds = {
    circuit: '电路图',
    control: '控制图',
    detail: '局部放大',
    waveform: '波形',
    modes: '模态总览',
  };
  function renderPanels() {
    dialog.querySelector('[data-panels]').innerHTML = composition.subfigures
      .map(
        (panel, index) =>
          `<fieldset data-index="${index}" draggable="true"><legend>(${String.fromCharCode(97 + index)}) ${tr('子图')}</legend>
      <label>${tr('类型')} <select data-panel-field="kind">${Object.entries(kinds)
        .map(
          ([k, v]) =>
            `<option value="${k}" ${k === panel.kind ? 'selected' : ''}>${tr(v)}</option>`,
        )
        .join('')}</select></label>
      <label>${tr('来源 JSON')} <input data-panel-field="source" value="${esc(panel.source)}" placeholder="${tr('留空使用当前图纸')}"></label>
      <label>${tr('图注')} <input data-panel-field="caption" value="${esc(panel.caption)}"></label>
      ${panel.kind === 'detail' ? `<label>${tr('范围 cm')} <input data-panel-field="crop_cm" value="${esc((panel.crop_cm || []).join(', '))}" placeholder="left, bottom, right, top"></label>` : ''}
      ${panel.kind === 'waveform' ? `<p>${esc(panel.waveform?.source || '')}</p><label>${tr('通道')} <input data-panel-field="channels" value="${esc((panel.channels || []).join(', '))}" placeholder="${tr('留空使用已选通道')}"></label><label>${tr('时间区间 s')} <input data-panel-field="window" value="${esc((panel.window || []).join(', '))}" placeholder="start, end"></label>${window.openWaveforms && !panel.source ? button('edit-waveform', '编辑波形与标注') : ''}` : ''}
      ${panel.kind === 'modes' ? `<label>${tr('模态名称')} <input data-panel-field="modes" value="${esc((panel.modes || []).join(', '))}" placeholder="${tr('留空使用全部模态')}"></label>` : ''}
      <div>${button('up', '上移')}${button('down', '下移')}${button('remove', '移除')}</div></fieldset>`,
      )
      .join('');
    for (const row of dialog.querySelectorAll('[data-index]')) {
      row.querySelector('[data-panel-field=kind]').onchange = () => {
        readComposition();
        renderPanels();
      };
      row.ondragstart = (event) => {
        if (event.target.closest('input,select')) {
          event.preventDefault();
          return;
        }
        event.dataTransfer.setData('text/plain', row.dataset.index);
      };
      row.ondragover = (event) => event.preventDefault();
      row.ondrop = (event) => {
        event.preventDefault();
        const from = Number(event.dataTransfer.getData('text/plain')),
          to = Number(row.dataset.index);
        if (!Number.isInteger(from) || from < 0 || from >= composition.subfigures.length) return;
        readComposition();
        composition.subfigures.splice(to, 0, composition.subfigures.splice(from, 1)[0]);
        renderPanels();
      };
    }
  }
  function readComposition() {
    Object.assign(composition, {
      id: element('id').value,
      preset: element('preset').value,
      align: element('align').value,
      gap_mm: Number(element('gap').value),
      caption_position: element('caption').value,
      font_pt: Number(element('font').value),
    });
    for (const row of dialog.querySelectorAll('[data-index]')) {
      const old = composition.subfigures[Number(row.dataset.index)],
        panel = {};
      for (const input of row.querySelectorAll('[data-panel-field]')) {
        const key = input.dataset.panelField,
          value = input.value.trim();
        if (!value) continue;
        panel[key] = ['channels', 'modes'].includes(key)
          ? value
              .split(/[,，]/)
              .map((v) => v.trim())
              .filter(Boolean)
          : ['window', 'crop_cm'].includes(key)
            ? value.split(/[,，\s]+/).map(Number)
            : value;
      }
      // Retain provenance only while referring to the same simulation source.
      if (old.source === panel.source && old.kind === panel.kind && old.circuit_signature)
        panel.circuit_signature = old.circuit_signature;
      if (old.source === panel.source && panel.kind === 'waveform' && old.waveform)
        panel.waveform = old.waveform;
      composition.subfigures[Number(row.dataset.index)] = panel;
    }
    return composition;
  }
  async function saveComposition() {
    const result = await request('save-composition', { composition: readComposition() });
    install(result.table, '保存组合');
    composition = JSON.parse(JSON.stringify(result.table.figure.compositions.find((c) => c.id === composition.id)));
    let tab = document.querySelector('[data-composition-tab]');
    if (!tab) {
      tab = document.createElement('button');
      tab.dataset.compositionTab = 'true';
      document.querySelector('.document-tabs').append(tab);
    }
    tab.textContent = tr('组合图');
    tab.onclick = () => openComposition();
    message('组合已保存');
  }
  async function previewComposition() {
    readComposition();
    const source = await request('sources', { composition });
    dialog.querySelector('[data-sources]').textContent = source.sources
      .map((s) => `(${String.fromCharCode(97 + s.index)}) ${s.source} · ${tr(s.note)}`)
      .join('\n');
    const result = await request('preview', { composition });
    const image = dialog.querySelector('[data-preview]');
    image.src = 'data:image/png;base64,' + result.png;
    image.hidden = false;
    message('预览已更新');
  }
  function handle(action, row) {
    if (busy) return;
    if (action === 'close') {
      dialog.close();
      return;
    }
    if (page === 'composition') {
      readComposition();
      const index = Number(row?.dataset.index);
      if (action === 'edit-waveform') {
        const panel = composition.subfigures[index];
        run(async () => {
          const result = await request('waveform-view', {panel});
          dialog.close();
          window.openWaveforms({publication: true, config: result.config,
            onCompose: (config) => {
              panel.waveform = config;
              delete panel.window;
              delete panel.channels;
              openComposition(composition);
            }});
        });
        return;
      }
      if (['up', 'down', 'remove', 'add'].includes(action)) {
        if (action === 'add') composition.subfigures.push({ kind: 'detail' });
        else if (action === 'remove' && composition.subfigures.length > 1)
          composition.subfigures.splice(index, 1);
        else if (action === 'up' && index > 0)
          composition.subfigures.splice(index - 1, 0, composition.subfigures.splice(index, 1)[0]);
        else if (action === 'down' && index < composition.subfigures.length - 1)
          composition.subfigures.splice(index + 1, 0, composition.subfigures.splice(index, 1)[0]);
        renderPanels();
        return;
      }
      if (action === 'preview') run(previewComposition);
      if (action === 'save') run(saveComposition);
      if (action === 'compose-export')
        run(saveComposition).then((saved) => {
          if (saved) openChecks(composition);
        });
      return;
    }
    if (action === 'inspect') run(inspect);
    if (action === 'export' || action === 'fix-export')
      run(() => exportFiles(action === 'fix-export'));
  }
  window.addEventListener('circuit-drawer:layout-changed', updateExportButtons);
  function calibrate(onChange) {
    let density = 96 / 2.54;
    try {
      density = Number(localStorage.getItem('circuitDrawer.beautify.pixelsPerCm')) || density;
    } catch {
      /* Storage is optional. */
    }
    const calibration = document.createElement('dialog');
    calibration.className = 'figure-calibration';
    calibration.innerHTML = `<form><h3>${tr('屏幕尺寸校准')}</h3><p>${tr('用直尺测量下方横线，填写实际厘米数。')}</p>
      <div class="figure-calibration-line" style="width:${density * 10}px"></div>
      <label>${tr('实测长度')} <input type="number" min="1" max="50" step="0.1" value="10" required> cm</label>
      <button type="button">${tr('取消')}</button><button type="submit">${tr('应用校准')}</button></form>`;
    document.body.append(calibration);
    calibration.querySelector('button[type=button]').onclick = () => calibration.close();
    calibration.onclose = () => calibration.remove();
    calibration.querySelector('form').onsubmit = (event) => {
      event.preventDefault();
      density *= 10 / Number(calibration.querySelector('input').value);
      try {
        localStorage.setItem('circuitDrawer.beautify.pixelsPerCm', JSON.stringify(density));
      } catch {
        /* Calibration still applies for this session. */
      }
      calibration.close();
      onChange(density);
    };
    calibration.showModal();
  }
  function addWaveform(config) {
    if (!config || busy) return;
    const saved = snapshot().table.figure?.compositions || [];
    const existing = saved.findLast(item => item.subfigures.every(panel => panel.kind === 'waveform'));
    const next = JSON.parse(JSON.stringify(existing || {
      id: 'waveforms-' + (saved.length + 1), preset: 'grid_three', subfigures: []
    }));
    next.subfigures.push({kind: 'waveform', waveform: JSON.parse(JSON.stringify(config))});
    openComposition(next);
  }
  window.CircuitFigureOutput = { openChecks, openComposition, addWaveform, calibrate, busy: () => busy };
})();
