/* EMI runs and suggestions are snapshots. Only explicit adoption edits the drawing. */
let lastEMIEstimate = null;
let emiRunning = false;

function localizeEMISpectrum(image) {
  if (!image?.emiSource) return;
  const document = new DOMParser().parseFromString(image.emiSource, 'image/svg+xml');
  document.querySelectorAll('text').forEach(text => { text.textContent = cdI18n.translate(text.textContent); });
  image.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(new XMLSerializer().serializeToString(document));
}
document.addEventListener('editor-language-change', () => localizeEMISpectrum(document.querySelector('#emiResult img')));

document.getElementById("emi").onclick = async () => {
  showDialog("传导 EMI 估算", '<div id="emiPanel"><p role="status">读取设置…</p></div>');
  const panel = document.getElementById("emiPanel"), origin = editorTableSnapshot(layout);
  const base = JSON.parse(JSON.stringify(layout));
  const active = () => panel.isConnected && document.getElementById("dlg").open;
  try {
    const info = await api("/api/emi-info", {layout:base, profile});
    if (!active()) return;
    const cfg = base.emi || Object.fromEntries(Object.entries(info.inputs).map(([k,v]) => [k,v[1]]));
    const select = (id, entries, value, empty = false) => `<select id="${id}">${empty ? '<option value="">未指定</option>' : ''}${entries.map(([v,t]) => `<option value="${esc(v)}" ${v===value?'selected':''}>${esc(t)}</option>`).join("")}</select>`;
    const input = (id, value) => `<input id="${id}" value="${esc(value)}" type="text">`;
    const row = (label, control) => `<label class="export-row">${esc(label)} ${control}</label>`;
    const probes = (base.simulation?.probes || []).filter(p=>p.kind==='voltage').map(p=>[p.name,p.name]);
    const elements = kind => base.elements.filter(e=>e.kind===kind).map(e=>[e.id,e.id]);
    panel.innerHTML = `<p>${esc(info.note)}</p>
      <p>先画好两条电源线的 LISN、50 Ω 接收端和机壳回路。可在“AI → 描述新拓扑…”输入 buck_emi，再点“直接载入模板”（无需调用模型）。修改供电电压与采样时间请到“分析 → SPICE 仿真…”。</p>
      <details id="emiSettings" open><summary>测量设置与参数</summary><fieldset id="emiFields"><legend>测量与时间窗</legend>
      ${Object.entries(info.inputs).map(([k,v])=>row(v[0], ['positive','negative'].includes(k)?select('emi_'+k,probes,cfg[k]):k==='reference'?select('emi_'+k,Object.entries(info.references),cfg[k]):input('emi_'+k,cfg[k]))).join('')}
      <details><summary>滤波器初算与重跑</summary><p>选择已经画在图上的器件。共模绕组 p1/s1 朝 LISN，p2/s2 接两只差模电感的 a；电感 b 接 X 两端与各 Y 的 a，Y 的 b 接机壳。推荐只改这些器件的数值。</p>
      ${Object.entries(info.filter_parts).map(([k,[label,kind]])=>row(label,select('emif_'+k,elements(kind),cfg.filter?.[k],true))).join('')}
      ${Object.entries(info.filter_inputs).map(([k,v])=>row(v[0],input('emif_'+k,cfg.filter?.[k]??v[1]))).join('')}
      <p>共模电感每绕组 L、耦合 k；程序换算为现有耦合绕组模型。X/Y 是接法名称，器件额定电压、漏电和安规等级需自行核定。</p></details>
      <button id="emiSave">采用测量设置</button>
      <button id="emiRun">运行当前参数</button><button id="emiCompare">初算滤波器并重跑对比</button>
      <details><summary>导线上对机壳的寄生电容</summary>
      <p>先用普通电容把导线与机壳连起来，再在这里登记。a 接被测导线，b 接机壳；采用后在画布和论文图中用虚线显示。</p>
      ${row('已画电容',select('emip_element',elements('C'),cfg.parasitics?.[0]?.element))}
      ${row('关联导线的引脚',input('emip_net',cfg.parasitics?.[0]?.net||''))}
      ${row('来源',select('emip_source',Object.entries(info.sources),cfg.parasitics?.[0]?.source||'manual'))}
      ${row('直接填写电容 (F)',input('emip_value','100p'))}
      ${row('平行板面积 (mm²)',input('emip_area_mm2','100'))}${row('间距 (mm)',input('emip_gap_mm','0.1'))}${row('相对介电常数',input('emip_epsilon_r','4'))}
      <p>平行板 C=ε₀εᵣA/d，不含边缘场。采用值只更新已画电容，可撤销。</p>
      <button id="emiParasitic">采用寄生参数</button>
      <p>实测／Q3D：先把报告转换为 CSV，表头为 element,net,capacitance_F，电容单位必须是 F；不直接读取 .aedt 或电容矩阵。</p>
      <label>CSV 文件 <input id="emiImport" type="file" accept=".csv,text/csv"></label>
      </details></fieldset></details><p id="emiState" role="status"></p><div id="emiResult"></div>`;
    const $ = id => panel.querySelector('#'+id);
    const state = (text,bad=false) => { if(active()){ $('emiState').textContent=text; $('emiState').className=bad?'bad':'hint'; } };
    const current = () => {
      const doc = JSON.parse(JSON.stringify(base));
      doc.emi = {...base.emi,version:1};
      for (const k of Object.keys(info.inputs)) doc.emi[k]=$('emi_'+k).value;
      const bound=Object.keys(info.filter_parts).map(k=>$('emif_'+k).value);
      if(bound.some(Boolean)) {
        doc.emi.filter=Object.fromEntries([...Object.keys(info.filter_parts),...Object.keys(info.filter_inputs)].map(k=>[k,$('emif_'+k).value]));
      } else delete doc.emi.filter;
      return doc;
    };
    const adopt = candidate => {
      if (editorTableSnapshot(layout)!==origin) throw new Error('图纸在此期间已改动；请重新打开 EMI 窗口，原图未覆盖。');
      pushHistory(); layout=candidate; changed(); document.getElementById('dlg').close();
      flash('已采用，可撤销；请保存图纸。');
    };
    async function prepare(action, extra={}) {
      $('emiFields').disabled=true;
      try {
        const r=await api('/api/emi-prepare',{layout:current(),profile,action,...extra});
        if(active()) adopt(r.layout);
      } catch(e) {state(e.message,true);}
      finally {if(active()) $('emiFields').disabled=false;}
    }
    function showResult(record) {
      const r=record.result, box=$('emiResult');
      $('emiSettings').open=false;
      box.replaceChildren();
      const image=document.createElement('img'); image.alt='EMI 频谱估算对比'; image.style.width='100%';
      image.emiSource=r.svg;localizeEMISpectrum(image);box.append(image);
      const p=document.createElement('p');p.textContent=`实际分析窗 ${r.before.start_s.toPrecision(6)}～${r.before.last_sample_s.toPrecision(6)} s；FFT ${r.before.samples} 点，频点间隔 ${r.before.bin_hz.toPrecision(5)} Hz，Hann 等效噪声带宽 ${r.before.hann_enbw_hz.toPrecision(5)} Hz。`;box.append(p);
      const table=document.createElement('table');
      table.innerHTML='<thead><tr><th>通道／模式</th><th>原最大值 dBμV</th><th>建议最大值 dBμV</th></tr></thead><tbody>'+Object.keys(r.before.peaks).map(key=>`<tr><td>${esc(info.channels[key])}</td><td>${r.before.peaks[key].dbuv.toFixed(2)}</td><td>${r.after?r.after.peaks[key].dbuv.toFixed(2):'—'}</td></tr>`).join('')+'</tbody>';box.append(table);
      if(r.before.max_above_average_reference_db!==undefined){
        const gap=document.createElement('p');gap.id='emiGap';gap.textContent=`相对 AV 参考线的最大数值差：原参数 ${r.before.max_above_average_reference_db.toFixed(2)} dB${r.after?'；建议参数 '+r.after.max_above_average_reference_db.toFixed(2)+' dB':''}。正值表示仍高于参考线；负值也不代表合规。`;box.append(gap);
      }
      const stats=document.createElement('details');stats.innerHTML='<summary>时间窗前后半段波形统计（核对启动与漂移）</summary>';
      const statsTable=document.createElement('table');statsTable.innerHTML='<tr><th>参数组／通道</th><th>前半段均值</th><th>后半段均值</th><th>窗口最小～最大</th></tr>';
      for(const name of ['before','after'])if(r[name])for(const [channel,s] of Object.entries(r[name].time_summary||{})){
        const tr=document.createElement('tr');for(const text of [(name==='before'?'原参数':'建议参数')+' / '+channel,s.first_half.mean.toPrecision(5),s.second_half.mean.toPrecision(5),Math.min(s.first_half.min,s.second_half.min).toPrecision(5)+' ～ '+Math.max(s.first_half.max,s.second_half.max).toPrecision(5)]){const td=document.createElement('td');td.textContent=text;tr.append(td);}statsTable.append(tr);
      }stats.append(statsTable);box.append(stats);
      if(r.proposal) {
        const text=document.createElement('p'); text.textContent=`初算：共模每绕组 ${(r.proposal.per_winding_h*1e6).toPrecision(5)} μH，两只差模电感各 ${(r.proposal.dm_per_line_h*1e6).toPrecision(5)} μH；X ${(r.proposal.x_f*1e6).toPrecision(4)} μF，Y 各 ${(r.proposal.y_each_f*1e9).toPrecision(4)} nF。${r.proposal.note}`;box.append(text);
        const button=document.createElement('button');button.id='emiApply';button.textContent='采用已重跑的滤波参数';
        button.onclick=()=>{try{
          if(editorTableSnapshot(layout)!==record.origin || JSON.stringify(current())!==record.request) throw new Error('图纸或测量设置已变化，请重新计算；未采用旧结果。');
          adopt(r.candidate);
        }catch(e){state(e.message,true);}};box.append(button);
      }
      for(const [name,type,text] of [['spectrum.svg','image/svg+xml',r.svg],['result.json','application/json',JSON.stringify(r,null,2)]]){
        const a=document.createElement('a');a.download=name;a.textContent=' 下载 '+name+' ';a.href=`data:${type};charset=utf-8,`+encodeURIComponent(text);box.append(a);
      }
      const files=document.createElement('p');files.textContent='原始波形、网表及频谱 CSV 已保存：'+r.files.join('；');box.append(files);
    }
    async function simulate(compare) {
      if(emiRunning){state('已有 EMI 仿真正在运行；完成后重新打开此窗口查看结果。');return;}
      emiRunning=true;
      const request=current();$('emiFields').disabled=true;state('ngspice 计算中…关闭窗口后结果仍可在这里重新打开查看。');
      try{
        const result=await api('/api/emi-run',{layout:request,profile,compare,out_dir:joinPath(defaultExportDir,'emi')});
        lastEMIEstimate={result,origin,request:JSON.stringify(request)};
        if(active()){showResult(lastEMIEstimate);state('计算完成。请查看参考差距及滤波前后波形，不代表正式测试合格。');}
        else flash('EMI 计算完成，可重新打开“分析 → 传导 EMI 估算…”查看。');
      }catch(e){if(active())state(e.message,true);else flash('EMI 计算失败：'+e.message,true);}
      finally{emiRunning=false;if(active())$('emiFields').disabled=false;}
    }
    $('emiSave').onclick=()=>prepare('settings');
    $('emiRun').onclick=()=>simulate(false);$('emiCompare').onclick=()=>simulate(true);
    $('emiParasitic').onclick=()=>{
      const item={element:$('emip_element').value,net:$('emip_net').value,source:$('emip_source').value};
      if(item.source==='parallel_plate') for(const k of ['area_mm2','gap_mm','epsilon_r'])item[k]=$('emip_'+k).value;
      prepare('parasitic',{item,value:$('emip_value').value});
    };
    $('emiImport').onchange=async event=>{
      const file=event.target.files[0];if(!file)return;
      if(file.size>65536){state('寄生参数 CSV 最大 64 KiB',true);return;}
      const source=$('emip_source').value;
      if(!['measured','q3d'].includes(source)){state('导入前请选择“实测”或“Q3D 导出值”来源',true);return;}
      prepare('import',{csv:await file.text(),source});
    };
    $('emip_element').onchange=()=>{
      const id=$('emip_element').value, item=cfg.parasitics?.find(p=>p.element===id);
      $('emip_value').value=base.simulation?.components?.[id]?.value || '';
      $('emip_net').value=item?.net || '';
      $('emip_source').value=item?.source || 'manual';
      for(const [key,value] of Object.entries({area_mm2:'100',gap_mm:'0.1',epsilon_r:'4'})) $('emip_'+key).value=item?.[key]??value;
    };
    $('emip_element').onchange();
    if(lastEMIEstimate){showResult(lastEMIEstimate);state('显示上次计算记录；图纸或参数变化后须重新计算。');}
    if(emiRunning)state('已有 EMI 仿真正在运行；完成后重新打开此窗口查看结果。');
  }catch(e){if(active())panel.textContent=e.message;}
};
