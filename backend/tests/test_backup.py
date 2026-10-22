"""Backup (#161): one file for data, surveys, config, layouts and tokens.

What matters is what the file holds and what it never holds: the recordings
as a consistent, compact database without the settings and layouts that are
parts of their own, and no credential unless the tokens part was asked for.
"""

import asyncio
import io
import json
import os
import sqlite3
import stat
import threading
import zipfile
from collections import namedtuple

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.processing.cars import CarDatabase
from app.service import TelemetryService
from app.storage import backup
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository
from tests.test_api import drive_laps

HOOK = "https://discord.com/api/webhooks/1/hook-secret"
SYNC_TOKEN = "gt7s_" + "a" * 40
ADMIN_TOKEN = "admin-s3cret"
LAYOUT = {"version": 2, "cols": 12, "rows": 8, "widgets": []}


async def make_client(tmp_path, admin_token: str = ""):
    settings = Settings(
        source="udp",
        db_path=tmp_path / "data" / "gt7.db",
        ws_rate=1000,
        telemetry_port=43745,
        admin_token=admin_token,
    )
    engine = make_engine(settings.db_path)
    await init_db(engine)
    repo = Repository(make_session_factory(engine))
    service = TelemetryService(settings, repo, CarDatabase())
    service.processor.min_lap_ticks = 1
    app = create_app()
    app.router.lifespan_context = None  # type: ignore[assignment]
    app.state.service = service
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test"), service, engine


@pytest.fixture
async def client(tmp_path):
    c, service, engine = await make_client(tmp_path)
    async with c:
        yield c, service
    await service.stop()
    await engine.dispose()


def write_survey_files(data_dir) -> None:
    bundles = data_dir / "track-bundles"
    (bundles / "corrections").mkdir(parents=True)
    (bundles / "compiled").mkdir()
    (bundles / "deep-forest-raceway.json").write_text('{"format": "bundle"}')
    (bundles / "corrections" / "deep-forest-raceway.json").write_text('{"areas": []}')
    (bundles / "compiled" / "deep-forest-raceway.json").write_text('{"derived": true}')
    (data_dir / "surface_survey_20260830_220427.jsonl").write_text('{"meta": {}}\n')
    (data_dir / "car-widths.json").write_text("{}")
    (data_dir / "source-id.json").write_text('{"source_id": "0123456789ab"}')
    # Never in a backup: rebuilt, or this install's own sync state.
    (data_dir / "cars.json").write_text("{}")
    (data_dir / "sync-sessions.json").write_text("{}")
    (data_dir / "sync-state.json").write_text("{}")


async def make_backup(c, parts, headers=None) -> zipfile.ZipFile:
    made = await c.post("/api/admin/backup", json={"parts": parts}, headers=headers or {})
    assert made.status_code == 200, made.text
    resp = await c.get(made.json()["url"])
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-disposition"].endswith('.zip"')
    return zipfile.ZipFile(io.BytesIO(resp.content))


def open_snapshot(zf: zipfile.ZipFile, tmp_path) -> sqlite3.Connection:
    path = tmp_path / "restored.db"
    path.write_bytes(zf.read("gt7.db"))
    return sqlite3.connect(path)


