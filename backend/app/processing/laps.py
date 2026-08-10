"""Lap detection and per-lap sample series derived from the 60 Hz stream."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from statistics import median

from app.models import TelemetryPacket
from app.processing.surface import encode_surface, off_track_excursions

log = logging.getLogger(__name__)

TICK_SECONDS = 1 / 60
FULL_INPUT = 250  # of 255; GT7 rarely reports exactly 255 with analog triggers
TIRE_SPIN_THRESHOLD = 1.1
# A "completed lap" with almost no samples is a phantom: GT7's lap counter
# flickers through old values in menus/replays and re-reports a stale
# last_lap_time. No real lap is shorter than this many samples (~10 s).
MIN_LAP_TICKS = 600
# Time/distance integrate packet_id deltas so dropped datagrams don't shrink
# the axes. Gaps beyond this (>1 s) are a discontinuity (source restart, pid
# reset, long outage) — extrapolating a minute of distance at current speed
# would corrupt the lap worse than under-counting one frame does.
MAX_FRAME_GAP = 60
# Partial-lap guard: a lap the logger only saw part of — an out-lap from the
# pits, or capture starting mid-lap — covers less of the track than a real
# lap, so it must never become the session best, the live-delta reference or
# a coaching reference. GT7 still reports a lap time for it, and that time is
# short, which is exactly what makes it win.
#
# Calibrated against 850 real recorded laps: 98 % of laps sit within 0.5 % of
# their session's median span, while genuinely partial laps came in at 39-95 %.
# 97 % therefore separates them with a wide margin on both sides.
FULL_LAP_SPAN_RATIO = 0.97
# Spans are compared against the MEDIAN of recent laps, not the longest: 12 of
# those 850 laps ran longer than the median (one by 44 % — an off-track
# excursion), and a single such lap would make every normal lap after it look
# partial against a max-based yardstick.
SPAN_WINDOW = 5
# Below this the median can still be swung by one bad lap, so the longest lap
# seen is the better guess at the real track length.
SPANS_FOR_MEDIAN = 3
# Until then, two laps that disagree are ambiguous: "this lap is 6 % short"
# and "that lap ran 6 % wide" look identical, and a third lap settles it. The
# looser ratio holds off on those while still catching the flagrant case —
# no legitimate lap in 850 recorded ones came in below 94 % of its session,
# and the real partials sat at 88 %, 88 %, 81 %, 65 % and 40 %.
PROVISIONAL_SPAN_RATIO = 0.93

# Columnar per-tick series kept for each lap. Column order matters for the
# frontend; keep in sync with frontend/src/lib/types.ts.
SAMPLE_COLUMNS = (
    "t", "dist", "speed", "throttle", "brake", "coast", "gear", "rpm",
    "boost", "tire_slip", "yaw_rate", "pos_x", "pos_z", "body_height", "fuel",
    # Tier 1 per-corner channels (FL FR RL RR)
    "slip_fl", "slip_fr", "slip_rl", "slip_rr",
    "tt_fl", "tt_fr", "tt_rl", "tt_rr",
    "sus_fl", "sus_fr", "sus_rl", "sus_rr",  # suspension compression, mm
    "aids",  # AidsBits mask: TCS | ASM | handbrake | rev limiter
    "surface",  # packed per-wheel surface codes (see processing/surface.py)
)


def new_sample_store() -> dict[str, list[float]]:
    return {c: [] for c in SAMPLE_COLUMNS}


def _time_weights(t: list[float]) -> list[float]:
    """Per-sample durations from t deltas; uniform when too short to tell.

    Metrics weight samples by how much time each one covered, so a sample
    recorded after a dropped-frame gap counts for the whole gap instead of
    skewing percentages toward whatever happened while packets flowed.
    """
    if len(t) < 2:
        return [1.0] * len(t)
    w = [max(t[i] - t[i - 1], 0.0) for i in range(1, len(t))]
    return [w[0], *w]  # first sample inherits the first interval


@dataclass(slots=True)
class CompletedLap:
    number: int
    time_ms: int
    finished_at: str
    car_id: int
    samples: dict[str, list[float]]
    fuel_start: float
    fuel_end: float
    tod_ms: int = -1  # in-game time of day when the lap completed
    # metrics
    fuel_consumed: float = 0.0
    full_throttle_pct: float = 0.0
    full_brake_pct: float = 0.0
    coasting_pct: float = 0.0
    tire_spin_pct: float = 0.0
    max_speed: float = 0.0
    min_body_height: float = 0.0
    total_ticks: int = 0
    tcs_active_pct: float = 0.0
    asm_active_pct: float = 0.0
    # Engine health — per-lap aggregates only (these drift over minutes, not
    # corners), tracked by the processor rather than sampled per tick.
    max_water_temp: float = 0.0
    max_oil_temp: float = 0.0
    min_oil_pressure: float = -1.0  # sampled above idle rpm only; -1 = unknown
    # Track-limits verdict from the per-tick surface column. Distinct from
    # counts_for_best (which flags partial pit out-laps): a lap can be full
    # AND dirty. -1/None = unknown (recorded without packet-C surface data).
    off_track_count: int = -1
    clean_lap: bool | None = None
    # Static per lap: {"ratios": [...], "top_speed": float, "rpm_alert": float}
    gearing: dict[str, object] | None = None
    events: list[dict[str, object]] = field(default_factory=list)
    # Partial-lap flags (see FULL_LAP_SPAN_RATIO): whether this lap may set
    # the session best, and whether it proved the previous best was partial.
    counts_for_best: bool = True
    invalidated_best: bool = False
    # Lap numbers in this session that now look partial — re-flagged in the DB
    # when `invalidated_best` fires. Only the short ones: a longer lap does not
    # prove that every earlier lap was partial.
    partial_lap_numbers: list[int] = field(default_factory=list)
    # True once enough full laps agree on the track's length for the span
    # check to be trustworthy. Coaching waits for this.
    span_confirmed: bool = False
    # Session best over the full laps BEFORE this one (-1 = none). Personal
    # bests and the delta widget's end-of-lap fallback need the best that
    # excludes the lap being reported — a best that already contains it can
    # never show an improvement.
    session_best_before_ms: int = -1

    def compute_metrics(self) -> None:
        # Imported laps from older export versions may lack the newer columns;
        # every metric guards with .get so they degrade to 0 rather than raise.
        from app.models import AidsBits
        from app.processing.events import detect_events

        s = self.samples
        n = len(s["t"])
        self.total_ticks = n
        if n == 0:
            return
        # Percentages are time-weighted: after a dropped-frame gap a sample
        # covers the whole gap, so drops don't skew the input metrics.
        w = _time_weights(s["t"])
        total_w = sum(w) or 1.0

        def pct(flags: list[bool]) -> float:
            return 100.0 * sum(wi for wi, f in zip(w, flags, strict=True) if f) / total_w

        self.fuel_consumed = max(0.0, self.fuel_start - self.fuel_end)
        self.full_throttle_pct = pct([v >= 98.0 for v in s["throttle"]])
        self.full_brake_pct = pct([v >= 98.0 for v in s["brake"]])
        self.coasting_pct = pct([v > 0 for v in s["coast"]])
        self.tire_spin_pct = pct([v >= TIRE_SPIN_THRESHOLD for v in s["tire_slip"]])
        self.max_speed = max(s["speed"])
        self.min_body_height = min(s["body_height"])
        aids = s.get("aids") or []
        if len(aids) == n:
            self.tcs_active_pct = pct([bool(int(v) & AidsBits.TCS) for v in aids])
            self.asm_active_pct = pct([bool(int(v) & AidsBits.ASM) for v in aids])
        surface = s.get("surface") or []
        if len(surface) == n:
            self.off_track_count = off_track_excursions(surface)
            self.clean_lap = self.off_track_count == 0 if self.off_track_count >= 0 else None
        self.events = detect_events(s)


@dataclass(slots=True)
class SessionInfo:
    car_id: int
    started_at: str
    lap_count: int = 0
    best_lap_time_ms: int = -1


LapCallback = Callable[[CompletedLap], Awaitable[None]]
SessionCallback = Callable[[SessionInfo], Awaitable[None]]


@dataclass
class LapProcessor:
    """Consumes packets, emits completed laps and session boundaries.

    A new session starts when the car changes, or when the lap counter resets
    (race restart / return to track). Time-trial "lap 0" out-laps are ignored.
    """

    on_lap: LapCallback
    on_session: SessionCallback
    min_lap_ticks: int = MIN_LAP_TICKS

    _session: SessionInfo | None = None
    _current_lap: int = -1
    _samples: dict[str, list[float]] = field(default_factory=new_sample_store)
    _distance: float = 0.0
    _elapsed_s: float = 0.0
    _last_pid: int = -1
    _pending_dt: int = 1  # frames covered by the next sample (1 = no drops)
    _dropped_frames: int = 0
    # (lap number, distance span, time) for every lap of the session, and the
    # lap numbers currently judged partial. Both are needed to *recompute* the
    # best whenever the yardstick moves: dropping a partial lap must promote
    # the fastest remaining real lap, not blank the best until the next one.
    _laps: list[tuple[int, float, int]] = field(default_factory=list)
    _partial: set[int] = field(default_factory=set)
    _fuel_start: float = 0.0
    _last_packet: TelemetryPacket | None = None
    # Engine-health aggregates for the lap in progress (not per-tick columns)
    _max_water: float = 0.0
    _max_oil: float = 0.0
    _min_oil_pressure: float = -1.0

    @property
    def session(self) -> SessionInfo | None:
        return self._session

    @property
    def live_lap_samples(self) -> dict[str, list[float]]:
        return self._samples

    @property
    def dropped_frames(self) -> int:
        return self._dropped_frames

    async def feed(self, p: TelemetryPacket) -> None:
        if p.is_loading:
            return

        # Frames covered since the previous packet, from the console's own
        # packet counter. Tracked for EVERY non-loading packet (paused ones
        # too) so unpausing sees a ~1-frame gap and pauses add no lap time.
        gap = p.packet_id - self._last_pid if self._last_pid >= 0 else 1
        self._last_pid = p.packet_id
        if 1 <= gap <= MAX_FRAME_GAP:
            self._pending_dt = gap
            self._dropped_frames += gap - 1
        else:
            self._pending_dt = 1  # first packet, pid reset, or discontinuity

        if self._session is not None and p.car_id != self._session.car_id:
            log.info("car changed (%d -> %d): starting new session", self._session.car_id, p.car_id)
            self._session = None

        lap_reset = (
            self._current_lap > 0 and 0 <= p.current_lap < self._current_lap
        )
        if self._session is None or lap_reset:
            self._session = SessionInfo(
                car_id=p.car_id,
                started_at=datetime.now(UTC).isoformat(),
            )
            self._current_lap = -1
            self._laps.clear()
            self._partial.clear()
            await self.on_session(self._session)

        if p.current_lap != self._current_lap:
            await self._handle_lap_boundary(p)

        # After the checkered flag GT7 reports current_lap = total_laps + 1;
        # the cool-down lap is not real driving, so don't record it.
        past_finish = 0 < p.total_laps < p.current_lap
        if p.is_on_track and not p.is_paused and p.current_lap > 0 and not past_finish:
            self._append_sample(p)
        self._last_packet = p

    async def _handle_lap_boundary(self, p: TelemetryPacket) -> None:
        prev = self._current_lap
        completing = (
            prev > 0
            and p.current_lap == prev + 1
            and p.last_lap_time_ms > 0
            and len(self._samples["t"]) >= self.min_lap_ticks
        )
        finished_samples = self._samples
        fuel_start = self._fuel_start
        engine = (self._max_water, self._max_oil, self._min_oil_pressure)

        # Commit all state BEFORE any await: packets keep arriving while the
        # lap is persisted, and a stale _current_lap would re-trigger this
        # boundary once per packet (duplicate laps at ~60 Hz).
        self._current_lap = p.current_lap
        self._samples = new_sample_store()
        self._distance = 0.0
        self._elapsed_s = 0.0
        self._fuel_start = p.fuel_level
        self._max_water = 0.0
        self._max_oil = 0.0
        self._min_oil_pressure = -1.0

        if completing:
            lap = CompletedLap(
                number=prev,
                time_ms=p.last_lap_time_ms,
                finished_at=datetime.now(UTC).isoformat(),
                car_id=p.car_id,
                samples=finished_samples,
                fuel_start=fuel_start,
                fuel_end=p.fuel_level,
                tod_ms=p.day_progression_ms,
            )
            lap.max_water_temp = round(engine[0], 1)
            lap.max_oil_temp = round(engine[1], 1)
            lap.min_oil_pressure = round(engine[2], 3)
            lap.gearing = {
                "ratios": [round(r, 4) for r in p.gear_ratios if r > 0],
                "top_speed": round(p.transmission_top_speed, 1),
                "rpm_alert": p.rpm_alert_max,
            }
            lap.compute_metrics()
            assert self._session is not None
            self._session.lap_count += 1

            self._apply_span_guard(lap, finished_samples)
            await self.on_lap(lap)

    def _apply_span_guard(self, lap: CompletedLap, samples: dict[str, list[float]]) -> None:
        """Judge which laps of this session covered the whole track.

        The yardstick is how far recent laps ran, so it needs no knowledge of
        the circuit — but it moves as laps arrive, so the verdict for EVERY
        lap of the session is recomputed each time rather than fixed when the
        lap finished. That is what lets a lap the logger only half-saw be
        retracted later, and what stops the best from being blanked when it is:
        the fastest remaining full lap takes over instead.

        It only becomes trustworthy once several laps agree on the distance
        (`span_confirmed`) — which is what coaching waits for.
        """
        assert self._session is not None
        span = samples["dist"][-1] if samples["dist"] else 0.0
        # Keyed by lap number, because that is what the stored rows are
        # re-flagged by. A repeated number (GT7 re-reporting after a rewind)
        # would otherwise put two laps behind one key and let a single verdict
        # condemn both — the later lap replaces the earlier one instead.
        self._laps = [entry for entry in self._laps if entry[0] != lap.number]
        self._laps.append((lap.number, span, lap.time_ms))

        reference = self._reference_span()
        ratio = (
            FULL_LAP_SPAN_RATIO if len(self._laps) >= SPANS_FOR_MEDIAN
            else PROVISIONAL_SPAN_RATIO
        )
        full_enough = reference * ratio
        # A lone lap has nothing to be compared with, so it counts — until a
        # second lap gives the comparison meaning.
        partial = (
            set()
            if len(self._laps) == 1
            else {number for number, value, _ in self._laps if value < full_enough}
        )

        lap.counts_for_best = lap.number not in partial
        lap.span_confirmed = self._span_confirmed(reference)
        # The set changing means an earlier verdict was wrong in one direction
        # or the other; the stored rows have to be brought back in line.
        if partial != self._partial:
            lap.invalidated_best = True
            lap.partial_lap_numbers = sorted(partial)
            self._partial = partial

        prior = [
            time
            for number, _, time in self._laps
            if number not in partial and number != lap.number
        ]
        lap.session_best_before_ms = min(prior) if prior else -1
        valid = [time for number, _, time in self._laps if number not in partial]
        self._session.best_lap_time_ms = min(valid) if valid else -1

    def _reference_span(self) -> float:
        """How far a full lap of this circuit runs, as far as we can tell."""
        spans = sorted(value for _, value, _ in self._laps[-SPAN_WINDOW:])
        if len(spans) < SPANS_FOR_MEDIAN:
            # Two laps that disagree are ambiguous — one is short or the other
            # ran wide. The longer is the better guess at the track's length,
            # and a third lap settles it either way.
            return spans[-1] if spans else 0.0
        return median(spans)

    def _span_confirmed(self, reference: float) -> bool:
        """True once several laps agree on the track length within tolerance."""
        if len(self._laps) < SPANS_FOR_MEDIAN or reference <= 0:
            return False
        agreeing = sum(
            1
            for _, value, _ in self._laps[-SPAN_WINDOW:]
            if value >= reference * FULL_LAP_SPAN_RATIO
        )
        return agreeing >= SPANS_FOR_MEDIAN - 1

    def _append_sample(self, p: TelemetryPacket) -> None:
        s = self._samples
        dt_s = self._pending_dt * TICK_SECONDS
        if s["t"]:  # the lap's first sample anchors at t=0
            self._elapsed_s += dt_s
        self._distance += p.speed_mps * dt_s
        throttle = round(p.throttle_pct, 1)
        brake = round(p.brake_pct, 1)
        s["t"].append(round(self._elapsed_s, 4))
        s["dist"].append(round(self._distance, 2))
        s["speed"].append(round(p.speed_kmh, 2))
        s["throttle"].append(throttle)
        s["brake"].append(brake)
        s["coast"].append(1.0 if throttle < 1 and brake < 1 else 0.0)
        s["gear"].append(float(p.current_gear))
        s["rpm"].append(round(p.engine_rpm, 1))
        s["boost"].append(round(p.boost, 3))
        s["tire_slip"].append(round(p.tire_slip_ratio, 4))
        s["yaw_rate"].append(round(abs(p.angular_velocity_y), 4))
        s["pos_x"].append(round(p.position_x, 2))
        s["pos_z"].append(round(p.position_z, 2))
        s["body_height"].append(round(p.body_height * 1000, 1))  # mm
        s["fuel"].append(round(p.fuel_level, 3))
        slips = p.wheel_slips
        for i, w in enumerate(("fl", "fr", "rl", "rr")):
            s[f"slip_{w}"].append(round(slips[i], 4))
        s["tt_fl"].append(round(p.tire_temp_fl, 1))
        s["tt_fr"].append(round(p.tire_temp_fr, 1))
        s["tt_rl"].append(round(p.tire_temp_rl, 1))
        s["tt_rr"].append(round(p.tire_temp_rr, 1))
        s["sus_fl"].append(round(p.suspension_fl * 1000, 1))  # mm
        s["sus_fr"].append(round(p.suspension_fr * 1000, 1))
        s["sus_rl"].append(round(p.suspension_rl * 1000, 1))
        s["sus_rr"].append(round(p.suspension_rr * 1000, 1))
        s["aids"].append(float(p.aids_bits))
        s["surface"].append(float(encode_surface(p.surface_types)))
        # Engine-health aggregates (per-lap, not per-tick)
        self._max_water = max(self._max_water, p.water_temp)
        self._max_oil = max(self._max_oil, p.oil_temp)
        if p.engine_rpm > 1200:  # ignore idle — pressure at idle is meaningless
            self._min_oil_pressure = (
                p.oil_pressure
                if self._min_oil_pressure < 0
                else min(self._min_oil_pressure, p.oil_pressure)
            )
