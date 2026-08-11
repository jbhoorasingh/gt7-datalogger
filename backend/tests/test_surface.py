"""Surface encoding, off-track excursion counting, and per-tick persistence."""

import pytest

from app.models import SimulatorFlags, TelemetryPacket
from app.processing.analysis import resample_by_distance
from app.processing.laps import CompletedLap, LapProcessor, SessionInfo
from app.processing.surface import (
    OFF_TRACK_MIN_TICKS,
    SURFACE_GRASS,
    SURFACE_KERB,
    SURFACE_NONE,
    SURFACE_OTHER,
    SURFACE_TARMAC,
    encode_surface,
    loose_wheel_count,
    off_track_excursions,
    wheel_codes,
)
from app.telemetry.packet import build_packet, parse_packet

ON_TRACK = int(SimulatorFlags.CAR_ON_TRACK)

TTTT = encode_surface("TTTT")
GGGG = encode_surface("GGGG")


def make_packet(**kwargs) -> TelemetryPacket:
    kwargs.setdefault("flags", ON_TRACK)
    return parse_packet(build_packet(**kwargs))


# --- encoding ----------------------------------------------------------------


def test_encode_none_and_short() -> None:
    assert encode_surface(None) == SURFACE_NONE
    assert encode_surface("TT") == SURFACE_NONE


def test_encode_wheel_order_fl_lowest_nibble() -> None:
    assert wheel_codes(encode_surface("CTTT")) == (
        SURFACE_KERB, SURFACE_TARMAC, SURFACE_TARMAC, SURFACE_TARMAC,
    )
    assert wheel_codes(encode_surface("TTTG"))[3] == SURFACE_GRASS


def test_encode_roundtrip_all_known() -> None:
    codes = wheel_codes(encode_surface("TCDG"))
    assert codes == (1, 2, 3, 4)
    assert wheel_codes(encode_surface("SsTT"))[:2] == (5, 6)


def test_unknown_char_maps_to_other_not_loose() -> None:
    value = encode_surface("XXXX")
    assert wheel_codes(value) == (SURFACE_OTHER,) * 4
    assert loose_wheel_count(value) == 0


def test_loose_wheel_count() -> None:
    assert loose_wheel_count(TTTT) == 0
    assert loose_wheel_count(GGGG) == 4
    assert loose_wheel_count(encode_surface("GGCT")) == 2


# --- excursion counting -------------------------------------------------------


def test_excursions_unknown_without_data() -> None:
    assert off_track_excursions([]) == -1
    assert off_track_excursions([0.0] * 100) == -1


def test_excursions_clean_lap() -> None:
    col = [float(TTTT)] * 50 + [float(encode_surface("CTCT"))] * 10
    assert off_track_excursions(col) == 0


def test_excursion_counted_once_per_run() -> None:
    col = (
        [float(TTTT)] * 20
        + [float(encode_surface("GGGT"))] * (OFF_TRACK_MIN_TICKS + 5)
        + [float(TTTT)] * 20
    )
    assert off_track_excursions(col) == 1


def test_short_flicker_not_an_excursion() -> None:
    col = (
        [float(TTTT)] * 20
        + [float(GGGG)] * (OFF_TRACK_MIN_TICKS - 1)
        + [float(TTTT)] * 20
    )
    assert off_track_excursions(col) == 0


def test_two_wheels_on_grass_is_not_off_track() -> None:
    col = [float(encode_surface("GGTT"))] * 100
    assert off_track_excursions(col) == 0


def test_separate_runs_count_separately() -> None:
    burst = [float(GGGG)] * OFF_TRACK_MIN_TICKS
    clean = [float(TTTT)] * 10
    assert off_track_excursions(clean + burst + clean + burst + clean) == 2


# --- lap integration ----------------------------------------------------------


class Collector:
    def __init__(self) -> None:
        self.laps: list[CompletedLap] = []
        self.sessions: list[SessionInfo] = []

    async def on_lap(self, lap: CompletedLap) -> None:
        self.laps.append(lap)

    async def on_session(self, info: SessionInfo) -> None:
        self.sessions.append(info)


@pytest.fixture
def setup() -> tuple[LapProcessor, Collector]:
    c = Collector()
    return LapProcessor(on_lap=c.on_lap, on_session=c.on_session, min_lap_ticks=1), c


async def test_surface_column_recorded_and_lap_judged(setup) -> None:
    proc, c = setup
    for _ in range(30):
        await proc.feed(make_packet(current_lap=1, fmt="C", surface_types="TTTT"))
    for _ in range(OFF_TRACK_MIN_TICKS + 2):
        await proc.feed(make_packet(current_lap=1, fmt="C", surface_types="GGGG"))
    for _ in range(30):
        await proc.feed(make_packet(current_lap=1, fmt="C", surface_types="TTTT"))
    await proc.feed(
        make_packet(current_lap=2, last_lap_time_ms=61_000, fmt="C", surface_types="TTTT")
    )
    assert len(c.laps) == 1
    lap = c.laps[0]
    assert lap.samples["surface"][0] == float(TTTT)
    assert float(GGGG) in lap.samples["surface"]
    assert lap.off_track_count == 1
    assert lap.clean_lap is False


