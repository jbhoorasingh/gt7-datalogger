"""API integration tests against an in-memory pipeline (no UDP, no network)."""

import asyncio
import time

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models import SimulatorFlags
from app.processing.cars import CarDatabase
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


async def drive_laps(service: TelemetryService, laps: int = 2) -> None:
    # GT7 keeps last_lap_time_ms set to the previous lap's time on every packet.
    for lap in range(1, laps + 1):
        for tick in range(60):
            await service._on_packet(
                parse_packet(
                    build_packet(
                        packet_id=lap * 100 + tick,
                        current_lap=lap,
                        last_lap_time_ms=59_000 if lap > 1 else -1,
                        speed_mps=40.0 + lap,
                        throttle=255,
                        fuel_level=100.0 - lap,
                        flags=ON_TRACK,
                        car_id=7,
                    )
                )
            )
    # Final boundary completes the last lap
    await service._on_packet(
        parse_packet(
            build_packet(
                current_lap=laps + 1,
                last_lap_time_ms=59_000,
                fuel_level=100.0 - laps - 1.5,
                flags=ON_TRACK,
                car_id=7,
            )
        )
    )


async def test_empty_sessions_are_dropped(client) -> None:
    """Menu bounces open sessions that never see a lap; when a new session
    starts, the previous empty one is deleted instead of piling up."""
    c, service = client
    # Session A: one real lap
    await drive_laps(service, laps=1)
    # Session B: car change, no laps driven
    await service._on_packet(
        parse_packet(build_packet(current_lap=1, flags=ON_TRACK, car_id=42))
    )
    # Session C: another car change — session B was empty and must vanish
    await service._on_packet(
        parse_packet(build_packet(current_lap=1, flags=ON_TRACK, car_id=43))
    )
    sessions = (await c.get("/api/sessions")).json()
    lap_counts = [s["lap_count"] for s in sessions]
    assert len(sessions) == 2  # A (1 lap) + C (current, still empty)
    assert sorted(lap_counts) == [0, 1]


async def test_health(client) -> None:
    c, _ = client
    resp = await c.get("/api/health")
    assert resp.status_code == 200


@pytest.mark.parametrize("connected", [True, False])
@pytest.mark.parametrize(
    "flags", [ON_TRACK, ON_TRACK | int(SimulatorFlags.PAUSED), 0, int(SimulatorFlags.LOADING)]
)
@pytest.mark.parametrize("has_laps", [True, False])
async def test_delete_current_session_preserves_recording(
    client, has_laps, flags, connected,
) -> None:
    """Deleting the recorder's session must not orphan subsequent laps/results,
    including pauses, inactive pit packets and connection interruptions."""
    c, service = client
    if has_laps:
        await drive_laps(service, laps=1)
    else:
        await service._on_packet(
            parse_packet(build_packet(current_lap=0, flags=ON_TRACK, car_id=7))
        )
    await service._on_packet(parse_packet(build_packet(
        current_lap=2 if has_laps else 0, flags=flags, car_id=7,
    )))
    session_id = service.session_id
    before = (await c.get("/api/sessions")).json()
    before_laps = (await c.get(f"/api/sessions/{session_id}/laps")).json()
    service.source._last_packet_at = time.monotonic() - (0 if connected else 6)

    response = await c.delete(f"/api/sessions/{session_id}")

    assert response.status_code == 409
    assert (await c.get("/api/sessions")).json() == before
    assert (await c.get(f"/api/sessions/{session_id}/laps")).json() == before_laps
    service.recording = True
    await drive_race(service, race_laps=3, start_lap=2 if has_laps else 1)
    finished = (await c.get("/api/sessions")).json()[0]
    assert finished["id"] == session_id
    assert finished["lap_count"] == 3
    assert finished["final_position"] == 2
    assert finished["final_total_positions"] == 8
    assert finished["race_time_ms"] == (181_000 if has_laps else 183_000)


