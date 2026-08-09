"""Regenerate backend/data/tracks.json from official GT7 data + the GT wiki.

Primary source is the official tracklist's own data bundle — the JS asset the
page at gran-turismo.com/gb/gt7/tracklist/ renders from. It carries what no
wiki can: Polyphony's per-configuration ids (config `id`, track `baseId`),
official corner counts, and — in metric locales — exact integer metres for
length, elevation gap and longest straight. Two locales are fetched and joined
by id: `gb` for English names, `de` for the metric values. The asset URLs are
hash-stamped per build, so they are discovered from the page each run
(HTML -> index chunk -> tracks.<locale>-<hash>.js).

The wiki's Gran_Turismo_7/Track_List then enriches each layout with the facts
the official data lacks: real/original, rain, GT Sophy support, reversibility,
roadway, and the wiki page link. Wiki layouts are matched to official configs
by track name (a small alias table), then length + fuzzy layout name.

Run it again when a GT7 update adds tracks:

    python scripts/build_track_metadata.py

Validation is printed at the end; a wiki layout that stops matching or an
official config with no wiki data (e.g. Nürburgring Endurance II, which the
wiki list omits) is reported rather than silently dropped.
"""

from __future__ import annotations

import datetime
import difflib
import json
import re
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

OFFICIAL_PAGE = "https://www.gran-turismo.com/gb/gt7/tracklist/"
OFFICIAL_ASSETS = "https://www.gran-turismo.com/common/dist/gt7/tracklist/assets/"
WIKI_API = "https://gran-turismo.fandom.com/api.php"
WIKI_LIST_PAGE = "Gran Turismo 7/Track List"
UA = {"User-Agent": "gt7-datalogger track metadata builder"}
OUT = Path(__file__).resolve().parents[1] / "data" / "tracks.json"

# Official nameBase -> wiki course name, where they differ.
WIKI_TRACK_ALIASES = {
    "Autopolis": "Autopolis International Racing Course",
    "Goodwood": "Goodwood Motor Circuit",
    "Grand Valley - Highway 1": "Grand Valley",
    "Michelin Raceway Road Atlanta": "Michelin Raceway Road Atlanta",
}


def http_get(url: str) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return str(resp.read().decode("utf-8"))


# ---------------------------------------------------------------- official ---

def discover_locale_assets(page_html: str) -> dict[str, str]:
    """Page HTML -> index chunk -> {locale: tracks asset filename}."""
    index = re.search(r"assets/(index-[A-Za-z0-9_-]+\.js)", page_html)
    if not index:
        raise RuntimeError("could not find the tracklist index chunk in the page")
    chunk = http_get(OFFICIAL_ASSETS + index.group(1))
    return {
        m.group(1): m.group(0)
        for m in re.finditer(r"tracks\.([a-z]+)-[A-Za-z0-9_-]+\.js", chunk)
    }


def parse_official_js(src: str) -> dict[str, dict[str, Any]]:
    """The asset is `const e={...};export{e as Tracks};` — quote the keys and
    fix bare decimals, then it is JSON."""
    body = src[src.index("{"): src.rindex(";export")]
    body = re.sub(r"([{,])([A-Za-z_][A-Za-z0-9_]*|\d+):", r'\1"\2":', body)
    body = re.sub(r":\s*\.(\d)", r":0.\1", body)
    parsed: dict[str, dict[str, Any]] = json.loads(body)
    return parsed


def fetch_official() -> list[dict[str, Any]]:
    """Join gb (English names) and de (exact metres) locales by config id."""
    assets = discover_locale_assets(http_get(OFFICIAL_PAGE))
    for locale in ("gb", "de"):
        if locale not in assets:
            raise RuntimeError(f"tracklist assets missing locale {locale!r}: {assets}")
    en = parse_official_js(http_get(OFFICIAL_ASSETS + assets["gb"]))
    metric = parse_official_js(http_get(OFFICIAL_ASSETS + assets["de"]))
    configs = []
    for cid, e in en.items():
        m = metric[cid]
        configs.append(
            {
                "id": e["id"],
                "track_id": e["baseId"],
                "name_base": e["nameBase"].strip(),
                "name_long": e["nameLong"].strip(),
                "country_name": e["countryName"],
                "turns": e["cornerCount"],
                "length_m": m["length_v"],
                "elevation_m": m["elevationGap_v"],
                "longest_straight_m": m["straight_v"],
                "reverse": e["nameLong"].strip().endswith("Reverse"),
            }
        )
    return configs


