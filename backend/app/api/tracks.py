"""Track and survey management: the three sources of track knowledge, joined.

Three of them exist and nothing showed them together (#46):

| source          | where                     | what it knows                       |
|-----------------|---------------------------|-------------------------------------|
| named tracks    | DB `tracks` table         | geometry signature -> auto-identify |
| survey bundles  | `data/track-bundles/*`    | borders, finish line, elevation     |
| official catalog| `backend/data/tracks.json`| length, turns, elevation, layouts   |

Being able to name a track, having surveyed it, and knowing which official
layout it is are three separate facts, and until this endpoint existed
nothing said so — which is how a survey ran for ~55 minutes attached to no
circuit at all (#45) with no screen anywhere reporting the gap.

`/track-overview` is one query over all three rather than three fetches
stitched in the browser, because the interesting rows are the ones where the
sources DISAGREE (a bundle with no named track, a named track nobody has
surveyed, two bundles that are the same circuit under near-miss names), and
that is a join, not a list.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.auth import require_admin
from app.processing import survey_log, track_bundle, track_catalog

if TYPE_CHECKING:
    from app.service import TelemetryService

router = APIRouter(prefix="/api")

# An imported bundle is a JSON document of up to MAX_POINTS border records,
# which is a few MB — but nothing stops a caller posting a gigabyte, and the
# body is fully buffered before anything gets to validate it.
MAX_IMPORT_BYTES = 64 * 1024 * 1024


def svc(request: Request) -> TelemetryService:
    service: TelemetryService = request.app.state.service
    return service


def data_dir(request: Request) -> Path:
    return svc(request).settings.db_path.parent


def _catalog_path(request: Request) -> Path | None:
    path = svc(request).settings.tracks_json
    if not path.exists():
        # The default is relative to backend/; dev servers often run from the
        # repo root (cars.csv papers over this with GT7_CARS_CSV in .env).
        path = Path(__file__).resolve().parents[2] / "data" / "tracks.json"
    return path if path.exists() else None


def _catalog(request: Request) -> dict[str, Any] | None:
    path = _catalog_path(request)
    return track_catalog.load(str(path)) if path else None


@router.get("/track-catalog")
async def catalog(request: Request) -> dict[str, Any]:
    """Official GT7 track/layout metadata (bundled data/tracks.json)."""
    doc = _catalog(request)
    if doc is None:
        raise HTTPException(404, "track catalog not bundled")
    return doc


# --- the joined view ----------------------------------------------------------


@router.get("/track-overview")
async def overview(request: Request) -> dict[str, Any]:
    """Every track this installation knows anything about, merged by name."""
    service = svc(request)
    directory = data_dir(request)
    named = await service.repo.list_tracks()
    sessions = await service.repo.list_sessions()
    bundles = track_bundle.list_bundles(directory)
    doc = _catalog(request)
    configs = track_catalog.configurations(doc) if doc else []

    session_counts: dict[str, int] = {}
    for session in sessions:
        label = (session.get("track_name") or "").strip()
        if label:
            session_counts[label] = session_counts.get(label, 0) + 1

    # Keyed on the slug, because that is what actually decides whether two
    # labels share a bundle: "Lago Maggiore - East" and "lago maggiore east"
    # are one row, "- East" and "- East End" are two, and seeing them side by
    # side is the point (they can then be merged by renaming one).
    rows: dict[str, dict[str, Any]] = {}

    def row_for(name: str) -> dict[str, Any]:
        slug = track_bundle.slugify(name)
        row = rows.get(slug)
        if row is None:
            row = {
                "slug": slug,
                "name": name,
                "named": False,
                "track_id": None,
                "length_m": None,
                "bundle": None,
                "sessions": 0,
                "official": None,
                "suggestion": None,
            }
            rows[slug] = row
        return row

    for track in named:
        row = row_for(track["name"])
        row["name"] = track["name"]  # the DB label is the canonical spelling
        row["named"] = True
        row["track_id"] = track["id"]
        row["length_m"] = track["length_m"]

    for bundle in bundles:
        row = row_for(bundle["track"])
        row["bundle"] = bundle
        row["official"] = bundle.get("official")

    for label, count in session_counts.items():
        row_for(label)["sessions"] = count

    for row in rows.values():
        if row["official"] is None:
            # A guess, never a lookup: shown so a human can confirm it.
            row["suggestion"] = track_catalog.suggest(
                row["name"], configs, row["length_m"]
            )

    return {
        "source": track_bundle.source_id(directory),
        "tracks": sorted(rows.values(), key=lambda r: r["name"].lower()),
        "logs": survey_log.list_logs(directory),
        "catalog_configs": len(configs),
    }


# --- survey logs: the rescue path for a run that went nowhere ------------------


@router.get("/survey/logs")
async def survey_logs(request: Request) -> list[dict[str, Any]]:
    """Every survey run's JSONL, and whether it ever reached a circuit."""
    return survey_log.list_logs(data_dir(request))