async def test_lap_without_surface_data_is_unknown(setup) -> None:
    proc, c = setup
    for _ in range(30):
        await proc.feed(make_packet(current_lap=1))  # format A: no surface
    await proc.feed(make_packet(current_lap=2, last_lap_time_ms=61_000))
    lap = c.laps[0]
    assert set(lap.samples["surface"]) == {float(SURFACE_NONE)}
    assert lap.off_track_count == -1
    assert lap.clean_lap is None


# --- resampling ----------------------------------------------------------------


def test_surface_resamples_nearest_not_interpolated() -> None:
    samples = {
        "dist": [0.0, 10.0, 20.0, 30.0],
        "surface": [float(TTTT), float(TTTT), float(GGGG), float(GGGG)],
    }
    out = resample_by_distance(samples, step=5.0, columns=("surface",))
    assert set(out["surface"]) <= {float(TTTT), float(GGGG)}


# --- live survey (issue #37) ----------------------------------------------------


def test_survey_records_transition_with_contacts(tmp_path) -> None:
    import json
    import math

    from app.processing.survey import SurfaceSurvey

    survey = SurfaceSurvey()
    survey.start(tmp_path, track_width_m=1.6, track="Suzuka Circuit", session_id=7)
    common = dict(
        fmt="C", position=(100.0, 2.0, 50.0), velocity=(20.0, 0.0, 10.0),
        speed_mps=22.4, wheelbase_m=2.6,
    )
    assert survey.feed(make_packet(surface_types="TTTT", packet_id=1, **common)) is None
    record = survey.feed(make_packet(surface_types="CTTT", packet_id=2, **common))
    assert record is not None
    assert record["changed"] == ["FL"]
    contacts = record["contacts"]
    assert contacts is not None
    # Every contact sits one (wheelbase/2, track/2) diagonal from the car.
    expected = math.hypot(2.6 / 2, 1.6 / 2)
    for x, z in contacts.values():
        assert math.hypot(x - 100.0, z - 50.0) == pytest.approx(expected, rel=1e-3)
    # Front contacts lead the car along its velocity (positive projection).
    vx, vz = 20.0, 10.0
    assert (contacts["FL"][0] - 100.0) * vx + (contacts["FL"][1] - 50.0) * vz > 0
    assert (contacts["RL"][0] - 100.0) * vx + (contacts["RL"][1] - 50.0) * vz < 0

    survey.stop()
    assert survey.log_path is not None
    lines = survey.log_path.read_text().splitlines()
    # Self-describing log: a meta header (the track-width assumption is
    # invisible in the derived records), then one line per transition.
    assert len(lines) == 2
    meta = json.loads(lines[0])["meta"]
    assert meta["track"] == "Suzuka Circuit"
    assert meta["session_id"] == 7
    assert meta["track_width_m"] == 1.6
    assert json.loads(lines[1])["to"] == "CTTT"
    assert json.loads(lines[1])["session_id"] == 7
    status = survey.status()
    assert status["transitions"] == 1
    assert status["track"] == "Suzuka Circuit"
    assert status["histogram"]["FL"] == {"T": 1, "C": 1}
    assert status["recent"][0]["n"] == 1


def test_survey_trail_breadcrumbs(tmp_path) -> None:
    from app.processing.survey import SurfaceSurvey

    survey = SurfaceSurvey()
    survey.start(tmp_path, track_width_m=1.6)
    for i in range(101):  # 0.5 m steps, 50 m total
        survey.feed(make_packet(
            fmt="C", surface_types="TTTT", packet_id=i, position=(i * 0.5, 0.0, 0.0),
        ))
    status = survey.status()
    # One point per >= 2 m of travel, plus the starting point.
    assert 24 <= status["trail_points"] <= 27
    assert survey.trail[0] == [0.0, 0.0]
    assert survey.trail[-1][0] == pytest.approx(50.0, abs=2.0)
    survey.stop()


