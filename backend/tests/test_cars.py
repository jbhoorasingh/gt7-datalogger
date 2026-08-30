"""The car inventory: parsing it, refreshing it, and what happens without it (#57).

Before this file the car lookup had no coverage at all — nineteen test modules
built an empty CarDatabase and none of them ever called load(), so the parse,
the malformed-row skip and the `Car #{id}` fallback were all untested while
being the only thing standing between a session row and a number.

The shipped inventory is exercised here as itself, not as a fixture: it is
committed to the tree, it is what every fresh install reads, and a release that
breaks it should fail here rather than on someone's console.
"""

import datetime
import json
import logging

import httpx
import pytest

from app.config import Settings
from app.main import GENERATED_KEY, refresh_cars_if_stale, sync_car_inventory
from app.processing import car_refresh, car_source, gt7_assets
from app.processing.cars import SCHEMA_VERSION, Car, CarDatabase, Inventory, dumps, loads
from app.processing.laps import SessionInfo
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def repo(tmp_path):
    engine = make_engine(tmp_path / "cars.db")
    await init_db(engine)
    yield Repository(make_session_factory(engine))
    await engine.dispose()


def _inventory(*cars: Car, generated: str = "2026-08-30") -> Inventory:
    return Inventory(cars={c.id: c for c in cars}, generated=generated)


NISSAN = Car(
    id=102, name="Skyline GTS-R (R31) '87", full_name="Nissan Skyline GTS-R (R31) '87",
    manufacturer="Nissan", year=1987, category="Gr.N", drivetrain="FR", aspiration="TC",
    displacement_cc=1998, power_bhp=207, torque_kgfm=25.0, weight_kg=1340,
    length_mm=4660, width_mm=1690, height_mm=1365, performance_points=440.85,
)


# --- the shipped file ---------------------------------------------------------


def test_the_bundled_inventory_loads_and_covers_the_old_csv() -> None:
    """Layer 1 of #57: a fresh install with no network names every car it ships
    knowing about, and never fewer than the CSV it replaced."""
    db = CarDatabase()
    db.load(Settings().cars_json)
    assert db.count >= 575  # the pre-#57 CSV had 575 rows; the merge only adds
    assert db.generated  # the file records when it was built
    assert db.schema_version == SCHEMA_VERSION
    car = db.get(102)
    assert car is not None
    assert car.manufacturer == "Nissan"
    assert car.year == 1987
    assert car.drivetrain == "FR"


def test_the_bundled_inventory_is_reachable_from_any_working_directory(tmp_path, monkeypatch):
    """The bug #57 calls out: a CWD-relative default silently resolves to
    nothing anywhere but the repo root."""
    monkeypatch.chdir(tmp_path)
    db = CarDatabase()
    db.load(Settings().car_inventory())
    assert db.count > 0


def test_every_bundled_car_has_a_name() -> None:
    """A row with no name is worse than a missing row: it renders as blank
    rather than falling back to `Car #{id}`."""
    db = CarDatabase()
    db.load(Settings().cars_json)
    assert [c.id for c in db.all().values() if not c.name.strip()] == []


# --- parsing ------------------------------------------------------------------


def test_round_trip_preserves_every_field() -> None:
    inv = _inventory(NISSAN)
    assert loads(dumps(inv)).cars == inv.cars


def test_malformed_rows_are_skipped_not_fatal() -> None:
    """One bad entry must not cost the other cars their names."""
    raw = json.dumps(
        {
            "meta": {"generated": "2026-08-30", "schema_version": SCHEMA_VERSION},
            "cars": {
                "102": {"name": "Skyline GTS-R (R31) '87"},
                "not-an-id": {"name": "nonsense"},
                "999": {"name": "Odd", "unexpected_field": True},
                "1000": "not an object",
            },
        }
    )
    inv = loads(raw)
    assert inv.cars[102].name == "Skyline GTS-R (R31) '87"
    assert inv.cars[999].name == "Odd"  # unknown fields ignored, row kept
    assert 1000 not in inv.cars


