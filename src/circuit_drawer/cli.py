"""Circuit figures, editor, operating modes, waveforms, AI, and ngspice / Simulink / PLECS exports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, templates
from .compile import FORMATS, CompileError
from .layout import Layout
from .options import OptionError
from .pipeline import LayoutRejected, from_template, render
from .profiles import PROFILES, get_profile
from .modes import Mode, ModeError, Style
from .validate import check
from .spice import SpiceError


def _formats(text: str) -> tuple[str, ...]:
    fmts = tuple(f.strip() for f in text.split(",") if f.strip())
    bad = [f for f in fmts if f not in FORMATS]
    if bad:
        raise argparse.ArgumentTypeError(f"未知输出格式 {bad}；可用：{', '.join(FORMATS)}")
    return fmts


SIMULATION_COMMANDS = ("spice-init", "spice", "simulink", "plecs", "psim", "emi", "impedance", "dq-impedance", "parameter-search")
SIMULATION_PROFILE_HELP = ("column width the drawing is checked at; default ieee-single, or ieee-double "
                           "when only that passes (the exported model is the same at any width)")


def _simulation_profile(path: str) -> tuple[str, str | None]:
    """The column width a simulation export checks the drawing at when no
    --profile is given.  Only the drawing check depends on it, never the
    exported model.  A table drawn for two columns (it fails ieee-single but
    passes ieee-double) is checked at ieee-double instead of being refused."""
    try:
        layout = Layout.load(path)
        if check(layout, get_profile("ieee-single")).ok or not check(layout, get_profile("ieee-double")).ok:
            return "ieee-single", None
    except (OSError, ValueError, KeyError, TypeError):
        return "ieee-single", None    # the export itself reports what is wrong
    return "ieee-double", ("提示：这张表按双栏宽度绘制，单栏校验不通过，本次按 ieee-double 校验；"
                           "导出的模型与栏宽无关。要强制单栏，请加 --profile single。")


def _table_kind(path) -> str | None:
    """"control" or "circuit" for an existing JSON table, None for anything else
    (a template name, a file still to be created)."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8")) if path and Path(path).is_file() else None
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    return "control" if raw.get("kind") == "control" else "circuit" if "elements" in raw else None


