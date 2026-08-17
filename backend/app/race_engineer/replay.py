"""Post-lap coaching notes, replayed from storage (#23).

CoachingDetector's findings — "repeated front-left lockups into turn 3",
"you lost half a second in turn 5, braked 12 metres earlier" — existed only
as speech, which made them invisible to anyone who never enables voice. This
replays a stored session's laps, in order, through the SAME detector: same
thresholds, same repetition windows, same wording. Not a recording of what
was actually spoken — a recomputation from the stored laps, which is what
lets it work on sessions driven before voice existed, on machines where
voice is off, and on a session someone else recorded.

Fidelity to the live pipeline, where it matters:

  * `ctx.laps` is newest-first and capped at MAX_LAP_HISTORY, so the
    "3 of the last 5 laps" windows read the same laps they would have live.
  * The reference is the session best SO FAR — adopted after the lap that
    set it, exactly when live `refresh_reference` would have — so lap 4 is
    judged against the best of laps 1-3, not against a lap driven later.
    (The Analysis view's own reference picker deliberately plays no part:
    these are the engineer's notes as the session unfolded.)
  * `prev_best_ms` is the best before each lap, so "where you lost time"
    stays silent on a lap that was itself the new best, same as live.

And where it deliberately is not:

  * Stored `counts_for_best` flags are the lap processor's FINAL judgment,
    so the replay is better informed than the live pass was mid-session —
    a lap later found partial never pollutes the comparisons here.
  * The manager's category gates and cooldown clock do not apply: the whole
    point is text for people with voice off, and a written log wants each
    distinct observation once, so repeats of a dedupe key are dropped
    outright instead of rate-limited by a clock that isn't running.
"""

from __future__ import annotations

from typing import Any

from app.processing.analysis import corners_for_lap
from app.race_engineer.detectors.coaching import CoachingDetector
from app.race_engineer.manager import MAX_LAP_HISTORY
from app.race_engineer.state import EngineerContext, LapRecord


def coaching_notes(
    laps: list[dict[str, Any]],
    samples_by_id: dict[int, dict[str, list[float]]],
    events_by_id: dict[int, list[dict[str, Any]]],
    authored: list[dict[str, Any]],
    units: str,
) -> list[dict[str, Any]]:
    """The coaching callouts a session's laps earn, grouped per lap.

    `laps` are the session's lap rows in any order; replay runs them
    chronologically. Returns one entry per lap that has findings:
    `{lap_id, number, findings: [{type, text, corner}]}`.
    """
    ctx = EngineerContext(units=units, session_seq=1, span_confirmed=True)
    detector = CoachingDetector()
    detector.reset(ctx)
    seen: set[str] = set()
    best: int | None = None
    out: list[dict[str, Any]] = []

    for row in sorted(laps, key=lambda r: (int(r["number"]), int(r["id"]))):
        samples = samples_by_id.get(row["id"]) or {}
        record = LapRecord(
            number=int(row["number"]),
            time_ms=int(row["time_ms"]),
            car_id=int(row.get("car_id") or 0),
            fuel_consumed=float(row.get("fuel_consumed") or 0.0),
            counts_for_best=bool(row.get("counts_for_best", True)),
            session_seq=ctx.session_seq,
            events=events_by_id.get(row["id"]) or [],
            samples=samples,
        )
        ctx.laps.insert(0, record)
        del ctx.laps[MAX_LAP_HISTORY:]
        ctx.prev_best_ms = best

        findings: list[dict[str, Any]] = []
        for request in detector.on_lap(record, ctx):
            key = request.dedupe_key or request.event_type
            if key in seen:
                continue
            seen.add(key)
            findings.append({
                "type": request.event_type,
                "text": request.text,
                # Which corner the note is about, when it names one — the UI
                # uses it to zoom the charts and map there.
                "corner": request.metadata.get("corner"),
            })
        if findings:
            out.append({"lap_id": row["id"], "number": record.number, "findings": findings})

        if record.counts_for_best and samples.get("dist") and (
            best is None or record.time_ms < best
        ):
            best = record.time_ms
            ctx.best_lap_ms = best
            ctx.reference = samples
            # 10-90 ms per new best lap; the caller runs the whole replay off
            # the event loop for exactly this reason.
            ctx.corners = corners_for_lap(samples, authored)
    return out
