"""Re-judging stored laps when a circuit's survey changes (#91).

A lap's surveyed-edge verdict — `off_survey_count`, and the `clean_lap` it
feeds — is decided when the lap is saved, against the bundle as it was at
that moment. The bundle keeps changing afterwards: a survey autosaves as it
drives, a friend's bundle is merged in, two spellings are merged by rename,
a bad survey is deleted. Nothing about the lap changed, so its verdict has
to be asked again — and for every lap ever driven on the circuit, not only
the live session's, which is all `TelemetryService._backfill_survey_verdicts`
ever covered.

Cost is the design constraint. Each lap means reading and decoding its sample
blob, hundreds of kilobytes, so a circuit with a few hundred laps is a few
hundred megabytes off disk. Hence:

- one pass per circuit, coalesced: every bundle write calls `changed`, and
  the pass runs once the bundle has been left alone for the settle time. The
  survey autosaves about once a minute and each save is a write, so during a
  run the clock keeps restarting and the history is judged once, after the
  run stops — never every minute against a bundle that is not done. (The
  sync client waits the same way, for the same reason.) An explicit edit —
  an import, a rename, a delete — passes a settle time of zero: one edit is
  one change, not a run in progress.
- the pass runs on its own task; decode and judge run on a worker thread.
  Nothing waits on it: the endpoint that wrote the bundle has answered
  before the first blob is read.
- `run_now` skips the wait and waits for the pass — the Tracks view's
  **Re-check laps**, which also wants to say how many verdicts changed.

A circuit is its slug, not a label. Session labels that differ only in
spelling or case share one bundle (that is what slugify is for), and the
live judge finds the bundle by the same slug, so a pass covers exactly the
sessions the bundle would have judged. No bundle — deleted, or too little
surveyed road to support a judge — takes every verdict back to unknown:
a lap must not keep a verdict from geometry that no longer exists.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.processing import track_bundle, track_limits
from app.processing.laps import clean_verdict
from app.storage.repository import Repository

log = logging.getLogger(__name__)

# How long a bundle must be left alone after a survey write before its laps
# are re-judged. Longer than the autosave interval (~60 s) so a running
# survey never triggers a pass; short enough that a driver who has just
# stopped a survey sees the laps it re-judged while still looking at them.
SETTLE_S = 120.0


def judge_samples_json(judge: track_limits.RoadJudge, raw: str) -> int:
    """Decode + judge a stored lap's blob in one worker-thread hop: the
    samples_json of a real lap is hundreds of kilobytes, and parsing it on
    the event loop is the cost the repository API exists to avoid."""
    samples = json.loads(raw)
    return judge.excursions(
        samples.get("pos_x") or [], samples.get("pos_z") or [], samples.get("pos_y")
    )


class SurveyRejudge:
    def __init__(
        self, repo: Repository, data_dir: Path, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.repo = repo
        self.data_dir = data_dir
        self._clock = clock
        # slug -> the moment its pass may run. One entry per circuit: a
        # later write moves the moment, it never queues a second pass.
        self._due: dict[str, float] = {}
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        # One pass at a time. Two on the same circuit would read every blob
        # twice for one answer; two on different circuits would only compete
        # for the same disk.
        self._lock = asyncio.Lock()
        self._inflight = 0
        # Told (slug, verdicts changed) after a pass that changed something,
        # so open views can refetch. Nothing is said about a pass that found
        # every verdict already right.
        self.on_changed: Callable[[str, int], Awaitable[None]] | None = None

    # --- entry points -------------------------------------------------------

    def changed(self, track: str, settle_s: float = SETTLE_S) -> None:
        """A bundle was written: re-judge the circuit's laps once it settles.

        Safe to call from anywhere on the loop thread. Calling it again
        before the settle time is up starts the wait over.
        """
        if not track:
            return
        self._due[track_bundle.slugify(track)] = self._clock() + settle_s
        self._kick()

    def pending(self) -> dict[str, float]:
        """Seconds until each queued circuit's pass — the Tracks view, tests."""
        now = self._clock()
        return {slug: max(0.0, at - now) for slug, at in self._due.items()}

    async def run_now(self, slug: str) -> dict[str, Any]:
        """Re-judge every stored lap on a circuit, now, and say what changed.

        Covers whatever was queued for the circuit too: a manual run is the
        most recent bundle judging the laps, which is all a queued pass was
        going to do.
        """
        self._due.pop(slug, None)
        self._inflight += 1
        try:
            async with self._lock:
                return await self._pass(slug)
        finally:
            self._inflight -= 1

    async def wait_idle(self, timeout: float = 5.0) -> None:
        """Wait until nothing is due or running — tests, mostly."""
        deadline = time.monotonic() + timeout
        while (self._due_now() or self._inflight) and time.monotonic() < deadline:
            await asyncio.sleep(0.01)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._due.clear()

    # --- the worker ---------------------------------------------------------

    def _due_now(self) -> list[str]:
        now = self._clock()
        return sorted(slug for slug, at in self._due.items() if at <= now)

    def _kick(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop (a unit test writing bundles): stays queued
        self._wake.set()
        if self._task is None or self._task.done():
            self._task = loop.create_task(self._run())

    async def _run(self) -> None:
        try:
            while self._due:
                slug = min(self._due, key=self._due.__getitem__)
                wait = self._due[slug] - self._clock()
                if wait > 0:
                    self._wake.clear()
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self._wake.wait(), timeout=wait)
                    continue
                try:
                    await self.run_now(slug)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("re-judging laps on %s failed", slug)
        finally:
            self._task = None

    # --- one circuit --------------------------------------------------------

    async def _pass(self, slug: str) -> dict[str, Any]:
        judge = await asyncio.to_thread(track_limits.judge_for_track, self.data_dir, slug)
        labels = sorted(
            label
            for label in await self.repo.session_track_labels()
            if track_bundle.slugify(label) == slug
        )
        rows: list[dict[str, Any]] = []
        for label in labels:
            rows.extend(await self.repo.list_laps(track=label))
        changed = 0
        done = 0
        try:
            for row in rows:
                _count, moved = await self.judge_row(judge, row)
                changed += moved
                done += 1
        except asyncio.CancelledError:
            # Each row is its own transaction, so what was done stays done;
            # the rest is stale until the next bundle write or a manual run.
            log.warning(
                "re-judge of %s interrupted after %d of %d laps — Re-check laps finishes it",
                slug, done, len(rows),
            )
            raise
        log.info(
            "re-judged %d lap(s) on %s against %s: %d verdict(s) changed",
            len(rows), slug, "the surveyed edges" if judge else "no survey", changed,
        )
        if changed and self.on_changed is not None:
            await self.on_changed(slug, changed)
        return {
            "slug": slug,
            "labels": labels,
            "laps": len(rows),
            "changed": changed,
            "judged": judge is not None,
        }

    async def judge_row(
        self, judge: track_limits.RoadJudge | None, row: dict[str, Any]
    ) -> tuple[int, bool]:
        """Judge one stored lap and bring its row in line with the verdict.

        Returns the verdict and whether the row changed. A row changes when
        the count moved OR when clean_lap no longer follows from the two
        counts — the second is how a lap the one-way flag spoiled (#92)
        is repaired even though its count reads the same.
        """
        if judge is None:
            count = -1
        else:
            raw = await self.repo.lap_samples_json(row["id"])
            if not raw:
                return int(row["off_survey_count"]), False
            count = await asyncio.to_thread(judge_samples_json, judge, raw)
        clean = clean_verdict(row["off_track_count"], count)
        if count == row["off_survey_count"] and clean == row["clean_lap"]:
            return count, False
        await self.repo.set_lap_survey_verdict(row["id"], count, clean)
        log.debug(
            "lap %d of session %d re-judged: %d excursion(s) past the surveyed edge",
            row["number"], row["session_id"], count,
        )
        return count, True