def _print_options(options) -> None:
    for o in options:
        choices = "|".join(map(str, o.choices)) if o.choices else \
            ("yes|no" if isinstance(o.default, bool) else "text")
        kw = f"  keywords: {', '.join(o.keywords)}" if o.keywords else ""
        print(f"  {o.name}={choices} (default {o.default}) {o.help}{kw}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="circuit-drawer", description=__doc__)
    ap.add_argument("--version", action="version", version="%(prog)s " + __version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list templates and their options")
    sub.add_parser("blocks", help="list built-in modules, public ports and one-to-one drawing sizes")

    si = sub.add_parser("spice-init", help="save example Buck/Boost simulation parameters into a new JSON table")
    si.add_argument("layout")
    si.add_argument("--preset", choices=("buck", "boost"), required=True)
    si.add_argument("--out", "-o", required=True, help="configured JSON file (input stays untouched)")
    si.add_argument("--profile", "-p", default=None, help=SIMULATION_PROFILE_HELP)
    si.add_argument("--force", action="store_true", help="replace an existing output JSON")

    sp = sub.add_parser("spice", help="export a validated table to ngspice; optionally run and produce CSV")
    sp.add_argument("layout", help="JSON table with a simulation section")
    sp.add_argument("--out", "-o", default="out/spice", help="output directory")
    sp.add_argument("--name", help="output file stem")
    sp.add_argument("--profile", "-p", default=None, help=SIMULATION_PROFILE_HELP)
    sp.add_argument("--run", action="store_true", help="run ngspice; write genuine CSV with SI unit headers")
    sp.add_argument("--ngspice", help="ngspice executable (or CIRCUIT_DRAWER_NGSPICE / PATH)")
    em = sub.add_parser("emi", help="run a drawn two-line LISN circuit and estimate conducted-noise spectra")
    em.add_argument("layout")
    em.add_argument("--out", "-o", default="out/emi")
    em.add_argument("--profile", "-p", default=None, help=SIMULATION_PROFILE_HELP)
    em.add_argument("--compare", action="store_true", help="also simulate explicitly bound filter components with proposed values")
    em.add_argument("--ngspice")

    im = sub.add_parser("impedance", help="sweep DC averaged output impedance and compare transient injection")
    im.add_argument("layout")
    im.add_argument("--probe", required=True, help="voltage probe across the output port")
    im.add_argument("--out", "-o", default="out/impedance")
    im.add_argument("--profile", "-p", default=None, help=SIMULATION_PROFILE_HELP)
    im.add_argument("--compare", action="store_true", help="compare three frequencies using time-domain injection")
    im.add_argument("--ngspice")
    from .impedance import INPUTS as IMPEDANCE_INPUTS
    for key, (label, default) in IMPEDANCE_INPUTS.items():
        im.add_argument("--" + key.replace("_", "-"), default=default, help=label)

    ratio = sub.add_parser("impedance-ratio", help="analyse scalar source/load port models and Nyquist stability")
    ratio.add_argument("model", help="JSON with source_impedance and load_admittance polynomials")
    ratio.add_argument("--out", "-o", default="out/impedance-ratio")

    stability = sub.add_parser("dq-stability", help="analyse dq state-space port interconnection and internal poles")
    stability.add_argument("model", help="JSON with source_impedance and load_admittance state-space matrices")
    stability.add_argument("--out", "-o", default="out/dq-stability")

    dq = sub.add_parser("dq-impedance", help="identify a dq impedance matrix by two-axis voltage injection")
    dq.add_argument("layout")
    dq.add_argument("--sources", nargs=3, required=True, metavar=("A", "B", "C"), help="three grid voltage-source IDs in ABC order")
    dq.add_argument("--out", "-o", default="out/dq-impedance")
    dq.add_argument("--profile", "-p", default=None, help=SIMULATION_PROFILE_HELP)
    dq.add_argument("--compare", action="store_true", help="repeat three frequencies with half amplitude and time step")
    dq.add_argument("--ngspice")
    from .dq_impedance import INPUTS as DQ_INPUTS
    for key, (label, default) in DQ_INPUTS.items():
        dq.add_argument("--" + key.replace("_", "-"), default=default, help=label)

    for target, description in (("simulink", "MATLAB builder for Simulink + Simscape Foundation"),
                                ("plecs", "native PLECS Standalone .plecs model"),
                                ("psim", "Python builder for PSIM 2026+")):
        ex = sub.add_parser(target, help="export a validated table to " + description)
        ex.add_argument("layout", help="JSON table with the existing simulation section")
        ex.add_argument("--out", "-o", default="out/" + target, help="output directory")
        ex.add_argument("--name", help="model name (converted to a safe ASCII identifier)")
        ex.add_argument("--profile", "-p", default=None, help=SIMULATION_PROFILE_HELP)
        ex.add_argument("--diode-current", default="1", help="diode tangent reference current in A (default 1)")
        ex.add_argument("--numerical-resistance", default="1m",
                        help="series resistance of numerical node capacitors in ohm (default 1m)"
                        if target == "plecs" else argparse.SUPPRESS)
        ex.add_argument("--approximate-mosfet", action="store_true",
                        help="explicitly replace LEVEL=1 channels with switches using each device's ron/roff")
        ex.add_argument("--control", default="continuous", choices=("continuous", "discrete"),
                        help="closed_loop controller: continuous (as ngspice, default) or discrete (code.fs and code.method)")

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--profile", "-p", default=None,
                       help=f"style profile: {sorted(PROFILES)} or single/double")
        p.add_argument("--out", "-o", default="out", help="output directory")
        p.add_argument("--formats", "-f", type=_formats, default=FORMATS)
        p.add_argument("--name", help="output file stem")
        p.add_argument("--grid", action="store_true",
                       help="overlay the coordinate grid (editing preview, not for papers)")

    d = sub.add_parser("draw", help="draw a template")
    d.add_argument("template")
    d.add_argument("options", nargs="*", help='e.g. switch=igbt 全桥整流 currents')
    common(d)
    d.add_argument("--mono", action="store_true",
                   help="background regions and callouts in greys (black-and-white print)")

    m = sub.add_parser("modes", help="one figure per operating mode: conducting parts "
                                      "highlighted, current arrows, the rest grey")
    m.add_argument("template", help="template name (e.g. llc, dab) or a JSON table")
    m.add_argument("options", nargs="*", help="template options, as for draw")
    common(m)
    m.add_argument("--modes", dest="mode_file",
                   help="JSON file with a list of modes (replaces the built-in ones)")
    m.add_argument("--only", help="mode numbers to draw, e.g. 1,3")
    m.add_argument("--list", action="store_true", help="print the modes and stop")
    m.add_argument("--no-sheet", action="store_true",
                   help="skip <name>_modes, the overview with all modes on one page")
    m.add_argument("--color", default=None, help="highlight colour, #rrggbb or an "
                                                 "xcolor name/mix such as red!50!blue (default #c8102e)")
    m.add_argument("--mono", action="store_true",
                   help="black and white: heavy black paths, grey dashed rest")
    m.add_argument("--off", choices=("grey", "dashed"), default=None,
                   help="how idle wires look (default grey)")
    m.add_argument("--arrows", type=int, default=None,
                   help="automatic current arrows per loop (default 3, 0 = none)")

    e = sub.add_parser("export", help="write a template's coordinate table as JSON")
    e.add_argument("template")
    e.add_argument("options", nargs="*")
    e.add_argument("--out", "-o", required=True, help="JSON file to write")
    e.add_argument("--force", action="store_true", help="replace an existing output JSON")

    c = sub.add_parser("check", help="validate a JSON coordinate table")
    c.add_argument("layout")
    c.add_argument("--profile", "-p", default="ieee-single",
                   help=f"style profile: {sorted(PROFILES)} or single/double (default: single)")

    ed = sub.add_parser("edit", help="open the local web editor on a JSON coordinate table")
    ed.add_argument("layout", help="JSON file; created from --template if missing")
    ed.add_argument("--template", "-t", help="template to start from, e.g. flyback")
    ed.add_argument("options", nargs="*", help="template options when starting from one")
    ed.add_argument("--profile", "-p", default=None)
    ed.add_argument("--port", type=int, default=8765)
    ed.add_argument("--out", "-o", help="export directory (default: next to the JSON)")
    ed.add_argument("--no-browser", action="store_true")
    ed.add_argument("--provider", choices=("anthropic", "openai"),
                    help="model provider for the editor's describe-a-topology button")
    ed.add_argument("--model", help="model id for that button")

    r = sub.add_parser("render", help="validate and draw a JSON coordinate table")
    r.add_argument("layout")
    common(r)
    r.add_argument("--mono", action="store_true",
                   help="background regions and callouts in greys (black-and-white print)")

    am = sub.add_parser("modes-from-sim", help="simulate and propose operating modes for confirmation in the editor")
    am.add_argument("layout", help="coordinate table JSON")
    am.add_argument("--out", "-o", required=True, help="candidate report and simulation output folder")
    am.add_argument("--profile", "-p", default="ieee-single")
    am.add_argument("--ngspice", help="ngspice executable")

    ps = sub.add_parser("parameter-search", help="scan explicit parameter ranges without modifying the drawing")
    ps.add_argument("layout", help="coordinate table with simulation parameters")
    ps.add_argument("--config", required=True, help="JSON ranges, output target and limits; power_file is relative to the drawing")
    ps.add_argument("--out", "-o", default="out/parameter-search", help="parent folder for unique batch results")
    ps.add_argument("--profile", "-p", default=None, help=SIMULATION_PROFILE_HELP)
    ps.add_argument("--ngspice", help="ngspice executable")

    w = sub.add_parser("waveforms", help="export CSV waveforms and manually bound operating modes")
    w.add_argument("layout", help="JSON table with waveform settings saved by the editor")
    common(w)
    w.set_defaults(formats=("pdf", "svg", "png"))
    w.add_argument("--wave-only", action="store_true", help="export time/XY figures without circuit modes")
    w.add_argument("--xy-only", action="store_true", help="only export the configured XY/state-plane plot")

    n = sub.add_parser("new", help="describe a topology without a template; a model "
                                   "writes the coordinate table, the validator judges it")
    n.add_argument("description", help='e.g. "四开关 Buck-Boost，输入电容，输出 Co 和 RL"')
    n.add_argument("--out", "-o", required=True, help="JSON file to write")
    n.add_argument("--profile", "-p", default="ieee-single")
    n.add_argument("--rounds", type=int, default=4,
                   help="maximum model replies, including reworks (default 4)")
    n.add_argument("--provider", choices=("anthropic", "openai"),
                   help="model provider (default $CIRCUIT_DRAWER_PROVIDER, else anthropic; "
                        "openai also covers compatible APIs via OPENAI_BASE_URL)")
    n.add_argument("--model", help="model id (default $CIRCUIT_DRAWER_MODEL; "
                                   "anthropic falls back to claude-opus-5-5)")
    n.add_argument("--edit", action="store_true", help="open the result in the web editor")
    n.add_argument("--review", action="store_true", help="review the rendered picture and try one connection-preserving layout edit")
    n.add_argument("--plan", action="store_true",
                   help="only print the model's reading of the circuit (parts and "
                        "connections) to check before drawing; -o gets that text")
    n.add_argument("--image", action="append", default=[],
                   help="reference figure (png/jpg/webp) whose arrangement to follow; repeatable")
    n.add_argument("--anyway", action="store_true",
                   help="call the model even when a hand-drawn template matches")
    n.add_argument("--structure", help="text file with a confirmed parts list "
                                       "(e.g. an edited --plan output) to draw exactly")
    n.add_argument("--port", type=int, default=8765)

    tw = sub.add_parser("tweak", help="change an existing JSON table with one sentence; "
                                       "a model edits it, the validator judges it")
    tw.add_argument("layout", help="JSON table to change (left untouched)")
    tw.add_argument("instruction", help='e.g. "把 D 挪到右边" or "add an input capacitor Cin"')
    tw.add_argument("--out", "-o", help="JSON file to write (default: <name>_ai.json next to it)")
    tw.add_argument("--profile", "-p", default="ieee-single")
    tw.add_argument("--rounds", type=int, default=3)
    tw.add_argument("--provider", choices=("anthropic", "openai"))
    tw.add_argument("--model")
    tw.add_argument("--edit", action="store_true", help="open the result in the web editor")
    tw.add_argument("--review", action="store_true", help="review the rendered picture and try one connection-preserving layout edit")
    tw.add_argument("--port", type=int, default=8765)

    st = sub.add_parser("save-template", help="save a validated JSON table as a template")
    st.add_argument("layout")
    st.add_argument("name", help="template name: lowercase letters, digits, _")
    st.add_argument("--title", default="")
    st.add_argument("--alias", action="append", default=[], help="extra name, repeatable")
    st.add_argument("--profile", "-p", default="ieee-single",
                    help="profile the table must pass")
    st.add_argument("--dir", help="folder (default $CIRCUIT_DRAWER_TEMPLATES or "
                                  "~/.circuit_drawer/templates)")
    st.add_argument("--force", action="store_true", help="overwrite an existing one")

    argv = list(sys.argv[1:] if argv is None else argv)
    mixed = {"draw": d, "modes": m, "export": e, "edit": ed}
    if argv and argv[0] in mixed:
        # Subparsers cannot parse intermixed arguments; use the selected parser.
        a = mixed[argv[0]].parse_intermixed_args(argv[1:])
        a.cmd = argv[0]
    else:
        a = ap.parse_args(argv)
    table = getattr(a, "layout", None) or (a.template if a.cmd == "modes" else None)
    if _table_kind(table) == "control":
        command = a.cmd if a.cmd in ("edit", "check", "render") else "check"
        print(f"error: {table} 是控制框图表，请用 circuit-drawer-control {command} 打开或处理", file=sys.stderr)
        return 2
    if a.cmd in SIMULATION_COMMANDS and a.profile is None:
        a.profile, note = _simulation_profile(a.layout)
        if note:
            print(note, file=sys.stderr)
    if getattr(a, "profile", "") is None:
        # large templates name the column width they are drawn for
        tpl = getattr(a, "template", None)
        try:
            a.profile = getattr(templates.get(tpl), "DEFAULT_PROFILE", None) if tpl else None
        except KeyError:
            a.profile = None
        a.profile = a.profile or "ieee-single"
    try:
        if a.cmd == "parameter-search":
            from .parameter_search import Search
            path = Path(a.layout).resolve()
            config = json.loads(Path(a.config).read_text(encoding="utf-8-sig"))
            result = Search(Layout.load(path), config, profile=a.profile, base_dir=path.parent).run(a.out, ngspice=a.ngspice)
            print(f"完成 {len(result['points'])} 组；符合约束 {sum(p['feasible'] for p in result['points'])} 组；Pareto 候选 {result['pareto_ids']}。")
            print(Path(result['directory']) / 'results.json')
            return 0 if result['pareto_ids'] else 1
        if a.cmd == "modes-from-sim":
            from .auto_modes import run
            result = run(Layout.load(a.layout), a.out, profile=a.profile, ngspice=a.ngspice,
                         base_dir=Path(a.layout).resolve().parent)
            print(result["note"])
            print(result["reason"] or f"{len(result['candidates'])} 个候选模态，含短区间；请在编辑器确认采用。")
            for path in result["files"]:
                print(path)
            return 0 if result["ready"] else 1
        if a.cmd in ("impedance-ratio", "dq-stability"):
            from .impedance_ratio import analyse, export
            if a.cmd == "dq-stability":
                from .dq_stability import analyse
            result = analyse(json.loads(Path(a.model).read_text(encoding="utf-8-sig")))
            print(result["note"])
            print(result["verdict"])
            print(export(result, a.out, prefix=a.cmd))
            return 0
        if a.cmd == "dq-impedance":
            from .dq_impedance import run
            result = run(Layout.load(a.layout), a.out, sources=a.sources, compare=a.compare,
                         options={k: getattr(a, k) for k in DQ_INPUTS}, profile=a.profile,
                         ngspice=a.ngspice, base_dir=Path(a.layout).resolve().parent)
            print(result["note"])
            for path in result["files"]:
                print(path)
            return 0
        if a.cmd == "impedance":
            from .impedance import run
            result = run(Layout.load(a.layout), a.out, probe=a.probe, compare=a.compare,
                         options={k: getattr(a, k) for k in IMPEDANCE_INPUTS}, profile=a.profile,
                         ngspice=a.ngspice, base_dir=Path(a.layout).resolve().parent)
            print(result["note"])
            for path in result["files"]:
                print(path)
            return 0
        if a.cmd == "emi":
            from .emi import run
            result = run(Layout.load(a.layout), a.out, profile=a.profile, compare=a.compare,
                         ngspice=a.ngspice, base_dir=Path(a.layout).resolve().parent)
            print(result["note"])
            for path in result["files"]:
                print(path)
            return 0
        if a.cmd == "spice-init":
            from .spice import circuit_deck, example_settings
            lay = Layout.load(a.layout)
            lay.extra["simulation"] = example_settings(lay, a.preset, a.profile)
            circuit_deck(lay, a.profile)
            target = Path(a.out)
            if target.exists() and not a.force:
                raise SpiceError(f"{target} 已存在；用新文件名或 --force")
            target.parent.mkdir(parents=True, exist_ok=True)
            lay.save(target)
            print(f"示例参数已写入 {target}；请核对数值与工况，再运行 spice")
            return 0
        if a.cmd in ("simulink", "plecs", "psim"):
            from .simulation_export import export_simulation
            result = export_simulation(Layout.load(a.layout), a.out,
                                       a.name or Path(a.layout).stem.removesuffix(".layout"),
                                       target=a.cmd, profile=a.profile, diode_current=a.diode_current,
                                       numerical_resistance=a.numerical_resistance, control=a.control,
                                       approximate_mosfet=a.approximate_mosfet,
                                       base_dir=Path(a.layout).parent)
            for note in result["notes"]:
                print(note, file=sys.stderr)
            for path in result["files"]:
                print(path)
            return 0
        if a.cmd == "spice":
            from .spice import export_spice
            lay = Layout.load(a.layout)
            result = export_spice(lay, a.out, a.name or Path(a.layout).stem.removesuffix(".layout"),
                                  profile=a.profile, run=a.run, ngspice=a.ngspice,
                                  base_dir=Path(a.layout).resolve().parent)
            for note in result["notes"]:
                print(note, file=sys.stderr)
            for path in result["files"]:
                print(path)
            return 0
        if a.cmd == "blocks":
            from .module_store import builtin_blocks
            print("内置模块：名称 | 说明 | 公开端口 | 1:1 宽 × 高（cm，含标签，双栏默认字号）")
            for block in builtin_blocks():
                print(f"{block['name']} | {block['note']} | {', '.join(block['ports'])} | "
                      f"{block['width']:.2f} × {block['height']:.2f}")
            return 0
        if a.cmd == "list":
            for name, tpl in templates.available().items():
                src = f"  [saved: {tpl.path}]" if hasattr(tpl, "path") else ""
                print(f"{name}: {tpl.TITLE}{src}")
                aliases = [alias for alias, target in templates.ALIASES.items() if target == name]
                aliases.extend(getattr(tpl, "ALIASES", ()))
                if aliases:
                    print(f"  aliases: {', '.join(dict.fromkeys(aliases))}")
                _print_options(tpl.OPTIONS)
            return 0
        if a.cmd == "export":
            target = Path(a.out)
            if target.exists() and not a.force:
                raise ValueError(f"{target} 已存在；用新文件名或 --force")
            layout = from_template(a.template, " ".join(a.options))
            target.parent.mkdir(parents=True, exist_ok=True)
            layout.save(target)
            print(a.out)
            return 0
        if a.cmd == "edit":
            from .editor.server import serve
            path = Path(a.layout)
            if not path.exists():
                if not a.template:
                    print(f"error: {path} does not exist; pass --template to start "
                          f"from one", file=sys.stderr)
                    return 2
                get_profile(a.profile)
                from_template(a.template, " ".join(a.options)).save(path)
            get_profile(a.profile)
            serve(path, a.profile, a.port, Path(a.out) if a.out else None,
                  not a.no_browser, a.provider, a.model)
            return 0
        if a.cmd == "new":
            return _new(a)
        if a.cmd == "tweak":
            return _tweak(a)
        if a.cmd == "save-template":
            path = templates.save(Layout.load(a.layout), a.name, title=a.title,
                                  aliases=tuple(a.alias), profile=a.profile,
                                  directory=Path(a.dir) if a.dir else None,
                                  force=a.force)
            saved = templates.SavedTemplate(path)
            print(f"saved template {saved.NAME} -> {path}")
            if a.name != saved.NAME:
                print(f"模板名已规范为 {saved.NAME!r}（去掉首尾空白并转为小写）")
            if a.dir:
                print(f"自定义模板目录：{path.parent}；使用前请将 CIRCUIT_DRAWER_TEMPLATES 设为此目录。")
            print(f"use it: circuit-drawer draw {saved.NAME}")
            if saved.OPTIONS:
                print("此模板可用选项：")
                _print_options(saved.OPTIONS)
            else:
                print("此模板按保存的画法出图，可在编辑器中继续修改。")
            return 0
        if a.cmd == "modes":
            return _modes(a)
        if a.cmd == "check":
            layout, prof = Layout.load(a.layout), get_profile(a.profile)
            rep = check(layout, prof)
            from .control_view import prepare as prepare_control_view
            from .validate import Issue
            try:
                prepare_control_view(layout, prof, rep.scale, Path(a.layout).resolve().parent)
            except ValueError as exc:
                if not any(i.code == "bad-control-view" for i in rep.issues):
                    rep.issues.append(Issue("bad-control-view", str(exc)))
            print(json.dumps(rep.to_dict(), indent=1, ensure_ascii=False))
            if not rep.ok and prof.name == "ieee-single":
                wide = get_profile("double")
                wide_report = check(layout, wide)
                try:
                    prepare_control_view(layout, wide, wide_report.scale, Path(a.layout).resolve().parent)
                except ValueError:
                    pass
                else:
                    if wide_report.ok:
                        print("提示：这张表单栏校验不通过，但双栏通过；可加 --profile double 重新校验。原图未修改。", file=sys.stderr)
            return 0 if rep.ok else 1
        if a.cmd == "waveforms":
            from .waveforms import WaveStore, export_waveforms
            path = Path(a.layout).resolve()
            layout = Layout.load(path)
            cfg = layout.extra.get("waveform")
            if not isinstance(cfg, dict) or not cfg.get("source"):
                raise ValueError("这张表没有已保存的波形数据；请先在编辑器“模态与波形 → 波形与模态”导入 CSV，核对曲线和模态对应关系并保存，再运行 waveforms。")
            result = export_waveforms(layout, WaveStore(path), Path(a.out),
                                      a.name or path.stem.removesuffix(".layout"), a.profile,
                                      a.formats, not a.wave_only, xy_only=a.xy_only)
            for file in result["files"]:
                print(file)
            return 0
        layout = (from_template(a.template, " ".join(a.options)) if a.cmd == "draw"
                  else Layout.load(a.layout))
        name = a.name or (None if a.cmd == "draw" else Path(a.layout).stem.removesuffix(".layout"))
        res = render(layout, a.profile, a.out, name, a.formats, a.grid, a.mono,
                     base_dir=None if a.cmd == "draw" else Path(a.layout).resolve().parent)
        for w in res.report.issues:
            print(w, file=sys.stderr)
        for path in res.files.values():
            print(path)
        return 0
    except LayoutRejected as exc:
        print(exc, file=sys.stderr)
        if a.cmd in ("draw", "render", *SIMULATION_COMMANDS) and get_profile(a.profile).name != "ieee-double":
            shown = layout if a.cmd in ("draw", "render") else Layout.load(a.layout)
            if check(shown, get_profile("ieee-double")).ok:
                print("提示：这张表双栏校验通过，可加 --profile double 出图；原图未修改。", file=sys.stderr)
        return 1
    except (OptionError, KeyError, CompileError, templates.TemplateError, ModeError, SpiceError, ValueError, OSError) as exc:
        message = exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)
        print(f"error: {message}", file=sys.stderr)
        return 2


