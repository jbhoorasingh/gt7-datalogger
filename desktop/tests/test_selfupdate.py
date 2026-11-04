"""Installing a downloaded update: which installs can, and the install steps
that run anywhere (the AppImage swap, the macOS bundle swap's shell half, the
supervisor's exit 77). The macOS disk image and Windows Setup paths need the
signed release files and run in desktop.yml's verify jobs."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from gt7_desktop import main, paths, ports, selfupdate
from gt7_desktop.selfupdate import Install, InstallFailed
from gt7_desktop.supervisor import Supervisor

POSIX = pytest.mark.skipif(os.name == "nt", reason="POSIX shell helpers")


def dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def wait_until(cond, seconds: float = 15) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return False


# --- which installs can update themselves ---------------------------------------------


def test_a_source_checkout_is_notify_only() -> None:
    assert not getattr(sys, "frozen", False)
    assert selfupdate.detect() is None


def test_windows_tells_the_installer_from_the_portable_zip(tmp_path: Path) -> None:
    machine = tmp_path / "Program Files" / "GT7 Datalogger"
    user = tmp_path / "Local" / "Programs" / "GT7 Datalogger"
    portable = tmp_path / "Downloads" / "GT7 Datalogger"
    for folder in (machine, user, portable):
        folder.mkdir(parents=True)
        (folder / "gt7-datalogger.exe").write_bytes(b"")
    for folder in (machine, user):
        (folder / "unins000.exe").write_bytes(b"")
    env = {"ProgramFiles": str(tmp_path / "Program Files")}
    found = selfupdate._detect_windows(machine / "gt7-datalogger.exe", env)
    assert found == Install("windows-setup", machine.resolve())
    found = selfupdate._detect_windows(user / "gt7-datalogger.exe", env)
    assert found is not None and found.kind == "windows-setup-user"
    assert selfupdate._detect_windows(portable / "gt7-datalogger.exe", env) is None


def test_an_appimage_updates_only_where_it_can_write(tmp_path: Path) -> None:
    image = tmp_path / "GT7.AppImage"
    assert selfupdate._detect_appimage(None) is None  # the tarball
    assert selfupdate._detect_appimage(str(image)) is None  # gone
    image.write_bytes(b"")
    assert selfupdate._detect_appimage(str(image)) == Install("appimage", image)


def test_a_macos_app_needs_a_developer_id_and_a_writable_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "Applications" / "GT7 Datalogger.app"
    exe = bundle / "Contents" / "MacOS" / "gt7-datalogger"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    developer_id = ('anchor apple generic and identifier "io.github.jbhoorasingh.gt7datalogger" '
                    "and certificate leaf[subject.OU] = J8SQ64Y2NC")
    monkeypatch.setattr(selfupdate, "designated_requirement", lambda _b: developer_id)
    assert selfupdate._detect_macos(exe) == Install("macos-app", bundle.resolve(), developer_id)
    monkeypatch.setattr(selfupdate, "designated_requirement", lambda _b: 'cdhash H"00"')
    assert selfupdate._detect_macos(exe) is None  # ad-hoc: a local build
    translocated = tmp_path / "AppTranslocation" / "X" / "d" / "GT7 Datalogger.app"
    moved = translocated / "Contents" / "MacOS" / "gt7-datalogger"
    moved.parent.mkdir(parents=True)
    moved.write_bytes(b"")
    monkeypatch.setattr(selfupdate, "designated_requirement", lambda _b: developer_id)
    assert selfupdate._detect_macos(moved) is None


def test_only_the_supervised_app_is_told_it_can_update(tmp_path: Path) -> None:
    f = paths.folders(tmp_path)
    paths.create(f)
    assert main.server_env(f, gui=True, update_kind="appimage")["GT7_UPDATE_INSTALL"] == "appimage"
    assert "GT7_UPDATE_INSTALL" not in main.server_env(f, gui=False, update_kind="appimage")
    assert "GT7_UPDATE_INSTALL" not in main.server_env(f, gui=True)


def test_a_signed_build_takes_only_its_own_publisher() -> None:
    signed = ("Valid", "CN=Jermaine Bhoorasingh, O=Jermaine Bhoorasingh")
    selfupdate.check_signer(signed, signed)
    with pytest.raises(InstallFailed, match="same publisher"):
        selfupdate.check_signer(signed, ("Valid", "CN=Someone Else"))
    with pytest.raises(InstallFailed, match="same publisher"):
        selfupdate.check_signer(signed, ("NotSigned", ""))
    # An unsigned release candidate has no signer to compare.
    selfupdate.check_signer(("NotSigned", ""), ("NotSigned", ""))


def test_a_relaunched_copy_leaves_this_ones_mount_behind() -> None:
    env = selfupdate.relaunch_env({
        "PATH": "/usr/bin", "APPIMAGE": "/a/old.AppImage", "APPDIR": "/tmp/.mount_x",
        "_PYI_APPLICATION_HOME_DIR": "/tmp/.mount_x/usr/lib", "LD_LIBRARY_PATH": "/tmp/.mount_x",
        "LD_LIBRARY_PATH_ORIG": "/opt/lib",
    })
    assert env == {"PATH": "/usr/bin", "LD_LIBRARY_PATH": "/opt/lib",
                   "PYINSTALLER_RESET_ENVIRONMENT": "1"}


# --- the handover files and a staged release --------------------------------------------


@pytest.fixture
def staged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Stage release v9.0.0 of `name` as the server would, under a test key."""
    from Crypto.PublicKey import ECC
    from Crypto.Signature import eddsa

    from app import self_update

    key = ECC.generate(curve="ed25519")
    public = base64.b64encode(key.public_key().export_key(format="raw")).decode()
    monkeypatch.setattr(self_update, "UPDATE_KEYS", (public,))

    def stage(name: str, body: bytes) -> Path:
        folder = self_update.updates_dir(tmp_path / "data") / "v9.0.0"
        folder.mkdir(parents=True)
        doc = json.dumps({"version": "9.0.0", "tag": "v9.0.0", "assets": [
            {"name": name, "size": len(body), "sha256": hashlib.sha256(body).hexdigest()}]})
        (folder / self_update.MANIFEST).write_bytes(doc.encode())
        (folder / self_update.SIGNATURE).write_bytes(
            base64.b64encode(eddsa.new(key, "rfc8032").sign(doc.encode())))
        (folder / name).write_bytes(body)
        self_update.write_json(folder / self_update.STAGED, {"tag": "v9.0.0"})
        return folder

    return stage


