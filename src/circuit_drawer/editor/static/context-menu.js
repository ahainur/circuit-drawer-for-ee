// Canvas actions share the menu bar / property handlers. This menu owns only
// target picking, focus, availability and the explicit right-click location.
const contextPopup = document.createElement('div');
contextPopup.id = 'canvasContext'; contextPopup.hidden = true;
contextPopup.setAttribute('role','menu'); contextPopup.setAttribute('aria-label','画布右键菜单');
document.body.append(contextPopup);
let contextSource = null, contextAnchor = [8,8];
function positionCanvasContext() {
  contextPopup.style.left=Math.max(8,Math.min(contextAnchor[0],innerWidth-contextPopup.offsetWidth-8))+'px';
  contextPopup.style.top=Math.max(8,Math.min(contextAnchor[1],innerHeight-contextPopup.offsetHeight-8))+'px';
}
function closeCanvasContext(restore = false) {
  if (contextPopup.hidden) return;
  contextPopup.hidden = true;
  if (restore) svg.focus({preventScroll:true});
}
function pasteDelta(points, at) {
  return [0,1].map(k=>r4(snap(at[k] - (Math.min(...points.map(p=>p[k])) + Math.max(...points.map(p=>p[k])))/2)));
}
function copyDecoration() {
  if (!['regions','blocks','callouts'].includes(selected?.type)) return false;
  const item = layout[selected.type]?.[selected.index]; if (!item) return false;
  clipboard = {decoration:selected.type, item:clone(item)};
  flash('已复制；可粘贴或在空白处右键“粘贴到这里”'); syncSelectionControls(); return true;
}
function pasteDecoration(at) {
  const {decoration:type} = clipboard, item = clone(clipboard.item);
  const points = [item.from,item.to].filter(Array.isArray);
  // A fitted region still follows its members; copying it must not invent bounds.
  const delta = at && points.length ? pasteDelta(points,at) : clipboard.cut ? [0,0] : PASTE_STEP;   // a cut goes back in place
  for (const key of ['from','to']) if (Array.isArray(item[key])) item[key] = shiftedPoint(item[key],delta);
  if (item.via) item.via = item.via.map(p=>shiftedPoint(p,delta));
  if (item.id) {
    const used = new Set(['regions','blocks','callouts'].flatMap(k=>(layout[k]||[]).map(x=>x.id)));
    const base = item.id; for (let n=1; used.has(item.id); n++) item.id = `${base}_copy${n}`;
  }
  pushHistory(); layout[type] ||= []; layout[type].push(item);
  clipboard.item = clone(item); clipboard.cut = false; select({type,index:layout[type].length-1}); changed();
  if (item.fit) flash('区域副本仍随原成员贴合；可在属性栏固定边界后移动');
}
function focusPropertyText() {
  showSidebar('properties'); showProps();
  const label = [...document.querySelectorAll('#props > label')].find(n=>['标签','文字','变比'].includes(cdI18n.sourceText(n)));
  const input = label?.nextElementSibling;
  if (input?.matches('input,textarea')) { input.focus(); input.select(); input.scrollIntoView({block:'nearest'}); }
}
function contextCommand(id, reason = '') {
  const original = document.getElementById(id);
  return {label:[...original.childNodes].filter(n=>n.nodeType===Node.TEXT_NODE).map(n=>cdI18n.sourceText(n)).join('').trim(),
    shortcut:original.querySelector('kbd')?.textContent, id:`context-${id}`,
    reason:reason || (original.disabled ? '当前选择不能执行此操作' : ''),
    run:()=>original.onclick ? original.onclick() : original.click()};
}
function populateContext(box, entries) {
  for (const entry of entries) {
    if (!entry) { const hr=document.createElement('hr'); hr.setAttribute('role','separator'); box.append(hr); continue; }
    const b=document.createElement('button'); b.type='button'; b.tabIndex=-1; b.setAttribute('role','menuitem');
    b.textContent=entry.label; if (entry.id) b.id=entry.id;
    if(entry.shortcut){const k=document.createElement('kbd'); k.textContent=entry.shortcut; b.append(k);}
    if (entry.layer) b.dataset.layerAction=entry.layer;
    b.setAttribute('aria-disabled',String(!!entry.reason)); b.title=entry.reason || '';
    if(entry.reason) b.setAttribute('aria-description',entry.reason);
    box.append(b);
    if (entry.children) {
      b.setAttribute('aria-haspopup','menu'); b.setAttribute('aria-expanded','false');
      const sub=document.createElement('div'); sub.className='context-submenu'; sub.hidden=true; sub.setAttribute('role','menu');
      sub.setAttribute('aria-label',entry.label); box.append(sub); populateContext(sub,entry.children);
      b.onclick=()=>{ if(entry.reason)return; sub.hidden=!sub.hidden; b.setAttribute('aria-expanded',String(!sub.hidden));
        positionCanvasContext();
        if(!sub.hidden) sub.querySelector('button')?.focus(); };
    } else b.onclick=async()=>{
      if (entry.reason) return;
      if (editorTableSnapshot(layout)!==contextSource || selectionBusy) { closeCanvasContext(true); flash('图纸已变化，请重新打开右键菜单'); return; }
      closeCanvasContext(true);
      try { await entry.run(); } catch(e) { flash(e.message,true); }
      syncSelectionControls();
    };
  }
}
function openCanvasContext(ev, title, entries) {
  contextPopup.replaceChildren(); const caption=document.createElement('p'); caption.textContent=title; contextPopup.append(caption);
  populateContext(contextPopup,entries); contextSource=editorTableSnapshot(layout); contextPopup.hidden=false;
  contextAnchor=[ev.clientX,ev.clientY]; contextPopup.scrollTop=0; positionCanvasContext();
  contextPopup.querySelector('button[aria-disabled="false"]')?.focus();
}
function groupContext(ev) {
  selected=null;showSidebar('properties');showProps();render();syncSelectionControls();
  const entries=['cutSelection','copySelection','pasteSelection','duplicateSelection','del'].map(id=>contextCommand(id));
  entries.push(contextCommand('scaleCircuit'));
  entries.push(null,contextCommand('rotateSelection',groupRotateReason()),contextCommand('rotateSelectionCCW',groupRotateReason()),contextCommand('mirrorSelection','请单独选中一个元件后操作'));
  entries.push(null,{label:'存为模块…',id:'contextSaveModule',reason:definitionEdit?'请先保存或取消模块定义':
    !moduleSelection.size || selectedObjects().length?'请只选元件；存为模块会自动包含组内导线':'',run:openSaveModule});
  openCanvasContext(ev,`已选 ${selectionCount()} 个对象`,entries);
}
function issueEntries(ref) {
  const key=ref.type==='el'?`el:${ref.id}`:`${ref.type}:${ref.index}`;
  const rows=[...document.querySelectorAll('#issues > li')].filter(li=>JSON.parse(li.dataset.issueTargets||'[]').includes(key));
  return rows.length?[null,{label:`相关问题（${rows.length}） ›`,id:'contextIssues',children:rows.flatMap((li,i)=>[
    {label:`${i+1}、${li.querySelector('b')?.textContent||'查看问题'}`,run:()=>{showSidebar('issues');li.scrollIntoView({block:'center'});li.classList.add('context-target');}},
    ...[...li.querySelectorAll('[data-issue-intent]')].map(b=>({label:b.textContent,run:()=>b.click()}))
  ])}]:[];
}
function measurementEntries(e) {
  const max=(layout.simulation?.probes||[]).length>=report.probe_limit?`测量通道已达到 ${report.probe_limit} 个，请先在仿真窗口整理`:'';
  const entries=[];
  if(e.pins.a && e.pins.b)entries.push({label:e.kind==='pmsm'?'测线电压（a − b）':'测两端电压（a − b）',id:'contextMeasureVoltage',reason:max,
    run:()=>addContextMeasurement({kind:'voltage',positive:e.id+'.a',negative:e.id+'.b'},'v_'+e.id)});
  if(e.kind==='pmsm')entries.push({label:'电机测量 ›',id:'contextMeasureMotor',reason:max,
    children:Object.entries(report.motor_quantities).map(([quantity,{label,unit}])=>({
      label:`${label} (${unit})`,id:'contextMotor-'+quantity,reason:max,
      run:()=>addContextMeasurement({kind:'motor',element:e.id,quantity},quantity+'_'+e.id)}))});
  if(!['port','pmsm'].includes(e.kind))entries.push({label:'测电流',id:'contextMeasureCurrent',reason:max ||
    (!(report?.probe_kinds?.current||[]).includes(e.kind)?'当前仿真模型尚不支持此器件的电流通道':
      e.kind==='transformer'?'变压器需指定绕组，请在仿真参数窗口添加电流通道':''),
    run:()=>addContextMeasurement({kind:'current',element:e.id},'i_'+e.id)});
  if((report?.probe_kinds?.drive||[]).includes(e.kind))entries.push({label:'测驱动（0/1）',id:'contextMeasureDrive',reason:max,
    run:()=>addContextMeasurement({kind:'drive',element:e.id},'pwm_'+e.id)});
  return entries.length?[null,...entries]:[];
}
async function addContextMeasurement(probe,prefix) {
  if(definitionEdit){flash('请先保存模块定义或取消编辑，再操作整个项目。',true);return;}
  const snapshot=editorTableSnapshot(layout),info=await api('/api/spice-info',{layout,profile});
  if(snapshot!==editorTableSnapshot(layout)){flash('图纸已变化，请重新添加测量');return;}
  const used=new Set((layout.simulation?.probes||[]).map(p=>String(p.name).toLowerCase()));
  if((layout.simulation?.probes||[]).length>=report.probe_limit){flash(`测量通道已达到 ${report.probe_limit} 个`,true);return;}
  const base=prefix.replace(/[^a-zA-Z0-9_]/g,'_');
  let name=probe.kind==='motor'?motorProbeName(probe.element,probe.quantity,layout.simulation?.probes||[]):base;
  if(probe.kind!=='motor')for(let n=2;used.has(name.toLowerCase());n++)name=`${base}_${n}`;
  pushHistory();layout.simulation||={version:1,ground:'',components:{},transient:{},probes:[]};
  layout.simulation.probes||=[];layout.simulation.probes.push({name,...probe});changed();
  openSpice(info);const field=document.getElementById('spiceProbes'),section=field.closest('details');
  section.open=true;section.classList.add('context-target');field.scrollIntoView({block:'center'});field.focus({preventScroll:true});
  field.setSelectionRange(field.value.lastIndexOf('"name": "'+name+'"'),field.value.lastIndexOf('}'));
  field.scrollTop=field.scrollHeight;
}
function wireMeasurement(index) {
  const viewIndex=layout.modules?Object.keys(viewInfo().root_wires||{}).find(i=>viewInfo().root_wires[i]===index):index;
  const net=report?.wire_nets?.[viewIndex], positive=net===undefined?null:Object.keys(report.nets||{}).find(ref=>report.nets[ref]===net);
  const ground=layout.simulation?.ground;
  return {label:'测此点对地电压',id:'contextMeasureNode',reason:!positive?'该导线还没有可引用的电气引脚':
    !ground || report?.nets?.[ground]===undefined?'请先在“仿真 → SPICE 仿真…”选择并保存参考地':
    (layout.simulation?.probes||[]).length>=report.probe_limit?`测量通道已达到 ${report.probe_limit} 个`:'',
    run:()=>addContextMeasurement({kind:'voltage',positive,negative:ground},'v_node')};
}
function crossingEntry(at) {
  const tolerance=WIRE_PICK_PX/Math.abs(svg.getScreenCTM().a),segments=crossingWires().flatMap(w=>w.slice(1).map((p,i)=>[w[i],p]));
  const on=(p,a,b)=>same(p,a)||same(p,b)||inside(p,a,b);
  for(const [a,b] of segments)for(const [c,d] of segments){
    if(a[1]!==b[1] || c[0]!==d[0])continue;
    const p=[c[0],a[1]];
    if(!on(p,a,b)||!on(p,c,d)||Math.hypot(p[0]-at[0],p[1]-at[1])>tolerance)continue;
    if(!layout.wires.some(w=>w.slice(1).some((q,i)=>on(p,w[i],q))))continue;
    const arms=new Set();for(const [q,r] of segments)if(on(p,q,r))for(const end of [q,r]){
      if(end[1]===p[1] && end[0]!==p[0])arms.add(end[0]<p[0]?'L':'R');
      if(end[0]===p[0] && end[1]!==p[1])arms.add(end[1]<p[1]?'D':'U');
    }
    if(arms.size!==4)continue;
    const hop=layout.hops.some(h=>same(h,p));
    return {label:hop?'在交叉处相连（节点）':'在交叉处跨线（不相连）',id:'contextCrossing',
      reason:projected(()=>layout.elements.some(e=>Object.values(e.pins).some(q=>same(q,p))))?'此处接有元件引脚，不能改为跨线':'',run:()=>{
        pushHistory();if(hop)toNode(p);else if(!toHop(p)){history.pop();return;}
        clearGroupSelection();selected=null;changed();}};
  }
  return null;
}
function componentContext(ev, id, pin=null) {
  focusedModuleId=null; focusedLink=null;
  if(groupHas({type:'el',id}) && selectionCount()>1)return groupContext(ev);
  if (!moduleSelection.has(id)) select({type:'el',id});
  else if (moduleSelection.size===1) select({type:'el',id});
  else { selected=null; showSidebar('properties'); showProps(); render(); }
  syncSelectionControls(); const group=moduleSelection.size>1, why=group?'请单独选中一个元件后操作':'';
  const kind=layout.elements.find(e=>e.id===id).kind;
  const entries=['cutSelection','copySelection','pasteSelection','duplicateSelection','del'].map(id=>contextCommand(id,
    id==='pasteSelection' && !clipboard ? '请先复制或剪切对象' : ''));
  entries.push(contextCommand('scaleCircuit'));
  // the toolbar's delete button has no shortcut label; the key is Delete
  const del=entries.find(entry=>entry.id==='context-del'); del.label='删除'; del.shortcut='Delete';
  const mirrorReason=why || (!['nmos','pmos','igbt','scr',...TWO_TERMINAL].includes(kind)?'此类元件暂不支持镜像／反向':'');
  entries.push(null,contextCommand('rotateSelection',why),contextCommand('rotateSelectionCCW',why),contextCommand('mirrorSelection',mirrorReason),
    {label:'编辑标签…',id:'contextLabel',reason:why || (kind==='transformer'?'此元件使用变比标记，请在属性栏编辑变比':''),run:focusPropertyText},
    {label:'仿真参数…',id:'contextSpice',reason:why || (kind==='port'?'端子没有独立的仿真参数':''),run:()=>openSpiceForElement(id)});
  if (group) entries.push(null,{label:'存为模块…',id:'contextSaveModule',reason:definitionEdit?'请先保存或取消模块定义':'',run:openSaveModule});
  const e=layout.elements.find(e=>e.id===id);
  entries.push(...measurementEntries(e),...issueEntries({type:'el',id}));
  if(pin){const ref=id+'.'+pin,enabled=(layout.no_connect||[]).includes(ref);entries.unshift(
    {label:enabled?'取消故意悬空（NoConn）':'设为故意悬空（NoConn）',id:'contextNoConnect',run:()=>setNoConnect(ref,!enabled)},null);}
  openCanvasContext(ev,pin?`引脚 ${id}.${pin}`:group?`已选 ${moduleSelection.size} 个元件`:`元件 ${id}`,entries);
}
function decorationContext(ev, type, index) {
  if(groupHas({type,index}) && selectionCount()>1)return groupContext(ev);
  focusedModuleId=null; focusedLink=null; select({type,index}); updateLayerControls();
  const names={regions:'区域',blocks:'方框',callouts:'箭头'}, state=layerSelection();
  const entries=[['上移一层','raise'],['下移一层','lower'],['置顶','top'],['置底','bottom']].map(([label,action])=>({
    label,layer:action,shortcut:document.querySelector(`#layer-menu-panel [data-layer-action="${action}"] kbd`)?.textContent,
    reason:!state || layerDestination(action,state)===state.position?'已到同类对象的边界':'',run:()=>reorderLayer(action)}));
  entries.push(null,...['cutSelection','copySelection','duplicateSelection','del'].map(id=>contextCommand(id)),{label:'编辑文字…',id:'contextText',run:focusPropertyText},...issueEntries({type,index}));
  openCanvasContext(ev,`${names[type]} · 只调整同类对象图层`,entries);
}
function annotationContext(ev,index) {
  select({type:'ann',index});syncSelectionControls();
  openCanvasContext(ev,'文字标注',[
    ...['cutSelection','copySelection','duplicateSelection','del'].map(id=>contextCommand(id)),
    {label:'编辑文字…',id:'contextText',run:focusPropertyText},...issueEntries({type:'ann',index})]);
}
function wireContext(ev,index,at) {
  if(groupHas({type:'wire',index}) && selectionCount()>1)return groupContext(ev);
  const w=layout.wires[index], hit=wireHits(at).find(h=>h.index===index); if(!w || !hit)return;
  const tolerance=WIRE_PICK_PX/Math.abs(svg.getScreenCTM().a);
  const point=w.findIndex(p=>Math.hypot(p[0]-at[0],p[1]-at[1])<=tolerance);
  focusedModuleId=null; focusedLink=null;
  select({type:'wire',index,...(point>=0?{point}:{segment:hit.segment})});
  const p=point>=0?w[point]:wireProjection(at.map(v=>r4(snap(v))),w[hit.segment],w[hit.segment+1]).at.map(r4);
  const endpoint=same(p,w[0])||same(p,w.at(-1)), hop=layout.hops.some(h=>same(h,p));
  const joins=layout.wires.flatMap((other,j)=>j!==index&&joinWires(w,other)?[{label:`与导线 ${j+1} 合并`,run:()=>mergeWire(index,j)}]:[]);
  openCanvasContext(ev,`导线 ${index+1}`, [
    {label:'在此处加折点',id:'contextWireAdd',reason:point>=0?'这里已有端点或折点':hop?'跨线处请先确定连接方式':'',run:()=>addWirePoint(index,hit.segment,at)},
    {label:'删除折点',id:'contextWireRemove',reason:point<=0||point>=w.length-1?'请右键内部折点；端点需保留':'',run:deleteWirePoint},
    {label:'在此处拆成两条',id:'contextWireSplit',reason:endpoint?'端点不能拆分':hop?'跨线处请先确定连接方式':'',run:()=>splitWire(at)},
    {label:'和首尾相接的导线合成一条',id:'contextWireMerge',reason:joins.length?'':'没有首尾相接的其它导线',...(joins.length===1?{run:joins[0].run}:{children:joins})},
    contextCommand('mergeOverlaps'),
    null,...['cutSelection','copySelection','duplicateSelection'].map(id=>contextCommand(id)),
    {label:'删除这条线',id:'contextWireDelete',run:()=>{selected={type:'wire',index};delSelected();}},
    null,wireMeasurement(index),...(crossingEntry(at)?[crossingEntry(at)]:[]),...issueEntries({type:'wire',index})
  ]);
}
function ellipsisOwner(id) {
  const own=layout.modules?.instances.find(i=>i.group?.style==='ellipsis' && (i.id===id || i.group.members.includes(id)));
  if(own)return own;
  const legacy=layout.module_view==='folded' && Object.entries(layout.stack_usage || {}).find(([,u])=>
    u.purpose==='instances' && u.style==='ellipsis' && u.instances.includes(id));
  return legacy?{id:legacy[1].instances[0],region:legacy[0]}:null;
}
function ellipsisSelectionReason(ids) {
  if(selectionCount()!==instanceSelection.size)return '省略前请取消选择普通元件、导线和图形';
  if(ids.length<3)return '至少选择三个模块实例，保留首尾并省略中间实例';
  const grouped = [...(layout.modules?.instances || []).filter(i=>i.group).flatMap(i=>[i.id,...i.group.members]),
    ...Object.values(layout.stack_usage || {}).filter(u=>u.purpose==='instances').flatMap(u=>u.instances || [])];
  if(ids.some(id=>grouped.includes(id)))return '所选实例已有分组，请先恢复全部实例';
  return '';
}
async function changeInstanceEllipsis(ids, restore=false) {
  const before=editorTableSnapshot(layout), candidate=clone(layout);
  if(restore) {
    const owner=ellipsisOwner(ids[0]); if(!owner)return;
    if(owner.region!==undefined) {
      delete candidate.stack_usage[owner.region];
      delete candidate.regions.find(r=>r.id===owner.region).stack;
      if(!Object.values(candidate.stack_usage).some(u=>u.purpose==='instances'))candidate.module_view='expanded';
    } else delete candidate.modules.instances.find(i=>i.id===owner.id).group;
  } else {
    const reason=ellipsisSelectionReason(ids); if(reason){flash(reason);return;}
    // Order the chosen frames along their dominant axis, independent of click order.
    const centres=ids.map(id=>{const b=viewInfo().frames[id];return {id,x:(b[0]+b[2])/2,y:(b[1]+b[3])/2};});
    const span=k=>Math.max(...centres.map(c=>c[k]))-Math.min(...centres.map(c=>c[k]));
    const horizontal=span('x')>span('y');
    centres.sort((a,b)=>(horizontal?a.x-b.x:b.y-a.y) || a.id.localeCompare(b.id));
    const ordered=centres.map(c=>c.id);
    candidate.modules.instances.find(i=>i.id===ordered[0]).group={style:'ellipsis',members:ordered.slice(1)};
  }
  selectionBusy=true;syncSelectionControls();
  try {
    const trial=await trialCheck(candidate);
    if(editorTableSnapshot(layout)!==before)return;
    const broken=!trial?'校验没有完成':trial.issues?.find(i=>i.code==='bad-module')?.message || (trial.module_info?'':'模块拼装失败');
    if(broken){flash(`没有改省略显示：${zhModule(broken)}。原图已保留`,true);return;}
    pushHistory();layout=candidate;clearGroupSelection();selected=null;focusedModuleId=null;focusedLink=null;
    changed();
  } finally {selectionBusy=false;syncSelectionControls();}
}
function instanceSelectionContext(ev) {
  const ids=[...instanceSelection];showSidebar('properties');moduleSelectionStatus();
  openCanvasContext(ev,'模块多选',[
    contextCommand('scaleCircuit'),
    {label:'省略中间实例',id:'contextEllipsis',reason:ellipsisSelectionReason(ids),run:()=>changeInstanceEllipsis(ids)},
    {label:'取消选择',id:'contextClearInstances',run:()=>{clearGroupSelection();showProps();render();}}
  ]);
}
function instanceContext(ev,id) {
  clearGroupSelection(); selected=null; focusedLink=null; focusedModuleId=id;
  showSidebar('properties'); showInstanceProps(id); render();
  const inst=focusedModule(), own=!!layout.modules.definitions?.[inst.definition];
  const fromProps=(label,id,source,reason='')=>{
    // A background library refresh can replace the property buttons while this
    // menu is open. Retain its action; the shared table guard still rejects edits.
    const button=document.getElementById(source);
    return {label,id,reason,run:()=>button.onclick()};
  };
  const entries=[contextCommand('scaleCircuit'),{label:'省略中间实例',id:'contextEllipsis',reason:'至少选择三个模块实例，保留首尾并省略中间实例'},
    ...(ellipsisOwner(id)?[{label:'恢复全部实例',id:'contextRestoreInstances',run:()=>changeInstanceEllipsis([id],true)}]:[]),null,
    fromProps('编辑模块定义','contextDefinition','instDefinition',own?'':'内置实例需先转换为项目模块'),
    fromProps('实例参数…','contextParameters','instParams'),
    fromProps('展开 / 折叠','contextDisplay','instDisplay',layout.modules.links?'':'旧式叠板请用“视图 → 模块显示”切换'),
    fromProps('解散为普通元件','contextDissolve','instDissolve'),
    fromProps('存进我的模块库…','contextLibrarySave','instToLibrary',own?'':'请先保存为项目模块')];
  if (catalogueEntry(inst.definition)?.update) entries.push(fromProps('从库更新','contextLibraryUpdate','instLibraryUpdate'));
  openCanvasContext(ev,`模块实例 ${id}`,entries);
}
function blankContext(ev,at) {
  focusedModuleId=null; focusedLink=null; select(null);
  const components=[...document.querySelectorAll('#menu-insert-panel [data-component]')].map(b=>({
    label:b.textContent,id:`context-${b.id}`,reason:document.getElementById('drawWire').disabled?'当前不能插入元件':'',run:()=>addFromLibrary(b.dataset.component,at)}));
  components.push(null,contextCommand('addModule'));
  openCanvasContext(ev,'画布空白处',[
    {label:'粘贴到这里',id:'contextPasteHere',reason:!clipboard?'请先复制或剪切对象':pastePositionReason(),run:()=>paste(at)},
    {label:'新建空子系统',id:'contextEmptySubsystem',reason:definitionEdit?'请先返回上层，当前只支持一层子系统':'',run:()=>newEmptySubsystem(at)},
    {label:'在这里插入 ›',id:'contextInsert',children:components},null,
    ...['drawRegion','drawBlock','drawCallout'].map(id=>contextCommand(id)),null,
    {label:'全选顶层对象',id:'contextSelectAll',reason:selectionCandidates().length?'':'没有可选的顶层对象',run:selectAllObjects},
    contextCommand('scaleCircuit'),
    contextCommand('menuFit')]);
}
function canvasContextMenu(ev) {
  ev.preventDefault(); ev.stopImmediatePropagation(); closeCanvasContext();
  if (!layout || selectionBusy || drag || moduleBox || instanceDrag || linkDrag || resizing) return;
  if(layout.modules && checking) { flash('正在校验模块布局，请稍后再打开右键菜单'); return; }
  if (wireDraft || boxDraft || pendingModule) { flash('请先完成当前绘制或按 Esc 取消，再打开右键菜单'); return; }
  const at=toUnits(ev), node=ev.target.closest('[data-region],[data-block],[data-callout]');
  const ref=objectAtTarget(ev.target);
  const annotation=ev.target.closest('[data-ann]');
  const omission=annotation && viewInfo().ellipsis_markers?.[annotation.dataset.ann];
  if(omission && ellipsisOwner(omission))return instanceContext(ev,omission);
  const instance=instanceOfTarget(ev) || (node && instanceAt(ev));
  if(instanceSelection.size && (instanceSelection.has(instance) || groupHas(ref) || ev.target===svg || ev.target.classList.contains('bg')))
    return instanceSelectionContext(ev);
  if(groupHas(ref) && selectionCount()>1)return groupContext(ev);
  if(ref?.type==='ann')return annotationContext(ev,ref.index);
  // Resize/bend handles are lifted above the drawing at the SVG root, outside
  // their object's group; selection still identifies their authored owner.
  if(ev.target.classList.contains('vtx') && ['regions','blocks','callouts'].includes(selected?.type))
    return decorationContext(ev,selected.type,selected.index);
  if (node) {
    const [key,type]=[['region','regions'],['block','blocks'],['callout','callouts']].find(([k])=>node.dataset[k]!==undefined);
    const viewIndex=+node.dataset[key], index=layout.modules?viewInfo().root_decorations?.[type]?.[viewIndex]:viewIndex;
    if (index!==undefined && layout[type]?.[index]) return decorationContext(ev,type,index);
  }
  if(instance) return instanceContext(ev,instance);
  const wave=ev.target.closest('[data-wave-symbol]');
  if(wave && !layout.modules){
    const index=+wave.dataset.waveSymbol;select({type:'wave_symbols',index});syncSelectionControls();
    return openCanvasContext(ev,'波形标记',[contextCommand('del'),...issueEntries({type:'wave_symbols',index})]);
  }
  const id=ev.target.closest('g.element[data-id]')?.dataset.id;
  const tolerance=WIRE_PICK_PX/Math.abs(svg.getScreenCTM().a);
  const pins=layout.elements.flatMap(e=>Object.entries(e.pins).map(([pin,p])=>({id:e.id,pin,d:Math.hypot(p[0]-at[0],p[1]-at[1])})))
    .filter(p=>p.d<=tolerance && (!id||p.id===id)).sort((a,b)=>a.d-b.d);
  if(pins.length)return componentContext(ev,pins[0].id,pins[0].pin);
  if(id && layout.elements.some(e=>e.id===id)) return componentContext(ev,id);
  const wire=ev.target.closest('[data-wire]');
  if (wire) {
    const viewIndex=+wire.dataset.wire,index=layout.modules?viewInfo().root_wires?.[viewIndex]:viewIndex;
    if(index!==undefined) return wireContext(ev,index,at);
    return openCanvasContext(ev,'模块连接',[{label:'请通过模块端口连接编辑',reason:'这条线由模块连接自动生成'}]);
  }
  // Junction hit targets and the selected-wire overlay need physical hit testing.
  const hit=wireHits(at).find(h=>h.index===selected?.index)||wireHits(at)[0];
  if(!id && !node && hit) return wireContext(ev,hit.index,at);
  if(ev.target===svg || ev.target.classList.contains('bg')) return blankContext(ev,at);
  openCanvasContext(ev,'图中对象',[{label:'请点选后在属性栏编辑',reason:'此类对象的右键操作在下一阶段补齐'}]);
}
document.addEventListener('editor-menu-open',()=>closeCanvasContext());
document.addEventListener('pointerdown',ev=>{if(!contextPopup.contains(ev.target))closeCanvasContext();});
document.addEventListener('focusin',ev=>{if(!contextPopup.contains(ev.target))closeCanvasContext();});
// Resize delivery can lag behind the click that opens a menu. Keep its action
// and table snapshot, only clamp its position to the current viewport.
window.addEventListener('resize',()=>{if(!contextPopup.hidden)positionCanvasContext();});
window.addEventListener('blur',()=>closeCanvasContext());
window.addEventListener('keydown',ev=>{
  if(contextPopup.hidden)return;
  // Advertised application shortcuts still use the normal command handlers,
  // just as they do while a menu-bar menu is open.
  const shortcut=!ev.altKey && ((ev.ctrlKey||ev.metaKey) && ['x','c','v','d','z','y','[',']'].includes(ev.key.toLowerCase()) ||
    !ev.ctrlKey&&!ev.metaKey&&['r','R','f','F','+','=','-','_','Delete','Backspace'].includes(ev.key));
  if(shortcut){
    const stale=editorTableSnapshot(layout)!==contextSource || selectionBusy;
    closeCanvasContext(true);
    if(stale){ev.preventDefault();ev.stopImmediatePropagation();flash('图纸已变化，请重新选择后操作');}
    return;
  }
  // Capture before editing shortcuts: menu arrows never nudge the drawing.
  ev.stopImmediatePropagation();
  if(ev.key==='Escape'||ev.key==='Tab'){if(ev.key==='Escape')ev.preventDefault();closeCanvasContext(true);return;}
  const active=document.activeElement, box=active.closest('[role="menu"]') || contextPopup;
  const buttons=[...box.children].filter(n=>n.matches('button'));
  if(['ArrowDown','ArrowUp','Home','End'].includes(ev.key)){
    ev.preventDefault();const i=buttons.indexOf(active), next=ev.key==='Home'?0:ev.key==='End'?buttons.length-1:
      (i+(ev.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length;
    buttons[next]?.focus();
  }else if(['Enter',' '].includes(ev.key)){ev.preventDefault();active.click();}
  else if(ev.key==='ArrowRight'&&active.hasAttribute('aria-haspopup')){ev.preventDefault();if(active.getAttribute('aria-expanded')!=='true')active.click();}
  else if(ev.key==='ArrowLeft'&&box!==contextPopup){ev.preventDefault();box.hidden=true;box.previousElementSibling.setAttribute('aria-expanded','false');box.previousElementSibling.focus();}
},true);
