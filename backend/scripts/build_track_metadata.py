"""Regenerate backend/data/tracks.json from the Gran Turismo wiki.

The wiki's Gran_Turismo_7/Track_List page carries one CourseWithLayouts
template per track and one CourseLayout template per layout — name, country,
real/original, per-layout length, reversible/rain/Sophy flags. Each layout's
own page adds an Infobox/Track with turns, elevation and longest straight.
Both are parsed from wikitext via the MediaWiki API: the templates are
structured data, so no HTML scraping and no guesswork.

Run it again when a GT7 update adds tracks:

    python scripts/build_track_metadata.py

Pages are cached in the system temp dir for the run, and the wiki is fetched
politely (one request every 250 ms, ~85 pages). The list's per-layout length
is authoritative; a layout whose wiki page measures a different variant (some
layouts share a page) is flagged page_describes_layout=false rather than
inheriting the wrong numbers silently.
"""

from __future__ import annotations

import datetime
import json
import re
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

API = "https://gran-turismo.fandom.com/api.php"
UA = {"User-Agent": "gt7-datalogger track metadata builder"}
LIST_PAGE = "Gran Turismo 7/Track List"
OUT = Path(__file__).resolve().parents[1] / "data" / "tracks.json"

# The track list links some layouts to a hub page (Infobox/Location) instead
# of the layout's own page; point them at the page that has the infobox.
PAGE_OVERRIDES = {
    ("Watkins Glen International", "Long Course"): "Watkins Glen Long Course",
    ("Watkins Glen International", "Short Course"): "Watkins Glen Short Course",
}

CONVERT = re.compile(r"\{\{Convert-lua\|([\d.,]+)\|m\|")
FLAG = re.compile(r"\{\{Flag\|([^}|]+)")


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
        req = urllib.request.Request(f"{API}?{query}", headers=UA)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
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


def parse_track_list(wikitext: str) -> list[dict[str, Any]]:
    tracks: list[dict[str, Any]] = []
    for block in wikitext.split("{{CourseWithLayouts")[1:]:
        header = parse_params(block.split("|layouts=")[0])
        layouts: list[dict[str, Any]] = []
        for chunk in re.findall(r"\{\{CourseLayout\|(.*?)\}\}", block, re.S):
            p = parse_params(chunk)
            length = p.get("length", "").replace(",", "")
            layouts.append(
                {
                    "name": p.get("name", ""),
                    "wiki_page": p.get("link") or header.get("link") or header.get("name", ""),
                    "type": "real" if p.get("type", "").lower() == "real" else "original",
                    "length_m": int(length) if length.isdigit() else None,
                    "reversible": p.get("reversible", "").lower() in ("y", "yes"),
                    "rain": p.get("rain", "").lower() == "yes",
                    "sophy": {
                        "yes": "yes",
                        "no": "no",
                        "forwards only": "forwards_only",
                    }.get(p.get("sophy", "").lower(), "no"),
                }
            )
        tracks.append(
            {
                "name": header.get("name", ""),
                "country": header.get("country") or None,
                "layouts": layouts,
            }
        )
    return tracks


def infobox_field(box: str, field: str) -> str | None:
    m = re.search(rf"^\|{field}\s*=\s*(.+?)\s*$", box, re.M)
    return m.group(1).strip() if m else None


def metres(raw: str | None) -> float | None:
    if not raw:
        return None
    m = CONVERT.search(raw)
    return float(m.group(1).replace(",", "")) if m else None


def parse_infobox(wikitext: str) -> dict[str, Any]:
    if "{{Infobox/Track" not in wikitext:
        return {}
    box = wikitext.split("{{Infobox/Track", 1)[1]
    turns = re.search(r"\d+", infobox_field(box, "turns") or "")
    flag = FLAG.search(infobox_field(box, "country") or "")
    return {
        "turns": int(turns.group()) if turns else None,
        "elevation_m": metres(infobox_field(box, "elevation")),
        "longest_straight_m": metres(infobox_field(box, "straight")),
        "length_m_infobox": metres(infobox_field(box, "length")),
        "roadway": infobox_field(box, "roadway") or None,
        "country_name": flag.group(1).strip() if flag else None,
    }


def main() -> None:
    cache = Path(tempfile.gettempdir()) / "gt7-track-wiki-cache"
    cache.mkdir(exist_ok=True)

    tracks = parse_track_list(fetch_wikitext(LIST_PAGE, cache))
    for track in tracks:
        for layout in track["layouts"]:
            layout["wiki_page"] = PAGE_OVERRIDES.get(
                (track["name"], layout["name"]), layout["wiki_page"]
            )

    pages = sorted(
        {la["wiki_page"] for t in tracks for la in t["layouts"] if la["wiki_page"]}
    )
    total = sum(len(t["layouts"]) for t in tracks)
    print(f"{len(tracks)} tracks, {total} layouts, {len(pages)} wiki pages to fetch")

    boxes = {}
    for i, page in enumerate(pages, 1):
        boxes[page] = parse_infobox(fetch_wikitext(page, cache))
        print(f"[{i}/{len(pages)}] {page}: turns={boxes[page].get('turns')}")

    for track in tracks:
        for layout in track["layouts"]:
            box = boxes.get(layout["wiki_page"], {})
            layout["turns"] = box.get("turns")
            layout["elevation_m"] = box.get("elevation_m")
            layout["longest_straight_m"] = box.get("longest_straight_m")
            layout["roadway"] = box.get("roadway")
            box_len = box.get("length_m_infobox")
            if box_len and layout["length_m"] and abs(box_len - layout["length_m"]) > 5:
                layout["page_describes_layout"] = False
            track.setdefault("country_name", box.get("country_name"))

    reversible = sum(1 for t in tracks for la in t["layouts"] if la["reversible"])
    missing_turns = sum(1 for t in tracks for la in t["layouts"] if not la["turns"])
    print(
        f"tracks={len(tracks)} layouts={total} reversible={reversible} "
        f"configs={total + reversible} layouts-missing-turns={missing_turns}"
    )

    out = {
        "meta": {
            "generated": datetime.date.today().isoformat(),
            "source": (
                "https://gran-turismo.fandom.com/wiki/Gran_Turismo_7/Track_List "
                "(CC BY-SA; upstream: https://www.gran-turismo.com/gb/gt7/tracklist/)"
            ),
            "tracks": len(tracks),
            "layouts": total,
            "configurations_including_reverse": total + reversible,
            "note": (
                "Per-layout template data is authoritative; the list page's prose "
                "may state a different configuration total. page_describes_layout="
                "false flags layouts whose wiki page infobox measures a different "
                "variant; their length_m (from the list) is still per-layout."
            ),
        },
        "tracks": tracks,
    }
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
