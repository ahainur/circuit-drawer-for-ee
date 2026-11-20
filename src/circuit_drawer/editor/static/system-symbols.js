/* The library uses the same vector marks as placed blocks and paper exports. */
(() => {
  const tr = (text) => window.cdI18n?.translate(text) || text;
  const control = !!document.querySelector('#world');
  const dialog = document.createElement('dialog');
  dialog.id = 'systemSymbolLibrary';
  dialog.innerHTML = `<form method="dialog"><header><h3>系统图标库</h3><button aria-label="关闭">×</button></header></form>
    <input type="search" aria-label="搜索系统图标" placeholder="搜索系统图标">
    <p>选择符号后，在图上拖出方框。信号箭头可用“画信号线”连接。</p><div class="system-symbol-grid"></div>`;
  document.body.append(dialog);
  function open(options={}) {
    const info = control ? analysis?.constants : report;
    if (!info || window.CircuitAgentEditor?.busy()) return;
    const onPaper = window.CircuitFigure?.isOpen();
    const hint = control
      ? (onPaper ? '选择系统图标会回到控制图放置方框并连接信号线。' : '选择图标后插入方框，可在画布上移动并连接信号线。')
      : (onPaper && !document.getElementById('figurePaper').hidden
        ? '在纸面拖出图标范围，放好后可直接拖动图标。' : '选择符号后，在图上拖出方框。信号箭头可用“画信号线”连接。');
    dialog.querySelector('p').textContent = tr(options.pick ? '点击图标后在波形上放置，也可拖入绘图区。' : hint);
    dialog.classList.toggle('wave-symbol-picker', !!options.pick);
    const personal = document.getElementById('figureImagesEntry');
    if (personal) personal.hidden = !!options.pick;
    const grid = dialog.querySelector('.system-symbol-grid');
    grid.replaceChildren();
    for (const [group, kinds] of info.icon_menu?.groups || []) {
      const heading = document.createElement('h4');
      heading.textContent = tr(group);
      heading.dataset.group = group;
      grid.append(heading);
      for (const kind of kinds) {
      const button = document.createElement('button');
      button.type = 'button';
      button.dataset.symbol = kind;
      button.dataset.group = group;
      const image = el('svg', { viewBox: '-.08 -1.08 1.16 1.16', 'aria-hidden': 'true' }, button);
      el(
        'rect',
        {
          x: 0,
          y: -1,
          width: 1,
          height: 1,
          rx: 0.08,
          fill: '#fff',
          stroke: '#444',
          'stroke-width': 0.015,
        },
        image,
      );
      if (control) {
        const group = el('g', {transform: 'scale(1,-1)'}, image);
        drawIcon(group, {icon:kind}, [0,0,1,1], '#222', .015, {
          icon:info.glyph_library[kind], icon_name:kind, icon_box:[0,0,1,1],
        });
      } else sketchMark(image, kind, 0, 0, 1, 1, info.glyph_library?.[kind]);
      const label = document.createElement('span');
      label.textContent = tr(info.icon_menu.labels[kind] || kind);
      button.append(label);
      button.onclick = () => {
        dialog.close();
        if (options.pick) options.pick(kind);
        else if (control) {
          if (window.CircuitFigure?.isOpen()) window.CircuitFigure.leave();
          addItem('icon:' + kind);
        } else if (window.CircuitFigure?.isOpen()) window.CircuitFigure.insertSymbol(kind);
        else {
          startBoxDraft('blocks');
          if (boxDraft) boxDraft.icon = kind;
        }
      };
      if (options.pick) {
        button.draggable = true;
        button.ondragstart = event => {
          event.dataTransfer.setData('application/x-circuit-wave-symbol',kind);
          event.dataTransfer.effectAllowed='copy';
        };
        button.ondragend = () => { if (dialog.open) dialog.close(); };
      }
      grid.append(button);
      }
    }
    const search = dialog.querySelector('input');
    search.value = '';
    search.oninput = () => {
      const query = search.value.toLowerCase().trim();
      for (const button of grid.querySelectorAll('button'))
        button.hidden = !`${button.dataset.symbol} ${button.dataset.group} ${button.textContent}`
          .toLowerCase()
          .includes(query);
      for (const heading of grid.querySelectorAll('h4'))
        heading.hidden = ![...grid.querySelectorAll('button')].some(button => button.dataset.group === heading.dataset.group && !button.hidden);
    };
    window.cdI18n?.apply?.(dialog);
    if (options.pick) dialog.show(); else dialog.showModal();
    search.focus();
  }
  const entry = document.createElement('button');
  entry.id = 'systemSymbols';
  entry.type = 'button';
  entry.textContent = '系统图标库';
  entry.onclick = open;
  (control ? document.getElementById('addSel').parentElement : document.querySelector('.library-heading')).append(entry);
  if (control) {
    const paperEntry = entry.cloneNode(true);
    paperEntry.id = 'figureSymbols';
    paperEntry.onclick = open;
    document.getElementById('figureCompose').before(paperEntry);
  }
  window.CircuitSystemSymbols = { open };
})();
