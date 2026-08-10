"""Race-event detection on the live packet stream (overtakes, off-road).

Distinct from processing/events.py, which finds driving events (lockups,
wheelspin) inside recorded lap samples — this module watches the live 60 Hz
stream for things worth an immediate webhook notification.

Detection is deliberately debounced:

- A position change must hold for ~1 s before it counts. Side-by-side racing
  makes GT7 flip the position field every few frames; without the hold, a
  single battle would fire dozens of notifications.
- Off-road needs >= 3 wheels on a loose surface for ~0.5 s, and re-arms only
  after ~2 s continuously back on tarmac/kerb, so one excursion is one event.
  This requires packet format C (the only format carrying surface data).

GT7 caveat: the position field is only live in some race types — the game
reports -1 outside them, in which case position events simply never fire.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models import TelemetryPacket
from app.processing.surface import LOOSE_SURFACES

POSITION_HOLD_TICKS = 60  # ~1 s at 60 Hz
OFFROAD_MIN_TICKS = 30  # ~0.5 s
OFFROAD_REARM_TICKS = 120  # ~2 s back on road before the next event
OFFROAD_MIN_SPEED_MPS = 8.0  # ignore crawling/parked excursions
OFFROAD_MIN_WHEELS = 3  # two wheels on the grass is a normal track-limits nibble


@dataclass(slots=True)
class LiveEvent:
    kind: str  # "overtake" | "position_lost" | "off_road"
    position: int = 0
    previous_position: int = 0
    total_positions: int = 0


@dataclass(slots=True)
class LiveEventWatcher:
    """Feed packets, get race events back. Reset on every new session."""

    _committed_pos: int = -1
    _candidate_pos: int = -1
    _candidate_ticks: int = 0
    _offroad_ticks: int = 0
    _onroad_ticks: int = 0
    _offroad_armed: bool = True
    _events: list[LiveEvent] = field(default_factory=list)

    def reset(self) -> None:
        self._committed_pos = -1
        self._candidate_pos = -1
        self._candidate_ticks = 0
        self._offroad_ticks = 0
        self._onroad_ticks = 0
        self._offroad_armed = True

    def feed(self, p: TelemetryPacket) -> list[LiveEvent]:
        self._events = []
        if p.is_on_track and not p.is_paused:
            self._watch_position(p)
            self._watch_surface(p)
        return self._events

    def _watch_position(self, p: TelemetryPacket) -> None:
        pos = p.race_position
        if pos < 1 or p.total_positions < 2:
            return
        if self._committed_pos < 1:
            # First valid reading of the session: baseline, no event.
            self._committed_pos = pos
            self._candidate_pos = pos
            return
        if pos != self._candidate_pos:
            self._candidate_pos = pos
            self._candidate_ticks = 0
            return
        if pos == self._committed_pos:
            return
        self._candidate_ticks += 1
        if self._candidate_ticks < POSITION_HOLD_TICKS:
            return
        kind = "overtake" if pos < self._committed_pos else "position_lost"
        self._events.append(
            LiveEvent(
                kind=kind,
                position=pos,
                previous_position=self._committed_pos,
                total_positions=p.total_positions,
            )
        )
        self._committed_pos = pos
        self._candidate_ticks = 0

    def _watch_surface(self, p: TelemetryPacket) -> None:
        if p.surface_types is None:
            return  # not packet C
        loose = sum(1 for c in p.surface_types if c in LOOSE_SURFACES)
        if loose >= OFFROAD_MIN_WHEELS and p.speed_mps >= OFFROAD_MIN_SPEED_MPS:
            self._onroad_ticks = 0
            self._offroad_ticks += 1
            if self._offroad_armed and self._offroad_ticks >= OFFROAD_MIN_TICKS:
                self._offroad_armed = False
                self._events.append(LiveEvent(kind="off_road"))
        else:
            self._offroad_ticks = 0
            self._onroad_ticks += 1
            if self._onroad_ticks >= OFFROAD_REARM_TICKS:
                self._offroad_armed = True