def test_survey_width_measured_from_kerb_rides(tmp_path) -> None:
    """Riding all four wheels over an edge and back measures the axle width."""
    import math

    from app.processing.survey import SurfaceSurvey

    tw_true, wb, speed, z_edge = 1.5, 2.6, 40.0, 1.0
    angle = math.radians(6)  # shallow approach, like a real kerb ride
    survey = SurfaceSurvey()
    survey.start(tmp_path, track_width_m=1.9)  # deliberately wrong assumption

    # (a, b) per wheel as in the production contact formula: contact =
    # P + a·f + b·(tw/2)·r with r = (f_z, -f_x); left wheels have b = -1.
    wheel_geometry = ((wb / 2, -1), (wb / 2, 1), (-wb / 2, -1), (-wb / 2, 1))

    px = pz = 0.0
    pid = 0
    for _ride in range(3):  # angle in, angle out, then run straight
        for heading, ticks in ((angle, 60), (-angle, 60), (0.0, 40)):
            fx, fz = math.cos(heading), math.sin(heading)
            for _ in range(ticks):
                pid += 1
                px += fx * speed / 60
                pz += fz * speed / 60
                surface = "".join(
                    "C" if pz + a * fz + b * (tw_true / 2) * -fx > z_edge else "T"
                    for a, b in wheel_geometry
                )
                survey.feed(make_packet(
                    fmt="C", surface_types=surface, packet_id=pid,
                    position=(px, 0.0, pz), velocity=(speed * fx, 0.0, speed * fz),
                    speed_mps=speed, wheelbase_m=wb,
                ))

    status = survey.status()
    # Every ride yields at least one accepted measurement (crossings of
    # adjacent rides inside the pairing window can add extra, equally valid
    # ones), and the measured width replaces the (wrong) assumption.
    assert status["width_samples"] >= 3
    assert status["width_estimate_m"] == pytest.approx(tw_true, abs=0.15)
    assert status["width_in_use_m"] == pytest.approx(tw_true, abs=0.15)
    assert status["recent"][-1]["tw_m"] is not None
    survey.stop()


def test_survey_width_rejects_strip_crossings(tmp_path) -> None:
    """Crossing a two-edged stripe is NOT a width measurement.

    The out/back crossings land on two different (parallel) lines, so the
    fitted "edge" is bogus — the group must be rejected, not mismeasured.
    """
    import math

    from app.processing.survey import SurfaceSurvey

    tw_true, wb = 1.5, 2.6
    nx = nz = math.sqrt(0.5)  # stripe edges at 45° to the direction of travel
    survey = SurfaceSurvey()
    survey.start(tmp_path, track_width_m=1.9)
    offsets = (  # (dx, dz), FL FR RL RR with f=(1,0) so r=(0,-1)
        (wb / 2, tw_true / 2), (wb / 2, -tw_true / 2),
        (-wb / 2, tw_true / 2), (-wb / 2, -tw_true / 2),
    )
    for i in range(260):
        px = 5.0 * i / 60.0
        surface = "".join(
            "G" if 4.0 < nx * (px + dx) + nz * dz < 8.0 else "T"
            for dx, dz in offsets
        )
        survey.feed(make_packet(
            fmt="C", surface_types=surface, packet_id=i,
            position=(px, 0.0, 0.0), velocity=(5.0, 0.0, 0.0),
            speed_mps=5.0, wheelbase_m=wb,
        ))
    status = survey.status()
    assert status["width_samples"] == 0
    assert status["width_in_use_m"] == 1.9  # assumption stays in force
    survey.stop()


def test_survey_width_falls_back_to_assumption(tmp_path) -> None:
    from app.processing.survey import SurfaceSurvey

    survey = SurfaceSurvey()
    survey.start(tmp_path, track_width_m=1.7)
    survey.feed(make_packet(fmt="C", surface_types="TTTT", packet_id=1))
    status = survey.status()
    assert status["width_estimate_m"] is None
    assert status["width_in_use_m"] == 1.7
    survey.stop()


def test_survey_flags_unknown_chars_and_non_c_packets(tmp_path) -> None:
    from app.processing.survey import SurfaceSurvey

    survey = SurfaceSurvey()
    survey.start(tmp_path, track_width_m=1.6)
    survey.feed(make_packet(fmt="C", surface_types="TTTW", packet_id=1))
    assert survey.status()["unknown_chars"] == {"W": 1}
    survey.feed(make_packet(packet_id=2))  # format A: no surface data
    assert survey.status()["no_surface_packets"] == 1
    survey.stop()
    assert survey.status()["active"] is False


def test_survey_border_side_tagging() -> None:
    """One side on kerb/loose + other side fully on tarmac = that border."""
    from app.processing.survey import _border_side

    assert _border_side("TTTT", "CTTT") == "L"  # FL onto kerb
    assert _border_side("CTTT", "TTTT") == "L"  # ...and back off it
    assert _border_side("TTTT", "CTGT") == "L"  # FL kerb + RL grass together
    assert _border_side("TTTT", "TCTT") == "R"  # FR onto kerb
    assert _border_side("TTTT", "TTTG") == "R"  # RR onto grass
    # Both sides involved, or the opposite side already off tarmac: no claim.
    assert _border_side("TTTT", "CCTT") is None
    assert _border_side("TGTT", "CGTT") is None  # FR was on grass already
    assert _border_side("GGGG", "GGGG") is None  # nothing changed


def test_survey_transition_carries_border(tmp_path) -> None:
    from app.processing.survey import SurfaceSurvey

    survey = SurfaceSurvey()
    survey.start(tmp_path, track_width_m=1.6)
    survey.feed(make_packet(fmt="C", surface_types="TTTT", packet_id=1))
    record = survey.feed(make_packet(fmt="C", surface_types="TTGT", packet_id=2))
    assert record is not None
    assert record["border"] == "L"  # RL is a left-side wheel
    survey.stop()


