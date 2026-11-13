// Both circuit AI dialogs keep their own images while sharing the input behavior.
function createReferenceImages(form, inputId, thumbsId, onChange, onPending) {
  const MAX_IMAGES = 3, MAX_EDGE = 2048;
  const MAX_DATA_LENGTH = 7_000_000; // Same base64 limit as editor.server._images.
  const area = document.createElement('section');
  area.className = 'reference-images'; area.tabIndex = 0;
  area.setAttribute('aria-label', '参考图');
  area.innerHTML = `<div>参考图（最多 3 张） <button type="button" data-ref-choose>选择图片…</button>
    <input id="${inputId}" type="file" accept="image/png,image/jpeg,image/webp" multiple hidden></div>
    <p class="hint">在本窗口粘贴或拖入 PNG、JPG、WebP 图片，也可选择文件。</p>
    <div id="${thumbsId}" class="reference-thumbs"></div>
    <p data-ref-status role="status" class="hint" hidden>正在处理参考图…</p>
    <div data-ref-errors role="alert" class="bad"></div>`;
  form.querySelector('[data-reference-images]').replaceWith(area);
  const input = area.querySelector('input'), thumbs = area.querySelector('.reference-thumbs');
  const status = area.querySelector('[data-ref-status]'), errors = area.querySelector('[data-ref-errors]');
  const items = [];
  let queue = Promise.resolve(), pending = 0;

  async function readImage(file) {
    const type = file.type || {png:'image/png', jpg:'image/jpeg', jpeg:'image/jpeg', webp:'image/webp'}[file.name.split('.').pop().toLowerCase()];
    if (!['image/png', 'image/jpeg', 'image/webp'].includes(type)) throw new Error('请选择 PNG、JPG 或 WebP 图片。');
    const objectURL = URL.createObjectURL(file), image = new Image();
    let dataURL;
    try {
      image.src = objectURL;
      try { await image.decode(); } catch { throw new Error('无法读取这张图片，请重新选择。'); }
      if (Math.max(image.naturalWidth, image.naturalHeight) > MAX_EDGE || Math.ceil(file.size / 3) * 4 >= MAX_DATA_LENGTH) {
        const ratio = Math.min(1, MAX_EDGE / Math.max(image.naturalWidth, image.naturalHeight));
        const canvas = document.createElement('canvas');
        canvas.width = Math.max(1, Math.round(image.naturalWidth * ratio));
        canvas.height = Math.max(1, Math.round(image.naturalHeight * ratio));
        canvas.getContext('2d').drawImage(image, 0, 0, canvas.width, canvas.height);
        dataURL = canvas.toDataURL(type, 0.9);
      } else {
        dataURL = await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(`data:${type};base64,${reader.result.split(',')[1]}`);
          reader.onerror = () => reject(new Error('无法读取这张图片，请重新选择。'));
          reader.readAsDataURL(file);
        });
      }
    } finally { URL.revokeObjectURL(objectURL); }
    if (!dataURL.startsWith('data:image/') || dataURL.split(',')[1].length >= MAX_DATA_LENGTH)
      throw new Error('图片缩小后仍超过大小限制，请裁剪图片后再添加。');
    return dataURL;
  }

  function render() {
    const restoreFocus = thumbs.contains(document.activeElement);
    thumbs.replaceChildren();
    for (const item of items) {
      const card = document.createElement('div'); card.className = 'reference-thumb';
      const image = document.createElement('img'); image.src = item.url; image.alt = item.name;
      const name = document.createElement('span'); name.textContent = item.name; name.setAttribute('translate', 'no');
      const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = '移除图片';
      remove.setAttribute('aria-label', `移除图片 ${item.name}`);
      remove.onclick = () => { items.splice(items.indexOf(item), 1); onChange(); render(); };
      card.append(image, name, remove); thumbs.append(card);
    }
    if (restoreFocus) area.focus();
  }

  function add(files) {
    if (!files.length) return;
    errors.replaceChildren();
    ++pending; status.hidden = false; onChange(); onPending();
    // Serialize all three input methods so quick repeated pastes share the limit.
    queue = queue.then(async () => {
      for (const file of files) {
        try {
          if (items.length >= MAX_IMAGES) throw new Error('最多添加 3 张参考图，请先移除一张。');
          const url = await readImage(file);
          items.push({name:file.name, url}); onChange(); render();
        } catch (error) {
          const message = document.createElement('p');
          const name = document.createElement('span'); name.textContent = file.name; name.setAttribute('translate', 'no');
          message.append(name, document.createTextNode(' '), document.createTextNode(error.message)); errors.append(message);
        }
      }
    }).finally(() => { --pending; status.hidden = pending === 0; onPending(); });
  }

  area.querySelector('[data-ref-choose]').onclick = () => input.click();
  input.onchange = () => { const files = [...input.files]; input.value = ''; add(files); };
  const dialog = form.closest('dialog');
  dialog.addEventListener('paste', event => {
    if (!form.isConnected) return;
    const files = [...(event.clipboardData?.items || [])]
      .filter(item => item.kind === 'file' && item.type.startsWith('image/')).map(item => item.getAsFile()).filter(Boolean);
    if (!files.length) return;
    event.preventDefault(); add(files);
  });
  dialog.addEventListener('dragover', event => {
    if (!form.isConnected || !event.dataTransfer.types.includes('Files')) return;
    event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; area.classList.add('dragover');
  });
  dialog.addEventListener('dragleave', event => {
    if (!dialog.contains(event.relatedTarget)) area.classList.remove('dragover');
  });
  dialog.addEventListener('drop', event => {
    if (!form.isConnected || !event.dataTransfer.types.includes('Files')) return;
    event.preventDefault(); area.classList.remove('dragover'); add([...event.dataTransfer.files]);
  });
  return {get images() { return items.map(item => item.url); }, get pending() { return pending > 0; }};
}
