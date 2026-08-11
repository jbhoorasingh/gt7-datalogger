"""Live surface survey (issue #37, the seed of #38/#39).

Validates the packet-C `surface_types` encoding and the wheel-contact
derivation on real hardware: while a survey is running, every per-wheel
surface char is histogrammed, chars the mapping doesn't know are flagged,
and every surface TRANSITION is logged with derived wheel-contact positions
(car position + velocity-heading rotation of (±wheelbase/2, ±track/2)).

This lives server-side in the 60 Hz packet path, not in the browser:
transitions are single-tick events, and the ~30 Hz live WebSocket stream
would miss half of them. The Survey view is a window onto this state —
transitions are pushed to it as they happen, a breadcrumb trail of the path
driven shows coverage, and the full record is written to a JSONL file for
offline analysis (raw rotation floats included, so the euler-vs-quaternion
question can be settled after the drive).

Track width is NOT broadcast by GT7, but it can be MEASURED: riding all four
wheels over one surface edge and back pins the crossing contact points onto
a single boundary line. The same wheel's out-and-back crossings give the
edge DIRECTION (a single one-way pass cannot — the rear wheels retrace the
fronts' paths, leaving the edge angle unconstrained); opposite-side wheels
crossing the same line then fix the width, and remaining same-side wheels
must agree the points are collinear, which rejects two-edged strips, curved
kerbs and mid-corner crossings. The median over accepted crossings replaces
the assumed width once enough agree (the assumption remains the fallback,
and the value stored in the JSONL meta header). The "right" vector is
assumed to be (forward_z, -forward_x); if kerb contacts come out mirrored
left/right, that sign is wrong — flip it and note it in
docs/internals/surface-survey.md.
"""

from __future__ import annotations

import json
import logging
import math
from collections import Counter, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import IO, Any

from app.models import TelemetryPacket
from app.processing import car_width, track_bundle
from app.processing.surface import CHAR_CODES

log = logging.getLogger(__name__)

WHEELS = ("FL", "FR", "RL", "RR")
MIN_HEADING_SPEED_MPS = 3.0  # below this the velocity heading is noise
DEFAULT_TRACK_WIDTH_M = 1.6
RECENT_TRANSITIONS = 50  # kept in memory for the status endpoint / view seed

# GT7 does NOT broadcast a track-limits/penalty field in any known packet —
# the documented flag bits stop at TCS (bit 11). If one exists it can only
# hide in the undocumented upper bits, so the survey watches them: any of
# these ever activating on real hardware is a finding.
DOCUMENTED_FLAG_BITS = 12  # bits 0..11 are known (SimulatorFlags)

# --- driven-path breadcrumb ---------------------------------------------------
TRAIL_MIN_STEP_M = 2.0  # don't record a point until the car moved this far
TRAIL_MAX_POINTS = 20_000  # on overflow the trail is decimated 2:1

# Border-edge points are the track taking shape — every lap adds more, and
# unlike breadcrumbs each one is unique boundary evidence, so they are kept
# server-side for the whole run (the browser only ever holds a window of
# recent transitions) and served incrementally like the trail.
EDGES_MAX_POINTS = 50_000  # hard backstop; the JSONL always has everything

# Autosave the labeled run's bundle roughly once a minute: dev reloads,
# crashes and hard kills must never cost more than the last few corners
# (graceful shutdown saves too, but never rely on shutdowns being graceful).
AUTOSAVE_PACKETS = 3600  # ~60 s at 60 Hz

# Finish line: GT7 increments current_lap exactly as the car crosses the
# start/finish line, so each rollover pins a point ON the line. One crossing
# locates it provisionally; repeat crossings landing close together make it
# confident. Guards keep menu/restart lap-counter flicker out.
FINISH_MIN_SPEED_MPS = 5.0
FINISH_CONFIDENT_CROSSINGS = 2
FINISH_CONFIDENT_SPREAD_M = 15.0
FINISH_KEEP = 50

# Manual boundary marking: the driver declares "the boundary is on my
# left/right right now, and it is <kind>". Needed where surface data is
# silent — paved run-off reads as plain tarmac, and wall-lined track has no
# off-surface at all — so while a side is armed, the survey samples edge
# points from the car's own path (that side's wheel line) every few meters.
# kind "edge" = ordinary track edge, "runoff" = outer limit of paved
# run-off (excluded from road fill), "wall" = wall-lined boundary.
MARK_KINDS = ("edge", "runoff", "wall")
MARK_STEP_M = 2.0  # one manual edge point per this much travel

