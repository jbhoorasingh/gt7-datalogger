"""UDP telemetry capture from a PlayStation running GT7.

Sends a heartbeat to the console every ~1.6 s (GT7 stops sending after
100 packets without one) and receives ~60 Hz encrypted packets. The
heartbeat character selects the packet format ("A", "B", "~", or "C" —
richer formats add steering, motion, surface, and lap-timer data). If no
console IP is configured, the heartbeat is broadcast so the console is
auto-discovered from the first packet's source address, and broadcast again
whenever the stream goes stale, so a console that comes back on a new
address is picked up without the user stepping in.

"Find console" (`discover`) broadcasts the heartbeat on demand even when an
IP is configured, and reports which address answers with GT7 telemetry.
It shares the one socket the live stream uses — GT7 replies to the port
the heartbeat came from, so a second socket would have to bind the
telemetry port too — and keeps whatever answers only the broadcast out of
the pipeline.
"""

from __future__ import annotations

import asyncio
import errno
import ipaddress
import logging
import os
import socket
import sys
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
# Two heartbeats this far apart means the loop itself was not running: the
# machine slept (#207). GT7 stopped streaming long ago by then.
SLEEP_GAP = 10.0
# After a receive error the socket is reopened, backing off like this (#181).
RECEIVE_RESTART_DELAYS = (1.0, 2.0, 5.0, 10.0, 30.0)
# One "failed to decrypt" warning per source address per this many seconds.
DECODE_WARN_EVERY = 60.0


