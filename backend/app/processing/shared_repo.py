"""Pulling contributed track bundles from a shared repository (#47).

Import (#50) made survey work portable one file at a time; this is the
"somewhere to pull from": a plain static file host holding an `index.json`
and the bundle documents it points at. No API, no auth, no server logic — the
point of a self-describing, versioned bundle format is that publishing one is
just putting the file where others can GET it. The canonical instance is the
project's own data repo, gt7-datalogger-track-data, whose Pages site already
serves exactly that; its builder writes the index this module reads:

    {
      "format": "gt7-datalogger-track-index",
      "version": 1,
      "bundle_format_version": 4,
      "configurations": [
        {"official_name": "Autodrome Lago Maggiore - East End", ...,
         "bundle": {"file": "tracks/autodrome-lago-maggiore-east-end.json",
                    "track": "Autodrome Lago Maggiore - East End",
                    "points": 3144, "runs": 3, "updated_at": "..."} | null}
      ],
      "unmatched_bundles": [ ...same shape as "bundle"... ]
    }

What this module cares about is the bundles: every configuration that HAS
one, plus the unmatched list (surveyed circuits nobody has tied to an
official layout — still worth pulling). `file` may be relative (resolved
against the index's own location) or absolute; the counts are advisory
display numbers — the truth is whatever the pulled document validates to.
Nothing pulled is trusted: it goes through `track_bundle.validate_document`
and the normal voting merge, exactly as a hand-imported file would (a shared
repo is the least trusted source of all — it is other people's machines).

Everything network-shaped lives here rather than in the API layer so the
size caps and index validation are unit-testable without an event loop.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from app.processing.track_bundle import MAX_TRACK_NAME, BundleError, _integer, _text

INDEX_FORMAT = "gt7-datalogger-track-index"
INDEX_VERSION = 1
# An index is names, URLs and counts; ten megabytes of it is not an index.
MAX_INDEX_BYTES = 10 * 1024 * 1024
MAX_INDEX_ENTRIES = 500
FETCH_TIMEOUT_S = 15.0


def index_url(configured: str) -> str:
    """The index document's URL from the configured setting.

    A URL ending in .json is taken as the index itself; anything else is a
    directory and gets the conventional name appended.
    """
    url = configured.strip().rstrip("/")
    return url if url.endswith(".json") else f"{url}/index.json"


def resolve_url(index: str, entry: str) -> str:
    """An index entry's bundle URL, made absolute and confined to http(s).

    The index is remote content: letting it name a `file://` path — or any
    other scheme — would turn "list what's available" into "read what you
    like off the server's disk".
    """
    resolved = urljoin(index, entry)
    if urlparse(resolved).scheme not in ("http", "https"):
        raise BundleError(f"bundle url {entry!r} is not http(s)")
    return resolved


def _validate_entry(bundle: Any, where: str) -> dict[str, Any]:
    if not isinstance(bundle, dict):
        raise BundleError(f"{where} is not an object")
    track = _text(bundle.get("track", ""), f"{where}: track", MAX_TRACK_NAME).strip()
    url = _text(bundle.get("file", ""), f"{where}: file", 500).strip()
    if not track or not url:
        raise BundleError(f"{where}: track and file are required")
    row: dict[str, Any] = {"track": track, "url": url}
    # Advisory display numbers; refused when malformed rather than shown.
    for field in ("points", "runs", "corners"):
        if bundle.get(field) is not None:
            row[field] = _integer(bundle[field], f"{where}: {field}", limit=1e7)
    if bundle.get("updated_at") is not None:
        row["updated_at"] = _text(bundle["updated_at"], f"{where}: updated_at", 64)
    return row


def validate_index(raw: Any) -> list[dict[str, Any]]:
    """The bundles an untrusted index offers, rebuilt from checked values.

    Configurations without a bundle are what most of the index is — the
    catalog's 121 layouts, listed so the site can show coverage — and they are
    simply not this module's business.
    """
    if not isinstance(raw, dict) or raw.get("format") != INDEX_FORMAT:
        raise BundleError(f"not a {INDEX_FORMAT} document")
    version = raw.get("version")
    if not isinstance(version, int) or version < 1:
        raise BundleError("missing or invalid index version")
    if version > INDEX_VERSION:
        raise BundleError(
            f"index is format v{version}; this build reads v{INDEX_VERSION} — upgrade first"
        )
    configurations = raw.get("configurations") or []
    unmatched = raw.get("unmatched_bundles") or []
    if not isinstance(configurations, list) or not isinstance(unmatched, list):
        raise BundleError("configurations and unmatched_bundles must be lists")
    if len(configurations) + len(unmatched) > MAX_INDEX_ENTRIES:
        raise BundleError(f"index lists more than {MAX_INDEX_ENTRIES} entries")
    out: list[dict[str, Any]] = []
    for i, config in enumerate(configurations):
        if not isinstance(config, dict):
            raise BundleError(f"configuration {i} is not an object")
        if config.get("bundle") is None:
            continue
        row = _validate_entry(config["bundle"], f"configuration {i}: bundle")
        if config.get("official_name"):
            row["official_name"] = _text(
                config["official_name"], f"configuration {i}: official_name",
                MAX_TRACK_NAME * 2,
            )
        out.append(row)
    for i, bundle in enumerate(unmatched):
        out.append(_validate_entry(bundle, f"unmatched bundle {i}"))
    return out


async def fetch_json(url: str, cap: int) -> Any:
    """GET a JSON document, refusing to buffer more than `cap` bytes.

    The cap is enforced while reading, not after: a Content-Length header is
    optional and unverified, so trusting it would let one oversized response
    take the process down anyway.
    """
    async with (
        httpx.AsyncClient(timeout=FETCH_TIMEOUT_S, follow_redirects=True) as client,
        client.stream("GET", url) as resp,
    ):
        resp.raise_for_status()
        chunks: list[bytes] = []
        size = 0
        async for chunk in resp.aiter_bytes():
            size += len(chunk)
            if size > cap:
                raise BundleError(f"document exceeds the {cap // (1024 * 1024)} MB cap")
            chunks.append(chunk)
    try:
        return json.loads(b"".join(chunks))
    except ValueError as exc:
        raise BundleError(f"not valid JSON: {exc}") from exc