def _modes(a: argparse.Namespace) -> int:
    from .pipeline import render_modes
    if a.arrows is not None and a.arrows < 0:
        raise ValueError("--arrows 要填非负整数，0 表示不添加自动电流箭头")
    src = Path(a.template)
    if src.suffix.lower() == ".json" and src.exists():
        layout = Layout.load(src)
    else:
        layout = from_template(a.template, " ".join(a.options))
    modes = None
    if a.mode_file:
        raw = json.loads(Path(a.mode_file).read_text(encoding="utf-8"))
        raw = raw.get("modes", []) if isinstance(raw, dict) else raw
        modes = [Mode.from_dict(d) for d in raw]
    if a.list:
        if not (modes or layout.modes):
            print("这张图没有模态；可在编辑器“模态与波形 → 波形与模态”里新增，或用 --modes file.json 给出", file=sys.stderr)
            return 1
        for i, m in enumerate(modes or [Mode.from_dict(d) for d in layout.modes], 1):
            print(f"{i}. {m.name}  {m.caption}" + (f"  [{m.condition}]" if m.condition else ""))
            if m.on is not None:
                print(f"   on: {', '.join(m.on) or '-'}")
            for loop in m.loops:
                print(f"   loop: {' -> '.join(loop)}")
        return 0
    style = Style.mono() if a.mono else Style()
    if a.color is not None:
        style.color = a.color
    if a.off:
        style.off = a.off
    if a.arrows is not None:
        style.arrows = a.arrows
    only = None
    if a.only:
        count = len(modes or layout.modes)
        try:
            only = [int(x) for x in a.only.split(",") if x.strip()]
        except ValueError:
            raise ValueError(f"--only 只能写模态编号，用逗号分隔，例如 1,3（共 {count} 个模态）") from None
        if not only or any(not 1 <= n <= count for n in only):
            raise ValueError(f"--only 的编号要在 1～{count} 之间（用 --list 查看全部模态）")
    results = render_modes(layout, a.profile, a.out, a.name, a.formats, style, modes, only,
                           not a.no_sheet, base_dir=src.resolve().parent if src.suffix.lower() == ".json" else None)
    print("checked: loop connections and device directions only; bias and operating "
          "conditions are not checked", file=sys.stderr)
    for r in results:
        for n in r.notes:
            print(f"note: {n}", file=sys.stderr)
        print(f"{r.mode.name}: " + "  ".join(str(p) for p in r.files.values()))
    return 0


