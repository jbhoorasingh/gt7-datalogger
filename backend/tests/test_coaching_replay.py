"""Post-lap coaching notes replayed from storage (#23): the same findings the
live CoachingDetector speaks, recomputed for sessions where voice was off."""

from app.race_engineer import replay

# An authored corner with explicit entry/exit anchors on a straight-line lap:
# projection puts it at entry 500 m / exit 620 m, mirroring the corner the
# live coaching tests inject directly.
AUTHORED = [{
    "n": 4, "name": "", "direction": "R", "note": "",
    "apex": {"x": 560.0, "z": 0.0},
    "entry": {"x": 500.0, "z": 0.0},
    "exit": {"x": 620.0, "z": 0.0},
}]


def _lap_samples(brake_at: float = 400.0, corner_extra_s: float = 0.0) -> dict:
    """Straight-line lap at 50 m/s; optionally `corner_extra_s` seconds lost
    smoothly across the corner window (500-620 m)."""
    dist = [i * 5.0 for i in range(200)]

    def t_at(d: float) -> float:
        base = d / 50.0
        if d <= 500.0:
            return base
        ramp = min(1.0, (d - 500.0) / 120.0)
        return base + corner_extra_s * ramp

    return {
        "dist": dist,
        "pos_x": list(dist),
        "pos_z": [0.0] * len(dist),
        "t": [t_at(d) for d in dist],
        "speed": [180.0] * len(dist),
        "brake": [100.0 if brake_at <= d < 500 else 0.0 for d in dist],
    }


def _row(lap_id: int, number: int, time_ms: int, counts: bool = True) -> dict:
    return {
        "id": lap_id, "number": number, "time_ms": time_ms,
        "car_id": 1, "fuel_consumed": 1.0, "counts_for_best": counts,
    }


def _notes(rows, samples, events=None, authored=AUTHORED):
    return replay.coaching_notes(rows, samples, events or {}, authored, "metric")


def test_replay_speaks_the_live_braking_callout() -> None:
    """Laps 2-3 braking 30 m before the reference: the exact live wording."""
    rows = [_row(i, i, 92_000 if i == 1 else 92_000 + i) for i in (1, 2, 3)]
    samples = {
        1: _lap_samples(brake_at=400.0),  # the session best -> reference
        2: _lap_samples(brake_at=370.0),
        3: _lap_samples(brake_at=370.0),
    }
    notes = _notes(rows, samples)
    assert len(notes) == 1
    assert notes[0]["number"] == 3
    finding = notes[0]["findings"][0]
    assert finding["type"] == "braking_early"
    assert finding["text"] == "You are braking early into turn four, about thirty meters."
    assert finding["corner"] == 4


def test_replay_says_each_observation_once() -> None:
    """A written log wants the habit noted once, not on every following lap."""
    rows = [_row(i, i, 92_000 if i == 1 else 92_000 + i) for i in range(1, 6)]
    samples = {1: _lap_samples(400.0)} | {i: _lap_samples(370.0) for i in range(2, 6)}
    notes = _notes(rows, samples)
    braking = [
        f for lap in notes for f in lap["findings"] if f["type"] == "braking_early"
    ]
    assert len(braking) == 1


def test_replay_finds_repeated_lockups_from_stored_events() -> None:
    rows = [_row(i, i, 92_000 if i == 1 else 92_000 + i) for i in (1, 2, 3)]
    samples = {i: _lap_samples() for i in (1, 2, 3)}
    lockup = {"type": "lockup", "severity": 0.5, "start_dist": 470.0, "wheels": ["fl"]}
    events = {i: [dict(lockup)] for i in (1, 2, 3)}
    notes = _notes(rows, samples, events)
    lockups = [
        f for lap in notes for f in lap["findings"] if f["type"] == "repeated_lockups"
    ]
    assert len(lockups) == 1
    assert lockups[0]["text"] == "Repeated front-left lockups into turn four."
    assert lockups[0]["corner"] == 4


def test_replay_reports_corner_time_loss_against_the_best_so_far() -> None:
    rows = [_row(1, 1, 92_000), _row(2, 2, 92_600)]
    samples = {
        1: _lap_samples(),
        2: _lap_samples(corner_extra_s=0.6),  # ~600 ms lost across the corner
    }
    notes = _notes(rows, samples)
    loss = [f for lap in notes for f in lap["findings"] if f["type"] == "corner_time_loss"]
    assert len(loss) == 1
    assert "turn four" in loss[0]["text"]


def test_replay_is_quiet_when_there_is_nothing_to_say() -> None:
    assert _notes([], {}) == []
    # Identical laps: no habit, no loss.
    rows = [_row(i, i, 92_000 + i) for i in (1, 2, 3)]
    samples = {i: _lap_samples() for i in (1, 2, 3)}
    assert _notes(rows, samples) == []


def test_replay_skips_partial_laps() -> None:
    """A lap flagged partial by the processor takes no part, same as live."""
    rows = [
        _row(1, 1, 92_000),
        _row(2, 2, 60_000, counts=False),  # out-lap: fast but not a lap
        _row(3, 3, 92_100),
    ]
    samples = {1: _lap_samples(400.0), 2: _lap_samples(370.0), 3: _lap_samples(370.0)}
    notes = _notes(rows, samples)
    # The partial lap neither becomes the reference nor earns findings.
    assert all(lap["number"] != 2 for lap in notes)


def test_replay_without_corners_stays_generic_or_silent() -> None:
    """No authored corners and a dead-straight lap: detection finds nothing,
    and every corner-anchored finding must stay quiet rather than crash."""
    rows = [_row(i, i, 92_000 if i == 1 else 92_000 + i) for i in (1, 2, 3)]
    samples = {1: _lap_samples(400.0), 2: _lap_samples(370.0), 3: _lap_samples(370.0)}
    notes = _notes(rows, samples, authored=[])
    assert all(f["type"] != "braking_early" for lap in notes for f in lap["findings"])
