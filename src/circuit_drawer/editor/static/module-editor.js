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
  const d = moduleOffsets().get(focusedModuleId) || [0, 0];
  if (frame) el("rect", {id: "moduleFocus", x: frame[0]+d[0], y: -frame[3]-d[1], width: frame[2]-frame[0], height: frame[3]-frame[1],
    fill: "none", stroke: "var(--accent)", "stroke-width": .035, "stroke-dasharray": ".12 .08", "pointer-events": "none"}, svg);
}
// The folded box whose resize corner (drawn by drawResizeHandles) is under the pointer.
function resizeCornerAt(ev) {
  if (!layout?.modules?.links) return null;
  const [x, y] = toUnits(ev), grab = RESIZE_GRAB;
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
  moduleButton("menuModuleDisplay").disabled = !enabled;
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
  moduleButton("menuAll" + display[0].toUpperCase() + display.slice(1)).onclick = () => setModuleDisplay(null,display);
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
  const instance = instanceOfTarget(ev);
  const ref = objectAtTarget(ev.target) || (instance ? {type:'instance',id:instance} : null);
  if (selectionBusy) { ev.preventDefault(); ev.stopImmediatePropagation(); return true; }
  const title = ev.target.closest('[data-module-title]');
  if (title && !ev.shiftKey && !modePick && !groupHas(ref)) return startDragModuleTitle(ev, title.dataset.moduleTitle);
  const label = ev.target.closest('[data-element-label]');
  if (label && !ev.shiftKey && !modePick && startDragLabel(ev, label.dataset.elementLabel)) return true;
  if (ev.shiftKey && ref) {
    ev.preventDefault(); ev.stopImmediatePropagation();
    svg.focus({preventScroll:true});
    if (!selectionCount() && selected) groupAdd(selected);
    if (!selectionCount() && focusedModuleId) groupAdd({type:'instance',id:focusedModuleId});
    groupToggle(ref);
    selected = null; focusedModuleId = null; focusedLink = null;
    render(); moduleSelectionStatus(); return true;
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
    if (ev.shiftKey && focusedModuleId) groupAdd({type:'instance',id:focusedModuleId});
    moduleBox = {a: toUnits(ev), b: toUnits(ev), additive: ev.shiftKey, start:[ev.clientX,ev.clientY], pointer:ev.pointerId,
                 region: onRegion && layout.regions?.[regionIndex] ? regionIndex : undefined};
    svg.setPointerCapture(ev.pointerId); return true;
  }
  if (!ev.shiftKey) clearGroupSelection();
  return false;
}

function moduleSelectionStatus() {
  const box = moduleButton("props"); box.className = 'hint';
  if(instanceSelection.size) {
    box.textContent = `已选 ${selectionCount()} 个对象，其中 ${instanceSelection.size} 个模块实例。拖动选中对象或按方向键可一起移动；Shift+方向键十格，可一步撤销。只选三个以上模块时可右键“省略中间实例”。`;
    if(selectionCount()!==instanceSelection.size)box.append(document.createTextNode(' 省略前请取消选择普通元件、导线和图形。'));
    return;
  }
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
  moduleMarquee = false; selected = null; focusedModuleId = null; focusedLink = null; showProps(); render();
}, true);
svg.addEventListener('pointercancel', () => { moduleBox=null; render(); }, true);

async function moduleCommit(path, data, source = layout, refit = true) {
  const before = JSON.stringify(layout);
  const dialogContent = document.getElementById("dlgBody").firstChild;
  const result = await api(path, {layout: source, profile, ...data});
  if (JSON.stringify(layout) !== before) throw new Error("等待模块操作时图纸已改变，已保留当前编辑，请重试。");
  pushHistory(); layout = result.layout; selected = null; clearGroupSelection();
  if (document.getElementById("dlgBody").firstChild === dialogContent) dlg.close();
  await revalidate();
  if (refit) fitView(); render();
}

async function newEmptySubsystem(at) {
  try {
    await moduleCommit("/api/module-empty", {at: at.map(v => r4(snap(v)))});
    focusedModuleId = layout.modules.instances.at(-1).id;
    render(); showInstanceProps(focusedModuleId); refreshModuleLibrary();
    flash("空子系统已放置。选中后按 Enter 或右键“编辑模块定义”，进入内部搭电路。");
  } catch (error) { flash(error.message, true); }
}

