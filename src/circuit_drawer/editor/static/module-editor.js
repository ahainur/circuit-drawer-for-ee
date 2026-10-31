"use strict";

// All mutations are transactions against the source table. The main canvas
// remains the existing editor; the port picker is a view of that same drawing.
const moduleSelection = new Set();
let moduleMarquee = false, moduleBox = null, definitionEdit = null;
const moduleButton = id => document.getElementById(id);
let focusedModuleId = null; // Canvas/menu focus only; never persisted in the table.

function focusedModule() {
  return layout?.modules?.instances?.find(i => i.id === focusedModuleId) || null;
}
function drawModuleFocus() {
  drawLinkFocus();
  const frame = focusedModule() && report?.module_info?.frames?.[focusedModuleId];
  if (frame) el("rect", {id: "moduleFocus", x: frame[0], y: -frame[3], width: frame[2]-frame[0], height: frame[3]-frame[1],
    fill: "none", stroke: "var(--accent)", "stroke-width": .035, "stroke-dasharray": ".12 .08", "pointer-events": "none"}, svg);
}
// The folded box whose resize corner (drawn by drawResizeHandles) is under the pointer.
function resizeCornerAt(ev) {
  if (!layout?.modules?.links || typeof boxRect !== "function") return null;
  const [x, y] = toUnits(ev), grab = typeof RESIZE_GRAB === "number" ? RESIZE_GRAB : .3;
  return layout.modules.instances.find(i => {
    const r = i.display === "collapsed" && boxRect(i.id);
    return r && Math.abs(x - r[2]) < grab && Math.abs(y - r[1]) < grab;
  })?.id || null;
}
// A press anywhere on the canvas drops the instance / link focus first; the
// canvas handler below sets it again when the press lands on one. A press on a
// box's resize corner focuses that instance: the resize edits it, and the
// View menu's size entries keep following it afterwards.
window.addEventListener("pointerdown", ev => {
  if (ev.button !== 0 || !svg.contains(ev.target) || !layout?.modules?.instances?.length) return;
  const corner = pendingModule ? null : resizeCornerAt(ev);
  focusedModuleId = corner; focusedLink = null;
  // Update just the overlay; replacing the clicked SVG node would interrupt its handler.
  document.getElementById("moduleFocus")?.remove(); document.getElementById("linkFocus")?.remove();
  if (corner) { drawModuleFocus(); showInstanceProps(corner); }
}, true);

function syncModuleMenus() {
  const instances = layout?.modules?.instances || [], inst = focusedModule();
  const enabled = !!layout?.modules?.links && instances.length > 0 && !definitionEdit && !checking;
  const pick = moduleButton("menuInstanceSelect"), previous = focusedModuleId || "";
  pick.replaceChildren(new Option("请点选实例", ""), ...instances.map(i => new Option(i.id, i.id)));
  pick.value = inst ? previous : ""; pick.disabled = !enabled;
  for (const id of ["menuAllExpanded", "menuAllCollapsed"]) moduleButton(id).disabled = !enabled;
  for (const id of ["menuModuleExpanded", "menuModuleCollapsed", "menuModuleSymbol", "menuDissolve"])
    moduleButton(id).disabled = !enabled || !inst;
  moduleButton("menuModulePartial").disabled = !enabled || !inst || !report?.module_info?.partial?.includes(inst.id);
  for (const id of ["menuModuleSize", "menuModuleReset"]) moduleButton(id).disabled = !enabled || inst?.display !== "collapsed";
  moduleButton("menuEditDefinition").disabled = !enabled || !inst || !layout.modules.definitions?.[inst.definition];
  // File -> Save as module wraps the selected parts: nothing selected, nothing to save
  const save = moduleButton("saveModule");
  save.disabled = !savableSelection().length;
  save.title = save.disabled ? "请先框选或 Shift 点选要封装的元件" : "存为模块…";
}
function savableSelection() {
  const ids = new Set(moduleSelection);
  if (!ids.size && selected?.type === "el") ids.add(selected.id);
  return [...ids].filter(id => layout?.elements?.some(e => e.id === id));
}
document.addEventListener("editor-menu-open", syncModuleMenus);
moduleButton("menuInstanceSelect").onchange = ev => { focusedModuleId = ev.target.value || null; syncModuleMenus(); render(); };
for (const display of ["expanded", "collapsed", "partial"]) {
  moduleButton("menuModule" + display[0].toUpperCase() + display.slice(1)).onclick = () => setModuleDisplay(focusedModuleId, display);
}
for (const display of ["expanded", "collapsed"]) {
  moduleButton("menuAll" + display[0].toUpperCase() + display.slice(1)).onclick = async () => {
    const candidate = clone(layout); candidate.modules.instances.forEach(i => { i.display = display; });
    try { await applyModuleSource(candidate); } catch (e) { flash(e.message, true); }
  };
}
moduleButton("menuEditDefinition").onclick = () => editModuleDefinition(focusedModuleId);
moduleButton("menuDissolve").onclick = async () => {
  try { await moduleCommit("/api/module-dissolve", {id: focusedModuleId}); focusedModuleId = null; }
  catch (e) { flash(zhModule(e.message), true); }
};
moduleButton("menuModuleReset").onclick = () => resizeModuleInstance(focusedModuleId, null);
moduleButton("menuModuleSize").onclick = () => {
  const id = focusedModuleId, rect = boxRect(id);
  showDialog("模块方框尺寸", `<p>调整选中实例 ${esc(id)}；尺寸仍由原校验器检查。</p>
    <label>宽 <input id="moduleWidth" type="number" step="0.05" value="${r4(rect[2]-rect[0])}"></label>
    <label>高 <input id="moduleHeight" type="number" step="0.05" value="${r4(rect[3]-rect[1])}"></label><button id="moduleSizeApply">应用</button>`);
  moduleButton("moduleSizeApply").onclick = () => {
    const size = {w: Number(moduleButton("moduleWidth").value), h: Number(moduleButton("moduleHeight").value)};
    dlg.close(); resizeModuleInstance(id, size);
  };
};
moduleButton("menuModuleSymbol").onclick = () => {
  const inst = focusedModule();
  showDialog("模块框内标志", '<label>标志 <select id="moduleSymbol"></select></label> <button id="moduleSymbolApply">应用</button><p id="moduleSymbolError" role="status"></p>');
  const labels = {text: "仅文字", acdc: "AC/DC", dcdc: "DC/DC", dcac: "DC/AC", mosfet: "MOSFET", igbt: "IGBT", diode: "二极管"};
  const select = moduleButton("moduleSymbol");
  select.add(new Option("沿用模块定义", ""));
  for (const kind of window.moduleSymbolChoices || []) select.add(new Option(labels[kind] || kind, kind));
  select.value = inst.symbol || "";
  moduleButton("moduleSymbolApply").onclick = async () => {
    const candidate = clone(layout), target = candidate.modules.instances.find(i => i.id === inst.id);
    if (select.value) target.symbol = select.value;
    else delete target.symbol;
    try { await applyModuleSource(candidate); } catch (e) { moduleButton("moduleSymbolError").textContent = e.message; }
  };
};

function modulePointerDown(ev) {
  if (ev.button === 2) { ev.stopImmediatePropagation(); return true; }
  if (ev.button !== 0 || wireDraft || boxDraft) return false;
  const ref = objectAtTarget(ev.target);
  if (selectionBusy) { ev.preventDefault(); ev.stopImmediatePropagation(); return true; }
  if (ev.shiftKey && ref) {
    ev.preventDefault(); ev.stopImmediatePropagation();
    svg.focus({preventScroll:true});
    if (!selectionCount() && selected) groupAdd(selected);
    groupToggle(ref);
    selected = null; render(); moduleSelectionStatus(); return true;
  }
  // A coloured region is background too: a drag starting on one that is not
  // selected draws a selection box; a click without moving selects the region.
  const regionNode = ref?.type !== 'el' && ev.target.closest('[data-region]');
  const regionIndex = regionNode ? (layout.modules ? viewInfo().root_decorations?.regions?.[+regionNode.dataset.region]
    : +regionNode.dataset.region) : undefined;
  const onRegion = regionNode && !(selected?.type === 'regions' && selected.index === regionIndex);
  if (!ev.shiftKey && groupHas(ref)) { startGroupDrag(ev); return true; }
  if ((ev.target === svg || ev.target.classList.contains('bg') || onRegion) && !instanceOfTarget(ev)) {
    ev.preventDefault(); ev.stopImmediatePropagation();
    svg.focus({preventScroll:true});
    if (ev.shiftKey && selected) groupAdd(selected);
    moduleBox = {a: toUnits(ev), b: toUnits(ev), additive: ev.shiftKey, start:[ev.clientX,ev.clientY], pointer:ev.pointerId,
                 region: onRegion && layout.regions?.[regionIndex] ? regionIndex : undefined};
    svg.setPointerCapture(ev.pointerId); return true;
  }
  if (!ev.shiftKey) clearGroupSelection();
  return false;
}

