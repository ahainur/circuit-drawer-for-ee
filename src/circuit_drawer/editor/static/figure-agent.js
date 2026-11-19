/* Figure tasks use the shared document version, request queue and adoption adapter. */
(() => {
  if (!window.CircuitFigure || location.search.includes('agent-preview')) return;
  const el = (id) => document.getElementById(id);
  const tr = (text) => window.cdI18n?.translate(text) || text;
  const esc = (text) =>
    String(text ?? '').replace(
      /[&<>"']/g,
      (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
    );
  const session = () => window.CircuitAgentSession;
  const active = () => document.body.classList.contains('figure-open');
  const duration = (ms) =>
    `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, '0')}`;
  let task = null,
    taskId = sessionStorage.getItem('figure-task-id') || '',
    selected = 'A',
    mode = 'side';
  let busy = false,
    polling = false,
    renderKey = '',
    compareKey = '',
    generation = 0,
    comparing = false;
  let pair = null,
    previewQueue = Promise.resolve(),
    pendingAck = JSON.parse(sessionStorage.getItem('figure-pending-ack') || 'null');
  let barKey = '';
  let draftScope = null;
  const scopeLabel = document.createElement('p');
  scopeLabel.id = 'figureAgentScope';
  el('figureInput').before(scopeLabel);
  const cache = new Map();
  const group = () => task?.candidates?.at(-1);
  const variant = () =>
    group()?.variants.find((v) => v.variant_id === selected) || group()?.variants[0];
  const allowed = (action) => !busy && (task?.allowed_actions || []).includes(action);
  const labels = {
    start: '发送',
    regenerate: '按当前图重新生成',
    revise: '让 AI 再改',
    retry_appearance_only: '只保留外观改动，重新生成',
    discard: '放弃',
    back_to_drawing: '返回绘图',
    stop: '停止',
    retry_step: '重试',
    undo_candidate: '撤销采用',
    confirm_export: '检查后导出',
  };
  const btn = (action, text = labels[action]) =>
    `<button type="button" data-figure-action="${action}" ${allowed(action) ? '' : 'disabled'}>${esc(tr(text))}</button>`;
  const notices = {
    figure_electrical_change: '候选改动了电气连接',
    figure_parameter_change: '候选改动了器件参数',
    figure_binding_change: '候选改动了控制绑定或器件 ID',
    figure_field_change: '候选包含外观范围之外的字段',
    figure_invalid: '候选数据未通过检查',
  };
  const header = document.createElement('div');
  header.className = 'figure-agent-header';
  header.innerHTML =
    '<strong></strong><button id="figureAgentNew"></button><button id="figureAgentHistory"></button>';
  el('figureAgentContent').before(header);
  const notice = document.createElement('p');
  notice.id = 'figureAgentNotice';
  notice.setAttribute('role', 'status');
  el('figureAgentActions').after(notice);
  const bar = document.createElement('div');
  bar.id = 'figureCandidateBar';
  bar.hidden = true;
  const compare = document.createElement('section');
  compare.id = 'figureCompare';
  compare.hidden = true;
  compare.setAttribute('aria-label', tr('候选对比'));
  el('figureViewport').prepend(bar, compare);

  function say(message) {
    notice.textContent = tr(message);
  }
  function keepAck(value) {
    pendingAck = value;
    if (value) sessionStorage.setItem('figure-pending-ack', JSON.stringify(value));
    else sessionStorage.removeItem('figure-pending-ack');
  }
  async function request(action, extra = {}) {
    return session().request(action, {
      task_id: taskId || undefined,
      task_kind: 'figure',
      ...extra,
    });
  }
  async function poll() {
    if (!session() || polling || busy || !active()) return;
    polling = true;
    try {
      if (pendingAck) {
        task = await request('ack', {
          task_id: pendingAck.taskId,
          token: pendingAck.token,
          applied: pendingAck.applied,
        });
        keepAck(null);
      } else {
        const expected = taskId;
        const response = await request('status', { prompt: el('figureInput').value });
        if (expected !== taskId) return;
        task = response;
      }
      render();
    } catch (error) {
      say(error.message);
    } finally {
      polling = false;
    }
  }
  async function act(action, exportOptions = null) {
    if (!allowed(action) || pendingAck) {
      if (exportOptions) throw new Error(tr('任务状态已改变，请重新打开导出检查'));
      return;
    }
    if (action === 'confirm_export' && !exportOptions) {
      window.CircuitFigureOutput.openChecks(null, {
        formats: task.export_check.formats,
        onExport: options => act('confirm_export', options),
      });
      return;
    }
    const chosen = variant(),
      currentGroup = group();
    if (['adopt', 'readopt'].includes(action) && !chosen?.allowed_actions.includes(action)) return;
    busy = true;
    renderKey = '';
    render();
    say('');
    try {
      let data = await request(action, {
        prompt: el('figureInput').value,
        text: el('figureInput').value,
        group_id: currentGroup?.group_id,
        variant_id: chosen?.variant_id,
        ...(action === 'start' && draftScope),
        ...exportOptions,
      });
      if (action === 'start') {
        draftScope = null;
        taskId = data.task_id;
        sessionStorage.setItem('figure-task-id', taskId);
        selected = 'A';
        const library = el('figureAgent').querySelector('[data-prompt-library]');
        if (library && !library.querySelector('[data-content]').hidden)
          library.querySelector('[data-toggle]').click();
      }
      if (data.command) {
        const applied = await session().applyCommand(data.command);
        keepAck({ taskId, token: data.command.token, applied });
        data = await request('ack', { token: data.command.token, applied });
        keepAck(null);
        comparing = false;
        compareKey = '';
        ++generation;
      }
      if (['start', 'revise', 'regenerate', 'retry_appearance_only', 'supplement'].includes(action))
        el('figureInput').value = '';
      if (action === 'back_to_drawing') window.CircuitFigure.leave();
      if (action === 'discard') {
        comparing = false;
        compareKey = '';
        ++generation;
      }
      task = data;
      renderKey = '';
    } catch (error) {
      say(error.message);
      if (exportOptions) throw error;
    } finally {
      busy = false;
      render();
    }
  }
  function diffHTML(chosen) {
    const rows = chosen.diff || [];
    return `<div class="figure-blocked"><strong>${esc(tr('此方案已拦下'))}</strong>
      ${(chosen.errors || []).map((error) => `<p>${esc(tr(error.code === 'figure_invalid' ? error.message : notices[error.code] || error.message))}</p>`).join('')}
      ${
        rows.length
          ? `<table><thead><tr><th>${esc(tr('连接点'))}</th><th>${esc(tr('改前'))}</th><th>${esc(tr('候选'))}</th></tr></thead><tbody>
      ${rows.map((row) => `<tr><th>${esc(row.point)}</th><td>${esc(row.before)}</td><td>${esc(row.after)}</td></tr>`).join('')}</tbody></table>`
          : ''
      }
      <button data-figure-highlight>${esc(tr('在图上看这处'))}</button></div>`;
  }
  function changeSummary(v) {
    const groups = new Map();
    for (const change of v.changes || []) {
      const path = change.path;
      const key =
        path.includes('label_offset') || path.includes('label_pos')
          ? '标签位置'
          : path.includes('label')
            ? '标签文字'
            : path.includes('regions')
              ? '色块'
              : path.includes('wires') || path.includes('hops')
                ? '导线'
                : path.includes('callouts')
                  ? '说明箭头'
                  : '外观字段';
      groups.set(key, (groups.get(key) || 0) + 1);
    }
    if (Object.keys(v.displays || {}).length)
      groups.set('模块显示', Object.keys(v.displays).length);
    return [...groups]
      .map(([label, count]) => `<span class="figure-change">${esc(tr(label))} ${count}</span>`)
      .join('');
  }
  function guardsHTML(v) {
    return Object.entries({
      electrical: '电气连接一致',
      parameters: '器件参数一致',
      bindings: '控制绑定一致',
      mono: '黑白可辨',
    })
      .map(
        ([key, label]) =>
          `<span class="${v.guard[key] === false ? (key === 'mono' ? 'figure-advice' : 'bad') : ''}">${v.guard[key] === null ? '?' : v.guard[key] ? '✓' : '!'} ${esc(tr(label))}</span>`,
      )
      .join('');
  }
  function sizeText(size) {
    return size ? size.map((n) => Number(n).toFixed(2)).join(' × ') + ' cm' : '';
  }
  function cardsHTML() {
    const g = group();
    if (!g) return '';
    return `<section class="figure-variants"><h4>${esc(tr('候选'))} #${g.number} · ${esc(tr({ pending: '待采用', adopted: '已采用', undone: '已撤销', stale: '基于旧版本', discarded: '已放弃' }[g.state]))}</h4>
      ${g.variants
        .map(
          (
            v,
          ) => `<article class="figure-variant ${v.passed ? '' : 'blocked'}" data-variant-card="${v.variant_id}">
        <button class="figure-variant-select" data-figure-variant="${v.variant_id}" aria-pressed="${v.variant_id === selected}">${esc(tr('方案'))} ${v.variant_id} · ${esc(v.title)}</button>
        <img class="figure-thumb" data-figure-thumb="${v.variant_id}" alt="${esc(tr('方案'))} ${v.variant_id}" hidden>
        <div>${changeSummary(v)}</div><p>${sizeText(v.size_cm)}</p><div class="figure-guards">${guardsHTML(v)}</div><p>${esc(v.note)}</p>
        ${v.matches_current ? `<p>${esc(tr('与当前图纸相同'))}</p>` : ''}
        ${!v.passed && v.variant_id === selected ? diffHTML(v) : ''}
      </article>`,
        )
        .join('')}</section>`;
  }
  function render() {
    const targets = draftScope?.selection || task?.selection || [];
    scopeLabel.hidden = !targets.length;
    scopeLabel.textContent = targets.length ? tr('仅修改选中对象') + ' · ' + targets.join('、') : '';
    if (!task) return;
    const g = group(),
      v = variant();
    if (v) selected = v.variant_id;
    const lang = window.cdI18n?.language;
    const key = JSON.stringify([{ ...task, elapsed_ms: 0, waiting_ms: 0 }, busy, selected, lang]);
    if (key !== renderKey) {
      renderKey = key;
      header.querySelector('strong').textContent = task.title || tr('出图 Agent');
      el('figureAgentNew').textContent = tr('新任务');
      el('figureAgentHistory').textContent = tr('历史');
      const running = ['planning', 'running'].includes(task.status);
      el('figureAgentNew').disabled = busy || running || !!pendingAck;
      el('figureAgentHistory').disabled = busy || running || !!pendingAck;
      const completed = (task.steps || []).filter((s) => s.status === 'done').length;
      el('figureAgentContent').innerHTML =
        `${task.prompt ? `<details class="figure-task-request"><summary>${esc(tr('出图要求'))}</summary><p>${esc(task.requests.map((r) => r.text).join('\n'))}</p></details>` : ''}
        <div class="figure-tags">${(task.understood_tags || []).map((tag) => `<span>${esc(tag)}</span>`).join('')}</div>
        ${
          (task.steps || []).length
            ? `<details open class="figure-progress"><summary>${esc(tr('进度'))} ${completed}/${task.steps.length} · ${duration(task.elapsed_ms)}</summary>
        ${task.steps.map((s) => `<div class="figure-step ${esc(s.status)}"><span>${{ done: '✓', running: '■', waiting: '!', failed: '×', cancelled: '—' }[s.status] || '□'}</span><span>${s.index + 1}. ${esc(tr(s.title))}</span><small>${s.elapsed_ms ? duration(s.elapsed_ms) : ''}</small></div>`).join('')}</details>`
            : ''
        }
        ${cardsHTML()}
        ${task.error ? `<div class="figure-blocked">${esc(tr(task.error.detail))}</div>` : ''}
        ${
          task.export_check
            ? `<section class="figure-export-check"><h4>${esc(tr('导出检查'))}</h4><p>${esc(task.export_check.formats.join(' + '))} · v${task.export_check.drawing_version}</p>
          <p>${esc(tr(task.export_check.stale ? '图纸已改变，请重新生成候选' : task.export_check.validation.ok ? '图纸校验通过' : '图纸校验未通过'))}</p>
          ${task.export_check.validation.issues?.map((issue) => `<p>${esc(issue.message)}</p>`).join('') || ''}${btn('confirm_export')}</section>`
            : ''
        }
        ${(task.runs || []).map((run) => `<section><h4>${esc(tr('导出文件'))} · v${run.drawing_version}</h4>${run.files.map((f) => `<p><a href="/api/agent-file?${new URLSearchParams({ task: taskId, path: f.path })}" target="_blank" rel="noopener">${esc(f.name || f.path.split(/[\\/]/).at(-1))}</a></p>`).join('')}</section>`).join('')}`;
      const actions = !taskId
        ? ['start']
        : running
          ? ['stop']
          : v && !v.passed
            ? ['retry_appearance_only', 'back_to_drawing', 'discard']
            : ['revise', 'regenerate', 'undo_candidate', 'retry_step'];
      el('figureAgentActions').innerHTML = actions
        .filter((a) => !taskId || task.allowed_actions.includes(a))
        .map((a) => btn(a))
        .join('');
      const ackButton = document.createElement('button');
      if (pendingAck) {
        ackButton.textContent = tr('重试采用确认');
        ackButton.onclick = poll;
        el('figureAgentActions').append(ackButton);
      }
      bind(el('figureAgentContent'));
      bind(el('figureAgentActions'));
    }
    if (g && ['pending', 'stale'].includes(g.state) && !compareKey) comparing = true;
    renderBar();
    compare.hidden = !comparing || !g;
    el('figureCanvas').hidden = comparing && !!g;
    if (g && comparing) previewComparison();
    if (g)
      for (const choice of g.variants) {
        const cached = cache.get(`${taskId}/${g.group_id}/${choice.variant_id}/${mode === 'mono'}`);
        cached
          ?.then((result) => {
            const thumb = el('figureAgentContent').querySelector(
              `[data-figure-thumb="${choice.variant_id}"]`,
            );
            if (thumb && group()?.group_id === g.group_id) {
              thumb.src = 'data:image/png;base64,' + result.png;
              thumb.hidden = false;
            }
          })
          .catch(() => {});
      }
  }
  function bind(root) {
    root
      .querySelectorAll('[data-figure-action]')
      .forEach((button) => (button.onclick = () => act(button.dataset.figureAction)));
    root.querySelectorAll('[data-figure-variant]').forEach(
      (button) =>
        (button.onclick = () => {
          selected = button.dataset.figureVariant;
          comparing = true;
          renderKey = '';
          render();
        }),
    );
    root.querySelector('[data-figure-highlight]')?.addEventListener('click', highlight);
  }
  function renderBar() {
    const g = group(),
      v = variant();
    bar.hidden = !g || g.state === 'discarded';
    if (bar.hidden) return;
    const key = JSON.stringify([
      g.group_id,
      g.state,
      selected,
      mode,
      comparing,
      busy,
      v.allowed_actions,
      task.allowed_actions,
      window.cdI18n?.language,
    ]);
    if (key === barKey) return;
    barKey = key;
    bar.className = v.passed ? '' : 'blocked';
    const action = (v.allowed_actions || [])[0];
    bar.innerHTML = `<strong>${esc(tr(!v.passed ? '此方案已拦下' : g.state === 'stale' ? '基于旧版本' : g.state === 'adopted' ? '已采用' : '候选'))}</strong>
      <span>${g.variants.map((x) => `<button data-figure-variant="${x.variant_id}" aria-pressed="${x.variant_id === selected}">${esc(tr('方案'))} ${x.variant_id}</button>`).join('')}</span>
      <span>${[
        ['side', '并排'],
        ['slider', '滑动'],
        ['mono', '黑白'],
      ]
        .map(
          ([value, label]) =>
            `<button data-figure-mode="${value}" aria-pressed="${mode === value && comparing}">${esc(tr(label))}</button>`,
        )
        .join('')}</span>
      <button data-figure-current>${esc(tr(comparing ? '编辑当前图纸' : '查看对比'))}</button>
      ${action ? `<button class="primary" data-figure-action="${action}" ${allowed(action) ? '' : 'disabled'}>${esc(tr('采用方案'))} ${selected} (${esc(tr('一步撤销'))})</button>` : ''}
      ${task.allowed_actions.includes('discard') ? btn('discard') : ''}`;
    bind(bar);
    bar.querySelector('[data-figure-current]').onclick = () => {
      comparing = !comparing;
      render();
    };
    bar.querySelectorAll('[data-figure-mode]').forEach(
      (button) =>
        (button.onclick = () => {
          mode = button.dataset.figureMode;
          comparing = true;
          render();
        }),
    );
  }
  function preview(table, profile, mono, key) {
    if (cache.has(key)) return cache.get(key);
    const promise = previewQueue.then(() =>
      api('/api/figure-preview', { layout: table, profile, mono }),
    );
    previewQueue = promise.catch(() => {});
    cache.set(key, promise);
    promise.catch(() => cache.delete(key));
    if (cache.size > 12) cache.delete(cache.keys().next().value);
    return promise;
  }
  async function previewComparison() {
    const g = group(),
      v = variant(),
      mono = mode === 'mono',
      key = `${taskId}/${g.group_id}/${selected}/${mode}`;
    if (compareKey === key) {
      sizeComparison();
      return;
    }
    compareKey = key;
    const ticket = ++generation;
    compare.dataset.loading = 'true';
    compare.textContent = tr('正在生成真实出图预览…');
    pair = null;
    try {
      const before = await preview(
        g.before_table,
        g.before_profile,
        mono,
        `${taskId}/${g.group_id}/before/${mono}`,
      );
      const after = v.passed
        ? await preview(
            v.preview,
            v.profile,
            mono,
            `${taskId}/${g.group_id}/${v.variant_id}/${mono}`,
          )
        : before;
      if (ticket !== generation) return;
      pair = { before, after };
      compare.innerHTML = `<div class="figure-comparison-papers ${mode === 'slider' ? 'slider' : ''}">
        <figure data-paper="before"><figcaption>${esc(tr('改前'))} · v${g.base_version} · ${sizeText([before.bounds_cm[2] - before.bounds_cm[0], before.bounds_cm[3] - before.bounds_cm[1]])}</figcaption><div class="figure-compare-sheet"><img alt="${esc(tr('改前'))}"></div></figure>
        <figure data-paper="after"><figcaption>${esc(tr('方案'))} ${selected} · ${esc(v.title)}</figcaption><div class="figure-compare-sheet"><img alt="${esc(tr('候选'))}"></div></figure></div>
        ${mode === 'slider' ? `<label class="figure-slider-label">${esc(tr('对比分界'))}<input type="range" min="0" max="100" value="50" aria-label="${esc(tr('对比分界'))}"></label>` : ''}
        <p>${esc(tr(v.passed ? '两边使用相同纸面比例' : '显示改前图纸及发生连接变化的位置'))}</p>`;
      for (const name of ['before', 'after'])
        compare.querySelector(`[data-paper="${name}"] img`).src =
          'data:image/png;base64,' + pair[name].png;
      compare.querySelector('input')?.addEventListener('input', (event) => {
        compare.querySelector('[data-paper="after"]').style.clipPath =
          `inset(0 0 0 ${event.target.value}%)`;
      });
      sizeComparison();
      if (!v.passed) await drawHighlights(v, g.before_table);
      for (const choice of g.variants.filter((item) => item.passed)) {
        const result = await preview(
          choice.preview,
          choice.profile,
          mono,
          `${taskId}/${g.group_id}/${choice.variant_id}/${mono}`,
        );
        if (ticket !== generation) return;
        const thumb = el('figureAgentContent').querySelector(
          `[data-figure-thumb="${choice.variant_id}"]`,
        );
        if (thumb) {
          thumb.src = 'data:image/png;base64,' + result.png;
          thumb.hidden = false;
        }
      }
    } catch (error) {
      if (ticket === generation) {
        compare.textContent = tr(error.message);
        say(error.message);
        const retry = document.createElement('button');
        retry.textContent = tr('重试');
        retry.onclick = () => {
          compareKey = '';
          previewComparison();
        };
        compare.append(retry);
      }
    } finally {
      if (ticket === generation) compare.dataset.loading = 'false';
    }
  }
  function sizeComparison() {
    if (!pair || compare.hidden) return;
    const widths = Object.values(pair).map((p) => p.bounds_cm[2] - p.bounds_cm[0]);
    const heights = Object.values(pair).map((p) => p.bounds_cm[3] - p.bounds_cm[1]);
    const px = Math.min(
      37.8,
      Math.max(2, (compare.clientWidth - 44) / (Math.max(...widths) * (mode === 'slider' ? 1 : 2))),
    );
    for (const name of ['before', 'after']) {
      const p = pair[name],
        figure = compare.querySelector(`[data-paper="${name}"]`),
        sheet = figure?.querySelector('.figure-compare-sheet');
      if (!sheet) continue;
      figure.style.width = Math.max(...widths) * px + 'px';
      sheet.style.width = (p.bounds_cm[2] - p.bounds_cm[0]) * px + 'px';
      sheet.style.height = (p.bounds_cm[3] - p.bounds_cm[1]) * px + 'px';
      if (mode === 'slider') figure.style.height = Math.max(...heights) * px + 32 + 'px';
    }
    const papers = compare.querySelector('.slider');
    if (papers) papers.style.height = Math.max(...heights) * px + 32 + 'px';
  }
  async function drawHighlights(v, table) {
    if (!pair) return;
    const ticket = generation;
    const checked = await api('/api/figure-check', {
      layout: table,
      profile: group().before_profile,
    });
    if (ticket !== generation) return;
    const f = checked.figure,
      b = pair.before.bounds_cm,
      w = b[2] - b[0],
      h = b[3] - b[1];
    const rects = f.shapes
      .filter((s) => v.highlight.elements.some((id) => id === s.id || id.startsWith(s.id + '/')))
      .map((s) => s.bounds);
    for (const i of v.highlight.wires) {
      const wire = table.wires?.[i] || v.preview.wires?.[i];
      if (wire?.length) {
        const xs = wire.map((p) => p[0] * f.scale[0]),
          ys = wire.map((p) => p[1] * f.scale[1]);
        rects.push([Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]);
      }
    }
    compare.querySelectorAll('.figure-compare-sheet').forEach((sheet) => {
      const overlay = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      overlay.classList.add('figure-diff-highlight');
      overlay.setAttribute('viewBox', `0 0 ${w} ${h}`);
      for (const box of rects) {
        const rect = document.createElementNS(overlay.namespaceURI, 'rect');
        rect.setAttribute('x', box[0] - b[0] - 0.1);
        rect.setAttribute('y', b[3] - box[3] - 0.1);
        rect.setAttribute('width', box[2] - box[0] + 0.2);
        rect.setAttribute('height', box[3] - box[1] + 0.2);
        overlay.append(rect);
      }
      sheet.append(overlay);
    });
  }
  function highlight() {
    comparing = true;
    compareKey = '';
    render();
    compare.scrollIntoView({ block: 'nearest' });
  }
  el('figureAgentNew').onclick = () => {
    draftScope = null;
    taskId = '';
    task = null;
    sessionStorage.removeItem('figure-task-id');
    compareKey = '';
    comparing = false;
    ++generation;
    cache.clear();
    render();
    poll();
  };
  el('figureAgentHistory').onclick = async () => {
    try {
      const data = await request('status', { task_id: undefined });
      const select = document.createElement('select');
      select.setAttribute('aria-label', tr('出图任务历史'));
      select.innerHTML =
        `<option value="">${esc(tr('历史'))}</option>` +
        data.history
          .map((h) => `<option value="${esc(h.task_id)}">${esc(h.title)}</option>`)
          .join('');
      select.onchange = () => {
        if (select.value) {
          draftScope = null;
          taskId = select.value;
          sessionStorage.setItem('figure-task-id', taskId);
          compareKey = '';
          comparing = true;
          poll();
        }
        select.remove();
      };
      header.after(select);
      select.focus();
    } catch (error) {
      say(error.message);
    }
  };
  let draftTimer;
  el('figureInput').addEventListener('input', () => {
    clearTimeout(draftTimer);
    draftTimer = setTimeout(poll, 200);
  });
  el('figureInput').addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      act(taskId ? 'revise' : 'start');
    }
  });
  const shortcuts = {
    figureRepeat: '只展开第一个重复模块，其余折叠，保留模块名和端口。',
    figurePartition: '按模块添加不同的浅色色块，区域名称放下方。',
    figureArrange: '整理标签位置，避免标签压线，保留标签文字。',
    figureNumber: '统一显示标签的模块下标，保留器件 ID 和参数。',
  };
  for (const [id, prompt] of Object.entries(shortcuts).filter(([id]) => el(id)))
    el(id).onclick = async () => {
      window.CircuitFigure.tab('agent');
      el('figureInput').value = tr(prompt);
      await poll();
      const action = taskId ? 'revise' : 'start';
      if (!allowed(action)) {
        say('请先停止当前运行中的 Agent 任务');
        return;
      }
      act(action);
    };
  el('figureAssign').onclick = () => {
    const objects = window.CircuitFigure.selected();
    if (!objects.length || busy || pendingAck || ['running','planning'].includes(task?.status)) return;
    el('figureAgentNew').click();
    draftScope = {start_from:'selection', selection:objects.map(s => s.type + ':' + s.id),
      selection_version:session().context().version};
    window.CircuitFigure.tab('agent');
    render();
    el('figureInput').focus();
  };
  new ResizeObserver(sizeComparison).observe(compare);
  document.addEventListener('editor-language-change', () => {
    renderKey = '';
    compareKey = '';
    render();
  });
  window.addEventListener('circuit-drawer:layout-changed', () => {
    if (active()) poll();
  });
  setInterval(poll, 1000);
  poll();
})();
