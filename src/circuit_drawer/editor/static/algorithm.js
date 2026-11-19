/* Algorithm documents share the control editor, candidate history and paper exports. */
(() => {
  if (!window.CircuitFigure || !document.getElementById('figureCompose')) return;
  const tr = text => window.cdI18n?.translate(text) || text;
  const esc = value => String(value ?? '').replace(/[&<>"']/g,
    c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'})[c]);
  const dialog = document.createElement('dialog');
  dialog.id = 'algorithmDialog';
  dialog.className = 'figure-document-dialog';
  document.body.append(dialog);
  const button = (action, text) => `<button type="button" data-action="${action}">${esc(tr(text))}</button>`;
  let busy = false, candidate = null, basis = '';
  const snapshot = () => window.CircuitAgentEditor.snapshot();
  const key = () => JSON.stringify(snapshot());
  function message(text) { dialog.querySelector('[role=status]').textContent = tr(text); }
  function shell(title, body) {
    dialog.innerHTML = `<header><h3>${esc(tr(title))}</h3>${button('close','关闭')}</header>${body}<p role="status"></p>`;
    window.cdI18n?.apply?.(dialog);
    if (!dialog.open) dialog.showModal();
  }
  async function run(action) {
    if (busy) return;
    busy = true;
    dialog.setAttribute('aria-busy','true');
    message('处理中…');
    try { await action(); }
    catch (error) { message(error.message); }
    finally { busy = false; dialog.removeAttribute('aria-busy'); }
  }
  dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
  dialog.onclick = event => {
    const target = event.target.closest('[data-action]');
    if (!target || busy) return;
    const action = target.dataset.action;
    if (action === 'close') dialog.close();
    if (action === 'create' || action === 'open') {
      const name = action === 'create' ? dialog.querySelector('[name=name]').value.trim() : target.dataset.name;
      const child = window.open('about:blank','_blank');
      if (child) child.opener = null;
      run(async () => {
        try {
          const result = await api('/api/figure-document',{action,name});
          if (child) { child.location.href = result.url; dialog.close(); }
          else {
            const link = document.createElement('a');
            link.href = result.url; link.target = '_blank'; link.rel = 'noopener';
            link.textContent = tr('打开算法流程图');
            dialog.querySelector('[role=status]').replaceChildren(link);
          }
        } catch (error) { child?.close(); throw error; }
      });
    }
    if (action === 'preview') run(async () => {
      candidate = null;
      dialog.querySelector('[data-action=adopt]').disabled = true;
      basis = key();
      const current = snapshot();
      const steps = dialog.querySelector('textarea').value.split('\n').map(s => s.trim()).filter(Boolean);
      const result = await api('/api/algorithm-steps',{table:current.table,profile:current.profile,steps});
      if (basis !== key()) throw new Error(tr('等待操作时图纸已改变，请重试。'));
      candidate = result.table;
      const img = dialog.querySelector('img');
      img.src = 'data:image/png;base64,' + result.png; img.hidden = false;
      dialog.querySelector('[data-action=adopt]').disabled = false;
      message('预览已生成，采用后可一步撤销。');
    });
    if (action === 'adopt') {
      if (!candidate || basis !== key()) { message('等待操作时图纸已改变，请重试。'); return; }
      window.CircuitFigure.install(candidate,tr('更新算法步骤'));
      candidate = null; dialog.close();
      document.getElementById('fitBtn').click();
    }
  };
  function documents() {
    if (busy) return;
    shell('算法流程图',`<label>${tr('新图名称')} <input name="name" maxlength="80" value="algorithm"></label>
      ${button('create','新建空白算法图')}<p>${tr('算法图单独保存，可用 Agent 生成、手工修改，再加入组合出图。')}</p>
      <div class="algorithm-documents"></div>`);
    run(async () => {
      const result = await api('/api/figure-document',{action:'list'});
      dialog.querySelector('.algorithm-documents').innerHTML = result.files.map(file =>
        `<button type="button" data-action="open" data-name="${esc(file.replace(/\.algorithm\.json$/,''))}">${esc(file)}</button>`).join('');
      message('');
    });
  }
  function stepsDialog() {
    if (busy) return;
    candidate = null;
    shell('算法步骤',`<p>${tr('每行一个步骤，支持 LaTeX 公式。首尾使用圆角框。')}</p>
      <p>${tr('采用本次重建会替换图中的方块、连线、分区与文字。')}</p>
      <textarea rows="8" aria-label="${esc(tr('算法步骤'))}"></textarea>
      <img hidden alt="${esc(tr('算法图预览'))}"><footer>${button('preview','生成预览')}${button('adopt','采用（一步撤销）')}${button('close','取消')}</footer>`);
    dialog.querySelector('textarea').value = snapshot().table.blocks.map(b => b.text || '').join('\n');
    dialog.querySelector('[data-action=adopt]').disabled = true;
    dialog.querySelector('textarea').oninput = () => {
      candidate = null;
      dialog.querySelector('[data-action=adopt]').disabled = true;
    };
  }
  const entry = document.createElement('button');
  entry.id = 'figureAlgorithm'; entry.type = 'button'; entry.textContent = tr('算法流程图');
  entry.onclick = documents;
  document.getElementById('figureCompose').before(entry);
  let configured = false;
  function configure() {
    if (configured || !snapshot()?.table?.algorithm) return;
    configured = true;
    document.body.classList.add('algorithm-document');
    const steps = document.createElement('button');
    steps.id = 'algorithmSteps'; steps.type = 'button'; steps.textContent = tr('算法步骤'); steps.onclick = stepsDialog;
    document.getElementById('addSel').before(steps);
    document.title = tr('算法流程图') + ' · Circuit Drawer';
    document.getElementById('menu-analysis').hidden = true;
    document.getElementById('describeControl').textContent = tr('描述算法流程…');
    for (const target of document.querySelectorAll('#add-block, #addSel option[value=block]'))
      target.textContent = tr('处理步骤');
    window.cdI18n?.apply?.(steps);
  }
  if (document.getElementById('world')) {
    window.addEventListener('circuit-drawer:layout-changed',configure);
    document.addEventListener('circuit-drawer:document-ready',configure);
    document.addEventListener('DOMContentLoaded', () => {
      if (snapshot()?.table) configure();
    });
  }
})();
