"""Every setting in one table: what it is, its default, and how it applies.

`app.config.Settings` declares the fields and reads the environment; this
module says everything else about each one — the group it shows under in
Settings › Advanced, whether a change applies live or at the next start,
whether Admin may change it at all, whether it is a secret, its bounds, and
the sentence the documentation prints for it. The configuration reference
table (docs/getting-started/configuration.md) is generated from here by
scripts/gen_config_docs.py, and a test fails when a Settings field has no
entry or the table has drifted, so a new setting cannot ship undocumented or
unreachable from Admin.

Values persist in the `settings` table as strings and override the
environment at the next start (`apply_stored`); "reset to default" deletes
the row, which hands the setting back to the environment, or to the shipped
default when the environment does not set it either.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import TypeAdapter, ValidationError

from app.config import Settings
from app.notify import ALL_EVENTS
from app.race_engineer.models import CATEGORIES, VERBOSITY_MODES
from app.sync.connection import mask_token
from app.telemetry.simulator import SCENARIOS

log = logging.getLogger(__name__)

Apply = Literal["live", "restart", "env"]
Kind = Literal["str", "int", "float", "bool", "path", "list"]

GROUPS = (
    "Connection",
    "Live stream",
    "Race Engineer",
    "Notifications",
    "Sync",
    "Tracks & cars",
    "Server",
)


@dataclass(frozen=True, slots=True)
class Entry:
    key: str
    group: str
    label: str
    # Markdown: the documentation's sentence for this setting. The UI shows
    # it with links reduced to their text.
    doc: str
    # "live" applies the moment it is saved, "restart" is stored and takes
    # effect at the next start, "env" is read before the database is opened
    # (or IS the database) and can only be set in the environment.
    apply: Apply = "live"
    kind: Kind = "str"
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    secret: bool = False
    # What the docs table prints as the default, when the literal default
    # would mislead (a path inside site-packages, an empty list meaning all).
    doc_default: str = ""

    @property
    def env(self) -> str:
        return f"GT7_{self.key.upper()}"

    @property
    def editable(self) -> bool:
        return self.apply != "env"


ENTRIES: tuple[Entry, ...] = (
    # --- Connection -----------------------------------------------------------
    Entry(
        "source", "Connection", "Telemetry source",
        "`udp` (PlayStation) or `sim` (simulated laps)",
        choices=("udp", "sim"),
    ),
    Entry(
        "ps_ip", "Connection", "Console IP",
        "Console IP; empty = broadcast auto-discovery",
    ),
    Entry(
        "packet_format", "Connection", "Packet format",
        "Telemetry format requested from the console: `A`, `B`, `~`, or `C` "
        "(richest, needs GT7 v1.68+)",
        choices=("A", "B", "~", "C"),
    ),
    Entry(
        "telemetry_port", "Connection", "Telemetry port",
        "Inbound telemetry UDP port. Changing it restarts the listener",
        kind="int", minimum=1, maximum=65535,
    ),
    Entry(
        "heartbeat_port", "Connection", "Heartbeat port",
        "Outbound heartbeat UDP port (the console listens on it). Changing it "
        "restarts the listener",
        kind="int", minimum=1, maximum=65535,
    ),
    Entry(
        "sim_scenario", "Connection", "Simulator scenario",
        "With `GT7_SOURCE=sim`: `practice`, `race`, `fuel_shortage`, `overheating`, "
        "`oil_pressure` — staged situations for testing callouts — and "
        "`leader_replay`: pre-roll, one flying lap streamed as lap 0 with a running "
        "packet-C lap clock, then `LOADING`, which exercises the "
        "[replay-salvage path](../internals/lap-detection.md#replay-salvage) without "
        "a console. Changing it restarts the simulator",
        choices=tuple(SCENARIOS),
    ),
    # --- Live stream ----------------------------------------------------------
    Entry(
        "ws_rate", "Live stream", "Live stream rate",
        "Live stream rate (Hz) for clients that do not ask for their own — the "
        "browser UI. Capture stays at ~60 Hz; a client can request up to 60 Hz in "
        "its [hello](../reference/client-protocol.md)",
        kind="int", minimum=1, maximum=60,
    ),
    Entry(
        "mdns", "Live stream", "Announce on the LAN",
        "Advertise this server over mDNS as `_gt7dl._tcp` so the phone app finds it "
        "without an address. Multicast does not leave a Docker bridge network: in a "
        "container it needs `network_mode: host`",
        kind="bool",
    ),
    Entry(
        "cors_origins", "Live stream", "CORS origins",
        "Comma-separated origins allowed for cross-origin API use. Empty sends no "
        "CORS headers — the bundled UI is same-origin and needs none, and so does a "
        "native app",
    ),
    # --- Race Engineer --------------------------------------------------------
    Entry(
        "race_engineer", "Race Engineer", "Race Engineer",
        "Generate voice callouts. Detection only runs while a browser has voice "
        "enabled, so leaving this on costs nothing",
        kind="bool",
    ),
    Entry(
        "race_engineer_verbosity", "Race Engineer", "Verbosity ceiling",
        "The **most** any device may hear: `minimal`, `race` or `coach`. Each "
        "browser chooses its own verbosity under this ceiling, so the default "
        "produces everything and lets the device decide; lowering it puts those "
        "categories out of reach for every device",
        choices=VERBOSITY_MODES,
    ),
    Entry(
        "race_engineer_categories", "Race Engineer", "Callout categories",
        "Comma-separated callout categories: " + ", ".join(f"`{c}`" for c in CATEGORIES),
        kind="list", choices=CATEGORIES, doc_default="*(all)*",
    ),
    Entry(
        "race_engineer_units", "Race Engineer", "Spoken units",
        "Units spoken inside callouts (`metric` = meters and km/h, `imperial` = "
        "feet and mph)",
        choices=("metric", "imperial"),
    ),
    Entry(
        "race_engineer_sector_voice_enabled", "Race Engineer",
        "Sector voice announcements",
        "Announce sectors against the active reference lap (requires authored boundaries). "
        "Also enables configurable precision for completed-lap announcements; "
        "disabling restores original lap voice formatting.",
        kind="bool",
    ),
    Entry(
        "race_engineer_sector_voice_decimals", "Race Engineer",
        "Sector voice decimal places",
        "Number of decimal places spoken for sector times, completed laps, personal bests "
        "and their timing differences (1 to 3), when sector voice is enabled.",
        kind="int", minimum=1, maximum=3,
    ),
    Entry(
        "race_engineer_sector_voice_rounding", "Race Engineer",
        "Sector voice rounding",
        "Round or truncate spoken sector times, completed-lap times, personal bests "
        "and their timing differences when sector voice is enabled.",
        choices=("round", "truncate"),
    ),
    Entry(
        "race_engineer_sector_voice_content", "Race Engineer",
        "Sector announcement content",
        "Announce the reference delta, sector time, or both.",
        choices=("delta", "time", "both"),
    ),
    Entry(
        "race_engineer_sector_voice_comparison", "Race Engineer",
        "Sector comparison mode",
        "Compare the individual sector or cumulative lap progress.",
        choices=("individual", "cumulative"),
    ),
    # --- Notifications --------------------------------------------------------
    Entry(
        "webhook_url", "Notifications", "Webhook URL",
        "Webhook for race notifications. Discord webhook URLs get a rich embed; "
        "any other URL gets plain JSON",
    ),
    Entry(
        "webhook_events", "Notifications", "Webhook events",
        "Comma-separated events to send: " + ", ".join(f"`{e}`" for e in ALL_EVENTS),
        kind="list", choices=ALL_EVENTS, doc_default="*(all)*",
    ),
    # --- Sync -----------------------------------------------------------------
    Entry(
        "sync_url", "Sync", "Sync server",
        "The [sync service](../guide/settings.md#sync) this installation contributes "
        "to. Settings › Sync also takes a whole connection string here",
    ),
    Entry(
        "sync_token", "Sync", "Sync token",
        "The account token for the sync service. A secret: masked in the UI, sent "
        "only as a `Bearer` header, never logged",
        secret=True,
    ),
    Entry(
        "sync_enabled", "Sync", "Sync",
        "The master switch for sync. On its own it sends nothing — each data type "
        "has its own toggle below",
        kind="bool",
    ),
    Entry(
        "sync_tracks", "Sync", "Sync tracks",
        "Upload survey bundles of circuits with a confirmed official layout, once a "
        "changed bundle has been left alone for ten minutes. Flipped off "
        "automatically if the server stops accepting tracks",
        kind="bool",
    ),
    Entry(
        "sync_sessions", "Sync", "Sync sessions",
        "Send your own laps as you drive — each lap as the export document, against "
        "a session the service opens with the first lap — and the totals when the "
        "drive ends. Laps queue in `data/sync-sessions.json` while the service is "
        "away. Private on the service by default. Flipped off automatically if the "
        "server stops accepting sessions",
        kind="bool",
    ),
    Entry(
        "sync_live", "Sync", "Sync live",
        "Hold one WebSocket to the service while the car is on track and send where "
        "it is a few times a second (position, speed, gear, lap, lap time) for the "
        "service's spectate page. Opens on the first on-track packet, closes after "
        "five minutes without one. Flipped off automatically if the server stops "
        "accepting live",
        kind="bool",
    ),
    Entry(
        "sync_live_hz", "Sync", "Sync live rate",
        "Frames per second for the live stream. The server states its own ceiling "
        "when the stream connects and the lower of the two wins. Applies from the "
        "next connection",
        kind="float", minimum=0.5, maximum=60,
    ),
    # --- Tracks & cars --------------------------------------------------------
    Entry(
        "shared_bundles_url", "Tracks & cars", "Shared track repository",
        "Shared repository of contributed track bundles: the URL of its "
        "`index.json`, or of the directory holding one. The Tracks view lists what "
        "it offers and can pull a bundle straight in (same validation and voting "
        "merge as a file import). A fresh installation (no bundles yet) pulls every "
        "circuit once on its first start; otherwise nothing is fetched until the "
        "Tracks view is opened. Empty turns pulling off, the first-start pull "
        "included",
        doc_default="*(the project's [track-data repo]"
        "(https://jbhoorasingh.github.io/gt7-datalogger-track-data/))*",
    ),
    Entry(
        "tracks_json", "Tracks & cars", "Track metadata",
        "Official GT7 track and layout metadata",
        kind="path", doc_default="*(bundled)*",
    ),
    Entry(
        "track_signatures_json", "Tracks & cars", "Track signatures",
        "Shipped track signatures, synced into the `tracks` table at startup so a "
        "fresh install [identifies circuits]"
        "(../internals/track-identification.md#the-shipped-signatures) it has never "
        "seen driven. Defaults to the copy inside the package. Set it blank to turn "
        "seeding off and identify only what you have named and surveyed",
        apply="restart", kind="path", doc_default="*(bundled)*",
    ),
    Entry(
        "cars_json", "Tracks & cars", "Car inventory",
        "Car inventory: id → name, manufacturer, year, category, drivetrain, "
        "aspiration and the published figures. Resolves from the package, so it "
        "works whatever directory you start from",
        apply="restart", kind="path", doc_default="*(bundled)*",
    ),
    Entry(
        "cars_csv", "Tracks & cars", "Legacy car CSV",
        "Deprecated pre-0.6 `id,name` CSV. Set = read cars from here instead, names "
        "only. Kept for one release",
        apply="restart", kind="path", doc_default="*(empty)*",
    ),
    # --- Server ---------------------------------------------------------------
    Entry(
        "log_level", "Server", "Log level",
        "Root log level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    ),
    Entry(
        "admin_token", "Server", "Admin token",
        "When set, the Settings pages and all destructive/mutating API calls require "
        "this token via the `X-API-Key` header; overlay/dash/read endpoints and the "
        "live WebSocket stay open. Empty = fully open (LAN-trusted)",
        secret=True,
    ),
    Entry(
        "http_host", "Server", "HTTP bind host",
        "HTTP bind host. Read before the database opens, so environment only",
        apply="env",
    ),
    Entry(
        "http_port", "Server", "HTTP port",
        "HTTP port. Read before the database opens — and a Docker install maps it "
        "from outside — so environment only",
        apply="env", kind="int", minimum=1, maximum=65535,
    ),
    Entry(
        "db_path", "Server", "Database",
        "SQLite database path — also accepts a full SQLAlchemy async URL (e.g. "
        "Postgres). The database holds the other settings, so environment only",
        apply="env", kind="path",
    ),
)

BY_KEY: dict[str, Entry] = {e.key: e for e in ENTRIES}


class SettingError(ValueError):
    """A value Admin may not store: wrong type, out of bounds, or read-only."""


# --- values ------------------------------------------------------------------
#
# Three shapes of one value: the Settings attribute (lists are comma strings
# there, as the environment spells them), the API value (lists are lists,
# paths and None are strings), and the stored string.


def _field_type(key: str) -> Any:
    return Settings.model_fields[key].annotation


def to_api(entry: Entry, value: Any) -> Any:
    if entry.kind == "list":
        return [v.strip() for v in str(value or "").split(",") if v.strip()]
    if value is None:
        return ""
    if isinstance(value, Path):
        return str(value)
    return value


def coerce(entry: Entry, value: Any) -> Any:
    """An API value as the Settings attribute, validated. Raises SettingError."""
    if entry.kind == "list":
        if isinstance(value, str):
            value = [v for v in value.split(",") if v.strip()]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise SettingError(f"{entry.key}: expected a list of names")
        items = [v.strip() for v in value]
        unknown = [v for v in items if v not in entry.choices]
        if unknown:
            raise SettingError(f"{entry.key}: unknown {', '.join(unknown)}")
        return ",".join(dict.fromkeys(items))
    if entry.key == "cars_csv" and value in (None, ""):
        return None
    if entry.kind == "bool" and isinstance(value, str):
        if value.lower() not in ("true", "false"):
            raise SettingError(f"{entry.key}: expected true or false")
        value = value.lower() == "true"
    try:
        out = TypeAdapter(_field_type(entry.key)).validate_python(value)
    except ValidationError as exc:
        raise SettingError(f"{entry.key}: {exc.errors()[0]['msg']}") from exc
    if entry.choices and out not in entry.choices:
        raise SettingError(f"{entry.key}: must be one of {', '.join(entry.choices)}")
    if entry.minimum is not None and out < entry.minimum:
        raise SettingError(f"{entry.key}: must be at least {entry.minimum:g}")
    if entry.maximum is not None and out > entry.maximum:
        raise SettingError(f"{entry.key}: must be at most {entry.maximum:g}")
    return out


def to_store(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if value is None:
        return ""
    return str(value)


def from_store(entry: Entry, text: str) -> Any:
    """A stored string as the Settings attribute. Raises SettingError."""
    return coerce(entry, text)


# --- startup ----------------------------------------------------------------


def apply_stored(settings: Settings, stored: dict[str, str]) -> None:
    """Overlay what Admin saved onto the environment's values, at startup.

    A stored value that no longer validates (a scenario renamed, a format
    dropped) is skipped with a warning rather than stopping the app: the
    environment's value stands, and saving the setting again replaces it.
    """
    for entry in ENTRIES:
        if not entry.editable or entry.key not in stored:
            continue
        text = stored[entry.key]
        # An empty stored address means "the default server", which is what
        # the field already holds.
        if entry.key == "sync_url" and not text:
            continue
        try:
            value = from_store(entry, text)
        except SettingError as exc:
            log.warning("ignoring stored setting %s", exc)
            continue
        setattr(settings, entry.key, value)
        if entry.key == "log_level":
            logging.getLogger().setLevel(value)


# --- describing -------------------------------------------------------------


def default_of(key: str) -> Any:
    return Settings.model_fields[key].default


def describe(
    entry: Entry,
    running: Settings,
    baseline: Settings,
    stored: dict[str, str],
) -> dict[str, Any]:
    """One setting as Settings › Advanced shows it.

    `running` is what the server is using now, `baseline` what the
    environment said at startup (what a reset falls back to), `stored` the
    saved overrides.
    """
    value = getattr(running, entry.key)
    if entry.key in stored:
        source = "saved"
    elif entry.key in baseline.model_fields_set:
        source = "env"
    else:
        source = "default"
    # A restart setting saved since startup: stored, not yet in effect.
    pending = False
    if entry.apply == "restart":
        upcoming = baseline_value = getattr(baseline, entry.key)
        if entry.key in stored:
            try:
                upcoming = from_store(entry, stored[entry.key])
            except SettingError:
                upcoming = baseline_value
        pending = upcoming != value
    out: dict[str, Any] = {
        "key": entry.key,
        "env": entry.env,
        "group": entry.group,
        "label": entry.label,
        "doc": entry.doc,
        "kind": entry.kind,
        "apply": entry.apply,
        "editable": entry.editable,
        "secret": entry.secret,
        "source": source,
        "restart_pending": pending,
    }
    if entry.choices:
        out["choices"] = list(entry.choices)
    if entry.minimum is not None:
        out["min"] = entry.minimum
    if entry.maximum is not None:
        out["max"] = entry.maximum
    if entry.secret:
        # Never the secret itself: whether one is set, and a hint of which.
        out["value"] = None
        out["is_set"] = bool(value)
        out["hint"] = mask_token(str(value or ""))
        out["default"] = None
    else:
        out["value"] = to_api(entry, value)
        out["default"] = to_api(entry, default_of(entry.key))
        # What Reset hands back: the environment's value where it sets one,
        # which is not always the shipped default.
        out["reset_value"] = to_api(entry, getattr(baseline, entry.key))
    return out


# --- documentation ----------------------------------------------------------

DOCS_START = "<!-- settings-table:start (generated by backend/scripts/gen_config_docs.py) -->"
DOCS_END = "<!-- settings-table:end -->"

_APPLY_WORDS = {
    "live": "live",
    "restart": "next start",
    "env": "environment only",
}


def _doc_default(entry: Entry) -> str:
    if entry.doc_default:
        return entry.doc_default
    if entry.secret:
        return "*(empty)*"
    value = to_api(entry, default_of(entry.key))
    if value in ("", []):
        return "*(empty)*"
    if isinstance(value, bool):
        value = str(value).lower()
    return f"`{value}`"


def render_markdown() -> str:
    """The configuration reference tables, one per group."""
    out = [DOCS_START, ""]
    for group in GROUPS:
        out += [
            f"### {group}",
            "",
            "| Variable | Default | Applies | Description |",
            "| --- | --- | --- | --- |",
        ]
        for e in (e for e in ENTRIES if e.group == group):
            doc = e.doc.replace("|", "\\|")
            out.append(f"| `{e.env}` | {_doc_default(e)} | {_APPLY_WORDS[e.apply]} | {doc} |")
        out.append("")
    out.append(DOCS_END)
    return "\n".join(out)


def splice_docs(text: str) -> str:
    """`text` (configuration.md) with its generated section replaced."""
    start, end = text.index(DOCS_START), text.index(DOCS_END) + len(DOCS_END)
    return text[:start] + render_markdown() + text[end:]