function moduleSelectionStatus() {
  const box = moduleButton("props"); box.className = 'hint';
  box.textContent = `已选 ${selectionCount()} 个对象（${moduleSelection.size} 个元件）。拖动其中任意一个或按方向键可整体移动；Shift+方向键十格，Delete 删除，Ctrl+C / X / D 复制／剪切／创建副本。整组操作可一步撤销，Shift 点选可增减。`;
}

svg.addEventListener("pointermove", ev => {
  if (!moduleBox) return;
  ev.stopImmediatePropagation(); moduleBox.b = toUnits(ev);
  const [a, b] = [moduleBox.a, moduleBox.b];
  const rect = document.getElementById("moduleSelectionBox") || el("rect", {id: "moduleSelectionBox", fill: "#2457d61a", stroke: "#2457d6", "stroke-width": .025, "pointer-events": "none"}, svg);
  Object.entries({x: Math.min(a[0], b[0]), y: -Math.max(a[1], b[1]), width: Math.abs(a[0] - b[0]), height: Math.abs(a[1] - b[1])}).forEach(([k,v]) => rect.setAttribute(k,v));
}, true);
svg.addEventListener("pointerup", ev => {
  if (!moduleBox) return;
  ev.stopImmediatePropagation();
  const {a,b,additive,start,pointer,region} = moduleBox; moduleBox = null;
  const dragged = Math.hypot(ev.clientX-start[0],ev.clientY-start[1]) >= WIRE_DRAG_PX;
  if (!dragged && region !== undefined) {
    if (svg.hasPointerCapture(pointer)) svg.releasePointerCapture(pointer);
    clearGroupSelection(); moduleMarquee = false; select({type: 'regions', index: region}); return;
  }
  if (!additive) clearGroupSelection();
  for (const ref of dragged ? selectionCandidates() : []) {
    if (ref.points.every(p => p[0] >= Math.min(a[0],b[0]) && p[0] <= Math.max(a[0],b[0]) && p[1] >= Math.min(a[1],b[1]) && p[1] <= Math.max(a[1],b[1]))) groupAdd(ref);
  }
  if (svg.hasPointerCapture(pointer)) svg.releasePointerCapture(pointer);
  moduleMarquee = false; selected = null; showProps(); render();
}, true);
svg.addEventListener('pointercancel', () => { moduleBox=null; render(); }, true);

async function moduleCommit(path, data, source = layout) {
  const result = await api(path, {layout: source, profile, ...data});
  pushHistory(); layout = result.layout; selected = null; clearGroupSelection();
  dlg.close(); await revalidate(); fitView(); render();
}

moduleButton("saveModule").onclick = openSaveModule;
async function openSaveModule() {
  if (definitionEdit) { flash("请先保存或取消当前模块定义", true); return; }
  if (selectedObjects().length) { flash("请只选元件；存为模块会自动包含组内导线", true); return; }
  const ids = savableSelection();
  if (!ids.length) { flash("请先框选或 Shift 点选要封装的元件，再存为模块", true); return; }
  if (!moduleSelection.size) moduleSelection.add(ids[0]);
  try {
    const info = await api("/api/module-selection", {layout, profile, ids});
    openPortForm({ids, info, ports: [], name: "", parameters: info.parameters, exposed: []});
  } catch (e) { dlg.close(); flash(e.message, true); }
}

function openPortForm(state) {
  const {info} = state;
  showDialog(state.editing ? "模块端口与共用参数" : "存为模块", `
    <p><label>模块名 <input id="moduleName" value="${esc(state.name)}" ${state.editing ? "readonly" : ""} placeholder="如 Buck、LLC_HB"></label></p>
    <p>点击图上的节点标端口；红圈是仍与外部相连的节点。端口行可拖动排序，也可用上下按钮。</p>
    <div id="portDrawing" style="height:260px;border:1px solid #ddd"></div>
    <div id="portRows"></div><p id="portWarning" role="status"></p>
    <details open><summary>元件参数：勾选允许每个实例单独修改的项</summary><div id="moduleParameterRows" style="max-height:160px;overflow:auto"></div></details>
    ${layout.modes?.length ? "<p>当前模态将保存在项目中；最后一个实例解散后恢复。模块期间不编辑跨端口模态。</p>" : ""}
    ${state.editing ? "" : '<p><label><input type="checkbox" id="moduleToLibrary"> 同时存进我的模块库（其他电路文件也能用）</label></p>'}
    <p><button id="moduleSaveConfirm" class="primary">${state.editing ? "保存定义并同步所有实例" : "保存并原地替换"}</button> <span id="moduleSaveError" role="status"></span></p>`);
  const dialog = document.getElementById("dlg");
  dialog.style.marginTop = "3vh";
  dialog.addEventListener("close", () => { dialog.style.marginTop = ""; }, {once: true});
  const copy = svg.cloneNode(true); copy.removeAttribute("id"); copy.style.cssText = "width:100%;height:100%";
  copy.querySelectorAll("[id]").forEach(e => e.removeAttribute("id"));
  moduleButton("portDrawing").appendChild(copy);
  // The thumbnail shows the whole canvas view in a small box: size the port
  // names and dots in screen pixels so they stay readable at any zoom.
  const shown = copy.getBoundingClientRect(), vb = copy.viewBox?.baseVal;
  const perUnit = vb?.width && vb?.height && shown.width ? Math.min(shown.width / vb.width, shown.height / vb.height) : 0;
  const units = (px, least) => perUnit ? Math.max(least, px / perUnit) : least;
  const fontSize = units(13, .18), dotR = units(5, .075), gap = units(5, .12);
  const nodes = el("g", {}, copy);
  const drawNodes = () => {
    nodes.innerHTML = "";
    info.nodes.forEach(n => {
      const port = state.ports.find(p => same(p.at,n.at));
      const dot = el("circle", {cx:n.at[0],cy:-n.at[1],r:dotR,fill:port ? "#2457d6" : "white",stroke:n.boundary ? "#d12f2f" : "#2457d6","stroke-width":dotR/3,style:"cursor:pointer"},nodes);
      const title = el("title",{},dot); title.textContent = `${n.net}${n.boundary ? "（外连节点，必须标端口）" : ""}`;
      if (port) { const t = el("text",{x:n.at[0]+dotR+gap/2,y:-n.at[1]-dotR-gap/2,"font-size":fontSize,"font-weight":600,fill:"#2457d6",stroke:"white","stroke-width":fontSize/8,"paint-order":"stroke"},nodes); t.textContent=port.name; }
      dot.onclick = () => {
        if (port) return;
        // the side follows where the node is: left or right half of the selection
        const xs = info.nodes.map(m => m.at[0]), mid = (Math.min(...xs) + Math.max(...xs)) / 2;
        state.ports.push({name:`P${state.ports.length+1}`,at:n.at.slice(),side:n.at[0] > mid ? "right" : "left"}); drawRows();
      };
    });
  };
  let dragged = null;
  const drawRows = () => {
    moduleButton("moduleSaveError").textContent = "";
    const box = moduleButton("portRows"); box.innerHTML = "";
    state.ports.forEach((p,i) => {
      const row = document.createElement("div"); row.draggable = true; row.style.cssText="display:flex;gap:6px;margin:5px 0;align-items:center";
      row.innerHTML = `<span title="拖动调整顺序">↕</span><input aria-label="端口名" value="${esc(p.name)}" style="width:100px"><select aria-label="端口边"><option value="left">左边</option><option value="right">右边</option></select><span>${esc(info.nodes.find(n=>same(n.at,p.at))?.net || "")}</span><button title="上移">↑</button><button title="下移">↓</button><button title="移除端口">移除</button>`;
      row.querySelector("input").oninput = e => { p.name=e.target.value; drawNodes(); };
      const side = row.querySelector("select"); side.value=p.side; side.onchange=e=>p.side=e.target.value;
      const move = j => { if(j<0 || j>=state.ports.length)return; state.ports.splice(j,0,state.ports.splice(i,1)[0]); drawRows(); };
      const buttons=row.querySelectorAll("button"); buttons[0].onclick=()=>move(i-1); buttons[1].onclick=()=>move(i+1);
      buttons[2].onclick=()=>{ if(state.editing){moduleButton("moduleSaveError").textContent="已有实例引用端口，请保留名称；可在画布里移动端口节点。";return;} state.ports.splice(i,1);drawRows(); };
      row.ondragstart=ev=>{dragged=i;ev.dataTransfer.setData("text/plain",String(i));};
      row.ondragover=ev=>ev.preventDefault(); row.ondrop=ev=>{ev.preventDefault(); if(dragged===null)return;state.ports.splice(i,0,state.ports.splice(dragged,1)[0]);dragged=null;drawRows();};
      box.appendChild(row);
    });
    const missing=info.missing.filter(at=>!state.ports.some(p=>same(p.at,at)));
    moduleButton("portWarning").textContent=missing.length ? `还缺 ${missing.length} 个外连端口：${missing.map(p=>p.join(", ")).join("；")}` : "外连节点已全部标注。";
    moduleButton("portWarning").style.color=missing.length ? "#d12f2f" : "#1f8a4c";
    drawNodes();
  };
  drawRows();
  const paramRows=moduleButton("moduleParameterRows");
  state.parameters.forEach(p=>{
    const row=document.createElement("label");row.style.cssText="display:flex;gap:8px;margin:5px 0;align-items:center";
    const checked=state.exposed.some(x=>x.element===p.element&&x.field===p.field);
    row.innerHTML=`<input type="checkbox" ${checked ? "checked" : ""}><span style="flex:1">${esc(p.element)} · ${esc(p.label || p.field)}</span><input aria-label="${esc(p.element+"."+p.field)}" value="${esc(String(p.value))}" ${state.editing ? "" : "readonly"} style="width:125px" placeholder="尚未配置">`;
    row.querySelector("input[type=checkbox]").onchange=ev=>{state.exposed=state.exposed.filter(x=>!(x.element===p.element&&x.field===p.field));if(ev.target.checked)state.exposed.push({element:p.element,field:p.field});};
    row.querySelector("input:not([type=checkbox])").oninput=ev=>p.value=ev.target.value;
    paramRows.appendChild(row);
  });
  moduleButton("moduleSaveConfirm").onclick=async ev=>{
    ev.target.disabled=true;
    try {
      if(state.editing){
        const d=clone(definitionEdit.definition);d.layout=clone(layout);delete d.layout.simulation;d.defaults??={};
        d.port_layout={};state.ports.forEach((p,i)=>d.port_layout[p.name]={side:p.side,order:i});
        // Preserve the unrestricted legacy form when the user leaves every
        // permission enabled; a current field list would also limit future fields.
        const allExposed=state.parameters.every(p=>state.exposed.some(x=>x.element===p.element&&x.field===p.field));
        if(d.editable!==undefined || !allExposed)d.editable=state.exposed;
        for(const p of state.parameters)if(p.value!==""){d.defaults[p.element]??={};d.defaults[p.element][p.field]=p.value;}
        const source=definitionEdit.source;
        const result=await api("/api/module-definition",{layout:source,profile,name:definitionEdit.name,definition:d});
        history=definitionEdit.history;redoHistory=definitionEdit.redoHistory;redoBase=definitionEdit.redoBase;
        layout=source;definitionEdit=null;moduleButton("definitionActions").hidden=true;
        pushHistory();layout=result.layout;selected=null;clearGroupSelection();dlg.close();await revalidate();fitView();render();
      }else{
        const name=moduleButton("moduleName").value.trim(),toLibrary=moduleButton("moduleToLibrary")?.checked;
        await moduleCommit("/api/module-extract",{ids:state.ids,name,ports:state.ports,exposed:state.exposed});
        if(toLibrary)await saveToLibrary(name,true); else refreshModuleLibrary();
      }
    }catch(e){moduleButton("moduleSaveError").textContent=e.message;}
    finally{ev.target.disabled=false;}
  };
}

