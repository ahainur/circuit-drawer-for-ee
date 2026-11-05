// Interface text only. Diagram content, form values and simulator output stay original.
(() => {
  const key = 'circuit-drawer-language', languages = ['zh-CN', 'zh-TW', 'en'];
  const entries = window.CircuitDrawerMessages || [], exact = new Map(), patterns = [];
  const normalize = text => text.replace(/\s+/g, ' ').trim();
  const escape = text => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  for (const [source, english, traditional] of entries) {
    const translations = {'zh-CN':source, en:english, 'zh-TW':traditional};
    exact.set(source, translations);
    if (/\{\d+\}/.test(source)) {
      const slots = [];
      const expression = source.split(/(\{\d+\})/).map(part => {
        if (/^\{\d+\}$/.test(part)) { slots.push(part); return '(.*?)'; }
        return escape(part);
      }).join('');
      patterns.push({regex:new RegExp('^' + expression + '$'), slots, translations,
        specificity:source.replace(/\{\d+\}/g, '').length});
    }
  }
  patterns.sort((a,b) => b.specificity - a.specificity);
  let language = 'zh-CN', selector;
  try { const saved = localStorage.getItem(key); if (languages.includes(saved)) language = saved; } catch {}
  const originals = new WeakMap(), attributes = new WeakMap();
  const raw = 'script,style,textarea,svg,code,pre,[translate="no"],[contenteditable="true"]';
  const attributeNames = ['title','placeholder','aria-label','aria-description','alt'];

  function translate(text) {
    if (typeof text !== 'string' || language === 'zh-CN') return text;
    const source = normalize(text), direct = exact.get(source);
    let result = direct?.[language];
    if (result === undefined && /[\u3400-\u9fff]/.test(source)) {
      for (const {regex,slots,translations} of patterns) {
        const match = regex.exec(source);
        if (!match) continue;
        const values = new Map(slots.map((slot,i)=>[slot,match[i+1]]));
        result = translations[language].replace(/\{\d+\}/g,slot=>values.get(slot) ?? slot);
        break;
      }
    }
    if (result === undefined) return text;
    return text.match(/^\s*/)[0] + result + text.match(/\s*$/)[0];
  }

  function sourceText(node) {
    if (node.nodeType === Node.TEXT_NODE) {
      const stored = originals.get(node);
      return stored && stored.display === node.data ? stored.source : node.data;
    }
    return [...node.childNodes].map(sourceText).join('');
  }

  function textNode(node) {
    if (!node.parentElement || node.parentElement.closest(raw)) return;
    const before = node.data, old = originals.get(node);
    const source = old && before === old.display ? old.source : before;
    const after = translate(source);
    // An option without a value derives it from its label; preserve the original value.
    const option = node.parentElement.closest('option');
    if (option && !option.hasAttribute('value') && before !== after) option.value = option.textContent;
    originals.set(node, {source, display:after});
    if (after !== before) node.data = after;
  }

  function element(node) {
    if (node.closest(raw)) return;
    let saved = attributes.get(node);
    if (!saved) { saved = new Map(); attributes.set(node,saved); }
    for (const name of attributeNames) {
      const before = node.getAttribute(name);
      if (before === null) { saved.delete(name); continue; }
      const old = saved.get(name), source = old && old.display === before ? old.source : before;
      const after = translate(source); saved.set(name,{source,display:after});
      if (after !== before) node.setAttribute(name,after);
    }
  }

  function localize(root) {
    if (root.nodeType === Node.TEXT_NODE) { textNode(root); return; }
    if (root.nodeType !== Node.ELEMENT_NODE || root.closest(raw)) return;
    element(root);
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
      acceptNode: node => node.nodeType === Node.ELEMENT_NODE && node.matches(raw)
        ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT
    });
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (node.nodeType === Node.TEXT_NODE) textNode(node); else element(node);
    }
  }

  function choose(value, persist = true) {
    if (!languages.includes(value)) return;
    language = value; document.documentElement.lang = language;
    if (selector) selector.value = language;
    if (persist) { try { localStorage.setItem(key,language); } catch {} }
    localize(document.body); localize(document.querySelector('title'));
    document.dispatchEvent(new Event('editor-language-change'));
  }

  function start() {
    const initialize = () => {
      const label = document.createElement('label'); label.className = 'language-picker';
      label.setAttribute('translate','no');
      label.innerHTML = '<span aria-hidden="true">文 / A</span><select id="uiLanguage" aria-label="界面语言 / Language">' +
        '<option value="zh-CN">简体中文</option><option value="zh-TW">繁體中文</option><option value="en">English</option></select>';
      document.querySelector('.menu-row').append(label); selector = label.querySelector('select');
      selector.addEventListener('change',()=>choose(selector.value));
      choose(language,false);
      new MutationObserver(records => {
        const targets = new Set();
        for (const record of records) {
          if (record.type === 'childList') record.addedNodes.forEach(node=>targets.add(node));
          else targets.add(record.target);
        }
        for (const node of targets) if (node.isConnected) {
          if (node.nodeType === Node.ELEMENT_NODE && node.closest(raw)) continue;
          if (node.nodeType === Node.ELEMENT_NODE && !node.childNodes.length) element(node);
          else localize(node);
        }
      }).observe(document.body, {subtree:true, childList:true, characterData:true,
        attributes:true, attributeFilter:attributeNames});
      window.addEventListener('storage',event=>{ if (event.key === key) choose(event.newValue || 'zh-CN',false); });
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded',initialize,{once:true});
    else initialize();
  }
  window.cdI18n = {translate,sourceText,start,
    alert:message=>window.alert(translate(message)),
    confirm:message=>window.confirm(translate(message)),
    prompt:(message,value)=>window.prompt(translate(message),value)};
})();
