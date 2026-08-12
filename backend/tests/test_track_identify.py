"""Identifying a circuit from its survey bundle (#41).

The gap this closes: surveying a track and NAMING it were two separate facts,
so a driver who surveyed three circuits and never used "name track…" got no
identification at all — and everything downstream of the circuit name (the
outline under the race line, category bests, corner labels) stayed empty
while the app plainly had the map.
"""

import json
import math

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models import SimulatorFlags
from app.processing import track_bundle, tracks
from app.processing.cars import CarDatabase
from app.service import TelemetryService
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository
from app.telemetry.packet import build_packet, parse_packet

ON_TRACK = int(SimulatorFlags.CAR_ON_TRACK)
SOURCE = "abc123abc123"


def _circle(radius: float, centre=(0.0, 0.0), n: int = 400):
    cx, cz = centre
    return [
        (cx + radius * math.cos(2 * math.pi * i / n), cz + radius * math.sin(2 * math.pi * i / n))
        for i in range(n)
    ]


def _bundle(track: str, radius: float, centre=(0.0, 0.0), coverage: float = 1.0):
    """A circuit surveyed as a ring of border evidence either side of the road.

    `coverage` < 1 leaves part of the lap unsurveyed, which is what a
    half-finished survey looks like.
    """
    edges = []
    points = _circle(radius, centre)
    keep = int(len(points) * coverage)
    for i, (x, z) in enumerate(points[:keep]):
        hx, hz = -math.sin(2 * math.pi * i / len(points)), math.cos(2 * math.pi * i / len(points))
        # The right border lies along the right-normal of travel, (hz, -hx).
        for side, offset in (("L", -6.0), ("R", 6.0)):
            edges.append({
                "x": x + offset * hz, "z": z - offset * hx, "y": None,
                "hx": hx, "hz": hz, "side": side, "kind": "auto",
                "votes": {"auto": {SOURCE: [1, 1]}}, "run": 1, "tw": 1.6,
            })
    return {
        "format": track_bundle.BUNDLE_FORMAT,
        "version": track_bundle.BUNDLE_VERSION,
        "meta": {
            "track": track, "runs": 1, "source_runs": {SOURCE: 1},
            "updated_at": "2026-08-01T00:00:00+00:00", "official": None,
        },
        "edges": edges,
        "finish_crossings": [],
        "corners": [],
        "sections": [],
    }


def _lap_samples(radius: float, centre=(0.0, 0.0), n: int = 900):
    xs, zs = [], []
    for x, z in _circle(radius, centre, n):
        xs.append(round(x, 2))
        zs.append(round(z, 2))
    return {
        "t": [i / 60 for i in range(n)],
        "dist": [i * 2.0 for i in range(n)],
        "pos_x": xs,
        "pos_z": zs,
        "speed": [120.0] * n,
    }


