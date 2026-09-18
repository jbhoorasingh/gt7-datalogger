"""The `sessions` adapter: your own laps, sent to the service as you drive.

What gets sent is the lap document exactly as **Export** writes it — the
`gt7-datalogger-lap` v2 envelope with the full 60 Hz series inside — one
per lap, against a session the service opened for the drive. It is the
driver's own data and stays theirs: private by default, readable by the
account and by an administrator, and never anywhere near a track outline or
a pull request. The racing line the service keeps for a lap is the line
the driver took, not survey evidence.

When: a session is announced to the service at its **first lap**, not at
its start. The logger opens a local session on the first packet of a stint
and drops it again if no lap ever completes — menu visits and race restarts
open dozens of them — and the circuit is only identified once a lap has
been driven round it, so the summary posted at the first lap carries the
track and the layout id where one posted at the start would not. Every
lap goes as soon as it is saved; the totals go when the drive ends (the
next session starts, or the logger stops). Nothing waits to settle: a lap
is finished the moment it is.

Offline: the queue is a list of lap ids, persisted in
`data/sync-sessions.json` next to the remote session id each local session
was given, so a service that is unreachable for an afternoon gets the
afternoon's laps in order once it answers again, and a restart flushes what
the previous run could not send. The local database is the authority;
this file only remembers what has and has not reached the server. Posting
a lap is idempotent on the server (re-posting replaces), which is what
makes a flush after an ambiguous failure safe, and what lets a lap ruled in
or out of the bests by hand (#74) be sent again with its new verdict.

What comes back decides the next move exactly as it does for tracks: a
refused document (`4xx` with a reason, `409` for a session at the server's
lap cap) is recorded and skipped, a session the server no longer has
(`404` — deleted in the portal, or expired) is closed here too, a bad
token holds everything, and an unreachable server is retried with backoff.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.sync.transport import SyncError

if TYPE_CHECKING:
    from app.sync.client import SyncClient

log = logging.getLogger(__name__)

TYPE = "sessions"
SCOPE = "sessions:write"
SESSIONS_PATH = "/v1/sessions"
STATE_FILE = "sync-sessions.json"
STATE_FORMAT = "gt7-datalogger-sync-sessions"
STATE_VERSION = 1
BACKOFF_BASE_S = 30.0
BACKOFF_MAX_S = 15 * 60.0
# The server's default cap on one lap document; its capabilities name the
# real one and that is what is checked.
DEFAULT_MAX_BYTES = 16 * 1024 * 1024
# Laps waiting to go, across every session. A server that is down for a
# fortnight of driving would otherwise queue thousands; the local database
# keeps every one of them, so the oldest queued are dropped, and said so.
MAX_QUEUED_LAPS = 500
# Finished sessions kept on file after they are done, so a lap ruled in or
# out of the bests later can still be re-sent to the right remote session.
KEEP_DONE = 50

# Loads the export envelope for a lap id (the repo's export_lap), and the
# aggregate a session's totals are made of (the repo's session_lap_stats).
LapLoader = Callable[[int], Awaitable[dict[str, Any] | None]]
StatsLoader = Callable[[int], Awaitable[dict[str, Any]]]


@dataclass(slots=True)
class SessionSync:
    """One local session's standing with the service."""

    local_id: int
    remote_id: str = ""
    # The summary `POST /v1/sessions` takes, gathered as the facts arrive:
    # the car at the start, the circuit once a lap has identified it.
    car: str = ""
    car_id: int = 0
    official_id: str = ""
    track_name: str = ""
    started_at: str = ""
    source_id: str = ""
    # (lap id, lap number) still to send, in driving order.
    queue: list[tuple[int, int]] = field(default_factory=list)
    # Laps the server has had its say on: lap id -> number, status, error.
    laps: dict[int, dict[str, Any]] = field(default_factory=dict)
    end_pending: bool = False
    ended_at: str = ""
    # Set when the server no longer has the session; nothing more is sent.
    closed: str = ""

    @property
    def pending(self) -> bool:
        return not self.closed and (bool(self.queue) or self.end_pending)

    def persisted(self) -> dict[str, Any]:
        return {
            "remote_id": self.remote_id,
            "car": self.car,
            "car_id": self.car_id,
            "official_id": self.official_id,
            "track_name": self.track_name,
            "started_at": self.started_at,
            "source_id": self.source_id,
            "queue": [list(item) for item in self.queue],
            "laps": {str(lap_id): dict(v) for lap_id, v in self.laps.items()},
            "end_pending": self.end_pending,
            "ended_at": self.ended_at,
            "closed": self.closed,
        }


