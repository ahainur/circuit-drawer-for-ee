"""circuit-drawer-control: list / export / check / render / edit / save-template control block
diagrams, loop / bode / design for loop analysis on a diagram's ``loop`` section, and code for
discretised fixed- or floating-point C from its ``code`` section."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..compile import FORMATS, CompileError
from ..profiles import PROFILES, get_profile
from .check import check
from .library import LibraryError, save_template
from .codegen import CodeError
from .loop import LoopError, analyse, design
from .render import TEMPLATES, Rejected, available, bode, load, render, template

_SI = {"G": 1e9, "M": 1e6, "k": 1e3, "m": 1e-3, "u": 1e-6, "n": 1e-9, "p": 1e-12}


def _si(text: str) -> float:
    """A number with an optional SI suffix: 30k, 4.7u, 1e3."""
    t = text.strip().removesuffix("Hz").removesuffix("hz")
    try:
        return float(t[:-1]) * _SI[t[-1]] if t and t[-1] in _SI else float(t)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number such as 30k or 4.7e3") from None


def _formats(text: str) -> tuple[str, ...]:
    fmts = tuple(f.strip() for f in text.split(",") if f.strip())
    bad = [f for f in fmts if f not in FORMATS]
    if bad:
        raise argparse.ArgumentTypeError(f"unknown formats {bad}; use {FORMATS}")
    return fmts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="circuit-drawer-control", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list the example control diagrams")
    e = sub.add_parser("export", help="write an example table as JSON to edit")
    e.add_argument("template")
    e.add_argument("--out", "-o", required=True)
    c = sub.add_parser("check", help="validate a control diagram table")
    c.add_argument("source", help="JSON file or example name")
    c.add_argument("--profile", "-p", default=None,
                   help=f"{sorted(PROFILES)} or single/double (default: the table's, else double)")
    r = sub.add_parser("render", help="validate and draw a control diagram")
    r.add_argument("source", help="JSON file or example name")
    r.add_argument("--profile", "-p", default=None)
    r.add_argument("--out", "-o", default="out")
    r.add_argument("--formats", "-f", type=_formats, default=FORMATS)
    r.add_argument("--name", help="output file stem (default: the table's name)")
    r.add_argument("--mono", action="store_true", help="greys only (black-and-white print)")
    r.add_argument("--grid", action="store_true", help="coordinate grid, for editing only")
    ed = sub.add_parser("edit", help="open the control diagram editor in the browser")
    ed.add_argument("table", help="JSON file; created from --template if missing")
    ed.add_argument("--template", "-t", help="example or saved template to start from")
    ed.add_argument("--profile", "-p", default=None)
    ed.add_argument("--port", type=int, default=8766)
    ed.add_argument("--out", "-o", help="export directory (default: next to the JSON)")
    ed.add_argument("--no-browser", action="store_true")
    st = sub.add_parser("save-template", help="save a table that passes the checker as a template")
    st.add_argument("table")
    st.add_argument("name", help="lowercase letters, digits and _")
    st.add_argument("--title", default="")
    st.add_argument("--profile", "-p", default=None)
    st.add_argument("--force", action="store_true", help="overwrite a saved template")
    lp = sub.add_parser("loop", help="loop gains, margins and closed-loop poles from the loop section")
    lp.add_argument("source", help="JSON file or example name")
    bp = sub.add_parser("bode", help="Bode plot of the loop gains and closed-loop responses")
    bp.add_argument("source", help="JSON file or example name")
    bp.add_argument("--profile", "-p", default="ieee-single",
                    help="plot width (default: single column)")
    bp.add_argument("--out", "-o", default="out")
    bp.add_argument("--formats", "-f", type=_formats, default=FORMATS)
    bp.add_argument("--name", help="output file stem (default: <name>_bode)")
    bp.add_argument("--mono", action="store_true", help="black traces told apart by dashes")
    dp = sub.add_parser("design", help="suggest a Type I/II/III compensator (k-factor method)")
    dp.add_argument("source", help="JSON file or example name")
    dp.add_argument("--block", "-b", required=True, help="the compensator block's id")
    dp.add_argument("--fc", type=_si, required=True, help="crossover frequency in Hz, e.g. 30k")
    dp.add_argument("--pm", type=float, default=60.0, help="phase margin in degrees (default 60)")
    dp.add_argument("--type", type=int, choices=(1, 2, 3), help="force the compensator type")
    dp.add_argument("--break", dest="cut", help="where to cut the loop (default: first of loop.breaks)")
    dp.add_argument("--r1", type=_si, default=10e3, help="op-amp input resistor in ohm (default 10k)")
    dp.add_argument("--write", "-w", help="write the table with the new tf to this JSON file")
    cp = sub.add_parser("code", help="discretise the controller and write embedded C")
    cp.add_argument("source", help="JSON file or example name")
    cp.add_argument("--out", "-o", default="out")
    cp.add_argument("--arith", choices=("fixed", "float", "double"),
                    help="override code.arith")
    cp.add_argument("--fs", type=_si, help="override code.fs (Hz), e.g. 100k")
    cp.add_argument("--method", choices=("tustin", "zoh", "backward", "forward"),
                    help="override code.method")
    cp.add_argument("--q", type=int, help="override code.q (fraction bits of a signal)")
    cp.add_argument("--harness", action="store_true",
                    help="also write <name>_test.c, a PC program that runs stdin samples")
    a = ap.parse_args(argv)
    from ..cli import _table_kind
    given = getattr(a, "table", None) or getattr(a, "source", None)
    if _table_kind(given) == "circuit":
        print(f"error: {given} 是电路坐标表，请用 circuit-drawer {a.cmd} 打开或处理", file=sys.stderr)
        return 2
    try:
        if a.cmd == "list":
            for name, title in available().items():
                print(f"{name}: {title}")
            return 0
        if a.cmd == "export":
            Path(a.out).write_text(json.dumps(template(a.template), indent=1, ensure_ascii=False)
                                   + "\n", encoding="utf-8")
            print(a.out)
            return 0
        if a.cmd == "edit":
            from .editor import serve
            path = Path(a.table)
            if not path.exists():
                if not a.template:
                    print(f"error: {path} does not exist; pass --template to start from one",
                          file=sys.stderr)
                    return 2
                path.write_text(json.dumps(template(a.template), indent=1, ensure_ascii=False)
                                + "\n", encoding="utf-8")
            serve(path, a.profile, a.port, Path(a.out) if a.out else None, not a.no_browser)
            return 0
        if a.cmd == "save-template":
            path = save_template(load(a.table), a.name, a.title, a.profile,
                                 reserved=set(TEMPLATES), force=a.force)
            print(f"saved control template {a.name.lower()} -> {path}")
            return 0
        raw = load(a.source)
        if a.cmd in ("loop", "design"):
            rep = check(raw, get_profile(str(raw.get("profile") or "ieee-double")))
            if not rep.ok:
                raise Rejected(rep)
            if a.cmd == "loop":
                res = analyse(rep)
                print(json.dumps(res, indent=1, ensure_ascii=False))
                return 0 if res["stable"] else 1
            res = design(rep, a.block, a.fc, a.pm, a.type, a.cut, a.r1)
            print(json.dumps(res, indent=1, ensure_ascii=False))
            if a.write:
                old = raw["loop"].get("tf", {}).get(a.block)
                keep = {k: v for k, v in old.items() if k in ("delay", "port")} \
                    if isinstance(old, dict) else {}
                raw["loop"].setdefault("tf", {})[a.block] = {**res["tf"], **keep}
                Path(a.write).write_text(json.dumps(raw, indent=1, ensure_ascii=False) + "\n",
                                         encoding="utf-8")
                print(a.write)
            return 0
        if a.cmd == "code":
            from .codegen import write_code
            for key in ("arith", "fs", "method", "q"):
                if getattr(a, key) is not None:
                    raw.setdefault("code", {})[key] = getattr(a, key)
            res = write_code(raw, Path(a.out), a.harness)
            for w in res["warnings"]:
                print(f"warning: {w}", file=sys.stderr)
            for path in res["files"]:
                print(path)
            return 0
        if a.cmd == "bode":
            res = bode(raw, a.profile, a.out, a.name, a.formats, a.mono)
            for path in res.files.values():
                print(path)
            return 0
        if a.cmd == "check":
            prof = get_profile(a.profile or str(raw.get("profile") or "ieee-double"))
            rep = check(raw, prof)
            print(json.dumps(rep.to_dict(), indent=1, ensure_ascii=False))
            return 0 if rep.ok else 1
        res = render(raw, a.profile, a.out, a.name, a.formats, a.mono, a.grid)
        for w in res.report.warnings:
            print(f"warning: {w}", file=sys.stderr)
        for path in res.files.values():
            print(path)
        return 0
    except Rejected as exc:
        print(exc, file=sys.stderr)
        return 1
    except (KeyError, CompileError, LibraryError, LoopError, CodeError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
