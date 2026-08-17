"""REST API for sessions, laps, analysis, and controls."""

from __future__ import annotations

import asyncio
import csv
import io
import math
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.api.auth import require_admin
from app.processing import analysis
from app.processing.laps import SAMPLE_COLUMNS
from app.processing.tracks import signature_from_samples

if TYPE_CHECKING:
    from app.service import TelemetryService

router = APIRouter(prefix="/api")


def svc(request: Request) -> TelemetryService:
    service: TelemetryService = request.app.state.service
    return service


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/status")
async def status(request: Request) -> dict[str, Any]:
    return await svc(request).status()


# --- sessions & laps --------------------------------------------------------


@router.get("/sessions")
async def sessions(
    request: Request,
    category: str = Query(
        "", max_length=16, description="car category (packet C), e.g. Gr.3 — all when blank"
    ),
) -> list[dict[str, Any]]:
    return await svc(request).repo.list_sessions(category.strip() or None)


@router.delete("/sessions/{session_id}", dependencies=[Depends(require_admin)])
async def delete_session(request: Request, session_id: int) -> dict[str, str]:
    await svc(request).repo.delete_session(session_id)
    return {"status": "deleted"}


@router.get("/sessions/{session_id}/laps")
async def session_laps(request: Request, session_id: int) -> list[dict[str, Any]]:
    laps = await svc(request).repo.list_laps(session_id)
    cars = svc(request).cars
    for lap in laps:
        lap["car_name"] = cars.name(lap["car_id"])
    return laps


@router.get("/laps")
async def laps(request: Request) -> list[dict[str, Any]]:
    laps = await svc(request).repo.list_laps()
    cars = svc(request).cars
    for lap in laps:
        lap["car_name"] = cars.name(lap["car_id"])
    return laps


@router.get("/laps/best")
async def best_lap(
    request: Request,
    track: str = Query(..., min_length=1, max_length=80),
    category: str = Query(..., min_length=1, max_length=16),
) -> dict[str, Any] | None:
    """Fastest full lap at a circuit in one car category (#19).

    Declared BEFORE /laps/{lap_id}: FastAPI matches in declaration order, and
    "best" would otherwise be handed to the lap-id route as a path parameter.
    """
    return await svc(request).repo.best_lap_in(track.strip(), category.strip())


@router.get("/laps/{lap_id}")
async def lap_detail(
    request: Request,
    lap_id: int,
    samples: bool = Query(True, description="include the per-tick sample series"),
) -> dict[str, Any]:
    lap = await svc(request).repo.get_lap(lap_id, with_samples=samples)
    if lap is None:
        raise HTTPException(404, "lap not found")
    lap["car_name"] = svc(request).cars.name(lap["car_id"])
    return lap


@router.delete("/laps/{lap_id}", dependencies=[Depends(require_admin)])
async def delete_lap(request: Request, lap_id: int) -> dict[str, str]:
    await svc(request).repo.delete_lap(lap_id)
    return {"status": "deleted"}


@router.get("/laps/{lap_id}/export")
async def export_lap(request: Request, lap_id: int) -> dict[str, Any]:
    data = await svc(request).repo.export_lap(lap_id)
    if data is None:
        raise HTTPException(404, "lap not found")
    return data


