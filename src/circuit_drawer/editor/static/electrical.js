// Electrical controls edit the same table and participate in its undo history.
const SCREEN_LABEL_FONT_RATIO = .74;
async function enableElectrical() {
  if (layout.electrical) return;
  const snapshot = JSON.stringify(layout);
  const result = await api('/api/electrical', {layout, profile});
  if (snapshot !== JSON.stringify(layout)) throw new Error('图已变化，请再试一次');
  pushHistory(); layout.electrical = result.electrical;
  await revalidate();
}

function updateElectricalPanel() {
  const target = document.getElementById('netlistText');
  if (!target || !report) return;
  document.getElementById('liveSpice').textContent = report.live_spice || '等待连接与仿真参数…';
  const section = layout.electrical;
  target.textContent = report.electrical_view ? report.electrical_view.lines.join('\n') : '等待校验…';
  if (section) target.textContent += '\n\n' + Object.entries(section.branches).map(([ref, n]) => `支路 ${n}  ${ref}`).join('\n');
}

function drawElectrical() {
  if (!layout || !report) return;
  const layer = el('g', {id: 'electricalOverlay'}, svg);
  if (document.getElementById('showElectrical').checked && report.electrical_view) {
    for (const label of report.electrical_labels || []) {
      const group = el('g', {transform: `translate(${label.at[0]},${-label.at[1]}) scale(${1/label.scale[0]},${1/label.scale[1]})`}, layer);
      // Smaller screen typography stays inside the checker's original label box.
      const font = label.font_cm * SCREEN_LABEL_FONT_RATIO;
      el('text', {x:0, y:0, 'text-anchor':'middle', 'dominant-baseline':'central',
        'data-label-ref':label.ref, 'data-label-box':JSON.stringify(label.box),
        style:`font-size:${font}px`,
        class:'electrical-label'+(label.kind === 'branch' ? ' electrical-branch' : '')}, group).textContent=label.text;
      if (label.kind !== 'branch') {
        const node = group.querySelector('text'), box = node.getBBox(), pad = font * .15;
        const left = Math.max(box.x-pad, (label.box[0]-label.at[0])*label.scale[0]);
        const right = Math.min(box.x+box.width+pad, (label.box[2]-label.at[0])*label.scale[0]);
        const top = Math.max(box.y-pad, (label.at[1]-label.box[3])*label.scale[1]);
        const bottom = Math.min(box.y+box.height+pad, (label.at[1]-label.box[1])*label.scale[1]);
        const background = el('rect', {x:left, y:top, width:right-left, height:bottom-top,
          rx:font*.18, class:'electrical-label-bg'}, group);
        group.insertBefore(background, node);
      }
    }
  }

  for (const mark of report.no_connect || []) {
    for (const [a,b] of mark) line(layer,a,b,'no-connect');
  }
}

function electricalProps() {
  const box = document.getElementById('props');
  box.querySelectorAll('.electrical-prop').forEach(e => e.remove());
  if (!selected || !layout) return;
  const add = (label, node) => { const l = document.createElement('label'); l.textContent = label; l.className = node.className = 'electrical-prop'; box.append(l,node); };
  let refs = [];
  if (selected.type === 'wire' && report?.wire_nets) refs = Object.keys(report.nets || {}).filter(p => report.nets[p] === report.wire_nets[selected.index]);
  if (selected.type === 'node') refs = layout.elements.flatMap(e => Object.entries(e.pins).filter(([,p]) => same(p,selected.at)).map(([k]) => e.id+'.'+k));
  if (refs.length) {
    const section = layout.electrical, target = section?.nodes.find(n => n.pins.some(p => refs.includes(p)));
    const input = document.createElement('input'); input.id = 'netName'; input.placeholder = '自动编号 / OP / SW'; input.value = target?.name || '';
    input.onchange = async () => {
      try {
        // the first rename also creates the labels: one undo step for both
        const fresh = !layout.electrical;
        await enableElectrical();
        const n = layout.electrical.nodes.find(n => n.pins.some(p => refs.includes(p)));
        const result = await api('/api/electrical', {layout, profile, action:'rename', id:n.id, name:input.value.trim()});
        if (!fresh) pushHistory();
        layout.electrical = result.electrical; changed();
      } catch(e) { input.value = target?.name || ''; flash(e.message,true); }
    };
    add('网络名',input);
  }
  if (selected.type === 'el') {
    const e = layout.elements.find(e => e.id === selected.id); if (!e) return;
    for (const k of Object.keys(e.pins)) {
      const ref = `${e.id}.${k}`, label = document.createElement('label'), cb = document.createElement('input');
      cb.type = 'checkbox'; cb.checked = (layout.no_connect || []).includes(ref); cb.dataset.noconn = ref;
      cb.onchange = () => setNoConnect(ref,cb.checked);
      label.append(cb,document.createTextNode('故意悬空（NoConn）')); add(k,label);
    }
  }
}

