"""Is there a newer release? A notify-only check against GitHub Releases (#196).

Nothing is downloaded and nothing is installed: the answer is a version and a
link, shown by the Settings banner and the desktop tray. The check is off
unless `update_check` is on (the desktop launcher turns it on; Docker and
source installs opt in from Settings), runs at most once a day, and on any
failure says nothing and tries again the next day.

`update_channel` picks the question asked:

  stable      GET /releases/latest, which GitHub defines as the most recent
              non-prerelease, non-draft release, so a stable build is never
              offered an rc.
  prerelease  GET /releases?per_page=20 and the highest non-draft tag, so an
              rc tester on rc.1 is told about rc.2 (and about the final).

Tags are ordered by full precedence (`0.9.0-rc.1` < `0.9.0-rc.2` < `0.9.0`),
which a plain X.Y.Z tuple cannot do, and only a version above the running
one is ever offered. Whether a stored answer is newer is decided when it is
read, against the running version, so the banner goes away by itself once
the update is installed.

The day's cadence is what keeps this under GitHub's 60 unauthenticated
requests an hour per address; the ETag saves the body but not the quota
(a 304 is only exempt with an Authorization header). On a 403 or 429 the
check waits for `x-ratelimit-reset` (or `retry-after`) before asking again.

What it found, when it last succeeded, the ETag and when the next check is
due are settings rows, so a restart neither re-checks nor forgets the answer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx
from packaging.version import InvalidVersion, Version

from app.version import version

if TYPE_CHECKING:  # annotations only: the checker is built before the repo is used
    from app.config import Settings
    from app.storage.repository import Repository

log = logging.getLogger(__name__)

REPO = "jbhoorasingh/gt7-datalogger"
API = f"https://api.github.com/repos/{REPO}"
LATEST_URL = f"{API}/releases/latest"
LIST_URL = f"{API}/releases?per_page=20"
CHANNELS = ("stable", "prerelease")

# Settings-table keys; the same persisted key-value pattern as the car refresh.
CHECKED_AT_KEY = "update_checked_at"  # ISO time of the last check that got an answer
RESULT_KEY = "update_result"          # JSON {channel, tag, url} of that answer ("" = none)
ETAG_KEY = "update_etag"              # the ETag that answer came with
NEXT_AT_KEY = "update_next_at"        # ISO time the next scheduled check is due
KEYS = (CHECKED_AT_KEY, RESULT_KEY, ETAG_KEY, NEXT_AT_KEY)

INTERVAL = timedelta(days=1)
# How long the loop sleeps between looks at the clock when nothing is due,
# so a clock that jumps or a missed wake-up costs at most this.
IDLE_S = 3600.0
TIMEOUT_S = 15.0

_PRE = re.compile(r"-(a|b|rc|alpha|beta)\.?(\d+)", re.IGNORECASE)


def parse(tag: str) -> Version | None:
    """A release tag or build version as a comparable Version, or None.

    `v0.9.0-rc.2` (the tag and build spelling) becomes `0.9.0rc2`, and a
    `+g1a2b3c4` local part (a build off a tag) is dropped: it says which
    commit, not which release, and must not make a build look newer than
    the release it was cut from.
    """
    text = tag.strip().removeprefix("v").removeprefix("V").split("+", 1)[0]
    text = _PRE.sub(lambda m: f"{m.group(1)}{m.group(2)}", text)
    try:
        return Version(text)
    except InvalidVersion:
        return None


def is_newer(tag: str, current: str) -> bool:
    """Whether `tag` is a higher release than `current`; never on doubt."""
    offered, running = parse(tag), parse(current)
    return offered is not None and running is not None and offered > running


def _pick(channel: str, body: Any) -> dict[str, str] | None:
    """The release a 200 answer names, as {tag, url}, or None for none."""
    if channel == "stable":
        releases = [body] if isinstance(body, dict) else []
    else:
        releases = body if isinstance(body, list) else []
    best: tuple[Version, dict[str, str]] | None = None
    for release in releases:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        tag = release.get("tag_name")
        if not isinstance(tag, str):
            continue
        parsed = parse(tag)
        if parsed is None:
            continue
        if best is None or parsed > best[0]:
            best = (parsed, {"tag": tag, "url": str(release.get("html_url") or "")})
    return best[1] if best else None


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _iso(when: datetime | None) -> str | None:
    return when.isoformat(timespec="seconds") if when else None


class UpdateChecker:
    """The check, its schedule and its last answer; one per running service."""

    def __init__(
        self,
        settings: Settings,
        repo: Repository,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        now: Callable[[], datetime] | None = None,
        current: Callable[[], str] = version,
    ) -> None:
        self.settings = settings
        self.repo = repo
        # Tests hand in an httpx.MockTransport; None is the real network.
        self.transport = transport
        self.now = now or (lambda: datetime.now(UTC))
        self.current = current
        self.checked_at: datetime | None = None
        self.result: dict[str, str] | None = None  # {channel, tag, url}
        self.etag = ""
        self.next_at: datetime | None = None
        # Until when GitHub asked us to stop (403/429); a manual check waits too.
        self.blocked_until: datetime | None = None
        self._wake = asyncio.Event()
        self._lock = asyncio.Lock()

    # --- state ---------------------------------------------------------------

    def load(self, stored: dict[str, str]) -> None:
        """Take up what an earlier run recorded (startup, and after a restore)."""
        self.checked_at = _parse_time(stored.get(CHECKED_AT_KEY))
        self.next_at = _parse_time(stored.get(NEXT_AT_KEY))
        self.etag = stored.get(ETAG_KEY, "")
        self.result = None
        try:
            result = json.loads(stored.get(RESULT_KEY) or "null")
        except ValueError:
            result = None
        if isinstance(result, dict) and all(isinstance(result.get(k), str) for k in
                                            ("channel", "tag", "url")):
            self.result = {k: result[k] for k in ("channel", "tag", "url")}
        self.wake()

    def status(self, *, enabled: bool | None = None) -> dict[str, Any]:
        """The `update` object of /api/admin/stats.

        `available` only for a release above the running version on the
        channel now chosen; `version` and `url` are null otherwise. With the
        check off, nothing is offered whatever was found before.
        """
        on = self.settings.update_check if enabled is None else enabled
        result = self.result
        available = bool(
            on
            and result is not None
            and result["channel"] == self.settings.update_channel
            and is_newer(result["tag"], self.current())
        )
        return {
            "available": available,
            "version": result["tag"].removeprefix("v") if available and result else None,
            "url": (result["url"] or None) if available and result else None,
            "checked_at": _iso(self.checked_at) if on else None,
        }

    def due(self) -> bool:
        """Whether a scheduled check should run now."""
        if self._blocked():
            return False
        if self.result is not None and self.result["channel"] != self.settings.update_channel:
            # A channel change asks a different question; the old answer
            # says nothing about it.
            return True
        return self.next_at is None or self.now() >= self.next_at

    def _blocked(self) -> bool:
        return self.blocked_until is not None and self.now() < self.blocked_until

    def wake(self) -> None:
        """Re-read the settings now: called when update_check or the channel changes."""
        self._wake.set()

    # --- the check -----------------------------------------------------------

    async def check(self) -> dict[str, Any]:
        """Ask GitHub now and return the status. Never raises."""
        async with self._lock:
            if self._blocked():
                return self.status(enabled=True)
            try:
                await self._check()
            except Exception as exc:  # noqa: BLE001 - a failed check is silent
                log.info("update check failed (%s); trying again tomorrow", exc)
                self.next_at = self.now() + INTERVAL
            await self._save()
            return self.status(enabled=True)

    async def _check(self) -> None:
        channel = self.settings.update_channel
        if channel not in CHANNELS:
            channel = "stable"
        url = LATEST_URL if channel == "stable" else LIST_URL
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": f"gt7-datalogger/{self.current()}",
        }
        same_question = self.result is not None and self.result["channel"] == channel
        if self.etag and same_question:
            headers["If-None-Match"] = self.etag
        async with httpx.AsyncClient(
            timeout=TIMEOUT_S, follow_redirects=True, transport=self.transport
        ) as client:
            resp = await client.get(url, headers=headers)
        now = self.now()
        if resp.status_code in (403, 429):
            until = self._reset_time(resp, now)
            self.blocked_until = until
            self.next_at = until
            log.info("update check rate limited; next try after %s", _iso(until))
            return
        if resp.status_code == 304 and same_question:
            self.checked_at = now
            self.next_at = now + INTERVAL
            return
        if resp.status_code == 404 and channel == "stable":
            # No stable release yet: an answer, and nothing to offer.
            self.result = {"channel": channel, "tag": "", "url": ""}
            self.etag = ""
            self.checked_at = now
            self.next_at = now + INTERVAL
            return
        resp.raise_for_status()
        picked = _pick(channel, resp.json())
        # "Nothing to offer" is an answer too, kept with the channel it is for.
        self.result = {"channel": channel, **(picked or {"tag": "", "url": ""})}
        self.etag = resp.headers.get("etag", "")
        self.checked_at = now
        self.next_at = now + INTERVAL
        self.blocked_until = None
        if picked and is_newer(picked["tag"], self.current()):
            log.info("update available: %s (%s)", picked["tag"], picked["url"])

    @staticmethod
    def _reset_time(resp: httpx.Response, now: datetime) -> datetime:
        """When GitHub says asking again is allowed; a day when it does not say."""
        reset = resp.headers.get("x-ratelimit-reset")
        if reset:
            try:
                return max(datetime.fromtimestamp(int(reset), UTC), now)
            except (ValueError, OverflowError, OSError):
                pass
        retry = resp.headers.get("retry-after")
        if retry:
            try:
                return now + timedelta(seconds=max(int(retry), 0))
            except ValueError:
                pass
        return now + INTERVAL

    async def _save(self) -> None:
        values = {
            CHECKED_AT_KEY: _iso(self.checked_at),
            RESULT_KEY: json.dumps(self.result) if self.result else None,
            ETAG_KEY: self.etag or None,
            NEXT_AT_KEY: _iso(self.next_at),
        }
        try:
            for key, value in values.items():
                if value is None:
                    await self.repo.delete_setting(key)
                else:
                    await self.repo.set_setting(key, value)
        except Exception:  # noqa: BLE001 - losing the bookkeeping costs one extra check
            log.info("update check result could not be saved", exc_info=True)

    # --- the schedule --------------------------------------------------------

    async def run(self) -> None:
        """The background task: check whenever due while update_check is on.

        Woken by `wake()` when the setting changes, so turning it on in
        Settings checks straight away (if due) without a restart.
        """
        while True:
            self._wake.clear()
            if self.settings.update_check and self.due():
                await self.check()
                continue
            delay = IDLE_S
            if self.settings.update_check and self.next_at is not None:
                delay = min(max((self.next_at - self.now()).total_seconds(), 1.0), IDLE_S)
            try:
                await asyncio.wait_for(self._wake.wait(), delay)
            except TimeoutError:
                pass