def test_survey_width_rejects_two_sided_lane_weave(tmp_path) -> None:
    """Weaving a narrow lane with grass on BOTH sides must not be accepted:
    left and right wheels cross different parallel boundaries, and solving
    across them yields the lane separation, not the axle width."""
    import math

    from app.processing.survey import SurfaceSurvey

    tw_true, wb, speed = 1.5, 2.6, 20.0
    angle = math.radians(8)
    survey = SurfaceSurvey()
    survey.start(tmp_path, track_width_m=1.7)
    wheel_geometry = ((wb / 2, -1), (wb / 2, 1), (-wb / 2, -1), (-wb / 2, 1))

    # Oscillate pz between ±0.8 on a lane with grass beyond |z| > 1.05:
    # both LEFT wheels dip into the left grass at the top of each swing and
    # both RIGHT wheels into the right grass at the bottom — two different
    # parallel boundaries sharing one T<->G signature, never a full cross.
    px = pz = 0.0
    pid = 0
    segments = [(angle, 17)] + [(-angle, 34), (angle, 34)] * 3
    for heading, ticks in segments:
        fx, fz = math.cos(heading), math.sin(heading)
        for _ in range(ticks):
            pid += 1
            px += fx * speed / 60
            pz += fz * speed / 60
            surface = ""
            for a, b in wheel_geometry:
                cz = pz + a * fz + b * (tw_true / 2) * -fx
                surface += "G" if abs(cz) > 1.05 else "T"
            survey.feed(make_packet(
                fmt="C", surface_types=surface, packet_id=pid,
                position=(px, 0.0, pz), velocity=(speed * fx, 0.0, speed * fz),
                speed_mps=speed, wheelbase_m=wb,
            ))
    status = survey.status()
    assert status["transitions"] > 0  # the weave produced boundary evidence...
    assert status["width_samples"] == 0  # ...but no width measurement
    assert status["width_in_use_m"] == 1.7  # the assumption stays in force
    survey.stop()


def test_survey_track_set_mid_run_reaches_the_jsonl(tmp_path) -> None:
    """A label identified after start must land in the log — the offline
    artifact has to stay joinable to a circuit on its own."""
    import json

    from app.processing.survey import SurfaceSurvey

    survey = SurfaceSurvey()
    survey.start(tmp_path, track_width_m=1.6)  # circuit not yet identified
    assert survey.track_locked is False
    survey.set_track("Deep Forest Raceway")
    survey.stop()
    assert survey.log_path is not None
    lines = [json.loads(line) for line in survey.log_path.read_text().splitlines()]
    assert {"track": "Deep Forest Raceway"} in lines

    # A user-typed label is locked; service wiring must not overwrite it.
    survey2 = SurfaceSurvey()
    survey2.start(tmp_path, track_width_m=1.6, track="My Circuit", track_user_set=True)
    assert survey2.track_locked is True
    survey2.stop()


def test_survey_manual_marks_logged_even_past_memory_cap(tmp_path, monkeypatch) -> None:
    import json

    from app.processing import survey as survey_mod

    monkeypatch.setattr(survey_mod, "EDGES_MAX_POINTS", 1)
    survey = survey_mod.SurfaceSurvey()
    survey.start(tmp_path, track_width_m=1.6)
    survey.set_mark("L", "wall")
    for i in range(13):  # 6 m of travel: several mark points past the cap
        survey.feed(make_packet(
            fmt="C", surface_types="TTTT", packet_id=i, position=(i * 0.5, 0.0, 0.0),
            velocity=(30.0, 0.0, 0.0), speed_mps=30.0, wheelbase_m=2.6,
        ))
    assert len(survey.edges) == 1  # memory capped...
    survey.stop()
    assert survey.log_path is not None
    marks = [line for line in survey.log_path.read_text().splitlines()
             if set(json.loads(line)) == {"mark"}]
    assert len(marks) >= 3  # ...but the JSONL kept every manual point


