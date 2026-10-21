"""Credentials kept beside the database, not in it (#94)."""

import json
import logging
import os
import sqlite3
from contextlib import closing

import pytest
from httpx import ASGITransport, AsyncClient

from app import settings_registry as registry
from app.config import Settings
from app.main import create_app
from app.processing.cars import CarDatabase
from app.service import TelemetryService
from app.storage import secret_store
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository
from app.storage.secret_store import SecretStore, move_out_of_database

TOKEN = "gt7s_abcdefghijklmnopqrstuvwxyz0123456789"
HOOK = "https://discord.com/api/webhooks/123/secret-hook-value"

owner_only = pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")


@pytest.fixture
async def repo(tmp_path):
    engine = make_engine(tmp_path / "test.db")
    await init_db(engine)
    yield Repository(make_session_factory(engine))
    await engine.dispose()


def _old_rows(path, rows: dict[str, str]) -> None:
    """Settings rows as a version before #94 saved them, past the guard."""
    with closing(sqlite3.connect(path)) as db, db:
        db.executemany("INSERT OR REPLACE INTO settings VALUES (?, ?)", rows.items())


# --- the file ---------------------------------------------------------------


def test_values_round_trip(tmp_path) -> None:
    store = SecretStore(tmp_path)
    assert store.read() == {}
    store.set("sync_token", TOKEN)
    store.set("webhook_url", HOOK)
    assert store.read() == {"sync_token": TOKEN, "webhook_url": HOOK}
    # A cleared value is kept: it outranks the environment's.
    store.set("webhook_url", "")
    assert store.read() == {"sync_token": TOKEN, "webhook_url": ""}
    store.delete("webhook_url")
    store.delete("webhook_url")  # already gone: nothing to do
    assert store.read() == {"sync_token": TOKEN}
    assert not (tmp_path / "secrets.json.tmp").exists()


@owner_only
def test_the_file_is_owner_only(tmp_path) -> None:
    store = SecretStore(tmp_path)
    # A leftover from a crashed write, readable by anyone, is not reused.
    tmp = tmp_path / "secrets.json.tmp"
    tmp.write_text("{}")
    tmp.chmod(0o644)
    store.set("sync_token", TOKEN)
    assert store.path.stat().st_mode & 0o777 == 0o600
    # Rewriting a file that was loosened by hand tightens it again.
    store.path.chmod(0o644)
    store.set("webhook_url", HOOK)
    assert store.path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("text", [
    "not json",
    "[1, 2]",
    json.dumps({"format": "something-else", "settings": {"sync_token": TOKEN}}),
    json.dumps({"format": secret_store.FORMAT, "settings": ["sync_token"]}),
])
def test_an_unreadable_file_reads_as_empty(tmp_path, caplog, text) -> None:
    (tmp_path / "secrets.json").write_text(text)
    with caplog.at_level(logging.WARNING):
        assert SecretStore(tmp_path).read() == {}
    assert "secrets.json" in caplog.text


def test_only_the_credentials_are_read(tmp_path) -> None:
    (tmp_path / "secrets.json").write_text(json.dumps({
        "format": secret_store.FORMAT,
        "settings": {"sync_token": TOKEN, "ps_ip": "10.0.0.2", "webhook_url": 5},
    }))
    assert SecretStore(tmp_path).read() == {"sync_token": TOKEN}


async def test_the_database_refuses_a_credential(repo) -> None:
    for key in secret_store.KEYS:
        with pytest.raises(ValueError, match="secrets.json"):
            await repo.set_setting(key, "x")


# --- the upgrade -------------------------------------------------------------


async def test_upgrade_moves_credentials_out_and_leaves_no_trace(repo, tmp_path) -> None:
    db_file = tmp_path / "test.db"
    _old_rows(db_file, {"source": "sim", "sync_url": "https://sync.test"})
    # Earlier values, each a different length, then the ones in use: an
    # update that moves a value leaves the old one in the page's free space.
    for i in range(4):
        _old_rows(db_file, {
            "sync_token": f"gt7s_OLDTOKEN{i}_" + "a" * (10 + 17 * i),
            "webhook_url": f"https://discord.com/api/webhooks/OLDHOOK{i}/" + "z" * (5 + 23 * i),
        })
    _old_rows(db_file, {"sync_token": TOKEN, "webhook_url": HOOK})
    store = SecretStore(tmp_path)

    await move_out_of_database(repo, store)

    assert store.read() == {"sync_token": TOKEN, "webhook_url": HOOK}
    assert await repo.get_settings() == {"source": "sim", "sync_url": "https://sync.test"}
    raw = db_file.read_bytes()
    for trace in [TOKEN, HOOK, "OLDTOKEN", "OLDHOOK"]:
        assert trace.encode() not in raw, trace

    # The next start finds nothing to move, and the store is left alone.
    store.set("sync_token", "")
    await move_out_of_database(repo, store)
    assert store.read()["sync_token"] == ""


