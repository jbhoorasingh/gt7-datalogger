"""Track management: import/merge across installations (#47), authored corners
(#48) and the joined overview that makes the gaps visible (#46)."""

import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.processing import track_bundle, track_catalog
from app.processing.cars import CarDatabase
from app.service import TelemetryService
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository

FOREIGN = "beef0000cafe"


@pytest.fixture
async def client(tmp_path):
    settings = Settings(source="udp", db_path=tmp_path / "test.db", ws_rate=1000)
    engine = make_engine(settings.db_path)
    await init_db(engine)
    repo = Repository(make_session_factory(engine))
    service = TelemetryService(settings, repo, CarDatabase())
    service.processor.min_lap_ticks = 1

    app = create_app()
    app.router.lifespan_context = None  # type: ignore[assignment]
    app.state.service = service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, service, tmp_path
    await engine.dispose()


def _foreign_bundle(track="Ring", runs=4, source=FOREIGN, n=6, version=4):
    """A bundle as another installation would have exported it."""
    edges = [
        {
            "x": float(i), "z": 0.0, "y": 12.5, "hx": 1.0, "hz": 0.0,
            "side": "L", "kind": "edge", "run": 1, "tw": 1.72,
            "votes": ({"edge": {source: [runs, runs]}} if version >= 4
                      else {"edge": [runs, runs]}),
        }
        for i in range(n)
    ]
    doc = {
        "format": track_bundle.BUNDLE_FORMAT,
        "version": version,
        "meta": {"track": track, "runs": runs, "updated_at": "2026-08-01T00:00:00+00:00"},
        "edges": edges,
        "finish_crossings": [{"x": 0.0, "z": 0.0, "hx": 1.0, "hz": 0.0, "lap": 2}],
    }
    if version >= 4:
        doc["meta"]["source_runs"] = {source: runs}
        doc["meta"]["official"] = None
        doc["corners"] = []
        doc["sections"] = []
    return doc


# --- import and cross-installation merge (#47) --------------------------------


async def test_import_merges_a_stranger_bundle_and_is_idempotent(client) -> None:
    """Fidelity accumulates across people — but only if re-pulling the same
    shared bundle does not manufacture agreement it never had."""
    c, _service, tmp = client
    doc = _foreign_bundle(n=6, runs=4)

    first = (await c.post("/api/track-bundles/import", json=doc)).json()
    assert first["points"] == 6
    assert first["added_points"] == 6

    second = (await c.post("/api/track-bundles/import", json=doc)).json()
    assert second["points"] == 6  # same evidence, not twice as much
    assert second["added_points"] == 0

    stored = track_bundle.load(tmp, "Ring")
    assert stored is not None
    assert stored["edges"][0]["votes"]["edge"] == {FOREIGN: [4, 4]}
    assert stored["meta"]["source_runs"] == {FOREIGN: 4}


async def test_imported_evidence_adds_to_local_evidence(client) -> None:
    """The point of the source id: two people who each drove a metre once have
    seen it twice between them, and both observations survive the merge."""
    c, _service, tmp = client
    mine = track_bundle.source_id(tmp)
    track_bundle.save(
        tmp, "Ring",
        [track_bundle.new_edge(0.0, 0.0, 1.0, 0.0, "L", "edge", 1, mine, 1.6)],
        [], count_run=True,
    )
    await c.post("/api/track-bundles/import", json=_foreign_bundle(n=1, runs=3))

    stored = track_bundle.load(tmp, "Ring")
    assert stored is not None
    votes = stored["edges"][0]["votes"]["edge"]
    assert votes == {mine: [1, 1], FOREIGN: [3, 3]}
    assert track_bundle.vote_count(stored["edges"][0]["votes"], "edge") == 4
    # Their 3 runs and my 1 are 4 runs of evidence, and the next run I start
    # here is MY second — not my fifth.
    assert stored["meta"]["runs"] == 4
    assert track_bundle.next_run(tmp, "Ring") == 2


