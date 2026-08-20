"""The shipped signature seed, and the precedence it has to respect (#58).

Seeding is the fix for a bootstrapping hole — nothing identified a circuit
until a human named one — but it also puts the tracks table in a state it has
never been in: holding rows that can both match the same lap. These tests
cover the two rules that makes safe (a user's own name wins; an ambiguous seed
declines) and the sync that keeps seeded rows current without touching rows
the user owns.
"""

import json

import pytest

from app.config import Settings
from app.main import SEED_DIGEST_KEY, sync_track_seed
from app.processing import track_seed
from app.processing.track_bundle import BundleError
from app.processing.tracks import TrackSignature
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository

pytestmark = pytest.mark.anyio


def _row(official_id: str, name: str, length: float, cx: float = 0.0, cz: float = 0.0,
         half: float = 300.0, provenance: str = "capture") -> dict:
    return {
        "official_id": official_id, "official_name": name, "length_m": length,
        "min_x": cx - half, "max_x": cx + half, "min_z": cz - half, "max_z": cz + half,
        "provenance": provenance, "ambiguous_with": [], "flags": [],
    }


def _doc(*rows: dict) -> dict:
    return {
        "format": track_seed.SEED_FORMAT, "version": track_seed.SEED_VERSION,
        "counts": {}, "signatures": list(rows),
    }


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def repo(tmp_path):
    engine = make_engine(tmp_path / "seed.db")
    await init_db(engine)
    yield Repository(make_session_factory(engine))
    await engine.dispose()


# --- parsing ------------------------------------------------------------------


def test_a_valid_seed_parses() -> None:
    rows = track_seed.parse(_doc(_row("abc123", "Deep Forest Raceway", 3500.0)))
    assert [(r.official_id, r.name, r.length_m) for r in rows] == [
        ("abc123", "Deep Forest Raceway", 3500.0)
    ]


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d.update(format="something-else"), "not a"),
        (lambda d: d.update(version=99), "upgrade first"),
        (lambda d: d.update(signatures={}), "must be a list"),
        (lambda d: d["signatures"][0].update(official_id=""), "are required"),
        (lambda d: d["signatures"][0].update(provenance="invented"), "provenance"),
        (lambda d: d["signatures"][0].update(length_m=0), "must be positive"),
        (lambda d: d["signatures"][0].update(min_x=500.0), "inside out"),
    ],
)
def test_a_malformed_seed_is_refused(mutate, message) -> None:
    doc = _doc(_row("abc123", "Deep Forest Raceway", 3500.0))
    mutate(doc)
    with pytest.raises(BundleError, match=message):
        track_seed.parse(doc)


def test_two_rows_for_one_configuration_are_refused() -> None:
    """A duplicate id means the generator merged two sources wrongly."""
    doc = _doc(_row("abc123", "Deep Forest", 3500.0), _row("abc123", "Deep Forest", 3600.0))
    with pytest.raises(BundleError, match="duplicate"):
        track_seed.parse(doc)


def test_an_unreadable_seed_is_not_fatal(tmp_path) -> None:
    """Identification without a seed is the old behaviour, not a broken app."""
    assert track_seed.load(tmp_path / "absent.json") == []
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert track_seed.load(broken) == []
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"format": "something else"}), encoding="utf-8")
    assert track_seed.load(wrong) == []


# --- precedence ---------------------------------------------------------------


async def test_a_user_named_circuit_beats_a_seeded_one(repo) -> None:
    """The rule #58 must not break: a name a person typed outranks a seed."""
    sig = TrackSignature(3500.0, -300.0, 300.0, -300.0, 300.0)
    await repo.sync_seeded_tracks(track_seed.parse(_doc(
        _row("abc123", "Seeded Name", 3500.0)
    )))
    assert await repo.find_track(sig) == "Seeded Name"

    await repo.create_track("My Own Name", sig)
    assert await repo.find_track(sig) == "My Own Name"


async def test_two_matching_seeds_produce_no_name(repo) -> None:
    """Lago Maggiore Full Course and Suzuka share a box; guessing is wrong.

    Before this rule find_track returned whichever row came back first, which
    would put a wrong circuit on a session with no way to tell.
    """
    await repo.sync_seeded_tracks(track_seed.parse(_doc(
        _row("aaa111", "Autodrome Lago Maggiore - Full Course", 3500.0),
        _row("bbb222", "Suzuka Circuit", 3520.0),
    )))
    assert await repo.find_track(TrackSignature(3500.0, -300.0, 300.0, -300.0, 300.0)) is None


