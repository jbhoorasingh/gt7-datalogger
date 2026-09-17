"""Judging lap positions against the surveyed road edges (#41)."""

import json
import math

import pytest
from httpx import ASGITransport, AsyncClient

from app import rejudge
from app.config import Settings
from app.main import create_app
from app.processing import track_bundle, track_compile, track_limits
from app.processing.cars import Car, CarDatabase
from app.processing.laps import CompletedLap, SessionInfo, clean_verdict, new_sample_store
from app.processing.surface import OFF_TRACK_MIN_TICKS
from app.processing.tracks import signature_from_samples
from app.service import TelemetryService
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository

SOURCE = "abc123abc123"


def _edge(x, z, hx, hz, side, kind="auto", y=None):
    return {
        "x": x, "z": z, "y": y, "hx": hx, "hz": hz,
        "side": side, "kind": kind, "votes": {kind: {SOURCE: [1, 1]}},
        "run": 1, "tw": 1.6,
    }


def _ring(radius=100.0, width=10.0, skip=()):
    """A circular circuit (see test_track_compile): the road is the annulus
    between r = radius - width/2 and r = radius + width/2."""
    edges = []
    steps = int(2 * math.pi * radius)
    for i in range(steps):
        a = 2 * math.pi * i / steps
        hx, hz = -math.sin(a), math.cos(a)
        if ("L", i) not in skip:
            r = radius - width / 2
            edges.append(_edge(r * math.cos(a), r * math.sin(a), hx, hz, "L"))
        if ("R", i) not in skip:
            r = radius + width / 2
            edges.append(_edge(r * math.cos(a), r * math.sin(a), hx, hz, "R"))
    return edges


def _document(edges, track="Test Circuit"):
    return {
        "format": track_bundle.BUNDLE_FORMAT,
        "version": track_bundle.BUNDLE_VERSION,
        "meta": {
            "track": track, "runs": 2, "source_runs": {SOURCE: 2},
            "updated_at": "2026-08-01T00:00:00+00:00", "official": None,
        },
        "edges": edges,
        "finish_crossings": [],
        "corners": [],
        "sections": [],
    }


@pytest.fixture(scope="module")
def judge() -> track_limits.RoadJudge:
    compiled = track_compile.compile_bundle(_document(_ring()))
    assert compiled["coverage"]["road_pct"] > 95.0  # the fixture must be judgeable
    return track_limits.RoadJudge(compiled)


def _arc(r, n, start=0.0):
    """n samples along radius r, ~1 m apart — a plausible 60 Hz trace."""
    xs = [r * math.cos(start + 0.01 * i) for i in range(n)]
    zs = [r * math.sin(start + 0.01 * i) for i in range(n)]
    return xs, zs


# --- classify -----------------------------------------------------------------


def test_classify_on_off_unknown(judge) -> None:
    assert judge.classify(100.0, 0.0) == "on"  # centre of the road
    assert judge.classify(120.0, 0.0) == "off"  # 15 m beyond a known edge
    assert judge.classify(300.0, 0.0) == "unknown"  # far from any surveyed road
    # the ring's infield is >30 m from the road too: never an excursion
    assert judge.classify(0.0, 0.0) == "unknown"


def test_edge_margin_keeps_a_car_straddling_the_border_on(judge) -> None:
    # A centre-of-car within EDGE_MARGIN_M of the border still has wheels on
    # the road. Probe at an angle where the road is definitely resolved.
    a = next(
        0.1 * i for i in range(63)
        if judge.classify(104.0 * math.cos(0.1 * i), 104.0 * math.sin(0.1 * i)) == "on"
    )
    assert judge.classify(105.9 * math.cos(a), 105.9 * math.sin(a)) == "on"
    assert judge.classify(107.5 * math.cos(a), 107.5 * math.sin(a)) == "off"


# --- the honesty mask: unsurveyed ground never reads "off" --------------------


def test_a_flagged_gap_corridor_is_unknown_not_off() -> None:
    # 30 unsurveyed metres in both borders: the quads at either end are well
    # within NEAR_ROAD_M of a mid-gap point, but the ground under it was
    # never surveyed — driving through must not count as an excursion.
    steps = int(2 * math.pi * 100)
    hole = {(s, i) for s in ("L", "R") for i in range(100, 130)}
    compiled = track_compile.compile_bundle(_document(_ring(skip=hole)))
    judge = track_limits.RoadJudge(compiled)
    a = 2 * math.pi * 115 / steps
    assert judge.classify(100.0 * math.cos(a), 100.0 * math.sin(a)) == "unknown"
    # Beside the surveyed road, far from the gap, "off" still holds.
    assert judge.classify(-120.0, 0.0) == "off"


