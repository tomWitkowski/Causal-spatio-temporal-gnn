#!/usr/bin/env python3
"""Thin wrapper around the kozy_data CLI: fetch all enabled sources.

Usage:
    python scripts/fetch_all.py --since 2020-01-01
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kozy_data.__main__ import main  # noqa: E402

if __name__ == "__main__":
    argv = ["fetch", "all"] + sys.argv[1:]
    raise SystemExit(main(argv))