def _write(directory, doc):
    path = track_bundle.bundle_path(directory, doc["meta"]["track"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


# --- matching ------------------------------------------------------------------


def test_a_lap_is_matched_to_the_circuit_it_was_driven_on() -> None:
    prints = [
        tracks.fingerprint(_bundle("Ring", 200.0)),
        tracks.fingerprint(_bundle("Elsewhere", 200.0, centre=(5000.0, 5000.0))),
    ]
    hit = tracks.match_bundles(_lap_samples(200.0), prints)
    assert hit is not None
    assert hit[0] == "Ring"
    assert hit[1] > 0.95


def test_a_lap_driven_somewhere_else_matches_nothing() -> None:
    prints = [tracks.fingerprint(_bundle("Ring", 200.0))]
    assert tracks.match_bundles(_lap_samples(200.0, centre=(9000.0, 0.0)), prints) is None


def test_a_half_surveyed_circuit_still_identifies_itself() -> None:
    """A bundle only covers ground that has been driven; requiring full
    coverage would mean a circuit cannot be recognised until it is finished."""
    prints = [tracks.fingerprint(_bundle("Ring", 200.0, coverage=0.75))]
    hit = tracks.match_bundles(_lap_samples(200.0), prints)
    assert hit is not None and hit[0] == "Ring"


def test_two_circuits_that_fit_equally_well_are_refused() -> None:
    """Configurations of one venue share tarmac. Guessing between them puts a
    wrong name on a session silently, which is worse than no name."""
    prints = [
        tracks.fingerprint(_bundle("Layout A", 200.0)),
        tracks.fingerprint(_bundle("Layout B", 200.0)),  # same road
    ]
    assert tracks.match_bundles(_lap_samples(200.0), prints) is None


def test_no_bundles_means_no_answer() -> None:
    assert tracks.match_bundles(_lap_samples(200.0), []) is None


def test_fingerprints_are_rebuilt_when_a_bundle_changes(tmp_path) -> None:
    _write(tmp_path, _bundle("Ring", 200.0))
    first = tracks.load_fingerprints(tmp_path)
    assert [p.track for p in first] == ["Ring"]
    assert tracks.load_fingerprints(tmp_path) is first  # cached

    _write(tmp_path, _bundle("Other", 300.0, centre=(4000.0, 0.0)))
    assert {p.track for p in tracks.load_fingerprints(tmp_path)} == {"Ring", "Other"}


# --- through the pipeline ------------------------------------------------------


@pytest.fixture
async def client(tmp_path):
    settings = Settings(source="udp", db_path=tmp_path / "data" / "test.db", ws_rate=1000)
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
        yield c, service, settings.db_path.parent
    await engine.dispose()


async def drive(service: TelemetryService, radius: float, laps: int = 2) -> None:
    points = _circle(radius, n=300)
    for lap in range(1, laps + 1):
        for tick, (x, z) in enumerate(points):
            await service._on_packet(
                parse_packet(
                    build_packet(
                        packet_id=lap * 1000 + tick,
                        current_lap=lap,
                        last_lap_time_ms=60_000 if lap > 1 else -1,
                        position=(x, 0.0, z),
                        speed_mps=40.0,
                        throttle=200,
                        flags=ON_TRACK,
                    )
                )
            )
    await service._on_packet(
        parse_packet(
            build_packet(
                packet_id=99_999, current_lap=laps + 1, last_lap_time_ms=60_000,
                speed_mps=40.0, flags=ON_TRACK,
            )
        )
    )


async def test_a_new_session_names_itself_from_the_bundle(client) -> None:
    c, service, data_dir = client
    _write(data_dir, _bundle("Ring", 200.0))

    await drive(service, radius=200.0)

    assert service.track_name == "Ring"
    assert (await c.get("/api/sessions")).json()[0]["track_name"] == "Ring"


async def test_a_session_somewhere_unsurveyed_stays_unnamed(client) -> None:
    c, service, data_dir = client
    _write(data_dir, _bundle("Ring", 200.0, centre=(9000.0, 9000.0)))

    await drive(service, radius=200.0)

    assert service.track_name == ""
    assert (await c.get("/api/sessions")).json()[0]["track_name"] == ""


async def test_backfill_names_sessions_recorded_before_the_survey(client) -> None:
    """The case that made this necessary: laps were on disk long before the
    circuit was ever surveyed, so nothing had a chance to identify them."""
    c, service, data_dir = client
    await drive(service, radius=200.0)
    assert (await c.get("/api/sessions")).json()[0]["track_name"] == ""

    _write(data_dir, _bundle("Ring", 200.0))
    result = (await c.post("/api/tracks/identify")).json()

    assert result == {"checked": 1, "identified": 1, "tracks": {"Ring": 1}}
    assert (await c.get("/api/sessions")).json()[0]["track_name"] == "Ring"
    # ...and the outline the whole thing exists to unlock now resolves.
    lap_id = (await c.get("/api/laps")).json()[0]["id"]
    outline = (await c.get(f"/api/track-outline?lap_id={lap_id}")).json()
    assert outline["track"] == "Ring"
    assert len(outline["road"]) > 0


async def test_backfill_leaves_unrecognised_sessions_alone(client) -> None:
    c, service, data_dir = client
    await drive(service, radius=200.0)
    _write(data_dir, _bundle("Ring", 200.0, centre=(9000.0, 9000.0)))

    result = (await c.post("/api/tracks/identify")).json()
    assert result["checked"] == 1 and result["identified"] == 0
    assert (await c.get("/api/sessions")).json()[0]["track_name"] == ""


async def test_backfill_refuses_when_nothing_has_been_surveyed(client) -> None:
    c, service, _data_dir = client
    await drive(service, radius=200.0)
    assert (await c.post("/api/tracks/identify")).status_code == 409


async def test_a_named_signature_still_wins(client) -> None:
    """A circuit a human named outranks anything inferred from a bundle."""
    c, service, data_dir = client
    _write(data_dir, _bundle("Ring", 200.0))
    await drive(service, radius=200.0)
    lap_id = (await c.get("/api/laps")).json()[0]["id"]
    await c.post("/api/tracks", json={"name": "My Own Name", "lap_id": lap_id})

    service.track_name = ""
    service.session_id = None
    await drive(service, radius=200.0)
    assert service.track_name == "My Own Name"