def test_past_the_open_end_of_a_fragmented_survey_is_unknown() -> None:
    # A third of the ring surveyed, both ends open: the road continues past
    # where the quads stop, and points there are ambiguous, not "off".
    steps = int(2 * math.pi * 100)
    hole = {(s, i) for s in ("L", "R") for i in range(steps // 3, steps)}
    compiled = track_compile.compile_bundle(_document(_ring(skip=hole)))
    judge = track_limits.RoadJudge(compiled)
    mid = 2 * math.pi * (steps // 6) / steps
    assert judge.classify(100.0 * math.cos(mid), 100.0 * math.sin(mid)) == "on"
    assert judge.classify(120.0 * math.cos(mid), 120.0 * math.sin(mid)) == "off"
    # ~10 m past the surveyed end, on the road's continuation.
    past = -10.0 / 100.0
    assert judge.classify(100.0 * math.cos(past), 100.0 * math.sin(past)) == "unknown"


# --- excursions ---------------------------------------------------------------


def test_a_sustained_off_run_counts_once(judge) -> None:
    xs, zs = [], []
    for r, n in ((100.0, 20), (120.0, OFF_TRACK_MIN_TICKS), (100.0, 20)):
        x, z = _arc(r, n)
        xs += x
        zs += z
    assert judge.excursions(xs, zs) == 1


def test_short_offs_and_unknown_breaks_do_not_count(judge) -> None:
    # Two off runs one tick short of the threshold, split by an unknown
    # sample: the unknown breaks the run without counting it.
    xs, zs = [], []
    for r, n in (
        (100.0, 10),
        (120.0, OFF_TRACK_MIN_TICKS - 1),
        (300.0, 1),
        (120.0, OFF_TRACK_MIN_TICKS - 1),
        (100.0, 10),
    ):
        x, z = _arc(r, n)
        xs += x
        zs += z
    assert judge.excursions(xs, zs) == 0


def test_a_mostly_unsurveyed_lap_refuses_a_verdict(judge) -> None:
    # More than half the samples classify as unknown: -1, not "clean".
    x_on, z_on = _arc(100.0, 5)
    x_far, z_far = _arc(300.0, 6)
    assert judge.excursions(x_on + x_far, z_on + z_far) == -1
    assert judge.excursions([], []) == -1


# --- the coverage gate --------------------------------------------------------


def test_judge_for_track_gates_on_road_coverage(tmp_path) -> None:
    track = "Test Circuit"
    path = track_bundle.bundle_path(tmp_path, track)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_document(_ring())), encoding="utf-8")
    track_compile._CACHE.clear()
    assert track_limits.judge_for_track(tmp_path, track) is not None

    # The right border entirely unsurveyed: no road resolved (road_pct 0),
    # so no judge — a border-less survey must not condemn laps.
    steps = int(2 * math.pi * 100)
    doc = _document(_ring(skip={("R", i) for i in range(steps)}))
    path.write_text(json.dumps(doc), encoding="utf-8")
    track_compile._CACHE.clear()
    assert track_limits.judge_for_track(tmp_path, track) is None


def test_a_circuit_without_a_bundle_has_no_judge(tmp_path) -> None:
    assert track_limits.judge_for_track(tmp_path, "Nowhere") is None
    assert track_limits.judge_for_track(tmp_path, "") is None


def test_judge_follows_the_recompiled_document(tmp_path) -> None:
    track = "Test Circuit"
    path = track_bundle.bundle_path(tmp_path, track)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_document(_ring())), encoding="utf-8")
    track_compile._CACHE.clear()
    track_limits._CACHE.clear()
    first = track_limits.judge_for_track(tmp_path, track)
    assert first is not None
    assert track_limits.judge_for_track(tmp_path, track) is first  # repeat is cheap

    # More evidence saved: the bundle file changes, for_track recompiles
    # (new compiled_at), and a stale judge must not survive it.
    path.write_text(json.dumps(_document(_ring(skip={("L", 0)}))), encoding="utf-8")
    second = track_limits.judge_for_track(tmp_path, track)
    assert second is not None
    assert second is not first


# --- late identification must not leak a stale WS verdict (#41) ---------------


