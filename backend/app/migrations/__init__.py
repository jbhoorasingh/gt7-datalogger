"""Alembic migration environment (#14).

Lives inside the `app` package rather than beside it so `script_location`
can be resolved from `__file__` — the app builds its Alembic Config in
Python (see `app.storage.db`) and never reads `alembic.ini` at runtime. The
ini at the repo's `backend/alembic.ini` exists only for the CLI
(`alembic revision --autogenerate`, `alembic history`).
"""