# Channel map for CSV export: sample column -> (display name, unit).
CSV_CHANNELS = (
    ("t", "Time", "s"),
    ("dist", "Distance", "m"),
    ("speed", "Ground Speed", "km/h"),
    ("throttle", "Throttle Pos", "%"),
    ("brake", "Brake Pos", "%"),
    ("gear", "Gear", ""),
    ("rpm", "Engine RPM", "rpm"),
    ("boost", "Boost Pressure", "bar"),
    ("tire_slip", "Tyre Slip Ratio", ""),
    ("yaw_rate", "Yaw Rate", "rad/s"),
    ("pos_x", "Pos X", "m"),
    ("pos_z", "Pos Z", "m"),
    ("body_height", "Ride Height", "mm"),
    ("fuel", "Fuel Level", "L"),
    ("slip_fl", "Tyre Slip FL", ""),
    ("slip_fr", "Tyre Slip FR", ""),
    ("slip_rl", "Tyre Slip RL", ""),
    ("slip_rr", "Tyre Slip RR", ""),
    ("tt_fl", "Tyre Temp FL", "C"),
    ("tt_fr", "Tyre Temp FR", "C"),
    ("tt_rl", "Tyre Temp RL", "C"),
    ("tt_rr", "Tyre Temp RR", "C"),
    ("sus_fl", "Susp Travel FL", "mm"),
    ("sus_fr", "Susp Travel FR", "mm"),
    ("sus_rl", "Susp Travel RL", "mm"),
    ("sus_rr", "Susp Travel RR", "mm"),
    ("aids", "Driver Aids", ""),
    ("surface", "Surface Mask", ""),
    ("steer", "Steering Angle", "rad"),
    # Raw broadcast units — see analysis.accel_calibration for what they turn
    # out to be. Exported unconverted so an external tool calibrates its own way.
    ("acc_lat", "Accel Lateral", ""),
    ("acc_long", "Accel Longitudinal", ""),
    ("acc_vert", "Accel Vertical", ""),
    ("throttle_f", "Throttle Applied", "%"),
    ("brake_f", "Brake Applied", "%"),
)


def _csv_text(value: str) -> str:
    """Neutralize spreadsheet formula injection in text cells."""
    return f"'{value}" if value[:1] in ("=", "+", "-", "@") else value


@router.get("/laps/{lap_id}/export.csv")
async def export_lap_csv(request: Request, lap_id: int) -> PlainTextResponse:
    """MoTeC-compatible CSV export (i2 'CSV file' import, Excel, etc.).

    The "Sample Rate" header is nominal — the `t` column is authoritative
    (it integrates packet-id deltas, so dropped frames widen its steps).
    """
    lap = await svc(request).repo.get_lap(lap_id, with_samples=True)
    if lap is None:
        raise HTTPException(404, "lap not found")
    samples = lap["samples"]
    cols = [c for c in CSV_CHANNELS if c[0] in samples]
    time_ms = lap["time_ms"]
    duration = f"{time_ms // 60000}:{(time_ms % 60000) / 1000:06.3f}"
    car = svc(request).cars.name(lap["car_id"])

    buf = io.StringIO()
    meta = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\n")
    data = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    meta.writerow(["Format", "MoTeC CSV File"])
    meta.writerow(["Device", "GT7 Datalogger"])
    meta.writerow(["Vehicle", _csv_text(car)])
    meta.writerow(["Comment", _csv_text(f"Lap {lap['number']} - {duration}")])
    meta.writerow(["Log Date", _csv_text(str(lap.get("finished_at", "")))])
    meta.writerow(["Sample Rate", "60.000"])
    buf.write("\n")  # blank separator line between metadata and channels
    meta.writerow([name for _, name, _ in cols])
    meta.writerow([unit for _, _, unit in cols])
    n = len(samples["t"])
    for i in range(n):
        # Guard against ragged legacy rows; values are numeric so
        # QUOTE_MINIMAL leaves them unquoted.
        data.writerow(
            [samples[key][i] if i < len(samples[key]) else "" for key, _, _ in cols]
        )

    return PlainTextResponse(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="gt7-lap-{lap_id}.csv"'},
    )


# --- tracks -----------------------------------------------------------------


@router.get("/tracks")
async def tracks(request: Request) -> list[dict[str, Any]]:
    return await svc(request).repo.list_tracks()


class TrackPayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    lap_id: int