def test_a_missing_file_leaves_ids_showing(tmp_path) -> None:
    db = CarDatabase()
    db.load(tmp_path / "absent.json")
    assert db.count == 0
    assert db.name(4127) == "Car #4127"
    assert db.get(4127) is None


def test_an_unparseable_file_does_not_raise(tmp_path) -> None:
    """A corrupt inventory degrades to ids, exactly like a missing one — it
    does not take the process down on the way up."""
    path = tmp_path / "cars.json"
    path.write_text("{ this is not json", encoding="utf-8")
    db = CarDatabase()
    db.load(path)
    assert db.count == 0
    assert db.name(1) == "Car #1"


def test_an_unknown_id_falls_back_to_its_number() -> None:
    db = CarDatabase()
    db.replace(_inventory(NISSAN))
    assert db.name(102) == "Skyline GTS-R (R31) '87"
    assert db.name(4127) == "Car #4127"


# --- the legacy CSV -----------------------------------------------------------


def test_a_pinned_legacy_csv_is_still_read(tmp_path) -> None:
    """#57 keeps the old shape working for one release: an install that pinned
    GT7_CARS_CSV keeps its names, it just gets nothing richer."""
    csv_path = tmp_path / "cars.csv"
    csv_path.write_text("id,name\n24,180SX Type X '96\n", encoding="utf-8")
    settings = Settings(cars_csv=csv_path)
    assert settings.car_inventory() == csv_path

    db = CarDatabase()
    db.load(settings.car_inventory())
    assert db.name(24) == "180SX Type X '96"
    car = db.get(24)
    assert car is not None and car.manufacturer == ""


def test_malformed_csv_rows_are_skipped(tmp_path) -> None:
    csv_path = tmp_path / "cars.csv"
    csv_path.write_text(
        "id,name\n24,Good\nnot-a-number,Bad\n,Missing\n31,Also Good\n", encoding="utf-8"
    )
    db = CarDatabase()
    db.load(csv_path)
    assert db.count == 2
    assert db.name(24) == "Good"
    assert db.name(31) == "Also Good"


def test_a_blank_cars_csv_setting_means_unset(tmp_path) -> None:
    """Pydantic renders an empty environment variable as Path("."), which must
    not be mistaken for "load the current directory"."""
    settings = Settings(cars_csv="")
    assert settings.car_inventory() == settings.cars_json


def test_a_refreshed_inventory_wins_over_the_shipped_one(tmp_path) -> None:
    """The refresh writes beside the database, never inside the package."""
    settings = Settings(db_path=tmp_path / "gt7.db")
    assert settings.refreshed_car_inventory() == tmp_path / "cars.json"
    assert settings.car_inventory() == settings.cars_json  # nothing written yet

    (tmp_path / "cars.json").write_text(dumps(_inventory(NISSAN)), encoding="utf-8")
    assert settings.car_inventory() == tmp_path / "cars.json"


# --- mapping the source -------------------------------------------------------


CARS_JS = (
    "var e={car102:{aspirationShort:`TC`,carClass:`Gr.N`,displacement_v:`1998`,"
    "driveTrain:`FR`,height_v:1365,id:`car102`,length_v:4660,manufacturerId:`tnr28`,"
    "nameLong:`Nissan Skyline GTS-R (R31) '87`,nameShort:`Skyline GTS-R (R31) '87`,"
    "performancePoint:`PP 440.85`,power_v:207,torque_v:25,weight_v:1340,width_v:1690},"
    "car999:{aspirationShort:`---`,carClass:`Gr.X`,displacement_v:`654x2`,driveTrain:`---`,"
    "id:`car999`,nameLong:`Mazda 787B '91`,nameShort:`787B '91`,manufacturerId:`tnr20`,"
    "performancePoint:`PP 831.54`,power_v:700,torque_v:60.5,weight_v:830}"
    "};export{e as Cars}"
)
TUNERS_JS = (
    "var e={tnr28:{id:`tnr28`,name:`Nissan`},"
    "tnr20:{id:`tnr20`,name:`Mazda `}};export{e as Tuners}"
)