@pytest.fixture
async def service(tmp_path):
    settings = Settings(source="udp", db_path=tmp_path / "data" / "test.db", ws_rate=1000)
    engine = make_engine(settings.db_path)
    await init_db(engine)
    repo = Repository(make_session_factory(engine))
    svc = TelemetryService(settings, repo, CarDatabase())
    yield svc, settings.db_path.parent
    await svc.stop()
    await engine.dispose()


@pytest.fixture
async def client(service):
    svc, data_dir = service
    app = create_app()
    app.router.lifespan_context = None  # type: ignore[assignment]
    app.state.service = svc
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c, svc, data_dir


def _write_bundle(data_dir, edges, track="Test Circuit") -> None:
    path = track_bundle.bundle_path(data_dir, track)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_document(edges, track)), encoding="utf-8")
    track_compile._CACHE.clear()
    track_limits._CACHE.clear()


def _lap(number: int = 1, wide: bool = True, **fields) -> CompletedLap:
    """One lap round the ring at r=100 with — when wide — a single sustained
    excursion to r=120: off the 10 m road of _ring(), on the 30 m road of
    _ring(radius=110, width=30)."""
    samples = new_sample_store()
    steps = int(2 * math.pi * 100)
    for i in range(steps):
        a = 2 * math.pi * i / steps
        r = 120.0 if wide and 300 <= i < 300 + OFF_TRACK_MIN_TICKS else 100.0
        samples["pos_x"].append(r * math.cos(a))
        samples["pos_z"].append(r * math.sin(a))
        for column in ("t", "dist", "speed", "throttle", "brake", "coast",
                       "tire_slip", "body_height", "fuel"):
            samples[column].append(float(i))
    return CompletedLap(
        number=number, time_ms=90_000, finished_at="", car_id=1,
        samples=samples, fuel_start=1.0, fuel_end=1.0, total_ticks=steps, **fields,
    )


async def test_lap_event_after_late_identification_matches_the_row(service) -> None:
    """The session's first lap: the circuit is identified only after the lap
    was saved unjudged, so the verdict lands by backfill — and the WS lap
    event emitted afterwards must carry it, not the pre-judgement -1."""
    svc, data_dir = service
    track = "Test Circuit"
    _write_bundle(data_dir, _ring())
    # One lap around the ring with a single sustained excursion to r=120.
    lap = _lap()

    # A stored signature makes identification land, exactly one lap late.
    await svc.repo.create_track(track, signature_from_samples(lap.samples))
    svc.session_id = await svc.repo.create_session(
        SessionInfo(car_id=1, started_at="now"), Car(id=1, name="Car")
    )
    events: list[dict] = []
    svc._publish = events.append  # type: ignore[method-assign]
    await svc._on_lap(lap)

    assert svc.track_name == track
    (lap_event,) = [e for e in events if e["type"] == "lap"]
    (row,) = await svc.repo.list_laps(svc.session_id)
    assert row["off_survey_count"] == 1
    assert lap_event["data"]["off_survey_count"] == row["off_survey_count"]
    assert lap_event["data"]["clean_lap"] == row["clean_lap"]
    assert lap.clean_lap is False


# --- clean_lap is derived from both judges, two-way (#92) ---------------------


@pytest.mark.parametrize(
    ("surface", "survey", "clean"),
    [
        (0, 0, True), (0, -1, True), (0, 2, False),
        (1, 0, False), (1, -1, False), (1, 3, False),
        (-1, 0, None), (-1, -1, None), (-1, 1, False),
    ],
)
def test_clean_needs_both_judges(surface, survey, clean) -> None:
    assert clean_verdict(surface, survey) is clean


def test_a_corrected_survey_makes_the_lap_clean_again() -> None:
    lap = _lap(off_track_count=0, clean_lap=True)
    lap.apply_survey_verdict(1)  # survey v1: the edge was in the wrong place
    assert lap.clean_lap is False
    lap.apply_survey_verdict(0)  # survey v2: the road is under the lap after all
    assert lap.clean_lap is True
    lap.apply_survey_verdict(-1)  # no survey: whatever the flags said
    assert lap.clean_lap is True


def test_a_surface_excursion_keeps_a_lap_dirty_whatever_the_survey_says() -> None:
    lap = _lap(off_track_count=1, clean_lap=False)
    for count in (1, 0, -1):
        lap.apply_survey_verdict(count)
        assert lap.clean_lap is False


