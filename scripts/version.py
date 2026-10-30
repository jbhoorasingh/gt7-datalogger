#!/usr/bin/env python3
"""The release version, kept in five places in four files (#179).

    python scripts/version.py --print        the version, e.g. 0.8.1
    python scripts/version.py check          every place agrees; on a tag build,
                                             the tag (minus -rc.N) matches too
    python scripts/version.py bump 0.9.0     rewrite all five places
    python scripts/version.py stamp          CI only: write the gitignored
                                             backend/app/_build_info.py

The five places: backend/pyproject.toml, backend/app/_version.py,
frontend/package.json and the two root entries of frontend/package-lock.json.
The lock is edited line by line, never re-serialised, so npm's own formatting
and every other entry stay byte-for-byte as they were.

A pre-release lives only in the tag (vX.Y.Z-rc.N): the files always hold the
plain X.Y.Z, and `stamp` adds "-rc.N" — or "+g<sha>" for a build off a tag —
to what the running app reports. Stdlib only: it runs before anything is
installed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "backend" / "pyproject.toml"
VERSION_PY = ROOT / "backend" / "app" / "_version.py"
BUILD_INFO = ROOT / "backend" / "app" / "_build_info.py"
PACKAGE_JSON = ROOT / "frontend" / "package.json"
PACKAGE_LOCK = ROOT / "frontend" / "package-lock.json"

PLAIN = re.compile(r"^\d+\.\d+\.\d+$")
TAG = re.compile(r"^v(?P<version>\d+\.\d+\.\d+)(?P<rc>-rc\.\d+)?$")
_PYPROJECT_LINE = re.compile(r'^version = "([^"]*)"$', re.M)
_VERSION_PY_LINE = re.compile(r'^__version__ = "([^"]*)"$', re.M)
_JSON_LINE = re.compile(r'^(  "version": ")([^"]*)(",?)$', re.M)
# The lock's own version, then the root package's: the first two "version"
# lines at two and six spaces of indent. Every dependency is deeper.
_LOCK_LINES = (
    re.compile(r'^(  "version": ")([^"]*)(",?)$', re.M),
    re.compile(r'^(      "version": ")([^"]*)(",?)$', re.M),
)


# Explicit, so a Windows runner does not read the files as cp1252.
UTF8 = {"encoding": "utf-8"}


class VersionError(Exception):
    pass


def _one(pattern: re.Pattern[str], text: str, where: Path, group: int = 1) -> str:
    match = pattern.search(text)
    if match is None:
        raise VersionError(f"{where.relative_to(ROOT)}: no version line found")
    return match.group(group)


def read_all() -> dict[str, str]:
    """Each place's version, labelled by where it was read."""
    lock = PACKAGE_LOCK.read_text(**UTF8)
    return {
        "backend/pyproject.toml": _one(_PYPROJECT_LINE, PYPROJECT.read_text(**UTF8), PYPROJECT),
        "backend/app/_version.py": _one(_VERSION_PY_LINE, VERSION_PY.read_text(**UTF8), VERSION_PY),
        "frontend/package.json": _one(_JSON_LINE, PACKAGE_JSON.read_text(**UTF8), PACKAGE_JSON, 2),
        "frontend/package-lock.json (lock)": _one(_LOCK_LINES[0], lock, PACKAGE_LOCK, 2),
        "frontend/package-lock.json (root package)": _one(_LOCK_LINES[1], lock, PACKAGE_LOCK, 2),
    }


def current() -> str:
    return read_all()["backend/app/_version.py"]


def _tag() -> str | None:
    """The tag this build is for, from GitHub Actions' environment."""
    if os.environ.get("GITHUB_REF_TYPE") == "tag":
        return os.environ.get("GITHUB_REF_NAME") or None
    ref = os.environ.get("GITHUB_REF", "")
    return ref.removeprefix("refs/tags/") if ref.startswith("refs/tags/") else None


def check() -> list[str]:
    """Every disagreement, as a sentence. Empty means all is well."""
    found = read_all()
    problems = []
    if len(set(found.values())) != 1:
        problems.append(
            "versions disagree: " + ", ".join(f"{k} = {v}" for k, v in found.items())
        )
    version = found["backend/app/_version.py"]
    if not PLAIN.match(version):
        problems.append(f"{version!r} is not plain X.Y.Z; a pre-release lives in the tag")
    tag = _tag()
    if tag is not None:
        match = TAG.match(tag)
        if match is None:
            problems.append(f"tag {tag!r} is not vX.Y.Z or vX.Y.Z-rc.N")
        elif match["version"] != version:
            problems.append(f"tag {tag} does not match the files' {version}")
    return problems


def _replace(pattern: re.Pattern[str], text: str, new: str, where: Path) -> str:
    def swap(match: re.Match[str]) -> str:
        if pattern.groups == 1:
            return match.group(0).replace(match.group(1), new)
        return match.group(1) + new + match.group(3)

    out, count = pattern.subn(swap, text, count=1)
    if count != 1:
        raise VersionError(f"{where.relative_to(ROOT)}: no version line found")
    return out


def bump(new: str) -> None:
    if not PLAIN.match(new):
        raise VersionError(f"{new!r} is not X.Y.Z")
    PYPROJECT.write_text(_replace(_PYPROJECT_LINE, PYPROJECT.read_text(**UTF8), new, PYPROJECT))
    VERSION_PY.write_text(_replace(_VERSION_PY_LINE, VERSION_PY.read_text(**UTF8), new, VERSION_PY))
    PACKAGE_JSON.write_text(_replace(_JSON_LINE, PACKAGE_JSON.read_text(**UTF8), new, PACKAGE_JSON))
    lock = PACKAGE_LOCK.read_text(**UTF8)
    for pattern in _LOCK_LINES:
        lock = _replace(pattern, lock, new, PACKAGE_LOCK)
    PACKAGE_LOCK.write_text(lock, **UTF8)


def _short_sha() -> str:
    sha = os.environ.get("GITHUB_SHA", "")
    if sha:
        return sha[:7]
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return out.stdout.strip()


def stamp() -> str:
    """Write _build_info.py and return the suffix it carries."""
    tag = _tag()
    match = TAG.match(tag) if tag else None
    if match is not None:
        suffix = match["rc"] or ""
    else:
        sha = _short_sha()
        suffix = f"+g{sha}" if sha else ""
    BUILD_INFO.write_text(
        '"""Written by scripts/version.py stamp for this build; never committed."""\n\n'
        f"SUFFIX = {json.dumps(suffix)}\n"
    )
    return suffix


def main(argv: list[str]) -> int:
    try:
        if argv == ["--print"]:
            print(current())
        elif argv == ["check"]:
            problems = check()
            for problem in problems:
                print(f"version check: {problem}", file=sys.stderr)
            if problems:
                return 1
            print(f"version {current()} is consistent")
        elif len(argv) == 2 and argv[0] == "bump":
            bump(argv[1])
            print(f"bumped to {argv[1]}")
        elif argv == ["stamp"]:
            print(f"stamped {current()}{stamp()}")
        else:
            print(__doc__, file=sys.stderr)
            return 2
    except VersionError as exc:
        print(f"version: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
