// Presentation state only: do not persist these settings in the coordinate table.
let sidebarPage = 'properties', editorFilePath = '', savedEditorTable = null, fittedViewWidth = null;
let noticeTimer = null;
const NOTICE_DURATION_MS = 3000;

function showEditorNotice(message, bad) {
  const notice = document.getElementById('editorNotice');
  clearTimeout(noticeTimer);
  notice.textContent = message; notice.className = bad ? 'bad' : ''; notice.hidden = false;
  noticeTimer = setTimeout(() => { notice.hidden = true; }, NOTICE_DURATION_MS);
}

function editorTableSnapshot(table) {
  const value = {...table};
  // Existing drawing code materializes these empty sections on first render.
  for (const key of ['regions', 'callouts', 'blocks']) if (Array.isArray(value[key]) && !value[key].length) delete value[key];
  return JSON.stringify(value, (_, item) => item && typeof item === 'object' && !Array.isArray(item)
    ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item);
}

function showSidebar(name, focus = false) {
  sidebarPage = name;
  document.querySelectorAll('[data-sidebar]').forEach(tab => {
    const active = tab.dataset.sidebar === name;
    tab.setAttribute('aria-selected', String(active)); tab.tabIndex = active ? 0 : -1;
    document.getElementById(tab.getAttribute('aria-controls')).hidden = !active;
    if (active && focus) tab.focus();
  });
  const toggle = document.getElementById('showNetlist');
  if (name === 'netlist' && !toggle.checked) toggle.click();
}

function setEditorFile(path, saved = false) {
  editorFilePath = path;
  const name = String(path).split(/[\\/]/).at(-1);
  document.getElementById('file').textContent = name;
  document.getElementById('currentDocument').title = path;
  document.getElementById('file').title = path;
  if (saved) savedEditorTable = editorTableSnapshot(typeof saved === 'string' ? JSON.parse(saved) : layout);
  updateEditorChrome();
}

function updateEditorChrome() {
  if (!layout) return;
  const count = report?.module_info?.element_count ?? layout.elements.length;
  const nodes = new Set(Object.values(report?.nets || {})).size;
  // A module table draws only once its instances are assembled; when that
  // fails the canvas is blank, but the file is not empty.
  const modular = !!layout.modules?.instances?.length, unassembled = modular && !!report && !report.module_info;
  document.getElementById('drawingStats').textContent = unassembled ? '模块拼装失败，见问题栏' : `${count} 元件 · ${nodes} 节点`;
  document.getElementById('drawingStats').style.color = unassembled ? 'var(--bad)' : '';
  document.getElementById('emptyCanvas').hidden = !!(modular || count || layout.wires.length ||
    (layout.annotations || []).length || (layout.regions || []).length ||
    (layout.callouts || []).length || (layout.blocks || []).length || (layout.wave_symbols || []).length);
  if (view && fittedViewWidth) document.getElementById('zoomStatus').textContent = `${Math.round(fittedViewWidth / view.w * 100)}%`;
  const tip = document.getElementById('modeTip');
  const text = wireDraft || boxDraft ? tip.textContent :
    (typeof moduleMarquee !== 'undefined' && moduleMarquee) ? '框选：拖出矩形，Shift 点选可追加' :
    selected ? `${selected.id || '已选对象'} · 拖动或方向键调整，属性页编辑` : '左键框选；中键或空格加左键平移';
  document.getElementById('toolHint').textContent = text;
  document.getElementById('toolHint').title = text;
  // notes record overlaps the user allowed; they are not problems to count
  const issues = (report?.issues || []).filter(i => i.level !== 'note' && i.code !== 'empty-drawing').length;
  document.getElementById('issueCount').hidden = !issues;
  document.getElementById('issueCount').textContent = issues;
  const snapshot = editorTableSnapshot(layout);
  document.getElementById('fileDirty').hidden = savedEditorTable === null || savedEditorTable === snapshot;
  updateHistoryControls(snapshot);
  if (typeof syncSelectionControls === 'function') syncSelectionControls();
}

function drawCanvasGrid(root) {
  const px = Math.hypot(svg.getScreenCTM()?.a || 1, svg.getScreenCTM()?.b || 0);
  const defs = el('defs', {}, root), pattern = el('pattern', {
    id: 'snapDots', x: -SNAP / 2, y: -SNAP / 2, width: SNAP, height: SNAP, patternUnits: 'userSpaceOnUse'
  }, defs);
  // Keep the dots on the actual snap lattice; fade them when zooming out.
  el('circle', {cx: SNAP / 2, cy: SNAP / 2, r: Math.min(.55 / px, SNAP / 3), fill: '#b6c2d1', opacity: .38 * Math.min(1, SNAP * px / 4)}, pattern);
  el('rect', {x:view.x0 - view.w, y:-view.y0 - 2 * view.h, width:view.w * 3, height:view.h * 3,
    fill:'url(#snapDots)', 'pointer-events':'none', id:'canvasDots'}, root);
  if (!document.getElementById('showCoordinates').checked) return;
  const group = el('g', {id:'canvasCoordinates', fill:'#94a0af', 'font-size':10 / px, 'pointer-events':'none'}, root);
  // Screen-space spacing bounds the number of labels even at extreme zoom.
  const step = .5 * Math.max(1, 2 ** Math.ceil(Math.log2(54 / px / .5)));
  for (let x = Math.ceil(view.x0 / step) * step; x < view.x0 + view.w; x += step)
    el('text', {x, y:-view.y0 - 8 / px, 'text-anchor':'middle'}, group).textContent = r4(x);
  for (let y = Math.ceil(view.y0 / step) * step; y < view.y0 + view.h; y += step)
    el('text', {x:view.x0 + 8 / px, y:-y}, group).textContent = r4(y);
}

