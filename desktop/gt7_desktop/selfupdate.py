"""Install the update the server downloaded and checked (app.self_update).

The server child fetches a newer release into `<data>/updates/` and checks its
signature and checksum. Installing it is this process's job, since it is the
one being replaced. There are two ways in:

- **Restart and update.** The server writes install.json and exits 77
  (supervisor.EXIT_UPDATE). `install()` checks the release again, installs it,
  and the app comes back as the new version.
- **Quit.** Quit from the menu bar, the tray or Settings › About, with
  on-quit.json present (the update_install_on_quit setting), installs it and
  does not start it again.

Per system:

  macOS    The DMG is mounted and the app copied out beside the installed one,
           on the same volume. It must meet the running app's designated
           requirement (same Team ID and bundle ID) and pass Gatekeeper (it is
           notarized). Once this process has exited, a /bin/sh helper swaps the
           two bundles and deletes the old one: several installed copies
           confuse System Settings' Local Network list (TN3179, FB15568200).
           Restart and update then opens the new one.
  Windows  Restart and update runs Setup with /SILENT /RELAUNCH=1: Setup stops
           this app with `--quit`, as on any update, and starts the new one.
           On Quit (an install "for me only") Setup runs /VERYSILENT once this
           process has exited. A PowerShell helper runs Setup and records how
           it ended. Once Windows builds are signed, Setup must carry this
           exe's signer.

The app's --data-dir and --port, when it was started with them, go to the
new copy too (Keep), so it serves the same data on the same port.
  Linux    The new AppImage is written beside the old one and renamed over it,
           which a running AppImage survives (it keeps the old file open). A
           helper starts it once this process has exited.

A failure is logged and written to failed.json for Settings › About. The old
version keeps running, or stays installed, and the update notice's download
link still works.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.self_update import Staged

log = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008

# Waits for the app (argument 1) to exit, then swaps the new bundle (3) in for
# the installed one (2). On failure the old one is put back and failed.json (6)
# says why. Either way, for Restart and update (4 = 1), the app is opened again
# with the options it ran with (8 onwards: --data-dir, --port). The staging
# folder (5) is removed when the helper made it.
MACOS_SWAP = r"""
pid=$1 target=$2 new=$3 relaunch=$4 stage=$5 failed=$6 tag=$7
shift 7
error=""
i=0
while kill -0 "$pid" 2>/dev/null; do
  i=$((i + 1))
  if [ "$i" -gt 1200 ]; then error="GT7 Datalogger did not quit within two minutes"; break; fi
  sleep 0.1
done
if [ -z "$error" ]; then
  old="$(dirname "$new")/replaced.app"
  rm -rf "$old"
  if ! mv "$target" "$old"; then
    error="the installed app could not be moved aside"
  elif ! mv "$new" "$target"; then
    mv "$old" "$target"
    error="the new app could not be moved into place"
  else
    rm -rf "$old"
    [ -n "$stage" ] && rm -rf "$stage"
  fi
fi
[ -n "$error" ] && printf '{"tag": "%s", "error": "%s"}' "$tag" "$error" > "$failed"
[ "$relaunch" = 1 ] && open "$target" --args --background "$@"
[ -z "$error" ]
"""

# What Restart and update runs Setup with: a progress window and nothing to
# answer; Setup stops this app (`--quit`) and starts the new one (gt7.iss).
RELAUNCH_ARGS = "/SILENT /SUPPRESSMSGBOXES /NORESTART /RELAUNCH=1"
# On Quit, with nobody to watch: no window at all, and nothing started after.
QUIET_ARGS = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART"

# Waits for the app (1) to exit, then starts the AppImage (2) in the
# background, with the options the app ran with (3 onwards).
APPIMAGE_RELAUNCH = r"""
pid=$1 image=$2
shift 2
i=0
while kill -0 "$pid" 2>/dev/null; do
  i=$((i + 1)); [ "$i" -gt 1200 ] && exit 1
  sleep 0.1
