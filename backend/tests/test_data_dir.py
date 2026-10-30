"""One data folder, a frontend that is found or missed loudly, a log file (#178, #180)."""

from __future__ import annotations

import logging
import re
import sqlite3
import sys
from pathlib import Path

import pytest

from app import main
from app.config import PACKAGE_DATA, Settings
from app.logging_config import configure_logging
from app.storage.db import make_engine

APP = Path(__file__).resolve().parents[1] / "app"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("DATA_DIR", "DB_PATH", "FRONTEND_DIST", "LOG_FILE", "CARS_JSON"):
        monkeypatch.delenv(f"GT7_{var}", raising=False)


def test_without_a_data_dir_everything_is_where_it_always_was() -> None:
    settings = Settings(_env_file=None)
    assert settings.db_path == Path("data/gt7.db")
    assert settings.data_root() == Path("data")


def test_data_dir_moves_the_database_and_everything_beside_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GT7_DATA_DIR", str(tmp_path))
    settings = Settings(_env_file=None)
    assert settings.db_path == tmp_path / "gt7.db"
    assert settings.data_root() == tmp_path
    assert settings.refreshed_car_inventory() == tmp_path / "cars.json"


def test_an_explicit_db_path_still_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GT7_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GT7_DB_PATH", str(tmp_path / "elsewhere.db"))
    settings = Settings(_env_file=None)
    assert settings.db_path == tmp_path / "elsewhere.db"
    assert settings.data_root() == tmp_path / "data"


def test_a_blank_data_dir_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GT7_DATA_DIR", "")
    settings = Settings(_env_file=None)
    assert settings.data_dir is None
    assert settings.db_path == Path("data/gt7.db")


def test_nothing_recomputes_the_data_folder_from_the_database_path() -> None:
    """data_root() is the one answer; db_path.parent sites drifted apart once."""
    offenders = [
        f"{path.relative_to(APP)}:{n}"
        for path in APP.rglob("*.py")
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"db_path\.parent", line)
        # The definition itself, and make_engine creating the file's own folder.
        and path.name not in ("config.py", "db.py")
    ]
    assert offenders == []


def test_the_car_refresh_never_writes_into_the_package(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, cars_json=PACKAGE_DATA / "cars.json", data_dir=tmp_path)
    assert settings.refreshed_car_inventory() == tmp_path / "cars.json"
    pinned = tmp_path / "mine.json"
    assert Settings(_env_file=None, cars_json=pinned).refreshed_car_inventory() == pinned


async def test_a_database_under_an_awkward_folder_name_opens(tmp_path: Path) -> None:
    # "?" cannot be in a Windows file name at all; "#" and "%" can.
    name = "100%20 Racing#é" if sys.platform == "win32" else "100%20 Racing?#é"
    db = tmp_path / name / "gt7.db"
    engine = make_engine(db)
    async with engine.begin() as conn:
        await conn.exec_driver_sql("CREATE TABLE t (x INTEGER)")
    await engine.dispose()
    assert sqlite3.connect(db).execute("SELECT name FROM sqlite_master").fetchall() == [("t",)]


def _dist(root: Path) -> Path:
    (root / "index.html").parent.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text("<!doctype html>")
    return root


def test_frontend_dist_honours_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dist = _dist(tmp_path / "ui")
    monkeypatch.setenv("GT7_FRONTEND_DIST", str(dist))
    assert main.frontend_dist() == dist


def test_frontend_dist_finds_the_frozen_bundles_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import app.config as config

    dist = _dist(tmp_path / "frontend" / "dist")
    monkeypatch.setattr(config.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(config, "REPO_FRONTEND_DIST", tmp_path / "nowhere")
    assert main.frontend_dist() == dist


def test_a_configured_frontend_that_is_missing_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import app.config as config

    monkeypatch.setenv("GT7_FRONTEND_DIST", str(tmp_path / "missing"))
    monkeypatch.setattr(config, "REPO_FRONTEND_DIST", tmp_path / "nowhere")
    with caplog.at_level(logging.INFO):
        assert main.frontend_dist() is None
    assert [r.levelname for r in caplog.records if "no built frontend" in r.message] == ["ERROR"]


def test_the_log_file_gets_app_and_uvicorn_lines(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "gt7.log"
    uvicorn_logger = logging.getLogger("uvicorn")
    was = uvicorn_logger.propagate, list(uvicorn_logger.handlers)
    uvicorn_logger.propagate = False  # as uvicorn's default config leaves it
    try:
        configure_logging("INFO", log_file)
        configure_logging("INFO", log_file)  # twice: no doubled lines
        logging.getLogger("app").info("hello from the app")
        logging.getLogger("uvicorn.error").error("[Errno 48] address already in use")
        for handler in logging.getLogger().handlers + uvicorn_logger.handlers:
            handler.flush()
        text = log_file.read_text()
        assert text.count("hello from the app") == 1
        assert text.count("address already in use") == 1
    finally:
        uvicorn_logger.propagate, uvicorn_logger.handlers[:] = was[0], was[1]
        configure_logging("INFO")
