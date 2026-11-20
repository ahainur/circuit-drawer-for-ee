/* Adapters keep candidate transactions in the editors' existing undo stacks. */
(() => {
  const control = !!document.getElementById('world');
  const transactionLabels = new Map();
  const copy = value => JSON.parse(JSON.stringify(value));
  const normalize = value => {
    const result = copy(value);
    if (!control) for (const key of ['regions', 'callouts', 'blocks']) {
      if (Array.isArray(result[key]) && !result[key].length) delete result[key];
    }
    return result;
  };
  const current = () => control ? table : layout;
  const canonical = value => JSON.stringify(normalize(value), (_, x) => x && typeof x === 'object' && !Array.isArray(x)
    ? Object.fromEntries(Object.keys(x).sort().map(k => [k, x[k]])) : x);
  const file = () => control ? state?.file || '' : editorFilePath;
  const busy = () => !!(window.CircuitFigure?.busy() || window.CircuitFigureOutput?.busy() ||
    (control ? drag : selectionBusy || drag || wireDraft || boxDraft || instanceDrag || linkDrag || resizing || definitionEdit));
  const redraw = () => control ? draw() : render();
  const open = () => {
    document.body.classList.add('agent-open');
    if (control) {
      document.getElementById('agentControlExisting').hidden = true;
      document.getElementById('panel-agent').hidden = false;
      document.querySelectorAll('[data-agent-control-tab]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.agentControlTab === 'agent')));
    } else showSidebar('agent');
  };
  function close() {
    document.body.classList.remove('agent-open');
    if (control) {
      document.getElementById('agentControlExisting').hidden = false;
      document.getElementById('panel-agent').hidden = true;
      document.querySelectorAll('[data-agent-control-tab]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.agentControlTab === 'check')));
    } else showSidebar('properties');
  }
  async function apply(command) {
    if (busy()) throw new Error(window.CircuitAgentText('finish_edit'));
    const before = canonical(current());
    const beforeTable = JSON.stringify(current()), beforeProfile = profile;
    if (['undo_candidate','undo_task_changes'].includes(command.action)) {
      const stack = control ? undoStack : history;
      const count = command.undo_count || 1;
      if (stack.length < count || canonical(JSON.parse(control ? controlHistoryTable(stack.at(-count)) : historyTable(stack.at(-count)))) !== canonical(command.table)) {
        throw new Error(window.CircuitAgentText('undo_conflict'));
      }
      if (!control && command.profile && (stack.at(-count).profile || profile) !== command.profile) {
        throw new Error(window.CircuitAgentText('undo_conflict'));
      }
      if (control && command.profile && controlHistoryProfile(stack.at(-count)) !== command.profile) {
        throw new Error(window.CircuitAgentText('undo_conflict'));
      }
      for (let i = 0; i < count; i++) undo();
    } else if (control) {
      commit(copy(command.table)); clearSel(); fit(); showSelection();
    } else {
      pushHistory(); layout = copy(command.table); ++layoutGeneration;
      if (command.profile) { profile = command.profile; document.getElementById('profile').value = profile; }
      selected = null; clearGroupSelection(); changed(false); fitView(); render();
    }
    window.CircuitFigure?.adopted(beforeTable, beforeProfile, command);
    if (!command.action.startsWith('undo')) {
      transactionLabels.set(before + '\n' + canonical(current()), command.undo_label);
      if (transactionLabels.size > 200) transactionLabels.delete(transactionLabels.keys().next().value);
    }
    return snapshot();
  }
  function selection() {
    if (!control) return [];
    return [...[...sel.blocks].map(id => 'block:' + id), ...[...sel.texts].map(i => 'text:' + i),
      ...(sel.link == null ? [] : ['link:' + sel.link]), ...(sel.region == null ? [] : ['region:' + sel.region])];
  }
  function snapshot() {
    const previous = (control ? undoStack : history).at(-1);
    const label = previous && transactionLabels.get(canonical(JSON.parse(control ? controlHistoryTable(previous) : historyTable(previous))) + '\n' + canonical(current()));
    const button = document.getElementById(control ? 'undoBtn' : 'undo');
    if (label) button.title = label;
    else if (button.title.startsWith('Agent · ')) button.title = window.cdI18n?.translate('撤销') || 'Undo';
    return {table: normalize(current()), file: file(), profile};
  }
  function mount() {
    const inspector = control ? document.querySelector('main > aside') : document.getElementById('inspector');
    inspector.classList.add('agent-inspector');
    let tabs;
    if (control) {
      const existing = document.createElement('div'); existing.id = 'agentControlExisting';
      existing.append(...inspector.childNodes); inspector.append(existing);
      tabs = document.createElement('div'); tabs.className = 'inspector-tabs'; tabs.setAttribute('role', 'tablist');
      for (const [name, label] of [['check', '校验'], ['selection', '选中'], ['templates', '模板与片段']]) {
        const button = document.createElement('button'); button.type = 'button'; button.textContent = label;
        button.dataset.agentControlTab = name; button.setAttribute('role', 'tab'); button.setAttribute('aria-selected', String(name === 'check'));
        button.onclick = () => {
          close(); tabs.querySelectorAll('button').forEach(b => b.setAttribute('aria-selected', String(b === button)));
          const target = existing.querySelectorAll('h3')[{check: 0, selection: 1, templates: 2}[name]];
          target?.scrollIntoView({block: 'start'});
        };
        tabs.append(button);
      }
      inspector.prepend(tabs);
    } else tabs = inspector.querySelector('.inspector-tabs');
    const tab = document.createElement('button'); tab.id = 'tab-agent'; tab.textContent = 'Agent';
    tab.setAttribute('role', 'tab'); tab.setAttribute('aria-selected', 'false'); tab.setAttribute('aria-controls', 'panel-agent');
    tab.dataset.sidebar = 'agent'; tab.dataset.agentControlTab = 'agent'; tab.onclick = open; tabs.append(tab);
    if (!control) tab.onkeydown = document.getElementById('tab-modes').onkeydown;
    if (control) tabs.addEventListener('keydown', event => {
      const buttons = [...tabs.querySelectorAll('button')], index = buttons.indexOf(event.target);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
        : event.key === 'ArrowRight' ? (index + 1) % buttons.length
        : event.key === 'ArrowLeft' ? (index + buttons.length - 1) % buttons.length : null;
      if (next !== null) { event.preventDefault(); event.stopPropagation(); buttons[next].click(); buttons[next].focus(); }
    });
    const panel = document.createElement('section'); panel.id = 'panel-agent'; panel.className = 'agent'; panel.hidden = true;
    panel.setAttribute('role', 'tabpanel'); panel.setAttribute('aria-labelledby', 'tab-agent'); panel.setAttribute('translate', 'no');
    inspector.append(panel);
    new MutationObserver(() => {
      document.body.classList.toggle('agent-open', !panel.hidden);
      window.dispatchEvent(new Event('circuit-drawer:agent-visibility'));
    }).observe(panel, {attributes:true, attributeFilter:['hidden']});
    const toolbar = document.createElement('button'); toolbar.id = 'agentToggle'; toolbar.textContent = '▣ Agent'; toolbar.onclick = open;
    const ai = document.getElementById('quickAi') || document.querySelector('[data-command="tweak"]') || document.getElementById('tweak');
    (ai?.parentElement || document.querySelector('.quick-tools')).append(toolbar);
    const column = document.createElement('div'); column.id = 'agentCanvasColumn';
    const canvas = document.getElementById('canvasWrap'); canvas.before(column); column.append(canvas);
    return {panel, canvas, column, inspector};
  }
  async function renderPreview(value, selectedProfile, requestedView, viewport) {
    if (!location.search.includes('agent-preview')) throw new Error('Preview requires its isolated editor');
    while (!current()) await new Promise(resolve => setTimeout(resolve, 30));
    profile = selectedProfile;
    const surface = document.getElementById('canvas');
    if (viewport) {
      surface.style.width = viewport[0]+'px'; surface.style.height = viewport[1]+'px';
    }
    if (control) {
      table = copy(value); analysis = await api('/api/check', {table, profile});
      Object.assign(constants, analysis.constants); clearSel(); draw();
      if (requestedView) { view = requestedView; draw(); } else fit();
    } else {
      layout = copy(value); selected = null;
      report = stampReport(await api('/api/check', {layout, profile}), layout); showIssues();
      if (requestedView) view = requestedView; else fitView(); render();
    }
    document.querySelectorAll('dialog[open]').forEach(d => d.close());
    const canvas = document.getElementById('canvas'), bounds = {};
    const attrs = control ? {block: 'data-block', link: 'data-link', text: 'data-text', region: 'data-region'}
      : {element: 'data-id', wire: 'data-wire', text: 'data-ann', region: 'data-region', callout: 'data-callout'};
    const rect = canvas.getBoundingClientRect();
    for (const [type, attr] of Object.entries(attrs)) for (const item of canvas.querySelectorAll(`[${attr}]`)) {
      const r = item.getBoundingClientRect();
      if (r.width || r.height) {
        const key = type + ':' + item.getAttribute(attr), box = [r.x - rect.x, r.y - rect.y, r.width, r.height], old = bounds[key];
        if (old) {
          const x = Math.min(old[0], box[0]), y = Math.min(old[1], box[1]);
          bounds[key] = [x, y, Math.max(old[0] + old[2], box[0] + box[2]) - x, Math.max(old[1] + old[3], box[1] + box[3]) - y];
        } else bounds[key] = box;
      }
    }
    const clone = canvas.cloneNode(true);
    if (viewport) {clone.style.removeProperty('width');clone.style.removeProperty('height');}
    clone.removeAttribute('id'); clone.querySelectorAll('[id]').forEach(n => n.removeAttribute('id'));
    return {markup: clone.outerHTML, bounds, view: copy(view)};
  }
  window.CircuitAgentEditor = {kind: control ? 'control' : 'circuit', snapshot, selection, busy, apply, open, close, mount,
    ready: () => !!current(), renderPreview, redraw};
})();
