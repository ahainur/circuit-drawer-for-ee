/* Control figures share the checked table, paper renderer and Agent candidate service. */
(() => {
  const get = (id) => document.getElementById(id);
  const tr = (text) => window.cdI18n?.translate(text) || text;
  const esc = (value) =>
    String(value ?? '').replace(
      /[&<>"']/g,
      (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
    );
  const button = (id, text) => `<button id="${id}" type="button">${tr(text)}</button>`;
  const main = document.querySelector('main');
  const ribbon = document.createElement('div');
  ribbon.id = 'figureRibbon';
  ribbon.hidden = true;
  ribbon.innerHTML = `<div class="figure-group">${button('figureBack', '返回绘图')}${button('figureSave', '保存')}${button('figureUndo', '撤销')}${button('figureRedo', '重做')}<small>${tr('文件')}</small></div>
    <div class="figure-group"><div class="figure-settings"><label>${tr('栏宽')} <select id="figureProfile"><option value="ieee-single">${tr('IEEE 单栏')}</option><option value="ieee-double">${tr('IEEE 双栏')}</option></select></label>
    <label>${tr('字号')} <input id="figureFont" type="number" min="5" max="24" step="0.5"> pt</label><label>${tr('线宽')} <input id="figureLine" type="number" min="0.1" max="3" step="0.05"> pt</label></div>${button('figureTrim', '去留白')}<small>${tr('版式')}</small></div>
    <div class="figure-group">${button('figureMono', '黑白预览')}${button('figureArrange', '整理标签 ✦')}${button('figureCompose', '组合出图')}${button('figureExport', '导出检查')}<small>${tr('预览与导出')}</small></div>`;
  const tabs = document.createElement('div');
  tabs.className = 'document-tabs';
  tabs.hidden = true;
  main.before(ribbon, tabs);
  const entry = document.createElement('button');
  entry.id = 'figureToggle';
  entry.textContent = tr('美化出图');
  entry.onclick = enter;
  document.querySelector('.quick-tools').append(entry);
  const workspace = document.createElement('main');
  workspace.id = 'figureWorkspace';
  workspace.hidden = true;
  workspace.innerHTML = `<aside id="figureLeft"><section class="figure-section"><h3>${tr('纸面')}</h3><div id="figureStats"></div></section>
    <section class="figure-section"><h3>${tr('操作记录（每条一步撤销）')}</h3><ol id="figureHistory"></ol></section></aside>
    <section id="figureViewport"><div id="figureCanvas"><div id="figurePaper" hidden><img id="figureImage" alt="${tr('当前图纸的真实出图预览')}"><svg id="figureOverlay" tabindex="0" aria-label="${tr('选择图中对象')}"></svg></div></div>
    <div id="figureZoom">${button('figureFit', '适应窗口')}${button('figureActual', '纸面 100%')}${button('figureCalibrate', '校准尺寸')}${button('figureSidebar', '属性／提示词')}</div><div id="figureStatus"><span id="figureStatusText" role="status"></span></div></section>
    <aside id="figureRight"><div class="figure-tabs" role="tablist"><button data-figure-tab="properties" role="tab" aria-selected="false">${tr('属性')}</button><button data-figure-tab="agent" role="tab" aria-selected="true">${tr('出图 Agent')}</button></div>
    <section id="figureProperties" class="figure-panel" hidden><label>${tr('对象')} <select id="figureObject"></select></label>
    <label>${tr('填色')} <select id="figureFill"></select></label><label>${tr('圆角')} cm <input id="figureRadius" type="number" min="0" max="1" step="0.05"></label>
    <label>${tr('无边框')} <input id="figureNoBorder" type="checkbox"></label><label>${tr('彩色图标')} <input id="figureIconColor" type="checkbox"></label>
    <label>${tr('轻投影')} <input id="figureShadow" type="checkbox"></label><label>${tr('灰色内阴影')} <input id="figureInset" type="checkbox"></label><label>${tr('边框线宽')} (pt) <input id="figureWidth" type="number" min=".1" max="3" step=".05" placeholder="${tr('跟随纸面')}"></label>
    <label>${tr('标签粗体')} <input id="figureBold" type="checkbox"></label>${button('figureStyleApply', '应用样式')}${button('figureAssign','交给出图 Agent…')}<p id="figurePropertyNote"></p></section>
    <section id="figureAgent" class="figure-panel"><div id="figureAgentContent"></div><textarea id="figureInput" placeholder="${tr('描述想要的论文图效果…')}"></textarea><div id="figureAgentActions"></div></section></aside>`;
  main.after(workspace);
  let open = false,
    working = false,
    mono = false,
    zoom = 'fit',
    data = null,
    preview = null,
    version = 0,
    timer;
  let records = [];
  const menuStates = new Map();
  const key = () => JSON.stringify(window.CircuitAgentEditor.snapshot());
  function say(text) {
    get('figureStatusText').textContent = tr(text);
  }
  function enter() {
    if (!table || open) return;
    if (window.CircuitAgentEditor.busy()) {
      toast(tr('请先完成当前编辑'));
      return;
    }
    open = true;
    workspace.hidden = ribbon.hidden = tabs.hidden = false;
    main.inert = true;
    document.body.classList.add('figure-open');
    for (const item of document.querySelectorAll('[data-menu]')) {
      if (['file', 'help'].includes(item.dataset.menu)) continue;
      menuStates.set(item, item.disabled);
      item.disabled = true;
    }
    window.CircuitPromptLibrary.mount({
      textarea: get('figureInput'),
      domain: 'figure',
      mode: 'edit',
      table: () => table,
      api,
      busy: () => working,
    });
    refresh();
    get('figureBack').focus();
  }
  function leave() {
    if (working) return;
    open = false;
    ++version;
    workspace.hidden = ribbon.hidden = tabs.hidden = true;
    main.inert = false;
    document.body.classList.remove('figure-open');
    menuStates.forEach((disabled, item) => (item.disabled = disabled));
    menuStates.clear();
    draw();
    entry.focus();
  }
  function tab(name) {
    for (const id of ['properties', 'agent'])
      get(id === 'agent' ? 'figureAgent' : 'figureProperties').hidden = id !== name;
    for (const item of workspace.querySelectorAll('[data-figure-tab]'))
      item.setAttribute('aria-selected', String(item.dataset.figureTab === name));
  }
  workspace
    .querySelectorAll('[data-figure-tab]')
    .forEach((item) => (item.onclick = () => tab(item.dataset.figureTab)));
  function install(next, label) {
    if (JSON.stringify(next) === JSON.stringify(table)) return;
    commit(next);
    records.push({ table: JSON.stringify(next), label });
    refresh();
  }
  async function transaction(path, payload, label) {
    if (working) return false;
    const before = key();
    working = true;
    controls();
    say('校验中…');
    try {
      const result = await api(path, { table, layout: table, profile, ...payload });
      if (key() !== before) throw new Error(tr('等待操作时图纸已改变，请重试。'));
      install(result.table || result.layout, label);
      return true;
    } catch (error) {
      say(error.message);
      return false;
    } finally {
      working = false;
      controls();
    }
  }
  function controls() {
    ribbon.querySelectorAll('button,input,select').forEach((item) => (item.disabled = working));
    get('figureUndo').disabled = working || !undoStack.length;
    get('figureRedo').disabled = working || !redoStack.length;
    get('figureExport').disabled = working || !data?.report?.ok;
  }
  async function refresh() {
    if (!open || working) return;
    const sequence = ++version,
      before = key();
    get('figurePaper').dataset.loading = 'true';
    say('正在生成真实纸面预览…');
    try {
      const result = await api('/api/figure-check', { table, profile });
      if (sequence !== version || before !== key() || !open) return;
      data = result;
      get('figureProfile').value =
        profile === 'single' ? 'ieee-single' : profile === 'double' ? 'ieee-double' : profile;
      get('figureFont').value = table.figure?.font_pt ?? 8;
      get('figureLine').value = table.figure?.line_pt ?? 0.55;
      get('figureStats').textContent = result.report.ok
        ? tr('图纸校验通过')
        : (result.report.errors || []).join('\n');
      properties();
      controls();
      get('figureHistory').innerHTML = records
        .map(
          (record) =>
            `<li ${record.table === JSON.stringify(table) ? 'class="figure-history-current"' : ''}>${esc(tr(record.label))}</li>`,
        )
        .join('');
      if (!result.report.ok) {
        get('figurePaper').hidden = true;
        say('校验未通过，请返回绘图处理');
        return;
      }
      const rendered = await api('/api/figure-preview', { table, profile, mono });
      if (sequence !== version || before !== key() || !open) return;
      preview = rendered;
      get('figureImage').src = 'data:image/png;base64,' + rendered.png;
      get('figurePaper').hidden = false;
      sizePaper();
      say('纸面预览已更新');
    } catch (error) {
      if (sequence === version) say(error.message);
    } finally {
      if (sequence === version) get('figurePaper').dataset.loading = 'false';
    }
  }
  function sizePaper() {
    if (!preview || !open) return;
    const bounds = preview.bounds_cm,
      width = bounds[2] - bounds[0],
      height = bounds[3] - bounds[1];
    let density = 96 / 2.54;
    try {
      density = JSON.parse(localStorage.getItem('circuitDrawer.beautify.pixelsPerCm')) || density;
    } catch {}
    const scale =
      zoom === 'fit'
        ? Math.min(
            (get('figureViewport').clientWidth - 75) / width,
            (get('figureViewport').clientHeight - 100) / height,
          )
        : density * zoom;
    get('figurePaper').style.width = width * Math.max(4, scale) + 'px';
    get('figurePaper').style.height = height * Math.max(4, scale) + 'px';
    const overlay = get('figureOverlay');
    overlay.replaceChildren();
    overlay.setAttribute('viewBox', `0 0 ${width} ${height}`);
    const s = data.report.scale_cm_per_unit;
    for (const [section, items] of [
      ['regions', data.report.regions],
      ['blocks', data.report.blocks],
    ]) {
      (items || []).forEach((item, index) => {
        const box = item.box || item.rect;
        if (!box) return;
        const rect = document.createElementNS(SVGNS, 'rect');
        const values = {
          x: box[0] * s - bounds[0],
          y: bounds[3] - box[3] * s,
          width: (box[2] - box[0]) * s,
          height: (box[3] - box[1]) * s,
        };
        Object.entries(values).forEach(([name, value]) => rect.setAttribute(name, value));
        rect.setAttribute(
          'aria-selected',
          String(get('figureObject').value === section + ':' + index),
        );
        rect.onclick = () => {
          get('figureObject').value = section + ':' + index;
          selectedProperties();
          tab('properties');
          sizePaper();
        };
        overlay.append(rect);
      });
    }
    window.CircuitFigureImages?.paper(overlay, data.report.images, [s, -s, -bounds[0], bounds[3]]);
  }
  function properties() {
    const selected = get('figureObject').value;
    get('figureObject').innerHTML = ['blocks', 'regions', 'links', 'texts']
      .flatMap((section) =>
        (table[section] || []).map(
          (item, index) =>
            `<option value="${section}:${index}">${esc(item.id || item.label || item.text || tr({regions:'色块',links:'连线',texts:'文字'}[section]) + ' ' + (index + 1))}</option>`,
        ),
      )
      .join('');
    if ([...get('figureObject').options].some((item) => item.value === selected))
      get('figureObject').value = selected;
    const colors = Object.keys(data.report.constants?.palette || {});
    get('figureFill').innerHTML = ['white', 'transparent', ...colors]
      .map((color) => `<option value="${esc(color)}">${esc(tr(color))}</option>`)
      .join('');
    selectedProperties();
  }
  function selectedProperties() {
    const [section, index] = get('figureObject').value.split(':'),
      item = table[section]?.[Number(index)];
    const rectangular =
      item && (section === 'regions' || ['block', 'limiter', 'tag'].includes(item.type));
    const fill =
      item?.fill ||
      (section === 'regions' ? 'gray' : data.report.blocks[Number(index)]?.fill || 'white');
    if (![...get('figureFill').options].some((option) => option.value === fill))
      get('figureFill').add(new Option(fill, fill));
    get('figureFill').value = fill;
    get('figureRadius').value = item?.corner_radius || 0;
    get('figureShadow').checked = !!item?.shadow;
    get('figureInset').checked = !!item?.inner_shadow;
    get('figureBold').checked = item?.font_weight === 'bold';
    get('figureWidth').value = item?.border_width ?? '';
    get('figureFill').disabled = !['blocks','regions'].includes(section);
    get('figureInset').disabled = get('figureWidth').disabled = get('figureRadius').disabled = get('figureShadow').disabled = !rectangular;
    get('figureStyleApply').disabled = !item;
    get('figureAssign').disabled = !item;
    get('figureNoBorder').closest('label').hidden = item?.type !== 'block';
    get('figureNoBorder').checked = item?.border === 'none';
    get('figureIconColor').closest('label').hidden = !data.report.constants.color_icons.includes(item?.icon);
    get('figureIconColor').checked = !!item?.icon_color;
  }
  get('figureObject').onchange = () => {
    selectedProperties();
    sizePaper();
  };
  get('figureStyleApply').onclick = async () => {
    const [section, index] = get('figureObject').value.split(':'),
      base = [section, Number(index)];
    if (!get('figureWidth').reportValidity()) return;
    const edits = [{path:[...base,'font_weight'],value:get('figureBold').checked?'bold':'normal'}];
    const item = table[section][Number(index)];
    if (item.type === 'block' && (item.border === 'none') !== get('figureNoBorder').checked)
      edits.push({path:[...base,'border'], value:get('figureNoBorder').checked ? 'none' : 'black'});
    if (data.report.constants.color_icons.includes(item.icon) && !!item.icon_color !== get('figureIconColor').checked)
      edits.push({path:[...base,'icon_color'], value:get('figureIconColor').checked});
    if (!get('figureFill').disabled) edits.push(
      {
        path: [...base, 'fill'],
        value:
          get('figureFill').value === 'transparent' && section === 'regions'
            ? 'none'
            : get('figureFill').value,
      },
    );
    if (!get('figureRadius').disabled)
      edits.push(
        { path: [...base, 'corner_radius'], value: Number(get('figureRadius').value) },
        { path: [...base, 'shadow'], value: get('figureShadow').checked },
        { path: [...base, 'inner_shadow'], value: get('figureInset').checked },
        { path: [...base, 'border_width'], value: get('figureWidth').value ? Number(get('figureWidth').value) : null },
      );
    if (await transaction('/api/figure-output', { action: 'style', edits }, '调整方框样式'))
      refresh();
  };
  get('figureBack').onclick = leave;
  get('figureSave').onclick = () => get('saveBtn').click();
  get('figureUndo').onclick = () => {
    undo();
    refresh();
  };
  get('figureRedo').onclick = () => {
    redo();
    refresh();
  };
  get('figureProfile').onchange = () => {
    const next = clone(table);
    next.profile = get('figureProfile').value;
    install(next, '更改栏宽');
  };
  for (const [id, field] of [
    ['figureFont', 'font_pt'],
    ['figureLine', 'line_pt'],
  ])
    get(id).onchange = async () => {
      await transaction(
        '/api/figure-output',
        { action: 'style', edits: [{ path: ['figure', field], value: Number(get(id).value) }] },
        '调整纸面样式',
      );
      refresh();
    };
  get('figureTrim').onclick = async () => {
    await transaction(
      '/api/figure-output',
      { action: 'style', edits: [{ path: ['figure', 'margin_mm'], value: 0 }] },
      '去除页面留白',
    );
    refresh();
  };
  get('figureMono').onclick = () => {
    mono = !mono;
    get('figureMono').setAttribute('aria-pressed', String(mono));
    refresh();
  };
  get('figureCompose').onclick = () => window.CircuitFigureOutput.openComposition();
  get('figureExport').onclick = () => window.CircuitFigureOutput.openChecks();
  get('figureFit').onclick = () => {
    zoom = 'fit';
    sizePaper();
  };
  get('figureActual').onclick = () => {
    zoom = 1;
    sizePaper();
  };
  get('figureCalibrate').onclick = () => window.CircuitFigureOutput.calibrate(sizePaper);
  get('figureSidebar').onclick = () => {
    workspace.classList.toggle('aside-closed');
    sizePaper();
  };
  new ResizeObserver(sizePaper).observe(get('figureViewport'));
  window.addEventListener('circuit-drawer:layout-changed', () => {
    clearTimeout(timer);
    timer = setTimeout(refresh, 300);
  });
  window.addEventListener(
    'keydown',
    (event) => {
      if (!open || event.target.closest('input,textarea,select,dialog')) return;
      if (event.ctrlKey || event.metaKey) {
        if (event.key.toLowerCase() === 'z') {
          event.preventDefault();
          get(event.shiftKey ? 'figureRedo' : 'figureUndo').click();
        }
        if (event.key.toLowerCase() === 'y') {
          event.preventDefault();
          get('figureRedo').click();
        }
        if (event.key.toLowerCase() === 's') {
          event.preventDefault();
          get('figureSave').click();
        }
      }
      if (!['Tab', 'Enter', ' '].includes(event.key)) event.stopImmediatePropagation();
    },
    true,
  );
  document.addEventListener('editor-language-change', () => {
    if (open) refresh();
  });
  window.CircuitFigure = {
    enter,
    leave,
    refresh,
    tab,
    install,
    transaction,
    isOpen: () => open,
    busy: () => open && working,
    selected: () => {
      const [type, index] = get('figureObject').value.split(':');
      return type ? [{ type:type.slice(0,-1), id:type === 'blocks' ? table.blocks[Number(index)].id : index }] : [];
    },
    measurement: () => data,
    adopted: (before, oldProfile, command) => {
      records.push({ table: JSON.stringify(table), label: command.undo_label });
      refresh();
    },
  };
})();