# --- the history is re-judged when the survey changes (#91) ------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def _session(svc, label: str, *laps: CompletedLap) -> int:
    sid = await svc.repo.create_session(
        SessionInfo(car_id=1, started_at="now"), Car(id=1, name="Car")
    )
    await svc.repo.set_session_track(sid, label)
    for lap in laps:
        await svc.repo.save_lap(sid, lap)
    return sid


def _judged(data_dir, lap: CompletedLap) -> CompletedLap:
    """The lap as the live path saves it: judged against the bundle of the
    moment, and the verdict stored with the row."""
    judge = track_limits.judge_for_track(data_dir, "Test Circuit")
    assert judge is not None
    lap.apply_survey_verdict(judge.excursions(lap.samples["pos_x"], lap.samples["pos_z"]))
    return lap


async def _verdict(svc, session_id: int) -> tuple[int, bool | None]:
    (row,) = await svc.repo.list_laps(session_id)
    return row["off_survey_count"], row["clean_lap"]


async def test_a_corrected_survey_clears_the_flag_on_every_session(service) -> None:
    """A lap flagged wide by survey v1 — its edge in the wrong place — reads
    clean again once v2 puts the road under it, and the pass reaches every
    session on the circuit, not just the live one. The second session's
    label differs in case: one slug, so one bundle, so one pass."""
    svc, data_dir = service
    _write_bundle(data_dir, _ring())  # road r 95..105: the run to r=120 is off it
    first = await _session(svc, "Test Circuit", _judged(data_dir, _lap(off_track_count=0)))
    second = await _session(svc, "test circuit", _judged(data_dir, _lap(off_track_count=0)))
    assert await _verdict(svc, first) == (1, False)
    assert await _verdict(svc, second) == (1, False)
    events: list[dict] = []
    svc._publish = events.append  # type: ignore[method-assign]

    _write_bundle(data_dir, _ring(radius=110, width=30))  # road r 95..125: on it
    result = await svc.rejudge.run_now("test-circuit")
    assert result == {
        "slug": "test-circuit", "labels": ["Test Circuit", "test circuit"],
        "laps": 2, "changed": 2, "judged": True,
    }
    assert await _verdict(svc, first) == (0, True)
    assert await _verdict(svc, second) == (0, True)
    # Open Sessions/Analysis views are nudged to refetch...
    assert [e["type"] for e in events] == ["session"]
    # ...and only when something moved.
    assert (await svc.rejudge.run_now("test-circuit"))["changed"] == 0
    assert len(events) == 1


async def test_the_surface_flags_outrank_any_survey(service) -> None:
    svc, data_dir = service
    _write_bundle(data_dir, _ring())
    sid = await _session(svc, "Test Circuit", _judged(data_dir, _lap(off_track_count=1)))
    assert await _verdict(svc, sid) == (1, False)
    _write_bundle(data_dir, _ring(radius=110, width=30))
    assert (await svc.rejudge.run_now("test-circuit"))["changed"] == 1
    # The survey count moved; the verdict did not, because three wheels
    # were on the grass whatever the surveyed edge says.
    assert await _verdict(svc, sid) == (0, False)


async def test_a_lap_the_one_way_flag_spoiled_is_repaired(service) -> None:
    """What the old derivation left behind: count 0 (re-judged against a
    corrected survey) but clean_lap False (carried over from the bad one).
    The count reads the same as the judge's, so comparing counts alone would
    skip the row; it changes because clean_lap no longer follows from them."""
    svc, data_dir = service
    _write_bundle(data_dir, _ring(radius=110, width=30))
    sid = await _session(
        svc, "Test Circuit", _lap(off_track_count=0, off_survey_count=0, clean_lap=False)
    )
    assert (await svc.rejudge.run_now("test-circuit"))["changed"] == 1
    assert await _verdict(svc, sid) == (0, True)


async def test_no_survey_takes_the_verdicts_back_to_unknown(service) -> None:
    """A deleted bundle's laps must not keep verdicts from geometry that no
    longer exists — and the surface flags alone decide cleanliness again."""
    svc, data_dir = service
    _write_bundle(data_dir, _ring())
    sid = await _session(svc, "Test Circuit", _judged(data_dir, _lap(off_track_count=0)))
    assert await _verdict(svc, sid) == (1, False)
    assert track_bundle.delete(data_dir, "test-circuit")
    result = await svc.rejudge.run_now("test-circuit")
    assert result["judged"] is False
    assert result["changed"] == 1
    assert await _verdict(svc, sid) == (-1, True)


