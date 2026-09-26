"""The shared repo's corrections, read here: the format is held to the data
repo's, what an area keeps out and what is drawn in are applied at compile
time and never to the bundle, and the endpoints keep, show and drop a
circuit's file."""

import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.processing import track_bundle, track_compile, track_corrections
from app.processing.cars import CarDatabase
from app.service import TelemetryService
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository
from tests.test_track_manager import FOREIGN, _foreign_bundle

OFFICIAL = {
    "track": "Ring", "layout": "GP", "official_id": "ring-gp", "official_name": "Ring GP",
    "turns": 10, "length_m": 4000.0, "reverse": False,
}


def _area(polygon, sides=("L",), **over):
    return {
        "id": "a1", "sides": list(sides), "polygon": polygon, "y": None,
        "only_drawn": False, "reason": "wrong side", "by": "JB", "at": "", **over,
    }


def _drawn(x, z, side="L", source="drawn-0a1b2c3d", **over):
    return {
        "x": x, "z": z, "y": None, "hx": 1.0, "hz": 0.0, "side": side, "kind": "edge",
        "votes": {"edge": {source: [1, 1]}}, "run": 1, "tw": None, **over,
    }


def _document(*areas, draw=(), smooth=None, official_id="ring-gp", track="Ring"):
    return {
        "format": track_corrections.FORMAT,
        "version": track_corrections.VERSION,
        "official_id": official_id,
        "track": track,
        "compile": {"smooth_borders": smooth},
        "exclude": list(areas),
        "draw": list(draw),
    }


# The stranger's bundle (`_foreign_bundle`) is six L records at x = 0..5, z = 0.
# This rectangle takes in the first four of them.
FIRST_FOUR = [[-0.5, -1.0], [3.5, -1.0], [3.5, 1.0], [-0.5, 1.0]]


# --- the format ---------------------------------------------------------------


def test_an_empty_document_validates_and_says_nothing() -> None:
    doc = track_corrections.validate(_document())
    assert track_corrections.is_empty(doc)
    assert track_corrections.is_empty(None)
    assert not track_corrections.is_empty(track_corrections.validate(_document(_area(FIRST_FOUR))))
    assert not track_corrections.is_empty(track_corrections.validate(_document(smooth=False)))
    assert track_corrections.summary(doc) == {"areas": 0, "drawn": 0, "smooth_borders": None}


@pytest.mark.parametrize(
    "mutate, why",
    [
        (lambda d: d.update(format="something-else"), "format"),
        (lambda d: d.update(version=2), "version"),
        (lambda d: d.update(extra=1), "unknown key"),
        (lambda d: d.update(official_id=""), "official_id"),
        (lambda d: d.update(compile={"smooth_borders": "off"}), "smooth_borders"),
        (lambda d: d["exclude"].append(_area(FIRST_FOUR, reason="")), "reason"),
        (lambda d: d["exclude"].append(_area(FIRST_FOUR, sides=["L", "L"])), "sides"),
        (lambda d: d["exclude"].append(_area([[0, 0], [1, 1], [2, 2]])), "encloses no ground"),
        (lambda d: d["exclude"].append(_area(FIRST_FOUR, y=[3, 1])), "y must"),
        (lambda d: d["exclude"].extend([_area(FIRST_FOUR), _area(FIRST_FOUR)]), "id of its own"),
        (lambda d: d["draw"].append(_drawn(0.0, 0.0, source=FOREIGN)), "only a drawn-"),
        (lambda d: d["draw"].append(
            _drawn(0.0, 0.0, kind="auto", votes={"auto": {"drawn-1": [1, 1]}})
        ), "kind must be"),
        (lambda d: d["draw"].extend([_drawn(0.0, 0.0), _drawn(0.2, 0.0)]), "two records"),
        (lambda d: d["draw"].append(_drawn(90_000.0, 0.0)), "not on any circuit"),
    ],
)
def test_malformed_documents_are_refused(mutate, why) -> None:
    doc = _document()
    mutate(doc)
    with pytest.raises(track_bundle.BundleError, match=why):
        track_corrections.validate(doc)


def test_validation_normalises_sides_and_orders_drawn_records() -> None:
    doc = track_corrections.validate(
        _document(_area(FIRST_FOUR, sides=["R", "L"]), draw=[_drawn(2.0, 0.0), _drawn(1.0, 0.0)])
    )
    assert doc["exclude"][0]["sides"] == ["L", "R"]
    assert [r["x"] for r in doc["draw"]] == [1.0, 2.0]
    assert track_corrections.summary(doc) == {"areas": 1, "drawn": 2, "smooth_borders": None}


# --- what a correction does ---------------------------------------------------


def _edge(x, z, side="L", y=None, source=FOREIGN):
    return {"x": x, "z": z, "y": y, "hx": 1.0, "hz": 0.0, "side": side, "kind": "edge",
            "votes": {"edge": {source: [1, 1]}}, "run": 1, "tw": 1.6}


