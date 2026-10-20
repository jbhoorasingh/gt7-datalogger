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

import ipaddress
import logging
import socket
import sys
from pathlib import PurePath
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
    value = _cli_option("--port", sys.argv if argv is None else argv)
    if value is not None:
        try:
            return int(value)
        except ValueError:
            pass
    return settings.http_port


def served_host(settings: Settings, argv: list[str] | None = None) -> str:
    """The address uvicorn was told to bind, read back the way served_port is.

    uvicorn's own CLI binds 127.0.0.1 unless given `--host`, which is what
    the dev script ran with (#151): the API was loopback-only while the
    advertisement put the LAN address in front of its port. `python -m
    app.main` binds GT7_HTTP_HOST.
    """
    args = sys.argv if argv is None else argv
    value = _cli_option("--host", args)
    if value is not None:
        return value
    if _is_uvicorn_cli(args):
        return "127.0.0.1"
    return settings.http_host


def is_loopback(host: str) -> bool:
    """Whether a bind address is reachable from this machine only."""
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False  # a host name; assume it resolves to something public


def lan_address(settings: Settings, argv: list[str] | None = None) -> str | None:
    """The address a phone on the LAN reaches this server at, or None when
    the server is bound where the LAN cannot reach it (or there is no LAN).

    A server bound to one specific address is reached at that address; one
    bound to every interface (0.0.0.0, ::) at the machine's LAN address.
    """
    host = served_host(settings, argv)
    if is_loopback(host):
        return None
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return netinfo.lan_ip() or None
    if ip.is_unspecified or ip.version != 4:
        return netinfo.lan_ip() or None
    return host


def _cli_option(name: str, args: list[str]) -> str | None:
    """`--name value` or `--name=value` from a command line."""
    for i, arg in enumerate(args):
        if arg == name and i + 1 < len(args):
            return args[i + 1]
        if arg.startswith(f"{name}="):
            return arg.split("=", 1)[1]
    return None


def _is_uvicorn_cli(args: list[str]) -> bool:
    """Whether this process is uvicorn's command line (`uvicorn ...` or
    `python -m uvicorn ...`) rather than `python -m app.main`."""
    if not args:
        return False
    path = PurePath(args[0])
    return path.name == "uvicorn" or (path.name == "__main__.py" and path.parent.name == "uvicorn")


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
        host = served_host(self.settings)
        if is_loopback(host):
            log.warning(
                "mDNS: the server listens on %s only, which phones on the LAN cannot "
                "reach; not advertising (start uvicorn with --host 0.0.0.0)",
                host,
            )
            return
        ip = lan_address(self.settings)
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
