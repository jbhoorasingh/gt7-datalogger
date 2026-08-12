# Local development

## One-command dev environment

From the repo root, one command starts everything — backend with auto-reload on `:8000`,
frontend with hot reload on `:5173` — and bootstraps the virtualenv and `node_modules`
on first run. It also rebuilds `frontend/dist` on every start, so `:8000` always serves
the current UI (not a stale build). ++ctrl+c++ stops both:

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
    views/        # Live, Analysis, Sessions, Overlay, Admin
    components/   # charts, overlay builder, UI primitives
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
