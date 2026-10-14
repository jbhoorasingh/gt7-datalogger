"""Every setting in Admin: the registry, GET/PUT /api/admin/config, the docs."""

import logging
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app import settings_registry as registry
from app.config import Settings
from app.main import create_app
from app.processing.cars import CarDatabase
from app.service import TelemetryService
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository

DOC = Path(__file__).resolve().parents[2] / "docs" / "getting-started" / "configuration.md"


@pytest.fixture
async def client(tmp_path):
    settings = Settings(
        source="udp", db_path=tmp_path / "test.db", ws_rate=30, telemetry_port=43746
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
        yield c, service
    await service.stop()
    await engine.dispose()


def by_key(body: dict) -> dict[str, dict]:
    return {s["key"]: s for s in body["settings"]}


# --- the registry -----------------------------------------------------------


def test_every_setting_is_registered() -> None:
    assert set(registry.BY_KEY) == set(Settings.model_fields)
    assert {e.group for e in registry.ENTRIES} <= set(registry.GROUPS)


def test_documentation_table_is_current() -> None:
    text = DOC.read_text()
    assert registry.splice_docs(text) == text, (
        "configuration.md is out of date: run python backend/scripts/gen_config_docs.py"
    )


def test_documented_defaults_are_the_shipped_ones() -> None:
    table = registry.render_markdown()
    assert "| `GT7_WS_RATE` | `30` | live |" in table
    assert "| `GT7_HTTP_PORT` | `8000` | environment only |" in table
    assert "| `GT7_SYNC_TOKEN` | *(empty)* | live |" in table


@pytest.mark.parametrize(("key", "value", "expected"), [
    ("ws_rate", "45", 45),
    ("sync_live_hz", 2, 2.0),
    ("race_engineer", "false", False),
    ("webhook_events", ["overtake", "overtake", "off_road"], "overtake,off_road"),
    ("cars_csv", "", None),
])
def test_coerce(key, value, expected) -> None:
    assert registry.coerce(registry.BY_KEY[key], value) == expected


@pytest.mark.parametrize(("key", "value"), [
    ("ws_rate", 0),
    ("ws_rate", 61),
    ("source", "carrier-pigeon"),
    ("race_engineer_categories", ["pace", "gossip"]),
    ("telemetry_port", "port"),
])
def test_coerce_rejects(key, value) -> None:
    with pytest.raises(registry.SettingError):
        registry.coerce(registry.BY_KEY[key], value)


def test_apply_stored_overrides_and_skips_what_no_longer_validates(caplog) -> None:
    settings = Settings(ws_rate=30)
    stored = {"ws_rate": "12", "sim_scenario": "retired_scenario", "race_engineer": "false",
              "sync_url": "", "http_port": "9999"}
    with caplog.at_level(logging.WARNING):
        registry.apply_stored(settings, stored)
    assert settings.ws_rate == 12
    assert settings.race_engineer is False
    assert settings.sim_scenario == "practice"
    assert "retired_scenario" not in settings.sim_scenario
    assert settings.sync_url == Settings.model_fields["sync_url"].default
    assert settings.http_port == 8000  # environment only, never from the database
    assert "sim_scenario" in caplog.text


# --- GET /api/admin/config ----------------------------------------------------


async def test_config_lists_every_setting_with_its_origin(client) -> None:
    c, _ = client
    body = (await c.get("/api/admin/config")).json()
    rows = by_key(body)
    assert set(rows) == set(Settings.model_fields)
    assert rows["ws_rate"]["value"] == 30 and rows["ws_rate"]["source"] == "env"
    assert rows["ws_rate"]["default"] == 30
    assert rows["packet_format"]["source"] == "default"
    assert rows["http_port"]["editable"] is False
    assert rows["webhook_events"]["value"] == list(registry.BY_KEY["webhook_events"].choices)


async def test_config_never_returns_a_secret(client) -> None:
    c, service = client
    service.settings.sync_token = "gt7s_abcdefghijklmnopqrstuvwxyz"
    rows = by_key((await c.get("/api/admin/config")).json())
    assert rows["sync_token"]["value"] is None
    assert rows["sync_token"]["is_set"] is True
    assert "abcdefgh" not in str(rows["sync_token"])


# --- PUT /api/admin/config ----------------------------------------------------


async def test_live_setting_applies_and_persists(client) -> None:
    c, service = client
    resp = await c.put("/api/admin/config", json={"values": {"ws_rate": 15}})
    assert resp.status_code == 200
    assert by_key(resp.json())["ws_rate"]["source"] == "saved"
    assert service.settings.ws_rate == 15
    assert (await service.repo.get_settings())["ws_rate"] == "15"


async def test_restart_setting_is_stored_but_pending(client) -> None:
    c, service = client
    before = service.settings.cars_json
    resp = await c.put("/api/admin/config", json={"values": {"cars_json": "/tmp/cars.json"}})
    row = by_key(resp.json())["cars_json"]
    assert row["restart_pending"] is True
    assert service.settings.cars_json == before
    assert (await service.repo.get_settings())["cars_json"] == "/tmp/cars.json"
    # Resetting it again leaves nothing pending.
    resp = await c.put("/api/admin/config", json={"reset": ["cars_json"]})
    assert by_key(resp.json())["cars_json"]["restart_pending"] is False


async def test_reset_hands_the_setting_back_to_the_environment(client) -> None:
    c, service = client
    await c.put("/api/admin/config", json={"values": {"ws_rate": 5}})
    resp = await c.put("/api/admin/config", json={"reset": ["ws_rate"]})
    row = by_key(resp.json())["ws_rate"]
    assert row["value"] == 30 and row["source"] == "env"
    assert service.settings.ws_rate == 30
    assert "ws_rate" not in await service.repo.get_settings()


async def test_handled_settings_go_through_their_own_handlers(client) -> None:
    c, service = client
    resp = await c.put("/api/admin/config", json={
        "values": {"race_engineer_units": "imperial", "source": "sim"},
    })
    assert resp.status_code == 200
    assert service.settings.race_engineer_units == "imperial"
    assert type(service.source).__name__ == "SimTelemetrySource"
    legacy = (await c.get("/api/admin/settings")).json()
    assert legacy["race_engineer_units"] == "imperial"
    # Reset goes the same way: the source switches back.
    await c.put("/api/admin/config", json={"reset": ["source"]})
    assert type(service.source).__name__ == "UdpTelemetrySource"
    assert "source" not in await service.repo.get_settings()


async def test_port_change_restarts_the_listener(client) -> None:
    c, service = client
    first = service.source
    await c.put("/api/admin/config", json={"values": {"telemetry_port": 43747}})
    assert service.source is not first
    assert service.settings.telemetry_port == 43747


@pytest.mark.parametrize(("patch", "fragment"), [
    ({"values": {"no_such_thing": 1}}, "unknown setting"),
    ({"values": {"http_port": 9000}}, "environment"),
    ({"values": {"ws_rate": 0}}, "at least"),
    ({"values": {"ws_rate": 20}, "reset": ["ws_rate"]}, "both"),
])
async def test_bad_patches_change_nothing(client, patch, fragment) -> None:
    c, service = client
    resp = await c.put("/api/admin/config", json=patch)
    assert resp.status_code == 400
    assert fragment in resp.json()["detail"]
    assert service.settings.ws_rate == 30
    assert await service.repo.get_settings() == {}


async def test_a_bad_value_rejects_the_whole_patch(client) -> None:
    c, service = client
    resp = await c.put("/api/admin/config", json={"values": {"ws_rate": 20, "source": "x"}})
    assert resp.status_code == 400
    assert service.settings.ws_rate == 30


async def test_cors_origins_apply_live(client) -> None:
    c, _ = client
    origin = {"Origin": "http://phone.local"}
    assert "access-control-allow-origin" not in (await c.get("/api/health", headers=origin)).headers
    await c.put("/api/admin/config", json={"values": {"cors_origins": "http://phone.local"}})
    resp = await c.get("/api/health", headers=origin)
    assert resp.headers["access-control-allow-origin"] == "http://phone.local"


async def test_admin_token_set_from_admin_locks_admin(client) -> None:
    c, _ = client
    await c.put("/api/admin/config", json={"values": {"admin_token": "s3cret-token"}})
    assert (await c.get("/api/admin/config")).status_code == 401
    ok = await c.get("/api/admin/config", headers={"X-API-Key": "s3cret-token"})
    assert ok.status_code == 200
    assert by_key(ok.json())["admin_token"]["is_set"] is True


async def test_a_handled_setting_refused_late_changes_nothing(client) -> None:
    # The sync address is checked by its own handler; it must be refused
    # before the console IP beside it is applied, not after.
    c, service = client
    resp = await c.put("/api/admin/config", json={
        "values": {"ps_ip": "192.168.1.50", "ws_rate": 20, "sync_url": "gt7sync://x/?nope"},
    })
    assert resp.status_code == 400
    assert service.settings.ps_ip == ""
    assert service.settings.ws_rate == 30
    assert await service.repo.get_settings() == {}


async def test_a_port_in_use_is_refused_and_the_listener_restored(client) -> None:
    import socket

    c, service = client
    await service.source.start()
    taken = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    taken.bind(("0.0.0.0", 0))
    try:
        port = taken.getsockname()[1]
        resp = await c.put("/api/admin/config", json={"values": {"telemetry_port": port}})
        assert resp.status_code == 400
        assert "telemetry_port" in resp.json()["detail"]
        assert service.settings.telemetry_port == 43746
        assert service.source.running  # listening on the old port again
        assert "telemetry_port" not in await service.repo.get_settings()
    finally:
        taken.close()


async def test_reset_value_is_the_environments(client) -> None:
    c, _ = client
    rows = by_key((await c.get("/api/admin/config")).json())
    assert rows["ws_rate"]["reset_value"] == 30  # the fixture's environment
    assert rows["packet_format"]["reset_value"] == "C"
    assert "reset_value" not in rows["sync_token"]