@pytest.mark.parametrize("connected", [True, False])
@pytest.mark.parametrize("has_laps", [True, False])
async def test_delete_stopped_current_session_and_resume(client, connected, has_laps):
    c, service = client
    if has_laps:
        await drive_laps(service, laps=1)
    else:
        await service._on_packet(
            parse_packet(build_packet(current_lap=1, flags=ON_TRACK, car_id=7))
        )
    session_id = service.session_id
    await c.post("/api/control/recording", json={"recording": False})
    service.source._last_packet_at = time.monotonic() - (0 if connected else 6)

    response = await c.delete(f"/api/sessions/{session_id}")

    assert response.status_code == 200
    assert (await c.get("/api/sessions")).json() == []
    assert (await c.get("/api/status")).json()["session_id"] is None
    assert (await c.post("/api/control/log-lap-now")).status_code == 409
    await c.post("/api/control/recording", json={"recording": True})
    # Continue the same car/lap counter: no natural session boundary can
    # rescue a stale recorder that still points at the deleted session.
    await drive_race(service, race_laps=3, start_lap=2)
    sessions = (await c.get("/api/sessions")).json()
    assert len(sessions) == 1
    assert sessions[0]["lap_count"] == 2
    assert sessions[0]["final_position"] == 2
    assert sessions[0]["final_total_positions"] == 8
    laps = await service.repo.list_laps(sessions[0]["id"])
    assert sorted(lap["number"] for lap in laps) == [2, 3]


@pytest.mark.parametrize("manual", [True, False])
async def test_stop_and_delete_session_wait_for_inflight_lap(client, monkeypatch, manual):
    c, service = client
    await drive_laps(service, laps=1)
    session_id = service.session_id
    saving = asyncio.Event()
    release = asyncio.Event()
    save_lap = service.repo.save_lap

    async def delayed_save(*args, **kwargs):
        saving.set()
        await release.wait()
        return await save_lap(*args, **kwargs)

    monkeypatch.setattr(service.repo, "save_lap", delayed_save)
    logging = asyncio.create_task(
        c.post("/api/control/log-lap-now") if manual else service._on_packet(
            parse_packet(build_packet(
                current_lap=3, last_lap_time_ms=61_000, flags=ON_TRACK, car_id=7,
            ))
        )
    )
    try:
        await asyncio.wait_for(saving.wait(), timeout=5)
        stopping = asyncio.create_task(c.post("/api/control/recording", json={"recording": False}))
        await asyncio.wait({stopping}, timeout=0.1)
        assert not stopping.done()
        deletion = asyncio.create_task(c.delete(f"/api/sessions/{session_id}"))
        await asyncio.wait({deletion}, timeout=0.1)
        assert not deletion.done()
    finally:
        release.set()
        await logging
        await stopping
        await deletion
    assert deletion.result().status_code == 200
    assert (await c.get("/api/sessions")).json() == []
    assert (await c.get("/api/laps")).json() == []


async def test_delete_current_session_waits_for_best_override(client, monkeypatch):
    c, service = client
    await drive_laps(service, laps=2)
    await c.post("/api/control/recording", json={"recording": False})
    session_id = service.session_id
    laps = sorted(await service.repo.list_laps(session_id), key=lambda lap: lap["number"])
    await c.patch(f"/api/laps/{laps[0]['id']}", json={"best_override": True})
    reading = asyncio.Event()
    release = asyncio.Event()
    lap_samples_json = service.repo.lap_samples_json

    async def delayed_samples(lap_id):
        raw = await lap_samples_json(lap_id)
        reading.set()
        await release.wait()
        return raw

    monkeypatch.setattr(service.repo, "lap_samples_json", delayed_samples)
    override = asyncio.create_task(
        c.patch(f"/api/laps/{laps[1]['id']}", json={"best_override": False})
    )
    deletion = None
    try:
        await asyncio.wait_for(reading.wait(), timeout=5)
        deletion = asyncio.create_task(c.delete(f"/api/sessions/{session_id}"))
        await asyncio.wait({deletion}, timeout=0.1)
        assert not deletion.done()
    finally:
        release.set()
        await override
        if deletion is not None:
            await deletion
    assert deletion.result().status_code == 200
    assert service.session_id is None
    assert service._best_ref is None
    assert service._best_ref_lap is None


