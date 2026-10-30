"""One version source, and the script that bumps and checks it (#179)."""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

from app import netinfo, version
from app.processing import track_compile
from app.sync import transport

ROOT = Path(__file__).resolve().parents[2]


def _load_script():
    spec = importlib.util.spec_from_file_location("version_script", ROOT / "scripts" / "version.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["version_script"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The script pointed at copies of the four files, never the real ones."""
    module = _load_script()
    for name in ("PYPROJECT", "VERSION_PY", "PACKAGE_JSON", "PACKAGE_LOCK"):
        real: Path = getattr(module, name)
        copy = tmp_path / real.relative_to(ROOT)
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(real, copy)
        monkeypatch.setattr(module, name, copy)
    monkeypatch.setattr(module, "BUILD_INFO", tmp_path / "backend/app/_build_info.py")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    for var in ("GITHUB_REF", "GITHUB_REF_TYPE", "GITHUB_REF_NAME", "GITHUB_SHA"):
        monkeypatch.delenv(var, raising=False)
    return module


def test_every_reader_reports_the_committed_version() -> None:
    from app._version import __version__

    assert version.version() == __version__
    assert netinfo.server_version() == __version__
    assert track_compile.app_version() == __version__
    assert transport._user_agent() == f"gt7-datalogger/{__version__}"


def test_the_repository_is_consistent() -> None:
    assert _load_script().check() == []


def test_bump_rewrites_all_five_places_and_nothing_else(script) -> None:
    lock_before = script.PACKAGE_LOCK.read_text()
    script.bump("1.2.3")
    assert set(script.read_all().values()) == {"1.2.3"}
    assert script.check() == []
    # Exactly the two root lines changed: the lock is edited, not re-serialised.
    after = script.PACKAGE_LOCK.read_text().splitlines()
    changed = [(a, b) for a, b in zip(lock_before.splitlines(), after, strict=True) if a != b]
    assert len(changed) == 2
    assert all('"version": "1.2.3"' in b for _, b in changed)


def test_check_names_the_place_that_disagrees(script) -> None:
    text = script.VERSION_PY.read_text().replace(script.current(), "9.9.9")
    script.VERSION_PY.write_text(text)
    [problem] = script.check()
    assert "backend/app/_version.py = 9.9.9" in problem


@pytest.mark.parametrize(("tag", "ok"), [
    ("v{v}", True),
    ("v{v}-rc.3", True),
    ("v0.0.1", False),
    ("v{v}-beta.1", False),
])
def test_check_compares_the_tag(
    script, monkeypatch: pytest.MonkeyPatch, tag: str, ok: bool
) -> None:
    monkeypatch.setenv("GITHUB_REF", "refs/tags/" + tag.format(v=script.current()))
    assert (script.check() == []) is ok


def test_stamp_carries_the_rc_suffix_or_the_commit(script, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_REF", f"refs/tags/v{script.current()}-rc.2")
    assert script.stamp() == "-rc.2"
    assert 'SUFFIX = "-rc.2"' in script.BUILD_INFO.read_text()
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_SHA", "1a2b3c4d5e6f")
    assert script.stamp() == "+g1a2b3c4"


def test_bump_refuses_a_pre_release(script) -> None:
    with pytest.raises(script.VersionError):
        script.bump("1.0.0-rc.1")