@router.post("/tracks", dependencies=[Depends(require_admin)])
async def create_track(request: Request, payload: TrackPayload) -> dict[str, Any]:
    """Name the circuit a lap was driven on; future sessions auto-match it."""
    service = svc(request)
    lap = await service.repo.get_lap(payload.lap_id, with_samples=True)
    if lap is None:
        raise HTTPException(404, "lap not found")
    sig = signature_from_samples(lap["samples"])
    if sig is None:
        raise HTTPException(400, "lap has no position data")
    name = payload.name.strip()
    track_id = await service.repo.create_track(name, sig)
    await service.repo.set_session_track(lap["session_id"], name)
    if service.session_id == lap["session_id"]:
        service.track_name = name
        # Naming the circuit you are on labels a survey running on it, unless
        # the driver already named it themselves.
        if service.survey.active and not service.survey.track_locked:
            service.survey.set_track(name)
    return {"id": track_id, "name": name}


@router.delete("/tracks/{track_id}", dependencies=[Depends(require_admin)])
async def delete_track(request: Request, track_id: int) -> dict[str, str]:
    await svc(request).repo.delete_track(track_id)
    return {"status": "deleted"}


class ImportPayload(BaseModel):
    format: str
    version: int
    lap: dict[str, Any]


# Columns every consumer indexes unconditionally (metrics, event detection,
# distance resampling, peak/valley detection). Everything else in
# SAMPLE_COLUMNS is optional and degrades gracefully via .get.
REQUIRED_IMPORT_COLUMNS = frozenset(
    {"t", "dist", "speed", "throttle", "brake", "coast", "tire_slip",
     "body_height", "pos_x", "pos_z"}
)
# ~33 min at 60 Hz — roughly twice the slowest plausible GT7 lap; also caps
# the samples_json blob at a size SQLite handles comfortably.
MAX_IMPORT_SAMPLES = 120_000


class LapImportModel(BaseModel):
    """Validated shape of an exported lap file's `lap` object."""

    model_config = ConfigDict(extra="ignore")

    number: int = Field(ge=0)
    time_ms: int = Field(gt=0)
    finished_at: str = ""
    car_id: int = 0
    fuel_start: float = 0.0
    fuel_end: float = 0.0
    max_water_temp: float = 0.0
    max_oil_temp: float = 0.0
    min_oil_pressure: float = -1.0
    gearing: dict[str, Any] | None = None
    samples: dict[str, list[float]]


def _validate_import_samples(samples: dict[str, list[float]]) -> dict[str, list[float]]:
    samples = {k: v for k, v in samples.items() if k in SAMPLE_COLUMNS}
    missing = REQUIRED_IMPORT_COLUMNS - samples.keys()
    if missing:
        raise ValueError(f"missing sample columns: {', '.join(sorted(missing))}")
    lengths = {len(v) for v in samples.values()}
    if len(lengths) > 1:
        raise ValueError("sample columns have unequal lengths")
    n = lengths.pop()
    if not 0 < n <= MAX_IMPORT_SAMPLES:
        raise ValueError(f"sample count must be 1..{MAX_IMPORT_SAMPLES}, got {n}")
    # json.loads happily parses NaN/Infinity literals; they poison metrics
    # and can't be re-serialized as strict JSON.
    if any(not math.isfinite(v) for col in samples.values() for v in col):
        raise ValueError("samples contain non-finite values")
    return samples


@router.post("/laps/import", dependencies=[Depends(require_admin)])
async def import_lap(request: Request, payload: ImportPayload) -> dict[str, Any]:
    service = svc(request)
    if payload.format != "gt7-datalogger-lap":
        raise HTTPException(400, "unrecognized lap export format")
    try:
        lap = LapImportModel.model_validate(payload.lap)
        lap.samples = _validate_import_samples(lap.samples)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(400, f"invalid lap file: {exc}") from exc

    # Create the fallback session only after validation, so a rejected file
    # doesn't leak an empty "imported" session.
    if service.session_id is None:
        from app.processing.laps import SessionInfo

        info = SessionInfo(car_id=lap.car_id, started_at="imported")
        service.session_id = await service.repo.create_session(
            info, service.cars.name(info.car_id)
        )
    clean = payload.model_dump()
    clean["lap"] = lap.model_dump()
    try:
        lap_id = await service.repo.import_lap(clean, service.session_id)
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(400, f"invalid lap file: {exc}") from exc
    return {"id": lap_id}


