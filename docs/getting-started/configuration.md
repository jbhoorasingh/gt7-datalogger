# Configuration

Most day-to-day settings — the console IP, telemetry source, log level, and webhook URL —
can be changed **at runtime from the Admin view** with no restart. Those values persist
in the database and override the environment on the next start.

Everything else is configured with environment variables, or a `.env` file in the
working directory.

## Environment variables

| Variable | Default | Description |
| --- | --- | --- |
| `GT7_SOURCE` | `udp` | `udp` (PlayStation) or `sim` (simulated laps) |
| `GT7_PS_IP` | *(empty)* | Console IP; empty = broadcast auto-discovery |
| `GT7_PACKET_FORMAT` | `C` | Telemetry format requested from the console: `A`, `B`, `~`, or `C` (richest, needs GT7 v1.68+; also settable in Admin) |
| `GT7_DB_PATH` | `data/gt7.db` | SQLite database path — also accepts a full SQLAlchemy async URL (e.g. Postgres) |
| `GT7_CARS_JSON` | *(bundled)* | Car inventory: id → name, manufacturer, year, category, drivetrain, aspiration and the published figures. Resolves from the package, so it works whatever directory you start from |
| `GT7_CARS_CSV` | *(empty)* | Deprecated pre-0.6 `id,name` CSV. Set = read cars from here instead, names only. Kept for one release |
| `GT7_TRACK_SIGNATURES_JSON` | *(bundled)* | Shipped track signatures, synced into the `tracks` table at startup so a fresh install [identifies circuits](../internals/track-identification.md#the-shipped-signatures) it has never seen driven. Defaults to the copy inside the package, so it resolves whatever directory the app was started from. Set it blank to turn seeding off and identify only what you have named and surveyed |
| `GT7_WS_RATE` | `30` | Live stream rate to the browser (Hz); capture stays at ~60 Hz |
| `GT7_WEBHOOK_URL` | *(empty)* | Webhook for race notifications (also settable in Admin) |
| `GT7_WEBHOOK_EVENTS` | *(all)* | Comma-separated events to send: `personal_best`, `session_summary`, `overtake`, `position_lost`, `off_road` (toggles in Admin) |
| `GT7_RACE_ENGINEER` | `true` | Generate voice callouts. Detection only runs while a browser has voice enabled, so leaving this on costs nothing (also settable in Admin) |
| `GT7_RACE_ENGINEER_VERBOSITY` | `coach` | The **most** any device may hear: `minimal`, `race` or `coach`. Each browser chooses its own verbosity under this ceiling, so the default produces everything and lets the device decide; lowering it puts those categories out of reach for every device |
| `GT7_RACE_ENGINEER_CATEGORIES` | *(all)* | Comma-separated callout categories: `system`, `lap`, `pace`, `race`, `position`, `fuel`, `strategy`, `engine`, `tires`, `chassis`, `coaching` |
| `GT7_RACE_ENGINEER_UNITS` | `metric` | Units spoken inside callouts (`metric` = meters and km/h, `imperial` = feet and mph) |
| `GT7_SIM_SCENARIO` | `practice` | With `GT7_SOURCE=sim`: `practice`, `race`, `fuel_shortage`, `overheating`, `oil_pressure` — staged situations for testing callouts — and `leader_replay`: pre-roll, one flying lap streamed as lap 0 with a running packet-C lap clock, then `LOADING`, which exercises the [replay-salvage path](../internals/lap-detection.md#replay-salvage) without a console |
| `GT7_SHARED_BUNDLES_URL` | *(the project's [track-data repo](https://jbhoorasingh.github.io/gt7-datalogger-track-data/))* | Shared repository of contributed track bundles: the URL of its `index.json`, or of the directory holding one. The Tracks view lists what it offers and can pull a bundle straight in (same validation and voting merge as a file import). Empty hides the feature; nothing is fetched until the Tracks view is opened |
| `GT7_LOG_LEVEL` | `INFO` | Root log level (also settable in Admin) |
| `GT7_ADMIN_TOKEN` | *(empty)* | When set, the Admin pages and all destructive/mutating API calls require this token via the `X-API-Key` header; overlay/dash/read endpoints stay open. Empty = fully open (LAN-trusted) |
| `GT7_CORS_ORIGINS` | *(empty)* | Comma-separated origins allowed for cross-origin API use. Empty (default) sends no CORS headers — the bundled UI is same-origin and needs none |
| `GT7_HTTP_HOST` | `0.0.0.0` | HTTP bind host |
| `GT7_HTTP_PORT` | `8000` | HTTP port |
| `GT7_TELEMETRY_PORT` | `33740` | Inbound telemetry UDP port |
| `GT7_HEARTBEAT_PORT` | `33739` | Outbound heartbeat UDP port |

!!! note "Precedence"
    Settings changed in the **Admin** view are persisted to the database and take
    precedence over environment variables on subsequent starts.

## The car database

Telemetry identifies the car by a numeric ID. The inventory that maps those IDs ships with
the app — every car GT7 publishes, with its manufacturer, model year, Gr. category,
drivetrain, aspiration and published figures — so names work on the first packet with no
network at all.

After a GT7 content update adds cars, the app picks them up on its own: it checks GT7's own
car list in the background on first run and weekly after that. A failed check is not an
error — you keep the bundled inventory and it tries again later.

To refresh immediately instead of waiting:

- from the UI: **Admin → Update car database**, or
- from the command line:

```bash
python backend/scripts/update_cars.py
```

That writes `cars.json` next to your database, which the app prefers over the bundled copy
from then on. `python backend/scripts/build_car_metadata.py` is the other half of this: it
regenerates the copy committed to the repository, for cutting a release.

Cars GT7 stops publishing are never dropped — a refresh only ever adds and updates, so
sessions you recorded years ago keep their car names.

## Units & browser settings

Display units (km/h vs mph) and other UI preferences are set from the dashboard itself
and persist in the browser's local storage — they are per-device, not server-side.

## Notifications

Set a webhook URL (environment variable or **Admin** view) to get:

- **New personal best** notifications as they happen
- **End-of-session summaries**
- **Overtakes** and **positions lost** (in race types where GT7 reports live positions)
- **Off-road excursions** (requires packet format C)

Every event type has its own toggle in the Admin view (`GT7_WEBHOOK_EVENTS` via env).
Discord webhook URLs receive a rich embed; any other URL receives plain JSON.
See [Admin view](../guide/admin.md) for details.

## Race Engineer voice

Spoken callouts are generated on the server and **played by the browser** — no audio
device, speaker, or text-to-speech package is needed on the Raspberry Pi, NAS, or
Docker host. Server-side settings (feature switch, verbosity, categories) live in the
Admin view or the environment variables above; voice, volume, rate and per-category
toggles are per-device browser settings. See [Race Engineer](../guide/race-engineer.md).
