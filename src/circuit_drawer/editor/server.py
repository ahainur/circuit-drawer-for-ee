"""Local web editor: drag parts on a coordinate grid, validate live, export.

Runs entirely on the user's machine with the standard library.  The page
edits the same JSON coordinate table the CLI uses; every change is checked by
the same validator, and exports never carry the grid.
"""

from __future__ import annotations

import base64
import json
import math
import os
import re
import shutil
import string
import threading
import time
import uuid
import tempfile
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ..ai.settings import CONFIG_LOCK, SettingsRoutes, WebSettings
from pathlib import Path

from .. import templates
from ..compile import FORMATS, CompileError, build
from ..emit import emit
from ..geometry import annotation_box, footprint, unscaled
from ..layout import SWITCHES, TWO_TERMINAL, Layout
from ..modes import ModeError
from ..modules import ModuleError
from ..profiles import PROFILES, get_profile
from ..validate import check

STATIC = Path(__file__).with_name("static")
# Repeated full routing is expensive; bound the optional resize diagnostic.
MAX_RESIZE_LIMIT_CHECKS = 200


def _box(b, s) -> list[float]:
    (x0, y0), (x1, y1) = unscaled((b.x0, b.y0), s), unscaled((b.x1, b.y1), s)
    return [round(v, 4) for v in (x0, y0, x1, y1)]


def _pt(p, s) -> list[float]:
    return [round(v, 4) for v in unscaled(p, s)]


def _callout_geometry(c, s, prof) -> dict:
    from ..geometry import (callout_curve, callout_handle, callout_heads,
                            callout_label_box, callout_shape, callout_visible_paths, isolation_bars)
    from ..regions import callout_rgb
    lab = callout_label_box(c, s, prof)
    out = {"shape": [_pt(p, s) for p in callout_shape(c, s)],
           "fill": "#%02x%02x%02x" % callout_rgb(c, edge=False),
           "edge": "#%02x%02x%02x" % callout_rgb(c), "style": c.style,
           "label": _box(lab, s) if lab else None,
           "mono_fill": "#%02x%02x%02x" % callout_rgb(c, True, edge=False),
           "mono_edge": "#%02x%02x%02x" % callout_rgb(c, True),
           "border": c.border_color != "none", "fill_opacity": c.fill_opacity,
           "foreground": c.foreground,
           "border_width": c.border_width * 2.54 / 72.27 / s.x if c.border_width is not None else None}
    if c.style != "block":
        out.update(paths=[[_pt(p, s) for p in curve] for curve in callout_visible_paths(c, s, prof)],
                   heads=[[_pt(p, s) for p in head] for head in callout_heads(c, s)],
                   bars=[[_pt(a, s), _pt(b, s)] for a, b in isolation_bars(c, s)], dash=c.dash)
    if c.style == "line" and not c.via:
        out.update(curve=[_pt(p, s) for p in callout_curve(c, s)],
                   handle=_pt(callout_handle(c, s), s))
    return out


def callout_preview(req: dict) -> dict:
    """Cheap geometry while dragging; it neither saves nor validates a table.

    The normal check still runs on release and on every export. Keep the
    printed scale fixed during the gesture, avoiding a bend/scale feedback loop.
    """
    from dataclasses import replace
    from ..geometry import Scale, callout_bend_at
    from ..regions import _point, parse_callout
    scale = _point(req["scale"], "scale")
    if any(v <= 0 or not math.isfinite(v) for v in scale):
        raise ValueError("scale must contain positive finite numbers")
    s = Scale(*scale)
    c = parse_callout(req["callout"], 0)
    if "handle" in req:
        if c.style != "line" or c.via:
            raise ValueError("only a line arrow has a bend handle")
        c = replace(c, bend=callout_bend_at(c, s, _point(req["handle"], "handle")))
    return {"bend": c.bend,
            "geometry": _callout_geometry(c, s, get_profile(req["profile"]))}


def module_devices(layout: Layout, profile: str) -> dict | None:
    """A module table's whole circuit for the SPICE dialog: every device with
    the name the netlist uses (the pre-encapsulation name where the table
    keeps one), the instance it belongs to and its effective parameters,
    plus every pin's net. None when the table does not assemble."""
    from ..modules import assemble
    source = Layout.from_dict(layout.to_dict())
    sim = source.extra.setdefault("simulation", {})
    if not isinstance(sim, dict):
        return None
    sim.setdefault("components", {})       # the assembly then fills in instance values
    try:
        full = assemble(source, None, get_profile(profile)).full
    except (ValueError, TypeError, KeyError):
        return None
    aliases = layout.extra.get("module_authoring", {}).get("aliases", {})
    instances = {i.get("id") for i in layout.extra["modules"].get("instances", []) if isinstance(i, dict)}
    values = full.extra.get("simulation", {}).get("components", {})
    owner = lambda eid: eid.split("/")[0] if "/" in eid and eid.split("/")[0] in instances else None
    # top-level devices (drawn or placed by modules.parts) have no owner
    devices = [{"id": e.id, "name": aliases.get(e.id, e.id), "kind": e.kind, "pins": list(e.pins),
                "instance": owner(e.id), "values": values.get(e.id, {})}
               for e in full.elements if e.kind != "port"]
    names = {d["id"]: d["name"] for d in devices}
    pins = {}
    for ref, net in full.netlist.items():
        eid, _, pin = ref.rpartition(".")
        if eid in names:
            pins[f"{names[eid]}.{pin}"] = net
    return {"devices": devices, "pins": pins}


