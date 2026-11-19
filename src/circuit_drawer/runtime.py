"""Find external tools in explicit settings, the Windows bundle, or PATH."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys

# Bundled MiKTeX tools run offline using the pinned DLLs and portable configuration.
POPPLER_OFFLINE_FLAGS = ("--miktex-disable-installer", "--miktex-disable-maintenance", "--miktex-disable-diagnose")

_TOOLS = {
    "pdflatex": ("tex/bin/windows", ("pdflatex",)),
    "pdftocairo": ("poppler/bin", ("pdftocairo",)),
    "pdffonts": ("poppler/bin", ("pdffonts",)),
    "pdfinfo": ("poppler/bin", ("pdfinfo",)),
    "ngspice": ("ngspice/bin", ("ngspice_con", "ngspice")),
}


def runtime_root() -> Path | None:
    override = os.environ.get("CIRCUIT_DRAWER_RUNTIME")
    if override:
        return Path(override).resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "runtime"
    return None


def find_tool(name: str, explicit: str | Path | None = None) -> str | None:
    """An invalid explicit setting stays invalid so a typo cannot switch engines."""
    if explicit:
        found = shutil.which(str(explicit))
        return str(Path(found).absolute()) if found else None
    directory, names = _TOOLS[name]
    root = runtime_root()
    candidates = [str(root / directory / f"{item}.exe") for item in names] if root else []
    for candidate in [*candidates, *names]:
        found = shutil.which(candidate)
        if found:
            # TeX Live selects the format from the executable's symlink name.
            return str(Path(found).absolute())
    return None


def latex_environment(executable: str) -> dict[str, str] | None:
    """Keep bundled TeX caches writable and independent of host TeX settings."""
    root = runtime_root()
    if root is None or Path(executable).resolve() != (root / "tex/bin/windows/pdflatex.exe").resolve():
        return None
    cache = (Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".config")))
             / "CircuitDrawer" / "runtime-cache" / "tex")
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(("TEX", "MIKTEX", "MKTEX", "KPSE"))}
    for variable, directory in (("TEXMFVAR", "var"), ("TEXMFCONFIG", "config"), ("TEXMFHOME", "home")):
        target = cache / directory
        target.mkdir(parents=True, exist_ok=True)
        env[variable] = target.as_posix()
    # Formats, font metrics and bitmaps are supplied by the offline bundle.
    env.update(MKTEXFMT="0", MKTEXTFM="0", MKTEXPK="0")
    return env


def poppler_environment(executable: str) -> dict[str, str] | None:
    """Isolate the bundled MiKTeX converter from host configuration and caches."""
    root = runtime_root()
    if root is None or Path(executable).resolve().parent != (root / "poppler/bin").resolve():
        return None
    cache = (Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".config")))
             / "CircuitDrawer" / "runtime-cache" / "poppler")
    cache.mkdir(parents=True, exist_ok=True)
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(("TEX", "MIKTEX", "MKTEX", "KPSE"))}
    startup = str(root / "poppler/miktex/config/miktexstartup.ini")
    env.update(MIKTEX_COMMONSTARTUPFILE=startup, MIKTEX_USERSTARTUPFILE=startup,
               MIKTEX_COMMONINSTALL=str(root / "poppler"), MIKTEX_USERINSTALL=str(root / "poppler"),
               MIKTEX_COMMONCONFIG=str(cache), MIKTEX_USERCONFIG=str(cache),
               MIKTEX_COMMONDATA=str(cache), MIKTEX_USERDATA=str(cache))
    return env


def ngspice_environment(executable: str) -> dict[str, str] | None:
    """Keep ngspice's narrow-character temporary filename relative to its run directory."""
    root = runtime_root()
    if root is None or Path(executable).resolve().parent != (root / "ngspice/bin").resolve():
        return None
    env = dict(os.environ)
    # ngspice 47 falls back to HOME/USERPROFILE when the Windows CRT tmpfile fails.
    env.pop("HOME", None)
    env["USERPROFILE"] = "."
    return env