async function editModuleDefinition(id) {
  const inst=layout.modules.instances.find(i=>i.id===id),definition=layout.modules.definitions?.[inst.definition];
  if(!definition){moduleButton("contextError").textContent="这是内置模块；请先将其定义保存到项目库再编辑。";return;}
  definitionEdit={source:clone(layout),history:history.slice(),redoHistory:redoHistory.slice(),redoBase,name:inst.definition,definition:clone(definition)};
  layout=clone(definition.layout);history=[];redoHistory=[];redoBase=null;selected=null;clearGroupSelection();dlg.close();
  moduleButton("definitionActions").hidden=false;
  await revalidate();fitView();render();flash("正在编辑共用定义；改完后点击“保存模块定义”，所有实例一起更新。");
}
moduleButton("cancelDefinition").onclick=async()=>{
  if(!definitionEdit)return;
  layout=definitionEdit.source;history=definitionEdit.history;redoHistory=definitionEdit.redoHistory;redoBase=definitionEdit.redoBase;definitionEdit=null;
  moduleButton("definitionActions").hidden=true;selected=null;clearGroupSelection();await revalidate();fitView();render();
};
moduleButton("saveDefinition").onclick=async()=>{
  if(!definitionEdit)return;
  const d=definitionEdit.definition;
  try{
    // Ask the existing parameter schema without exposing a JSON text editor.
    // parameters of parts deleted in this draft leave with them (the server
    // drops them from defaults, editable and every instance's overrides too)
    const kept=Object.entries(d.defaults || {}).filter(([id])=>layout.elements.some(e=>e.id===id&&e.kind!=="port"));
    const probe=clone(layout);probe.simulation={components:Object.fromEntries(kept)};
    const info=await api("/api/module-selection",{layout:probe,profile,ids:layout.elements.map(e=>e.id)});
    const ports=Object.entries(d.ports).map(([name,ref])=>({name,at:layout.elements.find(e=>e.id===ref.slice(0,-2)).pins.p,side:d.port_layout?.[name]?.side || "left"}));
    ports.sort((a,b)=>(d.port_layout?.[a.name]?.order||0)-(d.port_layout?.[b.name]?.order||0));
    // Omitting editable means all supported parameters may be overridden.
    // An explicit [] means none; do not turn an older definition into that
    // restricted form and invalidate the other instances' existing overrides.
    const exposed = d.editable === undefined
      ? info.parameters.map(p => ({element:p.element, field:p.field})) : clone(d.editable);
    openPortForm({editing:true,name:definitionEdit.name,info,ports,parameters:info.parameters,exposed});
  }catch(e){flash(e.message,true);}
};

function moduleParameters(id){
  const inst=layout.modules.instances.find(i=>i.id===id),d=layout.modules.definitions?.[inst.definition];
  showDialog(`实例 ${id} 的参数`,`<p>取消勾选即可恢复共用值；其他实例不受影响。</p><div id="instanceFields"></div><p><button id="instanceSave" class="primary">保存参数</button> <span id="instanceError" role="status"></span></p>`);
  const overrides=clone(inst.overrides||{});
  for(const p of d?.editable||[]){
    const row=document.createElement("label");row.style.cssText="display:flex;gap:8px;margin:8px 0";
    const custom=Object.hasOwn(overrides[p.element]||{},p.field);
    row.innerHTML=`<input type="checkbox" ${custom?"checked":""}><span>${esc(p.element+"."+p.field)}</span><input value="${esc(String(custom?overrides[p.element][p.field]:d.defaults?.[p.element]?.[p.field]??""))}" aria-label="${esc(p.element+"."+p.field)}">`;
    const [check,value]=row.querySelectorAll("input");
    const update=()=>{if(check.checked){overrides[p.element]??={};overrides[p.element][p.field]=value.value;}else{delete overrides[p.element]?.[p.field];if(!Object.keys(overrides[p.element]||{}).length)delete overrides[p.element];}};
    check.onchange=update;value.oninput=()=>{check.checked=true;update();};moduleButton("instanceFields").appendChild(row);
  }
  if(!d?.editable?.length)moduleButton("instanceFields").textContent="此定义尚未勾选可按实例修改的参数。";
  moduleButton("instanceSave").onclick=async()=>{try{await moduleCommit("/api/module-parameters",{id,overrides});}catch(e){moduleButton("instanceError").textContent=e.message;}};
}