def _new(a: argparse.Namespace) -> int:
    import base64
    from .ai import ModelError, generate, plan, provider_info
    images = []
    for path in a.image:
        try:
            data = Path(path).read_bytes()
        except OSError as exc:
            raise ValueError(f"无法读取参考图 {path}：{exc}") from None
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            mime = "image/png"
        elif data.startswith(b"\xff\xd8\xff"):
            mime = "image/jpeg"
        elif data.startswith((b"GIF87a", b"GIF89a")):
            mime = "image/gif"
        elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            mime = "image/webp"
        else:
            raise ValueError(f"参考图 {path} 的文件头不是 PNG / JPEG / GIF / WebP；请重新导出图片，不能只改扩展名。")
        images.append((mime, base64.b64encode(data).decode()))
    try:
        structure = Path(a.structure).read_text(encoding="utf-8") if a.structure else None
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"无法读取结构文件 {a.structure}（需要 UTF-8 文本）：{exc}") from None
    hit = templates.match(a.description)
    if hit and not a.anyway:
        print(f"已有手画模板 {hit}（{templates.get(hit).TITLE}），是标准画法，不需要调用模型：\n"
              f"  circuit-drawer draw {hit}\n  circuit-drawer edit x.json -t {hit}\n"
              f"仍要让模型画，加 --anyway。", file=sys.stderr)
        return 0
    info = provider_info(a.provider, a.model)
    if info.get("problem"):
        print(f"error: {info['problem']}", file=sys.stderr)
        return 2
    print(f"模型：{info['provider']} / {info['model']} @ {info['base_url']}，"
          f"单次请求最长等待 {info['timeout_s']:.0f} 秒（CIRCUIT_DRAWER_TIMEOUT）",
          file=sys.stderr)
    try:
        if a.plan:
            text = plan(a.description, a.profile, model=a.model, provider=a.provider,
                        images=images or None)
            Path(a.out).write_text(text + "\n", encoding="utf-8")
            print(text)
            print(f"\n核对并修改 {a.out} 后：circuit-drawer new \"{a.description}\" "
                  f"--structure {a.out} -o figure.json", file=sys.stderr)
            return 0
        gen = generate(a.description, a.profile, a.rounds, model=a.model,
                       provider=a.provider, structure=structure, images=images or None,
                       log=lambda m: print(m, file=sys.stderr), review=a.review)
    except ModelError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    best = gen.best
    if best is None:
        print("error: 模型没有给出可读的坐标表", file=sys.stderr)
        return 1
    best.save(a.out)
    print(a.out)
    for note in gen.notes:
        print(f"布局：{note}", file=sys.stderr)
    profile = a.profile
    if gen.passes_double and not gen.ok:
        print(f"单栏放不下，但双栏校验通过，已停止返工：circuit-drawer render {a.out} -p double",
              file=sys.stderr)
        profile = "ieee-double"
    elif gen.ok:
        print(f"校验通过（{len(gen.attempts)} 轮）。出图：circuit-drawer render {a.out}；"
              f"存为模板：circuit-drawer save-template {a.out} <名字>", file=sys.stderr)
    else:
        print(f"{len(gen.attempts)} 轮后仍有问题，已保存问题最少的一版：", file=sys.stderr)
        for e in gen.best_attempt.errors:
            print(f"  {e}", file=sys.stderr)
        print(f"在编辑器里改完即可：circuit-drawer edit {a.out}", file=sys.stderr)
    if a.edit:
        from .editor.server import serve
        serve(Path(a.out), profile, a.port, provider=a.provider, model=a.model)
    return 0 if gen.ok or gen.passes_double else 1


