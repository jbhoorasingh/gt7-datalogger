"""GT7's suggested gear (#108): recorded beside the gear, judged per corner
against the gear the car was in, and counted for every lap of a session.

When GT7 fills the suggestion has not been checked against a console, so
what is pinned here is the app's own rule: what is kept, what a corner is
judged on, and that every place that reports the judgement agrees.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.processing import analysis
from app.processing.cars import CarDatabase
from app.processing.corner_metrics import BRAKE_SEARCH_M, LapTrace, gear_high, measure, windows
from app.processing.laps import OPTIONAL_COLUMNS, SAMPLE_COLUMNS, CompletedLap, LapProcessor
from app.service import TelemetryService
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository
from app.telemetry import simulator
from tests import stadium_track as track

# --- recording ---------------------------------------------------------------


async def recorded(suggest: int | None) -> list[CompletedLap]:
    """Two laps of the stadium on packet A, the narrowest format, GT7
    suggesting `suggest` while the car brakes (None: nothing, all lap)."""
    laps: list[CompletedLap] = []

    async def on_lap(lap: CompletedLap) -> None:
        laps.append(lap)

    async def on_session(_info: object) -> None:
        return None

    proc = LapProcessor(on_lap=on_lap, on_session=on_session, min_lap_ticks=1)
    driver = track.Driver()
    for number in (1, 2):
        for packet in driver.lap(number, corner_gear=3, suggest=suggest):
            await proc.feed(packet)
    await proc.feed(driver.line(3))
    assert len(laps) == 2
    return laps


def test_the_column_is_not_optional() -> None:
    """Every packet format carries the gear byte."""
    assert "suggested_gear" in SAMPLE_COLUMNS
    assert "suggested_gear" not in OPTIONAL_COLUMNS


async def test_every_tick_keeps_the_nibble_as_broadcast() -> None:
    lap = (await recorded(suggest=2))[0]
    s = lap.samples
    assert len(s["suggested_gear"]) == len(s["t"])
    # 15 is kept where GT7 suggested nothing: a column of floats has no gap.
    assert set(s["suggested_gear"]) == {2.0, 15.0}


async def test_a_lap_with_no_suggestion_keeps_no_column() -> None:
    for lap in await recorded(suggest=None):
        assert "suggested_gear" not in lap.samples


async def test_reverse_is_no_suggestion_either() -> None:
    for lap in await recorded(suggest=0):
        assert "suggested_gear" not in lap.samples


# --- per corner ----------------------------------------------------------------


def lap(
    gear: list[tuple[float, float, float]],
    suggest: list[tuple[float, float, float]],
    slowest: float = 600.0,
    length: int = 2000,
) -> dict[str, list[float]]:
    """A lap in 1 m steps, slowest at `slowest`. `gear` and `suggest` are
    (from, to, gear) stretches over fifth and no suggestion."""
    dist = [float(d) for d in range(length)]

    def level(spans: list[tuple[float, float, float]], default: float, d: float) -> float:
        for lo, hi, value in spans:
            if lo <= d < hi:
                return value
        return default

    return {
        "dist": dist,
        "t": [d / 50.0 for d in dist],
        "speed": [100.0 + abs(d - slowest) / 10 for d in dist],
        "gear": [level(gear, 5.0, d) for d in dist],
        "suggested_gear": [level(suggest, 15.0, d) for d in dist],
    }


def corner(n: int, apex: float, half: float = 75.0) -> dict[str, float | int]:
    return {"n": n, "entry_dist": apex - half, "apex_dist": apex, "exit_dist": apex + half}


def judged(samples: dict[str, list[float]], corners: list[dict]) -> dict:
    return measure(LapTrace(samples), windows(corners))


def test_a_suggestion_cleared_before_the_slowest_point_still_holds() -> None:
    """GT7 may take the hint down once the driver is in the gear it asked
    for; the gear it asked for is still the one the corner is judged on."""
    m = judged(lap(gear=[(480, 700, 3)], suggest=[(450, 560, 3)]), [corner(1, 600.0)])[1]
    assert m.suggested_gear == 3
    assert m.min_speed_gear == 3
    assert gear_high(m) is False


def test_a_corner_taken_a_gear_high() -> None:
    m = judged(lap(gear=[(480, 700, 4)], suggest=[(450, 560, 3)]), [corner(1, 600.0)])[1]
    assert m.suggested_gear == 3
    assert gear_high(m) is True


def test_the_last_suggestion_on_the_way_in_is_the_one() -> None:
    samples = lap(gear=[(480, 700, 3)], suggest=[(450, 500, 4), (500, 580, 3)])
    assert judged(samples, [corner(1, 600.0)])[1].suggested_gear == 3


def test_no_suggestion_judges_nothing() -> None:
    m = judged(lap(gear=[(480, 700, 4)], suggest=[]), [corner(1, 600.0)])[1]
    assert m.suggested_gear is None
    assert gear_high(m) is None
    samples = lap(gear=[(480, 700, 4)], suggest=[])
    del samples["suggested_gear"]  # a lap stored before the column
    assert gear_high(judged(samples, [corner(1, 600.0)])[1]) is None


@pytest.mark.parametrize("in_gear", [0.0, 15.0])
def test_neutral_or_reverse_at_the_slowest_point_is_not_judged(in_gear: float) -> None:
    m = judged(lap(gear=[(590, 610, in_gear)], suggest=[(450, 560, 3)]), [corner(1, 600.0)])[1]
    assert m.suggested_gear == 3
    assert gear_high(m) is None


def test_a_suggestion_for_the_corner_before_is_not_this_ones() -> None:
    """The approach begins BRAKE_SEARCH_M before the entry and never before
    the previous apex: a hint shown for turn 1 is not one for turn 2."""
    corners = [corner(1, 400.0), corner(2, 600.0)]
    samples = lap(gear=[(480, 700, 4)], suggest=[(350, 395, 2)])
    assert 350 > corners[1]["entry_dist"] - BRAKE_SEARCH_M
    assert judged(samples, corners)[2].suggested_gear is None
    far = lap(gear=[(480, 700, 4)], suggest=[(100, 200, 2)])
    assert judged(far, [corner(1, 600.0)])[1].suggested_gear is None


def test_a_corner_across_the_line_is_not_judged() -> None:
    wrap = {"n": 1, "entry_dist": 1950.0, "apex_dist": 10.0, "exit_dist": 60.0}
    m = judged(lap(gear=[(0, 100, 4)], suggest=[(1900, 2000, 3)], slowest=20.0), [wrap])[1]
    assert gear_high(m) is None


def test_the_corner_report_carries_the_judgement() -> None:
    samples = lap(gear=[(480, 700, 4)], suggest=[(450, 560, 3)])
    row = analysis.corner_report([corner(1, 600.0)], samples)[0]
    assert row["suggested_gear"] == 3
    assert row["gear_high"] is True
    del samples["suggested_gear"]
    row = analysis.corner_report([corner(1, 600.0)], samples)[0]
    assert row["suggested_gear"] is None and row["gear_high"] is None


# --- through the API -----------------------------------------------------------


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


async def drive(service: TelemetryService, laps: list[dict]) -> None:
    driver = track.Driver()
    for number, how in enumerate(laps, start=1):
        for packet in driver.lap(number, **how):
            await service._on_packet(packet)
    await service._on_packet(driver.line(len(laps) + 1))


# The quickest lap (the session's reference) in third as suggested, one braking
# later and in fourth through both corners, and one GT7 suggested nothing on.
LAPS = [
    {"brake_before_m": 80.0, "corner_gear": 3, "suggest": 3},
    {"brake_before_m": 94.0, "corner_gear": 4, "suggest": 3},
    {"brake_before_m": 94.0, "corner_gear": 4},
]


async def lap_ids(c: AsyncClient) -> dict[int, int]:
    return {lap["number"]: lap["id"] for lap in (await c.get("/api/laps")).json()}


async def test_the_comparison_reports_the_gear_choice_per_corner(client) -> None:
    c, service = client
    await drive(service, LAPS)
    ids = await lap_ids(c)
    ref, lazy, silent = ids[1], ids[2], ids[3]
    data = (
        await c.get(f"/api/analysis/compare?laps={ref},{lazy},{silent}&ref={ref}")
    ).json()
    rows = {lap_id: data["laps"][str(lap_id)]["corner_report"] for lap_id in ids.values()}
    assert len(rows[ref]) == 2
    assert all(r["suggested_gear"] == 3 and r["gear_high"] is False for r in rows[ref])
    assert all(r["suggested_gear"] == 3 and r["gear_high"] is True for r in rows[lazy])
    assert all(r["suggested_gear"] is None and r["gear_high"] is None for r in rows[silent])


async def test_gear_choice_counts_every_lap_against_the_sessions_quickest(client) -> None:
    c, service = client
    await drive(service, LAPS)
    ids = await lap_ids(c)
    session_id = (await c.get("/api/sessions")).json()[0]["id"]
    resp = await c.get(f"/api/analysis/gear-choice?session_id={session_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reference"] == ids[1]
    by_lap = {row["lap_id"]: row for row in body["laps"]}
    assert by_lap[ids[1]] == {"lap_id": ids[1], "judged": 2, "gear_high": 0}
    assert by_lap[ids[2]] == {"lap_id": ids[2], "judged": 2, "gear_high": 2}
    assert by_lap[ids[3]] == {"lap_id": ids[3], "judged": 0, "gear_high": 0}


async def test_gear_choice_remembers_each_lap_until_the_reference_changes(client) -> None:
    c, service = client
    await drive(service, LAPS)
    ids = await lap_ids(c)
    session_id = (await c.get("/api/sessions")).json()[0]["id"]
    url = f"/api/analysis/gear-choice?session_id={session_id}"
    first = (await c.get(url)).json()
    # Every lap is remembered: the two with suggestions judged, the silent
    # one as having none, without its samples being read.
    assert len(service.gear_verdicts) == 3
    assert sorted(len(v) for v in service.gear_verdicts.values()) == [0, 2, 2]

    reads: list[int] = []
    scanned: list[list[int]] = []
    real = service.repo.lap_samples_json
    real_scan = service.repo.laps_with_sample_column

    async def counted(lap_id: int) -> str | None:
        reads.append(lap_id)
        return await real(lap_id)

    async def counted_scan(lap_ids: list[int], column: str) -> set[int]:
        scanned.append(lap_ids)
        return await real_scan(lap_ids, column)

    service.repo.lap_samples_json = counted  # type: ignore[method-assign]
    service.repo.laps_with_sample_column = counted_scan  # type: ignore[method-assign]
    assert (await c.get(url)).json() == first
    # Asked again, nothing is read or scanned again.
    assert reads == []
    assert scanned == [[]]

    # Ruling the reference out makes the next quickest lap the yardstick, and
    # every lap is judged again against it.
    await c.patch(f"/api/laps/{ids[1]}", json={"best_override": False})
    reads.clear()
    again = (await c.get(url)).json()
    assert again["reference"] == ids[2]
    assert sorted(reads) == sorted([ids[1], ids[2]])
    assert len(service.gear_verdicts) == 6


async def test_gear_choice_for_no_such_session(client) -> None:
    c, _service = client
    assert (await c.get("/api/analysis/gear-choice?session_id=999")).status_code == 404


async def test_the_lap_analysis_document_carries_it(client) -> None:
    c, service = client
    await drive(service, LAPS)
    ids = await lap_ids(c)
    session_id = (await c.get("/api/sessions")).json()[0]["id"]
    doc = (await c.get(f"/api/sessions/{session_id}/analysis.json")).json()
    by_lap = {lap["id"]: lap for lap in doc["laps"]}
    for corner_ in by_lap[ids[2]]["corners"]:
        assert corner_["speed"]["suggested_gear"] == 3
        assert corner_["speed"]["gear_high"] is True
    for corner_ in by_lap[ids[3]]["corners"]:
        assert "suggested_gear" not in corner_["speed"]
        assert "gear_high" not in corner_["speed"]


async def test_the_column_is_exported_and_comes_back_in(client) -> None:
    c, service = client
    await drive(service, LAPS)
    ids = await lap_ids(c)
    header = (await c.get(f"/api/laps/{ids[1]}/export.csv")).text.splitlines()[7]
    assert "Suggested Gear" in header
    header = (await c.get(f"/api/laps/{ids[3]}/export.csv")).text.splitlines()[7]
    assert "Suggested Gear" not in header

    samples = (await c.get(f"/api/laps/{ids[1]}")).json()["samples"]
    exported = (await c.get(f"/api/laps/{ids[1]}/export")).json()
    assert exported["version"] == 2  # an added column bumps nothing
    imported = (await c.post("/api/laps/import", json=exported)).json()["id"]
    again = (await c.get(f"/api/laps/{imported}")).json()["samples"]
    assert again["suggested_gear"] == samples["suggested_gear"]

    # A file written before the column imports as a lap without it.
    del exported["lap"]["samples"]["suggested_gear"]
    imported = (await c.post("/api/laps/import", json=exported)).json()["id"]
    assert "suggested_gear" not in (await c.get(f"/api/laps/{imported}")).json()["samples"]


# --- the simulator -------------------------------------------------------------


def test_the_gear_rule_is_the_one_the_simulator_always_had() -> None:
    for speed in (0.0, 10.9, 11.0, 30.0, 54.9, 55.0, 80.0):
        assert simulator._gear_for(speed) == min(6, max(1, int(speed / 11) + 1))


def test_the_simulator_suggests_nothing_on_a_straight() -> None:
    # The straight between the ~780 m corner and the 1600 m one: no corner
    # within sight.
    assert simulator._suggested_gear(1100.0, 6, 0.0) == 15


def test_the_simulator_suggests_the_corners_gear_on_the_way_in() -> None:
    """Approaching the tight right-hander at about 1600 m in top gear."""
    corner_speed = simulator._corner_speed_ahead(1500.0, 0.0)
    wanted = simulator._gear_for(corner_speed)
    assert wanted < 6
    assert simulator._suggested_gear(1500.0, 6, 0.0) == wanted
    # Once in the gear it asked for, the hint goes.
    assert simulator._suggested_gear(1500.0, wanted, 0.0) == 15


def test_a_lazy_lap_holds_a_gear_too_many_through_that_corner() -> None:
    slowest = 25.0  # m/s, about what the 47 m radius allows
    assert simulator._driven_gear(1580.0, slowest, lazy=False) == simulator._gear_for(slowest)
    assert simulator._driven_gear(1580.0, slowest, lazy=True) == simulator._gear_for(slowest) + 1
    # And only there.
    assert simulator._driven_gear(700.0, slowest, lazy=True) == simulator._gear_for(slowest)
