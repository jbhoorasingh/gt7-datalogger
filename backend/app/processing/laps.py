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
# Lap-clock cross-check (#20): warn when our integrated time drifts more than
# this from GT7's own lap timer within one lap. 100 ms is ~6 frames of
# mis-credited time — well past rounding noise, small enough to catch early.
LAP_CLOCK_WARN_MS = 100
# Salvage acceptance window (#26): a lap only commits when the counter steps
# forward, and a stream that ends AT the finish line — the time-trial-leader
# replay is the canonical case — never shows that step. The buffered lap is
# kept anyway, but only when a lap time GT7 itself reported agrees with our
# integrated duration. The #20 cross-check puts genuine integration drift
# well under half a second over a lap, while an aborted lap that happens to
# resemble a reported time almost never lands inside a window this tight.
# The ratio term keeps the window proportionate on endurance-length laps,
# where half a second of honest drift is plausible.
SALVAGE_TOLERANCE_MS = 500
SALVAGE_TOLERANCE_RATIO = 0.005
# A backward jump in GT7's per-tick lap clock (packet C) marks a line
# crossing inside the buffer: replays pre-roll from before the flying lap,
# and the clock re-anchors when the lap starts. 250 ms is far beyond the
# tick-to-tick jitter of a counting clock and far below any real lap time,
# so a jump this size can only be a re-anchor.
SALVAGE_CLOCK_JUMP_MS = 250
# Lap 0 is buffered too (a replay may stream its flying lap with the counter
# parked at 0), but menus can stream stale "on track, lap 0" packets
# indefinitely and no boundary ever empties that buffer. Cap it at 15
# minutes — no real flying lap is longer — so a console left in a menu
# cannot grow it without bound.
LAP0_MAX_TICKS = 60 * 60 * 15
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

# Columns only the extended packet formats can fill (#15, #16, #18). Unlike
# every other column these are NOT appended on ticks that lack them, and a lap
# that did not carry one from start to finish drops it entirely — see
# prune_optional for why zero-filling would be worse than absence.
#
#   steer                       packet B  wheel_rotation, radians as broadcast
#   acc_lat/acc_long/acc_vert   packet B  sway/surge/heave, raw accelerometer
#                                         units (calibrated in analysis.py —
#                                         GT7 documents no unit for these)
#   throttle_f/brake_f          packet ~  pedal AFTER the aids acted on it, %;
#                                         the gap to throttle/brake is the
#                                         intervention, measured not inferred
OPTIONAL_COLUMNS = (
    "steer",
    "acc_lat", "acc_long", "acc_vert",
    "throttle_f", "brake_f",
)

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
    *OPTIONAL_COLUMNS,
)


def new_sample_store() -> dict[str, list[float]]:
    return {c: [] for c in SAMPLE_COLUMNS}


def prune_optional(samples: dict[str, list[float]]) -> list[str]:
    """Drop optional columns this lap did not carry all the way through.

    An optional column is only appended on the ticks that actually supplied
    it, so a lap recorded on packet A leaves `steer` empty and a lap that
    switched format mid-way leaves it short. Both are dropped whole rather
    than padded: a zero-filled steering trace reads as "the driver never
    turned", and a flat zero g-g scatter reads as "the car never gripped" —
    lies that an absent panel does not tell. Consumers already treat a
    missing column as "this recording has no such channel".

    Mutates `samples` and returns what it removed.
    """
    n = len(samples.get("t") or [])
    dropped = [c for c in OPTIONAL_COLUMNS if c in samples and len(samples[c]) != n]
    for column in dropped:
        del samples[column]
    return dropped


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


