"""Restore (#161): a backup put back into an install, part by part.

What matters is that only the parts ticked change anything; that the
recordings are replaced whole, with the ones they replace kept and this
install's own settings and layouts carried over; that nothing writes while
the database file changes hands; and that a file which is not a backup this
version can take changes nothing at all.
"""

import asyncio
import io
import json
import sqlite3
import threading
import zipfile
from pathlib import Path

import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine

from app.config import Settings
from app.main import create_app
from app.processing import car_width, track_bundle, track_corrections
from app.processing.cars import CarDatabase
from app.service import TelemetryService
from app.storage import backup, restore
from app.storage.db import alembic_config, init_db, make_engine, make_session_factory
from app.storage.repository import Repository
from tests.test_api import drive_laps
from tests.test_track_manager import _foreign_bundle

HOOK = "https://discord.com/api/webhooks/1/hook-secret"
SYNC_TOKEN = "gt7s_" + "a" * 40
ADMIN_TOKEN = "admin-s3cret"
LAYOUT = {"version": 2, "cols": 12, "rows": 8, "widgets": []}
OTHER_LAYOUT = {"version": 2, "cols": 6, "rows": 4, "widgets": []}
OFFICIAL = {
    "track": "Ring", "layout": "GP", "official_id": "ring-gp", "official_name": "Ring GP",
    "turns": 10, "length_m": 4000.0, "reverse": False,
}
STRANGER = "beef0000cafe"
SOURCE_ID = "0123456789ab"
ALL = ["data", "surveys", "source_id", "config", "layouts", "tokens"]


class StubSource:
    """A telemetry source that sends nothing: the restore stops and starts
    it, and a test must not bind a port or run the simulator to see that.
    Started while running, it fails as the UDP listener does on a port it
    already holds, and says so in `events`."""

    def __init__(self) -> None:
        self.starts = 0
        self.stops = 0
        self.running = False
        self.events: list[str] = []

    async def start(self) -> None:
        if self.running:
            self.events.append("started twice")
            raise OSError(48, "Address already in use")
        self.starts += 1
        self.running = True
        self.events.append("start")

    async def stop(self) -> None:
        self.stops += 1
        self.running = False
        self.events.append("stop")

    @property
    def stats(self) -> dict[str, object]:
        return {"connected": False, "packets_received": 0}


class Install:
    def __init__(self, client: AsyncClient, service: TelemetryService, engine, app) -> None:
        self.client = client
        self.service = service
        self.engine = engine
        self.app = app

    @property
    def data_dir(self) -> Path:
        return self.service.settings.db_path.parent

    @property
    def db_path(self) -> Path:
        return self.service.settings.db_path


async def make_install(root: Path, admin_token: str = "") -> Install:
    settings = Settings(
        source="udp", db_path=root / "data" / "gt7.db", ws_rate=1000, admin_token=admin_token
    )
    engine = make_engine(settings.db_path)
    await init_db(engine)
    repo = Repository(make_session_factory(engine))
    service = TelemetryService(settings, repo, CarDatabase())
    service.source = StubSource()  # type: ignore[assignment]
    service.processor.min_lap_ticks = 1
    app = create_app()
    app.router.lifespan_context = None  # type: ignore[assignment]
    app.state.service = service
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return Install(client, service, engine, app)


@pytest.fixture
async def installs(tmp_path):
    made: list[Install] = []

    async def make(name: str, admin_token: str = "") -> Install:
        install = await make_install(tmp_path / name, admin_token)
        made.append(install)
        return install

    yield make
    for install in made:
        await install.client.aclose()
        await install.service.stop()
        await install.engine.dispose()


async def make_backup(install: Install, parts: list[str]) -> bytes:
    made = await install.client.post("/api/admin/backup", json={"parts": parts})
    assert made.status_code == 200, made.text
    resp = await install.client.get(made.json()["url"])
    assert resp.status_code == 200, resp.text
    return resp.content


async def upload(install: Install, body: bytes, headers=None):
    return await install.client.post(
        "/api/admin/restore?name=gt7-backup-2026-10-21.zip",
        content=body,
        headers={"Content-Type": "application/zip", **(headers or {})},
    )


async def restore_parts(install: Install, body: bytes, items, machine=()):
    up = await upload(install, body)
    assert up.status_code == 200, up.text
    resp = await install.client.post(
        f"/api/admin/restore/{up.json()['id']}",
        json={"items": list(items), "machine_settings": list(machine)},
    )
    return up.json(), resp


def craft(files: dict[str, bytes | str], manifest: dict | None = None, **parts) -> bytes:
    """A backup made by hand: `files` as given, and a manifest listing each
    under the part restore files it under, unless one is given."""
    if manifest is None:
        listed: dict[str, dict] = {}
        for name in files:
            part = restore.part_of(name)
            assert part is not None, name
            listed.setdefault(part, {"files": []})["files"].append(name)
        for part, extra in parts.items():
            listed.setdefault(part, {"files": []}).update(extra)
        manifest = {
            "format": backup.FORMAT,
            "version": backup.VERSION,
            "app_version": "0.8.1",
            "created_at": "2026-10-21T20:00:00+00:00",
            "alembic_revision": restore.schema_revisions()[-1],
            "parts": listed,
        }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
        zf.writestr(backup.MANIFEST, json.dumps(manifest))
    return out.getvalue()


def count(db_path: Path, table: str) -> int:
    with sqlite3.connect(db_path) as db:
        return db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def kept_copies(install: Install) -> list[Path]:
    return sorted(install.data_dir.glob("gt7.db.before-restore-*"))


def write_bundle(data_dir: Path, track="Ring", source=STRANGER, n=6, official=None) -> None:
    doc = track_bundle.validate_document(_foreign_bundle(track=track, source=source, n=n))
    track_bundle.merge_document(data_dir, doc)
    if official is not None:
        track_bundle.set_official(data_dir, track, official)


def corrections_doc(official_id="ring-gp", smooth=False) -> dict:
    return {
        "format": track_corrections.FORMAT,
        "version": track_corrections.VERSION,
        "official_id": official_id,
        "track": "Ring",
        "compile": {"smooth_borders": smooth},
        "exclude": [],
        "draw": [],
    }


# --- the round trip ----------------------------------------------------------------


