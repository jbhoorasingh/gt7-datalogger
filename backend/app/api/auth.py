"""Opt-in admin token gate for mutating and admin endpoints.

With GT7_ADMIN_TOKEN unset (the default) every endpoint stays open — the
LAN-trusted behavior this tool started with. When a token is configured,
requests must carry it in the X-API-Key header. A custom header (rather
than Authorization: Bearer) so reverse proxies / SSO middlewares that
consume Authorization can't collide with it.

Read-only telemetry endpoints (status, laps, analysis, the live WebSocket)
are deliberately never gated so overlays and dash devices keep working.
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Header, HTTPException, Request

from app.api.guard import peer_is_loopback


async def require_admin(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    # Via app.state, not the lru-cached get_settings(): tests and the admin
    # API mutate the live Settings object the service holds.
    settings = request.app.state.service.settings
    token: str = settings.admin_token
    if not token:
        return
    # The desktop app's own browser (#195): a loopback peer that passed the
    # guard's Host and Origin checks is the person at this computer, who
    # owns the install. The token is for the phone and the tablet.
    if settings.desktop and peer_is_loopback(request.scope):
        return
    if x_api_key is None:
        raise HTTPException(401, "admin token required (X-API-Key header)")
    if not secrets.compare_digest(x_api_key.encode(), token.encode()):
        raise HTTPException(403, "invalid admin token")