class SessionsAdapter:
    def __init__(
        self,
        client: SyncClient,
        data_dir: Path,
        load_lap: LapLoader | None = None,
        load_stats: StatsLoader | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.data_dir = data_dir
        self._load_lap = load_lap
        self._load_stats = load_stats
        self._clock = clock
        self._sessions: dict[int, SessionSync] = {}
        self._current: int | None = None
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._inflight = ""
        # Connection-level standing: a bad token or an unreachable server is
        # about every lap alike, so one clock and one complaint cover them.
        self._not_before = 0.0
        self._attempts = 0
        self._error = ""
        self._switched_off = ""
        self.last_ok_at: str | None = None
        self.last_attempt_at: str | None = None
        self.uploads = 0
        self._state_server = ""
        self.reload_state()

    # --- what the service tells us ------------------------------------------

    def session_started(
        self,
        local_id: int,
        *,
        car: str,
        car_id: int,
        started_at: str,
        source_id: str = "",
    ) -> None:
        """A local session opened. Nothing is sent until its first lap."""
        rec = self._record(local_id)
        rec.car, rec.car_id, rec.started_at, rec.source_id = car, car_id, started_at, source_id
        self._current = local_id

    def lap_saved(
        self,
        local_id: int,
        lap_id: int,
        number: int,
        *,
        track_name: str = "",
        official_id: str = "",
    ) -> None:
        """A lap is in the database: queue it. Safe on the packet path."""
        rec = self._record(local_id)
        if rec.closed:
            return
        # The circuit is known one lap late; whatever is known now goes on
        # the summary if it has not been posted yet.
        if not rec.remote_id:
            rec.track_name = track_name or rec.track_name
            rec.official_id = official_id or rec.official_id
        if all(item[0] != lap_id for item in rec.queue):
            rec.queue.append((lap_id, number))
        self._trim_queue()
        self._write_state()
        self._kick()

    def lap_changed(self, local_id: int, lap_id: int, number: int) -> None:
        """A stored lap's verdict changed by hand: send it again, replacing
        the copy the server holds. Ignored for a session it never had."""
        rec = self._sessions.get(local_id)
        if rec is None or rec.closed or not rec.remote_id:
            return
        if rec.laps.get(lap_id, {}).get("status") != "synced":
            return
        if all(item[0] != lap_id for item in rec.queue):
            rec.queue.append((lap_id, number))
        self._write_state()
        self._kick()

    def session_ended(self, local_id: int) -> None:
        """The drive is over: the totals go once the queued laps have."""
        rec = self._sessions.get(local_id)
        if rec is None or rec.closed:
            return
        if not rec.remote_id and not rec.queue:
            # Never announced and nothing to announce it with: a session
            # with no lap is dropped locally too.
            self._sessions.pop(local_id, None)
        else:
            rec.end_pending = True
            rec.ended_at = rec.ended_at or _now()
        if self._current == local_id:
            self._current = None
        self._write_state()
        self._kick()

    def forget(self, local_id: int) -> None:
        """The local session was dropped (no laps) or deleted."""
        if self._sessions.pop(local_id, None) is not None:
            self._write_state()
        if self._current == local_id:
            self._current = None

    # --- the adapter protocol -----------------------------------------------

    async def sweep(self) -> None:
        """Send whatever is waiting, now: the type was (re)enabled, the logger
        started with a queue on file, or a person pressed the button."""
        self._error = self._switched_off = ""
        self._not_before = 0.0
        self._kick()

    def clear_backoff(self) -> None:
        self._not_before = 0.0
        self._attempts = 0
        self._error = ""
        self._wake.set()

    def halt(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self._inflight = ""

    def reload_state(self) -> None:
        """Read the queue and the remote ids an earlier run left. Another
        server's record is dropped: its session ids mean nothing here."""
        self._sessions.clear()
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
        sessions = doc.get("sessions")
        if not isinstance(sessions, dict):
            return
        for key, raw in sessions.items():
            try:
                local_id = int(key)
            except ValueError:
                continue
            if not isinstance(raw, dict):
                continue
            rec = SessionSync(local_id=local_id)
            for name in ("remote_id", "car", "official_id", "track_name",
                         "started_at", "source_id", "ended_at", "closed"):
                value = raw.get(name)
                if isinstance(value, str):
                    setattr(rec, name, value)
            if isinstance(raw.get("car_id"), int):
                rec.car_id = raw["car_id"]
            rec.end_pending = bool(raw.get("end_pending"))
            queue = raw.get("queue")
            if isinstance(queue, list):
                rec.queue = [
                    (int(item[0]), int(item[1]))
                    for item in queue
                    if isinstance(item, list) and len(item) == 2
                    and all(isinstance(v, int) for v in item)
                ]
            laps = raw.get("laps")
            if isinstance(laps, dict):
                for lap_key, verdict in laps.items():
                    if isinstance(verdict, dict) and str(lap_key).isdigit():
                        rec.laps[int(lap_key)] = {
                            "number": int(verdict.get("number") or 0),
                            "status": str(verdict.get("status") or ""),
                            "error": str(verdict.get("error") or ""),
                        }
            self._sessions[local_id] = rec

    # --- the worker ---------------------------------------------------------

    def _record(self, local_id: int) -> SessionSync:
        rec = self._sessions.get(local_id)
        if rec is None:
            rec = self._sessions[local_id] = SessionSync(local_id=local_id)
        return rec

    def _kick(self) -> None:
        if not self.client.active(TYPE):
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop (a unit test): stays queued on file
        self._wake.set()
        if self._task is None or self._task.done():
            self._task = self.client.spawn(self._run())

    def _next(self) -> SessionSync | None:
        for local_id in sorted(self._sessions):
            rec = self._sessions[local_id]
            if rec.pending:
                return rec
        return None

    def _due(self) -> bool:
        return self._next() is not None and self._not_before <= self._clock()

    async def _run(self) -> None:
        try:
            while self.client.active(TYPE):
                if self._next() is None:
                    self._prune()
                    return
                if not self._due():
                    wait = max(0.0, self._not_before - self._clock())
                    self._wake.clear()
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self._wake.wait(), timeout=wait)
                    continue
                await self._push_next()
        finally:
            self._task = None
            self._inflight = ""

    async def wait_idle(self, timeout: float = 5.0) -> None:
        """Wait until nothing is due or in flight — tests, and the Admin push."""
        deadline = time.monotonic() + timeout
        while (self._due() or self._inflight) and time.monotonic() < deadline:
            await asyncio.sleep(0.01)

    async def _push_next(self) -> None:
        rec = self._next()
        if rec is None:
            return
        if not rec.remote_id:
            await self._start(rec)
            return
        if rec.queue:
            lap_id, number = rec.queue[0]
            await self._send_lap(rec, lap_id, number)
            return
        if rec.end_pending:
            await self._end(rec)

    # --- the three calls ----------------------------------------------------

    async def _start(self, rec: SessionSync) -> None:
        body = json.dumps(_summary(rec), separators=(",", ":")).encode("utf-8")
        reply = await self._call(rec, f"session {rec.local_id}", "POST", SESSIONS_PATH, body)
        if reply is None:
            return
        remote_id = str(reply.get("session_id") or "")
        if not remote_id:
            self._transient(SyncError("protocol", "the server named no session id"))
            return
        rec.remote_id = remote_id
        self._write_state()
        log.info("sync: session %d opened on the server as %s", rec.local_id, remote_id)

    async def _send_lap(self, rec: SessionSync, lap_id: int, number: int) -> None:
        doc = await self._load_lap(lap_id) if self._load_lap else None
        if doc is None:
            # Deleted since it was queued. Nothing to send, nothing to remember.
            rec.queue.remove((lap_id, number))
            rec.laps.pop(lap_id, None)
            self._write_state()
            return
        body = json.dumps(doc, separators=(",", ":")).encode("utf-8")
        cap = self.client.hint(TYPE, "max_bytes", DEFAULT_MAX_BYTES)
        if len(body) > cap:
            rec.queue.remove((lap_id, number))
            rec.laps[lap_id] = {
                "number": number, "status": "rejected",
                "error": f"lap is {len(body) // (1024 * 1024)} MB; the server takes"
                         f" {cap // (1024 * 1024)} MB",
            }
            self._write_state()
            return
        path = f"{SESSIONS_PATH}/{rec.remote_id}/laps"
        reply = await self._call(rec, f"lap {number}", "POST", path, body, lap=(lap_id, number))
        if reply is None:
            return
        rec.queue.remove((lap_id, number))
        rec.laps[lap_id] = {"number": number, "status": "synced", "error": ""}
        self.uploads += 1
        self._write_state()
        log.info(
            "sync: lap %d of session %d (%d bytes) -> %s%s",
            number, rec.local_id, len(body), rec.remote_id,
            " (replaced)" if reply.get("replaced") else "",
        )

    async def _end(self, rec: SessionSync) -> None:
        stats = await self._load_stats(rec.local_id) if self._load_stats else {}
        totals: dict[str, Any] = {"ended_at": rec.ended_at or _now()}
        count = int(stats.get("count") or 0)
        best = int(stats.get("best_ms") or -1)
        if count > 0:
            totals["laps"] = count
        if best > 0:
            totals["best_lap_ms"] = best
        body = json.dumps(totals, separators=(",", ":")).encode("utf-8")
        path = f"{SESSIONS_PATH}/{rec.remote_id}"
        reply = await self._call(rec, f"session {rec.local_id} totals", "PATCH", path, body)
        if reply is None:
            if rec.closed:
                self._write_state()
            return
        rec.end_pending = False
        self._write_state()
        log.info("sync: session %d closed on the server (%d laps)", rec.local_id, count)

    async def _call(
        self,
        rec: SessionSync,
        what: str,
        method: str,
        path: str,
        body: bytes,
        lap: tuple[int, int] | None = None,
    ) -> dict[str, Any] | None:
        """One request, with every failure sorted into the adapter's moves.

        Returns the reply, or None after recording what went wrong: a
        transient failure sets the backoff, a refused document is written
        into the record (`lap` names the one it was), a bad token holds
        everything.
        """
        self._inflight = what
        self.last_attempt_at = _now()
        try:
            reply = await self.client.transport().request(method, path, body)
        except SyncError as exc:
            await self._failed(rec, what, exc, lap)
            return None
        finally:
            self._inflight = ""
        self.last_ok_at = _now()
        self._error = ""
        self._attempts = 0
        return reply

    async def _failed(
        self, rec: SessionSync, what: str, exc: SyncError, lap: tuple[int, int] | None
    ) -> None:
        if exc.kind == "type_disabled":
            self._error = self._switched_off = "server no longer accepts this"
            await self.client.type_disabled(TYPE, exc.message)
            return
        if exc.kind == "auth":
            self._error = exc.message
            self._not_before = self._clock() + BACKOFF_MAX_S
            log.warning("sync: %s", exc.message)
            return
        if exc.kind in ("rejected", "conflict"):
            if exc.status == 404 and rec.remote_id:
                # The server no longer has the session: deleted in the
                # portal, or past its retention. Not ours to recreate.
                rec.closed = exc.message or "the server no longer has this session"
                rec.queue.clear()
                rec.end_pending = False
                log.warning("sync: session %d closed by the server: %s", rec.local_id, rec.closed)
            elif lap is not None:
                # About this document; the next lap gets its own answer.
                lap_id, number = lap
                if lap in rec.queue:
                    rec.queue.remove(lap)
                rec.laps[lap_id] = {"number": number, "status": "rejected", "error": exc.message}
            else:
                # The summary or the totals refused: the session cannot be
                # sent as it stands, and re-trying the same document would
                # only collect the same answer.
                rec.closed = exc.message
                rec.queue.clear()
                rec.end_pending = False
            log.warning("sync: %s refused by the server: %s", what, exc.message)
            self._write_state()
            return
        self._transient(exc)

    def _transient(self, exc: SyncError) -> None:
        self._attempts += 1
        self._error = exc.message
        delay = exc.retry_after or min(BACKOFF_BASE_S * 2 ** (self._attempts - 1), BACKOFF_MAX_S)
        self._not_before = self._clock() + delay
        log.info("sync: sessions not sent (%s); retrying in %.0f s", exc.message, delay)

    # --- housekeeping -------------------------------------------------------

    def _trim_queue(self) -> None:
        total = sum(len(rec.queue) for rec in self._sessions.values())
        if total <= MAX_QUEUED_LAPS:
            return
        dropped = 0
        for local_id in sorted(self._sessions):
            rec = self._sessions[local_id]
            while rec.queue and total > MAX_QUEUED_LAPS:
                rec.queue.pop(0)
                total -= 1
                dropped += 1
        log.warning(
            "sync: %d queued laps dropped — the queue is capped at %d; the local"
            " database keeps them", dropped, MAX_QUEUED_LAPS,
        )

    def _prune(self) -> None:
        """Keep the file small: finished sessions beyond the last few go."""
        done = [rec.local_id for rec in self._sessions.values()
                if not rec.pending and rec.local_id != self._current]
        for local_id in sorted(done)[:-KEEP_DONE] if len(done) > KEEP_DONE else []:
            self._sessions.pop(local_id, None)
        self._write_state()

    # --- status -------------------------------------------------------------

    def state(self) -> str:
        if not self.client.active(TYPE):
            return "off"
        if self._inflight:
            return "syncing"
        if self._error:
            return "error"
        return "idle"

    def status(self) -> dict[str, Any]:
        queued = sum(len(rec.queue) + int(rec.end_pending) for rec in self._sessions.values())
        due = self._not_before - self._clock() if self._next() is not None else 0.0
        current = self._sessions.get(self._current) if self._current is not None else None
        return {
            "state": self.state(),
            "error": self._error if self.client.active(TYPE) else self._switched_off,
            "last_ok_at": self.last_ok_at,
            "last_attempt_at": self.last_attempt_at,
            "uploads": self.uploads,
            "queued": queued,
            "due_in_s": max(0, round(due)) if due > 0 else None,
            "sessions": {
                "synced": sum(1 for rec in self._sessions.values() if rec.remote_id),
                "pending": sum(1 for rec in self._sessions.values() if rec.pending),
                "closed": sum(1 for rec in self._sessions.values() if rec.closed),
                "laps_rejected": sum(
                    1 for rec in self._sessions.values()
                    for v in rec.laps.values() if v["status"] == "rejected"
                ),
                "current": (
                    {
                        "local_id": current.local_id,
                        "remote_id": current.remote_id,
                        "laps_synced": sum(
                            1 for v in current.laps.values() if v["status"] == "synced"
                        ),
                        "laps_queued": len(current.queue),
                        "closed": current.closed,
                    }
                    if current is not None else None
                ),
            },
        }

    def session_status(self, local_id: int) -> dict[str, Any] | None:
        """One local session's standing, for its row in the Sessions view."""
        if not self.client.active(TYPE):
            return None
        rec = self._sessions.get(local_id)
        if rec is None:
            return {"status": "unknown"}
        synced = sum(1 for v in rec.laps.values() if v["status"] == "synced")
        rejected = sum(1 for v in rec.laps.values() if v["status"] == "rejected")
        if rec.closed:
            status = "closed"
        elif self._inflight and self._next() is rec:
            status = "uploading"
        elif rec.queue or rec.end_pending:
            status = "queued"
        elif rec.remote_id:
            status = "synced"
        else:
            status = "unknown"
        return {
            "status": status,
            "remote_id": rec.remote_id,
            "laps_synced": synced,
            "laps_queued": len(rec.queue),
            "laps_rejected": rejected,
            "error": rec.closed,
        }

    # --- persistence --------------------------------------------------------

    def _state_path(self) -> Path:
        return self.data_dir / STATE_FILE

    def _write_state(self) -> None:
        path = self._state_path()
        doc = {
            "format": STATE_FORMAT,
            "version": STATE_VERSION,
            "server": self._state_server,
            "sessions": {
                str(local_id): rec.persisted()
                for local_id, rec in sorted(self._sessions.items())
            },
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:  # pragma: no cover - a full disk must not stop uploads
            log.warning("sync: could not write %s: %s", path.name, exc)


def _summary(rec: SessionSync) -> dict[str, Any]:
    """The session summary the service takes. A start time it cannot parse
    is left out rather than refused: the service then dates the session
    itself, and the laps behind it are not lost over a timestamp."""
    body: dict[str, Any] = {
        "car": rec.car,
        "car_id": rec.car_id,
        "official_id": rec.official_id,
        "track_name": rec.track_name,
    }
    if _is_time(rec.started_at):
        body["started_at"] = rec.started_at
    if rec.source_id:
        body["source_id"] = rec.source_id
    return body


def _is_time(text: str) -> bool:
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
