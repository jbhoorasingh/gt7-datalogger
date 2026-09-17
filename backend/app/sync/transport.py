"""HTTP transport to a sync service: bearer auth and error classification.

Everything network-shaped for the sync client lives here, so the adapters
only ever see a `SyncError` with a *kind* and decide what to do with it —
retry, give up until the document changes, or switch the type off. The
kinds, and what an adapter is expected to do with each:

| kind            | meaning                                      | adapter's move          |
| --------------- | -------------------------------------------- | ----------------------- |
| `unreachable`   | no answer (DNS, refused, timeout)            | retry with backoff      |
| `server`        | 5xx                                          | retry with backoff      |
| `rate_limited`  | 429; honours Retry-After                     | retry after that long   |
| `auth`          | 401, or a 403 that is not `type_disabled`    | stop; tell the user     |
| `type_disabled` | 403 `type_disabled` — the server turned off  | flip the type off       |
| `rejected`      | 4xx with a reason — this document is refused | wait for it to change   |
| `conflict`      | 409 — a source id belongs to another account | wait for it to change   |
| `protocol`      | a 2xx that is not the JSON we expect         | retry with backoff      |

The token is sent as an `Authorization` header and appears in no log line,
no exception message and no URL. Redirects are not followed: a redirecting
response must not be able to re-aim a bearer token at a host the user never
pasted (the same rule the webhook notifier lives by).
"""

from __future__ import annotations

import json
import logging
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Literal

import httpx

log = logging.getLogger(__name__)

Kind = Literal[
    "unreachable",
    "server",
    "rate_limited",
    "auth",
    "type_disabled",
    "rejected",
    "conflict",
    "protocol",
]

CAPABILITIES_PATH = "/v1/capabilities"
FETCH_TIMEOUT_S = 15.0
# A bundle can be tens of MB, and a Raspberry Pi on Wi-Fi is a legitimate
# place to run this from.
UPLOAD_TIMEOUT_S = 120.0
MAX_ERROR_BODY = 2000  # how much of an error response is kept for the reason
DEFAULT_RETRY_AFTER_S = 60.0


def _user_agent() -> str:
    try:
        return f"gt7-datalogger/{version('gt7-datalogger')}"
    except PackageNotFoundError:  # pragma: no cover - a source checkout without install
        return "gt7-datalogger/dev"


