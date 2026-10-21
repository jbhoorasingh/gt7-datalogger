"""Backup (#161): one file holding a whole installation, made in Settings › Data.

Two steps, because a browser download cannot send the X-API-Key header and
a backup holds the settings and, when asked, the credentials:

1. POST /api/admin/backup — token-gated. Takes the database snapshot (the
   slow part, and the part that can fail: a full disk, a database that kept
   changing) and answers with a download link.
2. GET /api/backup/{id}/{name} — the link. Open, because the id is the
   credential: unguessable, one backup at a time, gone after its download
   or after fifteen minutes. Streamed as it is written; see
   app.storage.backup for what is in it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.auth import require_admin
from app.storage import backup

if TYPE_CHECKING:
    from app.service import TelemetryService

router = APIRouter(prefix="/api")


def svc(request: Request) -> TelemetryService:
    service: TelemetryService = request.app.state.service
    return service


@router.get("/admin/backup", dependencies=[Depends(require_admin)])
async def backup_inventory(request: Request) -> dict[str, Any]:
    """What each part would hold if a backup were made now, and the space
    free beside the database."""
    service = svc(request)
    db_path = service.settings.db_path
    return await backup.inventory(
        service.repo, db_path.parent, db_path, service.secrets.read()
    )


class BackupRequest(BaseModel):
    parts: list[backup.Part] = Field(min_length=1)


@router.post("/admin/backup", dependencies=[Depends(require_admin)])
async def make_backup(request: Request, payload: BackupRequest) -> dict[str, Any]:
    service = svc(request)
    try:
        prepared = await service.backups.prepare(
            payload.parts, service.settings.db_path, await service.repo.schema_revision()
        )
    except backup.NotEnoughSpace as exc:
        raise HTTPException(507, str(exc)) from exc
    except backup.BackupError as exc:
        raise HTTPException(409, str(exc)) from exc
    return prepared.describe()


@router.get("/backup/{backup_id}/{name}")
async def download_backup(request: Request, backup_id: str, name: str) -> StreamingResponse:
    service = svc(request)
    prepared = service.backups.get(backup_id)
    if prepared is None or name != prepared.name:
        raise HTTPException(404, "no such backup — it may have expired; make it again")
    chunks = backup.write_archive(
        prepared, service.settings.db_path.parent, service.repo, service.secrets.read()
    )
    return StreamingResponse(
        service.backups.stream(prepared, chunks),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{prepared.name}"',
            # A link that can carry credentials is not for a cache to keep.
            "Cache-Control": "no-store",
        },
    )
