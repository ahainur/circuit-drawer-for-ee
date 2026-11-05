"""Run pdflatex and convert the PDF to SVG and PNG."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

FORMATS = ("tex", "pdf", "svg", "png")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class CompileError(RuntimeError):
    pass


def have_latex() -> bool:
    return shutil.which("pdflatex") is not None


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
    if not have_latex():
        raise CompileError("pdflatex not found; install TeX Live with circuitikz")
    with tempfile.TemporaryDirectory() as tmp:
        tmpd = Path(tmp)
        (tmpd / "fig.tex").write_text(tex, encoding="utf-8")
        run = subprocess.run(["pdflatex", "-interaction=nonstopmode",
                              "-halt-on-error", "fig.tex"],
                             cwd=tmpd, capture_output=True, text=True, creationflags=_NO_WINDOW)
        if run.returncode != 0:
            log = (tmpd / "fig.log").read_text(errors="replace") \
                if (tmpd / "fig.log").exists() else run.stdout
            err = [l for l in log.splitlines() if l.startswith("!")]
            raise CompileError("pdflatex failed: " + "; ".join(err[:5] or [log[-800:]]))
        pdf = out_dir / f"{stem}.pdf"
        shutil.copy(tmpd / "fig.pdf", pdf)
    if "pdf" in formats:
        made["pdf"] = pdf
    if "svg" in formats:
        svg = out_dir / f"{stem}.svg"
        _tool(["pdftocairo", "-svg", str(pdf), str(svg)])
        made["svg"] = svg
    if "png" in formats:
        _tool(["pdftocairo", "-png", "-singlefile", "-r", str(png_dpi),
               str(pdf), str(out_dir / stem)])
        made["png"] = out_dir / f"{stem}.png"
    if "pdf" not in formats:
        pdf.unlink()
    return made


def _tool(cmd: list[str]) -> None:
    if shutil.which(cmd[0]) is None:
        raise CompileError(f"{cmd[0]} not found (install poppler-utils)")
    subprocess.run(cmd, check=True, capture_output=True, creationflags=_NO_WINDOW)


def build_sheet(pdfs: list[Path], captions: list[str], out_dir: Path, stem: str,
                formats: tuple[str, ...] = FORMATS, cols: int = 2, font_pt: float = 8.0,
                png_dpi: int = 300) -> dict[str, Path]:
    """All mode figures on one page, (a), (b), ... captions under each:
    an overview to check a set at a glance, or a figure* for a paper."""
    names = [f"m{i}.pdf" for i in range(len(pdfs))]
    cells = []
    for i, (name, cap) in enumerate(zip(names, captions)):
        cells.append(r"\begin{tabular}[t]{@{}c@{}}\includegraphics{" + name + r"}\\[2pt]"
                     + f"({chr(ord('a') + i)}) {cap}" + r"\end{tabular}")
    rows = [" & ".join(cells[i:i + cols]) for i in range(0, len(cells), cols)]
    tex = (r"\documentclass[border=2pt]{standalone}" "\n"
           r"\usepackage{mathptmx}\usepackage{graphicx}" "\n"
           r"\begin{document}" "\n"
           rf"\fontsize{{{font_pt:g}}}{{{font_pt * 1.2:g}}}\selectfont" "\n"
           rf"\begin{{tabular}}{{@{{}}{'c' * cols}@{{}}}}" "\n"
           + " \\\\[6pt]\n".join(rows) + "\n"
           r"\end{tabular}" "\n" r"\end{document}" "\n")
    out_dir.mkdir(parents=True, exist_ok=True)
    if not have_latex():
        raise CompileError("pdflatex not found; install TeX Live with circuitikz")
    with tempfile.TemporaryDirectory() as tmp:
        tmpd = Path(tmp)
        for src, name in zip(pdfs, names):
            shutil.copy(src, tmpd / name)
        (tmpd / "sheet.tex").write_text(tex, encoding="utf-8")
        run = subprocess.run(["pdflatex", "-interaction=nonstopmode", "-halt-on-error",
                              "sheet.tex"], cwd=tmpd, capture_output=True, text=True,
                             creationflags=_NO_WINDOW)
        if run.returncode != 0:
            raise CompileError("pdflatex failed on the mode sheet: " + run.stdout[-600:])
        pdf = out_dir / f"{stem}.pdf"
        shutil.copy(tmpd / "sheet.pdf", pdf)
    made: dict[str, Path] = {}
    if "pdf" in formats:
        made["pdf"] = pdf
    if "png" in formats:
        _tool(["pdftocairo", "-png", "-singlefile", "-r", str(png_dpi), str(pdf),
               str(out_dir / stem)])
        made["png"] = out_dir / f"{stem}.png"
    if "svg" in formats:
        _tool(["pdftocairo", "-svg", str(pdf), str(out_dir / f"{stem}.svg")])
        made["svg"] = out_dir / f"{stem}.svg"
    if "pdf" not in formats:
        pdf.unlink()
    return made
