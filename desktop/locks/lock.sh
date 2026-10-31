#!/usr/bin/env bash
# Regenerate the hashed dependency locks the desktop builds install (#193).
#
#   desktop/locks/lock.sh          # needs uv on PATH (or UV="python -m uv")
#
# One lock per build target, compiled for CPython 3.14 and that platform
# from the backend's dependencies with its `desktop` extra, the pinned build
# tools, and setuptools (to build the backend package itself). Every entry
# carries its hashes, so a build installs exactly what was reviewed here —
# not whatever a resolver picks on the day. Re-run after changing
# backend/pyproject.toml or desktop/requirements-build.txt, and commit the
# result.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
UV=(${UV:-uv})
IN="$(mktemp)"
trap 'rm -f "$IN"' EXIT
printf 'setuptools>=75\n' > "$IN"
for target in aarch64-apple-darwin x86_64-pc-windows-msvc x86_64-manylinux_2_28; do
  "${UV[@]}" pip compile --quiet --python-version 3.14 \
    --python-platform "$target" --generate-hashes --extra desktop \
    --no-header --annotation-style line \
    backend/pyproject.toml desktop/requirements-build.txt "$IN" \
    -o "desktop/locks/$target.txt"
  echo "desktop/locks/$target.txt"
done
