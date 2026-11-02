"""The lap analysis document (#115, app.processing.lap_analysis).

The circuit is a circle, so "the same place" is exact: two laps are level
when they are at the same angle, whatever distance each has covered. Laps run
anticlockwise from angle 0, which in GT7's coordinates is a right-hander all
the way round — the inside of every corner is the centre of the circle.
"""

import io
import json
import math
import zipfile
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models import AidsBits
from app.processing import analysis, cars, lap_analysis
from app.processing.cars import CarDatabase
from app.processing.track_limits import BorderIndex
from app.service import TelemetryService
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository
from tests.circle_track import RADIUS, TICK, Driver

OMEGA = 50.0 / RADIUS
FULL = 2 * math.pi
# Two corners, a quarter and three quarters of the way round.
APEX_1 = math.radians(90.0)
APEX_2 = math.radians(270.0)
AUTHORED = [
    {"n": 1, "name": "One", "direction": "R", "note": "",
     "apex": {"x": RADIUS * math.cos(APEX_1), "z": RADIUS * math.sin(APEX_1)},
     "entry": None, "exit": None},
    {"n": 2, "name": "", "direction": "R", "note": "",
     "apex": {"x": RADIUS * math.cos(APEX_2), "z": RADIUS * math.sin(APEX_2)},
     "entry": None, "exit": None},
]
SECTIONS = [
    {"n": 1, "name": "Back straight",
     "start": {"x": RADIUS * math.cos(math.radians(120.0)),
               "z": RADIUS * math.sin(math.radians(120.0))},
     "end": {"x": RADIUS * math.cos(math.radians(180.0)),
             "z": RADIUS * math.sin(math.radians(180.0))}},
]
SESSION = {
    "id": 12, "started_at": "2026-09-26T10:00:00+00:00", "car_id": 7,
    "car_name": "Test Car", "car_category": "GR3", "car_manufacturer": "Testa",
    "car_year": 2022, "car_drivetrain": "MR", "car_aspiration": "NA",
    "car_displacement_cc": 4194, "car_power_bhp": 557, "car_torque_kgfm": 51.0,
    "car_weight_kg": 1250, "car_performance_points": 731.91,
    # Carried on the session row and of no use to the document.
    "car_full_name": "Testa Test Car", "car_length_mm": 4619,
    "note": "", "tags": ["dry"], "track_name": "Circle", "bests_excluded": False,
    "final_position": None, "final_total_positions": None,
    "race_laps": None, "race_time_ms": None,
}


def in_degrees(theta: float, spans: list[tuple[float, float]]) -> bool:
    deg = math.degrees(theta)
    return any(lo <= deg < hi for lo, hi in spans)


def circle_lap(
    radius: float = RADIUS,
    omega: float = OMEGA,
    brake: list[tuple[float, float]] | None = None,
    slow: list[tuple[float, float]] | None = None,
    tcs: list[tuple[float, float]] | None = None,
    positions: bool = True,
) -> dict[str, list[float]]:
    """A lap round the circle. `brake`, `slow` and `tcs` are stretches in
    degrees of angle: full braking, 90 km/h on the speedometer, TCS acting."""
    brake = [(70.0, 85.0), (250.0, 265.0)] if brake is None else brake
    slow = [(80.0, 100.0), (260.0, 280.0)] if slow is None else slow
    n = int(FULL / omega / TICK)
    out: dict[str, list[float]] = {
        key: [] for key in (
            "t", "dist", "speed", "brake", "throttle", "gear", "rpm", "yaw_rate",
            "aids", "tt_fl", "tt_fr", "tt_rl", "tt_rr",
        )
    }
    if positions:
        out["pos_x"], out["pos_z"] = [], []
    for k in range(n):
        theta = omega * k * TICK
        braking = in_degrees(theta, brake)
        out["t"].append(k * TICK)
        out["dist"].append(radius * theta)
        out["speed"].append(90.0 if in_degrees(theta, slow) else 180.0)
        out["brake"].append(100.0 if braking else 0.0)
        out["throttle"].append(0.0 if braking else 100.0)
        out["gear"].append(3.0 if math.degrees(theta) < 180.0 else 4.0)
        out["rpm"].append(7000.0 + math.degrees(theta))
        out["yaw_rate"].append(omega)
        out["aids"].append(float(AidsBits.TCS) if in_degrees(theta, tcs or []) else 0.0)
        for wheel, temp in (("fl", 70.0), ("fr", 72.0), ("rl", 80.0), ("rr", 82.0)):
            out[f"tt_{wheel}"].append(temp)
        if positions:
            out["pos_x"].append(radius * math.cos(theta))
            out["pos_z"].append(radius * math.sin(theta))
    return out


