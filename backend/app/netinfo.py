"""This machine as other devices on the LAN see it."""

from __future__ import annotations

import ipaddress
import socket

from app.version import version

# Adapters a phone on the LAN cannot reach this machine through: VPN tunnels,
# container and VM bridges, Apple's peer-to-peer links.
_VIRTUAL = ("utun", "tun", "tap", "wg", "ppp", "ipsec", "docker", "br-", "veth",
            "vboxnet", "vmnet", "virbr", "zt", "tailscale", "awdl", "llw", "bridge")


def _route_ip() -> str:
    """The address the default route leaves from. With a full-tunnel VPN up
    this is the tunnel's, which is why it is only a tie-breaker now."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))  # no packet is sent; just picks a route
            ip: str = s.getsockname()[0]
            return ip
    except OSError:
        return ""


def lan_candidates() -> list[str]:
    """Private IPv4 addresses on physical-looking adapters (#186)."""
    try:
        import ifaddr
    except ImportError:  # pragma: no cover - zeroconf brings it
        return []
    found = []
    for adapter in ifaddr.get_adapters():
        name = (adapter.nice_name or adapter.name or "").lower()
        if name.startswith(_VIRTUAL):
            continue
        for entry in adapter.ips:
            if not isinstance(entry.ip, str):
                continue
            ip = ipaddress.ip_address(entry.ip)
            if ip.is_private and not ip.is_loopback and not ip.is_link_local:
                found.append(entry.ip)
    return found


def lan_ip() -> str:
    """This machine's LAN address (for overlay URLs and phones on the LAN).

    Chosen from the interfaces rather than the route to 8.8.8.8, which a
    full-tunnel VPN points at the tunnel; the route only breaks a tie
    between two real adapters (Wi-Fi and Ethernet both up).
    """
    candidates = lan_candidates()
    routed = _route_ip()
    if routed in candidates or (not candidates and routed):
        return routed
    return candidates[0] if candidates else ""


def server_version() -> str:
    return version()