async def test_a_full_backup_holds_every_part_laid_out_as_the_data_folder(
    client, tmp_path
) -> None:
    c, service = client
    data_dir = service.settings.db_path.parent
    await drive_laps(service, laps=2)
    write_survey_files(data_dir)
    assert (await c.put(
        "/api/admin/config",
        json={"values": {"race_engineer_units": "imperial", "ws_rate": 20}},
    )).status_code == 200
    assert (await c.put("/api/admin/settings", json={
        "webhook_url": HOOK, "sync_token": SYNC_TOKEN,
    })).status_code == 200
    assert (await c.post(
        "/api/layouts", json={"name": "Pit wall", "kind": "dash", "config": LAYOUT}
    )).status_code == 200
    # What has been done to the recordings stays with them.
    await service.repo.set_setting("lap_start_check", "1")

    zf = await make_backup(c, list(backup.PARTS))
    names = set(zf.namelist())
    assert names == {
        "manifest.json",
        "gt7.db",
        "track-bundles/deep-forest-raceway.json",
        "track-bundles/corrections/deep-forest-raceway.json",
        "surface_survey_20260830_220427.jsonl",
        "car-widths.json",
        "source-id.json",
        "config.json",
        "layouts.json",
        "secrets.json",
    }

    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["format"] == "gt7-datalogger-backup"
    assert manifest["version"] == 1
    assert manifest["app_version"]
    assert manifest["alembic_revision"] == await service.repo.schema_revision()
    parts = manifest["parts"]
    assert list(parts) == ["data", "surveys", "config", "layouts", "tokens"]
    assert parts["data"]["sessions"] == 1
    assert parts["data"]["laps"] == 2
    assert parts["data"]["bytes"] == zf.getinfo("gt7.db").file_size
    assert zf.getinfo("gt7.db").date_time > (1980, 1, 1, 0, 0, 0)  # the snapshot's own time
    assert parts["surveys"] | {"files": None, "bytes": None} == {
        "files": None, "bytes": None,
        "bundles": 1, "corrections": 1, "logs": 1, "car_widths": True, "source_id": True,
    }
    assert parts["config"]["settings"] == 3  # sync_url is saved with the token
    assert parts["layouts"]["layouts"] == 1
    assert parts["tokens"]["keys"] == ["sync_token", "webhook_url"]

    db = open_snapshot(zf, tmp_path)
    assert db.execute("select count(*) from laps").fetchone() == (2,)
    assert db.execute("select count(*) from layouts").fetchone() == (0,)
    assert dict(db.execute("select key, value from settings")) == {"lap_start_check": "1"}
    db.close()

    config = json.loads(zf.read("config.json"))
    assert config["values"] == {
        "race_engineer_units": "imperial",
        "ws_rate": 20,
        "sync_url": "https://sync.gt7-datalogger.com",
    }
    layouts = json.loads(zf.read("layouts.json"))["layouts"]
    assert [(lay["name"], lay["kind"], lay["config"]) for lay in layouts] == [
        ("Pit wall", "dash", LAYOUT)
    ]
    assert "id" not in layouts[0]
    tokens = json.loads(zf.read("secrets.json"))
    assert tokens == {
        "format": "gt7-datalogger-secrets",
        "version": 1,
        "settings": {"sync_token": SYNC_TOKEN, "webhook_url": HOOK},
    }
    # Unzipped, it is as private as the file SecretStore writes.
    assert stat.S_IMODE(zf.getinfo("secrets.json").external_attr >> 16) == 0o600


async def test_config_is_a_body_the_settings_endpoint_takes_back(client, tmp_path) -> None:
    c, service = client
    await c.put("/api/admin/config", json={"values": {
        "race_engineer_units": "imperial",
        "race_engineer_categories": ["lap", "fuel"],
        "ws_rate": 20,
    }})
    zf = await make_backup(c, ["config"])
    assert zf.namelist() == ["config.json", "manifest.json"]
    await c.put("/api/admin/config", json={
        "reset": ["race_engineer_units", "race_engineer_categories", "ws_rate"]
    })
    resp = await c.put("/api/admin/config", content=zf.read("config.json"),
                       headers={"Content-Type": "application/json"})
    assert resp.status_code == 200, resp.text
    assert service.settings.race_engineer_units == "imperial"
    assert service.settings.race_engineer_categories == "lap,fuel"
    assert service.settings.ws_rate == 20