moduleButton("addModule").onclick=async()=>{
  if(definitionEdit){flash("请先保存或取消模块定义",true);return;}
  showDialog("添加模块",`<p>点击模块后在画布点选位置，或将按钮拖到画布。随后选择端口连接。左侧器件库的“模块（子系统）”分组也能直接放。</p><div id="addModuleLibrary"></div>`);
  await refreshModuleLibrary();
  if(dlg.open)drawModuleLibrary(moduleButton("addModuleLibrary"),true);
};
let pendingModule=null, draggedModule=null;   // a module picked to place: clicked, or being dragged in
svg.addEventListener("dragover",ev=>{ev.preventDefault();if(draggedModule)placeGhost(toUnits(ev),draggedModule);});
svg.addEventListener("dragleave",ev=>{
  const r=svg.getBoundingClientRect();
  if(ev.clientX<=r.left||ev.clientX>=r.right||ev.clientY<=r.top||ev.clientY>=r.bottom)placeGhost(null);
});
document.addEventListener("dragend",()=>{draggedModule=null;placeGhost(null);});
svg.addEventListener("drop",ev=>{ev.preventDefault();draggedModule=null;placeGhost(null);
  const name=ev.dataTransfer.getData("application/x-circuit-module");
  if(name)openModulePlacement(name,toUnits(ev));
  const lib=ev.dataTransfer.getData("application/x-circuit-library-module");
  if(lib)openModulePlacement(null,toUnits(ev),null,JSON.parse(lib));});
// Window capture runs before the projection's pointer guard.
window.addEventListener("pointerdown",ev=>{
  if(!pendingModule||!svg.contains(ev.target)||ev.button!==0)return;
  ev.preventDefault();ev.stopImmediatePropagation();const pick=pendingModule;pendingModule=null;placeGhost(null);
  if(typeof pick==="string")openModulePlacement(pick,toUnits(ev));else openModulePlacement(null,toUnits(ev),null,pick);
},true);
document.addEventListener("keydown",ev=>{if(ev.key==="Escape"){pendingModule=null;placeGhost(null);moduleMarquee=false;moduleBox=null;render();}});
// The module being placed: its extent in its own coordinates (catalogue box)
function pickBox(pick){
  const entry=typeof pick==="string"?moduleCatalogue?.project?.find(e=>e.name===pick):pick;
  return entry?.box||null;
}
// Where an instance goes when the point is clicked: its drawing's lower-left corner there
function placedAt(point,box){
  return [r4(snap(point[0]-(box?box[0]:0))),r4(snap(point[1]-(box?box[1]:0)))];
}
const GHOST_PAD=0.3;   // layout units: part bodies reach this far past their pins
// The frame the module will take, drawn where a click or a drop puts it
function placeGhost(point,pick=pendingModule){
  document.getElementById("placeGhost")?.remove();
  const box=point&&pick&&pickBox(pick);
  if(!box)return;
  const at=placedAt(point,box),w=box[2]-box[0],h=box[3]-box[1];
  el("rect",{id:"placeGhost",x:at[0]+box[0]-GHOST_PAD,y:-(at[1]+box[3]+GHOST_PAD),width:w+2*GHOST_PAD,height:h+2*GHOST_PAD,
    fill:"#2457d610",stroke:"var(--accent)","stroke-width":.03,"stroke-dasharray":".12 .08","pointer-events":"none"},svg);
}
svg.addEventListener("pointermove",ev=>{if(pendingModule)placeGhost(toUnits(ev));});
// A definition's public ports: this file's copy, else the bundled module's
// (an instance may name a bundled module without a copy in the file).
function definitionPorts(definition){
  const own=layout.modules?.definitions?.[definition];
  if(own)return Object.keys(own.ports||{});
  return moduleCatalogue?.builtin?.find(e=>e.name===definition)?.ports||null;
}
// What a port may connect to: the top-level parts' pins, the parts placed for
// the modules (modules.parts, such as Vin and RL) and the other instances' ports.
function portEndpoints(existing){
  const endpoints=layout.elements.flatMap(e=>Object.keys(e.pins).map(p=>`${e.id}.${p}`));
  const drawn=new Map((report?.module_info?.view?.elements||[]).map(e=>[e.id,e]));
  for(const part of layout.modules?.parts||[])
    endpoints.push(...Object.keys(drawn.get(part.id)?.pins||{a:0,b:0}).map(p=>`${part.id}.${p}`));
  for(const inst of layout.modules?.instances||[])
    if(inst.id!==existing)endpoints.push(...(definitionPorts(inst.definition)||[]).map(p=>`${inst.id}.${p}`));
  return endpoints;
}
// Server messages quote the validator after a Chinese lead-in ("请先修正电路：…；…"):
// word each quoted problem as the issue list does.
function zhModule(msg){
  if(typeof zh!=="function")return msg;
  const parts=[];let depth=0,cur="";
  for(const ch of String(msg)){
    if(ch==="（")depth++;else if(ch==="）")depth=Math.max(0,depth-1);
    if(ch==="；"&&!depth){parts.push(cur);cur="";}else cur+=ch;
  }
  parts.push(cur);
  return parts.map(part=>{
    const latin=part.search(/[A-Za-z]{3}/),k=part.lastIndexOf("：",latin>=0?latin:part.length);
    const lead=k>=0?part.slice(0,k+1):"",body=k>=0?part.slice(k+1):part;
    const [,text,tail=""]=body.match(/^([\s\S]*?)(（实例 [^（）]*）)?$/);
    return lead+zh(text)+tail;
  }).join("；");
}
// After placing: a problem at the new instance is fixed by dragging it clear;
// new problems elsewhere, once the wider figure was scaled down to the
// column, are not, and say so.
function placementAdvice(id,before){
  const key=i=>i.code+"|"+i.message;
  const fresh=(report?.issues||[]).filter(i=>i.level==="error"&&!before.issues.has(key(i)));
  const own=new RegExp(`(^|[^\\w/])${id.replace(/[.*+?^${}()|[\]\\]/g,"\\$&")}[/.]|实例 ${id}(?![\\w])`);
  const mine=fresh.filter(i=>own.test(i.message));
  const scale=report?.scale_cm_per_unit?.[0],shrunk=before.scale&&scale&&scale<before.scale*.99;
  const cramped=fresh.some(i=>i.code==="too-cramped"||i.code==="too-short");
  if((shrunk||cramped)&&fresh.length>mine.length){
    const ratio=shrunk?`（比例从 ${before.scale.toFixed(2)} 降到 ${scale.toFixed(2)} cm/格）`:"";
    return `已放置；整图变宽，按栏宽缩小了比例${ratio}，原有电路因此出现新问题（见问题栏）。拖开实例解决不了：`+
      `可把模块放到电路下方${/single/.test(profile)?"、换成双栏":""}，或撤销后换个位置${mine.length?"；压到新实例的地方按住实例拖开即可":""}`;
  }
  return mine.length||!fresh.length ? "已放置；和已有电路有重叠的地方在问题栏里，按住实例拖开即可"
    : "已放置；问题栏里有新问题，可撤销后换个位置";
}
// name: a definition in this table; entry: a library module ({library, name, title, ports}),
// copied into the table when it is placed
function openModulePlacement(name,point,existing=null,entry=null){
  const connections={},ports=entry?entry.ports:definitionPorts(name);
  if(!ports){flash(`还没读到模块 ${name} 的端口；请稍候再试`,true);refreshModuleLibrary();return;}
  const endpoints=portEndpoints(existing);
  showDialog(existing?`实例 ${existing} 的端口连接`:`添加 ${entry?entry.title:name}`,`<p>为每个端口选择连接。明确留空的端口保留供后续接线，也可以放好后从引脚画导线到端口。</p><div id="moduleConnections"></div><button id="modulePlace" class="primary">放置实例</button><p id="modulePlaceError" role="status"></p>`);
  for(const port of ports){
    const me=`${existing}.${port}`,links=existing?(layout.modules.links||[]).filter(pair=>pair.includes(me)):[];
    connections[port]=links[0]?.find(ref=>ref!==me)||"";
    // a connection this list does not offer is kept as it is, never dropped
    const options=connections[port]&&!endpoints.includes(connections[port])?[...endpoints,connections[port]]:endpoints;
    const row=document.createElement("label");row.style.cssText="display:block;margin:8px 0";
    row.innerHTML=`${esc(port)} <select aria-label="${esc(port)} 连接"><option value="">留空（未连接）</option>${options.map(ref=>`<option value="${esc(ref)}">${esc(ref)}</option>`).join("")}</select>`+
      (links.length>1?` <span class="hint">另有 ${links.length-1} 条连接（${esc(links.slice(1).map(pair=>pair.find(ref=>ref!==me)).join("、"))}）保持不变；选“留空”会一并断开</span>`:"");
    row.querySelector("select").value=connections[port];row.querySelector("select").onchange=ev=>connections[port]=ev.target.value;moduleButton("moduleConnections").appendChild(row);
  }
  moduleButton("modulePlace").textContent=existing ? "保存连接" : "放置实例";
  moduleButton("modulePlace").onclick=async()=>{try{
    const at=placedAt(point,existing?null:pickBox(entry||name));
    const before={scale:report?.scale_cm_per_unit?.[0],issues:new Set((report?.issues||[]).map(i=>i.code+"|"+i.message))};
    if(existing)await moduleCommit("/api/module-connections",{id:existing,connections});
    else if(entry)await moduleCommit("/api/module-library-place",{library:entry.library,name:entry.name,at,connections});
    else await moduleCommit("/api/module-add",{name,at,connections});
    if(!existing){   // select the new instance: if it landed on something, drag it clear
      focusedModuleId=layout.modules.instances.at(-1).id;render();showInstanceProps(focusedModuleId);
      if(!report?.ok)flash(placementAdvice(focusedModuleId,before));
    }
    refreshModuleLibrary();
  }catch(e){moduleButton("modulePlaceError").textContent=zhModule(e.message);}};
}