async def test_import_accepts_an_older_bundle_without_claiming_it(client) -> None:
    """A v2 export predates source ids. It is upgraded on the way in, and
    attributed to a synthetic id — calling it OURS would collide a stranger's
    ordinals with the ones our next survey is about to use."""
    c, _service, tmp = client
    resp = await c.post("/api/track-bundles/import", json=_foreign_bundle(version=2, n=3))
    assert resp.status_code == 200

    stored = track_bundle.load(tmp, "Ring")
    assert stored is not None
    assert stored["version"] == 4
    sources = set(stored["edges"][0]["votes"]["edge"])
    assert sources and track_bundle.source_id(tmp) not in sources
    # Re-importing the same anonymous file is still idempotent: the synthetic
    # id is derived from the document, not generated fresh.
    await c.post("/api/track-bundles/import", json=_foreign_bundle(version=2, n=3))
    again = track_bundle.load(tmp, "Ring")
    assert again is not None
    assert set(again["edges"][0]["votes"]["edge"]) == sources


@pytest.mark.parametrize(
    ("mutate", "why"),
    [
        (lambda d: d.update(format="something-else"), "wrong format"),
        (lambda d: d.update(version=99), "from the future"),
        (lambda d: d["meta"].update(track="  "), "no circuit named"),
        (lambda d: d["edges"][0].update(side="U"), "impossible side"),
        (lambda d: d["edges"][0].update(votes={"grass": {FOREIGN: [1, 1]}}), "unknown kind"),
        (lambda d: d["edges"][0].update(votes={"edge": {"not a source": [1, 1]}}), "bad source"),
        (lambda d: d["edges"][0].update(x=1e12), "position off the planet"),
        (lambda d: d["edges"][0].update(votes={}), "no evidence at all"),
        (lambda d: d.update(edges="lots"), "edges not a list"),
    ],
)
async def test_import_rejects_documents_that_must_not_be_merged(client, mutate, why) -> None:
    """An import writes into the same store the app surveys into, so a
    malformed or hostile document is rejected, never merged (#47)."""
    c, _service, tmp = client
    doc = _foreign_bundle()
    mutate(doc)
    resp = await c.post("/api/track-bundles/import", json=doc)
    assert resp.status_code == 400, why
    assert track_bundle.load(tmp, "Ring") is None  # nothing was written


