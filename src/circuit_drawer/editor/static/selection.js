// Editor-only selection transactions; no derived selection geometry is saved.
let selectionBusy = false, spacePan = false, panGesture = null;
const selectedParts = () => layout.elements.filter(e => moduleSelection.has(e.id));
const shiftedPoint = (p, d) => p.map((v, k) => r4(v + d[k]));
const objectSelection = Object.fromEntries(['wire','ann','regions','blocks','callouts','glyphs'].map(k=>[k,new Set()]));
const instanceSelection = new Set();
const selectionSection = type => ({wire:'wires',ann:'annotations'})[type] || type;
const selectionCount = () => instanceSelection.size + moduleSelection.size + Object.values(objectSelection).reduce((n,s)=>n+s.size,0);
function clearGroupSelection() { moduleSelection.clear(); instanceSelection.clear(); Object.values(objectSelection).forEach(s=>s.clear()); }
function groupHas(ref) { return !!ref && (ref.type==='instance'?instanceSelection.has(ref.id):ref.type==='el'?moduleSelection.has(ref.id):objectSelection[ref.type]?.has(ref.index)); }
function groupAdd(ref) { if(ref?.type==='instance')instanceSelection.add(ref.id); else if(ref?.type==='el')moduleSelection.add(ref.id); else objectSelection[ref?.type]?.add(ref.index); }
function groupToggle(ref) {
  const set=ref.type==='instance'?instanceSelection:ref.type==='el'?moduleSelection:objectSelection[ref.type], key=['el','instance'].includes(ref.type)?ref.id:ref.index;
  if(set.has(key))set.delete(key);else set.add(key);
}
function selectedObjects(source=layout) {
  return Object.entries(objectSelection).flatMap(([type,indices])=>[...indices].flatMap(index=>{
    const item=source[selectionSection(type)]?.[index]; return item?[{type,index,item}]:[];
  }));
}
function sourceObjectIndex(type,index) {
  if(!layout.modules)return index;
  return type==='wire'?viewInfo().root_wires?.[index]:viewInfo().root_decorations?.[selectionSection(type)]?.[index];
}
function objectAtTarget(target) {
  const part=target.closest('g.element[data-id]')?.dataset.id;
  if(part && layout.elements.some(e=>e.id===part))return {type:'el',id:part};
  for(const [key,type] of [['wire','wire'],['ann','ann'],['region','regions'],['block','blocks'],['callout','callouts'],['glyph','glyphs']]) {
    const node=target.closest(`[data-${key}]`); if(!node)continue;
    const index=sourceObjectIndex(type,+node.dataset[key]);
    if(index!==undefined && layout[selectionSection(type)]?.[index])return {type,index};
  }
  return null;
}
function selectionCandidates() {
  const candidates=layout.elements.map(e=>({type:'el',id:e.id,points:Object.values(e.pins)}));
  for (const [id,b] of Object.entries(viewInfo().frames || {}))
    candidates.push({type:'instance',id,points:[[b[0],b[1]],[b[2],b[3]]]});
  layout.wires.forEach((w,index)=>candidates.push({type:'wire',index,points:w}));
  svg.querySelectorAll('g[data-ann],g[data-region],g[data-block],g[data-callout],g[data-glyph]').forEach(node=>{
    const ref=objectAtTarget(node);if(!ref)return;
    const b=node.getBBox(); candidates.push({...ref,points:[[b.x,-b.y],[b.x+b.width,-b.y-b.height]]});
  });
  return candidates;
}
function selectAllObjects() {
  clearGroupSelection();selectionCandidates().forEach(groupAdd);selected=null;focusedModuleId=null;focusedLink=null;
  showSidebar('properties');showProps();render();
}
function drawGroupSelection() {
  if(!selectionCount())return;
  const overlay=el('g',{id:'groupSelectionOverlay','pointer-events':'none'},svg);
  for (const id of instanceSelection) {
    const b=boxRect(id); if(!b)continue;
    el('rect',{'data-selected-instance':id,x:b[0],y:-b[3],width:b[2]-b[0],height:b[3]-b[1],fill:'var(--accent-soft)',
      stroke:'var(--accent)','fill-opacity':.15,'stroke-width':.035,'stroke-dasharray':'.12 .06'},overlay);
  }
  for(const {type,index,item} of selectedObjects()) {
    if(type==='wire') {el('path',{d:wirePath(item),fill:'none',stroke:'var(--accent)','stroke-width':.045},overlay);continue;}
    const key={ann:'ann',regions:'region',blocks:'block',callouts:'callout',glyphs:'glyph'}[type];
    const node=[...svg.querySelectorAll(`g[data-${key}]`)].find(n=>sourceObjectIndex(type,+n.dataset[key])===index);
    if(!node)continue;const b=node.getBBox();
    el('rect',{x:b.x,y:b.y,width:b.width,height:b.height,fill:'none',stroke:'var(--accent)','stroke-width':.035,'stroke-dasharray':'.12 .06'},overlay);
  }
}
function groupMoveReason() {
  return selectedObjects().some(({item})=>item.fit && !item.members?.every(id=>moduleSelection.has(id)))
    ?'贴合区域随成员移动；请一起选中全部成员，或先在属性栏固定边界':'';
}
function shiftDecoration(item,delta) {
  if(item.fit)return;
  for(const key of ['at','from','to'])if(Array.isArray(item[key]))item[key]=shiftedPoint(item[key],delta);
  if(item.via)item.via=item.via.map(p=>shiftedPoint(p,delta));
}
function groupRotateReason() {
  if (instanceSelection.size) return '模块请进入定义后旋转内部元件';
  if (!moduleSelection.size || selectedObjects().some(o=>o.type!=='wire')) return '请选中元件及导线后一起旋转';
  return '';
}
function rotateGroup(direction = 1) {
  const reason=groupRotateReason();if(reason){flash(reason,true);return;}
  return groupTransaction((parts,plan,source,objects)=>{
    const points=[...parts.flatMap(e=>Object.values(e.pins)),...plan.runs.filter(r=>r.inside).flatMap(r=>r.wire)];
    const center=[0,1].map(k=>snap((Math.min(...points.map(p=>p[k]))+Math.max(...points.map(p=>p[k])))/2));
    const turn=p=>[r4(center[0]+direction*(p[1]-center[1])),r4(center[1]-direction*(p[0]-center[0]))];
    pushHistory();
    for (const original of parts) {
      const part=layout.elements.find(e=>e.id===original.id);
      part.pins=Object.fromEntries(Object.entries(original.pins).map(([pin,p])=>[pin,turn(p)]));
      if (part.label_pos) part.label_pos=(direction===1?TURN:TURN_BACK)[part.label_pos];
      if (part.opts?.label_offset) {const [x,y]=original.opts.label_offset;part.opts.label_offset=[direction*y,-direction*x];}
    }
    layout.wires=planWires(plan,[0,0],turn);layout.hops=planHops(plan,layout.wires,[0,0],turn);
    const chosen=new Set(objects.filter(o=>o.type==='wire').map(o=>o.index));
    objectSelection.wire.clear();plan.runs.forEach((r,i)=>{if(chosen.has(r.source))objectSelection.wire.add(i);});
    changed();
  });
}

