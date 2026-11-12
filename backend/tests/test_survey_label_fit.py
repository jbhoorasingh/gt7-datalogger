"""A survey's label is a claim about where the car is. These hold it to it:
a whole lap that is not a lap of the labelled circuit takes the label away,
and takes the run's evidence back out of that circuit's bundle."""

import json
import math
from pathlib import Path

from app.models import SimulatorFlags
from app.processing import survey as survey_module
from app.processing import survey_log, track_bundle
from app.processing.survey import FIT_MIN_CELLS, SurfaceSurvey
from app.telemetry.packet import build_packet, parse_packet

ON_TRACK = int(SimulatorFlags.CAR_ON_TRACK)
RADIUS_M = 150.0
STEP_M = 2.0
ELSEWHERE = (3000.0, 0.0)


def drive(
    survey: SurfaceSurvey,
    centre: tuple[float, float],
    laps: int,
    *,
    first_lap: int = 1,
    pid: int = 0,
    radius: float = RADIUS_M,
    mark_until: float = 1.0,
) -> int:
    """Drive `laps` whole circles round `centre`, marking the right-hand
    border for the first `mark_until` of each, then cross the line once more
    so the last lap is complete. Returns the next free packet id."""
    ticks = int(2 * math.pi * radius / STEP_M)
    for lap in range(laps + 1):
        for i in range(ticks if lap < laps else 1):
            angle = 2 * math.pi * i / ticks
            survey.set_mark("R" if lap < laps and i < ticks * mark_until else None, "edge")
            pid += 1
            survey.feed(parse_packet(build_packet(
                fmt="C", flags=ON_TRACK, surface_types="TTTT", packet_id=pid,
                position=(centre[0] + radius * math.cos(angle), 0.0,
                          centre[1] + radius * math.sin(angle)),
                velocity=(-30.0 * math.sin(angle), 0.0, 30.0 * math.cos(angle)),
                speed_mps=30.0, wheelbase_m=2.6, current_lap=first_lap + lap,
            )))
    return pid


def surveyed(data_dir: Path, track: str, centre: tuple[float, float], **kwargs) -> None:
    survey = SurfaceSurvey()
    survey.start(data_dir, track_width_m=1.6, track=track, track_user_set=True)
    drive(survey, centre, laps=2, **kwargs)
    survey.stop()


def near(edges: list[dict], centre: tuple[float, float]) -> list[dict]:
    return [
        e for e in edges
        if math.hypot(e["x"] - centre[0], e["z"] - centre[1]) < RADIUS_M + 50
    ]


def test_a_lap_driven_elsewhere_takes_the_label_away(tmp_path) -> None:
    surveyed(tmp_path, "Home", (0.0, 0.0))
    before = track_bundle.load(tmp_path, "Home")
    assert before is not None

    survey = SurfaceSurvey()
    survey.start(tmp_path, track_width_m=1.6, track="Home", track_user_set=True)
    assert survey.track_locked
    drive(survey, ELSEWHERE, laps=1)

    assert survey.track == ""
    assert not survey.track_locked  # the lock named a circuit the car has left
    dropped = survey.status()["label_dropped"]
    assert dropped["track"] == "Home" and dropped["share"] < 0.1
    # The run keeps what it laid, as a run of its own and of nowhere yet.
    assert survey.edges and len(near(survey.edges, ELSEWHERE)) == len(survey.edges)
    assert {e["run"] for e in survey.edges} == {1}

    # And the bundle is exactly what it was before the run touched it.
    after = track_bundle.load(tmp_path, "Home")
    assert after is not None
    assert not near(after["edges"], ELSEWHERE)
    assert len(after["edges"]) == len(before["edges"])
    assert after["finish_crossings"] == before["finish_crossings"]
    assert after["meta"]["source_runs"] == before["meta"]["source_runs"]
    survey.stop()
    assert track_bundle.load(tmp_path, "Home")["meta"]["runs"] == before["meta"]["runs"]