def test_an_area_keeps_out_its_sides_its_levels_and_only_what_it_encloses() -> None:
    area = track_corrections.validate(_document(_area(FIRST_FOUR)))["exclude"][0]
    assert track_corrections.excludes(area, _edge(1.0, 0.0))
    assert not track_corrections.excludes(area, _edge(1.0, 0.0, side="R"))
    assert not track_corrections.excludes(area, _edge(5.0, 0.0))

    levelled = track_corrections.validate(_document(_area(FIRST_FOUR, y=[6.5, 9.5])))["exclude"][0]
    assert track_corrections.excludes(levelled, _edge(1.0, 0.0, y=8.0))
    assert not track_corrections.excludes(levelled, _edge(1.0, 0.0, y=0.0))
    # No elevation is every level: the metre recorded before elevation existed.
    assert track_corrections.excludes(levelled, _edge(1.0, 0.0, y=None))

    drawn_only = track_corrections.validate(
        _document(_area(FIRST_FOUR, only_drawn=True))
    )["exclude"][0]
    assert track_corrections.excludes(drawn_only, _edge(1.0, 0.0, source="drawn-0a1b2c3d"))
    assert not track_corrections.excludes(drawn_only, _edge(1.0, 0.0))


def test_apply_takes_records_out_and_draws_records_in_but_never_over_a_driven_one() -> None:
    edges = [_edge(float(x), 0.0) for x in range(6)]
    doc = track_corrections.validate(
        _document(_area(FIRST_FOUR), draw=[_drawn(20.0, 0.0), _drawn(21.0, 0.0), _drawn(5.0, 0.0)])
    )
    kept, counts = track_corrections.apply(edges, doc)
    assert counts == {"excluded": 4, "drawn": 2}
    assert [e["x"] for e in kept] == [4.0, 5.0, 20.0, 21.0]
    # The evidence is untouched: what comes back is for the compiler alone.
    assert len(edges) == 6
    assert track_corrections.apply(edges, None) == (edges, {"excluded": 0, "drawn": 0})

    # A drawn record on another level of a surveyed cell is another road.
    stacked = track_corrections.validate(_document(draw=[_drawn(5.0, 0.0, y=20.0)]))
    _kept, counts = track_corrections.apply([_edge(5.0, 0.0, y=0.0)], stacked)
    assert counts == {"excluded": 0, "drawn": 1}


def test_an_area_can_take_back_what_was_drawn() -> None:
    doc = track_corrections.validate(
        _document(_area(FIRST_FOUR, only_drawn=True), draw=[_drawn(1.0, 0.0), _drawn(20.0, 0.0)])
    )
    kept, counts = track_corrections.apply([_edge(2.0, 0.0)], doc)
    assert counts == {"excluded": 0, "drawn": 1}
    assert [e["x"] for e in kept] == [2.0, 20.0]


# --- on disk ------------------------------------------------------------------


def test_load_treats_a_file_that_will_not_validate_as_absent(tmp_path, caplog) -> None:
    assert track_corrections.load(tmp_path, "ring") is None
    track_corrections.write(
        tmp_path, "ring", track_corrections.validate(_document(_area(FIRST_FOUR)))
    )
    loaded = track_corrections.load(tmp_path, "ring")
    assert loaded is not None and loaded["exclude"][0]["id"] == "a1"
    assert track_corrections.identity(tmp_path, "ring") is not None

    track_corrections.path(tmp_path, "ring").write_text("{not json", encoding="utf-8")
    assert track_corrections.load(tmp_path, "ring") is None
    assert "ignored" in caplog.text
    assert track_corrections.remove(tmp_path, "ring")
    assert not track_corrections.remove(tmp_path, "ring")
    assert track_corrections.identity(tmp_path, "ring") is None


def test_move_follows_a_rename_unless_the_target_has_its_own(tmp_path) -> None:
    mine = track_corrections.validate(_document(_area(FIRST_FOUR)))
    track_corrections.write(tmp_path, "ring", mine)
    assert track_corrections.move(tmp_path, "ring", "ring-gp")
    assert track_corrections.load(tmp_path, "ring") is None
    assert track_corrections.load(tmp_path, "ring-gp") == mine

    theirs = track_corrections.validate(_document(smooth=False))
    track_corrections.write(tmp_path, "loop", theirs)
    assert not track_corrections.move(tmp_path, "ring-gp", "loop")
    assert track_corrections.load(tmp_path, "loop") == theirs
    assert track_corrections.load(tmp_path, "ring-gp") is None
    assert not track_corrections.move(tmp_path, "nowhere", "loop")


# --- the endpoints ------------------------------------------------------------


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
        yield c, service, tmp_path
    await engine.dispose()


async def test_corrections_need_a_bundle_to_correct(client) -> None:
    c, _service, _tmp = client
    resp = await c.put("/api/track-bundles/ring/corrections", json=_document(_area(FIRST_FOUR)))
    assert resp.status_code == 404
    assert (await c.get("/api/track-bundles/ring/corrections")).status_code == 404
    assert (await c.delete("/api/track-bundles/ring/corrections")).status_code == 404