def row(lap_id: int, number: int, time_ms: int, **fields: Any) -> dict[str, Any]:
    return {
        "id": lap_id, "number": number, "time_ms": time_ms, "counts_for_best": True,
        "clean_lap": True, "fuel_start": 50.0, "fuel_end": 48.0, "fuel_consumed": 2.0,
        "full_throttle_pct": 60.0, "full_brake_pct": 5.0, "coasting_pct": 3.0,
        "tire_spin_pct": 0.5, "max_speed": 180.0, "tcs_active_pct": 1.0,
        "asm_active_pct": 0.0, "off_track_count": 0, "off_survey_count": -1,
        "event_counts": {}, "total_ticks": 1000,
    } | fields


def ring(radius: float, y: float | None = None) -> list[list[float | None]]:
    points = [
        [radius * math.cos(math.radians(a)), radius * math.sin(math.radians(a)), y]
        for a in range(0, 360, 2)
    ]
    return [*points, points[0]]


BORDERS = {"borders": {"R": [ring(RADIUS - 6.0)], "L": [ring(RADIUS + 6.0)]}}


def compile_document(
    laps: list[tuple[dict[str, Any], dict[str, list[float]]]],
    events: dict[int, list[dict[str, Any]]] | None = None,
    borders: BorderIndex | None = None,
    sections: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    counting = [r for r, _ in laps if r["counts_for_best"]]
    reference = min(counting, key=lambda r: r["time_ms"], default=None)
    samples = {r["id"]: s for r, s in laps}
    compiler = lap_analysis.SessionAnalysis(
        session=SESSION,
        circuit={"name": "Circle", "official_id": "C1"},
        reference=reference,
        reference_samples=samples[reference["id"]] if reference else None,
        authored_corners=AUTHORED,
        authored_sections=sections or [],
        borders=borders,
        app_version="1.2.3",
    )
    gearing = {"ratios": [3.0, 2.0, 1.5], "top_speed": 2.8, "rpm_alert": 7400.0}
    for r, s in laps:
        compiler.add(r, s, (events or {}).get(r["id"], []), gearing)
    document = compiler.document()
    # Whatever else it is, it is a JSON document.
    return json.loads(json.dumps(document))


def lap_of(document: dict[str, Any], number: int) -> dict[str, Any]:
    return next(lap for lap in document["laps"] if lap["number"] == number)


def corner_of(lap: dict[str, Any], n: int) -> dict[str, Any]:
    return next(c for c in lap["corners"] if c["n"] == n)


def test_the_envelope_and_what_the_session_was() -> None:
    document = compile_document([
        (row(21, 2, 38_000), circle_lap()),
        (row(20, 1, 37_700, counts_for_best=False), circle_lap()),  # quicker, but an out-lap
        (row(22, 3, 37_900), circle_lap()),
    ])
    assert document["format"] == "gt7-datalogger-lap-analysis"
    assert document["version"] == 1
    assert document["app_version"] == "1.2.3"
    assert document["compiled_at"]
    assert document["conventions"]["vs_ref"]
    assert document["session"] == {
        "id": 12, "started_at": "2026-09-26T10:00:00+00:00", "laps": 3,
        "counting_laps": 2, "tags": ["dry"],
    }
    assert document["car"] == {
        # What the car is...
        "id": 7, "name": "Test Car", "category": "GR3",
        "manufacturer": "Testa", "year": 2022, "drivetrain": "MR",
        # ...what it was when it left the showroom, which tuning changes...
        "stock": {
            "aspiration": "NA", "displacement_cc": 4194, "power_bhp": 557,
            "torque_kgfm": 51.0, "weight_kg": 1250, "performance_points": 731.91,
        },
        # ...and the gearbox it was driven with, which GT7 does broadcast.
        "gearing": {"ratios": [3.0, 2.0, 1.5], "rpm_alert": 7400.0},
    }
    assert "showroom" in document["conventions"]["car.stock"]
    assert document["circuit"] == {
        "name": "Circle", "official_id": "C1",
        # The last sample of a lap is a tick short of the line.
        "lap_length_m": pytest.approx(RADIUS * FULL, abs=2.0),
        "corners": "authored", "sections": "none", "surveyed": False,
    }
    # The fastest lap that COUNTS, not the fastest lap.
    assert document["reference"]["lap_id"] == 22
    assert document["reference"]["number"] == 3
    assert document["reference"]["scope"] == "session"
    assert [lap["number"] for lap in document["laps"]] == [1, 2, 3]
    assert lap_of(document, 3)["reference"] is True
    assert "reference" not in lap_of(document, 2)
    assert lap_of(document, 2)["time_vs_ref"] == 100
    assert "time_vs_ref" in lap_of(document, 1)
    assert lap_of(document, 1)["counts_for_best"] is False


def test_a_car_the_inventory_does_not_know_has_no_figures_at_all() -> None:
    """The session row then holds empty strings and noughts, which are the
    inventory having no answer — not a car with no power."""
    unknown = SESSION | {
        "car_name": "Car #9999", "car_manufacturer": "", "car_year": 0,
        "car_drivetrain": "", "car_aspiration": "", "car_displacement_cc": 0,
        "car_power_bhp": 0, "car_torque_kgfm": 0.0, "car_weight_kg": 0,
        "car_performance_points": 0.0,
    }
    compiler = lap_analysis.SessionAnalysis(
        session=unknown, circuit={"name": "Circle"}, reference=row(1, 1, 37_700),
        reference_samples=circle_lap(),
    )
    compiler.add(row(1, 1, 37_700), circle_lap())
    assert compiler.document()["car"] == {"id": 7, "name": "Car #9999", "category": "GR3"}


def test_an_electric_car_has_stock_figures_but_no_displacement() -> None:
    electric = SESSION | {"car_aspiration": "EV", "car_displacement_cc": 0}
    compiler = lap_analysis.SessionAnalysis(
        session=electric, circuit={"name": "Circle"}, reference=None, reference_samples=None,
    )
    stock = compiler.document()["car"]["stock"]
    assert stock["aspiration"] == "EV"
    assert "displacement_cc" not in stock
    assert stock["power_bhp"] == 557


def test_corners_are_the_circuits_own_on_the_reference_lap() -> None:
    document = compile_document([(row(1, 1, 37_700), circle_lap())])
    one, two = document["corners"]
    assert one["n"] == 1
    assert one["name"] == "One"
    assert one["direction"] == "R"
    assert one["apex_m"] == pytest.approx(RADIUS * APEX_1, abs=1.0)
    assert one["entry_m"] == pytest.approx(one["apex_m"] - 75.0, abs=1.0)
    assert one["exit_m"] == pytest.approx(one["apex_m"] + 75.0, abs=1.0)
    assert "name" not in two  # an unnamed corner has no name, not an empty one
    assert two["apex_m"] == pytest.approx(RADIUS * APEX_2, abs=1.0)


def test_what_the_reference_lap_did_carries_no_comparison() -> None:
    document = compile_document([(row(1, 1, 37_700), circle_lap())])
    corner = corner_of(lap_of(document, 1), 1)
    apex = RADIUS * APEX_1
    assert corner["braking"]["on_from_apex_m"] == pytest.approx(
        RADIUS * math.radians(70.0) - apex, abs=1.0
    )
    assert corner["braking"]["off_from_apex_m"] == pytest.approx(
        RADIUS * math.radians(85.0) - apex, abs=1.0
    )
    assert corner["braking"]["length_m"] == pytest.approx(RADIUS * math.radians(15.0), abs=1.5)
    assert corner["braking"]["peak_pct"] == 100.0
    assert corner["speed"]["min"] == 90.0
    assert corner["speed"]["min_gear"] == 3
    assert corner["throttle"]["flat"] is False
    assert not any(key.endswith("vs_ref") for key in corner)
    assert not any(key.endswith("vs_ref") for key in corner["speed"])
    assert "on_vs_ref" not in corner["braking"]
    assert "line" not in corner


def test_braking_is_compared_at_the_same_place_not_the_same_distance() -> None:
    """A lap on a wider line covers 3 % more road. Braking at the same spot,
    its own distance says it braked 12 m later; it did not."""
    wide = circle_lap(radius=RADIUS + 10.0)
    early = circle_lap(brake=[(64.0, 85.0), (250.0, 265.0)])
    document = compile_document([
        (row(1, 1, 37_700), circle_lap()),
        (row(2, 2, 37_800), wide),
        (row(3, 3, 37_900), early),
    ])
    assert lap_of(document, 2)["aligned"] is True
    assert corner_of(lap_of(document, 2), 1)["braking"]["on_vs_ref"] == pytest.approx(0.0, abs=1.0)
    own = (RADIUS + 10.0) * math.radians(70.0) - RADIUS * math.radians(70.0)
    assert own == pytest.approx(12.2, abs=0.1)
    # Six degrees of a 300 m circle earlier.
    assert corner_of(lap_of(document, 3), 1)["braking"]["on_vs_ref"] == pytest.approx(
        -RADIUS * math.radians(6.0), abs=1.0
    )
    assert corner_of(lap_of(document, 3), 2)["braking"]["on_vs_ref"] == pytest.approx(0.0, abs=1.0)


def test_time_lost_is_accounted_for_round_the_whole_lap() -> None:
    slow = circle_lap(omega=OMEGA / 1.02)  # 2 % slower everywhere
    document = compile_document([(row(1, 1, 37_700), circle_lap()), (row(2, 2, 38_454), slow)])
    ref, lap = lap_of(document, 1), lap_of(document, 2)
    total = 0.0
    for n in (1, 2):
        mine, theirs = corner_of(lap, n), corner_of(ref, n)
        assert theirs["time_ms"] == pytest.approx(150.0 / 50.0 * 1000, abs=30.0)
        assert mine["time_vs_ref"] == pytest.approx(theirs["time_ms"] * 0.02, abs=5.0)
        assert mine["approach"]["time_vs_ref"] == pytest.approx(
            theirs["approach"]["time_ms"] * 0.02, abs=5.0
        )
        assert mine["approach"]["length_m"] == theirs["approach"]["length_m"]
        total += mine["time_vs_ref"] + mine["approach"]["time_vs_ref"]
    total += lap["to_line"]["time_vs_ref"]
    # Corners, the road between them and the run to the line: the whole lap.
    assert total == pytest.approx(37_700 * 0.02, abs=25.0)
    assert "time_vs_ref" not in ref["to_line"]


def test_speeds_are_compared_with_the_reference() -> None:
    lap = circle_lap()
    lap["speed"] = [v - 4.0 if v == 90.0 else v for v in lap["speed"]]
    document = compile_document([(row(1, 1, 37_700), circle_lap()), (row(2, 2, 37_800), lap)])
    speed = corner_of(lap_of(document, 2), 1)["speed"]
    assert speed["min"] == 86.0
    assert speed["min_vs_ref"] == -4.0
    assert speed["exit_vs_ref"] == 0.0
    assert speed["min_from_apex_m"] == pytest.approx(-RADIUS * math.radians(10.0), abs=1.5)


def test_the_line_is_measured_against_the_reference_and_the_road_edges() -> None:
    tight = circle_lap(radius=RADIUS - 3.0)
    document = compile_document(
        [(row(1, 1, 37_700), circle_lap()), (row(2, 2, 37_800), tight)],
        borders=BorderIndex(BORDERS),
    )
    assert document["circuit"]["surveyed"] is True
    ref, lap = corner_of(lap_of(document, 1), 1), corner_of(lap_of(document, 2), 1)
    # Three metres nearer the centre: tighter, at every mark.
    for mark in ("entry_m", "apex_m", "exit_m"):
        assert lap["line"][mark] == pytest.approx(3.0, abs=0.05)
    assert ref["edges"]["apex"] == {
        "inside_m": pytest.approx(6.0, abs=0.1), "outside_m": pytest.approx(6.0, abs=0.1),
    }
    assert lap["edges"]["apex"] == {
        "inside_m": pytest.approx(3.0, abs=0.1), "outside_m": pytest.approx(9.0, abs=0.1),
    }
    assert lap["edges"]["entry"]["inside_m"] == pytest.approx(3.0, abs=0.1)


def test_a_wider_line_reads_as_wider() -> None:
    document = compile_document(
        [(row(1, 1, 37_700), circle_lap()), (row(2, 2, 37_800), circle_lap(radius=RADIUS + 2.0))]
    )
    assert corner_of(lap_of(document, 2), 2)["line"]["apex_m"] == pytest.approx(-2.0, abs=0.05)
    assert "edges" not in corner_of(lap_of(document, 2), 2)


def test_a_lap_that_cannot_be_lined_up_says_so_and_has_no_line() -> None:
    document = compile_document([
        (row(1, 1, 37_700), circle_lap()),
        (row(2, 2, 37_800), circle_lap(positions=False)),
    ])
    lap = lap_of(document, 2)
    assert lap["aligned"] is False
    assert "line" not in corner_of(lap, 1)
    assert corner_of(lap, 1)["time_ms"] > 0


def test_a_corner_the_reference_braked_for_and_the_lap_did_not() -> None:
    document = compile_document([
        (row(1, 1, 37_700), circle_lap()),
        (row(2, 2, 37_800), circle_lap(brake=[(250.0, 265.0)])),
    ])
    assert corner_of(lap_of(document, 2), 1)["braking"] == {"none": True}
    assert "on_vs_ref" in corner_of(lap_of(document, 2), 2)["braking"]


def test_a_corner_nobody_braked_for_has_no_braking_block() -> None:
    document = compile_document([
        (row(1, 1, 37_700), circle_lap(brake=[(250.0, 265.0)])),
        (row(2, 2, 37_800), circle_lap(brake=[(250.0, 265.0)])),
    ])
    assert "braking" not in corner_of(lap_of(document, 1), 1)
    assert "braking" not in corner_of(lap_of(document, 2), 1)


def test_events_and_traction_control_are_counted_at_their_corner() -> None:
    at = lambda deg: RADIUS * math.radians(deg)  # noqa: E731
    events = {
        2: [
            {"type": "lockup", "start_dist": at(75.0), "end_dist": at(78.0),
             "wheels": ["fl"], "severity": 0.7},
            {"type": "wheelspin", "start_dist": at(95.0), "end_dist": at(97.0),
             "wheels": ["rl"], "severity": 1.3},
            {"type": "lockup", "start_dist": at(255.0), "end_dist": at(258.0),
             "wheels": ["fr"], "severity": 0.6},
        ]
    }
    document = compile_document(
        [
            (row(1, 1, 37_700), circle_lap()),
            (row(2, 2, 37_800, event_counts={"lockup": 2, "wheelspin": 1}),
             circle_lap(tcs=[(90.0, 97.0)])),
        ],
        events=events,
    )
    lap = lap_of(document, 2)
    assert lap["events"] == {"lockup": 2, "wheelspin": 1}
    one, two = corner_of(lap, 1), corner_of(lap, 2)
    assert one["braking"]["lockups"] == 1
    assert one["throttle"]["wheelspin"] == 1
    # Seven degrees of the apex-to-exit stretch, which is 75 m of a 300 m circle.
    assert one["throttle"]["tcs_pct"] == pytest.approx(
        100 * math.radians(7.0) * RADIUS / 75.0, abs=2.0
    )
    assert two["braking"]["lockups"] == 1
    assert "wheelspin" not in two["throttle"]
    assert "tcs_pct" not in two["throttle"]
    assert "lockups" not in corner_of(lap_of(document, 1), 1)["braking"]


def test_sections_are_timed_and_carry_a_speed_trap() -> None:
    slow = circle_lap(omega=OMEGA / 1.02)
    document = compile_document(
        [(row(1, 1, 37_700), circle_lap()), (row(2, 2, 38_454), slow)],
        sections=SECTIONS,
    )
    assert document["circuit"]["sections"] == "authored"
    (section,) = document["sections"]
    length = RADIUS * math.radians(60.0)
    assert section["name"] == "Back straight"
    assert section["length_m"] == pytest.approx(length, abs=1.5)
    (ref,) = lap_of(document, 1)["sections"]
    (lap,) = lap_of(document, 2)["sections"]
    assert ref["time_ms"] == pytest.approx(length / 50.0 * 1000, abs=30.0)
    assert "time_vs_ref" not in ref
    assert lap["time_vs_ref"] == pytest.approx(ref["time_ms"] * 0.02, abs=5.0)
    assert ref["top"]["speed"] == 180.0
    assert ref["top"]["gear"] == 3
    assert ref["end"]["speed"] == 180.0
    assert ref["end"]["gear"] == 4
    assert ref["end"]["rpm"] == pytest.approx(7180, abs=2)


def test_upshifts_are_summed_up_by_gear() -> None:
    lap = circle_lap()
    document = compile_document([(row(1, 1, 37_700), lap)])
    assert lap_of(document, 1)["shifts"] == [
        {"from_gear": 3, "count": 1, "rpm_avg": 7180, "rpm_min": 7180, "rpm_max": 7180}
    ]


def test_the_laps_own_figures_come_along() -> None:
    document = compile_document([(row(1, 1, 37_700, off_survey_count=2), circle_lap())])
    lap = lap_of(document, 1)
    assert lap["fuel"] == {"start": 50.0, "end": 48.0, "used": 2.0}
    assert lap["aids"] == {"tcs_pct": 1.0, "asm_pct": 0.0}
    assert lap["tyre_temp"]["rr"] == {"avg": 82.0, "max": 82.0, "end": 82.0}
    assert lap["clean"] is True
    assert lap["off_track"] == 0
    assert lap["off_survey"] == 2
    assert "events" not in lap  # none happened


def test_a_verdict_that_could_not_be_reached_is_absent_not_nought() -> None:
    document = compile_document(
        [(row(1, 1, 37_700, clean_lap=None, off_track_count=-1), circle_lap())]
    )
    lap = lap_of(document, 1)
    assert lap["clean"] is None
    assert "off_track" not in lap
    assert "off_survey" not in lap


def consistency_laps(clean: list[bool | None]) -> list[tuple[dict, dict]]:
    starts = [70.0, 68.0, 66.0, 64.0]
    return [
        (
            row(i + 1, i + 1, 37_700 + 100 * i, clean_lap=clean[i]),
            circle_lap(brake=[(starts[i], 85.0), (250.0, 265.0)]),
        )
        for i in range(len(clean))
    ]


def test_consistency_is_taken_over_clean_laps() -> None:
    document = compile_document(consistency_laps([True, True, True, False]))
    consistency = document["consistency"]
    assert consistency["lap_time"] == {
        "laps": 4, "best_ms": 37_700, "median_ms": 37_850,
        "std_ms": pytest.approx(129.1, abs=0.1), "pct": pytest.approx(0.341, abs=0.001),
    }
    corners = consistency["corners"]
    assert corners["basis"] == "clean"
    assert corners["laps"] == [1, 2, 3]
    one = next(c for c in corners["by_corner"] if c["n"] == 1)
    # Braking points two degrees apart, three laps of them.
    step = RADIUS * math.radians(2.0)
    assert one["brake_on"]["laps"] == 3
    assert one["brake_on"]["spread"] == pytest.approx(2 * step, abs=1.0)
    assert one["brake_on"]["std"] == pytest.approx(step, abs=0.5)
    two = next(c for c in corners["by_corner"] if c["n"] == 2)
    assert two["brake_on"]["spread"] == pytest.approx(0.0, abs=1.0)
    assert two["min_speed"]["spread"] == 0.0


def test_unjudged_laps_stand_in_when_too_few_were_verified() -> None:
    document = compile_document(consistency_laps([None, None, True, False]))
    corners = document["consistency"]["corners"]
    assert corners["basis"] == "counting"
    assert corners["laps"] == [1, 2, 3]  # the lap known to be dirty stays out


def test_two_laps_make_no_spread() -> None:
    document = compile_document(consistency_laps([True, True]))
    assert document["consistency"] is None


def test_a_session_with_no_lap_that_counts_has_no_reference() -> None:
    document = compile_document([
        (row(1, 1, 37_700, counts_for_best=False), circle_lap()),
    ])
    assert document["reference"] is None
    assert document["corners"] == []
    assert document["circuit"]["corners"] == "none"
    lap = lap_of(document, 1)
    assert lap["fuel"]["used"] == 2.0
    assert "corners" not in lap
    assert "time_vs_ref" not in lap
    assert "aligned" not in lap


def test_detected_corners_say_that_they_were_detected() -> None:
    reference = circle_lap()
    compiler = lap_analysis.SessionAnalysis(
        session=SESSION, circuit={"name": ""}, reference=row(1, 1, 37_700),
        reference_samples=reference, authored_corners=[], authored_sections=[],
    )
    compiler.add(row(1, 1, 37_700), reference)
    document = compiler.document()
    expected = "detected" if analysis.detect_corners(reference) else "none"
    assert document["circuit"]["corners"] == expected
    assert all("name" not in corner for corner in document["corners"])


# --- the road's edges ----------------------------------------------------------


def test_a_border_is_measured_only_where_it_was_surveyed() -> None:
    """Past the loose end of a surveyed stretch the edge carries on
    unrecorded: the distance to that end is not the distance to the edge."""
    borders = BorderIndex({"borders": {
        "L": [[[0.0, 5.0, None], [100.0, 5.0, None]]],
        "R": [[[0.0, -5.0, None], [50.0, -5.0, None], [100.0, -5.0, None]]],
    }})
    assert borders.distance("L", 50.0, 0.0) == pytest.approx(5.0)
    assert borders.distance("R", 50.0, 1.0) == pytest.approx(6.0)  # abeam of a joint
    assert borders.distance("L", 104.0, 2.0) is None  # past the end: 5 m to it, unknown
    assert borders.distance("L", -3.0, 5.0) is None
    assert borders.distance("L", 50.0, 60.0) is None  # too far to be this road's edge


def test_a_closed_border_has_no_loose_ends() -> None:
    borders = BorderIndex(BORDERS)
    assert borders.distance("R", RADIUS, 0.0) == pytest.approx(6.0, abs=0.1)


def test_a_border_on_another_level_is_not_this_roads() -> None:
    borders = BorderIndex({"borders": {
        "L": [[[0.0, 5.0, 20.0], [100.0, 5.0, 20.0]]], "R": [],
    }})
    assert borders.distance("L", 50.0, 0.0, 20.5) == pytest.approx(5.0)
    assert borders.distance("L", 50.0, 0.0, 8.0) is None
    assert borders.distance("L", 50.0, 0.0) == pytest.approx(5.0)  # no elevation: plan decides


def test_a_section_the_lap_never_came_near_is_left_out() -> None:
    far = [{"n": 2, "name": "Elsewhere", "start": {"x": 5000.0, "z": 0.0},
            "end": {"x": 5100.0, "z": 0.0}}]
    placed = analysis.project_sections(circle_lap(), SECTIONS + far)
    assert [section["n"] for section in placed] == [1]
    assert placed[0]["start_dist"] == pytest.approx(RADIUS * math.radians(120.0), abs=1.0)
    assert placed[0]["end_dist"] == pytest.approx(RADIUS * math.radians(180.0), abs=1.0)


# --- over the API --------------------------------------------------------------


def inventory(tmp_path) -> CarDatabase:
    """An inventory that knows the car the test driver is in (id 7)."""
    path = tmp_path / "cars.json"
    path.write_text(cars.dumps(cars.Inventory(
        generated="2026-08-30",
        cars={7: cars.Car(
            id=7, name="GT-R NISMO '17", full_name="Nissan GT-R NISMO '17",
            manufacturer="Nissan", year=2017, category="Gr.N", drivetrain="4WD",
            aspiration="TC", displacement_cc=3799, power_bhp=599, torque_kgfm=66.5,
            weight_kg=1720, performance_points=620.43,
        )},
    )))
    database = CarDatabase()
    database.load(path)
    return database


@pytest.fixture
async def client(tmp_path):
    settings = Settings(source="udp", db_path=tmp_path / "test.db", ws_rate=1000)
    engine = make_engine(settings.db_path)
    await init_db(engine)
    service = TelemetryService(
        settings, Repository(make_session_factory(engine)), inventory(tmp_path)
    )

    app = create_app()
    app.router.lifespan_context = None  # type: ignore[assignment]
    app.state.service = service
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c, service
    await engine.dispose()


async def drive_laps(service: TelemetryService, radii: list[float]) -> None:
    driver = Driver()
    for lap, radius in enumerate(radii, start=1):
        for p in driver.lap(lap, radius=radius, last_lap_ms=37_699 if lap > 1 else -1):
            await service._on_packet(p)
    await service._on_packet(driver.cross(len(radii) + 1, 37_699))


async def test_the_document_is_served_for_a_session(client) -> None:
    c, service = client
    await drive_laps(service, [RADIUS, RADIUS + 5.0, RADIUS])
    session_id = service.session_id

    resp = await c.get(f"/api/sessions/{session_id}/analysis.json")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/json"
    assert resp.headers["content-disposition"] == (
        f'attachment; filename="gt7-session-{session_id}-analysis.json"'
    )
    document = resp.json()
    assert document["format"] == "gt7-datalogger-lap-analysis"
    assert document["version"] == 1
    assert document["session"]["id"] == session_id
    laps = (await c.get(f"/api/sessions/{session_id}/laps")).json()
    assert [lap["id"] for lap in document["laps"]] == sorted(lap["id"] for lap in laps)
    counting = [lap for lap in laps if lap["counts_for_best"]]
    best = min(counting, key=lambda lap: (lap["time_ms"], lap["id"]))
    assert document["reference"]["lap_id"] == best["id"]
    assert all(lap["aligned"] for lap in document["laps"])
    # None of what it was compiled from.
    text = resp.text
    assert "pos_x" not in text
    assert "samples" not in text
    assert len(text) < 20_000


async def test_a_session_recorded_today_carries_the_car_and_its_gearbox(client) -> None:
    """From the first packet of a new session: the inventory's facts and
    stock figures are written onto the session row as it opens, the gearbox
    onto each lap as it is saved, and the document reads both back."""
    c, service = client
    driver = Driver()
    ratios = (3.2, 2.1, 1.5, 1.2, 1.0, 0.8)
    times = [37_699, 37_700]
    for lap in (1, 2):
        packets = driver.lap(lap, last_lap_ms=times[lap - 2] if lap > 1 else -1)
        for p in packets:
            p.gear_ratios = ratios
            p.rpm_alert_max = 8000
            await service._on_packet(p)
    await service._on_packet(driver.cross(3, times[-1]))

    document = (await c.get(f"/api/sessions/{service.session_id}/analysis.json")).json()
    assert document["car"] == {
        "id": 7, "name": "GT-R NISMO '17", "category": "Gr.N",
        "manufacturer": "Nissan", "year": 2017, "drivetrain": "4WD",
        "stock": {
            "aspiration": "TC", "displacement_cc": 3799, "power_bhp": 599,
            "torque_kgfm": 66.5, "weight_kg": 1720, "performance_points": 620.43,
        },
        "gearing": {"ratios": list(ratios), "rpm_alert": 8000},
    }


async def test_the_document_of_a_missing_session_is_404(client) -> None:
    c, _ = client
    assert (await c.get("/api/sessions/9999/analysis.json")).status_code == 404


async def test_the_session_archive_carries_the_document(client) -> None:
    c, service = client
    await drive_laps(service, [RADIUS, RADIUS])
    session_id = service.session_id
    resp = await c.get(f"/api/sessions/{session_id}/export.zip")
    assert resp.status_code == 200
    folder = f"gt7-session-{session_id}"
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        manifest = json.loads(zf.read(f"{folder}/session.json"))
        document = json.loads(zf.read(f"{folder}/analysis.json"))
    assert manifest["analysis"] == "analysis.json"
    served = (await c.get(f"/api/sessions/{session_id}/analysis.json")).json()
    served.pop("compiled_at")
    document.pop("compiled_at")
    assert document == served


async def test_an_archive_is_still_written_when_the_document_cannot_be(
    client, monkeypatch
) -> None:
    c, service = client
    await drive_laps(service, [RADIUS, RADIUS])

    async def broken(session_id: int) -> dict:
        raise RuntimeError("no figures today")

    monkeypatch.setattr(service, "lap_analysis", broken)
    resp = await c.get(f"/api/sessions/{service.session_id}/export.zip")
    assert resp.status_code == 200
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = zf.namelist()
        manifest = json.loads(zf.read(f"gt7-session-{service.session_id}/session.json"))
    assert not any(name.endswith("analysis.json") for name in names)
    assert "analysis" not in manifest
    assert len(manifest["laps"]) == 2