function syncSelectionControls() {
  const ids = new Set(layout.elements.map(e => e.id));
  for (const id of moduleSelection) if (!ids.has(id)) moduleSelection.delete(id);
  for (const id of instanceSelection) if (!viewInfo().frames?.[id]) instanceSelection.delete(id);
  for(const [type,indices] of Object.entries(objectSelection))for(const i of indices)if(!layout[selectionSection(type)]?.[i])indices.delete(i);
  const parts = selectedParts(), count=selectionCount(), canCopy = !!count || ['el','wire','ann','regions','blocks','callouts','glyphs'].includes(selected?.type);
  document.getElementById('del').disabled = selectionBusy || !!instanceSelection.size || !(count || selected);
  for (const id of ['cutSelection', 'copySelection', 'duplicateSelection'])
    document.getElementById(id).disabled = selectionBusy || !!instanceSelection.size || !canCopy;
  document.getElementById('pasteSelection').disabled = selectionBusy || !clipboard;
  document.getElementById('mergeOverlaps').disabled = wireMergeBusy || selectionBusy || !layout.wires.length;
  for (const id of ['rotateSelection','rotateSelectionCCW'])
    document.getElementById(id).disabled = selectionBusy || (count>1 ? !!groupRotateReason() : selected?.type!=='el' && parts.length!==1);
  for (const id of ['mirrorSelection', 'growSymbol', 'shrinkSymbol'])
    document.getElementById(id).disabled = selectionBusy || count > 1 || (selected?.type !== 'el' && parts.length !== 1);
  if (count && !selected) moduleSelectionStatus();
}
document.addEventListener('editor-menu-open', syncSelectionControls);

