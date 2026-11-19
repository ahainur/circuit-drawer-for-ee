/* Paper workspace shares the source table, preview renderer and undo stack. */
(() => {
  const get = id => document.getElementById(id);
  const tr = text => window.cdI18n?.translate(text) || text;
  const copy = value => JSON.parse(JSON.stringify(value));
  const storage = (key, value) => {
    try {
      if (value !== undefined) localStorage.setItem(key, JSON.stringify(value));
      else return JSON.parse(localStorage.getItem(key));
    } catch { return null; }
  };
  const icon = path => `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${path}"/></svg>`;
  const icons = {
    back:'M15 5l-7 7 7 7M8 12h14', save:'M4 3h14l3 3v15H3V3zM7 3v7h10V3M7 21v-7h10v7',
    undo:'M9 4L3 9l6 5M3 9h11a6 6 0 010 12', redo:'M15 4l6 5-6 5M21 9H10a6 6 0 000 12',
    fold:'M3 4h18v16H3zM8 12h8', expand:'M3 4h18v16H3zM8 12h8M12 8v8',
    partial:'M3 4h8v16H3zM15 4h6M15 12h6M15 20h6', color:'M3 4h18v16H3zM3 10h18',
    mono:'M12 3a9 9 0 100 18 9 9 0 000-18M12 3v18M12 4l4 4M12 8l8 8M12 12l6 6M12 16l3 3',
    arrow:'M3 18L18 3M11 3h7v7', label:'M3 5h18M12 5v16M8 21h8',
    preview:'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12M15 12a3 3 0 11-6 0 3 3 0 016 0',
    check:'M4 3h16v18H4zM7 8l2 2 5-5M7 16l2 2 7-7', beauty:'M3 5h14v11H3zM8 10h13v11H8zM19 1v6M16 4h6'
  };
  const button = (id, text, symbol) => `<button type="button" id="${id}" title="${text}">${icon(icons[symbol])}<span>${text}</span></button>`;
  const originalMain = document.querySelector('main');
  const ribbon = document.createElement('div'); ribbon.id = 'figureRibbon'; ribbon.hidden = true;
  ribbon.setAttribute('role', 'toolbar'); ribbon.setAttribute('aria-label', '美化出图工具栏');
  ribbon.innerHTML = `<div class="figure-group">${button('figureBack','返回绘图','back')}<small>工作区</small></div>
    <div class="figure-group">${button('figureSave','保存','save')}${button('figureUndo','撤销','undo')}${button('figureRedo','重做','redo')}<small>文件</small></div>
    <div class="figure-group"><div class="figure-settings"><label>栏宽 <select id="figureProfile"><option value="ieee-single">IEEE 单栏</option><option value="ieee-double">IEEE 双栏</option></select></label>
      <label>符号 <select id="figureSymbol"><option value="power">功率电子</option><option value="ic">模拟 IC</option><option value="mixed" disabled>混合样式</option></select></label>
      <label>字号 <input id="figureFont" type="number" min="5" max="24" step="0.5" value="8"> pt</label>
      <label>线宽 <input id="figureLine" type="number" min="0.1" max="3" step="0.05" value="0.55"> pt</label></div>${button('figureTrim','去留白','partial')}<small>版式</small></div>
    <div class="figure-group">${button('figureFold','折叠','fold')}${button('figureExpand','展开','expand')}${button('figurePartial','部分折叠','partial')}${button('figureRepeat','重复省略 ✦','partial')}<small>模块显示</small></div>
    <div class="figure-group">${button('figureColor','加色块','color')}${button('figurePartition','按模块分区 ✦','color')}${button('figureMono','黑白预览','mono')}<small>色块与配色</small></div>
    <div class="figure-group">${button('figureLabel','标签位置','label')}${button('figureArrange','整理标签 ✦','label')}${button('figureNumber','统一编号 ✦','label')}${button('figureCurrent','电流方向','arrow')}${button('figureCallout','说明箭头','arrow')}<small>标注整理</small></div>
    <div class="figure-group" id="figurePreviewGroup">${button('figureSymbols','系统图标库','partial')}${button('figureWaveforms','波形出图','preview')}${button('figureCompose','组合出图','color')}${button('figureFit','适应窗口','preview')}${button('figureExport','导出检查','check')}<small>预览与导出</small></div>
    <details id="figureMore"><summary>更多 ▾</summary><div></div></details>`;
  document.querySelector('.document-tabs').before(ribbon);
  const compactRibbon = matchMedia('(max-width:1023px)');
  function arrangeRibbon() {
    const group = get('figurePreviewGroup');
    if (compactRibbon.matches) get('figureMore').querySelector('div').append(group);
    else get('figureMore').before(group);
    get('figureMore').hidden = !compactRibbon.matches;
    get('figureMore').open = false;
  }
  compactRibbon.addEventListener('change', arrangeRibbon);
  arrangeRibbon();
  get('figureMore').ontoggle = () => {
    const box = get('figureMore').getBoundingClientRect();
    const menu = get('figureMore').querySelector('div');
    menu.style.top = box.bottom + 4 + 'px';
    menu.style.left = Math.max(5, box.right - 170) + 'px';
  };
  const badge = document.createElement('span'); badge.id = 'figureMode'; badge.textContent = '美化出图'; badge.hidden = true;
  document.querySelector('.document-tabs').append(badge);
  const workspace = document.createElement('main'); workspace.id = 'figureWorkspace'; workspace.hidden = true;
  workspace.innerHTML = `<aside id="figureLeft" aria-label="纸面与投稿检查">
    <section class="figure-section"><h3>纸面</h3><dl class="figure-stats" id="figureStats"></dl></section>
    <section class="figure-section"><h3>投稿检查</h3><div id="figureChecks"></div>
      <details><summary>调整投稿建议</summary><div id="figureAdvice"></div></details></section>
    <section class="figure-section"><h3>操作记录（每条一步撤销）</h3><ol id="figureHistory"></ol></section></aside>
    <section id="figureViewport" aria-label="纸面预览"><div id="figureCanvas"><div id="figurePaper" hidden>
      <svg id="figureRuler" aria-label="厘米标尺"></svg><img id="figureImage" alt="当前图纸的真实出图预览" draggable="false">
      <svg id="figureOverlay" tabindex="0" aria-label="选择图中对象"></svg><div id="figureGuideSingle" class="figure-guide"><span>单栏图宽</span></div>
      <div id="figureGuideDouble" class="figure-guide figure-guide-end"><span>双栏图宽</span></div>
      <div id="figureCaption"></div></div><div id="figureFloat" hidden>
        <span id="figureSelectionCount"></span><button data-figure-command="figureFold">折叠</button><button data-figure-command="figureExpand">展开</button>
        <button data-figure-command="figurePartial">部分折叠</button>
        <button data-figure-command="figureColor">加色块</button><button data-figure-command="figureLabel">标签位置</button>
        <button data-figure-command="figureCurrent">电流方向</button><button data-figure-command="figureNumber">统一编号 ✦</button><button data-figure-command="figureAssign">交给出图 Agent…</button></div></div>
      <div id="figureZoom"><button id="figureFitSmall">适应窗口</button><button id="figureActual">纸面 100%</button><button id="figureDouble">200%</button><button id="figureCalibrate">校准尺寸</button><button id="figureSidebar">属性／提示词</button></div>
      <div id="figureStatus"><span id="figureStatusText" role="status"></span><div id="figureActionError" class="bad" role="alert" hidden></div></div></section>
    <aside id="figureRight" aria-label="出图信息"><div class="figure-tabs" role="tablist">
      <button role="tab" data-figure-tab="properties" aria-selected="false" aria-controls="figureProperties">属性</button>
      <button role="tab" data-figure-tab="agent" aria-selected="true" aria-controls="figureAgent">出图 Agent</button>
      <button role="tab" data-figure-tab="issues" aria-selected="false" aria-controls="figureIssuesPanel">问题</button></div>
      <section id="figureProperties" class="figure-panel" role="tabpanel" hidden></section>
      <section id="figureAgent" class="figure-panel" role="tabpanel">
        <div id="figureAgentContent"></div>
        <textarea id="figureInput" placeholder="描述想要的论文图效果…" aria-label="出图要求"></textarea>
        <div id="figureAgentActions"></div><button id="figureAssign" hidden>交给出图 Agent…</button></section>
      <section id="figureIssuesPanel" class="figure-panel" role="tabpanel" hidden><ul id="figureIssues"></ul><button id="figureFixCircuit">返回绘图处理问题</button></section></aside>`;
  originalMain.after(workspace);
  const palette = document.createElement('dialog'); palette.id = 'figurePalette';
  palette.innerHTML = `<form><h3>色块与区域</h3><div id="figureSwatches"></div>
    <label>边框 <select id="figureBorder"><option value="none">无边框</option><option value="solid">实线</option><option value="dashed">虚线</option></select></label>
    <label>圆角半径 <input id="figureRadius" type="number" min="0" max="1" step="0.05" value="0.2"> cm</label>
    <label><input id="figureShadow" type="checkbox">轻投影</label>
    <label><input id="figureInset" type="checkbox">灰色内阴影</label>
    <label>边框线宽 <input id="figureWidth" type="number" min=".1" max="3" step=".05" placeholder="跟随纸面"> pt</label>
    <label><input id="figureBold" type="checkbox">标签粗体</label>
    <label>区域名称 <select id="figureRegionNameMode"><option value="module">模块名</option><option value="none">不加名称</option><option value="custom">自定义名称</option></select></label>
    <input id="figureRegionName" maxlength="500" aria-label="自定义区域名称" hidden>
    <label>名称位置 <select id="figureRegionPosition"><option value="below">下方</option><option value="above">上方</option><option value="inside">框内顶部</option></select></label>
    <label id="figureSeparateLabel"><input id="figureSeparate" type="checkbox" checked>每个模块一个色块</label>
    <p id="figurePaletteError" class="bad" role="status"></p><div class="figure-dialog-actions"><button type="button" id="figurePaletteCancel">取消</button><button id="figurePaletteApply" type="submit">应用（一步撤销）</button></div></form>`;
  document.body.append(palette);
  get('figureToggle').innerHTML = icon(icons.beauty) + '<span>美化出图</span>';
  get('figureToggle').querySelector('svg').classList.add('tool-icon');
  get('agentToggle')?.after(get('figureToggle'));

  let open = false, data = null, timer, requestVersion = 0, activePreviewKey = '', pendingPreview = null;
  let renderedKey = '', paperBounds = null, selection = [], zoom = 'fit', mono = false, working = false;
  let originalView = null, records = [], entryKey = '', color = 'green';
  let arrowMode = false, symbolMode = null, paperDrag = null, suppressPaperClick = false;
  let pixelsPerCm = storage('circuitDrawer.beautify.pixelsPerCm') || 96/2.54;
  const menuDisabled = new Map();
  const advice = storage('circuitDrawer.beautify.advice') || {};
  const COLORS = {green:'浅绿',blue:'浅蓝',orange:'浅橙',red:'浅红',purple:'浅紫',yellow:'浅黄',teal:'青绿',gray:'浅灰'};
  const fmt = number => number === null || number === undefined ? '—' : Number(number.toFixed(2));
  const message = (text, bad = false) => {
    get('figureStatusText').textContent = tr(text);
    get('figureStatusText').classList.toggle('bad', bad);
  };
  const snapshot = () => JSON.stringify(layout);
  const paperKey = before => [before,profile,mono,editorFilePath].join('\n');
  function tab(name) {
    for (const button of workspace.querySelectorAll('[data-figure-tab]')) {
      const active = button.dataset.figureTab === name;
      button.setAttribute('aria-selected', String(active)); button.tabIndex = active ? 0 : -1;
      get(button.getAttribute('aria-controls')).hidden = !active;
    }
  }
  workspace.querySelectorAll('[data-figure-tab]').forEach(button => button.onclick = () => tab(button.dataset.figureTab));
  workspace.querySelector('.figure-tabs').onkeydown = event => {
    const buttons = [...workspace.querySelectorAll('[data-figure-tab]')], index = buttons.indexOf(event.target);
    if (index < 0 || !['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
    event.preventDefault(); const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length-1 : (index + (event.key === 'ArrowLeft' ? -1 : 1) + buttons.length) % buttons.length;
    buttons[next].click(); buttons[next].focus();
  };
  function enter() {
    if (!layout || open) return;
    if (window.CircuitAgentEditor.busy() || definitionEdit || drag || wireDraft || boxDraft || pendingModule) {
      showEditorNotice('请先完成当前编辑', true);
      return;
    }
    originalView = copy(view); entryKey = historyKey(); records = []; selection = [];
    open = true; workspace.hidden = ribbon.hidden = badge.hidden = false;
    originalMain.inert = true; document.body.classList.add('figure-open');
    document.querySelectorAll('[data-menu]').forEach(button => {
      if (['file','help'].includes(button.dataset.menu)) return;
      menuDisabled.set(button, button.disabled); button.disabled = true;
    });
    storage('circuitDrawer.beautify.open', true);
    window.CircuitPromptLibrary.mount({textarea:get('figureInput'), domain:'figure', mode:'edit', table:()=>layout, api, busy:()=>working});
    refresh(); get('figureBack').focus();
  }
  function leave() {
    if (working) return;
    cancelPaperDrag(); symbolMode = null; arrowMode = false;
    get('figureOverlay').classList.remove('drawing-arrow');
    get('figureCallout').setAttribute('aria-pressed', 'false');
    open = false; ++requestVersion; clearTimeout(timer); pendingPreview = null;
    workspace.hidden = ribbon.hidden = badge.hidden = true; originalMain.inert = false;
    menuDisabled.forEach((disabled, button) => { button.disabled = disabled; }); menuDisabled.clear();
    document.body.classList.remove('figure-open'); storage('circuitDrawer.beautify.open', false);
    if (originalView) view = originalView;
    render(); get('figureToggle').focus();
  }
  get('figureToggle').onclick = enter;
  get('figureBack').onclick = get('figureFixCircuit').onclick = leave;
  get('figureSave').onclick = () => get('save').click();
  get('figureUndo').onclick = () => { if (!working) { undo(); selection = []; refresh(); } };
  get('figureRedo').onclick = () => { if (!working) { redo(); selection = []; refresh(); } };
  get('figureProfile').onchange = () => {
    const before = snapshot(), oldProfile = profile;
    pushHistory(); profile = get('figureProfile').value; get('profile').value = profile;
    record(before, '更改栏宽', oldProfile); changed(false); refresh();
  };
  function record(before, label, oldProfile = profile) {
    if (before === snapshot() && oldProfile === profile) return;
    const reachable = new Set(history.map(entry => historyKey(entry)));
    records = records.filter(record => reachable.has(record.key));
    records.push({key:historyKey(), label});
  }
  async function transaction(path, payload, label) {
    if (working) return false;
    const before = snapshot(), requestedProfile = profile, requestedFile = editorFilePath;
    get('figureActionError').hidden = true;
    working = true; controls(); message('校验中…');
    try {
      const result = await api(path, {layout:JSON.parse(before), profile, ...payload});
      if (!open || snapshot() !== before || profile !== requestedProfile || editorFilePath !== requestedFile) {
        throw new Error('等待操作时图纸已改变，请重试。');
      }
      const next = result.layout || result.table;
      if (!next) throw new Error('外观操作未完成');
      if (!install(next, label)) message('纸面预览已更新');
      return true;
    } catch (error) {
      get('figureActionError').textContent = tr(error.message);
      get('figureActionError').hidden = false;
      message(error.message, true);
      return false;
    }
    finally { working = false; controls(); }
  }
  function install(next, label) {
    const before = snapshot();
    if (JSON.stringify(next) === before) return false;
    pushHistory(); layout = next; ++layoutGeneration; changed(false); record(before, label); refresh();
    return true;
  }
  const chosen = () => ({kind:selection[0]?.type, ids:selection.map(s => s.id)});
  for (const [id, display, label] of [['figureFold','collapsed','折叠模块'],['figureExpand','expanded','展开模块'],['figurePartial','partial','部分折叠模块']]) {
    get(id).onclick = () => transaction('/api/figure-edit', {action:'display', display, ...chosen()}, label);
  }
  get('figureSymbol').onchange = () => transaction('/api/symbol-style', {style:get('figureSymbol').value}, '符号风格');
  get('figureMono').onclick = () => { mono = !mono; get('figureMono').setAttribute('aria-pressed', String(mono)); refresh(); };
  get('figureFit').onclick = get('figureFitSmall').onclick = () => { zoom = 'fit'; sizePaper(); };
  get('figureActual').onclick = () => { zoom = 1; sizePaper(); message('纸面按已保存的尺寸校准显示'); };
  get('figureDouble').onclick = () => { zoom = 2; sizePaper(); };
  get('figureSidebar').onclick = () => { workspace.classList.toggle('aside-closed'); sizePaper(); };
  get('figureExport').onclick = () => window.CircuitFigureOutput?.openChecks();
  get('figureCompose').onclick = () => window.CircuitFigureOutput?.openComposition();
  get('figureWaveforms').onclick = () => window.openWaveforms({publication: true});
  get('figureSymbols').onclick = () => window.CircuitSystemSymbols.open();
  for (const [id,key] of [['figureFont','font_pt'],['figureLine','line_pt']]) {
    get(id).onchange = async () => {
      await transaction('/api/figure-edit', {action:'paper',settings:{[key]:Number(get(id).value)}}, '调整纸面样式');
      checks();
    };
  }
  get('figureTrim').onclick = () => transaction('/api/figure-edit', {action:'paper',settings:{margin_mm:0}}, '去除页面留白');
  get('figureCalibrate').onclick = () => window.CircuitFigureOutput.calibrate(value => {
    pixelsPerCm = value;
    sizePaper();
  });
  get('figureLabel').onclick = () => { tab('properties'); get('figureObjectLabel')?.focus(); };
  get('figureCurrent').onclick = () => { tab('properties'); get('figureObjectCurrent')?.focus(); };
  get('figureCallout').onclick = () => {
    symbolMode = null;
    arrowMode = !arrowMode;
    get('figureCallout').setAttribute('aria-pressed', String(arrowMode));
    get('figureOverlay').classList.toggle('drawing-arrow', arrowMode);
    message(arrowMode ? '从起点拖到箭头尖，Esc 取消。' : '纸面预览已更新');
  };
  for (const button of get('figureFloat').querySelectorAll('button')) button.onclick = () => get(button.dataset.figureCommand).click();
  function controls() {
    const type = selection[0]?.type, ids = selection.map(s => s.id);
    get('figureAssign').disabled = working || !ids.length;
    get('figureFold').disabled = get('figureExpand').disabled = working || type !== 'module';
    get('figurePartial').disabled = working || type !== 'module' || !ids.every(id => data?.report.module_info?.partial?.includes(id));
    get('figureColor').disabled = working || !['region','element','module'].includes(type);
    get('figureLabel').disabled = working || type !== 'element' || ids.length !== 1;
    const element = type === 'element' && layout.elements.find(e => e.id === ids[0]);
    get('figureCurrent').disabled = working || ids.length !== 1 || !element || !('a' in element.pins && 'b' in element.pins);
    get('figureUndo').disabled = working || get('undo').disabled;
    get('figureRedo').disabled = working || get('redo').disabled;
    get('figureProfile').disabled = get('figureSymbol').disabled = working;
    get('figureFont').disabled = get('figureLine').disabled = working;
    get('figureBack').disabled = working;
    get('figureCallout').disabled = working || !paperBounds || !data?.report.ok;
    get('figureExport').disabled = working || !data?.report.ok;
    const definitions = (layout.modules?.instances || []).map(instance => instance.definition);
    get('figureRepeat').disabled = definitions.length === new Set(definitions).size;
    const hints = {
      figureFold:'先选中模块实例', figureExpand:'先选中模块实例',
      figurePartial:'先选中有部分展开视图的模块', figureColor:'先选中元件、模块或色块',
      figureLabel:'先选中一个元件', figureCurrent:'先选中一个两端器件',
      figureCallout:'等待有效纸面预览', figureExport:'先处理图纸校验问题'
    };
    for (const [id, hint] of Object.entries(hints)) {
      const button = get(id);
      button.title = tr(button.disabled ? (working ? '正在完成外观操作' : hint) : button.querySelector('span').textContent);
    }
    const counts = {module:'模块', element:'元件', region:'色块', block:'系统符号', glyph:'系统符号', annotation:'文字', callout:'说明箭头'};
    get('figureSelectionCount').textContent = selection.length ? `${tr(counts[type])} × ${selection.length}` : '';
    get('figureFloat').querySelectorAll('button').forEach(b => b.hidden = get(b.dataset.figureCommand).disabled);
  }
  function checks() {
    const f = data.figure, size = f.size_cm, limits = f.advice;
    get('figureFont').value = layout.figure?.font_pt ?? f.profiles[f.profile].font_pt;
    get('figureLine').value = f.line_pt;
    get('figureStats').replaceChildren();
    for (const [name, value] of [['图宽',size ? fmt(size[0]) + ' cm' : '—'],['图高',size ? fmt(size[1]) + ' cm' : '—'],['最小正文字号',fmt(f.font_pt) + ' pt'],['线宽',fmt(f.line_pt) + ' pt']]) {
      const dt = document.createElement('dt'), dd = document.createElement('dd'); dt.textContent = tr(name); dd.textContent = value; get('figureStats').append(dt,dd);
    }
    const lines = [
      [data.report.ok ? 'pass' : 'error', data.report.ok ? '图纸校验通过' : '校验未通过，请返回绘图处理'],
      [!size ? 'unknown' : size[0] <= f.profiles[get('figureProfile').value].width_cm + .01 ? 'pass' : 'error', '图宽不超过目标图宽'],
      [!size ? 'unknown' : size[1] <= limits.height_cm ? 'pass' : 'warn', tr('建议图高') + ' ≤ ' + limits.height_cm + ' cm'],
      [f.font_pt === null ? 'unknown' : f.font_pt >= limits.font_pt ? 'pass' : 'warn', tr('建议正文字号') + ' ≥ ' + limits.font_pt + ' pt'],
      [f.line_pt >= limits.line_pt ? 'pass' : 'warn', tr('建议线宽') + ' ≥ ' + limits.line_pt + ' pt'],
      [!f.gray_checked ? 'unknown' : f.same_colors.length ? 'warn' : 'pass', '相邻色块颜色可区分'],
      [!f.gray_checked ? 'unknown' : f.gray_pairs.length ? 'warn' : 'pass', !f.gray_checked ? '暂无相邻色块可比对' : f.gray_pairs.length ? tr('相邻色块灰度接近') + ' · ' + f.gray_pairs.length : '相邻色块灰度差符合建议'],
      [!data.report.ok ? 'unknown' : data.report.issues.some(i=> /label|text|overlap/.test(i.code) && i.level !== 'note') ? 'warn' : 'pass', '标签与重叠检查'],
      [f.simulation_matches === null ? 'unknown' : f.simulation_matches ? 'pass' : 'warn', f.simulation_matches === null ? '尚无已关联仿真可比对' : f.simulation_matches ? '电路与已关联仿真一致' : '电路与已关联仿真不同']
    ];
    get('figureChecks').replaceChildren();
    for (const [state, text] of lines) {
      const row = document.createElement('div'); row.className = 'figure-check'; row.dataset.state = state;
      const mark = document.createElement('span'), label = document.createElement('span');
      mark.textContent = {pass:'✓',warn:'!',error:'×',unknown:'·'}[state]; label.textContent = tr(text); row.append(mark,label); get('figureChecks').append(row);
    }
    get('figureIssues').replaceChildren();
    for (const issue of data.report.issues) {
      const li = document.createElement('li'); li.textContent = issue.message; li.className = issue.level === 'error' ? 'bad' : ''; get('figureIssues').append(li);
    }
    if (!get('figureIssues').children.length) get('figureIssues').textContent = tr('校验通过');
    if (!get('figureAdvice').children.length) for (const [key, title, step] of [['height_cm','图高上限（cm）',1],['font_pt','正文字号下限（pt）',.5],['line_pt','线宽下限（pt）',.05],['gray_difference','灰度差下限（0–1）',.01]]) {
      const row = document.createElement('label'), input = document.createElement('input'); row.textContent = tr(title);
      input.type = 'number'; input.min = step; input.max = key === 'gray_difference' ? 1 : 100; input.step = step; input.value = limits[key];
      input.onchange = () => { if (!input.checkValidity() || !input.value) { input.reportValidity(); return; } advice[key] = Number(input.value); storage('circuitDrawer.beautify.advice', advice); refresh(); };
      row.append(input); get('figureAdvice').append(row);
    }
  }
  function historyView() {
    get('figureHistory').replaceChildren();
    function rewind(target) {
      while (historyKey() !== target && history.some(entry => historyKey(entry) === target)) {
        const old = historyKey();
        undo();
        if (historyKey() === old) break;
      }
      refresh();
    }
    const entry = document.createElement('li'), entryButton = document.createElement('button');
    entryButton.textContent = tr('进入美化出图');
    const current = historyKey();
    const reachable = new Set(history.map(entry => historyKey(entry)));
    entryButton.disabled = working || current === entryKey || !reachable.has(entryKey);
    entryButton.onclick = () => rewind(entryKey);
    entry.append(entryButton);
    get('figureHistory').append(entry);
    records.forEach(record => {
      const li = document.createElement('li'), button = document.createElement('button'); button.textContent = tr(record.label);
      const isCurrent = current === record.key;
      button.disabled = working || isCurrent || !reachable.has(record.key);
      li.classList.toggle('figure-history-current', isCurrent);
      li.classList.toggle('figure-history-undone', !isCurrent && !reachable.has(record.key));
      button.onclick = () => rewind(record.key);
      li.append(button); get('figureHistory').append(li);
    });
  }
  function properties() {
    const box = get('figureProperties'); box.replaceChildren();
    if (selection.length !== 1) { box.textContent = tr(selection.length ? '已选多个对象，可使用上方快捷操作。' : '可直接拖动纸面上的系统符号和文字。点选说明箭头可调整两端。'); return; }
    const {type,id} = selection[0], heading = document.createElement('h3');
    const name = {callout:'说明箭头',annotation:'文字',glyph:'系统符号'}[type];
    heading.textContent = name ? tr(name)+' '+(Number(id)+1) : id; box.append(heading);
    const source = type === 'element' ? layout.elements.find(e => e.id === id) : type === 'module' ? layout.modules.instances.find(i=>i.id === id) : type === 'block' ? layout.blocks.find(b=>b.id===id) : layout[type+'s']?.[Number(id)];
    if (!source) return;
    if (type === 'callout') {
      const hint = document.createElement('p');
      hint.textContent = tr('拖动圆点改变箭头方向。拖动线身可移动自由端，已连接的端点会留在原处。');
      box.append(hint);
    }
    if (type === 'block') {
      const index = layout.blocks.indexOf(source);
      const style = (field,value) => transaction('/api/figure-output', {action:'style', edits:[{path:['blocks',index,field],value}]}, '调整方框样式');
      const border = document.createElement('select'), caption = document.createElement('label');
      caption.textContent = tr('边框'); border.dataset.blockField = 'border';
      const borders = {solid:'实线',dashed:'虚线',dotted:'点线',none:'无边框'};
      for (const name of data.report.block_options.borders) border.add(new Option(tr(borders[name]), name));
      border.value = source.border || 'solid'; border.onchange = () => style('border',border.value);
      caption.append(border); box.append(caption);
      const fields = [['corner_radius','圆角 cm','number'],['shadow','轻投影','checkbox'],['inner_shadow','灰色内阴影','checkbox'],['border_width','边框线宽','number'],['fill','填色','text']];
      if (data.report.block_options.color_icons.includes(source.icon)) fields.push(['icon_color','彩色图标','checkbox']);
      for (const [field,title,inputType] of fields) {
        const label = document.createElement('label'), input = document.createElement('input');
        label.textContent = tr(title); input.type = inputType; input.dataset.blockField = field;
        if (inputType === 'checkbox') input.checked = !!source[field];
        else input.value = source[field] ?? (field === 'fill' ? 'white' : field === 'border_width' ? '' : 0);
        if (inputType === 'number') { input.min=field === 'border_width' ? .1 : 0; input.max=field === 'border_width' ? 3 : 1; input.step=.05;
          if (field === 'border_width') {label.textContent += ' (pt)'; input.placeholder = tr('跟随纸面');} }
        input.onchange = () => style(field,inputType==='checkbox'?input.checked:inputType==='number'?(input.value === '' && field === 'border_width' ? null : Number(input.value)):input.value);
        label.append(input); box.append(label);
      }
    }
    if (type === 'element' || type === 'annotation' || (type === 'block' && source.text) || (type === 'callout' && source.label)) {
      const bold = document.createElement('input'), label = document.createElement('label');
      bold.type = 'checkbox'; bold.checked = (type === 'element' ? source.opts : source)?.font_weight === 'bold';
      const path = type === 'element' ? ['elements',layout.elements.indexOf(source),'opts','font_weight'] :
        [type+'s',layout[type+'s'].indexOf(source),'font_weight'];
      bold.onchange = () => transaction('/api/figure-output', {action:'style',edits:[{path,value:bold.checked?'bold':'normal'}]}, '标签粗体');
      label.textContent = tr('标签粗体'); label.append(bold); box.append(label);
    }
    if (type === 'element') {
      const text = document.createElement('input'); text.id = 'figureObjectLabel'; text.value = source.label || ''; text.maxLength = 500;
      const position = document.createElement('select'); position.id = 'figureLabelPosition';
      for (const [key,name] of [['auto','自动'],['left','左侧'],['right','右侧'],['above','上方'],['below','下方']]) position.add(new Option(tr(name),key));
      position.value = source.label_pos || 'auto';
      for (const [title,input] of [['标签文字',text],['标签位置',position]]) { const label = document.createElement('label'); label.textContent = tr(title); label.append(input); box.append(label); }
      text.onchange = position.onchange = () => transaction('/api/figure-edit',{action:'label',...chosen(),text:text.value,position:position.value},'修改标签');
      if ('a' in source.pins && 'b' in source.pins) {
        const label = document.createElement('label'), current = document.createElement('input'); label.textContent = tr('电流参考标签'); current.id = 'figureObjectCurrent'; current.value = source.opts?.current || '';
        const sideLabel = document.createElement('label'), side = document.createElement('select');
        sideLabel.textContent = tr('电流标签位置'); side.id = 'figureCurrentSide';
        side.add(new Option(tr('沿 a→b 左侧'), '^'));
        side.add(new Option(tr('沿 a→b 右侧'), '_'));
        side.value = source.opts?.current_side || '^';
        current.onchange = side.onchange = () => transaction('/api/figure-edit',
          {action:'current', ...chosen(), text:current.value, side:side.value}, '电流参考标注');
        label.append(current); sideLabel.append(side); box.append(label, sideLabel);
      }
    }
    if (type === 'region') {
      const edit = document.createElement('button');
      edit.textContent = tr('编辑色块');
      edit.onclick = openPalette;
      box.append(edit);
      if (source.fit === 'members') {
        const hint = document.createElement('p');
        hint.className = 'hint';
        hint.textContent = tr('此色块跟随成员位置，可修改配色与名称。');
        box.append(hint);
      }
    }
    if (type !== 'module' && type !== 'element' && !source.members) return;
    const details = document.createElement('details'), summary = document.createElement('summary'), pre = document.createElement('pre');
    summary.textContent = tr('电气数据（只读）'); pre.textContent = JSON.stringify(type === 'element' ? {id:source.id, kind:source.kind, pins:source.pins, simulation:layout.simulation?.components?.[id]} : type === 'module' ? {definition:source.definition,overrides:source.overrides,connections:source.connections} : {members:source.members},null,2);
    details.append(summary,pre); box.append(details);
  }
  async function refresh() {
    clearTimeout(timer); if (!open) return;
    const version = ++requestVersion, before = snapshot(), requestedProfile = profile, requestedFile = editorFilePath;
    controls(); historyView();
    try {
      const result = await api('/api/figure-check', {layout:JSON.parse(before),profile:requestedProfile,advice});
      if (!open || version !== requestVersion || before !== snapshot() || requestedProfile !== profile || requestedFile !== editorFilePath) return;
      data = result;
      get('figureProfile').value = data.figure.profile;
      get('figureSymbol').value = data.figure.symbol_style;
      selection = selection.map(s => data.figure.shapes.find(x=>x.type===s.type && x.id===s.id)).filter(Boolean);
      checks(); properties(); controls();
      if (!data.report.ok) { get('figurePaper').hidden = true; message('纸面预览待校验通过，请查看问题栏。',true); return; }
      if (!data.figure.size_cm) {
        get('figurePaper').hidden = true;
        get('figureExport').disabled = true;
        message('图纸为空，请返回绘图添加内容。');
        return;
      }
      const key = paperKey(before);
      pendingPreview = null;
      if (key === activePreviewKey) return;
      if (key === renderedKey && paperBounds) {
        get('figurePaper').hidden = false;
        sizePaper();
        message('纸面预览已更新');
        return;
      }
      pendingPreview = {key, before, profile:requestedProfile, file:requestedFile, mono}; preview();
    } catch (error) { if (version === requestVersion) message(error.message,true); }
  }
  async function preview() {
    if (activePreviewKey || !pendingPreview || !open) return;
    const request = pendingPreview; activePreviewKey = request.key; pendingPreview = null;
    get('figurePaper').inert = true;
    get('figurePaper').dataset.loading = 'true'; get('figureOverlay').replaceChildren(); message('正在生成真实出图预览…');
    try {
      const result = await api('/api/figure-preview',{layout:JSON.parse(request.before),profile:request.profile,mono:request.mono});
      if (!open || request.before !== snapshot() || request.profile !== profile || request.mono !== mono || request.file !== editorFilePath) return;
      renderedKey = request.key; paperBounds = result.bounds_cm;
      get('figureImage').src = 'data:image/png;base64,' + result.png; get('figurePaper').hidden = false;
      sizePaper(); message('纸面预览已更新');
    } catch (error) {
      if (open && request.before === snapshot() && request.profile === profile && request.file === editorFilePath && request.mono === mono) {
        get('figurePaper').hidden = true;
        message(error.message,true);
      }
    }
    finally {
      activePreviewKey = '';
      get('figurePaper').inert = false;
      get('figurePaper').dataset.loading = 'false';
      if (pendingPreview) preview();
    }
  }
  function sizePaper() {
    if (!paperBounds || !open || paperDrag || get('figurePaper').hidden) return;
    const [x0,y0,x1,y1] = paperBounds;
    const contentWidth = x1-x0, height = y1-y0;
    const width = Math.max(contentWidth, data.figure.profiles[data.figure.profile].width_cm);
    const margin = (width-contentWidth)/2, viewport = get('figureViewport');
    const pixels = zoom === 'fit' ? Math.min((viewport.clientWidth-70)/width,(viewport.clientHeight-100)/height) : pixelsPerCm*zoom;
    const scale = Math.max(8,pixels); get('figurePaper').style.width = width*scale+'px'; get('figurePaper').style.height = height*scale+'px';
    get('figureImage').style.width = contentWidth*scale+'px';
    get('figureImage').style.marginLeft = margin*scale+'px';
    get('figureRuler').setAttribute('viewBox',`0 0 ${width*scale} 23`); get('figureRuler').replaceChildren();
    for (let mm=0;mm<=width*10;mm++) {
      const x=mm/10*scale, major=mm%10===0, size=major ? 10 : mm%5===0 ? 6 : 3;
      el('line',{x1:x,x2:x,y1:23,y2:23-size,stroke:'#666','stroke-width':.6},get('figureRuler'));
      if (major) el('text',{x:x+2,y:10,'font-size':9,fill:'#555'},get('figureRuler')).textContent=String(mm/10);
    }
    for (const [id, name] of [['figureGuideSingle','ieee-single'],['figureGuideDouble','ieee-double']]) {
      const columnWidth = data.figure.profiles[name].width_cm;
      get(id).style.left = columnWidth * scale + 'px';
      get(id).hidden = columnWidth > width + .01;
    }
    get('figureCaption').textContent=`${fmt(contentWidth)} × ${fmt(height)} cm · ${tr('含页面边距')}`;
    const overlay=get('figureOverlay'); overlay.setAttribute('viewBox',`${x0-margin} ${-y1} ${width} ${height}`); overlay.replaceChildren();
    const order = {region:0, module:1, element:2, block:3, glyph:3, annotation:4, callout:5};
    const shapes = [...data.figure.shapes].sort((a,b) => order[a.type] - order[b.type]);
    for (const shape of shapes) {
      const [sx,sy]=data.figure.scale, [a,b,c,d]=shape.bounds;
      const selected = selection.some(s=>s.type===shape.type && s.id===shape.id);
      const attrs = {'data-type':shape.type,'data-id':shape.id,tabindex:0,role:'button','aria-label':shape.id,'aria-selected':selected};
      let target;
      if (shape.type === 'callout') {
        const arrow = data.report.callouts[shape.geometry_index];
        target = el('g', attrs, overlay);
        const points = arrow.shape.map(([x,y]) => [x*sx,-y*sy].join(',')).join(' ');
        const tag = arrow.style === 'block' ? 'polygon' : 'polyline';
        el(tag, {points, class:'figure-arrow-hit'}, target);
        el(tag, {points, class:'figure-arrow-outline'}, target);
        if (arrow.label) {
          const [l,b,r,t] = arrow.label;
          el('rect',{x:l*sx,y:-t*sy,width:(r-l)*sx,height:(t-b)*sy},target);
        }
      } else {
        target = el('rect', {x:a*sx,y:-d*sy,width:(c-a)*sx,height:(d-b)*sy,...attrs}, overlay);
      }
      const choose = event => {
        event.stopPropagation();
        const matches = item => item.type === shape.type && item.id === shape.id;
        if (event.shiftKey && selection[0]?.type === shape.type) {
          selection = selection.some(matches) ? selection.filter(item => !matches(item)) : [...selection,shape];
        } else selection = [shape];
        tab('properties'); properties(); controls(); sizePaper();
      };
      target.onclick = choose;
      target.onkeydown = event => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); choose(event); }
      };
      for (const [a,b,c,d] of shape.labels || []) {
        const labelRect = el('rect', {x:a*sx, y:-d*sy, width:(c-a)*sx, height:(d-b)*sy,
          'data-label-id':shape.id, class:'figure-label-target'}, overlay);
        labelRect.onclick = choose;
      }
    }
    const selectedShapes=[...overlay.querySelectorAll('[aria-selected="true"]')];
    window.CircuitFigureImages?.paper(overlay, data.figure.images, [data.figure.scale[0], -data.figure.scale[1], 0, 0]);
    if (selection.length === 1 && selection[0].type === 'callout') {
      const shape = selection[0], arrow = data.report.callouts[shape.geometry_index];
      for (const end of ['from','to']) {
        const [x,y] = arrow[end], [sx,sy] = data.figure.scale;
        el('circle', {cx:x*sx,cy:-y*sy,r:6/scale,class:'figure-arrow-end',
          'data-id':shape.id,'data-type':'callout','data-end':end}, overlay);
      }
    }
    const floating=get('figureFloat'); floating.hidden=!selectedShapes.length;
    if (selectedShapes.length) {
      const boxes=selectedShapes.map(n=>n.getBoundingClientRect()), root=get('figureCanvas').getBoundingClientRect();
      floating.style.left=Math.max(5,Math.min(Math.min(...boxes.map(b=>b.left))-root.left,root.width-floating.offsetWidth-5))+'px';
      const top=Math.min(...boxes.map(b=>b.top))-root.top-floating.offsetHeight-8;
      floating.style.top=(top<5?Math.max(...boxes.map(b=>b.bottom))-root.top+8:top)+'px';
    }
  }
  get('figureOverlay').onclick = event => {
    if (suppressPaperClick) { suppressPaperClick = false; return; }
    if (event.target === get('figureOverlay')) {
      selection = [];
      properties(); controls(); sizePaper();
    }
  };
  function signalAnchor(point) {
    const matrix = get('figureOverlay').getScreenCTM();
    if (!matrix) return null;
    const [sx,sy] = data.figure.scale;
    let nearest = 9, result = null;
    function offer(at, anchor) {
      const distance = Math.hypot((at[0]-point[0])*sx*matrix.a, (at[1]-point[1])*sy*matrix.d);
      if (distance < nearest) {nearest = distance; result = anchor;}
    }
    for (const part of layout.elements) for (const [pin,at] of Object.entries(part.pins))
      offer(at,{element:part.id,pin});
    for (const block of layout.blocks || []) {
      const [x0,y0] = block.from.map((v,i)=>Math.min(v,block.to[i]));
      const [x1,y1] = block.from.map((v,i)=>Math.max(v,block.to[i]));
      const x=(x0+x1)/2, y=(y0+y1)/2;
      for (const [side,at] of Object.entries({n:[x,y1],s:[x,y0],w:[x0,y],e:[x1,y]})) offer(at,{block:block.id,side});
    }
    return result;
  }
  function paperPoint(event) {
    const overlay = get('figureOverlay');
    const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(overlay.getScreenCTM().inverse());
    return [point.x / data.figure.scale[0], -point.y / data.figure.scale[1]];
  }
  const overlay = get('figureOverlay');
  function cancelPaperDrag() {
    if (!paperDrag) return;
    const move = paperDrag;
    paperDrag = null;
    cancelAnimationFrame(move.frame);
    move.ghost?.remove();
    if (overlay.hasPointerCapture(move.pointerId)) overlay.releasePointerCapture(move.pointerId);
  }
  overlay.addEventListener('pointerdown', event => {
    if (event.button !== 0 || paperDrag || working || !paperBounds || !data?.report.ok) return;
    const before = snapshot();
    if (renderedKey !== paperKey(before)) return;
    suppressPaperClick = false;
    const labelId = event.target.dataset.labelId, hit = event.target.closest('[data-type]');
    const type = labelId ? 'element' : hit?.dataset.type, id = labelId || hit?.dataset.id;
    const shape = data.figure.shapes.find(s => s.type === type && s.id === id);
    const inserting = symbolMode || arrowMode;
    if (!inserting && (!shape || event.shiftKey || (!labelId && !['region','block','glyph','annotation','callout'].includes(type)))) return;
    if (!inserting && type === 'region' && layout.regions[Number(id)].fit === 'members') return;
    event.preventDefault(); event.stopPropagation();
    const move = {pointerId:event.pointerId, start:paperPoint(event), client:[event.clientX,event.clientY],
      before, profile, file:editorFilePath, kind:inserting ? null : type, id, symbol:symbolMode,
      endKey:event.target.dataset.end, frame:0, moved:false};
    if (!inserting) {
      move.bounds = shape.bounds;
      if (labelId) {
        const box = event.target.getBBox(), [sx,sy] = data.figure.scale;
        move.bounds = [box.x/sx,-(box.y+box.height)/sy,(box.x+box.width)/sx,-box.y/sy];
        move.offset = layout.elements.find(e => e.id === id).opts?.label_offset || [0,0];
      }
      if (type === 'callout') {
        move.arrow = data.report.callouts[shape.geometry_index];
        move.source = layout.callouts[Number(id)];
      }
      selection = [shape]; tab('properties'); properties(); controls(); sizePaper();
    }
    paperDrag = move;
    overlay.setPointerCapture(event.pointerId);
    suppressPaperClick = true;
    get('figureFloat').hidden = true;
  });
  function showPaperDrag(move) {
    const at = move.at, [sx,sy] = data.figure.scale;
    const delta = at.map((v,i) => v-move.start[i]);
    const shift = p => p.map((v,i) => v+delta[i]);
    const path = points => 'M' + points.map(([x,y]) => [x*sx,-y*sy].join(',')).join(' L');
    if (!move.ghost) {
      move.ghost = el('g', {id:'figureDragPreview','pointer-events':'none'}, overlay);
      move.outline = el('path', {class:'figure-drag-outline'}, move.ghost);
      if (move.bounds && !move.arrow && move.kind !== 'region') {
        // Carry the printed symbol or caption under the cursor, including its actual font.
        const [l,b,r,t] = move.bounds, [x0,y0,x1,y1] = paperBounds;
        const crop = el('svg', {x:l*sx,y:-t*sy,width:(r-l)*sx,height:(t-b)*sy,
          viewBox:[l*sx,-t*sy,(r-l)*sx,(t-b)*sy].join(' ')}, move.ghost);
        el('image',{href:get('figureImage').src,x:x0,y:-y1,width:x1-x0,height:y1-y0,preserveAspectRatio:'none'},crop);
        move.ghost.append(move.outline);
      }
    }
    if (move.symbol || (move.bounds && !move.arrow)) {
      const [l,b,r,t] = move.bounds || [...move.start,...at];
      move.outline.setAttribute('d',path([[l,b],[r,b],[r,t],[l,t]])+' Z');
      if (!move.symbol) move.ghost.setAttribute('transform',`translate(${delta[0]*sx} ${-delta[1]*sy})`);
      return;
    }
    const arrow = move.arrow, source = move.source;
    const point = key => {
      if (move.endKey === key) return at;
      if (move.endKey || source[key+'_anchor']) return arrow[key];
      return shift(arrow[key]);
    };
    const points = arrow ? [point('from'),...(source.via || []).map(p=>move.endKey?p:shift(p)),point('to')] : [move.start,at];
    move.outline.setAttribute('d',arrowGuidePath(points,sx,sy,CALLOUT_GUIDE_HEAD_PX/overlay.getScreenCTM().a,source?.head));
  }
  overlay.addEventListener('pointermove', event => {
    const move = paperDrag;
    if (!move || move.pointerId !== event.pointerId) return;
    move.moved ||= Math.hypot(event.clientX-move.client[0], event.clientY-move.client[1]) >= WIRE_DRAG_PX;
    if (!move.moved) return;
    move.at = paperPoint(event);
    if (!move.frame) move.frame = requestAnimationFrame(() => {
      move.frame = 0;
      if (paperDrag === move) showPaperDrag(move);
    });
  });
  overlay.addEventListener('pointerup', async event => {
    const move = paperDrag;
    if (!move || move.pointerId !== event.pointerId) return;
    const at = paperPoint(event);
    cancelPaperDrag();
    const distance = Math.hypot(event.clientX-move.client[0],event.clientY-move.client[1]);
    if (distance < WIRE_DRAG_PX || move.before !== snapshot() || move.profile !== profile || move.file !== editorFilePath) { sizePaper(); return; }
    const round = n => Number(n.toFixed(4));
    const delta = at.map((v,i) => round(v-move.start[i]));
    const target = {kind:move.kind, ids:[move.id]};
    if (move.kind === 'element') {
      const offset = move.offset.map((v,i) => round(v+delta[i]));
      await transaction('/api/figure-edit', {...target,action:'move-label',offset}, '移动标签');
    } else if (move.endKey) {
      const anchor = !event.altKey && move.source.style === 'line' ? signalAnchor(at) : null;
      await transaction('/api/figure-edit', {...target,action:'move-end',end:move.endKey,at,anchor}, '调整箭头端点');
    } else if (move.kind) {
      const label = {region:'移动色块',block:'移动系统符号',glyph:'移动系统符号',annotation:'移动标签',callout:'移动说明箭头'}[move.kind];
      await transaction('/api/figure-edit', {...target,action:'move-decoration',delta}, label);
    } else {
      symbolMode = null; arrowMode = false;
      get('figureCallout').setAttribute('aria-pressed', 'false');
      overlay.classList.remove('drawing-arrow');
      if (move.symbol) {
        await transaction('/api/figure-edit', {action:'symbol',icon:move.symbol,from:move.start,to:at}, '添加系统符号');
      } else {
        const from = signalAnchor(move.start), to = signalAnchor(at);
        await transaction('/api/figure-edit', {action:'callout', from:move.start, to:at,
          ...(from ? {from_anchor:from} : {}), ...(to ? {to_anchor:to} : {})}, '说明箭头');
      }
    }
  });
  for (const name of ['pointercancel','lostpointercapture']) overlay.addEventListener(name, event => {
    if (paperDrag?.pointerId !== event.pointerId) return;
    cancelPaperDrag(); suppressPaperClick = false; sizePaper();
  });
  function openPalette() {
    if (!selection.length || working || !data) return;
    const source = selection[0].type === 'region' ? layout.regions[Number(selection[0].id)] : null;
    color = source?.fill in data.figure.palette ? source.fill : 'green';
    get('figureBorder').value = source?.border || 'none';
    get('figureRadius').value = source?.corner_radius ?? 0.2;
    get('figureShadow').checked = !!source?.shadow;
    get('figureInset').checked = !!source?.inner_shadow;
    get('figureWidth').value = source?.border_width ?? '';
    get('figureBold').checked = source?.font_weight === 'bold';
    get('figureRegionName').value = source?.label || '';
    let nameMode = selection[0].type === 'module' ? 'module' : 'none';
    if (source) nameMode = source.label ? 'custom' : 'none';
    get('figureRegionNameMode').value = nameMode;
    get('figureRegionNameMode').onchange();
    get('figureRegionPosition').value = source?.label_pos || 'below';
    get('figureSeparateLabel').hidden = selection[0].type !== 'module';
    get('figurePaletteError').textContent = '';
    get('figureSwatches').replaceChildren();
    for (const [name,swatch] of Object.entries(data.figure.palette)) {
      const button = document.createElement('button');
      button.type = 'button'; button.dataset.color = name;
      button.setAttribute('aria-pressed', String(color === name));
      const sample = document.createElement('span');
      sample.className = 'figure-swatch'; sample.style.background = swatch.color;
      const graySample = document.createElement('span');
      graySample.style.background = `rgb(${Array(3).fill(Math.round(swatch.gray * 255)).join(',')})`;
      sample.append(graySample);
      const title = document.createElement('span'); title.textContent = tr(COLORS[name]);
      const gray = document.createElement('small'); gray.textContent = `${tr('灰度')} ${Math.round(swatch.gray * 100)}%`;
      button.append(sample,title,gray);
      button.onclick = () => {
        color = name;
        get('figureSwatches').querySelectorAll('button').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
      };
      get('figureSwatches').append(button);
    }
    palette.showModal();
  }
  get('figureColor').onclick = openPalette;
  get('figurePaletteCancel').onclick = () => palette.close();
  get('figureRegionNameMode').onchange = () => { get('figureRegionName').hidden = get('figureRegionNameMode').value !== 'custom'; };
  palette.querySelector('form').onsubmit = async event => {
    event.preventDefault();
    get('figurePaletteApply').disabled = true;
    const okay = await transaction('/api/figure-edit', {
      action:'region', ...chosen(), color, border:get('figureBorder').value,
      corner_radius:Number(get('figureRadius').value), shadow:get('figureShadow').checked,
      inner_shadow:get('figureInset').checked, border_width:get('figureWidth').value ? Number(get('figureWidth').value) : null,
      font_weight:get('figureBold').checked ? 'bold' : 'normal',
      label:get('figureRegionName').value, label_mode:get('figureRegionNameMode').value,
      label_pos:get('figureRegionPosition').value, separate:get('figureSeparate').checked
    }, '添加或修改色块');
    get('figurePaletteApply').disabled = false;
    if (okay) palette.close();
    else get('figurePaletteError').textContent = get('figureActionError').textContent;
  };
  window.addEventListener('circuit-drawer:layout-changed',()=>{if(open){clearTimeout(timer);timer=setTimeout(refresh,300);}});
  new ResizeObserver(()=>sizePaper()).observe(get('figureViewport'));
  window.addEventListener('keydown',event=>{
    if(!open||event.target.closest('input,textarea,select,dialog,#wavePanel'))return;
    if(event.key==='Escape'){
      arrowMode=false; symbolMode=null; cancelPaperDrag(); get('figureCallout').setAttribute('aria-pressed','false');
      get('figureOverlay').classList.remove('drawing-arrow');
      selection=[];properties();controls();sizePaper();
    }
    if(event.ctrlKey||event.metaKey){
      if(event.key.toLowerCase()==='z'){event.preventDefault();get(event.shiftKey?'figureRedo':'figureUndo').click();}
      if(event.key.toLowerCase()==='y'){event.preventDefault();get('figureRedo').click();}
      if(event.key.toLowerCase()==='s'){event.preventDefault();get('figureSave').click();}
    }
    // Drawing shortcuts cannot alter hidden components while viewing the paper.
    const tabNavigation = event.target.closest('.figure-tabs') && ['ArrowLeft','ArrowRight','Home','End'].includes(event.key);
    if(!['Tab','Enter',' '].includes(event.key) && !tabNavigation)event.stopImmediatePropagation();
  },true);
  document.addEventListener('editor-menu-open', () => {
    if (open) get('saveModule').disabled = true;
  });
  let pan=null;
  get('figureViewport').addEventListener('pointerdown',event=>{
    if(event.button!==1)return;event.preventDefault();const viewport=get('figureViewport');
    pan={x:event.clientX,y:event.clientY,left:viewport.scrollLeft,top:viewport.scrollTop};viewport.setPointerCapture(event.pointerId);viewport.classList.add('panning');get('figureFloat').hidden=true;
  });
  get('figureViewport').addEventListener('pointermove',event=>{if(pan){get('figureViewport').scrollLeft=pan.left+pan.x-event.clientX;get('figureViewport').scrollTop=pan.top+pan.y-event.clientY;}});
  for(const name of ['pointerup','pointercancel'])get('figureViewport').addEventListener(name,()=>{
    if (!pan) return;
    pan=null;get('figureViewport').classList.remove('panning');sizePaper();
  });
  document.addEventListener('editor-language-change',()=>{
    get('figureInput').placeholder=tr('描述想要的论文图效果…');
    if(open)refresh();
  });
  // Restore the workspace preference after the normal editor has loaded its file.
  const startup=new MutationObserver(()=>{if(layout&&report&&!checking){startup.disconnect();if(storage('circuitDrawer.beautify.open')&&!location.search.includes('agent-preview'))enter();}});
  startup.observe(get('drawingStats'),{childList:true,subtree:true});
  window.CircuitFigure={enter,leave,refresh,tab,transaction,install,isOpen:()=>open,
    insertSymbol:kind=>{
      if(!paperBounds || get('figurePaper').hidden){leave();startBoxDraft('blocks');if(boxDraft)boxDraft.icon=kind;return;}
      symbolMode=kind;arrowMode=false;get('figureCallout').setAttribute('aria-pressed','false');get('figureOverlay').classList.add('drawing-arrow');message('在纸面上拖出系统符号的范围，Esc 取消');
    },
    busy:()=>open && (working || !!paperDrag),
    selected:()=>copy(selection), measurement:()=>data,
    adopted:(before, oldProfile, command)=>{
      if (!command.action.startsWith('undo')) record(before, command.undo_label, oldProfile);
      selection=[]; if(open)refresh();
    }};
})();