def test_the_source_maps_onto_our_schema() -> None:
    inv = car_source.build_inventory(CARS_JS, TUNERS_JS, generated="2026-08-30", pivot=26)
    car = inv.cars[102]
    assert car == NISSAN
    assert inv.generated == "2026-08-30"


def test_rotary_displacement_is_the_swept_volume() -> None:
    """"654x2" is rotor notation, not a typo: the 13B is quoted as 1,308 cc."""
    inv = car_source.build_inventory(CARS_JS, TUNERS_JS, pivot=26)
    assert inv.cars[999].displacement_cc == 1308


def test_the_sources_blanks_become_ours() -> None:
    inv = car_source.build_inventory(CARS_JS, TUNERS_JS, pivot=26)
    car = inv.cars[999]
    assert car.drivetrain == ""  # "---" upstream
    assert car.aspiration == ""
    assert car.manufacturer == "Mazda"  # trailing space stripped


@pytest.mark.parametrize(
    ("short", "long", "expected"),
    [
        ("Skyline GTS-R (R31) '87", "Nissan Skyline GTS-R (R31) '87", 1987),
        ("Mini-Cooper 'S' '65", "Mini-Cooper 'S' '65", 1965),  # apostrophes inside
        ("AFEELA 1 '26", "AFEELA 1 '26", 2026),  # at the pivot: this century
        ("S Barker Tourer '29", "Mercedes-Benz S Barker Tourer '29", 1929),  # past it
        ("Ford Roadster", "1932 Ford Roadster", 1932),  # spelled out, at the front
        ("AFEELA Prototype 2024", "AFEELA Prototype 2024", 2024),  # at the end
        ("L500R HYbrid VGT", "Peugeot L500R HYbrid Vision Gran Turismo, 2017", 2017),
        # Model names, not years — reading these as years would invent data.
        ("HYUNDAI N 2025 VGT", "HYUNDAI N 2025 Vision Gran Turismo", 0),
        ("Red Bull X2014 Standard", "Red Bull X2014 Standard", 0),
        ("FT-1", "Toyota FT-1", 0),
    ],
)
def test_model_years_are_read_only_where_they_are_unambiguous(short, long, expected) -> None:
    assert car_source._year(short, long, 26) == expected


def test_an_empty_car_list_is_an_error_not_an_empty_inventory() -> None:
    """Silently replacing 584 cars with nothing is the one outcome worse than
    failing the refresh."""
    with pytest.raises(ValueError):
        car_source.build_inventory("var e={};export{e as Cars}", TUNERS_JS)


def test_strings_keep_their_punctuation_and_accents() -> None:
    """The chunk is scanned rather than regex-substituted, because car names
    contain the very characters the JSON fix-ups rewrite."""
    src = (
        "var e={car1:{id:`car1`,nameShort:`Citroën {a:1}, \"quoted\"`,"
        "nameLong:`Citroën GT by Citroën`,manufacturerId:`tnr9`}};export{e as Cars}"
    )
    parsed = gt7_assets.parse_js_object(src)
    assert parsed["car1"]["nameShort"] == 'Citroën {a:1}, "quoted"'


def test_a_chunk_that_is_not_a_chunk_is_rejected() -> None:
    with pytest.raises(ValueError):
        gt7_assets.parse_js_object("this is not a data chunk")


def test_assets_are_discovered_from_the_page_not_hardcoded() -> None:
    """The hashes change on every site build, so both hops are dynamic."""
    page = '<script type="module" src="/common/dist/gt7/carlist/assets/index-BxejHdeX.js"></script>'
    assert gt7_assets.index_chunk_name(page) == "index-BxejHdeX.js"
    index = 'import("./cars.gb-40_LojsM.js");import("./tuners.gb-zrIjzZI5.js")'
    assert gt7_assets.data_chunk_names(index, "cars") == {"gb": "cars.gb-40_LojsM.js"}
    assert gt7_assets.data_chunk_names(index, "tuners") == {"gb": "tuners.gb-zrIjzZI5.js"}