def analyse(layout: Layout, profile: str) -> dict:
    """Validation report plus every footprint, in layout units, so the page
    draws exactly the boxes the validator checks."""
    prof = get_profile(profile)
    electrical = layout.extra.get("electrical")
    from ..validate import check_with_assembly
    rep, assembly = check_with_assembly(layout, prof)
    # Offer a wider page only after the same source passes the normal check.
    passes_double = (not rep.ok and prof.name == "ieee-single"
                     and not any(i.code == "empty-drawing" for i in rep.issues)
                     and check(layout, get_profile("double")).ok)
    module_data = None
    if "modules" in layout.extra:
        from ..modules import assemble
        try:
            # the check's own assembly is drawn at the report's scale; build one
            # only when the check could not (it then reports why)
            assembly = assembly or assemble(layout, rep.scale, prof)
            # The editor edits the source: tell it which drawn wires are the
            # source's own (view index -> source index), which draw a link,
            # where every instance port is, and which parts are placed for it.
            unused = list(range(len(layout.wires)))
            root_wires = {}
            for j, w in enumerate(assembly.view.wires):
                k = next((k for k in unused if [list(p) for p in layout.wires[k]] == [list(p) for p in w]), None)
                if k is not None:
                    unused.remove(k); root_wires[j] = k
            root_decorations = {}
            source_table, view_table = layout.to_dict(), assembly.view.to_dict()
            for section in ("regions", "blocks", "callouts", "annotations"):
                source = source_table.get(section, [])
                unused = list(range(len(source)))
                mapping = {}
                # the expanded view drops a group region's derived "stack": compare without it
                bare = lambda d: {k: v for k, v in d.items() if k != "stack"} if isinstance(d, dict) else d
                same = lambda a, b: bare(a) == bare(b)
                for j, item in enumerate(view_table.get(section, [])):
                    k = next((k for k in unused if source[k] == item or same(source[k], item)), None)
                    if k is not None:
                        unused.remove(k)
                        mapping[j] = k
                root_decorations[section] = mapping
            module_data = {"root_decorations": root_decorations, "view": assembly.view.to_dict(), "instances": assembly.instances,
                           "hidden": assembly.hidden, "collapsed": assembly.collapsed,
                           "frames": assembly.frames, "linked": "links" in layout.extra["modules"],
                           "partial": assembly.partial,
                           "element_count": sum(e.kind != "port" for e in assembly.full.elements),
                           "link_wires": assembly.link_wires, "ports": assembly.ports,
                           "root_wires": root_wires, "root_wire_count": len(layout.wires),
                           "unrouted": assembly.unrouted, "origins": assembly.origins,
                           "instance_wires": assembly.instance_wires,
                           "auto_parts": [p.get("id") for p in layout.extra["modules"].get("parts", [])
                                          if isinstance(p, dict)]}
            layout = assembly.view
        except (ValueError, TypeError, KeyError):
            pass  # check() already reports the error; keep the source editable.
    s = rep.scale
    geo: dict[str, dict] = {}
    for el in layout.elements:
        try:
            fp = footprint(el, s, prof)
        except (KeyError, ValueError):
            continue
        g = {"bodies": [_box(b, s) for b in fp.bodies],
             "labels": [_box(b, s) for b in fp.labels],
             "label_offset": fp.extra.get("label_offset", [0, 0]),
             "side": fp.label_side}
        if "source_wave" in fp.extra:
            g["source_wave"] = [_pt(p, s) for p in fp.extra["source_wave"]["points"]]
        if "current_arrow" in fp.extra:
            g["current_arrow"] = {k: [_pt(p, s) for p in pts]
                                  for k, pts in fp.extra["current_arrow"].items()}
        if fp.centre:
            g["centre"] = _pt(fp.centre, s)
        if el.kind in SWITCHES and "u" in fp.extra:
            from ..geometry import PMOS_DOT_RADIUS, SW_HALF_H, SW_LEFT, SW_RIGHT, axis_sizes, sw_k
            length, width = axis_sizes(el)
            u, kl, kw = fp.extra["u"], sw_k(prof) * length, sw_k(prof) * width
            along, across = (s.y, s.x) if u[0] == 0 else (s.x, s.y)
            # k: symbol size in layout units across and along the pins, so the
            # page never re-derives it from the box and the geometry constants
            g["orient"] = {"u": list(u), "mirror": bool(el.opts.get("mirror")),
                           "box": [round(v, 4) for v in (-SW_LEFT * kw / across, -SW_HALF_H * kl / along,
                                                         SW_RIGHT * kw / across, SW_HALF_H * kl / along)],
                           "k": [round(kw / across, 6), round(kl / along, 6)]}
            if el.kind == "pmos":
                radius = PMOS_DOT_RADIUS * prof.bipole_len
                g["orient"]["gate_dot"] = [radius * width / length / across, radius / along]
        if "diag" in fp.extra:
            d = fp.extra["diag"]           # square scale: one factor for both axes
            g["diag"] = {"u": [round(v, 6) for v in d["u"]],
                         "half_len": round(d["half_len"] / s.x, 4),
                         "half_h": round(d["half_h"] / s.x, 4)}
        if "gate" in fp.extra:
            g["gate"] = [_pt(p, s) for p in fp.extra["gate"]]   # a thyristor's gate lead
        if "zener_hook" in fp.extra:
            g["zener_hook"] = fp.extra["zener_hook"]
        if el.kind == "transformer":
            ex = fp.extra
            g["coils"] = [[_pt(a, s), _pt(b, s)] for a, b in ex["coils"]]
            g["dots"] = [_pt(d, s) for d in ex.get("dots", [])]
            g["core_lines"] = [[_pt(a, s), _pt(b, s)] for a, b in ex["core_lines"]]
        if el.kind == "pmsm" and "motor_marks" in fp.extra:
            g["motor_leads"] = [[_pt(a, s), _pt(b, s)] for a, b in fp.leads]
            g["motor_marks"] = [{"text": text, "at": _pt(at, s), "height": height / s.y}
                                for text, at, height in fp.extra["motor_marks"]]
        geo[el.id] = g
    def ann_box(a):
        try:
            return _box(annotation_box(a, s, prof), s)
        except KeyError:
            return None  # check() reports the bad side; the page falls back to at
    ann = [ann_box(a) for a in layout.annotations]
    out = rep.to_dict()
    out["passes_double"] = passes_double
    if module_data is not None:
        out["module_info"] = module_data
    out.update(_region_geometry(layout, s, prof))
    from ..source_waves import WAVES, POSITIONS, SCALE_RANGE, symbols
    from ..geometry import scaled, wave_symbol
    from ..regions import ink_rgb
    marks = []
    for w in symbols(layout):
        if w is None:
            marks.append(None)
            continue
        pts, box = wave_symbol(scaled(w["at"], s), w["wave"], w["scale"])
        mark = {"points": [_pt(p, s) for p in pts], "box": _box(box, s),
                "color": "#%02x%02x%02x" % ink_rgb(w["color"]), "on_wire": w["on_wire"]}
        if w["on_wire"]:
            # the patch the exporter paints under the mark, in the colour behind it
            from ..blocks import safe_blocks
            from ..emit import KNOCKOUT_PAD, backdrop
            from ..geometry import Box
            from ..regions import safe_regions
            mark["knockout"] = _box(Box(box.x0 - KNOCKOUT_PAD, box.y0 - KNOCKOUT_PAD,
                                        box.x1 + KNOCKOUT_PAD, box.y1 + KNOCKOUT_PAD), s)
            mark["backdrop"] = "#%02x%02x%02x" % backdrop(w["at"], safe_regions(layout, s, prof)[0],
                                                          safe_blocks(layout), False)
        marks.append(mark)
    out.update(wave_symbols=marks, source_wave_options={"waves": WAVES, "positions": POSITIONS,
                                                       "scale_range": SCALE_RANGE})
    if layout.modes and rep.nets:
        from ..modes import check_modes
        out["modes"] = check_modes(layout, rep)
    from ..electrical import display, reconcile
    if electrical is not None and not any(i.code == "bad-electrical" for i in rep.issues):
        out["electrical"] = electrical
        out["electrical_view"] = display(layout, electrical)
    else:
        out["electrical_view"] = display(layout, reconcile(Layout.from_dict({**layout.to_dict(), "electrical": {}}), rep.nets))
    from ..geometry import no_connect_mark, electrical_labels
    out["electrical_labels"] = [{"kind": label.kind, "ref": label.ref, "text": label.text,
                                 "at": _pt(label.centre, s), "box": _box(label.box, s),
                                 "font_cm": label.font_pt * 2.54 / 72.27,
                                 "scale": [s.x, s.y]}
                                for label in electrical_labels(layout, s, prof, electrical)]
    nc = layout.extra.get("no_connect", [])
    out["no_connect"] = [no_connect_mark(p, s) for e in layout.elements for k, p in e.pins.items()
                         if isinstance(nc, list) and f"{e.id}.{k}" in nc]
    out["wire_nets"] = rep.wire_nets
    from ..spice import CURRENT_PROBE_KINDS, GATED, MAX_PROBES
    from ..pmsm import QUANTITIES
    out["probe_kinds"] = {"current": CURRENT_PROBE_KINDS, "drive": GATED}
    out["motor_quantities"] = QUANTITIES
    out["probe_limit"] = MAX_PROBES
    out.update(geometry=geo, annotations=ann, nets=rep.nets,
               junctions=[list(j) for j in rep.junctions],
               width_cm=round(prof.width_cm, 2))
    return out