function setNoConnect(ref,value) {
  pushHistory();const nc=new Set(layout.no_connect||[]);value?nc.add(ref):nc.delete(ref);layout.no_connect=[...nc];changed();
}

let issueCursor = 0;
function updateValidationBar() {
  const status = document.getElementById('validationStatus');
  // an empty drawing gets its own hint in the issues page, not a count
  const issues = (report?.issues || []).filter(i => i.level !== 'note' && i.code !== 'empty-drawing');
  status.textContent = !report ? '未检查' : issues.length ? `有 ${issues.length} 个问题` : '✓ 校验通过';
  status.className = !report ? '' : issues.length ? 'bad' : 'ok';
  status.onclick = () => {
    const items = [...document.querySelectorAll('#issues > li')];
    showSidebar('issues');
    if (!items.length) return;
    const item = items[issueCursor++ % items.length]; item.click(); showSidebar('issues'); item.scrollIntoView({block:'nearest'});
    let points = [];
    if (selected?.type === 'el') points = Object.values(layout.elements.find(e => e.id === selected.id)?.pins || {});
    if (selected?.type === 'wire') points = layout.wires[selected.index] || [];
    if (points.length) { view.x0 = points.reduce((a,p)=>a+p[0],0)/points.length-view.w/2; view.y0 = points.reduce((a,p)=>a+p[1],0)/points.length-view.h/2; render(); }
  };
}

