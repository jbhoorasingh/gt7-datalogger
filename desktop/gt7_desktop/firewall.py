"""Windows Firewall rules for this executable (#194, #199).

Program-scoped, not port-scoped: the telemetry ports are editable in
Settings. Allowed on the Private AND Public profiles, remote LocalSubnet
only — Windows marks a new network Public, so a Private-only rule would
silently block telemetry at home. The installer runs `--firewall install`
elevated; "Repair firewall" relaunches the same signed exe elevated with
`--firewall repair`, which also clears block rules a "Cancel" left behind.
"""

from __future__ import annotations

import os
import subprocess
import sys

RULES = (("GT7 Datalogger (UDP in)", "UDP"), ("GT7 Datalogger (TCP in)", "TCP"))


def _netsh(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["netsh", "advfirewall", "firewall", *args], capture_output=True, text=True, check=False
    )


def rules_present() -> bool:
    if os.name != "nt":
        return True
    return all(_netsh("show", "rule", f"name={name}").returncode == 0 for name, _p in RULES)


def install() -> bool:
    exe = sys.executable
    ok = True
    for name, protocol in RULES:
        _netsh("delete", "rule", f"name={name}")
        result = _netsh(
            "add", "rule", f"name={name}", "dir=in", "action=allow", f"program={exe}",
            f"protocol={protocol}", "profile=private,public", "remoteip=localsubnet",
            "enable=yes",
        )
        ok = ok and result.returncode == 0
    return ok


def remove() -> bool:
    for name, _protocol in RULES:
        _netsh("delete", "rule", f"name={name}")
    return True


def repair() -> bool:
    # Every rule for this program, the block rules a "Cancel" created included.
    _netsh("delete", "rule", "name=all", f"program={sys.executable}")
    return install()


def repair_elevated() -> None:
    """Relaunch this exe elevated (one UAC prompt) to repair the rules."""
    if os.name != "nt":
        return
    import ctypes

    ctypes.windll.shell32.ShellExecuteW(  # type: ignore[attr-defined]
        None, "runas", sys.executable, "--firewall repair", None, 0
    )


def run(action: str) -> int:
    if os.name != "nt":
        print("--firewall is for Windows only", file=sys.stderr)
        return 2
    done = {"install": install, "remove": remove, "repair": repair}[action]()
    return 0 if done else 1
