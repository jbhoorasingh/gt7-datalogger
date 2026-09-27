"""UDP telemetry capture from a PlayStation running GT7.

Sends a heartbeat to the console every ~1.6 s (GT7 stops sending after
100 packets without one) and receives ~60 Hz encrypted packets. The
heartbeat character selects the packet format ("A", "B", "~", or "C" —
richer formats add steering, motion, surface, and lap-timer data). If no
console IP is configured, the heartbeat is broadcast so the console is
auto-discovered from the first packet's source address.

"Find console" (`discover`) broadcasts the heartbeat on demand even when an
IP is configured, and reports which address answers with GT7 telemetry.
It shares the one socket the live stream uses — GT7 replies to the port
the heartbeat came from, so a second socket would have to bind the
telemetry port too — and keeps whatever answers only the broadcast out of
the pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from app.config import Settings
from app.models import TelemetryPacket
from app.telemetry.crypto import decrypt_packet
from app.telemetry.packet import HEARTBEAT_FORMATS, parse_packet

log = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 1.6
STALE_AFTER = 5.0  # seconds without packets -> report disconnected
BROADCAST_ADDR = "255.255.255.255"
# Find console: a few broadcasts spread over the window, so one lost
# datagram on a busy Wi-Fi network does not read as "no console".
DISCOVERY_WINDOW = 3.0
DISCOVERY_SENDS = 3
# GT7 keeps streaming ~100 packets (~1.7 s at 60 Hz) after the last
# heartbeat it saw, so a console that answered only the broadcast is still
# talking for that long after the window closes.
DISCOVERY_TAIL = 2.0

PacketCallback = Callable[[TelemetryPacket], Awaitable[None]]


class UdpTelemetrySource:
    """Async UDP source. Calls `on_packet` for every decoded packet."""

    def __init__(self, settings: Settings, on_packet: PacketCallback) -> None:
        self._settings = settings
        self._on_packet = on_packet
        self._transport: asyncio.DatagramTransport | None = None
        self._console_addr: tuple[str, int] | None = None
        self._last_packet_at: float = 0.0
        self._packet_count = 0
        self._decode_errors = 0
        self._dropped = 0
        self._running = False
        self._tasks: list[asyncio.Task[None]] = []
        # Packets are consumed by ONE task so processing stays strictly
        # ordered and never overlaps — overlapping feeds corrupt lap
        # detection (duplicate lap saves while a DB write is in flight).
        self._queue: asyncio.Queue[TelemetryPacket] = asyncio.Queue(maxsize=600)
        # Find console state. `_found` collects, in order, the addresses that
        # sent GT7 telemetry while a discovery runs (None when none does).
        # `_keep` is the console the stream already belongs to — the
        # configured IP and whatever was live when discovery began; anyone
        # else answered only the broadcast and is kept out of the pipeline
        # until `_quarantine_until`, so a second console (or the right one
        # while a wrong IP is saved) never splices into a session or opens a
        # three-second one of its own.
        self._found: list[str] | None = None
        self._found_event = asyncio.Event()
        self._keep: set[str] = set()
        self._live: str | None = None
        self._quarantine_until = 0.0
        self._discovery_lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._last_packet_at > 0 and (time.monotonic() - self._last_packet_at) < STALE_AFTER

    @property
    def heartbeat_char(self) -> str:
        fmt = self._settings.packet_format
        return fmt if fmt in HEARTBEAT_FORMATS else "A"

    @property
    def stats(self) -> dict[str, object]:
        return {
            "connected": self.connected,
            "console_ip": self._console_addr[0] if self._console_addr else self._settings.ps_ip,
            "packets_received": self._packet_count,
            "decode_errors": self._decode_errors,
            "packets_dropped": self._dropped,
            "packet_format": self.heartbeat_char,
        }

    @property
    def running(self) -> bool:
        return self._running and self._transport is not None

    def reset_discovery(self) -> None:
        """Forget the discovered console (after the configured IP changes)."""
        self._console_addr = None
        self._last_packet_at = 0.0

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        source = self

        class _Protocol(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
                source._handle_datagram(data, addr)

        self._transport, _ = await loop.create_datagram_endpoint(
            _Protocol,
            local_addr=("0.0.0.0", self._settings.telemetry_port),
            allow_broadcast=True,
        )
        self._running = True
        self._tasks.append(asyncio.create_task(self._heartbeat_loop()))
        self._tasks.append(asyncio.create_task(self._consume_loop()))
        target = self._settings.ps_ip or "<broadcast>"
        log.info(
            "UDP telemetry listening on :%d, heartbeat to %s:%d",
            self._settings.telemetry_port, target, self._settings.heartbeat_port,
        )

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        # Wait for the heartbeat/consume tasks to actually finish before the
        # socket goes away — a restart rebinds the same port immediately, and
        # a half-dead consumer racing the new bind corrupts the swap.
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self._transport:
            self._transport.close()
            self._transport = None
            # close() only schedules the socket teardown; yield so the loop
            # actually releases the port before a restart rebinds it.
            await asyncio.sleep(0)

    async def discover(
        self, window: float | None = None, sends: int = DISCOVERY_SENDS
    ) -> str | None:
        """Broadcast the heartbeat and return the first address that answers
        with GT7 telemetry, or None if nothing does within `window` seconds.

        Changes nothing: the saved IP, the heartbeat target, and the console
        the live stream belongs to stay as they were. A console that is
        already streaming is the answer — it wins over anything that the
        broadcast wakes, whichever packet lands first — so with the right IP
        configured this simply confirms it. Concurrent calls queue rather
        than share a window, so each gets an answer of its own.
        """
        window = DISCOVERY_WINDOW if window is None else window
        async with self._discovery_lock:
            if not self.running:
                return None
            live = self._console_addr[0] if self.connected and self._console_addr else None
            self._live = live
            self._keep = {h for h in (self._settings.ps_ip, live) if h}
            self._found = []
            self._found_event.clear()
            loop = asyncio.get_running_loop()
            deadline = loop.time() + window
            try:
                for i in range(max(sends, 1)):
                    if not self.running:
                        break
                    self._broadcast_heartbeat()
                    # Spread the sends over the window; the last one waits
                    # out the rest of it.
                    last = i == sends - 1
                    until = deadline if last else loop.time() + window / sends
                    try:
                        await asyncio.wait_for(
                            self._found_event.wait(), max(until - loop.time(), 0.0)
                        )
                        break
                    except TimeoutError:
                        continue
                found = self._found
            finally:
                self._found = None
                self._quarantine_until = time.monotonic() + DISCOVERY_TAIL
            ip = live if live in found else (found[0] if found else None)
            log.info("find console: %s", f"answered from {ip}" if ip else "no answer")
            return ip

    def _broadcast_heartbeat(self) -> None:
        if self._transport is None:
            return
        try:
            self._transport.sendto(
                self.heartbeat_char.encode("ascii"),
                (BROADCAST_ADDR, self._settings.heartbeat_port),
            )
        except OSError as exc:
            log.warning("find console: broadcast failed: %s", exc)

    def _record_found(self, host: str) -> None:
        if self._found is not None and host not in self._found:
            self._found.append(host)
            # With a console live, only it ends the window early: its next
            # packet is milliseconds away and it is the better answer.
            if self._live is None or host == self._live:
                self._found_event.set()

    def _answered_broadcast_only(self, host: str) -> bool:
        """Whether `host` is streaming only because Find console woke it.

        With nothing to keep (auto-discovery and no console yet) there is no
        stream to protect: the first answer is what auto-discovery would
        have picked anyway, so it flows as usual.
        """
        if not self._keep or host in self._keep:
            return False
        return self._found is not None or time.monotonic() < self._quarantine_until

    def _handle_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        if self._answered_broadcast_only(addr[0]):
            if self._found is not None and decrypt_packet(data) is not None:
                self._record_found(addr[0])
            return
        if self._console_addr is None or self._console_addr[0] != addr[0]:
            log.info("telemetry source discovered at %s", addr[0])
        self._console_addr = addr
        self._last_packet_at = time.monotonic()
        plain = decrypt_packet(data)
        if plain is None:
            self._decode_errors += 1
            if self._decode_errors in (1, 100):
                log.warning("failed to decrypt packet from %s (bad key/format?)", addr[0])
            return
        self._record_found(addr[0])
        try:
            packet = parse_packet(plain)
        except ValueError:
            self._decode_errors += 1
            return
        self._packet_count += 1
        try:
            self._queue.put_nowait(packet)
        except asyncio.QueueFull:
            # Consumer is behind (e.g. slow disk); drop the oldest to keep live.
            self._dropped += 1
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self._queue.put_nowait(packet)

    async def _consume_loop(self) -> None:
        while True:
            packet = await self._queue.get()
            try:
                await self._on_packet(packet)
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "error processing telemetry packet %d: %s",
                    packet.packet_id,
                    exc,
                    exc_info=True,
                )

    async def _heartbeat_loop(self) -> None:
        while self._running:
            try:
                self._send_heartbeat()
            except OSError as exc:
                log.warning("heartbeat send failed: %s", exc)
            await asyncio.sleep(HEARTBEAT_INTERVAL)

    def _send_heartbeat(self) -> None:
        if self._transport is None:
            return
        if self._settings.ps_ip:
            target = (self._settings.ps_ip, self._settings.heartbeat_port)
        elif self._console_addr:
            target = (self._console_addr[0], self._settings.heartbeat_port)
        else:
            target = (BROADCAST_ADDR, self._settings.heartbeat_port)
        self._transport.sendto(self.heartbeat_char.encode("ascii"), target)
