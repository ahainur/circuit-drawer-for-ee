/* Proposals stay outside the drawing until the user accepts and saves them. */
function initSimulationRecommendations(info, base, profile, accept) {
  const host = document.getElementById("spiceRecommendation");
  if (base.closed_loop) {
    host.innerHTML = '<summary>推荐仿真参数（暂不支持闭环）</summary><p class="hint">参数推荐本版用于展开的开环电路；当前闭环的功率级和控制器请配合设计。</p>';
    return;
  }
  host.innerHTML = `<summary>推荐仿真参数…</summary>
    <p class="hint">基础 Buck、Boost 和交错 Buck 按公式计算；其他电路或文字需求使用现有 AI 配置。
      留空时采用明确列出的演示工况。每项附理由，并自动做一次短 ngspice 检查。</p>
    <fieldset id="recommendInputs" style="border:0;padding:0"><div class="spice-fields">
      ${Object.entries(info.recommendation_inputs).map(([key,label]) => `<label>${esc(label)}
        <input data-requirement="${esc(key)}" placeholder="留空使用建议工况" autocomplete="off"></label>`).join("")}</div>
      <label>文字需求（选填，使用 AI）<textarea id="recommendDescription" rows="2" style="width:100%"
        maxlength="8000" placeholder="例如：输入 24 V，输出 12 V、24 W，开关频率 50 kHz，电感纹波 30%"></textarea></label>
      <button type="button" id="recommendGo">生成推荐并检查</button></fieldset>
    <button type="button" id="recommendCancel" hidden>取消推荐</button>
    <p id="recommendState" class="hint" role="status" aria-live="polite"></p>
    <div id="recommendResult"></div>
    <button type="button" id="recommendApply" hidden disabled>采用到参数表</button>
    <p class="hint">采用会替换本窗口参数；点击“保存参数”或导出后才写入电路文件。短仿真不代表稳态、损耗或额定值合格。</p>`;
  const $ = id => host.querySelector("#" + id);
  const dialog = host.closest("dialog"), state = $("recommendState");
  let jobId = null, proposal = null, snapshot = null, cancelled = false, serial = 0;
  function busy(value) {
    $("recommendInputs").disabled = value;
    $("recommendCancel").hidden = !value;
    document.getElementById("spiceOptions").disabled = value;
    document.getElementById("spiceBuck").disabled = value;
    document.getElementById("spiceBoost").disabled = value;
  }
  async function cancel() {
    cancelled = true;
    proposal = null; $("recommendApply").disabled = true;
    state.textContent = "已取消，返回的建议不会采用。";
    if (jobId) await api("/api/cancel", {id: jobId}).catch(() => {});
  }
  $("recommendCancel").onclick = cancel;
  dialog.addEventListener("close", cancel, {once:true});
  host.addEventListener("input", () => {
    if (proposal) {
      proposal = null; $("recommendApply").disabled = true;
      state.textContent = "需求已修改，请重新生成推荐。";
    }
  });
  const labels = {ground:"参考地", probes:"测量通道"};
  for (const e of base.elements) {
    for (const [key,label] of [...(info.fields[e.kind] || []), ...(info.optional_fields[e.kind] || [])])
      labels[`components.${e.id}.${key}`] = `${e.id} · ${label}`;
  }
  for (const [key,label] of info.transient_fields) labels[`transient.${key}`] = label;
  const display = value => value == null ? "未设置" : typeof value === "object" ? JSON.stringify(value) : String(value);
  function show(result) {
    const check = result.verification;
    const stats = Object.entries(check.channels || {}).map(([name,s]) =>
      `<tr><td>${esc(name)}</td><td>${s.mean.toPrecision(5)}</td><td>${s.min.toPrecision(5)}</td><td>${s.max.toPrecision(5)}</td></tr>`).join("");
    $("recommendResult").innerHTML = `<p><b>${esc(result.method)}</b></p>
      <ol>${result.assumptions.map(s=>`<li>${esc(s)}</li>`).join("")}</ol>
      <p>${esc(check.message)}</p>
      ${check.stop_s ? `<p>检查至 ${(check.stop_s*1000).toPrecision(4)} ms；统计窗口从 ${(check.window_start_s*1000).toPrecision(4)} ms 开始。</p>` : ""}
      ${check.voltage_error_pct != null ? `<p>末段输出电压均值相对目标偏差：${check.voltage_error_pct.toFixed(2)}%。请核对是否已稳态，再判断是否需要调参。</p>` : ""}
      ${stats ? `<details><summary>短仿真统计</summary><table><thead><tr><th>通道</th><th>均值</th><th>最小值</th><th>最大值</th></tr></thead><tbody>${stats}</tbody></table></details>` : ""}
      <div style="max-height:300px;overflow:auto"><table style="width:100%;table-layout:fixed;text-align:left">
        <thead><tr><th>参数</th><th>原值</th><th>建议</th><th>理由</th></tr></thead><tbody>
        ${result.parameters.map(p=>`<tr data-parameter="${esc(p.path)}">${[labels[p.path] || p.path,display(p.before),display(p.value),p.reason]
          .map(s=>`<td style="overflow-wrap:anywhere;vertical-align:top">${esc(s)}</td>`).join("")}</tr>`).join("")}
        </tbody></table></div>`;
    $("recommendApply").hidden = false;
    $("recommendApply").disabled = check.status !== "completed";
  }
  $("recommendGo").onclick = async () => {
    const ticket = ++serial;
    cancelled = false; jobId = null; proposal = null;
    snapshot = JSON.stringify(layout);
    $("recommendApply").hidden = true; $("recommendApply").disabled = true;
    $("recommendResult").replaceChildren();
    state.className = "hint"; state.textContent = "正在生成参数建议并检查；原图和文件保持不动…";
    busy(true);
    const present = () => ticket === serial && host.isConnected && dialog.open && !cancelled;
    try {
      const requirements = Object.fromEntries([...host.querySelectorAll("[data-requirement]")]
        .map(e=>[e.dataset.requirement,e.value.trim()]).filter(([,v])=>v!==""));
      let job = await api("/api/spice-recommend", {layout:clone(base), profile, requirements,
        description:$("recommendDescription").value.trim()});
      jobId = job.id;
      if (!present()) { await api("/api/cancel", {id:jobId}); return; }
      while (job.state === "running") {
        state.textContent = `正在计算或等待 AI，并做短仿真（${job.elapsed_s} 秒）…`;
        await new Promise(resolve=>setTimeout(resolve,350));
        if (!present()) return;
        job = await api("/api/job/" + jobId);
      }
      if (!present()) return;
      if (job.state === "cancelled") { state.textContent = "已取消。"; return; }
      if (job.state === "error") throw new Error(job.error);
      if (!job.result?.ok) throw new Error("未得到通过校验的建议：\n" + (job.result?.rounds || [])
        .map(r=>`第 ${r.round} 轮：${r.errors.join("；")}`).join("\n"));
      proposal = job.result.proposal; show(proposal);
      state.textContent = proposal.verification.status === "completed" ? "建议已生成，原参数尚未改变。请核对理由和短仿真结果。" : proposal.verification.message;
    } catch (e) {
      if (present()) { state.className = "bad"; state.textContent = e.message; }
    } finally {
      if (host.isConnected && ticket === serial) busy(false);
    }
  };
  $("recommendApply").onclick = () => {
    if (!proposal || proposal.verification.status !== "completed") return;
    if (JSON.stringify(layout) !== snapshot) {
      state.className = "bad"; state.textContent = "当前图已发生变化，请重新生成推荐。";
      $("recommendApply").disabled = true; return;
    }
    accept(clone(proposal.simulation));
    proposal = null; $("recommendApply").disabled = true;
    state.className = "ok"; state.textContent = "已采用到本窗口参数；点击“保存参数”或导出后写入电路文件。";
  };
}
