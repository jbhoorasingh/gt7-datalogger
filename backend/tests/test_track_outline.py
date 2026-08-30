"""The surveyed road, compiled for the race-line map (#51)."""

import json
import math

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.processing import track_bundle, track_compile, track_outline
from app.processing.cars import Car, CarDatabase
from app.processing.laps import CompletedLap, SessionInfo, new_sample_store
from app.service import TelemetryService
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository

SOURCE = "abc123abc123"


def _edge(x, z, hx, hz, side, kind="auto"):
    return {
        "x": x, "z": z, "y": None, "hx": hx, "hz": hz,
        "side": side, "kind": kind, "votes": {kind: {SOURCE: [1, 1]}},
        "run": 1, "tw": 1.6,
    }


def _straight(length=40, width=12.0, kind="auto"):
    """A straight road running along +x, with both borders evidenced.

    Sides follow the survey's convention: the right border sits along the
    right-normal of the travel direction, which for a heading of (1, 0) is
    (hz, -hx) = (0, -1) — so the right border is at negative z.
    """
    edges = []
    for i in range(length):
        edges.append(_edge(float(i), width / 2, 1.0, 0.0, "L", kind))
        edges.append(_edge(float(i), -width / 2, 1.0, 0.0, "R", kind))
    return edges


def _ring(radius=40.0, width=10.0, skip=()):
    """A circular circuit at the compile's native pitch: one border cell per
    metre per side, headings tangent to travel — what the ordered compile
    (#44) needs to chain. `skip` = (side, step) pairs left unsurveyed."""
    edges = []
    steps = int(2 * math.pi * radius)
    for i in range(steps):
        a = 2 * math.pi * i / steps
        hx, hz = -math.sin(a), math.cos(a)
        for side, r in (("L", radius - width / 2), ("R", radius + width / 2)):
            if (side, i) not in skip:
                edges.append(_edge(r * math.cos(a), r * math.sin(a), hx, hz, side))
    return edges


def _document(edges, finish=(), track="Test Circuit"):
    return {
        "format": track_bundle.BUNDLE_FORMAT,
        "version": track_bundle.BUNDLE_VERSION,
        "meta": {
            "track": track, "runs": 2, "source_runs": {SOURCE: 2},
            "updated_at": "2026-08-01T00:00:00+00:00", "official": None,
        },
        "edges": edges,
        "finish_crossings": list(finish),
        "corners": [],
        "sections": [],
    }


# --- pairing -----------------------------------------------------------------


def test_facing_borders_fill_the_road() -> None:
    quads = track_outline.road_quads(_straight())
    assert len(quads) == 40
    # Corners run left-back, left-forward, right-forward, right-back: the road
    # width across the carriageway and a fixed extent along travel.
    (lbx, lbz), (lfx, lfz), _rf, (rbx, rbz) = (
        tuple(quads[0][i : i + 2]) for i in (0, 2, 4, 6)
    )
    assert math.hypot(rbx - lbx, rbz - lbz) == pytest.approx(12.0, abs=0.01)
    assert math.hypot(lfx - lbx, lfz - lbz) == pytest.approx(
        2 * track_outline.QUAD_HALF_LENGTH_M, abs=0.01
    )


def test_a_road_wider_than_any_road_is_not_paired() -> None:
    """Two borders 60 m apart are the two legs of a hairpin, not one road."""
    assert track_outline.road_quads(_straight(width=60.0)) == []


def test_borders_recorded_travelling_opposite_ways_are_not_paired() -> None:
    edges = [
        _edge(0.0, 6.0, 1.0, 0.0, "L"),
        _edge(0.0, -6.0, -1.0, 0.0, "R"),  # the other leg, driven back the way
    ]
    assert track_outline.road_quads(edges) == []


def test_run_off_marks_still_count_as_road_edges() -> None:
    """They bound the road and happen to have pavement beyond them; excluding
    them drew no road at all through hand-surveyed corners."""
    assert len(track_outline.road_quads(_straight(kind="runoff"))) == 40


def test_walls_are_kept_apart_from_ordinary_borders() -> None:
    edges = _straight(length=4) + [_edge(2.0, -9.0, 1.0, 0.0, "R", kind="wall")]
    outline = track_outline.build(_document(edges))
    assert len(outline["walls"]) == 1
    assert len(outline["edges"]) == 8
    # A wall is still a border, so it can still close a road span.
    assert outline["road"]


# --- finish line -------------------------------------------------------------


def test_finish_line_lies_across_the_direction_of_travel() -> None:
    crossings = [
        {"x": 0.0, "z": -2.0, "hx": 1.0, "hz": 0.0, "lap": 1},
        {"x": 0.0, "z": 2.0, "hx": 1.0, "hz": 0.0, "lap": 2},
    ]
    x1, z1, x2, z2 = track_outline.finish_line(crossings)
    assert x1 == pytest.approx(0.0) and x2 == pytest.approx(0.0)
    assert math.hypot(x2 - x1, z2 - z1) == pytest.approx(2 * track_outline.FINISH_HALF_M)


def test_no_crossings_means_no_finish_line() -> None:
    assert track_outline.finish_line([]) is None


# --- loading, caching, and the endpoint --------------------------------------


