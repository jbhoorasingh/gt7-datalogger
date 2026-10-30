#!/usr/bin/env bash
# Build the Linux desktop app inside quay.io/pypa/manylinux_2_28_x86_64 (#200).
#
#   docker run --rm -v "$PWD:/src" -w /src quay.io/pypa/manylinux_2_28_x86_64 \
#       desktop/linux/build-in-manylinux.sh [--sign]
#
# The repository is the working directory, with frontend/dist already built
# (the workflow's frontend job; locally `cd frontend && npm ci && npm run build`).
# The image's glibc 2.28 is the floor the build runs on: RHEL 8+, Ubuntu
# 22.04+, Debian 12+.
#
# 1. uv (pinned, hash-checked) into the image's own Python 3.12;
# 2. CPython 3.14 from python-build-standalone via `uv python install`: the
#    manylinux CPythons are linked statically and PyInstaller needs a shared
#    libpython;
# 3. a venv with wheels only (--only-binary :all: --require-hashes), from
#    the committed lock desktop/locks/x86_64-manylinux_2_28.txt;
# 4. desktop/scripts/build.py freezes it and runs the frozen --self-test;
# 5. unless --no-appimage: zsync, then desktop/linux/build-appimage.sh
#    (other arguments pass through, so --sign works here as there). CI
#    passes --no-appimage and packages on the Ubuntu host instead, because
#    EPEL 8 has no zsync for this image.
#
# Outputs land in desktop/dist/, owned by root when run as root in Docker.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

UV_VERSION="0.13.1"
UV_WHEEL_SHA256="696dcf6d807d85da978f4db8472da81dccc1254b18b52cbd1367c4d923c421e0"
PYTHON_VERSION="${PYTHON_VERSION:-3.14}"
WORK="${GT7_BUILD_WORK:-$ROOT/desktop/build/manylinux}"
VENV="$WORK/venv"

die() { echo "build-in-manylinux: $*" >&2; exit 1; }

[ -f frontend/dist/index.html ] || die "frontend/dist is missing: build the frontend first"
[ "$(uname -m)" = "x86_64" ] || die "run this in the x86_64 manylinux image"

mkdir -p "$WORK"

# 1. uv, from PyPI with its hash: no curl | sh.
BOOT="/opt/python/cp312-cp312/bin/python"
[ -x "$BOOT" ] || BOOT="$(command -v python3)" || die "no Python to install uv with"
printf 'uv==%s --hash=sha256:%s\n' "$UV_VERSION" "$UV_WHEEL_SHA256" > "$WORK/uv.txt"
"$BOOT" -m pip install --quiet --disable-pip-version-check --only-binary :all: \
    --require-hashes -r "$WORK/uv.txt"
UV=("$BOOT" -m uv)

# 2. A python-build-standalone CPython, which has the shared libpython.
export UV_PYTHON_PREFERENCE=only-managed
"${UV[@]}" python install "$PYTHON_VERSION"
rm -rf "$VENV"
"${UV[@]}" venv --python "$PYTHON_VERSION" "$VENV"
PY="$VENV/bin/python"
"$PY" - <<'EOF'
import sys, sysconfig
assert sysconfig.get_config_var("Py_ENABLE_SHARED"), "this Python has no shared libpython"
print("build-in-manylinux: python", sys.version.split()[0], sys.prefix)
EOF

# 3. Wheels only, every one hash-checked against the committed lock
#    (desktop/locks/lock.sh writes it; #193), so the build installs what was
#    reviewed rather than what a resolver picks on the day.
"${UV[@]}" pip install --python "$PY" --only-binary :all: --require-hashes \
    -r desktop/locks/x86_64-manylinux_2_28.txt
"${UV[@]}" pip install --python "$PY" --no-deps --no-build-isolation ./backend

# 4. Freeze, and the frozen app's own self-test.
"$PY" desktop/scripts/build.py --skip-frontend

# 5. The AppImage and the tarball.
if [ "${1:-}" = "--no-appimage" ]; then
    exit 0
fi
if ! command -v zsyncmake >/dev/null; then
    dnf install -y -q epel-release >/dev/null 2>&1 || true
    dnf install -y -q zsync || die "could not install zsync"
fi
PYTHON="$PY" desktop/linux/build-appimage.sh "$@"
