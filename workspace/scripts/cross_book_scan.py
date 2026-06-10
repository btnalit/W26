#!/usr/bin/env python3
"""
cross_book_scan.py — workspace shim, forwards to canonical skill version.

The canonical cross_book_scan lives at:
  profile/skills/odds-analysis/scripts/cross_book_scan.py

Deployment copies the canonical version to both:
  /skills/odds-analysis/scripts/cross_book_scan.py
  /hermesdata/worldcup-2026-handicap/scripts/cross_book_scan.py

This file exists only for backward compatibility during the v1→v2 migration.
Do NOT add logic here — modify the canonical version instead.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_CANONICAL = Path(__file__).resolve().parent.parent.parent / "profile" / "skills" / "odds-analysis" / "scripts" / "cross_book_scan.py"
if not _CANONICAL.exists():
    # Production fallback: deployed path
    _CANONICAL = Path("/skills/odds-analysis/scripts/cross_book_scan.py")

spec = importlib.util.spec_from_file_location("cross_book_scan_canonical", str(_CANONICAL))
assert spec is not None
assert spec.loader is not None
_canonical = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_canonical)

# Re-export all public names
__all__ = [name for name in dir(_canonical) if not name.startswith("_")]

# Re-export CLI entry point
if __name__ == "__main__":
    sys.exit(_canonical.main() if hasattr(_canonical, "main") else 1)