document.addEventListener("keydown", ev => {
  if (ev.key !== "Enter" || ev.ctrlKey || ev.metaKey || ev.altKey || definitionEdit || dlg.open ||
      /INPUT|TEXTAREA|SELECT|BUTTON/.test(ev.target.tagName) || !focusedModuleId || checking ||
      wireDraft || boxDraft || pendingModule || selectionBusy || drag || moduleBox || instanceDrag || linkDrag || resizing) return;
  ev.preventDefault(); ev.stopImmediatePropagation(); editModuleDefinition(focusedModuleId);
}, true);

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
    <p><label>模块名 <input id="moduleName" value="${esc(state.name)}" placeholder="如 Buck、LLC_HB"></label>
      <span class="hint">以英文字母开头，可用字母、数字、下划线和短横线。${state.editing ? "改名会同步所有引用此定义的实例。" : ""}</span></p>
    <p>点击图上的圆点，把该节点设为子系统的对外端口，再在下方填写端口名并选择左边或右边。
      可直接选元件引脚或导线节点。红圈表示与所选电路外部相连的节点，必须设为端口。</p>
    <p><button id="portZoomIn" aria-label="放大端口画布">放大</button> <button id="portZoomOut" aria-label="缩小端口画布">缩小</button>
      <button id="portFit">适应所选电路</button> <button id="portExpand" aria-expanded="false">扩大画布</button>
      <span class="hint">滚轮缩放；空白处左键或中键拖动平移。</span></p>
    <div id="portDrawing" style="height:260px;border:1px solid #ddd;overflow:hidden"></div>
    <div id="portRows"></div><p id="portWarning" role="status"></p>
    <details open><summary>元件参数：勾选允许每个实例单独修改的项</summary>
      <p><button id="moduleParametersAll">全选</button> <button id="moduleParametersNone">全不选</button>
        <span class="hint">参数列表内按 Ctrl+A 全选，Ctrl+Shift+A 全不选。</span></p>
      <div id="moduleParameterRows" tabindex="0" aria-label="可按实例修改的元件参数" style="max-height:160px;overflow:auto"></div></details>
    ${layout.modes?.length ? "<p>封装会同步模态中的器件编号，模态仍保留在当前项目中。</p>" : ""}
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
  const vb = copy.viewBox.baseVal;
  copy.style.touchAction = "none";
  const nodes = el("g", {}, copy);
  const drawNodes = () => {
    const shown = copy.getBoundingClientRect(), perUnit = Math.min(shown.width / vb.width, shown.height / vb.height);
    const fontSize = 13 / perUnit, dotR = 5 / perUnit, gap = 5 / perUnit;
    nodes.innerHTML = "";
    info.nodes.forEach(n => {
      const port = state.ports.find(p => same(p.at,n.at));
      const dot = el("circle", {cx:n.at[0],cy:-n.at[1],r:dotR,fill:port ? "#2457d6" : "white",stroke:n.boundary ? "#d12f2f" : "#2457d6","stroke-width":dotR/3,style:"cursor:pointer"},nodes);
      dot.dataset.portNode = "true";
      dot.setAttribute("aria-label", `${port ? "端口 " + port.name : "设为端口"}，节点 ${n.net}，坐标 ${n.at.join(", ")}`);
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
  const point = ev => {
    const p = copy.createSVGPoint(); p.x = ev.clientX; p.y = ev.clientY;
    return p.matrixTransform(copy.getScreenCTM().inverse());
  };
  const zoom = (factor, p = {x:vb.x + vb.width / 2, y:vb.y + vb.height / 2}) => {
    vb.x = p.x - (p.x - vb.x) * factor; vb.y = p.y - (p.y - vb.y) * factor;
    vb.width *= factor; vb.height *= factor; drawNodes();
  };
  copy.addEventListener("wheel", ev => { ev.preventDefault(); zoom(ev.deltaY > 0 ? 1.1 : 1 / 1.1, point(ev)); }, {passive:false});
  let pan = null;
  copy.onpointerdown = ev => {
    if (ev.button !== 1 && (ev.button !== 0 || ev.target.closest("[data-port-node]"))) return;
    ev.preventDefault(); pan = point(ev); copy.setPointerCapture(ev.pointerId);
  };
  copy.onpointermove = ev => {
    if (!pan) return;
    const p = point(ev); vb.x += pan.x - p.x; vb.y += pan.y - p.y;
  };
  copy.onpointerup = copy.onpointercancel = ev => {
    pan = null; if (copy.hasPointerCapture(ev.pointerId)) copy.releasePointerCapture(ev.pointerId);
  };
  moduleButton("portZoomIn").onclick = () => zoom(1 / 1.25);
  moduleButton("portZoomOut").onclick = () => zoom(1.25);
  moduleButton("portFit").onclick = () => {
    const xs = info.nodes.map(n => n.at[0]), ys = info.nodes.map(n => -n.at[1]);
    if (!xs.length) return;
    vb.x = Math.min(...xs) - 1; vb.y = Math.min(...ys) - 1;
    vb.width = Math.max(...xs) - vb.x + 1; vb.height = Math.max(...ys) - vb.y + 1; drawNodes();
  };
  moduleButton("portExpand").onclick = ev => {
    const expanded = ev.target.getAttribute("aria-expanded") !== "true";
    ev.target.setAttribute("aria-expanded", expanded); ev.target.textContent = expanded ? "恢复画布高度" : "扩大画布";
    moduleButton("portDrawing").style.height = expanded ? "60vh" : "260px"; drawNodes();
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
      buttons[2].onclick=()=>{ if(state.editing && Object.hasOwn(definitionEdit.definition.ports,p.name)){moduleButton("moduleSaveError").textContent="已有实例引用端口，请保留名称；可在画布里移动端口节点。";return;} state.ports.splice(i,1);drawRows(); };
      row.ondragstart=ev=>{dragged=i;ev.dataTransfer.setData("text/plain",String(i));};
      row.ondragover=ev=>ev.preventDefault(); row.ondrop=ev=>{ev.preventDefault(); if(dragged===null)return;state.ports.splice(i,0,state.ports.splice(dragged,1)[0]);dragged=null;drawRows();};
      box.appendChild(row);
    });
    const missing=info.missing.filter(at=>!state.ports.some(p=>same(p.at,at)));
    const shared = new Map();
    for (const p of state.ports) {
      const net = info.nodes.find(n => same(n.at,p.at))?.net;
      if (!shared.has(net)) shared.set(net, []);
      shared.get(net).push(p.name);
    }
    const joined = [...shared.values()].filter(names => names.length > 1);
    moduleButton("portWarning").textContent=(missing.length ? `还缺 ${missing.length} 个外连端口：${missing.map(p=>p.join(", ")).join("；")}`
      : `已设置 ${state.ports.length} 个对外端口。` + (state.ports.length ? "保存后，它们会出现在子系统方框两侧。" : "请点击上图要引出的节点。"))
      + joined.map(names => ` ${names.join("、")} 属于同一内部网络，电气上互通。若要引出不同电位，请选择未被导线连通的节点。`).join("");
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
  const setAllParameters = checked => {
    state.exposed = checked ? state.parameters.map(p => ({element:p.element, field:p.field})) : [];
    paramRows.querySelectorAll('input[type="checkbox"]').forEach(input => input.checked = checked);
  };
  moduleButton("moduleParametersAll").onclick = () => setAllParameters(true);
  moduleButton("moduleParametersNone").onclick = () => setAllParameters(false);
  paramRows.onkeydown = ev => {
    if (!(ev.ctrlKey || ev.metaKey) || ev.key.toLowerCase() !== "a" ||
        ev.target.matches('input:not([type="checkbox"])')) return;
    ev.preventDefault(); ev.stopPropagation(); setAllParameters(!ev.shiftKey);
  };
  moduleButton("moduleSaveConfirm").onclick=async ev=>{
    ev.target.disabled=true;
    try {
      if(state.editing){
        const d=clone(definitionEdit.definition);d.layout=clone(layout);delete d.layout.simulation;d.defaults??={};
        for (const p of state.ports) {
          if (Object.hasOwn(d.ports,p.name)) continue;
          const terminal = d.layout.elements.find(e => e.kind === "port" && same(e.pins.p,p.at));
          if (terminal) { d.ports[p.name] = terminal.id + ".p"; continue; }
          const used = new Set(d.layout.elements.map(e => e.id));
          let id = "Port_" + p.name, n = 1; while (used.has(id)) id = "Port_" + p.name + n++;
          d.layout.elements.push({id,kind:"port",pins:{p:p.at.slice()}});
          d.layout.netlist[id+".p"] = info.nodes.find(node => same(node.at,p.at)).net;
          d.ports[p.name] = id + ".p";
        }
        d.port_layout={};state.ports.forEach((p,i)=>d.port_layout[p.name]={side:p.side,order:i});
        // Preserve the unrestricted legacy form when the user leaves every
        // permission enabled; a current field list would also limit future fields.
        const allExposed=state.parameters.every(p=>state.exposed.some(x=>x.element===p.element&&x.field===p.field));
        if(d.editable!==undefined || !allExposed)d.editable=state.exposed;
        for(const p of state.parameters)if(p.value!==""){d.defaults[p.element]??={};d.defaults[p.element][p.field]=p.value;}
        const source=definitionEdit.source;
        const result=await api("/api/module-definition",{layout:source,profile,name:definitionEdit.name,
          new_name:moduleButton("moduleName").value.trim(),definition:d});
        history=definitionEdit.history;redoHistory=definitionEdit.redoHistory;redoBase=definitionEdit.redoBase;
        layout=source;definitionEdit=null;moduleButton("definitionActions").hidden=true;
        pushHistory();layout=result.layout;selected=null;clearGroupSelection();dlg.close();await revalidate();fitView();render();refreshModuleLibrary();
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
  if(!definition){flash("这是内置模块；请先将其定义保存到项目库再编辑。",true);return;}
  definitionEdit={source:clone(layout),history:history.slice(),redoHistory:redoHistory.slice(),redoBase,name:inst.definition,definition:clone(definition)};
  layout=clone(definition.layout);history=[];redoHistory=[];redoBase=null;selected=null;clearGroupSelection();dlg.close();
  moduleButton("definitionActions").hidden=false;
  await revalidate();fitView();render();flash("正在编辑子系统内部。搭好电路后点“设置端口并保存…”，在图上点选要引出的节点，再保存返回上层。");
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
    if(checking){flash("请等当前连接检查完成后再保存定义");return;}
    if(!d.layout.elements.length)adoptNetlist(false);
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
  showDialog("添加模块",`<p>点击模块后在画布点选位置，或将按钮拖到画布直接放置。放好后从端口画线，或把端口拖到已有导线上。左侧器件库的“模块（子系统）”分组也能直接放。</p><div id="addModuleLibrary"></div>`);
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
  if(lib)openModulePlacement(null,toUnits(ev),JSON.parse(lib));});
// Window capture runs before the projection's pointer guard.
window.addEventListener("pointerdown",ev=>{
  if(!pendingModule||!svg.contains(ev.target)||ev.button!==0)return;
  ev.preventDefault();ev.stopImmediatePropagation();const pick=pendingModule;pendingModule=null;placeGhost(null);
  if(typeof pick==="string")openModulePlacement(pick,toUnits(ev));else openModulePlacement(null,toUnits(ev),pick);
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
// Server messages quote the validator after a Chinese lead-in ("请先修正电路：…；…"):
// word each quoted problem as the issue list does.
function zhModule(msg){
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
async function openModulePlacement(name, point, entry = null) {
  const ports = entry ? entry.ports : definitionPorts(name);
  if (!ports) { flash(`还没读到模块 ${name} 的端口；请稍候再试`, true); refreshModuleLibrary(); return; }
  const connections = Object.fromEntries(ports.map(port => [port, ""]));
  const at = placedAt(point, pickBox(entry || name));
  const before = {scale: report?.scale_cm_per_unit?.[0], issues: new Set((report?.issues || []).map(i => i.code + "|" + i.message))};
  try {
    if (entry) await moduleCommit("/api/module-library-place", {library: entry.library, name: entry.name, at, connections});
    else await moduleCommit("/api/module-add", {name, at, connections});
    focusedModuleId = layout.modules.instances.at(-1).id;
    render(); showInstanceProps(focusedModuleId); refreshModuleLibrary();
    flash(report?.ok ? "模块已放置。可直接从端口画线，或把端口拖到已有导线上。" : placementAdvice(focusedModuleId, before));
  } catch (error) { flash(zhModule(error.message), true); }
}

// A definition draft is not a whole project and must never overwrite its file.
for(const id of ["save","saveAs","saveTpl","openExample","describe","tweak","addModule","spice","waveforms"]){
  moduleButton(id).addEventListener("click",ev=>{
    if(!definitionEdit)return;ev.preventDefault();ev.stopImmediatePropagation();
    flash("请先保存模块定义或取消编辑，再操作整个项目。",true);
  },true);
}


// ---------- editing a module drawing ----------
// The canvas shows the assembled view (report.module_info.view). The table's
// own wires come first in it (root_wires maps view index -> table index), each
// instance's wires follow (instance_wires), then the link wires (link_wires).
let focusedLink = null, instanceDrag = null, modulePreview = null;
function drawModuleMotion() {
  const offsets = moduleOffsets();
  const keys = {annotations:'ann', regions:'region', blocks:'block', callouts:'callout', glyphs:'glyph', wave_symbols:'wave-symbol'};
  for (const [id, sections] of Object.entries(viewInfo().instance_decorations || {})) {
    const d = offsets.get(id); if (!d) continue;
    for (const [section, key] of Object.entries(keys)) {
      const [first, end] = sections[section] || [0, 0];
      for (const node of svg.querySelectorAll(`g[data-${key}]`)) {
        const index = +node.getAttribute(`data-${key}`);
        if (index >= first && index < end) node.setAttribute('transform', `translate(${d[0]} ${-d[1]}) ${node.getAttribute('transform') || ''}`);
      }
    }
  }
}
function viewInfo() { return report?.module_info || {}; }
function instanceOfWire(j) {
  return Object.entries(viewInfo().instance_wires || {}).find(([, [a, b]]) => j >= a && j < b)?.[0] || null;
}
function instanceOfTarget(ev) {
  const ids = new Set((layout.modules?.instances || []).map(i => i.id));
  const eid = ev.target?.closest?.("g.element[data-id]")?.dataset.id;
  if (eid?.includes("/") && ids.has(eid.split("/")[0])) return eid.split("/")[0];
  if (eid && layout.elements.some(e => e.id === eid)) return null;
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
    if (info.root_wires?.[j] !== undefined) return false;   // the table's own wire
    const link = (info.link_wires || []).find(w => w.wire === j);
    if (link) { startLinkPick(ev, link); return true; }
  }
  const id = instanceOfTarget(ev);
  if (id) { startInstanceDrag(ev, id); return true; }
  const eid = ev.target.closest("g.element[data-id]")?.dataset.id;
  if (eid && (info.auto_parts || []).includes(eid)) {
    startAutoPartDrag(ev, eid); return true;
  }
  return !!wire && info.root_wires?.[+wire.dataset.wire] === undefined;   // other derived wires
}

function autoPartMover(id, drawn = null) {
  const part = layout.modules.parts.find(p => p.id === id);
  drawn = clone(drawn || currentView(layout, viewInfo()).elements.find(e => e.id === id));
  const offset = part.offset || [0, 0], pins = clone(drawn.pins), move = pinMover(drawn);
  return next => {
    const delta = next.map((v, k) => r4(v - offset[k]));
    move(Object.fromEntries(Object.entries(pins).map(([pin, at]) => [pin, shiftedPoint(at, delta)])));
    part.offset = next.map(r4);
  };
}

function showAutoPartProps(id, box) {
  const part = layout.modules?.parts?.find(p => p.id === id);
  if (!part) return;
  const name = document.createElement('span'); name.textContent = `${id}（${part.kind}）`;
  row(box, '元件', name);
  const hint = document.createElement('p'); hint.className = 'hint';
  hint.textContent = '位置按端口相对偏移保存，展开和折叠时分别计算。';
  hint.style.gridColumn = '1 / -1'; box.appendChild(hint);
  for (const [axis, label] of [[0, '水平偏移'], [1, '竖直偏移']]) {
    const input = numInput((part.offset || [0, 0])[axis], value => {
      const next = [...(part.offset || [0, 0])]; next[axis] = value;
      autoPartMover(id)(next);
    });
    input.id = axis ? 'modulePartOffsetY' : 'modulePartOffsetX';
    row(box, label, input);
  }
  const label = document.createElement('input'); label.value = part.label || '';
  label.id = 'elementLabel';
  label.onchange = () => { pushHistory(); part.label = label.value; changed(); };
  row(box, '标签', label);
  if (report.symbol_styles?.kinds.includes(part.kind)) {
    const style = symbolStyleSelect(part.opts?.symbol_style); style.id = 'elementSymbolStyle';
    style.onchange = () => { pushHistory(); setSymbolStyle(part, style.value); changed(); };
    row(box, '符号风格', style);
  }
}

function startAutoPartDrag(ev, id) {
  ev.preventDefault();
  ev.stopImmediatePropagation();
  svg.setPointerCapture(ev.pointerId);
  svg.focus({preventScroll:true});
  clearGroupSelection(); focusedModuleId = null; focusedLink = null;
  selected = {type:'modulePart', id};
  showSidebar('properties'); showProps();
  const before = clone(layout), start = toUnits(ev);
  // Use the existing selection transaction so a click or cancellation keeps the source.
  const transaction = {
    group: true, deferCheck: true, source: before, members: [],
    moved: false, point: start, alt: false,
    move(point, event) {
      this.point = point;
      this.alt = !!event?.altKey;
      if (!this.apply) return;
      if (!this.moved && same(point, start)) return;
      const delta = this.guides.snap(point[0] - start[0], point[1] - start[1], this.alt);
      if (!this.moved && same(delta, [0, 0])) return;
      if (!this.moved) {
        pushHistory();
        this.moved = true;
      }
      this.apply(shiftedPoint(this.offset, delta));
    }
  };
  drag = transaction;
  selectionBusy = true;
  syncSelectionControls();
  transaction.ready = api('/api/module-part-prepare', {layout:before, profile, id}).then(result => {
    if (drag !== transaction) return;
    if (editorTableSnapshot(layout) !== editorTableSnapshot(before)) {
      transaction.error = '图纸已变化，请重新选择后拖动';
      return;
    }
    transaction.offset = result.offset;
    transaction.apply = autoPartMover(id, result.element);
    transaction.guides = projected(() => smartGuides(result.element));
    transaction.move(transaction.point, {altKey:transaction.alt});
    render();
  }).catch(error => { transaction.error = error.message; });
}

const PRESS_CLICK_MS = 600;   // a press and its click / double-click (browsers allow ~500 ms)
let instancePress = null;
function startInstanceDrag(ev, id) {
  instancePress = {id, at: ev.timeStamp};
  const info = viewInfo(), inst = layout.modules.instances.find(i => i.id === id);
  const redraw = !!selected;   // only a dropped element selection needs the full redraw
  focusedModuleId = id; focusedLink = null; selected = null;
  instanceDrag = {id, origin: info.origins?.[id] || inst.at || [0, 0], start: toUnits(ev), d: [0, 0],
    before: JSON.stringify(layout), table: clone(layout), pointer: ev.pointerId};
  modulePreview = instanceDrag;
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
  if (same(d, instanceDrag.d)) return;
  instanceDrag.d = d;
  instanceDrag.table.modules.instances.find(i => i.id === instanceDrag.id).at = shiftedPoint(instanceDrag.origin, d);
  renderNextFrame();
}, true);
svg.addEventListener("pointerup", ev => {
  if (!instanceDrag) return;
  ev.stopImmediatePropagation();
  const pending = instanceDrag, {id, origin, d} = pending; instanceDrag = null;
  if (!d[0] && !d[1]) { modulePreview = null; return; }
  if (JSON.stringify(layout) !== pending.before) {
    modulePreview = null; render(); flash('等待模块操作时图纸已改变，已保留当前编辑，请重试。', true); return;
  }
  moveModuleInstance(id, shiftedPoint(origin, d)).finally(() => {
    if (modulePreview === pending) modulePreview = null;
    render();
  });
}, true);
function cancelInstanceDrag() {
  if (!instanceDrag) return;
  const pointer = instanceDrag.pointer;
  instanceDrag = modulePreview = instancePress = null;
  if (svg.hasPointerCapture(pointer)) svg.releasePointerCapture(pointer);
  renderNextFrame();
}
window.addEventListener('pointercancel', cancelInstanceDrag);
window.addEventListener('blur', cancelInstanceDrag);
document.addEventListener('keydown', ev => { if (ev.key === 'Escape') cancelInstanceDrag(); });

async function moveModuleInstance(id, at) {
  const before = JSON.stringify(layout), candidate = clone(layout);
  const inst = candidate.modules.instances.find(i => i.id === id);
  inst.at = at;
  selectionBusy = true; ++pendingTrialChecks; checking = true;
  let failure = null;
  try {
    await checkModuleGeometry(candidate, JSON.parse(before));
    if (JSON.stringify(layout) !== before) throw new Error("等待模块操作时图纸已改变，已保留当前编辑，请重试。");
    await moduleCommit("/api/module-contact", {id}, candidate, false);
  } catch (error) { failure = error; }
  finally {
    --pendingTrialChecks; checking = validating || pendingTrialChecks > 0;
    if (!checking) checkAgain = false;
    await revalidate(); selectionBusy = false;
  }
  flash(failure ? failure.message : `已移动实例 ${id}，连接状态见问题栏`, !!failure);
  return !failure;
}

let moduleNudges = null;
async function nudgeModuleInstance(id, delta) {
  if (moduleNudges) { moduleNudges.push({id, delta}); return; }
  moduleNudges = [{id, delta}];
  try {
    while (moduleNudges.length) {
      const {id, delta} = moduleNudges.shift();
      const inst = layout.modules?.instances.find(i => i.id === id);
      if (!inst) break;
      const at = inst.at || viewInfo().origins?.[id];
      if (!at || !await moveModuleInstance(id, at.map((v, k) => r4(v + delta[k])))) break;
    }
  } finally { moduleNudges = null; }
}

function showInstanceProps(id) {
  const inst = layout.modules.instances.find(i => i.id === id), box = moduleButton("props");
  if (!inst) return;
  const own = !!layout.modules.definitions?.[inst.definition];
  box.className = "";   // not the two-column grid an element's fields use
  box.innerHTML = `<p><b>模块实例 ${esc(id)}</b>（${esc(inst.definition)}）</p>
    <p class="hint">拖动可整体移动；内部元件请在“编辑模块定义”里改，所有同类实例一起更新。</p>
    <p><button id="instParams">实例参数…</button> <button id="instDisplay">展开 / 折叠</button></p>
    <p><button id="instDefinition" ${own ? "" : "disabled"}>编辑模块定义</button> <button id="instDissolve">解散为普通元件</button> <button id="instDelete">删除实例</button></p>
    <p><button id="instToLibrary" ${own ? "" : "disabled"}>存进我的模块库…</button> <span class="hint">${esc(librarySourceText(inst.definition))}</span></p>
    <div id="instUpdate"></div><div id="instAppearance"></div><div id="instGroup"></div>`;
  moduleButton("instToLibrary").onclick = () => saveToLibrary(inst.definition);
  showLibraryUpdate(inst.definition, moduleButton("instUpdate"));
  instanceAppearance(inst, moduleButton("instAppearance"));
  instanceGroup(inst, moduleButton("instGroup"));
  moduleButton("instParams").onclick = () => moduleParameters(id);
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
  if (!cdI18n.confirm(`删除实例 ${id} 及其所有端口连接？模块定义保留在项目库里。`)) return;
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
  if (!m.instances.length && !layout.modes?.length && layout.module_authoring?.modes) layout.modes = clone(layout.module_authoring.modes);
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
  try {
    const result = await api("/api/check", {layout: candidate, profile});
    return Array.isArray(result?.issues) ? result : null;
  }
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
  // Taking a link away only splits nets. Refuse when visible ports still
  // touch, or when the remaining drawing would short the newly separated nets.
  const trial = await trialCheck(candidate);
  if (JSON.stringify(layout) !== before) return;   // a newer edit owns the table
  if (!trial?.module_info || trial.issues.some(i => i.code === "bad-module")) {
    flash("校验没有完成，未删除连接。请检查服务和模块连接后重试", true); return;
  }
  const known = new Set((report?.issues || []).map(i => i.message));
  const ports = trial.module_info.ports;
  if ((ports[a] && ports[b] && same(ports[a], ports[b])) ||
      trial.issues.some(i => i.code === "netlist-short" && !known.has(i.message))) {
    flash(`没有删除连接 ${a} ↔ ${b}：图中的端口仍重合或相连，直接删除会造成接线与网表不一致。请先拖开实例或调整接线，再删除连接`, true);
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

// Public module pins take part in the ordinary wire tool.
const PORT_SNAP = 0.06;
function portAt(q) {
  return Object.entries(viewInfo().ports || {}).find(([, at]) => Math.abs(at[0] - q[0]) <= PORT_SNAP && Math.abs(at[1] - q[1]) <= PORT_SNAP)?.[0] || null;
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
  let editVersion = 0;
  box.innerHTML = "<p><b>折叠方框外观</b> <span class=\"hint\">（折叠显示时生效）</span></p>";
  const set = async (key, value, fallback) => {
    const version = ++editVersion;
    const next = {...(inst.appearance || {}), [key]: value};
    if (next.border === "none" && (next.fill || "none") === "none") {
      flash("无边框时要先选填充色，否则方框看不见", true); showInstanceProps(inst.id); return;
    }
    const before = JSON.stringify(layout), candidate = clone(layout);
    const target = candidate.modules.instances.find(i => i.id === inst.id);
    target.appearance = {...(target.appearance || {})};
    if (key !== "title" && (!value || value === fallback)) delete target.appearance[key]; else target.appearance[key] = value;
    if (!Object.keys(target.appearance).length) delete target.appearance;
    try {
      if (key === "title") await checkModuleGeometry(candidate, JSON.parse(before));
      if (version !== editVersion || JSON.stringify(layout) !== before) return;
      pushHistory(); layout = candidate; changed();
    } catch (error) { flash(error.message, true); }
  };
  const title = document.createElement("input"); title.id = "instTitle";
  const ownTitle = Object.keys(viewInfo().titles || {}).find(i => viewInfo().titles[i].id === inst.id);
  title.value = a.title ?? viewInfo().view?.annotations?.[ownTitle]?.text ?? inst.label?.text ?? inst.definition;
  title.placeholder = "留空隐藏标题"; title.onchange = () => set("title", title.value.trim());
  propRow(box, "标题文字", title);
  propRow(box, "填充", choice(["none", ...choices.fills.filter(f => f !== "none")], a.fill || "none", COLOR_NAMES, v => set("fill", v, "none"), "instFill"));
  propRow(box, "边框", choice(["solid", "dashed", "dotted", "none"], a.border || "solid", BORDER_NAMES, v => set("border", v, "solid"), "instBorder"));
  const inks = Object.keys(choices.inks || {black: 1});
  propRow(box, "边框颜色", choice(inks.includes("black") ? inks : ["black", ...inks], a.border_color || "black", COLOR_NAMES,
    v => set("border_color", v === "black" ? "" : v, ""), "instBorderColor"));
}
function startDragModuleTitle(ev, id) {
  const inst = layout.modules?.instances.find(item => item.id === id);
  if (!inst) return false;
  ev.preventDefault(); ev.stopImmediatePropagation(); svg.setPointerCapture(ev.pointerId);
  selected = null; focusedModuleId = id; showInstanceProps(id); svg.focus({preventScroll:true});
  const start = toUnits(ev), screen = [ev.clientX, ev.clientY], offset = inst.appearance?.title_offset || [0, 0];
  drag = {label: 'module:' + id, moved: false, move(p, event) {
    if (!this.moved) {
      if (Math.hypot(event.clientX - screen[0], event.clientY - screen[1]) < WIRE_DRAG_PX) return;
      pushHistory(); this.moved = true;
    }
    inst.appearance = {...inst.appearance, title_offset: offset.map((v, k) => r4(v + p[k] - start[k]))};
  }};
  return true;
}
// Draw this instance for a group of same-definition instances: the members
// stay in the circuit and are shown as panels stacked behind it.
function instanceGroup(inst, box) {
  const m = layout.modules, choices = report?.region_options || {fills: []};
  const inGroup = new Set(m.instances.filter(i => i.id !== inst.id).flatMap(i => i.group?.members || []));
  if (inGroup.has(inst.id)) {
    const owner = m.instances.find(i => (i.group?.members || []).includes(inst.id));
    if(owner.group.style==='ellipsis') {
      box.textContent='本实例属于省略显示组；右键首尾实例可恢复全部实例。'; return;
    }
    box.innerHTML = `<p class="hint">本实例由 ${esc(owner.id)} 代表显示（画成它背后的底板）。</p>`; return;
  }
  if(inst.group?.style==='ellipsis') {
    box.textContent='中间实例已省略；右键首尾实例可恢复全部实例。'; return;
  }
  const peers = m.instances.filter(i => i.id !== inst.id && i.definition === inst.definition && !inGroup.has(i.id) && !i.group);
  box.innerHTML = "<p><b>一组显示</b> <span class=\"hint\">（只画本实例，勾选的同类实例画成背后的底板，仿真照样包含它们）</span></p>";
  if (!peers.length && !inst.group) { box.insertAdjacentHTML("beforeend", "<p class=\"hint\">没有其他同类实例。</p>"); return; }
  const g = inst.group || {members: []};
  // Check the derived view before replacing the current drawing.
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
      flash(`没有改一组显示：${zhModule(broken)}。请检查模块定义和端口连接；原图已保留`, true);
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
      b.innerHTML = `${MODULE_ICON}<span${entry.library === "builtin" ? "" : ' translate="no"'}>${esc(entry.title)}</span>`;
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
        if (!cdI18n.confirm(`从我的模块库删除 ${entry.title}？已经放进电路文件的副本不受影响。`)) return;
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
    b.hidden = !!q && ![b.textContent, cdI18n.sourceText(b), cdI18n.translate(cdI18n.sourceText(b)), "模块 子系统", cdI18n.translate("模块（子系统）"), b.dataset.moduleName].join(" ").toLowerCase().includes(q));
}
document.getElementById("componentSearch")?.addEventListener("input", filterModuleLibrary);
async function saveToLibrary(definition, quiet = false) {
  const existing = (moduleCatalogue?.user || []).some(e => e.name === definition);
  if (existing && !cdI18n.confirm(`我的模块库里已有 ${definition}，覆盖它？用过旧版本的电路文件不受影响，打开时会提示有新版本。`)) return;
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
    if (entry.edited && !cdI18n.confirm("本文件里的这个模块改过，更新会用模块库的版本替换它。继续？")) return;
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