def _region_geometry(layout: Layout, s, prof) -> dict:
    """Colours and label boxes of every region and callout, by index (None
    for an entry that fails its schema), so the page draws what the emitter
    prints without its own palette or text metrics."""
    from ..geometry import region_fit_box, region_label_box
    from ..regions import RegionError, css_rgb, parse_callout, parse_region
    regions, callouts = [], []
    for key, parse, dst in (("regions", parse_region, regions),
                            ("callouts", parse_callout, callouts)):
        items = layout.extra.get(key)
        for i, d in enumerate(items if isinstance(items, list) else []):
            try:
                x = parse(d, i, layout, s, prof) if key == "regions" else parse(d, i)
            except RegionError:
                dst.append(None)
                continue
            if key == "regions":
                lab = region_label_box(x, s, prof)
                fit = None
                try:
                    b = region_fit_box(layout, d.get("members"), s, prof)
                    fit = [b.x0, b.y0, b.x1, b.y1]
                except RegionError:
                    pass
                dst.append({"rects": [list(x.rect(k)) for k in range(len(x.layers) + 1)],
                            "fit": fit, "fills": [css_rgb(x.fill) if x.fill != "none" else None]
                            + [css_rgb(f) for _, _, f in x.layers],
                            "edges": [css_rgb(x.border_color or (x.fill if x.fill != "none" else "gray"),
                                              edge=True)] + [css_rgb(f, edge=True) for _, _, f in x.layers],
                            "mono_fills": [css_rgb(x.fill, True) if x.fill != "none" else None]
                            + [css_rgb(f, True) for _, _, f in x.layers],
                            "mono_edges": [css_rgb(x.border_color or (x.fill if x.fill != "none" else "gray"),
                                                   True, edge=True)] + [css_rgb(f, True, edge=True) for _, _, f in x.layers],
                            "label": _box(lab, s) if lab else None})
            else:
                dst.append(_callout_geometry(x, s, prof))
    from .. import blocks as block_defs
    from ..geometry import block_text_box
    from ..regions import ink_rgb as block_ink
    block_info = []
    raw_blocks = layout.extra.get("blocks")
    for i, d in enumerate(raw_blocks if isinstance(raw_blocks, list) else []):
        try:
            b = block_defs.parse_block(d, i)
        except RegionError:
            block_info.append(None)
            continue
        fill = block_defs.fill_rgb(b)
        text = block_text_box(b, s, prof)
        block_info.append({"rect": [b.x0, b.y0, b.x1, b.y1],
                           "fill": "#%02x%02x%02x" % fill if fill else None,
                           "edge": "#%02x%02x%02x" % block_ink(b.color), "border": b.border,
                           "text": _box(text, s) if text else None})
    from ..regions import (BORDERS, CALLOUT_LABEL_POS, FILLS, INKS, PALETTE, REGION_LABEL_POS,
                           CALLOUT_DASHES, CALLOUT_HEADS, CALLOUT_STYLES, CALLOUT_BORDER_WIDTH,
                           MAX_BEND, MAX_WAVE_CYCLES, STACK_MAX, WAVE_AMPLITUDE,
                           WAVE_CYCLES, ink_rgb, valid_ink)
    return {"regions": regions, "callouts": callouts, "blocks": block_info,
            "block_options": {"fills": list(block_defs.FILLS), "borders": list(block_defs.BORDERS)},
            "region_options": {"fills": list(FILLS), "borders": list(BORDERS),
                               "palette": {k: css_rgb(k) for k in PALETTE},
                               "label_pos": list(REGION_LABEL_POS),
                               "callout_label_pos": list(CALLOUT_LABEL_POS),
                               "callout_dashes": list(CALLOUT_DASHES), "callout_heads": list(CALLOUT_HEADS),
                               "callout_styles": list(CALLOUT_STYLES),
                               "callout_border_width": list(CALLOUT_BORDER_WIDTH),
                               "stack_max": STACK_MAX,
                               "max_bend": MAX_BEND,
                               "wave_amplitude": WAVE_AMPLITUDE, "wave_cycles": WAVE_CYCLES,
                               "max_wave_cycles": MAX_WAVE_CYCLES,
                               "inks": {k: v for k, v in INKS.items()}},
            # resolved ink of every coloured part and text, aliases and all
            "inks": {"elements": {e.id: "#%02x%02x%02x" % ink_rgb(e.opts["color"])
                                  for e in layout.elements
                                  if e.opts.get("color") and valid_ink(e.opts["color"])},
                     "annotations": ["#%02x%02x%02x" % ink_rgb(a.color)
                                     if a.color and valid_ink(a.color) else None
                                     for a in layout.annotations]}}


def preview_png(layout: Layout, profile: str, grid: bool, mode: int | None = None,
                style: dict | None = None, mono: bool = False) -> str:
    prof = get_profile(profile)
    rep = check(layout, prof)
    paint = None
    if mode is not None:
        from ..modes import Paint, WireGraph, modes_of, solve
        st = _style(style)
        graph = WireGraph(layout)
        lit = solve(layout, modes_of(layout)[mode], rep, graph, prof, st.arrows)
        paint = Paint(layout, lit, graph, prof, st)
    tex = emit(layout, prof, rep, grid=grid, paint=paint, mono=mono)
    with tempfile.TemporaryDirectory() as tmp:
        files = build(tex, Path(tmp), "preview", ("png",), png_dpi=300)
        return base64.b64encode(files["png"].read_bytes()).decode()


def _style(req: dict | None):
    from ..modes import Style
    req = req or {}
    st = Style.mono() if req.get("mono") else Style()
    if req.get("color"):
        st.color = str(req["color"])
    if req.get("off") in ("grey", "dashed"):
        st.off = req["off"]
    if req.get("arrows") is not None:
        st.arrows = int(req["arrows"])
    return st


def _round(a) -> dict:
    return {"round": a.round, "errors": a.errors, "note": a.note, "fixes": a.fixes}


IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


QUICK_ROUTE_BUDGET = 2500   # grid states per link while trying candidate box sizes


def module_resize_limit(layout: Layout, profile: str, iid: str, step: float) -> dict:
    """First valid grid size along the current aspect ratio, without saving.

    Routing is not monotonic in size, so check every smaller candidate rather
    than binary-searching a nominal symbol minimum. Only called on rejection.
    """
    from ..modules import MIN_BOX, ModuleError, assemble, route_budget
    from ..module_authoring import _assembles, _move_port_wires, new_edit_errors
    prof = get_profile(profile)
    if type(step) not in (int, float) or not math.isfinite(step) or not .01 <= step <= 1:
        raise ValueError("缩放网格须在 0.01–1 之间")
    rep = _assembles(layout, profile)
    assembly = assemble(layout, rep.scale, prof)
    region = next((r for r in assembly.view.extra.get("regions", []) if r.get("id") == iid + "/box"), None)
    if region is None:
        raise ValueError("请选择折叠的模块实例")
    w, h = (abs(region["to"][k] - region["from"][k]) for k in (0, 1))
    longest = max(w, h)
    start = math.ceil(MIN_BOX * longest / min(w, h) / step - 1e-8)
    stop = math.floor(longest / step + 1e-8)
    # Bound an expensive diagnostic on unusually large drawings. In that case
    # return the known valid size, explicitly without claiming it is minimal.
    if stop - start + 1 > MAX_RESIZE_LIMIT_CHECKS:
        return {"size": {"w": w, "h": h}, "minimum": False}
    candidate = Layout.from_dict(layout.to_dict())
    inst = next(i for i in candidate.extra["modules"]["instances"] if i["id"] == iid)
    for tick in range(start, stop + 1):
        size = {"w": round(round(w / longest * tick) * step, 4),
                "h": round(round(h / longest * tick) * step, 4)}
        if min(size.values()) < MIN_BOX:
            continue
        inst["collapsed_size"] = size
        # a size that routes under the short search routes under the full one
        with route_budget(QUICK_ROUTE_BUDGET):
            trial = candidate.to_dict()
            try:
                ports = assemble(candidate, rep.scale, prof).ports
            except ModuleError:
                continue
            _move_port_wires(trial, assembly.ports, ports)
            if not new_edit_errors(rep, check(Layout.from_dict(trial), prof)):
                return {"size": size, "minimum": True}
    return {"size": {"w": w, "h": h}, "minimum": False}