def test_the_handover_files_round_trip(tmp_path: Path) -> None:
    from app.self_update import updates_dir, write_json

    data = tmp_path / "data"
    updates_dir(data).mkdir(parents=True)
    assert selfupdate.take_request(data) == ""
    write_json(updates_dir(data) / "install.json", {"tag": "v9.0.0"})
    assert selfupdate.take_request(data) == "v9.0.0"
    assert selfupdate.take_request(data) == ""  # taken once
    write_json(updates_dir(data) / "on-quit.json", {"tag": "../../etc"})
    assert selfupdate.on_quit_tag(data) == ""  # never a path
    selfupdate.record_failure(data, "v9.0.0", "boom")
    assert json.loads((updates_dir(data) / "failed.json").read_text()) == {
        "tag": "v9.0.0", "error": "boom"}


def test_a_staged_release_is_checked_again_before_it_installs(
    tmp_path: Path, staged
) -> None:
    from app.self_update import KINDS

    folder = staged(KINDS["appimage"], b"new")
    (folder / KINDS["appimage"]).write_bytes(b"swapped")
    image = tmp_path / "GT7.AppImage"
    image.write_bytes(b"old")
    with pytest.raises(InstallFailed, match="checksum"):
        selfupdate.install(Install("appimage", image), tmp_path / "data", tmp_path, "v9.0.0",
                           relaunch=False)
    assert image.read_bytes() == b"old"


