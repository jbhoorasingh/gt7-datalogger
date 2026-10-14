"""WebSocket endpoint streaming live telemetry, lap and session events.

Traffic is overwhelmingly server-push. The client→server direction carries
the client protocol's `hello` and `subscribe` (app.protocol: a native app's
rate, frame and events) and the Race Engineer voice protocol (which browser is
allowed to speak, and what it did with a callout); anything else a client
sends is ignored, so pages from older builds — which send pings or nothing at
all — keep working.

Deliberately unauthenticated even when GT7_ADMIN_TOKEN is set: claiming voice
output is a driver-device action on a read-only surface, not an admin one.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app import protocol

if TYPE_CHECKING:
    from app.service import TelemetryService

log = logging.getLogger(__name__)

router = APIRouter()

MAX_ID_LEN = 64


def _text(data: dict[str, Any], key: str, limit: int = MAX_ID_LEN) -> str:
    """A bounded string field — client input, so never trusted for length."""
    value = data.get(key)
    return value[:limit] if isinstance(value, str) else ""


def _first_error(exc: ValidationError) -> str:
    err = exc.errors()[0]
    where = ".".join(str(p) for p in err["loc"])
    return f"{where}: {err['msg']}" if where else str(err["msg"])


def _handle_client_message(service: TelemetryService, ws: WebSocket, raw: str) -> None:
    try:
        message = json.loads(raw)
    except (ValueError, TypeError):
        return  # pings and garbage from older pages
    if not isinstance(message, dict):
        return
    kind = message.get("type")
    data = message.get("data")
    if not isinstance(data, dict):
        data = {}

    if kind == "hello":
        try:
            hello = protocol.Hello.model_validate(data)
        except ValidationError as exc:
            service.client_error(ws, "hello", _first_error(exc))
            return
        service.client_hello(ws, hello)
    elif kind == "subscribe":
        try:
            sub = protocol.Subscribe.model_validate(data)
        except ValidationError as exc:
            service.client_error(ws, "subscribe", _first_error(exc))
            return
        service.client_subscribe(ws, sub)
    elif kind == "client_capabilities":
        service.set_client_capabilities(
            ws,
            client_id=_text(data, "client_id"),
            page=_text(data, "page"),
            voice_supported=bool(data.get("voice_supported")),
            voice_enabled=bool(data.get("voice_enabled")),
        )
    elif kind == "claim_voice_output":
        service.claim_voice_output(ws, _text(data, "client_id"))
    elif kind == "release_voice_output":
        service.release_voice_output(ws, _text(data, "client_id"))
    elif kind == "voice_callout_ack":
        service.record_callout_ack(
            _text(data, "client_id"),
            _text(data, "callout_id"),
            _text(data, "status"),
            # Free-text engine reason ("not-allowed", "synthesis-failed", ...).
            # The difference between "the browser said no" and "no idea".
            _text(data, "reason", limit=120),
        )


@router.websocket("/ws/live")
async def live(ws: WebSocket) -> None:
    service: TelemetryService = ws.app.state.service
    await ws.accept()
    await service.register(ws)
    try:
        while True:
            _handle_client_message(service, ws, await ws.receive_text())
    except WebSocketDisconnect:
        pass
    finally:
        await service.unregister(ws)