async function openExamplePicker() {
  showDialog('打开示例', '<p>载入已有手画模板，先查看，再按需保存。</p><div class="example-list" id="exampleChoices"></div><p id="exampleError" role="status"></p>');
  for (const [name, title] of [['buck','Buck 降压'], ['boost','Boost 升压'], ['flyback','反激']]) {
    const button = document.createElement('button'); button.textContent = title; button.dataset.example = name;
    button.onclick = async () => {
      button.disabled = true;
      try {
        const result = await api('/api/from-template', {name});
        useLayout(result.layout); document.getElementById('dlg').close();
      } catch (error) { document.getElementById('exampleError').textContent = error.message; }
      finally { button.disabled = false; }
    };
    document.getElementById('exampleChoices').append(button);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const icons = {
    save:'M5 3h12l4 4v14H3V3z M7 3v6h10V3 M7 21v-8h10v8',
    export:'M12 3v12 M8 7l4-4 4 4 M4 14v7h16v-7',
    undo:'M8 5L3 10l5 5 M3 10h11a6 6 0 0 1 6 6v3',
    redo:'M16 5l5 5-5 5 M21 10H10a6 6 0 0 0-6 6v3',
    delete:'M4 6h16 M9 6V3h6v3 M6 6l1 15h10l1-15 M10 10v7 M14 10v7',
    wire:'M4 5h8v14h8 M2 3h4v4H2z M18 17h4v4h-4z',
    select:'M8 3H3v5 M16 3h5v5 M21 16v5h-5 M8 21H3v-5 M9 8l7 5-4 1-1 4z',
    fit:'M8 3H3v5 M16 3h5v5 M21 16v5h-5 M8 21H3v-5 M8 8h8v8H8z',
    preview:'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12 M15 12a3 3 0 1 0-6 0 3 3 0 1 0 6 0',
    wave:'M3 4v16h18 M5 13l4-6 5 10 4-7 3 3',
    ai:'M12 3l2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z M20 2v4 M18 4h4'
  };
  document.querySelectorAll('[data-icon]').forEach(button => {
    const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    icon.setAttribute('viewBox', '0 0 24 24'); icon.setAttribute('class', 'tool-icon'); icon.setAttribute('aria-hidden', 'true');
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path'); path.setAttribute('d', icons[button.dataset.icon]);
    icon.append(path); button.prepend(icon);
    if (!button.title) button.title = button.textContent.trim();
  });
  document.querySelectorAll('.menu-panel button').forEach(button => {
    const key = button.querySelector('kbd')?.textContent;
    const action = [...button.childNodes].filter(n => n.nodeType === Node.TEXT_NODE).map(n => n.textContent).join('').trim();
    button.title = action + (key ? ` (${key})` : '');
  });
  document.querySelectorAll('[data-sidebar]').forEach(tab => {
    tab.onclick = () => showSidebar(tab.dataset.sidebar);
    tab.onkeydown = event => {
      const tabs = [...document.querySelectorAll('[data-sidebar]')], i = tabs.indexOf(tab);
      const next = event.key === 'ArrowRight' ? (i + 1) % tabs.length : event.key === 'ArrowLeft' ? (i + tabs.length - 1) % tabs.length :
        event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : null;
      if (next !== null) { event.preventDefault(); event.stopPropagation(); showSidebar(tabs[next].dataset.sidebar, true); }
    };
  });
  document.getElementById('showCoordinates').onchange = () => render();
  document.getElementById('openExample').onclick = openExamplePicker;
  document.getElementById('emptyDescribe').onclick = () => document.getElementById('describe').click();
  document.getElementById('currentDocument').onclick = () => svg.focus();
  svg.addEventListener('pointerdown', () => {
    if (typeof focusedModuleId !== 'undefined' && focusedModuleId) showSidebar('properties');
  }, true);
  document.getElementById('menuInstanceSelect').addEventListener('change', event => {
    if (event.target.value) showSidebar('properties');
  });
  new MutationObserver(updateEditorChrome).observe(document.getElementById('modeTip'), {attributes:true, childList:true, subtree:true});
  window.addEventListener('resize', () => { if (layout) render(); });
  updateEditorChrome();
});