@POSIX
def test_an_appimage_is_replaced_and_started_again(
    tmp_path: Path, staged, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.self_update import KINDS

    started = tmp_path / "started.json"
    new = (
        "#!/bin/sh\n"
        f"printf '{{\"args\": \"%s\", \"appimage\": \"%s\"}}' \"$*\" \"$APPIMAGE\" > '{started}'\n"
    ).encode()
    staged(KINDS["appimage"], new)
    image = tmp_path / "Apps" / "GT7.AppImage"
    image.parent.mkdir()
    image.write_bytes(b"#!/bin/sh\nexit 1\n")
    image.chmod(0o700)
    monkeypatch.setenv("APPIMAGE", str(image))
    monkeypatch.setattr(selfupdate.os, "getpid", dead_pid)
    assert selfupdate.install(Install("appimage", image), tmp_path / "data", tmp_path,
                              "v9.0.0", relaunch=True) is True
    assert image.read_bytes() == new
    assert image.stat().st_mode & 0o777 == 0o755
    assert list(image.parent.iterdir()) == [image]  # no leftover part file
    assert wait_until(lambda: started.exists() and started.read_text())
    assert json.loads(started.read_text()) == {"args": "--background", "appimage": ""}


@POSIX
def test_the_macos_swap_replaces_the_bundle_once_the_app_is_gone(tmp_path: Path) -> None:
    target = tmp_path / "Applications" / "GT7 Datalogger.app"
    (target / "Contents").mkdir(parents=True)
    (target / "Contents" / "old").write_text("1.0.0")
    stage = tmp_path / "updates" / "v9.0.0"
    new = stage / "GT7 Datalogger.app"
    (new / "Contents").mkdir(parents=True)
    (new / "Contents" / "new").write_text("9.0.0")
    failed = tmp_path / "failed.json"
    subprocess.run(["/bin/sh", "-c", selfupdate.MACOS_SWAP, "gt7-update", str(dead_pid()),
                    str(target), str(new), "0", "", str(failed), "v9.0.0"], check=True)
    assert [p.name for p in (target / "Contents").iterdir()] == ["new"]
    assert not new.exists() and not (stage / "replaced.app").exists()
    assert not failed.exists()


@POSIX
def test_a_failed_macos_swap_puts_the_old_app_back(tmp_path: Path) -> None:
    target = tmp_path / "Applications" / "GT7 Datalogger.app"
    (target / "Contents").mkdir(parents=True)
    (target / "Contents" / "old").write_text("1.0.0")
    failed = tmp_path / "failed.json"
    gone = tmp_path / "updates" / "v9.0.0" / "GT7 Datalogger.app"
    gone.parent.mkdir(parents=True)
    result = subprocess.run(["/bin/sh", "-c", selfupdate.MACOS_SWAP, "gt7-update",
                             str(dead_pid()), str(target), str(gone), "0", "", str(failed),
                             "v9.0.0"], capture_output=True)
    assert result.returncode == 1
    assert (target / "Contents" / "old").read_text() == "1.0.0"
    assert json.loads(failed.read_text()) == {
        "tag": "v9.0.0", "error": "the new app could not be moved into place"}


# --- the supervisor ---------------------------------------------------------------------


def fake_child(code: int) -> list[str]:
    """A child that exits `code` at once, before /api/health ever answers."""
    return [sys.executable, "-c", f"import sys, time; time.sleep(0.3); sys.exit({code})"]


def test_exit_77_installs_and_ends_supervision(tmp_path: Path) -> None:
    f = paths.folders(tmp_path)
    paths.create(f)
    sock, _ = ports.choose("127.0.0.1", 0, explicit=True)
    calls: list[int] = []
    done = threading.Event()

    def on_update() -> bool:
        calls.append(1)
        done.set()
        return True

    sup = Supervisor(sock, dict(os.environ), f.log_file, command=fake_child(77),
                     on_update=on_update)
    sup.start()
    try:
        assert done.wait(30)
        assert wait_until(lambda: sup.state.phase == "stopped")
        time.sleep(1.0)
        assert calls == [1]  # no new child after an install that quits the app
    finally:
        sup.stop()
        sock.close()


def test_exit_77_that_cannot_quit_starts_the_next_child(tmp_path: Path) -> None:
    f = paths.folders(tmp_path)
    paths.create(f)
    sock, _ = ports.choose("127.0.0.1", 0, explicit=True)
    calls: list[int] = []
    sup = Supervisor(sock, dict(os.environ), f.log_file, command=fake_child(77),
                     on_update=lambda: calls.append(1) is not None)
    sup.start()
    try:
        assert wait_until(lambda: len(calls) >= 2, 30)
        assert sup.state.phase != "failed"  # not counted as crashes
    finally:
        sup.stop()
        sock.close()


def test_the_app_installs_on_a_users_quit_only(tmp_path: Path, monkeypatch) -> None:
    f = paths.folders(tmp_path)
    paths.create(f)
    app = main.DesktopApp(main.parse([]), f)
    app.install = Install("appimage", tmp_path / "GT7.AppImage")
    from app.self_update import updates_dir, write_json

    updates_dir(f.data).mkdir(parents=True)
    write_json(updates_dir(f.data) / "on-quit.json", {"tag": "v9.0.0"})
    installed: list[tuple[str, bool]] = []
    monkeypatch.setattr(selfupdate, "install",
                        lambda inst, data, logs, tag, relaunch: installed.append((tag, relaunch)))

    class Sup:
        def stop(self) -> None:
            pass

    app.supervisor = Sup()
    app.quit()  # a signal, logout, or the installer's --quit
    assert installed == []

    again = main.DesktopApp(main.parse([]), f)
    again.install = app.install
    again.supervisor = Sup()
    again.user_quit = True
    again.quit()
    assert installed == [("v9.0.0", False)]