async def test_delete_session_during_creation_preserves_recorder(client, monkeypatch) -> None:
    """A committed session must be protected before its ID is published."""
    c, service = client
    service.source._last_packet_at = time.monotonic()
    committed = asyncio.Event()
    release = asyncio.Event()
    create_session = service.repo.create_session

    async def delayed_create(*args, **kwargs):
        session_id = await create_session(*args, **kwargs)
        committed.set()
        await release.wait()
        return session_id

    monkeypatch.setattr(service.repo, "create_session", delayed_create)
    creation = asyncio.create_task(service._on_packet(
        parse_packet(build_packet(current_lap=0, flags=ON_TRACK, car_id=7))
    ))
    try:
        await asyncio.wait_for(committed.wait(), timeout=5)
        session_id = (await c.get("/api/sessions")).json()[0]["id"]
        deletion = asyncio.create_task(c.delete(f"/api/sessions/{session_id}"))
        # Let deletion reach the committed-but-not-published window. With
        # serialization it waits; without it the real database row is lost.
        await asyncio.wait({deletion}, timeout=0.1)
    finally:
        release.set()
        await creation
    response = await deletion
    assert response.status_code == 409
    await drive_race(service, race_laps=3)
    session = (await c.get("/api/sessions")).json()[0]
    assert session["id"] == session_id
    assert session["final_position"] == 2
    assert session["race_time_ms"] == 183_000


async def test_delete_historical_session_keeps_current_session(client) -> None:
    c, service = client
    await drive_laps(service, laps=1)
    historical_id = service.session_id
    await service._on_packet(
        parse_packet(build_packet(current_lap=1, flags=ON_TRACK, car_id=42))
    )
    current_id = service.session_id

    response = await c.delete(f"/api/sessions/{historical_id}")

    assert response.status_code == 200
    assert (await c.get(f"/api/sessions/{historical_id}/laps")).json() == []
    sessions = (await c.get("/api/sessions")).json()
    assert [s["id"] for s in sessions] == [current_id]


async def test_pipeline_persists_sessions_and_laps(client) -> None:
    c, service = client
    await drive_laps(service, laps=2)

    sessions = (await c.get("/api/sessions")).json()
    assert len(sessions) == 1
    assert sessions[0]["lap_count"] == 2

    laps = (await c.get(f"/api/sessions/{sessions[0]['id']}/laps")).json()
    assert len(laps) == 2
    assert all(lap["time_ms"] == 59_000 or lap["time_ms"] > 0 for lap in laps)

    detail = (await c.get(f"/api/laps/{laps[0]['id']}")).json()
    assert "samples" in detail
    assert len(detail["samples"]["speed"]) == 60


async def test_compare_endpoint(client) -> None:
    c, service = client
    await drive_laps(service, laps=2)
    laps = (await c.get("/api/laps")).json()
    ids = [lap["id"] for lap in laps]
    resp = await c.get(f"/api/analysis/compare?laps={ids[0]},{ids[1]}&ref={ids[1]}")
    assert resp.status_code == 200
    data = resp.json()
    assert str(ids[0]) in data["laps"]
    assert "delta" in data["laps"][str(ids[0])]
    assert "pos_x" in data["laps"][str(ids[1])]["series"]
    assert "peaks_valleys" in data["laps"][str(ids[1])]


async def test_export_import_roundtrip(client) -> None:
    c, service = client
    await drive_laps(service, laps=1)
    laps = (await c.get("/api/laps")).json()
    exported = (await c.get(f"/api/laps/{laps[0]['id']}/export")).json()
    assert exported["format"] == "gt7-datalogger-lap"

    resp = await c.post("/api/laps/import", json=exported)
    assert resp.status_code == 200
    assert len((await c.get("/api/laps")).json()) == 2


