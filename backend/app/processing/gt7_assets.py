"""Reading the data GT7's own list pages render from.

The car list and the track list at gran-turismo.com are single-page apps, and
neither ships its data as JSON. Each page names a hash-stamped index chunk,
that chunk names one hash-stamped data chunk per locale, and each data chunk
is one object literal exported under a single name:

    var e={car102:{nameShort:`Skyline GTS-R (R31) '87`,...},...};export{e as Cars}

The hashes change on every site build, so the chain is walked each run rather
than hard-coded — which is why this is three small functions over text instead
of a URL constant. `scripts/build_track_metadata.py` has walked it for tracks
since #58; `car_source.py` walks it for cars at runtime, over httpx rather than
urllib. Nothing here does I/O for that reason: callers fetch, these parse.
"""

from __future__ import annotations

import json
import re
import urllib.request
from typing import Any

UA = {"User-Agent": "gt7-datalogger metadata builder"}

INDEX_CHUNK = re.compile(r"assets/(index-[A-Za-z0-9_-]+\.js)")


def http_get(url: str, timeout: int = 30) -> str:
    """Plain synchronous fetch, for the offline build scripts.

    The runtime path does not use this — it has an httpx client and an event
    loop, and a blocking urlopen inside either is a bug waiting to happen.
    """
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return str(resp.read().decode("utf-8"))


def index_chunk_name(page_html: str) -> str:
    """The `index-<hash>.js` the page loads its app from."""
    m = INDEX_CHUNK.search(page_html)
    if not m:
        raise ValueError("no index chunk in the page: the site layout changed")
    return m.group(1)


def data_chunk_names(index_js: str, dataset: str) -> dict[str, str]:
    """{locale: filename} for `<dataset>.<locale>-<hash>.js` in the index chunk.

    `dataset` is "cars", "tuners" or "tracks". Every locale the site publishes
    comes back; callers take the ones they need (English names from `gb`, and
    for tracks the metric values from `de`).
    """
    pattern = re.compile(rf"{re.escape(dataset)}\.([a-z]+)-[A-Za-z0-9_-]+\.js")
    return {m.group(1): m.group(0) for m in pattern.finditer(index_js)}


def parse_js_object(src: str) -> dict[str, Any]:
    """The single object literal a data chunk exports, as a dict.

    The literal is JSON in all but three respects: keys are bare, strings are
    backtick-quoted, and decimals may be written bare (`.5`). Fixing those with
    plain regex over the whole text would rewrite matching sequences *inside*
    the strings too — car names really do contain colons, commas and braces —
    so the source is scanned once, strings are re-emitted as JSON strings, and
    the syntax fixes are applied only to the code between them.
    """
    try:
        body = src[src.index("{"): src.rindex(";export")]
    except ValueError as exc:
        raise ValueError("not a GT7 data chunk: no `{...};export` found") from exc

    out: list[str] = []
    code: list[str] = []
    i, n = 0, len(body)
    escapes = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f"}

    def flush_code() -> None:
        segment = "".join(code)
        segment = re.sub(r'([{,])([A-Za-z_][A-Za-z0-9_]*|\d+):', r'\1"\2":', segment)
        segment = re.sub(r":\s*\.(\d)", r":0.\1", segment)
        out.append(segment)
        code.clear()

    while i < n:
        ch = body[i]
        if ch in "`\"'":
            flush_code()
            quote = ch
            i += 1
            buf: list[str] = []
            while i < n and body[i] != quote:
                if body[i] == "\\" and i + 1 < n:
                    nxt = body[i + 1]
                    if nxt == "u" and i + 6 <= n:
                        buf.append(chr(int(body[i + 2:i + 6], 16)))
                        i += 6
                    else:
                        # \" \' \` \\ \/ all stand for the character itself.
                        buf.append(escapes.get(nxt, nxt))
                        i += 2
                    continue
                buf.append(body[i])
                i += 1
            if i >= n:
                raise ValueError("unterminated string in data chunk")
            out.append(json.dumps("".join(buf)))
            i += 1
        else:
            code.append(ch)
            i += 1
    flush_code()

    parsed: dict[str, Any] = json.loads("".join(out))
    return parsed
