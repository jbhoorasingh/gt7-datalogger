"""Install a newer release in place: the desktop app updating itself.

The update check (app.updates) says which release is newer than this one. On
a desktop install that can replace itself, this module downloads that
release's file for the platform, checks it, and keeps it ready in
`<data>/updates/` until it is installed: by Restart and update (Settings ›
About, the banner, the tray), or when the app is quit. The launcher names the
kind of install in GT7_UPDATE_INSTALL (KINDS). Every other install (Docker,
source, the Windows portable zip, the Linux tarball, a headless server) has
no such kind and stays notify-only.

A downloaded file is installable only when all of this holds, checked when it
arrives and again by the supervisor just before it installs it
(`verify_staged`):

1. `release-manifest.json` carries an Ed25519 signature,
   `release-manifest.json.sig`, by a key in UPDATE_KEYS. The release
   workflow signs with UPDATE_SIGNING_KEY from the `desktop-release`
   environment; nothing else holds that key.
2. The manifest names the offered release, and its version is above the
   running one: an update never goes back.
3. The file's size and sha256 are the ones the manifest gives for it.

On top of that come the platform's own checks, in the desktop package: the
macOS app must carry the running app's Developer ID and be notarized, and a
Windows Setup must carry the running exe's Authenticode signer once Windows
builds are signed.

Installing is never done here. The supervisor is the process being replaced,
and on Windows this server runs in a job object that dies with it. The two
talk through files in the updates folder:

  <tag>/          the manifest, its signature, the file and staged.json
  install.json    {"tag"}: Restart and update was asked for; this server then
                  exits 77 and the supervisor installs that release
  on-quit.json    {"tag"}: install this release when the app is quit; kept
                  while it is ready and update_install_on_quit is on
  failed.json     {"tag", "error"}: the supervisor could not install it
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import os
import re
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from packaging.version import Version

from app.updates import REPO, is_newer, parse
from app.version import version

if TYPE_CHECKING:
    from app.config import Settings
    from app.updates import UpdateChecker

log = logging.getLogger(__name__)

# The public half of the release workflow's UPDATE_SIGNING_KEY, as base64 of
# the raw 32 bytes (desktop/scripts/update_key.py prints it). A new key is
# added here one release before the old one is dropped, so installs that
# have the new key never meet an update signed only with the old one. Empty,
# a build has no way to trust an update and stays notify-only.
UPDATE_KEYS: tuple[str, ...] = ()

DOWNLOAD = f"https://github.com/{REPO}/releases/download"
MANIFEST = "release-manifest.json"
SIGNATURE = MANIFEST + ".sig"

# GT7_UPDATE_INSTALL -> the release file that kind of install takes.
KINDS = {
    "macos-app": "GT7-Datalogger-macOS-arm64.dmg",
    # Installed for every user: Setup asks for administrator approval.
    "windows-setup": "GT7-Datalogger-Windows-x64-Setup.exe",
    # Installed "for me only": Setup needs no approval.
    "windows-setup-user": "GT7-Datalogger-Windows-x64-Setup.exe",
    "appimage": "GT7-Datalogger-Linux-x86_64.AppImage",
}
# The kinds that can install while quitting, with nobody there to answer a
# prompt. A per-machine Windows install always asks, so it installs only
# when Restart and update is clicked.
ON_QUIT_KINDS = frozenset({"macos-app", "windows-setup-user", "appimage"})

STAGED = "staged.json"
INSTALL = "install.json"
ON_QUIT = "on-quit.json"
FAILED = "failed.json"

MAX_MANIFEST = 64 * 1024
MAX_SIGNATURE = 1024
MAX_ASSET = 512 * 1024 * 1024
TIMEOUT_S = 30.0
IDLE_S = 3600.0
# A release tag as the workflow pushes it: safe in a URL and as a folder name.
_TAG = re.compile(r"^v\d+\.\d+\.\d+(-rc\.\d+)?$")


class UpdateError(Exception):
    """Why a release cannot be installed, in words for Settings › About."""


@dataclass(frozen=True)
class Staged:
    tag: str
    version: str
    file: Path


def updates_dir(data_dir: Path) -> Path:
    return data_dir / "updates"


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_json(path: Path, data: dict[str, Any]) -> None:
    """Whole or not at all: the other process may read it at any moment."""
    part = path.with_name(path.name + ".part")
    part.write_text(json.dumps(data), encoding="utf-8")
    os.replace(part, path)


def tag_of(path: Path) -> str:
    """The `tag` a request file names, or "" for none (or a malformed one)."""
    data = read_json(path) or {}
    tag = data.get("tag")
    return tag if isinstance(tag, str) and _TAG.match(tag) else ""


# --- checks -------------------------------------------------------------------


def verify_manifest(data: bytes, signature: bytes, keys: Iterable[str]) -> dict[str, Any]:
    """The manifest, parsed, once a trusted key has signed exactly these bytes."""
    from Crypto.Signature import eddsa

    try:
        sig = base64.b64decode(signature.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise UpdateError("the release's signature is malformed") from exc
    for key in keys:
        try:
            public = eddsa.import_public_key(base64.b64decode(key))
            eddsa.new(public, "rfc8032").verify(data, sig)
        except ValueError:
            continue
        break
    else:
        raise UpdateError("the release is not signed with the GT7 Datalogger release key")
    try:
        manifest = json.loads(data)
    except ValueError as exc:
        raise UpdateError("the release's manifest is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise UpdateError("the release's manifest is not valid JSON")
    return manifest


def asset_entry(manifest: dict[str, Any], tag: str, name: str, current: str) -> tuple[int, str]:
    """The size and sha256 the signed manifest gives `name`, for release `tag`."""
    offered = parse(tag)
    named = parse(str(manifest.get("version")))
    if manifest.get("tag") != tag or offered is None or named != offered:
        raise UpdateError(f"the release's manifest is for {manifest.get('tag')}, not {tag}")
    if not is_newer(tag, current):
        raise UpdateError(f"{tag} is not newer than the running {current}")
    for asset in manifest.get("assets") or []:
        if isinstance(asset, dict) and asset.get("name") == name:
            size, sha = asset.get("size"), asset.get("sha256")
            if (
                isinstance(size, int) and 0 < size <= MAX_ASSET
                and isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha)
            ):
                return size, sha
            break
    raise UpdateError(f"the release's manifest has no usable entry for {name}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_staged(folder: Path, kind: str, current: str, keys: Iterable[str]) -> Staged:
    """Check a staged release again from its own files, as if just downloaded."""
    tag = tag_of(folder / STAGED)
    if not tag or kind not in KINDS:
        raise UpdateError("the downloaded update is incomplete")
    name = KINDS[kind]
    try:
        data = (folder / MANIFEST).read_bytes()
        sig = (folder / SIGNATURE).read_bytes()
    except OSError as exc:
        raise UpdateError("the downloaded update is incomplete") from exc
    size, sha = asset_entry(verify_manifest(data, sig, keys), tag, name, current)
    file = folder / name
    try:
        matches = file.stat().st_size == size and sha256_file(file) == sha
    except OSError:
        matches = False
    if not matches:
        raise UpdateError(f"{name} does not match the release's checksum")
    return Staged(tag=tag, version=tag.removeprefix("v"), file=file)


# --- the download -----------------------------------------------------------------


class SelfUpdater:
    """The download, its checks and its state; one per running service."""

    def __init__(
        self,
        settings: Settings,
        checker: UpdateChecker,
        *,
        keys: Iterable[str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        current: Callable[[], str] = version,
    ) -> None:
        self.settings = settings
        self.checker = checker
        self.kind = settings.update_install if settings.update_install in KINDS else ""
        self.keys = tuple(UPDATE_KEYS if keys is None else keys)
        self.transport = transport
        self.current = current
        # idle, downloading, ready, installing or failed; for self.tag.
        self.state = "idle"
        self.tag = ""
        self.progress = 0.0
        self.error = ""
        self._retry = False
        self._wake = asyncio.Event()

    @property
    def enabled(self) -> bool:
        return bool(self.kind and self.keys)

    @property
    def folder(self) -> Path:
        return updates_dir(self.settings.data_root())

    def _offered(self) -> str:
        """The tag the update check offers right now, or "" for none."""
        offered = self.checker.status()
        result = self.checker.result
        if not offered["available"] or result is None:
            return ""
        tag = result["tag"]
        return tag if _TAG.match(tag) else ""

    def wake(self) -> None:
        """A new answer from the check, or a setting changed: look again."""
        self._retry = True
        self._wake.set()

    def status(self) -> dict[str, Any] | None:
        """The `install` object inside stats.update; null when notify-only."""
        if not self.enabled:
            return None
        offered = self._offered()
        state = self.state if self.tag and self.tag == offered else "idle"
        return {
            "state": state,
            "version": self.tag.removeprefix("v") if state != "idle" else None,
            "progress": round(self.progress, 3) if state == "downloading" else None,
            "error": (self.error or None) if state == "failed" else None,
            "on_quit": self.kind in ON_QUIT_KINDS and self.settings.update_install_on_quit,
        }

    def ready_version(self) -> str | None:
        """For /api/status: the version Restart and update would install."""
        status = self.status()
        return status["version"] if status and status["state"] == "ready" else None

    # --- install requests ----------------------------------------------------

    def request_install(self) -> str:
        """Leave the supervisor its instruction; the caller then exits 77."""
        if self.status() is None or self.state != "ready" or self.tag != self._offered():
            raise UpdateError("no update is ready to install")
        write_json(self.folder / INSTALL, {"tag": self.tag})
        self.state = "installing"
        log.info("installing %s: restarting into it", self.tag)
        return self.tag

    def sync_on_quit(self) -> None:
        """Keep on-quit.json naming the ready release, or remove it."""
        path = self.folder / ON_QUIT
        wanted = (
            self.enabled
            and self.state == "ready"
            and self.tag == self._offered()
            and self.kind in ON_QUIT_KINDS
            and self.settings.update_install_on_quit
        )
        try:
            if wanted:
                write_json(path, {"tag": self.tag})
            else:
                path.unlink(missing_ok=True)
        except OSError:
            log.info("could not record the update to install on quit", exc_info=True)

    # --- the background task -------------------------------------------------

    async def run(self) -> None:
        """Download whatever the check offers, whenever it offers something new."""
        if not self.enabled:
            return
        await asyncio.to_thread(self._load)
        while True:
            self._wake.clear()
            tag = self._offered()
            fresh = tag and tag != self.tag
            if tag and (fresh or self.state == "idle" or (self.state == "failed" and self._retry)):
                self._retry = False
                await self.fetch(tag)
                continue
            self.sync_on_quit()
            try:
                await asyncio.wait_for(self._wake.wait(), IDLE_S)
            except TimeoutError:
                pass

    def _load(self) -> None:
        """Take up what an earlier run staged; drop what is installed or stale."""
        root = self.folder
        (root / INSTALL).unlink(missing_ok=True)
        failed = read_json(root / FAILED) or {}
        valid: list[Staged] = []
        for folder in (p for p in root.glob("v*") if p.is_dir()):
            try:
                valid.append(verify_staged(folder, self.kind, self.current(), self.keys))
            except UpdateError:
                shutil.rmtree(folder, ignore_errors=True)
        valid.sort(key=lambda s: parse(s.tag) or Version("0"))
        for older in valid[:-1]:
            shutil.rmtree(older.file.parent, ignore_errors=True)
        best = valid[-1] if valid else None
        if best is None:
            (root / FAILED).unlink(missing_ok=True)
            return
        self.tag = best.tag
        if failed.get("tag") == best.tag and isinstance(failed.get("error"), str):
            self.state, self.error = "failed", failed["error"]
        else:
            self.state = "ready"
            (root / FAILED).unlink(missing_ok=True)

    async def fetch(self, tag: str) -> None:
        """Download and check `tag`; never raises. Ends ready or failed."""
        name = KINDS[self.kind]
        folder = self.folder / tag
        self.tag, self.state, self.progress, self.error = tag, "downloading", 0.0, ""
        try:
            await self._fetch(tag, name, folder)
        except UpdateError as exc:
            self._fail(str(exc))
        except (httpx.HTTPError, OSError) as exc:
            self._fail(f"the download failed ({exc.__class__.__name__}: {exc})")
        else:
            self.state = "ready"
            log.info("update %s downloaded and checked: ready to install", tag)
            for other in self.folder.glob("v*"):
                if other.is_dir() and other.name != tag:
                    shutil.rmtree(other, ignore_errors=True)
            (self.folder / FAILED).unlink(missing_ok=True)
        self.sync_on_quit()

    def _fail(self, error: str) -> None:
        self.state, self.error = "failed", error
        log.warning("update %s could not be downloaded: %s", self.tag, error)

    async def _fetch(self, tag: str, name: str, folder: Path) -> None:
        current = self.current()
        if (folder / STAGED).exists():
            try:
                await asyncio.to_thread(verify_staged, folder, self.kind, current, self.keys)
                return
            except UpdateError:
                pass
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
        headers = {"User-Agent": f"gt7-datalogger/{current}"}
        async with httpx.AsyncClient(
            timeout=TIMEOUT_S, follow_redirects=True, transport=self.transport, headers=headers
        ) as client:
            data = await self._small(client, f"{DOWNLOAD}/{tag}/{MANIFEST}", MAX_MANIFEST)
            sig = await self._small(client, f"{DOWNLOAD}/{tag}/{SIGNATURE}", MAX_SIGNATURE)
            size, sha = asset_entry(verify_manifest(data, sig, self.keys), tag, name, current)
            await self._file(client, f"{DOWNLOAD}/{tag}/{name}", folder / name, size, sha)
        (folder / MANIFEST).write_bytes(data)
        (folder / SIGNATURE).write_bytes(sig)
        write_json(folder / STAGED, {"tag": tag})

    @staticmethod
    def _https(resp: httpx.Response) -> None:
        resp.raise_for_status()
        if resp.url.scheme != "https":
            raise UpdateError("the download was redirected away from https")

    async def _small(self, client: httpx.AsyncClient, url: str, limit: int) -> bytes:
        resp = await client.get(url)
        self._https(resp)
        if len(resp.content) > limit:
            raise UpdateError(f"{url.rsplit('/', 1)[-1]} is larger than any release's")
        return resp.content

    async def _file(
        self, client: httpx.AsyncClient, url: str, dest: Path, size: int, sha: str
    ) -> None:
        part = dest.with_name(dest.name + ".part")
        digest = hashlib.sha256()
        got = 0
        async with client.stream("GET", url) as resp:
            self._https(resp)
            with open(part, "wb") as fh:
                async for chunk in resp.aiter_bytes(1 << 16):
                    got += len(chunk)
                    if got > size:
                        raise UpdateError(f"{dest.name} is larger than the release says")
                    digest.update(chunk)
                    fh.write(chunk)
                    self.progress = got / size
        if got != size or digest.hexdigest() != sha:
            part.unlink(missing_ok=True)
            raise UpdateError(f"{dest.name} does not match the release's checksum")
        os.replace(part, dest)
