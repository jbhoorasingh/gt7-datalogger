"""Restore (#161): a backup made in Settings › Data, put back part by part.

1. POST /api/admin/restore — the ZIP as the request body, streamed into the
   data directory, never held in memory. Checked, and answered with what it
   holds part by part and what each part would do here.
2. GET /api/admin/restore — the same answer for the upload waiting.
3. POST /api/admin/restore/{id} — apply the parts ticked; answers with what
   each one did.
4. DELETE /api/admin/restore/{id} — forget the upload.

All four are token-gated: a restore replaces the recordings and can rewrite
every setting. See app.storage.restore for what each part does.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.exc import IntegrityError

from app import settings_registry
from app.api import admin, layouts
from app.api.auth import require_admin
from app.processing import track_bundle
from app.storage import backup, restore, secret_store

if TYPE_CHECKING:
    from app.service import TelemetryService

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/restore", dependencies=[Depends(require_admin)])

LABELS: dict[str, str] = {
    "data": "Data",
    "surveys": "Surveys",
    "source_id": "Install identity",
    "config": "Config",
    "layouts": "Dash/OBS",
    "tokens": "Tokens",
}


def svc(request: Request) -> TelemetryService:
    service: TelemetryService = request.app.state.service
    return service


# --- what an upload holds -----------------------------------------------------------


def _count(entry: Any, key: str) -> int:
    """A count from the manifest, which is the file's word, not ours."""
    value = entry.get(key) if isinstance(entry, dict) else None
    return value if isinstance(value, int) and value >= 0 else 0


def setting_problem(key: str, value: Any) -> str:
    """Why a backed-up setting cannot be restored here, or ''."""
    entry = settings_registry.BY_KEY.get(key)
    if entry is None:
        return "not a setting this version has"
    if not entry.editable:
        return f"{entry.env} can only be set in the environment"
    if key == "admin_token":
        return "the admin token is never restored"
    if entry.secret or key in secret_store.KEYS:
        return "a credential: the Tokens part carries those"
    try:
        settings_registry.coerce(entry, value)
    except settings_registry.SettingError as exc:
        return str(exc).removeprefix(f"{key}: ")
    return ""


def config_listing(values: dict[str, Any]) -> dict[str, Any]:
    settings: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for key, value in values.items():
        problem = setting_problem(key, value)
        if problem:
            skipped.append({"key": key, "reason": problem})
            continue
        entry = settings_registry.BY_KEY[key]
        settings.append(
            {
                "key": key,
                "label": entry.label,
                "group": entry.group,
                "value": settings_registry.to_api(entry, settings_registry.coerce(entry, value)),
                "machine": restore.is_machine_setting(key),
            }
        )
    return {"settings": settings, "skipped": skipped}


def layout_listing(
    items: list[dict[str, Any]],
) -> tuple[list[layouts.LayoutPayload], list[dict[str, str]]]:
    """The backup's layouts that POST /api/layouts would take, and why the others would not."""
    valid: list[layouts.LayoutPayload] = []
    skipped: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        name = str(item.get("name", ""))[:80]
        try:
            layout = layouts.LayoutPayload.model_validate(
                {k: item[k] for k in ("name", "kind", "config") if k in item}
            )
            layouts.validate_config(layout.config)
        except ValidationError as exc:
            skipped.append({"name": name, "reason": exc.errors()[0]["msg"]})
            continue
        except HTTPException as exc:
            skipped.append({"name": name, "reason": str(exc.detail)})
            continue
        layout.name = layout.name.strip()
        if not layout.name or layout.name in seen:
            skipped.append({"name": name, "reason": "blank or repeated name"})
            continue
        seen.add(layout.name)
        valid.append(layout)
    return valid, skipped