# --- analysis ---------------------------------------------------------------

# Default channel set — the pre-Tier-1 payload, so clients that never open the
# newer channels pay nothing extra.
COMPARE_COLUMNS = (
    "t", "speed", "throttle", "brake", "coast", "gear", "rpm", "boost", "tire_slip", "yaw_rate",
    "pos_x", "pos_z",
)

# Columns the channels= param may request beyond the defaults.
EXTRA_COMPARE_COLUMNS = tuple(
    c for c in SAMPLE_COLUMNS if c not in COMPARE_COLUMNS and c != "dist"
)


@router.get("/analysis/compare")
async def compare(
    request: Request,
    laps: str = Query(..., description="comma-separated lap ids"),
    ref: int = Query(..., description="reference lap id"),
    step: float = Query(5.0, gt=0.5, le=50),
    channels: str | None = Query(
        None,
        description="comma-separated sample channels; defaults to the classic set. "
        "t/pos_x/pos_z are always included.",
    ),
) -> dict[str, Any]:
    """Distance-resampled series for each lap + time delta vs the reference."""
    try:
        lap_ids = [int(x) for x in laps.split(",") if x.strip()]
    except ValueError as exc:
        raise HTTPException(400, "laps must be comma-separated integers") from exc
    if ref not in lap_ids:
        lap_ids.append(ref)

    columns: tuple[str, ...]
    if channels is None:
        columns = COMPARE_COLUMNS
    else:
        requested = [c for c in channels.split(",") if c.strip()]
        allowed = set(COMPARE_COLUMNS) | set(EXTRA_COMPARE_COLUMNS)
        unknown = [c for c in requested if c not in allowed]
        if unknown:
            raise HTTPException(400, f"unknown channels: {', '.join(unknown)}")
        # Delta and the race-line map always need these three.
        columns = tuple(dict.fromkeys(["t", "pos_x", "pos_z", *requested]))

    samples_by_id = await svc(request).repo.get_laps_samples(lap_ids)
    if ref not in samples_by_id:
        raise HTTPException(404, f"reference lap {ref} not found")
    events_by_id = await svc(request).repo.get_laps_events(lap_ids)

    # The circuit's authored corners, if it has been labelled (#48). Reading
    # them parses the track bundle the first time, which is a multi-megabyte
    # document — off the event loop.
    track = await svc(request).repo.track_for_lap(ref)
    authored = await asyncio.to_thread(svc(request).authored_corners, track)
    # Corner numbering comes from the reference lap only, so every overlaid
    # lap shares one consistent set of map markers — and from the circuit's
    # authored corners when it has them, so the numbering is the same in
    # every session too, not just within this one.
    ref_corners = analysis.corners_for_lap(samples_by_id[ref], authored)

    out: dict[str, Any] = {
        "ref": ref,
        "step": step,
        "channels": list(columns),
        # Unit + sign calibration for the broadcast accelerometer, fitted on
        # the REFERENCE lap and applied to every lap in the comparison, so the
        # g-g diagram plots them all on one axis (#16).
        "accel": analysis.accel_calibration(samples_by_id[ref]),
        "laps": {},
    }
    for lap_id, samples in samples_by_id.items():
        present = tuple(c for c in columns if c in samples)
        entry: dict[str, Any] = {
            "series": analysis.resample_by_distance(samples, step, present),
            "peaks_valleys": analysis.speed_peaks_valleys(samples),
            "events": events_by_id.get(lap_id, []),
        }
        if out["accel"]["available"] and "acc_lat" in samples:
            # Peaks come from the RAW ticks, not the resampled series the
            # scatter draws: distance resampling smooths exactly the moments a
            # traction-circle readout is about.
            entry["gg"] = analysis.gg_extremes(samples, out["accel"])
        if lap_id == ref:
            entry["corners"] = ref_corners
        else:
            entry["delta"] = analysis.time_delta_series(samples, samples_by_id[ref], step)
        if ref_corners:
            # Every lap measured through the SAME corner windows (the
            # reference's), which is what makes the per-corner report card's
            # time-lost column mean something (#21).
            entry["corner_report"] = analysis.corner_report(ref_corners, samples)
        out["laps"][str(lap_id)] = entry
    return out