def test_a_refresh_never_drops_a_car_the_source_stopped_publishing() -> None:
    """Ten ids in the pre-#57 CSV are gone from GT7's list, and sessions still
    refer to them: the merge only ever adds and updates."""
    base = _inventory(NISSAN, Car(id=3503, name="RX-VISION GT3 CONCEPT Stealth Model"))
    fresh = _inventory(Car(id=102, name="Renamed"), Car(id=4127, name="New Car"))
    merged = car_source.merge(base, fresh)
    assert merged.cars[3503].name == "RX-VISION GT3 CONCEPT Stealth Model"  # kept
    assert merged.cars[102].name == "Renamed"  # updated
    assert merged.cars[4127].name == "New Car"  # added


# --- the staleness decision ---------------------------------------------------


TODAY = datetime.date(2026, 8, 30)


def test_a_first_run_is_stale() -> None:
    assert car_refresh.is_stale({}, TODAY) is True


def test_a_recent_refresh_is_not_stale() -> None:
    stored = {
        car_refresh.UPDATED_AT_KEY: "2026-08-28",
        car_refresh.VERSION_KEY: str(SCHEMA_VERSION),
    }
    assert car_refresh.is_stale(stored, TODAY) is False


def test_an_old_refresh_is_stale() -> None:
    stored = {
        car_refresh.UPDATED_AT_KEY: "2026-08-01",
        car_refresh.VERSION_KEY: str(SCHEMA_VERSION),
    }
    assert car_refresh.is_stale(stored, TODAY) is True


def test_a_schema_bump_is_stale_however_recent_the_data() -> None:
    """Data built by a release that knew fewer fields is not current, whatever
    its date says."""
    stored = {car_refresh.UPDATED_AT_KEY: TODAY.isoformat(), car_refresh.VERSION_KEY: "0"}
    assert car_refresh.is_stale(stored, TODAY) is True


@pytest.mark.parametrize("marker", ["", "not-a-date", "2027-01-01"])
def test_an_unreadable_or_future_marker_costs_one_fetch(marker) -> None:
    """A clock that moved backwards should not mean permanent staleness in
    either direction."""
    stored = {car_refresh.UPDATED_AT_KEY: marker, car_refresh.VERSION_KEY: str(SCHEMA_VERSION)}
    assert car_refresh.is_stale(stored, TODAY) is True


# --- the refresh --------------------------------------------------------------


def _stub_client(page: str = "", index: str = "", cars: str = CARS_JS, tuners: str = TUNERS_JS):
    page = page or '<script src="/assets/index-abc12345.js">'
    index = index or 'import("./cars.gb-aaaaaaaa.js");import("./tuners.gb-bbbbbbbb.js")'

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/carlist/"):
            return httpx.Response(200, text=page)
        if "index-" in url:
            return httpx.Response(200, text=index)
        if "cars.gb" in url:
            return httpx.Response(200, text=cars)
        if "tuners.gb" in url:
            return httpx.Response(200, text=tuners)
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_a_refresh_writes_the_file_and_updates_the_loaded_inventory(tmp_path) -> None:
    db = CarDatabase()
    db.replace(_inventory(Car(id=3503, name="Gone From The List")))
    destination = tmp_path / "cars.json"

    async with _stub_client() as client:
        merged = await car_refresh.fetch_and_store(db, destination, client=client)

    assert destination.exists()
    assert merged.cars[102].manufacturer == "Nissan"
    assert db.get(102) is not None  # the live lookup sees it immediately
    assert db.get(3503) is not None  # and keeps what the source no longer lists
    assert loads(destination.read_text()).cars == merged.cars