// A definition draft is not a whole project and must never overwrite its file.
for(const id of ["save","saveTpl","describe","tweak","addModule","spice","waveforms"]){
  moduleButton(id).addEventListener("click",ev=>{
    if(!definitionEdit)return;ev.preventDefault();ev.stopImmediatePropagation();
    flash("请先保存模块定义或取消编辑，再操作整个项目。",true);
  },true);
}


// ---------- editing a module drawing ----------
// The canvas shows the assembled view (report.module_info.view). The table's
// own wires come first in it (root_wires maps view index -> table index), each
// instance's wires follow (instance_wires), then the link wires (link_wires).
let focusedLink = null, instanceDrag = null;
function viewInfo() { return report?.module_info || {}; }
function instanceOfWire(j) {
  return Object.entries(viewInfo().instance_wires || {}).find(([, [a, b]]) => j >= a && j < b)?.[0] || null;
}
function instanceOfTarget(ev) {
  const ids = new Set((layout.modules?.instances || []).map(i => i.id));
  const eid = ev.target?.closest?.("g.element[data-id]")?.dataset.id;
  if (eid?.includes("/") && ids.has(eid.split("/")[0])) return eid.split("/")[0];
  const wire = ev.target?.closest?.("[data-wire]");
  if (wire) return instanceOfWire(+wire.dataset.wire);
  const region = ev.target?.closest?.("[data-region]");
  if (region) {
    const id = (viewInfo().view?.regions || [])[+region.dataset.region]?.id || "";
    if (id.includes("/") && ids.has(id.split("/")[0])) return id.split("/")[0];
  }
  // anywhere inside a folded box, its title and mark included
  if (ev.clientX !== undefined) {
    const [x, y] = toUnits(ev);
    for (const inst of layout.modules?.instances || []) {
      const r = inst.display === "collapsed" && boxRect(inst.id);
      if (r && x >= r[0] && x <= r[2] && y >= r[1] && y <= r[3]) return inst.id;
    }
  }
  // a press on an instance captures the pointer, so the click / double-click
  // that follows arrives on the canvas itself: it belongs to that instance
  if ((ev.type === "click" || ev.type === "dblclick") && ev.target === svg && instancePress
      && ev.timeStamp - instancePress.at < PRESS_CLICK_MS) return instancePress.id;
  return null;
}
function moduleCanvasPointerDown(ev) {
  const info = viewInfo(), wire = ev.target.closest("[data-wire]");
  if (wire) {
    const j = +wire.dataset.wire;
    if (info.root_wires?.[j] === j) return false;           // the table's own wire
    const link = (info.link_wires || []).find(w => w.wire === j);
    if (link) { startLinkPick(ev, link); return true; }
  }
  const id = instanceOfTarget(ev);
  if (id) { startInstanceDrag(ev, id); return true; }
  const eid = ev.target.closest("g.element[data-id]")?.dataset.id;
  if (eid && (info.auto_parts || []).includes(eid)) {
    flash(`${eid} 由模块自动摆放在端口旁，随模块移动；要自己摆放，先解散所在实例`, true); return true;
  }
  return !!wire && info.root_wires?.[+wire.dataset.wire] === undefined;   // other derived wires
}

const PRESS_CLICK_MS = 600;   // a press and its click / double-click (browsers allow ~500 ms)
let instancePress = null;
function startInstanceDrag(ev, id) {
  instancePress = {id, at: ev.timeStamp};
  const info = viewInfo(), inst = layout.modules.instances.find(i => i.id === id);
  const redraw = !!selected;   // only a dropped element selection needs the full redraw
  focusedModuleId = id; focusedLink = null; selected = null;
  instanceDrag = {id, origin: info.origins?.[id] || inst.at || [0, 0], frame: info.frames?.[id], start: toUnits(ev), d: [0, 0]};
  svg.setPointerCapture(ev.pointerId);
  // Replacing the pressed SVG node would turn a double-click (fold / unfold)
  // into two single clicks, so normally only the focus outline is redrawn.
  if (redraw) render();
  else { document.getElementById("moduleFocus")?.remove(); document.getElementById("linkFocus")?.remove(); drawModuleFocus(); }
  showInstanceProps(id);
}
svg.addEventListener("pointermove", ev => {
  if (!instanceDrag) return;
  ev.stopImmediatePropagation();
  const p = toUnits(ev), d = [r4(snap(p[0] - instanceDrag.start[0])), r4(snap(p[1] - instanceDrag.start[1]))];
  instanceDrag.d = d;
  const f = instanceDrag.frame;
  if (!f) return;
  const ghost = document.getElementById("instanceGhost") || el("rect", {id: "instanceGhost", fill: "#2457d610",
    stroke: "var(--accent)", "stroke-width": .03, "stroke-dasharray": ".1 .06", "pointer-events": "none"}, svg);
  Object.entries({x: f[0] + d[0], y: -(f[3] + d[1]), width: f[2] - f[0], height: f[3] - f[1]}).forEach(([k, v]) => ghost.setAttribute(k, v));
}, true);
svg.addEventListener("pointerup", ev => {
  if (!instanceDrag) return;
  ev.stopImmediatePropagation();
  const {id, origin, d} = instanceDrag; instanceDrag = null;
  document.getElementById("instanceGhost")?.remove();
  if (!d[0] && !d[1]) return;
  pushHistory();
  layout.modules.instances.find(i => i.id === id).at = [r4(origin[0] + d[0]), r4(origin[1] + d[1])];
  changed(); flash(`已移动实例 ${id}；连线按端口重新连接`);
}, true);

