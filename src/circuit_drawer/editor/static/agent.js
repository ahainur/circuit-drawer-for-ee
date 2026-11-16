/* Shared Agent cards. Mutation buttons follow allowed_actions from the task service. */
(() => {
  const resultPage = location.pathname === '/agent-results.html';
  if (location.search.includes('agent-preview')) { document.body.classList.add('agent-preview-page'); return; }
  const editor = window.CircuitAgentEditor;
  const language = () => window.cdI18n?.language || localStorage.getItem('circuit-drawer-language') || 'zh-CN';
  const t = (key, ...args) => {
    const values = window.CircuitAgentMessages?.['agent.' + key];
    let text = values?.[{'zh-CN': 0, en: 1, 'zh-TW': 2}[language()] ?? 0] || key;
    return text.replace(/\{(\d+)\}/g, (_, i) => args[i] ?? '');
  };
  window.CircuitAgentText = t;
  const escape = text => String(text ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  const canonical = value => JSON.stringify(value, (_, x) => x && typeof x === 'object' && !Array.isArray(x)
    ? Object.fromEntries(Object.keys(x).sort().map(k => [k, x[k]])) : x);
  const el = id => document.getElementById(id);
  let task = null, taskId = new URLSearchParams(location.search).get('task') || '', documentId = '', version = 1;
  let lastDocument = '', draft = '', startFrom = 'current', busy = false, polling = false, requestQueue = Promise.resolve();
  let nodes, params = {}, beforeAfter = 'overlay', previewKey = '', previewFrame, previewGeneration = 0, pendingAck = null;
  let renderKey = '', resultKey = '', lastAnnouncement = '', focusNext = false;
  let resultsTab = 'summary', waveRange = 'window';
  const allowed = name => !busy && (task?.allowed_actions || []).includes(name);
  const button = (action, primary = false, label = action) => `<button type="button" data-action="${escape(action)}" ${allowed(action) ? '' : 'disabled'} class="${primary ? 'primary' : ''}">${escape(t(label))}</button>`;
  const duration = ms => `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, '0')}`;
  const url = path => '/api/agent-file?' + new URLSearchParams({task: taskId, path});

  function context() {
    const value = editor.snapshot(), signature = canonical(value);
    if (lastDocument && signature !== lastDocument) ++version;
    lastDocument = signature;
    sessionStorage.setItem('agent-document-version', String(version));
    return {...value, document_id: documentId, version};
  }
  function saveAck(value) {
    pendingAck = value;
    if (value) sessionStorage.setItem('agent-pending-ack', JSON.stringify({taskId, ...value}));
    else sessionStorage.removeItem('agent-pending-ack');
  }
  function request(action, extra = {}, withContext = true) {
    const work = async () => {
      const response = await fetch('/api/agent', {method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({action, task_id: taskId || undefined, ...(withContext && !resultPage ? {context:context()} : {}), ...extra})});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || response.statusText);
      return result;
    };
    const pending = requestQueue.then(work); requestQueue = pending.catch(() => {}); return pending;
  }
  function notify(text) { const target = el('agentNotice'); if (target) target.textContent = text; }
  // One line per step: icon, title, state/time. Tool name and detail move out of the way.
  function stepHTML(s) {
    const active = ['running','waiting','failed'].includes(s.status);
    const state = s.status === 'todo' ? '' : s.status === 'done' ? (s.elapsed_ms ? duration(s.elapsed_ms) : t('step.done'))
      : t('step.' + s.status) + (s.elapsed_ms ? ' · ' + duration(s.elapsed_ms) : '');
    const tip = t('tool.' + s.tool) + (s.detail && !active ? ' · ' + s.detail : '');
    return `<div class="step ${escape(s.status)}" title="${escape(tip)}"><span class="step-icon" aria-hidden="true">${({done:'✓',running:'■',waiting:'!',failed:'×',cancelled:'–'})[s.status] || ''}</span>
      <span class="step-title">${s.index + 1}. ${escape(s.title)}</span><span class="step-state">${escape(state)}</span>
      ${active ? `<div class="step-extra">
      ${s.activity ? `<div>${escape(s.activity)}</div>` : ''}
      ${s.ai_round && s.status === 'running' ? `<div>${escape(t('generating_round', s.ai_round))}</div>` : ''}
      ${s.progress?.total > 0 ? `<progress value="${Number(s.progress.value)}" max="${Number(s.progress.total)}"></progress><span>${escape(s.progress.label)}</span>` : ''}
      ${s.detail ? `<details data-keep="step-detail-${s.index}"><summary>${escape(t('step_details'))}</summary><div class="muted">${escape(s.detail)}</div></details>` : ''}
      </div>` : ''}</div>`;
  }
  function stepsHTML(data, fold = true) {
    const steps = data.steps || [];
    const lead = steps.findIndex(s => s.status !== 'done');
    // Fold a long run of finished steps into one line so the current step stays in view.
    if (fold && lead === -1 && steps.length > 8) return `<details class="step-fold" data-keep="done-steps"><summary><span class="step-icon" aria-hidden="true">✓</span>${escape(t('steps_done', steps.length))}</summary>${steps.map(stepHTML).join('')}</details>`;
    if (!fold || lead < 3) return steps.map(stepHTML).join('');
    return `<details class="step-fold" data-keep="done-steps"><summary><span class="step-icon" aria-hidden="true">✓</span>${escape(t('steps_done', lead))}</summary>${steps.slice(0, lead).map(stepHTML).join('')}</details>`
      + steps.slice(lead).map(stepHTML).join('');
  }
  function progressTitle(data) {
    const steps = data.steps || [], done = steps.filter(s => s.status === 'done').length;
    return `${t('progress')} ${done}/${steps.length} · ${duration(data.elapsed_ms)}${data.waiting_ms >= 1000 ? ' · ' + t('waiting_time') + ' ' + duration(data.waiting_ms) : ''}`;
  }
  function compact(value) { return typeof value === 'string' ? value : JSON.stringify(value, null, 1); }
  function quantity(value, unit = '') {
    if (value == null) return '—';
    const prefixes = [[1e9,'G'],[1e6,'M'],[1e3,'k'],[1,''],[1e-3,'m'],[1e-6,'µ'],[1e-9,'n'],[1e-12,'p']];
    const scaled = ['V','A','H','F','Hz','s','Ω'].includes(unit) && value !== 0;
    const [factor, prefix] = scaled ? (prefixes.find(([scale]) => Math.abs(value) >= scale) || prefixes.at(-1)) : [1,''];
    return new Intl.NumberFormat(language(), {maximumSignificantDigits:unit === 's' ? 12 : 5}).format(value / factor) + (unit ? ' ' + prefix + unit : '');
  }
  function valueSummary(value) {
    if (value == null) return '—';
    if (typeof value !== 'object') return String(value);
    if (Array.isArray(value)) return value.map(v => v?.name || compact(v)).join(', ');
    return value.label || value.name || value.kind || compact(value);
  }
  // "新增 32 · 设置 3": only the kinds that actually occur, so a drawing candidate never reads as "settings only".
  function summaryText(changes) {
    const counts = {};
    for (const c of changes) counts[c.kind] = (counts[c.kind] || 0) + 1;
    return ['add','modify','remove','setting'].filter(k => counts[k]).map(k => `${t(k)} ${counts[k]}`).join(' · ') || t('no_changes');
  }
  const sectionName = section => { const key = 'section.' + section, text = t(key); return text === key ? section : text; };
  function changeValue(c) {
    const show = v => v != null && typeof v === 'object' && !Array.isArray(v) && !v.label && !v.name ? t('setting_changed') : valueSummary(v);
    return `<span class="change-value">${c.before != null ? `<span class="before">${escape(show(c.before))}</span> → ` : ''}${escape(show(c.after))}</span>`;
  }
  function controlSettings(change, path = [], before = change.before, after = change.after) {
    // Long descriptive metadata stays in the raw disclosure below the parameter table.
    if (path.at(-1) === 'note') return [];
    const object = v => v != null && typeof v === 'object' && !Array.isArray(v);
    if (object(before) || object(after)) {
      return [...new Set([...Object.keys(before || {}), ...Object.keys(after || {})])]
        .flatMap(key => controlSettings(change, [...path, key], before?.[key], after?.[key]));
    }
    if (canonical(before) === canonical(after)) return [];
    const group = ['blocks','tf','inputs','outputs'].includes(path[0]) ? path[1] : sectionName(change.section);
    const key = 'field.' + path.at(-1), translated = t(key);
    return [{...change, before, after, label: `${group} · ${translated === key ? path.slice(2).join('.') || path.join('.') : translated}`}];
  }
  function changesHTML(changes) {
    const objects = changes.filter(c => c.object_type !== 'sim_setting');
    const settings = changes.filter(c => c.object_type === 'sim_setting')
      .flatMap(c => ['code','circuit','loop'].includes(c.section) ? controlSettings(c) : [c]);
    const sections = [...new Set(objects.map(c => c.section))];
    // Drawing objects: one line per section with clickable names; wires and links only by count.
    const drawing = sections.map(section => {
      const list = objects.filter(c => c.section === section), counted = ['wires','links'].includes(section) || list.every(c => /^\d+$/.test(c.object_id));
      return `<div class="change-group"><div class="change-head"><strong>${escape(sectionName(section))}</strong><span class="muted">${escape(summaryText(list))}</span></div>
        ${counted ? '' : `<div class="chips">${list.map(c => `<button type="button" class="chip ${escape(c.kind)}" data-locate="${escape(c.object_type + ':' + c.object_id)}" title="${escape(t(c.kind) + ' · ' + t('locate'))}">${escape(c.object_id)}</button>`).join('')}</div>`}</div>`;
    }).join('');
    // Parameters: grouped by the part before " · " (device name), reason under the name.
    const groups = new Map();
    for (const c of settings) {
      const [head, ...rest] = String(c.label).split(' · ');
      const group = rest.length ? head : sectionName(c.section);
      if (!groups.has(group)) groups.set(group, []);
      groups.get(group).push({...c, name: rest.length ? rest.join(' · ') : sectionName(c.label)});
    }
    const table = settings.length ? `<div class="table-scroll changes"><table class="params"><tbody>${[...groups].map(([group, list]) => `<tr class="group"><th colspan="2">${escape(group)}</th></tr>${list.map(c => `<tr>
      <td>${escape(c.name)}${c.reason ? `<div class="reason">${escape(c.reason)}</div>` : ''}</td><td class="value">${changeValue(c)}</td></tr>`).join('')}`).join('')}</tbody></table></div>` : '';
    const raw = `<details class="raw" data-keep="raw-changes"><summary>${escape(t('raw_changes'))}</summary><pre class="log">${escape(compact(changes.map(c => ({kind:c.kind, object:c.label, before:c.before, after:c.after}))))}</pre></details>`;
    return drawing + table + raw;
  }
  function candidateHTML(candidate) {
    const pending = ['pending','stale'].includes(candidate.state);
    const facts = [t(candidate.validation.passed ? 'valid' : 'invalid'), summaryText(candidate.changes)];
    if (candidate.rework_count) facts.push(t('rework', candidate.rework_count));
    if (candidate.base_version !== task.drawing_version) facts.push(t('versions', candidate.base_version, task.drawing_version));
    const body = `<p class="facts">${facts.map(escape).join(' · ')}</p>
      ${candidate.state === 'stale' ? `<p role="alert">${escape(t('stale'))}</p>` : ''}
      ${candidate.validation.issues.length ? `<details><summary>${escape(t('issues'))}</summary><pre class="log">${escape(JSON.stringify(candidate.validation.issues, null, 2))}</pre></details>` : ''}
      ${changesHTML(candidate.changes)}
      ${candidate.assumptions?.length ? `<details><summary>${escape(t('assumptions'))}</summary>${candidate.assumptions.map(item => `<p>${escape(item)}</p>`).join('')}</details>` : ''}`;
    // Adopted, undone or discarded candidates shrink to their title line.
    return pending ? `<section class="card waiting" id="agentCandidate" tabindex="-1"><h3>${escape(t('candidate', candidate.number))} · ${escape(t('candidate.' + candidate.state))}</h3><div class="body">${body}</div></section>`
      : `<details class="card past" id="agentCandidate" tabindex="-1" data-keep="past-candidate"><summary>${escape(t('candidate', candidate.number))} · ${escape(t('candidate.' + candidate.state))} · ${escape(summaryText(candidate.changes))}</summary><div class="body">${body}</div></details>`;
  }
  function errorHTML(error) {
    return `<section class="card failed" tabindex="-1"><h3>${escape(t('error.' + error.code))}</h3><div class="body"><p>${escape(error.detail)}</p>
      <p>${escape(t('retained', task.drawing_version, task.steps.filter(s => s.status === 'done').length, task.runs.length))}</p>
      ${error.advice ? `<p>${escape(error.advice)}</p>` : ''}<details><summary>${escape(t('raw'))}</summary><pre class="log">${escape(error.raw)}</pre></details>
      <div class="row">${button('retry_step', true)}${['auth','quota','network','engine_missing'].includes(error.code) ? `<button data-settings>${escape(t('settings'))}</button>` : ''}${error.code === 'engine_missing' ? button('recheck_engine') : ''}${allowed('apply_fix') ? button('apply_fix') : ''}${allowed('skip_simulation') ? button('skip_simulation') : ''}</div></div></section>`;
  }
  function questionsHTML() {
    return `<section class="card waiting" id="agentQuestions"><h3>${escape(t('parameters'))}</h3><div class="body parameters">${task.questions.map(q => `<label>${escape(q.label)} ${escape(q.unit)}
      <input data-param="${escape(q.id)}" ${q.required === false ? '' : 'required'} value="${escape(params[q.id] ?? '')}" placeholder="${escape(q.suggested || '')}"><small>${escape(q.help || '')}</small></label>`).join('')}
      <div class="row">${button('submit_params', true)}${button('use_defaults')}</div></div></section>`;
  }
  function idleHTML() {
    const samples = editor.kind === 'control' ? ['example.control1','example.control2','example.control3'] : ['example1','example2','example3'];
    return `<p><strong>${escape(t('intro'))}</strong></p><p class="muted">${escape(t('intro_detail'))}</p>
      <section class="card"><h3>${escape(t('capabilities'))}</h3><div class="body">${(task?.capabilities || []).map(c => `<p>${c.state === 'available' ? '✓' : '□'} ${escape(t('tool.' + c.id))}${c.state === 'available' ? '' : ' · ' + escape(t('cap.' + c.reason))}</p>`).join('')}</div></section>
      <p>${escape(t('examples'))}</p>${samples.map(key => `<p><button class="example" data-example="${key}">${escape(t(key))}</button></p>`).join('')}`;
  }
  function render() {
    if (resultPage) { renderResultsPage(); return; }
    for (const [id, key] of Object.entries({agentNew:'new_task', agentHistory:'history', agentPresets:'presets', agentStartLabel:'start_from'})) {
      el(id).textContent = t(key);
    }
    el('agentStartFrom').options[0].textContent = t('current');
    el('agentStartFrom').options[1].textContent = t('blank');
    el('agentInput').placeholder = t('task_input');
    el('agentInput').setAttribute('aria-label', t('task_input'));
    el('agentClose').setAttribute('aria-label', t('close'));
    el('agentTaskTitle').textContent = taskId ? task.title : t('new_task');
    el('agentTaskTitle').title = el('agentTaskTitle').textContent;
    const key = canonical({status:task?.status, candidates:task?.candidates, questions:task?.questions, detail:task?.detail,
      steps:task?.steps, error:task?.error, retry:task?.retry_after_s, queued:task?.queued, requests:task?.requests, actions:task?.allowed_actions, history:task?.history, language:language(), busy});
    if (key !== renderKey) {
      renderKey = key;
      const body = el('agentBody'), scroll = body.scrollTop, opened = [...body.querySelectorAll('details[open][data-keep]')].map(d => d.dataset.keep);
      const focused = document.activeElement?.dataset.param;
      const expanded = el('agentTaskPrompt')?.dataset.task === taskId && !el('agentTaskPrompt').classList.contains('clamped');
      body.querySelectorAll('[data-param]').forEach(input => { params[input.dataset.param] = input.value; });
      // The request, follow-ups and AI notes share one clamped block; one toggle opens all of it.
      body.innerHTML = !taskId ? idleHTML() : `<div class="request${expanded ? '' : ' clamped'}" id="agentTaskPrompt" data-task="${escape(taskId)}"><p class="prompt">${escape(task.prompt)}</p>
        ${(task.requests || []).slice(1).map(r => `<p class="request-message"><b>${escape(t('request_followup'))}</b> ${escape(r.text)}</p>`).join('')}
        </div><button type="button" class="link" data-expand aria-expanded="${!!expanded}" aria-controls="agentTaskPrompt">${escape(t('expand'))}</button>
        ${task.detail ? `<p class="ai-note">${escape(task.detail)}</p>` : ''}
        ${task.status === 'planning' ? `<p role="status">${escape(t('planning'))}</p>` : ''}
        ${task.retry_after_s != null ? `<p role="status">${escape(t('retry_delay', task.retry_after_s, task.retry_count))}</p>` : ''}
        ${task.status === 'waiting_input' ? questionsHTML() : ''}
        ${task.steps.length ? `<section class="card steps"><h3>${escape(progressTitle(task))}</h3>${stepsHTML(task)}</section>` : ''}
        ${task.candidates.length ? candidateHTML(task.candidates.at(-1)) : ''}
        ${task.error ? errorHTML(task.error) : ''}
        ${task.status === 'cancelled' ? `<section class="card"><h3>${escape(t('cancelled'))}</h3><div class="body"><p>${escape(t('stopped_at', Math.min(task.current_step + 1, task.steps.length)))}</p>${button('resume_from_step', true)}</div></section>` : ''}
        ${task.status === 'done' ? `<section class="card done"><h3>${escape(t('done'))}</h3><div class="body">${task.runs.length ? `<button data-results>${escape(t('view_results'))}</button>` : ''}
          <p class="muted">${escape(t('followups'))}</p><ul class="followups">${task.followups.map(key => `<li><button type="button" class="link" data-example="${escape(key)}">${escape(t(key))}</button></li>`).join('')}</ul></div></section>` : ''}
        ${(task.queued || []).map(q => `<div class="card body"><span>${escape(t('queued'))} · ${escape(q.text)}</span><button data-withdraw="${escape(q.id)}" ${allowed('withdraw') ? '' : 'disabled'}>${escape(t('withdraw'))}</button></div>`).join('')}
        <details data-keep="log"><summary>${escape(t('log'))}</summary><pre class="log">${escape((task.log || []).join('\n'))}</pre></details>`;
      for (const name of opened) body.querySelector(`[data-keep="${CSS.escape(name)}"]`)?.setAttribute('open', '');
      body.scrollTop = scroll;
      if (focused) body.querySelector(`[data-param="${CSS.escape(focused)}"]`)?.focus();
      if (focusNext && ['waiting_input','waiting_candidate','failed'].includes(task?.status)) {
        body.querySelector('.waiting input,.waiting button,.failed button')?.focus(); focusNext = false;
      }
      body.querySelector('[data-expand]')?.addEventListener('click', event => {
        const clamped = el('agentTaskPrompt').classList.toggle('clamped');
        event.currentTarget.setAttribute('aria-expanded', String(!clamped));
      });
      body.querySelector('[data-results]')?.addEventListener('click', () => { el('agentResults').hidden = false; el('agentResults').focus(); });
      body.querySelectorAll('[data-example]').forEach(b => b.onclick = () => { el('agentInput').value = t(b.dataset.example); draftChanged(); el('agentInput').focus(); });
      body.querySelectorAll('[data-withdraw]').forEach(b => b.onclick = () => act('withdraw', {message_id:b.dataset.withdraw}));
      body.querySelectorAll('[data-locate]').forEach(b => b.onclick = () => locate(b.dataset.locate));
      body.querySelectorAll('[data-settings]').forEach(b => b.onclick = () => el('aiSettings').click());
      body.querySelectorAll('[data-param]').forEach(input => input.oninput = () => {
        params[input.dataset.param] = input.value;
        body.querySelector('[data-action="submit_params"]').disabled = !allowed('submit_params') || !validParams();
      });
      const submitParams = body.querySelector('[data-action="submit_params"]');
      if (submitParams) submitParams.disabled = !allowed('submit_params') || !validParams();
      wireActions(body);
    }
    el('agentSubmit').disabled = !allowed(taskId ? 'supplement' : 'start') || !el('agentInput').value.trim();
    el('agentSubmit').textContent = t(taskId ? 'send' : 'start');
    el('agentNew').disabled = el('agentHistory').disabled = busy;
    el('agentStartOptions').hidden = !!taskId;
    const actions = el('agentActions');
    actions.innerHTML = taskId ? ['adopt','discard','revise','regenerate','undo_candidate','undo_task_changes','readopt','stop']
      .filter(a => task.allowed_actions.includes(a) || a === 'adopt' && task.status === 'waiting_candidate')
      .map(a => button(a, a === 'adopt')).join('') : '';
    actions.hidden = !actions.innerHTML; wireActions(actions);
    const candidate = task?.candidates?.at(-1), waiting = task?.status === 'waiting_candidate';
    el('agentBanner').hidden = !waiting && !['running','planning'].includes(task?.status);
    el('agentBanner').innerHTML = waiting ? `<strong>${escape(t('candidate', candidate.number))} · ${escape(t('candidate.' + candidate.state))}</strong>
      <span class="row" ${candidate.changes.some(c => c.object_type !== 'sim_setting') ? '' : 'hidden'}>${['before','after','overlay'].map(mode => `<button data-preview-mode="${mode}" aria-pressed="${beforeAfter === mode}">${escape(t(mode))}</button>`).join('')}</span><span class="grow">${escape(summaryText(candidate.changes))}</span>${button('adopt', true)}${button('discard')}`
      : `<span class="grow">${escape(t('snapshot_run', task?.run_version || version))}</span>${button('stop')}`;
    wireActions(el('agentBanner'));
    // Wide windows: buttons already on the canvas banner are not repeated in the panel (narrow drawers keep them).
    const onBanner = new Set(el('agentBanner').hidden ? [] : [...el('agentBanner').querySelectorAll('[data-action]')].map(b => b.dataset.action));
    actions.querySelectorAll('[data-action]').forEach(b => b.classList.toggle('dup', onBanner.has(b.dataset.action)));
    actions.classList.toggle('only-dup', !actions.querySelector('[data-action]:not(.dup)'));
    el('agentBanner').querySelectorAll('[data-preview-mode]').forEach(b => b.onclick = () => { beforeAfter = b.dataset.previewMode; previewKey = ''; render(); });
    const announce = `${task?.status} ${candidate?.state} ${task?.current_step}`;
    if (announce !== lastAnnouncement) { el('agentLive').textContent = taskId ? t('state.' + task.status) : t('new_task'); lastAnnouncement = announce; }
    if (waiting && document.body.classList.contains('agent-open')) preview(candidate).catch(e => notify(e.message));
    else clearPreview();
    renderResults();
  }
  const validParams = () => task.questions.every(q => q.required === false || String(params[q.id] || '').trim());
  function wireActions(container) {
    container.querySelectorAll('[data-action]').forEach(b => b.onclick = () => act(b.dataset.action));
  }
  async function act(action, extra = {}) {
    if (!allowed(action)) return;
    if (action === 'submit_params' && !validParams()) return;
    if (action === 'revise' && !el('agentInput').value.trim()) { el('agentInput').focus(); return; }
    if (action === 'adopt' || action === 'readopt') clearPreview();
    busy = true; render(); notify('');
    try {
      const input = el('agentInput').value;
      let response = await request(action, {prompt:input, text:input, params, start_from:startFrom, ...extra});
      if (response.waveform_source) {
        if (context().version !== response.source_version) throw new Error(t('stale'));
        await window.openWaveforms({source:response.waveform_source, isCurrent:() => context().version === response.source_version});
      }
      if (response.command) {
        const current = context(), command = response.command;
        if (current.version !== command.version || current.file !== command.file || editor.busy()) {
          throw new Error(t('stale'));
        }
        await editor.apply(command);
        const applied = context();
        saveAck({token:command.token, applied});
        response = await request('ack', pendingAck);
        saveAck(null);
      }
      task = response; taskId = response.task_id || taskId;
      if (action === 'start') { params = {}; sessionStorage.setItem('agent-task-id', taskId); }
      if (['start','supplement','revise'].includes(action)) { el('agentInput').value = ''; draft = ''; }
      focusNext = ['start','submit_params','use_defaults','revise'].includes(action);
      renderKey = ''; previewKey = '';
    } catch (error) { notify(error.message); }
    finally { busy = false; render(); }
  }
  async function poll() {
    if (polling || busy || !resultPage && !editor.ready()) return;
    polling = true;
    try {
      if (pendingAck) { task = await request('ack', pendingAck); saveAck(null); }
      const expectedTask = taskId, response = await request('status', taskId ? {} : {prompt:draft});
      if (expectedTask !== taskId) return;
      task = response;
      render();
    } catch (error) { notify(error.message); }
    finally { polling = false; }
  }
  function draftChanged() { draft = el('agentInput').value; poll(); }
  function clearPreview() {
    ++previewGeneration; previewKey = '';
    el('agentPreview')?.remove();
  }
  async function frameReady() {
    if (!previewFrame) {
      previewFrame = document.createElement('iframe'); previewFrame.className = 'agent-preview-frame';
      previewFrame.src = '/?agent-preview=1'; previewFrame.title = t('candidate_preview'); previewFrame.tabIndex = -1;
      document.body.append(previewFrame);
    }
    const box = nodes.canvas.getBoundingClientRect(); previewFrame.style.width = box.width + 'px'; previewFrame.style.height = box.height + 'px';
    const start = performance.now();
    while (!previewFrame.contentWindow?.CircuitAgentEditor?.ready()) {
      if (performance.now() - start > 15000) throw new Error(t('preview_failed'));
      await new Promise(resolve => setTimeout(resolve, 40));
    }
    return previewFrame.contentWindow.CircuitAgentEditor;
  }
  async function preview(candidate) {
    const key = candidate.candidate_id + ':' + beforeAfter + ':' + nodes.canvas.clientWidth + ':' + nodes.canvas.clientHeight;
    if (key === previewKey) return;
    previewKey = key;
    const stamp = ++previewGeneration, bridge = await frameReady();
    const picture = await bridge.renderPreview(beforeAfter === 'before' ? candidate.before_table : candidate.preview, editor.snapshot().profile);
    const removed = beforeAfter === 'overlay' && candidate.changes.some(c => c.kind === 'remove')
      ? await bridge.renderPreview(candidate.before_table, editor.snapshot().profile, picture.view) : null;
    if (stamp !== previewGeneration) return;
    let layer = el('agentPreview');
    if (!layer) { layer = document.createElement('div'); layer.id = 'agentPreview'; layer.className = 'agent'; nodes.canvas.append(layer); }
    layer.innerHTML = `<div class="agent-preview-scene">${picture.markup}</div>`;
    const scene = layer.firstElementChild;
    if (beforeAfter === 'overlay') for (const change of candidate.changes) {
      const key = change.object_type + ':' + change.object_id, box = (change.kind === 'remove' ? removed?.bounds : picture.bounds)?.[key];
      if (!box) continue;
      const mark = document.createElement('button'); mark.className = 'agent-mark ' + change.kind;
      mark.dataset.agentObject = key; mark.title = `${t(change.kind)} ${change.label}`; mark.setAttribute('aria-label', mark.title);
      mark.style.cssText = `left:${box[0]-4}px;top:${box[1]-4}px;width:${Math.max(10,box[2]+8)}px;height:${Math.max(10,box[3]+8)}px`;
      if (change.kind === 'remove') mark.textContent = '× ' + change.label;
      mark.onclick = () => { notify(`${change.label} · ${compact(change.kind === 'remove' ? change.before : change.after)}`); mark.focus(); };
      scene.append(mark);
    }
  }
  async function locate(key) {
    if (beforeAfter !== 'overlay') {
      beforeAfter = 'overlay'; previewKey = '';
      await preview(task.candidates.at(-1)); render();
    }
    const mark = [...(el('agentPreview')?.querySelectorAll('[data-agent-object]') || [])].find(n => n.dataset.agentObject === key);
    if (!mark) return;
    const layer = el('agentPreview'), scene = mark.parentElement;
    const scale = Math.min(3, layer.clientWidth * .6 / mark.offsetWidth, layer.clientHeight * .6 / mark.offsetHeight);
    const x = layer.clientWidth / 2 - (mark.offsetLeft + mark.offsetWidth / 2) * scale;
    const y = layer.clientHeight / 2 - (mark.offsetTop + mark.offsetHeight / 2) * scale;
    scene.style.transform = `translate(${x}px,${y}px) scale(${scale})`;
    mark.click(); mark.focus({preventScroll:true});
  }
  function waveSVG(wave) {
    const windowed = waveRange === 'window' && wave.window_samples?.length > 1;
    const points = (windowed ? wave.window_samples : wave.samples) || [];
    if (points.length < 2) return `<p>${escape(t('no_waveforms'))}</p>`;
    const ys = points.map(p => p[1]), min = Math.min(...ys), max = Math.max(...ys), span = max - min || Math.abs(max) * .1 || 1;
    const [a,b] = windowed ? wave.window : wave.t_range, x = t => 70 + (t-a)/(b-a)*480, y = v => 140 - (v-min)/span*110;
    return `<svg class="wave" viewBox="0 0 570 175" role="img" aria-label="${escape(wave.label)}">
      <rect width="570" height="175" fill="white"/><rect x="${x(wave.window[0])}" y="20" width="${x(wave.window[1])-x(wave.window[0])}" height="120" fill="#e8eaec"/>
      <path d="M70 20V140H550" fill="none" stroke="#999"/>
      <polyline points="${points.map(p => `${x(p[0]).toFixed(2)},${y(p[1]).toFixed(2)}`).join(' ')}" fill="none" stroke="#4a5f74" stroke-width="1.3"/>
      <text x="4" y="22">${escape(quantity(max))}</text><text x="4" y="142">${escape(quantity(min))}</text>
      <text x="70" y="160">${escape(quantity(a, 's'))}</text><text x="460" y="160">${escape(quantity(b, 's'))}</text>
      <text x="75" y="14">${escape(wave.label)} / ${escape(wave.unit)}</text></svg>`;
  }
  function modeHTML(run) {
    if (!run.modes.length) return `<p>${escape(t('no_modes'))}</p>`;
    const intervals = run.modes.flatMap(m => m.intervals.map(i => ({...i, mode:m.index})));
    const begin = Math.min(...intervals.map(i => i.begin)), end = Math.max(...intervals.map(i => i.end));
    const timeline = `<svg class="mode-timeline" viewBox="0 0 600 45" role="img" aria-label="${escape(t('modes'))}">${intervals.map(i => {
      const x = 600 * (i.begin - begin) / (end - begin), width = 600 * (i.end - i.begin) / (end - begin);
      return `<rect x="${x}" y="2" width="${width}" height="22" fill="${['#d7dfd1','#d8dfe6','#f0e5a6'][i.mode % 3]}" stroke="#888"/><text x="${x + width / 2}" y="18" text-anchor="middle">${width > 20 ? i.mode + 1 : ''}</text>`;
    }).join('')}<text x="0" y="42">${escape(quantity(begin, 's'))}</text><text x="600" y="42" text-anchor="end">${escape(quantity(end, 's'))}</text></svg>`;
    return timeline + run.modes.map(m => `<figure style="margin:8px 0">${m.figure_ref?.endsWith('.svg') ? `<img class="mode-figure" alt="${escape(m.name)}" src="${escape(url(m.figure_ref))}">` : ''}<figcaption>${escape(m.name)} · ${escape(m.intervals.map(i => `${quantity(i.begin, 's')}–${quantity(i.end, 's')}`).join(', '))} · ${escape(m.conducting.join(', '))}</figcaption></figure>`).join('');
  }
  function resultsHTML() {
    const runs = [...(task?.runs || [])].reverse().filter(run =>
      resultsTab === 'modes' ? run.modes.length || run.mode_analysis :
      resultsTab === 'waveforms' ? run.waveforms.length || run.loop : true);
    if (resultsTab === 'summary') runs.sort((a, b) => Number(!!b.metrics.length) - Number(!!a.metrics.length));
    if (!runs.length) return `<p class="body">${escape(t('no_results'))}</p>`;
    if (resultsTab === 'log') return `<a href="${escape(url(task.record))}" target="_blank" rel="noopener">${escape(t('run_record'))}</a><pre class="log">${escape(task.log.join('\n'))}</pre>`;
    return runs.map(run => `<section class="card"><h3 title="${escape(run.run_id + ' · ' + run.finished_at)}">${escape(runTitle(run))}</h3>
      <div class="results-grid ${resultsTab === 'summary' && (run.waveforms.length || run.loop) ? '' : 'single'}"><div ${['summary','waveforms'].includes(resultsTab) && (run.waveforms.length || run.loop) ? '' : 'hidden'}>${run.waveforms.length ? run.waveforms.map(w => `<figure style="margin:0 0 8px">${waveSVG(w)}<figcaption>${escape(w.signal)} · ${escape(t('sampling_label'))} ${escape(quantity(w.window[0], 's'))} – ${escape(quantity(w.window[1], 's'))}</figcaption></figure>`).join('') : run.engine.name === 'Circuit Drawer' ? run.files.filter(f => f.format === 'svg').map(f => `<img class="mode-figure" alt="Bode" src="${escape(url(f.path))}">`).join('') : `<p>${escape(t('no_waveforms'))}</p>`}
      ${!resultPage && run.waveforms.length ? `<button data-waveform="${escape(run.run_id)}" ${allowed('attach_waveform') && run.drawing_version === task.drawing_version ? '' : 'disabled'}>${escape(t('attach_waveform'))}</button>` : ''}</div>
      <div ${resultsTab === 'waveforms' ? 'hidden' : ''}><section ${resultsTab === 'summary' ? '' : 'hidden'}>${assessmentHTML(run)}${run.metrics.length ? `<div class="table-scroll"><table><thead><tr><th>${escape(t('metric'))}</th><th>${escape(t('value'))}</th><th>${escape(t('sampling_label'))}</th></tr></thead><tbody>${run.metrics.map(m => `<tr><td>${escape(m.signal)} · ${escape(t('metric.' + m.name))}</td><td>${escape(quantity(m.value, m.unit))}</td><td>${m.window ? `${escape(quantity(m.window.from, m.window.unit))} – ${escape(quantity(m.window.to, m.window.unit))}` : '—'}</td></tr>`).join('')}</tbody></table></div>${run.waveforms.length ? `<p class="muted">${escape(t('sample_window'))}</p>` : ''}` : `<p>${escape(t('no_metrics'))}</p>`}</section>
      <section ${['summary','files'].includes(resultsTab) ? '' : 'hidden'}><h3>${escape(t('files'))}</h3>${run.files.map(f => `<p>${f.exists ? `<a ${f.format === 'csv' ? 'download' : 'target="_blank" rel="noopener"'} href="${escape(url(f.path))}">${escape(f.path.split(/[\\/]/).at(-1))}</a>` : `${escape(f.path.split(/[\\/]/).at(-1))} · ${escape(t('file_missing'))}`} <span class="muted">${escape(String(f.format).toUpperCase())}</span></p>`).join('')}</section>
      <section ${resultsTab === 'modes' ? '' : 'hidden'}><h3>${escape(t('modes'))}</h3>${modeHTML(run)}</section>
      ${(run.notes || []).some(Boolean) ? `<details data-result-keep="notes-${escape(run.run_id)}"><summary>${escape(t('model_notes'))}</summary>${run.notes.map(n => `<p class="muted">${escape(n)}</p>`).join('')}</details>` : ''}
      </div></div></section>`).join('');
  }
  function runTitle(run) {
    const engine = String(run.engine.version || '').split(' :')[0] || run.engine.name;
    const time = run.finished_at ? new Date(run.finished_at) : null;
    const clock = time && !isNaN(time) ? time.toLocaleTimeString(language(), {hour:'2-digit', minute:'2-digit'}) : '';
    return [engine.toLowerCase().includes(String(run.engine.name).toLowerCase()) ? engine : `${run.engine.name} ${engine}`.trim(),
      t('drawing_version', run.drawing_version), run.duration_ms ? duration(run.duration_ms) : '', clock,
      run.exported ? t('model_exported') : ''].filter(Boolean).join(' · ');
  }
  function assessmentHTML(run) {
    const assessment = run.assessment;
    if (!assessment) return '';
    return `<div class="assessment">${assessment.targets.map(item => `<p><strong>${escape(t('target_comparison', quantity(item.measured, item.unit), quantity(item.target, item.unit), quantity(item.error_pct, '%')))}</strong></p>`).join('')}
      ${assessment.periodicity.length ? `<details data-result-keep="periodicity-${escape(run.run_id)}"><summary>${escape(periodicitySummary(assessment.periodicity))}</summary>${assessment.periodicity.map(item => `<p title="${escape(item.reason ? t('periodicity.' + item.reason, item.cycles) :
          t('periodicity_evidence', item.cycles, quantity(item.drift, item.unit), quantity(item.tolerance, item.unit)))}"><span class="mark ${escape(item.state)}">${item.state === 'repeatable' ? '✓' : '!'}</span> ${escape(item.signal)} · ${escape(t('periodicity.' + item.state))}</p>`).join('')}</details>` : ''}</div>`;
  }
  function periodicitySummary(items) {
    const ok = items.filter(i => i.state === 'repeatable').length;
    const bad = items.filter(i => i.state === 'changing').map(i => i.signal);
    const unverified = items.length - ok - bad.length;
    if (unverified === items.length) return t('periodicity.unavailable');
    return t('periodicity_summary', ok, ok + bad.length) +
      (bad.length ? ' · ' + t('periodicity_check', bad.slice(0, 2).join(', ') + (bad.length > 2 ? '…' : '')) : '') +
      (unverified ? ' · ' + t('periodicity_unavailable_count', unverified) : '');
  }
  function renderResults() {
    const key = canonical({runs:task?.runs, actions:task?.allowed_actions, version:task?.drawing_version, tab:resultsTab, language:language()});
    if (key === resultKey) return;
    resultKey = key;
    const results = el('agentResults');
    results.hidden = !(task?.runs?.length);
    results.innerHTML = `<div class="row header">${resultsTabs()}<span class="grow"></span><a target="_blank" rel="noopener" href="/agent-results.html?task=${encodeURIComponent(taskId)}">${escape(t('new_tab'))}</a><button data-hide-results aria-label="${escape(t('close'))}">×</button></div>${resultsHTML()}`;
    results.querySelector('[data-hide-results]').onclick = () => { results.hidden = true; };
    wireResults(results);
  }
  function resultsTabs() {
    return ['summary','waveforms','modes','files', ...(resultPage ? ['log'] : [])].map(tab => `<button data-result-tab="${tab}" aria-pressed="${resultsTab === tab}">${escape(t(tab === 'summary' ? 'results' : tab))}</button>`).join('') +
      (['summary','waveforms'].includes(resultsTab) ? `<span class="wave-range">${['window','full'].map(range => `<button data-wave-range="${range}" aria-pressed="${waveRange === range}">${escape(t('range.' + range))}</button>`).join('')}</span>` : '');
  }
  function wireResults(root) {
    root.querySelectorAll('[data-result-tab]').forEach(b => b.onclick = () => { resultsTab = b.dataset.resultTab; resultKey = ''; resultPage ? renderResultsPage() : renderResults(); });
    root.querySelectorAll('[data-waveform]').forEach(b => b.onclick = () => act('attach_waveform', {run_id:b.dataset.waveform}));
    root.querySelectorAll('[data-wave-range]').forEach(b => b.onclick = () => { waveRange = b.dataset.waveRange; resultKey = ''; resultPage ? renderResultsPage() : renderResults(); });
  }
  function renderResultsPage() {
    if (!task) return;
    const key = canonical({runs:task.runs, status:task.status, language:language()});
    if (key === resultKey) return;
    resultKey = key;
    el('agentResultSteps').innerHTML = `<h2>${escape(task.title)}</h2>${(task.requests || [{text:task.prompt}]).map(r => `<p>${escape(r.text)}</p>`).join('')}${stepsHTML(task, false)}<p>${escape(t('state.' + task.status))}</p><a href="${escape(url(task.record))}" target="_blank" rel="noopener">${escape(t('run_record'))}</a>`;
    el('agentResultContent').innerHTML = `<h2>${escape(t('results'))}</h2><div class="row">${resultsTabs()}</div>${resultsHTML()}`;
    wireResults(el('agentResultContent'));
  }
  async function init() {
    if (resultPage) { await poll(); setInterval(poll, 1500); return; }
    while (!editor?.ready()) await new Promise(resolve => setTimeout(resolve, 50));
    documentId = sessionStorage.getItem('agent-document-id') || crypto.randomUUID();
    sessionStorage.setItem('agent-document-id', documentId);
    version = (Number(sessionStorage.getItem('agent-document-version')) || 0) + 1;
    taskId = sessionStorage.getItem('agent-task-id') || '';
    const savedAck = JSON.parse(sessionStorage.getItem('agent-pending-ack') || 'null');
    if (savedAck?.taskId === taskId) pendingAck = {token:savedAck.token, applied:savedAck.applied};
    nodes = editor.mount();
    nodes.panel.innerHTML = `<div class="header row"><strong id="agentTaskTitle" class="grow"></strong><button id="agentNew">${escape(t('new_task'))}</button><button id="agentHistory">${escape(t('history'))}</button><button id="agentClose" aria-label="${escape(t('close'))}">×</button></div>
      <div id="agentBody"></div><div id="agentNotice" role="status"></div><div id="agentActions" class="row actions"></div>
      <div class="composer"><div id="agentStartOptions" class="row"><label><span id="agentStartLabel">${escape(t('start_from'))}</span><select id="agentStartFrom"><option value="current">${escape(t('current'))}</option><option value="blank">${escape(t('blank'))}</option></select></label><button id="agentPresets">${escape(t('presets'))}</button></div>
      <textarea id="agentInput" aria-label="${escape(t('task_input'))}" placeholder="${escape(t('task_input'))}" rows="3"></textarea><div class="row"><span class="grow"></span><button id="agentSubmit" class="primary" disabled>${escape(t('start'))}</button></div></div><div id="agentLive" class="sr-only" aria-live="polite"></div>`;
    const banner = document.createElement('div'); banner.id = 'agentBanner'; banner.className = 'agent row'; banner.hidden = true; nodes.column.prepend(banner);
    const results = document.createElement('section'); results.id = 'agentResults'; results.className = 'agent'; results.tabIndex = 0; results.hidden = true; nodes.column.append(results);
    el('agentInput').oninput = draftChanged;
    el('agentStartFrom').onchange = e => {
      startFrom = e.target.value;
      nodes.panel.querySelector('fieldset[data-prompt-library]')?.remove();
      delete el('agentInput').dataset.promptLibrary;
    };
    el('agentSubmit').onclick = () => act(taskId ? 'supplement' : 'start');
    el('agentClose').onclick = () => { editor.close(); clearPreview(); };
    el('agentNew').onclick = async () => {
      if (task && ['running','planning'].includes(task.status)) { notify(t('stop_first')); return; }
      taskId = ''; params = {}; sessionStorage.removeItem('agent-task-id'); renderKey = ''; clearPreview(); await poll();
    };
    el('agentHistory').onclick = async () => {
      const data = await request('status', {task_id:undefined, prompt:draft});
      const select = document.createElement('select'); select.setAttribute('aria-label', t('history'));
      select.innerHTML = `<option value="">${escape(t('history'))}</option>` + data.history.map(h => `<option value="${escape(h.task_id)}">${escape(h.title)} · ${escape(t('state.' + h.status))}</option>`).join('');
      select.onchange = () => { if (select.value) { taskId = select.value; sessionStorage.setItem('agent-task-id', taskId); renderKey = ''; poll(); } select.remove(); };
      el('agentHistory').after(select); select.focus();
    };
    el('agentPresets').onclick = async () => {
      if (!window.CircuitPromptLibrary) {
        try {
          await new Promise((resolve, reject) => { const script = document.createElement('script'); script.src = '/prompt-library.js'; script.onload = resolve; script.onerror = reject; document.head.append(script); });
        } catch { notify(t('presets_pending')); return; }
      }
      window.CircuitPromptLibrary.mount({textarea:el('agentInput'), domain:editor.kind, mode:startFrom === 'blank' ? 'create' : 'edit', table:() => editor.snapshot().table, api, busy:() => busy});
    };
    document.addEventListener('keydown', e => {
      const key = e.key.toLowerCase(), mod = e.ctrlKey || e.metaKey;
      if (mod && e.shiftKey && key === 'a') { e.preventDefault(); editor.open(); el('agentInput').focus(); }
      else if (mod && key === '.') { e.preventDefault(); act('stop'); }
      else if (e.key === 'Escape' && nodes.panel.contains(document.activeElement)) { e.preventDefault(); e.stopImmediatePropagation(); editor.close(); clearPreview(); }
      else if (e.key === 'F6' && document.body.classList.contains('agent-open')) {
        e.preventDefault(); const targets = [el('canvas'), el('agentInput'), el('agentResults')].filter(n => !n.hidden);
        const index = targets.findIndex(n => n === document.activeElement || n.contains(document.activeElement));
        targets[(index + 1) % targets.length].setAttribute('tabindex', '0'); targets[(index + 1) % targets.length].focus();
      } else if (e.key === 'Enter' && mod && el('agentCandidate')?.contains(document.activeElement)) { e.preventDefault(); act('adopt'); }
      else if (e.key === 'Enter' && !e.shiftKey && document.activeElement === el('agentInput')) { e.preventDefault(); el('agentSubmit').click(); }
    }, true);
    document.addEventListener('editor-language-change', () => { renderKey = ''; resultKey = ''; render(); });
    window.addEventListener('circuit-drawer:layout-changed', () => { if (editor.ready()) context(); });
    window.addEventListener('circuit-drawer:agent-visibility', () => {
      if (nodes.panel.hidden) clearPreview(); else render();
    });
    new ResizeObserver(() => { if (task?.status === 'waiting_candidate') { previewKey = ''; render(); } }).observe(nodes.canvas);
    await poll(); setInterval(poll, 800);
  }
  init().catch(e => notify(e.message));
})();