async def describe(service: TelemetryService, upload: restore.Upload) -> dict[str, Any]:
    """An upload as Settings lists it: each part the backup holds, with what
    it would do here and, when this install cannot take it, why."""
    found = upload.found
    manifest = upload.manifest
    parts = manifest["parts"]
    data_dir = service.settings.db_path.parent
    free = shutil.disk_usage(data_dir).free
    stats = await service.repo.stats()
    here_layouts = {layout["name"] for layout in await service.repo.list_layouts()}
    here_tokens = sorted(k for k, v in service.secrets.read().items() if v)
    out: dict[str, dict[str, Any]] = {item: {"available": False} for item in restore.ITEMS}

    if "data" in parts:
        needs = restore.database_bytes_needed(found)
        refused = restore.revision_problem(
            manifest.get("alembic_revision"), manifest.get("app_version")
        )
        if not refused and free < needs:
            refused = (
                f"restoring the recordings needs {needs >> 20} MB free here; "
                f"{free >> 20} MB is"
            )
        out["data"] = {
            "available": True,
            "bytes": int(found.get("database_bytes", 0)),
            "sessions": _count(parts["data"], "sessions"),
            "laps": _count(parts["data"], "laps"),
            "needs_bytes": needs,
            "refused": refused,
        }
    if "surveys" in parts:
        entry = parts["surveys"]
        needs = int(found.get("surveys_bytes", 0)) + restore.SPACE_MARGIN
        out["surveys"] = {
            "available": True,
            "bytes": _count(entry, "bytes"),
            "bundles": _count(entry, "bundles"),
            "corrections": _count(entry, "corrections"),
            "logs": _count(entry, "logs"),
            "car_widths": upload.holds("car-widths.json"),
            "refused": (
                f"the surveys unpack to {needs >> 20} MB; {free >> 20} MB is free here"
                if free < needs
                else ""
            ),
        }
        if upload.holds(track_bundle.SOURCE_FILE):
            out["source_id"] = {"available": True, "refused": ""}
    if "config" in parts and upload.holds(backup.CONFIG):
        error = found.get("config_error", "")
        out["config"] = {
            "available": True,
            **config_listing(found.get("config") or {}),
            "refused": error,
        }
    if "layouts" in parts and upload.holds(backup.LAYOUTS):
        valid, skipped = layout_listing(found.get("layouts") or [])
        out["layouts"] = {
            "available": True,
            "layouts": [
                {"name": x.name, "kind": x.kind, "replaces": x.name in here_layouts}
                for x in valid
            ],
            "skipped": skipped,
            "refused": found.get("layouts_error", ""),
        }
    if "tokens" in parts and upload.holds(secret_store.FILENAME):
        keys = parts["tokens"].get("keys") if isinstance(parts["tokens"], dict) else None
        out["tokens"] = {
            "available": True,
            "keys": [k for k in secret_store.KEYS if isinstance(keys, list) and k in keys],
            "refused": "",
        }

    loop = asyncio.get_running_loop()
    return {
        "id": upload.id,
        "name": upload.name,
        "bytes": upload.size,
        "received_at": upload.received_at,
        "expires_in_s": round(upload.expiry.when() - loop.time()) if upload.expiry else 0,
        "app_version": str(manifest.get("app_version") or ""),
        "created_at": str(manifest.get("created_at") or ""),
        "alembic_revision": str(manifest.get("alembic_revision") or ""),
        "free_bytes": free,
        "here": {
            "sessions": stats["sessions"],
            "laps": stats["laps"],
            "source_id": (data_dir / track_bundle.SOURCE_FILE).is_file(),
            "tokens": here_tokens,
        },
        "parts": out,
    }


# --- endpoints ---------------------------------------------------------------------


@router.post("")
async def upload_restore(request: Request) -> dict[str, Any]:
    """Take a backup to restore. `?name=` carries the file's own name, for display."""
    service = svc(request)
    header = request.headers.get("content-length")
    length = int(header) if header is not None and header.isdigit() else None
    try:
        upload = await service.restores.receive(
            request.stream(), request.query_params.get("name", ""), length
        )
    except backup.DiskBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except restore.NotEnoughSpace as exc:
        raise HTTPException(507, str(exc)) from exc
    except restore.RestoreError as exc:
        raise HTTPException(400, f"not a backup this version can restore: {exc}") from exc
    return await describe(service, upload)