async def test_import_keeps_the_salvage_marker(client) -> None:
    """A replay-salvaged lap must stay traceable after changing machines —
    the importer used to validate the flag away."""
    c, service = client
    await drive_laps(service, laps=1)
    lap_id = (await c.get("/api/laps")).json()[0]["id"]
    doc = (await c.get(f"/api/laps/{lap_id}/export")).json()
    doc["lap"]["salvaged"] = True

    resp = await c.post("/api/laps/import", json=doc)
    assert resp.status_code == 200
    imported = (await c.get(f"/api/laps/{resp.json()['id']}?samples=false")).json()
    assert imported["salvaged"] is True


async def test_import_rejects_bad_format(client) -> None:
    c, _ = client
    resp = await c.post(
        "/api/laps/import", json={"format": "nope", "version": 1, "lap": {}}
    )
    assert resp.status_code == 400


async def test_fuel_endpoint(client) -> None:
    c, service = client
    await drive_laps(service, laps=1)
    laps = (await c.get("/api/laps")).json()
    resp = await c.get(f"/api/analysis/fuel?lap_id={laps[0]['id']}")
    assert resp.status_code == 200
    assert len(resp.json()["rows"]) == 11


async def test_survey_edges_serve_the_shape_the_map_draws(client, tmp_path) -> None:
    """/survey/edges is the map's only source of border geometry.

    It must carry a resolved `kind` per metre — the frontend colours ticks and
    excludes run-off from the road fill by it, and must never have to reduce
    votes itself — with the evidence alongside for the raw inspector.
    """
    c, service = client
    survey = service.survey
    survey.start(tmp_path, track_width_m=1.6, track="Ring", track_user_set=True)
    survey._append_edge(x=10.0, z=5.0, hx=1.0, hz=0.0, side="L", kind="straddle", pid=1)
    survey._append_edge(x=10.2, z=5.0, hx=1.0, hz=0.0, side="L", kind="runoff", pid=2)

    body = (await c.get("/api/survey/edges")).json()
    assert body["total"] == 1  # one metre of border, not two points
    point = body["points"][0]
    assert point["kind"] == "runoff"  # the hand mark, resolved server-side
    # Votes are per kind per SOURCE — the installation id is what keeps two
    # people's run ordinals from being read as the same fact (#47).
    src = body["points"][0]["votes"]["runoff"]
    assert list(src.values()) == [[1, 1]]
    assert point["votes"] == {"straddle": src, "runoff": src}
    assert point["run"] == 1 and point["tw"] == 1.6
    assert {"x", "z", "hx", "hz", "side"} <= set(point)
    survey.stop()


async def test_car_category_is_persisted_and_served(client) -> None:
    """Packet C broadcasts the category ("Gr.3", "Gr.4"...) and it was being
    dropped. It is the free grouping key for "best in a Gr.3 car here" (#19),
    so it has to survive onto the session AND the lap — denormalised like
    car_id, so filtering never needs a join.
    """
    c, service = client
    for lap in range(1, 3):
        for tick in range(60):
            await service.processor.feed(
                parse_packet(build_packet(
                    fmt="C", car_id=42, car_category="Gr.3", current_lap=lap,
                    last_lap_time_ms=61_000 if lap > 1 else -1,
                    flags=ON_TRACK, packet_id=lap * 100 + tick,
                    position=(float(tick), 0.0, 0.0), surface_types="TTTT",
                ))
            )

    sessions = (await c.get("/api/sessions")).json()
    assert sessions, "a session should have been created"
    assert sessions[0]["car_category"] == "Gr.3"

    laps = (await c.get("/api/laps")).json()
    assert laps and laps[0]["car_category"] == "Gr.3"


async def test_laps_without_packet_c_have_a_blank_category(client) -> None:
    """Format A carries no category; blank must not read as a real one."""
    c, service = client
    await drive_laps(service, laps=1)
    laps = (await c.get("/api/laps")).json()
    assert laps[0]["car_category"] == ""


