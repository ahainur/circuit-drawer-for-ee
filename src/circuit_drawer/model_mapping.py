"""Explicit global simulation mapping; drawing symbols never change."""

SCHEMA = {
    "switch": {"ideal_switch": {}, "mosfet": {"vto": "3", "kp": "10", "gate_voltage": "10"},
               "igbt": {"vce0": "1.5"}},
    "R": {"ideal": {}, "parasitic": {"series_inductance": "10n", "parallel_capacitance": "1p"}},
    "L": {"ideal": {}, "parasitic": {"series_resistance": "0.1", "parallel_capacitance": "1p"}},
    "C": {"ideal": {}, "parasitic": {"series_resistance": "0.01", "series_inductance": "1n"}},
}


def problems(layout):
    from .spice import number
    mapping = layout.extra.get("model_mapping", {})
    if not isinstance(mapping, dict) or set(mapping) - SCHEMA.keys():
        yield "bad-model-mapping", "model_mapping 允许类别 switch、R、L、C"
        return
    for kind, cfg in mapping.items():
        try:
            if not isinstance(cfg, dict) or not isinstance(cfg.get("model"), str) or cfg["model"] not in SCHEMA[kind]:
                raise ValueError(f"model 允许 {', '.join(SCHEMA[kind])}")
            fields = SCHEMA[kind][cfg["model"]]
            if set(cfg) != {"model", *fields}:
                raise ValueError(f"字段必须为 model, {', '.join(fields)}")
            for key in fields:
                if number(cfg[key], key) <= 0:
                    raise ValueError(f"{key} 必须大于 0")
            if cfg["model"] == "mosfet" and number(cfg["gate_voltage"], "gate_voltage") <= number(cfg["vto"], "vto"):
                raise ValueError("gate_voltage 必须大于 vto")
            if kind == "switch" and cfg["model"] == "igbt":
                pmos = [e.id for e in layout.elements if e.kind == "pmos"]
                if pmos:
                    raise ValueError(f"{', '.join(pmos)}：P 沟 MOSFET 不能映射为单向 IGBT；请选择 ideal_switch 或 mosfet")
            if kind == "L" and cfg["model"] == "parasitic":
                simulation = layout.extra.get("simulation", {})
                components = simulation.get("components", {}) if isinstance(simulation, dict) else {}
                for element in layout.elements:
                    component = components.get(element.id, {}) if isinstance(components, dict) else {}
                    if (element.kind == "L" and isinstance(component, dict)
                            and number(component.get("series_resistance", 0), "series_resistance") > 0):
                        raise ValueError(f"{element.id}: 器件 series_resistance 与全图 L 寄生模型不能同时启用；请选择一种串联电阻设置")
        except (ValueError, TypeError) as exc:
            yield "bad-model-mapping", f"model_mapping.{kind}: {exc}"


def passive_lines(kind, cfg, name, a, b, value, initial):
    from .spice import number
    s = lambda key: f"{number(cfg[key], key):.15g}"
    internal = f"cd_{name}_par"
    if kind == "R":
        return [f"Lpar_{name} {a} {internal} {s('series_inductance')}",
                f"{name} {internal} {b} {value}",
                f"Cpar_{name} {a} {b} {s('parallel_capacitance')}"]
    if kind == "L":
        return [f"Rpar_{name} {a} {internal} {s('series_resistance')}",
                f"{name} {internal} {b} {value} IC={initial}",
                f"Cpar_{name} {a} {b} {s('parallel_capacitance')}"]
    return [f"Rpar_{name} {a} {internal} {s('series_resistance')}",
            f"Lpar_{name} {internal} {internal}2 {s('series_inductance')}",
            f"{name} {internal}2 {b} {value} IC={initial}"]


def switch_lines(cfg, e, name, positive, negative, val, body="IS=1e-9 N=1 RS=0.01"):
    """Common sensed input permits current probes regardless of mapped model."""
    from .spice import number
    s = lambda key: f"{val[key]:.15g}"
    channel = f"cd_{name}_channel"
    out = [f"Vsense_{name} {positive} {channel} 0"]
    if cfg["model"] == "ideal_switch":
        out += [f"Smap_{name} {channel} {negative} ctl_{name} 0 ms_{name}",
                f".model ms_{name} SW(RON={s('ron')} ROFF={s('roff')} VT=0.5 VH=0)"]
    elif cfg["model"] == "mosfet":
        gate = f"cd_{name}_gate"
        polarity = -1 if e.kind == "pmos" else 1
        model = "PMOS" if e.kind == "pmos" else "NMOS"
        out += [f"Egate_{name} {gate} {negative} ctl_{name} 0 {polarity * number(cfg['gate_voltage'], 'gate_voltage'):.15g}",
                f"Mmap_{name} {channel} {gate} {negative} {negative} mm_{name}",
                f".model mm_{name} {model}(LEVEL=1 VTO={polarity * number(cfg['vto'], 'vto'):.15g} KP={number(cfg['kp'], 'kp'):.15g})"]
    else:
        vce = f"v({channel},{negative})"
        out += [f"Bchannel_{name} {channel} {negative} I = {vce}/{s('roff')} + "
                f"(v(ctl_{name}) > 0.5 ? max({vce}-{number(cfg['vce0'], 'vce0'):.15g},0)/{s('ron')} : 0)"]
    if e.opts.get("diode", True) is not False:
        # Match the terminal-current convention: all parallel branches sit
        # behind Vsense, including the snubber appended by the shared exporter.
        anode, cathode = (channel, negative) if e.kind == "pmos" else (negative, channel)
        out += [f"Dbody_{name} {anode} {cathode} mbody_{name}",
                f".model mbody_{name} D({body})"]
    return out
