"""Pulling contributed bundles from a shared repo (#47): the index is
validated, the pulled document goes through the normal import path, and the
server never fetches anywhere the index didn't point."""

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.processing import shared_repo, track_bundle
from app.processing.cars import CarDatabase
from app.service import TelemetryService
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository
from tests.test_track_manager import _foreign_bundle

INDEX_URL = "https://bundles.example/index.json"


def _entry(track="Ring", file="ring.json", **extra):
    """A `bundle` object as the data repo's index builder writes it."""
    return {"track": track, "file": file, **extra}


def _index(*entries, unmatched=(), official_name=None):
    """An index in the shape gt7-datalogger-track-data publishes: mostly
    configurations WITHOUT a bundle (catalog coverage rows), a few with one."""
    return {
        "format": shared_repo.INDEX_FORMAT,
        "version": shared_repo.INDEX_VERSION,
        "bundle_format_version": 4,
        "configurations": [
            {"official_name": "Some Circuit Nobody Surveyed", "bundle": None},
            *({"official_name": official_name, "bundle": e} for e in entries),
        ],
        "unmatched_bundles": list(unmatched),
    }


# --- pure validation ----------------------------------------------------------


def test_index_url_appends_conventional_name() -> None:
    assert shared_repo.index_url("https://x.test/repo") == "https://x.test/repo/index.json"
    assert shared_repo.index_url("https://x.test/repo/") == "https://x.test/repo/index.json"
    assert shared_repo.index_url("https://x.test/my-index.json") == "https://x.test/my-index.json"


def test_resolve_url_relative_and_absolute() -> None:
    assert (
        shared_repo.resolve_url(INDEX_URL, "ring.json")
        == "https://bundles.example/ring.json"
    )
    assert (
        shared_repo.resolve_url(INDEX_URL, "https://elsewhere.test/b.json")
        == "https://elsewhere.test/b.json"
    )


def test_resolve_url_refuses_non_http_schemes() -> None:
    with pytest.raises(track_bundle.BundleError):
        shared_repo.resolve_url(INDEX_URL, "file:///etc/passwd")
    with pytest.raises(track_bundle.BundleError):
        shared_repo.resolve_url("file:///srv/index.json", "ring.json")


def test_validate_index_good_document() -> None:
    entries = shared_repo.validate_index(
        _index(
            _entry(points=4634, runs=12, updated_at="2026-08-01T00:00:00Z"),
            official_name="Ring GP",
            unmatched=[_entry(track="Mystery Circuit", file="tracks/mystery.json")],
        )
    )
    assert entries == [
        {
            "track": "Ring", "url": "ring.json",
            "points": 4634, "runs": 12, "updated_at": "2026-08-01T00:00:00Z",
            "official_name": "Ring GP",
        },
        {"track": "Mystery Circuit", "url": "tracks/mystery.json"},
    ]


def test_validate_index_skips_unsurveyed_configurations() -> None:
    # The bulk of the real index: catalog rows with bundle: null.
    assert shared_repo.validate_index(_index()) == []


def test_validate_index_rejects_malformed_documents() -> None:
    with pytest.raises(track_bundle.BundleError):
        shared_repo.validate_index({"format": "something-else", "version": 1})
    with pytest.raises(track_bundle.BundleError):
        shared_repo.validate_index(_index(_entry(track="")))
    with pytest.raises(track_bundle.BundleError):
        shared_repo.validate_index(_index(_entry(file="")))
    with pytest.raises(track_bundle.BundleError):
        shared_repo.validate_index(_index(_entry(points="lots")))
    with pytest.raises(track_bundle.BundleError):
        shared_repo.validate_index(
            _index(unmatched=[_entry()] * (shared_repo.MAX_INDEX_ENTRIES + 1))
        )


def test_validate_index_refuses_newer_versions() -> None:
    doc = _index(_entry())
    doc["version"] = shared_repo.INDEX_VERSION + 1
    with pytest.raises(track_bundle.BundleError, match="upgrade first"):
        shared_repo.validate_index(doc)


# --- API ----------------------------------------------------------------------


@pytest.fixture
async def client(tmp_path):
    settings = Settings(
        source="udp", db_path=tmp_path / "test.db", ws_rate=1000,
        shared_bundles_url=INDEX_URL,
    )
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