function circuitScaleSelection() {
  const chosen = {elements:[...moduleSelection],wires:[...objectSelection.wire]};
  for (const [type, indices] of Object.entries(objectSelection)) chosen[selectionSection(type)] = [...indices];
  if (instanceSelection.size) chosen.instances = [...instanceSelection];
  if (!selectionCount() && selected) {
    if (selected.type === 'el') chosen.elements.push(selected.id);
    else if (selected.type === 'modulePart') chosen.parts = [selected.id];
    else if (selected.type === 'wave_symbols') chosen.wave_symbols = [selected.index];
    else if (selected.type in objectSelection) chosen[selectionSection(selected.type)].push(selected.index);
  }
  if (!selectionCount() && focusedModuleId) chosen.instances = [focusedModuleId];
  return Object.values(chosen).some(items=>items.length) ? chosen : null;
}
function circuitNetGroups(nets) {
  const groups = new Map();
  for (const [pin, net] of Object.entries(nets || {})) {
    if (!groups.has(net)) groups.set(net, []);
    groups.get(net).push(pin);
  }
  return JSON.stringify([...groups.values()].map(pins=>pins.sort()).sort((a,b)=>JSON.stringify(a).localeCompare(JSON.stringify(b))));
}
document.getElementById('scaleCircuit').onclick = () => {
  const before = editorTableSnapshot(layout), source = clone(layout), requestedProfile = profile;
  const selection = circuitScaleSelection();
  showDialog('整体等比缩放', `<p>以选区中心为基准，同时调整元件、引脚、导线和间距。字号可同步缩放，线宽保持原值。</p>
    <label>范围 <select id="circuitScaleScope"><option value="selected">当前选区</option><option value="all">整张电路</option></select></label>
    <label>缩放倍率 <input id="circuitScaleFactor" type="number" value="0.8" min="0.01" step="0.1"></label>
    <label><input id="circuitScaleText" type="checkbox" checked>同步缩放字号</label>
    <p class="hint">0.8 表示缩小到 80%。缩放后固定当前图纸比例，避免自动适配抵消改变；过小或超出栏宽会提示。</p>
    <p><button id="circuitScalePreview">预览缩放</button> <button id="circuitScaleApply" disabled>应用到当前图</button>
    <button id="circuitScaleAuto">恢复自动适配</button></p>
    <p id="circuitScaleState" role="status"></p><img id="circuitScaleImage" alt="缩放预览" hidden style="max-width:100%;max-height:55vh;object-fit:contain">`);
  const scope = document.getElementById('circuitScaleScope'), factor = document.getElementById('circuitScaleFactor');
  const fonts = document.getElementById('circuitScaleText'), preview = document.getElementById('circuitScalePreview');
  const apply = document.getElementById('circuitScaleApply'), reset = document.getElementById('circuitScaleAuto');
  const state = document.getElementById('circuitScaleState'), image = document.getElementById('circuitScaleImage');
  scope.value = selection ? 'selected' : 'all'; scope.querySelector('[value="selected"]').disabled = !selection;
  reset.disabled = !source.drawing_scale;
  let serial = 0, candidate = null;
  const active = version => dlg.open && apply.isConnected && version === serial;
  const unchanged = () => before === editorTableSnapshot(layout) && profile === requestedProfile;
  const invalidate = () => { ++serial; candidate=null; apply.disabled=true; preview.disabled=false; image.hidden=true; state.textContent=''; };
  for (const input of [scope,factor,fonts]) input.oninput = invalidate;
  preview.onclick = async () => {
    invalidate(); const version = serial;
    preview.disabled=true; state.className='hint'; state.textContent='校验中…';
    try {
      const prepared = await api('/api/scale-drawing', {layout:source, profile:requestedProfile,
        selection:scope.value==='all'?null:selection, factor:+factor.value, text:fonts.checked});
      if (!active(version)) return;
      const proposed=prepared.layout, point=p=>p.map((v,k)=>r4(prepared.anchor[k]+(v-prepared.anchor[k])*prepared.factor));
      if (prepared.factor !== 1) {
        proposed.wires=planWires(prepared.plan,[0,0],point);
        proposed.hops=prepared.all ? source.hops.map(point) : planHops(prepared.plan,proposed.wires,[0,0],point);
      }
      const checked=await api('/api/check',{layout:proposed,profile:requestedProfile});
      if (!active(version)) return;
      if (!checked.ok) throw new Error(checked.issues.filter(i=>i.level==='error').map(i=>i.message).join('\n'));
      if (circuitNetGroups(checked.nets)!==circuitNetGroups(prepared.before_nets)) throw new Error('缩放改变了电气连接，请调整倍率或扩大选区。');
      const result=await api('/api/preview',{layout:proposed,profile:requestedProfile});
      if (!active(version)) return;
      if (!unchanged()) throw new Error('图纸已变化，请重新打开缩放窗口。');
      image.src='data:image/png;base64,'+result.png; image.hidden=false;
      candidate=proposed; apply.disabled=false; state.textContent='预览已就绪；应用后可一步撤销。';
    } catch(error) { if(active(version)){state.className='bad';state.textContent=error.message;} }
    finally { if(active(version)) preview.disabled=false; }
  };
  apply.onclick=()=>{
    if(!candidate || !unchanged()){state.textContent='图纸已变化，请重新打开缩放窗口。';apply.disabled=true;return;}
    if(editorTableSnapshot(candidate)!==before){pushHistory();layout=candidate;selected=null;clearGroupSelection();changed(false);}
    dlg.close();
  };
  reset.onclick=()=>{
    if(!unchanged()){state.textContent='图纸已变化，请重新打开缩放窗口。';return;}
    pushHistory();delete layout.drawing_scale;changed(false);dlg.close();
  };
};

