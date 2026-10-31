// Shared menu navigation and help; each editor owns its action handlers.
(() => {
  const header = document.querySelector("header");
  const triggers = [...header.querySelectorAll("[data-menu]")];
  const byId = id => document.getElementById(id);
  let current = null;
  const panel = trigger => byId(trigger.getAttribute("aria-controls"));
  const entries = trigger => [...panel(trigger).querySelectorAll("button, select, input")]
    .filter(e => !e.disabled && !e.closest("[hidden]"));
  const submenus = [...header.querySelectorAll("[data-submenu]")];
  function collapseSubmenus(restore = false) {
    const active = submenus.find(t => t.getAttribute("aria-expanded") === "true");
    submenus.forEach(t => { byId(t.dataset.submenu).hidden = true; t.setAttribute("aria-expanded", "false"); });
    if (restore) active?.focus();
    return !!active;
  }
  function close(restore = false) {
    if (!current) return;
    const old = current;
    collapseSubmenus();
    panel(old).hidden = true; old.setAttribute("aria-expanded", "false"); current = null;
    if (restore) old.focus();
  }
  function open(trigger, focus = false, last = false) {
    close(); current = trigger;
    document.dispatchEvent(new Event("editor-menu-open"));
    sync();
    const menu = panel(trigger); menu.hidden = false;
    trigger.setAttribute("aria-expanded", "true");
    const r = trigger.getBoundingClientRect();
    menu.style.left = Math.max(8, Math.min(r.left, innerWidth - menu.offsetWidth - 8)) + "px";
    menu.style.top = r.bottom + 5 + "px";
    menu.style.maxHeight = Math.max(80, innerHeight - r.bottom - 13) + "px";
    if (focus) { const items = entries(trigger); (last ? items.at(-1) : items[0])?.focus(); }
  }
  triggers.forEach(trigger => {
    trigger.onclick = () => current === trigger ? close() : open(trigger);
  });
  submenus.forEach(trigger => {
    trigger.onclick = () => {
      const show = byId(trigger.dataset.submenu).hidden;
      collapseSubmenus();
      byId(trigger.dataset.submenu).hidden = !show;
      trigger.setAttribute("aria-expanded", String(show));
    };
  });
  const mirrors = [...header.querySelectorAll("[data-command], [data-availability]")];
  function sync() {
    mirrors.forEach(item => {
      const source = byId(item.dataset.command || item.dataset.availability);
      item.hidden = source.hidden || source.style.display === "none";
      item.disabled = source.disabled;
      if (item.dataset.command) item.classList.toggle("on", source.classList.contains("on"));
    });
  }
  const observer = new MutationObserver(sync);
  new Set(mirrors.map(item => byId(item.dataset.command || item.dataset.availability)))
    .forEach(source => observer.observe(source, {attributes: true, attributeFilter: ["hidden", "disabled", "class", "style"]}));
  sync();
  header.addEventListener("click", ev => {
    const item = ev.target.closest('[role="menuitem"]');
    if (item && !item.disabled && !item.dataset.menu && !item.dataset.submenu) close(true);
  }, true);
  header.querySelectorAll("[data-command]").forEach(item => {
    item.onclick = () => byId(item.dataset.command).click();
  });
  document.addEventListener("pointerdown", ev => { if (!header.contains(ev.target)) close(); });
  document.addEventListener("focusin", ev => { if (!header.contains(ev.target)) close(); });
  window.addEventListener("resize", () => close());
  window.addEventListener("blur", () => close());
  // Capture before the editors' canvas shortcuts, including the module projection guard.
  window.addEventListener("keydown", ev => {
    if (document.querySelector("dialog.menu-help[open]")) { ev.stopImmediatePropagation(); return; }
    const trigger = ev.target.closest("[data-menu]");
    if (!current && !trigger) return;
    const nativeField = /SELECT|INPUT/.test(ev.target.tagName);
    if (ev.key === "Escape" && current) { ev.preventDefault(); ev.stopImmediatePropagation(); if (!collapseSubmenus(true)) close(true); return; }
    if (ev.key === "Tab") { close(true); return; }
    if (nativeField) { if (!ev.ctrlKey && !ev.metaKey) ev.stopImmediatePropagation(); return; }
    if (ev.key === "ArrowRight" && ev.target.dataset.submenu && !ev.target.disabled) {
      ev.preventDefault(); ev.stopImmediatePropagation();
      if (byId(ev.target.dataset.submenu).hidden) ev.target.click();
      byId(ev.target.dataset.submenu).querySelector("button:not(:disabled),select:not(:disabled)")?.focus(); return;
    }
    if (ev.key === "ArrowLeft" && ev.target.closest(".menu-subpanel")) {
      ev.preventDefault(); ev.stopImmediatePropagation(); collapseSubmenus(true); return;
    }
    if (["ArrowLeft", "ArrowRight"].includes(ev.key)) {
      ev.preventDefault(); ev.stopImmediatePropagation();
      const index = triggers.indexOf(trigger || current), d = ev.key === "ArrowRight" ? 1 : -1;
      const next = triggers[(index + d + triggers.length) % triggers.length];
      next.focus(); if (current) open(next, true); return;
    }
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(ev.key)) {
      ev.preventDefault(); ev.stopImmediatePropagation();
      if (trigger) { open(trigger, true, ev.key === "ArrowUp" || ev.key === "End"); return; }
      const items = entries(current), index = items.indexOf(document.activeElement);
      const next = ev.key === "Home" ? 0 : ev.key === "End" ? items.length - 1 :
        (index + (ev.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      items[next]?.focus(); return;
    }
    if (ev.key === "Enter" || ev.key === " ") {
      ev.preventDefault(); ev.stopImmediatePropagation(); ev.target.click(); return;
    }
    // Existing application shortcuts still run, after dismissing the menu.
    close();
  }, true);
  byId("menuHelpOpen").onclick = () => byId("menuHelp").showModal();
  byId("menuHelpClose").onclick = () => byId("menuHelp").close();
  const project = "https://github.com/ahainur/circuit-drawer-for-ee";
  header.querySelectorAll("[data-guide]").forEach(button => {
    button.onclick = () => window.open(project + "/blob/main/docs/" + button.dataset.guide, "_blank", "noopener");
  });
  const about = document.createElement("dialog");
  about.id = "menuAbout"; about.className = "menu-help"; about.setAttribute("aria-label", "关于 Circuit Drawer");
  about.innerHTML = `<h3>Circuit Drawer for EE</h3><p>电路与控制框图的绘制、校验、导出和 AI 辅助编辑工具。</p>
    <p><a href="${project}" target="_blank" rel="noopener">项目主页与版本说明</a></p><button>关闭</button>`;
  about.querySelector("button").onclick = () => about.close(); document.body.appendChild(about);
  byId("menuAboutOpen").onclick = () => about.showModal();
})();