def _serve(monkeypatch, documents: dict) -> list[str]:
    """Stub the repo: fetch_json returns the canned document per URL, and the
    test gets the list of URLs actually fetched."""
    fetched: list[str] = []

    async def fake_fetch(url: str, cap: int):
        fetched.append(url)
        if url not in documents:
            raise httpx.ConnectError(f"no route to {url}")
        return documents[url]

    monkeypatch.setattr(shared_repo, "fetch_json", fake_fetch)
    return fetched


async def test_shared_listing_unconfigured(client, monkeypatch) -> None:
    c, service, _tmp = client
    monkeypatch.setattr(service.settings, "shared_bundles_url", "")
    resp = await c.get("/api/track-bundles/shared")
    assert resp.json() == {"configured": False, "bundles": []}


async def test_shared_listing_carries_slugs(client, monkeypatch) -> None:
    c, _service, _tmp = client
    _serve(monkeypatch, {INDEX_URL: _index(_entry(track="Lago - East End", points=9))})
    body = (await c.get("/api/track-bundles/shared")).json()
    assert body["configured"] is True
    assert body["bundles"] == [{
        "track": "Lago - East End", "url": "ring.json",
        "points": 9, "slug": "lago-east-end",
    }]


async def test_shared_listing_unreachable_repo_is_502(client, monkeypatch) -> None:
    c, _service, _tmp = client
    _serve(monkeypatch, {})
    resp = await c.get("/api/track-bundles/shared")
    assert resp.status_code == 502


async def test_pull_merges_through_the_import_path(client, monkeypatch) -> None:
    c, _service, tmp = client
    _serve(monkeypatch, {
        INDEX_URL: _index(_entry()),
        "https://bundles.example/ring.json": _foreign_bundle(n=6, runs=4),
    })
    first = (await c.post("/api/track-bundles/shared/ring/pull")).json()
    assert first["points"] == 6
    assert first["added_points"] == 6

    # Re-pulling the same bundle must not manufacture agreement (#47's whole
    # premise: counts stay a census of independent observations).
    second = (await c.post("/api/track-bundles/shared/ring/pull")).json()
    assert second["added_points"] == 0

    stored = track_bundle.load(tmp, "Ring")
    assert stored is not None
    assert stored["meta"]["source_runs"] == {"beef0000cafe": 4}


async def test_pull_with_track_override(client, monkeypatch) -> None:
    c, _service, tmp = client
    _serve(monkeypatch, {
        INDEX_URL: _index(_entry()),
        "https://bundles.example/ring.json": _foreign_bundle(n=3),
    })
    result = (await c.post("/api/track-bundles/shared/ring/pull?track=My Ring")).json()
    assert result["track"] == "My Ring"
    assert track_bundle.load(tmp, "My Ring") is not None
    assert track_bundle.load(tmp, "Ring") is None


async def test_pull_unknown_slug_404(client, monkeypatch) -> None:
    c, _service, _tmp = client
    _serve(monkeypatch, {INDEX_URL: _index(_entry())})
    resp = await c.post("/api/track-bundles/shared/nope/pull")
    assert resp.status_code == 404


async def test_pull_invalid_bundle_400(client, monkeypatch) -> None:
    c, _service, tmp = client
    _serve(monkeypatch, {
        INDEX_URL: _index(_entry()),
        "https://bundles.example/ring.json": {"format": "not-a-bundle"},
    })
    resp = await c.post("/api/track-bundles/shared/ring/pull")
    assert resp.status_code == 400
    assert track_bundle.load(tmp, "Ring") is None


async def test_pull_fetches_only_what_the_index_names(client, monkeypatch) -> None:
    """The client names a slug, never a URL: the server GETs the index and the
    one bundle the index maps that slug to — nothing else."""
    c, _service, _tmp = client
    fetched = _serve(monkeypatch, {
        INDEX_URL: _index(_entry()),
        "https://bundles.example/ring.json": _foreign_bundle(),
    })
    await c.post("/api/track-bundles/shared/ring/pull")
    assert fetched == [INDEX_URL, "https://bundles.example/ring.json"]


async def test_pull_unconfigured_404(client, monkeypatch) -> None:
    c, service, _tmp = client
    monkeypatch.setattr(service.settings, "shared_bundles_url", "")
    resp = await c.post("/api/track-bundles/shared/ring/pull")
    assert resp.status_code == 404