# --- axle track width from yaw rate (primary estimator) -----------------------
# Every corner measures the axle track for free. The outer wheels travel a
# larger radius than the inner ones, so their rolling speeds differ by exactly
# the yaw rate times the track width:
#
#     |v_outer - v_inner| = |yaw rate| * track_width      (v = rps * radius)
#
# GT7 broadcasts wheel_rps, tire_radius and angular_velocity_y, so this needs
# no special driving at all — unlike the edge-ride solver below, which asks
# for a deliberate out-and-back over one boundary and, across a full real
# session of heavy edge riding, accepted not one sample.
#
# BOTH axles are tried every tick and the plausibility range decides which
# one spoke, because a locked or spool differential forces its axle's wheels
# to identical speed no matter what the car is doing. Measured on real
# hardware: this car's rear wheels report the SAME speed to the centimetre
# even coasting (v -82.31 / -82.31 at zero throttle), so the rear axle
# carries no width information at all, while the free front axle answers
# 1.66-1.80 m consistently. A locked axle yields ~0 and falls outside the
# range on its own, so nothing here needs to know the drivetrain layout.
#
# Sign conventions cancel by taking magnitudes, so GT7's yaw sign never has
# to be pinned down. Steering barely matters: the lateral separation term
# omega*t dominates the difference between the two wheels on an axle.
YAW_MIN_RAD_S = 0.15  # below this the denominator is mostly noise
YAW_MIN_SPEED_MPS = 8.0  # crawling: wheel speeds are unreliable
# Braking is the one pedal that reliably corrupts this: ABS modulates wheels
# individually, and the same real capture that gave a steady 1.7-1.8 m
# produced 1.22, 2.03 and 4.87 m under brake pressure. Throttle needs no
# gate — wheelspin lifts an axle's MEAN off the car's speed, which the slip
# check below catches, and a torque-locked axle is already self-rejecting.
YAW_MAX_BRAKE = 8  # 0..255
YAW_SLIP_TOL = 0.05  # |axle mean wheel speed / car speed - 1| allowed
YAW_MIN_SAMPLES = 60  # ~1 s of qualifying cornering before it is trusted
YAW_KEEP_SAMPLES = 4000

# --- track-width auto-estimation (fallback: deliberate edge ride) --------------
# A measurement needs an out-and-back ride over ONE edge: the same wheel
# crossing X→Y and later Y→X (edge direction), at least one opposite-side
# crossing of the same line (the width equation), and a further same-side
# crossing agreeing the points are collinear (strip/curve rejection).
CROSSING_WINDOW_TICKS = 180  # ~3 s at 60 Hz: the whole ride must fit
CROSSING_HEADING_MIN_DOT = 0.95  # cos ~18°: near-straight rides only
MIN_EDGE_SEGMENT_M = 1.0  # out/back points closer than this give a bad angle
COLLINEAR_TOL_M = 0.25  # same-side agreement required of a straight edge
WIDTH_DENOM_MIN = 0.3  # crossing too perpendicular: no width information
WIDTH_RANGE_M = (0.8, 2.4)  # plausible axle track widths
WIDTH_MIN_SAMPLES = 3  # accepted rides needed before the estimate is used
WIDTH_KEEP_SAMPLES = 200


def _border_side(prev: str, cur: str) -> str | None:
    """"L"/"R" when a transition unambiguously belongs to one track border.

    Wheel order is FL FR RL RR: indices 0/2 are the left side, 1/3 the
    right. The verdict needs the whole opposite side on plain tarmac in
    both states — once wheels of both sides are off the racing surface the
    car may be anywhere (deep excursion, re-entry), so no side is claimed.
    """
    changed = [i for i in range(4) if prev[i] != cur[i]]
    if not changed:
        return None
    left = all(i in (0, 2) for i in changed)
    right = all(i in (1, 3) for i in changed)
    if left and all(prev[i] == "T" and cur[i] == "T" for i in (1, 3)):
        return "L"
    if right and all(prev[i] == "T" and cur[i] == "T" for i in (0, 2)):
        return "R"
    return None


@dataclass(slots=True)
class _Crossing:
    """One wheel's char flip, positioned for the width solver."""

    pid: int
    wheel: int  # index into WHEELS
    sig: tuple[str, str]  # (from char, to char)
    px: float  # car position, pulled back half a tick: the flip is seen on
    pz: float  # the first packet AFTER the wheel crossed the line
    fx: float  # forward unit (from velocity)
    fz: float
    wheelbase_m: float
    surface: str  # full 4-char state AFTER the flip (for same-line gating)


