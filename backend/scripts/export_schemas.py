"""Write the API contract out for clients that generate code from it.

Usage: python scripts/export_schemas.py [--check]

- docs/reference/schemas/ws/*.schema.json — the live WebSocket protocol
  (app.protocol), one document per direction.
- docs/reference/openapi.json — the REST API, as FastAPI describes it.

The phone app's repository generates its models from these. --check exits
non-zero when a file is out of date instead of writing it
(tests/test_client_protocol.py runs the same comparison).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.main import create_app  # noqa: E402
from app.protocol import json_schemas  # noqa: E402

REFERENCE = ROOT.parent / "docs" / "reference"


def documents() -> dict[Path, str]:
    out = {
        REFERENCE / "schemas" / "ws" / f"{name}.schema.json": doc
        for name, doc in json_schemas().items()
    }
    out[REFERENCE / "openapi.json"] = create_app().openapi()
    return {path: json.dumps(doc, indent=2, sort_keys=True) + "\n" for path, doc in out.items()}


def main() -> int:
    stale = []
    for path, text in documents().items():
        current = path.read_text() if path.exists() else ""
        if current == text:
            continue
        if "--check" in sys.argv:
            stale.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        print(f"wrote {path}")
    for path in stale:
        print(f"{path} is out of date: run python scripts/export_schemas.py")
    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
