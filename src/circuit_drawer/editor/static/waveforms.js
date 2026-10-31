/* CSV samples are read-only; display and interval settings live in layout.waveform. */
(() => {
  "use strict";
  const panel = document.getElementById("wavePanel");
  const $ = id => document.getElementById(id);
  const units = {s:1, ms:1e-3, us:1e-6};
  const colors = ["#c8102e","#2457d6","#17845a","#8753a0","#b66b0a","#237c89"];
  let info = null, active = 0, revision = 0, gesture = null, box = null, tool = null, selNote = null;
  let selLabel=null, selectedPart="body", previewTarget="overview", viewZoom="fit", nudges=Promise.resolve();
  const CM=40; // SVG user units per paper cm; the viewport scales uniformly.
  panel.innerHTML = `<div class="wave-toolbar"><strong>波形与模态</strong>
    <button id="waveImport">导入 CSV…</button>
    <span class="wave-tools" title="在波形上加文字、箭头和关键点圆圈；导出时一起出图">标注
    <button id="waveNote_text">文字</button><button id="waveNote_arrow">箭头</button><button id="waveNote_dot">关键点</button>
    <button id="waveNoteDelete" disabled>删除所选</button></span><button id="waveSave">保存对应关系</button>
    <button id="waveUndo">撤销</button><button id="waveTruePreview">真实出图预览</button>
    <button id="waveExport" class="primary">导出波形与模态…</button><button id="waveClose">收起</button></div>
    <div class="wave-paper-toolbar"><label>纸面尺寸 <select id="wavePaperTarget"><option value="overview">总览中的波形</option><option value="standalone">独立波形图</option></select></label>
    <label>查看比例 <select id="waveViewZoom"><option value="fit">适合画布</option><option value="1">100%</option><option value="1.5">150%</option><option value="2">200%</option></select></label>
    <span id="wavePaperSize"></span><span class="hint">数学文字为编辑示意，最终字体用“真实出图预览”核对。</span></div>
    <div class="wave-body"><div class="wave-chart-wrap"><svg id="waveChart" tabindex="0" role="img" aria-label="电压、电流波形及时间分界线"></svg></div>
    <div class="wave-sidebar"><div id="waveProperties"></div><div class="wave-settings" id="waveSettings"></div></div></div><div id="waveNotice" role="status" aria-live="polite"></div>`;

  function note(text, bad=false) { $("waveNotice").textContent=text; $("waveNotice").className=bad?"bad":"ok"; }
  function request(path, cfg, extra={}) {
    return api(path, {layout:{...layout,waveform:cfg},profile,preview_target:previewTarget,...extra});
  }
  function installed(result, remember=true) {
    if (remember) pushHistory();
    layout.waveform=clone(result.config); info=result;
    if(selNote!==null && !layout.waveform.notes?.[selNote]) selNote=null;
    active=Math.min(active,layout.waveform.intervals.length-1);
    controls(); draw(); properties();
    const warnings=[];
    if (info.stale) warnings.push("电路连接或仿真参数已改变，请重新载入对应工况的 CSV 后导出。");
    if (info.binding_errors.length) warnings.push("有绑定的模态已删除、改名或重名，请重新选择。");
    note(warnings.join(" ") || `${info.rows.toLocaleString()} 行原始数据；拖动分界线，点击区间切换电路模态。模态对应由你确认。`, !!warnings.length);
  }
  async function change(cfg) {
    const id=++revision;
    try {
      const result=await request("/api/waveform-view",cfg);
      if (id===revision) installed(result);
    } catch (e) { if(id===revision) { note(e.message,true); controls(); draw(); } }
  }
  async function importCsv(args) {
    const id=++revision;
    note("正在读取 CSV…");
    const result=await api("/api/waveform-import",{layout,profile,preview_target:previewTarget,...args});
    if (id===revision) installed(result);
  }
  window.openWaveforms=async (options={}) => {
    if (!layout) return;
    panel.hidden=false;
    try {
      if (options.source) await importCsv({source:options.source});
      else if (layout.waveform?.source) installed(await request("/api/waveform-view",layout.waveform),false);
      else { info=null; controls(); draw(); note("先导入仿真或测量 CSV。第一行是列名，随后是数值数据。"); }
    } catch (e) { info=null; controls(); draw(); note(e.message,true); }
  };
  $("waveforms").onclick=()=>window.openWaveforms();
  $("waveClose").onclick=()=>{panel.hidden=true;};
  $("waveUndo").onclick=()=>undo();
  $("wavePaperTarget").onchange=async()=> {
    previewTarget=$("wavePaperTarget").value;
    const id=++revision;
    if(info) {try {const r=await request("/api/waveform-view",layout.waveform);if(id===revision) installed(r,false);} catch(e) {if(id===revision) note(e.message,true);}}
  };
  $("waveViewZoom").onchange=()=>{viewZoom=$("waveViewZoom").value;draw();};
  $("waveTruePreview").onclick=async()=> {
    const button=$("waveTruePreview");button.disabled=true;note("正在按导出设置编译波形…");
    const snapshot=JSON.stringify(layout.waveform), target=previewTarget, savedProfile=profile;
    try {
      const r=await request("/api/waveform-preview",layout.waveform);
      if(snapshot!==JSON.stringify(layout.waveform) || target!==previewTarget || savedProfile!==profile) {
        note("编译期间设置已改变，请重新预览。",true);return;
      }
      showDialog("波形真实出图预览",`<p>${target==='overview'?'总览中的波形':'独立波形图'} · ${r.profile==='ieee-single'?'单栏':'双栏'}。与导出共用出图器，查看数学字体和标注间距。</p><img style="display:block;width:100%;height:auto" alt="波形真实出图预览" src="data:image/svg+xml;base64,${r.svg}">`);
      note("真实出图预览完成。");
    } catch(e) {note(e.message,true);} finally {button.disabled=false;}
  };
  window.addEventListener("circuit-drawer:simulation-complete",ev=> {
    document.getElementById("dlg").close();
    window.openWaveforms({source:ev.detail.csv});
  });
  window.addEventListener("circuit-drawer:layout-changed",async()=> {
    if(panel.hidden) return;
    selNote=null;selLabel=null;
    const id=++revision;
    if(!layout.waveform?.source) {info=null;controls();draw();return;}
    try {
      const result=await request("/api/waveform-view",layout.waveform);
      if(id===revision) installed(result,false);
    } catch(e) {if(id===revision) {info=null;controls();draw();note(e.message,true);}}
  });

  $("waveImport").onclick=()=> {
    showDialog("导入波形 CSV", `<p>选择文件，或填写本机 CSV 路径。首行为列名；支持逗号、分号及制表符分隔。</p>
      <p><input id="waveCsvFile" type="file" accept=".csv,.tsv,.txt"></p>
      <div class="export-row"><input id="waveCsvPath" type="text" placeholder="CSV 路径（相对电路 JSON 所在目录）" value="${esc(layout.waveform?.source || '')}"></div>
      <div class="export-row"><label>时间列 <input id="waveTimeColumn" placeholder="留空使用第一列"></label>
      <label>CSV 时间单位 <select id="waveTimeUnit"><option>s</option><option>ms</option><option>us</option></select></label></div>
      <p class="hint">UTF-8 编码，最多 64 MiB、100 万行。导入时在电路旁保存一份数据快照；同名同内容不重复复制。</p>
      <button id="waveCsvLoad" class="primary">载入 CSV</button><p id="waveImportState" role="status"></p>`);
    $("waveCsvLoad").onclick=async()=> {
      const button=$("waveCsvLoad"), state=$("waveImportState"); button.disabled=true;
      try {
        const file=$("waveCsvFile").files[0];
        if(file && file.size>64*1024*1024) throw new Error("CSV 最大支持 64 MiB");
        const args={time_column:$("waveTimeColumn").value.trim() || undefined,time_unit:$("waveTimeUnit").value};
        if(file) Object.assign(args,{text:await file.text(),filename:file.name});
        else args.source=$("waveCsvPath").value.trim();
        state.textContent="正在读取…";
        await importCsv(args); $("dlg").close();
      } catch(e) {state.textContent=e.message;state.className="bad";}
      finally {button.disabled=false;}
    };
  };

  function controls() {
    const cfg=layout.waveform;
    $("waveSave").disabled=$("waveExport").disabled=$("waveTruePreview").disabled=!info;
    if(!info) {$("waveSettings").textContent="导入 CSV 后，可选通道、时间范围和对应模态。";return;}
    const factor=units[cfg.display_unit];
    const number=t=>Number((t/factor).toPrecision(12));
    const names=(layout.modes || []).map(m=>m.name);
    $("waveSettings").innerHTML=`<div class="hint" id="waveFileSummary">${esc(cfg.source)}</div>
      <div class="wave-row"><label>显示单位 <select id="waveUnit">${Object.keys(units).map(u=>`<option ${u===cfg.display_unit?'selected':''}>${u}</option>`).join('')}</select></label>
      <button id="waveZoomIn" title="缩小时间范围">放大</button><button id="waveZoomOut">缩小</button><button id="waveFull">全范围</button></div>
      <label><input id="waveRelative" type="checkbox" ${cfg.relative_time?'checked':''}> 横轴以 t₀ 为零点</label>
      <div class="hint">下面的范围和分界输入使用 CSV 的绝对时间。</div>
      <div class="wave-row"><label>起点 <input id="waveStart" type="number" step="any" value="${number(cfg.window[0])}"></label>
      <label>终点 <input id="waveEnd" type="number" step="any" value="${number(cfg.window[1])}"></label><button id="waveRange">应用范围</button></div>
      <div class="wave-row"><select id="wavePwm" aria-label="周期参考通道">${info.columns.filter(c=>c!==cfg.time_column).map(c=>`<option ${c===(cfg.period_column || info.columns.find(n=>/pwm|gate|drive/i.test(n)))?'selected':''}>${esc(c)}</option>`).join('')}</select>
      <button id="wavePeriod">末尾一个 PWM 周期</button></div>
      <p class="hint">PWM 只用于建议分界时间；各区间的模态需自行选择。框选曲线区域可放大。</p>
      <div id="waveIntervals">${cfg.intervals.map((name,i)=>`<div class="wave-binding">
        <button data-interval="${i}" class="${i===active?'active':''}" title="显示这个区间的电路模态">t${i}→t${i+1}</button>
        <select data-binding="${i}" aria-label="区间 ${i+1} 对应模态"><option value="">请选择模态</option>
        ${name && !names.includes(name)?`<option selected value="${esc(name)}">已失效：${esc(name)}</option>`:''}
        ${names.map(n=>`<option ${name===n?'selected':''} value="${esc(n)}">${esc(n)}</option>`).join('')}</select></div>`).join('')}</div>
      <div class="wave-row"><button id="waveSplit">拆分所选区间</button></div>
      <details><summary>精确设置分界时间（${cfg.display_unit}）</summary>
      ${cfg.boundaries.map((t,i)=>`<div class="wave-row"><label>t${i} <input type="number" step="any" data-mark="${i}" value="${number(t)}" aria-label="t${i}"></label>
      ${i>0 && i<cfg.boundaries.length-1?`<button data-remove="${i}" title="合并相邻区间，并清除其模态选择">删除分界</button>`:''}</div>`).join('')}</details>
      <details><summary>通道、标签和单位</summary><div id="waveChannels">
      ${info.columns.filter(c=>c!==cfg.time_column).map((column,i)=> {
        const c=cfg.channels.find(c=>c.column===column) || {label:column,unit:'',color:colors[i%colors.length]};
        return `<div class="wave-channel" data-column="${esc(column)}"><input type="checkbox" ${cfg.channels.some(c=>c.column===column)?'checked':''} aria-label="显示 ${esc(column)}">
        <input type="text" data-field="label" value="${esc(c.label)}" aria-label="${esc(column)} 标签">
        <input type="text" data-field="unit" value="${esc(c.unit)}" aria-label="${esc(column)} 单位">
        <input type="color" value="${esc(c.color)}" aria-label="${esc(column)} 颜色"><small>${esc(column)}</small></div>`;
      }).join('')}</div><button id="waveApplyChannels">应用通道设置</button></details>`;
    $("waveUnit").onchange=()=>change({...clone(cfg),display_unit:$("waveUnit").value});
    $("waveRelative").onchange=()=>change({...clone(cfg),relative_time:$("waveRelative").checked});
    $("waveRange").onclick=()=>range(Number($("waveStart").value)*factor,Number($("waveEnd").value)*factor);
    $("waveFull").onclick=()=>range(...info.extent);
    $("waveZoomIn").onclick=()=>zoom(.5); $("waveZoomOut").onclick=()=>zoom(2);
    $("wavePeriod").onclick=async()=> {
      const column=$("wavePwm").value;
      try {
        const r=await request("/api/waveform-period",cfg,{column});
        await change({...clone(cfg),window:[r.boundaries[0],r.boundaries.at(-1)],boundaries:r.boundaries,
          intervals:cfg.intervals.length===2?[...cfg.intervals]:[null,null],display_unit:"us",period_column:column});
      } catch(e) {note(e.message,true);}
    };
    panel.querySelectorAll("[data-binding]").forEach(select=>select.onchange=async()=> {
      const candidate=clone(cfg); active=Number(select.dataset.binding);
      candidate.intervals[active]=select.value || null;
      await change(candidate); selectInterval(active);
    });
    panel.querySelectorAll("[data-interval]").forEach(button=>button.onclick=()=>selectInterval(Number(button.dataset.interval)));
    panel.querySelectorAll("[data-mark]").forEach(input=>input.onchange=()=> {
      const candidate=clone(cfg), i=Number(input.dataset.mark), value=Number(input.value)*factor;
      candidate.boundaries[i]=value; delete candidate.period_column;
      if(i===0) candidate.window[0]=value;
      if(i===candidate.boundaries.length-1) candidate.window[1]=value;
      change(candidate);
    });
    panel.querySelectorAll("[data-remove]").forEach(button=>button.onclick=()=> {
      const candidate=clone(cfg), i=Number(button.dataset.remove);
      candidate.boundaries.splice(i,1);candidate.intervals.splice(i-1,2,null);delete candidate.period_column;
      change(candidate);
    });
    $("waveSplit").onclick=()=> {
      const candidate=clone(cfg);
      candidate.boundaries.splice(active+1,0,(cfg.boundaries[active]+cfg.boundaries[active+1])/2);
      candidate.intervals.splice(active,1,null,null); delete candidate.period_column;
      change(candidate);
    };
    $("waveApplyChannels").onclick=()=> {
      const channels=[...$("waveChannels").children].filter(row=>row.querySelector('[type=checkbox]').checked).map(row=>({
        column:row.dataset.column,label:row.querySelector('[data-field=label]').value,
        unit:row.querySelector('[data-field=unit]').value,color:row.querySelector('[type=color]').value}));
      // Notes on a hidden channel stay in the table and come back with it.
      change({...clone(cfg),channels});
    };
  }
  function properties() {
    const host=$("waveProperties"), cfg=layout?.waveform, n=cfg?.notes?.[selNote], p=info?.paper;
    if(!p || (!n && !selLabel)) {host.innerHTML='<strong>标注属性</strong><p class="hint">点击文字、箭头、圆圈或 t / Mode 标签进行微调。</p>';return;}
    const offset=n?(n.offset || (n.kind==='dot'?p.dot_label_offset:[0,0])):(cfg.label_offsets?.[selLabel] || [0,0]);
    const field=(id,title,value,lo,hi)=>`<label>${title}<input id="${id}" type="number" step="any" min="${lo}" max="${hi}" value="${value}"></label>`;
    host.innerHTML=`<strong>${n?{text:'文字',arrow:'箭头',dot:'关键点'}[n.kind]:esc(selLabel)}属性</strong>
      ${n?`<label class="wave-note-text">文字（英文或 $数学$）<textarea id="wavePropText" rows="2" maxlength="120">${esc(n.text || '')}</textarea></label>
      <div class="wave-prop-grid"><label>颜色<input id="wavePropColor" type="color" value="${ink_css(n.color)}"></label>
      ${field('wavePropFont','字号 / pt',n.font_pt || p.font_pt,...p.note_limits.font_pt)}
      ${n.kind!=='text'?field('wavePropLine','线宽 / pt',n.line_pt || p.line_pt*(n.kind==='arrow'?p.arrow_weight:1),...p.note_limits.line_pt):''}
      ${n.kind==='dot'?field('wavePropRadius','圆圈半径 / cm',n.radius_cm || p.radius_cm,...p.note_limits.radius_cm):''}</div>`:''}
      <div class="wave-prop-grid">${field('wavePropDx','文字右移 / cm',offset[0],-p.max_offset_cm,p.max_offset_cm)}
      ${field('wavePropDy','文字上移 / cm',offset[1],-p.max_offset_cm,p.max_offset_cm)}</div>
      <div class="wave-row"><button id="wavePropApply" class="primary">应用属性</button><button id="wavePropReset">恢复默认样式</button></div>
      <p class="hint">方向键每次移动 ${p.nudge_cm} cm；Shift 加速 10 倍。${n&&selectedPart==='body'?'当前移动整个标注':'当前只移动文字'}。Ctrl+Z 撤销。</p>`;
    $("wavePropApply").onclick=async()=> {
      const candidate=clone(layout.waveform), dx=Number($("wavePropDx").value), dy=Number($("wavePropDy").value);
      if(![...host.querySelectorAll('input[type=number]')].every(e=>e.value!=='' && e.reportValidity())) return;
      if(n) {
        const item=candidate.notes[selNote]; item.text=$("wavePropText").value.trim();item.color=$("wavePropColor").value;
        item.font_pt=Number($("wavePropFont").value); item.offset=[dx,dy];
        if($("wavePropLine")) item.line_pt=Number($("wavePropLine").value);
        if($("wavePropRadius")) item.radius_cm=Number($("wavePropRadius").value);
      } else candidate.label_offsets={...(candidate.label_offsets || {}),[selLabel]:[dx,dy]};
      await change(candidate);$("waveChart").focus({preventScroll:true});
    };
    $("wavePropReset").onclick=()=> {
      const candidate=clone(layout.waveform);
      if(n) for(const k of ['color','font_pt','line_pt','radius_cm','offset']) delete candidate.notes[selNote][k];
      else if(candidate.label_offsets) delete candidate.label_offsets[selLabel];
      change(candidate);
    };
  }
  async function nudge(key, fast) {
    if(!info || (selNote===null && !selLabel)) return;
    const step=info.paper.nudge_cm*(fast?10:1), dx=key==='ArrowLeft'?-step:key==='ArrowRight'?step:0,
      dy=key==='ArrowDown'?-step:key==='ArrowUp'?step:0, candidate=clone(layout.waveform);
    const add=([x,y])=>[Number((x+dx).toFixed(3)),Number((y+dy).toFixed(3))];
    if(selLabel) candidate.label_offsets={...(candidate.label_offsets || {}),[selLabel]:add(candidate.label_offsets?.[selLabel] || [0,0])};
    else {
      const n=candidate.notes[selNote]; if(!n) return;
      if(selectedPart==='label') n.offset=add(n.offset || (n.kind==='dot'?info.paper.dot_label_offset:[0,0]));
      else {
        const row=box.rows[n.channel];if(!row) return;
        const dt=dx/info.paper.width_cm*(box.end-box.start), dv=dy/info.paper.row_cm*(row.hi-row.lo);
        const keys=['at','from','to'].filter(k=>n[k]);
        if(keys.some(k=>n[k][0]+dt<box.start || n[k][0]+dt>box.end)) {note('已到时间范围边缘。');return;}
        for(const k of keys) n[k]=[Number((n[k][0]+dt).toPrecision(15)),Number((n[k][1]+dv).toPrecision(15))];
      }
    }
    await change(candidate);
  }
  function range(start,end) {
    if(!Number.isFinite(start)||!Number.isFinite(end)||!(start<end)||start<info.extent[0]||end>info.extent[1]) {
      note("时间范围必须位于 CSV 内，且起点小于终点。",true);return;
    }
    const cfg=layout.waveform, candidate=clone(cfg);
    candidate.window=[start,end]; candidate.boundaries=[start,...cfg.boundaries.filter(t=>t>start && t<end),end];
    candidate.intervals=candidate.boundaries.slice(1).map((t,i)=> {
      const middle=(t+candidate.boundaries[i])/2;
      const old=cfg.boundaries.findIndex((a,j)=>a<=middle && middle<cfg.boundaries[j+1]);
      return cfg.intervals[old] || null;
    });
    delete candidate.period_column;change(candidate);
  }
  function zoom(factor) {
    const [a,b]=layout.waveform.window, mid=(a+b)/2, half=(b-a)*factor/2;
    range(Math.max(info.extent[0],mid-half),Math.min(info.extent[1],mid+half));
  }
  function selectInterval(index) {
    active=index;controls();draw();
    const name=layout.waveform.intervals[index];
    modeIdx=(layout.modes || []).findIndex(m=>m.name===name);
    selected=null; modePick=false;
    $("modePick").classList.remove("on"); $("modePickHint").style.display="none";
    loadMode();
    if(!name) note(`请为 t${index}→t${index+1} 选择对应模态。`);
  }
  // Paper cm <-> chart px. Offsets in the JSON are printed cm (y up), so a
  // dragged label lands at the same place in the exported figure.
  function scales() {
    const p=info.paper;
    return {sx:(box.right-box.left)/p.width_cm, sy:box.rowHeight/p.row_cm};
  }
  function toPx([dx,dy]) { const {sx,sy}=scales(); return [dx*sx,-dy*sy]; }
  function toCm(dx,dy) {
    const {sx,sy}=scales(), lim=info.paper.max_offset_cm, r=v=>Math.round(Math.max(-lim,Math.min(lim,v))*1000)/1000;
    return [r(dx/sx),r(-dy/sy)];
  }
  // Rough display of the TeX in a note: $, \mathrm{} and braces dropped; symbols as on the canvas.
  const plain=s=>texSymbols((s||"").replace(/\$/g,"").replace(/\\(mathrm|mathit|text)\{([^}]*)\}/g,"$2")).replace(/[{}\\]/g,"");
  // Same rule as note_anchor() in waveforms.py: the label sits behind the arrow's tail.
  function tailAnchor(dx,dy) { return Math.abs(dx)>=Math.abs(dy)?(dx>0?"east":"west"):(dy>0?"north":"south"); }
  function textAt(parent,x,y,anchor,text,attrs={}) {
    const h={east:"end",west:"start",north:"middle",south:"middle",center:"middle","south west":"start"}[anchor];
    const v={north:"hanging",south:"auto","south west":"auto"}[anchor] || "central";
    const pad=info.paper.pt_cm*CM; // TeX node inner sep = 1 pt
    if(anchor.includes("east")) x-=pad; if(anchor.includes("west")) x+=pad;
    if(anchor.includes("north")) y+=pad; if(anchor.includes("south")) y-=pad;
    const t=el("text",{x,y,"text-anchor":h,"dominant-baseline":v,"font-size":info.paper.font_pt*info.paper.pt_cm*CM,...attrs},parent);
    t.textContent=plain(text); return t;
  }
  function draw(candidate=layout?.waveform) {
    const svg=$("waveChart"); svg.replaceChildren();
    $("waveNoteDelete").disabled=selNote===null;
    if(!info || !candidate) {svg.setAttribute("viewBox","0 0 800 200");el("text",{x:30,y:80,fill:"#6b6b70"},svg).textContent="导入 CSV，查看波形并标注模态区间";return;}
    const p=info.paper, width=p.total_width_cm*CM, height=p.total_height_cm*CM;
    const left=p.left_cm*CM, right=left+p.width_cm*CM, top=p.top_cm*CM;
    const rowHeight=p.row_cm*CM, gap=p.gap_cm*CM, font=p.font_pt*p.pt_cm*CM;
    svg.setAttribute("viewBox",`0 0 ${width} ${height}`);
    $("wavePaperSize").textContent=`${p.profile==='ieee-single'?'单栏':'双栏'} · ${p.total_width_cm.toFixed(2)} × ${p.total_height_cm.toFixed(2)} cm`;
    const [start,end]=candidate.window, factor=units[candidate.display_unit];
    const x=t=>left+(t-start)/(end-start)*(right-left), rows={};
    box={left,right,top,bottom:height-p.bottom_cm*CM,start,end,width,height,rowHeight,rows};
    el("rect",{x:0,y:0,width,height,fill:"white","pointer-events":"none"},svg);
    const ticks=(a,b,n)=>{
      const raw=(b-a)/Math.max(1,n-1), mag=10**Math.floor(Math.log10(raw));
      const steps=[.1,.2,.5,1,2,5,10].map(m=>m*mag); let k=steps.findIndex(s=>s>=raw*(1-1e-9));
      for(;;) { const s=steps[k], out=[];
        for(let j=Math.ceil(a/s-1e-6);j<=Math.floor(b/s+1e-6);j++) out.push(Number((j*s).toPrecision(12)));
        if(out.length>=2 || k===0) return out; k--; }
    };
    const origin=candidate.relative_time?start:0, times=ticks((start-origin)/factor,(end-origin)/factor,5);
    info.channels.forEach((channel,i)=> {
      const y0=top+i*(rowHeight+gap), [lo,hi]=channel.axis.range;
      const y=v=>y0+rowHeight-(v-lo)/(hi-lo)*rowHeight;
      rows[channel.column]={y0,lo,hi,y};
      if((candidate.slice_style || 'shade')==='shade') candidate.intervals.forEach((name,j)=> {
        if(j%2===0) el("rect",{x:x(candidate.boundaries[j]),y:y0,width:x(candidate.boundaries[j+1])-x(candidate.boundaries[j]),height:rowHeight,
          fill:"#ededed","pointer-events":"none"},svg);
      });
      channel.axis.ticks.forEach((value,k)=> {
        el("line",{x1:left,y1:y(value),x2:right,y2:y(value),stroke:"#e4e4e4","stroke-width":.25*p.pt_cm*CM,"pointer-events":"none"},svg);
        textAt(svg,left-.1*CM,y(value),'east',channel.axis.labels[k],{"pointer-events":"none"});
      });
      const unit=["","1"].includes(channel.unit)?"":` [${channel.unit}]`, labelX=left-1.05*CM, labelY=y0+rowHeight/2;
      el("text",{x:labelX,y:labelY,transform:`rotate(-90 ${labelX} ${labelY})`,"text-anchor":"middle","dominant-baseline":"central","font-size":font},svg).textContent=channel.label+unit;
      const clip=el("clipPath",{id:`waveClip${i}`},el("defs",{},svg));
      el("rect",{x:left,y:y0,width:right-left,height:rowHeight},clip);
      const d=channel.points.map(([t,v],j)=>`${j?'L':'M'}${x(t).toFixed(5)},${y(v).toFixed(5)}`).join(' ');
      el("path",{d,fill:"none",stroke:channel.color,"stroke-width":p.line_pt*p.pt_cm*CM,"stroke-linejoin":"round","clip-path":`url(#waveClip${i})`,"pointer-events":"none"},svg);
      el("path",{d:`M${left},${y0} V${y0+rowHeight} H${right}`,fill:"none",stroke:"#111","stroke-width":.35*p.pt_cm*CM,"pointer-events":"none"},svg);
      times.forEach(t=> {
        const tx=x(t*factor+origin);
        el("line",{x1:tx,y1:y0+rowHeight,x2:tx,y2:y0+rowHeight-.06*CM,stroke:"#111","stroke-width":.35*p.pt_cm*CM,"pointer-events":"none"},svg);
        if(i===info.channels.length-1) textAt(svg,tx,y0+rowHeight+.04*CM,'north',String(t),{"pointer-events":"none"});
      });
    });
    candidate.boundaries.forEach((t,i)=> {
      const group=el("g",{"data-marker":i,class:"wave-marker"},svg);
      Object.values(rows).forEach(row=>el("line",{x1:x(t),y1:row.y0,x2:x(t),y2:row.y0+rowHeight,stroke:"gray","stroke-width":.3*p.pt_cm*CM,"stroke-dasharray":`${3*p.pt_cm*CM} ${3*p.pt_cm*CM}`},group));
      el("line",{x1:x(t),y1:top,x2:x(t),y2:box.bottom,stroke:"transparent","stroke-width":.18*CM},group);
      el("title",{},group).textContent=`t${i} = ${Number((t/factor).toPrecision(12))} ${candidate.display_unit}，拖动调整`;
    });
    for(const label of p.labels) {
      const [dx,dy]=toPx(candidate.label_offsets?.[label.key] || [0,0]);
      const t=textAt(svg,left+label.at[0]*CM+dx,top+rowHeight-label.at[1]*CM+dy,'south',label.text,
        {"data-label":label.key,class:"wave-label",fill:selLabel===label.key?'#2457d6':'#111'});
      if(label.key.startsWith('t')) {t.textContent='t';el('tspan',{'baseline-shift':'sub','font-size':font*.7},t).textContent=label.key.slice(1);}
      el("title",{},t).textContent="拖动或方向键移开标签（分界线不动）";
    }
    drawNotes(svg,candidate,x);
    if(gesture?.draft) el("line",{x1:gesture.px[0],y1:gesture.px[1],x2:gesture.now[0],y2:gesture.now[1],stroke:"#2457d6","stroke-dasharray":"3 3","pointer-events":"none"},svg);
    textAt(svg,(left+right)/2,box.bottom+.42*CM,'north',`Time ${candidate.relative_time?'t − t₀ ':''}[${candidate.display_unit==='us'?'μs':candidate.display_unit}]`);
    // Like TeX's minimum bounding box, include notes dragged into a margin.
    // Only annotations expand it; off-window trace samples are clipped.
    let x0=0,y0=0,x1=width,y1=height;
    svg.querySelectorAll('.wave-note,.wave-label').forEach(item=> {
      const b=item.getBBox(), pad=p.pt_cm*CM;
      x0=Math.min(x0,b.x-pad);y0=Math.min(y0,b.y-pad);
      x1=Math.max(x1,b.x+b.width+pad);y1=Math.max(y1,b.y+b.height+pad);
    });
    const vw=x1-x0,vh=y1-y0, wrap=svg.parentElement;
    svg.setAttribute('viewBox',`${x0} ${y0} ${vw} ${vh}`);
    svg.style.width=`${viewZoom==='fit'?Math.max(100,Math.min(wrap.clientWidth-24,(wrap.clientHeight-24)*vw/vh)):vw/CM/2.54*96*Number(viewZoom)}px`;
    svg.style.height='auto';svg.style.aspectRatio=`${vw} / ${vh}`;
    if(gesture?.viewport) {
      svg.setAttribute('viewBox',gesture.viewport.box);
      svg.style.width=gesture.viewport.width;svg.style.aspectRatio=gesture.viewport.ratio;
    }
  }
  function drawNotes(svg,candidate,x) {
    const [start,end]=candidate.window;
    (candidate.notes || []).forEach((note,i)=> {
      const row=box.rows[note.channel], pts=[note.at,note.from,note.to].filter(Boolean);
      if(!row || pts.some(p=>p[0]<start || p[0]>end)) return;
      const sel=i===selNote, ink=ink_css(note.color), g=el("g",{"data-note":i,class:"wave-note"},svg);
      const p=info.paper, font=(note.font_pt || p.font_pt)*p.pt_cm*CM;
      const line=(note.line_pt || p.line_pt*(note.kind==='arrow'?p.arrow_weight:1))*p.pt_cm*CM;
      const px=p=>[x(p[0]),row.y(p[1])];
      let at, anchor="center", [dx,dy]=toPx(note.offset || [0,0]);
      if(note.kind==="arrow") {
        const a=px(note.from), b=px(note.to), L=Math.hypot(b[0]-a[0],b[1]-a[1]) || 1;
        const head=p.arrow_head_cm*CM, half=head*.45;
        const u=[(b[0]-a[0])/L,(b[1]-a[1])/L], base=[b[0]-u[0]*head,b[1]-u[1]*head];
        el("line",{x1:a[0],y1:a[1],x2:base[0],y2:base[1],stroke:ink,"stroke-width":line,"stroke-linecap":"round"},g);
        el("line",{x1:a[0],y1:a[1],x2:b[0],y2:b[1],stroke:"transparent","stroke-width":12},g);
        el("polygon",{points:[b,[base[0]-u[1]*half,base[1]+u[0]*half],[base[0]+u[1]*half,base[1]-u[0]*half]].map(p=>p.join(",")).join(" "),fill:ink},g);
        // Decide in printed cm like the exporter; screen px are scaled differently per axis.
        const {sx,sy}=scales(); at=a; anchor=tailAnchor((b[0]-a[0])/sx,(a[1]-b[1])/sy);
        if(sel) [["from",a],["to",b]].forEach(([end,p])=>el("circle",{cx:p[0],cy:p[1],r:5,class:"wave-handle","data-note-end":end},g));
      } else {
        at=px(note.at);
        if(note.kind==="dot") {
          el("circle",{cx:at[0],cy:at[1],r:(note.radius_cm || p.radius_cm)*CM,fill:"#fff",stroke:ink,"stroke-width":line},g);
          anchor="south west"; if(!note.offset) [dx,dy]=toPx(info.paper.dot_label_offset);
        }
      }
      if(note.text) {
        const t=textAt(g,at[0]+dx,at[1]+dy,anchor,note.text,{fill:ink,"font-size":font,"data-note-label":i,class:"wave-label"});
        el("title",{},t).textContent=`${note.text}（拖动移动文字，双击修改）`;
      }
      if(sel) {const bounds=g.getBBox();el('rect',{x:bounds.x-2,y:bounds.y-2,width:bounds.width+4,height:bounds.height+4,fill:'none',stroke:'#2457d6','stroke-width':.6,'stroke-dasharray':'2 2','pointer-events':'none'},g);}
    });
  }
  function ink_css(c) { return (info.paper.inks || {})[c || "black"] || c || "#000"; }
  function svgPoint(ev) {
    const p=new DOMPoint(ev.clientX,ev.clientY).matrixTransform($("waveChart").getScreenCTM().inverse());
    return [p.x,p.y];
  }
  function timeAt(ev) {
    const x=svgPoint(ev)[0];
    return box.start+Math.max(0,Math.min(1,(x-box.left)/(box.right-box.left)))*(box.end-box.start);
  }
  // The channel row under a point and the [time, value] there (notes are stored in data units).
  function dataAt([px,py],column) {
    const entries=Object.entries(box.rows);
    const [name,row]=column?[column,box.rows[column]]:entries.reduce((best,e)=>
      Math.abs(py-(e[1].y0+box.rowHeight/2))<Math.abs(py-(best[1].y0+box.rowHeight/2))?e:best);
    const t=box.start+Math.max(0,Math.min(1,(px-box.left)/(box.right-box.left)))*(box.end-box.start);
    const v=row.lo+(row.y0+box.rowHeight-py)/box.rowHeight*(row.hi-row.lo);
    return {channel:name,point:[Number(t.toPrecision(12)),Number(v.toPrecision(6))]};
  }
  function setTool(kind) {
    selNote=null;selLabel=null;properties();
    tool=tool===kind?null:kind;
    ["text","arrow","dot"].forEach(k=>$(`waveNote_${k}`).classList.toggle("on",tool===k));
    $("waveChart").style.cursor=tool?"crosshair":"";
    if(tool) note({text:"点击放文字",arrow:"从箭尾拖到箭头尖",dot:"点击关键点放小圆圈"}[tool]+"；再点一次按钮退出。");
  }
  function addNote(item) {
    const candidate=clone(layout.waveform);
    candidate.notes=[...(candidate.notes || []),item];
    setTool(null);selNote=candidate.notes.length-1;selectedPart='body';change(candidate);
  }
  function editNote(i) {
    const candidate=clone(layout.waveform), n=candidate.notes[i];
    const text=prompt("标注文字（英文或 $数学$，如 $i_L$ peak；留空删除文字）",n.text || "");
    if(text===null) return;
    if(text.trim()) n.text=text.trim(); else if(n.kind==="text") return removeNote(i); else delete n.text;
    change(candidate);
  }
  function removeNote(i) {
    const candidate=clone(layout.waveform);
    candidate.notes.splice(i,1); if(!candidate.notes.length) delete candidate.notes;
    selNote=null; change(candidate);
  }
  $("waveNote_text").onclick=()=>setTool("text");
  $("waveNote_arrow").onclick=()=>setTool("arrow");
  $("waveNote_dot").onclick=()=>setTool("dot");
  $("waveNoteDelete").onclick=()=>{if(selNote!==null) removeNote(selNote);};
  $("waveChart").ondblclick=ev=> {
    const g=ev.target.closest("[data-note]");
    if(g) editNote(Number(g.dataset.note));
  };
  panel.addEventListener("keydown",ev=> {
    if(/INPUT|SELECT|TEXTAREA/.test(ev.target.tagName) || ev.target.isContentEditable) return;
    if((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase()==='z') {ev.preventDefault();undo();return;}
    if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(ev.key) && (selNote!==null || selLabel)) {
      ev.preventDefault();const key=ev.key, fast=ev.shiftKey;
      nudges=nudges.then(()=>nudge(key,fast)).catch(e=>note(e.message,true));return;
    }
    if((ev.key==="Delete" || ev.key==="Backspace") && selNote!==null) {ev.preventDefault();removeNote(selNote);}
    else if(ev.key==="Escape" && (selNote!==null || selLabel || tool)) {selNote=null;selLabel=null;$("waveNoteDelete").disabled=true;if(tool) setTool(tool); else draw();properties();}
  });
  $("waveChart").onpointerdown=ev=> {
    if(!info || ev.button!==0) return;
    const svg=$("waveChart"), p=svgPoint(ev), cfg=clone(layout.waveform);
    const base={px:p,now:p,x:ev.clientX,cfg,viewport:{box:svg.getAttribute('viewBox'),width:svg.style.width,ratio:svg.style.aspectRatio}};
    ev.currentTarget.setPointerCapture(ev.pointerId);
    // Keyboard focus on the chart, so Delete reaches the panel and not the circuit.
    ev.currentTarget.focus({preventScroll:true});
    if(tool) { gesture={...base,draft:tool}; return; }
    const label=ev.target.closest("[data-label]"), end=ev.target.closest("[data-note-end]");
    const text=ev.target.closest("[data-note-label]"), item=ev.target.closest("[data-note]");
    if(label) {selLabel=label.dataset.label;selNote=null;gesture={...base,label:selLabel};}
    else if(item) {
      const i=Number(item.dataset.note), n=cfg.notes[i];
      selLabel=null;selNote=i; selectedPart=text && n.kind!=="text"?'label':'body';$("waveNoteDelete").disabled=false;
      gesture={...base,note:i,part:end?end.dataset.noteEnd:text && n.kind!=="text"?"label":"body"};
    } else {
      selLabel=null;
      if(selNote!==null) {selNote=null;$("waveNoteDelete").disabled=true;}
      const marker=ev.target.closest("[data-marker]"), t=timeAt(ev);
      gesture={...base,marker:marker?Number(marker.dataset.marker):null,start:t,current:t};
    }
    draw();properties();
  };
  $("waveChart").onpointermove=ev=> {
    if(!gesture) return;
    const g=gesture, p=svgPoint(ev); g.now=p;
    const dx=p[0]-g.px[0], dy=p[1]-g.px[1];
    if(g.draft) { if(g.draft==="arrow") draw(); return; }
    if(g.label) {
      const candidate=clone(g.cfg), [ox,oy]=toPx(candidate.label_offsets?.[g.label] || [0,0]);
      candidate.label_offsets={...(candidate.label_offsets || {}),[g.label]:toCm(ox+dx,oy+dy)};
      g.candidate=candidate; draw(candidate); return;
    }
    if(g.note!==undefined) {
      const candidate=clone(g.cfg), n=candidate.notes[g.note], row=box.rows[n.channel];
      if(g.part==="label") {
        const fallback=n.kind==="dot"?info.paper.dot_label_offset:[0,0], [ox,oy]=toPx(n.offset || fallback);
        n.offset=toCm(ox+dx,oy+dy);
      } else {
        const move=q=>{const at=[box.left+(q[0]-box.start)/(box.end-box.start)*(box.right-box.left),row.y(q[1])];
          return dataAt([at[0]+dx,at[1]+dy],n.channel).point;};
        if(g.part==="from" || g.part==="to") n[g.part]=move(g.cfg.notes[g.note][g.part]);
        else for(const key of ["at","from","to"]) if(n[key]) n[key]=move(g.cfg.notes[g.note][key]);
      }
      g.candidate=candidate; draw(candidate); return;
    }
    const t=timeAt(ev);g.current=t;
    if(g.marker!==null) {
      const candidate=clone(g.cfg), i=g.marker, marks=candidate.boundaries;
      const eps=(candidate.window[1]-candidate.window[0])*1e-7;
      marks[i]=Math.max(i?marks[i-1]+eps:info.extent[0],Math.min(i<marks.length-1?marks[i+1]-eps:info.extent[1],t));
      // Keep the axes still during dragging; the new extent is applied on release.
      g.candidate=candidate; draw(candidate);
    } else {
      $("waveZoomBand")?.remove();
      const x=t=>box.left+(t-box.start)/(box.end-box.start)*(box.right-box.left);
      el("rect",{id:"waveZoomBand",x:Math.min(x(t),x(g.start)),y:box.top,width:Math.abs(x(t)-x(g.start)),height:box.bottom-box.top,
        fill:"#2457d6","fill-opacity":.12,"pointer-events":"none"},$("waveChart"));
    }
  };
  $("waveChart").onpointerup=ev=> {
    if(!gesture) return;
    const g=gesture;gesture=null;
    const moved=Math.hypot(g.now[0]-g.px[0],g.now[1]-g.px[1])>1e-6;
    if(g.draft) {
      const a=dataAt(g.px);
      if(g.draft==="arrow") {
        if(!moved) {note("箭头需要拖出一段：按住箭尾，拖到要指的点再松开。",true);draw();return;}
        const text=prompt("箭尾文字（可留空；英文或 $数学$）","");
        addNote({kind:"arrow",channel:a.channel,from:a.point,to:dataAt(g.now,a.channel).point,...(text?.trim()?{text:text.trim()}:{})});
      } else {
        const text=prompt(g.draft==="text"?"标注文字（英文或 $数学$，如 $i_L$ peak）":"关键点文字（可留空，如 $I_{L,\\max}$）","");
        if(text===null || (g.draft==="text" && !text.trim())) {draw();return;}
        addNote({kind:g.draft,channel:a.channel,at:a.point,...(text.trim()?{text:text.trim()}:{})});
      }
      return;
    }
    if((g.label || g.note!==undefined) && g.candidate && moved) {change(g.candidate);return;}
    if(g.label || g.note!==undefined) {draw();return;}
    if(g.marker!==null && g.candidate) {
      g.candidate.window=[g.candidate.boundaries[0],g.candidate.boundaries.at(-1)];delete g.candidate.period_column;
      change(g.candidate);
    } else if(g.marker===null && Math.abs(ev.clientX-g.x)>6) range(Math.min(g.start,g.current),Math.max(g.start,g.current));
    else if(g.marker===null) {
      const i=g.cfg.boundaries.findIndex((a,j)=>a<=g.start && g.start<=g.cfg.boundaries[j+1]);
      if(i>=0) selectInterval(i);
    }
    draw();
  };
  $("waveChart").onpointercancel=()=>{gesture=null;draw();};
  new ResizeObserver(()=>{if(!panel.hidden && !gesture) draw();}).observe(panel);
  $("waveSave").onclick=async()=> {
    try {const r=await request("/api/waveform-save",layout.waveform);setEditorFile(r.saved,JSON.stringify(layout));note(`已保存到 ${r.saved}`);}
    catch(e) {note(e.message,true);}
  };
  $("waveExport").onclick=()=> {
    showDialog("导出波形与模态",`<p>独立波形图、用到的模态图各一份；勾选模态时另有带 t₀、t₁… 对应关系的总览。</p>
      <div class="export-row"><label><input id="waveIncludeModes" type="checkbox" checked> 包含对应模态与总览</label></div>
      <div class="export-row"><label><input id="waveShade" type="checkbox" ${(layout.waveform.slice_style || 'shade')==='shade'?'checked':''}> 波形按模态切片：区间交替灰底，上方标 Mode k</label></div>
      <div class="export-row"><label>总览排版 <select id="waveArrange">${[["stack","竖排：波形下依次放各区间模态"],["side","并排：左波形、右模态（需双栏）"],["grid","上波形、下模态两列（双栏）"]].map(([v,t])=>`<option value="${v}" ${(layout.waveform.arrange || 'stack')===v?'selected':''}>${t}</option>`).join('')}</select></label></div>
      <p class="hint">并排与两列排版时每个模态只画一次，图注列出它对应的全部区间；模态图按单栏宽度绘制，字号与波形一致。当前图幅：${/double|双栏/.test(profile)?'双栏':'单栏'}。</p>
      <div class="export-row">${['svg','png','pdf','tex'].map(f=>`<label><input type="checkbox" name="waveFormat" value="${f}" ${f==='svg'?'checked':''}> ${f==='tex'?'TeX 源码（可选）':f.toUpperCase()}</label>`).join('')}</div>
      <p class="hint">Word 可选 SVG / PNG；LaTeX 可选 PDF。TeX 为可独立编译的源码。只生成勾选的格式，编译中间文件不放入输出目录。</p>
      <div class="export-row"><input id="waveOutDir" type="text" value="${esc(defaultExportDir+'/waveforms')}"><button id="waveBrowse">浏览文件夹…</button></div>
      <div id="waveFolders" hidden></div><button id="waveExportRun" class="primary">导出</button><p id="waveExportState" role="status"></p><div id="waveExportResult"></div>`);
    async function browse(path) {
      try {
        const r=await api("/api/directories",{path}), box=$("waveFolders");box.hidden=false;box.replaceChildren();
        const title=document.createElement("p");title.textContent=r.path;box.appendChild(title);
        const button=(text,fn)=>{const b=document.createElement("button");b.textContent=text;b.onclick=fn;box.appendChild(b);};
        button("使用此文件夹",()=>{$("waveOutDir").value=r.path;box.hidden=true;});
        button("上一级",()=>browse(r.parent));
        r.roots.forEach(root=>button(root,()=>browse(root)));
        r.directories.forEach(dir=>button(dir.name,()=>browse(dir.path)));
      } catch(e) {$("waveExportState").textContent=e.message;}
    }
    $("waveBrowse").onclick=()=>browse($("waveOutDir").value);
    $("waveExportRun").onclick=async()=> {
      const button=$("waveExportRun"), state=$("waveExportState");button.disabled=true;
      state.className="hint";state.textContent="正在校验并出图…";
      try {
        const cfg={...clone(layout.waveform),slice_style:$("waveShade").checked?"shade":"lines",arrange:$("waveArrange").value};
        const r=await request("/api/waveform-export",cfg,{out_dir:$("waveOutDir").value,
          formats:[...document.querySelectorAll('[name=waveFormat]:checked')].map(c=>c.value),include_modes:$("waveIncludeModes").checked});
        installed(await request('/api/waveform-view',cfg),false);
        state.className="ok";state.textContent="导出完成。排版设置已记在当前图里，保存文件即可保留。";
        $("waveExportResult").replaceChildren();
        r.files.forEach(file=>{const p=document.createElement("p");p.textContent=file;$("waveExportResult").appendChild(p);});
      } catch(e) {state.className="bad";state.textContent=e.message;}
      finally {button.disabled=false;}
    };
  };
})();