def _images(items) -> list[tuple[str, str]]:
    """Reference figures from the page as data URLs -> (media type, base64)."""
    out = []
    for url in items or []:
        head, _, data = str(url).partition(",")
        mt = head.removeprefix("data:").removesuffix(";base64")
        if mt in IMAGE_TYPES and data and len(data) < 7_000_000:
            out.append((mt, data))
    return out[:3]


class _Rejected(ValueError):
    """A request the validator turned down; the message is for the page."""


def _checked(lay: Layout, profile: str, message: str):
    rep = check(lay, get_profile(profile))
    if not rep.ok:
        raise _Rejected(message)
    return rep


# POST path -> handler method.  PLAIN routes read their own fields and answer
# for themselves; LAYOUT routes get the posted table and return the answer.
PLAIN_ROUTES = {
    "/api/ai-settings": "_set_ai_settings_route",
    "/api/callout-preview": "_callout_preview",
    "/api/directories": "_directories",
    "/api/generate": "_generate",
    "/api/save-as": "_save_as_route",
    "/api/clarify": "_clarify",
    "/api/match": "_match",
    "/api/from-template": "_from_template",
    "/api/cancel": "_cancel",
}
PLAIN_GET_ROUTES = {"/api/ai-settings": "_get_ai_settings"}
LAYOUT_ROUTES = {
    "/api/selection-plan": "_selection_plan",
    "/api/module-library": "_module_library",
    "/api/module-library-save": "_module_library_save",
    "/api/module-library-delete": "_module_library_delete",
    "/api/module-library-place": "_module_library_place",
    "/api/module-library-update": "_module_library_update",
    "/api/module-resize-limit": "_module_resize_limit",
    "/api/module-selection": "_module_selection",
    "/api/module-extract": "_module_extract",
    "/api/module-empty": "_module_empty",
    "/api/module-definition": "_module_definition",
    "/api/module-contact": "_module_contact",
    "/api/module-dissolve": "_module_dissolve",
    "/api/module-add": "_module_add",
    "/api/module-connections": "_module_connections",
    "/api/module-parameters": "_module_parameters",
    "/api/check": "_check",
    "/api/electrical": "_electrical",
    "/api/save": "_save",
    "/api/preview": "_preview",
    "/api/mode": "_mode",
    "/api/mode-edit": "_mode_edit",
    "/api/export": "_export",
    "/api/export-modes": "_export_modes",
    "/api/save-template": "_save_template",
    "/api/module-upgrade": "_module_upgrade",
    "/api/waveform-import": "_waveform_import",
    "/api/waveform-view": "_waveform_view",
    "/api/waveform-preview": "_waveform_preview",
    "/api/waveform-period": "_waveform_period",
    "/api/waveform-save": "_waveform_save",
    "/api/waveform-export": "_waveform_export",
    "/api/spice-info": "_spice_info",
    "/api/emi-info": "_emi_info",
    "/api/emi-prepare": "_emi_prepare",
    "/api/emi-run": "_emi_run",
    "/api/spice-preset": "_spice_preset",
    "/api/spice-recommend": "_spice_recommend",
    "/api/spice-save": "_spice_save",
    "/api/spice-export": "_spice_export",
    "/api/closed-loop-info": "_closed_loop_info",
    "/api/simulation-export": "_simulation_export",
}


class Job:
    """One model run in a background thread, polled by the page.  Cancelling
    stops between rounds; a request already in flight ends by itself (or by
    the request timeout) and its reply is dropped."""

    def __init__(self, description: str, profile: str, rounds: int, chat,
                 provider: str | None, model: str | None, kind: str = "draw",
                 structure: str | None = None, images: list | None = None,
                 current: dict | None = None, workflow=None):
        from ..ai import provider_info
        self.id = uuid.uuid4().hex[:12]
        self.started = time.monotonic()
        self.finished: float | None = None
        self.state = "running"          # running | done | error | cancelled
        self.rounds_max = rounds
        self.current = 1
        self.attempts: list[dict] = []
        self.result: dict | None = None
        self.error: str | None = None
        self.cancel = threading.Event()
        self.kind, self.structure, self.images = kind, structure, images or None
        self.base = current
        self.workflow = workflow
        self.partial = ""               # reply text of the request in flight
        self.stats: list[dict] = []     # timing and tokens, one per request
        self._chat = None
        self._args = (description, profile, rounds, chat, provider, model)
        self._chat_ready, self._chat_error = False, None
        # Client construction makes no request. Capture settings before a window
        # can change them; this conversation keeps its original model and key.
        with CONFIG_LOCK:
            self.info = provider_info(provider, model) if chat is None else \
                {"provider": "scripted", "model": "-", "problem": None}
            try:
                self._chat_for("plan" if kind == "plan" else "draw")
            except Exception as exc:
                self._chat_error = exc
            self._chat_ready = True
        threading.Thread(target=self._run, daemon=True).start()

    def _log(self, msg: str) -> None:
        print(f"[生成 {self.id}] {msg}", flush=True)

    def _text(self, piece: str) -> None:
        self.partial += piece

    def _chat_for(self, stage: str):
        """The injected test chat, or a real one, kept so its stats can be read."""
        if self._chat_ready:
            if self._chat_error:
                raise self._chat_error
            return self._chat
        from ..ai import loop
        from ..ai.prompt import PLAN_SYSTEM, system_prompt
        description, profile, rounds, chat, provider, model = self._args
        if chat is None:
            system = (self.workflow.system_prompt() if self.workflow else
                      PLAN_SYSTEM if stage == "plan" else system_prompt(get_profile(profile).name))
            chat = loop.make_chat(system, provider, model, stage=stage)
        self._chat = chat
        return chat

    def _seen(self, attempt) -> None:
        self.partial = ""
        self.attempts.append(_round(attempt))
        self.current = attempt.round + 1

    def _run(self) -> None:
        from ..ai import Cancelled, ModelError, changes, generate, plan
        description, profile, rounds, _, provider, model = self._args
        self._log(f"{self.kind}: {self.info.get('provider')} / {self.info.get('model')} "
                  f"@ {self.info.get('base_url', '-')}")
        try:
            if self.kind == "plan":
                self._log("等待模型列出它理解的结构…")
                text = plan(description, profile, self._chat_for("plan"), model, provider,
                            self.images, on_text=self._text, log=self._log)
                if self.cancel.is_set():
                    raise Cancelled("已取消")
                self.state, self.result = "done", {"plan": text}
                self.finished = time.monotonic()
                self._log("结构已返回")
                return
            gen = generate(description, profile, rounds, chat=self._chat_for("draw"),
                           provider=provider, model=model, log=self._log,
                           on_attempt=self._seen, should_stop=self.cancel.is_set,
                           structure=self.structure, images=self.images,
                           current=self.base, on_text=self._text, workflow=self.workflow)
            best = gen.best
            res = {"ok": gen.ok, "layout": best.to_dict() if best else None,
                   "rounds": [_round(a) for a in gen.attempts],
                   "passes_double": gen.passes_double, "notes": gen.notes,
                   "selected_round": gen.best_attempt.round if gen.best_attempt else None}
            if self.workflow is not None:
                res["table"] = res.pop("layout")
                res["changes"] = self.workflow.changes(best) if best else []
            elif self.base is not None and best is not None:
                res["changes"] = changes(Layout.from_dict(self.base), best)
            if self.workflow is None and best is not None and not gen.ok and not gen.passes_double and \
                    get_profile(profile).name != "ieee-double":
                res["passes_double"] = check(best, get_profile("ieee-double")).ok
            if best is None:
                self.state, self.error = "error", ("模型没有给出通过结构校验的框图" if self.workflow else "模型没有给出可读的坐标表")
            else:
                self.state, self.result = "done", res
        except Cancelled:
            self.state = "cancelled"
        except (ModelError, ValueError) as exc:
            self.state, self.error = "error", str(exc)
        except Exception as exc:   # surface anything unexpected to the page
            import traceback
            traceback.print_exc()
            self.state, self.error = "error", f"{type(exc).__name__}: {exc}"
        self.finished = time.monotonic()
        self._log(f"结束：{self.state}" + (f"，{self.error}" if self.error else ""))

    def status(self) -> dict:
        end = self.finished or time.monotonic()
        return {"id": self.id, "state": self.state, "elapsed_s": round(end - self.started),
                "round": min(self.current, self.rounds_max), "rounds_max": self.rounds_max,
                "attempts": self.attempts, "result": self.result, "error": self.error,
                "provider": self.info, "partial": self.partial if self.state == "running" else "",
                "stats": list(getattr(self._chat, "stats", None) or [])}