function showInstanceProps(id) {
  const inst = layout.modules.instances.find(i => i.id === id), box = moduleButton("props");
  if (!inst) return;
  const own = !!layout.modules.definitions?.[inst.definition];
  // a bundled module named without a copy in this file: its ports come from the library list
  const ports = definitionPorts(inst.definition);
  box.className = "";   // not the two-column grid an element's fields use
  box.innerHTML = `<p><b>模块实例 ${esc(id)}</b>（${esc(inst.definition)}）</p>
    <p class="hint">拖动可整体移动；内部元件请在“编辑模块定义”里改，所有同类实例一起更新。</p>
    <p><button id="instParams">实例参数…</button> <button id="instLinks" ${ports ? "" : 'disabled title="模块定义不在本文件，模块库列表还没读到它的端口；请稍候再试"'}>端口连接…</button> <button id="instDisplay">展开 / 折叠</button></p>
    <p><button id="instDefinition" ${own ? "" : "disabled"}>编辑模块定义</button> <button id="instDissolve">解散为普通元件</button> <button id="instDelete">删除实例</button></p>
    <p><button id="instToLibrary" ${own ? "" : "disabled"}>存进我的模块库…</button> <span class="hint">${esc(librarySourceText(inst.definition))}</span></p>
    <div id="instUpdate"></div><div id="instAppearance"></div><div id="instGroup"></div>`;
  moduleButton("instToLibrary").onclick = () => saveToLibrary(inst.definition);
  showLibraryUpdate(inst.definition, moduleButton("instUpdate"));
  instanceAppearance(inst, moduleButton("instAppearance"));
  instanceGroup(inst, moduleButton("instGroup"));
  moduleButton("instParams").onclick = () => moduleParameters(id);
  moduleButton("instLinks").onclick = () => openModulePlacement(inst.definition, inst.at || [0, 0], id);
  moduleButton("instDisplay").onclick = () => {
    const cycle = ["expanded", "collapsed"].concat((viewInfo().partial || []).includes(id) ? ["partial"] : []);
    setModuleDisplay(id, cycle[(cycle.indexOf(inst.display || "expanded") + 1) % cycle.length]);
  };
  moduleButton("instDefinition").onclick = () => editModuleDefinition(id);
  moduleButton("instDissolve").onclick = async () => {
    try { await moduleCommit("/api/module-dissolve", {id}); focusedModuleId = null; } catch (e) { flash(zhModule(e.message), true); }
  };
  moduleButton("instDelete").onclick = () => deleteInstance(id);
}
function deleteInstance(id) {
  if (!confirm(`删除实例 ${id} 及其所有端口连接？模块定义保留在项目库里。`)) return;
  pushHistory();
  const m = layout.modules, touches = ref => ref.split(".")[0] === id;
  const cut = (m.links || []).filter(pair => pair.some(touches));
  m.instances = m.instances.filter(i => i.id !== id);
  if (m.links) m.links = m.links.filter(pair => !pair.some(touches));
  for (const key of Object.keys(m.routes || {})) if (key.split("|").some(touches)) delete m.routes[key];
  if (m.routes && !Object.keys(m.routes).length) delete m.routes;
  // an instance drawn for a group no longer stands for the deleted one
  for (const inst of m.instances.filter(i => i.group?.members?.includes(id))) {
    inst.group.members = inst.group.members.filter(x => x !== id);
    if (!inst.group.members.length) delete inst.group;
  }
  // a port of another instance that only went to this one is now open on purpose
  for (const ref of cut.flat().filter(r => !touches(r))) {
    const [iid, port] = [ref.split(".")[0], ref.split(".").slice(1).join(".")];
    const inst = m.instances.find(i => i.id === iid);
    if (inst && !m.links.some(pair => pair.includes(ref)) && !(inst.unconnected || []).includes(port))
      inst.unconnected = [...(inst.unconnected || []), port];
  }
  focusedModuleId = null; changed(); flash(`已删除实例 ${id}`);
}
function moduleFocusActive() { return !!(focusedModuleId || focusedLink); }

// ---------- links ----------
function linkKey(link) {
  const [a, b] = link, routes = layout.modules.routes || {};
  return routes[`${a}|${b}`] ? `${a}|${b}` : routes[`${b}|${a}`] ? `${b}|${a}` : `${a}|${b}`;
}
function startLinkPick(ev, link) {
  focusedLink = link; focusedModuleId = null; selected = null;
  render(); showLinkProps();
  startLinkSegmentDrag(ev, link);
}
function showLinkProps() {
  const link = focusedLink, box = moduleButton("props");
  if (!link) return;
  box.className = "";
  box.innerHTML = `<p><b>连接</b> ${esc(link.link[0])} ↔ ${esc(link.link[1])}</p>
    <p class="hint">${link.manual ? "按你画的走线连接。" : "自动走线。"}拖动线段可以改走向；连接本身按端口名保存，移动或折叠模块不会断开。</p>
    <p><button id="linkAuto" ${link.manual ? "" : "disabled"}>恢复自动走线</button> <button id="linkDelete">删除连接</button></p>`;
  moduleButton("linkAuto").onclick = () => {
    pushHistory(); delete layout.modules.routes?.[linkKey(link.link)];
    if (layout.modules.routes && !Object.keys(layout.modules.routes).length) delete layout.modules.routes;
    focusedLink = null; changed();
  };
  moduleButton("linkDelete").onclick = () => deleteLink(link.link);
}
// Check a candidate table before taking it. The page's check flag is held
// meanwhile, so nothing treats the page as settled or checks it in between.
async function trialCheck(candidate) {
  ++pendingTrialChecks; checking = true;
  try { return await api("/api/check", {layout: candidate, profile}); }
  catch (e) { return null; }
  finally {
    --pendingTrialChecks; checking = validating || pendingTrialChecks > 0;
    if (!checking && checkAgain) { checkAgain = false; revalidate(); }
  }
}
async function deleteLink([a, b]) {
  const before = JSON.stringify(layout), candidate = JSON.parse(before), m = candidate.modules;
  m.links = (m.links || []).filter(pair => !((pair[0] === a && pair[1] === b) || (pair[0] === b && pair[1] === a)));
  if (m.routes) { delete m.routes[`${a}|${b}`]; delete m.routes[`${b}|${a}`]; if (!Object.keys(m.routes).length) delete m.routes; }
  // an instance port left with no connection must be listed as deliberately open
  for (const ref of [a, b]) {
    const [iid, port] = [ref.split(".")[0], ref.split(".").slice(1).join(".")];
    const inst = m.instances.find(i => i.id === iid);
    if (inst && !m.links.some(pair => pair.includes(ref)) && !(inst.unconnected || []).includes(port))
      inst.unconnected = [...(inst.unconnected || []), port];
  }
  // Taking a link away only splits nets. A new short means the two ends still
  // touch in the full (expanded) drawing, which a folded view does not show.
  const trial = await trialCheck(candidate);
  if (JSON.stringify(layout) !== before) return;   // a newer edit owns the table
  const known = new Set((report?.issues || []).map(i => i.message));
  if (trial?.issues?.some(i => i.code === "netlist-short" && !known.has(i.message))) {
    flash(`没有删除连接 ${a} ↔ ${b}：两个实例的端口在展开图里重合，去掉连接后仍碰在一起，会成为看不见的短接。请先按住实例拖开，留出间距，再删除连接`, true);
    return;
  }
  pushHistory(); layout = candidate;
  focusedLink = null; changed(); flash(`已删除连接 ${a} ↔ ${b}`);
}
let linkDrag = null;
function startLinkSegmentDrag(ev, link) {
  const path = (viewInfo().view?.wires || [])[link.wire];
  if (!path) return;
  const p = toUnits(ev);
  // the segment under the pointer (end segments move with a new stub at the pin)
  let best = null;
  for (let k = 1; k < path.length; k++) {
    const a = path[k - 1], b = path[k], hor = Math.abs(a[1] - b[1]) < 1e-9;
    const dist = hor ? Math.abs(p[1] - a[1]) + Math.max(0, Math.min(a[0], b[0]) - p[0], p[0] - Math.max(a[0], b[0]))
                     : Math.abs(p[0] - a[0]) + Math.max(0, Math.min(a[1], b[1]) - p[1], p[1] - Math.max(a[1], b[1]));
    if (!best || dist < best.dist) best = {k, hor, dist};
  }
  if (!best) return;
  linkDrag = {link, path: path.map(q => q.slice()), seg: best.k, hor: best.hor, start: p, moved: false};
  svg.setPointerCapture(ev.pointerId);
}
svg.addEventListener("pointermove", ev => {
  if (!linkDrag) return;
  ev.stopImmediatePropagation();
  const p = toUnits(ev), d = linkDrag.hor ? r4(snap(p[1] - linkDrag.start[1])) : r4(snap(p[0] - linkDrag.start[0]));
  if (!d) return;
  linkDrag.moved = true;
  const pts = movedLinkPath(linkDrag.path, linkDrag.seg, linkDrag.hor, d);
  const ghost = document.getElementById("linkGhost") || el("polyline", {id: "linkGhost", fill: "none", stroke: "var(--accent)",
    "stroke-width": .04, "stroke-dasharray": ".1 .06", "pointer-events": "none"}, svg);
  ghost.setAttribute("points", pts.map(q => `${q[0]},${-q[1]}`).join(" "));
  linkDrag.result = pts;
}, true);
svg.addEventListener("pointerup", ev => {
  if (!linkDrag) return;
  ev.stopImmediatePropagation();
  const {link, moved, result} = linkDrag; linkDrag = null;
  document.getElementById("linkGhost")?.remove();
  if (!moved || !result) return;
  pushHistory();
  // store the bend points in the link's own order (pin/port ends are re-derived)
  const routes = layout.modules.routes = layout.modules.routes || {};
  delete routes[`${link.link[1]}|${link.link[0]}`];
  routes[`${link.link[0]}|${link.link[1]}`] = result.slice(1, -1).map(q => [r4(q[0]), r4(q[1])]);
  focusedLink = null; changed(); flash("已按拖动的位置记下这条连接的走线");
}, true);
// Move segment k (path[k-1] -> path[k]) by d across its direction; a moved
// end segment gets a short stub so the pin or port end stays where it is.
function movedLinkPath(path, k, hor, d) {
  let pts = path.map(q => q.slice());
  if (k === 1) { pts.splice(1, 0, pts[0].slice()); k += 1; }
  if (k === pts.length - 1) { pts.splice(pts.length - 1, 0, pts[pts.length - 1].slice()); }
  const axis = hor ? 1 : 0;
  pts[k - 1][axis] = r4(pts[k - 1][axis] + d); pts[k][axis] = r4(pts[k][axis] + d);
  return pts.filter((q, i) => i === 0 || q[0] !== pts[i - 1][0] || q[1] !== pts[i - 1][1]);
}
function drawLinkFocus() {
  const info = viewInfo(), w = focusedLink && (info.view?.wires || [])[focusedLink.wire];
  if (w) el("polyline", {id: "linkFocus", points: w.map(q => `${q[0]},${-q[1]}`).join(" "), fill: "none",
    stroke: "var(--accent)", "stroke-width": .07, opacity: .35, "pointer-events": "none"}, svg);
}