done
exec "$image" --background "$@"
"""


class InstallFailed(Exception):
    """Why the update could not be installed, in words for Settings › About."""


@dataclass(frozen=True)
class Install:
    """An install that can replace itself, and what it replaces."""

    kind: str  # an app.self_update.KINDS key
    target: Path  # the .app bundle, the install folder, or the AppImage file
    requirement: str = ""  # macOS: the running app's designated requirement


# --- which kind of install this is ----------------------------------------------


def detect() -> Install | None:
    """This install, when it can update itself; None for notify-only."""
    if not getattr(sys, "frozen", False):
        return None
    if sys.platform == "darwin":
        return _detect_macos(Path(sys.executable))
    if os.name == "nt":
        return _detect_windows(Path(sys.executable), os.environ)
    return _detect_appimage(os.environ.get("APPIMAGE"))


def _detect_macos(exe: Path) -> Install | None:
    bundle = exe.resolve().parents[2]
    if bundle.suffix != ".app" or "AppTranslocation" in bundle.parts:
        # Run straight from Downloads or the DMG, macOS moved it somewhere
        # read-only; there is nothing to replace.
        return None
    if not (os.access(bundle, os.W_OK) and os.access(bundle.parent, os.W_OK)):
        return None
    requirement = designated_requirement(bundle)
    if "anchor apple generic" not in requirement or "subject.OU" not in requirement:
        # An ad-hoc or unsigned build: no Developer ID to hold an update to.
        return None
    return Install("macos-app", bundle, requirement)


def designated_requirement(bundle: Path) -> str:
    try:
        out = subprocess.run(
            ["codesign", "-d", "-r-", str(bundle)], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    for line in (out.stdout + out.stderr).splitlines():
        if line.startswith("designated => "):
            return line.removeprefix("designated => ").strip()
    return ""


def _detect_windows(exe: Path, environ: Mapping[str, str]) -> Install | None:
    folder = exe.resolve().parent
    if not (folder / "unins000.exe").exists():
        return None  # the portable zip: no installer, so notify-only
    roots = [environ.get(v) for v in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)")]
    per_machine = any(root and folder.is_relative_to(Path(root)) for root in roots)
    return Install("windows-setup" if per_machine else "windows-setup-user", folder)


def _detect_appimage(path: str | None) -> Install | None:
    if not path:
        return None  # the tarball
    image = Path(path)
    if not image.is_file() or not os.access(image.parent, os.W_OK):
        return None
    return Install("appimage", image)


# --- the handover files ----------------------------------------------------------


def take_request(data_dir: Path) -> str:
    """The tag Restart and update asked for, once: install.json is removed."""
    from app.self_update import INSTALL, tag_of, updates_dir

    path = updates_dir(data_dir) / INSTALL
    tag = tag_of(path)
    path.unlink(missing_ok=True)
    return tag


def on_quit_tag(data_dir: Path) -> str:
    from app.self_update import ON_QUIT, tag_of, updates_dir

    return tag_of(updates_dir(data_dir) / ON_QUIT)


def record_failure(data_dir: Path, tag: str, error: str) -> None:
    from app.self_update import FAILED, updates_dir, write_json

    try:
        write_json(updates_dir(data_dir) / FAILED, {"tag": tag, "error": error})
    except OSError:
        log.exception("could not record the failed update")


# --- installing ------------------------------------------------------------------------


@dataclass(frozen=True)
class Keep:
    """The options the app runs with that the updated one must run with too.
    None for each one the app was started without, which then stays default."""

    data_dir: Path | None = None
    port: int | None = None

    def args(self) -> list[str]:
        out = ["--data-dir", str(self.data_dir)] if self.data_dir is not None else []
        return out + (["--port", str(self.port)] if self.port is not None else [])


# Started with neither: the updated app starts with the defaults too.
DEFAULTS = Keep()


def install(
    inst: Install, data_dir: Path, logs: Path, tag: str, *, relaunch: bool, keep: Keep = DEFAULTS
) -> bool:
    """Install release `tag`. True when the app must now quit for it to finish."""
    from app.self_update import FAILED, updates_dir

    staged = _staged(inst, data_dir, tag)
    log.info("installing %s (%s) over %s", tag, inst.kind, inst.target)
    log_file = logs / "update.log"
    failed = updates_dir(data_dir) / FAILED
    try:
        if inst.kind == "macos-app":
            return install_macos(inst, staged.file, tag, failed, log_file, relaunch=relaunch,
                                 keep=keep)
        if inst.kind in ("windows-setup", "windows-setup-user"):
            return install_windows(inst, staged.file, tag, failed, relaunch=relaunch, keep=keep)
        if inst.kind == "appimage":
            return install_appimage(inst, staged.file, log_file, relaunch=relaunch, keep=keep)
    except (OSError, subprocess.SubprocessError) as exc:
        raise InstallFailed(f"the update could not be installed ({exc})") from exc
    raise InstallFailed(f"{inst.kind} installs cannot update themselves")


# Each install step below takes a file that has passed every check, and uses
# nothing outside the standard library: desktop/ci/update_smoke.py runs them
# on the signed release files in jobs that install nothing. `pid` is the app
# a helper waits for, this process unless the smoke test says otherwise.


def _staged(inst: Install, data_dir: Path, tag: str) -> Staged:
    from app.self_update import UPDATE_KEYS, UpdateError, updates_dir, verify_staged
    from app.version import version

    try:
        return verify_staged(updates_dir(data_dir) / tag, inst.kind, version(), UPDATE_KEYS)
    except UpdateError as exc:
        raise InstallFailed(str(exc)) from exc


def _spawn(args: list[str], log_file: Path, env: dict[str, str] | None = None) -> None:
    """A helper that outlives this process: its own session, output to log_file."""
    with open(log_file, "ab") as out:
        subprocess.Popen(
            args, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
            start_new_session=True, close_fds=True, env=env,
        )


def _run(args: list[str], what: str, timeout: float = 300) -> None:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise InstallFailed(f"{what} ({detail[-1] if detail else f'exit {result.returncode}'})")


def install_macos(
    inst: Install, dmg: Path, tag: str, failed: Path, log_file: Path, *, relaunch: bool,
    keep: Keep = DEFAULTS, pid: int | None = None,
) -> bool:
    target = inst.target
    # The swap is two renames, so the new bundle must sit on the target's
    # volume: in the updates folder when it is there (it normally is), else
    # in a hidden folder beside the installed app.
    stage_dir = dmg.parent
    made = ""
    if os.stat(stage_dir).st_dev != os.stat(target.parent).st_dev:
        made = tempfile.mkdtemp(prefix=".gt7-update-", dir=target.parent)
        stage_dir = Path(made)
    new = stage_dir / target.name
    shutil.rmtree(new, ignore_errors=True)
    mount = Path(tempfile.mkdtemp(prefix="gt7-update-"))
    try:
        _run(["hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen",
              "-mountpoint", str(mount), str(dmg)], "the disk image did not open")
        try:
            apps = sorted(mount.glob("*.app"))
            if not apps:
                raise InstallFailed("the disk image holds no app")
            # Downloaded by this app, the image carries no quarantine flag; the
            # checks below are the ones Gatekeeper would run on first open.
            _run(["ditto", "--noqtn", str(apps[0]), str(new)],
                 "the app could not be copied off the image")
        finally:
            subprocess.run(["hdiutil", "detach", str(mount), "-force"],
                           capture_output=True, timeout=60)
    finally:
        shutil.rmtree(mount, ignore_errors=True)
    _run(["codesign", "--verify", "--deep", "--strict", f"-R={inst.requirement}", str(new)],
         "the new app is not signed by the same developer")
    _run(["spctl", "--assess", "--type", "execute", str(new)],
         "Gatekeeper refused the new app")
    waited = pid if pid is not None else os.getpid()
    _spawn(["/bin/sh", "-c", MACOS_SWAP, "gt7-update", str(waited), str(target), str(new),
            "1" if relaunch else "0", made, str(failed), tag, *keep.args()], log_file)
    return True


def _ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def authenticode(path: Path) -> tuple[str, str]:
    """Windows: (status, signer subject) of a file's Authenticode signature."""
    script = (
        f"$s = Get-AuthenticodeSignature -LiteralPath {_ps_quote(str(path))}; "
        "@{status=[string]$s.Status; subject=[string]$s.SignerCertificate.Subject} "
        "| ConvertTo-Json -Compress"
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=120, creationflags=CREATE_NO_WINDOW,
    )
    try:
        data = json.loads(out.stdout)
    except ValueError:
        return "Unknown", ""
    return str(data.get("status") or ""), str(data.get("subject") or "")


