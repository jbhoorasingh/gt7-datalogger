"""Cross-session personal bests and the sessions excluded from them (#26).

GT7 records replays exactly like driven laps — no packet field tells a
TT-leader replay from your own driving — so the board's correctness rests on
two filters: partial laps never win, and user-flagged sessions never own a row.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.processing.cars import Car, CarDatabase
from app.processing.laps import CompletedLap, SessionInfo
from app.service import TelemetryService
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository

# Every key lap_summary emits. The listing endpoints build summaries from a
# column projection rather than full LapRow objects, so a key silently missing
# here is exactly the regression the projection could introduce.
SUMMARY_KEYS = {
    "id", "session_id", "number", "time_ms", "finished_at", "car_id", "car_category",
    "fuel_start", "fuel_end", "fuel_consumed", "full_throttle_pct", "full_brake_pct",
    "coasting_pct", "tire_spin_pct", "max_speed", "min_body_height", "total_ticks",
    "tod_ms", "tcs_active_pct", "asm_active_pct", "max_water_temp", "max_oil_temp",
    "min_oil_pressure", "counts_for_best", "off_track_count", "off_survey_count",
    "clean_lap", "salvaged", "event_counts",
}


@pytest.fixture
async def client(tmp_path):
    settings = Settings(source="udp", db_path=tmp_path / "test.db", ws_rate=1000)
    engine = make_engine(settings.db_path)
    await init_db(engine)
    repo = Repository(make_session_factory(engine))
    service = TelemetryService(settings, repo, CarDatabase())

    app = create_app()
    app.router.lifespan_context = None  # type: ignore[assignment]
    app.state.service = service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, repo
    await engine.dispose()


def make_lap(
    number: int,
    time_ms: int,
    car_id: int,
    category: str = "",
    counts: bool = True,
    salvaged: bool = False,
) -> CompletedLap:
    lap = CompletedLap(
        number=number,
        time_ms=time_ms,
        finished_at=f"2026-08-20T00:{number:02d}:00Z",
        car_id=car_id,
        samples={"t": [0.0], "dist": [0.0]},
        fuel_start=100.0,
        fuel_end=99.0,
    )
    lap.car_category = category
    lap.counts_for_best = counts
    lap.salvaged = salvaged
    return lap


async def seed(repo: Repository) -> dict[str, int]:
    """Two circuits, two cars, one partial lap, one unlabeled session.

    s2 is the "replay" session: same circuit and car as s1 but faster — the
    board is wrong in exactly the #26 way until it is flagged excluded.
    """
    s1 = await repo.create_session(
        SessionInfo(car_id=7, started_at="2026-08-20T00:00:00Z", car_category="Gr.3"),
        Car(id=7, name="Car 7"),
    )
    await repo.set_session_track(s1, "Suzuka")
    await repo.save_lap(s1, make_lap(1, 92_000, 7, "Gr.3"))
    await repo.save_lap(s1, make_lap(2, 90_500, 7, "Gr.3"))
    # A pit out-lap: much "faster" than any full lap because it isn't one.
    await repo.save_lap(s1, make_lap(3, 45_000, 7, "Gr.3", counts=False))

    s2 = await repo.create_session(
        SessionInfo(car_id=7, started_at="2026-08-20T01:00:00Z", car_category="Gr.3"),
        Car(id=7, name="Car 7"),
    )
    await repo.set_session_track(s2, "Suzuka")
    await repo.save_lap(s2, make_lap(1, 88_000, 7, "Gr.3"))

    s3 = await repo.create_session(
        SessionInfo(car_id=9, started_at="2026-08-20T02:00:00Z", car_category="Gr.4"),
        Car(id=9, name="Car 9"),
    )
    await repo.set_session_track(s3, "Monza")
    await repo.save_lap(s3, make_lap(1, 105_000, 9, "Gr.4"))
    await repo.save_lap(s3, make_lap(2, 103_250, 9, "Gr.4"))

    # Never identified: no circuit means no board row, however quick the lap.
    s4 = await repo.create_session(
        SessionInfo(car_id=7, started_at="2026-08-20T03:00:00Z", car_category="Gr.3"),
        Car(id=7, name="Car 7"),
    )
    await repo.save_lap(s4, make_lap(1, 60_000, 7, "Gr.3"))

    return {"s1": s1, "s2": s2, "s3": s3, "s4": s4}


async def test_one_row_per_track_and_car_with_true_minimum(client) -> None:
    c, repo = client
    ids = await seed(repo)

    bests = (await c.get("/api/laps/bests")).json()["bests"]
    assert [(b["track_name"], b["car_id"]) for b in bests] == [("Monza", 9), ("Suzuka", 7)]

    monza, suzuka = bests
    assert monza["time_ms"] == 103_250
    assert monza["lap_count"] == 2
    assert monza["car_category"] == "Gr.4"
    assert monza["car_name"]  # enriched by the route, never blank for a known id

    # Fastest counting lap across BOTH Suzuka sessions; the count spans them too.
    assert suzuka["time_ms"] == 88_000
    assert suzuka["session_id"] == ids["s2"]
    assert suzuka["lap_count"] == 3


async def test_partial_and_unlabeled_laps_never_win(client) -> None:
    c, repo = client
    await seed(repo)
    bests = (await c.get("/api/laps/bests")).json()["bests"]
    times = {b["time_ms"] for b in bests}
    assert 45_000 not in times  # counts_for_best=False
    assert 60_000 not in times  # session has no track_name


async def test_category_filter_narrows_the_board(client) -> None:
    c, repo = client
    await seed(repo)
    bests = (await c.get("/api/laps/bests?category=Gr.4")).json()["bests"]
    assert [(b["track_name"], b["car_category"]) for b in bests] == [("Monza", "Gr.4")]


async def test_excluding_a_session_removes_its_rows_everywhere(client) -> None:
    c, repo = client
    ids = await seed(repo)

    resp = await c.patch(f"/api/sessions/{ids['s2']}", json={"bests_excluded": True})
    assert resp.status_code == 200
    assert resp.json() == {"status": "updated"}

    # The board falls back to the fastest lap the user actually drove — and
    # the excluded session's laps stop counting toward the sample size too.
    suzuka = [b for b in (await c.get("/api/laps/bests")).json()["bests"]
              if b["track_name"] == "Suzuka"]
    assert len(suzuka) == 1
    assert suzuka[0]["time_ms"] == 90_500
    assert suzuka[0]["session_id"] == ids["s1"]
    assert suzuka[0]["lap_count"] == 2

    # The class benchmark must not be set by a replay either.
    best = (await c.get("/api/laps/best?track=Suzuka&category=Gr.3")).json()
    assert best["time_ms"] == 90_500

    flags = {s["id"]: s["bests_excluded"] for s in (await c.get("/api/sessions")).json()}
    assert flags[ids["s2"]] is True
    assert flags[ids["s1"]] is False


async def test_patch_missing_session_is_404(client) -> None:
    c, repo = client
    resp = await c.patch("/api/sessions/9999", json={"bests_excluded": True})
    assert resp.status_code == 404


async def test_patch_rejects_unknown_fields_and_empty_bodies(client) -> None:
    """A typo'd field name must fail loudly, not answer "updated" while the
    replay keeps owning the board — the flag is only useful if setting it
    can be trusted to have happened."""
    c, repo = client
    ids = await seed(repo)
    assert (
        await c.patch(f"/api/sessions/{ids['s2']}", json={"bests_exclude": True})
    ).status_code == 422
    assert (await c.patch(f"/api/sessions/{ids['s2']}", json={})).status_code == 400
    flags = {s["id"]: s["bests_excluded"] for s in (await c.get("/api/sessions")).json()}
    assert flags[ids["s2"]] is False


async def test_category_filter_narrows_rows_not_recomputes_them(client) -> None:
    """?category= must filter finished board rows: a car whose fastest lap
    predates packet C (category '') must not sprout a slower "Gr.3 best"
    that the unfiltered board never shows."""
    c, repo = client
    await seed(repo)
    s = await repo.create_session(
        SessionInfo(car_id=11, started_at="2026-08-20T04:00:00Z"), Car(id=11, name="Car 11")
    )
    await repo.set_session_track(s, "Monza")
    await repo.save_lap(s, make_lap(1, 100_000, 11, ""))  # fastest, pre-packet-C
    await repo.save_lap(s, make_lap(2, 101_000, 11, "Gr.3"))

    board = (await c.get("/api/laps/bests")).json()["bests"]
    row = next(b for b in board if b["car_id"] == 11)
    assert row["time_ms"] == 100_000 and row["car_category"] == ""

    gr3 = (await c.get("/api/laps/bests?category=Gr.3")).json()["bests"]
    assert [b for b in gr3 if b["car_id"] == 11] == []


async def test_board_dates_fall_back_to_session_start(client) -> None:
    """Imported laps can carry an empty finished_at; the board still owes the
    row a date — same fallback best_lap_in already uses."""
    c, repo = client
    s = await repo.create_session(
        SessionInfo(car_id=13, started_at="2026-08-20T05:00:00Z"), Car(id=13, name="Car 13")
    )
    await repo.set_session_track(s, "Monza")
    undated = make_lap(1, 99_000, 13, "Gr.3")
    undated.finished_at = ""
    await repo.save_lap(s, undated)

    row = next(
        b for b in (await c.get("/api/laps/bests")).json()["bests"] if b["car_id"] == 13
    )
    assert row["finished_at"] == "2026-08-20T05:00:00Z"


async def test_salvaged_provenance_survives_to_board_and_summaries(client) -> None:
    """A salvaged replay lap must stay identifiable wherever its time shows
    up — the alternative is a Bests row nobody can trace weeks later."""
    c, repo = client
    ids = await seed(repo)
    await repo.save_lap(ids["s2"], make_lap(2, 87_000, 7, "Gr.3", salvaged=True))

    suzuka = next(
        b for b in (await c.get("/api/laps/bests")).json()["bests"]
        if b["track_name"] == "Suzuka"
    )
    assert suzuka["time_ms"] == 87_000
    assert suzuka["salvaged"] is True

    laps = (await c.get(f"/api/sessions/{ids['s2']}/laps")).json()
    assert {lap["number"]: lap["salvaged"] for lap in laps} == {1: False, 2: True}


async def test_patch_note_leaves_the_exclusion_flag_alone(client) -> None:
    c, repo = client
    ids = await seed(repo)
    await c.patch(f"/api/sessions/{ids['s2']}", json={"bests_excluded": True})

    resp = await c.patch(f"/api/sessions/{ids['s2']}", json={"note": "TT leader replay"})
    assert resp.status_code == 200
    session = next(
        s for s in (await c.get("/api/sessions")).json() if s["id"] == ids["s2"]
    )
    assert session["note"] == "TT leader replay"
    assert session["bests_excluded"] is True  # absent field means untouched


async def test_laps_listing_filters_by_track_and_carries_track_name(client) -> None:
    c, repo = client
    ids = await seed(repo)

    monza = (await c.get("/api/laps?track=Monza")).json()
    assert len(monza) == 2
    assert all(lap["session_id"] == ids["s3"] for lap in monza)
    assert all(lap["track_name"] == "Monza" for lap in monza)

    # Unfiltered rows say where they were driven too ('' when never identified).
    all_laps = (await c.get("/api/laps")).json()
    assert len(all_laps) == 7
    assert {lap["track_name"] for lap in all_laps} == {"Suzuka", "Monza", ""}


async def test_session_lap_summaries_survive_the_projection(client) -> None:
    """list_laps stopped loading LapRow objects; every lap_summary key must
    still come through the column projection."""
    c, repo = client
    ids = await seed(repo)
    laps = (await c.get(f"/api/sessions/{ids['s1']}/laps")).json()
    assert len(laps) == 3
    for lap in laps:
        assert SUMMARY_KEYS <= set(lap)
