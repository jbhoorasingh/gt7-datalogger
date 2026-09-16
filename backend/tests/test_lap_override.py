"""Manual lap exclusion from best-lap aggregates (#74).

The span heuristic decides which laps are partial; the user decides which
laps they stand behind. The second outranks the first in both directions,
survives the first being re-run, and hands the lap back to it when cleared.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models import SimulatorFlags
from app.processing.cars import Car, CarDatabase
from app.processing.laps import CompletedLap, LapProcessor, SessionInfo
from app.processing.tracks import IDENTIFY_MIN_TICKS
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


# Two ticks of every column the importer insists on, so seeded laps survive
# an export/import round trip.
SAMPLES = {
    column: [0.0, 1.0]
    for column in (
        "t", "dist", "speed", "throttle", "brake", "coast", "tire_slip",
        "body_height", "pos_x", "pos_z",
    )
}


def make_lap(number: int, time_ms: int, counts: bool = True, ticks: int = 0) -> CompletedLap:
    lap = CompletedLap(
        number=number,
        time_ms=time_ms,
        finished_at=f"2026-09-16T00:{number:02d}:00Z",
        car_id=7,
        samples={column: list(values) for column, values in SAMPLES.items()},
        fuel_start=100.0,
        fuel_end=99.0,
    )
    lap.car_category = "Gr.3"
    lap.counts_for_best = counts
    lap.total_ticks = ticks
    return lap


async def seed(repo: Repository) -> tuple[int, dict[int, int]]:
    """Suzuka, one car: a clean lap, a quicker lap with contact in it, and a
    pit out-lap the heuristic already calls partial. Returns lap ids by number."""
    session = await repo.create_session(
        SessionInfo(car_id=7, started_at="2026-09-16T00:00:00Z", car_category="Gr.3"),
        Car(id=7, name="Car 7"),
    )
    await repo.set_session_track(session, "Suzuka")
    ids = {
        1: await repo.save_lap(session, make_lap(1, 92_000)),
        2: await repo.save_lap(session, make_lap(2, 88_000)),
        3: await repo.save_lap(session, make_lap(3, 45_000, counts=False)),
    }
    return session, ids


async def board(c: AsyncClient) -> dict:
    rows = (await c.get("/api/laps/bests")).json()["bests"]
    return next(r for r in rows if r["track_name"] == "Suzuka")


async def session_best(c: AsyncClient, session_id: int) -> int | None:
    sessions = (await c.get("/api/sessions")).json()
    return next(s["best_lap_time_ms"] for s in sessions if s["id"] == session_id)


async def test_excluding_a_lap_moves_every_best(client) -> None:
    c, service = client
    session, ids = await seed(service.repo)
    assert (await board(c))["time_ms"] == 88_000

    resp = await c.patch(
        f"/api/laps/{ids[2]}", json={"best_override": False, "exclude_reason": "contact"}
    )
    assert resp.status_code == 200
    lap = resp.json()
    assert lap["counts_for_best"] is False
    assert lap["full_lap"] is True  # the heuristic's verdict is untouched
    assert lap["best_override"] is False
    assert lap["exclude_reason"] == "contact"
    assert lap["car_name"]

    # The board, the session best, the class benchmark and the session-summary
    # aggregate all read the same answer.
    row = await board(c)
    assert row["time_ms"] == 92_000
    assert row["lap_count"] == 1
    # ...and the board can say why the quicker time is missing.
    assert row["excluded_faster"] == [
        {"lap_id": ids[2], "time_ms": 88_000, "reason": "contact"}
    ]
    assert await session_best(c, session) == 92_000
    best = (await c.get("/api/laps/best?track=Suzuka&category=Gr.3")).json()
    assert best["time_ms"] == 92_000
    assert (await service.repo.session_lap_stats(session))["best_ms"] == 92_000

    listed = (await c.get(f"/api/sessions/{session}/laps")).json()
    summaries = {lap["number"]: lap for lap in listed}
    assert summaries[2]["counts_for_best"] is False
    assert summaries[2]["exclude_reason"] == "contact"
    assert summaries[1]["best_override"] is None


async def test_clearing_the_override_hands_the_lap_back_to_the_heuristic(client) -> None:
    c, service = client
    session, ids = await seed(service.repo)

    await c.patch(f"/api/laps/{ids[2]}", json={"best_override": False, "exclude_reason": "dirty"})
    lap = (await c.patch(f"/api/laps/{ids[2]}", json={"best_override": None})).json()
    assert lap["counts_for_best"] is True
    assert lap["best_override"] is None
    assert lap["exclude_reason"] == ""
    row = await board(c)
    assert row["time_ms"] == 88_000
    assert row["excluded_faster"] == []

    # The other direction: a lap the heuristic misjudged can be ruled in...
    lap = (await c.patch(f"/api/laps/{ids[3]}", json={"best_override": True})).json()
    assert lap["counts_for_best"] is True
    assert lap["full_lap"] is False
    assert (await board(c))["time_ms"] == 45_000
    assert await session_best(c, session) == 45_000

    # ...and cleared, it is partial again.
    lap = (await c.patch(f"/api/laps/{ids[3]}", json={"best_override": None})).json()
    assert lap["counts_for_best"] is False
    assert await session_best(c, session) == 88_000


async def test_heuristic_reflagging_never_clobbers_an_override(client) -> None:
    c, service = client
    repo = service.repo
    session, ids = await seed(repo)
    await c.patch(f"/api/laps/{ids[2]}", json={"best_override": False})
    await c.patch(f"/api/laps/{ids[3]}", json={"best_override": True})

    async def counts() -> dict[int, tuple[bool, bool]]:
        laps = await repo.list_laps(session)
        return {lap["number"]: (lap["counts_for_best"], lap["full_lap"]) for lap in laps}

    # The pipeline re-judges the whole session: lap 1 is now the short one.
    await repo.mark_session_laps_partial(session, [1])
    assert await counts() == {1: (False, False), 2: (False, True), 3: (True, True)}
    await repo.mark_session_laps_partial(session, [])
    assert await counts() == {1: (True, True), 2: (False, True), 3: (True, True)}

    # Cleared, lap 3 takes the heuristic's LATEST verdict, not the one it had
    # when it was ruled in.
    await c.patch(f"/api/laps/{ids[3]}", json={"best_override": None})
    assert (await counts())[3] == (True, True)


async def test_the_patch_is_strict_about_what_it_accepts(client) -> None:
    c, service = client
    _, ids = await seed(service.repo)
    lap = f"/api/laps/{ids[1]}"

    # A reason only means something beside an exclusion.
    for override in (True, None):
        resp = await c.patch(lap, json={"best_override": override, "exclude_reason": "contact"})
        assert resp.status_code == 400
    assert (await c.patch(lap, json={"exclude_reason": "contact"})).status_code == 400
    # A closed vocabulary, and no fields beyond the two.
    bad_reason = {"best_override": False, "exclude_reason": "spun"}
    assert (await c.patch(lap, json=bad_reason)).status_code == 422
    assert (await c.patch(lap, json={"counts_for_best": False})).status_code == 422
    assert (await c.patch(lap, json={})).status_code == 400
    assert (await c.patch(lap, json={"exclude_reason": None})).status_code == 400
    assert (await c.patch("/api/laps/9999", json={"best_override": False})).status_code == 404
    assert (await c.patch("/api/laps/9999", json={"exclude_reason": "dirty"})).status_code == 404
    assert (await c.get(lap)).json()["best_override"] is None  # nothing above landed

    # Re-wording an existing exclusion keeps it excluded.
    await c.patch(lap, json={"best_override": False, "exclude_reason": "contact"})
    resp = await c.patch(lap, json={"exclude_reason": "off-track"})
    assert resp.status_code == 200
    assert resp.json()["best_override"] is False
    assert resp.json()["exclude_reason"] == "off-track"


async def test_track_identification_still_sees_an_excluded_lap_as_full(client) -> None:
    """Identification wants a lap that covered the route. An off-track lap the
    user excluded did; a long pit out-lap did not, however many ticks it ran."""
    c, service = client
    repo = service.repo
    session = await repo.create_session(
        SessionInfo(car_id=7, started_at="2026-09-16T01:00:00Z"), Car(id=7, name="Car 7")
    )
    full = await repo.save_lap(session, make_lap(1, 90_000, ticks=IDENTIFY_MIN_TICKS + 100))
    await repo.save_lap(
        session, make_lap(2, 70_000, counts=False, ticks=IDENTIFY_MIN_TICKS + 500)
    )
    await c.patch(f"/api/laps/{full}", json={"best_override": False, "exclude_reason": "off-track"})

    assert await repo.unnamed_sessions_with_lap() == [(session, full)]


async def test_export_and_import_carry_the_ruling(client) -> None:
    c, service = client
    _, ids = await seed(service.repo)
    await c.patch(f"/api/laps/{ids[2]}", json={"best_override": False, "exclude_reason": "restart"})

    imported = {}
    for number in (2, 3):
        doc = (await c.get(f"/api/laps/{ids[number]}/export")).json()
        resp = await c.post("/api/laps/import", json=doc)
        assert resp.status_code == 200
        imported[number] = (await c.get(f"/api/laps/{resp.json()['id']}?samples=false")).json()

    assert imported[2]["best_override"] is False
    assert imported[2]["exclude_reason"] == "restart"
    assert imported[2]["full_lap"] is True
    # A partial lap stays partial on the machine it is imported into.
    assert imported[3]["counts_for_best"] is False
    assert imported[3]["best_override"] is None


async def test_an_older_file_keeps_its_partial_verdict(client) -> None:
    """Files written before #74 carry counts_for_best alone, which was the
    heuristic's verdict then."""
    c, service = client
    _, ids = await seed(service.repo)
    doc = (await c.get(f"/api/laps/{ids[3]}/export")).json()
    for key in ("full_lap", "best_override", "exclude_reason"):
        del doc["lap"][key]
    resp = await c.post("/api/laps/import", json=doc)
    lap = (await c.get(f"/api/laps/{resp.json()['id']}?samples=false")).json()
    assert lap["counts_for_best"] is False
    assert lap["full_lap"] is False