async def test_no_credential_travels_unless_tokens_are_asked_for(client) -> None:
    c, _ = client
    await c.put("/api/admin/settings", json={"webhook_url": HOOK, "sync_token": SYNC_TOKEN})
    await c.put("/api/admin/config", json={"values": {"admin_token": ADMIN_TOKEN}})
    zf = await make_backup(c, ["data", "surveys", "config", "layouts"], {"X-API-Key": ADMIN_TOKEN})
    assert "secrets.json" not in zf.namelist()
    for name in zf.namelist():
        content = zf.read(name)
        for secret in (HOOK, SYNC_TOKEN, ADMIN_TOKEN):
            assert secret.encode() not in content, (name, secret)


async def test_the_admin_token_is_never_carried(client) -> None:
    c, _ = client
    await c.put("/api/admin/config", json={"values": {"admin_token": ADMIN_TOKEN}})
    zf = await make_backup(c, list(backup.PARTS), {"X-API-Key": ADMIN_TOKEN})
    assert all(ADMIN_TOKEN.encode() not in zf.read(n) for n in zf.namelist())


async def test_deleted_recordings_do_not_travel_in_the_snapshot(client) -> None:
    c, service = client
    await drive_laps(service, laps=1)
    session_id = service.session_id
    sentinel = "deleted-note-7f3a9c"
    await c.patch(f"/api/sessions/{session_id}", json={"note": sentinel})
    # Deleted with secure_delete off, so the bytes stay in the file's free
    # space as a copied database would carry them. Whether a plain DELETE
    # leaves them depends on how SQLite was built: Ubuntu's (CI) zeroes
    # them by default, macOS's does not.
    with sqlite3.connect(service.settings.db_path) as db:
        db.execute("PRAGMA secure_delete = OFF")
        db.execute("DELETE FROM laps WHERE session_id = ?", (session_id,))
        db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    assert sentinel.encode() in service.settings.db_path.read_bytes()
    zf = await make_backup(c, ["data"])
    assert sentinel.encode() not in zf.read("gt7.db")


async def test_making_a_backup_needs_the_token_but_its_link_does_not(tmp_path) -> None:
    c, service, engine = await make_client(tmp_path, ADMIN_TOKEN)
    async with c:
        assert (await c.get("/api/admin/backup")).status_code == 401
        assert (await c.post("/api/admin/backup", json={"parts": ["config"]})).status_code == 401
        made = await c.post(
            "/api/admin/backup", json={"parts": ["config"]}, headers={"X-API-Key": ADMIN_TOKEN}
        )
        assert made.status_code == 200
        # A browser download carries no header: the unguessable id is the key.
        assert (await c.get(made.json()["url"])).status_code == 200
        assert (await c.get("/api/backup/guess/" + made.json()["name"])).status_code == 404
    await service.stop()
    await engine.dispose()


async def test_a_link_works_until_downloaded_and_a_new_backup_replaces_it(client) -> None:
    c, service = client
    data_dir = service.settings.db_path.parent
    first = (await c.post("/api/admin/backup", json={"parts": ["data"]})).json()
    assert len(list(data_dir.glob(".backup-*"))) == 1
    second = (await c.post("/api/admin/backup", json={"parts": ["data"]})).json()
    assert (await c.get(first["url"])).status_code == 404
    assert len(list(data_dir.glob(".backup-*"))) == 1  # the first one's snapshot is gone
    assert (await c.get(second["url"])).status_code == 200
    # Downloaded: the snapshot's space goes back at once, and the link with it.
    assert list(data_dir.glob(".backup-*")) == []
    assert (await c.get(second["url"])).status_code == 404


async def test_an_expired_backup_is_gone(client) -> None:
    c, service = client
    made = (await c.post("/api/admin/backup", json={"parts": ["data"]})).json()
    prepared = service.backups.get(made["id"])
    assert prepared is not None and prepared.expiry is not None
    service.backups.discard(prepared)  # what the expiry timer calls
    assert (await c.get(made["url"])).status_code == 404
    assert list(service.settings.db_path.parent.glob(".backup-*")) == []


