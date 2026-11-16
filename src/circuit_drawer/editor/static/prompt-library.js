// Requirements stay editable and never start a model call or change the drawing.
window.CircuitPromptLibrary = (() => {
  const tr = text => window.cdI18n?.translate ? window.cdI18n.translate(text) : text;
  function toggleLabel(box) {
    box.querySelector('[data-toggle]').textContent = tr(box.querySelector('[data-content]').hidden ? '展开提示词库' : '关闭');
  }
  document.addEventListener('editor-language-change', () => {
    document.querySelectorAll('fieldset[data-prompt-library]').forEach(toggleLabel);
  });
  function mount({textarea, domain, mode, table, api, busy = () => false}) {
    if (!textarea) return;
    if (textarea.dataset.promptLibrary) {
      const existing = textarea.previousElementSibling;
      if (existing?.querySelector('[data-content]')?.hidden) existing.querySelector('[data-toggle]').click();
      return;
    }
    textarea.dataset.promptLibrary = 'true';
    const box = document.createElement('fieldset'); box.dataset.promptLibrary = 'true';
    box.innerHTML = `<legend><span>常用需求与个人提示词</span><button type="button" data-toggle aria-expanded="true">关闭</button></legend>
      <div data-content><input data-search placeholder="搜索提示词" aria-label="搜索提示词">
      <select data-presets aria-label="选择常用需求"></select><p data-capability class="hint"></p>
      <div data-fields></div><p data-context class="hint"></p>
      <button type="button" data-fill>填入需求（仍可编辑）</button>
      <input data-title placeholder="个人提示词名称" aria-label="个人提示词名称">
      <button type="button" data-save>另存个人提示词</button><button type="button" data-update>更新所选个人提示词</button>
      <button type="button" data-delete>删除所选个人提示词</button><p data-status role="status"></p></div>`;
    textarea.before(box);
    const el = key => box.querySelector('[data-' + key + ']');
    let items = [], revision = 0, context = {}, selected = null, generation = 0;
    const language = () => { try { return localStorage.getItem('circuit-drawer-language') || 'zh-CN'; } catch { return 'zh-CN'; } };
    const label = value => value?.[language()] || value?.['zh-CN'] || '';
    const status = text => { el('status').textContent = tr(text); };
    const values = () => Object.fromEntries([...el('fields').querySelectorAll('input')].map(i => [i.dataset.key, i.value]));
    el('content').id = textarea.id + '-prompt-library-content';
    el('toggle').setAttribute('aria-controls', el('content').id);
    el('toggle').onclick = () => {
      const collapsed = !el('content').hidden;
      el('content').hidden = collapsed;
      el('toggle').setAttribute('aria-expanded', String(!collapsed));
      toggleLabel(box);
      if (!collapsed && !items.length) refresh();
      (collapsed ? textarea : el('search')).focus();
    };
    function choose() {
      selected = items.find(x => x.id === el('presets').value) || null;
      ++generation;
      el('fields').replaceChildren();
      el('capability').textContent = label(selected?.capability);
      el('title').value = selected?.personal ? label(selected.title) : '';
      el('update').disabled = el('delete').disabled = !selected?.personal;
      for (const field of selected?.fields || []) {
        const row = document.createElement('label'); row.style.display = 'block';
        row.append(document.createTextNode(label(field.label) + (field.unit ? ' (' + field.unit + ')' : '') + ' '));
        const input = document.createElement('input'); input.dataset.key = field.key;
        input.setAttribute('aria-label', label(field.label)); input.maxLength = 500;
        input.placeholder = tr('待填写；留空保留符号'); input.value = context.values?.[field.key] || '';
        input.title = context.sources?.[field.key] || '';
        const candidates = context.candidates?.[field.key] || [];
        if (candidates.length) {
          const list = document.createElement('datalist'); list.id = 'prompt-' + domain + '-' + mode + '-' + field.key;
          input.setAttribute('list', list.id);
          for (const item of candidates) { const option = document.createElement('option'); option.value = item.value; option.label = item.source; list.append(option); }
          row.append(list);
        }
        row.append(input); el('fields').append(row);
      }
      el('context').textContent = context.objects?.length ? tr('当前图对象：') + context.objects.join(', ') : '';
    }
    async function refresh() {
      const stamp = ++generation;
      try {
        const result = await api('/api/prompt-library', {action: 'list', domain, mode, query: el('search').value, table: table()});
        if (stamp !== generation || !box.isConnected) return;
        items = result.items; revision = result.revision; context = result.context;
        el('presets').replaceChildren();
        for (const item of items) { const option = document.createElement('option'); option.value = item.id; option.textContent = label(item.title); el('presets').append(option); }
        choose();
      } catch (error) { if (stamp === generation) status(error.message); }
    }
    el('search').oninput = refresh;
    el('presets').onchange = choose;
    el('fields').oninput = () => ++generation;
    textarea.addEventListener('input', () => ++generation);
    el('fill').onclick = async () => {
      if (!selected || busy() || textarea.disabled) return;
      const stamp = ++generation, before = textarea.value;
      try {
        const result = await api('/api/prompt-library', {action:'render', domain, id:selected.id, values:values()});
        if (stamp !== generation || before !== textarea.value || !box.isConnected || el('content').hidden || busy() || textarea.disabled) return;
        textarea.value = result.text; textarea.dispatchEvent(new Event('input', {bubbles:true}));
        status(result.missing.length ? '缺少设计值：可先生成符号图，数值分析前必须补齐。' : '需求已填入，请检查后再生成。');
      } catch (error) { status(error.message); }
    };
    async function save(update) {
      if (busy() || textarea.disabled) return;
      const text = textarea.value, title = el('title').value;
      try {
        await api('/api/prompt-library', {action:'save', domain, mode, revision, ...(update ? {id:selected?.id} : {}), title, text});
        status('个人提示词已保存。'); await refresh();
      } catch (error) { status(error.message); }
    }
    el('save').onclick = () => save(false);
    el('update').onclick = () => { if(selected?.personal) save(true); };
    el('delete').onclick = async () => {
      if (!selected?.personal || busy() || textarea.disabled) return;
      if (!(window.cdI18n?.confirm || window.confirm)('删除所选个人提示词？')) return;
      try { await api('/api/prompt-library', {action:'delete', domain, id:selected.id, revision}); status('个人提示词已删除。'); await refresh(); }
      catch(error) { status(error.message); }
    };
    toggleLabel(box);
    refresh();
  }
  return {mount};
})();
