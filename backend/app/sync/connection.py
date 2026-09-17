"""The connection string: the one thing a user pastes.

The service issues one per token, `gt7sync://host/?token=…`, so connecting
is a single paste whether the server is the hosted one or somebody's own on
the LAN. It is parsed exactly once — into the server URL and the token, kept
as two separate settings — and after that the secret only ever travels as a
`Bearer` header. `gt7sync://` maps to `https://`; `gt7sync+http://` is
allowed so a self-hosted server on a LAN needs no certificate.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import SplitResult, parse_qs, urlsplit

# The hosted service. Overridden by whatever connection string is pasted.
DEFAULT_URL = "https://sync.gt7-datalogger.com"

SCHEMES = {
    "gt7sync": "https",
    "gt7sync+https": "https",
    "gt7sync+http": "http",
    # A plain URL is accepted too: someone running their own server may well
    # hand out the address in the form they are used to typing.
    "https": "https",
    "http": "http",
}
MAX_TOKEN_CHARS = 512
MAX_STRING_CHARS = 2048


class BadConnectionString(ValueError):
    """A string that cannot be turned into a server and a token."""


@dataclass(frozen=True, slots=True)
class Connection:
    url: str  # scheme://host[:port][/path], no trailing slash
    token: str


def parse_connection_string(text: str) -> Connection:
    text = text.strip()
    if not text:
        raise BadConnectionString("the connection string is empty")
    if len(text) > MAX_STRING_CHARS:
        raise BadConnectionString("the connection string is too long")
    parts = urlsplit(text)
    if parts.scheme.lower() not in SCHEMES:
        raise BadConnectionString(
            "expected a gt7sync://… connection string (or gt7sync+http:// for a LAN server)"
        )
    url = _server_url(parts)
    query = parse_qs(parts.query, keep_blank_values=True)
    token = (query.get("token") or [""])[0].strip()
    if not token:
        raise BadConnectionString("the connection string carries no token")
    return Connection(url=url, token=check_token(token))


def normalise_server(text: str) -> str:
    """A server typed by hand — `sync.example.com`, `https://sync.example.com`,
    `gt7sync+http://192.168.1.20:8787` — as the URL the client will call.

    Bare hosts get the `gt7sync://` (that is, https) reading; anything after
    the host is kept as a path prefix; a query is refused rather than
    silently dropped — a string with one is a connection string, and the
    caller routes those through `parse_connection_string` instead.
    """
    text = text.strip()
    if not text:
        raise BadConnectionString("the server address is empty")
    if len(text) > MAX_STRING_CHARS:
        raise BadConnectionString("the server address is too long")
    if "://" not in text:
        text = f"gt7sync://{text}"
    parts = urlsplit(text)
    if parts.scheme.lower() not in SCHEMES:
        raise BadConnectionString(
            "the server address must be a host name, or start with https://, http:// or gt7sync://"
        )
    if parts.query or parts.fragment:
        raise BadConnectionString("the server address must not carry a query string")
    return _server_url(parts)


def check_token(token: str) -> str:
    """A token as the service issues them: one printable line, no spaces."""
    token = token.strip()
    if not token:
        raise BadConnectionString("the token is empty")
    if len(token) > MAX_TOKEN_CHARS or any(c.isspace() or not c.isprintable() for c in token):
        raise BadConnectionString("the token is not well-formed")
    return token


def _server_url(parts: SplitResult) -> str:
    """scheme://host[:port][/path] from a split string whose scheme is known."""
    try:
        host = parts.hostname
        port = parts.port
    except ValueError as exc:  # a port that is not a number
        raise BadConnectionString(f"invalid host or port: {exc}") from exc
    if not host:
        raise BadConnectionString("no server named")
    if parts.username is not None or parts.password is not None:
        # The secret lives in the query, never in the authority: a string
        # with user:pass@ in it is not one the service issued.
        raise BadConnectionString("credentials in the host part are not supported")
    if ":" in host:  # IPv6 literal: urlsplit strips the brackets
        host = f"[{host}]"
    url = f"{SCHEMES[parts.scheme.lower()]}://{host}"
    if port is not None:
        url += f":{port}"
    return url + parts.path.rstrip("/")


def mask_token(token: str) -> str:
    """What the UI may show of a token: enough to recognise, not to use."""
    if not token:
        return ""
    if len(token) < 12:
        return "••••"
    return f"…{token[-4:]}"
