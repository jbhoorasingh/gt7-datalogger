"""Track and survey management: the three sources of track knowledge, joined.

Three of them exist and nothing showed them together (#46):

| source          | where                     | what it knows                       |
|-----------------|---------------------------|-------------------------------------|
| named tracks    | DB `tracks` table         | geometry signature -> auto-identify |
| survey bundles  | `data/track-bundles/*`    | borders, finish line, elevation     |
| official catalog| `app/data/tracks.json`    | length, turns, elevation, layouts   |

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

import asyncio
import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.auth import require_admin
from app.processing import (
    shared_repo,
    survey_log,
    track_bundle,
    track_catalog,
    track_compile,
    track_outline,
    tracks,
)
from app.processing.tracks import signature_from_samples

if TYPE_CHECKING:
    from app.service import TelemetryService

log = logging.getLogger(__name__)

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
    # Package-relative since #57, so it resolves the same from any working
    # directory and needs no repo-root fallback.
    path = svc(request).settings.tracks_json
    return path if path.exists() else None


def _catalog(request: Request) -> dict[str, Any] | None:
    path = _catalog_path(request)
    return track_catalog.load(str(path)) if path else None


@router.get("/track-catalog")
async def catalog(request: Request) -> dict[str, Any]:
    """Official GT7 track/layout metadata (bundled app/data/tracks.json)."""
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
    # The enriched rows, so the management view sees each bundle's coverage
    # score without a second fetch.
    bundles = await asyncio.to_thread(_bundle_rows, directory)
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
                # Which kind of signature does the naming: "user" (someone
                # typed it here) or "seed" (the build shipped it, #58).
                "provenance": None,
                "track_id": None,
                "length_m": None,
                "bundle": None,
                "sessions": 0,
                "official": None,
                "suggestion": None,
            }
            rows[slug] = row
        return row

    user_named = [t for t in named if t.get("provenance") != "seed"]
    seeded = [t for t in named if t.get("provenance") == "seed"]

    for track in user_named:
        row = row_for(track["name"])
        row["name"] = track["name"]  # the DB label is the canonical spelling
        row["named"] = True
        row["provenance"] = "user"
        row["track_id"] = track["id"]
        row["length_m"] = track["length_m"]

    for bundle in bundles:
        row = row_for(bundle["track"])
        row["bundle"] = bundle
        row["official"] = bundle.get("official")

    for label, count in session_counts.items():
        row_for(label)["sessions"] = count

    # Seeded signatures attach to rows that exist for another reason; they
    # never create one. The table is built around the DISAGREEMENTS between
    # the three sources, and 77 shipped configurations nobody has driven or
    # surveyed would bury every row that means something under rows that mean
    # "this circuit exists in GT7" — which the catalog already said. The count
    # goes in the footer instead, where the other whole-installation facts are.
    unlisted = 0
    for track in seeded:
        existing = rows.get(track_bundle.slugify(track["name"]))
        if existing is None:
            unlisted += 1  # nothing driven or surveyed here yet
            continue
        if existing["named"]:
            continue  # the user's own name already wins this row
        existing["named"] = True
        existing["provenance"] = "seed"
        existing["track_id"] = track["id"]
        if existing["length_m"] is None:
            existing["length_m"] = track["length_m"]

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
        # Shipped signatures held but NOT listed above (#58) — the circuits
        # that will name themselves the first time they are driven. Reported
        # because "nothing here recognises that circuit yet" and "it is
        # already waiting for you" look identical in a table that omits both.
        # Counting every seeded row instead would make the footer claim a
        # circuit is still waiting while its row sits directly above.
        "seeded_signatures": unlisted,
    }


# --- the surveyed road, compiled for drawing (#51, #44) ------------------------


def _outline_payload(directory: Path, name: str) -> dict[str, Any]:
    """The outline the map draws: ordered compiled geometry when the bundle
    compiles (#44 — the ordered pairing fills the road the local pairing
    could not), the legacy local pairing when it does not. A bundle the
    compiler chokes on must degrade to the old drawing, never to a 500."""
    compiled: dict[str, Any] | None
    try:
        compiled = track_compile.for_track(directory, name)
    except Exception:
        log.exception("track compile failed for %r; serving the local pairing", name)
        compiled = None
    if compiled is None:
        fallback = dict(track_outline.for_track(directory, name))
        fallback.setdefault("gaps", [])
        fallback.setdefault("coverage", None)
        return fallback
    # Border polylines flattened to drawable segments, the shape the map
    # already renders; gap spans ride alongside so the map can say where the
    # boundary is honest-blank rather than surveyed.
    edges: list[list[float]] = []
    for side in ("L", "R"):
        for poly in compiled["borders"][side]:
            for a, b in zip(poly, poly[1:], strict=False):
                edges.append([a[0], a[1], b[0], b[1]])
    return {
        "track": compiled["track"],
        "slug": compiled["slug"],
        "road": compiled["road"],
        "edges": edges,
        "walls": compiled["walls"],
        "finish": compiled["finish"],
        "runs": compiled["source"]["runs"],
        "updated_at": compiled["source"]["bundle_updated_at"],
        "gaps": compiled["gaps"]["L"] + compiled["gaps"]["R"],
        "coverage": compiled["coverage"],
    }


@router.get("/track-outline")
async def outline(
    request: Request,
    lap_id: int | None = Query(None, description="resolve the circuit from this lap"),
    track: str = Query("", max_length=track_bundle.MAX_TRACK_NAME),
) -> dict[str, Any]:
    """The road a lap was driven on, ready to draw under a race line.

    Answers with an EMPTY outline rather than a 404 when the circuit has no
    bundle — "this track has never been surveyed" is the common case, not an
    error, and the map simply falls back to drawing the lap on its own.

    Compiling parses the bundle (multiple megabytes) and orders thousands of
    border cells, so it runs off the event loop; repeat calls hit the caches
    in `track_compile` / `track_outline` and cost nothing.
    """
    name = track.strip()
    if not name and lap_id is not None:
        name = await svc(request).repo.track_for_lap(lap_id)
    if not name:
        return track_outline.EMPTY
    return await asyncio.to_thread(_outline_payload, data_dir(request), name)


# --- naming sessions from the survey bundles (#41) -----------------------------


def _decode(raw: str | None) -> dict[str, list[float]] | None:
    """One lap's samples. Runs on a worker thread: the JSON decode is the
    entire cost of identifying a session, and there is one per candidate."""
    if not raw:
        return None
    try:
        decoded: dict[str, list[float]] = json.loads(raw)
    except ValueError:
        return None
    return decoded


@router.post("/tracks/identify", dependencies=[Depends(require_admin)])
async def identify_sessions(request: Request) -> dict[str, Any]:
    """Name every unlabelled session this installation can now recognise.

    New sessions identify themselves as they are recorded; this is for the
    history that was already on disk before the evidence existed — which, for
    anyone whose install predates the shipped signatures, is all of it.

    Both fingerprints are tried, in the same order and by the same code the
    live path uses, so a backfilled name and a live one mean the same thing.
    That ordering was the bug this fixes: the backfill only ever consulted the
    survey bundles, which was right when a signature existed solely because
    somebody had typed a name — there was nothing to backfill from. Shipping
    78 of them (#58) made it wrong, and silently: history sat unnamed at
    circuits the app had recognised on sight all along.

    Sessions with no confident match are left alone rather than given a
    best guess: an unlabelled session is honest, a mislabelled one is not.
    """
    service = svc(request)
    directory = data_dir(request)
    prints = await asyncio.to_thread(tracks.load_fingerprints, directory)
    if not prints and not await service.repo.has_tracks():
        raise HTTPException(409, "no circuits are known yet — nothing to match against")

    candidates = await service.repo.unnamed_sessions_with_lap()
    named: dict[str, int] = {}
    by_source: dict[str, int] = {"signature": 0, "survey bundle": 0}
    for session_id, lap_id in candidates:
        raw = await service.repo.lap_samples_json(lap_id)
        samples = await asyncio.to_thread(_decode, raw)
        if samples is None:
            continue
        # Signature first, then bundles — `service._identify_track`'s order.
        # Passing the samples is what lets a seeded signature tell a layout
        # from its reverse, so a backfilled reverse lap is named after the
        # reverse configuration rather than its twin.
        sig = signature_from_samples(samples)
        track = await service.repo.find_track(sig, samples) if sig else None
        source = "signature"
        if track is None:
            hit = await asyncio.to_thread(tracks.match_bundles, samples, prints)
            if hit is None:
                continue
            track, _coverage = hit
            source = "survey bundle"
        await service.repo.set_session_track(session_id, track)
        named[track] = named.get(track, 0) + 1
        by_source[source] += 1
        if session_id == service.session_id:
            # The session being driven right now is in this list too.
            service.track_name = track
    if named:
        log.info(
            "identified %d sessions (%d by signature, %d by survey bundle): %s",
            sum(named.values()), by_source["signature"], by_source["survey bundle"],
            named,
        )
    return {
        "checked": len(candidates),
        "identified": sum(named.values()),
        "tracks": dict(sorted(named.items())),
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


@router.get("/survey/logs/{name}/download")
async def download_log(request: Request, name: str) -> FileResponse:
    """One run's raw JSONL, verbatim — the transportable record (#40).

    A log is the complete evidence of a run, so moving the file moves the
    run: downloaded here, uploaded (or assigned) on another installation.
    """
    path = survey_log.log_path(data_dir(request), name)
    if path is None:
        raise HTTPException(404, "no such survey log")
    return FileResponse(path, media_type="application/jsonl", filename=path.name)


# Uploaded names must land inside the shape `survey_log.list_logs` globs for,
# or the file arrives and no listing ever shows it.
_LOG_NAME = re.compile(r"surface_survey_[A-Za-z0-9._-]{1,80}\.jsonl")


def _claim_upload_path(directory: Path, requested: str | None, part: Path) -> Path:
    """Land a validated temp file under its final name: the caller's when it
    already fits the naming scheme, a fresh timestamped one otherwise — and
    never an existing file. Overwriting would silently replace one run's
    evidence with another's; a suffixed copy loses nothing.

    Claimed with os.link, which fails with FileExistsError instead of
    replacing — an exists()-then-rename gap would let two concurrent uploads
    of the same name both "win". Same directory, so same filesystem, which is
    what makes the link legal and atomic; the temp file is the caller's to
    unlink."""
    name = (requested or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    if not _LOG_NAME.fullmatch(name):
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        name = f"surface_survey_uploaded_{ts}.jsonl"
    stem = name.removesuffix(".jsonl")
    n = 1
    while True:
        path = directory / (name if n == 1 else f"{stem}_{n}.jsonl")
        try:
            os.link(part, path)
        except FileExistsError:
            n += 1
            continue
        return path


@router.post("/survey/logs/upload", dependencies=[Depends(require_admin)])
async def upload_log(request: Request) -> dict[str, Any]:
    """Land a survey JSONL from elsewhere where the log listing reads.

    Streamed to disk in chunks with the same cap and for the same reason as
    the bundle import: a chunked upload carries no Content-Length, and the
    cap must hold for it too. `?name=` carries the original filename.
    """
    directory = data_dir(request)
    directory.mkdir(parents=True, exist_ok=True)
    length = request.headers.get("content-length")
    if length is not None and length.isdigit() and int(length) > MAX_IMPORT_BYTES:
        raise HTTPException(413, "log too large")
    # A temp name unique to this request — concurrent uploads must not share
    # a spool file — and outside the listing glob, so nothing rejected or
    # abandoned here ever lists. The finally covers every exit: validation
    # failure, cap abort, client disconnect.
    part = directory / f"upload_{uuid4().hex}.part"
    size = 0
    try:
        with part.open("wb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_IMPORT_BYTES:
                    raise HTTPException(413, "log too large")
                out.write(chunk)
        if size == 0:
            raise HTTPException(400, "empty upload")
        # Full pass, not a sniff: any object-shaped JSON ({}, a bundle
        # export) used to upload "successfully", list as a run with nothing
        # in it, and only fail at assign time. Off the event loop — it is a
        # line-by-line parse of up to MAX_IMPORT_BYTES.
        try:
            await asyncio.to_thread(survey_log.validate_log, part)
        except survey_log.LogFormatError as exc:
            raise HTTPException(400, f"not a survey JSONL log: {exc}") from exc
        path = _claim_upload_path(directory, request.query_params.get("name"), part)
    finally:
        part.unlink(missing_ok=True)
    return survey_log.summarize(path)


# --- bundles ------------------------------------------------------------------


def _bundle_rows(directory: Path) -> list[dict[str, Any]]:
    """Bundle listing rows, each carrying its compiled coverage score (#40).

    Coverage is the compiled document's, so listing a bundle also states how
    much of its boundary the survey has actually established. Blocking on a
    stale compile (tens of ms per bundle, cached and persisted after that):
    callers run it off the event loop. A bundle that will not compile still
    lists — the row just carries no score.
    """
    rows = track_bundle.list_bundles(directory)
    for row in rows:
        try:
            compiled = track_compile.for_track(directory, row["track"])
        except Exception:
            log.exception("track compile failed for %r; listing without coverage",
                          row["track"])
            compiled = None
        if compiled is not None:
            row["coverage"] = compiled["coverage"]
            row["compiled_at"] = compiled["compiled_at"]
    return rows


@router.get("/track-bundles")
async def list_bundles(request: Request) -> list[dict[str, Any]]:
    """Every circuit's accumulated survey bundle (perimeters, finish line)."""
    return await asyncio.to_thread(_bundle_rows, data_dir(request))


def _slug(slug: str) -> str:
    if not track_bundle.slugify(slug) == slug:
        raise HTTPException(400, "invalid bundle name")
    return slug


# --- shared bundle repo (#47) -------------------------------------------------
# Declared BEFORE /track-bundles/{slug}: FastAPI matches in declaration order,
# and "shared" is itself a well-formed slug.


def _shared_index_url(request: Request) -> str | None:
    configured = svc(request).settings.shared_bundles_url.strip()
    return shared_repo.index_url(configured) if configured else None


@router.get("/track-bundles/shared")
async def shared_bundles(request: Request) -> dict[str, Any]:
    """What the configured shared repo has on offer. `configured: false` when
    no repo is set — the UI hides the feature rather than showing an error."""
    url = _shared_index_url(request)
    if url is None:
        return {"configured": False, "bundles": []}
    try:
        raw = await shared_repo.fetch_json(url, shared_repo.MAX_INDEX_BYTES)
        entries = shared_repo.validate_index(raw)
    except track_bundle.BundleError as exc:
        raise HTTPException(502, f"shared bundle repo: {exc}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"shared bundle repo unreachable: {exc}") from exc
    for entry in entries:
        entry["slug"] = track_bundle.slugify(entry["track"])
    return {"configured": True, "url": url, "bundles": entries}


@router.post(
    "/track-bundles/shared/{slug}/pull", dependencies=[Depends(require_admin)]
)
async def pull_shared_bundle(
    request: Request, slug: str, track: str | None = Query(None)
) -> dict[str, Any]:
    """Pull one shared bundle and merge it, exactly as an imported file would be.

    The index is re-fetched and the bundle looked up by slug rather than the
    client posting a URL: an admin token should not be a proxy for "make this
    server GET anywhere I say". `?track=` overrides the document's label, same
    as import.
    """
    url = _shared_index_url(request)
    if url is None:
        raise HTTPException(404, "no shared bundle repo configured")
    if track is not None:
        track = track.strip()[: track_bundle.MAX_TRACK_NAME]
        if not track:
            raise HTTPException(400, "track override cannot be blank")
    # Everything the REPO can get wrong is a 502: the client sent nothing but
    # a slug, so a malformed index, a bundle URL off http(s), or a bundle that
    # fails validation are all upstream-content failures, not client errors —
    # the same reading the listing endpoint above gives them. 4xx is reserved
    # for the caller's own inputs (unknown slug, blank track override).
    try:
        raw = await shared_repo.fetch_json(url, shared_repo.MAX_INDEX_BYTES)
        entries = shared_repo.validate_index(raw)
        entry = next(
            (e for e in entries if track_bundle.slugify(e["track"]) == slug), None
        )
        if entry is None:
            raise HTTPException(404, "the shared repo lists no such bundle")
        bundle_url = shared_repo.resolve_url(url, entry["url"])
        payload = await shared_repo.fetch_json(bundle_url, MAX_IMPORT_BYTES)
        doc = track_bundle.validate_document(payload)
        result = await asyncio.to_thread(
            track_bundle.merge_document, data_dir(request), doc, track
        )
    except track_bundle.BundleError as exc:
        raise HTTPException(502, f"shared bundle repo: {exc}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"shared bundle repo unreachable: {exc}") from exc
    # A pull can give a circuit its first authored corners, same as import.
    svc(request).invalidate_authored_corners(result["track"])
    return result


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