function startCanvasPan(ev) {
  ev.preventDefault(); ev.stopImmediatePropagation();
  if (!layout || drag || moduleBox || resizing || instanceDrag || linkDrag) return;
  svg.focus({preventScroll:true});
  panGesture = {pointer:ev.pointerId, start:[ev.clientX, ev.clientY], view:{...view}, scale:1 / Math.hypot(svg.getScreenCTM().a,svg.getScreenCTM().b)};
  svg.setPointerCapture(ev.pointerId); svg.style.cursor = 'grabbing';
}
window.addEventListener('pointermove', ev => {
  if (!panGesture || ev.pointerId !== panGesture.pointer) return;
  ev.preventDefault(); ev.stopImmediatePropagation();
  const {start, view:v, scale:k} = panGesture;
  view.x0 = v.x0 - (ev.clientX - start[0]) * k;
  view.y0 = v.y0 + (ev.clientY - start[1]) * k;
  applyView();
}, true);
function endCanvasPan(ev) {
  if (!panGesture || ev && ev.pointerId !== panGesture.pointer) return;
  ev?.stopImmediatePropagation();
  if (svg.hasPointerCapture(panGesture.pointer)) svg.releasePointerCapture(panGesture.pointer);
  panGesture = null; svg.style.cursor = '';
}
window.addEventListener('pointerup', endCanvasPan, true);
window.addEventListener('pointercancel', endCanvasPan, true);
window.addEventListener('blur', () => { spacePan = false; endCanvasPan(); });
window.addEventListener('keyup', ev => { if (ev.code === 'Space') spacePan = false; }, true);
window.addEventListener('keydown', ev => {
  if (ev.code !== 'Space' || ev.ctrlKey || ev.metaKey || ev.altKey ||
      ev.target.closest('input, textarea, select, button, a, [contenteditable="true"], [role="menu"]') ||
      document.querySelector('dialog[open]')) return;
  ev.preventDefault(); spacePan = true;
}, true);

function planWires(plan, delta, transform = p => shiftedPoint(p, delta)) {
  const moves = p => plan.moving.some(q => same(p, q)) && !plan.fixed.some(q => same(p, q));
  const wires = plan.runs.map(run => {
    if (run.inside) return run.wire.map(transform);
    let w = clone(run.wire);
    if (w.length < 2) return w;
    // Highest endpoint first; inserted corners never change the first index.
    if (moves(run.wire.at(-1))) w = moveWirePoint(w, w.length - 1, transform(run.wire.at(-1)));
    if (moves(run.wire[0])) w = moveWirePoint(w, 0, transform(run.wire[0]));
    return w;
  });
  for (const p of plan.fixed) if (!same(p, transform(p))) wires.push(moveWirePoint([p, p.slice()], 1, transform(p)));
  return wires;
}
function planHops(plan, wires, delta, transform = p => shiftedPoint(p, delta)) {
  return plan.hops.flatMap(marker => {
    if (!marker.runs.some(i => plan.runs[i].inside)) return [marker.at];
    if (marker.runs.every(i => plan.runs[i].inside)) return [transform(marker.at)];
    // One crossing wire moved, the other stayed: keep the bridge at their
    // actual crossing. If they no longer cross, the bridge is no longer needed.
    const candidates = [];
    for (const i of marker.runs) for (const j of marker.runs) if (i < j)
      candidates.push(...wireIntersections(wires[i], wires[j]));
    candidates.sort((a,b) => Math.hypot(a[0]-marker.at[0],a[1]-marker.at[1])-Math.hypot(b[0]-marker.at[0],b[1]-marker.at[1]));
    return candidates.slice(0,1);
  });
}