async def drive_race(
    service: TelemetryService, race_laps: int, *, skip_lap: int | None = None,
    start_lap: int = 1,
) -> None:
    """A short race with position reporting: P4 climbing to P2, checkered
    flag, a few cool-down packets. skip_lap simulates a stream gap — that
    lap's packets never arrive, and the counter jump costs the lap before it
    too (a boundary that isn't prev+1 completes nothing)."""
    pid = 0
    for lap in range(start_lap, race_laps + 1):
        if lap == skip_lap:
            continue
        for _ in range(60):
            pid += 1
            await service._on_packet(
                parse_packet(
                    build_packet(
                        packet_id=pid,
                        current_lap=lap,
                        total_laps=race_laps,
                        last_lap_time_ms=61_000 if lap > 1 else -1,
                        race_position=4 if lap == 1 else 2,
                        total_positions=8,
                        speed_mps=50.0,
                        flags=ON_TRACK,
                        car_id=7,
                    )
                )
            )
    for _ in range(5):
        pid += 1
        await service._on_packet(
            parse_packet(
                build_packet(
                    packet_id=pid,
                    current_lap=race_laps + 1,
                    total_laps=race_laps,
                    last_lap_time_ms=61_000,
                    race_position=2,
                    total_positions=8,
                    flags=ON_TRACK,
                    car_id=7,
                )
            )
        )


async def test_race_result_is_persisted(client) -> None:
    """A finished race leaves its result on the session and its position on
    every lap — the answer the live pipeline used to throw away (#60)."""
    c, service = client
    await drive_race(service, race_laps=3)

    session = (await c.get("/api/sessions")).json()[0]
    assert session["final_position"] == 2
    assert session["final_total_positions"] == 8
    assert session["race_laps"] == 3
    # All three laps stored at 61 000 ms each — the sum is trustworthy.
    assert session["race_time_ms"] == 3 * 61_000

    laps = (await c.get(f"/api/sessions/{session['id']}/laps")).json()
    assert sorted(lap["race_position"] for lap in laps) == [2, 2, 4]


async def test_race_time_absent_when_a_lap_is_missing(client) -> None:
    """A gap in the stream leaves a lap unrecorded; the result still lands
    but the total stays NULL rather than summing across the hole."""
    c, service = client
    await drive_race(service, race_laps=3, skip_lap=2)

    session = (await c.get("/api/sessions")).json()[0]
    assert session["final_position"] == 2
    assert session["race_time_ms"] is None


async def test_time_trial_session_has_no_race_result(client) -> None:
    c, service = client
    await drive_laps(service, laps=2)  # no total_laps, no position reporting
    session = (await c.get("/api/sessions")).json()[0]
    assert session["final_position"] == -1
    assert session["final_total_positions"] == -1
    assert session["race_laps"] == 0
    assert session["race_time_ms"] is None
    laps = (await c.get(f"/api/sessions/{session['id']}/laps")).json()
    assert all(lap["race_position"] == -1 for lap in laps)


async def test_session_note_and_tags_roundtrip(client) -> None:
    """Notes and tags land on the session and come back from the listing (#25).

    Patching one must not clobber the other — the UI edits them separately.
    """
    c, service = client
    await drive_laps(service, laps=1)
    sid = (await c.get("/api/sessions")).json()[0]["id"]

    resp = await c.patch(f"/api/sessions/{sid}", json={"note": "testing new diff"})
    assert resp.status_code == 200
    resp = await c.patch(f"/api/sessions/{sid}", json={"tags": ["wet", "race sim"]})
    assert resp.status_code == 200

    session = (await c.get("/api/sessions")).json()[0]
    assert session["note"] == "testing new diff"
    assert session["tags"] == ["wet", "race sim"]

    # An empty list clears the tags; the note survives.
    await c.patch(f"/api/sessions/{sid}", json={"tags": []})
    session = (await c.get("/api/sessions")).json()[0]
    assert session["tags"] == []
    assert session["note"] == "testing new diff"