def test_survey_straddle_traces_border_continuously(tmp_path) -> None:
    """A lap driven with one side's wheels held off the track must trace
    that border the whole way, not only at the surface-flip moments."""
    import json

    from app.processing.survey import SurfaceSurvey

    survey = SurfaceSurvey()
    survey.start(tmp_path, track_width_m=1.6)
    common = dict(velocity=(30.0, 0.0, 0.0), speed_mps=30.0, wheelbase_m=2.6)
    # 20 m with both LEFT wheels (FL, RL) on grass, right side on tarmac.
    for i in range(41):
        survey.feed(make_packet(
            fmt="C", surface_types="GTGT", packet_id=i,
            position=(i * 0.5, 0.0, 0.0), **common,
        ))
    straddles = [e for e in survey.edges if e["kind"] == "straddle"]
    # One point per >= 2 m of travel, on the LEFT wheel line: with f=(1,0)
    # the right vector is (0,-1), so the left side sits at z = +0.8.
    assert 9 <= len(straddles) <= 11
    assert all(e["side"] == "L" and e["z"] == pytest.approx(0.8) for e in straddles)

    # Fully off (all four on grass) is an excursion, not a border underneath.
    for i in range(41, 61):
        survey.feed(make_packet(
            fmt="C", surface_types="GGGG", packet_id=i,
            position=(i * 0.5, 0.0, 0.0), **common,
        ))
    assert len([e for e in survey.edges if e["kind"] == "straddle"]) == len(straddles)

    # Mirrored: right wheels off -> right border at z = -0.8.
    for i in range(61, 102):
        survey.feed(make_packet(
            fmt="C", surface_types="TGTG", packet_id=i,
            position=(i * 0.5, 0.0, 0.0), **common,
        ))
    rights = [e for e in survey.edges if e["kind"] == "straddle" and e["side"] == "R"]
    assert 9 <= len(rights) <= 11
    assert all(e["z"] == pytest.approx(-0.8) for e in rights)

    survey.stop()
    # Straddle points are not reconstructable from transitions — they must
    # be in the JSONL like manual marks. (Mark LINES are the single-key
    # {"mark": {...}} wrappers; transition records also have a "mark" field.)
    assert survey.log_path is not None
    logged = [json.loads(line)["mark"]
              for line in survey.log_path.read_text().splitlines()
              if set(json.loads(line)) == {"mark"}]
    assert len(logged) == len(straddles) + len(rights)
    assert all(m["kind"] == "straddle" for m in logged)


def test_survey_accumulates_edge_points(tmp_path) -> None:
    """Border-tagged transitions grow the run-long edge list, lap after lap."""
    from app.processing.survey import SurfaceSurvey

    survey = SurfaceSurvey()
    survey.start(tmp_path, track_width_m=1.6)
    common = dict(fmt="C", velocity=(20.0, 0.0, 0.0), speed_mps=20.0, wheelbase_m=2.6)
    survey.feed(make_packet(surface_types="TTTT", packet_id=1,
                            position=(0.0, 0.0, 0.0), **common))
    survey.feed(make_packet(surface_types="CTTT", packet_id=2,  # FL on, L
                            position=(5.0, 0.0, 0.0), **common))
    survey.feed(make_packet(surface_types="TTTT", packet_id=3,  # FL off, L
                            position=(10.0, 0.0, 0.0), **common))
    status = survey.status()
    assert status["edge_points"] == 2
    # A repeat contact on the same meter of boundary is not new evidence.
    survey.feed(make_packet(surface_types="CTTT", packet_id=4,
                            position=(10.0, 0.0, 0.0), **common))
    assert survey.status()["edge_points"] == 2
    assert all(e["side"] == "L" and e["kind"] == "auto" for e in survey.edges)
    # A deep excursion (both sides off) must add no bogus perimeter evidence.
    survey.feed(make_packet(surface_types="GGGG", packet_id=4, **common))
    assert survey.status()["edge_points"] == 2
    survey.stop()


def test_survey_manual_marking_traces_the_driven_line(tmp_path) -> None:
    """Armed marking samples the boundary from the wheel line — the only way
    to map walls and paved run-off, which surface chars cannot see."""
    import json

    from app.processing.survey import SurfaceSurvey

    survey = SurfaceSurvey()
    survey.start(tmp_path, track_width_m=1.6)
    survey.set_mark("R", "wall")
    for i in range(31):  # 0.5 m per tick, 15 m of travel along +x
        survey.feed(make_packet(
            fmt="C", surface_types="TTTT", packet_id=i,
            position=(i * 0.5, 0.0, 0.0), velocity=(30.0, 0.0, 0.0),
            speed_mps=30.0, wheelbase_m=2.6,
        ))
    status = survey.status()
    assert status["mark_side"] == "R"
    assert status["mark_kind"] == "wall"
    # One point per >= 2 m of travel, all on the right wheel line: with
    # f=(1,0) the right vector is (0,-1), so right side sits at z = -0.8.
    walls = [e for e in survey.edges if e["kind"] == "wall"]
    assert 7 <= len(walls) <= 9
    assert all(e["side"] == "R" and e["z"] == pytest.approx(-0.8) for e in walls)
    survey.set_mark(None, "edge")
    survey.feed(make_packet(
        fmt="C", surface_types="TTTT", packet_id=40, position=(30.0, 0.0, 0.0),
        velocity=(30.0, 0.0, 0.0), speed_mps=30.0, wheelbase_m=2.6,
    ))
    assert survey.status()["edge_points"] == len(walls)  # disarmed: no growth
    survey.stop()
    # Manual points exist nowhere else, so they must be in the JSONL too.
    assert survey.log_path is not None
    marks = [json.loads(line)["mark"]
             for line in survey.log_path.read_text().splitlines()
             if set(json.loads(line)) == {"mark"}]
    assert len(marks) == len(walls)
    assert marks[0]["kind"] == "wall"