@router.get("/analysis/deviation")
async def deviation(
    request: Request,
    session_id: int,
    count: int = Query(5, ge=2, le=20),
) -> dict[str, Any]:
    """Speed deviation across the session's best `count` laps."""
    lap_rows = await svc(request).repo.list_laps(session_id)
    best = sorted(lap_rows, key=lambda r: r["time_ms"])[:count]
    samples = await svc(request).repo.get_laps_samples([r["id"] for r in best])
    result = analysis.speed_deviation(list(samples.values()))
    result["lap_ids"] = [r["id"] for r in best]
    return result


@router.get("/analysis/fuel")
async def fuel(request: Request, lap_id: int) -> dict[str, Any]:
    """Relative fuel map based on a lap's consumption and time."""
    lap = await svc(request).repo.get_lap(lap_id, with_samples=False)
    if lap is None:
        raise HTTPException(404, "lap not found")
    service = svc(request)
    fuel_level = (
        service.latest_packet.fuel_level if service.latest_packet else lap["fuel_end"]
    )
    rows = analysis.fuel_map(fuel_level, lap["fuel_consumed"], lap["time_ms"])
    return {
        "fuel_level": fuel_level,
        "base_lap_ms": lap["time_ms"],
        "base_fuel_per_lap": lap["fuel_consumed"],
        "rows": [asdict(r) for r in rows],
    }


# --- surface survey (issue #37) ----------------------------------------------


class SurveyTrackPayload(BaseModel):
    track: str = Field(..., min_length=1, max_length=80)

    @field_validator("track")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        """Reject a name that is only whitespace.

        `min_length` passes "   ", which strips to nothing — and assigning an
        empty label would LOCK the survey against auto-identification while
        still leaving it unlabeled. That is strictly worse than the bug this
        endpoint exists to fix: the run could then never be rescued at all.
        """
        stripped = value.strip()
        if not stripped:
            raise ValueError("track name cannot be blank")
        return stripped


class SurveyStartPayload(BaseModel):
    # Track width isn't broadcast by GT7; the survey applies this assumption
    # to derive wheel-contact points and measures how far off it lands.
    track_width_m: float = Field(1.6, gt=0.5, lt=3.0)
    # Which circuit the samples describe (the per-track grid needs this).
    # Empty = use the current session's identified track.
    track: str = Field("", max_length=80)


@router.post("/survey/start", dependencies=[Depends(require_admin)])
async def survey_start(request: Request, payload: SurveyStartPayload) -> dict[str, Any]:
    service = svc(request)
    if service.survey.active:
        # start() would silently keep the old run's width/track otherwise —
        # a 200 that reflects none of the payload is worse than an error.
        raise HTTPException(409, "survey already running — stop it first")
    user_track = payload.track.strip()
    service.survey.start(
        service.settings.db_path.parent,
        payload.track_width_m,
        track=user_track or service.track_name,
        track_user_set=bool(user_track),
        session_id=service.session_id,
    )
    return service.survey.status()


@router.post("/survey/stop", dependencies=[Depends(require_admin)])
async def survey_stop(request: Request) -> dict[str, Any]:
    svc(request).survey.stop()
    return svc(request).survey.status()


@router.post("/survey/track", dependencies=[Depends(require_admin)])
async def survey_set_track(request: Request, payload: SurveyTrackPayload) -> dict[str, Any]:
    """Name the circuit a running survey is describing.

    A run started before the track was known accumulates border evidence
    against no circuit at all, and a survey with no label saves no bundle —
    so without this, an hour of driving can only be recovered from its JSONL.
    Assigning a label to an unlabeled run keeps everything it has gathered
    and merges it into that circuit's bundle.

    Re-assigning an ALREADY labeled run is a circuit change, not a
    correction: the accumulated evidence is flushed to the previous circuit
    first, so one track's driving can never land in another's bundle. To fix
    a wrong label, rebuild from the JSONL instead
    (`scripts/jsonl_to_bundle.py`).
    """
    survey = svc(request).survey
    if not survey.active:
        raise HTTPException(409, "no survey is running")
    survey.set_track(payload.track, lock=True)
    return survey.status()


