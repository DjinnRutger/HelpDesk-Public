"""
Simple builder for a single-file (one EXE) PyInstaller distribution.

Usage (from project root, ideally in your venv):
        python build.py

This will:
    - Ask you to manually delete ./build and ./dist (if present)
    - Run PyInstaller in --onefile mode
    - Output single executable at: ./dist/HelpfulDjinn.exe

Note: The first launch of a one-file build extracts bundled files to a temp
folder (PyInstaller's _MEIPASS). All included templates/static/images will
still be available to Flask.
"""

from __future__ import annotations

import os
import importlib.util
import subprocess
import sys
from pathlib import Path


def ensure_pyinstaller_available() -> None:
    if importlib.util.find_spec("PyInstaller") is None:
        print("PyInstaller is not installed in this Python environment.")
        print("Install it and re-run: pip install pyinstaller")
        sys.exit(1)


def main() -> None:
    repo_root = Path(__file__).resolve().parent
    os.chdir(repo_root)

    # 1) Ask user to manually delete previous outputs to avoid accidental data loss
    print("Before continuing, please manually delete the 'build' and 'dist' folders if they exist.")
    print(f"Project root: {repo_root}")
    print("Press Enter to continue once you've removed them, or Ctrl+C to cancel...")
    input()

    # 2) Ensure PyInstaller is present
    ensure_pyinstaller_available()

    # 3) Build new single-file dist
    # Note: On Windows, --add-data uses ';' as the separator for src;dest
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--clean",
        "--name",
        "HelpfulDjinn",
        "run.py",
        "--add-data",
        r"app\templates;app\templates",
        "--add-data",
        r"app\static;app\static",
        "--add-data",
        r"app\images;app\images",
        "--add-data",
        r"app\version.txt;app",
    ]

    print("Running:")
    print(" ".join(cmd))

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print("PyInstaller build failed with a non-zero exit code.")
        sys.exit(e.returncode)

    exe_path = repo_root / "dist" / "HelpfulDjinn.exe"
    print("\nBuild complete.")
    print(f"Executable: {exe_path}")
    print("Distribute just this single HelpfulDjinn.exe (plus any docs you want).")


if __name__ == "__main__":
    main()