async def test_corrections_are_kept_shown_applied_and_dropped(client) -> None:
    c, _service, tmp = client
    await c.post("/api/track-bundles/import", json=_foreign_bundle(n=6))
    track_compile._CACHE.clear()
    plain = track_compile.for_track(tmp, "Ring")
    assert plain is not None and plain["corrections"] is None

    resp = await c.put("/api/track-bundles/ring/corrections", json=_document(_area(FIRST_FOUR)))
    assert resp.status_code == 200
    assert resp.json()["corrections"] == {"areas": 1, "drawn": 0, "smooth_borders": None}
    shown = (await c.get("/api/track-bundles/ring/corrections")).json()
    assert shown["exclude"][0]["polygon"] == FIRST_FOUR

    # The map is compiled from what is left, and the evidence is what it was.
    track_compile._CACHE.clear()
    corrected = track_compile.for_track(tmp, "Ring")
    assert corrected is not None
    assert corrected["corrections"] == {"excluded": 4, "drawn": 0, "smooth_borders": None}
    assert corrected["source"]["points"] == 6
    stored = track_bundle.load(tmp, "Ring")
    assert stored is not None and len(stored["edges"]) == 6

    # Replaced whole, never merged; an empty document is the same as none.
    resp = await c.put("/api/track-bundles/ring/corrections", json=_document(smooth=False))
    assert resp.json()["corrections"] == {"areas": 0, "drawn": 0, "smooth_borders": False}
    assert (await c.get("/api/track-bundles/ring/corrections")).json()["exclude"] == []
    resp = await c.put("/api/track-bundles/ring/corrections", json=_document())
    assert resp.status_code == 200
    assert (await c.get("/api/track-bundles/ring/corrections")).status_code == 404

    await c.put("/api/track-bundles/ring/corrections", json=_document(_area(FIRST_FOUR)))
    assert (await c.delete("/api/track-bundles/ring/corrections")).status_code == 200
    assert track_corrections.load(tmp, "ring") is None


async def test_corrections_are_refused_when_malformed_or_for_another_layout(client) -> None:
    c, _service, tmp = client
    await c.post("/api/track-bundles/import", json=_foreign_bundle(n=6))
    resp = await c.put("/api/track-bundles/ring/corrections", content=b"{not json")
    assert resp.status_code == 400
    resp = await c.put(
        "/api/track-bundles/ring/corrections", json=_document(_area(FIRST_FOUR, reason=""))
    )
    assert resp.status_code == 400
    assert "reason" in resp.json()["detail"]

    # A bundle with no confirmed layout takes any; one with a layout takes its own.
    assert (await c.put(
        "/api/track-bundles/ring/corrections", json=_document(official_id="other")
    )).status_code == 200
    track_bundle.set_official(tmp, "Ring", OFFICIAL)
    resp = await c.put("/api/track-bundles/ring/corrections", json=_document(official_id="other"))
    assert resp.status_code == 400
    assert "ring-gp" in resp.json()["detail"]
    assert (await c.put(
        "/api/track-bundles/ring/corrections", json=_document(_area(FIRST_FOUR))
    )).status_code == 200


async def test_corrections_follow_the_bundle_through_rename_and_delete(client) -> None:
    c, _service, tmp = client
    await c.post("/api/track-bundles/import", json=_foreign_bundle(n=6))
    await c.put("/api/track-bundles/ring/corrections", json=_document(_area(FIRST_FOUR)))

    resp = await c.patch("/api/track-bundles/ring", json={"track": "Ring GP"})
    assert resp.status_code == 200
    assert track_corrections.load(tmp, "ring") is None
    moved = track_corrections.load(tmp, "ring-gp")
    assert moved is not None and moved["exclude"][0]["id"] == "a1"

    assert (await c.delete("/api/track-bundles/ring-gp")).status_code == 200
    assert track_corrections.load(tmp, "ring-gp") is None
    assert not track_corrections.path(tmp, "ring-gp").exists()


async def test_the_endpoints_are_admin_gated_except_the_read(tmp_path) -> None:
    settings = Settings(
        source="udp", db_path=tmp_path / "test.db", ws_rate=1000, admin_token="secret",
    )
    engine = make_engine(settings.db_path)
    await init_db(engine)
    repo = Repository(make_session_factory(engine))
    service = TelemetryService(settings, repo, CarDatabase())
    app = create_app()
    app.router.lifespan_context = None  # type: ignore[assignment]
    app.state.service = service
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            track_bundle.merge_document(
                tmp_path, track_bundle.validate_document(_foreign_bundle(n=6))
            )
            body = json.dumps(_document(_area(FIRST_FOUR))).encode()
            resp = await c.put("/api/track-bundles/ring/corrections", content=body)
            assert resp.status_code == 401
            assert (await c.delete("/api/track-bundles/ring/corrections")).status_code == 401
            resp = await c.put(
                "/api/track-bundles/ring/corrections", content=body,
                headers={"X-API-Key": "secret"},
            )
            assert resp.status_code == 200
            assert (await c.get("/api/track-bundles/ring/corrections")).status_code == 200
    finally:
        await engine.dispose()
