"""mDNS follows the bind address and the LAN policy, and keeps the hostname private (#186)."""

from __future__ import annotations

import asyncio
import socket
from types import SimpleNamespace

import pytest

from app import discovery, netinfo
from app.config import Settings


def test_the_name_never_carries_the_hostname() -> None:
    host = socket.gethostname().split(".")[0]
    name = discovery.instance_name("a1b2c3")
    assert name == "GT7 Datalogger a1b2c3"
    assert host not in name
    assert len(discovery.new_label()) == 6


def test_a_desktop_bind_is_the_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discovery, "BOUND", ("127.0.0.1", 8004))
    settings = Settings(_env_file=None)
    assert discovery.served_host(settings) == "127.0.0.1"
    assert discovery.served_port(settings) == 8004
    assert discovery.lan_address(settings) is None


async def test_no_lan_address_schedules_a_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discovery, "lan_address", lambda _s: None)
    monkeypatch.setattr(discovery, "served_host", lambda _s: "0.0.0.0")
    monkeypatch.setattr(discovery, "RETRY_DELAYS", (0.01,))
    monkeypatch.setattr(discovery, "RETRY_EVERY", 0.01)
    advertiser = discovery.Advertiser(Settings(_env_file=None, mdns=True))
    calls = 0
    original = advertiser._register

    async def counting(retrying: bool = False) -> None:
        nonlocal calls
        calls += 1
        if calls >= 3:
            advertiser._armed = False
        await original(retrying)

    advertiser._register = counting  # type: ignore[method-assign]
    await advertiser.start()
    await asyncio.sleep(0.1)
    assert calls >= 3
    await advertiser.stop()


async def test_lan_access_off_is_not_advertised() -> None:
    settings = Settings(_env_file=None, mdns=True, lan_access=False)
    advertiser = discovery.Advertiser(settings)
    await advertiser.start()
    assert advertiser.advertised is None and advertiser._retry is None
    await advertiser.stop()


def test_a_vpn_tunnel_is_not_the_lan_address(monkeypatch: pytest.MonkeyPatch) -> None:
    ip = SimpleNamespace
    adapters = [
        SimpleNamespace(nice_name="utun4", name="utun4", ips=[ip(ip="10.8.0.2")]),
        SimpleNamespace(nice_name="en0", name="en0", ips=[ip(ip="192.168.1.20")]),
        SimpleNamespace(nice_name="lo0", name="lo0", ips=[ip(ip="127.0.0.1")]),
    ]
    import ifaddr

    monkeypatch.setattr(ifaddr, "get_adapters", lambda: adapters)
    monkeypatch.setattr(netinfo, "_route_ip", lambda: "10.8.0.2")  # full-tunnel VPN
    assert netinfo.lan_candidates() == ["192.168.1.20"]
    assert netinfo.lan_ip() == "192.168.1.20"