def check_signer(running: tuple[str, str], setup: tuple[str, str]) -> None:
    """A signed app takes only a Setup with the same signer. An unsigned one (a
    release candidate before Windows signing exists) has none to compare, and
    only an unsigned one: a signature that fails to check is not the same as
    none, and refuses the update."""
    if running[0] == "NotSigned":
        log.warning("this build is not Authenticode-signed; the update's signature "
                    "is checked against the release key only")
        return
    if running[0] != "Valid":
        raise InstallFailed(f"this app's own signature could not be checked ({running[0]})")
    if setup[0] != "Valid" or setup[1] != running[1]:
        raise InstallFailed("the new Setup is not signed by the same publisher")


def setup_arguments(base: str, keep: Keep) -> str:
    """Setup's command line: the app's options go through as /DATADIR and /PORT,
    so Setup stops the right copy and starts the new one the same way (gt7.iss)."""
    out = base
    if keep.data_dir is not None:
        folder = str(keep.data_dir)
        if folder.endswith("\\"):
            folder += "."  # a trailing backslash would escape the closing quote
        out += f' "/DATADIR={folder}"'
    if keep.port is not None:
        out += f" /PORT={int(keep.port)}"
    return out


def setup_helper(setup: Path, arguments: str, failed: Path, tag: str,
                 wait_pid: int | None = None) -> str:
    """PowerShell that runs Setup (after `wait_pid` exits, when given), waits for
    it, and writes failed.json when it fails or never starts. Setup outlives
    this app, which it stops, so only a helper of its own can see how it ends."""
    wait = (f"Wait-Process -Id {int(wait_pid)} -Timeout 120 -ErrorAction SilentlyContinue; "
            if wait_pid is not None else "")
    return (
        wait
        + "try { $p = Start-Process -FilePath " + _ps_quote(str(setup))
        + " -ArgumentList " + _ps_quote(arguments) + " -PassThru -Wait; $code = $p.ExitCode; "
        + "$why = 'the installer stopped before it finished (exit ' + $code + ')' } "
        + "catch { $code = -1; $why = 'the installer did not start: ' + $_.Exception.Message }; "
        + "if ($code -ne 0) { $json = @{tag=" + _ps_quote(tag) + "; error=$why} "
        + "| ConvertTo-Json -Compress; [IO.File]::WriteAllText(" + _ps_quote(str(failed))
        + ", $json) }"
    )