async def test_upgrade_takes_the_databases_value_over_the_files(repo, tmp_path) -> None:
    # A row an older version saved after the move — run again against this
    # data directory — is the newer value.
    store = SecretStore(tmp_path)
    store.update({"sync_token": "gt7s_from_before_the_downgrade_0000", "webhook_url": HOOK})
    _old_rows(tmp_path / "test.db", {"sync_token": TOKEN})
    await move_out_of_database(repo, store)
    assert store.read() == {"sync_token": TOKEN, "webhook_url": HOOK}
    assert "sync_token" not in await repo.get_settings()


async def test_a_failed_move_keeps_the_rows(repo, tmp_path, monkeypatch, caplog) -> None:
    _old_rows(tmp_path / "test.db", {"sync_token": TOKEN})
    store = SecretStore(tmp_path)

    def unwritable(_values: dict[str, str]) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(store, "update", unwritable)
    with caplog.at_level(logging.WARNING):
        await move_out_of_database(repo, store)
    assert "could not move sync_token" in caplog.text
    # Still loaded from the row at this start; moved at the next one.
    assert (await repo.get_settings())["sync_token"] == TOKEN


async def test_startup_applies_what_was_moved(repo, tmp_path) -> None:
    _old_rows(tmp_path / "test.db", {"sync_token": TOKEN, "webhook_url": HOOK, "ws_rate": "5"})
    settings = Settings(db_path=tmp_path / "test.db", sync_token="gt7s_from_the_environment_00000")
    store = SecretStore(tmp_path)
    # What the lifespan does, in its order.
    await move_out_of_database(repo, store)
    registry.apply_stored(settings, {**await repo.get_settings(), **store.read()})
    assert (settings.sync_token, settings.webhook_url, settings.ws_rate) == (TOKEN, HOOK, 5)


# --- Admin ------------------------------------------------------------------


@pytest.fixture
async def client(tmp_path):
    settings = Settings(
        source="udp", db_path=tmp_path / "test.db", telemetry_port=43747,
        sync_token="gt7s_from_the_environment_00000",
    )
    engine = make_engine(settings.db_path)
    await init_db(engine)
    repo = Repository(make_session_factory(engine))
    service = TelemetryService(settings, repo, CarDatabase())
    service.env_settings = settings.model_copy()
    app = create_app()
    app.router.lifespan_context = None  # type: ignore[assignment]
    app.state.service = service
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, service
    await service.stop()
    await engine.dispose()


async def test_admin_saves_credentials_to_the_file(client) -> None:
    c, service = client
    resp = await c.put("/api/admin/settings", json={"webhook_url": HOOK, "sync_token": TOKEN})
    assert resp.status_code == 200
    assert service.notifier.url == HOOK
    assert service.secrets.read() == {"sync_token": TOKEN, "webhook_url": HOOK}
    stored = await service.repo.get_settings()
    assert not set(secret_store.KEYS) & set(stored)
    raw = service.settings.db_path.read_bytes()
    assert TOKEN.encode() not in raw and HOOK.encode() not in raw

    # Settings › Advanced still reports them as saved.
    rows = {s["key"]: s for s in (await c.get("/api/admin/config")).json()["settings"]}
    assert rows["webhook_url"]["source"] == "saved"
    assert rows["sync_token"]["source"] == "saved"


async def test_reset_hands_a_credential_back_to_the_environment(client) -> None:
    c, service = client
    await c.put("/api/admin/settings", json={"sync_token": TOKEN})
    resp = await c.put("/api/admin/config", json={"reset": ["sync_token", "webhook_url"]})
    assert resp.status_code == 200
    rows = {s["key"]: s for s in resp.json()["settings"]}
    assert rows["sync_token"]["source"] == "env"
    assert rows["webhook_url"]["source"] == "default"
    assert service.settings.sync_token == "gt7s_from_the_environment_00000"
    assert service.secrets.read() == {}
    assert not set(secret_store.KEYS) & set(await service.repo.get_settings())