function startGroupDrag(ev) {
  if (selectionBusy) return;
  ev.preventDefault(); ev.stopImmediatePropagation(); svg.setPointerCapture(ev.pointerId);
  svg.focus({preventScroll:true});
  const reason=groupMoveReason();if(reason){flash(reason);svg.releasePointerCapture(ev.pointerId);return;}
  selected = null;
  const before = clone(layout), parts = clone(selectedParts()), objects=selectedObjects(before), start = toUnits(ev);
  const instances=[...instanceSelection];
  const d = {group:true, source:before, members:[...instances.map(id=>({type:'instance',id})),...parts.map(e=>({type:'el',id:e.id})),...objects.map(({type,index})=>({type,index}))],moved:false, delta:[0,0], move(p) {
    this.delta = p.map((v,k) => r4(snap(v-start[k])));
    if (!this.plan || !this.moved && same(this.delta,[0,0])) return;
    if (!this.moved) { pushHistory(); this.moved = true; }
    applyGroupMove(parts,objects,this.plan,this.delta);
  }};
  drag = d; selectionBusy = true; syncSelectionControls();
  d.ready = api('/api/selection-plan',{layout:before, profile, ids:parts.map(e=>e.id),instances,wires:objects.filter(o=>o.type==='wire').map(o=>o.index)})
    .then(plan => {
      if (drag !== d) return;
      if (editorTableSnapshot(layout) !== editorTableSnapshot(before)) { d.error='图纸已变化，请重新选择后拖动'; return; }
      d.plan = plan; d.move(start.map((v,k)=>v+d.delta[k])); render();
    })
    .catch(e => { d.error = e.message; });
}
async function finishGroupDrag(d) {
  await d.ready;
  if (drag !== d) return;
  drag = null; selectionBusy = false;
  if (d.error) flash(d.error,true);
  if (d.moved) changed(); else render();
}
function cancelGroupDrag() {
  const d = drag; if (!d?.group) return;
  drag = null; selectionBusy = false;
  if (d.moved) { layout = d.source; selected = null; clearGroupSelection();d.members.forEach(groupAdd);history.pop(); changed(); } else render();
}
window.addEventListener('pointercancel', cancelGroupDrag);
window.addEventListener('blur', cancelGroupDrag);
document.addEventListener('keydown', ev => { if (ev.key === 'Escape') cancelGroupDrag(); });