class SurfaceSurvey:
    """Feed packets while active; ask for status; stop to close the log."""

    def __init__(self) -> None:
        self.active = False
        self.track_width_m = DEFAULT_TRACK_WIDTH_M
        self.started_at: str | None = None
        # What this run describes: the per-track grid (#38) needs samples
        # keyed by circuit, and the JSONL must be joinable back to the laps
        # recorded during the same drive. Both may update mid-run (session
        # restart, late track identification).
        self.track = ""
        # True when the label was typed by the user (never auto-overwritten);
        # auto-filled labels follow identification across circuit changes.
        self.track_locked = False
        self.session_id: int | None = None
        self.log_path: Path | None = None
        self._out: IO[str] | None = None
        self._prev: str | None = None
        self.packets = 0
        self.no_surface_packets = 0
        self.transitions = 0
        self.char_counts: list[Counter[str]] = [Counter() for _ in WHEELS]
        self.unknown_chars: Counter[str] = Counter()
        self.recent: deque[dict[str, Any]] = deque(maxlen=RECENT_TRANSITIONS)
        # Path driven while surveying, decimated 2:1 whenever it overflows.
        # The epoch tells incremental readers their point indices went stale.
        self.trail: list[list[float]] = []
        self.trail_epoch = 0
        self._trail_step = TRAIL_MIN_STEP_M
        # Every border-edge point of the run — the track taking shape.
        # Append-only within a run; the epoch bumps when a new run starts.
        # One record per metre per side on the bundle grid; re-driving mapped
        # ground casts a vote on what that metre is rather than duplicating
        # it, so the map can change its mind (see track_bundle).
        self.edges: list[dict[str, Any]] = []
        self.edges_epoch = 0
        self._edge_index: dict[tuple[int, int, str], dict[str, Any]] = {}
        # Ordinal of this run in its circuit's bundle: votes are counted once
        # per run, so every vote cast this run carries it — alongside this
        # installation's source id, without which the ordinal means nothing
        # once two people's bundles meet (#47).
        self._run_no = 1
        self._source = ""
        # Manual marking state (see MARK_KINDS above).
        self.mark_side: str | None = None  # "L" | "R" | None = off
        self.mark_kind: str = "edge"
        self._last_mark: tuple[float, float] | None = None
        self._last_straddle: tuple[float, float] | None = None
        self._prev_lap: int | None = None
        self.finish_crossings: list[dict[str, float]] = []
        self._data_dir: Path | None = None
        self._since_autosave = 0
        # Meta of the bundle this run resumed from (None = fresh circuit).
        self.bundle_info: dict[str, Any] | None = None
        self._crossings: deque[_Crossing] = deque(maxlen=64)
        self._width_estimates: list[float] = []
        # Per-tick yaw-rate width samples (see YAW_* above), and how many
        # ticks were rejected and why — a width that never converges should
        # say which gate is eating the data, not just sit at "assumed".
        self._yaw_widths: list[float] = []
        self._yaw_rejects: Counter[str] = Counter()
        # Width already measured for the car being driven, from earlier runs
        # (GT7 broadcasts the car id). Applied from the first tick, so a run
        # no longer lays its opening points at the assumption.
        self._car_id: int | None = None
        self._car_widths: dict[int, dict[str, Any]] = {}
        self._remembered_width_m: float | None = None
        # Undocumented packet-flag bits seen active: bit index -> tick count.
        self.unknown_flag_ticks: Counter[int] = Counter()

    def start(
        self,
        data_dir: Path,
        track_width_m: float,
        track: str = "",
        track_user_set: bool = False,
        session_id: int | None = None,
    ) -> None:
        if self.active:
            return
        self.track_width_m = track_width_m
        self.track = track
        self.track_locked = track_user_set
        self.session_id = session_id
        self.started_at = datetime.now(UTC).isoformat()
        self.packets = 0
        self.no_surface_packets = 0
        self.transitions = 0
        self.char_counts = [Counter() for _ in WHEELS]
        self.unknown_chars = Counter()
        self.recent.clear()
        self._prev = None
        self.trail = []
        self.trail_epoch += 1
        self._trail_step = TRAIL_MIN_STEP_M
        self.edges = []
        self.edges_epoch += 1
        self._edge_index = {}
        self._run_no = 1
        self._source = track_bundle.source_id(data_dir)
        self.mark_side = None
        self.mark_kind = "edge"
        self._last_mark = None
        self._last_straddle = None
        self._prev_lap = None
        self.finish_crossings = []
        self._data_dir = data_dir
        self._since_autosave = 0
        self.bundle_info = None
        self._crossings.clear()
        self._width_estimates = []  # widths are per-car; a new run may swap cars
        self._yaw_widths = []
        self._yaw_rejects = Counter()
        self._car_id = None
        self._car_widths = car_width.load(data_dir)
        self._remembered_width_m = None
        self.unknown_flag_ticks = Counter()
        data_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        self.log_path = data_dir / f"surface_survey_{ts}.jsonl"
        self._out = self.log_path.open("w", encoding="ascii")
        # Header line, so the log stays self-describing offline: the track
        # width assumption in particular is invisible in the records that
        # were derived with it (each record also carries the tw_m it used).
        self._out.write(json.dumps({"meta": {
            "started_at": self.started_at,
            "track_width_m": track_width_m,
            "track": track,
            "session_id": session_id,
            "wheels": list(WHEELS),
        }}, separators=(",", ":")) + "\n")
        self._out.flush()
        self.active = True
        log.info("surface survey started (track %r, width %.2f m) -> %s",
                 track or "?", track_width_m, self.log_path)
        if self.track:
            self._load_bundle()  # start from everything already mapped here

    def set_track(self, name: str, lock: bool = False) -> None:
        """Update the circuit label mid-run — and put it in the JSONL, which
        must stay joinable to a circuit offline (a header written before
        identification carries no label).

        `lock` marks the label as the driver's own, so later
        auto-identification leaves it alone. Assigning a label to a run that
        had none keeps everything it has accumulated: the flush below only
        fires when LEAVING an already-labeled circuit.
        """
        if lock:
            self.track_locked = True
        if name == self.track:
            return
        if self.active and self.track:
            # Leaving a labeled circuit (session restart; the next one may be
            # a different track): flush what this run learned to its bundle,
            # then clear the run's evidence so it can never pollute another
            # circuit's bundle. If the same circuit is identified again, the
            # bundle load below restores everything just saved.
            self._save_bundle(count_run=False)
            self.edges = []
            self.edges_epoch += 1
            self._edge_index = {}
            self._run_no = 1
            self.finish_crossings = []
            self.bundle_info = None
        self.track = name
        if self.active and self._out is not None and name:
            self._out.write(json.dumps({"track": name}, separators=(",", ":")) + "\n")
            self._out.flush()
        if self.active and name:
            self._load_bundle()

    def _load_bundle(self) -> None:
        if self._data_dir is None or not self.track:
            return
        doc = track_bundle.load(self._data_dir, self.track)
        if doc is None:
            return
        # Now that the circuit is known, so is this run's ordinal in it. Any
        # points laid before identification voted under a placeholder run
        # number that the bundle's own history outranks — restamp them, or
        # the pre-identification evidence merges in as no vote at all. The
        # ordinal counts THIS installation's runs: a bundle pulled from
        # someone who has driven here 30 times must not push our run 2 to 31.
        self._run_no = doc["meta"]["source_runs"].get(self._source, 0) + 1
        for e in self.edges:
            e["run"] = self._run_no
            for sources in e["votes"].values():
                mine = sources.get(self._source)
                if mine is not None:
                    mine[1] = self._run_no
        # Bundle first, this run's (few, pre-identification) points merged in;
        # the list is replaced wholesale, so incremental readers must resync.
        self.edges = track_bundle.merge_edges(doc["edges"], self.edges)
        self.edges_epoch += 1
        self._edge_index = {track_bundle.edge_key(e): e for e in self.edges}
        self.finish_crossings = track_bundle.merge_finish(
            doc["finish_crossings"], self.finish_crossings
        )
        self.bundle_info = {**doc["meta"], "points": len(doc["edges"])}
        log.info("resumed track bundle %r: %d points from %d runs",
                 self.track, len(doc["edges"]), doc["meta"]["runs"])

    def _save_bundle(self, count_run: bool) -> None:
        if self._data_dir is None or not self.track:
            return
        if not self.edges and not self.finish_crossings:
            return
        self._since_autosave = 0
        self.bundle_info = track_bundle.save(
            self._data_dir, self.track, self.edges, self.finish_crossings, count_run
        )

    def stop(self) -> None:
        if self.active:
            self._save_bundle(count_run=True)
            self._save_car_width()
        self.active = False
        if self._out is not None:
            self._out.close()
            self._out = None
        if self.transitions or self.packets:
            log.info("surface survey stopped: %d packets, %d transitions",
                     self.packets, self.transitions)

    def _follow_car(self, p: TelemetryPacket) -> None:
        """Track which car is being driven; its width is a different number."""
        if p.car_id == self._car_id:
            return
        self._car_id = p.car_id
        self._yaw_widths = []  # samples belong to the car that produced them
        self._yaw_rejects = Counter()
        known = self._car_widths.get(p.car_id)
        self._remembered_width_m = known["width_m"] if known else None
        if self._remembered_width_m is not None:
            log.info("car %s: applying remembered axle track %.3f m",
                     p.car_id, self._remembered_width_m)

    def _save_car_width(self) -> None:
        """Persist this run's measurement, if it produced one."""
        measured = self.yaw_width_m
        if self._data_dir is None or self._car_id is None or measured is None:
            return
        self._car_widths = car_width.remember(
            self._data_dir, self._car_id, measured, len(self._yaw_widths)
        )

    def _measure_width_from_yaw(self, p: TelemetryPacket) -> None:
        """One axle-track sample from this tick's cornering, if it qualifies.

        Both axles are offered; a differential-locked one answers ~0 and is
        filtered out by the plausible range, so the free axle is the one that
        gets heard without anyone naming the drivetrain.
        """
        if p.speed_mps < YAW_MIN_SPEED_MPS:
            self._yaw_rejects["slow"] += 1
            return
        yaw = abs(p.angular_velocity_y)
        if yaw < YAW_MIN_RAD_S:
            self._yaw_rejects["straight"] += 1
            return
        if p.brake > YAW_MAX_BRAKE:
            self._yaw_rejects["braking"] += 1
            return
        lo, hi = WIDTH_RANGE_M
        axles = (
            (p.wheel_rps_fl * p.tire_radius_fl, p.wheel_rps_fr * p.tire_radius_fr),
            (p.wheel_rps_rl * p.tire_radius_rl, p.wheel_rps_rr * p.tire_radius_rr),
        )
        best: float | None = None
        slipped = False
        for left, right in axles:
            mean = (abs(left) + abs(right)) / 2.0
            if mean <= 0 or abs(mean / p.speed_mps - 1.0) > YAW_SLIP_TOL:
                slipped = True
                continue
            width = abs(abs(right) - abs(left)) / yaw
            if lo <= width <= hi and (best is None or width > best):
                best = width
        if best is None:
            # Nothing plausible: either every axle slipped, or the ones that
            # did not are locked (identical wheel speeds -> a width of ~0).
            self._yaw_rejects["slip" if slipped else "locked_axle"] += 1
            return
        self._yaw_widths.append(best)
        del self._yaw_widths[:-YAW_KEEP_SAMPLES]
        if len(self._yaw_widths) == YAW_MIN_SAMPLES:
            log.info("axle track measured from cornering: %.3f m (%d samples)",
                     median(self._yaw_widths), len(self._yaw_widths))

    @property
    def yaw_width_m(self) -> float | None:
        """Median axle track from cornering — the primary measurement."""
        if len(self._yaw_widths) < YAW_MIN_SAMPLES:
            return None
        return round(median(self._yaw_widths), 3)

    @property
    def width_estimate_m(self) -> float | None:
        """Median of the accepted edge-crossing measurements, if any."""
        if not self._width_estimates:
            return None
        return round(median(self._width_estimates), 3)

    @property
    def width_source(self) -> str:
        """Which number width_in_use_m is currently serving."""
        if self.yaw_width_m is not None:
            return "cornering"
        if self._remembered_width_m is not None:
            return "car-memory"
        if (self.width_estimate_m is not None
                and len(self._width_estimates) >= WIDTH_MIN_SAMPLES):
            return "edge-ride"
        return "assumed"

    @property
    def width_in_use_m(self) -> float:
        """Width applied to contact derivation: measured once trusted.

        Cornering first — it converges within a corner or two and needs no
        deliberate driving — then the edge-ride solver, then the assumption.
        """
        cornering = self.yaw_width_m
        if cornering is not None:
            return cornering
        if self._remembered_width_m is not None:
            return self._remembered_width_m
        estimate = self.width_estimate_m
        if estimate is not None and len(self._width_estimates) >= WIDTH_MIN_SAMPLES:
            return estimate
        return self.track_width_m

    def feed(self, p: TelemetryPacket) -> dict[str, Any] | None:
        """Record one packet; returns the transition record when one occurred."""
        self.packets += 1
        self._since_autosave += 1
        if self._since_autosave >= AUTOSAVE_PACKETS:
            self._since_autosave = 0
            self._save_bundle(count_run=False)
            self._save_car_width()
        if p.surface_types is None:
            self.no_surface_packets += 1
            return None
        if p.is_paused or not p.is_on_track:
            return None
        self._follow_car(p)
        self._measure_width_from_yaw(p)
        self._append_trail(p)
        self._watch_finish_line(p)
        if self.mark_side is not None:
            self._append_manual_edge(p)
        else:
            self._append_straddle_edge(p)
        for bit in range(DOCUMENTED_FLAG_BITS, 16):
            if p.flags & (1 << bit):
                if self.unknown_flag_ticks[bit] == 0:
                    log.warning("undocumented packet flag bit %d active (flags=0x%04x) "
                                "at (%.1f, %.1f)", bit, p.flags, p.position_x, p.position_z)
                self.unknown_flag_ticks[bit] += 1
        surface = p.surface_types
        for i, ch in enumerate(surface):
            self.char_counts[i][ch] += 1
            if ch not in CHAR_CODES:
                if self.unknown_chars[ch] == 0:
                    log.warning("NEW surface char %r on %s at (%.1f, %.1f)",
                                ch, WHEELS[i], p.position_x, p.position_z)
                self.unknown_chars[ch] += 1
        record = None
        if self._prev is not None and surface != self._prev:
            record = self._transition(p, self._prev, surface)
            self.transitions += 1
            self.recent.append(record)
            if self._out is not None:
                self._out.write(json.dumps(record, separators=(",", ":")) + "\n")
                self._out.flush()  # export must see it while the run is live
        self._prev = surface
        return record

    def _watch_finish_line(self, p: TelemetryPacket) -> None:
        """Each lap rollover pins a point on the start/finish line.

        Only exact +1 increments between racing laps count: menu flicker,
        restarts (counter falls back) and grid starts (0 -> 1 away from the
        line) all fail the guards.
        """
        prev = self._prev_lap
        self._prev_lap = p.current_lap
        if prev is None or prev < 1 or p.current_lap != prev + 1:
            return
        if p.speed_mps < FINISH_MIN_SPEED_MPS:
            return
        norm = math.hypot(p.velocity_x, p.velocity_z)
        if norm <= 0 or len(self.finish_crossings) >= FINISH_KEEP:
            return
        crossing = {
            "x": round(p.position_x, 3), "z": round(p.position_z, 3),
            "hx": round(p.velocity_x / norm, 5), "hz": round(p.velocity_z / norm, 5),
            "lap": float(p.current_lap),
        }
        self.finish_crossings.append(crossing)
        if self._out is not None:
            self._out.write(json.dumps({"finish": crossing}, separators=(",", ":")) + "\n")
            self._out.flush()
        log.info("finish-line crossing #%d at (%.1f, %.1f)",
                 len(self.finish_crossings), p.position_x, p.position_z)

    def _finish_summary(self) -> dict[str, Any] | None:
        """Mean crossing point + heading; confident once repeats agree."""
        if not self.finish_crossings:
            return None
        n = len(self.finish_crossings)
        mx = sum(c["x"] for c in self.finish_crossings) / n
        mz = sum(c["z"] for c in self.finish_crossings) / n
        spread = max(
            math.hypot(c["x"] - mx, c["z"] - mz) for c in self.finish_crossings
        )
        hx = sum(c["hx"] for c in self.finish_crossings)
        hz = sum(c["hz"] for c in self.finish_crossings)
        norm = math.hypot(hx, hz)
        if norm < 0.1:  # crossings disagree on direction; trust the first
            hx, hz = self.finish_crossings[0]["hx"], self.finish_crossings[0]["hz"]
        else:
            hx, hz = hx / norm, hz / norm
        return {
            "x": round(mx, 2), "z": round(mz, 2),
            "hx": round(hx, 5), "hz": round(hz, 5),
            "crossings": n,
            "spread_m": round(spread, 1),
            "confident": n >= FINISH_CONFIDENT_CROSSINGS
            and spread <= FINISH_CONFIDENT_SPREAD_M,
        }

    def set_mark(self, side: str | None, kind: str) -> None:
        """Arm or disarm manual boundary marking (validated by the API layer)."""
        self.mark_side = side
        self.mark_kind = kind
        self._last_mark = None
        if side is not None:
            log.info("marking %s boundary on the %s side", kind, side)

    def _append_manual_edge(self, p: TelemetryPacket) -> None:
        """One edge point at the armed side's wheel line, every few meters.

        This is what maps boundaries the surface chars cannot see: hug the
        wall / white line / run-off limit and the driven line becomes the
        boundary polyline.
        """
        if p.speed_mps < MIN_HEADING_SPEED_MPS:
            return
        if self._last_mark is not None:
            lx, lz = self._last_mark
            dx, dz = p.position_x - lx, p.position_z - lz
            if dx * dx + dz * dz < MARK_STEP_M**2:
                return
        norm = math.hypot(p.velocity_x, p.velocity_z)
        if norm <= 0:
            return
        fx, fz = p.velocity_x / norm, p.velocity_z / norm
        rx, rz = fz, -fx
        lat = -self.width_in_use_m / 2.0 if self.mark_side == "L" else self.width_in_use_m / 2.0
        self._last_mark = (p.position_x, p.position_z)
        self._append_edge(
            x=p.position_x + lat * rx, z=p.position_z + lat * rz,
            y=p.position_y,
            hx=fx, hz=fz, side=self.mark_side or "L", kind=self.mark_kind,
            pid=p.packet_id,
        )

    def _append_straddle_edge(self, p: TelemetryPacket) -> None:
        """Continuous automatic border tracing, no button required.

        Surface flips only pin the border at the moment a wheel crosses it;
        a lap driven with one side's wheels HELD off the track produces
        almost no flips and would leave the border untraced between them.
        But that sustained state is itself the evidence: both wheels of one
        side off the tarmac while the other side is fully on it means the
        car is straddling that border, so the off-side wheel line traces
        it — one point per couple of meters, exactly like manual marking.
        """
        surface = p.surface_types
        assert surface is not None  # feed() returned before this otherwise
        left_off = surface[0] != "T" and surface[2] != "T"
        right_off = surface[1] != "T" and surface[3] != "T"
        left_on = surface[0] == "T" and surface[2] == "T"
        right_on = surface[1] == "T" and surface[3] == "T"
        if left_off and right_on:
            side = "L"
        elif right_off and left_on:
            side = "R"
        else:
            # Fully on, fully off, or diagonal — no border underneath the car.
            self._last_straddle = None
            return
        if p.speed_mps < MIN_HEADING_SPEED_MPS:
            return
        if self._last_straddle is not None:
            lx, lz = self._last_straddle
            dx, dz = p.position_x - lx, p.position_z - lz
            if dx * dx + dz * dz < MARK_STEP_M**2:
                return
        norm = math.hypot(p.velocity_x, p.velocity_z)
        if norm <= 0:
            return
        fx, fz = p.velocity_x / norm, p.velocity_z / norm
        rx, rz = fz, -fx
        lat = -self.width_in_use_m / 2.0 if side == "L" else self.width_in_use_m / 2.0
        self._last_straddle = (p.position_x, p.position_z)
        self._append_edge(
            x=p.position_x + lat * rx, z=p.position_z + lat * rz,
            y=p.position_y,
            hx=fx, hz=fz, side=side, kind="straddle", pid=p.packet_id,
        )

    def _append_edge(
        self, x: float, z: float, hx: float, hz: float, side: str, kind: str,
        pid: int, y: float | None = None,
    ) -> None:
        x, z = round(x, 3), round(z, 3)
        key = track_bundle.edge_key({"x": x, "z": z, "side": side})
        known = self._edge_index.get(key)
        if known is not None:
            prior = known["votes"].get(kind, {}).get(self._source)
            if prior is not None and prior[1] >= self._run_no:
                return  # this run already read this metre as this kind
            # Same metre, something new to say about it: a hand-marked wall
            # over ground the straddle tracer had called plain road, or a
            # second run agreeing. Either way it is a vote, not a duplicate.
            track_bundle.cast_vote(known, kind, self._run_no, self._source)
            if known.get("y") is None and y is not None:
                known["y"] = round(y, 3)  # metre mapped before v3: fill it in
            self._log_mark(x, z, hx, hz, side, kind, pid, y)
            return
        # The log line goes out BEFORE the memory cap, which must never cost
        # log completeness; the JSONL has everything.
        self._log_mark(x, z, hx, hz, side, kind, pid, y)
        if len(self.edges) >= EDGES_MAX_POINTS:
            return
        edge = track_bundle.new_edge(
            x, z, hx, hz, side, kind, self._run_no, self._source,
            self.width_in_use_m, y,
        )
        self._edge_index[key] = edge
        self.edges.append(edge)

    def _log_mark(
        self, x: float, z: float, hx: float, hz: float, side: str, kind: str,
        pid: int, y: float | None = None,
    ) -> None:
        """"auto" edges are reconstructable from the transition records;
        manual and straddle-sampled ones exist nowhere else, so they go to
        the JSONL too — one line per vote actually cast, so a re-drive of
        mapped ground does not spam the log.

        The width logged is the one in use NOW, which is what placed this
        sample; the bundle record keeps the width it was first laid with, and
        after a re-vote on known ground the two legitimately differ.
        """
        if self._out is None or kind == "auto":
            return
        line = {
            "x": x, "z": z, "y": round(y, 3) if y is not None else None,
            "hx": round(hx, 5), "hz": round(hz, 5),
            "side": side, "kind": kind, "run": self._run_no,
            "tw": round(self.width_in_use_m, 3), "pid": pid,
        }
        self._out.write(json.dumps({"mark": line}, separators=(",", ":")) + "\n")

    def _append_trail(self, p: TelemetryPacket) -> None:
        x, z = p.position_x, p.position_z
        if self.trail:
            lx, lz = self.trail[-1]
            if (x - lx) ** 2 + (z - lz) ** 2 < self._trail_step**2:
                return
        self.trail.append([round(x, 2), round(z, 2)])
        if len(self.trail) > TRAIL_MAX_POINTS:
            # Halve the resolution instead of forgetting the oldest laps —
            # coverage of the whole drive is the point of the trail.
            self.trail = self.trail[::2]
            self._trail_step *= 2
            self.trail_epoch += 1

    def _transition(self, p: TelemetryPacket, prev: str, cur: str) -> dict[str, Any]:
        speed = p.speed_mps
        heading = None
        contacts: dict[str, list[float]] | None = None
        tw_used: float | None = None
        # speed_mps is its own packet field, decoded separately from the
        # velocity vector — guard the norm too, or a mismatched packet
        # (speed reported, ground-plane velocity zero) divides by zero.
        norm = math.hypot(p.velocity_x, p.velocity_z)
        if speed >= MIN_HEADING_SPEED_MPS and p.wheelbase_m and norm > 0:
            # Ground-plane forward from velocity; the assumed right vector is
            # exactly what the survey validates (see module docstring).
            fx, fz = p.velocity_x / norm, p.velocity_z / norm
            self._record_crossings(p, prev, cur, fx, fz)
            rx, rz = fz, -fx
            half_wb = p.wheelbase_m / 2.0
            tw_used = self.width_in_use_m
            half_tw = tw_used / 2.0
            offsets = {
                "FL": (half_wb, -half_tw),
                "FR": (half_wb, half_tw),
                "RL": (-half_wb, -half_tw),
                "RR": (-half_wb, half_tw),
            }
            heading = math.atan2(p.velocity_x, p.velocity_z)
            contacts = {
                w: [
                    round(p.position_x + fwd * fx + lat * rx, 3),
                    round(p.position_z + fwd * fz + lat * rz, 3),
                ]
                for w, (fwd, lat) in offsets.items()
            }
        # Which track border this contact belongs to, relative to the
        # direction of travel: wheels of one side touching kerb/loose while
        # the whole other side stays on tarmac can only happen at that
        # side's edge of the road. Accumulated left/right-tagged points ARE
        # the two perimeter polylines forming — kept in self.edges for the
        # whole run so the map can draw the track taking shape lap by lap.
        border = _border_side(prev, cur)
        if border is not None and contacts is not None:
            for i in range(4):
                if prev[i] == cur[i]:
                    continue
                point = contacts[WHEELS[i]]  # norm > 0: contacts exist
                self._append_edge(
                    x=point[0], z=point[1], y=p.position_y,
                    hx=p.velocity_x / norm, hz=p.velocity_z / norm,
                    side=border, kind="auto", pid=p.packet_id,
                )
        return {
            "n": self.transitions + 1,
            "pid": p.packet_id,
            "session_id": self.session_id,
            "lap": p.current_lap,
            "from": prev,
            "to": cur,
            "changed": [WHEELS[i] for i in range(4) if prev[i] != cur[i]],
            "border": border,
            # Manual marking active while this transition happened, if any.
            "mark": self.mark_kind if self.mark_side is not None else None,
            "pos": [round(p.position_x, 3), round(p.position_y, 3), round(p.position_z, 3)],
            "vel": [round(p.velocity_x, 3), round(p.velocity_y, 3), round(p.velocity_z, 3)],
            "speed_mps": round(speed, 2),
            "heading_rad": round(heading, 5) if heading is not None else None,
            # Raw orientation fields, for settling euler-vs-quaternion offline.
            "rotation": [p.rotation_pitch, p.rotation_yaw, p.rotation_roll],
            "rel_north": p.rel_orientation_to_north,
            "wheelbase_m": p.wheelbase_m,
            # Raw flags, so undocumented bits can be correlated with
            # off-track moments offline (no track-limits field is known).
            "flags": p.flags,
            "tw_m": round(tw_used, 3) if tw_used is not None else None,
            "contacts": contacts,
        }

    # --- track-width estimation ----------------------------------------------

    def _record_crossings(
        self, p: TelemetryPacket, prev: str, cur: str, fx: float, fz: float
    ) -> None:
        assert p.wheelbase_m is not None
        # The flip is observed on the first packet after the wheel crossed,
        # so pull the position back half a tick to center the timing error.
        px = p.position_x - p.velocity_x * (0.5 / 60.0)
        pz = p.position_z - p.velocity_z * (0.5 / 60.0)
        for i in range(4):
            if prev[i] == cur[i]:
                continue
            self._crossings.append(_Crossing(
                pid=p.packet_id, wheel=i, sig=(prev[i], cur[i]),
                px=px, pz=pz, fx=fx, fz=fz, wheelbase_m=p.wheelbase_m,
                surface=cur,
            ))
        self._try_estimate_width(p.packet_id)

    def _try_estimate_width(self, pid: int) -> None:
        recent = [c for c in self._crossings if pid - c.pid <= CROSSING_WINDOW_TICKS]
        groups: dict[frozenset[str], list[_Crossing]] = {}
        for c in recent:
            groups.setdefault(frozenset(c.sig), []).append(c)
        for key, group in groups.items():
            width = self._estimate_from_group(group)
            if width is None:
                continue
            self._width_estimates.append(width)
            del self._width_estimates[:-WIDTH_KEEP_SAMPLES]
            log.info("track width measured: %.2f m (median %.2f m over %d rides)",
                     width, self.width_estimate_m or 0.0, len(self._width_estimates))
            # This edge ride is spent; keep events of other boundary types.
            remaining = [c for c in self._crossings if frozenset(c.sig) != key]
            self._crossings.clear()
            self._crossings.extend(remaining)

    def _estimate_from_group(self, group: list[_Crossing]) -> float | None:
        """One width measurement from an out-and-back ride over one edge.

        Every crossing pins a contact point P + a·f + b·(tw/2)·r onto the
        edge line n·x = const (a = ±wheelbase/2 and b = ±1 known per wheel).
        The same wheel's out/back crossings both lie on the line and their
        b-terms cancel, giving n without knowing tw; each opposite-side
        crossing then yields tw; remaining same-side crossings must agree
        the points are collinear or the "edge" wasn't one straight line.
        """
        if len(group) < 4:
            return None
        f0 = group[0]
        for c in group[1:]:
            if c.fx * f0.fx + c.fz * f0.fz < CROSSING_HEADING_MIN_DOT:
                return None

        def a_of(c: _Crossing) -> float:
            return c.wheelbase_m / 2.0 if c.wheel in (0, 1) else -c.wheelbase_m / 2.0

        def b_of(c: _Crossing) -> float:
            return -1.0 if c.wheel in (0, 2) else 1.0  # left wheels sit at -tw/2

        # Edge direction: the same wheel's out/back pair, widest apart.
        by_wheel: dict[int, list[_Crossing]] = {}
        for c in group:
            by_wheel.setdefault(c.wheel, []).append(c)
        pair: tuple[_Crossing, _Crossing] | None = None
        best_len = MIN_EDGE_SEGMENT_M
        for events in by_wheel.values():
            for i, one in enumerate(events):
                for two in events[i + 1:]:
                    if one.sig != (two.sig[1], two.sig[0]):
                        continue
                    a = a_of(one)
                    dx = (two.px - one.px) + a * (two.fx - one.fx)
                    dz = (two.pz - one.pz) + a * (two.fz - one.fz)
                    length = math.hypot(dx, dz)
                    if length > best_len:
                        best_len = length
                        pair = (one, two)
        if pair is None:
            return None
        out, back = pair
        a = a_of(out)
        ex = ((back.px - out.px) + a * (back.fx - out.fx)) / best_len
        ez = ((back.pz - out.pz) + a * (back.fz - out.fz)) / best_len
        nx, nz = ez, -ex  # normal sign is irrelevant: |tw| is the answer

        def along_normal(c: _Crossing, tw: float) -> float:
            rx, rz = c.fz, -c.fx
            return (nx * c.px + nz * c.pz + a_of(c) * (nx * c.fx + nz * c.fz)
                    + b_of(c) * (tw / 2.0) * (nx * rx + nz * rz))

        # Same-side crossings must land on the pair's line, or this was a
        # two-edged strip / curved kerb — no straight edge to measure against.
        side = b_of(out)
        reference = along_normal(out, self.width_in_use_m)
        validated = False
        for c in group:
            if c is out or c is back or b_of(c) != side:
                continue
            if abs(along_normal(c, self.width_in_use_m) - reference) > COLLINEAR_TOL_M:
                return None
            validated = True
        if not validated:
            return None

        candidates: list[float] = []
        # A genuine full ride has the pair-side wheels already off tarmac
        # when the opposite side crosses the same line. Weaving a narrow
        # lane with boundaries on BOTH sides (grass left AND right) leaves
        # them on tarmac at that moment — the opposite wheels crossed a
        # DIFFERENT parallel line, and using them would solve for the lane
        # separation instead of the axle width.
        pair_wheels = (0, 2) if side < 0 else (1, 3)
        for m in group:
            if b_of(m) == side:
                continue
            if all(m.surface[i] == "T" for i in pair_wheels):
                continue
            for anchor in (out, back):
                denom = (
                    b_of(anchor) * (nx * anchor.fz + nz * -anchor.fx)
                    - b_of(m) * (nx * m.fz + nz * -m.fx)
                )
                if abs(denom) < WIDTH_DENOM_MIN:
                    continue
                num = (
                    nx * (m.px - anchor.px) + nz * (m.pz - anchor.pz)
                    + a_of(m) * (nx * m.fx + nz * m.fz)
                    - a_of(anchor) * (nx * anchor.fx + nz * anchor.fz)
                )
                candidates.append(2.0 * num / denom)
        if not candidates:
            return None
        width = abs(median(candidates))
        if not WIDTH_RANGE_M[0] <= width <= WIDTH_RANGE_M[1]:
            return None
        return round(width, 3)

    def status(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "started_at": self.started_at,
            "track": self.track,
            "session_id": self.session_id,
            "track_width_m": self.track_width_m,
            "width_estimate_m": self.width_estimate_m,
            "width_samples": len(self._width_estimates),
            "width_in_use_m": round(self.width_in_use_m, 3),
            "width_source": self.width_source,
            "car_id": self._car_id,
            "remembered_width_m": self._remembered_width_m,
            "yaw_width_m": self.yaw_width_m,
            "yaw_samples": len(self._yaw_widths),
            "yaw_needed": YAW_MIN_SAMPLES,
            # Why cornering ticks were skipped — so a width that refuses to
            # converge names the gate eating it instead of staying silent.
            "yaw_rejects": dict(self._yaw_rejects),
            "packets": self.packets,
            "no_surface_packets": self.no_surface_packets,
            "transitions": self.transitions,
            "trail_points": len(self.trail),
            "trail_epoch": self.trail_epoch,
            "edge_points": len(self.edges),
            "edges_epoch": self.edges_epoch,
            "finish": self._finish_summary(),
            "bundle": self.bundle_info,
            # This installation's id, stamped on every vote this run casts.
            "source": self._source,
            "run_no": self._run_no,
            "mark_side": self.mark_side,
            "mark_kind": self.mark_kind,
            "histogram": {
                WHEELS[i]: dict(self.char_counts[i].most_common())
                for i in range(len(WHEELS))
            },
            "chars_seen": sorted({c for counter in self.char_counts for c in counter}),
            "known_chars": sorted(CHAR_CODES),
            "unknown_chars": dict(self.unknown_chars),
            "unknown_flag_bits": {str(bit): n for bit, n in self.unknown_flag_ticks.items()},
            "recent": list(self.recent),
            "log_path": str(self.log_path) if self.log_path else None,
        }