async def test_a_whole_install_goes_back_into_a_clean_one(installs) -> None:
    old = await installs("old")
    await drive_laps(old.service, laps=3)
    write_bundle(old.data_dir, official=OFFICIAL)
    track_corrections.write(
        old.data_dir, "ring", track_corrections.validate(corrections_doc())
    )
    (old.data_dir / "surface_survey_20260830_220427.jsonl").write_text(
        '{"meta": {"started_at": "x", "track_width_m": 1.7}}\n'
    )
    car_width.remember(old.data_dir, 7, 1.82, 400, "Car 7")
    track_bundle.set_source_id(old.data_dir, SOURCE_ID)
    assert (await old.client.put("/api/admin/config", json={
        "values": {"race_engineer_units": "imperial", "ws_rate": 20, "ps_ip": "10.0.0.9"},
    })).status_code == 200
    assert (await old.client.put("/api/admin/settings", json={
        "webhook_url": HOOK, "sync_token": SYNC_TOKEN,
    })).status_code == 200
    assert (await old.client.post(
        "/api/layouts", json={"name": "Pit wall", "kind": "dash", "config": LAYOUT}
    )).status_code == 200
    body = await make_backup(old, list(backup.PARTS))

    new = await installs("new")
    up = await upload(new, body)
    assert up.status_code == 200, up.text
    described = up.json()
    assert described["name"] == "gt7-backup-2026-10-21.zip"
    assert described["here"] == {"sessions": 0, "laps": 0, "source_id": False, "tokens": []}
    parts = described["parts"]
    assert parts["data"] | {"bytes": 0, "needs_bytes": 0} == {
        "available": True, "bytes": 0, "sessions": 1, "laps": 3, "needs_bytes": 0, "refused": "",
    }
    assert parts["surveys"]["bundles"] == 1
    assert parts["surveys"]["corrections"] == 1
    assert parts["surveys"]["logs"] == 1
    assert parts["surveys"]["car_widths"] is True
    assert parts["source_id"] == {"available": True, "refused": ""}
    listed = {s["key"]: s for s in parts["config"]["settings"]}
    assert listed["ps_ip"]["machine"] is True
    assert listed["ps_ip"]["value"] == "10.0.0.9"
    assert listed["race_engineer_units"]["machine"] is False
    assert parts["layouts"]["layouts"] == [{"name": "Pit wall", "kind": "dash", "replaces": False}]
    assert parts["tokens"]["keys"] == ["webhook_url", "sync_token"]

    resp = await new.client.post(
        f"/api/admin/restore/{described['id']}", json={"items": ALL}
    )
    assert resp.status_code == 200, resp.text
    report = resp.json()
    assert report["data"]["sessions"] == 1 and report["data"]["laps"] == 3
    assert report["config"]["left"] == ["ps_ip"]
    assert "race_engineer_units" in report["config"]["applied"]
    assert report["layouts"] == {"created": ["Pit wall"], "replaced": [], "skipped": []}
    assert report["tokens"] == {"applied": ["webhook_url", "sync_token"], "skipped": []}
    assert report["source_id"] == {"source_id": SOURCE_ID}
    assert report["rejudging"] == ["ring"]

    # The recordings, in the database the service now runs on.
    assert (await new.service.repo.stats())["laps"] == 3
    assert len(kept_copies(new)) == 1
    # The settings, applied live and saved.
    assert new.service.settings.race_engineer_units == "imperial"
    assert new.service.settings.ws_rate == 20
    assert new.service.settings.ps_ip == ""
    stored = await new.service.repo.get_settings()
    assert stored["race_engineer_units"] == "imperial"
    assert "ps_ip" not in stored
    # The credentials, beside the database and live.
    assert new.service.secrets.read() == {"webhook_url": HOOK, "sync_token": SYNC_TOKEN}
    assert new.service.notifier.url == HOOK
    assert "sync_token" not in stored
    # The layouts and the surveys.
    assert [x["name"] for x in await new.service.repo.list_layouts()] == ["Pit wall"]
    assert track_bundle.load(new.data_dir, "Ring")["meta"]["source_runs"] == {STRANGER: 4}
    assert track_corrections.load(new.data_dir, "ring") is not None
    assert (new.data_dir / "surface_survey_20260830_220427.jsonl").is_file()
    assert car_width.load(new.data_dir)[7]["width_m"] == 1.82
    assert track_bundle.source_id(new.data_dir) == SOURCE_ID
    # One restore per upload: it is gone once applied.
    assert not list(new.data_dir.glob(".restore-*"))
    assert (await new.client.get("/api/admin/restore")).status_code == 404


async def test_only_the_parts_ticked_change_anything(installs) -> None:
    old = await installs("old")
    await drive_laps(old.service, laps=2)
    write_bundle(old.data_dir)
    assert (await old.client.put(
        "/api/admin/config", json={"values": {"race_engineer_units": "imperial"}}
    )).status_code == 200
    body = await make_backup(old, ["data", "surveys", "config"])

    new = await installs("new")
    _, resp = await restore_parts(new, body, ["config"])
    assert resp.status_code == 200, resp.text
    assert set(resp.json()) == {"config", "rejudging"}
    assert (await new.service.repo.stats())["laps"] == 0
    assert track_bundle.load(new.data_dir, "Ring") is None
    assert not kept_copies(new)
    assert new.service.settings.race_engineer_units == "imperial"


# --- data --------------------------------------------------------------------------


async def test_data_replaces_the_recordings_and_keeps_the_ones_it_replaced(installs) -> None:
    old = await installs("old")
    await drive_laps(old.service, laps=2)
    body = await make_backup(old, ["data"])

    new = await installs("new")
    await drive_laps(new.service, laps=4)
    await new.service.repo.set_setting("race_engineer_units", "imperial")
    await new.service.repo.set_setting("cars_updated_at", "2026-10-01")
    layout = await new.service.repo.create_layout("Mine", "overlay", LAYOUT)
    before = (await new.client.get("/api/sessions/changes?since=0")).json()["revision"]
    stopped = new.service.source.stops  # type: ignore[attr-defined]
    replaced: list[int] = []

    async def database_replaced() -> None:
        # main.lifespan's: the start's work on the recordings, done again.
        replaced.append((await new.service.repo.stats())["laps"])

    new.service.database_replaced = database_replaced

    described, resp = await restore_parts(new, body, ["data"])
    assert described["here"]["laps"] == 4
    assert resp.status_code == 200, resp.text
    report = resp.json()["data"]
    assert report["sessions"] == 1 and report["laps"] == 2
    assert report["revision"] == restore.schema_revisions()[-1]
    assert report["upgraded_from"] is None

    # The old recordings, kept beside the new ones, whole.
    [kept] = kept_copies(new)
    assert report["kept"] == kept.name
    assert count(kept, "laps") == 4
    assert count(new.db_path, "laps") == 2
    assert (await new.service.repo.stats())["laps"] == 2
    # This install's own settings and layouts came across; the snapshot had neither.
    stored = await new.service.repo.get_settings()
    assert stored["race_engineer_units"] == "imperial"
    assert stored["cars_updated_at"] == "2026-10-01"
    assert await new.service.repo.list_layouts() == [layout]
    # A client keeping a copy of the sessions is told every one changed.
    changes = (await new.client.get(f"/api/sessions/changes?since={before}")).json()
    assert len(changes["sessions"]) == 1
    # The telemetry source was paused and started again, and what a start
    # does for the recordings was done for the restored ones.
    assert new.service.source.stops == stopped + 1  # type: ignore[attr-defined]
    assert new.service.source.starts == 1  # type: ignore[attr-defined]
    assert replaced == [2]


