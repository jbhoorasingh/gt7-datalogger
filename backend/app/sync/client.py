"""The sync client: settings, capabilities, per-type status, background tasks.

`SyncClient` is the thing the service holds and the Admin API talks to. It
owns the transport, knows what the server offers (`/v1/capabilities`), runs
the adapters, and is the one place that decides whether a data type is
*active* — which takes four facts agreeing:

1. the master switch is on and a server + token are configured;
2. the type's own toggle is on (`GT7_SYNC_<TYPE>`);
3. this build has an adapter for it (the server may offer `sessions`
   before the logger can send them);
4. the server has not said no — either by leaving the type out of its
   capabilities, or with a `403 type_disabled` on an upload, which flips
   the toggle off and says so in the status line.

Every network call happens on a task the client tracks and cancels at stop;
nothing here is awaited from the packet path.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import httpx

from app.sync.connection import mask_token
from app.sync.transport import SyncError, Transport

if TYPE_CHECKING:
    from app.config import Settings
    from app.sync.tracks import TracksAdapter

log = logging.getLogger(__name__)


class Adapter(Protocol):
    """What every data type's adapter gives the client."""

    async def sweep(self) -> None:
        """Queue everything eligible; already-sent things are skipped."""

    def halt(self) -> None:
        """Stop sending; keep the queue."""

    def reload_state(self) -> None:
        """The server changed: forget what the old one accepted."""

    def status(self) -> dict[str, Any]:
        """state, error, counters — merged into the type's status entry."""

