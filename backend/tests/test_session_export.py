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
from app.storage import archive
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
            [
                f"{folder}/session.json",
                # The lap analysis document (#115); tests/test_lap_analysis.py
                # is where what is in it is checked.
                f"{folder}/analysis.json",
                *(f"{folder}/{name}" for name in lap_files),
            ]
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


# --- every lap at once ------------------------------------------------------


async def _two_sessions(service: TelemetryService) -> list[int]:
    """Two sessions of laps, and an empty one between them."""
    await drive_laps(service, laps=2)
    first = service.session_id
    assert first is not None
    empty = await service.repo.create_session(
        SessionInfo(car_id=7, started_at="2026-09-16T00:00:00Z"), None
    )
    service.session_id = None  # the next packets open a fresh session
    await drive_laps(service, laps=1)
    second = service.session_id
    assert second is not None and second not in (first, empty)
    return [first, second]


async def test_all_laps_archive_is_every_session_archive(client) -> None:
    c, service = client
    session_ids = await _two_sessions(service)

    resp = await c.get("/api/export/laps.zip")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    disposition = resp.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="gt7-laps-')
    root = disposition.split('"')[1].removesuffix(".zip")

    docs: list[dict] = []
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert zf.testzip() is None
        names = set(zf.namelist())
        # One folder per session with laps; the empty one is left out.
        assert {n.split("/")[1] for n in names} == {f"gt7-session-{s}" for s in session_ids}
        assert all(n.startswith(f"{root}/") for n in names)
        for session_id in session_ids:
            folder = f"{root}/gt7-session-{session_id}"
            manifest = json.loads(zf.read(f"{folder}/session.json"))
            laps = (await c.get(f"/api/sessions/{session_id}/laps")).json()
            assert sorted(e["id"] for e in manifest["laps"]) == sorted(lap["id"] for lap in laps)
            # The session archive's manifest, less the analysis it leaves out.
            assert "analysis" not in manifest
            assert f"{folder}/analysis.json" not in names
            for entry in manifest["laps"]:
                doc = json.loads(zf.read(f"{folder}/{entry['file']}"))
                assert doc == (await c.get(f"/api/laps/{entry['id']}/export")).json()
                docs.append(doc)
    # Imported only once everything is compared: an import adds a lap.
    for doc in docs:
        assert (await c.post("/api/laps/import", json=doc)).status_code == 200


async def test_all_laps_csv_archive_holds_each_laps_csv(client) -> None:
    c, service = client
    session_ids = await _two_sessions(service)

    resp = await c.get("/api/export/laps-csv.zip")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    root = resp.headers["content-disposition"].split('"')[1].removesuffix(".zip")
    assert root.startswith("gt7-laps-csv-")

    expected: dict[str, str] = {}
    for session_id in session_ids:
        for lap in (await c.get(f"/api/sessions/{session_id}/laps")).json():
            name = f"{root}/gt7-session-{session_id}/lap-{lap['number']:03d}-{lap['id']}.csv"
            expected[name] = (await c.get(f"/api/laps/{lap['id']}/export.csv")).text
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert zf.testzip() is None
        assert sorted(zf.namelist()) == sorted(expected)
        for name, text in expected.items():
            assert zf.read(name).decode() == text


async def test_all_laps_archives_of_an_empty_database_are_valid(client) -> None:
    c, _ = client
    for url in ("/api/export/laps.zip", "/api/export/laps-csv.zip"):
        resp = await c.get(url)
        assert resp.status_code == 200
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            assert zf.namelist() == []


async def test_all_laps_archive_streams_a_lap_at_a_time(client) -> None:
    _, service = client
    await drive_laps(service, laps=3)
    # Read off the generator itself: the test transport buffers a response
    # whole, so it cannot show whether one was streamed.
    for stream in (
        archive.stream_all_laps(service.repo, "root"),
        archive.stream_all_laps_csv(service.repo, "root", service.cars.name),
    ):
        chunks = [chunk async for chunk in stream]
        # A chunk per lap and one for the directory, not one blob at the end.
        assert len(chunks) == 4
        assert all(chunks)
        with zipfile.ZipFile(io.BytesIO(b"".join(chunks))) as zf:
            assert zf.testzip() is None
            assert len([n for n in zf.namelist() if "/lap-" in n]) == 3
