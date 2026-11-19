/* Drag or use arrow keys on the splitters. Persist dimensions separately per editor. */
window.CircuitPanelLayout = (() => {
  const ranges = {dock:[300,600,400], library:[100,440,254], reference:[48,200,62],
    description:[60,270,90], results:[56,250,96], devices:[130,360,200]};
  const labels = {dock:'Agent 侧栏宽度',library:'提示词库高度',reference:'参考图区高度',
    description:'任务描述区高度',results:'结果区高度',devices:'器件列表宽度'};
  const variables = {dock:'--agent-dock-width',library:'--agent-library-height',reference:'--agent-reference-height',
    description:'--agent-description-height',results:'--agent-results-height',devices:'--spice-list-width'};
  const editor = document.getElementById('world') ? 'control' : 'circuit';
  const prefix = 'circuit-drawer.panel-layout.' + editor + '.';
  const tr = s => window.cdI18n?.translate(s) || s;
  function preferred(key) {
    try {
      // Launcher sessions use different localhost ports. Host cookies share sizes across them.
      const entry = document.cookie.split('; ').find(item => item.startsWith(prefix + key + '='));
      const stored = entry ? entry.slice(entry.indexOf('=') + 1) : localStorage.getItem(prefix + key);
      if (stored !== null && Number.isFinite(Number(stored))) return Number(stored);
    } catch {}
    return ranges[key][2];
  }
  function remember(key, size) {
    try { document.cookie = prefix + key + '=' + size + '; Path=/; Max-Age=31536000; SameSite=Strict'; } catch {}
    try { localStorage.setItem(prefix + key, String(size)); } catch {}
  }
  function restore(handle) {
    const key = handle.dataset.panelSize;
    remember(key, ranges[key][2]); apply(handle, ranges[key][2]);
  }
  function apply(handle, requested, save = false) {
    const key = handle.dataset.panelSize, [min, max] = ranges[key];
    const available = key === 'dock' ? Math.max(min, innerWidth - (editor === 'circuit' ? 420 : 200)) :
      key === 'devices' ? Math.max(min, handle.parentElement.clientWidth * .4) :
      key === 'library' && innerHeight < 720 ? Math.max(min, innerHeight * .24) : max;
    const upper = Math.floor(Math.min(max,available)), size = Math.round(Math.min(upper, Math.max(min, requested)));
    document.body.style.setProperty(variables[key], size + 'px');
    handle.setAttribute('aria-valuemin',min); handle.setAttribute('aria-valuemax',upper);
    handle.setAttribute('aria-valuenow',size); handle.setAttribute('aria-valuetext',size + ' px');
    if (save) remember(key, size);
  }
  function describe(handle) {
    handle.setAttribute('aria-label', tr(labels[handle.dataset.panelSize]));
    handle.title = tr('拖动或按方向键调节，双击或按 Enter 恢复此块');
  }
  document.addEventListener('editor-language-change', () =>
    document.querySelectorAll('[data-panel-size]').forEach(describe));
  function splitter(handle, key, controls) {
    const width = key === 'dock' || key === 'devices', sign = key === 'dock' || key === 'results' ? -1 : 1;
    handle.type = 'button'; handle.dataset.panelSize = key; handle.classList.add('panel-split');
    handle.setAttribute('role','separator'); handle.setAttribute('aria-orientation',width ? 'vertical' : 'horizontal');
    handle.setAttribute('aria-controls',controls); describe(handle);
    apply(handle,preferred(key));
    const pos = e => width ? e.clientX : e.clientY;
    let drag;
    handle.onpointerdown = event => {
      if (event.button !== 0 || drag) return;
      drag = {id:event.pointerId, start:pos(event), size:Number(handle.getAttribute('aria-valuenow'))};
      handle.setPointerCapture(event.pointerId); handle.focus(); event.preventDefault();
    };
    handle.onpointermove = event => {
      if (drag?.id === event.pointerId) apply(handle,drag.size + sign * (pos(event) - drag.start));
    };
    handle.onpointerup = event => {
      if (drag?.id === event.pointerId) apply(handle,Number(handle.getAttribute('aria-valuenow')),true);
    };
    handle.onlostpointercapture = () => { drag = null; };
    handle.ondblclick = () => restore(handle);
    handle.onkeydown = event => {
      const delta = (width ? {ArrowLeft:-1, ArrowRight:1} : {ArrowUp:-1, ArrowDown:1})[event.key];
      if (delta) apply(handle,Number(handle.getAttribute('aria-valuenow')) + 12 * delta * sign,true);
      else if (event.key === 'Enter' || event.key === ' ') restore(handle);
      else if (['Home','End'].includes(event.key)) {
        apply(handle,ranges[key][event.key === 'Home' ? 0 : 1],true);
      } else return;
      event.preventDefault(); event.stopPropagation();
    };
  }
  function add(target,key,before=false,controls=target.id) {
    const handle = document.createElement('button');
    if (before) target.before(handle); else target.after(handle);
    splitter(handle,key,controls);
  }
  function reset() {
    for (const key of Object.keys(ranges)) {
      remember(key, ranges[key][2]);
      document.body.style.removeProperty(variables[key]);
    }
    document.querySelectorAll('[data-panel-size]').forEach(h => apply(h,ranges[h.dataset.panelSize][2]));
  }
  window.addEventListener('resize', () => document.querySelectorAll('[data-panel-size]').forEach(h => apply(h,preferred(h.dataset.panelSize))));
  return {
    attachAgent(nodes) {
      const dock = document.createElement('button'); nodes.inspector.prepend(dock);
      splitter(dock,'dock',nodes.panel.id);
      add(document.getElementById('agentRefs'),'reference');
      add(document.getElementById('agentInput'),'description');
      add(document.getElementById('agentResults'),'results',true);
      document.getElementById('agentResetLayout').onclick = reset;
    },
    attachLibrary(box) {
      if (box && !box.querySelector('[data-panel-size]')) add(box.querySelector('[data-content]'),'library');
    },
    attachSpice(slot) {
      const handle = document.createElement('button'); handle.id = slot.id; slot.replaceWith(handle);
      splitter(handle,'devices','spiceDeviceList');
      document.getElementById('spiceResetLayout').onclick = reset;
    }
  };
})();