@router.get("/survey/status")
async def survey_status(request: Request) -> dict[str, Any]:
    return svc(request).survey.status()


@router.get("/survey/trail")
async def survey_trail(
    request: Request,
    since: int = Query(0, ge=0, description="index of the first point to return"),
    epoch: int = Query(-1, description="client's trail epoch; mismatch returns all"),
) -> dict[str, Any]:
    """Breadcrumb of the path driven, fetched incrementally.

    The trail is decimated 2:1 when it grows past its cap, which invalidates
    client point indices — the epoch bumps when that happens and the full
    trail is returned again.
    """
    survey = svc(request).survey
    if epoch != survey.trail_epoch or since > len(survey.trail):
        since = 0
    return {
        "epoch": survey.trail_epoch,
        "since": since,
        "points": survey.trail[since:],
        "total": len(survey.trail),
    }


@router.get("/survey/edges")
async def survey_edges(
    request: Request,
    since: int = Query(0, ge=0, description="index of the first edge point to return"),
    epoch: int = Query(-1, description="client's edges epoch; mismatch returns all"),
) -> dict[str, Any]:
    """Border-edge points of the whole run — the track taking shape.

    Append-only within a run (the epoch bumps when a new run starts), so
    incremental fetches are just index slices.
    """
    survey = svc(request).survey
    if epoch != survey.edges_epoch or since > len(survey.edges):
        since = 0
    return {
        "epoch": survey.edges_epoch,
        "since": since,
        "points": survey.edges[since:],
        "total": len(survey.edges),
    }


class SurveyMarkPayload(BaseModel):
    # side None disarms marking; kind says what boundary is being traced.
    side: Literal["L", "R"] | None = None
    kind: Literal["edge", "runoff", "wall"] = "edge"


@router.post("/survey/mark", dependencies=[Depends(require_admin)])
async def survey_mark(request: Request, payload: SurveyMarkPayload) -> dict[str, Any]:
    """Arm manual boundary marking: trace walls/run-off limits by driving them."""
    survey = svc(request).survey
    if not survey.active:
        raise HTTPException(409, "no survey running")
    survey.set_mark(payload.side, payload.kind)
    return survey.status()


@router.get("/survey/packet")
async def survey_packet(request: Request) -> dict[str, Any]:
    """The latest raw telemetry packet, fully decoded — for eyeballing fields."""
    packet = svc(request).latest_packet
    return {"packet": packet.to_dict() if packet is not None else None}


@router.get("/survey/export.jsonl")
async def survey_export(request: Request) -> PlainTextResponse:
    """Full transition log of the current (or last) survey run."""
    path = svc(request).survey.log_path
    if path is None or not path.exists():
        raise HTTPException(404, "no survey has run yet")
    return PlainTextResponse(
        path.read_text(encoding="ascii"),
        media_type="application/jsonl",
        headers={"Content-Disposition": f'attachment; filename="{path.name}"'},
    )


# Track bundles, the track catalog and the merged management view live in
# app/api/tracks.py — they are one subject (what this installation knows about
# circuits) and they outgrew being a section of this file.


# --- controls ---------------------------------------------------------------


class RecordingPayload(BaseModel):
    recording: bool


@router.post("/control/recording", dependencies=[Depends(require_admin)])
async def set_recording(request: Request, payload: RecordingPayload) -> dict[str, Any]:
    svc(request).recording = payload.recording
    return await svc(request).status()


@router.post("/control/log-lap-now", dependencies=[Depends(require_admin)])
async def log_lap_now(request: Request) -> dict[str, Any]:
    result = await svc(request).log_lap_now()
    if result is None:
        raise HTTPException(409, "no lap in progress")
    return result