async def test_recording_carries_on_into_the_restored_database(installs) -> None:
    old = await installs("old")
    await drive_laps(old.service, laps=2)
    body = await make_backup(old, ["data"])

    new = await installs("new")
    await drive_laps(new.service, laps=1)
    assert new.service.session_id is not None
    _, resp = await restore_parts(new, body, ["data"])
    assert resp.status_code == 200, resp.text
    # The recorder forgot the session that lives in the kept database ...
    assert new.service.session_id is None
    # ... and the next drive is a new session in the restored one.
    await drive_laps(new.service, laps=2)
    sessions = await new.service.repo.list_sessions()
    assert len(sessions) == 2
    assert (await new.service.repo.stats())["laps"] == 4


async def test_a_backup_from_a_newer_version_is_refused_and_changes_nothing(
    installs, tmp_path
) -> None:
    old = await installs("old")
    await drive_laps(old.service, laps=1)
    zf = zipfile.ZipFile(io.BytesIO(await make_backup(old, ["data"])))
    future = tmp_path / "future.db"
    future.write_bytes(zf.read("gt7.db"))
    with sqlite3.connect(future) as db:
        db.execute("UPDATE alembic_version SET version_num = '0099_from_the_future'")
    newer = craft(
        {"gt7.db": future.read_bytes()},
        data={"sessions": 1, "laps": 1},
    )
    manifest = json.loads(zipfile.ZipFile(io.BytesIO(newer)).read("manifest.json"))
    manifest["alembic_revision"] = "0099_from_the_future"
    manifest["app_version"] = "0.9.0"
    honest = craft({"gt7.db": future.read_bytes()}, manifest=manifest)

    new = await installs("new")
    await drive_laps(new.service, laps=2)
    described, resp = await restore_parts(new, honest, ["data"])
    refused = described["parts"]["data"]["refused"]
    assert "newer version (0.9.0" in refused and "update this install" in refused
    assert resp.status_code == 409
    assert "newer version" in resp.json()["detail"]

    # A manifest that says otherwise does not get it past: the database's
    # own revision decides.
    described, resp = await restore_parts(new, newer, ["data"])
    assert described["parts"]["data"]["refused"] == ""
    assert resp.status_code == 409, resp.text
    assert "newer version" in resp.json()["detail"]

    assert (await new.service.repo.stats())["laps"] == 2
    assert not kept_copies(new)
    assert not list(new.data_dir.glob(".restore-*.db"))


async def test_a_database_from_an_older_version_is_upgraded(installs, tmp_path) -> None:
    older = restore.schema_revisions()[-2]
    path = tmp_path / "older.db"
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        cfg = alembic_config()
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, older)
    engine.dispose()
    manifest_parts = {"data": {"files": ["gt7.db"], "sessions": 0, "laps": 0}}
    body = craft(
        {"gt7.db": path.read_bytes()},
        manifest={
            "format": backup.FORMAT, "version": backup.VERSION, "app_version": "0.8.0",
            "created_at": "2026-10-01T00:00:00+00:00", "alembic_revision": older,
            "parts": manifest_parts,
        },
    )
    new = await installs("new")
    _, resp = await restore_parts(new, body, ["data"])
    assert resp.status_code == 200, resp.text
    report = resp.json()["data"]
    assert report["upgraded_from"] == older
    assert report["revision"] == restore.schema_revisions()[-1]
    assert await new.service.repo.schema_revision() == restore.schema_revisions()[-1]


async def test_a_damaged_database_is_refused(installs) -> None:
    new = await installs("new")
    body = craft({"gt7.db": b"SQLite format 3\x00" + b"\x00" * 4000}, data={"sessions": 0})
    _, resp = await restore_parts(new, body, ["data"])
    assert resp.status_code == 409
    assert "database" in resp.json()["detail"]
    assert not kept_copies(new)


async def test_an_unfinished_transaction_refuses_the_swap(installs) -> None:
    old = await installs("old")
    await drive_laps(old.service, laps=1)
    body = await make_backup(old, ["data"])
    new = await installs("new")
    await drive_laps(new.service, laps=2)
    journal = new.db_path.with_name("gt7.db-journal")
    journal.write_bytes(b"")
    _, resp = await restore_parts(new, body, ["data"])
    journal.unlink()
    assert resp.status_code == 409
    assert "unfinished transaction" in resp.json()["detail"]
    assert not kept_copies(new)
    assert (await new.service.repo.stats())["laps"] == 2


async def staged_swap(installs, monkeypatch):
    """An install with three laps, a staged database from another with one,
    and an install step that waits in its thread until released."""
    old = await installs("old")
    await drive_laps(old.service, laps=1)
    zf = zipfile.ZipFile(io.BytesIO(await make_backup(old, ["data"])))
    new = await installs("new")
    await drive_laps(new.service, laps=3)
    staged = new.data_dir / ".restore-test.db"
    staged.write_bytes(zf.read("gt7.db"))
    entered, release = threading.Event(), threading.Event()
    real = restore.install_database

    def slow_install(live, staged, kept):
        entered.set()
        assert release.wait(5)
        real(live, staged, kept)

    monkeypatch.setattr(restore, "install_database", slow_install)
    return new, staged, entered, release


async def test_a_cancelled_restore_finishes_its_swap_before_letting_go(
    installs, monkeypatch
) -> None:
    new, staged, entered, release = await staged_swap(installs, monkeypatch)
    swap = asyncio.create_task(new.service.replace_database(staged))
    assert await asyncio.to_thread(entered.wait, 5)
    swap.cancel()
    await asyncio.sleep(0.05)
    # Cancelled while its thread is still renaming files: nothing is let go
    # of — not the gate, not the writers.
    assert not swap.done()
    assert new.service.repo.gate.is_shut
    assert new.service.source.starts == 0  # type: ignore[attr-defined]
    reader = asyncio.create_task(new.service.repo.stats())
    await asyncio.sleep(0.05)
    assert not reader.done()

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await swap
    # The swap went through first; the session held back reads the
    # restored recordings, and the writers started again after it.
    assert (await reader)["laps"] == 1
    assert not new.service.repo.gate.is_shut
    assert new.service.source.starts == 1  # type: ignore[attr-defined]
    assert len(kept_copies(new)) == 1
    assert not staged.exists()


async def test_a_service_stopped_during_a_restore_is_not_started_again(
    installs, monkeypatch
) -> None:
    new, staged, entered, release = await staged_swap(installs, monkeypatch)
    swap = asyncio.create_task(new.service.replace_database(staged))
    assert await asyncio.to_thread(entered.wait, 5)
    stopping = asyncio.create_task(new.service.stop())
    await asyncio.sleep(0.05)
    release.set()
    await asyncio.gather(swap, stopping)
    assert new.service.source.starts == 0  # type: ignore[attr-defined]


async def test_a_failed_swap_puts_the_recordings_back(installs, monkeypatch) -> None:
    old = await installs("old")
    await drive_laps(old.service, laps=1)
    body = await make_backup(old, ["data"])
    new = await installs("new")
    await drive_laps(new.service, laps=3)

    import app.service as service_module

    real = service_module.init_db
    calls = 0

    async def fail_on_the_swap(engine) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("migration exploded")

    monkeypatch.setattr(service_module, "init_db", fail_on_the_swap)
    up = await upload(new, body)
    with pytest.raises(RuntimeError):
        await new.client.post(
            f"/api/admin/restore/{up.json()['id']}", json={"items": ["data"]}
        )
    monkeypatch.setattr(service_module, "init_db", real)
    assert calls == 1
    assert not kept_copies(new)
    assert (await new.service.repo.stats())["laps"] == 3
    # And the service carries on.
    assert new.service.source.starts == 1  # type: ignore[attr-defined]
    await drive_laps(new.service, laps=1)


