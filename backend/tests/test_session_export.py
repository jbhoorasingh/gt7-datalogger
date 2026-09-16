"""Whole-session export as a ZIP (#76).

The archive is only useful if its lap files are the per-lap export documents
unchanged — that is what makes them importable today, one at a time, and what
the session import to come can rely on.
"""

import io
import json
import zipfile

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models import SimulatorFlags
from app.processing.cars import CarDatabase
from app.processing.laps import SessionInfo
from app.service import TelemetryService
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository
from app.telemetry.packet import build_packet, parse_packet

ON_TRACK = int(SimulatorFlags.CAR_ON_TRACK)


@pytest.fixture
async def client(tmp_path):
    settings = Settings(source="udp", db_path=tmp_path / "test.db", ws_rate=1000)
    engine = make_engine(settings.db_path)
    await init_db(engine)
    repo = Repository(make_session_factory(engine))
    service = TelemetryService(settings, repo, CarDatabase())
    service.processor.min_lap_ticks = 1

    app = create_app()
    app.router.lifespan_context = None  # type: ignore[assignment]
    app.state.service = service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, service
    await engine.dispose()


async def drive_laps(service: TelemetryService, laps: int) -> None:
    for lap in range(1, laps + 1):
        for tick in range(60):
            await service._on_packet(
                parse_packet(
                    build_packet(
                        packet_id=lap * 100 + tick,
                        current_lap=lap,
                        last_lap_time_ms=59_000 + lap if lap > 1 else -1,
                        speed_mps=40.0,
                        throttle=255,
                        flags=ON_TRACK,
                        car_id=7,
                    )
                )
            )
    await service._on_packet(
        parse_packet(
            build_packet(
                current_lap=laps + 1, last_lap_time_ms=59_500, flags=ON_TRACK, car_id=7
            )
        )
    )


async def test_session_archive_holds_every_lap_and_the_session(client) -> None:
    c, service = client
    await drive_laps(service, laps=2)
    session_id = service.session_id
    await c.patch(f"/api/sessions/{session_id}", json={"note": "baseline", "tags": ["wet"]})
    laps = sorted(
        (await c.get(f"/api/sessions/{session_id}/laps")).json(), key=lambda lap: lap["number"]
    )
    await c.patch(
        f"/api/laps/{laps[0]['id']}", json={"best_override": False, "exclude_reason": "contact"}
    )

    resp = await c.get(f"/api/sessions/{session_id}/export.zip")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert (
        resp.headers["content-disposition"]
        == f'attachment; filename="gt7-session-{session_id}.zip"'
    )
    assert int(resp.headers["content-length"]) == len(resp.content)

    folder = f"gt7-session-{session_id}"
    lap_files = [f"laps/lap-{lap['number']:03d}-{lap['id']}.json" for lap in laps]
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert zf.testzip() is None
        assert sorted(zf.namelist()) == sorted(
            [f"{folder}/session.json", *(f"{folder}/{name}" for name in lap_files)]
        )
        manifest = json.loads(zf.read(f"{folder}/session.json"))
        docs = [json.loads(zf.read(f"{folder}/{name}")) for name in lap_files]

    assert manifest["format"] == "gt7-datalogger-session"
    assert manifest["version"] == 1
    assert manifest["exported_at"]
    # The session row exactly as the sessions listing gives it.
    listed = next(s for s in (await c.get("/api/sessions")).json() if s["id"] == session_id)
    assert manifest["session"] == listed
    assert manifest["session"]["note"] == "baseline"
    assert manifest["session"]["tags"] == ["wet"]
    assert manifest["laps"] == [
        {
            "file": name,
            "id": lap["id"],
            "number": lap["number"],
            "time_ms": lap["time_ms"],
            "counts_for_best": lap["number"] != 1,
        }
        for name, lap in zip(lap_files, laps, strict=True)
    ]

    # Each lap file is the single-lap export, unchanged — and importable.
    for doc, lap in zip(docs, laps, strict=True):
        assert doc == (await c.get(f"/api/laps/{lap['id']}/export")).json()
        assert (await c.post("/api/laps/import", json=doc)).status_code == 200
    assert docs[0]["lap"]["exclude_reason"] == "contact"
    assert len(docs[0]["lap"]["samples"]["speed"]) == 60


async def test_a_session_without_laps_still_exports(client) -> None:
    c, service = client
    session_id = await service.repo.create_session(
        SessionInfo(car_id=7, started_at="2026-09-16T00:00:00Z"), None
    )
    resp = await c.get(f"/api/sessions/{session_id}/export.zip")
    assert resp.status_code == 200
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert zf.namelist() == [f"gt7-session-{session_id}/session.json"]
        manifest = json.loads(zf.read(f"gt7-session-{session_id}/session.json"))
    assert manifest["laps"] == []
    assert manifest["session"]["car_name"] == "Car #7"


async def test_exporting_a_missing_session_is_404(client) -> None:
    c, _ = client
    resp = await c.get("/api/sessions/9999/export.zip")
    assert resp.status_code == 404