def _clock_segments(clock: list[int]) -> list[tuple[int, int]]:
    """Candidate lap extents inside a buffered clock trace (#26).

    A salvage buffer can hold more than the lap. A replay pre-rolls from
    before the line, and a stream that runs PAST the crossing before cutting
    to LOADING leaves a post-line stub where GT7's clock has re-anchored for
    a lap nobody will see — trimming to a single "start of lap" index would
    hand salvage that stub and lose the flying lap sitting right before it.
    Every backward jump in the clock is such a re-anchor, so the trace is
    split into segments at them; within each, a frozen head (clock parked at
    0 until the line) and a frozen tail (the stream holding its final frame)
    are walked off, because the clock only counts while the lap it is
    anchored to is actually running. A monotone trace comes back as the one
    segment covering everything.
    """
    anchors = [0] + [
        i
        for i in range(1, len(clock))
        if clock[i] + SALVAGE_CLOCK_JUMP_MS < clock[i - 1]
    ]
    segments: list[tuple[int, int]] = []
    for k, anchor in enumerate(anchors):
        end = anchors[k + 1] if k + 1 < len(anchors) else len(clock)
        start = anchor
        while start + 1 < end and clock[start + 1] <= clock[start]:
            start += 1
        while end - 1 > start and clock[end - 1] <= clock[end - 2]:
            end -= 1
        segments.append((start, end))
    return segments


def _slice_lap_samples(
    samples: dict[str, list[float]], start: int, end: int
) -> dict[str, list[float]]:
    """Copy of the sample store over [start, end), t and dist rebased to 0.

    Only columns spanning the full buffer can be lined up with the extent; a
    shorter column (an optional channel that appeared mid-buffer) is carried
    unchanged — compute_metrics/prune_optional already drop optional columns
    whose length disagrees with `t`.
    """
    n = len(samples["t"])
    t0 = samples["t"][start]
    d0 = samples["dist"][start]
    out: dict[str, list[float]] = {}
    for column, values in samples.items():
        if len(values) != n:
            out[column] = values
        elif column == "t":
            out[column] = [round(v - t0, 4) for v in values[start:end]]
        elif column == "dist":
            out[column] = [round(v - d0, 2) for v in values[start:end]]
        else:
            out[column] = values[start:end]
    return out


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
    # The same question asked of the SURVEYED road edges instead (#41):
    # excursions beyond the compiled borders, judged by the service once the
    # circuit is known. -1 = unknown (no survey, or too little surveyed road
    # under this lap). Reported beside off_track_count, never folded into it.
    off_survey_count: int = -1
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
    car_category: str = ""  # packet C: "Gr.3", "Gr.4", "N300"...
    # True once enough full laps agree on the track's length for the span
    # check to be trustworthy. Coaching waits for this.
    span_confirmed: bool = False
    # Session best over the full laps BEFORE this one (-1 = none). Personal
    # bests and the delta widget's end-of-lap fallback need the best that
    # excludes the lap being reported — a best that already contains it can
    # never show an improvement.
    session_best_before_ms: int = -1
    # Lap recovered from a stream that ended without the counter increment —
    # replay endings (#26). The time is GT7's own, verified against the
    # integrated clock rather than reported at a boundary.
    salvaged: bool = False

    def compute_metrics(self) -> None:
        # Imported laps from older export versions may lack the newer columns;
        # every metric guards with .get so they degrade to 0 rather than raise.
        from app.models import AidsBits
        from app.processing.events import detect_events

        s = self.samples
        n = len(s["t"])
        self.total_ticks = n
        # Before anything reads them: a half-populated optional column would
        # otherwise be persisted and then silently mis-align with `dist`.
        dropped = prune_optional(s)
        if dropped:
            log.debug("lap %d: dropped incomplete channels %s", self.number, dropped)
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

    def apply_survey_verdict(self, count: int) -> None:
        """Record the surveyed-edge verdict (#41) and let it spoil cleanliness.

        Clean now means: surface flags clean AND no excursion past the
        surveyed border. Unknown (-1) spoils nothing — a lap on an unsurveyed
        circuit keeps whatever the surface flags said.
        """
        self.off_survey_count = count
        if count > 0:
            self.clean_lap = False


@dataclass(slots=True)
class SessionInfo:
    car_id: int
    started_at: str
    lap_count: int = 0
    best_lap_time_ms: int = -1
    car_category: str = ""  # packet C: "Gr.3", "Gr.4", "N300"...


LapCallback = Callable[[CompletedLap], Awaitable[None]]
SessionCallback = Callable[[SessionInfo], Awaitable[None]]