// A wire drawn to an instance port becomes a link: connections to modules are
// stored by name, so moving or folding an instance never breaks them. The
// wire's own bend points become the link's hand route.
const PORT_SNAP = 0.06;
function portAt(q) {
  return Object.entries(viewInfo().ports || {}).find(([, at]) => Math.abs(at[0] - q[0]) <= PORT_SNAP && Math.abs(at[1] - q[1]) <= PORT_SNAP)?.[0] || null;
}
function pinAt(q) {
  for (const e of layout.elements) for (const [p, at] of Object.entries(e.pins)) if (same(at, q)) return `${e.id}.${p}`;
  return null;
}
function wireToModuleLink(pts) {
  if (!layout.modules?.instances?.length) return false;
  const first = pts[0], last = pts[pts.length - 1];
  if (!portAt(first) && !portAt(last)) return false;          // an ordinary wire
  const a = portAt(first) || pinAt(first), b = portAt(last) || pinAt(last);
  if (!a || !b) { flash("接到模块端口的导线，另一端要落在元件引脚或另一个模块端口上；这条线没有保存", true); return true; }
  if (a === b) return true;
  pushHistory();
  const m = layout.modules; m.links = m.links || [];
  if (!m.links.some(pr => (pr[0] === a && pr[1] === b) || (pr[0] === b && pr[1] === a))) m.links.push([a, b]);
  const via = pts.slice(1, -1).map(q => [r4(q[0]), r4(q[1])]);
  if (via.length) { m.routes = m.routes || {}; delete m.routes[`${b}|${a}`]; m.routes[`${a}|${b}`] = via; }
  for (const ref of [a, b]) {       // a port now connected is no longer open on purpose
    const iid = ref.split(".")[0], port = ref.split(".").slice(1).join(".");
    const inst = m.instances.find(i => i.id === iid);
    if (inst?.unconnected) inst.unconnected = inst.unconnected.filter(p => p !== port);
  }
  changed(); flash(`已连接 ${a} ↔ ${b}；连接按端口名保存，移动或折叠模块不会断开`);
  return true;
}

// ---------- appearance ----------
const COLOR_NAMES = {green: "绿", blue: "蓝", orange: "橙", red: "红", purple: "紫", yellow: "黄", teal: "青", gray: "灰",
  none: "无", black: "黑", white: "白", magenta: "品红", cyan: "青"};
const BORDER_NAMES = {solid: "实线", dashed: "虚线", dotted: "点线", none: "无边框"};
function choice(values, current, names, onChange, id) {
  const sel = document.createElement("select"); if (id) sel.id = id;
  for (const v of values) sel.add(new Option(names[v] || v, v));
  sel.value = current; sel.onchange = () => onChange(sel.value); return sel;
}
function propRow(box, label, control) {
  const r = document.createElement("label"); r.style.cssText = "display:flex;gap:8px;align-items:center;margin:4px 0";
  const t = document.createElement("span"); t.textContent = label; t.style.minWidth = "5.5em";
  if (control.type !== "checkbox") control.style.flex = "1";
  r.append(t, control); box.appendChild(r);
}
// The folded box's title, fill and border; stored on the instance (appearance)
function instanceAppearance(inst, box) {
  const choices = report?.region_options || {fills: [], borders: []}, a = inst.appearance || {};
  box.innerHTML = "<p><b>折叠方框外观</b> <span class=\"hint\">（折叠显示时生效）</span></p>";
  const set = (key, value, fallback) => {
    const next = {...(inst.appearance || {}), [key]: value};
    if (next.border === "none" && (next.fill || "none") === "none") {
      flash("无边框时要先选填充色，否则方框看不见", true); showInstanceProps(inst.id); return;
    }
    pushHistory(); inst.appearance = {...(inst.appearance || {})};
    if (!value || value === fallback) delete inst.appearance[key]; else inst.appearance[key] = value;
    if (!Object.keys(inst.appearance).length) delete inst.appearance;
    changed();
  };
  const title = document.createElement("input"); title.id = "instTitle"; title.value = a.title || "";
  title.placeholder = inst.label?.text || inst.definition; title.onchange = () => set("title", title.value.trim(), "");
  propRow(box, "标题文字", title);
  propRow(box, "填充", choice(["none", ...choices.fills.filter(f => f !== "none")], a.fill || "none", COLOR_NAMES, v => set("fill", v, "none"), "instFill"));
  propRow(box, "边框", choice(["solid", "dashed", "dotted", "none"], a.border || "solid", BORDER_NAMES, v => set("border", v, "solid"), "instBorder"));
  const inks = Object.keys(choices.inks || {black: 1});
  propRow(box, "边框颜色", choice(inks.includes("black") ? inks : ["black", ...inks], a.border_color || "black", COLOR_NAMES,
    v => set("border_color", v === "black" ? "" : v, ""), "instBorderColor"));
}
// Draw this instance for a group of same-definition instances: the members
// stay in the circuit and are shown as panels stacked behind it.
function instanceGroup(inst, box) {
  const m = layout.modules, choices = report?.region_options || {fills: []};
  const inGroup = new Set(m.instances.filter(i => i.id !== inst.id).flatMap(i => i.group?.members || []));
  if (inGroup.has(inst.id)) {
    const owner = m.instances.find(i => (i.group?.members || []).includes(inst.id));
    box.innerHTML = `<p class="hint">本实例由 ${esc(owner.id)} 代表显示（画成它背后的底板）。</p>`; return;
  }
  const peers = m.instances.filter(i => i.id !== inst.id && i.definition === inst.definition && !inGroup.has(i.id) && !i.group);
  box.innerHTML = "<p><b>一组显示</b> <span class=\"hint\">（只画本实例，勾选的同类实例画成背后的底板，仿真照样包含它们）</span></p>";
  if (!peers.length && !inst.group) { box.insertAdjacentHTML("beforeend", "<p class=\"hint\">没有其他同类实例。</p>"); return; }
  const g = inst.group || {members: []};
  // Tried before it is taken: a member is drawn as a panel without its ports,
  // so a part placed beside them (modules.parts, such as RL) has nowhere to go.
  const apply = async change => {
    const before = JSON.stringify(layout), candidate = JSON.parse(before);
    const target = candidate.modules.instances.find(i => i.id === inst.id);
    if (!target) return;
    const next = change(target.group || {members: []});
    if (next.members.length) target.group = next; else delete target.group;
    const trial = await trialCheck(candidate);
    if (JSON.stringify(layout) !== before) return;   // a newer edit owns the table
    const broken = !trial ? "校验没有完成" : trial.issues?.find(i => i.code === "bad-module")?.message || (trial.module_info ? "" : "模块拼装失败");
    if (broken) {
      flash(`没有改一组显示：${zhModule(broken)}。被代表的实例不画端口，接在这些端口上的自动放置元件或连接就没有落点；可换一个实例来代表，或先改接线`, true);
      showInstanceProps(inst.id); return;
    }
    pushHistory(); layout = candidate; changed();
  };
  for (const id of [...new Set([...(g.members || []), ...peers.map(p => p.id)])]) {
    const c = document.createElement("input"); c.type = "checkbox"; c.checked = (g.members || []).includes(id); c.dataset.member = id;
    c.onchange = () => apply(now => ({...now, members: c.checked ? [...new Set([...(now.members || []), id])] : (now.members || []).filter(x => x !== id)}));
    propRow(box, id, c);
  }
  if (inst.group) {
    propRow(box, "底色", choice(choices.fills.filter(f => f !== "none"), g.fill || "gray", COLOR_NAMES, v => apply(now => ({...now, fill: v})), "groupFill"));
    const label = document.createElement("input"); label.id = "groupLabel"; label.value = g.label || "";
    label.placeholder = "如 Modules A / B / C"; label.onchange = () => apply(now => ({...now, label: label.value.trim() || undefined}));
    propRow(box, "标签", label);
  }
}

