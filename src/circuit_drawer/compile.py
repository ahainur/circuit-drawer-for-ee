"""Run pdflatex and convert the PDF to SVG and PNG."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from .runtime import find_tool, latex_environment, poppler_environment

FORMATS = ("tex", "pdf", "svg", "png")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class CompileError(RuntimeError):
    pass


def have_latex() -> bool:
    return find_tool("pdflatex") is not None


def _compile(tex_file: Path) -> Path:
    executable = find_tool("pdflatex")
    if executable is None:
        raise CompileError("pdflatex not found; install TeX Live with circuitikz")
    run = subprocess.run([executable, "-interaction=nonstopmode", "-halt-on-error", tex_file.name],
                         cwd=tex_file.parent, env=latex_environment(executable), capture_output=True,
                         text=True, errors="replace", creationflags=_NO_WINDOW)
    if run.returncode != 0:
        log_file = tex_file.with_suffix(".log")
        log = log_file.read_text(encoding="utf-8", errors="replace") if log_file.exists() else run.stdout
        errors = [line for line in log.splitlines() if line.startswith("!")]
        raise CompileError("pdflatex failed: " + "; ".join(errors[:5] or [(log + run.stderr)[-800:]]))
    return tex_file.with_suffix(".pdf")


def convert_pdf(pdf: Path, target: Path, png_dpi: int = 600) -> None:
    """Poppler on Windows needs ASCII arguments even when the working directory is Unicode."""
    if target.suffix not in (".svg", ".png"):
        raise ValueError("PDF conversion requires an .svg or .png target")
    executable = find_tool("pdftocairo")
    if executable is None:
        raise CompileError("pdftocairo not found (install poppler-utils)")
    env = poppler_environment(executable)
    # The offline MiKTeX subset includes the converter and its DLLs, without maintenance tools.
    flags = ("--miktex-disable-installer", "--miktex-disable-maintenance", "--miktex-disable-diagnose") if env else ()
    with tempfile.TemporaryDirectory() as tmp:
        tmpd = Path(tmp)
        shutil.copyfile(pdf, tmpd / "input.pdf")
        args = ["-svg", "input.pdf", "output.svg"] if target.suffix == ".svg" else [
            "-png", "-singlefile", "-r", str(png_dpi), "input.pdf", "output"]
        run = subprocess.run([executable, *flags, *args], cwd=tmpd, env=env, capture_output=True,
                             creationflags=_NO_WINDOW)
        if run.returncode != 0:
            raise CompileError("pdftocairo failed: " + run.stderr.decode(errors="replace")[-800:])
        shutil.copyfile(tmpd / f"output{target.suffix}", target)


def _export_pdf(pdf: Path, out_dir: Path, stem: str,
                formats: tuple[str, ...], png_dpi: int) -> dict[str, Path]:
    staged = {}
    for fmt in ("pdf", "svg", "png"):
        if fmt in formats:
            staged[fmt] = pdf.with_suffix(f".{fmt}")
            if fmt != "pdf":
                convert_pdf(pdf, staged[fmt], png_dpi)
    made = {}
    for fmt, source in staged.items():
        target = out_dir / f"{stem}.{fmt}"
        shutil.copyfile(source, target)
        made[fmt] = target
    return made


def build(tex: str, out_dir: Path, stem: str,
          formats: tuple[str, ...] = FORMATS, png_dpi: int = 600) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    made: dict[str, Path] = {}
    if "tex" in formats:
        tex_path = out_dir / f"{stem}.tex"
        tex_path.write_text(tex, encoding="utf-8")
        made["tex"] = tex_path
    if not set(formats) - {"tex"}:
        return made
    with tempfile.TemporaryDirectory() as tmp:
        tmpd = Path(tmp)
        (tmpd / "fig.tex").write_text(tex, encoding="utf-8")
        pdf = _compile(tmpd / "fig.tex")
        made.update(_export_pdf(pdf, out_dir, stem, formats, png_dpi))
    return made


def _panel_label(index: int) -> str:
    label = ""
    number = index + 1
    while number:
        number, remainder = divmod(number - 1, 26)
        label = chr(ord("a") + remainder) + label
    return label


def build_sheet(pdfs: list[Path], captions: list[str], out_dir: Path, stem: str,
                formats: tuple[str, ...] = FORMATS, cols: int = 2, font_pt: float = 8.0,
                png_dpi: int = 300) -> dict[str, Path]:
    """All mode figures on one page, (a), (b), ... captions under each:
    an overview to check a set at a glance, or a figure* for a paper."""
    names = [f"m{i}.pdf" for i in range(len(pdfs))]
    cells = []
    for i, (name, cap) in enumerate(zip(names, captions)):
        cells.append(r"\begin{tabular}[t]{@{}c@{}}\includegraphics{" + name + r"}\\[2pt]"
                     + f"({_panel_label(i)}) {cap}" + r"\end{tabular}")
    rows = [" & ".join(cells[i:i + cols]) for i in range(0, len(cells), cols)]
    tex = (r"\documentclass[border=2pt]{standalone}" "\n"
           r"\usepackage{mathptmx}\usepackage{graphicx}" "\n"
           r"\begin{document}" "\n"
           rf"\fontsize{{{font_pt:g}}}{{{font_pt * 1.2:g}}}\selectfont" "\n"
           rf"\begin{{tabular}}{{@{{}}{'c' * cols}@{{}}}}" "\n"
           + " \\\\[6pt]\n".join(rows) + "\n"
           r"\end{tabular}" "\n" r"\end{document}" "\n")
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmpd = Path(tmp)
        for src, name in zip(pdfs, names):
            shutil.copy(src, tmpd / name)
        (tmpd / "sheet.tex").write_text(tex, encoding="utf-8")
        pdf = _compile(tmpd / "sheet.tex")
        return _export_pdf(pdf, out_dir, stem, formats, png_dpi)