@router.get("")
async def restore_waiting(request: Request) -> dict[str, Any]:
    service = svc(request)
    upload = service.restores.get()
    if upload is None:
        raise HTTPException(404, "no backup uploaded")
    return await describe(service, upload)


@router.delete("/{upload_id}")
async def forget_restore(request: Request, upload_id: str) -> dict[str, str]:
    service = svc(request)
    upload = service.restores.get(upload_id)
    if upload is not None:
        service.restores.discard(upload)
    return {"status": "forgotten"}


class RestoreRequest(BaseModel):
    items: list[restore.Item] = Field(min_length=1)
    # Of the config part's per-machine settings, the ones to restore too;
    # the others in it go whenever Config is ticked.
    machine_settings: list[str] = Field(default_factory=list)


@router.post("/{upload_id}")
async def apply_restore(
    request: Request, upload_id: str, payload: RestoreRequest
) -> dict[str, Any]:
    service = svc(request)
    upload = service.restores.get(upload_id)
    if upload is None:
        raise HTTPException(404, "no such upload — it may have expired; upload the backup again")
    described = await describe(service, upload)
    items = [item for item in restore.ITEMS if item in payload.items]
    for item in items:
        part = described["parts"][item]
        if not part["available"]:
            raise HTTPException(400, f"the backup holds no {LABELS[item]} part")
        if part.get("refused"):
            raise HTTPException(409, f"{LABELS[item]}: {part['refused']}")
    if service.survey.active and {"data", "surveys", "source_id"} & set(items):
        raise HTTPException(409, "a survey is running; stop it before restoring")
    try:
        async with service.restores.applying(upload):
            # Measured holding the disk, so no upload or snapshot can be
            # counting on the same free space.
            free = shutil.disk_usage(service.settings.db_path.parent).free
            restore.check_room(upload.found, items, free)
            # To its end even when the request goes away (the browser closed,
            # the connection dropped): a restore abandoned halfway would let go
            # of the disk while a thread still writes to it, and leave merged
            # circuits never re-judged or re-synced.
            report = await restore.run_to_end(
                _apply(service, upload, items, set(payload.machine_settings))
            )
    except backup.DiskBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except restore.NotEnoughSpace as exc:
        raise HTTPException(507, str(exc)) from exc
    except restore.RestoreError as exc:
        raise HTTPException(409, str(exc)) from exc
    return report


# --- applying ----------------------------------------------------------------------


async def _apply(
    service: TelemetryService, upload: restore.Upload, items: list[restore.Item], machine: set[str]
) -> dict[str, Any]:
    """Each part ticked, in an order where each builds on the last: the
    recordings first, since they bring a database the settings and layouts
    are then written into; the laps re-judged last, against the road as the
    surveys now have it. Only the data part can fail the request, and it
    fails before anything is changed."""
    data_dir = service.settings.db_path.parent
    report: dict[str, Any] = {}
    rejudge: list[str] = []
    if "data" in items:
        report["data"] = await _restore_data(service, upload)
        # Judged where they were recorded, against that install's surveys.
        rejudge += await service.repo.session_track_labels()
    if "config" in items:
        report["config"] = await _restore_config(service, upload, machine)
    if "layouts" in items:
        report["layouts"] = await _restore_layouts(service, upload)
    if "source_id" in items:
        try:
            source = await asyncio.to_thread(restore.restore_source_id, upload.path, data_dir)
        except restore.RestoreError as exc:
            report["source_id"] = {"source_id": None, "error": str(exc)}
        else:
            report["source_id"] = {"source_id": source}
    if "surveys" in items:
        result = await restore.run_to_end(
            asyncio.to_thread(restore.restore_surveys, upload.path, data_dir)
        )
        changed = result.pop("tracks")
        for track in changed:
            # Re-judged at once, re-synced, and the map recompiled.
            service.bundle_changed(track)
        report["surveys"] = result
        rejudge += changed
    for track in rejudge:
        service.rejudge.changed(track, settle_s=0.0)
    report["rejudging"] = sorted({track_bundle.slugify(t) for t in rejudge if t})
    if "tokens" in items:
        report["tokens"] = await _restore_tokens(service, upload)
    # Done, whoever is still waiting for the answer: the upload goes.
    upload.applied = True
    log.info("backup restored: %s", ", ".join(LABELS[item] for item in items))
    return report


