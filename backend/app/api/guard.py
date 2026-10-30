"""Refuse requests that a web page sent on someone else's behalf (#183).

The server's client is usually the user's everyday browser, and the browser
will send a request to `http://127.0.0.1:8000` from any page it has open. A
bodiless POST such as `/api/admin/clear-data`, or a raw-body import, needs no
CORS preflight, so CORS alone stops nothing: the page cannot read the answer,
but the data is already gone. And with no check on the Host header, DNS
rebinding lets a page on `evil.example` read the API as if it were its own.

Two checks, both read from the running settings on every request so a change
in Settings › Advanced applies without a restart:

- **Host.** The name the request was sent to must be one that names a
  machine on this network: an IP literal, `localhost`, a single-label name,
  a LAN suffix (`.local`, `.lan`, `.home.arpa`, `.internal`, `.localdomain`,
  `.localhost`, `.test`), this machine's own hostname, or a name listed in
  `GT7_TRUSTED_HOSTS` (`*` turns the check off). A rebinding attack needs a
  public name, which is none of those. Requests with no Host header pass.
- **Origin.** A request that can change something (any method but GET, HEAD
  and OPTIONS) and the WebSocket handshake are refused when the browser says
  the request is `Sec-Fetch-Site: cross-site`, or when they carry an
  `Origin` that is neither the request's own host and port, nor listed in
  `GT7_CORS_ORIGINS`, nor a name listed in `GT7_TRUSTED_HOSTS`. Native
  clients (the phone app, curl, OBS) send no Origin and are unaffected.

A refusal is a 403 whose JSON body carries `detail` (the sentence, naming
the setting that would allow the request) and `reason`; a refused WebSocket
handshake is closed before it is accepted, which the server answers with a
403 too.

Pure ASGI rather than BaseHTTPMiddleware, so WebSocket handshakes pass
through it. The checks are a tuple of functions of one `Request`, run in
order, the first refusal winning: a further policy — a per-request check on
the peer address, say — is one more function in `CHECKS`.
"""

from __future__ import annotations

import ipaddress
import json
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import Settings, get_settings

# Methods that change nothing, so a cross-site one does no harm the Host
# check does not already stop. OPTIONS is the CORS preflight.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Suffixes that name a machine on the local network and cannot be bought:
# mDNS, the common router suffixes, RFC 8375, RFC 6761 and RFC 2606.
LAN_SUFFIXES = (
    ".local",
    ".lan",
    ".home.arpa",
    ".internal",
    ".localdomain",
    ".localhost",
    ".test",
)

# Why a request was refused, for a client that wants more than the sentence.
UNTRUSTED_HOST = "untrusted_host"
CROSS_SITE = "cross_site"


@dataclass(frozen=True, slots=True)
class Refusal:
    reason: str
    detail: str


@dataclass(frozen=True, slots=True)
class Request:
    """What the checks are given: the scope's parts, decoded once."""

    scope: Scope
    settings: Settings
    headers: dict[str, str]

    @property
    def websocket(self) -> bool:
        return bool(self.scope["type"] == "websocket")

    @property
    def method(self) -> str:
        return str(self.scope.get("method", "GET")).upper()

    @property
    def can_write(self) -> bool:
        """A request a cross-site page must not be able to send."""
        return self.websocket or self.method not in SAFE_METHODS


Check = Callable[[Request], Refusal | None]


def split_host(value: str) -> tuple[str, str]:
    """`host[:port]` as (lowercased host, port or ""). IPv6 keeps no brackets."""
    value = value.strip().lower()
    if value.startswith("["):
        host, _, rest = value[1:].partition("]")
        return host, rest.removeprefix(":")
    if value.count(":") > 1:  # a bare IPv6 address, no port
        return value, ""
    host, _, port = value.partition(":")
    return host.rstrip("."), port


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.split("%", 1)[0])  # drop an IPv6 zone id
    except ValueError:
        return False
    return True


def _own_names() -> set[str]:
    name = socket.gethostname().lower().rstrip(".")
    base = name.removesuffix(".local")
    return {name, base, f"{base}.local"}