function buildElectricalLibrary(lib) {
  const categories = {R:'无源器件',L:'无源器件',C:'无源器件',D:'半导体',scr:'半导体',V:'电源',vac:'电源',vac3:'电源',battery:'电源',I:'电源',port:'连接',nmos:'半导体',pmos:'半导体',igbt:'半导体',transformer:'磁性器件',coupled:'磁性器件'};
  const paths = {R:'M1 12 H7 V7 H25 V17 H7 V12 M25 12 H31',L:'M1 12 H6 C6 1 12 1 12 12 C12 1 18 1 18 12 C18 1 24 1 24 12 H31',C:'M1 12 H13 M13 4 V20 M19 4 V20 M19 12 H31',D:'M1 12 H10 M10 5 L22 12 L10 19 Z M22 5 V19 M22 12 H31',scr:'M1 12 H10 M10 5 L22 12 L10 19 Z M22 5 V19 M22 12 H31 M22 12 L28 6 V1',V:'M16 1 V5 M16 19 V23 M12 10 H20 M16 7 V13 M12 16 H20',vac:'M16 1 V5 M16 19 V23 M9 12 Q12 3 16 12 T23 12',vac3:'M4 4 V20 M4 4 H8 M4 12 H8 M4 20 H8 M8 4 Q11 0 14 4 T20 4 M8 12 Q11 8 14 12 T20 12 M8 20 Q11 16 14 20 T20 20 M20 4 H31 M20 12 H31 M20 20 H31',battery:'M16 1 V9 M7 9 H25 M11 14 H21 M16 14 V23',I:'M16 1 V5 M16 19 V23 M16 17 V7 L13 10 M16 7 L19 10',port:'M1 12 H13 M19 12 A3 3 0 1 0 13 12 A3 3 0 1 0 19 12',nmos:'M1 12 H11 M11 4 V20 M15 5 V19 M15 7 H26 V1 M15 17 H26 V23',pmos:'M1 12 H5 M9 12 H11 M5 12 A2 2 0 1 0 9 12 A2 2 0 1 0 5 12 M11 4 V20 M15 5 V19 M15 7 H26 V1 M15 17 H26 V23',igbt:'M1 12 H11 M11 4 V20 M15 5 V19 M15 8 L26 1 M15 16 L26 23',transformer:'M5 2 C18 2 18 9 5 9 C18 9 18 16 5 16 C18 16 18 23 5 23 M16 1 V23 M19 1 V23 M30 2 C20 2 20 9 30 9 C20 9 20 16 30 16 C20 16 20 23 30 23',coupled:'M5 2 C18 2 18 9 5 9 C18 9 18 16 5 16 C18 16 18 23 5 23 M30 2 C20 2 20 9 30 9 C20 9 20 16 30 16 C20 16 20 23 30 23'};
  categories.zener = categories.D;
  paths.zener = paths.D + ' M22 5 H19.6';
  categories.pmsm = '电机';
  paths.pmsm = 'M1 6 H11 M1 12 H9 M1 18 H11 M29 12 A10 10 0 1 0 9 12 A10 10 0 1 0 29 12 M14 16 V8 L19 13 L24 8 V16';
  // A summary folds its existing cards; search temporarily reveals matching groups.
  const groups = new Map();
  const ordered = [...LIBRARY].sort((a,b)=>Object.values(categories).indexOf(categories[a[0]])-Object.values(categories).indexOf(categories[b[0]]));
  for (const [kind,name] of ordered) {
    const category = categories[kind];
    if (!groups.has(category)) {
      const group=document.createElement('details'), heading=document.createElement('summary'), count=document.createElement('span'), cards=document.createElement('div');
      group.open=true; group.dataset.category=category; heading.append(document.createTextNode(category));
      count.className='category-count'; heading.append(count); cards.className='component-grid'; group.append(heading,cards); lib.append(group);
      groups.set(category,{group,cards,count});
    }
    const b=document.createElement('button'); b.draggable=true; b.dataset.kind=kind; b.setAttribute('aria-label',name);
    b.innerHTML=`<svg viewBox="0 0 32 24" aria-hidden="true"><path d="${paths[kind]}"></path>${['V','vac','I'].includes(kind)?'<circle cx="16" cy="12" r="8"/>':''}</svg><span>${name}</span>`;
    b.title=`添加${name}`;
    b.onclick=()=>addFromLibrary(kind); b.ondragstart=ev=>{ev.dataTransfer.setData('application/x-circuit-kind',kind); ev.dataTransfer.effectAllowed='copy';};
    const {cards,count}=groups.get(category); cards.append(b); count.textContent=cards.children.length;
  }
  document.getElementById('componentSearch').oninput=ev=>{
    const q=ev.target.value.trim().toLowerCase();
    lib.querySelectorAll('button').forEach(b=>{
      const category=categories[b.dataset.kind];
      b.hidden=![b.textContent,cdI18n.sourceText(b),b.dataset.kind,category,cdI18n.translate(category)].join(' ').toLowerCase().includes(q);
    });
    for (const {group,cards,count} of groups.values()) {
      const visible=[...cards.children].filter(b=>!b.hidden).length;
      group.hidden=!visible; count.textContent=visible;
      if (q) { if (!group.hasAttribute('data-before-search')) group.dataset.beforeSearch=String(group.open); group.open=true; }
      else if (group.hasAttribute('data-before-search')) { group.open=group.dataset.beforeSearch==='true'; delete group.dataset.beforeSearch; }
    }
    document.getElementById('libraryEmpty').hidden=[...groups.values()].some(({group})=>!group.hidden);
  };
}

