"""The Host and Origin checks in front of the whole app (#183), and the
other things a desktop browser needs from the server: the bundle's MIME
type."""

from __future__ import annotations

import mimetypes
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.staticfiles import StaticFiles

from app.api import guard
from app.config import Settings
from app.main import create_app
from app.processing.cars import CarDatabase
from app.service import TelemetryService
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository


@pytest.fixture
async def client(tmp_path):
    settings = Settings(
        source="udp", db_path=tmp_path / "test.db", ws_rate=1000, telemetry_port=43741
    )
    engine = make_engine(settings.db_path)
    await init_db(engine)
    service = TelemetryService(settings, Repository(make_session_factory(engine)), CarDatabase())
    app = create_app()
    app.router.lifespan_context = None  # type: ignore[assignment]
    app.state.service = service
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000"
    ) as c:
        yield c, service
    await service.stop()
    await engine.dispose()


# A POST that changes something and needs no body: what a hostile page would send.
WRITE = "/api/admin/config"
BODY = {"values": {"ws_rate": 20}}


# --- Origin -----------------------------------------------------------------


async def test_a_cross_site_post_is_refused(client) -> None:
    c, service = client
    resp = await c.put(WRITE, json=BODY, headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403
    assert resp.json()["reason"] == "cross_site"
    assert "GT7_CORS_ORIGINS" in resp.json()["detail"]
    assert service.settings.ws_rate == 1000


async def test_sec_fetch_site_cross_site_is_refused_without_an_origin(client) -> None:
    c, _ = client
    resp = await c.put(WRITE, json=BODY, headers={"Sec-Fetch-Site": "cross-site"})
    assert resp.status_code == 403
    assert resp.json()["reason"] == "cross_site"


async def test_a_same_origin_post_passes(client) -> None:
    c, service = client
    headers = {"Origin": "http://127.0.0.1:8000", "Sec-Fetch-Site": "same-origin"}
    resp = await c.put(WRITE, json=BODY, headers=headers)
    assert resp.status_code == 200
    assert service.settings.ws_rate == 20


async def test_a_post_without_an_origin_passes(client) -> None:
    # The phone app, curl, scripts: no Origin, no Sec-Fetch-Site.
    c, service = client
    assert (await c.put(WRITE, json=BODY)).status_code == 200
    assert service.settings.ws_rate == 20


async def test_another_port_on_the_same_host_is_another_origin(client) -> None:
    c, _ = client
    resp = await c.put(WRITE, json=BODY, headers={"Origin": "http://127.0.0.1:3000"})
    assert resp.status_code == 403


async def test_a_null_origin_is_refused(client) -> None:
    c, _ = client
    assert (await c.put(WRITE, json=BODY, headers={"Origin": "null"})).status_code == 403


async def test_a_cross_site_get_is_not_an_origin_matter(client) -> None:
    c, _ = client
    resp = await c.get("/api/health", headers={"Origin": "https://evil.example"})
    assert resp.status_code == 200


async def test_a_configured_cors_origin_may_write(client) -> None:
    c, service = client
    service.settings.cors_origins = "https://app.example.com"
    headers = {"Origin": "https://app.example.com", "Sec-Fetch-Site": "cross-site"}
    assert (await c.put(WRITE, json=BODY, headers=headers)).status_code == 200


async def test_a_trusted_host_is_the_servers_own_origin(client) -> None:
    # nginx rewrites Host to the upstream's by default: the page's origin is
    # the proxy's public name, which the user listed.
    c, service = client
    service.settings.trusted_hosts = "gt7.example.com"
    headers = {"Origin": "https://gt7.example.com", "Sec-Fetch-Site": "same-origin"}
    assert (await c.put(WRITE, json=BODY, headers=headers)).status_code == 200


# --- Host -------------------------------------------------------------------


async def test_an_untrusted_host_is_refused_and_names_the_setting(client) -> None:
    c, _ = client
    resp = await c.get("/api/health", headers={"Host": "rebind.evil.example"})
    assert resp.status_code == 403
    body = resp.json()
    assert body["reason"] == "untrusted_host"
    assert "GT7_TRUSTED_HOSTS" in body["detail"]


async def test_a_listed_host_passes_live(client) -> None:
    c, service = client
    headers = {"Host": "gt7.example.com"}
    assert (await c.get("/api/health", headers=headers)).status_code == 403
    service.settings.trusted_hosts = "other.example, *.example.com"
    assert (await c.get("/api/health", headers=headers)).status_code == 200


async def test_a_star_turns_the_host_check_off(client) -> None:
    c, service = client
    service.settings.trusted_hosts = "*"
    resp = await c.get("/api/health", headers={"Host": "anything.example.org"})
    assert resp.status_code == 200


async def test_the_setting_is_registered_live(client) -> None:
    c, service = client
    resp = await c.put("/api/admin/config", json={"values": {"trusted_hosts": "*"}})
    assert resp.status_code == 200
    assert service.settings.trusted_hosts == "*"


@pytest.mark.parametrize("host", [
    "127.0.0.1", "127.0.0.1:8000", "[::1]:8000", "::1", "192.168.1.20:8000",
    "localhost", "localhost:5173", "gt7", "test", "testserver", "pi.local", "nas.lan",
    "box.home.arpa", "host.internal", "pc.localdomain", "app.localhost",
    "gt.traefik.test", "PI.LOCAL.", "",
])
def test_lan_names_are_trusted(host: str) -> None:
    assert guard.host_is_trusted(guard.split_host(host)[0], Settings())


@pytest.mark.parametrize("host", ["evil.example", "rebind.attacker.com", "1.2.3.4.nip.io"])
def test_public_names_are_not(host: str) -> None:
    assert not guard.host_is_trusted(guard.split_host(host)[0], Settings())


def test_this_machines_hostname_is_trusted(monkeypatch) -> None:
    monkeypatch.setattr(guard.socket, "gethostname", lambda: "Racing-Rig.example.net")
    assert guard.host_is_trusted("racing-rig.example.net", Settings())
    monkeypatch.setattr(guard.socket, "gethostname", lambda: "studio.corp.example")
    assert guard.host_is_trusted("studio.corp.example.local", Settings())


# --- WebSocket handshake ----------------------------------------------------


async def _handshake(headers: list[tuple[bytes, bytes]]) -> dict[str, Any]:
    """The first thing the whole app sends back to an upgrade to /ws/live."""
    app = create_app()
    app.router.lifespan_context = None  # type: ignore[assignment]
    app.state.service = SimpleNamespace(settings=Settings())
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "websocket.connect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "websocket", "path": "/ws/live", "raw_path": b"/ws/live",
        "query_string": b"", "headers": headers, "scheme": "ws",
        "server": ("127.0.0.1", 8000), "client": ("127.0.0.1", 50000),
        "root_path": "", "subprotocols": [], "asgi": {"version": "3.0"},
    }
    await app(scope, receive, send)  # type: ignore[arg-type]
    return sent[0]