async def test_import_rejects_nan_smuggled_past_json(client) -> None:
    """json.loads parses the NaN literal happily; a NaN in a border poisons
    every bounding box drawn from it afterwards."""
    c, _service, tmp = client
    doc = _foreign_bundle()
    raw = json.dumps(doc, separators=(",", ":")).replace('"x":0.0', '"x":NaN', 1)
    assert "NaN" in raw
    resp = await c.post(
        "/api/track-bundles/import", content=raw,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert track_bundle.load(tmp, "Ring") is None


async def test_rename_merges_a_near_miss_spelling(client) -> None:
    """Bundle names are user-typed and slugified, so "- East" and "- East End"
    become two bundles of one circuit. The management view is where that
    becomes fixable (#46)."""
    c, _service, tmp = client
    await c.post("/api/track-bundles/import", json=_foreign_bundle(track="Lago - East", n=4))
    await c.post(
        "/api/track-bundles/import",
        json=_foreign_bundle(track="Lago - East End", n=4, source="0000feed0000"),
    )
    assert len(track_bundle.list_bundles(tmp)) == 2

    resp = await c.patch("/api/track-bundles/lago-east", json={"track": "Lago - East End"})
    assert resp.status_code == 200
    bundles = track_bundle.list_bundles(tmp)
    assert [b["track"] for b in bundles] == ["Lago - East End"]
    assert bundles[0]["sources"] == 2  # both people's evidence, one circuit


async def test_deleting_a_bundle_needs_it_to_exist(client) -> None:
    c, _service, _tmp = client
    assert (await c.delete("/api/track-bundles/nothing-here")).status_code == 404
    await c.post("/api/track-bundles/import", json=_foreign_bundle())
    assert (await c.delete("/api/track-bundles/ring")).status_code == 200
    assert (await c.get("/api/track-bundles/ring")).status_code == 404


# --- orphaned survey runs (#45 aftermath, surfaced by #46) --------------------


async def test_an_orphaned_run_is_listed_and_can_be_assigned(client) -> None:
    """A survey with no label saves no bundle at all: ~55 minutes of driving
    once survived only as its JSONL. The log is a complete record, so the run
    can be given its circuit afterwards."""
    c, service, tmp = client
    survey = service.survey
    survey.start(tmp, track_width_m=1.6)  # no track — this is the failure
    for i in range(6):
        survey._append_edge(x=float(i * 3), z=0.0, hx=1.0, hz=0.0,
                            side="L", kind="wall", pid=i, y=4.0)
    survey.stop()
    assert not track_bundle.list_bundles(tmp)  # nothing was saved, as designed

    logs = (await c.get("/api/survey/logs")).json()
    assert len(logs) == 1
    assert logs[0]["orphaned"] is True and logs[0]["marks"] == 6

    resp = await c.post(f"/api/survey/logs/{logs[0]['name']}/assign",
                        json={"track": "Ring"})
    assert resp.status_code == 200
    assert resp.json()["points"] == 6
    doc = track_bundle.load(tmp, "Ring")
    assert doc is not None
    assert doc["meta"]["runs"] == 1  # it was a real run; it just went to its
    assert doc["edges"][0]["kind"] == "wall"  # circuit late

    # ...and it stops reporting as orphaned, because it no longer is. Leaving
    # it in the list would read as "that didn't work" and invite assigning it
    # again, merging the same run twice.
    after = (await c.get("/api/survey/logs")).json()
    assert after[0]["orphaned"] is False and after[0]["track"] == "Ring"


async def test_a_running_survey_cannot_be_assigned_from_its_log(client) -> None:
    """Its evidence is still in memory and saves on stop; merging the partial
    log now would count the run twice."""
    c, service, tmp = client
    service.survey.start(tmp, track_width_m=1.6)
    service.survey._append_edge(x=1.0, z=0.0, hx=1.0, hz=0.0, side="L",
                                kind="wall", pid=1)
    name = service.survey.log_path.name
    resp = await c.post(f"/api/survey/logs/{name}/assign", json={"track": "Ring"})
    assert resp.status_code == 409
    service.survey.stop()


async def test_log_names_cannot_escape_the_data_directory(client, tmp_path) -> None:
    """The log name comes from the URL, and it is the only thing between a
    caller and an arbitrary file read."""
    from app.processing import survey_log

    c, _service, _tmp = client
    outside = tmp_path.parent / "secrets.jsonl"
    outside.write_text("{}", encoding="utf-8")
    for name in ("../secrets.jsonl", "..\\secrets.jsonl", "/etc/passwd",
                 "notes.txt", "subdir/run.jsonl"):
        assert survey_log.log_path(tmp_path, name) is None, name

    resp = await c.post("/api/survey/logs/..%5Csecrets.jsonl/assign",
                        json={"track": "Ring"})
    assert resp.status_code != 200


# --- the joined overview (#46) ------------------------------------------------


async def test_overview_shows_where_the_three_sources_disagree(client) -> None:
    """Named, surveyed and matched to an official layout are three separate
    facts, and the rows worth looking at are the ones missing one of them."""
    c, service, tmp = client
    await service.repo.create_track(
        "Autopolis International Racing Course",
        __import__("app.processing.tracks", fromlist=["TrackSignature"]).TrackSignature(
            length_m=4674.0, min_x=-100.0, max_x=100.0, min_z=-100.0, max_z=100.0
        ),
    )
    await c.post("/api/track-bundles/import", json=_foreign_bundle(track="Some Kart Track"))

    body = (await c.get("/api/track-overview")).json()
    rows = {r["name"]: r for r in body["tracks"]}

    named_only = rows["Autopolis International Racing Course"]
    assert named_only["named"] is True and named_only["bundle"] is None
    # A name typed from the catalog's own autocomplete matches it exactly.
    assert named_only["suggestion"]["official_name"].startswith("Autopolis")
    assert named_only["suggestion"]["turns"] > 0

    surveyed_only = rows["Some Kart Track"]
    assert surveyed_only["named"] is False  # auto-identification will NOT work
    assert surveyed_only["bundle"]["points"] == 6
    assert surveyed_only["suggestion"] is None  # no honest guess to offer


async def test_official_match_is_confirmed_not_inferred(client) -> None:
    """GT7 broadcasts no track id and the catalog has no world coordinates, so
    the match is a suggestion a human accepts — and once accepted it travels
    with the bundle."""
    c, _service, tmp = client
    await c.post("/api/track-bundles/import", json=_foreign_bundle(track="Ring"))
    official = {"track": "Alsace", "layout": "Village", "official_id": "81f860",
                "official_name": "Alsace - Village", "turns": 17,
                "length_m": 5423, "reverse": False}
    resp = await c.patch("/api/track-bundles/ring",
                         json={"official": official, "set_official": True})
    assert resp.status_code == 200

    doc = track_bundle.load(tmp, "Ring")
    assert doc is not None and doc["meta"]["official"]["turns"] == 17
    row = next(r for r in (await c.get("/api/track-overview")).json()["tracks"]
               if r["slug"] == "ring")
    assert row["official"]["official_id"] == "81f860"
    assert row["suggestion"] is None  # settled; stop guessing at it


def test_a_disagreeing_lap_length_kills_a_name_match() -> None:
    """A name can be typed wrong; a length measured from driving it cannot be
    off by a sixth."""
    catalog = track_catalog.load(
        str(__import__("pathlib").Path(__file__).resolve().parents[1] / "data" / "tracks.json")
    )
    configs = track_catalog.configurations(catalog)
    good = track_catalog.suggest("Autopolis International Racing Course", configs, 4674)
    assert good is not None and good["confidence"] >= 1.0
    wrong = track_catalog.suggest("Autopolis International Racing Course", configs, 1200)
    assert wrong is None or wrong["confidence"] < good["confidence"]


def test_reverse_layouts_are_their_own_configuration() -> None:
    catalog = track_catalog.load(
        str(__import__("pathlib").Path(__file__).resolve().parents[1] / "data" / "tracks.json")
    )
    configs = track_catalog.configurations(catalog)
    forward = track_catalog.suggest("Alsace - Village", configs)
    reverse = track_catalog.suggest("Alsace - Village (Reverse)", configs)
    assert forward is not None and reverse is not None
    assert forward["reverse"] is False and reverse["reverse"] is True
    assert forward["official_id"] != reverse["official_id"]


# --- authored corners reach their consumers (#48) -----------------------------


def _bundle_with_corners(tmp, track="Ring"):
    """A surveyed circuit — corners are anchored to a map, so there has to be
    one before they can be authored."""
    source = track_bundle.source_id(tmp)
    edges = [
        track_bundle.new_edge(float(i), 5.0, 1.0, 0.0, "L", "edge", 1, source, 1.6, 3.0)
        for i in range(20)
    ]
    track_bundle.save(tmp, track, edges, [], count_run=True)


async def test_corners_can_only_be_authored_on_a_surveyed_circuit(client) -> None:
    c, _service, _tmp = client
    resp = await c.put("/api/track-bundles/ring/corners",
                       json={"corners": [{"apex": {"x": 1, "z": 2}}]})
    assert resp.status_code == 404


async def test_authored_corners_round_trip_and_are_renumbered(client) -> None:
    c, _service, tmp = client
    _bundle_with_corners(tmp)
    resp = await c.put("/api/track-bundles/ring/corners", json={"corners": [
        {"name": "Turn 1", "direction": "R", "apex": {"x": 10, "z": 5}},
        {"name": "Hairpin", "apex": {"x": 40, "z": 5},
         "entry": {"x": 30, "z": 5}, "exit": {"x": 50, "z": 5}},
    ]})
    assert resp.status_code == 200
    corners = resp.json()["corners"]
    assert [c["n"] for c in corners] == [1, 2]
    assert corners[1]["entry"] == {"x": 30.0, "z": 5.0}

    got = (await c.get("/api/track-bundles/ring/corners")).json()
    assert [c["name"] for c in got["corners"]] == ["Turn 1", "Hairpin"]
    # ...and they travel with the export, which is what makes a shared bundle
    # worth pulling (#47).
    exported = (await c.get("/api/track-bundles/ring")).json()
    assert len(exported["corners"]) == 2


async def test_a_survey_run_never_overwrites_authored_corners(client) -> None:
    """Re-driving the borders and labelling the corners are two jobs; the
    second must not be able to undo the first."""
    c, _service, tmp = client
    _bundle_with_corners(tmp)
    await c.put("/api/track-bundles/ring/corners",
                json={"corners": [{"name": "Turn 1", "apex": {"x": 10, "z": 5}}]})
    source = track_bundle.source_id(tmp)
    track_bundle.save(
        tmp, "Ring",
        [track_bundle.new_edge(99.0, 5.0, 1.0, 0.0, "L", "wall", 2, source, 1.6)],
        [], count_run=True,
    )
    doc = track_bundle.load(tmp, "Ring")
    assert doc is not None
    assert [c["name"] for c in doc["corners"]] == ["Turn 1"]


async def test_import_never_overwrites_authored_corners(client) -> None:
    c, _service, tmp = client
    _bundle_with_corners(tmp)
    await c.put("/api/track-bundles/ring/corners",
                json={"corners": [{"name": "Mine", "apex": {"x": 10, "z": 5}}]})
    incoming = _foreign_bundle(track="Ring")
    incoming["corners"] = [{"n": 1, "name": "Theirs", "apex": {"x": 10, "z": 5}}]
    result = (await c.post("/api/track-bundles/import", json=incoming)).json()
    assert result["corners_kept"] is True

    doc = track_bundle.load(tmp, "Ring")
    assert doc is not None
    assert [c["name"] for c in doc["corners"]] == ["Mine"]


async def test_corners_are_rejected_when_they_are_not_corners(client) -> None:
    c, _service, tmp = client
    _bundle_with_corners(tmp)
    for bad in (
        {"corners": [{"apex": {"x": "over there", "z": 5}}]},
        {"corners": [{"apex": {"x": 1, "z": 2}, "direction": "sideways"}]},
        {"corners": [{"name": "x" * 500, "apex": {"x": 1, "z": 2}}]},
        {"corners": [{"apex": None}]},
    ):
        assert (await c.put("/api/track-bundles/ring/corners", json=bad)).status_code == 400


async def test_the_analysis_endpoint_prefers_authored_corners(client) -> None:
    """`/analysis/compare` numbers corners off the reference lap. Once the
    circuit has authored corners, that numbering comes from the circuit
    instead — so it is the same in every session, not just within one."""
    from tests.test_api import drive_laps

    c, service, tmp = client
    await drive_laps(service, laps=2)
    laps = (await c.get("/api/laps")).json()
    ref = laps[0]["id"]
    detail = await c.get(f"/api/laps/{ref}")
    xs = detail.json()["samples"]["pos_x"]
    zs = detail.json()["samples"]["pos_z"]

    await service.repo.set_session_track(laps[0]["session_id"], "Ring")
    _bundle_with_corners(tmp)
    mid = len(xs) // 2
    await c.put("/api/track-bundles/ring/corners", json={"corners": [
        {"name": "Village", "apex": {"x": xs[mid], "z": zs[mid]}},
    ]})

    body = (await c.get(f"/api/analysis/compare?laps={ref}&ref={ref}")).json()
    corners = body["laps"][str(ref)]["corners"]
    assert len(corners) == 1
    assert corners[0]["name"] == "Village" and corners[0]["authored"] is True


async def test_the_race_engineer_prefers_authored_corners(client) -> None:
    """"the next corner" becomes "turn four" — and, more to the point, turn
    four stays turn four between sessions."""
    from tests.test_api import drive_laps

    c, service, tmp = client
    service.engineer.enabled = True
    await drive_laps(service, laps=2)
    laps = (await c.get("/api/laps")).json()
    samples = (await service.repo.get_laps_samples([laps[0]["id"]]))[laps[0]["id"]]
    service.engineer.ctx.track_name = "Ring"

    assert samples.get("pos_x")
    mid = len(samples["pos_x"]) // 2
    _bundle_with_corners(tmp)
    await c.put("/api/track-bundles/ring/corners", json={"corners": [
        {"name": "Parabolica", "apex": {"x": samples["pos_x"][mid],
                                        "z": samples["pos_z"][mid]}},
    ]})

    service.engineer._pending_reference = samples
    await service.engineer.refresh_reference()
    assert [c["name"] for c in service.engineer.ctx.corners] == ["Parabolica"]


async def test_saving_corners_invalidates_the_cached_set(client) -> None:
    """The bundle is a multi-megabyte document, so corners are cached — which
    only works if the refine view's save drops the stale copy."""
    c, service, tmp = client
    _bundle_with_corners(tmp)
    assert service.authored_corners("Ring") == []  # caches the empty answer
    await c.put("/api/track-bundles/ring/corners",
                json={"corners": [{"name": "Turn 1", "apex": {"x": 10, "z": 5}}]})
    assert [x["name"] for x in service.authored_corners("Ring")] == ["Turn 1"]


# --- review follow-ups --------------------------------------------------------


def test_a_killed_run_does_not_make_the_next_one_invisible(tmp_path) -> None:
    """The autosave writes a run's votes as it goes; `meta.runs` only counts a
    run that ENDED. A process killed in between leaves votes at run N behind a
    counter saying N-1 — and if the next run then reuses N, every metre it
    re-observes merges in as already counted. That silently discards exactly
    the evidence the autosave exists to protect."""
    from app.processing import survey as survey_mod

    src = track_bundle.source_id(tmp_path)
    survey = survey_mod.SurfaceSurvey()
    survey.start(tmp_path, track_width_m=1.6, track="Ring", track_user_set=True)
    for i in range(5):
        survey._append_edge(x=float(i), z=0.0, hx=1.0, hz=0.0, side="L",
                            kind="wall", pid=i)
    survey._save_bundle(count_run=False)  # the autosave...
    # ...and then the process dies. No stop(), so no count_run=True.
    doc = track_bundle.load(tmp_path, "Ring")
    assert doc is not None
    assert doc["meta"]["source_runs"] == {src: 0} or doc["meta"]["runs"] == 0
    assert track_bundle.watermarks(doc["edges"])[src] == 1  # the votes know

    second = survey_mod.SurfaceSurvey()
    second.start(tmp_path, track_width_m=1.6, track="Ring", track_user_set=True)
    assert second._run_no == 2, "reused the dead run's ordinal"
    for i in range(5):
        second._append_edge(x=float(i), z=0.0, hx=1.0, hz=0.0, side="L",
                            kind="wall", pid=i)
    second.stop()

    after = track_bundle.load(tmp_path, "Ring")
    assert after is not None
    # Two independent observations of that metre, not one.
    assert track_bundle.vote_count(after["edges"][0]["votes"], "wall") == 2


@pytest.mark.parametrize(
    ("votes", "why"),
    [
        ({"edge": {FOREIGN: [1000000, 1]}}, "a million votes cast on run 1"),
        ({"edge": {FOREIGN: [3, 2]}}, "three votes across two runs"),
        ({"edge": {FOREIGN: [1.5, 2]}}, "a fractional count"),
    ],
)
async def test_import_rejects_impossible_vote_counts(client, votes, why) -> None:
    """A source casts at most one vote per kind per run, so a count above its
    own run ordinal is a claim no driving could produce. Unchecked, one such
    record outvotes every hand mark at that metre — in a scheme whose whole
    premise is that the counts are a census of real observations."""
    c, _service, tmp = client
    doc = _foreign_bundle(n=2)
    doc["edges"][0]["votes"] = votes
    resp = await c.post("/api/track-bundles/import", json=doc)
    assert resp.status_code == 400, why
    assert track_bundle.load(tmp, "Ring") is None


async def test_import_repairs_run_counters_that_undercount_their_votes(client) -> None:
    """`source_runs` is authoritative for the next ordinal, so a document
    whose counters sit below its own votes would hand its owner an ordinal
    already present in the file."""
    c, _service, tmp = client
    doc = _foreign_bundle(n=3, runs=4)
    doc["meta"]["source_runs"] = {}  # says nothing about the source it uses
    assert (await c.post("/api/track-bundles/import", json=doc)).status_code == 200

    stored = track_bundle.load(tmp, "Ring")
    assert stored is not None
    assert stored["meta"]["source_runs"][FOREIGN] == 4
    assert stored["meta"]["runs"] == 4


@pytest.mark.parametrize("runs", [{FOREIGN: -3}, {FOREIGN: 2.5}])
async def test_import_rejects_nonsense_run_counters(client, runs) -> None:
    c, _service, tmp = client
    doc = _foreign_bundle()
    doc["meta"]["source_runs"] = runs
    assert (await c.post("/api/track-bundles/import", json=doc)).status_code == 400
    assert track_bundle.load(tmp, "Ring") is None


async def test_two_anonymous_bundles_are_not_confused_for_one_source(client) -> None:
    """Pre-v4 files carry no source id, so one is synthesised from the
    document. Derived from a sample of it, two people's bundles of the same
    circuit could collide into a single 'source' — and their overlapping
    votes would then be dropped as one person's duplicates."""
    c, _service, tmp = client
    mine = _foreign_bundle(version=2, n=80, track="Ring")
    theirs = _foreign_bundle(version=2, n=80, track="Ring")
    # Identical opening metres and run count; they differ only further in.
    theirs["edges"][79] = {**theirs["edges"][79], "x": 999.0}

    await c.post("/api/track-bundles/import", json=mine)
    await c.post("/api/track-bundles/import", json=theirs)

    stored = track_bundle.load(tmp, "Ring")
    assert stored is not None
    shared = next(e for e in stored["edges"] if e["x"] == 0.0)
    assert len(shared["votes"]["edge"]) == 2, "two contributors read as one"


async def test_a_rescued_run_cannot_be_rescued_again(client, tmp_path) -> None:
    """The view hides a rescued log, but a retry still reaches the endpoint —
    and a second merge inflates both the vote counts and the run count."""
    c, service, tmp = client
    survey = service.survey
    survey.start(tmp, track_width_m=1.6)
    for i in range(4):
        survey._append_edge(x=float(i), z=0.0, hx=1.0, hz=0.0, side="L",
                            kind="wall", pid=i)
    name = survey.log_path.name
    survey.stop()

    first = await c.post(f"/api/survey/logs/{name}/assign", json={"track": "Ring"})
    assert first.status_code == 200
    again = await c.post(f"/api/survey/logs/{name}/assign", json={"track": "Ring"})
    assert again.status_code == 409

    doc = track_bundle.load(tmp, "Ring")
    assert doc is not None and doc["meta"]["runs"] == 1
    # ...but re-assigning to a DIFFERENT circuit is the mis-label correction.
    other = await c.post(f"/api/survey/logs/{name}/assign", json={"track": "Elsewhere"})
    assert other.status_code == 200


async def test_a_replayed_log_stays_the_evidence_of_whoever_drove_it(client) -> None:
    """Logs travel between machines. Stamping the replaying installation would
    let one physical run be counted twice, once by each machine, if the one
    that recorded it ever contributes its own bundle."""
    c, service, tmp = client
    survey = service.survey
    survey.start(tmp, track_width_m=1.6)
    recorded_by = survey._source
    for i in range(4):
        survey._append_edge(x=float(i), z=0.0, hx=1.0, hz=0.0, side="L",
                            kind="wall", pid=i)
    name = survey.log_path.name
    survey.stop()

    # Pretend the log came from elsewhere by rewriting the id in its header.
    path = tmp / name
    lines = path.read_text().splitlines()
    lines[0] = lines[0].replace(recorded_by, "aaaa0000bbbb")
    path.write_text("\n".join(lines) + "\n")

    await c.post(f"/api/survey/logs/{name}/assign", json={"track": "Ring"})
    doc = track_bundle.load(tmp, "Ring")
    assert doc is not None
    assert list(doc["edges"][0]["votes"]["wall"]) == ["aaaa0000bbbb"]
    assert track_bundle.source_id(tmp) not in doc["meta"]["source_runs"]


async def test_a_failed_assignment_marker_aborts_the_merge(client, monkeypatch) -> None:
    """The marker is the only durable record that a run was rescued. Merging
    first and swallowing a failed write reports success while leaving the run
    listed as orphaned — so the obvious retry merges it a second time."""
    c, service, tmp = client
    survey = service.survey
    survey.start(tmp, track_width_m=1.6)
    survey._append_edge(x=1.0, z=0.0, hx=1.0, hz=0.0, side="L", kind="wall", pid=1)
    name = survey.log_path.name
    survey.stop()
    (tmp / name).chmod(0o444)

    resp = await c.post(f"/api/survey/logs/{name}/assign", json={"track": "Ring"})
    assert resp.status_code == 400
    assert "rescued" in resp.json()["detail"]
    assert track_bundle.load(tmp, "Ring") is None  # nothing merged


async def test_a_circuit_being_surveyed_cannot_be_renamed_or_deleted(client) -> None:
    """The survey holds its circuit's name in memory and saves by it, so
    moving the bundle out from under a live run just recreates the old one on
    the next autosave — splitting the circuit rename exists to repair."""
    c, service, tmp = client
    await c.post("/api/track-bundles/import", json=_foreign_bundle(track="Ring"))
    service.survey.start(tmp, track_width_m=1.6, track="Ring", track_user_set=True)

    assert (await c.patch("/api/track-bundles/ring",
                          json={"track": "Ring Proper"})).status_code == 409
    assert (await c.delete("/api/track-bundles/ring")).status_code == 409
    service.survey.stop()
    assert (await c.patch("/api/track-bundles/ring",
                          json={"track": "Ring Proper"})).status_code == 200


async def test_every_bundle_change_drops_the_cached_corners(client) -> None:
    """Analysis and the Race Engineer read corners through a cache, because
    the bundle they live in is megabytes. Any path that can change a circuit's
    authored corners has to invalidate it, not just the corner editor's save."""
    c, service, tmp = client
    _bundle_with_corners(tmp, "Ring")
    assert service.authored_corners("Ring") == []  # caches the empty answer

    incoming = _foreign_bundle(track="Ring")
    incoming["corners"] = [{"n": 1, "name": "Imported", "apex": {"x": 1, "z": 5}}]
    await c.post("/api/track-bundles/import", json=incoming)
    assert [x["name"] for x in service.authored_corners("Ring")] == ["Imported"]

    await c.patch("/api/track-bundles/ring", json={"track": "Ring Two"})
    assert service.authored_corners("Ring") == []
    assert [x["name"] for x in service.authored_corners("Ring Two")] == ["Imported"]

    await c.delete("/api/track-bundles/ring-two")
    assert service.authored_corners("Ring Two") == []


async def test_the_track_override_cannot_be_an_arbitrary_filename(client) -> None:
    c, _service, _tmp = client
    resp = await c.post(f"/api/track-bundles/import?track={'x' * 500}",
                        json=_foreign_bundle())
    assert resp.status_code == 200  # capped, not crashed
    assert len(resp.json()["track"]) <= track_bundle.MAX_TRACK_NAME
    assert (await c.post("/api/track-bundles/import?track=%20%20",
                         json=_foreign_bundle())).status_code == 400


async def test_import_stops_reading_an_oversized_body(client, monkeypatch) -> None:
    """The size check ran after the whole body was buffered, so a chunked
    upload — which carries no Content-Length — could still make the process
    allocate far past the advertised cap."""
    from app.api import tracks as tracks_api

    monkeypatch.setattr(tracks_api, "MAX_IMPORT_BYTES", 2048)
    c, _service, _tmp = client

    async def chunks():
        for _ in range(64):
            yield b"x" * 512

    resp = await c.post("/api/track-bundles/import", content=chunks(),
                        headers={"Content-Type": "application/json"})
    assert resp.status_code == 413