def test_survey_locates_finish_line_from_lap_rollovers(tmp_path) -> None:
    import json

    from app.processing.survey import SurfaceSurvey

    survey = SurfaceSurvey()
    survey.start(tmp_path, track_width_m=1.6)
    common = dict(fmt="C", surface_types="TTTT", velocity=(40.0, 0.0, 0.0),
                  speed_mps=40.0)

    def cruise(pid: int, lap: int, x: float) -> None:
        survey.feed(make_packet(packet_id=pid, current_lap=lap, position=(x, 0.0, 5.0),
                                **common))

    cruise(1, 1, 100.0)
    assert survey.status()["finish"] is None  # no rollover seen yet
    cruise(2, 2, 200.0)  # lap 1 -> 2: first crossing, provisional
    finish = survey.status()["finish"]
    assert finish is not None
    assert finish["crossings"] == 1 and finish["confident"] is False
    assert finish["x"] == pytest.approx(200.0)
    cruise(3, 2, 300.0)
    cruise(4, 3, 204.0)  # lap 2 -> 3 four meters from the first crossing
    finish = survey.status()["finish"]
    assert finish["crossings"] == 2
    assert finish["confident"] is True
    assert finish["x"] == pytest.approx(202.0)  # mean of the two
    assert finish["hx"] == pytest.approx(1.0)

    # Counter falling back (restart) and 0 -> 1 (grid start) must not count.
    cruise(5, 1, 400.0)
    cruise(6, 1, 401.0)
    survey.feed(make_packet(packet_id=7, current_lap=0, position=(402.0, 0.0, 5.0), **common))
    cruise(8, 1, 403.0)
    assert survey.status()["finish"]["crossings"] == 2

    survey.stop()
    assert survey.log_path is not None
    logged = [json.loads(line)["finish"]
              for line in survey.log_path.read_text().splitlines()
              if set(json.loads(line)) == {"finish"}]
    assert len(logged) == 2


def _edge(x=10.0, z=5.0, side="L", kind="auto", run=1):
    from app.processing.track_bundle import new_edge

    return new_edge(x=x, z=z, hx=1.0, hz=0.0, side=side, kind=kind, run=run, tw=1.6)


def test_track_bundle_merge_dedups_on_grid() -> None:
    from app.processing.track_bundle import edge_key, merge_edges

    a = _edge()
    near_a = _edge(x=10.3)  # same 1 m cell -> same fact, not a second point
    far = _edge(x=14.0)
    other_side = _edge(side="R")
    merged = merge_edges([a], [near_a, far, other_side])
    assert len(merged) == 3
    keys = {edge_key(e) for e in merged}
    assert keys == {(10, 5, "L"), (14, 5, "L"), (10, 5, "R")}


def test_track_bundle_votes_resolve_one_kind_per_metre() -> None:
    """A metre of border is one fact; kinds seen there are votes on it.

    v1 keyed on kind too, so a hand-marked run-off limit was stored NEXT TO
    the auto/straddle point it contradicted and the consumer kept both — the
    metre stayed in the road fill despite the mark (112 such cells in the
    author's Lago Centre bundle).
    """
    from app.processing.track_bundle import merge_edges

    auto = _edge(kind="auto", run=1)
    merged = merge_edges([auto], [_edge(kind="runoff", run=2)])
    assert len(merged) == 1
    # Manual marks beat automatic inference outright: the surface chars are
    # blind to run-off, so an auto point there is not evidence against it.
    assert merged[0]["kind"] == "runoff"
    assert merged[0]["votes"] == {"auto": [1, 1], "runoff": [1, 2]}
    # ...and majority within the manual tier is the way back from a mis-mark.
    merged = merge_edges(merged, [_edge(kind="edge", run=3)])
    merged = merge_edges(merged, [_edge(kind="edge", run=4)])
    assert merged[0]["kind"] == "edge"


def test_track_bundle_votes_count_runs_not_saves(tmp_path) -> None:
    """The ~60 s autosave re-merges the same run; votes must not inflate."""
    from app.processing.track_bundle import load, save

    for _ in range(5):
        save(tmp_path, "Ring", [_edge(kind="auto", run=1)], [], count_run=False)
    doc = load(tmp_path, "Ring")
    assert doc is not None
    assert doc["edges"][0]["votes"] == {"auto": [1, 1]}  # not [5, 1]
    save(tmp_path, "Ring", [_edge(kind="auto", run=2)], [], count_run=True)
    doc = load(tmp_path, "Ring")
    assert doc is not None
    assert doc["edges"][0]["votes"] == {"auto": [2, 2]}  # a real second run