def _tweak(a: argparse.Namespace) -> int:
    from .ai import ModelError, changes, generate, provider_info
    src = Path(a.layout)
    old = Layout.load(src)
    out = Path(a.out) if a.out else src.with_name(f"{src.stem}_ai.json")
    if out.resolve() == src.resolve():
        print("error: -o 不能和原文件相同；原文件保持不动，结果另存", file=sys.stderr)
        return 2
    info = provider_info(a.provider, a.model)
    if info.get("problem"):
        print(f"error: {info['problem']}", file=sys.stderr)
        return 2
    print(f"模型：{info['provider']} / {info['model']} @ {info['base_url']}", file=sys.stderr)
    try:
        gen = generate(a.instruction, a.profile, a.rounds, model=a.model, provider=a.provider,
                       current=old.to_dict(), log=lambda m: print(m, file=sys.stderr),
                       review=a.review, base_dir=src.resolve().parent)
    except ModelError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    best = gen.best
    if best is None:
        print("error: 模型没有给出可读的坐标表", file=sys.stderr)
        return 1
    best.save(out)
    print(out)
    note = gen.best_attempt.note if gen.best_attempt else ""
    if note:
        print(note, file=sys.stderr)
    for note in gen.notes:
        print(note, file=sys.stderr)
    print("实际改动：", file=sys.stderr)
    for c in changes(old, best):
        print(f"  {c}", file=sys.stderr)
    if not gen.ok:
        print("仍有问题：", file=sys.stderr)
        for e in gen.best_attempt.errors:
            print(f"  {e}", file=sys.stderr)
    if a.edit:
        from .editor.server import serve
        serve(out, a.profile, a.port, provider=a.provider, model=a.model)
    return 0 if gen.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
