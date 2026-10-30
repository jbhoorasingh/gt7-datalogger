"""The version of the running build, from one place (#179).

Three readers used to work it out three ways — pyproject.toml through
`__file__`, the same with no fallback, and package metadata — and in a dev
venv they already disagreed (0.8.1, 0.8.1 and whatever was last
pip-installed). Frozen into a desktop bundle, pyproject.toml is not there at
all and metadata may not be either. `_version.py` is committed, so it is
there in every install kind; a CI build may add `_build_info.py` (never
committed) with the suffix a tag or commit gives it: `-rc.2`, or `+g1a2b3c4`
off a tag.
"""

from __future__ import annotations

from functools import cache

from app._version import __version__


@cache
def version() -> str:
    try:
        from app import _build_info  # type: ignore[attr-defined]
    except ImportError:
        return __version__
    suffix = str(getattr(_build_info, "SUFFIX", ""))
    return __version__ + suffix
