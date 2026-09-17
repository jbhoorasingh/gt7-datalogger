"""The `tracks` adapter: a circuit's survey bundle, pushed as it grows.

What gets sent is the bundle document exactly as export writes it — the
same v4 format the shared repo serves and import reads — and only for a
circuit whose official layout a human has confirmed, because the merge job
files uploads by `official_id` and an unconfirmed layout cannot be filed.
Nothing else leaves: not `source-id.json`, not settings, not laps.

When: once a bundle has been left alone for `SETTLE_S`. Every write to a
bundle — the survey's once-a-minute autosave, the save when it stops, an
import, a pull, a rename, a layout confirmation, a corner edit — calls
`changed(track)`, and each call restarts that clock. So a running survey
never uploads mid-run: the clock keeps moving until the run has stopped
and whatever follows it (naming corners, confirming the layout) is done,
and one upload carries all of it. The adapter does its work on its own
task and never makes the caller wait; "Sync now" and the sweep on enabling
skip the wait.

How much: at most one upload per bundle per `MIN_INTERVAL_S` (the server
rate-limits at the same cadence), and none at all when the document has not
changed since the last one the server accepted. "Changed" ignores
`meta.updated_at`, which every save rewrites; what counts is the evidence
and the authored data — the corner labels and sections travel inside the
document like everything else.

What comes back: the server answers `202 {upload_id, status}` and the
adapter remembers that per track, in `data/sync-state.json`, so a restart
does not re-send what it already sent. A document the server refuses
(`4xx` with a reason, or a `409` for a source id bound to someone else's
account) is remembered as *rejected* with that reason and is not retried
until the bundle changes; a server that is unreachable or failing is
retried with exponential backoff.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.processing import track_bundle
from app.sync.transport import SyncError

if TYPE_CHECKING:
    from app.sync.client import SyncClient

log = logging.getLogger(__name__)

TYPE = "tracks"
SCOPE = "tracks:write"
UPLOAD_PATH = "/v1/tracks/uploads"
STATE_FILE = "sync-state.json"
STATE_FORMAT = "gt7-datalogger-sync-state"
STATE_VERSION = 1
# A bundle is uploaded once it has gone this long without a change. The
# survey autosaves every minute and each save is a change, so the clock only
# runs out after a run has stopped — and after the labelling that usually
# follows one. Ten minutes covers that without holding an upload till the
# next day; Sync now is there for anyone who will not wait.
SETTLE_S = 10 * 60.0
# The floor under it: one upload per bundle per minute, which is what the
# server rate-limits at, so sending faster would only earn a 429.
MIN_INTERVAL_S = 60.0
BACKOFF_BASE_S = 30.0
BACKOFF_MAX_S = 15 * 60.0
# The server's cap on an upload; a bundle at the format's 50,000-point limit
# is a few MB, so this is head-room, not a boundary anyone should meet.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024

# Statuses that mean "the server has had its say on this exact document":
# the only ones worth remembering across restarts.
SETTLED = ("synced", "rejected")


@dataclass(slots=True)
class TrackSync:
    """What the adapter knows about one bundle's standing with the server."""

    slug: str
    track: str = ""
    official_id: str = ""
    # queued | uploading | synced | rejected | error | unconfirmed
    status: str = "queued"
    # Digest of the document behind `status`: the one the server accepted,
    # or the one it refused. Empty until either has happened.
    digest: str = ""
    upload_id: str = ""
    # The server's word for where the upload stands: pending (accepted, not
    # yet merged), then whatever the merge job reports.
    remote_status: str = ""
    uploaded_at: str = ""
    error: str = ""
    attempts: int = 0

    def persisted(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("slug")
        d.pop("attempts")
        return d


class TracksAdapter:
    def __init__(
        self,
        client: SyncClient,
        data_dir: Path,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.data_dir = data_dir
        self._clock = clock
        self._records: dict[str, TrackSync] = {}
        self._dirty: set[str] = set()
        # monotonic time before which a slug must not be tried again
        self._not_before: dict[str, float] = {}
        self._last_upload: dict[str, float] = {}
        self._inflight: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        # Type-level standing, distinct from any one track's: a rejected
        # token or an unreachable server is about the connection.
        self._error = ""
        # Why the toggle went off by itself, kept so the status line can say
        # "server no longer accepts this" after the type has stopped being
        # active — the one message that must outlive the state it describes.
        self._switched_off = ""
        self.last_ok_at: str | None = None
        self.last_attempt_at: str | None = None
        self.uploads = 0
        self._state_server = ""
        self.reload_state()

    # --- entry points -------------------------------------------------------

    def changed(self, track: str) -> None:
        """A bundle was written: upload it once it has settled.

        Safe to call from anywhere on the loop thread. Calling it again
        before the settle time is up starts the wait over, which is the
        whole point — a survey in progress is a bundle that is not done.
        """
        if not track:
            return
        self._defer(track_bundle.slugify(track), SETTLE_S)
        self._kick()

    def forget(self, slug: str) -> None:
        """The bundle was deleted: nothing to sync, nothing to remember."""
        self._dirty.discard(slug)
        self._not_before.pop(slug, None)
        if self._records.pop(slug, None) is not None:
            self._write_state()

    async def sweep(self) -> None:
        """Queue every bundle, due now. Unchanged ones are skipped by digest.

        A sweep is a fresh start — the type was just (re)enabled, or a person
        pressed Sync now — so nothing waits to settle, the standing complaint
        is dropped, and the next upload gets to make its own.
        """
        self._error = self._switched_off = ""
        for row in await asyncio.to_thread(track_bundle.list_bundles, self.data_dir):
            self._dirty.add(str(row["slug"]))
            self._not_before.pop(str(row["slug"]), None)
        self._kick()

    def clear_backoff(self) -> None:
        """Forget every retry delay: a person asked for it to go now."""
        self._not_before.clear()
        self._error = ""
        self._wake.set()

    def halt(self) -> None:
        """Stop sending. Keeps the queue: enabling again picks it back up."""
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self._inflight = None

    # --- the worker ---------------------------------------------------------

    def _kick(self) -> None:
        if not self.client.active(TYPE):
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop (a unit test writing bundles): stays queued
        self._wake.set()
        if self._task is None or self._task.done():
            self._task = self.client.spawn(self._run())

    def _due(self) -> list[str]:
        now = self._clock()
        return sorted(s for s in self._dirty if self._not_before.get(s, 0.0) <= now)

    async def _run(self) -> None:
        try:
            while self.client.active(TYPE):
                due = self._due()
                if not due:
                    if not self._dirty:
                        return
                    now = self._clock()
                    wait = max(0.0, min(self._not_before.get(s, now) for s in self._dirty) - now)
                    self._wake.clear()
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self._wake.wait(), timeout=wait)
                    continue
                for slug in due:
                    if not self.client.active(TYPE):
                        return
                    await self._push(slug)
        finally:
            self._task = None
            self._inflight = None

    async def wait_idle(self, timeout: float = 5.0) -> None:
        """Wait until nothing is due or in flight — the Admin push, and tests."""
        deadline = time.monotonic() + timeout
        while (self._due() or self._inflight is not None) and time.monotonic() < deadline:
            await asyncio.sleep(0.01)

    # --- one bundle ---------------------------------------------------------

    def _record(self, slug: str) -> TrackSync:
        rec = self._records.get(slug)
        if rec is None:
            rec = self._records[slug] = TrackSync(slug=slug)
        return rec

    def _defer(self, slug: str, seconds: float) -> None:
        self._dirty.add(slug)
        self._not_before[slug] = self._clock() + seconds

    async def _push(self, slug: str) -> None:
        self._dirty.discard(slug)
        self._not_before.pop(slug, None)
        doc = await asyncio.to_thread(track_bundle.load_slug, self.data_dir, slug)
        if doc is None:
            self.forget(slug)
            return
        rec = self._record(slug)
        rec.track = str(doc["meta"]["track"])
        official = doc["meta"].get("official") or {}
        rec.official_id = str(official.get("official_id") or "")
        if not rec.official_id:
            # Not an error and not remembered: the Tracks view says "confirm
            # the layout", and confirming it is what queues the bundle.
            rec.status, rec.error = "unconfirmed", ""
            return
        body, digest = await asyncio.to_thread(_serialise, doc)
        if rec.digest == digest and rec.status in SETTLED:
            return  # the server has already had its say on this exact document
        last = self._last_upload.get(slug)
        if last is not None and self._clock() - last < MIN_INTERVAL_S:
            rec.status = "queued"
            self._defer(slug, MIN_INTERVAL_S - (self._clock() - last))
            return
        if len(body) > MAX_UPLOAD_BYTES:
            rec.status, rec.digest = "rejected", digest
            rec.error = f"bundle is {len(body) // (1024 * 1024)} MB; the server takes 64 MB"
            self._write_state()
            return

        self._inflight = slug
        rec.status = "uploading"
        self.last_attempt_at = _now()
        try:
            reply = await self.client.transport().post(UPLOAD_PATH, body)
        except SyncError as exc:
            await self._failed(slug, rec, digest, exc)
            return
        finally:
            self._inflight = None
        self._last_upload[slug] = self._clock()
        self.last_ok_at = _now()
        self.uploads += 1
        self._error = ""
        rec.status = "synced"
        rec.digest = digest
        rec.upload_id = str(reply.get("upload_id") or "")
        rec.remote_status = str(reply.get("status") or "pending")
        rec.uploaded_at = self.last_ok_at
        rec.error = ""
        rec.attempts = 0
        self._write_state()
        log.info(
            "sync: uploaded %s (%d bytes) -> %s%s",
            slug, len(body), rec.remote_status,
            f" #{rec.upload_id}" if rec.upload_id else "",
        )

    async def _failed(self, slug: str, rec: TrackSync, digest: str, exc: SyncError) -> None:
        if exc.kind == "type_disabled":
            rec.status, rec.error = "queued", ""
            self._dirty.add(slug)
            self._error = self._switched_off = "server no longer accepts this"
            await self.client.type_disabled(TYPE, exc.message)
            return
        if exc.kind == "auth":
            # About the token, not the track: every bundle is in the same
            # boat, so hold them all back rather than fail them one by one.
            rec.status, rec.error = "queued", ""
            self._error = exc.message
            for other in list(self._dirty) + [slug]:
                self._defer(other, BACKOFF_MAX_S)
            log.warning("sync: %s", exc.message)
            return
        if exc.kind in ("rejected", "conflict"):
            rec.status, rec.digest, rec.error = "rejected", digest, exc.message
            rec.attempts = 0
            self._write_state()
            log.warning("sync: %s refused by the server: %s", slug, exc.message)
            return
        # Transient: unreachable, 5xx, rate limited, or a reply we could not
        # read. Back off and try again; the type shows the last reason.
        rec.attempts += 1
        rec.status, rec.error = "error", exc.message
        delay = exc.retry_after or min(BACKOFF_BASE_S * 2 ** (rec.attempts - 1), BACKOFF_MAX_S)
        self._defer(slug, delay)
        self._error = exc.message
        log.info("sync: %s not uploaded (%s); retrying in %.0f s", slug, exc.message, delay)

    # --- status -------------------------------------------------------------

    def state(self) -> str:
        if not self.client.active(TYPE):
            return "off"
        if self._inflight is not None:
            return "syncing"
        if self._error:
            return "error"
        return "idle"

    def status(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for rec in self._records.values():
            counts[rec.status] = counts.get(rec.status, 0) + 1
        return {
            "state": self.state(),
            "error": self._error if self.client.active(TYPE) else self._switched_off,
            "last_ok_at": self.last_ok_at,
            "last_attempt_at": self.last_attempt_at,
            "uploads": self.uploads,
            "queued": len(self._dirty),
            "tracks": counts,
        }

    def track_status(self, slug: str) -> dict[str, Any] | None:
        """One bundle's standing, for its row in the Tracks view."""
        if not self.client.active(TYPE):
            return None
        rec = self._records.get(slug)
        # Seconds until the next attempt — the rest of the settle time after
        # a change, or of the backoff after a failure. None when nothing is
        # scheduled.
        due = self._not_before.get(slug) if slug in self._dirty else None
        due_in = max(0, round(due - self._clock())) if due is not None else None
        if rec is None:
            if slug not in self._dirty:
                return {"status": "unknown"}
            return {"status": "queued", "due_in_s": due_in}
        # A change since the last verdict — synced, rejected, or unconfirmed
        # until a moment ago — is "queued"; an error keeps its name, since the
        # pending attempt is a retry of the same document.
        return {
            "status": (
                "uploading" if self._inflight == slug
                else "queued" if slug in self._dirty and rec.status != "error"
                else rec.status
            ),
            "remote_status": rec.remote_status,
            "upload_id": rec.upload_id,
            "uploaded_at": rec.uploaded_at,
            "error": rec.error,
            "attempts": rec.attempts,
            "due_in_s": due_in,
        }

    # --- persistence --------------------------------------------------------

    def _state_path(self) -> Path:
        return self.data_dir / STATE_FILE

    def reload_state(self) -> None:
        """Read what earlier runs uploaded. Another server's record is dropped:
        its accepted digests say nothing about what this one has seen."""
        self._records.clear()
        self._state_server = self.client.settings.sync_url
        path = self._state_path()
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            log.warning("sync: unreadable %s (%s); starting over", path.name, exc)
            return
        if (
            not isinstance(doc, dict)
            or doc.get("format") != STATE_FORMAT
            or doc.get("server") != self._state_server
        ):
            return
        tracks = doc.get("tracks")
        if not isinstance(tracks, dict):
            return
        for slug, raw in tracks.items():
            if not isinstance(raw, dict) or track_bundle.slugify(str(slug)) != slug:
                continue
            rec = TrackSync(slug=str(slug))
            for key in ("track", "official_id", "status", "digest", "upload_id",
                        "remote_status", "uploaded_at", "error"):
                value = raw.get(key)
                if isinstance(value, str):
                    setattr(rec, key, value)
            if rec.status not in SETTLED:
                continue
            self._records[rec.slug] = rec

    def _write_state(self) -> None:
        path = self._state_path()
        doc = {
            "format": STATE_FORMAT,
            "version": STATE_VERSION,
            "server": self._state_server,
            "tracks": {
                slug: rec.persisted()
                for slug, rec in sorted(self._records.items())
                if rec.status in SETTLED
            },
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:  # pragma: no cover - a full disk must not stop uploads
            log.warning("sync: could not write %s: %s", path.name, exc)


def _serialise(doc: dict[str, Any]) -> tuple[bytes, str]:
    """The upload body and the digest of its evidence. Blocking on a big bundle."""
    body = json.dumps(doc, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return body, digest(doc)


def digest(doc: dict[str, Any]) -> str:
    """A digest of the evidence, blind to the save timestamp."""
    meta = {k: v for k, v in doc["meta"].items() if k != "updated_at"}
    payload = json.dumps({**doc, "meta": meta}, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
