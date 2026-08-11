"""The official GT7 track catalog, flattened, and how a bundle gets matched to it.

`data/tracks.json` knows 41 tracks / 121 configurations with their real
lengths, turn counts and elevation. A survey bundle knows world coordinates
and a name somebody typed. Nothing joins the two automatically, and nothing
can: **GT7 broadcasts no track identifier**, and the catalog carries no world
coordinates, so there is no field on either side that identifies the other.

What is left is the name the driver typed (the Survey view autocompletes it
from this very catalog, so it is usually exact) and, for a circuit that has
also been named in the DB, the lap length its signature recorded. Both are
evidence, neither is proof — so this produces a SUGGESTION with its reasoning
attached, and the confirmed match is only ever written by a user accepting it
(#46). A silently wrong match would attach the wrong turn count to a circuit
and quietly mislead every "15 of 17 corners labelled" readout after it.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

# Below this the match is not worth putting in front of anyone.
SUGGEST_MIN_SCORE = 0.55
# Lap length varies with the racing line; the identification code already
# allows 4 % between a signature and a stored track.
LENGTH_AGREE = 0.04
LENGTH_DISAGREE = 0.15


@lru_cache(maxsize=4)
def load(path: str) -> dict[str, Any]:
    # The bundled file only changes with a release; cache the parse.
    data: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    return data


def configurations(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """Every drivable configuration, reverse layouts included, as flat rows.

    The reverse of a layout is a separate configuration with its own official
    id and turn count but the same tarmac — and a different world path, so it
    is a different bundle.
    """
    out: list[dict[str, Any]] = []
    for track in catalog.get("tracks", []):
        for layout in track.get("layouts", []):
            base = {
                "track": track["name"],
                "country": track.get("country_name", ""),
                "layout": layout["name"],
                "official_id": layout.get("official_id", ""),
                "official_name": layout.get("official_name", layout["name"]),
                "turns": layout.get("turns", 0) or 0,
                "length_m": layout.get("length_m", 0) or 0,
                "elevation_m": layout.get("elevation_m"),
                "reverse": False,
            }
            out.append(base)
            reverse = layout.get("reverse")
            if reverse:
                out.append({
                    **base,
                    "official_id": reverse.get("official_id", ""),
                    "official_name": f"{base['official_name']} (Reverse)",
                    "turns": reverse.get("turns", base["turns"]) or base["turns"],
                    "reverse": True,
                })
    return out


def _tokens(name: str) -> list[str]:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).split()


def _name_score(a: list[str], b: list[str]) -> float:
    """F1 over token sets: rewards overlap, punishes each side's extra words.

    Containment alone would rate "Lago Maggiore" a perfect match for every
    one of its five layouts, which is exactly the near-miss this has to tell
    apart.
    """
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    sa, sb = set(a), set(b)
    shared = len(sa & sb)
    if not shared:
        return 0.0
    return 2 * shared / (len(sa) + len(sb))


def suggest(
    name: str, configs: list[dict[str, Any]], length_m: float | None = None
) -> dict[str, Any] | None:
    """The official configuration a track name most likely refers to, or None."""
    tokens = _tokens(name)
    if not tokens:
        return None
    best: dict[str, Any] | None = None
    best_score = 0.0
    best_why = ""
    for cfg in configs:
        score = _name_score(tokens, _tokens(cfg["official_name"]))
        if score <= 0:
            continue
        why = "the name matches this layout exactly" if score >= 1.0 else "the name is close"
        if length_m and cfg["length_m"]:
            rel = abs(length_m - cfg["length_m"]) / cfg["length_m"]
            if rel <= LENGTH_AGREE:
                score += 0.15
                why += f", and the measured lap length agrees ({length_m:.0f} m)"
            elif rel > LENGTH_DISAGREE:
                # A name can be typed wrong; a lap length measured from
                # driving it cannot be off by a sixth.
                score -= 0.4
                why += f", but the measured lap length disagrees ({length_m:.0f} m)"
        if score > best_score:
            best, best_score, best_why = cfg, score, why
    if best is None or best_score < SUGGEST_MIN_SCORE:
        return None
    return {
        **{k: v for k, v in best.items() if k != "elevation_m"},
        "confidence": round(min(best_score, 1.0), 2),
        "why": best_why,
    }
