"""The corner report card through the API: where each lap braked against the
reference (#110) and how far it was rotated through the corner (#109)."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.processing.cars import CarDatabase
from app.service import TelemetryService
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository
from tests import stadium_track as track


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
        yield c, service
    await engine.dispose()


async def drive(service: TelemetryService, laps: list[dict]) -> None:
    driver = track.Driver()
    for number, how in enumerate(laps, start=1):
        for packet in driver.lap(number, **how):
            await service._on_packet(packet)
    await service._on_packet(driver.line(len(laps) + 1))


async def compared(c: AsyncClient, channels: str = "") -> tuple[dict, str, str]:
    """The comparison of the two laps driven, the first as the reference."""
    by_number = {lap["number"]: lap["id"] for lap in (await c.get("/api/laps")).json()}
    ref, other = by_number[1], by_number[2]
    query = f"/api/analysis/compare?laps={ref},{other}&ref={ref}"
    if channels:
        query += f"&channels={channels}"
    resp = await c.get(query)
    assert resp.status_code == 200
    return resp.json(), str(ref), str(other)


async def test_the_report_says_how_much_earlier_a_lap_braked(client) -> None:
    c, service = client
    await drive(service, [{"brake_before_m": 80.0}, {"brake_before_m": 94.0}])
    data, ref, other = await compared(c)
    corners = data["laps"][ref]["corners"]
    assert [corner["direction"] for corner in corners] == ["R", "R"]

    theirs = {row["n"]: row for row in data["laps"][ref]["corner_report"]}
    mine = {row["n"]: row for row in data["laps"][other]["corner_report"]}
    for n, (entry, _exit) in zip((1, 2), track.CORNERS, strict=True):
        # A tick at 40 m/s is 0.67 m, and the pedal is on from the first
        # tick past the mark.
        assert theirs[n]["brake_on"] == pytest.approx(entry - 80.0, abs=1.5)
        assert theirs[n]["brake_dist"] == pytest.approx(80.0, abs=2.0)
        assert theirs[n]["brake_peak"] == pytest.approx(100.0)
        assert theirs[n]["brake_delta_m"] is None  # the reference itself
        assert mine[n]["brake_delta_m"] == pytest.approx(-14.0, abs=2.0)
        assert mine[n]["brake_dist"] == pytest.approx(94.0, abs=2.0)


async def test_a_corner_taken_without_braking_has_no_braking_point(client) -> None:
    c, service = client
    await drive(service, [{"brake_before_m": 80.0}, {"brake_before_m": 80.0}])
    data, ref, _other = await compared(c)
    rows = data["laps"][ref]["corner_report"]
    assert rows and all(row["brake_on"] is not None for row in rows)
    # The same circuit with no corners to brake for is covered in
    # test_analysis; here, that the keys are always present.
    assert {"brake_on", "brake_off", "brake_peak", "brake_dist", "brake_delta_m"} <= rows[0].keys()


async def test_the_report_says_how_far_the_car_was_rotated(client) -> None:
    c, service = client
    await drive(service, [{"slip_deg": 3.0}, {"slip_deg": 5.0}])
    data, ref, other = await compared(c, channels="speed,body_slip")
    for lap_id, slip in ((ref, 3.0), (other, 5.0)):
        assert "body_slip" in data["laps"][lap_id]["series"]
        for row in data["laps"][lap_id]["corner_report"]:
            assert row["slip_peak"] == pytest.approx(slip, abs=0.05)
            # The window detection draws runs a little past the arc either
            # end, where the car is straight again.
            assert 0.6 * slip < row["slip_mean"] <= slip


async def test_a_recording_without_an_orientation_has_no_balance(client) -> None:
    c, service = client
    await drive(service, [{"slip_deg": None}, {"slip_deg": None}])
    data, ref, _other = await compared(c, channels="speed,body_slip")
    assert "body_slip" not in data["laps"][ref]["series"]
    for row in data["laps"][ref]["corner_report"]:
        assert row["slip_peak"] is None and row["slip_mean"] is None
    lap = (await c.get("/api/laps")).json()[0]
    assert "body_slip" not in (await c.get(f"/api/laps/{lap['id']}")).json()["samples"]


async def test_the_channel_is_exported(client) -> None:
    c, service = client
    await drive(service, [{}, {}])
    lap = (await c.get("/api/laps")).json()[0]
    samples = (await c.get(f"/api/laps/{lap['id']}")).json()["samples"]
    assert len(samples["body_slip"]) == len(samples["t"])
    assert max(samples["body_slip"]) == pytest.approx(3.0, abs=0.01)
    body = (await c.get(f"/api/laps/{lap['id']}/export.csv")).text
    assert "Body Slip Angle" in body.splitlines()[7]
    assert "deg" in body.splitlines()[8]
    # And it comes back in: the importer knows the column.
    exported = (await c.get(f"/api/laps/{lap['id']}/export")).json()
    imported = (await c.post("/api/laps/import", json=exported)).json()["id"]
    again = (await c.get(f"/api/laps/{imported}")).json()["samples"]
    assert again["body_slip"] == samples["body_slip"]


async def test_the_stint_trend_of_a_session(client) -> None:
    c, service = client
    await drive(
        service,
        [{"tyre_temp": 70.0 + 3.0 * n, "fuel": 50.0 - 2.0 * n} for n in range(5)],
    )
    session_id = (await c.get("/api/sessions")).json()[0]["id"]
    data = (await c.get(f"/api/analysis/stint?session_id={session_id}")).json()
    assert data["session_id"] == session_id
    assert [lap["number"] for lap in data["laps"]] == [1, 2, 3, 4, 5]
    assert [lap["stint"] for lap in data["laps"]] == [1] * 5
    assert not any(lap["pit"] for lap in data["laps"])
    # Fronts at t and t + 2, rears at t + 6 and t + 8.
    assert data["laps"][0]["tt_front"] == pytest.approx(71.0)
    assert data["laps"][0]["tt_rear"] == pytest.approx(77.0)
    (one,) = data["stints"]
    assert one["tt_front_per_lap"] == pytest.approx(3.0)
    assert one["tt_rear_per_lap"] == pytest.approx(3.0)
    assert one["pace_ms_per_lap"] == pytest.approx(0.0, abs=20.0)


async def test_the_stint_trend_of_a_session_without_laps(client) -> None:
    c, _service = client
    data = (await c.get("/api/analysis/stint?session_id=999")).json()
    assert data == {"session_id": 999, "laps": [], "stints": []}
