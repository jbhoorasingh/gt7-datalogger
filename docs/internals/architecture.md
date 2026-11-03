# Architecture

```
PlayStation (GT7) ──UDP 33740──▶ Telemetry service (FastAPI, Python 3.12)
       ▲                          ├─ Salsa20 decrypt + typed packet parser
       └──heartbeat 33739──────── ├─ Lap detection, session grouping, metrics
                                  ├─ SQLite storage (SQLAlchemy async)
                                  ├─ REST API  (/api/…)  – history & analysis
                                  └─ WebSocket (/ws/live) – 30 Hz live stream
                                              │
                                              ▼
                                  React 18 + TypeScript + Vite + Tailwind
                                  (ECharts for high-frequency chart rendering)
```

## One process, one event loop

The whole backend is a **single-process, single-threaded asyncio** application
(FastAPI + uvicorn). There are no worker threads or subprocesses. The data flow is:

```
UdpTelemetrySource (or SimTelemetrySource)
        │  decrypt + parse (~60 Hz)
        ▼
   asyncio.Queue (600 packets ≈ 10 s buffer)
        │  single consumer task — strictly ordered
        ▼
TelemetryService._on_packet
        ├──▶ LapProcessor.feed  ──▶ lap/session callbacks ──▶ SQLite (async)
        └──▶ WebSocket broadcast (rate-limited to GT7_WS_RATE, default 30 Hz)
```

Design points worth knowing:

- **Strict ordering** — exactly one consumer task drains the packet queue, so lap
  detection never runs concurrently with itself. Overlapping feeds could duplicate a lap
  save while a database write is in flight.
- **Backpressure that favors freshness** — if the queue fills (e.g. a slow disk stalls a
  write), the *oldest* packet is dropped and counted in `packets_dropped`, so the live
  view always shows current data.
- **Commit before await** — inside the lap processor, all state transitions (lap counter,
  sample buffer, fuel/engine aggregates) are committed *before* any `await`. At 60 Hz, a
  lap boundary that stayed "open" across an await would re-trigger and duplicate laps.

## Why FastAPI?

Native async fits a 60 Hz UDP stream plus WebSocket fan-out in one process; Pydantic
gives typed models and settings; and the Python ecosystem has mature Salsa20 support
(`pycryptodome`).

## Storage

SQLite via SQLAlchemy 2.x **async** engine (`sqlite+aiosqlite`). Because the storage
layer only sees a SQLAlchemy URL, SQLite can be swapped for Postgres
(`postgresql+asyncpg://…`) by changing `GT7_DB_PATH` — no code changes.

Five tables:

| Table | Contents |
| --- | --- |
| `sessions` | one row per driving session: start time, car ID/name/category, note, track name |
| `laps` | one row per lap: timing, per-lap metrics, detected events (JSON), gearing (JSON), and the **full 60 Hz sample series** (JSON) |
| `tracks` | named track signatures for auto-identification (length + bounding box) |
| `layouts` | saved overlay/dashboard grid configs (JSON) |
| `settings` | runtime overrides set from the Settings view (console IP, source, log level, webhook URL) |

### Schema evolution

Migrations are **Alembic** revisions under `backend/app/migrations/`, run to head on
every startup by `init_db`. The app builds its Alembic config in Python and passes the
already-open connection through `AsyncConnection.run_sync`, so nothing opens a second
writer against the SQLite file; `backend/alembic.ini` exists only for the CLI
(`alembic revision --autogenerate`).