async function groupTransaction(action, moveInstances = false) {
  if (selectionBusy || drag) return false;
  if (instanceSelection.size && !moveInstances) { flash('此操作请先取消选择模块实例'); return false; }
  const parts = clone(selectedParts()); if (!selectionCount()) return false;
  const before = editorTableSnapshot(layout), source = clone(layout);
  const objects=selectedObjects(source);
  selectionBusy = true; syncSelectionControls();
  try {
    const plan = await api('/api/selection-plan',{layout:source,profile,ids:parts.map(e=>e.id),instances:[...instanceSelection],wires:objects.filter(o=>o.type==='wire').map(o=>o.index)});
    if (editorTableSnapshot(layout) !== before) { flash('图纸已变化，请重新选择后操作'); return false; }
    action(parts, plan, source, objects); return true;
  } catch (e) { flash(e.message,true); return false; }
  finally { selectionBusy = false; syncSelectionControls(); }
}
function applyGroupMove(parts,objects,plan,delta) {
  for (const inst of layout.modules?.instances || []) {
    const origin=plan.origins?.[inst.id];
    if (origin && plan.instances.length) inst.at=plan.instances.includes(inst.id)?shiftedPoint(origin,delta):origin.slice();
  }
  for (const [key,route] of Object.entries(plan.routes || {})) layout.modules.routes[key]=route.map(p=>shiftedPoint(p,delta));
  for(const original of parts)layout.elements.find(e=>e.id===original.id).pins=
    Object.fromEntries(Object.entries(original.pins).map(([k,p])=>[k,shiftedPoint(p,delta)]));
  for(const {type,index,item} of objects)if(type!=='wire'){
    const copy=clone(item);shiftDecoration(copy,delta);layout[selectionSection(type)][index]=copy;
  }
  layout.wires=planWires(plan,delta);layout.hops=planHops(plan,layout.wires,delta);
  const chosen=new Set(objects.filter(o=>o.type==='wire').map(o=>o.index));
  objectSelection.wire.clear();plan.runs.forEach((r,i)=>{if(chosen.has(r.source))objectSelection.wire.add(i);});
}
function nudgeGroup(delta) {
  const reason=groupMoveReason();if(reason){flash(reason);return;}
  return groupTransaction((parts,plan,source,objects)=>{pushHistory();applyGroupMove(parts,objects,plan,delta);changed();}, true);
}
function groupClipboard(parts, plan, source, cut, objects) {
  const ids = new Set(parts.map(e=>e.id)), belongs = ref => ids.has(ref.slice(0,ref.lastIndexOf('.')));
  return {group:true, elements:parts, wires:plan.runs.filter(r=>r.inside).map(r=>r.wire),
    hops:plan.hops.filter(h=>h.runs.length && h.runs.every(i=>plan.runs[i].inside)).map(h=>h.at),
    netlist:Object.fromEntries(Object.entries(source.netlist || {}).filter(([ref])=>belongs(ref))),
    parameters:Object.fromEntries(Object.entries(source.simulation?.components || {}).filter(([id])=>ids.has(id))),
    no_connect:(source.no_connect || []).filter(belongs),
    decorations:objects.filter(o=>o.type!=='wire').map(({type,item})=>({type,item})),
    stack_usage:Object.fromEntries(objects.filter(o=>o.type==='regions' && source.stack_usage?.[o.item.id]).map(o=>[o.item.id,source.stack_usage[o.item.id]])),
    cut, side: cut ? sideOf(ids, objects.filter(o=>o.type==='regions').map(o=>o.index)) : null};
}
function copyGroup(cut = false) {
  return groupTransaction((parts,plan,source,objects) => {
    clipboard = groupClipboard(parts,plan,source,cut,objects);
    const count=selectionCount();if (cut) removeGroup(parts,plan,source,objects);
    flash(`已${cut?'剪切':'复制'} ${count} 个对象及组内导线；Ctrl+V 粘贴`);
  });
}
// What the table says about some parts besides their own entries. A cut keeps it
// so the first paste puts everything back; a copy carries parameters and nets over.
function sideOf(ids, explicitRegions = []) {
  const belongs = ref => typeof ref === 'string' && ids.has(ref.slice(0,ref.lastIndexOf('.')));
  const emptied = (layout.regions || []).flatMap((r,i) => !explicitRegions.includes(i) && r.fit && r.members?.length && r.members.every(id=>ids.has(id)) ? [[i,clone(r)]] : []);
  return {
    netlist: Object.fromEntries(Object.entries(layout.netlist || {}).filter(([ref])=>belongs(ref))),
    parameters: clone(Object.fromEntries(Object.entries(layout.simulation?.components || {}).filter(([id])=>ids.has(id)))),
    no_connect: (layout.no_connect || []).filter(belongs),
    probes: clone((layout.simulation?.probes || []).filter(p=>ids.has(p.element) || belongs(p.positive) || belongs(p.negative))),
    links: clone((layout.modules?.links || []).filter(pair=>pair.some(belongs))),
    members: (layout.regions || []).flatMap((r,i) => !explicitRegions.includes(i) && r.members?.some(id=>ids.has(id)) && !emptied.some(([j])=>j===i)
      ? [[r.id ?? i, r.members.filter(id=>ids.has(id))]] : []),
    regions: emptied,
  };
}
// Put back what a cut took, when the parts came back under their own ids.
function restoreSide(side) {
  for (const [ref, net] of Object.entries(side.netlist)) (layout.netlist ||= {})[ref] = net;
  if (Object.keys(side.parameters).length) { layout.simulation ||= {}; layout.simulation.components ||= {};
    Object.assign(layout.simulation.components, clone(side.parameters)); }
  if (side.no_connect.length) layout.no_connect = [...new Set([...(layout.no_connect || []), ...side.no_connect])];
  if (side.probes.length) { layout.simulation ||= {}; layout.simulation.probes = [...(layout.simulation.probes || []), ...clone(side.probes)]; }
  if (side.links.length && layout.modules) layout.modules.links = [...(layout.modules.links || []), ...clone(side.links)];
  for (const [i, region] of side.regions) (layout.regions ||= []).splice(Math.min(i, layout.regions.length), 0, clone(region));
  for (const [key, ids] of side.members) {
    const r = typeof key === 'number' ? layout.regions?.[key] : layout.regions?.find(x=>x.id === key);
    if (r) r.members = [...new Set([...(r.members || []), ...ids])];
  }
}
// A copy's nets are its own, named after the original's root (OUT -> OUT_copy2).
function copyNetNames(nets) {
  const used = new Set(Object.values(layout.netlist || {})), names = {};
  for (const net of nets) if (!(net in names)) {
    const root = net.replace(/_copy\d+$/, ''); let n = 1;
    while (used.has(`${root}_copy${n}`)) n++;
    used.add(names[net] = `${root}_copy${n}`);
  }
  return names;
}
// Everything else in the table that names deleted parts goes with them, so a new
// part that reuses an id starts clean (single delete, cut and group delete alike).
function forgetElements(ids) {
  const belongs = ref => typeof ref === 'string' && ids.has(ref.slice(0,ref.lastIndexOf('.')));
  for (const r of layout.regions || []) if (Array.isArray(r.members)) {
    r.members = r.members.filter(id=>!ids.has(id));
    if (!r.members.length) delete r.members;
  }
  // a region fitted to its members cannot outlive all of them
  if (layout.regions) layout.regions = layout.regions.filter(r=>!(r.fit && !r.members));
  layout.elements = layout.elements.filter(e=>!ids.has(e.id));
  if (layout.modules?.parts) layout.modules.parts = layout.modules.parts.filter(p=>!ids.has(p.id));
  for (const ref of Object.keys(layout.netlist || {})) if (belongs(ref)) delete layout.netlist[ref];
  if (layout.no_connect) layout.no_connect = layout.no_connect.filter(ref=>!belongs(ref));
  for (const id of ids) if (layout.simulation?.components) delete layout.simulation.components[id];
  if (layout.simulation?.probes) layout.simulation.probes = layout.simulation.probes.filter(p=>
    !ids.has(p.element) && !belongs(p.positive) && !belongs(p.negative));
  if (layout.modules?.links) layout.modules.links = layout.modules.links.filter(pair=>!pair.some(belongs));
}
// Points where something attaches: pins, hops, every wire end, and points lying on
// another wire's segment. A vertex or a meeting of two ends anywhere else is loose.
function attachments(wires, skip = -1) {
  const key = p => p.map(v => v.toFixed(4)).join(',');
  const fixed = new Set([...layout.elements.flatMap(e => Object.values(e.pins)), ...(layout.hops || [])].map(key));
  const onSegment = (p, w) => w.some((a, i) => i + 1 < w.length && (
    Math.abs(a[0] - w[i+1][0]) < 1e-6 && Math.abs(p[0] - a[0]) < 1e-6 && (p[1] - a[1]) * (p[1] - w[i+1][1]) < -1e-12 ||
    Math.abs(a[1] - w[i+1][1]) < 1e-6 && Math.abs(p[1] - a[1]) < 1e-6 && (p[0] - a[0]) * (p[0] - w[i+1][0]) < -1e-12));
  return {key, fixed, touches: (p, except) => wires.some((w, i) => !except.includes(i) && i !== skip &&
    (w.some(q => key(q) === key(p)) || onSegment(p, w)))};
}
// A group edit splits the wires it touches at pins and joints. Where such a split
// point is no longer used, the runs become one wire again and the point goes;
// the user's own wires, ends and vertices stay as drawn.
function rejoinWires(wires, before) {
  const key = p => p.map(v => v.toFixed(4)).join(',');
  const drawnEnds = new Set(before.flatMap(w => [key(w[0]), key(w.at(-1))]));
  const drawn = new Set(before.flat().map(key));
  let out = wires.map(w => w.map(p => p.slice()));
  for (let again = true; again;) {
    again = false;
    const {fixed, touches} = attachments(out), ends = new Map();
    out.forEach((w, i) => [[0, w[0]], [w.length - 1, w.at(-1)]].forEach(([j, p]) =>
      ends.set(key(p), [...(ends.get(key(p)) || []), [i, j]])));
    for (const [k, list] of ends) {
      if (list.length !== 2 || list[0][0] === list[1][0] || fixed.has(k) || drawnEnds.has(k)) continue;
      const [[a, ja], [b, jb]] = list, p = out[a][ja];
      if (touches(p, [a, b])) continue;
      const left = ja === 0 ? out[a].slice().reverse() : out[a], right = jb === 0 ? out[b] : out[b].slice().reverse();
      const joined = [...left, ...right.slice(1)];
      out = out.filter((_, i) => i !== a && i !== b); out.push(joined); again = true; break;
    }
  }
  const flat = (a, b, c) => Math.abs(a[0] - b[0]) < 1e-6 && Math.abs(b[0] - c[0]) < 1e-6 ||
    Math.abs(a[1] - b[1]) < 1e-6 && Math.abs(b[1] - c[1]) < 1e-6;
  return out.map((w, n) => { const {fixed, touches} = attachments(out, n);
    return w.filter((p, i) => i === 0 || i === w.length - 1 || drawn.has(key(p)) || !flat(w[i-1], p, w[i+1]) ||
      fixed.has(key(p)) || touches(p, [])); });
}
function removeGroup(parts,plan,source,objects=[]) {
  pushHistory();
  // Remove selected decorations before member cleanup can change their indices.
  for(const type of Object.keys(objectSelection).filter(t=>t!=='wire')) {
    const removed=objects.filter(o=>o.type===type).sort((a,b)=>b.index-a.index);
    for(const {index,item} of removed){layout[selectionSection(type)].splice(index,1);
      if(type==='regions' && item.id && layout.stack_usage)delete layout.stack_usage[item.id];}
  }
  forgetElements(new Set(parts.map(e=>e.id)));
  layout.wires = rejoinWires(plan.runs.filter(r=>!r.inside).map(r=>r.wire), layout.wires);
  layout.hops = plan.hops.filter(h=>!h.runs.every(i=>plan.runs[i].inside)).map(h=>h.at);
  clearGroupSelection(); selected = null; showProps(); changed();
}
function pastePositionReason() {
  const ids=new Set((clipboard?.elements||[]).map(e=>e.id));
  return clipboard?.item?.fit || clipboard?.decorations?.some(({item})=>item.fit && !item.members?.every(id=>ids.has(id)))
    ?'随成员贴合的区域需一起复制全部成员，或先固定边界':'';
}
function pasteGroup(at = null) {
  if (selectionBusy) return;
  if(at && pastePositionReason()){flash(pastePositionReason());return;}
  const clip = clone(clipboard);
  const points=[...clip.elements.flatMap(e=>Object.values(e.pins)),...clip.wires.flat(),
    ...(clip.decorations||[]).flatMap(({item})=>[item.at,item.from,item.to,...(item.via||[])].filter(Array.isArray))];
  const delta = at && points.length ? pasteDelta(points,at) : clip.cut ? [0,0] : PASTE_STEP;
  const used = new Set([...layout.elements.map(e=>e.id),...(layout.modules?.instances || []).map(e=>e.id)]), mapping = {};
  for (const e of clip.elements) {
    const old = e.id, prefix = old.replace(/\d+$/, '') || old;
    let name = old;
    for (let n=1; used.has(name); n++) name = prefix+n;
    used.add(name); mapping[old] = name; e.id = name;
    renameCopiedLabel(e, old);
    e.pins = Object.fromEntries(Object.entries(e.pins).map(([k,p])=>[k,shiftedPoint(p,delta)]));
  }
  const ref = r => mapping[r.slice(0,r.lastIndexOf('.'))]+r.slice(r.lastIndexOf('.'));
  const netNames = clip.cut ? Object.fromEntries(Object.values(clip.netlist).map(n=>[n,n])) : copyNetNames(Object.values(clip.netlist));
  pushHistory(); layout.elements.push(...clip.elements);
  layout.wires.push(...clip.wires.map(w=>w.map(p=>shiftedPoint(p,delta))));
  layout.hops.push(...clip.hops.map(p=>shiftedPoint(p,delta)));
  for (const [key,net] of Object.entries(clip.netlist)) layout.netlist[ref(key)] = netNames[net];
  if (Object.keys(clip.parameters).length) {
    layout.simulation ||= {}; layout.simulation.components ||= {};
    for (const [id,params] of Object.entries(clip.parameters)) layout.simulation.components[mapping[id]] = params;
  }
  if (clip.no_connect.length) { layout.no_connect ||= []; layout.no_connect.push(...clip.no_connect.map(ref)); }
  // a cut pasted back under its own ids also gets its probes, module links and region places back
  if (clip.cut && clip.side && clip.elements.every(e=>mapping[e.id] === e.id))
    restoreSide({...clip.side, netlist: {}, parameters: {}, no_connect: []});
  clearGroupSelection();clip.elements.forEach(e=>moduleSelection.add(e.id));selected=null;
  for(let i=layout.wires.length-clip.wires.length;i<layout.wires.length;i++)objectSelection.wire.add(i);
  const usedDecorations=new Set(['regions','blocks','callouts'].flatMap(k=>(layout[k]||[]).map(x=>x.id))),usage={};
  for(const {type,item} of clip.decorations||[]) {
    const old=item.id;
    if(old){for(let n=1;usedDecorations.has(item.id);n++)item.id=`${old}_copy${n}`;usedDecorations.add(item.id);}
    if(item.members)item.members=item.members.map(id=>mapping[id]||id);
    shiftDecoration(item,delta);
    const section=selectionSection(type);layout[section]||=[];layout[section].push(clone(item));objectSelection[type].add(layout[section].length-1);
    if(type==='regions' && clip.stack_usage?.[old]){layout.stack_usage||={};usage[item.id]=layout.stack_usage[item.id]=clone(clip.stack_usage[old]);}
  }
  // Subsequent pastes start at the last pasted group, using fresh identifiers.
  clipboard = {...clip, cut:false, wires:clip.wires.map(w=>w.map(p=>shiftedPoint(p,delta))), hops:clip.hops.map(p=>shiftedPoint(p,delta)),
    netlist:Object.fromEntries(Object.entries(clip.netlist).map(([key,net])=>[ref(key),netNames[net]])),
    parameters:Object.fromEntries(Object.entries(clip.parameters).map(([id,p])=>[mapping[id],p])),no_connect:clip.no_connect.map(ref),stack_usage:usage};
  showProps(); changed();
}
// Ctrl+D leaves the clipboard as it was, as in office programs
async function duplicateSelected() {
  const kept = clipboard, result = await copySelected();
  if (result !== false) paste();
  clipboard = kept; syncSelectionControls();
}