class AssignLogPayload(BaseModel):
    track: str = Field(min_length=1, max_length=track_bundle.MAX_TRACK_NAME)


@router.post("/survey/logs/{name}/assign", dependencies=[Depends(require_admin)])
async def assign_log(
    request: Request, name: str, payload: AssignLogPayload
) -> dict[str, Any]:
    """Rebuild an orphaned run from its log and merge it into a circuit."""
    directory = data_dir(request)
    path = survey_log.log_path(directory, name)
    if path is None:
        raise HTTPException(404, "no such survey log")
    if svc(request).survey.active and svc(request).survey.log_path == path:
        # Its evidence is still in memory and will be saved on stop; merging
        # the partial log now would count the same run twice.
        raise HTTPException(409, "that run is still going — stop the survey first")
    target = payload.track.strip()
    already = survey_log.summarize(path)["track"]
    if already and track_bundle.slugify(already) == track_bundle.slugify(target):
        # The view hides a rescued run, but a retry — a double-click, a
        # repeated request — still lands here, and assign() would allocate the
        # next run and merge the same evidence again, inflating exactly the
        # counts the whole format is careful about. Re-assigning to a
        # DIFFERENT circuit stays allowed: that is the mis-label correction.
        raise HTTPException(
            409, f"that run has already been assigned to {already!r}"
        )
    try:
        result = survey_log.assign(directory, path, target)
    except track_bundle.BundleError as exc:
        raise HTTPException(400, str(exc)) from exc
    svc(request).invalidate_authored_corners(result["track"])
    return result


# --- bundles ------------------------------------------------------------------


@router.get("/track-bundles")
async def list_bundles(request: Request) -> list[dict[str, Any]]:
    """Every circuit's accumulated survey bundle (perimeters, finish line)."""
    return track_bundle.list_bundles(data_dir(request))


def _slug(slug: str) -> str:
    if not track_bundle.slugify(slug) == slug:
        raise HTTPException(400, "invalid bundle name")
    return slug


@router.get("/track-bundles/{slug}")
async def download(request: Request, slug: str) -> dict[str, Any]:
    """One bundle document — the export unit, and what import consumes."""
    doc = track_bundle.load_slug(data_dir(request), _slug(slug))
    if doc is None:
        raise HTTPException(404, "no bundle for this track")
    return doc


@router.post("/track-bundles/import", dependencies=[Depends(require_admin)])
async def import_bundle(request: Request) -> dict[str, Any]:
    """Merge a bundle document from elsewhere into this installation's store.

    Read raw rather than through a pydantic body model: the document is
    already validated field by field by `track_bundle.validate_document`, and
    letting pydantic build a second full copy of a multi-megabyte edge list
    first buys nothing.

    `?track=` overrides the document's own label, which is how a friend's
    "Lago Maggiore - East End" merges into your "Lago Maggiore East".
    """
    length = request.headers.get("content-length")
    if length is not None and length.isdigit() and int(length) > MAX_IMPORT_BYTES:
        raise HTTPException(413, "bundle too large")
    # Read the body in chunks and stop AT the cap. `await request.body()`
    # buffers the whole payload first, so a chunked upload — which carries no
    # Content-Length for the check above to catch — could make the process
    # allocate a gigabyte for a limit it claims to enforce.
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_IMPORT_BYTES:
            raise HTTPException(413, "bundle too large")
        chunks.append(chunk)
    try:
        payload = json.loads(b"".join(chunks))
    except ValueError as exc:
        raise HTTPException(400, f"not valid JSON: {exc}") from exc
    target = request.query_params.get("track")
    if target is not None:
        # The document's own label is capped and PATCH goes through Pydantic;
        # this one arrives raw off the query string, and an overlong value
        # reaches the filesystem as a filename and fails as a 500.
        target = target.strip()[: track_bundle.MAX_TRACK_NAME]
        if not target:
            raise HTTPException(400, "track override cannot be blank")
    try:
        doc = track_bundle.validate_document(payload)
        result = track_bundle.merge_document(data_dir(request), doc, track=target)
    except track_bundle.BundleError as exc:
        raise HTTPException(400, f"invalid bundle: {exc}") from exc
    # An import can give a circuit its first authored corners.
    svc(request).invalidate_authored_corners(result["track"])
    return result


class BundlePatch(BaseModel):
    """Re-label a bundle and/or confirm which official layout it is."""

    track: str | None = Field(default=None, max_length=track_bundle.MAX_TRACK_NAME)
    # An explicit null clears a confirmed match; omitting the field leaves it.
    official: dict[str, Any] | None = None
    set_official: bool = False