async def test_session_tags_are_cleaned_not_trusted(client) -> None:
    """Whitespace-only and duplicate tags collapse; a comma would corrupt the
    comma-joined storage, so it is rejected outright."""
    c, service = client
    await drive_laps(service, laps=1)
    sid = (await c.get("/api/sessions")).json()[0]["id"]

    resp = await c.patch(
        f"/api/sessions/{sid}", json={"tags": ["  wet ", "wet", "WET", "", "race sim"]}
    )
    assert resp.status_code == 200
    assert (await c.get("/api/sessions")).json()[0]["tags"] == ["wet", "race sim"]

    assert (
        await c.patch(f"/api/sessions/{sid}", json={"tags": ["a,b"]})
    ).status_code == 422
    assert (
        await c.patch(f"/api/sessions/{sid}", json={"tags": ["x" * 41]})
    ).status_code == 422


async def test_survey_track_can_be_assigned_mid_run(client, tmp_path) -> None:
    """A survey started before the circuit was known must be attachable to
    one without losing what it gathered (#45)."""
    c, service = client
    service.survey.start(tmp_path, track_width_m=1.6, track="")
    assert (await c.get("/api/survey/status")).json()["track"] == ""

    resp = await c.post("/api/survey/track", json={"track": "Dragon Trail - Gardens"})
    assert resp.status_code == 200
    assert resp.json()["track"] == "Dragon Trail - Gardens"
    assert service.survey.track_locked is True  # auto-ID must not override
    service.survey.stop()


