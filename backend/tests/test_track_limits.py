"""Judging lap positions against the surveyed road edges (#41)."""

import json
import math

import pytest

from app.config import Settings
from app.processing import track_bundle, track_compile, track_limits
from app.processing.cars import CarDatabase
from app.processing.laps import CompletedLap, SessionInfo, new_sample_store
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
    await engine.dispose()


async def test_lap_event_after_late_identification_matches_the_row(service) -> None:
    """The session's first lap: the circuit is identified only after the lap
    was saved unjudged, so the verdict lands by backfill — and the WS lap
    event emitted afterwards must carry it, not the pre-judgement -1."""
    svc, data_dir = service
    track = "Test Circuit"
    path = track_bundle.bundle_path(data_dir, track)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_document(_ring())), encoding="utf-8")
    track_compile._CACHE.clear()
    track_limits._CACHE.clear()

    # One lap around the ring with a single sustained excursion to r=120.
    samples = new_sample_store()
    steps = int(2 * math.pi * 100)
    for i in range(steps):
        a = 2 * math.pi * i / steps
        r = 120.0 if 300 <= i < 300 + OFF_TRACK_MIN_TICKS else 100.0
        samples["pos_x"].append(r * math.cos(a))
        samples["pos_z"].append(r * math.sin(a))
        for column in ("t", "dist", "speed", "throttle", "brake", "coast",
                       "tire_slip", "body_height", "fuel"):
            samples[column].append(float(i))
    lap = CompletedLap(
        number=1, time_ms=90_000, finished_at="", car_id=1,
        samples=samples, fuel_start=1.0, fuel_end=1.0,
    )

    # A stored signature makes identification land, exactly one lap late.
    await svc.repo.create_track(track, signature_from_samples(samples))
    svc.session_id = await svc.repo.create_session(
        SessionInfo(car_id=1, started_at="now"), "Car"
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
