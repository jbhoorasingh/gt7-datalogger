# Local development

## One-command dev environment

From the repo root, one command starts everything — backend with auto-reload on `:8000`,
frontend with hot reload on `:5173` — and bootstraps the virtualenv and `node_modules`
on first run. It also rebuilds `frontend/dist` on every start, so `:8000` always serves
the current UI (not a stale build). The backend listens on every interface, as a Docker
or Pi install does, so the [phone app](../guide/settings.md#phone-app) can reach it on
the LAN. ++ctrl+c++ stops both:

```bash
./dev.sh
```

Settings come from a `.env` file in the repo root. Set `GT7_SOURCE=sim` there to develop
against the simulated telemetry source without a PlayStation:

```bash
# .env
GT7_SOURCE=sim
```

## Running the pieces individually

**Backend** (Python 3.12+):

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
GT7_SOURCE=sim python -m uvicorn app.main:app --reload
```

uvicorn's own command line listens on `127.0.0.1` unless given `--host 0.0.0.0`; on
loopback the server does not announce itself over mDNS, since a phone could not reach
the port it would announce. `python -m app.main` binds `GT7_HTTP_HOST` instead.

**Frontend** (Node 22+):

```bash
cd frontend
npm install
npm run dev   # http://localhost:5173, proxies /api and /ws to :8000
```

## Tests and linting

```bash
cd backend
ruff check app tests scripts
mypy app
pytest
```

The test suite exercises the packet decoder, lap detection, derived-channel math, event
detection, schema migrations (including a first-release database being upgraded in
place), and the REST API against fixture data — no PlayStation required.

## Building the desktop app locally

The [desktop app](desktop.md) is built with PyInstaller from the same backend, for the
system you build on. It needs Python 3.14 and Node 22+. Use a venv of its own, not the
backend's, since the build installs the launcher's extra dependencies and PyInstaller.

=== "macOS and Linux"

    ```bash
    python3.14 -m venv .venv-desktop
    source .venv-desktop/bin/activate
    pip install -e "backend[desktop]" -r desktop/requirements-build.txt
    python desktop/scripts/build.py
    ```

=== "Windows (PowerShell)"

    ```powershell
    py -3.14 -m venv .venv-desktop
    .venv-desktop\Scripts\Activate.ps1
    pip install -e "backend[desktop]" -r desktop/requirements-build.txt
    python desktop/scripts/build.py
    ```

`build.py` builds `frontend/dist` if it is missing (`--skip-frontend` skips that),
runs PyInstaller on `desktop/gt7.spec` into `desktop/dist/` and then runs the
result's `--self-test`. The app lands at:

| System | Executable |
| --- | --- |
| macOS | `desktop/dist/GT7 Datalogger.app/Contents/MacOS/gt7-datalogger` |
| Windows | `desktop\dist\gt7-datalogger\gt7-datalogger.exe` |
| Linux | `desktop/dist/gt7-datalogger/gt7-datalogger` |

The smoke test CI runs on every build starts it against an empty data folder, checks
the API, the live stream, a restart of the server process and `--quit`:

```bash
python desktop/ci/smoke.py "desktop/dist/GT7 Datalogger.app/Contents/MacOS/gt7-datalogger"
```

A local build is unsigned. macOS and Windows warn before opening it, and macOS does
not show the Local Network prompt for it reliably.

To run the app without freezing it, from the same venv:

```bash
python desktop/launcher.py
```

It runs exactly what the frozen executable runs, tray included, and takes the same
[options](desktop.md#command-line). It uses the desktop app's own
[data folder](desktop.md#where-your-data-lives), not `data/` in the checkout: pass
`--data-dir` to keep a test away from your real recordings. Stop `./dev.sh` first, or
pass `--port`, since both want port 8000. The launcher's own tests run from the
backend's dev venv: `pip install platformdirs`, then `pytest desktop/tests`.

The release builds and their signing are described in [Releasing](../internals/releasing.md).

## Changing the schema

Models live in `backend/app/storage/db.py`; every change to them needs an
[Alembic](https://alembic.sqlalchemy.org/) revision, which `init_db` then applies on
startup:

```bash
cd backend
.venv/bin/alembic revision --autogenerate -m "add whatever"
```

`alembic.ini` points at `../data/gt7.db` — pass `-x db=…` or edit it to autogenerate
against a different database. Review what comes out before committing (SQLite cannot
alter a column in place, so batch mode rewrites the table), and keep the history to a
single head.

## Project layout

```
backend/
  app/
    telemetry/    # UDP listener, Salsa20 decrypt, packet decode, simulator
    processing/   # lap detection, derived channels, events, tracks, cars
    storage/      # SQLAlchemy async engine + repository
    migrations/   # Alembic revisions (run to head on startup)
    api/          # REST routes, WebSocket, admin endpoints
    service.py    # wires capture → processing → storage → broadcast
  tests/
frontend/
  src/
    views/        # Live, Analysis, Sessions, Tracks, Survey, Overlays, Settings, Dash, Engineer
    components/   # charts, layout builder, settings sections, error pages, UI primitives
    lib/          # API client, channels, strategy math, overlay config
    store/        # settings, telemetry, analysis state
docs/             # this documentation site (MkDocs)
```

## Docs site

The documentation you are reading is built with [MkDocs Material](https://squidfunk.github.io/mkdocs-material/)
from the `docs/` folder and deployed to GitHub Pages by `.github/workflows/docs.yml` on
every push to `main`. To preview locally:

```bash
pip install mkdocs-material
mkdocs serve   # http://localhost:8000 (stop the backend first, same port)
```