async def test_a_failed_refresh_leaves_the_bundled_inventory_alone(tmp_path, repo, caplog) -> None:
    """Being offline is a normal state for a datalogger on a console's LAN."""
    settings = Settings(db_path=tmp_path / "gt7.db")
    db = CarDatabase()
    db.load(settings.cars_json)
    before = db.count

    async def explode(*args, **kwargs):
        raise httpx.ConnectError("no route to host")

    original = car_refresh.fetch_and_store
    car_refresh.fetch_and_store = explode
    try:
        await refresh_cars_if_stale(settings, repo, db, {}, logging.getLogger("t"))
    finally:
        car_refresh.fetch_and_store = original

    assert db.count == before
    assert not (tmp_path / "cars.json").exists()


async def test_a_fresh_marker_skips_the_fetch_entirely(tmp_path, repo) -> None:
    settings = Settings(db_path=tmp_path / "gt7.db")
    db = CarDatabase()
    called = False

    async def record(*args, **kwargs):
        nonlocal called
        called = True

    original = car_refresh.fetch_and_store
    car_refresh.fetch_and_store = record
    try:
        stored = {
            car_refresh.UPDATED_AT_KEY: datetime.date.today().isoformat(),
            car_refresh.VERSION_KEY: str(SCHEMA_VERSION),
        }
        await refresh_cars_if_stale(settings, repo, db, stored, logging.getLogger("t"))
    finally:
        car_refresh.fetch_and_store = original
    assert called is False


# --- the session rows ---------------------------------------------------------


async def test_a_new_session_carries_the_cars_details(repo) -> None:
    session_id = await repo.create_session(
        SessionInfo(car_id=102, started_at="2026-08-30T00:00:00Z"), NISSAN
    )
    row = next(s for s in await repo.list_sessions() if s["id"] == session_id)
    assert row["car_name"] == "Skyline GTS-R (R31) '87"
    assert row["car_manufacturer"] == "Nissan"
    assert row["car_year"] == 1987
    assert row["car_drivetrain"] == "FR"
    assert row["car_aspiration"] == "TC"
    assert row["car_category"] == "Gr.N"


async def test_an_unknown_car_still_opens_a_session(repo) -> None:
    """A car added by a GT7 update we have not refreshed for yet."""
    session_id = await repo.create_session(
        SessionInfo(car_id=4127, started_at="2026-08-30T00:00:00Z"), None
    )
    row = next(s for s in await repo.list_sessions() if s["id"] == session_id)
    assert row["car_name"] == "Car #4127"
    assert row["car_manufacturer"] == ""


async def test_the_packets_category_wins_over_the_inventorys(repo) -> None:
    """Packet C reports what the car is racing as; the inventory reports its
    showroom class, and the race is the truth about the session."""
    session_id = await repo.create_session(
        SessionInfo(car_id=102, started_at="2026-08-30T00:00:00Z", car_category="Gr.3"), NISSAN
    )
    row = next(s for s in await repo.list_sessions() if s["id"] == session_id)
    assert row["car_category"] == "Gr.3"


async def test_the_backfill_fills_old_sessions_and_leaves_names_alone(repo) -> None:
    """A session recorded before the car was known gets its details once it is,
    without the name the user has been looking at changing underneath them."""
    known = await repo.create_session(
        SessionInfo(car_id=102, started_at="2026-08-30T00:00:00Z"),
        Car(id=102, name="Skyline GTS-R (R31) '87"),
    )
    unknown = await repo.create_session(
        SessionInfo(car_id=4127, started_at="2026-08-30T01:00:00Z"), None
    )

    filled = await repo.backfill_session_cars(
        {102: NISSAN, 4127: Car(id=4127, name="A New Car", manufacturer="Genesis", year=2027)}
    )
    assert filled == 2
    rows = {s["id"]: s for s in await repo.list_sessions()}
    assert rows[known]["car_manufacturer"] == "Nissan"
    assert rows[known]["car_name"] == "Skyline GTS-R (R31) '87"
    # The placeholder is replaced, because it was never a name anyone chose.
    assert rows[unknown]["car_name"] == "A New Car"
    assert rows[unknown]["car_year"] == 2027


