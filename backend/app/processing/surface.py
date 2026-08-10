"""Per-wheel surface contact: encoding, decoding, and off-track detection.

Packet format C reports one ASCII char per wheel (FL FR RL RR). The chars
seen so far: T (tarmac), C (curb/kerb), D (dirt), G (grass), S (sand),
s (snow) — enumerated from the community layout docs and being validated on
real hardware (issue #37; see scripts/surface_spike.py).

Per tick the four chars are packed into one small int stored in the lap's
"surface" sample column — 4 bits per wheel, FL in the lowest nibble:

    value = FL | FR << 4 | RL << 8 | RR << 12

Code 0 means "no surface data" (packet A/B/~), so a column of zeros from an
old capture stays distinguishable from four wheels on tarmac (0x1111).
The frontend mirrors this in frontend/src/lib/types.ts (SURFACE_*).
"""

from __future__ import annotations

from collections.abc import Sequence

# Loose surfaces (chars): Dirt, Grass, Sand, snow. "T" (tarmac) and
# "C" (curb/kerb) count as on-road. Kept here as the single source of truth;
# live_events imports it for the off-road webhook.
LOOSE_SURFACES = frozenset("DGSs")

SURFACE_NONE = 0  # packet A/B/~ — no surface data
SURFACE_TARMAC = 1
SURFACE_KERB = 2
SURFACE_DIRT = 3
SURFACE_GRASS = 4
SURFACE_SAND = 5
SURFACE_SNOW = 6
SURFACE_OTHER = 7  # char the mapping doesn't know (yet) — treated as on-road

CHAR_CODES = {
    "T": SURFACE_TARMAC,
    "C": SURFACE_KERB,
    "D": SURFACE_DIRT,
    "G": SURFACE_GRASS,
    "S": SURFACE_SAND,
    "s": SURFACE_SNOW,
}
LOOSE_CODES = frozenset({SURFACE_DIRT, SURFACE_GRASS, SURFACE_SAND, SURFACE_SNOW})

# An off-track excursion = >= 3 wheels on a loose surface (two wheels over a
# line is a normal track-limits nibble, matching the live off-road webhook)
# held for >= 6 ticks (~0.1 s), so a single-frame surface flicker on a kerb
# edge doesn't condemn the lap.
OFF_TRACK_MIN_WHEELS = 3
OFF_TRACK_MIN_TICKS = 6


def encode_surface(surface: str | None) -> int:
    """Pack the packet's 4-char surface string into one int (0 = no data)."""
    if surface is None or len(surface) < 4:
        return SURFACE_NONE
    value = 0
    for i in range(4):
        value |= CHAR_CODES.get(surface[i], SURFACE_OTHER) << (4 * i)
    return value


def wheel_codes(value: int) -> tuple[int, int, int, int]:
    """Unpack a surface sample into per-wheel codes (FL, FR, RL, RR)."""
    v = int(value)
    return (v & 0xF, (v >> 4) & 0xF, (v >> 8) & 0xF, (v >> 12) & 0xF)


def loose_wheel_count(value: int) -> int:
    """How many wheels of this sample sit on a loose surface."""
    return sum(1 for c in wheel_codes(value) if c in LOOSE_CODES)


def off_track_excursions(surface_col: Sequence[float]) -> int:
    """Count off-track excursions in a lap's "surface" column.

    Returns -1 when the column carries no surface data at all (empty, or
    recorded with a packet format below C) — "unknown", not "clean".
    """
    if not surface_col or all(v == SURFACE_NONE for v in surface_col):
        return -1
    count = 0
    run = 0
    for v in surface_col:
        if loose_wheel_count(int(v)) >= OFF_TRACK_MIN_WHEELS:
            run += 1
            if run == OFF_TRACK_MIN_TICKS:
                count += 1
        else:
            run = 0
    return count
