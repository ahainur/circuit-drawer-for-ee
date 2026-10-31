"""One-time setup: create a windowed launcher for this editable checkout."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig


def main():
    if os.name != "nt":
        raise SystemExit("This helper creates a Windows .exe; use circuit-drawer-launcher on other platforms.")
    project = Path(__file__).resolve().parents[1]
    # no --no-build-isolation: Python 3.12+ no longer ships setuptools, pip fetches it
    subprocess.run([sys.executable, "-m", "pip", "install", "-e", str(project), "--no-deps"], check=True)
    source = Path(sysconfig.get_path("scripts")) / "circuit-drawer-launcher.exe"
    destination = project / "Circuit Drawer.exe"
    shutil.copy2(source, destination)
    print(f"Created: {destination}")
    print("Double-click to open the launcher. It uses this Python installation and checkout.")


if __name__ == "__main__":
    main()
