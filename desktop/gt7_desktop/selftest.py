"""`--self-test`: does this build have everything the freezer could miss? (#192)

Run by CI against the frozen binary on every OS, with no data folder of the
user's touched. Prints one JSON object and exits 0 when every check passed.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any


def _decrypt() -> str:
    from app.telemetry.crypto import decrypt_packet, encrypt_packet
    from app.telemetry.packet import build_packet

    assert decrypt_packet(encrypt_packet(build_packet(packet_id=7))) is not None
    return "ok"


def _alembic() -> str:
    import alembic.command  # noqa: F401 - imported lazily by migrations
    from alembic.script import ScriptDirectory

    from app.storage.db import alembic_config

    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    assert head, "no migration head: are the revision files bundled?"
    return str(head)


def _imports() -> str:
    import aiosqlite  # noqa: F401 - loaded by SQLAlchemy through __import__
    import zeroconf._handlers.answers  # noqa: F401 - hidden behind Cython

    return "ok"


def _frontend() -> str:
    from app.config import Settings

    found = Settings().frontend_root()
    assert found is not None, "no frontend/dist in the bundle"
    return str(found)


def _https() -> str:
    import httpx

    from app.config import Settings

    # The shipped default, not the environment's: this checks the bundle's
    # TLS stack (certifi's CA file), whatever the user pointed the repo at.
    default = str(Settings.model_fields["shared_bundles_url"].default)
    url = default.rstrip("/") + "/index.json"
    response = httpx.get(url, timeout=15, follow_redirects=True)
    return f"{response.status_code}"


def _version() -> str:
    from app.version import version

    return version()


CHECKS: tuple[tuple[str, Callable[[], str]], ...] = (
    ("version", _version),
    ("decrypt", _decrypt),
    ("alembic_head", _alembic),
    ("imports", _imports),
    ("frontend", _frontend),
    ("https", _https),
)


def run(skip_network: bool = False) -> int:
    results: dict[str, Any] = {}
    ok = True
    for name, check in CHECKS:
        if skip_network and name == "https":
            continue
        started = time.monotonic()
        try:
            results[name] = {"ok": True, "value": check()}
        except Exception as exc:  # noqa: BLE001 - every check reports
            ok = False
            results[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        results[name]["ms"] = round((time.monotonic() - started) * 1000)
    print(json.dumps({"ok": ok, "checks": results}, indent=2))
    return 0 if ok else 1