# -------------------------------------------------------------------- wiki ---

def fetch_wikitext(page: str, cache: Path) -> str:
    cached = cache / (re.sub(r"[^A-Za-z0-9]+", "_", page) + ".json")
    if cached.exists():
        data = json.loads(cached.read_text())
    else:
        query = urllib.parse.urlencode(
            {
                "action": "parse",
                "page": page,
                "format": "json",
                "prop": "wikitext",
                "redirects": "1",
            }
        )
        data = json.loads(http_get(f"{WIKI_API}?{query}"))
        cached.write_text(json.dumps(data))
        time.sleep(0.25)
    if "error" in data:
        return ""
    return str(data["parse"]["wikitext"]["*"])


def parse_params(chunk: str) -> dict[str, str]:
    params: dict[str, str] = {}
    for part in chunk.split("|"):
        if "=" in part:
            key, _, value = part.partition("=")
            params[key.strip()] = value.strip().rstrip("}").strip()
    return params


def parse_wiki_list(wikitext: str) -> list[dict[str, Any]]:
    """One record per wiki layout: flags the official data does not carry."""
    layouts: list[dict[str, Any]] = []
    for block in wikitext.split("{{CourseWithLayouts")[1:]:
        header = parse_params(block.split("|layouts=")[0])
        for chunk in re.findall(r"\{\{CourseLayout\|(.*?)\}\}", block, re.S):
            p = parse_params(chunk)
            length = p.get("length", "").replace(",", "")
            layouts.append(
                {
                    "track": header.get("name", ""),
                    "country": header.get("country") or None,
                    "name": p.get("name", ""),
                    "wiki_page": p.get("link") or header.get("link") or header.get("name", ""),
                    "length_m": int(length) if length.isdigit() else None,
                    "type": "real" if p.get("type", "").lower() == "real" else "original",
                    "reversible": p.get("reversible", "").lower() in ("y", "yes"),
                    "rain": p.get("rain", "").lower() == "yes",
                    "sophy": {
                        "yes": "yes",
                        "no": "no",
                        "forwards only": "forwards_only",
                    }.get(p.get("sophy", "").lower(), "no"),
                }
            )
    return layouts


def infobox_roadway(wikitext: str) -> str | None:
    m = re.search(r"^\|roadway\s*=\s*(.+?)\s*$", wikitext, re.M)
    return m.group(1).strip() if m else None


# ------------------------------------------------------------------- merge ---

FILLER = re.compile(r"\b(layout|course|circuit|full|the)\b|[^a-z0-9]")


def norm_layout(name: str, track: str) -> str:
    """Comparable form of a layout name: drop the track name, filler words and
    punctuation, and normalise the synonyms the two sources disagree on."""
    n = name.lower()
    for word in re.findall(r"[a-z0-9]+", track.lower()):
        n = n.replace(word, " ", 1)
    n = n.replace("grand prix", " gp ").replace("shortcut", " short ")
    n = FILLER.sub(" ", n)
    return "".join(n.split())


