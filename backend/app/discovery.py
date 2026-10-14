"""Announce the datalogger on the LAN over mDNS, so the phone app finds it.

The service type is `_gt7dl._tcp`. Its TXT record carries what a client
checks before connecting: the live client protocol version (app.protocol),
the server version, and the WebSocket path. GT7_MDNS turns it off; the change
applies at once.

Multicast does not leave a Docker bridge network: in a container this only
helps with `network_mode: host`, which broadcast console discovery needs
anyway. Everywhere else the QR card in Settings is the fallback.
"""

from __future__ import annotations

import logging
import socket
import sys
from typing import Any

from app import netinfo, protocol
from app.config import Settings

log = logging.getLogger(__name__)

SERVICE_TYPE = "_gt7dl._tcp.local."


def served_port(settings: Settings, argv: list[str] | None = None) -> int:
    """The port uvicorn was told to listen on.

    `python -m app.main` binds GT7_HTTP_PORT, but the Docker image and the dev
    launchers run uvicorn's own CLI with `--port`, which the app is never
    told about; reading it back is the only way to advertise the right one.
    """
    args = sys.argv if argv is None else argv
    for i, arg in enumerate(args):
        value = None
        if arg == "--port" and i + 1 < len(args):
            value = args[i + 1]
        elif arg.startswith("--port="):
            value = arg.split("=", 1)[1]
        if value is not None:
            try:
                return int(value)
            except ValueError:
                break
    return settings.http_port


def _host_label() -> str:
    return socket.gethostname().split(".")[0] or "gt7"


def instance_name() -> str:
    return f"GT7 Datalogger on {_host_label()}"


def service_properties() -> dict[str, str]:
    return {
        "protocol": str(protocol.PROTOCOL_VERSION),
        "version": netinfo.server_version(),
        "ws": "/ws/live",
        "api": "/api",
    }


class Advertiser:
    """Registers and withdraws the mDNS service, following GT7_MDNS.

    Inert until `start()` — called from the app's lifespan only — so a
    TelemetryService built in a test never touches the network.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._armed = False
        self._zc: Any = None
        self._info: Any = None
        self.advertised: dict[str, Any] | None = None

    async def start(self) -> None:
        self._armed = True
        await self.apply()

    async def stop(self) -> None:
        self._armed = False
        await self._withdraw()

    async def apply(self) -> None:
        """Bring the advertisement in line with the setting."""
        if not self._armed:
            return
        if self.settings.mdns and self._zc is None:
            await self._register()
        elif not self.settings.mdns and self._zc is not None:
            await self._withdraw()

    async def _register(self) -> None:
        ip = netinfo.lan_ip()
        if not ip:
            log.warning("mDNS: no LAN address found, not advertising")
            return
        from zeroconf import IPVersion
        from zeroconf.asyncio import AsyncServiceInfo, AsyncZeroconf

        port = served_port(self.settings)
        name = instance_name()
        info = AsyncServiceInfo(
            SERVICE_TYPE,
            f"{name}.{SERVICE_TYPE}",
            addresses=[socket.inet_aton(ip)],
            port=port,
            properties=service_properties(),
            # Our own host name for the address record, never the machine's:
            # the OS responder (mDNSResponder, Avahi) already owns <host>.local
            # and two answerers for one name trigger conflict renaming.
            server=f"gt7-datalogger-{_host_label()}.local.",
        )
        zc = AsyncZeroconf(ip_version=IPVersion.V4Only)
        try:
            # The call returns the probe-and-announce task; awaiting that is
            # what makes "advertising" true when we log it.
            await (await zc.async_register_service(info, allow_name_change=True))
        except Exception as exc:  # noqa: BLE001 — never stop the app over this
            log.warning("mDNS: could not advertise: %s", exc)
            await zc.async_close()
            return
        self._zc, self._info = zc, info
        self.advertised = {"name": info.name, "ip": ip, "port": port}
        log.info("mDNS: advertising %s at %s:%d", info.name, ip, port)

    async def _withdraw(self) -> None:
        zc, info = self._zc, self._info
        self._zc = self._info = self.advertised = None
        if zc is None:
            return
        try:
            # Awaited twice for the same reason: closing before the returned
            # task has sent its goodbye leaves phones listing a server that
            # is gone until their cache expires (75 minutes for the PTR).
            await (await zc.async_unregister_service(info))
        finally:
            await zc.async_close()
        log.info("mDNS: advertisement withdrawn")
