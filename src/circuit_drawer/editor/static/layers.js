/* Reorder source objects within their existing drawing band. No coordinates,
   extra z values or projected module children are written to the table. */
function layerSelection() {
  if (!layout || !selected || wireDraft || boxDraft) return null;
  const {type, index} = selected;
  if (!['regions', 'blocks', 'callouts', 'glyphs'].includes(type)) return null;
  const items = layout[type], item = items?.[index];
  if (!item) return null;
  const band = type === 'callouts' ? calloutLayer(item) : type;
  const indices = items.flatMap((candidate, i) =>
    type !== 'callouts' || calloutLayer(candidate) === band ? [i] : []);
  return {type, items, indices, position:indices.indexOf(index), band};
}

function layerDestination(action, state) {
  if (!state) return -1;
  const {position, indices} = state;
  return {raise:Math.min(position + 1, indices.length - 1), lower:Math.max(position - 1, 0),
    top:indices.length - 1, bottom:0}[action] ?? -1;
}

function reorderLayer(action) {
  const state = layerSelection(), destination = layerDestination(action, state);
  if (!state || destination < 0 || destination === state.position) return false;
  const {items, indices, position, type} = state;
  // Keep other bands' slots and relative order untouched, including mixed
  // block/line/wave and explicitly foreground arrows in the callouts list.
  const ordered = indices.map(i => items[i]);
  const [item] = ordered.splice(position, 1);
  ordered.splice(destination, 0, item);
  pushHistory();
  indices.forEach((slot, i) => { items[slot] = ordered[i]; });
  selected = {type, index:indices[destination]};
  showProps(); changed();
  return true;
}

function updateLayerControls() {
  const state = layerSelection();
  document.getElementById('menuLayers').disabled = !state;
  document.querySelectorAll('[data-layer-action]').forEach(button => {
    const destination = layerDestination(button.dataset.layerAction, state);
    button.disabled = !state || destination < 0 || destination === state.position;
  });
}

document.querySelectorAll('[data-layer-action]').forEach(button => {
  button.onclick = () => reorderLayer(button.dataset.layerAction);
});
document.addEventListener('editor-menu-open', updateLayerControls);
window.addEventListener('keydown', ev => {
  if (ev.defaultPrevented || document.querySelector('dialog[open]') || ev.target.closest('#wavePanel') ||
      ev.target.closest('input, textarea, select, [contenteditable="true"]')) return;
  if (!(ev.ctrlKey || ev.metaKey) || ev.altKey) return;
  const forward = ev.code === 'BracketRight', backward = ev.code === 'BracketLeft';
  if (!forward && !backward) return;
  ev.preventDefault(); ev.stopImmediatePropagation();
  reorderLayer(forward ? (ev.shiftKey ? 'top' : 'raise') : (ev.shiftKey ? 'bottom' : 'lower'));
});
updateLayerControls();
