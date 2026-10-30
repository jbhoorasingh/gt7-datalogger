"""Windows-safe file writes (#187): the atomic write and its retry, the
no-overwrite claim, and the file names Windows reserves."""

import errno
import os
import threading
import time

import pytest

from app.api.tracks import _claim_upload_path
from app.processing import track_bundle, track_compile, track_corrections
from app.storage import atomic, backup
from app.storage.atomic import atomic_write_bytes, atomic_write_text
from tests.test_track_corrections import FIRST_FOUR, _area, _document
from tests.test_track_manager import _foreign_bundle

# --- the write -----------------------------------------------------------------------


def test_a_write_lands_whole_and_leaves_no_temp_file(tmp_path) -> None:
    path = tmp_path / "doc.json"
    atomic_write_text(path, "one\n")
    atomic_write_text(path, "two\nlines\n")
    # Newlines as given, on every platform: no \r\n on Windows.
    assert path.read_bytes() == b"two\nlines\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["doc.json"]


def test_a_leftover_temp_file_is_not_reused(tmp_path) -> None:
    path = tmp_path / "doc.json"
    (tmp_path / "doc.json.tmp").write_text("half a document from a crash")
    atomic_write_bytes(path, b"{}")
    assert path.read_bytes() == b"{}"
    assert not (tmp_path / "doc.json.tmp").exists()


def test_a_failed_move_takes_its_temp_file_with_it(tmp_path, monkeypatch) -> None:
    def refuse(source, target):
        raise OSError(errno.EIO, "disk on fire")

    monkeypatch.setattr(atomic.os, "replace", refuse)
    with pytest.raises(OSError, match="disk on fire"):
        atomic_write_text(tmp_path / "doc.json", "{}")
    assert list(tmp_path.iterdir()) == []


# --- the retry -----------------------------------------------------------------------


@pytest.fixture
def held(monkeypatch):
    """os.replace refused `times` times, as Windows refuses it while a scanner
    holds the target open; then the real thing. Sleeps are recorded, not slept."""
    real = os.replace
    state = {"left": 0, "calls": 0, "slept": []}

    def replace(source, target):
        state["calls"] += 1
        if state["left"] > 0:
            state["left"] -= 1
            raise PermissionError(errno.EACCES, "in use by another process", str(target))
        real(source, target)

    def arm(times: int, windows: bool = True) -> dict:
        state["left"] = times
        monkeypatch.setattr(atomic, "_windows", lambda: windows)
        monkeypatch.setattr(atomic.os, "replace", replace)
        monkeypatch.setattr(atomic.time, "sleep", state["slept"].append)
        return state

    return arm


def test_windows_waits_out_a_target_held_open(tmp_path, held) -> None:
    path = tmp_path / "doc.json"
    path.write_text("old")
    state = held(3)
    atomic_write_text(path, "new")
    assert path.read_text() == "new"
    assert state["calls"] == 4
    assert state["slept"] == list(atomic.RETRY_DELAYS[:3])
    assert all(0.05 <= s <= 0.2 for s in atomic.RETRY_DELAYS)


def test_windows_gives_up_after_five_retries(tmp_path, held) -> None:
    path = tmp_path / "doc.json"
    path.write_text("old")
    state = held(len(atomic.RETRY_DELAYS) + 1)
    with pytest.raises(PermissionError):
        atomic_write_text(path, "new")
    assert state["calls"] == 6 and len(state["slept"]) == 5
    assert path.read_text() == "old"  # never half replaced
    assert not (tmp_path / "doc.json.tmp").exists()


def test_elsewhere_a_permission_error_is_the_answer(tmp_path, held) -> None:
    state = held(1, windows=False)
    with pytest.raises(PermissionError):
        atomic_write_text(tmp_path / "doc.json", "new")
    assert state["calls"] == 1 and state["slept"] == []


def test_a_renamed_bundle_carries_its_corrections_past_a_held_target(tmp_path, held) -> None:
    mine = track_corrections.validate(_document(_area(FIRST_FOUR)))
    track_corrections.write(tmp_path, "ring", mine)
    held(2)
    assert track_corrections.move(tmp_path, "ring", "ring-gp")
    assert track_corrections.load(tmp_path, "ring-gp") == mine


@pytest.mark.skipif(os.name != "nt", reason="only Windows refuses to replace an open file")
def test_a_target_held_open_by_another_thread(tmp_path) -> None:
    """The real thing on the Windows CI leg (#177): the target is held open,
    as a scanner would, and let go while the write is waiting."""
    path = tmp_path / "doc.json"
    path.write_text("old")
    opened = threading.Event()

    def hold() -> None:
        with path.open("rb"):
            opened.set()
            time.sleep(0.15)

    holder = threading.Thread(target=hold)
    holder.start()
    opened.wait()
    try:
        atomic_write_text(path, "new")
    finally:
        holder.join()
    assert path.read_text() == "new"


