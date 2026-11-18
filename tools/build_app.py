"""Build the one-folder desktop app with PyInstaller and report its size.

Usage: ``uv run --group build python tools/build_app.py``. Output: ``dist/SimpleDesign``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PyInstaller.__main__ import run

ROOT = Path(__file__).resolve().parent.parent


def folder_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def main() -> int:
    run(
        [
            str(ROOT / "packaging" / "simpledesign.spec"),
            "--noconfirm",
            "--clean",
            "--distpath",
            str(ROOT / "dist"),
            "--workpath",
            str(ROOT / "build"),
        ]
    )
    out = ROOT / "dist" / "SimpleDesign"
    print(f"{out}: {folder_size(out) / 1e6:.0f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