function openModelMapping() {
  const schema={switch:{ideal_switch:{},mosfet:{vto:'3',kp:'10',gate_voltage:'10'},igbt:{vce0:'1.5'}},R:{ideal:{},parasitic:{series_inductance:'10n',parallel_capacitance:'1p'}},L:{ideal:{},parasitic:{series_resistance:'0.1',parallel_capacitance:'1p'}},C:{ideal:{},parasitic:{series_resistance:'0.01',series_inductance:'1n'}}};
  const labels={legacy:'沿用各器件设置',ideal_switch:'理想开关',mosfet:'MOSFET（Level 1）',igbt:'IGBT（分段线性）',ideal:'理想',parasitic:'带寄生参数'};
  const fields={vto:'阈值电压 V',kp:'跨导参数 A/V²',gate_voltage:'栅极驱动 V',vce0:'正向压降 V',series_inductance:'串联电感 H',parallel_capacitance:'并联电容 F',series_resistance:'串联电阻 Ω'};
  let draft=clone(layout.model_mapping||{});
  showDialog('全图器件模型', '<p>按类别应用到全图，图形保持原样。仿真需要先填写器件数值和激励。</p><div id="mappingRows"></div><p class="hint">MOSFET 为通用 Level 1 模型；IGBT 为正向分段线性模型。参数须按用途核对。</p><button id="mappingApply" class="primary">应用到全图</button>');
  const rows=document.getElementById('mappingRows');
  const paint=()=>{rows.replaceChildren(); for(const [kind,models] of Object.entries(schema)) {
    const group=document.createElement('fieldset'), legend=document.createElement('legend'); legend.textContent={switch:'开关管',R:'电阻',L:'电感',C:'电容'}[kind]; group.append(legend);
    const select=document.createElement('select'); select.dataset.mapping=kind;
    for(const model of ['legacy',...Object.keys(models)]){const o=document.createElement('option');o.value=model;o.textContent=labels[model];select.append(o);}
    select.value=draft[kind]?.model||'legacy'; select.onchange=()=>{if(select.value==='legacy') delete draft[kind];else draft[kind]={model:select.value,...models[select.value]};paint();};group.append(select);
    for(const [key,value] of Object.entries(draft[kind]||{})){if(key==='model')continue;const label=document.createElement('label'),input=document.createElement('input'); label.textContent=fields[key]+' ';input.value=value;input.dataset.parameter=key;input.onchange=()=>draft[kind][key]=input.value;label.append(input);group.append(label);}
    rows.append(group);
  }};paint();
  document.getElementById('mappingApply').onclick=async()=>{const proposal={...layout,model_mapping:draft}; const result=await api('/api/check',{layout:proposal,profile}); const bad=result.issues.filter(i=>i.code==='bad-model-mapping');if(bad.length){flash(bad[0].message,true);return;}pushHistory();layout.model_mapping=draft;document.getElementById('dlg').close();changed();};
}

document.addEventListener('DOMContentLoaded',()=>{
  document.getElementById('showElectrical').onchange=async ev=>{try{if(ev.target.checked)await enableElectrical();render();}catch(e){ev.target.checked=false;flash(e.message,true);}};
  document.getElementById('showNetlist').onchange=async ev=>{document.getElementById('liveNetlist').hidden=!ev.target.checked;if(ev.target.checked)showSidebar('netlist');else if(sidebarPage==='netlist')showSidebar('properties');try{if(ev.target.checked){await enableElectrical();await revalidate();}updateElectricalPanel();}catch(e){flash(e.message,true);}};
  document.getElementById('modelMapping').onclick=openModelMapping;
  const canvas=document.getElementById('canvas');
  document.addEventListener('editor-language-change',()=>document.getElementById('componentSearch').dispatchEvent(new Event('input')));
  canvas.addEventListener('dragover',ev=>{if([...ev.dataTransfer.types].includes('application/x-circuit-kind')){ev.preventDefault();ev.dataTransfer.dropEffect='copy';}});
  canvas.addEventListener('drop',ev=>{const kind=ev.dataTransfer.getData('application/x-circuit-kind');if(!LIBRARY.some(([k])=>k===kind))return;ev.preventDefault();addFromLibrary(kind,toUnits(ev));});
});
