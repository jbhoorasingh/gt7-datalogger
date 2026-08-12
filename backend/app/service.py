"""Central service: telemetry source -> lap processing -> storage -> live clients."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import WebSocket

from app.config import Settings
from app.models import TelemetryPacket
from app.notify import Notifier
from app.processing import track_bundle, tracks
from app.processing.analysis import Samples, time_delta_at
from app.processing.cars import CarDatabase
from app.processing.laps import CompletedLap, LapProcessor, SessionInfo
from app.processing.live_events import LiveEvent, LiveEventWatcher
from app.processing.surface import encode_surface
from app.processing.survey import SurfaceSurvey
from app.processing.tracks import signature_from_samples
from app.race_engineer import CATEGORIES, VoiceCallout
from app.race_engineer.manager import RaceEngineerManager
from app.storage.repository import Repository, lap_summary  # noqa: F401  (re-export)
from app.telemetry.listener import UdpTelemetrySource
from app.telemetry.simulator import SimTelemetrySource, scenario_for

log = logging.getLogger(__name__)


@dataclass(slots=True)
class _ClientStream:
    """Per-client outbound state: a slow reader must never stall capture.

    Two lanes: telemetry frames land in a 1-slot latest-wins mailbox (missing
    intermediate frames is fine — each is a full state snapshot), while
    lap/session/status events queue up and are never dropped. A client whose
    event queue overflows has been unreadable for minutes and is disconnected.
    """

    ws: WebSocket
    events: asyncio.Queue[str] = field(default_factory=lambda: asyncio.Queue(maxsize=256))
    frame: str | None = None
    wakeup: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None
    # Race Engineer: who this browser is and whether it can speak. Set from
    # the client's `client_capabilities` message; absent for older pages,
    # which simply never register and never speak.
    client_id: str = ""
    page: str = ""
    voice_supported: bool = False
    voice_enabled: bool = False


async def _close_ws(ws: WebSocket) -> None:
    """Best-effort close; the socket may already be half-dead."""
    try:
        await ws.close(code=1013)  # 1013 = try again later (server overloaded)
    except Exception:  # noqa: BLE001
        pass


def _count_events(events: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in events:
        kind = str(e.get("type", ""))
        counts[kind] = counts.get(kind, 0) + 1
    return counts


class TelemetryService:
    def __init__(self, settings: Settings, repo: Repository, cars: CarDatabase) -> None:
        self.settings = settings
        self.repo = repo
        self.cars = cars
        self.started_at = time.time()
        self.processor = LapProcessor(on_lap=self._on_lap, on_session=self._on_session)
        self.source: UdpTelemetrySource | SimTelemetrySource
        if settings.source == "sim":
            self.source = SimTelemetrySource(
                self._on_packet, scenario_for(settings.sim_scenario)
            )
        else:
            self.source = UdpTelemetrySource(settings, self._on_packet)

        self.recording = True
        self.session_id: int | None = None
        self.track_name: str = ""
        self.latest_packet: TelemetryPacket | None = None
        self.notifier = Notifier()
        self.notifier.url = settings.webhook_url
        self.notifier.enabled = settings.enabled_webhook_events()
        self.event_watcher = LiveEventWatcher()
        self.survey = SurfaceSurvey()
        self.engineer = RaceEngineerManager(
            enabled=settings.race_engineer,
            verbosity=settings.race_engineer_verbosity,
            categories=settings.enabled_callout_categories(),
            units=settings.race_engineer_units,
        )
        # Authored corners outrank detection, and the bundles they live in are
        # this class's business, not the engineer's (#48).
        self.engineer.corner_source = self.authored_corners
        # client_id of the browser currently allowed to speak, and the last
        # one that held the claim (restored when the same page reconnects).
        self._active_voice_client = ""
        self._last_voice_client = ""
        self._session_best_ms: int | None = None
        self._prev_best_ms: int | None = None
        # Lap number the delta reference came from, so a later partial lap
        # doesn't discard a perfectly good reference.
        self._best_ref_lap: int | None = None
        # (dist, t) trace of the session-best lap — the reference for the
        # live delta. Safe to hold by reference: the processor allocates a
        # fresh sample store at every lap boundary.
        self._best_ref: Samples | None = None
        self._clients: dict[WebSocket, _ClientStream] = {}
        self._last_ws_send = 0.0
        self._ws_interval = 1.0 / settings.ws_rate
        # Authored corners per circuit slug, read out of the track bundle
        # (#48). Cached because the bundle is a multi-megabyte document and
        # the corners in it are a few hundred bytes — parsing the whole thing
        # at every lap boundary to re-read seventeen apexes would be absurd.
        # Invalidated when the refine view saves.
        self._authored: dict[str, list[dict[str, Any]]] = {}

    def authored_corners(self, track: str) -> list[dict[str, Any]]:
        """A circuit's hand-labelled corners, if it has any. Blocking: the
        callers run it on a worker thread."""
        if not track:
            return []
        key = track_bundle.slugify(track)
        corners = self._authored.get(key)
        if corners is None:
            doc = track_bundle.load(self.settings.db_path.parent, track)
            corners = doc["corners"] if doc else []
            self._authored[key] = corners
        return corners

    def invalidate_authored_corners(self, track: str) -> None:
        self._authored.pop(track_bundle.slugify(track), None)

    async def start(self) -> None:
        await self.source.start()

    async def stop(self) -> None:
        await self.source.stop()
        self.survey.stop()
        tasks = [c.task for c in self._clients.values() if c.task]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._clients.clear()

    async def switch_source(self, kind: str) -> None:
        """Swap between the UDP and simulated source at runtime."""
        await self.source.stop()
        self.settings.source = kind
        if kind == "sim":
            self.source = SimTelemetrySource(
                self._on_packet, scenario_for(self.settings.sim_scenario)
            )
        else:
            self.source = UdpTelemetrySource(self.settings, self._on_packet)
        await self.source.start()
        log.info("telemetry source switched to %s", kind)
        self._publish({"type": "status", "data": await self.status()})

    async def set_ps_ip(self, ip: str) -> None:
        self.settings.ps_ip = ip
        if isinstance(self.source, UdpTelemetrySource):
            self.source.reset_discovery()
        log.info("console IP set to %s", ip or "<auto-discover>")
        self._publish({"type": "status", "data": await self.status()})

    async def restart_source(self) -> None:
        await self.switch_source(self.settings.source)

    # --- pipeline callbacks -------------------------------------------------

    @property
    def engineer_active(self) -> bool:
        """Whether callout detection should run at all.

        Off unless a browser has actually enabled Race Engineer: a user who
        never touches the feature pays nothing for it, and detectors start
        from a clean baseline when someone does enable it mid-session.
        """
        return self.engineer.enabled and any(
            c.voice_enabled for c in self._clients.values()
        )

    async def _on_packet(self, p: TelemetryPacket) -> None:
        self.latest_packet = p
        if self.recording:
            await self.processor.feed(p)
        for event in self.event_watcher.feed(p):
            self._notify_live_event(event, p)
        if self.survey.active:
            # 60 Hz on purpose: transitions are single-tick events that the
            # downsampled live stream below would miss.
            record = self.survey.feed(p)
            if record is not None:
                self._publish({"type": "survey", "data": record})
        if self.engineer_active:
            self._publish_callouts(self.engineer.on_packet(p))
        now = time.monotonic()
        if now - self._last_ws_send >= self._ws_interval:
            self._last_ws_send = now
            self._publish({"type": "telemetry", "data": self._live_frame(p)})

    def _notify_live_event(self, event: LiveEvent, p: TelemetryPacket) -> None:
        # Voice reuses the watcher's debounced events rather than running a
        # second position detector that could disagree with the webhooks.
        if self.engineer_active:
            self._publish_callouts(self.engineer.on_live_event(event))
        car = self.cars.name(p.car_id)
        if event.kind == "overtake":
            self.notifier.overtake(
                event.position, event.previous_position, event.total_positions,
                car, self.track_name,
            )
        elif event.kind == "position_lost":
            self.notifier.position_lost(
                event.position, event.previous_position, event.total_positions,
                car, self.track_name,
            )
        elif event.kind == "off_road":
            self.notifier.off_road(p.current_lap, car, self.track_name)

    async def _on_session(self, info: SessionInfo) -> None:
        self.event_watcher.reset()
        await self._close_previous_session()
        self.session_id = await self.repo.create_session(info, self.cars.name(info.car_id))
        # A survey spanning a restart keeps labeling its records with the
        # session its transitions actually belong to. An auto-filled track
        # label goes back to unknown too — the new session may be a
        # different circuit, and identification will re-label it; a label
        # the user typed is theirs and stays.
        self.survey.session_id = self.session_id
        if self.survey.active and not self.survey.track_locked:
            self.survey.set_track("")
        self.engineer.on_session(info, self.session_id)
        self.track_name = ""
        self._session_best_ms = None
        self._prev_best_ms = None
        self._best_ref = None
        self._best_ref_lap = None
        log.info("new session %s (car %s)", self.session_id, self.cars.name(info.car_id))
        self._publish({"type": "session", "data": await self.status()})

    async def _close_previous_session(self) -> None:
        """Summarize a finished session, or drop it if it never got a lap.

        One aggregate query serves both paths — loading the lap rows would
        drag every samples_json blob out of the DB just for bookkeeping.
        """
        if self.session_id is None:
            return
        stats = await self.repo.session_lap_stats(self.session_id)
        if stats["count"] == 0:
            # Menu visits and race restarts open sessions that never get a
            # lap; drop them so they don't pile up.
            await self.repo.delete_session(self.session_id)
            log.info("dropped empty session %s", self.session_id)
            return
        self.notifier.session_summary(
            car=self.cars.name(stats["car_id"]),
            track=self.track_name,
            lap_count=stats["count"],
            best_ms=stats["best_ms"],
            fuel_used=stats["fuel_used"],
        )

    async def _on_lap(self, lap: CompletedLap) -> None:
        if self.session_id is None:
            return
        lap_id = await self.repo.save_lap(self.session_id, lap)
        log.info("lap %d saved (%d ms, id=%d)", lap.number, lap.time_ms, lap_id)

        # Which laps covered the whole track is re-judged on every lap (see
        # LapProcessor._apply_span_guard), so the verdict can change for laps
        # already saved. Bring the rows back in line, or the DB aggregates
        # (Sessions view, session-summary webhook) keep the old answer.
        if lap.invalidated_best:
            await self.repo.mark_session_laps_partial(
                self.session_id, lap.partial_lap_numbers
            )
            # Only drop the delta reference when the lap that PROVIDED it
            # turned out partial. A pit out-lap later in the stint says
            # nothing about the good lap the reference came from.
            if self._best_ref_lap in lap.partial_lap_numbers:
                self._best_ref = None
                self._best_ref_lap = None

        # The best BEFORE this lap: the live "Δ best" and the personal-best
        # check both compare against it (a best that already includes this lap
        # can never show an improvement).
        before = lap.session_best_before_ms
        self._prev_best_ms = before if before > 0 else None

        if lap.counts_for_best:
            if before > 0 and lap.time_ms < before:
                self.notifier.personal_best(
                    lap.time_ms, before, lap.number, self.cars.name(lap.car_id),
                    self.track_name,
                )
            if before <= 0 or lap.time_ms < before:
                self._best_ref = {"dist": lap.samples["dist"], "t": lap.samples["t"]}
                self._best_ref_lap = lap.number
        # The processor owns the session best: dropping a partial lap promotes
        # the fastest remaining real lap rather than blanking it.
        session = self.processor.session
        best = session.best_lap_time_ms if session else -1
        self._session_best_ms = best if best > 0 else None

        # Track auto-identification from the first completed lap's geometry
        if not self.track_name:
            await self._identify_track(lap)

        if self.engineer_active:
            self.engineer.ctx.track_name = self.track_name
            self._publish_callouts(self.engineer.on_lap(lap))
            # Corner detection for coaching runs off the event loop; a lap
            # boundary is the only place it can afford to happen.
            await self.engineer.refresh_reference()

        summary = {
            "id": lap_id,
            "session_id": self.session_id,
            "number": lap.number,
            "time_ms": lap.time_ms,
            "car_id": lap.car_id,
            "car_category": lap.car_category,
            "counts_for_best": lap.counts_for_best,
            "off_track_count": lap.off_track_count,
            "clean_lap": lap.clean_lap,
            "car_name": self.cars.name(lap.car_id),
            "fuel_consumed": round(lap.fuel_consumed, 3),
            "full_throttle_pct": round(lap.full_throttle_pct, 1),
            "full_brake_pct": round(lap.full_brake_pct, 1),
            "coasting_pct": round(lap.coasting_pct, 1),
            "tire_spin_pct": round(lap.tire_spin_pct, 1),
            "max_speed": round(lap.max_speed, 1),
            "min_body_height": round(lap.min_body_height, 1),
            "tcs_active_pct": round(lap.tcs_active_pct, 1),
            "asm_active_pct": round(lap.asm_active_pct, 1),
            "event_counts": _count_events(lap.events),
        }
        self._publish({"type": "lap", "data": summary})

    async def _identify_track(self, lap: CompletedLap) -> None:
        sig = signature_from_samples(lap.samples)
        if sig is None or self.session_id is None:
            return
        # A signature a human created outranks anything inferred; the survey
        # bundles answer when there is no signature, which is the normal state
        # for someone who has surveyed circuits but never named one (#41).
        name = await self.repo.find_track(sig)
        source = "signature"
        if not name:
            hit = await asyncio.to_thread(
                tracks.identify_from_bundles, self.settings.db_path.parent, lap.samples
            )
            if hit is not None:
                name, cover = hit
                source = f"survey bundle, {cover:.0%} of the lap on mapped road"
        if name:
            self.track_name = name
            await self.repo.set_session_track(self.session_id, name)
            # A survey started before the circuit was known picks the label
            # up now; an explicit user-picked label is never overwritten.
            if self.survey.active and not self.survey.track_locked:
                self.survey.set_track(name)
            log.info("track identified: %s (%s)", name, source)
            self._publish({"type": "session", "data": await self.status()})

    # --- live stream --------------------------------------------------------

    def _live_frame(self, p: TelemetryPacket) -> dict[str, Any]:
        """Compact frame for the live view (~30 Hz)."""
        session = self.processor.session
        live = self.processor.live_lap_samples
        elapsed_ms = round(live["t"][-1] * 1000) if live["t"] else -1
        delta_ms: float | None = None
        if self._best_ref is not None and live["t"] and p.is_on_track and not p.is_paused:
            delta_ms = time_delta_at(live["dist"][-1], live["t"][-1], self._best_ref)
            if delta_ms is not None:
                delta_ms = round(delta_ms)
        return {
            "on_track": p.is_on_track,
            "paused": p.is_paused,
            "speed_kmh": round(p.speed_kmh, 1),
            "rpm": round(p.engine_rpm),
            "rpm_alert": p.rpm_alert_max,
            "gear": p.current_gear,
            "suggested_gear": p.suggested_gear,
            "throttle": round(p.throttle_pct, 1),
            "brake": round(p.brake_pct, 1),
            "boost": round(p.boost, 2),
            "fuel_level": round(p.fuel_level, 2),
            "fuel_capacity": p.fuel_capacity,
            "current_lap": p.current_lap,
            "total_laps": p.total_laps,
            "best_lap_ms": p.best_lap_time_ms,
            "last_lap_ms": p.last_lap_time_ms,
            "position": p.race_position,
            "total_positions": p.total_positions,
            "tire_temps": [
                round(p.tire_temp_fl, 1), round(p.tire_temp_fr, 1),
                round(p.tire_temp_rl, 1), round(p.tire_temp_rr, 1),
            ],
            "tire_slip": round(p.tire_slip_ratio, 3),
            "water_temp": round(p.water_temp, 1),
            "oil_temp": round(p.oil_temp, 1),
            "oil_pressure": round(p.oil_pressure, 2),
            "aids": p.aids_bits,
            "surface": encode_surface(p.surface_types),
            "car_id": p.car_id,
            "car_name": self.cars.name(p.car_id),
            "car_category": p.car_category or "",
            "session_best_ms": session.best_lap_time_ms if session else -1,
            "prev_best_ms": self._prev_best_ms if self._prev_best_ms is not None else -1,
            "delta_ms": delta_ms,
            "lap_elapsed_ms": elapsed_ms,
            "pos_x": round(p.position_x, 2),
            "pos_z": round(p.position_z, 2),
            "tod_ms": p.day_progression_ms,
            "track_name": self.track_name,
        }

    async def status(self) -> dict[str, Any]:
        return {
            "source": self.settings.source,
            "recording": self.recording,
            "session_id": self.session_id,
            "track_name": self.track_name,
            # 60 Hz frames the console numbered but we never received
            # (distinct from packets_dropped: queue overflow on our side).
            "frames_dropped": self.processor.dropped_frames,
            **self.source.stats,
        }

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def register(self, ws: WebSocket) -> None:
        client = _ClientStream(ws=ws)
        self._clients[ws] = client
        # Start the sender before enqueueing so the initial status can't be
        # orphaned in a task-less queue.
        client.task = asyncio.create_task(self._client_sender(client))
        client.events.put_nowait(json.dumps({"type": "status", "data": await self.status()}))
        # Current Race Engineer state, never past callouts: a reconnecting
        # page must not hear what it missed while it was away.
        client.events.put_nowait(
            json.dumps({"type": "race_engineer_status", "data": self.engineer_status()})
        )
        client.wakeup.set()

    async def unregister(self, ws: WebSocket) -> None:
        client = self._clients.pop(ws, None)
        if client and client.task:
            client.task.cancel()
            await asyncio.gather(client.task, return_exceptions=True)
        if client and client.client_id and client.client_id == self._active_voice_client:
            # The speaker went away (tab closed, refresh, network drop). Clear
            # the claim but remember it, so the same page gets it back when it
            # comes home instead of the room going silent.
            self._active_voice_client = ""
            log.info("voice output released: client %s disconnected", client.client_id)
            self._publish_voice_status()

    # --- race engineer voice protocol ---------------------------------------

    # Only driver-facing pages may speak. An OBS overlay or the admin page
    # showing callouts must never start talking over the dashboard.
    VOICE_PAGES = frozenset({"dash", "engineer"})

    def set_client_capabilities(
        self,
        ws: WebSocket,
        client_id: str,
        page: str,
        voice_supported: bool,
        voice_enabled: bool,
    ) -> None:
        client = self._clients.get(ws)
        if client is None or not client_id:
            return
        client.client_id = client_id
        client.page = page
        client.voice_supported = voice_supported
        client.voice_enabled = voice_enabled
        # A refresh reconnects the same client_id; restoring its claim keeps
        # F5 from silently demoting the driver's own dashboard.
        if (
            voice_enabled
            and not self._active_voice_client
            and client_id == self._last_voice_client
            and page in self.VOICE_PAGES
        ):
            self._active_voice_client = client_id
        self._publish_voice_status()

    def claim_voice_output(self, ws: WebSocket, client_id: str) -> None:
        client = self._clients.get(ws)
        # The claim must come from the socket that registered the id: one page
        # cannot hand the microphone to another.
        if client is None or not client_id or client.client_id != client_id:
            return
        if client.page not in self.VOICE_PAGES:
            log.info("voice claim from %s page ignored (%s)", client.page or "?", client_id)
            return
        self._active_voice_client = client_id
        self._last_voice_client = client_id
        log.info("voice output claimed by %s (%s)", client_id, client.page)
        self._publish_voice_status()

    def release_voice_output(self, ws: WebSocket, client_id: str) -> None:
        client = self._clients.get(ws)
        if client is None or client.client_id != client_id:
            return
        if self._active_voice_client == client_id:
            self._active_voice_client = ""
            log.info("voice output released by %s", client_id)
            self._publish_voice_status()

    def record_callout_ack(
        self, client_id: str, callout_id: str, status: str, reason: str = ""
    ) -> None:
        """Diagnostics only — acks never gate anything in the pipeline."""
        self.engineer.record_ack(status, reason)
        if status == "speech_error":
            # Worth a warning: the driver is hearing nothing, and the reason
            # the browser reported is the only clue to why.
            log.warning("callout %s not spoken: %s", callout_id, reason or "no reason given")
        else:
            log.debug("callout %s %s (client %s)", callout_id, status, client_id)

    @property
    def voice_clients(self) -> list[dict[str, Any]]:
        return [
            {
                "client_id": c.client_id,
                "page": c.page,
                "voice_supported": c.voice_supported,
                "voice_enabled": c.voice_enabled,
                "is_active_speaker": bool(c.client_id)
                and c.client_id == self._active_voice_client,
            }
            for c in self._clients.values()
            if c.client_id
        ]

    def engineer_status(self) -> dict[str, Any]:
        return {
            "enabled": self.engineer.enabled,
            "active": self.engineer_active,
            "verbosity": self.engineer.verbosity,
            # What the server will actually send. Clients use this to grey out
            # categories they could never receive.
            "categories": [
                c for c in CATEGORIES if c in self.engineer.effective_categories
            ],
            # Why coaching is quiet: it waits for enough laps to agree on the
            # track's distance before comparing one lap against another.
            "coaching_ready": (
                self.engineer.ctx.span_confirmed and self.engineer.ctx.reference is not None
            ),
            "active_client_id": self._active_voice_client,
            "clients": self.voice_clients,
        }

    def _publish_voice_status(self) -> None:
        self._publish({"type": "voice_output_status", "data": {
            "active_client_id": self._active_voice_client,
        }})
        self.publish_engineer_status()

    def publish_engineer_status(self) -> None:
        self._publish({"type": "race_engineer_status", "data": self.engineer_status()})

    def publish_callout(self, callout: VoiceCallout) -> None:
        """Send one callout (used by the admin test-callout endpoint)."""
        self._publish_callouts([callout])

    def _publish_callouts(self, callouts: list[VoiceCallout]) -> None:
        """Send callouts on the event lane — a dropped warning is a bug.

        Staleness is handled by each callout's own expiry, not by dropping it
        from a queue: the browser decides, on arrival, whether it is still
        worth speaking.
        """
        for callout in callouts:
            self._publish({"type": "voice_callout", "data": callout.to_dict()})

    def _publish(self, message: dict[str, Any]) -> None:
        """Queue a message for every client without awaiting any client I/O.

        The capture pipeline calls this at 60 Hz; a browser that stops reading
        only loses its own telemetry frames (latest wins) — it can never
        backpressure lap detection or the other clients.
        """
        if not self._clients:
            return
        text = json.dumps(message)
        kind = message["type"]
        for client in list(self._clients.values()):
            if kind == "telemetry":
                client.frame = text
            else:
                try:
                    client.events.put_nowait(text)
                except asyncio.QueueFull:
                    # Survey transitions can burst at 60 Hz (kerb chatter)
                    # and are recoverable from the status/edges endpoints —
                    # drop them for a slow client rather than shrinking the
                    # lap/session lane's minutes-long overflow budget to
                    # seconds and disconnecting a dashboard mid-session.
                    if kind == "survey":
                        continue
                    # Unreadable for minutes — disconnect rather than lose
                    # a lap/session event silently.
                    self._drop_client(client)
                    continue
            client.wakeup.set()

    def _drop_client(self, client: _ClientStream) -> None:
        self._clients.pop(client.ws, None)
        if client.task:
            client.task.cancel()
        # Close the socket too, or the /ws/live receive loop keeps an open,
        # no-longer-tracked connection alive indefinitely.
        asyncio.get_running_loop().create_task(_close_ws(client.ws))

    async def _client_sender(self, client: _ClientStream) -> None:
        """Drain one client's lanes; events always go out before frames."""
        try:
            while True:
                await client.wakeup.wait()
                client.wakeup.clear()
                while not client.events.empty():
                    await client.ws.send_text(client.events.get_nowait())
                if client.frame is not None:
                    frame, client.frame = client.frame, None
                    await client.ws.send_text(frame)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Send failed — the WS endpoint's finally-unregister cleans up;
            # also remove eagerly and close in case the receive side is
            # still alive on a half-broken connection.
            self._clients.pop(client.ws, None)
            await _close_ws(client.ws)

    # --- controls -----------------------------------------------------------

    async def log_lap_now(self) -> dict[str, Any] | None:
        """Persist the in-progress lap without waiting for the finish line."""
        samples = self.processor.live_lap_samples
        if self.session_id is None or not samples["t"]:
            return None
        lap = CompletedLap(
            number=self.processor._current_lap,
            time_ms=int(samples["t"][-1] * 1000),
            finished_at=datetime.now(UTC).isoformat(),
            car_id=self.latest_packet.car_id if self.latest_packet else 0,
            samples={k: list(v) for k, v in samples.items()},
            fuel_start=samples["fuel"][0],
            fuel_end=samples["fuel"][-1],
        )
        lap.compute_metrics()
        lap_id = await self.repo.save_lap(self.session_id, lap)
        return {"id": lap_id, "number": lap.number, "time_ms": lap.time_ms}