def install_windows(
    inst: Install, setup: Path, tag: str, failed: Path, *, relaunch: bool,
    keep: Keep = DEFAULTS, pid: int | None = None,
) -> bool:
    check_signer(authenticode(Path(sys.executable)), authenticode(setup))
    if relaunch:
        # Start-Process goes through ShellExecute, so Setup can ask for
        # administrator approval. This app keeps running until Setup stops it:
        # a refused prompt leaves it as it was, and failed.json says so.
        script = setup_helper(setup, setup_arguments(RELAUNCH_ARGS, keep), failed, tag)
    elif inst.kind == "windows-setup-user":
        waited = pid if pid is not None else os.getpid()
        script = setup_helper(setup, setup_arguments(QUIET_ARGS, keep), failed, tag, waited)
    else:
        raise InstallFailed("an install for every user installs updates only on request")
    subprocess.Popen(
        ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
         "-Command", script],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW, close_fds=True,
    )
    return not relaunch


def relaunch_env(environ: dict[str, str]) -> dict[str, str]:
    """The environment for a fresh copy of the app: none of this one's AppImage
    mount or PyInstaller state, which would point the new one at the old."""
    env = {
        k: v for k, v in environ.items()
        if not k.startswith("_PYI") and k not in ("APPIMAGE", "APPDIR", "ARGV0", "OWD")
    }
    original = env.pop("LD_LIBRARY_PATH_ORIG", None)
    if original is not None:
        env["LD_LIBRARY_PATH"] = original
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install_appimage(
    inst: Install, new: Path, log_file: Path, *, relaunch: bool, keep: Keep = DEFAULTS,
    pid: int | None = None,
) -> bool:
    image = inst.target
    part = image.with_name(f".{image.name}.update-{os.getpid()}")
    try:
        shutil.copyfile(new, part)
        os.chmod(part, (image.stat().st_mode & 0o7777) | 0o755)
        with open(part, "rb") as fh:
            os.fsync(fh.fileno())
        if _sha256(part) != _sha256(new):
            raise InstallFailed("the new AppImage did not copy intact")
        os.replace(part, image)
    finally:
        part.unlink(missing_ok=True)
    if relaunch:
        waited = pid if pid is not None else os.getpid()
        _spawn(["/bin/sh", "-c", APPIMAGE_RELAUNCH, "gt7-update", str(waited), str(image),
                *keep.args()], log_file, env=relaunch_env(dict(os.environ)))
    return True
