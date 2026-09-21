"""Regenerate smooth_vectors.json from track_compile.smooth_run.

The vectors are what holds two implementations of one routine together: this
repository's `track_compile.smooth_run`, and `smoothRun` in the track-data
repository's `tools/track_editor/track-editor-core.mjs`, which the track editor
runs on its own. Both test suites read the same file. Changing the routine
means regenerating it here, copying it there, and making the other side agree:

    python tests/data/make_smooth_vectors.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from app.processing import track_compile


def _kerb(radius: float, step: float) -> list[tuple[float, float]]:
    """A straight, a 90° corner of `radius`, a straight — snapped to the
    bundle's 1 m grid, no cell twice, as a survey records it."""
    true: list[tuple[float, float]] = []
    s = -20.0
    while s < 0:
        true.append((s, 0.0))
        s += step
    a = 0.0
    while a < math.pi / 2 * radius:
        true.append((radius * math.sin(a / radius), radius * (1 - math.cos(a / radius))))
        a += step
    s = 0.0
    while s < 20:
        true.append((radius, radius + s))
        s += step
    snapped = [(float(round(x)), float(round(z))) for x, z in true]
    return [snapped[0]] + [b for a, b in zip(snapped, snapped[1:], strict=False) if b != a]


CASES: dict[str, dict] = {
    # A border that steps a metre sideways where the record kind changes.
    "lateral_step": {
        "closed": False,
        "points": [(2.0 * i, 0.0 if i < 10 else 1.0) for i in range(20)],
    },
    # A loop with no ends: every vertex moves, round the seam.
    "noisy_ring": {
        "closed": True,
        "points": [
            ((10 + 0.4 * (-1) ** i) * math.cos(2 * math.pi * i / 24),
             (10 + 0.4 * (-1) ** i) * math.sin(2 * math.pi * i / 24))
            for i in range(24)
        ],
    },
    # Uneven spacing and one record three metres out: the cap is what stops it.
    "spike_uneven": {
        "closed": False,
        "points": [(0.0, 0.0), (1.0, 0.0), (4.0, 0.0), (5.0, 3.0), (6.5, 0.0),
                   (11.0, 0.0), (12.0, 0.0), (14.0, 0.0)],
    },
    # The corner a moving average ruins.
    "kerb_r3": {"closed": False, "points": _kerb(3.0, 1.0)},
    # Two records on one spot.
    "coincident": {
        "closed": False,
        "points": [(0.0, 0.0), (2.0, 0.5), (2.0, 0.5), (4.0, -0.5), (6.0, 0.0)],
    },
    # Too short to have an interior.
    "pair": {"closed": False, "points": [(0.0, 0.0), (5.0, 5.0)]},
}


def main() -> None:
    out = {
        "parameters": {
            "iterations": track_compile.SMOOTH_ITERATIONS,
            "lambda": track_compile.SMOOTH_LAMBDA,
            "mu": track_compile.SMOOTH_MU,
            "cap_m": track_compile.SMOOTH_CAP_M,
        },
        "cases": {
            name: {
                "closed": case["closed"],
                "points": [[round(x, 6), round(z, 6)] for x, z in case["points"]],
                "expected": [
                    [round(x, 6), round(z, 6)]
                    for x, z in track_compile.smooth_run(
                        [(round(x, 6), round(z, 6)) for x, z in case["points"]],
                        closed=case["closed"],
                    )
                ],
            }
            for name, case in CASES.items()
        },
    }
    path = Path(__file__).with_name("smooth_vectors.json")
    path.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {path} ({len(out['cases'])} cases)")


if __name__ == "__main__":
    main()
