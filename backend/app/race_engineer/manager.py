"""The Race Engineer event manager.

Detectors decide *what happened*; this class decides whether the driver hears
about it. It owns the shared context, applies category/verbosity filtering,
cooldowns, deduplication and priority, stamps expiry, and keeps the counters
the admin diagnostics panel shows.

Wired from `TelemetryService` exactly like `LiveEventWatcher` and `Notifier`:
`on_packet` runs at 60 Hz and must stay cheap, `on_lap` does the per-lap work,
and the only genuinely slow step (corner detection on the reference lap) runs
off the event loop in `refresh_reference`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from app.models import TelemetryPacket
from app.processing.analysis import corners_for_lap
from app.processing.laps import CompletedLap, SessionInfo
from app.processing.live_events import LiveEvent
from app.race_engineer.detectors import (
    CoachingDetector,
    Detector,
    EngineDetector,
    FuelDetector,
    LapDetector,
    PaceDetector,
    RaceDetector,
    TireDetector,
)
from app.race_engineer.models import (
    CATEGORIES,
    DEFAULT_VERBOSITY,
    SPECS,
    CalloutRequest,
    VoiceCallout,
    categories_for,
)
from app.race_engineer.state import (
    EngineerContext,
    LapOnAxis,
    LapRecord,
    PacketClock,
    place_on_axis,
)
from app.race_engineer.thresholds import COACH_WINDOW_LAPS

log = logging.getLogger(__name__)

# Laps kept for the fuel model and coaching aggregation. Deliberately spans
# sessions: a race restart opens a new session but the stint's consumption is
# still the best information available.
MAX_LAP_HISTORY = 20
# How long a dedupe key blocks a repeat. Long enough to cover a lap, short
# enough that an hour-long stint doesn't accumulate keys forever.
DEDUPE_RETENTION_S = 600.0


class RaceEngineerManager:
    def __init__(
        self,
        enabled: bool = True,
        verbosity: str = DEFAULT_VERBOSITY,
        categories: set[str] | None = None,
        units: str = "metric",
    ) -> None:
        self.enabled = enabled
        self.verbosity = verbosity
        self.categories: set[str] = set(categories) if categories is not None else set(CATEGORIES)
        self.ctx = EngineerContext(units=units)
        self.clock = PacketClock()
        self._race = RaceDetector()
        self._detectors: list[Detector] = [
            LapDetector(),
            PaceDetector(),
            self._race,
            FuelDetector(),
            EngineDetector(),
            TireDetector(),
            CoachingDetector(),
        ]
        self._emitted: dict[str, float] = {}  # dedupe key -> packet-clock time
        self._severity: dict[str, int] = {}  # dedupe key -> highest severity seen
        self._seq = 0
        self._pending_reference: dict[str, list[float]] | None = None
        self._reference_lap: int | None = None
        # Historical reference selection (runtime only).
        # Session Best remains the default.
        self.reference_mode = "session_best"
        self.reference_lap_id: int | None = None
        self.reference_time_ms: int | None = None
        # Preserve the original historical lap if a faster lap is invalidated.
        self._chase_original_lap_id: int | None = None
        self._chase_original_time_ms: int | None = None
        self._chase_original_samples: dict[str, list[float]] | None = None
        # The lap about to be reported, already placed on the reference's
        # axis by prepare_lap: (its samples, where they were placed).
        self._prepared: tuple[dict[str, list[float]], LapOnAxis] | None = None
        self.last_callout: VoiceCallout | None = None
        self.stats: dict[str, int] = {
            "evaluated": 0,
            "emitted": 0,
            "suppressed_category": 0,
            "suppressed_cooldown": 0,
            "suppressed_duplicate": 0,
        }
        self.ack_counts: dict[str, int] = {}
        # Why the browser last failed to speak, in its own words.
        self.last_ack_reason = ""
        # Where a circuit's authored corners come from, when it has any.
        # Injected by TelemetryService (which owns the bundle cache) rather
        # than read here, so the manager keeps knowing nothing about files.
        self.corner_source: Callable[[str], list[dict[str, Any]]] | None = None

    # --- configuration ------------------------------------------------------

    @property
    def effective_categories(self) -> frozenset[str]:
        """Categories actually spoken: the verbosity preset, narrowed by toggles."""
        return frozenset(categories_for(self.verbosity) & self.categories)

    def configure(
        self,
        enabled: bool | None = None,
        verbosity: str | None = None,
        categories: set[str] | None = None,
        units: str | None = None,
    ) -> None:
        if enabled is not None:
            self.enabled = enabled
        if verbosity is not None:
            self.verbosity = verbosity
        if categories is not None:
            self.categories = set(categories)
        if units is not None:
            self.ctx.units = units

    # --- pipeline hooks -----------------------------------------------------

    def on_packet(self, p: TelemetryPacket) -> list[VoiceCallout]:
        self.ctx.now = self.clock.advance(p)
        self.ctx.packet = p
        if p.is_loading:
            # Loading screens stream stale values; nothing said about them
            # could be true by the time it is heard.
            return []
        out: list[VoiceCallout] = []
        for detector in self._detectors:
            for request in detector.on_packet(p, self.ctx):
                callout = self._emit(request)
                if callout is not None:
                    out.append(callout)
        return out

    def on_live_event(self, event: LiveEvent) -> list[VoiceCallout]:
        """Position changes, from the already-debounced live event watcher."""
        out: list[VoiceCallout] = []
        for request in self._race.on_live_event(event, self.ctx):
            callout = self._emit(request)
            if callout is not None:
                out.append(callout)
        return out

    async def prepare_lap(self, lap: CompletedLap) -> None:
        """Place the lap on the coaching reference's axis before `on_lap`.

        Coaching compares laps by where they were, not how far they had
        gone, which means walking the lap along the reference's path — tens
        of milliseconds for a full lap, and several times that on the small
        boards this runs on. `on_lap` is synchronous and on the event loop,
        so the walk happens here, on a worker thread, and `on_lap` finds it
        done. Skipping this call costs nothing but that: the detector places
        the lap itself when it has not been.
        """
        self._prepared = None
        ref = self.ctx.reference
        if ref is None or not lap.counts_for_best or not lap.span_confirmed:
            return
        if not {"coaching", "chassis"} & self.effective_categories:
            return
        corners = self.ctx.corners

        def _place() -> LapOnAxis:
            view = place_on_axis(lap.samples, lap.events, ref)
            if corners:
                view.measures(corners)
            return view

        try:
            self._prepared = (lap.samples, await asyncio.to_thread(_place))
        except Exception:  # noqa: BLE001 - coaching is optional, never fatal
            log.warning("could not line the lap up with the reference", exc_info=True)

    def on_lap(self, lap: CompletedLap) -> list[VoiceCallout]:
        record = LapRecord.from_lap(lap)
        record.session_seq = self.ctx.session_seq
        prepared, self._prepared = self._prepared, None
        if prepared is not None and prepared[0] is lap.samples:
            record.axis = prepared[1]
        self.ctx.laps.insert(0, record)
        del self.ctx.laps[MAX_LAP_HISTORY:]
        self.ctx.span_confirmed = lap.span_confirmed

        # Which laps covered the whole track is re-judged as laps arrive, so
        # the history is re-flagged too — a lap that turns out partial must
        # leave the fuel model and the coaching comparisons, not just the
        # session best. By the processor's full answer, not the heuristic's:
        # a lap the user ruled in or out keeps that ruling here too (#74).
        if lap.invalidated_best:
            self._reflag(set(lap.excluded_lap_numbers))

        # Detectors compare against the best BEFORE this lap; a best that
        # already includes it can never show an improvement.
        before = self._best_before(lap)
        self.ctx.prev_best_ms = before
        out: list[VoiceCallout] = []
        for detector in self._detectors:
            for request in detector.on_lap(record, self.ctx):
                callout = self._emit(request)
                if callout is not None:
                    out.append(callout)

        candidates = [t for t in (before, record.time_ms if record.counts_for_best else None)
                      if t is not None]
        self.ctx.best_lap_ms = min(candidates) if candidates else None
        if record.counts_for_best:
            if self.reference_mode == "chase":
                # Only a faster valid lap can replace the active benchmark.
                if (
                    self.reference_time_ms is not None
                    and record.time_ms < self.reference_time_ms
                ):
                    self._pending_reference = record.samples
                    self._reference_lap = record.number
                    self.reference_lap_id = None
                    self.reference_time_ms = record.time_ms

            elif self.ctx.best_lap_ms == record.time_ms:
                # Original Session Best behaviour.
                self._pending_reference = record.samples
                self._reference_lap = record.number
        return out

    def apply_best_override(self, excluded: set[int], best_ms: int | None) -> None:
        """A lap of the current session was ruled in or out by hand (#74).

        `excluded` is every lap number of the session that no longer counts
        and `best_ms` the session best that leaves — both the processor's,
        which sees the whole session where this history is capped. The
        coaching reference follows the best: an excluded reference gives way
        to the fastest lap still counting, and a lap ruled back in takes over
        if it is quicker. Call refresh_reference afterwards to adopt it.
        """
        self._reflag(excluded)
        self.ctx.best_lap_ms = best_ms

        # Recalculate the active benchmark after a manual lap override.
        if self.reference_mode == "chase":
            # Start with the originally selected historical benchmark.
            reference_time = self._chase_original_time_ms
            reference_samples = self._chase_original_samples
            reference_lap = None
            reference_id = self._chase_original_lap_id

            # Find the fastest valid live lap that beats it.
            for rec in self.ctx.laps:
                if (
                    rec.session_seq == self.ctx.session_seq
                    and rec.counts_for_best
                    and rec.samples.get("dist")
                    and reference_time is not None
                    and rec.time_ms < reference_time
                ):
                    reference_time = rec.time_ms
                    reference_samples = rec.samples
                    reference_lap = rec.number
                    reference_id = None

            if reference_samples is not None:
                self._pending_reference = reference_samples
                self._reference_lap = reference_lap
                self.reference_lap_id = reference_id
                self.reference_time_ms = reference_time

            return

        best = min(
            (
                rec
                for rec in self.ctx.laps
                if rec.session_seq == self.ctx.session_seq
                and rec.counts_for_best
                and rec.samples.get("dist")
            ),
            key=lambda rec: rec.time_ms,
            default=None,
        )
        if best is not None and best.number != self._reference_lap:
            self._pending_reference = best.samples
            self._reference_lap = best.number

    def _reflag(self, excluded: set[int]) -> None:
        """Bring this session's lap history in line with which laps count."""
        for rec in self.ctx.laps:
            if rec.session_seq == self.ctx.session_seq:
                rec.counts_for_best = rec.number not in excluded
        if self.reference_mode != "chase" and self._reference_lap in excluded:
            self.ctx.reference = None
            self.ctx.corners = []
            self._reference_lap = None

    def _best_before(self, lap: CompletedLap) -> int | None:
        """Best of the session's full laps, excluding the one just finished.

        The lap processor works this out across the whole session, which is
        what makes dropping a partial lap promote the fastest lap still
        standing rather than blanking the best. The lap history here is
        capped, so it is only the fallback (and what unit tests exercise).
        """
        if lap.session_best_before_ms > 0:
            return lap.session_best_before_ms
        times = [
            rec.time_ms
            for rec in self.ctx.laps[1:]  # [0] is the lap just recorded
            if rec.session_seq == self.ctx.session_seq and rec.counts_for_best
        ]
        return min(times) if times else None

    def on_session(self, info: SessionInfo, session_id: int | None = None) -> None:
        """Race restart, car change or a new session: forget per-race state.

        Lap history survives on purpose (see MAX_LAP_HISTORY) — everything
        else, including the reference lap and every dedupe key, does not.
        """
        self.ctx.session_seq += 1
        self.ctx.session_id = session_id
        self.ctx.car_id = info.car_id
        self.ctx.best_lap_ms = None
        self.ctx.prev_best_ms = None
        self.ctx.reference = None
        self.ctx.corners = []
        self.ctx.span_confirmed = False
        self._pending_reference = None
        self._reference_lap = None

        # A new GT7 session always starts in Session Best mode.
        self.reference_mode = "session_best"
        self.reference_lap_id = None
        self.reference_time_ms = None
        self._chase_original_lap_id = None
        self._chase_original_time_ms = None
        self._chase_original_samples = None

        self._prepared = None
        self._emitted.clear()
        self._severity.clear()
        self.clock.reset()
        for detector in self._detectors:
            detector.reset(self.ctx)
    def select_chase_reference(
        self,
        lap_id: int,
        time_ms: int,
        samples: dict[str, list[float]],
    ) -> None:
        """Select a stored lap as the historical coaching benchmark."""
        if time_ms <= 0 or not samples.get("dist"):
            raise ValueError("Historical lap has no usable telemetry")

        self.reference_mode = "chase"
        self.reference_lap_id = lap_id
        self.reference_time_ms = time_ms

        # Keep the original benchmark for manual invalidation fallback.
        self._chase_original_lap_id = lap_id
        self._chase_original_time_ms = time_ms
        self._chase_original_samples = samples

        # Use the existing reference-refresh pipeline.
        self._pending_reference = samples
        self._reference_lap = None

        # A valid live lap may already be faster than the selected history.
        best_live = min(
            (
                rec for rec in self.ctx.laps
                if rec.session_seq == self.ctx.session_seq
                and rec.counts_for_best
                and rec.samples.get("dist")
                and rec.time_ms < time_ms
            ),
            key=lambda rec: rec.time_ms,
            default=None,
        )

        if best_live is not None:
            self._pending_reference = best_live.samples
            self._reference_lap = best_live.number
            self.reference_lap_id = None
            self.reference_time_ms = best_live.time_ms

    def select_session_best_reference(self) -> None:
        """Return to automatic session-best reference selection."""
        self.reference_mode = "session_best"
        self.reference_lap_id = None
        self.reference_time_ms = None

        self._chase_original_lap_id = None
        self._chase_original_time_ms = None
        self._chase_original_samples = None

        best = min(
            (
                rec
                for rec in self.ctx.laps
                if rec.session_seq == self.ctx.session_seq
                and rec.counts_for_best
                and rec.samples.get("dist")
            ),
            key=lambda rec: rec.time_ms,
            default=None,
        )

        if best is not None:
            self._pending_reference = best.samples
            self._reference_lap = best.number
        else:
            self._pending_reference = None
            self._reference_lap = None
            self.ctx.reference = None
            self.ctx.corners = []


    async def refresh_reference(self) -> None:
        """Adopt the new session-best lap as the coaching reference.

        Corner detection costs 10-90 ms — far too much for the packet path, so
        it runs on a worker thread at a lap boundary and only when a callout
        that names corners is actually enabled. The circuit's authored corners
        (#48) are fetched on the same thread: reading them means parsing the
        track bundle the first time, and they turn "the next corner" into
        "turn four" — and, more importantly, keep the number meaning the same
        corner from one lap to the next.
        """
        samples = self._pending_reference
        self._pending_reference = None
        if samples is None:
            return
        self.ctx.reference = samples
        if not {"coaching", "chassis"} & self.effective_categories:
            self.ctx.corners = []
            return
        track = self.ctx.track_name
        source = self.corner_source
        # The laps the next comparison will read were placed on the OLD
        # reference's axis. Placing them on the new one belongs on the same
        # thread as the corner detection, for the same reason.
        recent = [
            rec
            for rec in self.ctx.laps[:COACH_WINDOW_LAPS]
            if rec.session_seq == self.ctx.session_seq
        ]

        def _corners() -> tuple[list[dict[str, Any]], list[LapOnAxis]]:
            authored = source(track) if source is not None else []
            corners = corners_for_lap(samples, authored)
            placed = [place_on_axis(rec.samples, rec.events, samples) for rec in recent]
            for view in placed:
                view.measures(corners)
            return corners, placed

        try:
            self.ctx.corners, placed = await asyncio.to_thread(_corners)
        except Exception:  # noqa: BLE001 - coaching is optional, never fatal
            log.warning("corner detection failed; coaching calls stay generic", exc_info=True)
            self.ctx.corners = []
            return
        for rec, view in zip(recent, placed, strict=True):
            rec.axis = view

    # --- emission -----------------------------------------------------------

    def _emit(self, request: CalloutRequest) -> VoiceCallout | None:
        spec = SPECS.get(request.event_type)
        if spec is None:  # pragma: no cover - a detector typo, not a runtime path
            log.warning("unknown callout type %s", request.event_type)
            return None
        self.stats["evaluated"] += 1
        if spec.category not in self.effective_categories:
            self.stats["suppressed_category"] += 1
            return None

        key = request.dedupe_key or request.event_type
        now = self.ctx.now
        last = self._emitted.get(key)
        escalated = request.severity > self._severity.get(key, 0)
        if last is not None and not escalated:
            if spec.cooldown_s <= 0:
                # No cooldown means the key is unique per occurrence (lap
                # number, position, session): a repeat is a duplicate.
                self.stats["suppressed_duplicate"] += 1
                return None
            if now - last < spec.cooldown_s:
                self.stats["suppressed_cooldown"] += 1
                return None

        self._emitted[key] = now
        self._severity[key] = max(self._severity.get(key, 0), request.severity)
        self._prune(now)

        self._seq += 1
        created = int(time.time() * 1000)
        callout = VoiceCallout(
            id=f"{request.event_type}-{self.ctx.session_seq}-{self._seq}",
            event_type=request.event_type,
            text=request.text,
            category=spec.category,
            priority=spec.priority,
            created_at_ms=created,
            expires_at_ms=created + spec.ttl_ms,
            ttl_ms=spec.ttl_ms,
            interrupt=spec.interrupt,
            dedupe_key=key,
            message_key=request.message_key,
            message_args=request.message_args,
            metadata=request.metadata,
        )
        self.stats["emitted"] += 1
        self.last_callout = callout
        log.debug("callout %s: %s", callout.event_type, callout.text)
        return callout

    def _prune(self, now: float) -> None:
        if len(self._emitted) < 200:
            return
        stale = [k for k, t in self._emitted.items() if now - t > DEDUPE_RETENTION_S]
        for k in stale:
            self._emitted.pop(k, None)
            self._severity.pop(k, None)

    # --- diagnostics --------------------------------------------------------

    def test_callout(self, event_type: str, text: str) -> VoiceCallout:
        """Build a callout for the admin test button, bypassing every gate."""
        spec = SPECS.get(event_type, SPECS["test"])
        self._seq += 1
        created = int(time.time() * 1000)
        callout = VoiceCallout(
            id=f"test-{self.ctx.session_seq}-{self._seq}",
            event_type=event_type,
            text=text,
            category=spec.category,
            priority=spec.priority,
            created_at_ms=created,
            expires_at_ms=created + spec.ttl_ms,
            ttl_ms=spec.ttl_ms,
            interrupt=spec.interrupt,
            message_key="system.test",
            message_args={"event_type": event_type},
            metadata={"test": True},
        )
        self.stats["emitted"] += 1
        self.last_callout = callout
        return callout

    def record_ack(self, status: str, reason: str = "") -> None:
        self.ack_counts[status] = self.ack_counts.get(status, 0) + 1
        if reason:
            self.last_ack_reason = reason

    def diagnostics(self) -> dict[str, Any]:
        last = self.last_callout
        return {
            "enabled": self.enabled,
            "verbosity": self.verbosity,
            "units": self.ctx.units,
            "categories": [c for c in CATEGORIES if c in self.categories],
            "effective_categories": [c for c in CATEGORIES if c in self.effective_categories],
            "session_id": self.ctx.session_id,
            "lap_history": len(self.ctx.laps),
            "best_lap_ms": self.ctx.best_lap_ms,
            "corners": len(self.ctx.corners),
            "span_confirmed": self.ctx.span_confirmed,
            "has_reference": self.ctx.reference is not None,
            "stats": dict(self.stats),
            "acks": dict(self.ack_counts),
            "last_ack_reason": self.last_ack_reason,
            "last_callout": last.to_dict() if last else None,
        }
