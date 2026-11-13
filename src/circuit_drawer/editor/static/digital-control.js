// Shared, draft-only settings for the circuit and control-diagram editors.
window.CircuitDrawerDigital = {
  create(control, inputs) {
    const original = JSON.parse(JSON.stringify(control));
    const cfg = original.digital || {};
    const box = document.createElement("div");
    box.className = "digital-control-settings";
    const fields = {};
    function field(parent, key, label, value, type = "text") {
      const wrap = document.createElement("label");
      wrap.style.cssText = "display:inline-flex;flex-direction:column;gap:4px;margin:4px;max-width:220px";
      const caption = document.createElement("span");
      caption.textContent = label;
      const input = document.createElement("input");
      input.type = type;
      input.dataset.digital = key;
      input.setAttribute("aria-label", label);
      if (type === "checkbox") input.checked = !!value;
      else input.value = value ?? "";
      wrap.append(caption, input);
      parent.append(wrap);
      fields[key] = input;
      return input;
    }
    const enabled = field(box, "enabled", "启用数字控制", !!original.digital, "checkbox");
    const settings = document.createElement("fieldset");
    settings.style.cssText = "display:flex;flex-wrap:wrap;gap:12px;margin-top:8px";
    box.append(settings);
    field(settings, "fs", "采样频率 (Hz)", original.code?.fs ?? 100000);
    field(settings, "delay_samples", "计算延迟（拍）", cfg.delay_samples ?? 0, "number");
    field(settings, "pwm_bits", "PWM 位数（留空为理想）", cfg.pwm_bits, "number");
    field(settings, "word_bits", "定点字长（留空为浮点）", cfg.word_bits, "number");
    field(settings, "fraction_bits", "定点小数位数", cfg.fraction_bits, "number");
    for (const id of inputs) {
      const row = document.createElement("fieldset");
      const legend = document.createElement("legend");
      legend.textContent = `ADC · ${id}`;
      row.append(legend);
      settings.append(row);
      const adc = cfg.adc?.[id] || {};
      field(row, `${id}.bits`, "ADC 位数（留空为理想）", adc.bits, "number");
      field(row, `${id}.min`, "ADC 下限", adc.min ?? 0);
      field(row, `${id}.max`, "ADC 上限", adc.max ?? 1);
    }
    const hint = document.createElement("p");
    hint.className = "hint";
    hint.textContent = "ADC 量程按绑定输入的物理数值填写。定点设置作用于方块输出和存储状态，字长支持 8–24 位。";
    box.append(hint);
    enabled.onchange = () => { settings.disabled = !enabled.checked; };
    enabled.onchange();
    const snapshot = () => JSON.stringify(Object.values(fields).map(f => f.type === "checkbox" ? f.checked : f.value));
    const initial = snapshot();
    const value = input => {
      const text = input.value.trim();
      return text !== "" && Number.isFinite(Number(text)) ? Number(text) : text;
    };
    return {
      element: box,
      changed: () => snapshot() !== initial,
      read() {
        const next = JSON.parse(JSON.stringify(original));
        if (snapshot() === initial) return next;
        if (!enabled.checked) { delete next.digital; return next; }
        next.code = {...next.code, fs: value(fields.fs)};
        const digital = {...cfg, delay_samples: value(fields.delay_samples)};
        for (const key of ["pwm_bits", "word_bits", "fraction_bits"]) {
          if (fields[key].value.trim() === "") delete digital[key];
          else digital[key] = value(fields[key]);
        }
        digital.adc = Object.create(null);
        for (const id of inputs) {
          if (fields[`${id}.bits`].value.trim() === "") continue;
          digital.adc[id] = Object.fromEntries(["bits", "min", "max"].map(k => [k, value(fields[`${id}.${k}`])]));
        }
        next.digital = digital;
        return next;
      }
    };
  }
};
