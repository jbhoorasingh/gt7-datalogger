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
from app.processing.sectors import Boundary
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
from app.race_engineer.sectors import (
    LiveSectorTracker,
    SectorCrossing,
    reference_sector_directions,
    reference_sector_splits,
    validated_sector_boundaries,
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
        self.sector_voice_enabled = False
        self.sector_voice_decimals = 3
        self.sector_voice_rounding = "round"
        self.sector_voice_content = "delta"
        self.sector_voice_comparison = "individual"
        self._sector_tracker = LiveSectorTracker()
        self._sector_reference_generation = 0
        self._sector_reference_splits_ms: list[float] | None = None
        self._pending_reference: dict[str, list[float]] | None = None
        self._reference_lap: int | None = None
        # What coaching compares against: the session's fastest counting lap
        # ("session_best", the default), or a stored lap the driver chose to
        # chase ("chase"), which a quicker lap of their own then takes over
        # from. Runtime only — nothing about it is saved.
        self.reference_mode = "session_best"
        # The lap being chased: its database id (None for one driven since,
        # which the engineer knows only by its samples) and its time.
        self.reference_lap_id: int | None = None
        self.reference_time_ms: int | None = None
        # What a chase falls back to when the lap that took over stops
        # counting: the chosen lap, or after a restart whatever was being
        # chased when it happened. (lap id, time, samples).
        self._chase_base: tuple[int | None, int, dict[str, list[float]]] | None = None
        self._chase_samples: dict[str, list[float]] | None = None
        # A chase belongs to the car and circuit it was chosen with. A restart
        # in the same car keeps it, parked until the circuit is identified
        # again: the same one resumes it, another ends it.
        self._chase_car_id: int | None = None
        self._chase_track = ""
        self._chase_confirmed = False
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
        self.sector_source: Callable[[str], list[dict[str, Any]]] | None = None

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
        sector_voice_enabled: bool | None = None,
        sector_voice_decimals: int | None = None,
        sector_voice_rounding: str | None = None,
        sector_voice_content: str | None = None,
        sector_voice_comparison: str | None = None,
    ) -> None:
        if enabled is not None:
            self.enabled = enabled
        if verbosity is not None:
            self.verbosity = verbosity
        if categories is not None:
            self.categories = set(categories)
        if units is not None:
            self.ctx.units = units
        if sector_voice_enabled is not None:
            self.sector_voice_enabled = sector_voice_enabled
        if sector_voice_decimals is not None:
            if type(sector_voice_decimals) is not int or sector_voice_decimals not in (1, 2, 3):
                raise ValueError("sector_voice_decimals must be 1, 2 or 3")
            self.sector_voice_decimals = sector_voice_decimals
        if sector_voice_rounding is not None:
            if sector_voice_rounding not in ("round", "truncate"):
                raise ValueError("sector_voice_rounding must be round or truncate")
            self.sector_voice_rounding = sector_voice_rounding
        if sector_voice_content is not None:
            if sector_voice_content not in ("delta", "time", "both"):
                raise ValueError("sector_voice_content must be delta, time or both")
            self.sector_voice_content = sector_voice_content
        if sector_voice_comparison is not None:
            if sector_voice_comparison not in ("individual", "cumulative"):
                raise ValueError("sector_voice_comparison must be individual or cumulative")
            self.sector_voice_comparison = sector_voice_comparison

    def sector_callout(
        self, lap: int, crossing: SectorCrossing
    ) -> VoiceCallout | None:
        """Build a spoken sector comparison through the normal voice policy."""
        from math import isfinite

        from app.race_engineer.formatter import (
            _precise_timing_seconds,
            spoken_sector_decimal,
            spoken_sector_duration,
        )

        splits = self._sector_reference_splits_ms
        if (
            not self.enabled
            or not self.sector_voice_enabled
            or self.ctx.reference is None
            or splits is None
            or len(splits) < 2
            or lap <= 0
            or not 1 <= crossing.sector <= len(splits)
        ):
            return None

        reference_ms = splits[crossing.sector - 1]

        if (
            not isfinite(reference_ms)
            or reference_ms <= 0
            or not isfinite(crossing.time_ms)
            or crossing.time_ms <= 0
            or not isfinite(crossing.cumulative_ms)
            or crossing.cumulative_ms < crossing.time_ms
        ):
            return None

        if self.sector_voice_comparison == "cumulative":
            reference_sectors = splits[:crossing.sector]
            if any(
                not isfinite(ms) or ms <= 0
                for ms in reference_sectors
            ):
                return None
            reference_ms = sum(reference_sectors)
            delta_ms = crossing.cumulative_ms - reference_ms
        else:
            delta_ms = crossing.time_ms - reference_ms
        sector_names = (
            "one", "two", "three", "four", "five",
            "six", "seven", "eight", "nine", "ten",
        )
        sector_name = (
            sector_names[crossing.sector - 1]
            if crossing.sector <= len(sector_names)
            else str(crossing.sector)
        )

        phrases = []

        if self.sector_voice_content in ("time", "both"):
            sector_time = spoken_sector_duration(
                crossing.time_ms,
                self.sector_voice_decimals,
                self.sector_voice_rounding,
            )
            phrases.append(sector_time)

        if self.sector_voice_content in ("delta", "both"):
            # Level is what the driver would hear as zero, not an exact
            # zero: a cumulative reference is a sum of splits, a hair off
            # the crossing it adds up to.
            level = _precise_timing_seconds(
                delta_ms, self.sector_voice_decimals, self.sector_voice_rounding
            ) == 0
            if level:
                phrases.append("level with the reference")
            else:
                if self.sector_voice_comparison == "cumulative":
                    direction = "ahead" if delta_ms < 0 else "behind"
                else:
                    direction = "faster" if delta_ms < 0 else "slower"
                spoken = spoken_sector_decimal(
                    abs(delta_ms) / 1000.0,
                    self.sector_voice_decimals,
                    self.sector_voice_rounding,
                )
                phrases.append(f"{spoken} seconds {direction}")

        text = f"Sector {sector_name}, {', '.join(phrases)}."

        return self._emit(
            CalloutRequest(
                event_type="sector_time",
                text=text,
                message_key="sector.time",
                message_args={
                    "lap": lap,
                    "sector": crossing.sector,
                    "time_ms": crossing.time_ms,
                    "delta_ms": delta_ms,
                },
                dedupe_key=(
                    f"sector_time:{self.ctx.session_seq}:"
                    f"{lap}:{crossing.sector}"
                ),
                metadata={
                    "lap": lap,
                    "sector": crossing.sector,
                    "time_ms": crossing.time_ms,
                    "reference_ms": reference_ms,
                    "delta_ms": delta_ms,
                },
            )
        )

    def observe_sector(
        self,
        lap: int,
        sample: tuple[float, float, float] | None,
    ) -> SectorCrossing | None:
        """Observe live lap timing using the current validated reference."""
        if sample is None:
            self._sector_tracker.interrupt()
            return None

        if (
            not self.enabled
            or self._sector_reference_splits_ms is None
            or self.ctx.reference is None
        ):
            return None
        return self._sector_tracker.observe(lap, sample)

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
            if self.reference_mode == "chase":
                # The lap that took over the chase may be the one that stopped
                # counting.
                self._chase_settle()

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

        # Announce S3 against the reference active during this lap,
        # before a faster completed lap can replace that reference.
        if record.counts_for_best and self.sector_voice_enabled:
            crossing = self._sector_tracker.complete(
                record.number, record.time_ms
            )
            if crossing is not None:
                callout = self.sector_callout(record.number, crossing)
                if callout is not None:
                    out.append(callout)

        candidates = [t for t in (before, record.time_ms if record.counts_for_best else None)
                      if t is not None]
        self.ctx.best_lap_ms = min(candidates) if candidates else None
        if self.reference_mode == "chase":
            # Only a quicker lap that counts takes over from the one chased.
            if (
                self._chase_confirmed
                and record.counts_for_best
                and record.samples.get("dist")
                and self.reference_time_ms is not None
                and record.time_ms < self.reference_time_ms
            ):
                self._chase_adopt(None, record.time_ms, record.samples, record.number)
        elif record.counts_for_best and self.ctx.best_lap_ms == record.time_ms:
            self._invalidate_sector_reference()
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
        if it is quicker. A chase follows the same rule with the chosen lap as
        its floor. Call refresh_reference afterwards to adopt it.
        """
        self._reflag(excluded)
        self.ctx.best_lap_ms = best_ms
        if self.reference_mode == "chase":
            self._chase_settle()
            return
        best = self._session_fastest()
        if best is not None and best.number != self._reference_lap:
            self._invalidate_sector_reference()
            self._pending_reference = best.samples
            self._reference_lap = best.number

    def _session_fastest(self) -> LapRecord | None:
        """This session's fastest lap that counts and can be placed on."""
        return min(
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

    def _reflag(self, excluded: set[int]) -> None:
        """Bring this session's lap history in line with which laps count."""
        for rec in self.ctx.laps:
            if rec.session_seq == self.ctx.session_seq:
                rec.counts_for_best = rec.number not in excluded
        if self._reference_lap in excluded:
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
        else, including the reference lap and every dedupe key, does not. A
        chase survives a restart in the same car (see on_track); a new car
        ends it.
        """
        self.ctx.session_seq += 1
        self.ctx.session_id = session_id
        self.ctx.car_id = info.car_id
        self.ctx.best_lap_ms = None
        self.ctx.prev_best_ms = None
        self.ctx.reference = None
        self.ctx.corners = []
        self.ctx.span_confirmed = False
        self._invalidate_sector_reference()
        self._pending_reference = None
        self._reference_lap = None
        if self.reference_mode == "chase":
            if info.car_id != self._chase_car_id:
                self._end_chase()
            else:
                # Carry on from whatever was being chased when it happened;
                # it is a lap of the previous session now, so it is the floor.
                if self._chase_samples is not None and self.reference_time_ms is not None:
                    self._chase_base = (
                        self.reference_lap_id, self.reference_time_ms, self._chase_samples,
                    )
                self._chase_samples = None
                self._chase_confirmed = False
        self._prepared = None
        self._emitted.clear()
        self._severity.clear()
        self.clock.reset()
        for detector in self._detectors:
            detector.reset(self.ctx)

    async def on_track(self, track: str) -> None:
        """The circuit being driven, as far as it is identified ("" until then).

        Called before each lap is processed. A chase parked by a restart
        resumes once the circuit turns out to be the one it was chosen at,
        and ends if it is another. It waits while the circuit is unknown:
        coaching against a lap of some other track would be noise.
        """
        self.ctx.track_name = track
        if self.reference_mode != "chase" or self._chase_confirmed or not track:
            return
        if track != self._chase_track:
            self.select_session_best_reference()
            return
        self._chase_confirmed = True
        self._chase_settle()
        await self.refresh_reference()

    def _sector_reference_time_ms(
        self,
        samples: dict[str, list[float]],
    ) -> int | None:
        """Return the duration belonging to these exact reference samples."""
        if self.reference_mode == "chase":
            if (
                samples is self._chase_samples
                and self.reference_time_ms is not None
                and self.reference_time_ms > 0
            ):
                return self.reference_time_ms
            return None

        for record in self.ctx.laps:
            if (
                record.session_seq == self.ctx.session_seq
                and record.number == self._reference_lap
                and record.samples is samples
                and record.counts_for_best
                and record.time_ms > 0
            ):
                return record.time_ms

        return None

    def _invalidate_sector_reference(self) -> None:
        """Discard sector timing and invalidate outstanding reference work."""
        self._sector_reference_generation += 1
        self._sector_reference_splits_ms = None
        self._sector_tracker.configure([])

    # --- coaching reference -------------------------------------------------

    def select_chase_reference(
        self,
        lap_id: int,
        time_ms: int,
        samples: dict[str, list[float]],
    ) -> None:
        """Chase a stored lap of the circuit and car being driven.

        The circuit and car are the context's at the time of the call; set
        ctx.track_name first. Call refresh_reference afterwards to adopt it.
        """
        if time_ms <= 0 or not samples.get("dist"):
            raise ValueError("That lap has no usable telemetry")
        self._invalidate_sector_reference()
        self.reference_mode = "chase"
        self._chase_base = (lap_id, time_ms, samples)
        self._chase_samples = None
        self._chase_car_id = self.ctx.car_id
        self._chase_track = self.ctx.track_name
        self._chase_confirmed = True
        # A lap driven this session may already be quicker than the chosen one.
        self._chase_settle()

    def select_session_best_reference(self) -> None:
        """Back to coaching against the session's fastest counting lap."""
        self._end_chase()
        self._invalidate_sector_reference()
        best = self._session_fastest()
        if best is not None:
            self._pending_reference = best.samples
            self._reference_lap = best.number
        else:
            self._pending_reference = None
            self._reference_lap = None
            self.ctx.reference = None
            self.ctx.corners = []

    def _end_chase(self) -> None:
        self.reference_mode = "session_best"
        self.reference_lap_id = None
        self.reference_time_ms = None
        self._chase_base = None
        self._chase_samples = None
        self._chase_car_id = None
        self._chase_track = ""
        self._chase_confirmed = False

    def _chase_settle(self) -> None:
        """Chase the quicker of the floor and this session's fastest counting lap."""
        if self._chase_base is None or not self._chase_confirmed:
            return
        lap_id, time_ms, samples = self._chase_base
        number: int | None = None
        best = self._session_fastest()
        if best is not None and best.time_ms < time_ms:
            lap_id, time_ms, samples, number = None, best.time_ms, best.samples, best.number
        self._chase_adopt(lap_id, time_ms, samples, number)

    def _chase_adopt(
        self,
        lap_id: int | None,
        time_ms: int,
        samples: dict[str, list[float]],
        number: int | None,
    ) -> None:
        """Make this the lap being chased; refresh_reference picks it up.

        `number` is its lap number when it is a lap of this session, which is
        what lets _reflag drop it if it stops counting.
        """
        if samples is not self._chase_samples or self.ctx.reference is None:
            self._invalidate_sector_reference()
            self._pending_reference = samples
        self._chase_samples = samples
        self.reference_lap_id = lap_id
        self.reference_time_ms = time_ms
        self._reference_lap = number

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
        self._invalidate_sector_reference()
        sector_generation = self._sector_reference_generation

        # Sector reference preparation is independent of coaching.
        # Track bundles and reference geometry are processed off the packet path.
        sector_source = self.sector_source
        if sector_source is not None and self.ctx.track_name:
            track = self.ctx.track_name

            reference_time_ms = self._sector_reference_time_ms(samples)

            def _sector_reference() -> tuple[
                list[Boundary],
                list[tuple[float, float]],
                list[float] | None,
            ]:
                authored = sector_source(track)
                boundaries = validated_sector_boundaries(authored)
                if boundaries is None or reference_time_ms is None:
                    return [], [], None
                directions = reference_sector_directions(samples, boundaries)
                if directions is None:
                    return [], [], None
                splits = reference_sector_splits(
                    samples, boundaries, directions, reference_time_ms
                )
                if splits is None or len(splits) != len(boundaries) + 1:
                    return [], [], None
                return boundaries, directions, splits

            try:
                boundaries, directions, splits = await asyncio.to_thread(_sector_reference)
                if sector_generation == self._sector_reference_generation:
                    self._sector_tracker.configure(boundaries, directions=directions)
                    self._sector_reference_splits_ms = splits
            except Exception:
                if sector_generation == self._sector_reference_generation:
                    log.warning(
                        "sector reference preparation failed; sector voice disabled",
                        exc_info=True,
                    )
                    self._sector_tracker.configure([])
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

    def _lap_announcement_text(self, request: CalloutRequest) -> str:
        """Use sector precision for lap announcements only when opted in."""
        if (
            not self.sector_voice_enabled
            or request.event_type not in ("lap_time", "personal_best")
        ):
            return request.text

        from app.race_engineer.detectors.lap import MIN_GAP_MS
        from app.race_engineer.formatter import (
            spoken_precise_gap,
            spoken_precise_lap_time,
        )

        args = request.message_args
        time_ms = args.get("time_ms")

        if not isinstance(time_ms, (int, float)) or time_ms <= 0:
            return request.text

        places = self.sector_voice_decimals
        rounding = self.sector_voice_rounding

        spoken_time = spoken_precise_lap_time(int(time_ms), places, rounding)

        # Do not say "zero point zero seconds faster" when truncating.
        min_gap_ms = max(
            MIN_GAP_MS,
            1000 // (10 ** places) if rounding == "truncate" else 0,
        )

        if request.event_type == "personal_best":
            text = f"New personal best, {spoken_time}."
            gain = args.get("gain_ms")

            if isinstance(gain, (int, float)) and gain >= min_gap_ms:
                spoken_gain = spoken_precise_gap(gain, places, rounding)
                text += f" {spoken_gain.capitalize()} faster."

            return text

        text = f"Lap time, {spoken_time}."
        delta = args.get("delta_ms")

        if isinstance(delta, (int, float)) and abs(delta) >= min_gap_ms:
            spoken_delta = spoken_precise_gap(delta, places, rounding)
            direction = "slower" if delta > 0 else "faster"
            text += f" {spoken_delta.capitalize()} {direction}."

        return text

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
            text=self._lap_announcement_text(request),
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