class RecommendationJob(Job):
    """Reuse provider/configuration, job polling and the bounded AI rewrite loop."""

    def _run(self):
        from ..ai import Cancelled, generate
        try:
            workflow = self.workflow
            if workflow.use_ai:
                description, profile, rounds, _, provider, model = self._args
                gen = generate(description, profile, rounds, chat=self._chat_for("draw"),
                               provider=provider, model=model, workflow=workflow,
                               on_attempt=self._seen, should_stop=self.cancel.is_set,
                               on_text=self._text, log=self._log)
                document = gen.best if gen.ok else None
            else:
                document = workflow.formula()
            if self.cancel.is_set():
                raise Cancelled("已取消")
            self.result = {"ok": document is not None,
                           "proposal": document.to_dict() if document else None,
                           "rounds": self.attempts}
            self.state = "done"
        except Cancelled:
            self.state = "cancelled"
        except Exception as exc:
            self.state, self.error = "error", str(exc)
        finally:
            self.finished = time.monotonic()


class Editor:
    def __init__(self, path: Path, profile: str, out_dir: Path, chat_factory=None,
                 provider: str | None = None, model: str | None = None, settings=None):
        self.path, self.profile, self.out_dir = path, profile, out_dir
        self.chat_factory = chat_factory   # tests inject a scripted model
        self.provider, self.model = provider, model
        self.web_settings = WebSettings(self, settings)
        self.jobs: dict[str, Job] = {}
        from ..waveforms import WaveStore
        self.wave_store = WaveStore(path)

    def output_path(self, value=None) -> Path:
        if value is None:
            return self.out_dir.resolve()
        if not isinstance(value, str) or not value.strip():
            raise ValueError("请填写导出文件夹")
        path = Path(value.strip()).expanduser()
        return (path if path.is_absolute() else self.path.parent / path).resolve()

    def directories(self, value=None) -> dict:
        path = self.output_path(value)
        # A newly typed destination may not exist until export. Start browsing
        # at its nearest existing parent without creating anything.
        while not path.exists() and path.parent != path:
            path = path.parent
        if not path.is_dir():
            raise ValueError("这个路径不是文件夹")
        children = []
        for child in path.iterdir():
            try:
                if child.is_dir():
                    children.append({"name": child.name, "path": str(child)})
            except OSError:
                continue
        roots = [str(self.path.parent.resolve()), str(Path.home())]
        if os.name == "nt":
            roots += [f"{c}:\\" for c in string.ascii_uppercase if Path(f"{c}:\\").is_dir()]
        else:
            roots.append("/")
        return {"path": str(path), "parent": str(path.parent),
                "roots": list(dict.fromkeys(roots)),
                "directories": sorted(children, key=lambda d: d["name"].casefold())}

    def export(self, lay: Layout, profile: str, req: dict, *, modes=False) -> dict:
        formats = req.get("formats", list(FORMATS))
        if not isinstance(formats, list) or not formats or any(f not in FORMATS for f in formats):
            raise ValueError("请选择至少一种导出格式：PDF、SVG、PNG、TeX")
        formats = tuple(dict.fromkeys(formats))
        prof = get_profile(profile)
        rep = check(lay, prof)
        if not rep.ok:
            raise ValueError("校验未通过，不能导出")
        destination = self.output_path(req.get("out_dir"))
        destination.mkdir(parents=True, exist_ok=True)
        stem = self.path.stem.removesuffix(".layout")
        notes = []
        # Keep compiler intermediates (including mode-sheet PDFs) out of the
        # destination. Never overwrite/delete an existing unselected format.
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp)
            if modes:
                from ..pipeline import render_modes
                results = render_modes(lay, profile, stage, stem, formats,
                                       _style(req.get("style")), node_labels=bool(req.get("node_labels")))
                sources = [p for r in results for f, p in r.files.items() if f in formats]
                notes = [n for r in results for n in r.notes]
            else:
                sources = list(build(emit(lay, prof, rep, mono=bool(req.get("mono")), node_labels=bool(req.get("node_labels"))),
                                     stage, stem, formats).values())
            files = []
            for src in sources:
                target = destination / src.name
                shutil.copyfile(src, target)
                files.append(str(target))
        # An export writes figures only. The table is saved by Save: an export
        # during module-definition editing must not overwrite the project file.
        from ..regions import transparency_notes
        notes.extend(transparency_notes(lay))
        return {"files": files, "notes": notes, "out_dir": str(destination)}

    def handler(self):
        editor = self

        class H(SettingsRoutes, BaseHTTPRequestHandler):
            web_settings = editor.web_settings
            def log_message(self, *args):  # keep the terminal quiet
                pass

            def _send(self, code: int, body: bytes, ctype: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _json(self, obj, code: int = 200) -> None:
                self._send(code, json.dumps(obj, ensure_ascii=False).encode(),
                           "application/json; charset=utf-8")

            def do_GET(self):
                if self.path in PLAIN_GET_ROUTES:
                    try:
                        self._json(getattr(self, PLAIN_GET_ROUTES[self.path])())
                    except (ValueError, OSError) as exc:
                        self._json({"error": str(exc)}, 400)
                    return
                if self.path in ("/", "/index.html"):
                    self._send(200, (STATIC / "index.html").read_bytes(),
                               "text/html; charset=utf-8")
                elif self.path in ("/i18n.js", "/i18n-messages.js", "/emi.js", "/waveforms.js", "/waveforms.css", "/module-editor.js", "/selection.js", "/context-menu.js", "/menu.js", "/menu.css", "/electrical.js", "/electrical.css", "/editor-ui.js", "/editor-ui.css", "/theme-eda.css", "/simulation-recommendations.js", "/ai-settings.js", "/layers.js", "/guides.js"):
                    self._send(200, (STATIC / self.path[1:]).read_bytes(),
                               "text/javascript; charset=utf-8" if self.path.endswith(".js")
                               else "text/css; charset=utf-8")
                elif self.path == "/api/ai-status":
                    from ..ai import provider_info
                    self._json({"provider": "scripted", "model": "-", "base_url": "-",
                                "timeout_s": 0, "problem": None} if editor.chat_factory
                               else provider_info(editor.provider, editor.model))
                elif self.path == "/api/ai-ping":
                    from ..ai import ping, provider_info
                    info = provider_info(editor.provider, editor.model)
                    self._json({**info, **ping(info["base_url"])} if info.get("base_url")
                               else info)
                elif self.path.startswith("/api/job/"):
                    job = editor.jobs.get(self.path.rsplit("/", 1)[-1])
                    if job is None:
                        self._json({"error": "没有这个任务"}, 404)
                    else:
                        self._json(job.status())
                elif self.path == "/api/state":
                    from ..modules import SYMBOLS
                    self._json({"layout": Layout.load(editor.path).to_dict(),
                                "file": str(editor.path),
                                "out_dir": str(editor.out_dir.resolve()),
                                "profile": editor.profile,
                                "profiles": sorted(PROFILES),
                                "two_terminal": sorted(TWO_TERMINAL), "module_symbols": list(SYMBOLS)})
                else:
                    self._send(404, b"not found", "text/plain")

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                if self.path.startswith("/api/waveform-") and n > 80 * 1024 * 1024:
                    self._json({"error": "CSV 请求过大，文件最大支持 64 MiB"}, 413)
                    return
                req = json.loads(self.rfile.read(n) or b"{}")
                if self.path in PLAIN_ROUTES:
                    getattr(self, PLAIN_ROUTES[self.path])(req)
                    return
                if self.path not in LAYOUT_ROUTES:
                    self._send(404, b"not found", "text/plain")
                    return
                try:
                    lay = Layout.from_dict(req["layout"])
                    prof = req.get("profile", editor.profile)
                    self._json(getattr(self, LAYOUT_ROUTES[self.path])(lay, prof, req))
                except _Rejected as exc:
                    self._json({"error": str(exc)}, 400)
                except templates.TemplateError as exc:
                    self._json({"error": str(exc)}, 400)
                except CompileError as exc:
                    self._json({"error": str(exc)}, 500)
                except ModeError as exc:
                    self._json({"error": f"模态定义有误：{exc}"}, 400)
                except ModuleError as exc:          # already a sentence for the user
                    self._json({"error": str(exc)}, 400)
                except OSError as exc:
                    self._json({"error": "无法读写文件夹，请检查路径和权限：" + (f"{exc.filename}（{exc.strerror}）" if exc.filename else str(exc.strerror or exc))}, 400)
                except (KeyError, ValueError, TypeError, IndexError) as exc:
                    # this package's own errors are already sentences for the user
                    own = type(exc).__module__.startswith("circuit_drawer") and not isinstance(exc, KeyError)
                    self._json({"error": str(exc) if own else f"{type(exc).__name__}: {exc}"}, 400)

            # ---- routes without a layout: each answers for itself ----------
            def _callout_preview(self, req: dict) -> None:
                try:
                    self._json(callout_preview(req))
                except (KeyError, ValueError, TypeError, OverflowError) as exc:
                    self._json({"error": f"箭头预览失败：{exc}"}, 400)

            def _directories(self, req: dict) -> None:
                try:
                    self._json(editor.directories(req.get("path")))
                except (OSError, ValueError, TypeError) as exc:
                    self._json({"error": f"无法打开文件夹：{exc}"}, 400)

            def _save_as_route(self, req: dict) -> None:
                try:
                    self._save_as(req)
                except OSError as exc:
                    self._json({"error": f"无法另存文件，请检查文件名和文件夹权限：{exc}"}, 400)
                except (KeyError, ValueError, TypeError) as exc:
                    self._json({"error": f"{type(exc).__name__}: {exc}"}, 400)

            def _clarify(self, req: dict) -> None:
                from ..ai.clarify import options
                self._json(options(req.get("description", "")) or {})

            def _match(self, req: dict) -> None:
                name = templates.match(req.get("description", ""))
                t = templates.get(name) if name else None
                self._json({"template": name, "title": t.TITLE if t else None,
                            "profile": getattr(t, "DEFAULT_PROFILE", None)})

            def _from_template(self, req: dict) -> None:
                try:
                    from ..pipeline import from_template
                    lay = from_template(req["name"], req.get("options", ""))
                    self._json({"layout": lay.to_dict()})
                except (KeyError, ValueError) as exc:
                    self._json({"error": str(exc)}, 400)

            def _cancel(self, req: dict) -> None:
                job = editor.jobs.get(req.get("id", ""))
                if job:
                    job.cancel.set()
                self._json({"cancelled": bool(job)})

            # ---- routes on the posted layout: return the JSON answer -------
            def _selection_plan(self, lay, prof, req):
                from .selection import selection_plan
                return selection_plan(lay, req["ids"], req.get("wires", []))

            def _module_selection(self, lay, prof, req):
                from ..module_authoring import inspect_selection
                return inspect_selection(lay, req["ids"], req.get("ports", []), prof)

            def _module_extract(self, lay, prof, req):
                from ..module_authoring import extract
                return {"layout": extract(lay, req["ids"], req["name"], req["ports"], req["exposed"], prof).to_dict()}

            def _module_empty(self, lay, prof, req):
                from ..module_authoring import create_empty
                return {"layout": create_empty(lay, req["at"], prof).to_dict()}

            def _module_definition(self, lay, prof, req):
                from ..module_authoring import update_definition
                return {"layout": update_definition(lay, req["name"], req["definition"], prof,
                                                    new_name=req.get("new_name")).to_dict()}

            def _module_dissolve(self, lay, prof, req):
                from ..module_authoring import dissolve
                return {"layout": dissolve(lay, req["id"], prof).to_dict()}

            # ---- module libraries: bundled modules and the user's own ----
            def _module_library(self, lay, prof, req):
                from ..module_store import catalogue
                return catalogue(lay)

            def _module_library_save(self, lay, prof, req):
                from ..module_store import catalogue, save_to_user
                path, out = save_to_user(lay, req["definition"], req.get("name") or None, bool(req.get("force")))
                return {"layout": out.to_dict(), "saved": str(path), "catalogue": catalogue(out)}

            def _module_library_delete(self, lay, prof, req):
                from ..module_store import catalogue, delete_from_user
                delete_from_user(req["name"])
                return {"catalogue": catalogue(lay)}

            def _module_library_place(self, lay, prof, req):
                from ..module_store import place
                out, name = place(lay, req["library"], req["name"], req["at"], req["connections"], prof)
                return {"layout": out.to_dict(), "definition": name}

            def _module_library_update(self, lay, prof, req):
                from ..module_store import update_from_library
                return {"layout": update_from_library(lay, req["definition"], prof).to_dict()}

            def _module_add(self, lay, prof, req):
                from ..module_authoring import add_instance
                return {"layout": add_instance(lay, req["name"], req["at"], req["connections"], prof).to_dict()}

            def _module_parameters(self, lay, prof, req):
                from copy import deepcopy
                from ..module_authoring import _valid
                raw = deepcopy(lay.to_dict())
                inst = next(i for i in raw["modules"]["instances"] if i["id"] == req["id"])
                inst["overrides"] = req["overrides"]
                result = Layout.from_dict(raw)
                _valid(result, prof)
                if result.extra.get("simulation"):
                    from ..spice import circuit_deck
                    circuit_deck(result, prof)
                return {"layout": result.to_dict()}

            def _module_connections(self, lay, prof, req):
                from ..module_authoring import set_connections
                return {"layout": set_connections(lay, req["id"], req["connections"], prof).to_dict()}

            def _module_contact(self, lay, prof, req):
                from ..module_authoring import connect_touching_ports
                return {"layout": connect_touching_ports(lay, prof, req.get("id")).to_dict()}

            def _electrical(self, lay, prof, req):
                from ..electrical import synchronize, NAME
                section = synchronize(lay, prof)
                if req.get("action") == "rename":
                    name = req.get("name", "")
                    if name and (not isinstance(name, str) or not NAME.fullmatch(name)):
                        raise ValueError("网络名须以英文字母开头，仅使用字母、数字、下划线（最多 41 字符）")
                    target = next((n for n in section["nodes"] if n["id"] == req.get("id")), None)
                    if target is None:
                        raise ValueError("节点标号不存在")
                    target.pop("name", None)
                    if name:
                        target["name"] = name
                lay.extra["electrical"] = section
                result = analyse(lay, prof)
                bad = [i["message"] for i in result["issues"] if i["code"] == "bad-electrical"]
                if bad:
                    raise ValueError("；".join(bad))
                return {"electrical": section}

            def _check(self, lay: Layout, prof: str, req: dict) -> dict:
                if req.get("sync_electrical") and "electrical" in lay.extra:
                    lay.extra["electrical"] = self._electrical(lay, prof, {})["electrical"]
                result = analyse(lay, prof)
                if req.get("live_spice"):
                    from ..spice import make_deck
                    try:
                        result["live_spice"] = make_deck(lay, prof, editor.path.parent).text
                    except (ValueError, KeyError, TypeError) as exc:
                        result["live_spice"] = "SPICE 尚未就绪：" + str(exc)
                return result

            def _module_resize_limit(self, lay, prof, req):
                return module_resize_limit(lay, prof, req["id"], req["step"])

            def _save(self, lay: Layout, prof: str, req: dict) -> dict:
                if "electrical" in lay.extra:
                    from ..electrical import synchronize
                    lay.extra["electrical"] = synchronize(lay, prof)
                lay.save(editor.path)
                return {"saved": str(editor.path)}

            def _preview(self, lay: Layout, prof: str, req: dict) -> dict:
                _checked(lay, prof, "校验未通过，先修正标红的问题")
                mode = req.get("mode")
                return {"png": preview_png(lay, prof, bool(req.get("grid")),
                                           None if mode is None else int(mode),
                                           req.get("style"), bool(req.get("mono")))}

            def _mode(self, lay: Layout, prof: str, req: dict) -> dict:
                from ..modes import overlay
                rep = _checked(lay, prof, "校验未通过，先修正标红的问题再看模态")
                st = _style(req.get("style"))
                return overlay(lay, int(req["index"]), rep, get_profile(prof), st.arrows)

            def _mode_edit(self, lay: Layout, prof: str, req: dict) -> dict:
                from ..modes import Mode, next_state, reverse_loop, set_state, states
                rep = _checked(lay, prof, "校验未通过，先修正标红的问题再改模态")
                mode = Mode.from_dict(lay.modes[int(req["index"])])
                if req.get("action") == "reverse":
                    mode = reverse_loop(mode, int(req["loop"]))
                else:
                    el_id = req["id"]
                    now = states(lay, mode).get(el_id)
                    mode = set_state(lay, rep, mode, el_id, next_state(lay, el_id, now))
                return {"mode": mode.to_dict()}

            def _export(self, lay: Layout, prof: str, req: dict) -> dict:
                return editor.export(lay, prof, req)

            def _export_modes(self, lay: Layout, prof: str, req: dict) -> dict:
                return editor.export(lay, prof, req, modes=True)

            def _module_upgrade(self, lay: Layout, prof: str, req: dict) -> dict:
                from ..modules import upgrade_links
                return {"layout": upgrade_links(lay).to_dict()}

            def _save_template(self, lay: Layout, prof: str, req: dict) -> dict:
                aliases = tuple(a for a in re.split(r"[,，\s]+", req.get("aliases", "")) if a)
                path = templates.save(lay, req.get("name", ""), title=req.get("title", ""),
                                      aliases=aliases, profile=prof,
                                      force=bool(req.get("force")))
                return {"saved": str(path), "name": req["name"].strip().lower()}

            # ---- waveforms: the CSV is referenced from layout.extra["waveform"]
            def _wave_store(self):
                editor.wave_store.path = editor.path
                return editor.wave_store

            def _waveform_import(self, lay: Layout, prof: str, req: dict) -> dict:
                return self._wave_store().import_csv(lay, req, prof,
                                                    target=req.get("preview_target", "overview"))

            def _waveform_view(self, lay: Layout, prof: str, req: dict) -> dict:
                return self._wave_store().preview(lay, lay.extra.get("waveform", {}), prof,
                                                 target=req.get("preview_target", "overview"))

            def _waveform_preview(self, lay: Layout, prof: str, req: dict) -> dict:
                from ..waveforms import chart_profile, validate_settings, wave_tex
                from ..emit import preamble
                if not check(lay, get_profile(prof)).ok:
                    raise ValueError("电路校验未通过，不能生成真实预览")
                cfg = lay.extra.get("waveform", {})
                data = self._wave_store().read(cfg)
                validate_settings(lay, cfg, data)
                target = chart_profile(cfg, prof, req.get("preview_target", "overview"))
                tex = preamble(get_profile(target)) + wave_tex(data, cfg, target,
                        [m.get("name") for m in lay.modes]) + "\n\\end{document}\n"
                with tempfile.TemporaryDirectory() as tmp:
                    svg = build(tex, Path(tmp), "wave-preview", ("svg",))["svg"].read_bytes()
                return {"svg": base64.b64encode(svg).decode(), "profile": get_profile(target).name}

            def _waveform_period(self, lay: Layout, prof: str, req: dict) -> dict:
                from ..waveforms import last_period
                data = self._wave_store().read(lay.extra.get("waveform", {}))
                return {"boundaries": last_period(data, req.get("column"))}

            def _waveform_save(self, lay: Layout, prof: str, req: dict) -> dict:
                from ..waveforms import validate_settings
                cfg = lay.extra.get("waveform", {})
                validate_settings(lay, cfg, self._wave_store().read(cfg))
                lay.save(editor.path)
                return {"saved": str(editor.path)}

            def _waveform_export(self, lay: Layout, prof: str, req: dict) -> dict:
                from ..waveforms import export_waveforms
                stem = re.sub(r"[^\w-]+", "_", editor.path.stem.removesuffix(".layout")).strip("_") or "circuit"
                result = export_waveforms(lay, self._wave_store(), editor.output_path(req.get("out_dir")),
                                          stem, prof, req.get("formats", ["pdf", "svg", "png"]),
                                          include_modes=req.get("include_modes", True) is True)
                return result

            def _emi_info(self, lay: Layout, prof: str, req: dict) -> dict:
                from ..emi import INPUTS, FILTER_PARTS, FILTER_INPUTS, REFERENCES, SOURCES, CHANNELS, NOTE
                return dict(inputs=INPUTS, filter_parts=FILTER_PARTS, filter_inputs=FILTER_INPUTS,
                            references=REFERENCES, sources=SOURCES, channels=CHANNELS, note=NOTE)

            def _emi_prepare(self, lay: Layout, prof: str, req: dict) -> dict:
                from ..emi import settings, set_parasitic, import_parasitics
                action = req.get("action", "settings")
                if action == "parasitic":
                    lay = set_parasitic(lay, req.get("item"), req.get("value"))
                elif action == "import":
                    lay = import_parasitics(lay, req.get("csv"), req.get("source"))
                elif action != "settings":
                    raise ValueError("不支持的 EMI 设置操作")
                settings(lay)
                return {"layout": lay.to_dict()}

            def _emi_run(self, lay: Layout, prof: str, req: dict) -> dict:
                from ..emi import run
                output = editor.output_path(req.get("out_dir")) / ("emi-" + uuid.uuid4().hex[:12])
                return run(lay, output, profile=prof, compare=req.get("compare") is True,
                           base_dir=editor.path.parent)

            def _spice_info(self, lay: Layout, prof: str, req: dict) -> dict:
                from ..spice import FIELDS, OPTIONAL_FIELDS, TRANSIENT_FIELDS, SOURCE_WAVE_FIELDS, LIMIT_NOTE, executable
                from ..spice_recommend import INPUTS
                from ..pmsm import QUANTITIES
                from ..completeness import simulation_completeness
                return {"fields": FIELDS, "optional_fields": OPTIONAL_FIELDS, "transient_fields": TRANSIENT_FIELDS,
                        "source_wave_fields": SOURCE_WAVE_FIELDS, "motor_quantities": QUANTITIES,
                        "completeness": simulation_completeness(lay),
                        "available": executable() is not None, "note": LIMIT_NOTE, "recommendation_inputs": INPUTS,
                        **({"module": module_devices(lay, prof)} if "modules" in lay.extra else {})}

            def _spice_preset(self, lay: Layout, prof: str, req: dict) -> dict:
                from ..spice import example_settings
                return {"simulation": example_settings(lay, req.get("preset"), prof)}

            def _spice_recommend(self, lay: Layout, prof: str, req: dict) -> dict:
                from ..ai import provider_info
                from ..spice_recommend import RecommendationWorkflow
                workflow = RecommendationWorkflow(lay, prof, req.get("requirements", {}),
                                                  req.get("description", ""), editor.path.parent)
                chat = editor.chat_factory() if workflow.use_ai and editor.chat_factory else None
                if workflow.use_ai and chat is None:
                    info = provider_info(editor.provider, editor.model)
                    if info.get("problem"):
                        raise ValueError(info["problem"])
                job = RecommendationJob(workflow.description or "按常见工况建议仿真参数", prof, 3,
                                        chat, editor.provider, editor.model,
                                        kind="simulation-parameters", workflow=workflow)
                editor.jobs[job.id] = job
                return job.status()

            def _spice_save(self, lay: Layout, prof: str, req: dict) -> dict:
                from ..spice import make_deck
                make_deck(lay, prof, editor.path.parent)
                lay.save(editor.path)
                return {"saved": str(editor.path)}

            def _spice_export(self, lay: Layout, prof: str, req: dict) -> dict:
                from ..spice import export_spice
                stem = re.sub(r"[^\w-]+", "_", editor.path.stem.removesuffix(".layout")).strip("_") or "circuit"
                result = export_spice(lay, editor.output_path(req.get("out_dir")), stem,
                                      profile=prof, run=req.get("run") is True,
                                      base_dir=editor.path.parent)
                lay.save(editor.path)
                return result

            def _closed_loop_info(self, lay: Layout, prof: str, req: dict) -> dict:
                from ..closed_loop import describe
                return describe(req.get("control"), editor.path.parent)

            def _simulation_export(self, lay: Layout, prof: str, req: dict) -> dict:
                from ..simulation_export import export_simulation
                result = export_simulation(lay, editor.output_path(req.get("out_dir")),
                                           editor.path.stem.removesuffix(".layout"),
                                           target=req.get("target"), profile=prof,
                                           diode_current=req.get("diode_current", 1),
                                           numerical_resistance=req.get("numerical_resistance", "1m"),
                                           control=req.get("control") or "continuous",
                                           approximate_mosfet=req.get("approximate_mosfet", False),
                                           base_dir=editor.path.parent)
                lay.save(editor.path)
                return result

            def _save_as(self, req: dict) -> None:
                """Save into a new file next to the current one and switch to it,
                so a generated figure never overwrites the open file."""
                lay = Layout.from_dict(req["layout"])
                name = re.sub(r"(?:\.layout)?\.json$|\.layout$", "", req.get("name") or lay.name or "figure", flags=re.IGNORECASE)
                stem = re.sub(r"[^\w\-]+", "_", name).strip("_") or "figure"
                folder = editor.path.parent
                path = folder / f"{stem}.json"
                n = 2
                while path.exists() and not req.get("overwrite"):
                    path = folder / f"{stem}_{n}.json"
                    n += 1
                lay.save(path)
                editor.path = path
                self._json({"saved": str(path)})

            def _generate(self, req: dict) -> None:
                """Start a background run and return its id at once; a missing
                key or package is reported here, before any waiting."""
                from ..ai import provider_info
                description = req.get("description", "").strip()
                if not description:
                    self._json({"error": "描述为空"}, 400)
                    return
                chat = editor.chat_factory() if editor.chat_factory else None
                if chat is None:
                    info = provider_info(editor.provider, editor.model)
                    if info.get("problem"):
                        self._json({"error": info["problem"], "provider": info}, 400)
                        return
                current = req.get("current")
                if current is not None:
                    try:
                        Layout.from_dict(current)
                    except (KeyError, ValueError, TypeError) as exc:
                        self._json({"error": f"当前坐标表读不了：{exc}"}, 400)
                        return
                kind = "plan" if req.get("plan") else "tweak" if current else "draw"
                job = Job(description, req.get("profile", editor.profile),
                          max(1, min(8, int(req.get("rounds", 4)))), chat,
                          editor.provider, editor.model, kind,
                          (req.get("structure") or "").strip() or None,
                          _images(req.get("images")), current)
                editor.jobs[job.id] = job
                self._json(job.status())

        return H


def serve(path: Path, profile: str = "ieee-single", port: int = 8765,
          out_dir: Path | None = None, open_browser: bool = True,
          provider: str | None = None, model: str | None = None) -> None:
    path = path.resolve()
    editor = Editor(path, profile, (out_dir or path.parent).resolve(),
                    provider=provider, model=model)
    from ..localserver import bind
    httpd = bind(port, editor.handler())
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"editing {path} at {url}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