async def test_data_that_would_not_fit_is_refused_before_anything_changes(
    installs, monkeypatch
) -> None:
    old = await installs("old")
    await drive_laps(old.service, laps=1)
    body = await make_backup(old, ["data"])
    new = await installs("new")
    up = await upload(new, body)
    size = up.json()["parts"]["data"]["needs_bytes"]
    usage = type("Usage", (), {"free": size - 1, "total": 0, "used": 0})
    monkeypatch.setattr("app.api.restore.shutil.disk_usage", lambda _: usage)
    described = (await new.client.get("/api/admin/restore")).json()
    assert "MB free here" in described["parts"]["data"]["refused"]
    resp = await new.client.post(
        f"/api/admin/restore/{up.json()['id']}", json={"items": ["data"]}
    )
    assert resp.status_code == 409
    assert not kept_copies(new)


# --- surveys and the install identity -------------------------------------------------


async def test_surveys_merge_and_never_replace_what_is_here(installs) -> None:
    old = await installs("old")
    write_bundle(old.data_dir, official=OFFICIAL)
    track_corrections.write(
        old.data_dir, "ring", track_corrections.validate(corrections_doc(smooth=False))
    )
    write_bundle(old.data_dir, track="Bay", n=4)
    track_corrections.write(
        old.data_dir, "bay",
        track_corrections.validate(corrections_doc(official_id="bay-a") | {"track": "Bay"}),
    )
    (old.data_dir / "surface_survey_a.jsonl").write_text("theirs\n")
    (old.data_dir / "surface_survey_b.jsonl").write_text("theirs\n")
    car_width.remember(old.data_dir, 7, 1.90, 900)
    car_width.remember(old.data_dir, 8, 1.70, 300)
    body = await make_backup(old, ["surveys"])

    new = await installs("new")
    mine = track_bundle.source_id(new.data_dir)
    write_bundle(new.data_dir, source=mine, n=8, official=OFFICIAL)
    track_corrections.write(
        new.data_dir, "ring", track_corrections.validate(corrections_doc(smooth=True))
    )
    (new.data_dir / "surface_survey_a.jsonl").write_text("mine\n")
    car_width.remember(new.data_dir, 7, 1.80, 100)

    _, resp = await restore_parts(new, body, ["surveys"])
    assert resp.status_code == 200, resp.text
    report = resp.json()["surveys"]
    assert sorted(b["track"] for b in report["bundles"]) == ["Bay", "Ring"]
    assert report["corrections"] == ["Bay"]
    assert report["logs"] == ["surface_survey_b.jsonl"]
    assert report["car_widths"] == 1
    assert report["failed"] == []
    assert resp.json()["rejudging"] == ["bay", "ring"]

    ring = track_bundle.load(new.data_dir, "Ring")
    assert ring["meta"]["source_runs"] == {mine: 4, STRANGER: 4}
    # What was here stays: corrections, a log of the same name, a measured car.
    assert track_corrections.load(new.data_dir, "ring")["compile"]["smooth_borders"] is True
    assert track_corrections.load(new.data_dir, "bay") is not None
    assert (new.data_dir / "surface_survey_a.jsonl").read_text() == "mine\n"
    assert (new.data_dir / "surface_survey_b.jsonl").read_text() == "theirs\n"
    widths = car_width.load(new.data_dir)
    assert widths[7]["width_m"] == 1.80 and widths[8]["width_m"] == 1.70
    # Not ticked, so this install is still itself.
    assert track_bundle.source_id(new.data_dir) == mine


async def test_a_bundle_that_does_not_validate_is_listed_and_the_rest_go(installs) -> None:
    good = _foreign_bundle(track="Ring")
    body = craft(
        {
            "track-bundles/ring.json": json.dumps(good),
            "track-bundles/broken.json": json.dumps({"format": "nope"}),
        },
        surveys={"bundles": 2},
    )
    new = await installs("new")
    _, resp = await restore_parts(new, body, ["surveys"])
    assert resp.status_code == 200, resp.text
    report = resp.json()["surveys"]
    assert [b["track"] for b in report["bundles"]] == ["Ring"]
    assert [f["file"] for f in report["failed"]] == ["track-bundles/broken.json"]


