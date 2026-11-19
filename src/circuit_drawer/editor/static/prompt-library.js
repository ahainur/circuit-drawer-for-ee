// Requirements stay editable and never start a model call or change the drawing.
window.CircuitPromptLibrary = (() => {
  const tr = text => window.cdI18n?.translate ? window.cdI18n.translate(text) : text;
  function toggleLabel(box) {
    box.querySelector('[data-toggle]').textContent = tr(box.querySelector('[data-content]').hidden ? '展开提示词库' : '关闭');
  }
  document.addEventListener('editor-language-change', () => {
    document.querySelectorAll('fieldset[data-prompt-library]').forEach(toggleLabel);
  });
  function mount({textarea, domain, mode, table, api, onScenario, busy = () => false}) {
    if (!textarea) return;
    if (textarea.dataset.promptLibrary) {
      const existing = document.getElementById(textarea.id + '-prompt-library-content')?.closest('fieldset') || textarea.previousElementSibling;
      if (existing?.querySelector('[data-content]')?.hidden) existing.querySelector('[data-toggle]').click();
      existing?.dispatchEvent(new Event('prompt-library-refresh'));
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
    const categories = {layout:'版式', color:'配色', module:'模块', label:'标注', compose:'组合', export:'导出'};
    const itemTitle = item => domain === 'figure'
      ? `${item.starred ? '★' : '☆'} ${label(item.title)} · ${tr(categories[item.category] || '我的')}`
      : label(item.title);
    const status = text => { el('status').textContent = tr(text); };
    const values = () => Object.fromEntries([...el('fields').querySelectorAll('[data-key]')].map(i => [i.dataset.key, i.value]));
    let category = '';
    const scenarios = !!onScenario && ['circuit','control'].includes(domain);
    const groups = [['','全部'],['dc','DC/DC'],['inverter','逆变与并网'],['pfc','整流与 PFC'],['motor','电机控制']];
    function scenarioLabels() {
      if (!scenarios) return;
      box.querySelector('legend span').textContent = tr('常用需求与个人提示词');
      for (const [key, text] of [['fill','填入需求（仍可编辑）'],['save','另存个人提示词'],
        ['update','更新所选个人提示词'],['delete','删除所选个人提示词']]) el(key).textContent = tr(text);
      for (const [key, text] of [['search','搜索提示词'],['title','个人提示词名称']]) {
        el(key).placeholder = tr(text); el(key).setAttribute('aria-label',tr(text));
      }
      el('presets').setAttribute('aria-label',tr('选择常用需求'));
      for (const [i, pair] of groups.entries()) el('group').options[i].textContent = tr(pair[1]);
      for (const [i, text] of ['只画图','示例仿真'].entries()) el('task-mode').options[i].textContent = tr(text);
      el('personal').previousElementSibling.textContent = tr('我的提示词');
      for (const [key, title] of [['group','场景分类'],['task-mode','任务方式'],['personal','我的提示词']])
        el(key).setAttribute('aria-label',tr(title));
      for (const o of el('personal').options) o.textContent = o.value ? itemTitle(items.find(x => x.id === o.value)) : tr('选择个人提示词');
    }
    if (scenarios) {
      el('search').insertAdjacentHTML('afterend', '<select data-group aria-label="场景分类"></select>');
      for (const [key, text] of groups)
        el('group').add(new Option(tr(text), key));
      el('group').onchange = () => { category = el('group').value; refresh(); };
      el('presets').size = 4;
      el('fill').insertAdjacentHTML('beforebegin', '<select data-task-mode aria-label="任务方式"></select>');
      el('task-mode').add(new Option(tr('只画图'),'draw'));
      el('task-mode').add(new Option(tr('示例仿真'),'example'));
      const personal = document.createElement('details'), summary = document.createElement('summary');
      summary.textContent = tr('我的提示词'); personal.append(summary);
      const mine = document.createElement('select'); mine.dataset.personal = ''; mine.setAttribute('aria-label',tr('我的提示词'));
      personal.append(mine); el('delete').after(personal);
      for (const key of ['title','save','update','delete']) personal.append(el(key));
      scenarioLabels();
      mine.onchange = () => { el('presets').selectedIndex = -1; choose(); };
    }
    if (domain === 'figure') {
      box.classList.add('figure-prompts');
      el('presets').size = 6;
      box.querySelector('legend span').textContent = '出图提示词库';
      const categories = document.createElement('div'); categories.className = 'prompt-categories';
      for (const [key, text] of [['','全部'],['layout','版式'],['color','配色'],['module','模块'],['label','标注'],['compose','组合'],['export','导出'],['starred','收藏'],['personal','我的']]) {
        const button = document.createElement('button'); button.type = 'button'; button.textContent = tr(text);
        button.dataset.category = key; button.setAttribute('aria-pressed', String(!key));
        button.onclick = () => {
          category = key;
          categories.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(b === button)));
          refresh();
        };
        categories.append(button);
      }
      el('search').after(categories);
      const favorite = document.createElement('button'); favorite.type = 'button'; favorite.dataset.star = '';
      favorite.onclick = async () => {
        if (!selected) return;
        try {
          await api('/api/prompt-library', {action:'star', domain, id:selected.id, revision, starred:!selected.starred});
          await refresh();
        } catch (error) { status(error.message); }
      };
      el('fill').after(favorite);
      const personal = document.createElement('details'), summary = document.createElement('summary');
      summary.textContent = tr('管理个人提示词'); personal.append(summary);
      el('delete').after(personal);
      for (const key of ['title','save','update','delete']) personal.append(el(key));
    }
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
      selected = items.find(x => x.id === (el('presets').value || el('personal')?.value)) || null;
      ++generation;
      el('fill').disabled = !selected;
      el('fields').replaceChildren();
      el('capability').textContent = label(selected?.capability);
      el('title').value = selected?.personal ? label(selected.title) : '';
      el('update').disabled = el('delete').disabled = !selected?.personal;
      if (el('star')) {
        el('star').disabled = !selected;
        el('star').textContent = tr(selected?.starred ? '★ 已收藏' : '☆ 收藏');
        el('star').setAttribute('aria-pressed', String(!!selected?.starred));
      }
      for (const field of selected?.fields || []) {
        const row = document.createElement('label'); row.style.display = 'block';
        row.append(document.createTextNode(label(field.label) + (field.unit ? ' (' + field.unit + ')' : '') + ' '));
        const options = field.source === 'modules' ? context.modules || [] : field.options;
        const input = document.createElement(options ? 'select' : 'input'); input.dataset.key = field.key;
        if (options) {
          input.add(new Option(tr('请选择'), ''));
          options.forEach(value => input.add(new Option(tr(value), value)));
        }
        input.setAttribute('aria-label', label(field.label)); input.maxLength = 500;
        input.placeholder = tr('待填写；留空保留符号'); input.value = context.values?.[field.key] || field.default || '';
        input.title = context.sources?.[field.key] || '';
        const candidates = context.candidates?.[field.key] || [];
        if (candidates.length && !options) {
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
      const previous = selected?.id, title = el('title').value;
      const edited = values();
      try {
        const result = await api('/api/prompt-library', {action: 'list', domain, mode, category, include_scenarios:scenarios, query: el('search').value, table: table()});
        if (stamp !== generation || !box.isConnected) return;
        items = result.items; revision = result.revision; context = result.context;
        el('presets').replaceChildren();
        el('personal')?.replaceChildren(new Option(tr('选择个人提示词'),''));
        for (const item of items) {
          const list = scenarios && item.personal ? el('personal') : el('presets');
          list.add(new Option(itemTitle(item),item.id));
        }
        if (scenarios && items.some(item => item.id === previous && item.personal)) {
          el('personal').value = previous; el('presets').selectedIndex = -1;
        } else if (items.some(item => item.id === previous)) el('presets').value = previous;
        else if (items.length) el('presets').selectedIndex = 0;
        choose();
        if (selected?.id === previous) {
          el('title').value = title;
          for (const input of el('fields').querySelectorAll('[data-key]')) {
            const value = edited[input.dataset.key];
            if (value === undefined) continue;
            if (input.tagName !== 'SELECT' || [...input.options].some(option => option.value === value)) input.value = value;
          }
        }
      } catch (error) { if (stamp === generation) status(error.message); }
    }
    el('search').oninput = refresh;
    box.addEventListener('prompt-library-refresh', refresh);
    if (domain === 'figure') window.addEventListener('circuit-drawer:layout-changed', () => {
      if (box.isConnected && !box.closest('[hidden]')) refresh();
    });
    el('presets').onchange = () => { if (el('personal')) el('personal').value = ''; choose(); };
    document.addEventListener('editor-language-change', () => {
      if (!box.isConnected) return;
      const edited = values(), title = el('title').value;
      scenarioLabels();
      for (const option of el('presets').options) {
        option.textContent = itemTitle(items.find(item => item.id === option.value));
      }
      choose(); el('title').value = title;
      for (const input of el('fields').querySelectorAll('[data-key]')) {
        if (input.dataset.key in edited) input.value = edited[input.dataset.key];
      }
    });
    el('fields').oninput = () => ++generation;
    textarea.addEventListener('input', () => ++generation);
    el('fill').onclick = async () => {
      if (!selected || busy() || textarea.disabled) return;
      const stamp = ++generation, before = textarea.value;
      try {
        const taskMode = el('task-mode')?.value;
        const result = await api('/api/prompt-library', {action:'render', domain, id:selected.id, values:values(), task_mode:taskMode});
        if (stamp !== generation || before !== textarea.value || !box.isConnected || el('content').hidden || busy() || textarea.disabled) return;
        textarea.value = result.text; textarea.dispatchEvent(new Event('input', {bubbles:true}));
        if (scenarios) onScenario(taskMode, selected.example_case);
        status(result.missing.length && taskMode !== 'example' ? (domain === 'figure' ? '请补充出图要求中的待填写项。' : '缺少设计值：可先生成符号图，数值分析前必须补齐。') : '需求已填入，请检查后再生成。');
      } catch (error) { status(error.message); }
    };
    async function save(update) {
      if (busy() || textarea.disabled) return;
      const text = textarea.value, title = el('title').value;
      try {
        await api('/api/prompt-library', {action:'save', domain, mode, revision, ...(update ? {id:selected?.id} : {}), title, text,
          ...(domain === 'figure' ? {category:selected?.category || 'layout'} : {})});
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
