"""Shared detector state: the packet clock and the per-lap context object."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models import TelemetryPacket
from app.processing.laps import CompletedLap
from app.processing.strategy import LapFuel

# Same rule as the lap processor: time comes from the console's packet
# counter, never the wall clock. Pauses add no time (GT7 keeps streaming at
# 60 Hz while paused) and a dropped datagram widens one step instead of
# silently shrinking every persistence window.
TICK_SECONDS = 1 / 60
MAX_FRAME_GAP = 60


@dataclass(slots=True)
class PacketClock:
    """Monotonic seconds derived from packet ids."""

    now: float = 0.0
    _last_pid: int = -1

    def reset(self) -> None:
        self.now = 0.0
        self._last_pid = -1

    def advance(self, p: TelemetryPacket) -> float:
        gap = p.packet_id - self._last_pid if self._last_pid >= 0 else 1
        self._last_pid = p.packet_id
        # First packet, packet-id reset, or a discontinuity: count one frame.
        self.now += (gap if 1 <= gap <= MAX_FRAME_GAP else 1) * TICK_SECONDS
        return self.now


@dataclass(slots=True)
class LapRecord:
    """A completed lap as the detectors need it (kept across sessions)."""

    number: int
    time_ms: int
    car_id: int
    fuel_consumed: float
    counts_for_best: bool
    # Lap numbers repeat across sessions and the history deliberately spans
    # them (for the fuel model), so re-flagging a partial lap has to know
    # which session's lap 1 it means.
    session_seq: int = 0
    invalidated_best: bool = False
    events: list[dict[str, Any]] = field(default_factory=list)
    samples: dict[str, list[float]] = field(default_factory=dict)

    @classmethod
    def from_lap(cls, lap: CompletedLap) -> LapRecord:
        return cls(
            number=lap.number,
            time_ms=lap.time_ms,
            car_id=lap.car_id,
            fuel_consumed=lap.fuel_consumed,
            counts_for_best=lap.counts_for_best,
            invalidated_best=lap.invalidated_best,
            events=lap.events,
            samples=lap.samples,
        )

    def as_fuel(self) -> LapFuel:
        return LapFuel(
            number=self.number,
            time_ms=self.time_ms,
            fuel_consumed=self.fuel_consumed,
            car_id=self.car_id,
        )


@dataclass(slots=True)
class EngineerContext:
    """Everything detectors may read; owned and updated by the manager.

    `laps` deliberately survives session boundaries (newest first): a race
    restart opens a new session, and dropping the stint's laps would blank the
    fuel model exactly when it matters. Same-car filtering happens in the
    projection, as it does on the frontend.
    """

    now: float = 0.0
    session_id: int | None = None
    session_seq: int = 0  # bumped on every session; part of dedupe keys
    # "metric" or "imperial" — spoken units for distances and speeds. The
    # browser's own km/h-vs-mph toggle is per-device and can't reach text the
    # server has already worded, so this is a server setting.
    units: str = "metric"
    track_name: str = ""
    car_id: int = 0
    best_lap_ms: int | None = None
    prev_best_ms: int | None = None
    # Most recent packet, so lap-boundary detectors can read live values
    # (fuel level, race distance) without the manager passing them along.
    packet: TelemetryPacket | None = None
    laps: list[LapRecord] = field(default_factory=list)
    # Reference lap for coaching: the session-best lap's samples and the
    # corners detected on it (empty until a best lap exists).
    reference: dict[str, list[float]] | None = None
    corners: list[dict[str, float | int | str]] = field(default_factory=list)
    # False until several laps agree on the track's distance. Everything that
    # compares one lap against another by position — braking points, corner
    # losses, where a lockup happened — is meaningless before then, because a
    # lap the logger only half-saw has its distance axis anchored elsewhere.
    span_confirmed: bool = False

    def corner_at(self, dist_m: float) -> int | None:
        """Corner number containing a track distance, if any.

        A start/finish corner reports entry_dist > exit_dist because its
        extent wraps past the line — the containment test has to wrap too.
        """
        for corner in self.corners:
            entry = float(corner["entry_dist"])
            exit_ = float(corner["exit_dist"])
            inside = (
                entry <= dist_m <= exit_
                if entry <= exit_
                else (dist_m >= entry or dist_m <= exit_)
            )
            if inside:
                return int(corner["n"])
        return None

    def corner_name(self, number: int | None) -> str:
        """A corner's hand-given name, if this circuit's corners were labelled.

        Only authored corners carry one — detection has nothing to name a
        corner after (#48).
        """
        if number is None:
            return ""
        for corner in self.corners:
            if int(corner["n"]) == number:
                return str(corner.get("name") or "")
        return ""

    def corner_ahead(self, dist_m: float, window_m: float) -> int | None:
        """The corner a driver at this distance is braking for, if any.

        Braking events land *before* the corner they belong to, so the corner
        containing them is usually None — the useful answer is the next one
        within a braking zone's reach.
        """
        best: int | None = None
        best_gap = window_m
        for corner in self.corners:
            gap = float(corner["entry_dist"]) - dist_m
            if 0 <= gap < best_gap:
                best_gap, best = gap, int(corner["n"])
        return best

    def corner_behind(self, dist_m: float, window_m: float) -> int | None:
        """The corner just exited (wheelspin happens on corner exit)."""
        best: int | None = None
        best_gap = window_m
        for corner in self.corners:
            gap = dist_m - float(corner["exit_dist"])
            if 0 <= gap < best_gap:
                best_gap, best = gap, int(corner["n"])
        return best