def _trusted_entries(settings: Settings) -> list[str]:
    return [e.strip().lower() for e in settings.trusted_hosts.split(",") if e.strip()]


def _listed(host: str, entries: list[str]) -> bool:
    """`host` is named by GT7_TRUSTED_HOSTS: exactly, or by a `*.`/`.` suffix."""
    for entry in entries:
        if entry == "*":
            return True
        if entry.startswith(("*.", ".")):
            suffix = "." + entry.lstrip("*.")
            if host.endswith(suffix) or host == suffix[1:]:
                return True
        elif host == split_host(entry)[0]:
            return True
    return False


def host_is_trusted(host: str, settings: Settings) -> bool:
    if not host:
        return True
    if (
        _is_ip(host)
        or host == "localhost"
        or "." not in host
        or host.endswith(LAN_SUFFIXES)
        or _listed(host, _trusted_entries(settings))
    ):
        return True
    return host in _own_names()


def check_host(request: Request) -> Refusal | None:
    raw = request.headers.get("host", "")
    host, _ = split_host(raw)
    if host_is_trusted(host, request.settings):
        return None
    return Refusal(
        UNTRUSTED_HOST,
        f"Host {raw!r} is not a name this server trusts. If it is reached through "
        "a reverse proxy or a DNS name of your own, add that name to "
        "GT7_TRUSTED_HOSTS (Settings › Advanced; * turns the check off).",
    )


def _origin_allowed(origin: str, request: Request) -> bool:
    configured = [o.strip().rstrip("/").lower() for o in request.settings.cors_origins.split(",")]
    if origin.rstrip("/") in configured:
        return True
    parts = urlsplit(origin)
    if parts.scheme not in ("http", "https", "ws", "wss") or not parts.hostname:
        return False  # "null", a file: page, an extension
    origin_host = parts.hostname
    default = "443" if parts.scheme in ("https", "wss") else "80"
    origin_port = str(parts.port) if parts.port is not None else default
    own_host, own_port = split_host(request.headers.get("host", ""))
    if origin_host == own_host and origin_port == (own_port or default):
        return True
    # A name the user said names this server is its own UI, wherever a proxy
    # forwarded the request: nginx, by default, rewrites Host to upstream's.
    entries = [e for e in _trusted_entries(request.settings) if e != "*"]
    return _listed(origin_host, entries)


def check_origin(request: Request) -> Refusal | None:
    if not request.can_write:
        return None
    origin = request.headers.get("origin", "").strip().lower()
    if origin:
        if _origin_allowed(origin, request):
            return None
        shown = origin
    elif request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        shown = "another site"
    else:
        return None
    return Refusal(
        CROSS_SITE,
        f"Cross-site request refused: it came from {shown}, not from this server's "
        "own pages. To let another web app make it, list its origin in "
        "GT7_CORS_ORIGINS (Settings › Advanced).",
    )


CHECKS: tuple[Check, ...] = (check_host, check_origin)


def _settings(scope: Scope) -> Settings:
    service = getattr(scope["app"].state, "service", None) if "app" in scope else None
    return service.settings if service is not None else get_settings()


class RequestGuard:
    """Runs `CHECKS` on every HTTP request and WebSocket handshake."""

    def __init__(self, app: ASGIApp, checks: tuple[Check, ...] = CHECKS) -> None:
        self.app = app
        self.checks = checks

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        request = Request(scope, _settings(scope), headers)
        for check in self.checks:
            refusal = check(request)
            if refusal is not None:
                await self._refuse(scope, send, refusal)
                return
        await self.app(scope, receive, send)

    @staticmethod
    async def _refuse(scope: Scope, send: Send, refusal: Refusal) -> None:
        if scope["type"] == "websocket":
            # Closing before accepting makes the server answer the upgrade
            # with 403; the reason travels in the close frame where a client
            # library shows one.
            await send({"type": "websocket.close", "code": 1008, "reason": refusal.reason})
            return
        body = json.dumps({"detail": refusal.detail, "reason": refusal.reason}).encode()
        await send({
            "type": "http.response.start",
            "status": 403,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})
