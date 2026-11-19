/* Imported artwork uses project-owned PNGs and the editor's normal undo transaction. */
(() => {
  if (!window.CircuitFigure || !document.querySelector('#systemSymbolLibrary header')) return;
  const tr = text => window.cdI18n?.translate(text) || text;
  const esc = value => String(value ?? '').replace(/[&<>"']/g,
    c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'})[c]);
  const snapshot = () => window.CircuitAgentEditor.snapshot();
  const key = () => JSON.stringify(snapshot());
  const control = !!document.getElementById('world');
  const cache = new Map();
  const dialog = document.createElement('dialog');
  dialog.id = 'figureImages';
  document.body.append(dialog);
  const fileInput = document.createElement('input');
  fileInput.type = 'file';
  fileInput.accept = 'image/png,image/jpeg,image/webp';
  fileInput.hidden = true;
  document.body.append(fileInput);
  let busy = false, editing = null, encoded = null, baseKey = '';
  let generator = null, generation = 0, savingPreferences = Promise.resolve();
  let preferences = {engine:'drawing', image_model:''};
  const field = name => dialog.querySelector(`[name="${name}"]`);
  const button = (action, text) => `<button type="button" data-action="${action}">${esc(tr(text))}</button>`;
  function message(text) {
    const target = dialog.open ? dialog.querySelector('[role=status]') : document.getElementById('figureStatusText');
    if (target) target.textContent = tr(text);
  }
  async function request(action, extra = {}) {
    const current = snapshot(), before = key();
    const result = await api('/api/figure-output', {action, layout:current.table,
      table:current.table, profile:current.profile, ...extra});
    if (before !== key()) throw new Error(tr('等待操作时图纸已改变，请重试。'));
    return result;
  }
  async function run(action) {
    if (busy) return;
    busy = true;
    dialog.setAttribute('aria-busy', 'true');
    message('处理中…');
    try { await action(); }
    catch (error) { message(error.message); }
    finally { busy = false; dialog.removeAttribute('aria-busy'); }
  }
  function shell(title, content) {
    dialog.innerHTML = `<header><h3>${esc(tr(title))}</h3>${button('close','关闭')}</header>${content}<p role="status"></p>`;
    window.cdI18n?.apply?.(dialog);
    if (!dialog.open) dialog.showModal();
  }
  dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
  dialog.addEventListener('close', () => { ++generation; generator = null; });
  dialog.addEventListener('click', event => {
    const action = event.target.closest('[data-action]')?.dataset.action;
    if (busy || !action) return;
    if (action === 'close') dialog.close();
    if (action === 'import') fileInput.click();
    if (action === 'library') library();
    if (action === 'generator') openGenerator();
    if (action === 'remember-only') run(async () => {
      const name = field('name').value.trim();
      if (!name) throw new Error(tr('请填写图标名称'));
      await request('remember-image', {...encoded, name});
      dialog.close();
    });
    if (action === 'apply') run(apply);
    if (action === 'remove') run(async () => {
      if (baseKey !== key()) throw new Error(tr('等待操作时图纸已改变，请重试。'));
      const result = await request('remove-image', {id:editing.id});
      window.CircuitFigure.install(result.table, tr('删除图片'));
      dialog.close();
    });
    if (action === 'use') run(async () => {
      const result = await request('use-image', {id:event.target.closest('[data-id]').dataset.id});
      const {preview, ...asset} = result.asset;
      edit(asset, 'data:image/png;base64,' + preview);
    });
  });
  async function preview(source) {
    const id = snapshot().file + ':' + source;
    if (!cache.has(id)) {
      const pending = request('image-preview', {source}).then(result => 'data:image/png;base64,' + result.png);
      cache.set(id, pending);
      pending.catch(() => cache.delete(id));
    }
    return cache.get(id);
  }
  function edit(asset, png, data = null, generated = false) {
    editing = structuredClone(asset);
    encoded = data;
    baseKey = key();
    const from = asset.from || [0, 0];
    const to = asset.to || [4, 4 * asset.pixel_size[1] / asset.pixel_size[0]];
    shell(asset.id ? '图片属性' : '插入图片', `<div class="figure-image-edit">
      <img src="${png}" alt="${esc(tr('图片预览'))}">
      <div><label>${tr('名称')} <input name="name" maxlength="120" value="${esc(asset.name)}"></label>
      <fieldset><legend>${tr('位置与边框（图纸单位）')}</legend>
      <label>X <input name="x" type="number" step="any" value="${from[0]}"></label>
      <label>Y <input name="y" type="number" step="any" value="${from[1]}"></label>
      <label>${tr('宽度')} <input name="width" type="number" step="any" min="0.01" value="${to[0]-from[0]}"></label>
      <label>${tr('高度')} <input name="height" type="number" step="any" min="0.01" value="${to[1]-from[1]}"></label></fieldset>
      <p>${tr('图片按原比例放入边框。拖动画布上的图片可调整位置。')}</p>
      <label><input name="foreground" type="checkbox" ${asset.foreground !== false ? 'checked' : ''}>${tr('置于图形前方')}</label>
      <label><input name="remember" type="checkbox">${tr('同时存入个人素材库')}</label></div></div>
      <footer>${button('apply','应用（一步撤销）')}${generated ? button('remember-only','只收藏到个人图标库') + button('generator','重新生成') : ''}${asset.id ? button('remove','删除图片') : ''}${button('close','取消')}</footer>`);
  }
  async function apply() {
    if (baseKey !== key()) throw new Error(tr('等待操作时图纸已改变，请重试。'));
    const name = field('name').value.trim();
    const values = ['x','y','width','height'].map(n => field(n).valueAsNumber);
    if (!name || !values.every(Number.isFinite) || values[2] <= 0 || values[3] <= 0)
      throw new Error(tr('请填写名称、有限坐标和正的宽高。'));
    let asset = {...editing, name};
    if (encoded || field('remember').checked) {
      const data = encoded || {png:(await preview(asset.source)).split(',')[1],
        mono_png:(await preview(asset.mono_source)).split(',')[1]};
      asset = {...asset, ...(await request('import-image', {...data, name,
        remember:field('remember').checked})).asset};
    }
    if (!asset.id) {
      const used = new Set((snapshot().table.images || []).map(item => item.id));
      let number = 1;
      while (used.has('image_' + number)) number++;
      asset.id = 'image_' + number;
    }
    asset.from = values.slice(0,2);
    asset.to = [values[0] + values[2], values[1] + values[3]];
    asset.foreground = field('foreground').checked;
    const result = await request('save-image', {id:asset.id, image:asset});
    window.CircuitFigure.install(result.table, tr('更新图片'));
    dialog.close();
  }
  function loadFile(file) {
    shell('导入图片', `<p>${tr('支持 PNG、JPEG、WebP，可直接粘贴截图。导入后保存为 PNG。')}</p>${button('close','取消')}`);
    run(async () => {
      if (!['image/png','image/jpeg','image/webp'].includes(file.type))
        throw new Error(tr('请选择 PNG、JPEG 或 WebP 图片。'));
      if (file.size > 16 * 1024 * 1024) throw new Error(tr('图片超过16 MiB'));
      const before = key();
      const bitmap = await createImageBitmap(file);
      const ratio = Math.min(1, 4096 / Math.max(bitmap.width, bitmap.height));
      const canvas = document.createElement('canvas');
      canvas.width = Math.max(1, Math.round(bitmap.width * ratio));
      canvas.height = Math.max(1, Math.round(bitmap.height * ratio));
      canvas.getContext('2d', {willReadFrequently:true}).drawImage(bitmap, 0, 0, canvas.width, canvas.height);
      bitmap.close();
      if (before !== key()) throw new Error(tr('等待操作时图纸已改变，请重试。'));
      editCanvas(canvas, (file.name || tr('截图')).replace(/\.[^.]+$/, '').slice(0,120));
    });
  }
  fileInput.onchange = () => {
    if (fileInput.files[0]) loadFile(fileInput.files[0]);
    fileInput.value = '';
  };
  function editCanvas(canvas, name, generated = false) {
    const ctx = canvas.getContext('2d', {willReadFrequently:true}), color = canvas.toDataURL('image/png');
    const pixels = ctx.getImageData(0,0,canvas.width,canvas.height);
    let visible = false;
    for (let i=0; i<pixels.data.length; i+=4) {
      visible ||= pixels.data[i+3] > 0;
      const gray = Math.round(.2126*pixels.data[i] + .7152*pixels.data[i+1] + .0722*pixels.data[i+2]);
      pixels.data[i] = pixels.data[i+1] = pixels.data[i+2] = gray;
    }
    if (generated && !visible) throw new Error(tr('模型生成了空白图标，请重新生成'));
    ctx.putImageData(pixels,0,0);
    edit({name, pixel_size:[canvas.width, canvas.height]}, color,
      {png:color.split(',')[1], mono_png:canvas.toDataURL('image/png').split(',')[1]}, generated);
  }

  function openGenerator() {
    shell('生成个人图标','');
    if (!generator) {
      generator = document.createElement('form');
      generator.innerHTML = `<fieldset><label>${tr('图标描述')} <textarea name="description" maxlength="2000" rows="3"></textarea></label>
        <label>${tr('风格')} <select name="style"><option value="line">${tr('线稿')}</option><option value="color">${tr('低饱和彩色')}</option></select></label>
        <label>${tr('生成方式')} <select name="engine"><option value="drawing">${tr('当前绘图模型（简洁图标）')}</option><option value="image">${tr('专用图像模型（设备插画）')}</option></select></label>
        <label hidden>${tr('图像模型名')} <input name="image_model" maxlength="160"></label>
        <div data-reference-images></div><button type="submit">${tr('生成候选图标')}</button></fieldset>`;
      const form = generator, engine = form.elements.engine, model = form.elements.image_model;
      engine.value = preferences.engine; model.value = preferences.image_model;
      const showModel = () => { model.parentElement.hidden = engine.value !== 'image'; model.required = !model.parentElement.hidden; };
      showModel();
      engine.onchange = model.onchange = () => {
        showModel();
        const imageModel = model.value.trim() || (engine.value === 'drawing' && preferences.image_model);
        if (!imageModel) return;
        const choice = {engine:engine.value, image_model:imageModel};
        // Save changes in click order, even when switching back before the first response.
        savingPreferences = savingPreferences.then(async () => {
          preferences = (await request('icon-preferences', choice)).preferences;
        }).catch(error => { if (form.isConnected) message(error.message); });
      };
      const refs = createReferenceImages(form,'iconReferenceFiles','iconReferenceThumbs',() => {},() => {});
      form.onsubmit = async event => {
        event.preventDefault();
        if (refs.pending) { message('正在处理参考图…'); return; }
        const serial = ++generation, before = key(), description = form.elements.description.value.trim();
        const fields = form.querySelector('fieldset');
        fields.disabled = true;
        message('正在生成候选图标…');
        try {
          const style = form.elements.namedItem('style').value;
          const result = await request('generate-icon', {description, style, engine:engine.value,
            image_model:model.value.trim(), references:refs.images});
          if (serial !== generation || !form.isConnected || !dialog.open) return;
          const canvas = document.createElement('canvas');
          if (result.png) {
            const image = new Image(); image.src = 'data:image/png;base64,' + result.png;
            await image.decode();
            canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
            canvas.getContext('2d', {willReadFrequently:true}).drawImage(image,0,0);
          } else {
            canvas.width = canvas.height = 512;
            const ctx = canvas.getContext('2d', {willReadFrequently:true});
            ctx.scale(canvas.width/100, canvas.height/100);
            ctx.lineJoin = ctx.lineCap = 'round'; ctx.lineWidth = 2; ctx.strokeStyle = '#27333c';
            const colors = {ink:'#27333c',white:'#fff',blue:'#abc6d5',green:'#b6c8ad',orange:'#e0bc98',gray:'#c9cdd0'};
            for (const part of result.paths) {
              const path = new Path2D(part.d);
              if (part.fill !== 'none') {
                ctx.fillStyle = style === 'line' && part.fill !== 'ink' ? '#fff' : colors[part.fill];
                ctx.fill(path);
              }
              ctx.stroke(path);
            }
          }
          if (serial !== generation || !dialog.open || !form.isConnected) return;
          if (before !== key()) throw new Error(tr('等待操作时图纸已改变，请重试。'));
          editCanvas(canvas, description.slice(0,120) || tr('个人图标'), true);
        } catch (error) {
          if (serial === generation && form.isConnected) message(error.message);
        } finally { fields.disabled = false; }
      };
    }
    dialog.querySelector('header').after(generator);
    window.cdI18n?.apply?.(generator);
    generator.elements.description.focus();
  }
  function library() {
    if (busy) return;
    shell('图片与个人图标', `${button('import','导入图片')} ${button('generator','AI 生成图标')}<p>${tr('支持 PNG、JPEG、WebP，可直接粘贴截图。导入后保存为 PNG。')}</p>
      <input type="search" aria-label="${esc(tr('搜索个人素材'))}" placeholder="${esc(tr('搜索个人素材'))}"><div class="figure-image-library"></div>`);
    run(async () => {
      await savingPreferences;
      const result = await request('image-library');
      preferences = result.preferences;
      const list = dialog.querySelector('.figure-image-library');
      list.innerHTML = result.items.map(item => `<button type="button" data-action="use" data-id="${esc(item.id)}">${esc(item.name)}</button>`).join('');
      dialog.querySelector('input[type=search]').oninput = event => {
        const query = event.target.value.trim().toLowerCase();
        for (const item of list.children) item.hidden = !item.textContent.toLowerCase().includes(query);
      };
      message(result.items.length ? '' : '个人素材库为空。导入图片时勾选收藏即可在其他图纸中使用。');
    });
  }
  function openImage(id) {
    if (busy) return;
    const item = snapshot().table.images?.find(image => image.id === id);
    if (!item) return;
    shell('图片属性', '');
    run(async () => edit(item, await preview(item.source)));
  }
  function draw(root, foreground) {
    const current = snapshot().table;
    const boxes = (control ? analysis : report)?.images || [];
    for (const item of current.images || []) {
      if ((item.foreground !== false) !== foreground) continue;
      const box = boxes.find(value => value.id === item.id)?.bounds;
      if (!box) continue;
      const parent = control ? el('g', {transform:'scale(1,-1)'}, root) : root;
      const node = el('image', {x:box[0],y:-box[3],width:box[2]-box[0],height:box[3]-box[1],
        preserveAspectRatio:'none', 'data-image-id':item.id, tabindex:0, role:'button',
        'aria-label':item.name || item.id}, parent);
      preview(item.source).then(url => { if (node.isConnected) node.setAttribute('href',url); }).catch(error => message(error.message));
      node.onpointerdown = event => event.stopPropagation();
      node.onclick = event => { event.stopPropagation(); openImage(item.id); };
      node.onkeydown = event => { if (event.key === 'Enter') { event.stopPropagation(); openImage(item.id); } };
    }
  }
  function paper(overlay, images, matrix) {
    const [sx,sy,dx,dy] = matrix;
    for (const image of images || []) {
      const [a,b,c,d] = image.bounds;
      const rect = el('rect', {x:a*sx+dx,y:d*sy+dy,width:(c-a)*sx,height:(b-d)*sy,
        fill:'transparent',stroke:'transparent','stroke-width':.025,
        'data-image-id':image.id,tabindex:0,role:'button','aria-label':image.id}, overlay);
      rect.onmouseenter = () => rect.setAttribute('stroke','#38659b');
      rect.onmouseleave = () => rect.setAttribute('stroke','transparent');
      rect.onkeydown = event => { if (event.key === 'Enter') { event.stopPropagation(); openImage(image.id); } };
      rect.onpointerdown = event => {
        if (event.button !== 0 || busy) return;
        event.stopImmediatePropagation();
        event.preventDefault();
        const before = key(), item = structuredClone(snapshot().table.images.find(value => value.id === image.id));
        const point = e => new DOMPoint(e.clientX,e.clientY).matrixTransform(overlay.getScreenCTM().inverse());
        const start = point(event), pixelStart = [event.clientX,event.clientY];
        let delta = [0,0], moved = false;
        rect.setPointerCapture(event.pointerId);
        rect.onpointermove = e => {
          const at = point(e);
          delta = [at.x-start.x,at.y-start.y];
          moved ||= Math.hypot(e.clientX-pixelStart[0],e.clientY-pixelStart[1]) > 3;
          rect.setAttribute('transform',`translate(${delta[0]},${delta[1]})`);
        };
        rect.onpointerup = e => {
          e.stopPropagation();
          rect.onpointermove = rect.onpointerup = rect.onpointercancel = null;
          rect.releasePointerCapture(e.pointerId);
          rect.removeAttribute('transform');
          if (!moved) { openImage(image.id); return; }
          run(async () => {
            if (before !== key()) throw new Error(tr('等待操作时图纸已改变，请重试。'));
            const shift = [delta[0]/sx,delta[1]/sy];
            for (const name of ['from','to']) item[name] = item[name].map((v,i) => v+shift[i]);
            const result = await request('save-image', {id:item.id,image:item});
            window.CircuitFigure.install(result.table,tr('移动图片'));
          });
        };
        rect.onpointercancel = () => {
          rect.onpointermove = rect.onpointerup = rect.onpointercancel = null;
          rect.removeAttribute('transform');
        };
      };
      rect.onclick = event => event.stopPropagation();
    }
  }
  const entry = document.createElement('button');
  entry.id = 'figureImagesEntry';
  entry.type = 'button';
  entry.textContent = tr('图片与个人图标');
  entry.onclick = () => { document.getElementById('systemSymbolLibrary').close(); library(); };
  document.querySelector('#systemSymbolLibrary header').after(entry);
  document.addEventListener('paste', event => {
    if (!window.CircuitFigure.isOpen() || busy || dialog.open ||
      event.target.closest('input,textarea,[contenteditable=true]')) return;
    const file = [...event.clipboardData.items].find(item => item.type.startsWith('image/'))?.getAsFile();
    if (file) { event.preventDefault(); event.stopImmediatePropagation(); loadFile(file); }
  }, true);
  const viewport = document.getElementById('figureViewport');
  viewport.addEventListener('dragover', event => {
    if (event.dataTransfer.types.includes('Files')) event.preventDefault();
  });
  viewport.addEventListener('drop', event => {
    if (!event.dataTransfer.files.length) return;
    event.preventDefault();
    if (!busy && !dialog.open) loadFile(event.dataTransfer.files[0]);
  });
  window.CircuitFigureImages = {draw, paper, open:library};
})();