// ---------- module library: bundled modules, the user's own, this file's ----------
// A library module is copied into the file when placed (with its source), so a
// file never depends on a library; a newer library version is only offered.
let moduleCatalogue = null;
const MODULE_ICON = '<svg viewBox="0 0 32 24" aria-hidden="true"><path d="M1 8 H7 M1 16 H7 M25 8 H31 M25 16 H31 M10 18 L22 6"></path><rect x="7" y="3" width="18" height="18" rx="1"></rect></svg>';
const LIBRARY_SECTIONS = [["builtin", "内置"], ["user", "我的模块库"], ["project", "本文件"]];
let catalogueTicket = 0;
async function refreshModuleLibrary() {
  const ticket = ++catalogueTicket;
  let catalogue = null;
  try { catalogue = await api("/api/module-library", {layout, profile}); }
  catch (e) { catalogue = null; }
  if (ticket !== catalogueTicket) return;
  moduleCatalogue = catalogue;
  drawModuleLibrary();
  if (focusedModule()) showInstanceProps(focusedModuleId);
}
function catalogueEntry(definition) {
  return moduleCatalogue?.project?.find(e => e.name === definition);
}
function librarySourceText(definition) {
  const src = layout.modules?.definitions?.[definition]?.source;
  if (!src) return layout.modules?.definitions?.[definition] ? "本文件自建" : "内置模块（未复制进本文件）";
  return `来自${src.library === "builtin" ? "内置" : "我的"}模块库：${src.name}`;
}
// the component panel group, or the Insert -> add module dialog (dialog = true)
function drawModuleLibrary(box = moduleButton("moduleLibrary"), dialog = false) {
  if (!box) return;
  box.replaceChildren();
  if (!moduleCatalogue) return;
  const group = document.createElement("details"), heading = document.createElement("summary"), count = document.createElement("span");
  group.open = true; group.dataset.category = "模块";
  heading.append(document.createTextNode("模块（子系统）")); count.className = "category-count"; heading.append(count);
  group.append(heading);
  let total = 0;
  for (const [key, title] of LIBRARY_SECTIONS) {
    const entries = moduleCatalogue[key] || [];
    const h = document.createElement("p"); h.className = "module-library-heading"; h.textContent = title; group.append(h);
    if (!entries.length) {
      const empty = document.createElement("p"); empty.className = "hint module-library-empty";
      empty.textContent = key === "user" ? "还没有。选中模块实例后点“存进我的模块库…”。" : "本文件还没有模块。";
      group.append(empty); continue;
    }
    const cards = document.createElement("div"); cards.className = "component-grid"; group.append(cards);
    for (const entry of entries) {
      const b = document.createElement("button"); b.draggable = true; b.type = "button";
      b.dataset.moduleLibrary = entry.library; b.dataset.moduleName = entry.name;
      b.innerHTML = `${MODULE_ICON}<span>${esc(entry.title)}</span>`;
      b.title = `${entry.title}${entry.note ? "：" + entry.note : ""}　端口 ${entry.ports.join("、")}${entry.update ? "\n模块库里有新版本" : ""}${entry.library === "user" ? "\n右键可从我的模块库删除" : ""}`;
      b.classList.toggle("has-update", !!entry.update);
      b.onclick = () => {
        if (dialog) dlg.close();
        pendingModule = entry.library === "project" ? entry.name : entry;
        flash(`在画布点击放置 ${entry.title} 的位置`);
      };
      b.ondragstart = ev => {
        if (entry.library === "project") ev.dataTransfer.setData("application/x-circuit-module", entry.name);
        else ev.dataTransfer.setData("application/x-circuit-library-module", JSON.stringify(entry));
        ev.dataTransfer.effectAllowed = "copy";
        draggedModule = entry.library === "project" ? entry.name : entry;   // the canvas shows where it lands
        if (dialog) setTimeout(() => dlg.close(), 0);
      };
      if (entry.library === "user") b.oncontextmenu = async ev => {
        ev.preventDefault();
        if (!confirm(`从我的模块库删除 ${entry.title}？已经放进电路文件的副本不受影响。`)) return;
        try {
          moduleCatalogue = (await api("/api/module-library-delete", {layout, profile, name: entry.name})).catalogue;
          drawModuleLibrary(); flash(`已从我的模块库删除 ${entry.title}`);
        } catch (e) { flash(e.message, true); }
      };
      cards.append(b); total++;
    }
  }
  count.textContent = total;
  box.append(group);
  filterModuleLibrary();
}
function filterModuleLibrary() {
  const q = (document.getElementById("componentSearch")?.value || "").trim().toLowerCase();
  moduleButton("moduleLibrary")?.querySelectorAll("button").forEach(b =>
    b.hidden = !!q && !(b.textContent + " 模块 子系统 " + b.dataset.moduleName).toLowerCase().includes(q));
}
document.getElementById("componentSearch")?.addEventListener("input", filterModuleLibrary);
async function saveToLibrary(definition, quiet = false) {
  const existing = (moduleCatalogue?.user || []).some(e => e.name === definition);
  if (existing && !confirm(`我的模块库里已有 ${definition}，覆盖它？用过旧版本的电路文件不受影响，打开时会提示有新版本。`)) return;
  try {
    const result = await api("/api/module-library-save", {layout, profile, definition, force: existing});
    if (!quiet) pushHistory();
    layout = result.layout; moduleCatalogue = result.catalogue; catalogueTicket++;
    changed(); drawModuleLibrary();
    if (focusedModule()) showInstanceProps(focusedModuleId);   // its source line changed
    flash(`已存进我的模块库：${definition}（${result.saved}）`);
  } catch (e) { flash(e.message, true); }
}
function showLibraryUpdate(definition, box) {
  const entry = catalogueEntry(definition);
  box.replaceChildren();
  if (!entry?.update) return;
  box.innerHTML = `<p class="hint">模块库里的 ${esc(entry.source.name)} 有新版本${entry.edited ? "；本文件里的副本也改过，更新会覆盖这些修改" : ""}。</p>
    <p><button id="instLibraryUpdate">更新本文件的定义</button></p>`;
  moduleButton("instLibraryUpdate").onclick = async () => {
    if (entry.edited && !confirm("本文件里的这个模块改过，更新会用模块库的版本替换它。继续？")) return;
    try {
      await moduleCommit("/api/module-library-update", {definition});
      refreshModuleLibrary(); flash(`已按模块库更新 ${definition}，所有实例一起更新`);
    } catch (e) { flash(e.message, true); }
  };
}
// Called after every check: the catalogue follows this file's definitions
// (edits, undo, another file opened); the libraries themselves change only
// through the calls above.
let librarySignature = null;
function syncModuleLibrary() {
  const signature = JSON.stringify(layout?.modules?.definitions || {});
  if (signature === librarySignature) return;
  librarySignature = signature;
  refreshModuleLibrary();
}