# --- the session being driven ------------------------------------------------


def packet(lap: int, last_ms: int = -1):
    return parse_packet(
        build_packet(
            current_lap=lap, speed_mps=40.0, last_lap_time_ms=last_ms, flags=ON_TRACK, car_id=7
        )
    )


async def drive_laps(service: TelemetryService, times: list[int]) -> None:
    """Full laps of equal length, completing with the given times."""
    for number, time_ms in enumerate(times, start=1):
        for _ in range(300):
            await service._on_packet(packet(number))
        await service._on_packet(packet(number + 1, last_ms=time_ms))


async def test_live_session_best_and_delta_follow_a_ruling(client) -> None:
    c, service = client
    await drive_laps(service, [89_000, 88_000, 90_000])
    assert service._session_best_ms == 88_000
    assert service._best_ref_lap == 2
    laps = {lap["number"]: lap["id"] for lap in await service.repo.list_laps(service.session_id)}

    await c.patch(f"/api/laps/{laps[2]}", json={"best_override": False})
    assert service._session_best_ms == 89_000
    assert service.processor.excluded_lap_numbers() == {2}
    # The delta reference moves to the lap that now holds the best, read
    # back from the database.
    assert service._best_ref_lap == 1
    assert service._best_ref is not None and service._best_ref["dist"]

    # The next lap is judged against the best that remains.
    for _ in range(300):
        await service._on_packet(packet(4))
    await service._on_packet(packet(5, last_ms=88_500))
    assert service._prev_best_ms == 89_000
    assert service._session_best_ms == 88_500

    # Ruled back in, lap 2 is the best and the reference again.
    await c.patch(f"/api/laps/{laps[2]}", json={"best_override": None})
    assert service._session_best_ms == 88_000
    assert service._best_ref_lap == 2