def match_wiki(
    config: dict[str, Any], candidates: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Best wiki layout for an official forward config: length within 60 m,
    ties broken by layout-name similarity."""
    best, best_score = None, -1.0
    target = norm_layout(config["name_long"], config["name_base"]) or "fullcourse"
    for w in candidates:
        if w["length_m"] is None or abs(w["length_m"] - config["length_m"]) > 60:
            continue
        name = norm_layout(w["name"], "") or "fullcourse"
        score = difflib.SequenceMatcher(None, target, name).ratio()
        if score > best_score:
            best, best_score = w, score
    return best


def main() -> None:
    cache = Path(tempfile.gettempdir()) / "gt7-track-wiki-cache"
    cache.mkdir(exist_ok=True)

    configs = fetch_official()
    forward = [c for c in configs if not c["reverse"]]
    reverse = [c for c in configs if c["reverse"]]
    print(f"official: {len(configs)} configs = {len(forward)} forward + {len(reverse)} reverse")

    wiki = parse_wiki_list(fetch_wikitext(WIKI_LIST_PAGE, cache))
    print(f"wiki: {len(wiki)} layouts")

    roadway_cache: dict[str, str | None] = {}

    # Group official configs into tracks by Polyphony's track id.
    by_track: dict[str, list[dict[str, Any]]] = {}
    for c in configs:
        by_track.setdefault(c["track_id"], []).append(c)

    tracks: list[dict[str, Any]] = []
    unmatched_official: list[str] = []
    claimed: set[int] = set()
    for track_id, group in sorted(by_track.items(), key=lambda kv: kv[1][0]["name_base"]):
        base = group[0]["name_base"]
        wiki_track = WIKI_TRACK_ALIASES.get(base, base)
        wiki_layouts = [w for w in wiki if w["track"] == wiki_track]

        layouts: list[dict[str, Any]] = []
        for c in sorted((g for g in group if not g["reverse"]), key=lambda g: -g["length_m"]):
            # Reverse partner: same track, closest length (names abbreviate,
            # e.g. "BB Raceway Reverse", so length is the reliable key).
            partner = min(
                (r for r in group if r["reverse"]),
                key=lambda r: abs(r["length_m"] - c["length_m"]),
                default=None,
            )
            if partner is not None and abs(partner["length_m"] - c["length_m"]) > 60:
                partner = None
            w = match_wiki(c, [x for x in wiki_layouts if id(x) not in claimed])
            if w is None:
                unmatched_official.append(c["name_long"])
            else:
                claimed.add(id(w))
            if w and w["wiki_page"] not in roadway_cache:
                roadway_cache[w["wiki_page"]] = infobox_roadway(
                    fetch_wikitext(w["wiki_page"], cache)
                )
            short = c["name_long"].removeprefix(c["name_base"]).strip(" -–:")
            layouts.append(
                {
                    "name": w["name"] if w else (short or "Full Course"),
                    "official_id": c["id"],
                    "official_name": c["name_long"],
                    "turns": c["turns"],
                    "length_m": c["length_m"],
                    "elevation_m": c["elevation_m"],
                    "longest_straight_m": c["longest_straight_m"],
                    "type": w["type"] if w else None,
                    "rain": w["rain"] if w else None,
                    "sophy": w["sophy"] if w else None,
                    "roadway": roadway_cache.get(w["wiki_page"]) if w else None,
                    "wiki_page": w["wiki_page"] if w else None,
                    "reverse": (
                        {"official_id": partner["id"], "turns": partner["turns"]}
                        if partner
                        else None
                    ),
                }
            )
        tracks.append(
            {
                "name": base,
                "official_track_id": track_id,
                "country": next(
                    (w["country"] for w in wiki_layouts if w["country"]), None
                ),
                "country_name": group[0]["country_name"],
                "layouts": layouts,
            }
        )

    orphan_wiki = [
        f"{w['track']} / {w['name']}" for w in wiki if id(w) not in claimed
    ]
    total_layouts = sum(len(t["layouts"]) for t in tracks)
    with_reverse = sum(1 for t in tracks for la in t["layouts"] if la["reverse"])
    print(
        f"merged: tracks={len(tracks)} layouts={total_layouts} "
        f"with-reverse={with_reverse} configs={total_layouts + with_reverse}"
    )
    print(f"official configs without wiki enrichment: {unmatched_official or 'none'}")
    print(f"wiki layouts not matched to official: {orphan_wiki or 'none'}")

    out = {
        "meta": {
            "generated": datetime.date.today().isoformat(),
            "sources": {
                "official": OFFICIAL_PAGE
                + " (per-config ids, names, corner counts; exact metres from the "
                "metric locale of the same data)",
                "wiki": "https://gran-turismo.fandom.com/wiki/Gran_Turismo_7/Track_List"
                " (CC BY-SA: real/original, rain, GT Sophy, reversibility, roadway,"
                " page links)",
            },
            "tracks": len(tracks),
            "layouts": total_layouts,
            "configurations_including_reverse": total_layouts + with_reverse,
        },
        "tracks": tracks,
    }
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
