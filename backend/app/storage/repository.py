"""Persistence for sessions and laps, plus JSON import/export."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Row, case, delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.processing.laps import CompletedLap, SessionInfo
from app.processing.tracks import IDENTIFY_MIN_TICKS, TrackSignature, matches
from app.storage.db import LapRow, LayoutRow, SessionRow, SettingRow, TrackRow

# v2 (Tier 1): per-corner sample columns, events, aid/engine metrics, gearing.
# v1 files import fine — missing columns stay absent and charts skip them.
EXPORT_VERSION = 2


def lap_summary(row: LapRow | Row[Any]) -> dict[str, Any]:
    """Summary dict from a lap row — the ORM object or a projected Row.

    list_laps hands in a column projection (the same attributes, minus the
    sample blob it exists to avoid loading); get_lap hands in the ORM row."""
    return {
        "id": row.id,
        "session_id": row.session_id,
        "number": row.number,
        "time_ms": row.time_ms,
        "finished_at": row.finished_at,
        "car_id": row.car_id,
        "car_category": row.car_category,
        "fuel_start": row.fuel_start,
        "fuel_end": row.fuel_end,
        "fuel_consumed": row.fuel_consumed,
        "full_throttle_pct": row.full_throttle_pct,
        "full_brake_pct": row.full_brake_pct,
        "coasting_pct": row.coasting_pct,
        "tire_spin_pct": row.tire_spin_pct,
        "max_speed": row.max_speed,
        "min_body_height": row.min_body_height,
        "total_ticks": row.total_ticks,
        "tod_ms": row.tod_ms,
        "tcs_active_pct": row.tcs_active_pct,
        "asm_active_pct": row.asm_active_pct,
        "max_water_temp": row.max_water_temp,
        "max_oil_temp": row.max_oil_temp,
        "min_oil_pressure": row.min_oil_pressure,
        "counts_for_best": row.counts_for_best,
        "off_track_count": row.off_track_count,
        "off_survey_count": row.off_survey_count,
        "clean_lap": row.clean_lap,
        "salvaged": bool(row.salvaged),
        "event_counts": _event_counts(row.events_json),
    }


def _event_counts(events_json: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    try:
        for e in json.loads(events_json or "[]"):
            counts[e["type"]] = counts.get(e["type"], 0) + 1
    except (ValueError, KeyError, TypeError):
        pass
    return counts


class Repository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create_session(self, info: SessionInfo, car_name: str) -> int:
        async with self._sf() as db:
            row = SessionRow(
                started_at=info.started_at, car_id=info.car_id, car_name=car_name,
                car_category=info.car_category,
            )
            db.add(row)
            await db.commit()
            return row.id

    async def save_lap(self, session_id: int, lap: CompletedLap) -> int:
        async with self._sf() as db:
            row = LapRow(
                session_id=session_id,
                number=lap.number,
                car_category=lap.car_category,
                time_ms=lap.time_ms,
                finished_at=lap.finished_at,
                car_id=lap.car_id,
                fuel_start=lap.fuel_start,
                fuel_end=lap.fuel_end,
                fuel_consumed=lap.fuel_consumed,
                full_throttle_pct=lap.full_throttle_pct,
                full_brake_pct=lap.full_brake_pct,
                coasting_pct=lap.coasting_pct,
                tire_spin_pct=lap.tire_spin_pct,
                max_speed=lap.max_speed,
                min_body_height=lap.min_body_height,
                total_ticks=lap.total_ticks,
                tod_ms=lap.tod_ms,
                tcs_active_pct=lap.tcs_active_pct,
                asm_active_pct=lap.asm_active_pct,
                counts_for_best=lap.counts_for_best,
                off_track_count=lap.off_track_count,
                off_survey_count=lap.off_survey_count,
                clean_lap=lap.clean_lap,
                salvaged=lap.salvaged,
                max_water_temp=lap.max_water_temp,
                max_oil_temp=lap.max_oil_temp,
                min_oil_pressure=lap.min_oil_pressure,
                events_json=json.dumps(lap.events, separators=(",", ":")),
                gearing_json=json.dumps(lap.gearing, separators=(",", ":")) if lap.gearing else "",
                samples_json=json.dumps(lap.samples, separators=(",", ":")),
            )
            db.add(row)
            await db.commit()
            return row.id

    async def list_sessions(self, category: str | None = None) -> list[dict[str, Any]]:
        # One aggregate query for all sessions; the outer join keeps lap-less
        # sessions and only touches LapRow ids/times (never samples_json).
        async with self._sf() as db:
            # Best excludes partial laps (pit out-laps, counts_for_best=0):
            # their GT7-reported "times" aren't full-lap times.
            best_expr = func.min(case((LapRow.counts_for_best, LapRow.time_ms)))
            # The session row's category comes from the FIRST packet of the
            # session, which is packet A/B often enough — a heartbeat format
            # that carries no category at all, or one sent before the console
            # switched. The laps know better, and max() over them picks the
            # non-empty value ('' sorts lowest). Without this, a session whose
            # laps are all plainly Gr.3 sits outside the Gr.3 filter (#19).
            rows = (
                await db.execute(
                    select(
                        SessionRow,
                        func.count(LapRow.id),
                        best_expr,
                        func.max(LapRow.car_category),
                    )
                    .outerjoin(LapRow, LapRow.session_id == SessionRow.id)
                    .group_by(SessionRow.id)
                    .order_by(SessionRow.id.desc())
                )
            ).all()
            sessions = [
                {
                    "id": s.id,
                    "started_at": s.started_at,
                    "car_id": s.car_id,
                    "car_name": s.car_name,
                    "car_category": s.car_category or lap_category or "",
                    "note": s.note,
                    "track_name": s.track_name,
                    "bests_excluded": bool(s.bests_excluded),
                    "lap_count": count,
                    "best_lap_time_ms": best,
                }
                for s, count, best, lap_category in rows
            ]
            if category:
                sessions = [s for s in sessions if s["car_category"] == category]
            return sessions

    async def update_session(
        self,
        session_id: int,
        note: str | None = None,
        bests_excluded: bool | None = None,
    ) -> bool:
        """Patch a session's user-editable fields; False when no such row.

        None means "leave alone", not "clear" — the PATCH endpoint sends only
        the fields the user touched, and a note must survive the neighbouring
        bests_excluded checkbox being flipped (#26).
        """
        async with self._sf() as db:
            row = await db.get(SessionRow, session_id)
            if row is None:
                return False
            if note is not None:
                row.note = note
            if bests_excluded is not None:
                row.bests_excluded = bests_excluded
            await db.commit()
            return True

    async def best_lap_in(self, track: str, category: str) -> dict[str, Any] | None:
        """Fastest full lap ever recorded at a circuit in a car category.

        Scoped by category because a Gr.3 lap and an N100 lap around the same
        circuit are not the same achievement, and one leaderboard over both is
        a leaderboard about the car (#19). Partial out-laps are excluded for
        the same reason they never own a session best.
        """
        if not track or not category:
            return None
        async with self._sf() as db:
            row = (
                await db.execute(
                    select(LapRow, SessionRow.car_name, SessionRow.started_at)
                    .join(SessionRow, SessionRow.id == LapRow.session_id)
                    .where(
                        SessionRow.track_name == track,
                        LapRow.car_category == category,
                        LapRow.counts_for_best,
                        # A session excluded from bests must not define the
                        # class benchmark either — a recorded replay is not a
                        # target anyone set (#26).
                        ~SessionRow.bests_excluded,
                    )
                    .order_by(LapRow.time_ms)
                    .limit(1)
                )
            ).first()
            if row is None:
                return None
            lap, car_name, started_at = row
            return {
                "lap_id": lap.id,
                "session_id": lap.session_id,
                "number": lap.number,
                "time_ms": lap.time_ms,
                "car_id": lap.car_id,
                "car_name": car_name,
                "car_category": lap.car_category,
                "track_name": track,
                "clean_lap": lap.clean_lap,
                "off_survey_count": lap.off_survey_count,
                "finished_at": lap.finished_at or started_at,
            }

    async def personal_bests(self, category: str | None = None) -> list[dict[str, Any]]:
        """The all-time board: fastest counting lap per (circuit, car) (#26).

        One window-function query rather than a Python group-by, because the
        naive route loads every lap row — samples_json included — to keep a
        handful of winners. row_number() picks the fastest per partition (id
        breaks the tie so re-runs return the same lap); count() over the same
        partition is the sample size, which is what tells a one-off banker
        lap apart from a best backed by fifty attempts.

        Excluded: partial laps (their GT7 "times" aren't full-lap times),
        unlabeled sessions (no circuit, no board to be on), and sessions the
        user flagged bests_excluded — recorded replays are indistinguishable
        from driving in the telemetry, so that verdict is theirs to make.
        """
        async with self._sf() as db:
            partition = (SessionRow.track_name, LapRow.car_id)
            ranked = (
                select(
                    SessionRow.track_name,
                    LapRow.car_id,
                    LapRow.car_category,
                    LapRow.id.label("lap_id"),
                    LapRow.session_id,
                    LapRow.number,
                    LapRow.time_ms,
                    LapRow.finished_at,
                    SessionRow.started_at,
                    LapRow.clean_lap,
                    LapRow.off_survey_count,
                    LapRow.salvaged,
                    func.row_number()
                    .over(partition_by=partition, order_by=(LapRow.time_ms, LapRow.id))
                    .label("rn"),
                    func.count().over(partition_by=partition).label("lap_count"),
                )
                .join(SessionRow, SessionRow.id == LapRow.session_id)
                .where(
                    SessionRow.track_name != "",
                    LapRow.counts_for_best,
                    ~SessionRow.bests_excluded,
                )
                .subquery()
            )
            q = (
                select(ranked)
                .where(ranked.c.rn == 1)
                .order_by(ranked.c.track_name, ranked.c.time_ms)
            )
            if category:
                # Applied OUTSIDE the window, so the filter narrows finished
                # board rows rather than re-ranking within the class: a car
                # whose fastest lap predates packet C (category '') must not
                # sprout a slower "Gr.3 best" that the unfiltered board never
                # shows.
                q = q.where(ranked.c.car_category == category)
            rows = (await db.execute(q)).all()
            return [
                {
                    "track_name": r.track_name,
                    "car_id": r.car_id,
                    "car_category": r.car_category or "",
                    "lap_id": r.lap_id,
                    "session_id": r.session_id,
                    "number": r.number,
                    "time_ms": r.time_ms,
                    # Imported laps can carry an empty finished_at; the board
                    # still owes the row a date — same fallback as best_lap_in.
                    "finished_at": r.finished_at or r.started_at,
                    "clean_lap": r.clean_lap,
                    "off_survey_count": r.off_survey_count,
                    "salvaged": bool(r.salvaged),
                    "lap_count": r.lap_count,
                }
                for r in rows
            ]

    async def session_lap_stats(self, session_id: int) -> dict[str, Any]:
        """Aggregates for a session without materializing lap rows.

        Loading LapRow objects pulls the (large) samples_json column along;
        session-boundary bookkeeping only needs these numbers.
        """
        async with self._sf() as db:
            count, best_ms, fuel_used, car_id = (
                await db.execute(
                    select(
                        func.count(LapRow.id),
                        # partial out-laps don't own the session best
                        func.min(case((LapRow.counts_for_best, LapRow.time_ms))),
                        func.coalesce(func.sum(LapRow.fuel_consumed), 0.0),
                        func.min(LapRow.car_id),
                    ).where(LapRow.session_id == session_id)
                )
            ).one()
            return {
                "count": count,
                "best_ms": best_ms if best_ms is not None else -1,
                "fuel_used": fuel_used,
                "car_id": car_id if car_id is not None else 0,
            }

    async def mark_session_laps_partial(self, session_id: int, numbers: list[int]) -> None:
        """Set which laps of a session are partial — and which are not.

        Called when later laps change the verdict on an earlier one. The whole
        session is rewritten rather than only the newly-condemned laps, because
        the yardstick moves in both directions: the lap that looked short next
        to one wide lap is full again once a third lap settles the distance.
        """
        async with self._sf() as db:
            await db.execute(
                update(LapRow)
                .where(LapRow.session_id == session_id)
                .values(counts_for_best=LapRow.number.notin_(numbers) if numbers else True)
            )
            await db.commit()

    async def set_lap_survey_verdict(
        self, lap_id: int, off_survey_count: int, clean_lap: bool | None
    ) -> None:
        """Re-judge a stored lap against the surveyed road (#41).

        Backfill path: laps saved before the session's circuit was identified
        went to the DB unjudged, and identification lands one lap late.
        """
        async with self._sf() as db:
            await db.execute(
                update(LapRow)
                .where(LapRow.id == lap_id)
                .values(off_survey_count=off_survey_count, clean_lap=clean_lap)
            )
            await db.commit()

    async def list_laps(
        self,
        session_id: int | None = None,
        track: str | None = None,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        """Lap summaries, newest first, optionally scoped to a session, a
        circuit, or a car category.

        Projects exactly the columns lap_summary reads instead of loading
        LapRow objects: each row would otherwise drag samples_json along —
        hundreds of kilobytes per lap — to build a summary that uses none of
        it, which is what made the unscoped listing unusable as a discovery
        surface (#26). The join is inner because every lap has a session; it
        also supplies track_name, so summaries say where they were driven
        without a per-row lookup.
        """
        async with self._sf() as db:
            q = (
                select(
                    LapRow.id,
                    LapRow.session_id,
                    LapRow.number,
                    LapRow.time_ms,
                    LapRow.finished_at,
                    LapRow.car_id,
                    LapRow.car_category,
                    LapRow.fuel_start,
                    LapRow.fuel_end,
                    LapRow.fuel_consumed,
                    LapRow.full_throttle_pct,
                    LapRow.full_brake_pct,
                    LapRow.coasting_pct,
                    LapRow.tire_spin_pct,
                    LapRow.max_speed,
                    LapRow.min_body_height,
                    LapRow.total_ticks,
                    LapRow.tod_ms,
                    LapRow.tcs_active_pct,
                    LapRow.asm_active_pct,
                    LapRow.max_water_temp,
                    LapRow.max_oil_temp,
                    LapRow.min_oil_pressure,
                    LapRow.counts_for_best,
                    LapRow.off_track_count,
                    LapRow.off_survey_count,
                    LapRow.clean_lap,
                    LapRow.salvaged,
                    LapRow.events_json,
                    SessionRow.track_name,
                )
                .join(SessionRow, SessionRow.id == LapRow.session_id)
                .order_by(LapRow.id.desc())
            )
            if session_id is not None:
                q = q.where(LapRow.session_id == session_id)
            if track is not None:
                q = q.where(SessionRow.track_name == track)
            if category is not None:
                q = q.where(LapRow.car_category == category)
            rows = (await db.execute(q)).all()
            laps = []
            for r in rows:
                # lap_summary reads attributes, which the projected Row serves
                # just as an ORM object would.
                lap = lap_summary(r)
                lap["track_name"] = r.track_name
                laps.append(lap)
            return laps

    async def get_lap(self, lap_id: int, with_samples: bool = True) -> dict[str, Any] | None:
        async with self._sf() as db:
            row = (await db.execute(select(LapRow).where(LapRow.id == lap_id))).scalar_one_or_none()
            if row is None:
                return None
            data = lap_summary(row)
            data["events"] = json.loads(row.events_json or "[]")
            data["gearing"] = json.loads(row.gearing_json) if row.gearing_json else None
            if with_samples:
                data["samples"] = json.loads(row.samples_json)
            return data

    async def get_laps_samples(self, lap_ids: list[int]) -> dict[int, dict[str, list[float]]]:
        async with self._sf() as db:
            rows = (await db.execute(select(LapRow).where(LapRow.id.in_(lap_ids)))).scalars()
            return {r.id: json.loads(r.samples_json) for r in rows}

    async def get_laps_events(self, lap_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
        async with self._sf() as db:
            rows = (
                await db.execute(
                    select(LapRow.id, LapRow.events_json).where(LapRow.id.in_(lap_ids))
                )
            ).all()
            return {lap_id: json.loads(ev or "[]") for lap_id, ev in rows}

    async def delete_session(self, session_id: int) -> None:
        async with self._sf() as db:
            await db.execute(delete(LapRow).where(LapRow.session_id == session_id))
            await db.execute(delete(SessionRow).where(SessionRow.id == session_id))
            await db.commit()

    async def delete_lap(self, lap_id: int) -> None:
        async with self._sf() as db:
            await db.execute(delete(LapRow).where(LapRow.id == lap_id))
            await db.commit()

    async def export_lap(self, lap_id: int) -> dict[str, Any] | None:
        lap = await self.get_lap(lap_id, with_samples=True)
        if lap is None:
            return None
        return {"format": "gt7-datalogger-lap", "version": EXPORT_VERSION, "lap": lap}

    # --- tracks -------------------------------------------------------------

    async def list_tracks(self) -> list[dict[str, Any]]:
        async with self._sf() as db:
            rows = (await db.execute(select(TrackRow).order_by(TrackRow.name))).scalars()
            return [
                {
                    "id": t.id,
                    "name": t.name,
                    "length_m": t.length_m,
                    "created_at": t.created_at,
                }
                for t in rows
            ]

    async def find_track(self, sig: TrackSignature) -> str | None:
        """Name of the stored track matching this signature, if any."""
        async with self._sf() as db:
            rows = (await db.execute(select(TrackRow))).scalars()
            for track in rows:
                if matches(sig, track):
                    return track.name
        return None

    async def create_track(self, name: str, sig: TrackSignature) -> int:
        async with self._sf() as db:
            row = TrackRow(
                name=name,
                length_m=sig.length_m,
                min_x=sig.min_x,
                max_x=sig.max_x,
                min_z=sig.min_z,
                max_z=sig.max_z,
                created_at=datetime.now(UTC).isoformat(),
            )
            db.add(row)
            await db.commit()
            return row.id

    async def delete_track(self, track_id: int) -> None:
        async with self._sf() as db:
            await db.execute(delete(TrackRow).where(TrackRow.id == track_id))
            await db.commit()

    async def unnamed_sessions_with_lap(self) -> list[tuple[int, int]]:
        """(session_id, lap_id) for every session with no circuit label.

        The lap chosen is the shortest FULL one. Full matters: a bundle match
        is a share of whatever samples it is given, so a pit out-lap covering
        only tarmac two layouts share can score 100 % while saying nothing
        about which was driven. Shortest matters too, for a duller reason —
        every lap is a sample blob of a few hundred kilobytes, and this is the
        difference between reading a gigabyte and reading a fraction of it.
        Being full costs nothing on top: a lap that covers the route is the
        route however quickly it was driven.

        Sessions with no full lap fall back to their longest, which is the best
        evidence they have; the matcher then decides whether it is enough.
        """
        async with self._sf() as db:
            shortest = (
                select(LapRow.id)
                .where(
                    LapRow.session_id == SessionRow.id,
                    LapRow.total_ticks >= IDENTIFY_MIN_TICKS,
                )
                .order_by(
                    LapRow.counts_for_best.desc(),
                    case(
                        (LapRow.counts_for_best, LapRow.total_ticks),
                        else_=-LapRow.total_ticks,
                    ),
                )
                .limit(1)
                .correlate(SessionRow)
                .scalar_subquery()
            )
            rows = (
                await db.execute(
                    select(SessionRow.id, shortest)
                    .where(SessionRow.track_name == "")
                    .order_by(SessionRow.id.desc())
                )
            ).all()
            return [(sid, lid) for sid, lid in rows if lid is not None]

    async def lap_samples_json(self, lap_id: int) -> str | None:
        """A lap's raw sample blob, unparsed — the caller decodes it off the
        event loop, which is the whole cost of reading one."""
        async with self._sf() as db:
            return (
                await db.execute(
                    select(LapRow.samples_json).where(LapRow.id == lap_id)
                )
            ).scalar_one_or_none()

    async def track_for_lap(self, lap_id: int) -> str:
        """The circuit label of the session a lap belongs to, if it has one."""
        async with self._sf() as db:
            name = (
                await db.execute(
                    select(SessionRow.track_name)
                    .join(LapRow, LapRow.session_id == SessionRow.id)
                    .where(LapRow.id == lap_id)
                )
            ).scalar_one_or_none()
            return name or ""

    async def set_session_track(self, session_id: int, track_name: str) -> None:
        async with self._sf() as db:
            row = await db.get(SessionRow, session_id)
            if row is not None:
                row.track_name = track_name
                await db.commit()

    # --- overlay/dashboard layouts ------------------------------------------

    @staticmethod
    def _layout_dict(row: LayoutRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "name": row.name,
            "kind": row.kind,
            "config": json.loads(row.config_json),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    async def list_layouts(self) -> list[dict[str, Any]]:
        async with self._sf() as db:
            rows = (await db.execute(select(LayoutRow).order_by(LayoutRow.name))).scalars()
            return [self._layout_dict(r) for r in rows]

    async def get_layout(self, ref: str) -> dict[str, Any] | None:
        """Look up by numeric id first, falling back to name (URLs carry either)."""
        async with self._sf() as db:
            row = None
            if ref.isdigit():
                row = await db.get(LayoutRow, int(ref))
            if row is None:
                row = (
                    await db.execute(select(LayoutRow).where(LayoutRow.name == ref))
                ).scalar_one_or_none()
            return self._layout_dict(row) if row else None

    async def get_layout_by_name(self, name: str) -> dict[str, Any] | None:
        async with self._sf() as db:
            row = (
                await db.execute(select(LayoutRow).where(LayoutRow.name == name))
            ).scalar_one_or_none()
            return self._layout_dict(row) if row else None

    async def create_layout(self, name: str, kind: str, config: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        async with self._sf() as db:
            row = LayoutRow(
                name=name,
                kind=kind,
                config_json=json.dumps(config, separators=(",", ":")),
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            await db.commit()
            return self._layout_dict(row)

    async def update_layout(
        self,
        layout_id: int,
        name: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        async with self._sf() as db:
            row = await db.get(LayoutRow, layout_id)
            if row is None:
                return None
            if name is not None:
                row.name = name
            if config is not None:
                row.config_json = json.dumps(config, separators=(",", ":"))
            row.updated_at = datetime.now(UTC).isoformat()
            await db.commit()
            return self._layout_dict(row)

    async def delete_layout(self, layout_id: int) -> None:
        async with self._sf() as db:
            await db.execute(delete(LayoutRow).where(LayoutRow.id == layout_id))
            await db.commit()

    # --- runtime settings ---------------------------------------------------

    async def get_settings(self) -> dict[str, str]:
        async with self._sf() as db:
            rows = (await db.execute(select(SettingRow))).scalars()
            return {r.key: r.value for r in rows}

    async def set_setting(self, key: str, value: str) -> None:
        async with self._sf() as db:
            row = await db.get(SettingRow, key)
            if row is None:
                db.add(SettingRow(key=key, value=value))
            else:
                row.value = value
            await db.commit()

    # --- admin --------------------------------------------------------------

    async def stats(self) -> dict[str, int]:
        async with self._sf() as db:
            sessions = (await db.execute(select(func.count(SessionRow.id)))).scalar_one()
            laps = (await db.execute(select(func.count(LapRow.id)))).scalar_one()
            return {"sessions": sessions, "laps": laps}

    async def clear_all(self) -> None:
        """Delete all recorded sessions and laps (settings are kept)."""
        async with self._sf() as db:
            await db.execute(delete(LapRow))
            await db.execute(delete(SessionRow))
            await db.commit()

    async def vacuum(self) -> None:
        async with self._sf() as db:
            await db.execute(text("VACUUM"))

    async def import_lap(self, payload: dict[str, Any], session_id: int) -> int:
        if payload.get("format") != "gt7-datalogger-lap":
            raise ValueError("unrecognized lap export format")
        lap = payload["lap"]
        completed = CompletedLap(
            number=int(lap["number"]),
            time_ms=int(lap["time_ms"]),
            finished_at=str(lap.get("finished_at", "")),
            car_id=int(lap.get("car_id", 0)),
            samples=lap["samples"],
            fuel_start=float(lap.get("fuel_start", 0)),
            fuel_end=float(lap.get("fuel_end", 0)),
        )
        # Aid metrics and events are recomputed from samples; engine-health
        # aggregates and gearing aren't derivable, so carry them from v2 files
        # (v1 files simply keep the "unknown" defaults).
        completed.max_water_temp = float(lap.get("max_water_temp", 0.0))
        completed.max_oil_temp = float(lap.get("max_oil_temp", 0.0))
        completed.min_oil_pressure = float(lap.get("min_oil_pressure", -1.0))
        gearing = lap.get("gearing")
        completed.gearing = gearing if isinstance(gearing, dict) else None
        # Provenance survives the round-trip: a salvaged lap exported and
        # imported elsewhere is still a salvaged lap (#26).
        completed.salvaged = bool(lap.get("salvaged", False))
        completed.compute_metrics()
        return await self.save_lap(session_id, completed)
