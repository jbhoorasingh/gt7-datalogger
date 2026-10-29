"""Configurable lap voice precision and upstream fallback behaviour."""

from types import SimpleNamespace

import pytest

from app.race_engineer.formatter import (
    spoken_precise_gap,
    spoken_precise_lap_time,
)
from app.race_engineer.manager import RaceEngineerManager
from app.race_engineer.models import CalloutRequest

CASES = [
    (1, "round", "one minute thirty-two point five", "point two seconds"),
    (1, "truncate", "one minute thirty-two point four", "point two seconds"),
    (2, "round", "one minute thirty-two point four nine", "point two five seconds"),
    (2, "truncate", "one minute thirty-two point four eight", "point two four seconds"),
    (3, "round", "one minute thirty-two point four eight seven", "point two four seven seconds"),
    (3, "truncate", "one minute thirty-two point four eight seven", "point two four seven seconds"),
]


@pytest.mark.parametrize("places,rounding,lap_text,gap_text", CASES)
def test_precise_formatters(places, rounding, lap_text, gap_text):
    assert spoken_precise_lap_time(92487, places, rounding) == lap_text
    assert spoken_precise_gap(247, places, rounding) == gap_text


@pytest.mark.parametrize("places,rounding,lap_text,gap_text", CASES)
def test_completed_lap_announcement(places, rounding, lap_text, gap_text):
    manager = RaceEngineerManager()
    manager.configure(
        sector_voice_enabled=True,
        sector_voice_decimals=places,
        sector_voice_rounding=rounding,
    )

    request = CalloutRequest(
        event_type="lap_time",
        text="Original upstream lap announcement",
        message_key="lap.time",
        message_args={"time_ms": 92487, "delta_ms": 247},
        dedupe_key=f"precision-test-{places}-{rounding}",
    )

    callout = manager._emit(request)

    assert callout is not None
    assert callout.text == (
        f"Lap time, {lap_text}. {gap_text.capitalize()} slower."
    )


@pytest.mark.parametrize("places,rounding,lap_text,gap_text", CASES)
def test_personal_best_announcement(places, rounding, lap_text, gap_text):
    manager = RaceEngineerManager()
    manager.configure(
        sector_voice_enabled=True,
        sector_voice_decimals=places,
        sector_voice_rounding=rounding,
    )

    request = SimpleNamespace(
        event_type="personal_best",
        text="Original upstream personal best",
        message_args={"time_ms": 92487, "gain_ms": 247},
    )

    assert manager._lap_announcement_text(request) == (
        f"New personal best, {lap_text}. {gap_text.capitalize()} faster."
    )


@pytest.mark.parametrize("event_type", ["lap_time", "personal_best"])
def test_disabled_sector_voice_preserves_upstream_text(event_type):
    manager = RaceEngineerManager()
    manager.configure(
        sector_voice_enabled=False,
        sector_voice_decimals=3,
        sector_voice_rounding="truncate",
    )

    request = SimpleNamespace(
        event_type=event_type,
        text="Original upstream wording must remain unchanged.",
        message_args={"time_ms": 92487, "delta_ms": 247, "gain_ms": 247},
    )

    assert manager._lap_announcement_text(request) == request.text


def test_truncated_zero_gap_is_not_announced():
    manager = RaceEngineerManager()
    manager.configure(
        sector_voice_enabled=True,
        sector_voice_decimals=1,
        sector_voice_rounding="truncate",
    )

    request = SimpleNamespace(
        event_type="lap_time",
        text="Original",
        message_args={"time_ms": 92487, "delta_ms": 99},
    )

    assert manager._lap_announcement_text(request) == (
        "Lap time, one minute thirty-two point four."
    )


def test_precision_does_not_change_unrelated_callouts():
    manager = RaceEngineerManager()
    manager.configure(
        sector_voice_enabled=True,
        sector_voice_decimals=3,
    )

    request = SimpleNamespace(
        event_type="fuel_warning",
        text="Fuel warning, original wording.",
        message_args={},
    )

    assert manager._lap_announcement_text(request) == request.text


@pytest.mark.parametrize(
    "time_ms,places,rounding,expected",
    [
        (59999, 2, "round", "one minute zero point zero zero"),
        (59999, 2, "truncate", "fifty-nine point nine nine seconds"),
        (59950, 1, "round", "one minute zero point zero"),
        (59950, 1, "truncate", "fifty-nine point nine seconds"),
        (60000, 3, "round", "one minute zero point zero zero zero"),
        (120000, 2, "round", "two minutes zero point zero zero"),
        (1000, 3, "round", "one point zero zero zero second"),
        (1, 3, "round", "point zero zero one seconds"),
    ],
)
def test_minute_boundary_precision(time_ms, places, rounding, expected):
    assert spoken_precise_lap_time(time_ms, places, rounding) == expected


@pytest.mark.parametrize(
    "gap_ms,places,rounding,expected",
    [
        (999, 2, "round", "one point zero zero second"),
        (999, 2, "truncate", "point nine nine seconds"),
        (-1249, 2, "round", "one point two five seconds"),
        (-1249, 2, "truncate", "one point two four seconds"),
        (0, 3, "round", "zero point zero zero zero seconds"),
    ],
)
def test_gap_precision_edge_cases(gap_ms, places, rounding, expected):
    assert spoken_precise_gap(gap_ms, places, rounding) == expected


def test_sector_voice_toggle_updates_lap_precision():
    manager = RaceEngineerManager()

    request = SimpleNamespace(
        event_type="lap_time",
        text="Original upstream lap announcement.",
        message_args={"time_ms": 92487, "delta_ms": 247},
    )

    manager.configure(
        sector_voice_enabled=False,
        sector_voice_decimals=3,
        sector_voice_rounding="round",
    )
    assert manager._lap_announcement_text(request) == request.text

    manager.configure(sector_voice_enabled=True)
    assert manager._lap_announcement_text(request) == (
        "Lap time, one minute thirty-two point four eight seven. "
        "Point two four seven seconds slower."
    )

    manager.configure(
        sector_voice_decimals=1,
        sector_voice_rounding="truncate",
    )
    assert manager._lap_announcement_text(request) == (
        "Lap time, one minute thirty-two point four. "
        "Point two seconds slower."
    )

    manager.configure(sector_voice_enabled=False)
    assert manager._lap_announcement_text(request) == request.text


def test_personal_best_truncated_zero_gain_is_suppressed():
    manager = RaceEngineerManager()
    manager.configure(
        sector_voice_enabled=True,
        sector_voice_decimals=1,
        sector_voice_rounding="truncate",
    )

    request = SimpleNamespace(
        event_type="personal_best",
        text="Original",
        message_args={"time_ms": 92487, "gain_ms": 99},
    )

    assert manager._lap_announcement_text(request) == (
        "New personal best, one minute thirty-two point four."
    )