def test_evidence_autosaved_under_the_wrong_label_is_taken_back(tmp_path) -> None:
    surveyed(tmp_path, "Home", (0.0, 0.0))
    survey = SurfaceSurvey()
    survey.start(tmp_path, track_width_m=1.6, track="Home", track_user_set=True)
    saved: list[str] = []
    survey.on_bundle_saved = saved.append
    ticks = int(2 * math.pi * RADIUS_M / STEP_M)
    # Half a lap elsewhere, then the autosave fires: the bundle now holds it.
    pid = 0
    for i in range(ticks // 2):
        angle = 2 * math.pi * i / ticks
        survey.set_mark("R", "edge")
        pid += 1
        survey.feed(parse_packet(build_packet(
            fmt="C", flags=ON_TRACK, surface_types="TTTT", packet_id=pid,
            position=(ELSEWHERE[0] + RADIUS_M * math.cos(angle), 0.0,
                      ELSEWHERE[1] + RADIUS_M * math.sin(angle)),
            velocity=(-30.0 * math.sin(angle), 0.0, 30.0 * math.cos(angle)),
            speed_mps=30.0, wheelbase_m=2.6, current_lap=1,
        )))
    survey._save_bundle(count_run=False)
    assert near(track_bundle.load(tmp_path, "Home")["edges"], ELSEWHERE)
    for i in range(ticks // 2, ticks + 1):
        angle = 2 * math.pi * i / ticks
        pid += 1
        survey.feed(parse_packet(build_packet(
            fmt="C", flags=ON_TRACK, surface_types="TTTT", packet_id=pid,
            position=(ELSEWHERE[0] + RADIUS_M * math.cos(angle), 0.0,
                      ELSEWHERE[1] + RADIUS_M * math.sin(angle)),
            velocity=(-30.0 * math.sin(angle), 0.0, 30.0 * math.cos(angle)),
            speed_mps=30.0, wheelbase_m=2.6, current_lap=1 if i < ticks else 2,
        )))
    assert survey.track == ""
    assert not near(track_bundle.load(tmp_path, "Home")["edges"], ELSEWHERE)
    assert saved[-1] == "Home"  # the sync client hears the corrected bundle
    survey.stop()


def test_the_run_lands_where_it_is_named_afterwards(tmp_path) -> None:
    surveyed(tmp_path, "Home", (0.0, 0.0))
    survey = SurfaceSurvey()
    survey.start(tmp_path, track_width_m=1.6, track="Home", track_user_set=True)
    pid = drive(survey, ELSEWHERE, laps=1)
    laid = len(survey.edges)
    survey.set_track("Away", lock=True)
    assert survey.status()["label_dropped"] is None
    drive(survey, ELSEWHERE, laps=1, first_lap=3, pid=pid)
    assert survey.track == "Away"
    survey.stop()
    away = track_bundle.load(tmp_path, "Away")
    assert away is not None and len(away["edges"]) >= laid
    assert not near(track_bundle.load(tmp_path, "Home")["edges"], ELSEWHERE)


def test_extending_a_half_surveyed_circuit_keeps_its_label(tmp_path) -> None:
    """The lap is mostly on road nobody has mapped yet — which is what
    surveying is. It still passed everything that WAS mapped."""
    surveyed(tmp_path, "Home", (0.0, 0.0), mark_until=0.5, radius=400.0)
    survey = SurfaceSurvey()
    survey.start(tmp_path, track_width_m=1.6, track="Home", track_user_set=True)
    # Enough mapped road for the lap to be judged at all — or this would pass
    # by never being asked.
    assert len(survey._prior_cells) >= FIT_MIN_CELLS
    drive(survey, (0.0, 0.0), laps=2, radius=400.0)
    assert survey.track == "Home" and survey.track_locked
    assert survey.status()["label_dropped"] is None
    survey.stop()


def test_a_shorter_layout_of_the_same_venue_is_not_the_circuit(tmp_path) -> None:
    surveyed(tmp_path, "Home", (0.0, 0.0))
    survey = SurfaceSurvey()
    survey.start(tmp_path, track_width_m=1.6, track="Home", track_user_set=True)
    # A loop touching the big one on one side: it shares that stretch only.
    drive(survey, (RADIUS_M - 80.0, 0.0), laps=1, radius=80.0)
    assert survey.track == ""
    survey.stop()


def test_a_fragment_is_not_judged(tmp_path) -> None:
    """An out-lap from the grid, a restart: not a lap, so not evidence."""
    surveyed(tmp_path, "Home", (0.0, 0.0))
    survey = SurfaceSurvey()
    survey.start(tmp_path, track_width_m=1.6, track="Home", track_user_set=True)
    ticks = int(2 * math.pi * RADIUS_M / STEP_M)
    for i in range(ticks // 2 + 1):  # half a circle elsewhere, then the counter steps
        angle = 2 * math.pi * i / ticks
        survey.feed(parse_packet(build_packet(
            fmt="C", flags=ON_TRACK, surface_types="TTTT", packet_id=i + 1,
            position=(ELSEWHERE[0] + RADIUS_M * math.cos(angle), 0.0,
                      ELSEWHERE[1] + RADIUS_M * math.sin(angle)),
            velocity=(-30.0 * math.sin(angle), 0.0, 30.0 * math.cos(angle)),
            speed_mps=30.0, wheelbase_m=2.6, current_lap=1 if i < ticks // 2 else 2,
        )))
    assert survey.track == "Home"
    survey.stop()


def test_a_fresh_circuit_has_nothing_to_hold_a_lap_against(tmp_path) -> None:
    survey = SurfaceSurvey()
    survey.start(tmp_path, track_width_m=1.6, track="New", track_user_set=True)
    drive(survey, ELSEWHERE, laps=2)
    assert survey.track == "New"
    survey.stop()


def test_the_log_says_the_run_lost_its_label(tmp_path) -> None:
    surveyed(tmp_path, "Home", (0.0, 0.0))
    survey = SurfaceSurvey()
    survey.start(tmp_path, track_width_m=1.6, track="Home", track_user_set=True)
    drive(survey, ELSEWHERE, laps=1)
    path = survey.log_path
    survey.stop()
    assert path is not None
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert any(line.get("unlabeled", {}).get("track") == "Home" for line in lines)
    # A replay must not file the run under the label it lost.
    assert survey_log.summarize(path)["track"] == ""
    assert survey_log.edges_from_log(path, 1, "abcdef")[2] == ""


def test_replacing_a_run_can_take_its_finish_crossings_too(tmp_path) -> None:
    surveyed(tmp_path, "Home", (0.0, 0.0))
    doc = track_bundle.load(tmp_path, "Home")
    assert doc is not None and doc["finish_crossings"]
    gone = doc["finish_crossings"][:1]
    track_bundle.save_replacing_run(
        tmp_path, "Home", [], [], "nobody", 1, drop_finish=gone,
    )
    after = track_bundle.load(tmp_path, "Home")
    assert after["finish_crossings"] == doc["finish_crossings"][1:]
    assert len(after["edges"]) == len(doc["edges"])


def test_laps_that_fitted_before_the_change_of_circuit_stay(tmp_path) -> None:
    """One run, two circuits: a lap at Home, then the drive to Away without
    the survey being stopped. Only what came after the last fitting lap
    leaves; the Home lap, and the crossing that closed it, were Home's."""
    surveyed(tmp_path, "Home", (0.0, 0.0), mark_until=0.5, radius=400.0)
    before = track_bundle.load(tmp_path, "Home")
    survey = SurfaceSurvey()
    survey.start(tmp_path, track_width_m=1.6, track="Home", track_user_set=True)
    assert len(survey._prior_cells) >= FIT_MIN_CELLS
    pid = drive(survey, (0.0, 0.0), laps=1, radius=400.0)  # maps the other half too
    home_now = len(survey.edges)
    assert home_now > len(before["edges"])
    pid = drive(survey, ELSEWHERE, laps=1, pid=pid)  # the counter falls back to 1

    assert survey.track == ""
    assert survey.edges and len(near(survey.edges, ELSEWHERE)) == len(survey.edges)
    home = track_bundle.load(tmp_path, "Home")
    assert not near(home["edges"], ELSEWHERE)
    assert len(home["edges"]) == home_now  # the fitting lap's borders stayed
    assert home["finish_crossings"] == before["finish_crossings"]
    assert all(
        math.hypot(c["x"] - ELSEWHERE[0], c["z"] - ELSEWHERE[1]) < RADIUS_M + 50
        for c in survey.finish_crossings
    )

    survey.set_track("Away", lock=True)
    survey.stop()
    away = track_bundle.load(tmp_path, "Away")
    assert away["edges"] and len(near(away["edges"], ELSEWHERE)) == len(away["edges"])

    # The log replays to the same split: only the carried part is unfiled.
    edges, finish, _ = survey_log.edges_from_log(survey.log_path, 1, "abcdef")
    assert edges and len(near(edges, ELSEWHERE)) == len(edges)
    assert all(
        math.hypot(c["x"] - ELSEWHERE[0], c["z"] - ELSEWHERE[1]) < RADIUS_M + 50
        for c in finish
    )


def test_a_crossing_the_bundle_already_held_is_not_taken_out(tmp_path) -> None:
    """Two layouts sharing a start/finish line: this run's crossing lands on
    the rounded spot of one the bundle already had, so the merge kept the
    old one — and dropping the label must not delete it."""
    surveyed(tmp_path, "Home", (0.0, 0.0))
    before = track_bundle.load(tmp_path, "Home")["finish_crossings"]
    survey = SurfaceSurvey()
    survey.start(tmp_path, track_width_m=1.6, track="Home", track_user_set=True)
    # A smaller loop through Home's finish line at (RADIUS_M, 0).
    drive(survey, (RADIUS_M - 80.0, 0.0), laps=1, radius=80.0)
    assert survey.track == ""
    assert track_bundle.load(tmp_path, "Home")["finish_crossings"] == before
    assert survey.finish_crossings  # and the run still has its own
    survey.stop()


def test_a_dropped_label_is_reported_so_the_session_can_supply_one(tmp_path) -> None:
    surveyed(tmp_path, "Home", (0.0, 0.0))
    survey = SurfaceSurvey()
    survey.start(tmp_path, track_width_m=1.6, track="Home", track_user_set=True)
    heard: list[str] = []

    def relabel(wrong: str) -> None:
        heard.append(wrong)
        survey.set_track("Away")  # what the service does when it knows

    survey.on_label_dropped = relabel
    drive(survey, ELSEWHERE, laps=1)
    assert heard == ["Home"]
    assert survey.track == "Away" and not survey.track_locked
    # The page has not been told yet, and still has "Home" in its box: a
    # name supplied without anybody typing it does not take the notice away.
    assert survey.status()["label_dropped"]["track"] == "Home"
    survey.stop()
    away = track_bundle.load(tmp_path, "Away")
    assert away is not None and away["edges"]
    assert not near(track_bundle.load(tmp_path, "Home")["edges"], ELSEWHERE)


def test_a_lap_that_never_ends_is_not_remembered_without_limit(tmp_path, monkeypatch) -> None:
    """The lap counter is the game's to move. A stream that holds it still
    while the car goes on and on must not grow the lap's memory for ever —
    and a lap that hit the ceiling is not one to judge a label by."""
    monkeypatch.setattr(survey_module, "FIT_MAX_LAP_CELLS", 30)
    surveyed(tmp_path, "Home", (0.0, 0.0))
    survey = SurfaceSurvey()
    survey.start(tmp_path, track_width_m=1.6, track="Home", track_user_set=True)
    drive(survey, ELSEWHERE, laps=1)  # a whole lap elsewhere, over far more than 30 cells
    assert len(survey._fit_cells) <= 30
    assert survey.track == "Home"  # not judged, so not dropped
    assert survey.status()["label_dropped"] is None
    survey.stop()


def test_a_replay_splits_at_the_log_and_not_at_a_packet_id(tmp_path) -> None:
    """GT7's packet counter starts again whenever the telemetry source does.
    Home's laps numbered in the thousands, then Away's from one: a replay
    that cut at "packets after the last fitting lap" would throw Away away."""
    surveyed(tmp_path, "Home", (0.0, 0.0), mark_until=0.5, radius=400.0)
    survey = SurfaceSurvey()
    survey.start(tmp_path, track_width_m=1.6, track="Home", track_user_set=True)
    drive(survey, (0.0, 0.0), laps=1, radius=400.0, pid=50_000)
    drive(survey, ELSEWHERE, laps=1, pid=0)  # the counter has started again
    assert survey.track == ""
    carried = len(survey.edges)
    assert carried > 0
    path = survey.log_path
    survey.stop()
    assert path is not None

    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert sum(1 for line in lines if "confirmed" in line) == 1
    survey_log.validate_log(path)
    edges, finish, track = survey_log.edges_from_log(path, 1, "abcdef")
    assert track == ""
    assert len(edges) == carried
    assert len(near(edges, ELSEWHERE)) == len(edges)
    assert finish and all(
        math.hypot(c["x"] - ELSEWHERE[0], c["z"] - ELSEWHERE[1]) < RADIUS_M + 50 for c in finish
    )
    # A fitting lap is a boundary in the log, not a record of border.
    assert survey_log.summarize(path)["transitions"] == 0


def test_a_replay_reads_nothing_inside_the_lines_that_mark_the_split(tmp_path) -> None:
    """What `validate_log` lets through, `edges_from_log` must cross without
    raising: the split is where the lines ARE, whatever they hold."""
    path = tmp_path / "surface_survey_20261112_000000.jsonl"
    header = {"meta": {"started_at": "2026-11-12T00:00:00+00:00", "track_width_m": 1.6,
                       "track": "Home", "session_id": None, "source": "abcdef", "wheels": []}}
    mark = {"mark": {"x": 1.0, "z": 2.0, "hx": 1.0, "hz": 0.0, "side": "L", "kind": "edge",
                     "run": 1, "pid": 7, "tw": 1.6, "y": None}}
    lines = [header, mark, {"confirmed": {"pid": "bad"}}, mark,
             {"unlabeled": {"since_pid": "bad", "finish_kept": "many", "pid": None}}, mark]
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    survey_log.validate_log(path)
    edges, _finish, track = survey_log.edges_from_log(path, 1, "abcdef")
    assert track == ""
    assert len(edges) == 2  # the one after the fitting lap, and the one after the drop
