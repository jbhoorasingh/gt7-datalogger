#!/usr/bin/env bash
# Package the frozen Linux build as an AppImage and a tarball (#200).
#
#   desktop/linux/build-appimage.sh [--sign]
#
# Takes PyInstaller's onedir at desktop/dist/gt7-datalogger and writes into
# desktop/dist/:
#
#   GT7-Datalogger-Linux-x86_64.AppImage         static type2 runtime, with
#                                                gh-releases-zsync update info
#   GT7-Datalogger-Linux-x86_64.AppImage.zsync   for delta updates
#   GT7-Datalogger-Linux-x86_64.tar.gz           the onedir as it is
#
# --sign embeds a gpg signature in the AppImage (appimagetool --sign) and
# writes a detached .asc next to each of the three, with the key in
# $LINUX_GPG_KEY_ID (the passphrase-less signing subkey, plan 08 §6).
#
# Needs zsyncmake on PATH: appimagetool writes the .zsync only when it finds
# one, and this script fails when the .zsync is missing. appimagetool 1.9.1
# and the type2 runtime are downloaded once into desktop/build/tools and
# checked against the sha256 pinned below; a mismatch stops the build.
set -euo pipefail

SIGN=0
for arg in "$@"; do
    case "$arg" in
        --sign) SIGN=1 ;;
        -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
        *) echo "build-appimage: unknown argument: $arg" >&2; exit 2 ;;
    esac
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP="$(dirname "$HERE")"
ROOT="$(dirname "$DESKTOP")"
DIST="$DESKTOP/dist"
ONEDIR="$DIST/gt7-datalogger"
TOOLS="$DESKTOP/build/tools"

APP_ID="io.github.jbhoorasingh.gt7-datalogger"
NAME="GT7-Datalogger-Linux-x86_64"
APPIMAGE="$DIST/$NAME.AppImage"
ZSYNC="$APPIMAGE.zsync"
TARBALL="$DIST/$NAME.tar.gz"
UPDATE_INFO="gh-releases-zsync|jbhoorasingh|gt7-datalogger|latest|$NAME.AppImage.zsync"

# Pinned tools. The sha256 values are the release assets' digests as GitHub
# reports them (gh release view <tag> -R <repo> --json assets).
APPIMAGETOOL_URL="https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage"
APPIMAGETOOL_SHA256="ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"
# appimagetool would otherwise fetch the runtime from the moving "continuous" tag.
RUNTIME_URL="https://github.com/AppImage/type2-runtime/releases/download/20251108/runtime-x86_64"
RUNTIME_SHA256="2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d"

die() { echo "build-appimage: $*" >&2; exit 1; }

[ -x "$ONEDIR/gt7-datalogger" ] \
    || die "$ONEDIR/gt7-datalogger not found: run python desktop/scripts/build.py first"
[ "$(uname -m)" = "x86_64" ] || die "this builds the x86_64 AppImage; run it on x86_64"
command -v zsyncmake >/dev/null || die "zsyncmake is not on PATH (install zsync)"
if [ "$SIGN" = 1 ]; then
    [ -n "${LINUX_GPG_KEY_ID:-}" ] || die "--sign needs LINUX_GPG_KEY_ID"
    command -v gpg >/dev/null || die "--sign needs gpg"
fi

# fetch URL SHA256 DEST: download once, verify every time.
fetch() {
    local url="$1" sha="$2" dest="$3"
    if [ ! -f "$dest" ]; then
        mkdir -p "$(dirname "$dest")"
        curl --fail --location --silent --show-error --retry 3 -o "$dest.part" "$url"
        mv "$dest.part" "$dest"
    fi
    if ! echo "$sha  $dest" | sha256sum --check --status; then
        rm -f "$dest"
        die "sha256 mismatch for $url"
    fi
    chmod 0755 "$dest"
}

fetch "$APPIMAGETOOL_URL" "$APPIMAGETOOL_SHA256" "$TOOLS/appimagetool-1.9.1-x86_64.AppImage"
fetch "$RUNTIME_URL" "$RUNTIME_SHA256" "$TOOLS/runtime-20251108-x86_64"

VERSION="${VERSION:-$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$ROOT/backend/app/_version.py")}"
[ -n "$VERSION" ] || die "could not read the version from backend/app/_version.py"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/gt7-appimage.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
APPDIR="$WORK/AppDir"

echo "build-appimage: assembling the AppDir"
mkdir -p "$APPDIR/usr/lib" \
    "$APPDIR/usr/share/applications" \
    "$APPDIR/usr/share/metainfo" \
    "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp -a "$ONEDIR" "$APPDIR/usr/lib/gt7-datalogger"
install -m 0755 "$HERE/AppRun" "$APPDIR/AppRun"
install -m 0644 "$HERE/$APP_ID.desktop" "$APPDIR/$APP_ID.desktop"
install -m 0644 "$HERE/$APP_ID.desktop" "$APPDIR/usr/share/applications/$APP_ID.desktop"
install -m 0644 "$HERE/$APP_ID.metainfo.xml" "$APPDIR/usr/share/metainfo/$APP_ID.metainfo.xml"
ICON="$APPDIR/usr/share/icons/hicolor/256x256/apps/$APP_ID.png"
install -m 0644 "$HERE/../icons/gt7.png" "$ICON"
cp "$ICON" "$APPDIR/$APP_ID.png"
ln -s "$APP_ID.png" "$APPDIR/.DirIcon"

rm -f "$APPIMAGE" "$ZSYNC" "$TARBALL" "$APPIMAGE.asc" "$ZSYNC.asc" "$TARBALL.asc"

TOOL_ARGS=(--runtime-file "$TOOLS/runtime-20251108-x86_64" -u "$UPDATE_INFO")
if [ "$SIGN" = 1 ]; then
    TOOL_ARGS+=(--sign --sign-key "$LINUX_GPG_KEY_ID")
fi

echo "build-appimage: running appimagetool"
# Containers have no FUSE: let the tool's own AppImage run extracted. The
# .zsync is written beside the output, named after it.
(
    cd "$DIST"
    APPIMAGE_EXTRACT_AND_RUN=1 ARCH=x86_64 VERSION="$VERSION" \
        "$TOOLS/appimagetool-1.9.1-x86_64.AppImage" "${TOOL_ARGS[@]}" "$APPDIR" "$APPIMAGE"
)

[ -f "$APPIMAGE" ] || die "appimagetool did not write $APPIMAGE"
[ -s "$ZSYNC" ] || die "$ZSYNC is missing: appimagetool did not run zsyncmake"
chmod 0755 "$APPIMAGE"
EMBEDDED="$("$APPIMAGE" --appimage-updateinformation 2>/dev/null || true)"
[ "$EMBEDDED" = "$UPDATE_INFO" ] \
    || die "the AppImage's update information is '$EMBEDDED', expected '$UPDATE_INFO'"

echo "build-appimage: writing the tarball"
tar -C "$DIST" --owner=0 --group=0 --numeric-owner --sort=name \
    -czf "$TARBALL" gt7-datalogger

if [ "$SIGN" = 1 ]; then
    for file in "$APPIMAGE" "$ZSYNC" "$TARBALL"; do
        gpg --batch --yes --local-user "$LINUX_GPG_KEY_ID" --armor \
            --output "$file.asc" --detach-sign "$file"
        gpg --batch --verify "$file.asc" "$file"
    done
fi

ls -l "$APPIMAGE" "$ZSYNC" "$TARBALL"