def test_track_bundle_upgrades_v1_in_place(tmp_path) -> None:
    """Existing v1 bundles keep their evidence and gain a resolved kind."""
    import json

    from app.processing.track_bundle import BUNDLE_FORMAT, bundle_path, load

    v1 = {
        "format": BUNDLE_FORMAT, "version": 1,
        "meta": {"track": "Ring", "runs": 3, "updated_at": "2026-08-10T00:00:00+00:00"},
        # The defeated-run-off case, exactly as v1 stored it.
        "edges": [
            {"x": 1.0, "z": 0.0, "hx": 1.0, "hz": 0.0, "side": "L", "kind": "straddle"},
            {"x": 1.1, "z": 0.0, "hx": 1.0, "hz": 0.0, "side": "L", "kind": "runoff"},
            {"x": 9.0, "z": 0.0, "hx": 1.0, "hz": 0.0, "side": "R", "kind": "auto"},
        ],
        "finish_crossings": [],
    }
    path = bundle_path(tmp_path, "Ring")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(v1), encoding="utf-8")

    doc = load(tmp_path, "Ring")
    assert doc is not None
    assert doc["version"] == 2
    assert len(doc["edges"]) == 2  # the co-located pair collapsed to one cell
    contested = next(e for e in doc["edges"] if e["side"] == "L")
    assert contested["kind"] == "runoff"  # the mark wins, at last
    assert contested["votes"] == {"straddle": [1, 0], "runoff": [1, 0]}
    assert contested["run"] == 0 and contested["tw"] is None  # unknown, honestly


def test_track_bundle_refuses_a_newer_format(tmp_path) -> None:
    """Better to ignore a future bundle than to save a lossy read over it."""
    import json

    from app.processing.track_bundle import BUNDLE_FORMAT, bundle_path, load

    path = bundle_path(tmp_path, "Ring")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "format": BUNDLE_FORMAT, "version": 99,
        "meta": {"track": "Ring", "runs": 1}, "edges": [], "finish_crossings": [],
    }), encoding="utf-8")
    assert load(tmp_path, "Ring") is None


def test_survey_marking_overrides_mapped_ground(tmp_path) -> None:
    """Driving a wall the straddle tracer already called road must re-label it."""
    from app.processing.survey import SurfaceSurvey

    common = dict(fmt="C", velocity=(30.0, 0.0, 0.0), speed_mps=30.0, wheelbase_m=2.6)
    survey = SurfaceSurvey()
    survey.start(tmp_path, track_width_m=1.6, track="Ring", track_user_set=True)
    # One side held off-tarmac traces that border as "straddle".
    for i in range(41):
        survey.feed(make_packet(surface_types="GTGT", packet_id=i, current_lap=1,
                                position=(i * 0.5, 0.0, 0.0), **common))
    from app.processing.track_bundle import edge_key

    straddled = {edge_key(e) for e in survey.edges if e["kind"] == "straddle"}
    assert straddled

    # Same ground, now hand-marked as a wall on that side.
    survey.set_mark("L", "wall")
    for i in range(41):
        survey.feed(make_packet(surface_types="TTTT", packet_id=100 + i, current_lap=1,
                                position=(i * 0.5, 0.0, 0.0), **common))
    by_key = {edge_key(e): e for e in survey.edges}
    assert len(by_key) == len(survey.edges)  # votes on known metres, no duplicates
    for key in straddled:
        cell = by_key[key]
        assert cell["kind"] == "wall", "the mark must win over the straddle trace"
        assert "straddle" in cell["votes"]  # the outvoted evidence is kept
    survey.stop()


def test_survey_bundle_persists_track_knowledge_across_runs(tmp_path) -> None:
    """Restarting the app or the session must not restart the map."""
    from app.processing.survey import SurfaceSurvey
    from app.processing.track_bundle import bundle_path, load

    common = dict(fmt="C", velocity=(30.0, 0.0, 0.0), speed_mps=30.0, wheelbase_m=2.6)

    # Run 1: straddle-trace 20 m of left border, cross the line twice.
    one = SurfaceSurvey()
    one.start(tmp_path, track_width_m=1.6, track="Test Ring", track_user_set=True)
    assert one.bundle_info is None  # fresh circuit
    for i in range(41):
        one.feed(make_packet(surface_types="GTGT", packet_id=i, current_lap=1,
                             position=(i * 0.5, 0.0, 0.0), **common))
    one.feed(make_packet(surface_types="TTTT", packet_id=50, current_lap=2,
                         position=(30.0, 0.0, 0.0), **common))
    one.feed(make_packet(surface_types="TTTT", packet_id=51, current_lap=3,
                         position=(31.0, 0.0, 0.0), **common))
    run1_edges = len(one.edges)
    one.stop()
    assert bundle_path(tmp_path, "Test Ring").exists()

    # Run 2 (fresh process): the map opens with run 1's knowledge...
    two = SurfaceSurvey()
    two.start(tmp_path, track_width_m=1.6, track="Test Ring", track_user_set=True)
    assert two.bundle_info is not None
    assert two.bundle_info["runs"] == 1
    assert len(two.edges) == run1_edges
    assert two.status()["finish"] is not None  # crossings carried over too
    # ...re-driving the same stretch does not grow it (grid dedup)...
    for i in range(41):
        two.feed(make_packet(surface_types="GTGT", packet_id=i, current_lap=1,
                             position=(i * 0.5, 0.05, 0.0), **common))
    assert len(two.edges) == run1_edges
    # ...but new ground does.
    for i in range(50, 91):
        two.feed(make_packet(surface_types="TGTG", packet_id=i, current_lap=1,
                             position=(i * 0.5, 0.0, 0.0), **common))
    assert len(two.edges) > run1_edges
    two.stop()
    doc = load(tmp_path, "Test Ring")
    assert doc is not None
    assert doc["meta"]["runs"] == 2
    assert len(doc["edges"]) == len(two.edges)