@router.patch("/track-bundles/{slug}", dependencies=[Depends(require_admin)])
async def patch_bundle(
    request: Request, slug: str, payload: BundlePatch
) -> dict[str, Any]:
    directory = data_dir(request)
    _slug(slug)
    result: dict[str, Any] = {"slug": slug}
    try:
        if payload.set_official:
            official = (
                track_bundle.validate_official(payload.official)
                if payload.official is not None
                else None
            )
            doc = track_bundle.load_slug(directory, slug)
            if doc is None:
                raise HTTPException(404, "no bundle for this track")
            track_bundle.set_official(directory, doc["meta"]["track"], official)
            result["official"] = official
        if payload.track is not None:
            _refuse_if_surveying(request, slug, "renamed")
            # Renaming onto an existing bundle MERGES: two near-miss spellings
            # of one circuit are one circuit, and keeping them apart was the
            # bug, not the feature.
            was = track_bundle.load_slug(directory, slug)
            result.update(track_bundle.rename(directory, slug, payload.track))
            if was is not None:
                svc(request).invalidate_authored_corners(was["meta"]["track"])
            svc(request).invalidate_authored_corners(payload.track)
    except track_bundle.BundleError as exc:
        raise HTTPException(400, str(exc)) from exc
    return result


def _refuse_if_surveying(request: Request, slug: str, verb: str) -> None:
    """A live survey holds its circuit's name in memory and writes by it.

    Move the bundle out from under it and the next autosave — or the stop —
    recreates the old one under the old name, splitting the circuit again,
    which is the exact thing rename exists to repair. Nothing here can retarget
    a running survey safely, so it waits for the survey to stop.
    """
    survey = svc(request).survey
    if survey.active and survey.track and track_bundle.slugify(survey.track) == slug:
        raise HTTPException(
            409,
            f"a survey is running on this circuit — it cannot be {verb} until "
            "that run stops, or its next save would recreate this bundle",
        )


@router.delete("/track-bundles/{slug}", dependencies=[Depends(require_admin)])
async def delete_bundle(request: Request, slug: str) -> dict[str, str]:
    directory = data_dir(request)
    _refuse_if_surveying(request, _slug(slug), "deleted")
    doc = track_bundle.load_slug(directory, _slug(slug))
    if not track_bundle.delete(directory, _slug(slug)):
        raise HTTPException(404, "no bundle for this track")
    if doc is not None:
        svc(request).invalidate_authored_corners(doc["meta"]["track"])
    return {"status": "deleted"}


# --- authored corners and sections (#48) --------------------------------------


class AuthoredPayload(BaseModel):
    """Hand-labelled corners/sections. Omitted lists are left untouched."""

    corners: list[dict[str, Any]] | None = None
    sections: list[dict[str, Any]] | None = None


@router.get("/track-bundles/{slug}/corners")
async def get_corners(request: Request, slug: str) -> dict[str, Any]:
    doc = track_bundle.load_slug(data_dir(request), _slug(slug))
    if doc is None:
        raise HTTPException(404, "no bundle for this track")
    return {
        "track": doc["meta"]["track"],
        "corners": doc["corners"],
        "sections": doc["sections"],
        "official": doc["meta"]["official"],
        "finish_crossings": doc["finish_crossings"],
    }


@router.put("/track-bundles/{slug}/corners", dependencies=[Depends(require_admin)])
async def put_corners(
    request: Request, slug: str, payload: AuthoredPayload
) -> dict[str, Any]:
    """Replace a circuit's authored corners — which then outrank detection.

    Stored in the bundle rather than the DB: this is track knowledge, it
    travels with export/import, and it is a large part of what makes a shared
    bundle worth pulling (#48).
    """
    directory = data_dir(request)
    _slug(slug)
    existing = track_bundle.load_slug(directory, slug)
    if existing is None:
        raise HTTPException(404, "no bundle for this track")
    try:
        corners = (
            None if payload.corners is None
            else [track_bundle.validate_corner(c, i) for i, c in enumerate(payload.corners)]
        )
        sections = (
            None if payload.sections is None
            else [track_bundle.validate_section(s, i) for i, s in enumerate(payload.sections)]
        )
    except track_bundle.BundleError as exc:
        raise HTTPException(400, str(exc)) from exc
    if corners is not None and len(corners) > track_bundle.MAX_CORNERS:
        raise HTTPException(400, f"at most {track_bundle.MAX_CORNERS} corners")
    if sections is not None and len(sections) > track_bundle.MAX_SECTIONS:
        raise HTTPException(400, f"at most {track_bundle.MAX_SECTIONS} sections")
    doc = track_bundle.set_authored(
        directory, existing["meta"]["track"], corners=corners, sections=sections
    )
    if doc is None:  # pragma: no cover - load succeeded a line ago
        raise HTTPException(404, "no bundle for this track")
    svc(request).invalidate_authored_corners(existing["meta"]["track"])
    return {"track": doc["meta"]["track"], "corners": doc["corners"],
            "sections": doc["sections"]}
