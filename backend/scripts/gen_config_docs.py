"""Regenerate the settings tables in docs/getting-started/configuration.md.

Usage: python scripts/gen_config_docs.py [--check]

The tables come from app.settings_registry, the same entries Settings ›
Advanced shows, so the documented defaults are the shipped ones by
construction. --check exits non-zero when the file is out of date instead of
writing it (tests/test_settings_registry.py runs the same comparison).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.settings_registry import splice_docs  # noqa: E402

DOC = ROOT.parent / "docs" / "getting-started" / "configuration.md"


def main() -> int:
    current = DOC.read_text()
    wanted = splice_docs(current)
    if "--check" in sys.argv:
        if wanted != current:
            print(f"{DOC} is out of date: run python scripts/gen_config_docs.py")
            return 1
        return 0
    DOC.write_text(wanted)
    print(f"wrote {DOC}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