async def test_the_install_identity_goes_only_when_ticked(installs) -> None:
    body = craft(
        {"source-id.json": json.dumps({"source_id": SOURCE_ID})}, surveys={"bundles": 0}
    )
    new = await installs("new")
    await drive_laps(new.service, laps=1)
    mine = track_bundle.source_id(new.data_dir)
    described, resp = await restore_parts(new, body, ["surveys"])
    assert described["here"]["source_id"] is True
    assert described["here"]["sessions"] == 1
    assert resp.status_code == 200
    assert track_bundle.source_id(new.data_dir) == mine

    _, resp = await restore_parts(new, body, ["source_id"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["source_id"] == {"source_id": SOURCE_ID}
    assert track_bundle.source_id(new.data_dir) == SOURCE_ID
    on_disk = json.loads((new.data_dir / "source-id.json").read_text())
    assert on_disk["source_id"] == SOURCE_ID


# --- config, layouts, tokens ----------------------------------------------------------


async def test_config_merges_through_the_registry(installs) -> None:
    config = {
        "format": backup.CONFIG_FORMAT,
        "version": 1,
        "values": {
            "race_engineer_units": "imperial",
            "webhook_events": ["personal_best"],
            "ps_ip": "10.0.0.9",
            "telemetry_port": 40000,
            "ws_rate": 999,
            "admin_token": "let-me-in",
            "sync_token": SYNC_TOKEN,
            "http_port": 9000,
            "flux_capacitor": True,
        },
    }
    body = craft({"config.json": json.dumps(config)}, config={"settings": 9})
    new = await installs("new")
    described, resp = await restore_parts(new, body, ["config"], machine=["ps_ip"])
    listing = described["parts"]["config"]
    assert [s["key"] for s in listing["settings"]] == [
        "race_engineer_units", "webhook_events", "ps_ip", "telemetry_port",
    ]
    assert {s["key"] for s in listing["skipped"]} == {
        "ws_rate", "admin_token", "sync_token", "http_port", "flux_capacitor",
    }
    assert resp.status_code == 200, resp.text
    report = resp.json()["config"]
    assert report["applied"] == ["race_engineer_units", "webhook_events", "ps_ip"]
    assert report["left"] == ["telemetry_port"]
    assert new.service.settings.ps_ip == "10.0.0.9"
    assert new.service.settings.telemetry_port != 40000
    assert new.service.settings.admin_token == ""
    assert new.service.settings.sync_token == ""
    assert new.service.settings.webhook_events == "personal_best"


async def test_a_setting_its_endpoint_refuses_is_skipped_and_the_rest_go(installs) -> None:
    config = {
        "format": backup.CONFIG_FORMAT,
        "version": 1,
        "values": {"ps_ip": "not a host!", "race_engineer_units": "imperial"},
    }
    body = craft({"config.json": json.dumps(config)}, config={"settings": 2})
    new = await installs("new")
    _, resp = await restore_parts(new, body, ["config"], machine=["ps_ip"])
    assert resp.status_code == 200, resp.text
    report = resp.json()["config"]
    assert report["applied"] == ["race_engineer_units"]
    assert report["skipped"] == [{"key": "ps_ip", "reason": "not a valid IP address or hostname"}]


async def test_layouts_merge_by_name_keeping_the_id_here(installs) -> None:
    layouts = {
        "format": backup.LAYOUTS_FORMAT,
        "version": 1,
        "layouts": [
            {"name": "Pit wall", "kind": "dash", "config": OTHER_LAYOUT},
            {"name": "Stream", "kind": "overlay", "config": LAYOUT},
            {"name": "Old", "kind": "overlay", "config": {"version": 1}},
        ],
    }
    body = craft({"layouts.json": json.dumps(layouts)}, layouts={"layouts": 3})
    new = await installs("new")
    mine = await new.service.repo.create_layout("Pit wall", "overlay", LAYOUT)
    described, resp = await restore_parts(new, body, ["layouts"])
    assert described["parts"]["layouts"]["layouts"] == [
        {"name": "Pit wall", "kind": "dash", "replaces": True},
        {"name": "Stream", "kind": "overlay", "replaces": False},
    ]
    assert resp.status_code == 200, resp.text
    assert resp.json()["layouts"] == {
        "created": ["Stream"],
        "replaced": ["Pit wall"],
        "skipped": [{"name": "Old", "reason": "layout config must have version 2"}],
    }
    pit = await new.service.repo.get_layout_by_name("Pit wall")
    assert pit["id"] == mine["id"]
    assert pit["kind"] == "dash" and pit["config"] == OTHER_LAYOUT


async def test_tokens_are_written_only_when_ticked(installs) -> None:
    secrets = {
        "format": "gt7-datalogger-secrets",
        "version": 1,
        "settings": {"webhook_url": HOOK, "sync_token": SYNC_TOKEN},
    }
    body = craft(
        {"secrets.json": json.dumps(secrets), "config.json": json.dumps(
            {"format": backup.CONFIG_FORMAT, "version": 1, "values": {}}
        )},
        tokens={"keys": ["webhook_url", "sync_token"]},
    )
    new = await installs("new")
    _, resp = await restore_parts(new, body, ["config"])
    assert resp.status_code == 200, resp.text
    assert new.service.secrets.read() == {}
    assert not (new.data_dir / "secrets.json").exists()

    _, resp = await restore_parts(new, body, ["tokens"])
    assert resp.status_code == 200, resp.text
    assert new.service.secrets.read() == {"webhook_url": HOOK, "sync_token": SYNC_TOKEN}
    assert new.service.settings.sync_token == SYNC_TOKEN
    assert "sync_token" not in await new.service.repo.get_settings()


# --- what is refused ------------------------------------------------------------------


def _zip(entries: list[tuple[str, bytes]]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return out.getvalue()


GOOD_MANIFEST = {
    "format": backup.FORMAT, "version": backup.VERSION, "app_version": "0.8.1",
    "created_at": "2026-10-21T20:00:00+00:00", "alembic_revision": "x",
    "parts": {"layouts": {"files": ["layouts.json"]}},
}


@pytest.mark.parametrize(
    "body, why",
    [
        (b"not a zip at all", "not a ZIP"),
        (_zip([("layouts.json", b"{}")]), "no manifest.json"),
        (_zip([("manifest.json", b"{nope")]), "manifest.json does not read"),
        (_zip([("manifest.json", json.dumps({"format": "other"}).encode())]),
         "not a datalogger backup"),
        (_zip([("manifest.json", json.dumps(GOOD_MANIFEST | {"version": 2}).encode()),
               ("layouts.json", b"{}")]), "update this install"),
        (_zip([("manifest.json", json.dumps(GOOD_MANIFEST).encode())]),
         "which is not in the file"),
        (_zip([("manifest.json", json.dumps(GOOD_MANIFEST).encode()),
               ("layouts.json", b"{}"), ("config.json", b"{}")]),
         "not in its manifest"),
        (_zip([("manifest.json", json.dumps(GOOD_MANIFEST).encode()),
               ("layouts.json", b"{}"), ("../gt7.db", b"x")]),
         "not something a backup holds"),
        (_zip([("manifest.json", json.dumps(GOOD_MANIFEST).encode()),
               ("layouts.json", b"{}"), ("track-bundles/compiled/ring.json", b"{}")]),
         "not something a backup holds"),
        (_zip([("manifest.json", json.dumps(GOOD_MANIFEST).encode()
                + b" " * restore.SMALL_MAX), ("layouts.json", b"{}")]),
         "more than this version reads"),
    ],
)
async def test_a_file_that_is_not_a_backup_is_refused(installs, body, why) -> None:
    new = await installs("new")
    resp = await upload(new, body)
    assert resp.status_code == 400
    assert why in resp.json()["detail"]
    assert not list(new.data_dir.glob(".restore-*"))


async def test_a_name_held_twice_is_refused(installs) -> None:
    with pytest.warns(UserWarning):
        body = _zip([
            ("manifest.json", json.dumps(GOOD_MANIFEST).encode()),
            ("layouts.json", b"{}"),
            ("layouts.json", b"{}"),
        ])
    new = await installs("new")
    resp = await upload(new, body)
    assert resp.status_code == 400
    assert "two entries under one name" in resp.json()["detail"]


async def test_a_document_that_does_not_read_refuses_its_part_only(installs) -> None:
    config = {
        "format": backup.CONFIG_FORMAT, "version": 1,
        "values": {"race_engineer_units": "imperial"},
    }
    body = craft({"layouts.json": "{broken", "config.json": json.dumps(config)})
    new = await installs("new")
    described, resp = await restore_parts(new, body, ["layouts"])
    assert "layouts.json does not read" in described["parts"]["layouts"]["refused"]
    assert resp.status_code == 409
    resp = await new.client.post(
        f"/api/admin/restore/{described['id']}", json={"items": ["config"]}
    )
    assert resp.status_code == 200, resp.text


async def test_a_part_the_backup_does_not_hold_is_refused(installs) -> None:
    body = craft({"layouts.json": json.dumps(
        {"format": backup.LAYOUTS_FORMAT, "version": 1, "layouts": []}
    )})
    new = await installs("new")
    _, resp = await restore_parts(new, body, ["data"])
    assert resp.status_code == 400
    assert "holds no Data" in resp.json()["detail"]


async def test_an_upload_that_would_fill_the_disk_is_refused(installs, monkeypatch) -> None:
    new = await installs("new")
    usage = type("Usage", (), {"free": restore.SPACE_MARGIN + 10, "total": 0, "used": 0})
    monkeypatch.setattr("app.storage.restore.shutil.disk_usage", lambda _: usage)
    resp = await upload(new, b"x" * 100)
    assert resp.status_code == 507
    assert not list(new.data_dir.glob(".restore-*"))


async def test_surveys_that_would_unpack_past_the_disk_are_refused(installs, monkeypatch) -> None:
    # A survey log of one repeated byte: 60 MB that compresses to almost nothing.
    log = b"x" * (60 << 20)
    body = craft({"surface_survey_bomb.jsonl": log}, surveys={"logs": 1})
    assert len(body) < 1 << 20
    new = await installs("new")
    up = await upload(new, body)
    assert up.status_code == 200, up.text
    usage = type("Usage", (), {"free": 32 << 20, "total": 0, "used": 0})
    monkeypatch.setattr("app.api.restore.shutil.disk_usage", lambda _: usage)
    described = (await new.client.get("/api/admin/restore")).json()
    assert "MB is free here" in described["parts"]["surveys"]["refused"]
    resp = await new.client.post(
        f"/api/admin/restore/{up.json()['id']}", json={"items": ["surveys"]}
    )
    assert resp.status_code == 409
    assert not (new.data_dir / "surface_survey_bomb.jsonl").exists()
    # Data and surveys together are measured together, too.
    room = restore.SPACE_MARGIN + (61 << 20)
    assert restore.check_room(described_found(new), ["surveys"], room) is None
    with pytest.raises(restore.NotEnoughSpace):
        restore.check_room(described_found(new), ["surveys"], room - (2 << 20))


def described_found(install: Install) -> dict:
    upload = install.service.restores.get()
    assert upload is not None
    return upload.found


async def test_files_past_what_an_import_takes_fail_alone(installs) -> None:
    # A long survey writes a log past the 64 MB an import takes; the backup
    # carries it whatever its size, and so must a restore. A bundle that
    # large is read whole to be validated, so it is listed instead, and
    # neither stops the rest of the backup going back.
    big_log = b'{"meta": {}}\n' + b"x" * (65 << 20)
    config = {
        "format": backup.CONFIG_FORMAT, "version": 1,
        "values": {"race_engineer_units": "imperial"},
    }
    body = craft(
        {
            "surface_survey_long.jsonl": big_log,
            "track-bundles/huge.json": b" " * (restore.DOCUMENT_MAX + 1),
            "track-bundles/ring.json": json.dumps(_foreign_bundle(track="Ring")),
            "layouts.json": b" " * (restore.SMALL_MAX + 1),
            "config.json": json.dumps(config),
        },
        surveys={"bundles": 2, "logs": 1},
    )
    new = await installs("new")
    described, resp = await restore_parts(new, body, ["config"])
    assert resp.status_code == 200, resp.text
    assert new.service.settings.race_engineer_units == "imperial"
    assert "more than this version reads" in described["parts"]["layouts"]["refused"]
    assert described["parts"]["surveys"]["refused"] == ""

    _, resp = await restore_parts(new, body, ["surveys"])
    assert resp.status_code == 200, resp.text
    report = resp.json()["surveys"]
    assert report["logs"] == ["surface_survey_long.jsonl"]
    assert (new.data_dir / "surface_survey_long.jsonl").stat().st_size == len(big_log)
    assert [b["track"] for b in report["bundles"]] == ["Ring"]
    assert report["failed"] == [{
        "file": "track-bundles/huge.json",
        "error": "track-bundles/huge.json is 65 MB, more than this version reads (64 MB)",
    }]


async def test_an_upload_that_expires_while_it_is_applied_goes_afterwards(installs) -> None:
    body = craft({"layouts.json": json.dumps(
        {"format": backup.LAYOUTS_FORMAT, "version": 1, "layouts": []}
    )})
    new = await installs("new")
    up = await upload(new, body)
    restores = new.service.restores
    held = restores.get(up.json()["id"])
    assert held is not None
    with pytest.raises(restore.RestoreError):
        async with restores.applying(held):
            restores._expire(held)
            assert held.path.exists()
            raise restore.RestoreError("the apply failed")
    assert not held.path.exists()
    assert restores.get() is None


async def test_restoring_needs_the_token(installs) -> None:
    new = await installs("new", admin_token=ADMIN_TOKEN)
    body = craft({"layouts.json": json.dumps(
        {"format": backup.LAYOUTS_FORMAT, "version": 1, "layouts": []}
    )})
    assert (await upload(new, body)).status_code == 401
    assert (await new.client.get("/api/admin/restore")).status_code == 401
    up = await upload(new, body, headers={"X-API-Key": ADMIN_TOKEN})
    assert up.status_code == 200
    resp = await new.client.post(
        f"/api/admin/restore/{up.json()['id']}", json={"items": ["layouts"]}
    )
    assert resp.status_code == 401


async def test_a_new_upload_replaces_the_last_and_forgetting_deletes_it(installs) -> None:
    body = craft({"layouts.json": json.dumps(
        {"format": backup.LAYOUTS_FORMAT, "version": 1, "layouts": []}
    )})
    new = await installs("new")
    first = (await upload(new, body)).json()
    second = (await upload(new, body)).json()
    assert len(list(new.data_dir.glob(".restore-*.zip"))) == 1
    resp = await new.client.post(f"/api/admin/restore/{first['id']}", json={"items": ["layouts"]})
    assert resp.status_code == 404
    assert (await new.client.get("/api/admin/restore")).json()["id"] == second["id"]
    assert (await new.client.delete(f"/api/admin/restore/{second['id']}")).status_code == 200
    assert not list(new.data_dir.glob(".restore-*"))


async def test_a_restore_waits_for_a_survey_to_stop(installs) -> None:
    old = await installs("old")
    await drive_laps(old.service, laps=1)
    body = await make_backup(old, ["data"])
    new = await installs("new")
    new.service.survey.active = True
    _, resp = await restore_parts(new, body, ["data"])
    new.service.survey.active = False
    assert resp.status_code == 409
    assert "survey" in resp.json()["detail"]


# --- one job filling the disk at a time ---------------------------------------------


async def held_body(body: bytes, release: asyncio.Event):
    """An upload whose browser sends the first bytes, then waits."""
    yield body[:10]
    await release.wait()
    yield body[10:]


async def holding(install: Install, holder: str) -> None:
    for _ in range(200):
        if install.service.restores.disk.holder == holder:
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"the disk never held {holder!r}")


async def spooling(install: Install) -> None:
    """Until an upload has begun writing its .part file."""
    for _ in range(200):
        if list(install.data_dir.glob(".restore-*.zip.part")):
            return
        await asyncio.sleep(0.005)
    raise AssertionError("no upload started")


LAYOUTS_ONLY = craft({"layouts.json": json.dumps(
    {"format": backup.LAYOUTS_FORMAT, "version": 1, "layouts": []}
)})


async def test_a_second_upload_at_the_same_time_is_refused(installs) -> None:
    # Two Settings tabs uploading at once would each count on the same free
    # space; the second is told why it cannot, before it writes a byte.
    new = await installs("new")
    release = asyncio.Event()
    first = asyncio.create_task(upload(new, held_body(LAYOUTS_ONLY, release)))
    await spooling(new)
    second = await upload(new, LAYOUTS_ONLY)
    assert second.status_code == 409
    assert second.json()["detail"] == "a backup is being uploaded; try again once that is done"
    assert len(list(new.data_dir.glob(".restore-*"))) == 1  # the first's .part alone

    release.set()
    assert (await first).status_code == 200
    assert (await upload(new, LAYOUTS_ONLY)).status_code == 200


async def test_nothing_else_fills_the_disk_while_an_upload_does(installs) -> None:
    new = await installs("new")
    await drive_laps(new.service, laps=1)
    waiting = (await upload(new, LAYOUTS_ONLY)).json()
    release = asyncio.Event()
    uploading = asyncio.create_task(upload(new, held_body(LAYOUTS_ONLY, release)))
    await spooling(new)

    # A restore is refused ...
    resp = await new.client.post(
        f"/api/admin/restore/{waiting['id']}", json={"items": ["layouts"]}
    )
    assert resp.status_code == 409
    assert "being uploaded" in resp.json()["detail"]
    # ... a backup of the recordings waits its turn ...
    snapshot = asyncio.create_task(
        new.client.post("/api/admin/backup", json={"parts": ["data"]})
    )
    await asyncio.sleep(0.05)
    assert not snapshot.done()
    # ... and one that copies no database does not need to.
    made = await new.client.post("/api/admin/backup", json={"parts": ["layouts"]})
    assert made.status_code == 200

    release.set()
    assert (await uploading).status_code == 200
    assert (await snapshot).status_code == 200


async def test_a_cancelled_snapshot_holds_the_disk_until_its_copy_ends(
    installs, monkeypatch
) -> None:
    new = await installs("new")
    await drive_laps(new.service, laps=1)
    started, release = threading.Event(), threading.Event()

    def held_snapshot(src, dest, drop, **kw):
        started.set()
        assert release.wait(5)
        dest.write_bytes(b"orphan")
        return {"revision": "r", "sessions": 0, "laps": 0}

    monkeypatch.setattr(backup, "snapshot_database", held_snapshot)
    making = asyncio.create_task(new.service.backups.prepare(["data"], new.db_path, "r"))
    assert await asyncio.to_thread(started.wait, 5)
    making.cancel()
    with pytest.raises(asyncio.CancelledError):
        await making
    # The request is gone, but its thread is still writing a copy.
    resp = await upload(new, LAYOUTS_ONLY)
    assert resp.status_code == 409
    assert "being made" in resp.json()["detail"]

    release.set()
    await holding(new, "")
    assert (await upload(new, LAYOUTS_ONLY)).status_code == 200
    assert not list(new.data_dir.glob(".backup-*"))


# --- what runs beside a restore --------------------------------------------------------


async def test_a_request_in_progress_finishes_before_the_swap_and_new_ones_wait(
    installs,
) -> None:
    """A request that reads ids in one session and writes by them in the next
    (as /api/tracks/identify does) must not straddle the swap: the old
    database's session 1 and the restored one's are different sessions."""
    old = await installs("old")
    await drive_laps(old.service, laps=1)
    zf = zipfile.ZipFile(io.BytesIO(await make_backup(old, ["data"])))
    new = await installs("new")
    await drive_laps(new.service, laps=3)
    staged = new.data_dir / ".restore-test.db"
    staged.write_bytes(zf.read("gt7.db"))
    read, write = asyncio.Event(), asyncio.Event()

    async def name_the_first_session() -> dict[str, int]:
        first = (await new.service.repo.list_sessions())[0]["id"]
        read.set()
        await write.wait()
        await new.service.repo.set_session_track(first, "Old circuit")
        return {"id": first}

    add_route_first(new, "/api/test/name-the-first-session", name_the_first_session)
    naming = asyncio.create_task(new.client.post("/api/test/name-the-first-session"))
    await asyncio.wait_for(read.wait(), 5)
    swap = asyncio.create_task(new.service.replace_database(staged))
    await asyncio.sleep(0.05)
    # The swap waits for the request, and holds back the ones after it.
    assert not swap.done()
    assert new.service.requests.is_shut
    listing = asyncio.create_task(new.client.get("/api/sessions"))
    await asyncio.sleep(0.05)
    assert not listing.done()

    write.set()
    first = (await asyncio.wait_for(naming, 5)).json()["id"]
    await asyncio.wait_for(swap, 5)
    # The name went to the session the request read, in the kept database ...
    [kept] = kept_copies(new)
    with sqlite3.connect(kept) as db:
        assert db.execute(
            "SELECT track_name FROM sessions WHERE id = ?", (first,)
        ).fetchone() == ("Old circuit",)
    # ... and the request held back saw only the restored recordings.
    sessions = (await listing).json()
    assert len(sessions) == 1
    assert all(s["track_name"] != "Old circuit" for s in sessions)


def add_route_first(install: Install, path: str, endpoint) -> None:
    """A route ahead of the app's own: a built frontend is mounted at "/"."""
    install.app.add_api_route(path, endpoint, methods=["POST"])
    install.app.router.routes.insert(0, install.app.router.routes.pop())


async def test_telemetry_is_not_restarted_by_a_request_in_the_middle_of_a_swap(
    installs, monkeypatch
) -> None:
    """A request restarting telemetry (Restart, or a port, scenario or source
    change) that arrives during the swap waits until the restore has started
    telemetry again; otherwise the restore's own start would be a second."""
    new, staged, entered, release = await staged_swap(installs, monkeypatch)
    source = new.service.source

    async def restart_source() -> None:  # what switch_source does, minus a new object
        await source.stop()
        await source.start()

    async def database_replaced() -> None:
        # main.lifespan's does database work, so other tasks get turns.
        await new.service.repo.stats()
        await asyncio.sleep(0.01)

    monkeypatch.setattr(new.service, "restart_source", restart_source)
    new.service.database_replaced = database_replaced
    swap = asyncio.create_task(new.service.replace_database(staged))
    assert await asyncio.to_thread(entered.wait, 5)
    restarting = asyncio.create_task(new.client.post("/api/admin/restart-source"))
    await asyncio.sleep(0.05)
    assert not restarting.done()

    release.set()
    await asyncio.wait_for(swap, 5)
    assert (await asyncio.wait_for(restarting, 5)).status_code == 200
    assert source.events == ["stop", "start", "stop", "start"]  # type: ignore[attr-defined]


async def test_telemetry_restarted_by_a_request_in_progress_is_stopped_after_it(
    installs, monkeypatch
) -> None:
    """A request already running when the restore begins may start telemetry
    as it finishes; the restore stops telemetry only once it has."""
    old = await installs("old")
    await drive_laps(old.service, laps=1)
    zf = zipfile.ZipFile(io.BytesIO(await make_backup(old, ["data"])))
    new = await installs("new")
    staged = new.data_dir / ".restore-test.db"
    staged.write_bytes(zf.read("gt7.db"))
    source = new.service.source
    started, finish = asyncio.Event(), asyncio.Event()

    async def change_the_port() -> dict[str, str]:
        started.set()
        await finish.wait()
        await source.stop()
        await source.start()
        return {"status": "restarted"}

    add_route_first(new, "/api/test/change-the-port", change_the_port)
    changing = asyncio.create_task(new.client.post("/api/test/change-the-port"))
    await asyncio.wait_for(started.wait(), 5)
    swap = asyncio.create_task(new.service.replace_database(staged))
    await asyncio.sleep(0.05)
    assert source.events == []  # type: ignore[attr-defined]

    finish.set()
    assert (await asyncio.wait_for(changing, 5)).status_code == 200
    await asyncio.wait_for(swap, 5)
    assert source.events == ["stop", "start", "stop", "start"]  # type: ignore[attr-defined]
    assert source.running  # type: ignore[attr-defined]


def test_survey_file_writers_take_turns(tmp_path) -> None:
    """A merge (a restore's, a shared pull's) reads the bundle, works, and
    writes it back; a corner labelled meanwhile must not be lost to it."""
    write_bundle(tmp_path)
    doc = track_bundle.validate_document(_foreign_bundle(track="Ring", source="feed0000beef"))
    merging, release = threading.Event(), threading.Event()
    real_write = track_bundle._write

    def slow_write(data_dir, track, document):
        merging.set()
        assert release.wait(5)
        return real_write(data_dir, track, document)

    corner = {"n": 1, "name": "T1", "apex": {"x": 1.0, "z": 0.0}}
    track_bundle._write = slow_write
    try:
        merge = threading.Thread(target=track_bundle.merge_document, args=(tmp_path, doc))
        merge.start()
        assert merging.wait(5)
        track_bundle._write = real_write
        label = threading.Thread(
            target=track_bundle.set_authored,
            args=(tmp_path, "Ring"),
            kwargs={"corners": [track_bundle.validate_corner(corner, 0)]},
        )
        label.start()
        label.join(0.1)
        assert label.is_alive()  # waits for the merge in progress
        release.set()
        merge.join(5)
        label.join(5)
    finally:
        track_bundle._write = real_write
        release.set()
    ring = track_bundle.load(tmp_path, "Ring")
    assert [c["name"] for c in ring["corners"]] == ["T1"]
    assert set(ring["meta"]["source_runs"]) == {STRANGER, "feed0000beef"}


async def test_a_restore_cancelled_mid_surveys_finishes_them_first(
    installs, monkeypatch
) -> None:
    old = await installs("old")
    write_bundle(old.data_dir)
    body = await make_backup(old, ["surveys"])
    new = await installs("new")
    changed: list[str] = []
    monkeypatch.setattr(
        new.service, "bundle_changed", lambda track, road=True: changed.append(track)
    )
    merging, release = threading.Event(), threading.Event()
    real = restore.restore_surveys

    def slow_surveys(upload_path, data_dir):
        merging.set()
        assert release.wait(5)
        return real(upload_path, data_dir)

    monkeypatch.setattr(restore, "restore_surveys", slow_surveys)
    up = (await upload(new, body)).json()
    applying = asyncio.create_task(
        new.client.post(f"/api/admin/restore/{up['id']}", json={"items": ["surveys"]})
    )
    assert await asyncio.to_thread(merging.wait, 5)
    applying.cancel()
    await asyncio.sleep(0.05)
    # The request is cancelled, but the merge goes on, and holds the disk.
    assert not applying.done()
    assert new.service.restores.disk.holder == backup.RESTORING
    refused = await upload(new, body)
    assert refused.status_code == 409

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await applying
    # Finished: merged, re-judged and re-synced, the disk let go, and the
    # upload gone, as after any restore.
    assert track_bundle.load(new.data_dir, "Ring") is not None
    assert changed == ["Ring"]
    assert new.service.restores.disk.holder == ""
    assert new.service.restores.get() is None
    assert not list(new.data_dir.glob(".restore-*"))


def test_leftovers_of_an_interrupted_restore_are_cleared_at_start(tmp_path) -> None:
    (tmp_path / ".restore-abc.zip.part").write_bytes(b"x")
    (tmp_path / ".restore-abc.db").write_bytes(b"x")
    (tmp_path / "gt7.db.before-restore-20261022-101010").write_bytes(b"kept")
    restore.Restores(tmp_path)
    assert [p.name for p in tmp_path.iterdir()] == ["gt7.db.before-restore-20261022-101010"]


# --- nothing writes while the file changes hands ---------------------------------------


async def test_the_gate_waits_for_sessions_in_progress_and_holds_new_ones(installs) -> None:
    install = await installs("one")
    repo = install.service.repo
    order: list[str] = []
    release = asyncio.Event()

    async def long_session() -> None:
        async with repo._sf() as db:
            order.append("long started")
            await release.wait()
            await db.commit()
        order.append("long done")

    async def swap() -> None:
        async with repo.gate.shut(5.0):
            order.append("swapping")
            await asyncio.sleep(0.05)
        order.append("swapped")

    async def late_session() -> None:
        order.append("late asked")
        await repo.stats()
        order.append("late done")

    first = asyncio.create_task(long_session())
    await asyncio.sleep(0.01)
    swapping = asyncio.create_task(swap())
    await asyncio.sleep(0.01)
    late = asyncio.create_task(late_session())
    await asyncio.sleep(0.01)
    assert order == ["long started", "late asked"]
    release.set()
    await asyncio.gather(first, swapping, late)
    assert order == [
        "long started", "late asked", "long done", "swapping", "swapped", "late done",
    ]


async def test_a_session_that_outlasts_the_wait_refuses_the_swap(installs) -> None:
    install = await installs("one")
    repo = install.service.repo
    release = asyncio.Event()

    async def long_session() -> None:
        async with repo._sf():
            await release.wait()

    task = asyncio.create_task(long_session())
    await asyncio.sleep(0.01)
    with pytest.raises(TimeoutError):
        async with repo.gate.shut(0.05):
            pass
    # Open again: new sessions are not held behind a swap that gave up.
    assert (await repo.stats())["laps"] == 0
    release.set()
    await task


async def test_a_session_cancelled_on_its_way_out_gives_its_place_back(installs) -> None:
    install = await installs("one")
    repo = install.service.repo
    inside = asyncio.Event()

    async def held() -> None:
        async with repo._sf():
            inside.set()
            await asyncio.sleep(10)

    tasks = [asyncio.create_task(held()) for _ in range(3)]
    await inside.wait()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    # Nothing still counted as open: a swap shuts the gate at once.
    async with repo.gate.shut(0.05):
        assert repo.gate.is_shut
    assert not repo.gate.is_shut


async def test_a_session_inside_a_session_passes_the_gate(installs) -> None:
    install = await installs("one")
    repo = install.service.repo
    async with repo._sf():
        # Would queue behind a swap waiting for the outer session, forever.
        assert (await repo.stats())["sessions"] == 0