async def test_a_backup_that_would_fill_the_disk_is_refused(client, monkeypatch) -> None:
    c, service = client
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(backup.shutil, "disk_usage", lambda _p: usage(100, 90, 10 << 20))
    resp = await c.post("/api/admin/backup", json={"parts": ["data"]})
    assert resp.status_code == 507
    assert "MB free" in resp.json()["detail"]
    # Without the recordings there is no snapshot to make room for.
    assert (await c.post("/api/admin/backup", json={"parts": ["config"]})).status_code == 200


async def test_unknown_or_no_parts_are_refused(client) -> None:
    c, _ = client
    assert (await c.post("/api/admin/backup", json={"parts": []})).status_code == 422
    assert (await c.post("/api/admin/backup", json={"parts": ["cars"]})).status_code == 422


async def test_inventory_says_what_each_part_would_hold(client) -> None:
    c, service = client
    await drive_laps(service, laps=2)
    write_survey_files(service.settings.db_path.parent)
    await c.put("/api/admin/settings", json={"sync_token": SYNC_TOKEN})
    inv = (await c.get("/api/admin/backup")).json()
    assert inv["data"]["available"] and inv["data"]["laps"] == 2
    assert inv["surveys"]["bundles"] == 1 and inv["surveys"]["logs"] == 1
    assert inv["config"] == {"available": True, "settings": 1}  # sync_url, saved with the token
    assert inv["layouts"] == {"available": False, "layouts": 0}
    assert inv["tokens"] == {"available": True, "keys": ["sync_token"]}
    assert inv["free_bytes"] > 0


# --- the snapshot ---------------------------------------------------------------


@pytest.fixture
async def database(tmp_path):
    path = tmp_path / "gt7.db"
    engine = make_engine(path)
    await init_db(engine)
    await engine.dispose()
    with sqlite3.connect(path) as db:
        db.executemany(
            "insert into settings (key, value) values (?, ?)",
            [("ws_rate", "20"), ("lap_start_check", "1")],
        )
        # Enough pages that a one-page step takes several steps.
        db.executemany(
            "insert into settings (key, value) values (?, ?)",
            [(f"filler-{i}", "x" * 3000) for i in range(20)],
        )
    return path


