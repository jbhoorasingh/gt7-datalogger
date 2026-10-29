"""Coaching callouts: repeated lockups/wheelspin and per-corner time loss.

Everything here is spoken after a lap completes, never during the corner —
a warning that arrives mid-apex is a distraction, not coaching.

Every comparison is made on the reference lap's distance axis
(`EngineerContext.on_axis`), and what a lap did at a corner — where it
braked, how slow it got — is `processing.corner_metrics`' answer, the same
one the lap analysis document carries. Nothing here defines a braking point.
"""

from __future__ import annotations

from typing import Any

from app.processing.analysis import time_delta_series
from app.processing.corner_metrics import brake_point_delta, min_speed_delta
from app.race_engineer.detectors.base import Detector
from app.race_engineer.formatter import (
    spoken_corner,
    spoken_distance,
    spoken_gap,
    spoken_speed,
    spoken_wheels,
)
from app.race_engineer.models import CalloutRequest
from app.race_engineer.state import EngineerContext, LapRecord
from app.race_engineer.thresholds import (
    BRAKE_POINT_MIN_M,
    BRAKE_WINDOW_LAPS,
    COACH_BUCKET_M,
    COACH_CORNER_WINDOW_M,
    COACH_LOCKUP_MAX_SLIP,
    COACH_MIN_OCCURRENCES,
    COACH_WHEELSPIN_MIN_SLIP,
    COACH_WINDOW_LAPS,
    CORNER_APEX_SPEED_DIFF_MIN_KMH,
    CORNER_BRAKE_DIFF_MIN_M,
    CORNER_LOSS_MIN_MS,
)

DELTA_STEP_M = 5.0
# Laps between two coaching calls about the same wheel and place. Hearing the
# same observation every lap is nagging, not engineering.
REPEAT_GAP_LAPS = 2