@dataclass
class LapProcessor:
    """Consumes packets, emits completed laps and session boundaries.

    A new session starts when the car changes, when the lap counter resets
    (race restart / return to track), or after a lap is salvaged (see below —
    the stream that produced it broke off, so what follows is a new stint).
    A lap normally commits when the counter steps to prev+1 — but a stream
    can end AT the finish line (watching the time-trial leader's replay does
    exactly that), so a buffered lap the counter abandoned is salvaged
    instead when GT7's own reported time matches the integrated duration
    (#26). Time-trial "lap 0" out-laps are buffered for that path too, and
    only ever commit through it: the 0 -> 1 boundary itself still commits
    nothing.
    """

    on_lap: LapCallback
    on_session: SessionCallback
    min_lap_ticks: int = MIN_LAP_TICKS

    _session: SessionInfo | None = None
    _current_lap: int = -1
    _samples: dict[str, list[float]] = field(default_factory=new_sample_store)
    # GT7's per-tick lap clock (packet C), kept parallel to _samples: the
    # salvage trimmer reads it to find where inside the buffer the lap began
    # (#26). Transient — never persisted with the lap's samples — and reset
    # everywhere _samples is, or a stale trace would mis-trim the next lap.
    _gt_clock: list[int] = field(default_factory=list)
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
    # Lap-clock cross-check (#20): how far our packet-id-integrated t axis
    # drifts from GT7's own lap timer (packet C). Baseline-relative, because
    # GT7's clock anchors on its own lap boundary — see _check_lap_clock.
    _clock_offset_ms: float | None = None
    _clock_last_gt_ms: int = -1
    _lap_clock_drift_ms: int = 0  # latest drift, current lap (signed)
    _lap_clock_peak_ms: int = 0  # peak |drift| of the lap in progress
    _lap_clock_worst_ms: int = 0  # peak |drift| of the session
    _lap_clock_samples: int = 0  # comparisons made this session
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

    @property
    def lap_clock_drift_ms(self) -> int:
        """Current-lap drift of our t axis vs GT7's lap clock (#20). Signed."""
        return self._lap_clock_drift_ms

    @property
    def lap_clock_drift_worst_ms(self) -> int:
        """Worst |drift| seen this session; 0 below packet C."""
        return self._lap_clock_worst_ms

    @property
    def lap_clock_samples(self) -> int:
        """Packets this session where both clocks were comparable."""
        return self._lap_clock_samples

    async def feed(self, p: TelemetryPacket) -> None:
        if p.is_loading:
            # A replay that just ended streams LOADING while the menu builds,
            # and the finished flying lap is still in the buffer — the counter
            # step that would commit it never arrives (#26). Same discipline
            # as the boundary handler: reset the buffer BEFORE the await. A
            # failed attempt leaves the buffer alone, because a loading blip
            # mid-driving must not destroy the lap in progress.
            if self._session is not None and len(self._samples["t"]) >= self.min_lap_ticks:
                finished = self._samples
                lap = self._build_salvaged_lap(self._current_lap, finished, self._gt_clock, p)
                if lap is not None:
                    self._reset_lap_buffer(p)
                    await self._emit_salvaged(lap, len(finished["t"]))
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

        # A car change or lap reset is about to tear the session down with a
        # full lap still buffered: the ending-at-the-line case again, seen
        # through a menu transition instead of a LOADING gap (#26). Attempted
        # BEFORE the teardown so the salvaged lap lands in the session it was
        # driven in; on failure the buffer is left for the existing paths to
        # discard, logged here because the teardown parks the lap counter at
        # -1 before the boundary handler could report it.
        if self._session is not None and len(self._samples["t"]) >= self.min_lap_ticks:
            tearing_down = p.car_id != self._session.car_id or (
                self._current_lap > 0 and 0 <= p.current_lap < self._current_lap
            )
            if tearing_down:
                finished = self._samples
                lap = self._build_salvaged_lap(self._current_lap, finished, self._gt_clock, p)
                if lap is not None:
                    self._reset_lap_buffer(p)
                    await self._emit_salvaged(lap, len(finished["t"]))
                else:
                    self._log_discard(self._current_lap, finished, p)

        if self._session is not None and p.car_id != self._session.car_id:
            log.info("car changed (%d -> %d): starting new session", self._session.car_id, p.car_id)
            self._session = None

        lap_reset = (
            self._current_lap > 0 and 0 <= p.current_lap < self._current_lap
        )
        if self._session is None or lap_reset:
            self._session = SessionInfo(
                car_id=p.car_id,
                car_category=p.car_category or "",
                started_at=datetime.now(UTC).isoformat(),
            )
            self._current_lap = -1
            self._laps.clear()
            self._partial.clear()
            self._lap_clock_worst_ms = 0
            self._lap_clock_samples = 0
            await self.on_session(self._session)

        if p.current_lap != self._current_lap:
            await self._handle_lap_boundary(p)

        # After the checkered flag GT7 reports current_lap = total_laps + 1;
        # the cool-down lap is not real driving, so don't record it.
        past_finish = 0 < p.total_laps < p.current_lap
        # Lap 0 is sampled too: a replay may stream its flying lap with the
        # counter parked at 0 (time-trial out-lap semantics), and salvage is
        # the only path that can commit it (#26). The completion rule still
        # requires prev > 0, so a real out-lap's 0 -> 1 boundary behaves
        # exactly as before.
        if p.is_on_track and not p.is_paused and p.current_lap >= 0 and not past_finish:
            if self._current_lap == 0 and len(self._samples["t"]) >= LAP0_MAX_TICKS:
                # No boundary ever empties the lap-0 buffer; without the cap a
                # menu streaming stale on-track packets grows it forever.
                log.info(
                    "lap-0 buffer reached %d ticks without a boundary: dropping it",
                    LAP0_MAX_TICKS,
                )
                self._reset_lap_buffer(p)
            self._append_sample(p)
            # An out-lap's GT7 lap clock is frozen, or anchored to a lap we
            # never saw start — comparing our lap-0 t axis against it would
            # report false drift, so the #20 cross-check waits for a real lap.
            if p.current_lap > 0:
                self._check_lap_clock(p)
        else:
            # Sampler gated off (pause, menus, cool-down): our t axis froze
            # while GT7's lap clock may have kept counting, so a comparison
            # spanning the gap would report false drift. Re-anchor on the
            # next sampled packet instead.
            self._clock_offset_ms = None
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
        finished_clock = self._gt_clock
        fuel_start = self._fuel_start
        engine = (self._max_water, self._max_oil, self._min_oil_pressure)

        # A transition that abandons the buffer instead of completing it — a
        # jump to -1, a forward jump past prev+1, an out-lap's 0 -> 1 — can
        # still hold a finished lap the counter never acknowledged (#26).
        # Built here, before the reset clears the engine aggregates it reads;
        # emission still waits until after the reset (the await rule below).
        # The 0 -> 1 out-lap boundary naturally fails the time match
        # (last_lap_time is -1 or stale), which is what keeps real time-trial
        # out-laps uncommitted.
        salvage: CompletedLap | None = None
        if not completing and prev >= 0 and len(finished_samples["t"]) >= self.min_lap_ticks:
            salvage = self._build_salvaged_lap(prev, finished_samples, finished_clock, p)

        # Commit all state BEFORE any await: packets keep arriving while the
        # lap is persisted, and a stale _current_lap would re-trigger this
        # boundary once per packet (duplicate laps at ~60 Hz).
        # One line per lap, not per packet: the peak says how far the two
        # clocks disagreed over the whole lap (#20).
        if prev > 0 and self._lap_clock_peak_ms > LAP_CLOCK_WARN_MS:
            log.warning(
                "lap %d: integrated time drifted up to %d ms from GT7's own "
                "lap clock (unaccounted frame gaps?)",
                prev, self._lap_clock_peak_ms,
            )

        self._current_lap = p.current_lap
        self._reset_lap_buffer(p)

        if completing:
            lap = CompletedLap(
                number=prev,
                time_ms=p.last_lap_time_ms,
                finished_at=datetime.now(UTC).isoformat(),
                car_id=p.car_id,
                car_category=p.car_category or "",
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
        elif salvage is not None:
            await self._emit_salvaged(salvage, len(finished_samples["t"]))
        elif prev >= 0 and len(finished_samples["t"]) >= self.min_lap_ticks:
            self._log_discard(prev, finished_samples, p)

    def _reset_lap_buffer(self, p: TelemetryPacket) -> None:
        """Fresh buffer for the next lap; the caller decides what lap it is.

        Shared by the boundary handler, the salvage call sites and the lap-0
        cap so no path can forget one of the trackers — a stale _gt_clock, in
        particular, would mis-trim the NEXT salvage attempt.
        """
        self._samples = new_sample_store()
        self._gt_clock = []
        self._distance = 0.0
        self._elapsed_s = 0.0
        self._clock_offset_ms = None
        self._clock_last_gt_ms = -1
        self._lap_clock_drift_ms = 0
        self._lap_clock_peak_ms = 0
        self._fuel_start = p.fuel_level
        self._max_water = 0.0
        self._max_oil = 0.0
        self._min_oil_pressure = -1.0

    def _salvage_candidates(self, p: TelemetryPacket) -> list[int]:
        """Lap times GT7 itself has vouched for, deduped (#26).

        Both the current packet and the previous one are consulted: on a car
        change or menu transition `p` can already belong to the NEW context
        (its times -1 or someone else's), while the last packet of the old
        context still carries the finished lap's. best_lap_time is included
        because a replay's single flying lap IS the best — some menus blank
        last_lap_time first.
        """
        out: list[int] = []
        for src in (p, self._last_packet):
            if src is None:
                continue
            for value in (src.last_lap_time_ms, src.best_lap_time_ms):
                if value > 0 and value not in out:
                    out.append(value)
        return out

    def _build_salvaged_lap(
        self,
        prev_lap: int,
        samples: dict[str, list[float]],
        gt_clock: list[int],
        p: TelemetryPacket,
    ) -> CompletedLap | None:
        """The buffered lap as a CompletedLap, if GT7 vouches for it (#26).

        Pure — no processor state is touched — so a failed attempt costs the
        caller nothing. The loading call site depends on that: it retries on
        every LOADING packet and must be free to leave a mid-driving buffer
        intact.
        """
        n = len(samples["t"])
        if n < self.min_lap_ticks:
            return None
        candidates = self._salvage_candidates(p)
        if not candidates:
            return None
        # The buffer may hold more than the lap: replay pre-roll before the
        # line, and a post-line stub when the stream ran past the crossing.
        # GT7's own per-tick clock splits it into candidate extents — but
        # only when it covered every sample; a partial trace can't be lined
        # up with them. Latest extent first: with a looping replay more than
        # one can match a reported time, and the lap that just ended is the
        # one the stream broke off from.
        segments = _clock_segments(gt_clock) if len(gt_clock) == n else [(0, n)]
        chosen: tuple[int, int, int] | None = None
        for start, end in reversed(segments):
            if end - start < self.min_lap_ticks:
                continue
            duration_ms = round((samples["t"][end - 1] - samples["t"][start]) * 1000)
            tolerance = max(SALVAGE_TOLERANCE_MS, duration_ms * SALVAGE_TOLERANCE_RATIO)
            matched = next(
                (c for c in candidates if abs(c - duration_ms) <= tolerance), None
            )
            if matched is not None:
                chosen = (start, end, matched)
                break
        if chosen is None:
            return None
        start, end, matched = chosen
        if start > 0 or end < n:
            samples = _slice_lap_samples(samples, start, end)
        # On a car change or menu transition `p` can already belong to the new
        # context; the last packet of the OLD context is the one describing
        # the salvaged lap's car, fuel and time of day.
        src = self._last_packet or p
        lap = CompletedLap(
            # Lap 0 stays 0: a later real lap 1 in this session must not
            # collide with the salvaged number.
            number=prev_lap,
            time_ms=matched,
            finished_at=datetime.now(UTC).isoformat(),
            car_id=src.car_id,
            car_category=src.car_category or "",
            samples=samples,
            fuel_start=samples["fuel"][0],
            fuel_end=src.fuel_level,
            tod_ms=src.day_progression_ms,
            salvaged=True,
        )
        lap.max_water_temp = round(self._max_water, 1)
        lap.max_oil_temp = round(self._max_oil, 1)
        lap.min_oil_pressure = round(self._min_oil_pressure, 3)
        lap.gearing = {
            "ratios": [round(r, 4) for r in src.gear_ratios if r > 0],
            "top_speed": round(src.transmission_top_speed, 1),
            "rpm_alert": src.rpm_alert_max,
        }
        lap.compute_metrics()
        return lap

    async def _emit_salvaged(self, lap: CompletedLap, buffered_ticks: int) -> None:
        """Emission mirrors the completing path; only the provenance differs."""
        assert self._session is not None
        self._session.lap_count += 1
        self._apply_span_guard(lap, lap.samples)
        log.info(
            "salvaged lap %d: %d ticks, %d ms (%d pre-roll ticks trimmed)",
            lap.number, lap.total_ticks, lap.time_ms, buffered_ticks - lap.total_ticks,
        )
        await self.on_lap(lap)
        # A salvaged lap means its stream broke off, so whatever streams next
        # — another replay, the user's own driving in the same car — is a
        # different stint and gets its own session. Nothing else would ever
        # separate them: a lap-0 replay parks the counter at 0, so the
        # lap_reset that normally splits sessions (it requires a counter
        # coming down from >0) can never fire. Without the split, the user's
        # laps after watching a replay would land in the replay's session,
        # and excluding that session from bests (#26) would take their own
        # driving with it — while a second replay would inherit the first
        # one's circuit label and lap number.
        self._session = None

    def _log_discard(
        self, prev_lap: int, samples: dict[str, list[float]], p: TelemetryPacket
    ) -> None:
        """One line per lap-sized buffer dropped without salvage (#26).

        Deliberately info, not debug: this is the trace that will tell us,
        from user logs, what a real console's replay stream reported at the
        moment a buffer had to be given up.
        """
        log.info(
            "lap %d buffer dropped without salvage: %d ticks, integrated %d ms, "
            "GT7 candidate times %s",
            prev_lap,
            len(samples["t"]),
            round(samples["t"][-1] * 1000) if samples["t"] else 0,
            self._salvage_candidates(p) or "none",
        )

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

    def _check_lap_clock(self, p: TelemetryPacket) -> None:
        """Cross-check our integrated t axis against GT7's lap timer (#20).

        Packet C carries the console's live current-lap clock. Its zero
        anchors on GT7's lap boundary, not on our first sample, so the
        absolute offset between the clocks is expected and meaningless —
        the first comparable packet of the lap fixes the baseline, and what
        is tracked is how far the two drift APART from there. Growing drift
        means the packet-id integration mis-credited time (frame gaps, pid
        discontinuities), which is exactly what this diagnostic is for.
        Called only on ticks that appended a sample; a no-op below packet C.
        """
        if p.lap_time_ms is None:
            return
        if p.lap_time_ms < self._clock_last_gt_ms:
            # GT7 re-anchored its clock (its lap boundary, slightly offset
            # from ours) — the old baseline no longer applies.
            self._clock_offset_ms = None
        self._clock_last_gt_ms = p.lap_time_ms
        offset = self._elapsed_s * 1000.0 - p.lap_time_ms
        if self._clock_offset_ms is None:
            self._clock_offset_ms = offset
            return
        drift = round(offset - self._clock_offset_ms)
        self._lap_clock_samples += 1
        self._lap_clock_drift_ms = drift
        self._lap_clock_peak_ms = max(self._lap_clock_peak_ms, abs(drift))
        self._lap_clock_worst_ms = max(self._lap_clock_worst_ms, abs(drift))

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
        # Optional columns: appended only on the ticks that carried them, so a
        # recording made on a narrower packet format ends up without the
        # column rather than with a column of invented zeros (prune_optional).
        if p.wheel_rotation is not None:
            s["steer"].append(round(p.wheel_rotation, 4))
        if p.sway is not None:
            s["acc_lat"].append(round(p.sway, 3))
        if p.surge is not None:
            s["acc_long"].append(round(p.surge, 3))
        if p.heave is not None:
            s["acc_vert"].append(round(p.heave, 3))
        if p.throttle_filtered is not None:
            s["throttle_f"].append(round(p.throttle_filtered / 2.55, 1))
        if p.brake_filtered is not None:
            s["brake_f"].append(round(p.brake_filtered / 2.55, 1))
        # GT7's own lap clock beside the samples (packet C only): the salvage
        # trimmer needs to know where inside this buffer the lap began (#26).
        if p.lap_time_ms is not None:
            self._gt_clock.append(p.lap_time_ms)
        # Engine-health aggregates (per-lap, not per-tick)
        self._max_water = max(self._max_water, p.water_temp)
        self._max_oil = max(self._max_oil, p.oil_temp)
        if p.engine_rpm > 1200:  # ignore idle — pressure at idle is meaningless
            self._min_oil_pressure = (
                p.oil_pressure
                if self._min_oil_pressure < 0
                else min(self._min_oil_pressure, p.oil_pressure)
            )