def test_survey_autosaves_bundle_periodically(tmp_path, monkeypatch) -> None:
    """A reload or hard kill mid-run must never lose more than ~a minute —
    graceful shutdowns can't be relied on (dev reloads killed real runs)."""
    from app.processing import survey as survey_mod
    from app.processing.track_bundle import load

    monkeypatch.setattr(survey_mod, "AUTOSAVE_PACKETS", 10)
    survey = survey_mod.SurfaceSurvey()
    survey.start(tmp_path, track_width_m=1.6, track="Auto Ring", track_user_set=True)
    common = dict(fmt="C", velocity=(30.0, 0.0, 0.0), speed_mps=30.0, wheelbase_m=2.6)
    for i in range(15):  # crosses the autosave threshold with edges present
        survey.feed(make_packet(surface_types="GTGT", packet_id=i,
                                position=(i * 0.5, 0.0, 0.0), **common))
    # NO stop() — simulating the process being killed.
    doc = load(tmp_path, "Auto Ring")
    assert doc is not None
    assert len(doc["edges"]) > 0
    assert doc["meta"]["runs"] == 0  # autosaves don't count as finished runs
    survey.stop()
    doc = load(tmp_path, "Auto Ring")
    assert doc is not None and doc["meta"]["runs"] == 1


def test_survey_circuit_change_never_pollutes_another_bundle(tmp_path) -> None:
    from app.processing.survey import SurfaceSurvey
    from app.processing.track_bundle import load

    common = dict(fmt="C", velocity=(30.0, 0.0, 0.0), speed_mps=30.0, wheelbase_m=2.6)
    survey = SurfaceSurvey()
    survey.start(tmp_path, track_width_m=1.6)  # unidentified at start
    survey.set_track("Circuit A")
    for i in range(21):
        survey.feed(make_packet(surface_types="GTGT", packet_id=i,
                                position=(i * 0.5, 0.0, 0.0), **common))
    a_points = len(survey.edges)
    assert a_points > 0
    # Session restart -> label back to unknown -> a DIFFERENT circuit loads.
    survey.set_track("")
    assert survey.edges == []  # A's evidence flushed to A's bundle, not kept
    survey.set_track("Circuit B")
    for i in range(30, 51):
        survey.feed(make_packet(surface_types="TGTG", packet_id=i,
                                position=(1000 + i * 0.5, 0.0, 0.0), **common))
    survey.stop()
    doc_a = load(tmp_path, "Circuit A")
    doc_b = load(tmp_path, "Circuit B")
    assert doc_a is not None and len(doc_a["edges"]) == a_points
    assert doc_b is not None
    assert all(e["x"] >= 1000 for e in doc_b["edges"])  # nothing of A leaked


def test_survey_transition_survives_speed_velocity_mismatch(tmp_path) -> None:
    """speed_mps and the velocity vector are separate packet fields; a
    packet reporting speed with a zero ground-plane velocity must degrade
    to contacts=None, not divide by zero in the 60 Hz feed path."""
    from app.processing.survey import SurfaceSurvey

    survey = SurfaceSurvey()
    survey.start(tmp_path, track_width_m=1.6)
    common = dict(fmt="C", speed_mps=5.0, velocity=(0.0, 0.0, 0.0), wheelbase_m=2.6)
    survey.feed(make_packet(surface_types="TTTT", packet_id=1, **common))
    record = survey.feed(make_packet(surface_types="CTTT", packet_id=2, **common))
    assert record is not None
    assert record["contacts"] is None
    assert record["heading_rad"] is None
    assert record["border"] == "L"  # the transition itself is still recorded
    survey.stop()


def test_survey_watches_undocumented_flag_bits(tmp_path) -> None:
    """No track-limits field is known; upper flag bits activating is a finding."""
    from app.processing.survey import SurfaceSurvey

    survey = SurfaceSurvey()
    survey.start(tmp_path, track_width_m=1.6)
    survey.feed(make_packet(fmt="C", surface_types="TTTT", packet_id=1))
    assert survey.status()["unknown_flag_bits"] == {}
    flags = int(SimulatorFlags.CAR_ON_TRACK) | (1 << 13)
    for pid in (2, 3):
        survey.feed(make_packet(fmt="C", surface_types="TTTT", packet_id=pid, flags=flags))
    assert survey.status()["unknown_flag_bits"] == {"13": 2}
    survey.stop()