async def test_discard_needs_a_running_survey_and_says_what_it_dropped(client, tmp_path) -> None:
    """The Survey view's Discard buttons (#98): 409 with no run, otherwise
    the status plus what went, so the toast can say it."""
    c, service = client
    assert (await c.post("/api/survey/discard", json={"scope": "lap"})).status_code == 409
    service.survey.start(tmp_path, track_width_m=1.6, track="Ring", track_user_set=True)
    common = dict(fmt="C", velocity=(30.0, 0.0, 0.0), speed_mps=30.0, wheelbase_m=2.6)
    for pid in range(21):
        service.survey.feed(parse_packet(build_packet(
            surface_types="GTGT", packet_id=pid, current_lap=1, flags=ON_TRACK,
            position=(pid * 0.5, 0.0, 0.0), **common,
        )))
    assert (await c.post("/api/survey/discard", json={"scope": "whole"})).status_code == 422
    resp = await c.post("/api/survey/discard", json={"scope": "lap"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["discarded"]["scope"] == "lap" and body["discarded"]["records"] > 0
    assert body["edge_points"] == 0 and body["lap_votes"] == 0
    assert body["discards"] == [body["discarded"]]
    service.survey.stop()


async def test_assigning_a_track_needs_a_running_survey(client) -> None:
    c, _ = client
    resp = await c.post("/api/survey/track", json={"track": "Somewhere"})
    assert resp.status_code == 409


async def test_assigning_rejects_an_empty_track(client, tmp_path) -> None:
    c, service = client
    service.survey.start(tmp_path, track_width_m=1.6, track="")
    assert (await c.post("/api/survey/track", json={"track": ""})).status_code == 422
    service.survey.stop()


async def test_a_blank_track_never_locks_an_unlabeled_survey(client, tmp_path) -> None:
    """A whitespace-only name is worse than no name: stripped it labels
    nothing, and locking on it would block auto-identification from ever
    rescuing the run — leaving a survey that can only ever write no bundle."""
    c, service = client
    service.survey.start(tmp_path, track_width_m=1.6, track="")
    resp = await c.post("/api/survey/track", json={"track": "   "})
    assert resp.status_code == 422
    assert service.survey.track == ""
    assert service.survey.track_locked is False  # auto-ID must still be able to
    service.survey.stop()


async def test_delete_all_while_recording_resumes_into_a_fresh_session(client):
    """Delete all wipes the recorder's session too, and is allowed while
    recording (the user typed DELETE). The recorder must be reset the way a
    single delete resets it, or the laps driven next are saved against a
    session that no longer exists."""
    c, service = client
    await drive_laps(service, laps=1)
    assert service.recording

    assert (await c.post("/api/admin/clear-data")).status_code == 200
    assert (await c.get("/api/sessions")).json() == []
    assert (await c.get("/api/status")).json()["session_id"] is None

    # The same car and lap counter carry on: no boundary would rescue a
    # recorder still pointing at the wiped session.
    await drive_race(service, race_laps=3, start_lap=2)
    sessions = (await c.get("/api/sessions")).json()
    assert len(sessions) == 1
    assert sessions[0]["lap_count"] == 2
    laps = await service.repo.list_laps(sessions[0]["id"])
    assert sorted(lap["number"] for lap in laps) == [2, 3]

async def test_replay_recording_api_excludes_new_sessions(client) -> None:
    """New replay sessions are excluded; subsequent normal sessions are not."""
    c, service = client

    enabled = await c.post(
        "/api/control/replay-recording", json={"enabled": True}
    )
    assert enabled.status_code == 200
    assert enabled.json()["replay_recording"] is True

    await service._on_packet(parse_packet(build_packet(
        current_lap=1, flags=0, speed_mps=40.0, car_id=7
    )))
    replay_id = service.session_id
    assert replay_id is not None

    sessions = (await c.get("/api/sessions")).json()
    replay_session = next(s for s in sessions if s["id"] == replay_id)
    assert replay_session["bests_excluded"] is True

    disabled = await c.post(
        "/api/control/replay-recording", json={"enabled": False}
    )
    assert disabled.status_code == 200
    assert disabled.json()["replay_recording"] is False

    # A different car forces a normal session boundary.
    await service._on_packet(parse_packet(build_packet(
        current_lap=1, flags=ON_TRACK, speed_mps=40.0, car_id=8
    )))
    normal_id = service.session_id
    assert normal_id is not None
    assert normal_id != replay_id

    sessions = (await c.get("/api/sessions")).json()
    flags = {s["id"]: s["bests_excluded"] for s in sessions}

    # Upstream deletes empty sessions when a new session starts.
    # The replay session was already verified as excluded above.
    assert replay_id not in flags
    assert flags[normal_id] is False

async def test_completed_replay_session_stays_excluded(client) -> None:
    """A saved replay lap survives a switch back to normal recording."""
    c, service = client

    response = await c.post(
        "/api/control/replay-recording", json={"enabled": True}
    )
    assert response.status_code == 200

    for tick in range(120):
        await service._on_packet(parse_packet(build_packet(
            packet_id=1000 + tick,
            current_lap=1,
            flags=0,
            speed_mps=45.0,
            car_id=7,
        )))

    samples = service.processor.live_lap_samples["t"]
    assert len(samples) == 120
    gt7_time_ms = round((samples[-1] - samples[0]) * 1000)

    await service._on_packet(parse_packet(build_packet(
        packet_id=1120,
        current_lap=2,
        flags=0,
        speed_mps=45.0,
        car_id=7,
        last_lap_time_ms=gt7_time_ms,
    )))

    replay_id = service.session_id
    assert replay_id is not None

    laps = await service.repo.list_laps(replay_id)
    assert len(laps) == 1
    assert laps[0]["time_ms"] == gt7_time_ms

    response = await c.post(
        "/api/control/replay-recording", json={"enabled": False}
    )
    assert response.status_code == 200

    # Changing cars opens a normal session and closes the saved replay.
    await service._on_packet(parse_packet(build_packet(
        current_lap=1,
        flags=ON_TRACK,
        speed_mps=40.0,
        car_id=8,
    )))

    normal_id = service.session_id
    assert normal_id is not None
    assert normal_id != replay_id

    sessions = (await c.get("/api/sessions")).json()
    flags = {s["id"]: s["bests_excluded"] for s in sessions}

    assert flags[replay_id] is True
    assert flags[normal_id] is False

    # The recorded telemetry remains available for comparison.
    saved_laps = await service.repo.list_laps(replay_id)
    assert len(saved_laps) == 1
    assert saved_laps[0]["time_ms"] == gt7_time_ms