class SyncError(Exception):
    """A request that did not do what was asked, classified for the adapter."""

    def __init__(
        self,
        kind: Kind,
        message: str,
        status: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.kind: Kind = kind
        self.message = message
        self.status = status
        self.retry_after = retry_after

    @property
    def transient(self) -> bool:
        """Whether trying the same request again later could succeed."""
        return self.kind in ("unreachable", "server", "rate_limited", "protocol")


def _reason(resp: httpx.Response) -> tuple[str, str]:
    """(error code, human reason) from an error body, best effort.

    The service answers `{"error": "<code>", "reason": "<text>"}`; anything
    else — a proxy's HTML page, FastAPI's `{"detail": …}` — is reduced to
    something a status line can show.
    """
    text = resp.text[:MAX_ERROR_BODY]
    try:
        body = resp.json()
    except ValueError:
        return "", text.strip() or resp.reason_phrase
    if not isinstance(body, dict):
        return "", text.strip() or resp.reason_phrase
    code = str(body.get("error") or "")
    for key in ("reason", "message", "detail"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return code, value.strip()
        if value is not None and not isinstance(value, str):
            return code, json.dumps(value)[:MAX_ERROR_BODY]
    return code, code or resp.reason_phrase


def classify(resp: httpx.Response) -> SyncError:
    """Turn an error response into the SyncError an adapter acts on."""
    status = resp.status_code
    code, reason = _reason(resp)
    if status == 401:
        return SyncError("auth", "token rejected — create a new one in the sync portal", status)
    if status == 403:
        if code == "type_disabled":
            return SyncError("type_disabled", reason or "server no longer accepts this", status)
        return SyncError("auth", reason or "not allowed — check the token's scopes", status)
    if status == 409:
        return SyncError("conflict", reason or "conflict", status)
    if status == 429:
        header = resp.headers.get("retry-after", "")
        try:
            retry_after = float(header) if header else DEFAULT_RETRY_AFTER_S
        except ValueError:
            retry_after = DEFAULT_RETRY_AFTER_S
        return SyncError("rate_limited", "rate limited by the server", status, retry_after)
    if 400 <= status < 500:
        return SyncError("rejected", reason or f"HTTP {status}", status)
    return SyncError("server", f"server error (HTTP {status})", status)


def validate_capabilities(raw: Any) -> dict[str, Any]:
    """The `/v1/capabilities` document, reduced to what the client uses.

    `types` lists only the data types the server currently accepts; the
    per-type objects are kept as-is (the server may add hints — limits,
    descriptions — that a future adapter reads).
    """
    if not isinstance(raw, dict):
        raise SyncError("protocol", "capabilities: not a JSON object")
    types = raw.get("types")
    if not isinstance(types, dict):
        raise SyncError("protocol", "capabilities: no data types listed")
    out: dict[str, Any] = {}
    for name, spec in types.items():
        if not isinstance(name, str) or not name.isidentifier():
            raise SyncError("protocol", f"capabilities: bad type name {name!r}")
        if spec is None or spec is False:
            continue
        out[name] = spec if isinstance(spec, dict) else {}
    return {
        "server": str(raw.get("server") or ""),
        "version": str(raw.get("version") or ""),
        "types": out,
    }


class Transport:
    """One server, one token. Stateless apart from those."""

    def __init__(
        self,
        url: str,
        token: str,
        http: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self._token = token
        # Injected by tests; None means httpx's own.
        self._http = http

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            transport=self._http,
            headers={"User-Agent": _user_agent(), "Accept": "application/json"},
        )

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    async def capabilities(self) -> dict[str, Any]:
        """What the server accepts. Public: no token is sent."""
        try:
            async with self._client(FETCH_TIMEOUT_S) as client:
                resp = await client.get(self.url + CAPABILITIES_PATH)
        except httpx.HTTPError as exc:
            raise SyncError("unreachable", _describe(exc)) from exc
        if resp.status_code >= 400:
            raise classify(resp)
        try:
            raw = resp.json()
        except ValueError as exc:
            raise SyncError("protocol", "capabilities: not valid JSON") from exc
        return validate_capabilities(raw)

    async def post(self, path: str, body: bytes) -> dict[str, Any]:
        """POST a JSON document with the token; the parsed JSON reply."""
        try:
            async with self._client(UPLOAD_TIMEOUT_S) as client:
                resp = await client.post(
                    self.url + path,
                    content=body,
                    headers={**self._auth(), "Content-Type": "application/json"},
                )
        except httpx.HTTPError as exc:
            raise SyncError("unreachable", _describe(exc)) from exc
        if resp.status_code >= 400:
            raise classify(resp)
        if resp.status_code in (204, 205) or not resp.content:
            return {}
        try:
            reply = resp.json()
        except ValueError as exc:
            raise SyncError("protocol", "reply is not valid JSON") from exc
        if not isinstance(reply, dict):
            raise SyncError("protocol", "reply is not a JSON object")
        return reply


def _describe(exc: httpx.HTTPError) -> str:
    """An httpx failure in words a status line can show, minus the request.

    httpx's own messages sometimes repeat the URL; the token is never in the
    URL, but the description is kept to the failure class and its message
    all the same.
    """
    if isinstance(exc, httpx.TimeoutException):
        return "timed out"
    if isinstance(exc, httpx.ConnectError):
        return f"cannot connect: {exc}".rstrip(": ")
    return f"{type(exc).__name__}: {exc}".rstrip(": ")