async def test_a_ruling_on_another_session_leaves_live_state_alone(client) -> None:
    c, service = client
    await drive_laps(service, [89_000, 88_000])
    _, ids = await seed(service.repo)
    await c.patch(f"/api/laps/{ids[1]}", json={"best_override": False})
    assert service._session_best_ms == 88_000
    assert service.processor.excluded_lap_numbers() == set()


def span_lap(number: int, time_ms: int, span: float) -> CompletedLap:
    lap = make_lap(number, time_ms)
    lap.samples["dist"] = [0.0, span]
    return lap


async def _noop(*_args) -> None:
    return None


def test_processor_applies_rulings_on_top_of_the_span() -> None:
    proc = LapProcessor(on_lap=_noop, on_session=_noop)
    proc._session = SessionInfo(car_id=7, started_at="")

    def complete(lap: CompletedLap) -> CompletedLap:
        proc._apply_span_guard(lap, lap.samples)
        return lap

    for number, time_ms in ((1, 90_000), (2, 88_000), (3, 91_000)):
        complete(span_lap(number, time_ms, 4000.0))
    assert proc.session.best_lap_time_ms == 88_000

    proc.set_best_override(2, False)
    assert proc.session.best_lap_time_ms == 90_000
    assert proc.excluded_lap_numbers() == {2}

    # Lap 2 re-driven (GT7 re-reporting after a rewind) is a new lap: the
    # ruling was about the one it replaced.
    redriven = complete(span_lap(2, 95_000, 4000.0))
    assert redriven.counts_for_best is True
    assert redriven.excluded_lap_numbers == []
    assert proc.excluded_lap_numbers() == set()

    # A short lap is partial by the span; ruled in, it counts, and a later
    # lap that leaves the heuristic's verdict unchanged keeps it counting.
    short = complete(span_lap(4, 40_000, 1000.0))
    assert short.counts_for_best is False
    assert short.partial_lap_numbers == [4]
    assert short.excluded_lap_numbers == [4]
    proc.set_best_override(4, True)
    assert proc.session.best_lap_time_ms == 40_000
    after = complete(span_lap(5, 92_000, 4000.0))
    assert after.excluded_lap_numbers == []
    assert after.session_best_before_ms == 40_000

    # None hands it back.
    proc.set_best_override(4, None)
    assert proc.excluded_lap_numbers() == {4}
    assert proc.session.best_lap_time_ms == 90_000
