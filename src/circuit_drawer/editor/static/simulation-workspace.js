/* Keep every draft field mounted when changing devices or following an issue. */
function mountSpiceWorkspace(form, {devices, base, profile, readSpice, available, initialBindings}) {
  const el = id => document.getElementById(id), tr = text => window.cdI18n?.translate(text) || text;
  const list = el('spiceDeviceList'), search = el('spiceDeviceSearch');
  const name = d => d.name || d.id;
  let active = '', stamp = 0, timer, lastDraft;

  function select(id, focus = false, field = '') {
    active = id;
    list.value = id;
    let selected;
    for (const row of el('spiceComponents').children) {
      row.hidden = row.dataset.id !== id;
      row.classList.toggle('context-target', focus && !row.hidden);
      if (!row.hidden) selected = row;
    }
    for (const group of el('spiceModules').querySelectorAll('[data-instance]')) {
      let found = false;
      for (const row of group.querySelectorAll('[data-module-device]')) {
        row.hidden = row.dataset.moduleDevice !== id;
        if (!row.hidden) found = true;
      }
      group.hidden = !found;
      if (found) selected = group;
    }
    el('spiceModules').hidden = !selected?.matches('[data-instance]');
    if (focus && selected) {
      selected.scrollIntoView({block:'nearest'});
      const target = [...selected.querySelectorAll('input,select,button')].find(n =>
        n.dataset.field === field || n.classList.contains(field.replaceAll('_','-') || 'no-field'));
      (target || selected.querySelector('input:not(:disabled),select:not(:disabled),button'))?.focus();
    }
  }
  function filter() {
    const text = search.value.toLowerCase().trim();
    const matches = devices.filter(d => [name(d),d.id,d.kind].join(' ').toLowerCase().includes(text));
    list.replaceChildren(...matches.map(d => new Option(name(d) + ' · ' + d.kind, name(d))));
    select(matches.some(d => name(d) === active) ? active : matches.length ? name(matches[0]) : '');
  }
  async function jump(issue) {
    if (issue.device) {
      const d = devices.find(d => name(d) === issue.device || d.id === issue.device);
      if (d) { search.value = ''; filter(); select(name(d), true, issue.field); return; }
    }
    const id = {ground:'spiceGround',transient:'spiceTransient',probes:'spiceProbes',
      closed_loop:'loopControl',drive:'spiceDeviceSearch',drawing:'spiceOverview'}[issue.target] || 'spiceDeviceSearch';
    let node = el(id);
    for (let parent = node; parent; parent = parent.parentElement) if (parent.tagName === 'DETAILS') parent.open = true;
    if (issue.target === 'closed_loop' && issue.field) {
      await initialBindings;
      if (!form.isConnected) return;
      const [, group, ...parts] = issue.field.split('.'), path = parts.join('.');
      // Controller output ids can contain dots, e.g. sv.da.dead_time refers to sv.da.
      const row = [...el('loopMap').querySelectorAll(group === 'inputs' ? '.loop-input' : '.loop-output')]
        .filter(r => path === r.dataset.id || path.startsWith(r.dataset.id + '.'))
        .sort((a,b) => b.dataset.id.length - a.dataset.id.length)[0];
      if (row) {
        const field = path.slice(row.dataset.id.length + 1).replaceAll('_','-');
        const controls = [...row.querySelectorAll('input:not(:disabled),select:not(:disabled)')].filter(n => n.getClientRects().length);
        node = controls.find(n => n.classList.contains('loop-' + field)) || controls[0] || node;
      }
    }
    node = node.querySelector('input, select') || node;
    if (node.disabled && id === 'loopControl') node = el('loopOn');
    node.scrollIntoView({block:'center'});
    if (id === 'spiceOverview') node.tabIndex = -1;
    node.focus();
  }
  form.jumpToIssue = jump;
  search.oninput = filter;
  list.onchange = () => select(list.value);
  form.addEventListener('invalid', event => {
    const row = event.target.closest('.spice-component');
    if (row?.hidden) jump({device:row.dataset.id, field:event.target.dataset.field});
  }, true);

  function quantity(value, unit) {
    const text = String(value).trim(), parts = /^(.*\d)\s*(meg|[fpnuµmkgt])$/i.exec(text);
    if (!parts) return text + ' ' + unit;
    const prefix = parts[2].toLowerCase();
    return parts[1] + ' ' + ({meg:'M',u:'µ',g:'G',t:'T'}[prefix] || prefix) + unit;
  }
  function showOperating(table) {
    const sim = table.simulation || {}, sources = [], loads = [], drives = new Map();
    const bindings = new Map();
    for (const output of Object.values(table.closed_loop?.outputs || {})) {
      for (const names of [output?.switches, output?.complement]) {
        if (Array.isArray(names)) names.forEach(id => bindings.set(id, output));
      }
    }
    for (const d of devices) {
      const id = name(d), cfg = sim.components?.[id] || d.values;
      if (!cfg) continue;
      if (cfg.wave?.kind === 'sine') sources.push(id + ' ' + quantity(cfg.wave.amplitude, d.kind === 'I' ? 'A' : 'V') + ' / ' + quantity(cfg.wave.frequency,'Hz'));
      else if (d.kind === 'V' && cfg.dc != null && cfg.dc !== '') sources.push(id + ' ' + quantity(cfg.dc,'V'));
      else if (d.kind === 'R' && cfg.value != null && cfg.value !== '') loads.push(id + ' ' + quantity(cfg.value,'Ω'));
      else if (d.kind === 'pmsm' && cfg.load_torque != null) loads.push(id + ' ' + quantity(cfg.load_torque,'N·m'));
      if (['nmos','pmos','igbt'].includes(d.kind) && (cfg.gate_drive || (d.pins?.g ? 'external' : 'internal')) !== 'external') {
        const output = bindings.get(id);
        let frequency = output?.frequency || cfg.frequency, f;
        if (output?.modulation === 'gate') f = tr('直接门极');
        else if (output?.modulation === 'vco') f = quantity(output.frequency_min,'Hz') + '–' + quantity(output.frequency_max,'Hz');
        else if (frequency) f = quantity(frequency,'Hz');
        else continue;
        if (!drives.has(f)) drives.set(f, []);
        drives.get(f).push(id);
      }
    }
    const operating = [...sources.slice(0,3), ...loads.slice(0,3), ...[...drives].slice(0,2).map(([f,ids]) =>
      ids[0] + (ids.length > 1 ? ' … ×' + ids.length : '') + ' ' + f)];
    if (sim.transient?.stop) operating.push(tr('结束时间') + ' ' + quantity(sim.transient.stop,'s'));
    el('spiceOperating').textContent = operating.join(' · ');
    const channels = Array.isArray(sim.probes) ? sim.probes.map(p => p?.name).filter(n => typeof n === 'string') : [];
    el('spiceChannels').textContent = tr('测量通道') + ' · ' + (channels.join('、') || tr('待填写'));
  }
  function paint(report) {
    el('spiceOverview').setAttribute('aria-busy','false');
    el('spiceReadyTitle').textContent = tr(report.ready ? '仿真就绪' : '仿真待补项') +
      (report.ready ? '' : ' (' + report.issues.length + ')');
    el('spiceIssues').replaceChildren(...report.issues.map(issue => {
      const button = document.createElement('button');
      button.type = 'button'; button.textContent = issue.message;
      button.dataset.target = issue.target; button.dataset.device = issue.device || '';
      button.onclick = () => jump(issue); return button;
    }));
    form.querySelector('[data-action="run"]').disabled = !available || !report.ready;
  }
  async function check(serial, table, draftIssues) {
    try {
      const report = await api('/api/simulation-readiness', {layout:table, profile});
      if (serial !== stamp || !form.isConnected) return;
      if (draftIssues.length) {
        report.ready = false;
        report.issues = report.issues.filter(i => !draftIssues.some(d => d.target === i.target)).concat(draftIssues);
      }
      paint(report);
    } catch (err) {
      if (serial === stamp && form.isConnected) paint({ready:false, issues:[{target:'drawing',message:err.message}]});
    }
  }
  function schedule(source) {
    const issues = [];
    try {
      const table = source || readSpice(issues);
      // Blurring a numeric field fires change again while the Run button is being pressed.
      const signature = JSON.stringify([table.simulation,table.closed_loop,table.modules,issues]);
      if (signature === lastDraft) return;
      lastDraft = signature;
      const serial = ++stamp;
      clearTimeout(timer);
      form.querySelector('[data-action="run"]').disabled = true;
      el('spiceOverview').setAttribute('aria-busy','true');
      el('spiceReadyTitle').textContent = tr('正在检查仿真参数…');
      showOperating(table);
      timer = setTimeout(() => { if (form.isConnected) check(serial, table, issues); }, source ? 0 : 160);
    } catch (err) {
      ++stamp; clearTimeout(timer); lastDraft = undefined;
      paint({ready:false, issues:[{target:'drawing',message:err.message}]});
    }
  }
  function edited(event) {
    if (![search, list, el('spiceDir')].includes(event.target)) schedule();
  }
  form.addEventListener('input', edited);
  form.addEventListener('change', edited);
  if (window.CircuitPanelLayout) window.CircuitPanelLayout.attachSpice(el('spiceListDivider'));
  filter(); schedule(base);
  return {refresh() { filter(); schedule(); }};
}