async def test_the_backfill_only_runs_when_the_inventory_changed(repo, tmp_path) -> None:
    """Startup work that would not change an answer should not happen."""
    settings = Settings(db_path=tmp_path / "gt7.db")
    log = logging.getLogger("t")
    db = CarDatabase()

    stored: dict[str, str] = {}
    await sync_car_inventory(settings, repo, db, stored, log)
    assert (await repo.get_settings())[GENERATED_KEY] == car_refresh.stamp(db)

    # Second start, same file: the marker matches, so nothing is rewritten.
    calls = 0
    original = Repository.backfill_session_cars

    async def counting(self, cars):
        nonlocal calls
        calls += 1
        return await original(self, cars)

    Repository.backfill_session_cars = counting
    try:
        await sync_car_inventory(settings, repo, db, await repo.get_settings(), log)
    finally:
        Repository.backfill_session_cars = original
    assert calls == 0


async def test_a_missing_inventory_does_not_stop_a_start(repo, tmp_path) -> None:
    settings = Settings(cars_json=tmp_path / "absent.json", db_path=tmp_path / "gt7.db")
    db = CarDatabase()
    await sync_car_inventory(settings, repo, db, {}, logging.getLogger("t"))
    assert db.count == 0
    assert GENERATED_KEY not in await repo.get_settings()


# --- the whole record on the row, and laps reading it across the join --------


FULL_FIELDS = (
    "car_manufacturer", "car_year", "car_drivetrain", "car_aspiration", "car_full_name",
    "car_displacement_cc", "car_power_bhp", "car_torque_kgfm", "car_weight_kg",
    "car_length_mm", "car_width_mm", "car_height_mm", "car_performance_points",
)


async def test_a_session_row_carries_the_whole_car_record(repo) -> None:
    """Denormalised in full, so the row answers for the car on its own rather
    than being a key into a file that may describe it differently later."""
    session_id = await repo.create_session(
        SessionInfo(car_id=102, started_at="2026-08-30T00:00:00Z"), NISSAN
    )
    row = next(s for s in await repo.list_sessions() if s["id"] == session_id)
    assert row["car_full_name"] == "Nissan Skyline GTS-R (R31) '87"
    assert row["car_displacement_cc"] == 1998
    assert row["car_power_bhp"] == 207
    assert row["car_torque_kgfm"] == 25.0
    assert row["car_weight_kg"] == 1340
    assert (row["car_length_mm"], row["car_width_mm"], row["car_height_mm"]) == (4660, 1690, 1365)
    assert row["car_performance_points"] == 440.85


async def test_an_unknown_car_leaves_every_figure_empty(repo) -> None:
    session_id = await repo.create_session(
        SessionInfo(car_id=4127, started_at="2026-08-30T00:00:00Z"), None
    )
    row = next(s for s in await repo.list_sessions() if s["id"] == session_id)
    assert [row[f] for f in FULL_FIELDS] == ["", 0, "", "", "", 0, 0, 0.0, 0, 0, 0, 0, 0.0]


async def test_the_backfill_fills_the_figures_too(repo) -> None:
    session_id = await repo.create_session(
        SessionInfo(car_id=102, started_at="2026-08-30T00:00:00Z"), None
    )
    assert await repo.backfill_session_cars({102: NISSAN}) == 1
    row = next(s for s in await repo.list_sessions() if s["id"] == session_id)
    assert row["car_power_bhp"] == 207
    assert row["car_performance_points"] == 440.85


async def _session_with_lap(repo, car: Car | None, car_id: int, track: str = "Suzuka") -> int:
    from app.processing.laps import CompletedLap

    session_id = await repo.create_session(
        SessionInfo(car_id=car_id, started_at=f"2026-08-30T0{car_id % 9}:00:00Z"), car
    )
    await repo.set_session_track(session_id, track)
    lap = CompletedLap(
        number=1, time_ms=92_000, finished_at="2026-08-30T00:01:00Z", car_id=car_id,
        samples={"t": [0.0], "dist": [0.0]}, fuel_start=100.0, fuel_end=99.0,
    )
    await repo.save_lap(session_id, lap)
    return session_id