class CoachingDetector(Detector):
    def __init__(self) -> None:
        self._last_lap_by_key: dict[str, int] = {}

    def reset(self, ctx: EngineerContext) -> None:
        self._last_lap_by_key.clear()

    def on_lap(self, lap: LapRecord, ctx: EngineerContext) -> list[CalloutRequest]:
        # Everything below compares one lap against another *by position on
        # the track*. A lap the logger only saw part of has its distance axis
        # anchored wherever capture began, so those comparisons are fiction
        # until enough laps agree on the distance (see LapProcessor's span
        # guard). Silence is the right answer until then.
        if not lap.counts_for_best or not ctx.span_confirmed:
            return []
        # At most one *driving* observation per lap: lap time and fuel already
        # use the post-lap window, and the budget is 1-3 messages a lap. A
        # bottoming note is setup feedback in its own category, so it doesn't
        # take the coaching slot with it.
        repeated = self._repeated_events(lap, ctx)
        if repeated and repeated[0].event_type != "repeated_bottoming":
            return repeated
        return repeated + (
            self._braking_point(lap, ctx) or self._corner_time_loss(lap, ctx)
        )

    # --- braking point ------------------------------------------------------

    def _braking_point(self, lap: LapRecord, ctx: EngineerContext) -> list[CalloutRequest]:
        """Consistently braking earlier (or later) than the reference lap.

        Independent of whether that corner also cost time: a driver asking
        "am I braking too early?" wants the answer even on a lap that was
        quick elsewhere. Consistency across laps is what makes it worth
        saying — one early brake is a moment, not a habit.
        """
        ref = ctx.reference
        if not ref or not ctx.corners:
            return []
        recent = [
            rec
            for rec in ctx.laps[:BRAKE_WINDOW_LAPS]
            if rec.counts_for_best and rec.samples.get("dist")
        ]
        if len(recent) < BRAKE_WINDOW_LAPS:
            return []

        theirs = ctx.reference_measures()
        mine = [ctx.on_axis(rec).measures(ctx.corners) for rec in recent]
        worst_corner: int | None = None
        worst_delta = 0.0
        for corner in ctx.corners:
            entry, exit_ = float(corner["entry_dist"]), float(corner["exit_dist"])
            if entry >= exit_:  # wraps the start line; distances aren't comparable
                continue
            number = int(corner["n"])
            deltas = [brake_point_delta(m.get(number), theirs.get(number)) for m in mine]
            if any(d is None for d in deltas):
                continue
            values = [d for d in deltas if d is not None]
            # Every lap on the same side of the reference, and the mildest of
            # them still past the threshold.
            if not (all(d < 0 for d in values) or all(d > 0 for d in values)):
                continue
            mildest = min(values, key=abs)
            if abs(mildest) < BRAKE_POINT_MIN_M or abs(mildest) <= abs(worst_delta):
                continue
            worst_delta, worst_corner = mildest, int(corner["n"])

        if worst_corner is None:
            return []
        early = worst_delta < 0
        return [
            CalloutRequest(
                event_type="braking_early" if early else "braking_late",
                text=(
                    f"You are braking {'early' if early else 'late'} into "
                    f"{spoken_corner(worst_corner, ctx.corner_name(worst_corner))}, about "
                    f"{spoken_distance(abs(worst_delta), ctx.units)}."
                ),
                message_key=f"coaching.braking_{'early' if early else 'late'}",
                message_args={
                    "corner": worst_corner,
                    "corner_name": ctx.corner_name(worst_corner),
                    "meters": round(abs(worst_delta)),
                    "units": ctx.units,
                },
                dedupe_key=f"braking:{ctx.session_seq}:{worst_corner}",
                metadata={
                    "corner": worst_corner,
                    "delta_m": round(worst_delta, 1),
                    "laps": len(recent),
                },
            )
        ]

    # --- repeated lockups / wheelspin ---------------------------------------

    def _repeated_events(self, lap: LapRecord, ctx: EngineerContext) -> list[CalloutRequest]:
        """Same wheel, same stretch of track, several times in recent laps."""
        buckets: dict[tuple[str, str, int], list[float]] = {}
        for record in ctx.laps[:COACH_WINDOW_LAPS]:
            # Where the event happened on the reference's axis, which is the
            # axis the corners are on: "the same stretch of track" and "the
            # corner it was braking for" are both questions of place.
            for event in ctx.on_axis(record).events:
                kind = str(event.get("type", ""))
                if kind not in ("lockup", "wheelspin", "bottoming"):
                    continue
                severity = float(event.get("severity", 0.0))
                if kind == "lockup" and severity > COACH_LOCKUP_MAX_SLIP:
                    continue
                if kind == "wheelspin" and severity < COACH_WHEELSPIN_MIN_SLIP:
                    continue
                start = float(event.get("start_dist", 0.0))
                wheels: list[str] = list(event.get("wheels") or [])
                for wheel in wheels:
                    buckets.setdefault(
                        (kind, wheel, int(start // COACH_BUCKET_M)), []
                    ).append(start)

        best: tuple[str, str, int] | None = None
        for key, hits in buckets.items():
            if len(hits) < COACH_MIN_OCCURRENCES:
                continue
            if best is None or len(hits) > len(buckets[best]):
                best = key
        if best is None:
            return []

        kind, wheel, bucket = best
        hits = buckets[best]
        dedupe = f"{kind}:{wheel}:{bucket}"
        last = self._last_lap_by_key.get(dedupe)
        if last is not None and lap.number - last < REPEAT_GAP_LAPS:
            return []
        self._last_lap_by_key[dedupe] = lap.number

        dist = sum(hits) / len(hits)
        # A lockup belongs to the corner it is braking for; wheelspin to the
        # one just exited. Neither usually sits inside a corner's own extent.
        corner = ctx.corner_at(dist) or (
            ctx.corner_ahead(dist, COACH_CORNER_WINDOW_M)
            if kind in ("lockup", "bottoming")
            else ctx.corner_behind(dist, COACH_CORNER_WINDOW_M)
        )
        # Wording differs when the corner is unknown: "lockups into the next
        # braking zone" and "wheelspin on the exit of the next braking zone"
        # both read as mistakes.
        where = spoken_corner(corner, ctx.corner_name(corner))
        if kind == "lockup":
            into = "into" if corner else "in"
            text = f"Repeated {spoken_wheels([wheel])} lockups {into} {where}."
            event_type = "repeated_lockups"
        elif kind == "bottoming":
            # Setup feedback rather than driving feedback: the same corner
            # grounding the car every lap is ride height, not the driver.
            text = f"The car is bottoming out {'at' if corner else 'in'} {where}."
            event_type = "repeated_bottoming"
        else:
            text = (
                f"Repeated wheelspin on the exit of {where}."
                if corner
                else "Repeated wheelspin on corner exit."
            )
            event_type = "repeated_wheelspin"
        return [
            CalloutRequest(
                event_type=event_type,
                text=text,
                message_key=f"coaching.{event_type}",
                message_args={
                    "wheel": wheel,
                    "corner": corner,
                    "corner_name": ctx.corner_name(corner),
                    "count": len(hits),
                },
                dedupe_key=f"{event_type}:{ctx.session_seq}:{dedupe}",
                metadata={
                    "count": len(hits),
                    "dist_m": round(dist, 1),
                    "corner": corner,
                    "wheels": [wheel],
                },
            )
        ]

    # --- corner time loss ---------------------------------------------------

    def _corner_time_loss(self, lap: LapRecord, ctx: EngineerContext) -> list[CalloutRequest]:
        """Where this lap lost the most against the session best.

        Only for laps that were slower overall: telling a driver who just set
        a personal best where they "lost time" reads as a bug, not coaching.
        """
        ref = ctx.reference
        if not ref or not ctx.corners or ctx.prev_best_ms is None:
            return []
        if lap.time_ms <= ctx.prev_best_ms:
            return []
        placed = ctx.on_axis(lap)
        series = time_delta_series(placed.samples, ref, step=DELTA_STEP_M)
        deltas = series["delta_ms"]
        if len(deltas) < 2:
            return []

        def delta_at(dist: float) -> float:
            index = min(len(deltas) - 1, max(0, round(dist / DELTA_STEP_M)))
            return deltas[index]

        worst_corner: dict[str, Any] | None = None
        worst_loss = 0.0
        for corner in ctx.corners:
            entry, exit_ = float(corner["entry_dist"]), float(corner["exit_dist"])
            # A start/finish corner wraps past the line, where the delta
            # series restarts at zero — the subtraction would be meaningless.
            if entry >= exit_:
                continue
            loss = delta_at(exit_) - delta_at(entry)
            if loss > worst_loss:
                worst_loss, worst_corner = loss, dict(corner)
        if worst_corner is None or worst_loss < CORNER_LOSS_MIN_MS:
            return []

        number = int(worst_corner["n"])
        entry = float(worst_corner["entry_dist"])
        exit_ = float(worst_corner["exit_dist"])
        mine = placed.measures(ctx.corners).get(number)
        theirs = ctx.reference_measures().get(number)
        brake_raw = brake_point_delta(mine, theirs)
        apex_raw = min_speed_delta(mine, theirs)
        # Only differences past the noise floor are worth a clause; the
        # metadata keeps the measurement either way.
        brake_delta = (
            brake_raw
            if brake_raw is not None and abs(brake_raw) >= CORNER_BRAKE_DIFF_MIN_M
            else None
        )
        apex_delta = (
            apex_raw
            if apex_raw is not None and abs(apex_raw) >= CORNER_APEX_SPEED_DIFF_MIN_KMH
            else None
        )
        detail = _detail_phrase(brake_delta, apex_delta, ctx.units)

        if detail:
            text = (
                f"You lost {spoken_gap(worst_loss)} in "
                f"{spoken_corner(number, ctx.corner_name(number))}. {detail}"
            )
        else:
            text = (
                f"Most time was lost in {spoken_corner(number, ctx.corner_name(number))}. "
                f"You were {spoken_gap(worst_loss)} slower."
            )
        return [
            CalloutRequest(
                event_type="corner_time_loss",
                text=text,
                message_key="coaching.corner_time_loss",
                # The deltas are None when the sentence leaves them out.
                message_args={
                    "corner": number,
                    "corner_name": ctx.corner_name(number),
                    "loss_ms": round(worst_loss),
                    "brake_delta_m": round(brake_delta, 1) if brake_delta is not None else None,
                    "apex_delta_kmh": round(apex_delta, 1) if apex_delta is not None else None,
                    "units": ctx.units,
                },
                dedupe_key=f"corner_time_loss:{ctx.session_seq}:{lap.number}",
                metadata={
                    "corner": number,
                    "loss_ms": round(worst_loss),
                    "apex_dist": worst_corner.get("apex_dist"),
                    "brake_delta_m": round(brake_raw, 1) if brake_raw is not None else None,
                    "apex_delta_kmh": round(apex_raw, 1) if apex_raw is not None else None,
                },
            )
        ]


# --- how the corner was driven differently -----------------------------------


def _detail_phrase(
    brake_delta: float | None, apex_delta: float | None, units: str
) -> str:
    """The "you braked X earlier and carried Y less" half of the callout.

    Empty when neither difference clears the noise floor — the plain wording
    is better than inventing a cause for the time loss. The caller has
    already dropped a difference below it (to None), so its arguments match.
    """
    clauses: list[str] = []
    if brake_delta is not None:
        when = "earlier" if brake_delta < 0 else "later"
        clauses.append(f"braked {spoken_distance(abs(brake_delta), units)} {when}")
    if apex_delta is not None:
        more = "less" if apex_delta < 0 else "more"
        clauses.append(
            f"carried {spoken_speed(abs(apex_delta), units)} {more} at the apex"
        )
    if not clauses:
        return ""
    return f"You {' and '.join(clauses)}."