# The data types the spec defines, in display order, with what each sends.
# Listed here rather than only in the UI so the status document can describe
# a type the server offers and this build cannot send yet.
TYPE_DESCRIPTIONS: dict[str, str] = {
    "tracks": (
        "Track surveys: the border evidence, finish line and corner labels of "
        "circuits whose official layout you have confirmed. Uploaded once a "
        "bundle has settled after a change. Nothing about your laps."
    ),
    "sessions": (
        "Your own sessions and laps — car, lap times, racing line — as a "
        "private cloud copy."
    ),
    "live": "A ~4 Hz position and lap-time stream for a spectate page or overlay.",
}


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class SyncClient:
    def __init__(
        self,
        settings: Settings,
        data_dir: Path,
        persist: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> None:
        from app.sync.tracks import TracksAdapter

        self.settings = settings
        self.data_dir = data_dir
        # How a flipped toggle reaches the settings table (the repo's
        # set_setting); None in unit tests that only want the in-memory flip.
        self._persist = persist
        # Injected by tests so no request leaves the process.
        self.http: httpx.AsyncBaseTransport | None = None
        self.capabilities: dict[str, Any] | None = None
        self.capabilities_error = ""
        self.checked_at: str | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._bring_up_task: asyncio.Task[Any] | None = None
        self.tracks: TracksAdapter = TracksAdapter(self, data_dir)
        self.adapters: dict[str, Adapter] = {"tracks": self.tracks}

    # --- what is on ---------------------------------------------------------

    @property
    def configured(self) -> bool:
        return bool(self.settings.sync_url.strip() and self.settings.sync_token)

    @property
    def enabled(self) -> bool:
        return self.configured and self.settings.sync_enabled

    def wanted(self, name: str) -> bool:
        """The type's own toggle, under the master switch."""
        return self.enabled and bool(getattr(self.settings, f"sync_{name}", False))

    def offered(self, name: str) -> bool | None:
        """Whether the server lists the type; None until capabilities are known."""
        if self.capabilities is None:
            return None
        return name in self.capabilities["types"]

    def active(self, name: str) -> bool:
        return self.wanted(name) and name in self.adapters and self.offered(name) is not False

    def transport(self) -> Transport:
        return Transport(self.settings.sync_url, self.settings.sync_token, http=self.http)

    # --- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        """Called once at service start. Nothing runs unless sync is on."""
        self.apply()

    def apply(self) -> None:
        """Settings changed (or startup): bring the tasks in line with them.

        Cheap and idempotent — the Admin PUT calls it after every change. A
        type that just became active gets a capabilities check and then a
        sweep; one that stopped being active is halted.
        """
        if not self.enabled:
            for adapter in self.adapters.values():
                adapter.halt()
            return
        if self._bring_up_task is not None and not self._bring_up_task.done():
            self._bring_up_task.cancel()
        self._bring_up_task = self.spawn(self._bring_up())

    async def _bring_up(self) -> None:
        if self.capabilities is None:
            await self.refresh_capabilities()
        for name, adapter in self.adapters.items():
            if self.active(name):
                await adapter.sweep()
            else:
                adapter.halt()

    def reset_connection(self) -> None:
        """The server or token changed: forget what the old one told us."""
        self.capabilities = None
        self.capabilities_error = ""
        self.checked_at = None
        for adapter in self.adapters.values():
            adapter.halt()
            adapter.reload_state()

    async def stop(self) -> None:
        for adapter in self.adapters.values():
            adapter.halt()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    def spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # --- capabilities -------------------------------------------------------

    async def check(self) -> dict[str, Any]:
        """Fetch capabilities now and raise on failure — the Test button."""
        if not self.configured:
            raise SyncError("auth", "no connection string configured")
        try:
            caps = await self.transport().capabilities()
        except SyncError as exc:
            self.capabilities_error = exc.message
            self.checked_at = utcnow()
            raise
        self.capabilities = caps
        self.capabilities_error = ""
        self.checked_at = utcnow()
        log.info(
            "sync: %s offers %s",
            self.settings.sync_url,
            ", ".join(sorted(caps["types"])) or "no data types",
        )
        # The answer may have changed which types are active.
        for name, adapter in self.adapters.items():
            if self.wanted(name) and not self.active(name):
                adapter.halt()
        return caps

    async def refresh_capabilities(self) -> None:
        """Background variant: a failure is a status line, not an exception."""
        try:
            await self.check()
        except SyncError as exc:
            log.info("sync: capabilities not available (%s)", exc.message)

    # --- the server said no -------------------------------------------------

    async def type_disabled(self, name: str, reason: str) -> None:
        """A `403 type_disabled`: flip the toggle off, persistently, and say why."""
        if not getattr(self.settings, f"sync_{name}", False):
            return
        setattr(self.settings, f"sync_{name}", False)
        if self._persist is not None:
            try:
                await self._persist(f"sync_{name}", "false")
            except Exception:  # pragma: no cover - a failed write must not kill the task
                log.exception("sync: could not persist the %s toggle", name)
        log.warning("sync: server no longer accepts %s (%s); toggle switched off", name, reason)

    # --- status -------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Everything the Admin Sync panel renders."""
        types: dict[str, Any] = {}
        offered = set(self.capabilities["types"]) if self.capabilities else set()
        for name in list(TYPE_DESCRIPTIONS) + sorted(offered - set(TYPE_DESCRIPTIONS)):
            adapter = self.adapters.get(name)
            entry: dict[str, Any] = {
                "description": TYPE_DESCRIPTIONS.get(name, ""),
                "enabled": bool(getattr(self.settings, f"sync_{name}", False)),
                "offered": self.offered(name),
                "supported": adapter is not None,
                "active": self.active(name),
            }
            if adapter is not None:
                entry.update(adapter.status())
            else:
                entry["state"] = "unsupported"
                entry["error"] = ""
            types[name] = entry
        return {
            "configured": self.configured,
            "url": self.settings.sync_url if self.configured else "",
            "token_hint": mask_token(self.settings.sync_token),
            "enabled": self.settings.sync_enabled,
            "capabilities": self.capabilities,
            "capabilities_error": self.capabilities_error,
            "checked_at": self.checked_at,
            "types": types,
        }
