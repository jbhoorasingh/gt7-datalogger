"""The `live` adapter: where the car is, a few times a second, for spectators.

One WebSocket to the service's `/v1/live`, held while the car is on track,
and a frame down it about four times a second: the stream's clock, the
position on the circuit's plane, speed, gear, lap, the lap's elapsed time
and whether the car is on the road. The service fans the frames out to
whoever is watching — its spectate page, an overlay, race control — and
keeps the last half-minute for anyone who joins late. Frames are not
stored anywhere unless the account has asked for a recording.

Downsampled from the 60 Hz feed, not a second capture: the packet handler
offers the adapter every packet and the adapter keeps one — the latest —
and sends it when the interval is up. There is no queue. A frame that could
not be sent because the socket was down is not worth sending later; the
spectator wants to know where the car *is*, and a reconnect resumes from
the next packet.

The socket is opened lazily, on the first packet that says the car is on
track, and closed after five minutes without one, so a logger that runs
all day on a Pi holds no connection open while the console is off, and the
listing on the service does not show a driver who is in the menus. When
the car leaves the track or the game pauses, one last frame says so and
then the stream goes quiet; the socket stays for the pit stop.

What the server sends back matters in three places. `ready` states the
rate the server will take, and the lower of that and `GT7_SYNC_LIVE_HZ`
wins. `spectators` is shown in the Sync panel. And a close code in the
4xxx range is the server's verdict rather than a dropped connection: kicked
by an administrator, replaced by another logger on the same account, or
sent nothing it could read — none of which a quick reconnect would mend,
so those hold the stream for a quarter of an hour and say why. A refused
upgrade is read the same way a refused upload is: `403 type_disabled`
switches the type off, a bad token holds it, anything else backs off.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from app.sync.transport import SyncError, classify_handshake

if TYPE_CHECKING:
    from app.models import TelemetryPacket
    from app.sync.client import SyncClient

log = logging.getLogger(__name__)

TYPE = "live"
SCOPE = "live:write"
LIVE_PATH = "/v1/live"
# What the server takes until its `ready` says otherwise.
SERVER_DEFAULT_MAX_HZ = 10.0
# No on-track frame for this long: close the socket. The next one reopens it.
IDLE_CLOSE_S = 5 * 60.0
BACKOFF_BASE_S = 5.0
BACKOFF_MAX_S = 15 * 60.0
OPEN_TIMEOUT_S = 15.0
# The server's own close codes (live/stream.ts): its verdicts, not drops.
CLOSE_RUBBISH = 4400
CLOSE_KICKED = 4403
CLOSE_REPLACED = 4409
# What the server bounds a frame to; a value outside is a frame it drops.
MAX_SPEED_KMH = 1000.0
# A frame the socket could not take within this long says where the car
# was, not where it is, and is dropped rather than sent late.
STALE_S = 2.0


class Socket(Protocol):
    """The three things the adapter does with a connection. `websockets`'
    client connection is one; the tests' fake is another."""

    async def send(self, message: str) -> None: ...
    async def recv(self) -> str | bytes: ...
    async def close(self, code: int = 1000, reason: str = "") -> None: ...


Connector = Callable[[str, dict[str, str]], Awaitable[Socket]]


async def websockets_connect(url: str, headers: dict[str, str]) -> Socket:
    """The real connection: `websockets`' asyncio client, with the token as a
    header, no compression (frames are a hundred bytes) and a bounded
    message size (the server sends nothing large)."""
    from websockets.asyncio.client import connect

    headers = dict(headers)
    agent = headers.pop("User-Agent", None)
    return await connect(
        url,
        additional_headers=headers,
        user_agent_header=agent,
        open_timeout=OPEN_TIMEOUT_S,
        compression=None,
        max_size=64 * 1024,
    )


