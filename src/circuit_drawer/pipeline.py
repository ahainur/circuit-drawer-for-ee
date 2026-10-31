"""Options -> coordinate table -> validation -> CircuitikZ -> files."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import templates
from .compile import FORMATS, build, build_sheet
from .emit import emit
from .layout import Layout
from .options import parse
from .profiles import get_profile
from .validate import Report, check
from .modes import Mode, ModeError, Paint, Style, WireGraph, modes_of, solve


class LayoutRejected(ValueError):
    def __init__(self, report: Report):
        self.report = report
        super().__init__("layout rejected:\n" + "\n".join(map(str, report.errors)))


@dataclass
class Result:
    layout: Layout
    report: Report
    tex: str
    files: dict[str, Path] = field(default_factory=dict)


def from_template(name: str, text: str | None = None, **given) -> Layout:
    tpl = templates.get(name)
    return tpl.build(parse(text, tpl.OPTIONS, given))


def render(layout: Layout, profile: str = "ieee-single",
           out_dir: str | Path | None = None, stem: str | None = None,
           formats: tuple[str, ...] = FORMATS, grid: bool = False,
           mono: bool = False) -> Result:
    """Validate, then emit and (optionally) compile.  Raises LayoutRejected
    instead of drawing anything when the table breaks a hard rule.  ``mono``
    draws background regions and callouts in greys."""
    prof = get_profile(profile)
    report = check(layout, prof)
    if not report.ok:
        raise LayoutRejected(report)
    from .regions import transparency_notes
    from .validate import Issue
    report.issues.extend(Issue("transparency-export", note, "note") for note in transparency_notes(layout))
    tex = emit(layout, prof, report, grid, mono=mono)
    res = Result(layout, report, tex)
    if out_dir is not None:
        res.files = build(tex, Path(out_dir), stem or layout.name, formats)
    return res


@dataclass
class ModeResult:
    mode: Mode
    tex: str
    files: dict[str, Path] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def render_modes(layout: Layout, profile: str = "ieee-single",
                 out_dir: str | Path | None = None, stem: str | None = None,
                 formats: tuple[str, ...] = FORMATS, style: Style | None = None,
                 modes: list[Mode] | None = None, only: list[int] | None = None,
                 sheet: bool = True, node_labels: bool = False) -> list[ModeResult]:
    """One figure per operating mode, all from the same validated table.
    ``modes`` defaults to the table's own; ``only`` picks some (1-based).
    With ``sheet`` and files being made, ``<stem>_modes`` puts them all on
    one page with (a), (b), ... captions; it is returned as the last result."""
    if "modules" in layout.extra:
        raise ModeError("模块初版尚不支持跨模块模态高亮；请在普通电路中编辑模态")
    prof = get_profile(profile)
    report = check(layout, prof)
    if not report.ok:
        raise LayoutRejected(report)
    modes = modes if modes is not None else modes_of(layout)
    if not modes:
        raise ModeError(f"{layout.name} has no operating modes; add a 'modes' list to the "
                        f"table or pass --modes file.json")
    style = style or Style()
    graph = WireGraph(layout)
    stem = stem or layout.name
    out = []
    want = formats
    sheet = sheet and out_dir is not None and bool(set(formats) - {"tex"})
    if sheet:
        want = tuple(dict.fromkeys(formats + ("pdf",)))
    for i, mode in enumerate(modes, 1):
        if only and i not in only:
            continue
        lit = solve(layout, mode, report, graph, prof, style.arrows)
        tex = emit(layout, prof, report, paint=Paint(layout, lit, graph, prof, style), node_labels=node_labels)
        from .regions import transparency_notes
        res = ModeResult(mode, tex, notes=lit.problems + transparency_notes(layout))
        if out_dir is not None:
            res.files = build(tex, Path(out_dir), f"{stem}_mode{i}", want)
        out.append(res)
    if sheet and out:
        pdfs = [r.files["pdf"] for r in out]
        caps = [r.mode.caption or r.mode.name for r in out]
        cols = 2 if prof.name == "ieee-single" else 1
        files = build_sheet(pdfs, caps, Path(out_dir), f"{stem}_modes",
                            tuple(f for f in formats if f != "tex"), cols, prof.font_pt)
        if "pdf" not in formats:
            for r in out:
                r.files.pop("pdf").unlink()
        out.append(ModeResult(Mode("sheet", "all modes"), "", files))
    return out
