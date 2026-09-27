"""Layout CRUD API tests (named overlay/dashboard grid configs)."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.processing.cars import CarDatabase
from app.service import TelemetryService
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository


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
        yield c
    await engine.dispose()


def config(cols: int = 8) -> dict:
    return {
        "version": 2,
        "grid": {"cols": cols, "rows": 5, "gap": 8},
        "cells": [
            {"id": "c1", "widget": "gear", "variant": "digits", "x": 0, "y": 0, "w": 1, "h": 1}
        ],
        "page": "transparent",
        "bg": 70,
        "size": None,
        "padX": 16,
        "padY": 16,
        "demo": False,
    }


async def test_layout_crud_roundtrip(client) -> None:
    # create
    resp = await client.post(
        "/api/layouts", json={"name": "race strip", "kind": "overlay", "config": config()}
    )
    assert resp.status_code == 200
    created = resp.json()
    assert created["name"] == "race strip"
    assert created["kind"] == "overlay"
    assert created["config"]["grid"]["cols"] == 8
    layout_id = created["id"]

    # list
    resp = await client.get("/api/layouts")
    assert [row["name"] for row in resp.json()] == ["race strip"]

    # get by id and by (URL-encoded) name
    assert (await client.get(f"/api/layouts/{layout_id}")).json()["id"] == layout_id
    assert (await client.get("/api/layouts/race%20strip")).json()["id"] == layout_id

    # update config + rename
    resp = await client.put(
        f"/api/layouts/{layout_id}", json={"name": "race", "config": config(cols=12)}
    )
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["name"] == "race"
    assert updated["config"]["grid"]["cols"] == 12
    assert updated["updated_at"] >= updated["created_at"]

    # delete
    assert (await client.delete(f"/api/layouts/{layout_id}")).status_code == 200
    assert (await client.get(f"/api/layouts/{layout_id}")).status_code == 404


async def test_duplicate_names_conflict(client) -> None:
    assert (
        await client.post("/api/layouts", json={"name": "dash", "config": config()})
    ).status_code == 200
    assert (
        await client.post("/api/layouts", json={"name": "dash", "config": config()})
    ).status_code == 409

    # renaming onto an existing name also conflicts
    other = (
        await client.post("/api/layouts", json={"name": "other", "config": config()})
    ).json()
    resp = await client.put(f"/api/layouts/{other['id']}", json={"name": "dash"})
    assert resp.status_code == 409
    # renaming to its own name is fine
    resp = await client.put(f"/api/layouts/{other['id']}", json={"name": "other"})
    assert resp.status_code == 200


async def test_rejects_bad_configs(client) -> None:
    # wrong version
    resp = await client.post(
        "/api/layouts", json={"name": "bad", "config": {"version": 1}}
    )
    assert resp.status_code == 400

    # oversized config
    big = config()
    big["blob"] = "x" * (65 * 1024)
    resp = await client.post("/api/layouts", json={"name": "big", "config": big})
    assert resp.status_code == 400

    # blank name
    resp = await client.post("/api/layouts", json={"name": "   ", "config": config()})
    assert resp.status_code == 400

    # unknown ref
    assert (await client.get("/api/layouts/nope")).status_code == 404


async def test_numeric_name_lookup_falls_back(client) -> None:
    """A layout literally named "42" is still reachable when id 42 is free."""
    created = (
        await client.post("/api/layouts", json={"name": "42", "config": config()})
    ).json()
    resp = await client.get("/api/layouts/42")
    assert resp.status_code == 200
    # id lookup wins when the id exists; otherwise the name matches
    assert resp.json()["id"] == created["id"] or resp.json()["name"] == "42"


async def test_switching_kind_keeps_everything_else(client) -> None:
    """Kind is only the picker a layout lists under; a PUT can flip it alone."""
    created = (
        await client.post(
            "/api/layouts", json={"name": "wheel", "kind": "overlay", "config": config()}
        )
    ).json()

    resp = await client.put(f"/api/layouts/{created['id']}", json={"kind": "dash"})
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["kind"] == "dash"
    assert updated["name"] == "wheel"
    assert updated["config"] == created["config"]
    # persisted, and the name lookup that ?layout= uses still resolves it
    fetched = (await client.get("/api/layouts/wheel")).json()
    assert fetched["id"] == created["id"]
    assert fetched["kind"] == "dash"

    # kind travels with a name/config change in one request too
    resp = await client.put(
        f"/api/layouts/{created['id']}",
        json={"name": "wheel 2", "kind": "overlay", "config": config(cols=10)},
    )
    assert resp.status_code == 200
    assert resp.json()["kind"] == "overlay"
    assert resp.json()["config"]["grid"]["cols"] == 10

    # omitting kind leaves it alone
    resp = await client.put(f"/api/layouts/{created['id']}", json={"name": "wheel 3"})
    assert resp.json()["kind"] == "overlay"


async def test_rejects_unknown_kind(client) -> None:
    created = (
        await client.post("/api/layouts", json={"name": "k", "config": config()})
    ).json()
    resp = await client.put(f"/api/layouts/{created['id']}", json={"kind": "hud"})
    assert resp.status_code == 422
    assert (await client.get(f"/api/layouts/{created['id']}")).json()["kind"] == "overlay"