def test_a_write_between_steps_starts_the_copy_again_and_lands_in_it(
    database, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(backup, "STEP_PAGES", 1)
    writes = 0

    def write_once() -> None:
        nonlocal writes
        if writes:
            return
        writes += 1
        # Another connection, as the app's own pool is: committed between
        # two steps, which nothing in the copy holds off.
        with sqlite3.connect(database, timeout=0) as db:
            db.execute("insert into settings (key, value) values ('written-mid-copy', '1')")

    dest = tmp_path / "snapshot.db"
    info = backup.snapshot_database(database, dest, {"ws_rate"}, on_step=write_once)
    assert writes == 1
    keys = {k for (k,) in sqlite3.connect(dest).execute("select key from settings")}
    assert "written-mid-copy" in keys
    assert "lap_start_check" in keys and "ws_rate" not in keys
    assert info["revision"]
    assert not (tmp_path / "snapshot.db.part").exists()


def test_a_database_that_keeps_changing_is_refused(database, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(backup, "STEP_PAGES", 1)
    monkeypatch.setattr(backup, "MAX_RESTARTS", 2)
    n = 0

    def write_every_step() -> None:
        nonlocal n
        n += 1
        with sqlite3.connect(database) as db:
            db.execute("insert into settings (key, value) values (?, '1')", (f"w{n}",))

    dest = tmp_path / "snapshot.db"
    with pytest.raises(backup.BackupError, match="kept changing"):
        backup.snapshot_database(database, dest, set(), on_step=write_every_step)
    assert not dest.exists()
    assert not (tmp_path / "snapshot.db.part").exists()


def test_writes_that_change_pages_in_place_are_refused_too(
    database, tmp_path, monkeypatch
) -> None:
    """An UPDATE that keeps the file the same size restarts the copy at the
    page count it already had: no count goes up, and it must still end."""
    monkeypatch.setattr(backup, "STEP_PAGES", 1)
    monkeypatch.setattr(backup, "MAX_RESTARTS", 3)
    steps = 0

    def update_every_step() -> None:
        nonlocal steps
        steps += 1
        assert steps < 100, "the copy was never refused"
        with sqlite3.connect(database) as db:
            db.execute(
                "update settings set value = ? where key = 'filler-0'", (str(steps % 2) * 3000,)
            )

    dest = tmp_path / "snapshot.db"
    with pytest.raises(backup.BackupError, match="kept changing"):
        backup.snapshot_database(database, dest, set(), on_step=update_every_step)
    assert not dest.exists()
    assert not (tmp_path / "snapshot.db.part").exists()
    # Once the writes stop, the next one goes through.
    backup.snapshot_database(database, dest, set())
    assert dest.exists()


def test_a_copy_that_runs_past_its_deadline_is_refused(database, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(backup, "COPY_DEADLINE_S", -1)
    dest = tmp_path / "snapshot.db"
    with pytest.raises(backup.BackupError, match="took more than"):
        backup.snapshot_database(database, dest, set())
    assert not dest.exists()
    assert not (tmp_path / "snapshot.db.part").exists()


async def test_a_large_survey_log_is_streamed_in_pieces(client) -> None:
    _, service = client
    data_dir = service.settings.db_path.parent
    log = os.urandom(4 * backup.CHUNK)  # incompressible: deflate cannot shrink it
    (data_dir / "surface_survey_20260101_000000.jsonl").write_bytes(log)
    prepared = backup.Prepared(id="x", parts=("surveys",), created_at="now", name="b.zip")
    chunks = [c async for c in backup.write_archive(prepared, data_dir, service.repo, {})]
    assert max(len(c) for c in chunks) <= backup.CHUNK + 64 * 1024
    zf = zipfile.ZipFile(io.BytesIO(b"".join(chunks)))
    assert zf.read("surface_survey_20260101_000000.jsonl") == log
    surveys = json.loads(zf.read("manifest.json"))["parts"]["surveys"]
    assert surveys["bytes"] == len(log)
    assert surveys["logs"] == 1


async def test_a_cancelled_snapshot_is_waited_for_and_removed(
    database, tmp_path, monkeypatch
) -> None:
    started, release = threading.Event(), threading.Event()
    real = backup.snapshot_database

    def held_snapshot(src, dest, drop, **kw):
        if not started.is_set():
            started.set()
            assert release.wait(5)
            dest.write_bytes(b"orphan")  # what the copy writes after its request is gone
            return {"revision": "r", "sessions": 0, "laps": 0}
        return real(src, dest, drop, **kw)

    monkeypatch.setattr(backup, "snapshot_database", held_snapshot)
    backups = backup.Backups(tmp_path)
    first = asyncio.create_task(backups.prepare(["data"], database, "r"))
    await asyncio.to_thread(started.wait, 5)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    # The next backup waits for the cancelled copy rather than running beside it.
    second = asyncio.create_task(backups.prepare(["data"], database, "r"))
    await asyncio.sleep(0.05)
    assert not second.done()
    release.set()
    prepared = await second
    # Only the new backup's snapshot is left: the cancelled one's is gone.
    assert list(tmp_path.glob(".backup-*")) == [prepared.snapshot]
    backups.discard_all()


def test_leftovers_of_an_interrupted_backup_are_cleared_at_start(tmp_path) -> None:
    (tmp_path / ".backup-abc.db").write_bytes(b"x")
    (tmp_path / ".backup-abc.db.part").write_bytes(b"x")
    (tmp_path / "gt7.db").write_bytes(b"x")
    backup.Backups(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["gt7.db"]
