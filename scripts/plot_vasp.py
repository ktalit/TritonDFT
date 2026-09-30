#!/usr/bin/env python3
"""Command-line entry point for the standalone VASP plotter."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vasp_plotter import main


if __name__ == "__main__":
    raise SystemExit(main())
