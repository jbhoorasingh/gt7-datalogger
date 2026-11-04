"""The desktop app's in-place update: download, signature, checksum, handover.

Every request goes to an httpx.MockTransport, and every key is made here:
nothing touches the network or the real release key.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from Crypto.PublicKey import ECC
from Crypto.Signature import eddsa
from httpx import ASGITransport, AsyncClient

from app import self_update, serve
from app.config import Settings
from app.main import create_app
from app.processing.cars import CarDatabase
from app.self_update import (
    KINDS,
    SelfUpdater,
    UpdateError,
    asset_entry,
    verify_manifest,
    verify_staged,
)
from app.service import TelemetryService
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository
from app.updates import UpdateChecker

DMG = KINDS["macos-app"]
BODY = b"pretend this is a signed, notarized disk image" * 100


class Key:
    def __init__(self) -> None:
        self.key = ECC.generate(curve="ed25519")
        self.public = base64.b64encode(self.key.public_key().export_key(format="raw")).decode()

    def sign(self, data: bytes) -> bytes:
        return base64.b64encode(eddsa.new(self.key, "rfc8032").sign(data))


def manifest(tag: str = "v1.0.1", body: bytes = BODY, name: str = DMG, **over) -> bytes:
    doc = {
        "version": tag.removeprefix("v"),
        "tag": tag,
        "commit": "0" * 40,
        "assets": [{"name": name, "size": len(body), "sha256": hashlib.sha256(body).hexdigest()}],
        **over,
    }
    return json.dumps(doc, indent=2).encode()


class Releases:
    """github.com/<repo>/releases/download/<tag>/<file>, as told."""

    def __init__(self, key: Key, tag: str = "v1.0.1") -> None:
        self.files: dict[str, bytes] = {}
        self.requests: list[httpx.Request] = []
        self.publish(key, tag)

    def publish(self, key: Key, tag: str, body: bytes = BODY, data: bytes | None = None) -> None:
        data = data if data is not None else manifest(tag, body)
        base = f"/jbhoorasingh/gt7-datalogger/releases/download/{tag}"
        self.files[f"{base}/release-manifest.json"] = data
        self.files[f"{base}/release-manifest.json.sig"] = key.sign(data)
        self.files[f"{base}/{DMG}"] = body

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = self.files.get(request.url.path)
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, content=body)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


class Offer:
    """Stands in for the update check: offers one tag, or nothing."""

    def __init__(self, tag: str = "v1.0.1") -> None:
        self.result: dict[str, str] | None = {"channel": "stable", "tag": tag, "url": "x"}

    def status(self, *, enabled: bool | None = None) -> dict[str, object]:
        return {"available": self.result is not None and bool(self.result["tag"])}


@pytest.fixture
def key() -> Key:
    return Key()


def updater(tmp_path: Path, key: Key, releases: Releases | None = None, *, offer=None,
            kind: str = "macos-app", current: str = "1.0.0", **settings) -> SelfUpdater:
    s = Settings(_env_file=None, db_path=tmp_path / "gt7.db", update_check=True,
                 update_install=kind, **settings)
    return SelfUpdater(
        s, offer or Offer(), keys=[key.public],  # type: ignore[arg-type]
        transport=releases.transport if releases else None, current=lambda: current,
    )


# --- the checks -------------------------------------------------------------------


def test_a_signed_manifest_is_read(key: Key) -> None:
    data = manifest()
    assert verify_manifest(data, key.sign(data), [key.public])["tag"] == "v1.0.1"


def test_a_manifest_signed_with_another_key_is_refused(key: Key) -> None:
    data = manifest()
    with pytest.raises(UpdateError, match="release key"):
        verify_manifest(data, Key().sign(data), [key.public])


def test_a_changed_manifest_is_refused(key: Key) -> None:
    data = manifest()
    signature = key.sign(data)
    with pytest.raises(UpdateError, match="release key"):
        verify_manifest(data.replace(b"1.0.1", b"1.0.2"), signature, [key.public])


def test_any_listed_key_will_do(key: Key) -> None:
    data = manifest()
    assert verify_manifest(data, key.sign(data), [Key().public, key.public])


def test_a_garbled_signature_is_refused(key: Key) -> None:
    with pytest.raises(UpdateError, match="malformed"):
        verify_manifest(manifest(), b"not base64!", [key.public])


def test_no_key_trusts_nothing(key: Key) -> None:
    data = manifest()
    with pytest.raises(UpdateError):
        verify_manifest(data, key.sign(data), [])


def test_the_manifest_must_be_for_the_offered_release() -> None:
    doc = json.loads(manifest("v1.0.2"))
    with pytest.raises(UpdateError, match="not v1.0.1"):
        asset_entry(doc, "v1.0.1", DMG, "1.0.0")


def test_an_update_never_goes_back() -> None:
    for current in ("1.0.1", "1.0.2", "1.1.0-rc.1"):
        with pytest.raises(UpdateError, match="not newer"):
            asset_entry(json.loads(manifest()), "v1.0.1", DMG, current)
    # A release candidate's tester is offered the final.
    assert asset_entry(json.loads(manifest()), "v1.0.1", DMG, "1.0.1-rc.2")


def test_the_platform_file_must_be_listed() -> None:
    with pytest.raises(UpdateError, match="no usable entry"):
        asset_entry(json.loads(manifest()), "v1.0.1", KINDS["appimage"], "1.0.0")
    bad = json.loads(manifest())
    bad["assets"][0]["sha256"] = "nope"
    with pytest.raises(UpdateError, match="no usable entry"):
        asset_entry(bad, "v1.0.1", DMG, "1.0.0")


# --- the download -----------------------------------------------------------------


async def test_a_release_is_downloaded_checked_and_staged(tmp_path: Path, key: Key) -> None:
    gh = Releases(key)
    u = updater(tmp_path, key, gh)
    await u.fetch("v1.0.1")
    assert u.status() == {"state": "ready", "version": "1.0.1", "progress": None,
                          "error": None, "on_quit": True}
    assert u.ready_version() == "1.0.1"
    folder = u.folder / "v1.0.1"
    assert (folder / DMG).read_bytes() == BODY
    assert verify_staged(folder, "macos-app", "1.0.0", [key.public]).file == folder / DMG
    assert json.loads((u.folder / "on-quit.json").read_text()) == {"tag": "v1.0.1"}
    assert {r.url.host for r in gh.requests} == {"github.com"}
    # Asked again, nothing is downloaded twice.
    count = len(gh.requests)
    await u.fetch("v1.0.1")
    assert len(gh.requests) == count and u.state == "ready"


async def test_a_file_that_does_not_match_the_manifest_is_dropped(tmp_path: Path, key: Key) -> None:
    gh = Releases(key)
    gh.files[next(p for p in gh.files if p.endswith(DMG))] = BODY[:-1] + b"!"
    u = updater(tmp_path, key, gh)
    await u.fetch("v1.0.1")
    status = u.status()
    assert status is not None and status["state"] == "failed"
    assert "checksum" in status["error"]
    assert not (u.folder / "v1.0.1" / DMG).exists()
    assert not (u.folder / "on-quit.json").exists()


async def test_a_release_signed_with_another_key_is_never_downloaded(
    tmp_path: Path, key: Key
) -> None:
    gh = Releases(Key())
    u = updater(tmp_path, key, gh)
    await u.fetch("v1.0.1")
    assert u.state == "failed" and "release key" in u.error
    assert not any(r.url.path.endswith(DMG) for r in gh.requests)


async def test_a_redirect_to_plain_http_is_refused(tmp_path: Path, key: Key) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.scheme == "https":
            return httpx.Response(302, headers={"location": f"http://evil.test{request.url.path}"})
        return httpx.Response(200, content=b"{}")

    u = updater(tmp_path, key)
    u.transport = httpx.MockTransport(handler)
    await u.fetch("v1.0.1")
    assert u.state == "failed" and "https" in u.error


async def test_an_offline_download_fails_quietly(tmp_path: Path, key: Key) -> None:
    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    u = updater(tmp_path, key)
    u.transport = httpx.MockTransport(offline)
    await u.fetch("v1.0.1")
    assert u.state == "failed" and "ConnectError" in u.error


def test_notify_only_installs_have_no_install_status(tmp_path: Path, key: Key) -> None:
    assert updater(tmp_path, key, kind="").status() is None
    assert updater(tmp_path, key, kind="docker").status() is None
    u = updater(tmp_path, key)
    u.keys = ()
    assert u.status() is None


async def test_a_stale_staged_release_is_not_offered(tmp_path: Path, key: Key) -> None:
    offer = Offer()
    u = updater(tmp_path, key, Releases(key), offer=offer)
    await u.fetch("v1.0.1")
    offer.result = {"channel": "stable", "tag": "v1.0.2", "url": "x"}
    status = u.status()
    assert status is not None and status["state"] == "idle" and status["version"] is None
    u.sync_on_quit()
    assert not (u.folder / "on-quit.json").exists()


async def test_install_on_quit_follows_the_setting_and_the_kind(tmp_path: Path, key: Key) -> None:
    u = updater(tmp_path, key, Releases(key), update_install_on_quit=False)
    await u.fetch("v1.0.1")
    assert not (u.folder / "on-quit.json").exists()
    u.settings.update_install_on_quit = True
    u.sync_on_quit()
    assert (u.folder / "on-quit.json").exists()
    # A Windows install for every user asks for approval: never on quit.
    w = updater(tmp_path / "w", key, kind="windows-setup")
    w.state, w.tag = "ready", "v1.0.1"
    status = w.status()
    assert status is not None and status["on_quit"] is False
    w.sync_on_quit()
    assert not (w.folder / "on-quit.json").exists()


async def test_restart_and_update_leaves_the_supervisor_its_instruction(
    tmp_path: Path, key: Key
) -> None:
    u = updater(tmp_path, key, Releases(key))
    with pytest.raises(UpdateError):
        u.request_install()
    await u.fetch("v1.0.1")
    assert u.request_install() == "v1.0.1"
    assert json.loads((u.folder / "install.json").read_text()) == {"tag": "v1.0.1"}
    assert u.status()["state"] == "installing"  # type: ignore[index]


async def test_a_changed_staged_file_fails_the_second_check(tmp_path: Path, key: Key) -> None:
    u = updater(tmp_path, key, Releases(key))
    await u.fetch("v1.0.1")
    folder = u.folder / "v1.0.1"
    (folder / DMG).write_bytes(BODY + b"x")
    with pytest.raises(UpdateError, match="checksum"):
        verify_staged(folder, "macos-app", "1.0.0", [key.public])
    # Installed meanwhile: the staged release is no longer newer.
    (folder / DMG).write_bytes(BODY)
    with pytest.raises(UpdateError, match="not newer"):
        verify_staged(folder, "macos-app", "1.0.1", [key.public])


async def test_a_restart_takes_up_what_was_staged(tmp_path: Path, key: Key) -> None:
    gh = Releases(key)
    gh.publish(key, "v1.0.2", body=BODY + b"2")
    first = updater(tmp_path, key, gh)
    await first.fetch("v1.0.1")
    # fetch drops other releases' folders; stage the second one by hand.
    second = updater(tmp_path, key, gh)
    await second._fetch("v1.0.2", DMG, second.folder / "v1.0.2")
    (first.folder / "install.json").write_text('{"tag": "v1.0.1"}')

    again = updater(tmp_path, key, offer=Offer("v1.0.2"))
    again._load()
    assert again.state == "ready" and again.tag == "v1.0.2"
    assert not (again.folder / "v1.0.1").exists()
    assert not (again.folder / "install.json").exists()

    # The supervisor said the install failed: shown, not retried at once.
    self_update.write_json(again.folder / "failed.json", {"tag": "v1.0.2", "error": "boom"})
    failed = updater(tmp_path, key, offer=Offer("v1.0.2"))
    failed._load()
    assert failed.status()["state"] == "failed"  # type: ignore[index]
    assert failed.status()["error"] == "boom"  # type: ignore[index]

    # Once that version runs, what was staged for it goes.
    after = updater(tmp_path, key, current="1.0.2", offer=Offer("v1.0.2"))
    after._load()
    assert after.state == "idle" and not any(after.folder.glob("v*"))
    assert not (after.folder / "failed.json").exists()


async def test_the_check_wakes_the_download(tmp_path: Path, key: Key) -> None:
    s = Settings(_env_file=None, db_path=tmp_path / "gt7.db", update_check=True,
                 update_install="macos-app")

    class Repo:
        async def set_setting(self, key: str, value: str) -> None:
            pass

        async def delete_setting(self, key: str) -> None:
            pass

    def github(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"tag_name": "v1.0.1", "html_url": "x"})

    checker = UpdateChecker(s, Repo(), transport=httpx.MockTransport(github),  # type: ignore[arg-type]
                            current=lambda: "1.0.0")
    u = SelfUpdater(s, checker, keys=[key.public], current=lambda: "1.0.0")
    checker.on_answer = u.wake
    await checker.check()
    assert u._wake.is_set() and u._offered() == "v1.0.1"


# --- the API ------------------------------------------------------------------------


@pytest.fixture
async def api(tmp_path: Path, key: Key, monkeypatch: pytest.MonkeyPatch):
    made = []
    calls: list[int] = []
    monkeypatch.setattr(serve, "QUIT", lambda: None)
    monkeypatch.setattr(serve, "UPDATE", lambda: calls.append(1))

    async def make(*, peer: str = "127.0.0.1", kind: str = "macos-app"):
        settings = Settings(
            _env_file=None, source="sim", db_path=tmp_path / "test.db", desktop=True,
            mdns=False, update_check=True, update_install=kind,
        )
        engine = make_engine(settings.db_path)
        await init_db(engine)
        service = TelemetryService(settings, Repository(make_session_factory(engine)),
                                   CarDatabase())
        service.self_update.keys = (key.public,)
        service.self_update.current = lambda: "1.0.0"
        service.self_update.transport = Releases(key).transport
        service.updates.current = lambda: "1.0.0"
        service.updates.result = {"channel": "stable", "tag": "v1.0.1", "url": "x"}
        app = create_app()
        app.router.lifespan_context = None  # type: ignore[assignment]
        app.state.service = service
        client = AsyncClient(transport=ASGITransport(app=app, client=(peer, 50000)),
                             base_url="http://127.0.0.1:8000")
        made.append((client, service, engine))
        return client, service

    yield make, calls
    for client, service, engine in made:
        await client.aclose()
        await service.stop()
        await engine.dispose()


async def test_stats_and_status_carry_the_install_state(api) -> None:
    make, _ = api
    c, service = await make()
    update = (await c.get("/api/admin/stats")).json()["update"]
    assert update["available"] is True and update["install"]["state"] == "idle"
    await service.self_update.fetch("v1.0.1")
    update = (await c.get("/api/admin/stats")).json()["update"]
    assert update["install"] == {"state": "ready", "version": "1.0.1", "progress": None,
                                 "error": None, "on_quit": True}
    assert (await c.get("/api/status")).json()["update_ready"] == "1.0.1"


async def test_restart_and_update_from_this_computer(api) -> None:
    make, calls = api
    c, service = await make()
    assert (await c.post("/api/admin/update-install")).status_code == 409  # nothing ready
    await service.self_update.fetch("v1.0.1")
    resp = await c.post("/api/admin/update-install")
    assert resp.status_code == 202 and resp.json()["install"]["state"] == "installing"
    await asyncio.sleep(0.3)
    assert calls == [1]


async def test_restart_and_update_is_refused_from_a_tablet(api) -> None:
    make, calls = api
    c, service = await make(peer="192.168.1.20")
    await service.self_update.fetch("v1.0.1")
    assert (await c.post("/api/admin/update-install")).status_code == 403
    assert calls == []


async def test_restart_and_update_waits_for_the_session(api, monkeypatch) -> None:
    make, calls = api
    c, service = await make()
    await service.self_update.fetch("v1.0.1")
    monkeypatch.setattr(service, "recording_now", lambda: "a session is recording")
    resp = await c.post("/api/admin/update-install")
    assert resp.status_code == 409 and "finish the session" in resp.json()["detail"]
    assert calls == [] and service.self_update.state == "ready"


async def test_try_again_is_only_for_installs_that_update_themselves(api) -> None:
    make, _ = api
    c, _ = await make(kind="")
    assert (await c.post("/api/admin/update-download")).status_code == 404
    c2, service = await make()
    assert (await c2.post("/api/admin/update-download")).status_code == 202
    assert service.self_update._retry is True


async def test_a_new_download_disarms_the_old_install_on_quit(tmp_path: Path, key: Key) -> None:
    gh = Releases(key)
    gh.publish(key, "v1.0.2", body=BODY + b"2")
    offer = Offer()
    u = updater(tmp_path, key, gh, offer=offer)
    await u.fetch("v1.0.1")
    assert (u.folder / "on-quit.json").exists()
    offer.result = {"channel": "stable", "tag": "v1.0.2", "url": "x"}
    seen: list[bool] = []
    real = u._fetch

    async def watch(tag: str, name: str, folder: Path) -> None:
        seen.append((u.folder / "on-quit.json").exists())  # mid-download
        await real(tag, name, folder)

    u._fetch = watch  # type: ignore[method-assign]
    await u.fetch("v1.0.2")
    assert seen == [False]
    assert json.loads((u.folder / "on-quit.json").read_text()) == {"tag": "v1.0.2"}


async def test_an_oversized_manifest_is_not_read_to_the_end(tmp_path: Path, key: Key) -> None:
    gh = Releases(key)
    path = next(p for p in gh.files if p.endswith("release-manifest.json"))
    gh.files[path] = b" " * (self_update.MAX_MANIFEST * 4)
    u = updater(tmp_path, key, gh)
    await u.fetch("v1.0.1")
    assert u.state == "failed" and "larger than any release" in u.error


async def test_a_failure_recorded_after_the_server_started_is_shown(
    tmp_path: Path, key: Key
) -> None:
    u = updater(tmp_path, key, Releases(key))
    await u.fetch("v1.0.1")
    # Windows: Setup ran (or its prompt was refused) after this server was back.
    (u.folder / "failed.json").write_text(
        '﻿{"tag": "v1.0.1", "error": "the installer stopped before it finished (exit 5)"}',
        encoding="utf-8",
    )
    status = u.status()
    assert status is not None and status["state"] == "failed"
    assert status["error"] == "the installer stopped before it finished (exit 5)"
    assert not (u.folder / "on-quit.json").exists()