def describe_send_error(exc: OSError) -> str:
    """A UDP error as the reason a person can act on (#181)."""
    code = exc.errno
    if code in (errno.EHOSTUNREACH, errno.ENETUNREACH) and sys.platform == "darwin":
        return (
            "macOS is blocking the local network for this app: allow it in "
            "System Settings › Privacy & Security › Local Network"
        )
    if code in (errno.EHOSTUNREACH, errno.ENETUNREACH):
        return "no route to the PlayStation: check its address and that both are on one network"
    if code in (errno.EACCES, errno.EPERM):
        return "the system refused to send to the PlayStation (a firewall?)"
    if code == errno.ECONNREFUSED:
        return "the PlayStation refused the heartbeat: is GT7 running?"
    return exc.strerror or str(exc)

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
        self._decode_warned: dict[str, float] = {}
        self._dropped = 0
        # The saved console IP as an address (#185): a hostname is resolved
        # once, not on every send, and strangers are compared against it.
        self._resolved: tuple[str, str] | None = None
        # The last UDP error and why it matters, cleared by the next packet
        # that decrypts (#181). Shown in status as heartbeat_error.
        self._error: dict[str, object] | None = None
        self._receive_restarts = 0
        self._last_beat_at = 0.0
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
            "heartbeat_error": self._error,
        }

    @property
    def running(self) -> bool:
        return self._running and self._transport is not None

    def reset_discovery(self) -> None:
        """Forget the discovered console (after the configured IP changes)."""
        self._console_addr = None
        self._last_packet_at = 0.0
        self._resolved = None
        self._error = None

    async def _open(self) -> None:
        loop = asyncio.get_running_loop()
        source = self

        class _Protocol(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
                source._handle_datagram(data, addr)

            def error_received(self, exc: Exception) -> None:
                # asyncio reports UDP errors here instead of raising them
                # from sendto, so without this a macOS Local Network denial
                # (EHOSTUNREACH on every heartbeat) was invisible.
                if isinstance(exc, OSError):
                    source._on_error(exc)

        self._transport, _ = await loop.create_datagram_endpoint(
            _Protocol,
            local_addr=("0.0.0.0", self._settings.telemetry_port),
            allow_broadcast=True,
        )

    async def start(self) -> None:
        await self._open()
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

    def _on_error(self, exc: OSError) -> None:
        reason = describe_send_error(exc)
        if self._error is None or self._error.get("errno") != exc.errno:
            log.warning("telemetry socket error: %s (%s)", reason, exc)
        self._error = {"errno": exc.errno, "reason": reason, "at": time.time()}
        # On Windows' Proactor loop a receive error ends reading on that
        # socket for good; open a fresh one, backing off, while still running.
        if os.name == "nt" and self._running:
            delay = RECEIVE_RESTART_DELAYS[
                min(self._receive_restarts, len(RECEIVE_RESTART_DELAYS) - 1)
            ]
            self._receive_restarts += 1
            self._tasks.append(asyncio.create_task(self._reopen(delay)))

    async def _reopen(self, delay: float) -> None:
        await asyncio.sleep(delay)
        if not self._running:
            return
        if self._transport is not None:
            self._transport.close()
            self._transport = None
            await asyncio.sleep(0)
        try:
            await self._open()
            log.info("telemetry socket reopened after an error")
        except OSError as exc:
            self._on_error(exc)

    async def _console_ip(self) -> str:
        """The saved console IP, a hostname resolved once (#185)."""
        name = self._settings.ps_ip
        if not name:
            return ""
        if self._resolved is not None and self._resolved[0] == name:
            return self._resolved[1]
        try:
            ipaddress.ip_address(name)
            ip = name
        except ValueError:
            try:
                infos = await asyncio.get_running_loop().getaddrinfo(
                    name, None, family=socket.AF_INET, type=socket.SOCK_DGRAM
                )
                ip = str(infos[0][4][0])
            except (OSError, IndexError) as exc:
                log.warning("console address %s does not resolve: %s", name, exc)
                return name  # try again next heartbeat; sendto may still manage
        self._resolved = (name, ip)
        return ip

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
            self._keep = {h for h in (self._settings.ps_ip, await self._console_ip(), live) if h}
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

    def _stranger(self, host: str) -> bool:
        """Whether a saved console IP rules this sender out (#185).

        With an IP saved, only that address is the console; anything else
        on UDP 33740 — a second PlayStation, a scanner, a stray packet on a
        café network — is dropped unread. Find console still hears everyone:
        its window is how a wrong saved IP gets found out.
        """
        if not self._settings.ps_ip or self._found is not None:
            return False
        resolved = self._resolved[1] if self._resolved else self._settings.ps_ip
        return host not in (resolved, self._settings.ps_ip)

    def _decode_failed(self, host: str) -> None:
        self._decode_errors += 1
        now = time.monotonic()
        if now - self._decode_warned.get(host, -DECODE_WARN_EVERY) >= DECODE_WARN_EVERY:
            self._decode_warned[host] = now
            log.warning("failed to decode a packet from %s (not GT7 telemetry?)", host)

    def _handle_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        if self._answered_broadcast_only(addr[0]):
            if self._found is not None and decrypt_packet(data) is not None:
                self._record_found(addr[0])
            return
        if self._stranger(addr[0]):
            return
        plain = decrypt_packet(data)
        if plain is None:
            self._decode_failed(addr[0])
            return
        try:
            packet = parse_packet(plain)
        except ValueError:
            self._decode_failed(addr[0])
            return
        # Only now is it the console. Adopting the sender before decrypting
        # let one junk datagram redirect the heartbeat away from the
        # PlayStation and read as "connected" on noise (#185).
        if self._console_addr is None or self._console_addr[0] != addr[0]:
            log.info("telemetry source discovered at %s", addr[0])
        self._console_addr = addr
        self._last_packet_at = time.monotonic()
        self._error = None
        self._receive_restarts = 0
        self._record_found(addr[0])
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
        self._last_beat_at = time.monotonic()
        while self._running:
            now = time.monotonic()
            gap = now - self._last_beat_at
            self._last_beat_at = now
            if gap > SLEEP_GAP:
                # The machine slept (#207): the console stopped streaming
                # long ago. Say so in the log, which is where "why did my
                # session end" gets answered, and beat at once rather than
                # wait out an interval. The lap processor already re-anchors
                # on the packet-id jump the console's resumed stream brings.
                log.info("woke after %.0f s without a heartbeat (the machine slept?)", gap)
            try:
                self._send_heartbeat(await self._console_ip())
            except OSError as exc:
                self._on_error(exc)
            await asyncio.sleep(HEARTBEAT_INTERVAL)

    def _heartbeat_targets(self, console_ip: str = "") -> list[str]:
        if self._settings.ps_ip:
            return [console_ip or self._settings.ps_ip]
        if self._console_addr and self.connected:
            return [self._console_addr[0]]
        # Auto-discovery with no live stream: the console may be back on a
        # different address (rest mode, a router reboot, a new DHCP lease),
        # so broadcast as at startup. The last known address gets one too,
        # for a network that drops broadcasts while the console kept its IP.
        if self._console_addr:
            return [BROADCAST_ADDR, self._console_addr[0]]
        return [BROADCAST_ADDR]

    def _send_heartbeat(self, console_ip: str = "") -> None:
        if self._transport is None:
            return
        for host in self._heartbeat_targets(console_ip):
            self._transport.sendto(
                self.heartbeat_char.encode("ascii"), (host, self._settings.heartbeat_port)
            )
