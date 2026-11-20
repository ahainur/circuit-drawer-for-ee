/* Paper objects share the waveform's transaction, channel rows and printed units. */
"use strict";
window.createWaveformInsets = function ({state,cm,stamp,request,change,draw,properties,note,svgPoint,dataAt,clearNotes}) {
  const $=id=>document.getElementById(id), chart=$("waveChart"), menu=$("waveInsertMenu");
  const tr=text=>window.cdI18n?.translate(text) || text;
  const titles={legend:"图例",topology:"拓扑示意",icon:"系统图标",image:"个人图片"};
  const assets=new Map();
  const clamp=(value,lo,hi)=>Math.max(lo,Math.min(hi,value));
  let selected=null, pending=null, drag=null, ticket=0, nudges=Promise.resolve();
  const current=()=>state().cfg?.insets?.find(i=>i.id===selected);
  const openMenu=open=>{menu.hidden=!open;$("waveInsert").setAttribute("aria-expanded",String(open));};
  function reset() {
    ++ticket; selected=pending=drag=null; openMenu(false);
    chart.classList.remove("wave-place");
    if ($("systemSymbolLibrary")?.classList.contains("wave-symbol-picker")) $("systemSymbolLibrary").close();
    if ($("waveTopologyPicker")?.open) $("waveTopologyPicker").close();
  }
  function sync(visible) {
    if (!visible?.some(i=>i.id===selected)) selected=null;
  }
  function ready(item) {
    clearNotes(); pending=item; openMenu(false); chart.classList.add("wave-place");
    chart.focus({preventScroll:true});
    note(tr("点击绘图区放置，Enter 使用默认位置，Esc 取消。"));
  }
  function freshId(items) {
    let n=1;
    while (items.some(i=>i.id==="inset"+n)) n++;
    return "inset"+n;
  }
  function update(id, edit) {
    const cfg=clone(state().cfg), item=cfg.insets?.find(i=>i.id===id);
    if (item) {edit(item);return change(cfg);}
  }
  function remove() {
    if (!selected) return;
    const cfg=clone(state().cfg);
    cfg.insets=cfg.insets.filter(i=>i.id!==selected); selected=null;
    return change(cfg);
  }
  function place(source, point) {
    const {info,box}=state();
    if (!info || !box) return;
    const loc=dataAt(point);
    const row=info.paper.rows.find(r=>r.columns.includes(loc.channel));
    const top=box.rows[loc.channel].y0;
    if (point[0]<box.left || point[0]>box.right || point[1]<top || point[1]>top+box.rowHeight) {
      note(tr("请在坐标轴内放置插图。"));return;
    }
    const cfg=clone(state().cfg), items=cfg.insets ||= [];
    let item=source.kind==="legend" && items.find(i=>i.kind==="legend" && row.columns.includes(i.channel));
    if (!item) {
      item={...source,id:freshId(items),channel:loc.channel};
      if (item.kind!=="legend") item.width_cm ||= item.kind==="topology"?3.1:1.45;
      if (item.symbol==="meter") item.probe_s=loc.point[0];
      items.push(item);
    }
    // Anchor the insertion within the chosen row even when its paper height changes.
    item.at=[(point[0]-box.left)/(box.right-box.left),(point[1]-top)/box.rowHeight].map(v=>clamp(v,0,1));
    cfg.plot_style ||= {...info.plot_defaults};
    pending=null;chart.classList.remove("wave-place");clearNotes(); selected=item.id;
    change(cfg);chart.focus({preventScroll:true});
  }
  function defaultPoint() {
    const b=state().box;
    return [b.left+.62*(b.right-b.left),b.top+.16*b.rowHeight];
  }
  function acceptImage(asset) {
    ready({kind:"image",source:asset.source,mono_source:asset.mono_source,size:asset.pixel_size,
      label:Array.from(asset.name).slice(0,60).join("")});
  }
  function pickImage(file) {
    const before=stamp();
    window.CircuitFigureImages.pick(asset=>{
      if (before===stamp() && !$("wavePanel").hidden) acceptImage(asset);
      else note(tr("图纸已改变，请重新选择素材。"),true);
    },file);
  }
  function assetURL(item) {
    if (item.kind==="image") return window.CircuitFigureImages.preview(item.source);
    if (!assets.has(item.source)) {
      const url=request("/api/waveform-inset",state().cfg,{action:"preview",source:item.source})
        .then(r=>"data:image/svg+xml;base64,"+r.svg);
      assets.set(item.source,url);
      url.catch(()=>assets.delete(item.source));
    }
    return assets.get(item.source);
  }
  const dialog=document.createElement("dialog");
  dialog.id="waveTopologyPicker";document.body.append(dialog);
  function topologyPicker(existing=null) {
    const before=stamp(), run=++ticket;
    const modes=layout.modes || [];
    dialog.innerHTML=`<form method="dialog"><header><strong>拓扑示意</strong><button aria-label="关闭">×</button></header></form>
      <label>来源 <select id="waveTopologyMode" aria-describedby="hint-waveCapture"><option value="">当前原理图</option>${modes.map(m=>`<option value="${esc(m.name)}">${esc(m.name)}</option>`).join('')}</select></label> ${editorHint("waveCapture")}
      <p class="hint">拖出矩形可截取局部。直接采用可放入整图。</p>
      <svg id="waveTopologyCrop" aria-label="拓扑裁切区域"></svg>
      <p id="waveCaptureState" role="status"></p><button id="waveCropAll">整图</button> <button id="waveCaptureUse" class="primary" disabled>采用拓扑</button>`;
    const choose=$("waveTopologyMode"), canvas=$("waveTopologyCrop"), btn=$("waveCaptureUse");
    choose.value=existing?.mode || "";
    let source=null, crop=existing?.crop?.slice(), start=null, capture=0;
    const valid=()=>dialog.open && run===ticket && before===stamp();
    function outline() {
      canvas.querySelector("rect")?.remove();
      if (crop && source) {
        const [w,h]=source.size,[l,t,r,b]=crop;
        el("rect",{x:l*w,y:t*h,width:(r-l)*w,height:(b-t)*h,fill:"#2457d6","fill-opacity":.12,stroke:"#2457d6","vector-effect":"non-scaling-stroke"},canvas);
      }
    }
    async function captureSource() {
      const n=++capture;btn.disabled=true;source=null;canvas.replaceChildren();
      $("waveCaptureState").textContent=tr("正在生成矢量快照…");
      try {
        const r=await request("/api/waveform-inset",state().cfg,{action:"capture",mode:choose.value || null});
        if (n!==capture || !dialog.open || run!==ticket) return;
        if (!valid()) throw new Error(tr("图纸已改变，请重新选择素材。"));
        const url="data:image/svg+xml;base64,"+r.svg;
        assets.set(r.source,Promise.resolve(url));
        source={kind:"topology",source:r.source,mono_source:r.mono_source,size:r.size,...(choose.value?{mode:choose.value}:{})};
        const [w,h]=r.size;canvas.setAttribute("viewBox",`0 0 ${w} ${h}`);
        el("image",{x:0,y:0,width:w,height:h,href:url},canvas);outline();
        $("waveCaptureState").textContent="";btn.disabled=false;
      } catch(error) {if (n===capture && dialog.open && run===ticket) $("waveCaptureState").textContent=error.message;}
    }
    function point(ev) {
      const p=new DOMPoint(ev.clientX,ev.clientY).matrixTransform(canvas.getScreenCTM().inverse());
      return [clamp(p.x/source.size[0],0,1),clamp(p.y/source.size[1],0,1)];
    }
    canvas.onpointerdown=e=>{if(source && e.button===0) {start=point(e);canvas.setPointerCapture(e.pointerId);}};
    canvas.onpointermove=e=>{
      if(!start || !source) return;
      const [x,y]=point(e);
      crop=[Math.min(x,start[0]),Math.min(y,start[1]),Math.max(x,start[0]),Math.max(y,start[1])];outline();
    };
    canvas.onpointerup=()=>{start=null;if(crop && (crop[2]-crop[0]<.01 || crop[3]-crop[1]<.01)) {crop=null;outline();}};
    canvas.onpointercancel=()=>{start=null;};
    $("waveCropAll").onclick=()=>{crop=null;outline();};
    choose.onchange=()=>{crop=null;captureSource();};
    btn.onclick=()=>{
      if (!valid() || !source) {note(tr("图纸已改变，请重新选择素材。"),true);return;}
      dialog.close();
      if(crop) source.crop=crop;
      if(existing) update(existing.id,item=>{delete item.mode;delete item.crop;Object.assign(item,source);});
      else ready(source);
    };
    window.cdI18n?.apply?.(dialog);dialog.showModal();captureSource();
  }
  $("waveInsert").onclick=()=>openMenu(menu.hidden);
  menu.onclick=event=>{
    const kind=event.target.closest("[data-wave-insert]")?.dataset.waveInsert;
    if(!kind) return;
    openMenu(false);
    if(kind==="legend") ready({kind});
    if(kind==="topology") topologyPicker();
    if(kind==="icon") window.CircuitSystemSymbols.open({pick:symbol=>ready({kind,symbol})});
    if(kind==="image") pickImage();
  };
  const legendType="application/x-circuit-wave-legend", symbolType="application/x-circuit-wave-symbol";
  menu.querySelector("[draggable]").ondragstart=e=>{e.dataTransfer.setData(legendType,"legend");e.dataTransfer.effectAllowed="copy";};
  chart.addEventListener("dragover",e=>{
    if ([...e.dataTransfer.types].some(t=>[legendType,symbolType,"Files"].includes(t))) e.preventDefault();
  });
  chart.addEventListener("drop",e=>{
    if(!state().info) return;
    const symbol=e.dataTransfer.getData(symbolType);
    const item=symbol && report.glyph_library?.[symbol]?{kind:"icon",symbol}:
      e.dataTransfer.getData(legendType)==="legend"?{kind:"legend"}:null;
    if(item) {e.preventDefault();openMenu(false);place(item,svgPoint(e));}
    else if(e.dataTransfer.files?.length) {e.preventDefault();pickImage(e.dataTransfer.files[0]);}
  });
  document.addEventListener("paste",e=>{
    if ($("wavePanel").hidden || !state().info || document.querySelector("dialog[open]") || e.target.closest("input,textarea,[contenteditable=true]")) return;
    const file=Array.from(e.clipboardData.items).find(i=>i.type.startsWith("image/"))?.getAsFile();
    if(file) {e.preventDefault();pickImage(file);}
  });
  function dash(line,p) {
    const units={dashed:[3,2],dashdot:[3,2,.7,2],dotted:[.7,2]}[line];
    return units?{"stroke-dasharray":units.map(n=>n*p.pt_cm*cm).join(" ")}:{};
  }
  function gridFields(grid, helpId="hint-waveGrid") {
    return [["horizontal","横向网格线"],["vertical","纵向网格线"]].map(([direction,title])=>
      `<label>${title}<select data-wave-grid-style="${direction}" aria-describedby="${helpId}">${[["none","不显示网格线"],["solid","实线"],["dashed","虚线"]]
        .map(([value,label])=>`<option value="${value}" ${value===grid[direction]?"selected":""}>${label}</option>`).join("")}</select></label>`).join("");
  }
  function gridChange(host, initial) {
    const grid={};
    for (const select of host.querySelectorAll("[data-wave-grid-style]")) grid[select.dataset.waveGridStyle]=select.value;
    return Object.keys(grid).some(key=>grid[key]!==initial[key])?grid:null;
  }
  function drawGrid(svg,p,left,top,width,height,xs,ys) {
    const grid=p.grid;
    for (const [direction,positions] of [["vertical",xs],["horizontal",ys]]) {
      if(grid[direction]==="none") continue;
      for(const pos of positions) {
        const ends=direction==="vertical"?{x1:pos,y1:top,x2:pos,y2:top+height}:{x1:left,y1:pos,x2:left+width,y2:pos};
        el("line",{...ends,stroke:grid.color,"stroke-width":grid.line_pt*p.pt_cm*cm,...dash(grid[direction],p),
          "pointer-events":"none","data-wave-grid":direction},svg);
      }
    }
  }
  function project(obj,item,b) {
    const w=obj.w*cm,h=obj.h*cm,row=b.rows[item.channel];
    return {x:b.left+item.at[0]*(b.right-b.left-w),y:row.y0+item.at[1]*(b.rowHeight-h),w,h};
  }
  function drawObjects(svg,cfg,b) {
    const p=state().info.paper, byId=new Map((cfg.insets || []).map(i=>[i.id,i]));
    for (const obj of p.insets || []) {
      const item=byId.get(obj.id);
      if(!item || !b.rows[item.channel]) continue;
      const rect=project(obj,item,b),{x,y,w,h}=rect;
      const font=p.font_pt*p.pt_cm*cm;
      if(obj.probe) {
        const [t,v]=obj.probe,px=b.left+(t-b.start)/(b.end-b.start)*(b.right-b.left),py=b.rows[item.channel].y(v);
        const line=el("g",{"data-wave-probe":item.id,"pointer-events":"none"},svg);
        el("line",{x1:px,y1:py,x2:clamp(px,x,x+w),y2:clamp(py,y,y+h),stroke:"#687a74","stroke-width":.4*p.pt_cm*cm},line);
        el("circle",{cx:px,cy:py,r:.045*cm,fill:"white",stroke:"#465a55","stroke-width":.45*p.pt_cm*cm},line);
      }
      const g=el("g",{"data-wave-inset":item.id,transform:`translate(${x} ${y})`,class:"wave-inset"},svg);
      const radius=Math.min(w/2,h/2,(item.radius_pt ?? p.plot_style.radius_pt)*p.pt_cm*cm);
      const white=(item.background || (item.kind==="legend"?"white":"transparent"))==="white";
      const border=item.border!==false;
      if(border && (item.shadow ?? item.kind==="legend")) {
        const shift=.035*cm, shadow={x:shift,y:shift,width:w,height:h,rx:radius,fill:"#b8b8b8"};
        if(!white) {
          const id=svg.id+"Shadow_"+item.id;
          const mask=el("mask",{id,maskUnits:"userSpaceOnUse",x:0,y:0,width:w+shift,height:h+shift},el("defs",{},g));
          el("rect",{width:w+shift,height:h+shift,fill:"white"},mask);
          el("rect",{width:w,height:h,rx:radius,fill:"black"},mask);
          shadow.mask="url(#"+id+")";
        }
        el("rect",shadow,g);
      }
      if(border || white) el("rect",{width:w,height:h,rx:radius,fill:white?"white":"none",stroke:border?"#808080":"none","stroke-width":.4*p.pt_cm*cm,"data-inset-frame":item.id},g);
      function label(text,px,py,limit,anchor) {
        const t=el("text",{x:px,y:py,"text-anchor":anchor,"dominant-baseline":"central","font-size":font,fill:"#222"},g);
        t.textContent=text;
        if(t.getComputedTextLength()>limit) {t.setAttribute("textLength",limit);t.setAttribute("lengthAdjust","spacingAndGlyphs");}
      }
      if(obj.keys) {
        obj.keys.forEach((key,i)=>{
          const ly=(i+.5)*h/obj.keys.length;
          el("line",{x1:.08*cm,y1:ly,x2:.39*cm,y2:ly,stroke:key.color,"stroke-width":p.line_pt*p.pt_cm*cm,...dash(key.line,p)},g);
          label(key.text,.48*cm,ly,Math.max(2,w-.55*cm),"start");
        });
      } else {
        const [ax,ay,aw,ah]=obj.art.map(v=>v*cm);
        if(item.kind==="icon") {
          const mark=el("svg",{x:ax,y:ay,width:aw,height:ah,viewBox:"0 -1 1 1"},g);
          sketchMark(mark,item.symbol,0,0,1,1,obj.glyph);
        } else {
          const [sw,sh]=item.size,[l,t,r,bottom]=item.crop || [0,0,1,1];
          const view=el("svg",{x:ax,y:ay,width:aw,height:ah,viewBox:`${sw*l} ${sh*t} ${sw*(r-l)} ${sh*(bottom-t)}`,overflow:"hidden"},g);
          const image=el("image",{width:sw,height:sh,x:0,y:0,preserveAspectRatio:"none"},view);
          assetURL(item).then(url=>{if(image.isConnected) image.setAttribute("href",url);}).catch(e=>{if(image.isConnected) note(e.message,true);});
        }
        obj.captions.forEach((txt,i)=>label(txt,w/2,ay+ah+(i+.5)*font*1.18,Math.max(2,w-.09*cm),"middle"));
      }
      const active=selected===item.id;
      el("rect",{width:w,height:h,rx:radius,fill:"transparent",stroke:active?"#2457d6":"none","stroke-dasharray":"3 2","vector-effect":"non-scaling-stroke","pointer-events":"all"},g);
      if(active && item.kind!=="legend") {
        const grip=el("g",{"data-inset-resize":item.id,transform:`translate(${w} ${h})`},g);
        el("rect",{x:-5,y:-5,width:10,height:10,fill:"transparent"},grip);
        el("rect",{x:-1.9,y:-1.9,width:3.8,height:3.8,fill:"white",stroke:"#2457d6","stroke-width":.6},grip);
      }
    }
  }
  function propView(host) {
    const {info,cfg}=state(), item=current();
    const style=Object.keys(info.paper.plot_style).length?info.paper.plot_style:
      {...info.plot_defaults,tint:0,row_cm:info.paper.row_cm,font:"journal"};
    const field=(name,title,value,min,max,step,topic)=>`<label>${tr(title)}<input data-field="${name}" type="number" value="${value}" min="${min}" max="${max}" step="${step}" aria-describedby="hint-${topic}"></label>`;
    const check=(name,title,checked,topic)=>`<label class="wave-check"><input data-field="${name}" type="checkbox" ${checked?'checked':''} aria-describedby="hint-${topic}">${tr(title)}</label>`;
    const group=(title,topic,content)=>`<fieldset class="wave-group"><legend><span>${tr(title)}</span> ${editorHint(topic)}</legend><div class="wave-prop-grid">${content}</div></fieldset>`;
    const f=name=>host.querySelector('[data-field="'+name+'"]');
    const opt=(value,label,chosen)=>`<option value="${esc(value)}" ${chosen===value?'selected':''}>${esc(label)}</option>`;
    const visible=new Set((info.paper.insets || []).map(i=>i.id));
    host.innerHTML=`<div class="wave-object-choice"><label for="waveArtSelection">${tr("已放置对象")}</label>${editorHint('waveObjects')}
      <select id="waveArtSelection" aria-describedby="hint-waveObjects"><option value="">${tr("绘图区属性")}</option>${(cfg.insets || []).filter(i=>visible.has(i.id)).map(i=>opt(i.id,(i.label || tr(titles[i.kind]))+" · "+i.id,selected)).join("")}</select></div>`;
    if (!item) {
      const swatches=[["#9677b1","浅紫"],["#5f9b91","青灰"],["#477eae","雾蓝"],["#c18c43","米黄"],["#ffffff","白色"]];
      host.insertAdjacentHTML("beforeend",`${group("绘图区底色","wavePaint",`<label>${tr("预设色")}<select data-field="preset" aria-describedby="hint-wavePaint"><option value="">${tr("自选颜色")}</option>${swatches.map(([v,t])=>opt(v,tr(t),style.color)).join("")}</select></label>
        <label>${tr("自选颜色")}<input type="color" data-field="color" value="${style.color}" aria-describedby="hint-wavePaint"></label>
        ${field("tint","着色浓度 / %",style.tint,0,30,1,"wavePaint")}`)}
        ${group("网格线","waveGrid",gridFields(info.paper.grid))}
        ${group("纸面尺寸","waveSize",`${field("height","坐标区高度 / cm",style.row_cm,1.55,12,.01,"waveSize")}${field("radius","边框圆角 / pt",style.radius_pt,0,12,.1,"waveSize")}
        <div class="wave-font-field"><div class="help-row"><label for="wavePrintFont">${tr("出图字体")}</label>${editorHint("waveFont")}</div><select id="wavePrintFont" data-field="font" aria-describedby="hint-waveFont">${opt("system",tr("宋体 / Times New Roman"),style.font)}${opt("journal",tr("期刊默认"),style.font)}</select></div>`)}
        <button id="waveStyleApply">${tr("应用绘图区样式")}</button>`);
      f("preset").onchange=()=>{if(f("preset").value) {f("color").value=f("preset").value;if(!f("tint").valueAsNumber) f("tint").value=info.plot_defaults.tint;}};
      f("color").oninput=()=>{f("preset").value=f("color").value;};
      $("waveStyleApply").onclick=()=>{
        if(!Array.from(host.querySelectorAll("input[type=number]")).every(i=>i.value!=="" && i.reportValidity())) return;
        const next=clone(cfg), grid=gridChange(host,info.paper.grid);
        const updated={color:f("color").value,tint:f("tint").valueAsNumber,radius_pt:f("radius").valueAsNumber,
          row_cm:f("height").valueAsNumber,font:f("font").value};
        if(grid) next.grid=grid;
        if(!grid || Object.keys(updated).some(k=>updated[k]!==style[k])) next.plot_style=updated;
        change(next);
      };
    } else {
      const legend=item.kind==="legend", probe=item.probe_s;
      host.insertAdjacentHTML("beforeend",`${group(titles[item.kind],"waveOwner",`<label>${tr("所属通道")}<select data-field="channel" aria-describedby="hint-waveOwner">${cfg.channels.map(c=>opt(c.column,c.label,item.channel)).join("")}</select></label>
        ${legend?"":field("width","宽度 / cm",item.width_cm,.35,18,"any","waveOwner")}
        ${legend?"":`<label class="wave-wide-field">${tr("插图标题")}<input data-field="label" maxlength="60" value="${esc(item.label || '')}"></label>`}`)}
        ${group("外观","waveInsetFrame",`<div class="wave-checks">${check("border","显示边框",item.border!==false,"waveInsetFrame")}
        ${check("transparent","透明背景",(item.background || (legend?"white":"transparent"))==="transparent","waveInsetFrame")}
        ${check("shadow","边框投影",item.shadow ?? legend,"waveInsetFrame")}</div>
        ${field("radius","边框圆角 / pt",item.radius_pt ?? style.radius_pt,0,12,.1,"waveInsetFrame")}`)}
        ${legend?"":group("测点","waveProbe",`${check("probe","绑定测点",probe!==undefined,"waveProbe")}${field("time","测点时间 / s",probe ?? cfg.window[0],info.extent[0],info.extent[1],"any","waveProbe")}`)}
        <div class="wave-row"><button id="waveInsetApply" class="primary">${tr("应用属性")}</button>${item.kind==="topology"?`<button id="waveInsetRefresh" aria-describedby="hint-waveRefresh">${tr("从源图刷新")}</button>${editorHint('waveCapture','hint-waveRefresh')}`:""}<button id="waveInsetDelete">${tr("删除所选")}</button></div>`);
      f("shadow").disabled=item.border===false;
      f("border").onchange=()=>{f("shadow").disabled=!f("border").checked;};
      if(!legend) {f("time").disabled=!f("probe").checked;f("probe").onchange=()=>{f("time").disabled=!f("probe").checked;};}
      $("waveInsetApply").onclick=()=>{
        if(!Array.from(host.querySelectorAll("input[type=number]:enabled")).every(i=>i.value!=="" && i.reportValidity())) return;
        update(item.id,v=>{
          v.radius_pt=f("radius").valueAsNumber;v.channel=f("channel").value;
          if(f("border").checked) delete v.border; else v.border=false;
          v.background=f("transparent").checked?"transparent":"white";v.shadow=f("shadow").checked;
          if(!legend) {v.width_cm=f("width").valueAsNumber;v.label=f("label").value;
            if(f("probe").checked) v.probe_s=f("time").valueAsNumber; else delete v.probe_s;}
        });
      };
      if($("waveInsetRefresh")) $("waveInsetRefresh").onclick=()=>topologyPicker(item);
      $("waveInsetDelete").onclick=remove;
    }
    $("waveArtSelection").onchange=e=>{selected=e.target.value || null;draw();properties();chart.focus({preventScroll:true});};
  }
  function down(ev) {
    if (!ev.isPrimary || drag) return !!drag;
    const point=svgPoint(ev);
    if(pending) {place(pending,point);return true;}
    const hit=ev.target.closest("[data-wave-inset]");
    if(!hit) return false;
    const {info,box,cfg}=state();
    clearNotes();selected=hit.dataset.waveInset;
    const obj=info.paper.insets.find(o=>o.id===selected), item=current(), rect=project(obj,item,box);
    chart.setPointerCapture(ev.pointerId);chart.focus({preventScroll:true});
    drag={pointer:ev.pointerId,id:selected,point,rect,obj,resize:!!ev.target.closest("[data-inset-resize]"),
      cfg};
    draw();properties();ev.preventDefault();return true;
  }
  function move(ev) {
    if(!drag || ev.pointerId!==drag.pointer) return false;
    const g=drag, {box,info}=state(), pt=svgPoint(ev), dx=pt[0]-g.point[0],dy=pt[1]-g.point[1];
    if(Math.abs(dx)+Math.abs(dy)<.0001) return true;
    if(state().cfg!==g.cfg) {drag=null;draw();return true;}
    const cfg=clone(g.cfg), item=cfg.insets.find(i=>i.id===g.id),b=g.rect, row=box.rows[item.channel];
    if(g.resize) {
      const scale=1+(dx*b.w+dy*b.h)/(b.w*b.w+b.h*b.h);
      item.width_cm=Number(clamp(b.w/cm*scale,.35,Math.min(18,info.paper.width_cm)).toFixed(3));
      const ratio=item.width_cm/(b.w/cm);
      item.at=[clamp((b.x-box.left)/Math.max(.001,box.right-box.left-b.w*ratio),0,1),
        clamp((b.y-row.y0)/Math.max(.001,box.rowHeight-b.h*ratio),0,1)];
      const group=chart.querySelector('[data-wave-inset="'+g.id+'"]');
      group?.setAttribute("transform",`translate(${b.x} ${b.y}) scale(${ratio})`);
    } else {
      item.at=[clamp((b.x+dx-box.left)/Math.max(.001,box.right-box.left-b.w),0,1),
               clamp((b.y+dy-row.y0)/Math.max(.001,box.rowHeight-b.h),0,1)];
      const moved=project(g.obj,item,box);
      const group=chart.querySelector('[data-wave-inset="'+g.id+'"]');
      group?.setAttribute("transform",`translate(${moved.x} ${moved.y})`);
      const leader=chart.querySelector('[data-wave-probe="'+g.id+'"] line');
      if(leader) {leader.setAttribute("x2",clamp(+leader.getAttribute("x1"),moved.x,moved.x+b.w));leader.setAttribute("y2",clamp(+leader.getAttribute("y1"),moved.y,moved.y+b.h));}
    }
    g.candidate=cfg;return true;
  }
  function up(ev) {
    if(!drag || ev.pointerId!==drag.pointer) return false;
    const g=drag;drag=null;
    if(chart.hasPointerCapture(ev.pointerId)) chart.releasePointerCapture(ev.pointerId);
    if(g.candidate && state().cfg===g.cfg) change(g.candidate); else draw();
    return true;
  }
  function cancel(){if(drag) {drag=null;draw();}}
  chart.addEventListener("pointercancel",cancel);
  chart.addEventListener("lostpointercapture",e=>{if(e.target===chart) cancel();});
  function key(e) {
    if(e.key==="Escape" && (selected || pending || drag || !menu.hidden)) {reset();draw();properties();return true;}
    if(pending && e.key==="Enter") {e.preventDefault();place(pending,defaultPoint());return true;}
    if(!selected) return false;
    if(["Delete","Backspace"].includes(e.key)) {e.preventDefault();remove();return true;}
    const direction={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]}[e.key];
    if(!direction) return false;
    e.preventDefault();const id=selected,fast=e.shiftKey;
    nudges=nudges.then(()=>{
      const {info}=state(),obj=info?.paper.insets?.find(o=>o.id===id);
      if(!obj || id!==selected) return;
      const step=info.paper.nudge_cm*(fast?10:1);
      return update(id,item=>{item.at=[clamp(item.at[0]+direction[0]*step/Math.max(.001,info.paper.width_cm-obj.w),0,1),
        clamp(item.at[1]+direction[1]*step/Math.max(.001,info.paper.row_cm-obj.h),0,1)];});
    }).catch(e=>note(e.message,true));
    return true;
  }
  return {sync,reset,current,remove,deselect:()=>{selected=null;},properties:propView,draw:drawObjects,down,move,up,key,dash,
    gridFields,gridChange,drawGrid};
};
