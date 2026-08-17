"""Application configuration via environment variables."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    cars_csv: Path = Path("data/cars.csv")
    # Official GT7 track/layout metadata (see scripts/build_track_metadata.py)
    tracks_json: Path = Path("data/tracks.json")
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

    def enabled_webhook_events(self) -> set[str]:
        from app.notify import parse_events

        return parse_events(self.webhook_events)

    def enabled_callout_categories(self) -> set[str]:
        from app.race_engineer import parse_categories

        return parse_categories(self.race_engineer_categories)


@lru_cache
def get_settings() -> Settings:
    return Settings()