async def _restore_data(service: TelemetryService, upload: restore.Upload) -> dict[str, Any]:
    data_dir = service.settings.db_path.parent
    needs = restore.database_bytes_needed(upload.found)
    free = shutil.disk_usage(data_dir).free
    if free < needs:
        raise restore.NotEnoughSpace(
            f"restoring the recordings needs {needs >> 20} MB free here; {free >> 20} MB is"
        )
    staged = data_dir / f".restore-{upload.id}.db"
    # Every step on the staged file runs to its end before the finally can
    # delete it, cancelled or not; see restore.run_to_end.
    try:
        await restore.run_to_end(
            asyncio.to_thread(restore.unpack_database, upload.path, staged)
        )
        facts = await restore.run_to_end(
            asyncio.to_thread(restore.check_database, staged, upload.manifest.get("app_version"))
        )
        revision = await restore.run_to_end(restore.upgrade_database(staged))
        kept = await service.replace_database(staged)
    finally:
        staged.unlink(missing_ok=True)
    return {
        "sessions": facts["sessions"],
        "laps": facts["laps"],
        "revision": revision,
        "upgraded_from": facts["revision"] if facts["revision"] != revision else None,
        "kept": kept.name,
    }


async def _restore_config(
    service: TelemetryService, upload: restore.Upload, machine: set[str]
) -> dict[str, Any]:
    """Each setting through PUT /api/admin/config's own checks, one at a
    time: one that does not apply here is listed, and the rest still go."""
    applied: list[str] = []
    skipped: list[dict[str, str]] = []
    left: list[str] = []
    for key, value in (upload.found.get("config") or {}).items():
        problem = setting_problem(key, value)
        if problem:
            skipped.append({"key": key, "reason": problem})
            continue
        if restore.is_machine_setting(key) and key not in machine:
            left.append(key)
            continue
        try:
            await admin.apply_config(service, admin.ConfigPatch(values={key: value}))
        except HTTPException as exc:
            skipped.append({"key": key, "reason": str(exc.detail)})
            continue
        applied.append(key)
    return {"applied": applied, "skipped": skipped, "left": left}


async def _restore_layouts(service: TelemetryService, upload: restore.Upload) -> dict[str, Any]:
    """By name: a backup's layout replaces the one here of the same name,
    keeping its id, so the overlay URLs that name it by id keep working."""
    valid, skipped = layout_listing(upload.found.get("layouts") or [])
    created: list[str] = []
    replaced: list[str] = []
    repo = service.repo
    for layout in valid:
        existing = await repo.get_layout_by_name(layout.name)
        try:
            if existing is not None:
                await repo.update_layout(existing["id"], kind=layout.kind, config=layout.config)
                replaced.append(layout.name)
            else:
                await repo.create_layout(layout.name, layout.kind, layout.config)
                created.append(layout.name)
        except IntegrityError:
            skipped.append({"name": layout.name, "reason": "made here at the same moment"})
    return {"created": created, "replaced": replaced, "skipped": skipped}


async def _restore_tokens(service: TelemetryService, upload: restore.Upload) -> dict[str, Any]:
    """Into secrets.json the way Settings saves them: checked, and applied
    live — the notifier gets the webhook, sync reconnects with the token."""
    try:
        values = await asyncio.to_thread(restore.read_tokens, upload.path)
    except restore.RestoreError as exc:
        return {"applied": [], "skipped": [{"key": secret_store.FILENAME, "reason": str(exc)}]}
    applied: list[str] = []
    skipped: list[dict[str, str]] = []
    for key, value in values.items():
        try:
            await admin.apply_settings_payload(
                service, admin.SettingsPayload.model_validate({key: value})
            )
        except ValidationError as exc:
            skipped.append({"key": key, "reason": exc.errors()[0]["msg"]})
            continue
        except HTTPException as exc:
            skipped.append({"key": key, "reason": str(exc.detail)})
            continue
        applied.append(key)
    return {"applied": applied, "skipped": skipped}