async def test_survey_writes_settle_before_the_history_is_re_judged(service) -> None:
    """A running survey autosaves once a minute, and none of those is the
    moment to read every lap ever driven on the circuit. Each write restarts
    the clock; the pass runs once the bundle has been left alone."""
    svc, data_dir = service
    clock = _Clock()
    svc.rejudge._clock = clock
    _write_bundle(data_dir, _ring())
    sid = await _session(svc, "Test Circuit", _judged(data_dir, _lap(off_track_count=0)))

    _write_bundle(data_dir, _ring(radius=110, width=30))
    svc.survey.on_bundle_saved("Test Circuit")  # what an autosave calls
    assert svc.rejudge.pending() == {"test-circuit": rejudge.SETTLE_S}
    await svc.rejudge.wait_idle()
    assert await _verdict(svc, sid) == (1, False)  # not yet
    # Three autosaves later: still not due, the wait restarted each time.
    for _ in range(3):
        clock.now += 60
        svc.survey.on_bundle_saved("Test Circuit")
    assert svc.rejudge.pending() == {"test-circuit": rejudge.SETTLE_S}
    # Left alone, it runs.
    clock.now += rejudge.SETTLE_S
    svc.rejudge._kick()
    await svc.rejudge.wait_idle()
    assert svc.rejudge.pending() == {}
    assert await _verdict(svc, sid) == (0, True)
    # The same hook queued the upload (#79).
    assert "test-circuit" in svc.sync.tracks._dirty


async def test_re_check_laps_says_what_changed(client) -> None:
    c, svc, data_dir = client
    _write_bundle(data_dir, _ring())
    await _session(svc, "Test Circuit", _judged(data_dir, _lap(off_track_count=0)))
    _write_bundle(data_dir, _ring(radius=110, width=30))

    resp = await c.post("/api/track-bundles/test-circuit/rejudge")
    assert resp.status_code == 200
    assert resp.json() == {
        "slug": "test-circuit", "labels": ["Test Circuit"],
        "laps": 1, "changed": 1, "judged": True,
    }
    assert (await c.post("/api/track-bundles/test-circuit/rejudge")).json()["changed"] == 0
    # A circuit nobody has driven or surveyed is an empty answer, not an error.
    assert (await c.post("/api/track-bundles/nowhere/rejudge")).json() == {
        "slug": "nowhere", "labels": [], "laps": 0, "changed": 0, "judged": False,
    }
    assert (await c.post("/api/track-bundles/Not%20A%20Slug/rejudge")).status_code == 400


async def test_deleting_a_bundle_re_judges_its_laps(client) -> None:
    c, svc, data_dir = client
    _write_bundle(data_dir, _ring())
    sid = await _session(svc, "Test Circuit", _judged(data_dir, _lap(off_track_count=0)))
    assert (await c.delete("/api/track-bundles/test-circuit")).status_code == 200
    # Queued by the endpoint, run by the worker: the request never waits.
    await svc.rejudge.wait_idle()
    assert await _verdict(svc, sid) == (-1, True)


async def test_renaming_a_bundle_re_judges_the_label_left_behind(client) -> None:
    """Sessions keep their label; the bundle moves out from under them, so
    their verdicts go back to unknown exactly as a deleted bundle's would."""
    c, svc, data_dir = client
    _write_bundle(data_dir, _ring())
    sid = await _session(svc, "Test Circuit", _judged(data_dir, _lap(off_track_count=0)))
    resp = await c.patch("/api/track-bundles/test-circuit", json={"track": "Ring Road"})
    assert resp.status_code == 200
    await svc.rejudge.wait_idle()
    assert await _verdict(svc, sid) == (-1, True)


async def test_identifying_old_sessions_judges_their_laps(client) -> None:
    """History named after the fact was saved with no circuit to judge
    against; naming it is what makes the judgement possible."""
    c, svc, data_dir = client
    _write_bundle(data_dir, _ring())
    lap = _lap(off_track_count=0, clean_lap=True)
    sid = await _session(svc, "", lap)
    assert await _verdict(svc, sid) == (-1, True)
    await svc.repo.create_track("Test Circuit", signature_from_samples(lap.samples))
    resp = await c.post("/api/tracks/identify")
    assert resp.json()["identified"] == 1
    await svc.rejudge.wait_idle()
    assert await _verdict(svc, sid) == (1, False)