# --- the claim -----------------------------------------------------------------------


@pytest.fixture(params=["link", "copy"])
def claiming(request, monkeypatch):
    """Both ways a claim is made: a hard link, and where the filesystem has
    none (FAT32, exFAT, SMB), an exclusive create and a copy."""
    if request.param == "copy":
        def no_link(source, target):
            raise OSError(errno.EPERM, "Operation not permitted")

        monkeypatch.setattr(atomic.os, "link", no_link)
    return request.param


def test_a_claim_never_lands_over_an_existing_file(tmp_path, claiming) -> None:
    part = tmp_path / "upload.part"
    part.write_bytes(b"theirs\n")
    target = tmp_path / "log.jsonl"
    assert atomic.claim(part, target)
    assert target.read_bytes() == b"theirs\n"
    assert part.exists()  # the caller's to remove

    other = tmp_path / "other.part"  # not `part`: a link shares its bytes
    other.write_bytes(b"someone else's\n")
    assert not atomic.claim(other, target)
    assert target.read_bytes() == b"theirs\n"


def test_an_upload_lands_beside_a_log_of_the_same_name(tmp_path, claiming) -> None:
    name = "surface_survey_20260801_120000.jsonl"
    (tmp_path / name).write_bytes(b"first\n")
    part = tmp_path / ".upload.part"
    part.write_bytes(b"second\n")
    landed = _claim_upload_path(tmp_path, name, part)
    assert landed.name == "surface_survey_20260801_120000_2.jsonl"
    assert landed.read_bytes() == b"second\n"
    assert (tmp_path / name).read_bytes() == b"first\n"


def test_a_failed_copy_leaves_no_half_file(tmp_path, monkeypatch) -> None:
    def no_link(source, target):
        raise OSError(errno.EPERM, "Operation not permitted")

    def fail(src, dst, *args):
        dst.write(b"half")
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(atomic.os, "link", no_link)
    monkeypatch.setattr(atomic.shutil, "copyfileobj", fail)
    part = tmp_path / "upload.part"
    part.write_bytes(b"whole\n")
    with pytest.raises(OSError, match="No space"):
        atomic.claim(part, tmp_path / "log.jsonl")
    assert not (tmp_path / "log.jsonl").exists()


# --- reserved names ------------------------------------------------------------------


@pytest.mark.parametrize("slug", ["con", "prn", "aux", "nul", "com1", "com9", "lpt1", "lpt9"])
def test_a_reserved_slug_gets_a_file_name_windows_will_open(slug) -> None:
    name = track_bundle.slug_filename(slug)
    assert name == f"{slug}_.json"
    assert track_bundle.filename_slug(name.removesuffix(".json")) == slug


@pytest.mark.parametrize("slug", ["ring", "con-gp", "console", "com10", "lpt", "nul-1"])
def test_other_slugs_are_their_own_file_name(slug) -> None:
    assert track_bundle.slug_filename(slug) == f"{slug}.json"
    assert track_bundle.filename_slug(slug) == slug


def test_a_circuit_called_con_round_trips(tmp_path) -> None:
    """Saved, listed, loaded, compiled, corrected and deleted as slug `con`,
    from a file named `con_.json`."""
    doc = track_bundle.validate_document(_foreign_bundle(track="CON"))
    result = track_bundle.merge_document(tmp_path, doc)
    assert result["slug"] == "con"
    bundles = tmp_path / track_bundle.BUNDLE_DIR
    assert (bundles / "con_.json").is_file()
    assert not (bundles / "con.json").exists()

    assert [b["slug"] for b in track_bundle.list_bundles(tmp_path)] == ["con"]
    loaded = track_bundle.load_slug(tmp_path, "con")
    assert loaded is not None and loaded["meta"]["track"] == "CON"

    corrections = track_corrections.validate(_document(_area(FIRST_FOUR), track="CON"))
    track_corrections.write(tmp_path, "con", corrections)
    assert (bundles / track_corrections.DIRECTORY / "con_.json").is_file()
    assert track_corrections.load(tmp_path, "con") == corrections

    assert track_compile.for_track(tmp_path, "CON") is not None
    assert (bundles / track_compile.COMPILED_DIR / "con_.json").is_file()

    # A backup names the files by slug, so it restores on any platform.
    names = [name for name, _ in backup.survey_files(tmp_path)]
    assert f"{track_bundle.BUNDLE_DIR}/con.json" in names
    assert f"{track_bundle.BUNDLE_DIR}/{track_corrections.DIRECTORY}/con.json" in names

    assert track_bundle.delete(tmp_path, "con")
    assert not (bundles / "con_.json").exists()
    assert track_bundle.list_bundles(tmp_path) == []
