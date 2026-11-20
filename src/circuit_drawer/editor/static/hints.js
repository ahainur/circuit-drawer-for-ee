/* Shared contextual help for the circuit and control editors. */
(() => {
  const topics = {
    waveObjects: ["已放置对象",
      "选择图例、拓扑或图标可编辑它；选回“绘图区属性”可调整整张图。也可直接点图中的对象。"],
    wavePaint: ["绘图区底色",
      "预设色和自选色都用着色浓度与白色混合。17% 表示 17% 所选颜色加 83% 白色，0% 为白色。点“应用绘图区样式”看效果。"],
    waveSize: ["坐标区高度 / cm",
      "高度是导出时每组纵轴占用的纸面高度，同轴曲线共用这一行。圆角用印刷点 pt，设为 0 可得到直角。"],
    waveFont: ["出图字体",
      "选择宋体和 Times New Roman 时，出图电脑需要这两种字体和 XeLaTeX。也可选“期刊默认”。用“真实出图预览”核对最终字形。"],
    waveGrid: ["横向网格线",
      "横线对齐纵轴主刻度，竖线对齐横轴主刻度。两个方向可分别关闭、画实线或画虚线。曲线线型在“通道、标签和单位”中设置。"],
    waveInsetFrame: ["透明背景",
      "透明背景让网格和曲线透出来，图片原有的底色会保留。关闭“显示边框”可同时隐藏外框和投影，白底仍可单独保留。"],
    waveOwner: ["所属通道",
      "所属通道决定对象放在哪组坐标轴上。图例列出该组当前显示的曲线；隐藏所属通道也会隐藏它的插图。"],
    waveProbe: ["绑定测点",
      "输入 CSV 中的绝对时间（秒），在所属通道上读取该时刻的值。拖动图标后，连线仍指向同一个数据点。"],
    waveCapture: ["拓扑示意",
      "选择原理图或模态，再框选局部或直接采用整图。插入的是矢量快照；源图更改后，选中插图并点“从源图刷新”。"],
    wavePaper: ["纸面尺寸",
      "选择总览或独立图可按对应纸面宽度排版。查看比例只改变屏幕放大倍数。最终位置和字体可用“真实出图预览”核对。"],
    waveInsert: ["插入",
      "图例跟随同轴组的曲线。拓扑可截取整图或局部；系统图标、个人图片可在选好素材后点击绘图区放置，再拖动调整。"],
    waveInput: ["CSV 时间单位",
      "时间列留空时读取第一列。单位要与原 CSV 的数值一致，例如文件用秒就选 s；导入后可另选 ms 显示横轴。"],
    waveTime: ["显示单位",
      "起点和终点使用当前显示单位下的 CSV 绝对时间。勾选 t₀ 后，刻度从所选范围的起点算起；输入 8 ms 仍定位到原文件的 8 ms。"],
    wavePeriod: ["末尾一个 PWM 周期",
      "选门极或 PWM 通道，用最后两个上升沿截取一个周期，中间的下降沿作为分界。随后为每段选择模态。"],
    waveBindings: ["拆分所选区间",
      "先点 t₀→t₁ 这样的区间按钮，再选择对应的电路模态。拆分会在区间中点增加分界；展开精确设置可输入开关时刻。"],
    waveChannels: ["通道、标签和单位",
      "勾选要绘制的列。单位相同的曲线可选同一纵轴组进行比较；用不同线型可在黑白打印时区分它们。改好后应用通道设置。"],
    waveOffsets: ["标注属性",
      "点击标注文字可单独移动文字。右移和上移按纸面厘米计，负值分别向左和向下。方向键可微调位置。"],
    waveXY: ["XY / 状态平面",
      "两个通道按同一采样时刻配对成一点。箭头指向时间前进方向；按模态着色时，灰色表示该段尚未绑定。"],
    waveExport: ["导出波形与模态…",
      "只需曲线图时可取消“包含对应模态与总览”。要导出总览，请先给每个时间区间绑定模态，再选排版和文件格式。"],
    spiceReadiness: ["仿真状态",
      "点待补项可跳到对应设置。修改负载或开关频率后，核对这里的工况与测量通道，再运行或复跑。"],
    spiceDevices: ["搜索器件",
      "按器件编号或类型筛选，例如 RL 或 MOSFET。选中后在右侧改值；切换器件会保留本窗口里已填的参数。"],
    agentMode: ["任务方式",
      "只画图会保留未填参数的符号。示例仿真会先匹配工况；采用图纸后核对当前值、建议值和假设，再采用工况并运行。"],
    agentRefs: ["参考图",
      "想让 Agent 参考器件和连接时选结构草图；想沿用排版或配色时选风格参考。开始前可在任务描述中补充要保留的细节。"],
    figureComposition: ["组合出图",
      "添加子图后可调版式、间距和图注。要改其中一幅波形，打开该子图，改好点“更新组合子图”，再保存组合。"],
    figureExport: ["导出设置",
      "用彩色和黑白预览检查相近颜色与线型。论文可选 PDF 或 SVG 矢量图；PNG 的 DPI 决定栅格图分辨率。"],
  };
  const translate = text => window.cdI18n?.translate(text) || text;
  window.editorHint = (topic, id = 'hint-' + topic) => {
    const [label, message] = topics[topic];
    return `<details class="help-tip" data-topic="${topic}"><summary aria-label="${translate(label)}" aria-describedby="${id}">?</summary><div class="help-body" id="${id}" role="tooltip">${translate(message)}</div></details>`;
  };
  document.addEventListener('editor-language-change', () => {
    // The Agent panel translates its own text and keeps the user's form mounted.
    document.querySelectorAll('.help-tip[data-topic]').forEach(tip => {
      const [label, message] = topics[tip.dataset.topic];
      tip.firstElementChild.setAttribute('aria-label', translate(label));
      tip.lastElementChild.textContent = translate(message);
    });
    position();
  });

  let active, anchor, pinned = false, keyboard = true, timer, serial = 0;
  function trigger(node) {
    if (!(node instanceof Element)) return null;
    const summary = node.closest('.help-tip > summary');
    if (summary) return summary;
    const field = node.closest('[aria-describedby]') || node.closest('label')?.control;
    const body = document.getElementById(field?.getAttribute('aria-describedby'));
    return body?.matches('.help-tip > .help-body') ? field : null;
  }
  function owner(el) {
    return el.closest('.help-tip') || document.getElementById(el.getAttribute('aria-describedby'))?.parentElement;
  }
  function dismiss() {
    clearTimeout(timer);
    if (!active) return;
    const body = active.lastElementChild;
    if (body.isConnected && body.hasAttribute('popover')) body.hidePopover();
    active.open = false;
    observer.disconnect();
    active = anchor = null;
    pinned = false;
  }
  function position() {
    if (!active) return;
    const rect = anchor.getBoundingClientRect(), body = active.lastElementChild;
    if (!rect.width || !rect.height || rect.bottom < 0 || rect.top > innerHeight) { dismiss(); return; }
    const gap = 4, pad = 8;
    const below = innerHeight - rect.bottom - pad - gap, above = rect.top - gap - pad;
    body.style.maxHeight = Math.max(1, Math.max(above, below)) + 'px';
    const size = body.getBoundingClientRect();
    const top = size.height <= below || below >= above ? rect.bottom + gap : rect.top - size.height - gap;
    body.style.top = Math.max(pad, top) + 'px';
    body.style.left = Math.max(pad, Math.min(rect.left, innerWidth - size.width - pad)) + 'px';
  }
  const observer = new IntersectionObserver(entries => {
    if (entries.some(entry => entry.target === anchor && !entry.isIntersecting)) dismiss();
  });
  function show(el, pin = false) {
    const tip = owner(el);
    if (!tip || !el.isConnected) return;
    dismiss();
    active = tip; anchor = el; pinned = pin;
    const summary = tip.firstElementChild, body = tip.lastElementChild;
    summary.removeAttribute('title');
    if (!body.id) body.id = 'editor-help-' + ++serial;
    summary.setAttribute('aria-describedby', body.id);
    body.setAttribute('role', 'tooltip');
    tip.open = true;
    if (body.showPopover) { body.popover = 'manual'; body.showPopover(); }
    body.classList.add('floating-help');
    position();
    if (active) observer.observe(el);
  }
  document.addEventListener('pointerover', event => {
    if (event.pointerType === 'touch' || pinned) return;
    if (active?.lastElementChild.contains(event.target)) { clearTimeout(timer); return; }
    const el = trigger(event.target);
    if (!el || el === trigger(event.relatedTarget)) return;
    clearTimeout(timer);
    if (el !== anchor) timer = setTimeout(() => show(el), el.matches('summary') ? 180 : 550);
  });
  document.addEventListener('pointerout', event => {
    if (event.pointerType === 'touch' || pinned) return;
    if (trigger(event.target) === trigger(event.relatedTarget) && !active?.lastElementChild.contains(event.target)) return;
    clearTimeout(timer);
    if (active && !active.contains(event.relatedTarget) && trigger(event.relatedTarget) !== anchor)
      timer = setTimeout(dismiss, 180);
  });
  document.addEventListener('pointerdown', event => {
    keyboard = false;
    clearTimeout(timer);
    if (active && !active.contains(event.target)) dismiss();
  }, true);
  document.addEventListener('focusin', event => {
    if (!keyboard || active?.contains(event.target)) return;
    const el = trigger(event.target);
    if (el) show(el);
    else dismiss();
  });
  document.addEventListener('focusout', event => {
    if (active && (anchor === event.target || active.contains(event.target)) &&
        !active.contains(event.relatedTarget)) dismiss();
  });
  document.addEventListener('click', event => {
    const summary = event.target.closest('.help-tip > summary');
    if (!summary) return;
    event.preventDefault(); event.stopPropagation();
    if (pinned && active === summary.parentElement) dismiss();
    else show(summary, true);
  }, true);
  window.addEventListener('keydown', event => {
    keyboard = true;
    if (event.key === 'Escape' && active) {
      event.preventDefault(); event.stopImmediatePropagation(); dismiss();
    }
  }, true);
  document.addEventListener('scroll', position, true);
  window.addEventListener('resize', position);
  window.addEventListener('blur', dismiss);
})();
