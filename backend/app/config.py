"""Application configuration via environment variables."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Files shipped inside the package, addressed from the package rather than from
# the working directory: a default that only works from one directory is a
# feature that silently does nothing everywhere else.
#
# Until #57 this pointed at backend/data — one directory ABOVE the package, so
# nothing under it shipped in a wheel at all (`packages.find` takes `app*`) and
# every default depended on the process being started from backend/. The
# read-only data now lives in app/data and is declared as package data in
# pyproject.toml, which is what makes `pip install ./backend` self-sufficient.
#
# db_path deliberately stays out: it is the user's recording, not shipped
# content, and writing it inside site-packages would be wrong on every install.
PACKAGE_DATA = Path(__file__).resolve().parent / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GT7_", env_file=".env", extra="ignore")

    # "udp" captures from a PlayStation, "sim" replays bundled sample data.
    source: str = "udp"
    ps_ip: str = ""  # empty -> broadcast auto-discovery
    heartbeat_port: int = 33739
    telemetry_port: int = 33740
    # Telemetry format requested from the console: "A", "B", "~", or "C".
    # "C" (game v1.68+) is the richest; older game versions only answer "A".
    packet_format: str = "C"

    # Simulated-source scenario: "practice" (default), "race", "fuel_shortage",
    # "overheating", "oil_pressure". See app.telemetry.simulator.SCENARIOS —
    # they exist to exercise Race Engineer callouts without a console.
    sim_scenario: str = "practice"

    db_path: Path = Path("data/gt7.db")
    # The car inventory: id -> name, manufacturer, year, category, drivetrain,
    # aspiration and the published figures (see scripts/build_car_metadata.py).
    # Shipped with the package so a fresh install names cars on its first
    # packet, then refreshed in the background against GT7's own list (#57).
    cars_json: Path = PACKAGE_DATA / "cars.json"
    # Pre-#57 two-column id,name CSV. Empty unless an existing install pinned
    # GT7_CARS_CSV, in which case it still wins and is still read — names only,
    # none of the richer fields. Kept for one release so an upgrade cannot
    # leave anybody with a config pointing at a file the app stopped reading.
    cars_csv: Path | None = None
    # Official GT7 track/layout metadata (see scripts/build_track_metadata.py)
    tracks_json: Path = PACKAGE_DATA / "tracks.json"
    # Pre-computed track signatures, generated in gt7-datalogger-track-data and
    # vendored here so a fresh install identifies circuits on its first packet
    # with no network (#58). Synced into the tracks table at startup whenever
    # the file's contents differ from what was last loaded; blank disables
    # seeding entirely and restores the pre-#58 behaviour of naming nothing
    # until the user does.
    track_signatures_json: Path = PACKAGE_DATA / "track-signatures.json"
    sample_lap: Path = Path("data/sample_lap.json")

    http_host: str = "0.0.0.0"
    http_port: int = 8000

    # When set, the Admin API and all mutating endpoints require this token
    # (X-API-Key header). Empty = fully open (LAN-trusted, the old behavior).
    admin_token: str = ""
    # Comma-separated origins allowed for cross-origin API use. Empty = no
    # CORS headers at all — the bundled UI is same-origin and needs none.
    cors_origins: str = ""

    # Client stream rate (Hz). Raw capture is ~60 Hz; the UI does not need all of it.
    ws_rate: int = 30

    log_level: str = "INFO"

    # A shared repository of contributed track bundles (#47): the URL of its
    # index document, or of the directory holding one ("/index.json" is
    # appended when the URL doesn't end in .json). Defaults to the project's
    # own data repo; empty hides the pull feature entirely. Nothing is fetched
    # until someone opens the Tracks view, and bundles pulled go through
    # exactly the same validation and voting merge as a hand-imported file.
    shared_bundles_url: str = "https://jbhoorasingh.github.io/gt7-datalogger-track-data"

    # Webhook for race notifications
    # (Discord webhook URLs get a rich embed; other URLs get plain JSON).
    webhook_url: str = ""
    # Comma-separated events to send; see app.notify.ALL_EVENTS.
    webhook_events: str = "personal_best,session_summary,overtake,position_lost,off_road"

    # Race Engineer voice callouts. Detection only runs while a browser has
    # registered itself as voice-capable, so leaving this on costs nothing
    # until someone presses "Enable Race Engineer".
    race_engineer: bool = True
    # The MOST any device may hear: "minimal", "race" or "coach". A browser
    # picks its own verbosity under this ceiling, so the default emits
    # everything and lets each device decide — a lower value here makes the
    # device setting silently unable to reach the categories it excludes.
    race_engineer_verbosity: str = "coach"
    # Comma-separated callout categories; see app.race_engineer.CATEGORIES.
    race_engineer_categories: str = (
        "system,lap,pace,race,position,fuel,strategy,engine,tires,chassis,coaching"
    )
    # Units spoken for distances and speeds ("metric" or "imperial"). Server
    # side because the callout text is worded before it reaches a browser.
    race_engineer_units: str = "metric"

    def car_inventory(self) -> Path:
        """The car file to read, most specific first.

        A pinned GT7_CARS_CSV wins: an install that set it before #57 meant
        "read cars from here", and that is still honoured — CarDatabase reads
        either shape, it just gets names only. Pydantic renders an empty
        environment variable as Path("."), so a blank GT7_CARS_CSV means unset
        rather than "load the current directory".

        Then a refreshed inventory if one has been written, then the one
        shipped in the package. That order is what makes the refresh visible
        without ever writing inside site-packages.
        """
        if self.cars_csv is not None and str(self.cars_csv) not in ("", "."):
            return self.cars_csv
        refreshed = self.refreshed_car_inventory()
        return refreshed if refreshed.exists() else self.cars_json

    def refreshed_car_inventory(self) -> Path:
        """Where a refresh writes: beside the database, with the user's data.

        An explicit GT7_CARS_JSON is taken as "this file is the inventory", so
        refreshes go there instead; otherwise the package copy stays pristine
        and the writable copy sits next to gt7.db.
        """
        if "cars_json" in self.model_fields_set:
            return self.cars_json
        return self.db_path.parent / "cars.json"

    def enabled_webhook_events(self) -> set[str]:
        from app.notify import parse_events

        return parse_events(self.webhook_events)

    def enabled_callout_categories(self) -> set[str]:
        from app.race_engineer import parse_categories

        return parse_categories(self.race_engineer_categories)


@lru_cache
def get_settings() -> Settings:
    return Settings()
