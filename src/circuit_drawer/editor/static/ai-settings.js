/* One settings form for both editors; the server never returns the API key. */
(() => {
  const button = document.getElementById("aiSettings");
  const escape = value => String(value).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  async function request(body, token) {
    const response = await fetch("/api/ai-settings", body ? {
      method:"POST", headers:{"Content-Type":"application/json", "X-CircuitDrawer-Settings":token},
      body:JSON.stringify(body)
    } : {cache:"no-store"});
    const result = await response.json();
    if (!response.ok || result.error) throw new Error(result.error || "无法读取 AI 接口设置");
    return result;
  }
  button.addEventListener("click", async () => {
    if (document.getElementById("aiSettingsDialog")) return;
    const dialog = document.createElement("dialog");
    dialog.id = "aiSettingsDialog";
    dialog.setAttribute("aria-label", "AI 接口设置");
    dialog.style.cssText = "width:620px;max-width:90vw;max-height:85vh;overflow:auto;padding:22px;border:1px solid #cbd5e1;border-radius:12px";
    dialog.innerHTML = '<h3>AI 接口设置</h3><p role="status">正在读取配置…</p><button type="button">关闭</button>';
    dialog.querySelector("button").onclick = () => dialog.close();
    dialog.addEventListener("close", () => { dialog.querySelectorAll('input[type="password"]').forEach(e=>e.value=""); dialog.remove(); button.focus(); }, {once:true});
    document.body.append(dialog); dialog.showModal();
    try {
      const saved = await request();
      if (!dialog.isConnected) return;
      dialog.innerHTML = `<h3>AI 接口设置</h3>
        <p>与启动器共用配置。应用后供后续请求使用，正在运行的请求保持原设置。</p>
        <form><fieldset style="border:0;padding:0;display:grid;gap:12px">
          <label>接口类型<select name="provider" style="display:block;width:100%">${saved.providers.map(p=>`<option value="${escape(p)}">${p === "openai" ? "OpenAI 兼容接口" : "Anthropic 兼容接口"}</option>`).join("")}</select></label>
          <label>模型 ID<input name="model" style="display:block;width:100%;box-sizing:border-box" autocomplete="off" placeholder="填写服务商提供的模型 ID"></label>
          <label>接口地址<input name="base_url" style="display:block;width:100%;box-sizing:border-box" autocomplete="off" placeholder="服务商的 http(s) API 地址，留空用官方地址"></label>
          <label>API Key<select name="key_action" style="display:block;width:100%"><option value="keep">保留已配置密钥</option><option value="replace">替换密钥</option><option value="clear">清除密钥</option></select></label>
          <input name="key" type="password" autocomplete="new-password" aria-label="新 API Key" placeholder="只在替换密钥时填写；现有密钥不会回显">
          <label><input name="remember" type="checkbox" ${saved.can_remember ? "" : "disabled"}> 在当前 Windows 账户中加密记住 API Key</label>
          <details><summary>思考强度</summary>${[["plan_effort","先看结构"],["draw_effort","生成 / 修改"]].map(([name,label])=>`<label style="display:block;margin-top:8px">${label}<select name="${name}">${saved.efforts.map(e=>`<option value="${escape(e)}">${e === "default" ? "服务商默认（不额外指定）" : escape(e)}</option>`).join("")}</select></label>`).join("")}</details>
          <p style="margin:0;color:#64748b">不记住时密钥只在本次服务会话使用。更换接口类型或地址时请重新填写该接口的密钥。此处不改变 ngspice 路径。</p>
          <div><button type="submit">应用设置</button> <button type="button" id="aiSettingsClose">关闭</button></div>
        </fieldset></form><p id="aiSettingsState" role="status" aria-live="polite"></p>`;
      const form = dialog.querySelector("form"), fields = form.elements, state = dialog.querySelector("#aiSettingsState");
      for (const name of ["provider","model","base_url","plan_effort","draw_effort"]) fields[name].value = saved[name];
      fields.remember.checked = saved.can_remember && saved.remember;
      fields.key_action.value = saved.key_set ? "keep" : "replace";
      function keyMode() { fields.key.disabled = fields.key_action.value !== "replace"; fields.key.value = ""; }
      keyMode(); fields.key_action.onchange = keyMode;
      fields.provider.onchange = () => { fields.model.value=""; fields.base_url.value=""; fields.plan_effort.value=fields.draw_effort.value="default"; fields.key_action.value="replace"; keyMode(); };
      dialog.querySelector("#aiSettingsClose").onclick = () => dialog.close();
      form.onsubmit = async event => {
        event.preventDefault();
        const payload = Object.fromEntries(["provider","model","base_url","key_action","key","plan_effort","draw_effort"].map(name=>[name,fields[name].value]));
        payload.remember=fields.remember.checked; payload.revision=saved.revision;
        const fieldset = dialog.querySelector("fieldset"); fieldset.disabled=true;
        state.textContent="正在应用…";
        try {
          const result = await request(payload,saved.token);
          Object.assign(saved,result);
          for (const name of ["provider","model","base_url","plan_effort","draw_effort"]) fields[name].value=result[name];
          fields.remember.checked=result.can_remember && result.remember;
          fields.key_action.value=result.key_set ? "keep" : "replace"; keyMode();
          state.textContent="设置已应用，后续模型请求使用新设置。";
        } catch(e) { state.textContent=e.message; }
        finally { fieldset.disabled=false; }
      };
    } catch(e) { if (dialog.isConnected) dialog.querySelector('[role="status"]').textContent=e.message; }
  });
})();