async def test_a_users_name_resolves_an_ambiguous_seed(repo) -> None:
    """Ambiguity among seeds never blocks a circuit the user has named."""
    sig = TrackSignature(3500.0, -300.0, 300.0, -300.0, 300.0)
    await repo.sync_seeded_tracks(track_seed.parse(_doc(
        _row("aaa111", "One", 3500.0), _row("bbb222", "Two", 3520.0),
    )))
    await repo.create_track("The Circuit I Drive", sig)
    assert await repo.find_track(sig) == "The Circuit I Drive"


async def test_a_lap_matching_nothing_is_still_unnamed(repo) -> None:
    await repo.sync_seeded_tracks(track_seed.parse(_doc(_row("abc123", "Elsewhere", 3500.0))))
    assert await repo.find_track(TrackSignature(9000.0, -8000.0, -7000.0, 4000.0, 5000.0)) is None


# --- sync ---------------------------------------------------------------------


async def test_sync_replaces_seeded_rows_and_keeps_the_users(repo) -> None:
    """The point of provenance: a re-sync owns its rows and only its rows."""
    mine = TrackSignature(1000.0, -100.0, 100.0, -100.0, 100.0)
    await repo.create_track("Mine", mine)
    await repo.sync_seeded_tracks(track_seed.parse(_doc(
        _row("aaa111", "Dropped Upstream", 3500.0, cx=5000.0),
        _row("bbb222", "Kept", 4500.0, cx=9000.0),
    )))
    assert {t["name"] for t in await repo.list_tracks()} == {"Mine", "Dropped Upstream", "Kept"}

    # A capture that turned out to be half a lap disappears upstream; a
    # reconcile that only added and updated would leave it here forever.
    await repo.sync_seeded_tracks(track_seed.parse(_doc(_row("bbb222", "Kept", 4500.0, cx=9000.0))))
    rows = await repo.list_tracks()
    assert {t["name"] for t in rows} == {"Mine", "Kept"}
    assert {t["name"]: t["provenance"] for t in rows} == {"Mine": "user", "Kept": "seed"}
    assert await repo.find_track(mine) == "Mine"


async def test_sync_carries_the_official_id(repo) -> None:
    """So a row that turns out wrong is identifiable against the source."""
    await repo.sync_seeded_tracks(track_seed.parse(_doc(_row("46b3d9", "Maggiore", 1972.0))))
    assert [t["official_id"] for t in await repo.list_tracks()] == ["46b3d9"]


# --- startup ------------------------------------------------------------------


async def test_startup_seeds_once_and_then_stays_quiet(repo, tmp_path) -> None:
    """The write happens on a first run and on a changed file, never otherwise."""
    import logging

    path = tmp_path / "seed.json"
    path.write_text(json.dumps(_doc(_row("aaa111", "First", 3500.0))), encoding="utf-8")
    settings = Settings(track_signatures_json=path)
    log = logging.getLogger("test")

    await sync_track_seed(settings, repo, {}, log)
    assert [t["name"] for t in await repo.list_tracks()] == ["First"]

    stored = await repo.get_settings()
    assert SEED_DIGEST_KEY in stored

    # Emptying the table and re-running proves the second pass really skips:
    # an unchanged file leaves the rows exactly as it found them, so a start
    # on an existing database is not 80 writes it does not need.
    await repo.delete_track((await repo.list_tracks())[0]["id"])
    await sync_track_seed(settings, repo, stored, log)
    assert await repo.list_tracks() == []

    # A release that ships a different seed does rewrite.
    path.write_text(json.dumps(_doc(_row("bbb222", "Second", 4500.0))), encoding="utf-8")
    await sync_track_seed(settings, repo, stored, log)
    assert [t["name"] for t in await repo.list_tracks()] == ["Second"]


async def test_a_missing_seed_does_not_stop_startup(repo, tmp_path) -> None:
    import logging

    settings = Settings(track_signatures_json=tmp_path / "absent.json")
    await sync_track_seed(settings, repo, {}, logging.getLogger("test"))
    assert await repo.list_tracks() == []


async def test_seeding_can_be_turned_off(repo) -> None:
    """Blank setting restores the pre-#58 behaviour of naming nothing."""
    import logging

    settings = Settings(track_signatures_json="")
    await sync_track_seed(settings, repo, {}, logging.getLogger("test"))
    assert await repo.list_tracks() == []
    assert SEED_DIGEST_KEY not in await repo.get_settings()


# --- the file we actually ship ------------------------------------------------


def test_the_shipped_seed_is_valid() -> None:
    """The vendored artifact parses, and its ambiguities are declared."""
    rows = track_seed.load(Settings().track_signatures_json)
    assert len(rows) > 50
    assert len({r.official_id for r in rows}) == len(rows)
    assert all(r.provenance in track_seed.PROVENANCES for r in rows)