Before it upgrades anything, `init_db` (#182):

- **Refuses a database from a newer version.** A revision the script directory does not
  know raises `DatabaseTooNew`, and the file is left alone. Every upgrade records the
  version that ran it in the `schema_written_by` settings row, so the refusal names the
  version to update to; the lifespan logs it as one line and the server exits with code 3,
  without a traceback.
- **Copies the database** when an upgrade is due on an existing file:
  `<data>/backups/pre-upgrade-<UTC>-<revision>/` holds `gt7.db`, copied with SQLite's
  backup API, and a `manifest.json` (app version, from and to revision, row counts,
  `quick_check`). It is written as `.partial` and renamed once the manifest is in it.
  With less than 2.2 times the database's size free, nothing is upgraded
  (`NoRoomForBackup`). One copy is kept, three on the desktop app (`GT7_DESKTOP=1`).
- **Upgrades in one transaction**, on a migration engine of its own
  (`make_migration_engine`: no pool, and SQLAlchemy's pysqlite/aiosqlite recipe, so
  BEGIN is really issued before DDL). A revision that fails rolls the whole upgrade
  back, earlier revisions in the same start included. The app's engine keeps the
  driver's own transaction handling, and Compact runs `VACUUM` on a `sqlite3`
  connection of its own.

Every connection sets `busy_timeout` to 30 s and keeps the rollback journal.
`foreign_keys` stays off: with it on, 0011's rebuild of `sessions` cascades into
deleting every lap.

Before Alembic, schema changes were a hand-rolled list of `ALTER TABLE ADD COLUMN`
statements applied at startup — which could only ever *add* columns, and grew by one
entry per feature. That list survives, frozen, for exactly one job: a database predating
migrations is brought up to the baseline revision by it and then **stamped**, so an
existing install and a fresh one converge on the same schema and everything after that
is an ordinary revision. Every recorded lap survives the transition; see
`backend/tests/test_migrations.py`, which asserts it against a first-release database.

To add a column: change the ORM model, then

```bash
cd backend && .venv/bin/alembic revision --autogenerate -m "add whatever"
```

review the generated file (SQLite cannot alter a column in place — `render_as_batch` is
on, which rewrites the table instead) and commit it. Keep one head: two make
`upgrade head` ambiguous, and a test guards against it.

**No downsampling on write**: every 60 Hz tick of a lap is stored. Downsampling happens
at *read* time — the analysis endpoints resample onto a uniform distance grid (default
5 m steps), and the live WebSocket stream is capped at `GT7_WS_RATE` (default 30 Hz).

## Frontend

React 18 + TypeScript, built with Vite, styled with Tailwind. High-frequency charts
(the stacked analysis panels, race line map, sparklines) render with **ECharts**, which
handles thousands of points per series without breaking a sweat. State lives in small
stores (settings, telemetry, analysis); the current analysis view is fully encoded in
the URL hash so links reproduce exact comparisons.

In production the backend serves the built frontend from `frontend/dist` — one container,
one port. The desktop app serves the copy frozen inside it (`GT7_FRONTEND_DIST`), the
same way. `GET /overlay` returns the SPA directly (a plain path, because some streaming
apps reject URLs with `#fragment`s).

## The desktop app

The [desktop app](../getting-started/desktop.md) is the same server, frozen with
PyInstaller into one folder with its own CPython. There is no webview: the UI is the
user's own browser at the server's address. One executable runs in three modes:

- **The app** (no options) is a small **supervisor** with no event loop of its own. It
  holds the single-instance lock (`gt7.lock` in the data folder), binds the
  dashboard's port, owns the tray (`desktop/gt7_desktop/tray/`) and starts the
  server as a child: the same executable with `--server`.
- **`--server`** (`backend/app/serve.py`) runs uvicorn on its main thread with the
  socket the supervisor handed down, exactly as Docker runs it. It holds
  `gt7-server.lock` so a second server never opens the same database, and it stops
  when its parent goes away. Exit code 75 asks the supervisor for a fresh child, to
  pick up a *next start* setting; 3 means it could not start, and the supervisor
  shows the log tail instead of retrying.
- **`--headless`** is the server with no parent and no tray, for a systemd unit.

Because the socket stays with the supervisor, a crashed or restarted server never
drops a browser's connection to a refused port, and a tray that dies takes its
server with it rather than leaving an orphan holding the database.

The launcher, not the server, decides where things live (`desktop/gt7_desktop/paths.py`)
and passes it down as environment: `GT7_DATA_DIR`, `GT7_DB_PATH`, `GT7_LOG_FILE`,
`GT7_DESKTOP=1`, and `GT7_FORCE_SIM=firstrun` until setup is completed. Its own
preferences, port and bind only, are in `desktop.json`; every app setting stays in
the database, where the server and a restore see it.

Exposure is a live setting, not a bind address. With `lan_access` off, a request from
any peer other than loopback is refused (`lan_off`, see [Refused
requests](../reference/client-protocol.md#refused-requests)), so turning it on or off
never restarts the server. In desktop mode, a loopback peer that passed the host and
origin checks is admin without a token; other devices need the admin token to change
anything. uvicorn runs with `proxy_headers=False`, so a forwarded header cannot pass
for loopback.

The build is `desktop/gt7.spec`, driven by `desktop/scripts/build.py`;
[Releasing](releasing.md) covers how the signed builds are made.

## The simulator

`GT7_SOURCE=sim` replaces the UDP listener with a deterministic synthetic source: a
3,200 m rounded-rectangle circuit driven at 60 Hz with two braking zones, deliberate
front lockups under braking, rear wheelspin on slow launches, a kerb strike, and
TCS/ASM/rev-limiter activity. Lap detection, event detection, charts, and the overlay
all get realistic data with no console — it's how the project is developed, demoed, and
screenshotted.

The simulated car is **internally consistent**, which matters more than it sounds: the
broadcast yaw rate is the actual turn rate of the line being drawn, the accelerometer is
`v × ω` and the real speed delta, and the filtered pedals differ from the raw ones
exactly while an aid is intervening. Features that check one channel against another —
the g-g diagram's calibration, the ABS/TCS intervention traces — can only be exercised
without a console if the synthetic data would pass the same check real data does.