def test_unsurveyed_circuit_gets_an_empty_outline(tmp_path) -> None:
    assert track_outline.for_track(tmp_path, "Never Driven") == track_outline.EMPTY
    assert track_outline.for_track(tmp_path, "") == track_outline.EMPTY


def test_outline_is_recompiled_when_the_bundle_changes(tmp_path) -> None:
    path = track_bundle.bundle_path(tmp_path, "Test Circuit")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_document(_straight(length=4))), encoding="utf-8")
    first = track_outline.for_track(tmp_path, "Test Circuit")
    assert len(first["road"]) == 4
    # Same call again is the cached object, not a fresh compile.
    assert track_outline.for_track(tmp_path, "Test Circuit") is first

    path.write_text(json.dumps(_document(_straight(length=9))), encoding="utf-8")
    second = track_outline.for_track(tmp_path, "Test Circuit")
    assert len(second["road"]) == 9


@pytest.fixture
async def client(tmp_path):
    settings = Settings(source="udp", db_path=tmp_path / "data" / "test.db", ws_rate=1000)
    engine = make_engine(settings.db_path)
    await init_db(engine)
    repo = Repository(make_session_factory(engine))
    service = TelemetryService(settings, repo, CarDatabase())

    app = create_app()
    app.router.lifespan_context = None  # type: ignore[assignment]
    app.state.service = service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, service, settings.db_path.parent
    await engine.dispose()


async def test_endpoint_resolves_the_circuit_from_a_lap(client) -> None:
    c, service, data_dir = client
    path = track_bundle.bundle_path(data_dir, "Test Circuit")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            _document(
                _ring(),
                finish=[{"x": 35.0, "z": 0.0, "hx": 0.0, "hz": 1.0, "lap": 1}],
            )
        ),
        encoding="utf-8",
    )

    samples = new_sample_store()
    for i in range(10):
        for column in ("t", "dist", "speed", "pos_x", "pos_z", "throttle", "brake",
                       "coast", "tire_slip", "body_height", "fuel"):
            samples[column].append(float(i))
    lap = CompletedLap(
        number=1, time_ms=90_000, finished_at="", car_id=1,
        samples=samples, fuel_start=1.0, fuel_end=1.0,
    )
    session_id = await service.repo.create_session(
        SessionInfo(car_id=1, started_at="now"), Car(id=1, name="Car")
    )
    lap_id = await service.repo.save_lap(session_id, lap)
    await service.repo.set_session_track(session_id, "Test Circuit")

    body = (await c.get(f"/api/track-outline?lap_id={lap_id}")).json()
    assert body["track"] == "Test Circuit"
    # The compiled pathway (#44): the whole ring's road, not the handful of
    # directly-opposite pairs the local pairing found.
    assert len(body["road"]) > 40
    assert body["finish"] is not None
    assert body["runs"] == 2


async def test_endpoint_answers_empty_rather_than_404(client) -> None:
    """A circuit nobody has surveyed is the common case, not an error — the
    map has to be able to fall back to drawing the lap on its own."""
    c, _service, _data_dir = client
    r = await c.get("/api/track-outline?track=Somewhere%20Else")
    assert r.status_code == 200
    assert r.json()["road"] == []
    assert r.json()["gaps"] == [] and r.json()["coverage"] is None
    assert (await c.get("/api/track-outline")).json()["slug"] is None


async def test_endpoint_serves_compiled_gaps_and_coverage(client) -> None:
    """A partially surveyed circuit answers with its holes stated: gap spans
    for the map to draw dashed, and the coverage score they count against."""
    c, _service, data_dir = client
    hole = {("L", i) for i in range(100, 130)}  # 30 unsurveyed metres
    path = track_bundle.bundle_path(data_dir, "Test Circuit")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_document(_ring(radius=60.0, skip=hole))),
                    encoding="utf-8")

    body = (await c.get("/api/track-outline?track=Test%20Circuit")).json()
    assert len(body["road"]) > 50
    assert body["edges"]  # border polylines flattened into drawable segments
    assert len(body["gaps"]) == 1
    x1, z1, x2, z2 = body["gaps"][0]
    assert math.hypot(x2 - x1, z2 - z1) > 20.0
    cov = body["coverage"]
    assert cov["L"]["pct"] < 100.0 and cov["L"]["gap_m"] > 20.0
    assert cov["R"]["pct"] == 100.0 and cov["R"]["closed"] is True
    assert cov["road_pct"] > 0.0
    assert body["updated_at"] == "2026-08-01T00:00:00+00:00"


async def test_endpoint_falls_back_when_the_compile_fails(client, monkeypatch) -> None:
    """A bundle the compiler chokes on must still draw — the legacy local
    pairing, with the gap/coverage keys present but empty. Never a 500."""
    c, _service, data_dir = client
    path = track_bundle.bundle_path(data_dir, "Test Circuit")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_document(_straight(length=40))), encoding="utf-8")

    def boom(*_args, **_kwargs):
        raise RuntimeError("compile exploded")

    monkeypatch.setattr(track_compile, "for_track", boom)
    r = await c.get("/api/track-outline?track=Test%20Circuit")
    assert r.status_code == 200
    body = r.json()
    assert len(body["road"]) == 40  # the local pairing's quads
    assert body["gaps"] == [] and body["coverage"] is None