async def test_a_websocket_from_a_foreign_origin_is_refused() -> None:
    first = await _handshake([(b"host", b"127.0.0.1:8000"), (b"origin", b"https://evil.example")])
    # Closed before it was accepted: the server answers the upgrade with 403.
    assert first == {"type": "websocket.close", "code": 1008, "reason": "cross_site"}


async def test_a_websocket_to_an_untrusted_host_is_refused() -> None:
    first = await _handshake([(b"host", b"rebind.evil.example")])
    assert first["type"] == "websocket.close"
    assert first["reason"] == "untrusted_host"


async def test_a_same_origin_or_native_websocket_reaches_the_app() -> None:
    reached: list[str] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        reached.append(scope["path"])

    async def never_called(message: Any) -> None:
        raise AssertionError(f"guard sent {message}")

    wrapped = guard.RequestGuard(inner)
    for headers in ([(b"host", b"pi.local:8000"), (b"origin", b"http://pi.local:8000")],
                    [(b"host", b"pi.local:8000")]):
        scope = {"type": "websocket", "path": "/ws/live", "headers": headers}
        await wrapped(scope, None, never_called)  # type: ignore[arg-type]
    assert reached == ["/ws/live", "/ws/live"]


# --- MIME types -------------------------------------------------------------


async def test_the_bundle_is_served_as_javascript(tmp_path) -> None:
    import app.main  # noqa: F401  (registers the types)

    (tmp_path / "index-abc.js").write_text("export {};\n")
    (tmp_path / "chunk.mjs").write_text("export {};\n")
    (tmp_path / "style.css").write_text("body{}\n")
    (tmp_path / "logo.svg").write_text("<svg/>\n")
    static = Starlette()
    static.mount("/", StaticFiles(directory=tmp_path))
    async with AsyncClient(transport=ASGITransport(app=static), base_url="http://t") as c:
        types = {
            name: (await c.get(f"/{name}")).headers["content-type"].split(";")[0]
            for name in ("index-abc.js", "chunk.mjs", "style.css", "logo.svg")
        }
    assert types == {
        "index-abc.js": "text/javascript",
        "chunk.mjs": "text/javascript",
        "style.css": "text/css",
        "logo.svg": "image/svg+xml",
    }
    assert mimetypes.guess_type("x.js")[0] == "text/javascript"