class LiveAdapter:
    def __init__(
        self,
        client: SyncClient,
        connect: Connector = websockets_connect,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self._connect = connect
        self._clock = clock
        # What the packet path leaves for the sender: the newest frame only,
        # and when it was offered.
        self._latest: dict[str, Any] | None = None
        self._latest_at = 0.0
        self._send = asyncio.Event()
        self._last_offer = 0.0
        self._on_track = False
        # The circuit and the car, as the service learns them; sent on connect
        # and again as a `meta` message whenever they change mid-stream.
        self._meta: dict[str, str] = {"official_id": "", "track": "", "car": ""}
        self._meta_dirty = False
        self._task: asyncio.Task[None] | None = None
        self._socket: Socket | None = None
        self._connecting = False
        self._t0: float | None = None
        self._not_before = 0.0
        self._attempts = 0
        self._error = ""
        self._switched_off = ""
        self._wake = asyncio.Event()
        # What the server said when it accepted the stream.
        self.user_id = ""
        self.started_at = ""
        self.server_max_hz = SERVER_DEFAULT_MAX_HZ
        self.recording = False
        self.spectators = 0
        self.frames = 0
        self.last_frame_at: str | None = None
        self.last_attempt_at: str | None = None

    # --- what the service tells us ------------------------------------------

    def set_meta(self, **facts: str) -> None:
        """The circuit (`official_id`, `track`) or the car changed."""
        changed = False
        for key, value in facts.items():
            if key in self._meta and self._meta[key] != value:
                self._meta[key] = value
                changed = True
        if changed and self._socket is not None:
            self._meta_dirty = True
            self._send.set()

    def on_packet(self, p: TelemetryPacket, lap_elapsed_s: Callable[[], float]) -> None:
        """Every packet, at 60 Hz. Cheap unless a frame is due.

        `lap_elapsed_s` is asked only when a frame is built, because the
        answer costs a look into the live lap's samples.
        """
        if not self.client.active(TYPE):
            return
        now = self._clock()
        if not p.is_on_track or p.is_paused:
            if self._on_track:
                # One frame to say the car has gone, then nothing until it
                # is back: the pits and the menus are not worth a rate.
                self._on_track = False
                self._offer(self._frame(p, lap_elapsed_s(), on_track=False), now)
            return
        self._on_track = True
        if now - self._last_offer < self.interval:
            return
        self._offer(self._frame(p, lap_elapsed_s(), on_track=True), now)

    @property
    def interval(self) -> float:
        """Seconds between frames: the setting, no faster than the server takes."""
        wanted = max(0.5, float(self.client.settings.sync_live_hz))
        return 1.0 / min(wanted, max(1.0, self.server_max_hz))

    def _offer(self, frame: dict[str, Any], now: float) -> None:
        self._last_offer = now
        self._latest = frame
        self._latest_at = now
        self._send.set()
        self._ensure()

    def _frame(self, p: TelemetryPacket, elapsed_s: float, on_track: bool) -> dict[str, Any]:
        if self._t0 is None:
            self._t0 = self._clock()
        return {
            "t": round(self._clock() - self._t0, 3),
            "x": round(p.position_x, 2),
            "z": round(p.position_z, 2),
            "speed": round(min(MAX_SPEED_KMH, max(0.0, p.speed_kmh)), 1),
            "gear": max(-1, min(15, p.current_gear)),
            "lap": max(0, p.current_lap),
            # Seconds: what the service's spectate page reads it as.
            "lap_time": round(max(0.0, elapsed_s), 3),
            "on_track": on_track,
        }

    # --- the adapter protocol -----------------------------------------------

    async def sweep(self) -> None:
        """The type was (re)enabled: forget the standing complaint. The socket
        opens on the next on-track packet, not now — there may be no car."""
        self._error = self._switched_off = ""
        self._not_before = 0.0
        self._attempts = 0
        if self._latest is not None and self._on_track:
            self._ensure()

    def clear_backoff(self) -> None:
        self._not_before = 0.0
        self._attempts = 0
        self._error = ""
        self._wake.set()

    def halt(self) -> None:
        """Stop streaming. Closes the socket, on its own task."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
        self._socket = None
        self._connecting = False
        self._on_track = False
        self._latest = None
        self._t0 = None

    def reload_state(self) -> None:
        """Another server: whatever this one said about the stream is void."""
        self.user_id = ""
        self.started_at = ""
        self.server_max_hz = SERVER_DEFAULT_MAX_HZ
        self.spectators = 0
        self._t0 = None

    # --- the connection -----------------------------------------------------

    def _ensure(self) -> None:
        if not self.client.active(TYPE):
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self._wake.set()
        if self._task is None or self._task.done():
            self._task = self.client.spawn(self._run())

    async def _run(self) -> None:
        try:
            while self.client.active(TYPE):
                now = self._clock()
                if self._not_before > now:
                    self._wake.clear()
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self._wake.wait(), timeout=self._not_before - now)
                    continue
                outcome = await self._session()
                if outcome == "idle":
                    return
        finally:
            self._task = None
            self._socket = None
            self._connecting = False

    async def _session(self) -> str:
        """One connection, from the upgrade to the close. Returns "idle" when
        the stream closed itself for want of a car, "again" otherwise."""
        transport = self.client.transport()
        url = transport.websocket_url(LIVE_PATH, self._meta)
        self._connecting = True
        self.last_attempt_at = _now()
        try:
            socket = await self._connect(url, transport.auth_headers())
        except Exception as exc:
            self._connecting = False
            await self._refused(exc)
            return "again"
        self._connecting = False
        self._socket = socket
        self._meta_dirty = False
        self._error = ""
        self._attempts = 0
        log.info("sync: live stream connected")

        reader = asyncio.ensure_future(self._read(socket))
        sender = asyncio.ensure_future(self._write(socket))
        outcome = "again"
        try:
            done, _pending = await asyncio.wait(
                {reader, sender}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                result = task.result()
                if isinstance(result, str):
                    outcome = result
        except Exception as exc:  # noqa: BLE001 - the socket's verdict, whatever raised it
            self._closed(exc)
        finally:
            for task in (reader, sender):
                if not task.done():
                    task.cancel()
                    with contextlib.suppress(BaseException):
                        await task
            self._socket = None
            with contextlib.suppress(Exception):
                await socket.close(1000, "logger stopped streaming")
        if outcome == "idle":
            # The next on-track packet is a new stream, with a new clock.
            log.info("sync: live stream closed after %.0f s without a car", IDLE_CLOSE_S)
            self._on_track = False
            self._latest = None
            self._t0 = None
        return outcome

    async def _read(self, socket: Socket) -> None:
        """The server's messages: `ready` tunes the rate, `spectators` is
        shown, `warning` is logged. Ends when the socket does."""
        while True:
            raw = await socket.recv()
            text = raw if isinstance(raw, str) else raw.decode("utf-8", "replace")
            try:
                message = json.loads(text)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            kind = message.get("type")
            if kind == "ready":
                self.user_id = str(message.get("user_id") or self.user_id)
                self.started_at = str(message.get("started_at") or self.started_at)
                hz = message.get("max_hz")
                if isinstance(hz, int | float) and hz > 0:
                    self.server_max_hz = float(hz)
                self.recording = bool(message.get("recording"))
                n = message.get("spectators")
                if isinstance(n, int):
                    self.spectators = n
            elif kind == "spectators":
                n = message.get("n")
                if isinstance(n, int):
                    self.spectators = n
            elif kind == "warning":
                log.warning("sync: live stream: %s", message.get("reason") or "warning")

    async def _write(self, socket: Socket) -> str:
        """Frames as they are offered; a `meta` first when it changed; and
        the idle close when nothing has been offered for a long time."""
        while True:
            self._send.clear()
            if self._latest is None and not self._meta_dirty:
                try:
                    await asyncio.wait_for(self._send.wait(), timeout=IDLE_CLOSE_S)
                except TimeoutError:
                    return "idle"
                continue
            if self._meta_dirty:
                self._meta_dirty = False
                await socket.send(json.dumps({
                    "type": "meta",
                    "official_id": self._meta["official_id"],
                    "track_name": self._meta["track"],
                    "car": self._meta["car"],
                }, separators=(",", ":")))
            frame, self._latest = self._latest, None
            if frame is None or self._clock() - self._latest_at > STALE_S:
                # Offered while the socket was down, and the car has moved
                # on since: the stream resumes from the next packet.
                continue
            await socket.send(json.dumps(frame, separators=(",", ":")))
            self.frames += 1
            self.last_frame_at = _now()

    # --- what went wrong ----------------------------------------------------

    async def _refused(self, exc: Exception) -> None:
        """The upgrade did not happen. A status in the reply is the server's
        answer and is read as one; anything else is the network's."""
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        if isinstance(status, int):
            body = getattr(response, "body", b"") or b""
            headers = {str(k): str(v) for k, v in dict(getattr(response, "headers", {})).items()}
            error = classify_handshake(status, bytes(body), headers)
        elif isinstance(exc, TimeoutError | asyncio.TimeoutError):
            error = SyncError("unreachable", "timed out")
        elif isinstance(exc, OSError):
            error = SyncError("unreachable", f"cannot connect: {exc}".rstrip(": "))
        else:
            error = SyncError("unreachable", f"{type(exc).__name__}: {exc}".rstrip(": "))
        await self._failed(error)

    def _closed(self, exc: Exception) -> None:
        """The socket ended. A 4xxx close is the server's verdict."""
        close = getattr(exc, "rcvd", None)
        code = getattr(close, "code", None)
        reason = str(getattr(close, "reason", "") or "")
        if code == CLOSE_KICKED:
            self._hold(f"closed by the server: {reason or 'kicked'}")
        elif code == CLOSE_REPLACED:
            self._hold("another logger is streaming on this account")
        elif code == CLOSE_RUBBISH:
            self._hold(f"the server could not read the stream: {reason or 'closed'}")
        else:
            self._transient(SyncError("unreachable", f"stream closed ({code or 'no code'})"))

    async def _failed(self, exc: SyncError) -> None:
        if exc.kind == "type_disabled":
            self._error = self._switched_off = "server no longer accepts this"
            await self.client.type_disabled(TYPE, exc.message)
            return
        if exc.kind == "auth":
            self._hold(exc.message)
            return
        if exc.kind in ("rejected", "conflict", "protocol"):
            # Not a connection problem and not one a retry mends; hold, and
            # let a person read the reason.
            self._hold(exc.message)
            return
        self._transient(exc)

    def _hold(self, message: str) -> None:
        self._error = message
        self._not_before = self._clock() + BACKOFF_MAX_S
        log.warning("sync: live stream held: %s", message)

    def _transient(self, exc: SyncError) -> None:
        self._attempts += 1
        self._error = exc.message
        delay = exc.retry_after or min(BACKOFF_BASE_S * 2 ** (self._attempts - 1), BACKOFF_MAX_S)
        self._not_before = self._clock() + delay
        log.info("sync: live stream down (%s); reconnecting in %.0f s", exc.message, delay)

    # --- status -------------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._socket is not None

    def state(self) -> str:
        if not self.client.active(TYPE):
            return "off"
        if self._socket is not None:
            return "connected"
        if self._connecting:
            return "syncing"
        if self._error:
            return "error"
        return "idle"

    def status(self) -> dict[str, Any]:
        due = self._not_before - self._clock()
        url = self.client.settings.sync_url.rstrip("/")
        return {
            "state": self.state(),
            "error": self._error if self.client.active(TYPE) else self._switched_off,
            "last_attempt_at": self.last_attempt_at,
            "last_ok_at": self.last_frame_at,
            "uploads": self.frames,
            "due_in_s": max(0, round(due)) if due > 0 and self._error else None,
            "live": {
                "connected": self.connected,
                "streaming": self.connected and self._on_track,
                "hz": round(1.0 / self.interval, 2),
                "spectators": self.spectators,
                "frames": self.frames,
                "user_id": self.user_id,
                "spectate_url": f"{url}/live/{self.user_id}" if self.user_id else "",
                "started_at": self.started_at,
                "recording": self.recording,
                "last_frame_at": self.last_frame_at,
            },
        }


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