async def test_lap_summaries_carry_the_cars_details_without_a_second_copy(repo) -> None:
    """The point of #57's lap-side answer: every lap query already joins the
    session, so the car columns come for free and `laps` stores no duplicate."""
    await _session_with_lap(repo, NISSAN, 102)
    laps = await repo.list_laps()
    assert laps[0]["car_manufacturer"] == "Nissan"
    assert laps[0]["car_year"] == 1987
    assert laps[0]["car_drivetrain"] == "FR"
    assert laps[0]["car_aspiration"] == "TC"


async def test_laps_filter_by_manufacturer_and_drivetrain(repo) -> None:
    await _session_with_lap(repo, NISSAN, 102)
    await _session_with_lap(
        repo, Car(id=200, name="MX-5", manufacturer="Mazda", drivetrain="FR"), 200
    )
    await _session_with_lap(
        repo, Car(id=300, name="Golf", manufacturer="Volkswagen", drivetrain="FF"), 300
    )

    assert [lap["car_id"] for lap in await repo.list_laps(manufacturer="Nissan")] == [102]
    fr = sorted(lap["car_id"] for lap in await repo.list_laps(drivetrain="FR"))
    assert fr == [102, 200]
    assert await repo.list_laps(manufacturer="Ferrari") == []


async def test_the_bests_board_and_lap_detail_carry_them_too(repo) -> None:
    await _session_with_lap(repo, NISSAN, 102)
    board = await repo.personal_bests()
    assert board[0]["car_manufacturer"] == "Nissan"
    assert board[0]["car_drivetrain"] == "FR"

    lap_id = (await repo.list_laps())[0]["id"]
    detail = await repo.get_lap(lap_id, with_samples=False)
    assert detail is not None
    assert detail["car_manufacturer"] == "Nissan"
    assert detail["car_year"] == 1987


async def test_the_class_benchmark_lookup_carries_them(repo) -> None:
    from app.processing.laps import CompletedLap

    session_id = await repo.create_session(
        SessionInfo(car_id=102, started_at="2026-08-30T00:00:00Z", car_category="Gr.3"), NISSAN
    )
    await repo.set_session_track(session_id, "Suzuka")
    lap = CompletedLap(
        number=1, time_ms=90_000, finished_at="2026-08-30T00:01:00Z", car_id=102,
        samples={"t": [0.0], "dist": [0.0]}, fuel_start=100.0, fuel_end=99.0,
    )
    lap.car_category = "Gr.3"
    await repo.save_lap(session_id, lap)

    best = await repo.best_lap_in("Suzuka", "Gr.3")
    assert best is not None
    assert best["car_manufacturer"] == "Nissan"


async def test_adding_columns_re_runs_the_backfill_on_upgrade(repo, tmp_path) -> None:
    """The 0009 upgrade path: a release that adds denormalised columns leaves
    the inventory file byte-identical, so the marker has to carry the column
    shape as well — keyed on the file alone, old rows would keep the empty
    values the migration gave them and nothing would ever fill them."""
    settings = Settings(db_path=tmp_path / "gt7.db")
    log = logging.getLogger("t")
    db = CarDatabase()
    db.replace(_inventory(NISSAN))
    await repo.create_session(SessionInfo(car_id=102, started_at="2026-08-30T00:00:00Z"), None)

    # A marker written by the previous release: same inventory, older shape.
    old_marker = f"{car_refresh.COLUMNS_VERSION - 1}:{db.schema_version}:{db.generated}"
    await repo.set_setting(GENERATED_KEY, old_marker)

    await sync_car_inventory(settings, repo, db, {GENERATED_KEY: old_marker}, log)

    row = (await repo.list_sessions())[0]
    assert row["car_power_bhp"] == 207  # a column the older shape never wrote
    assert (await repo.get_settings())[GENERATED_KEY] == car_refresh.stamp(db)
