#!/usr/bin/env bash
# Put a signed GT7 Datalogger.app in a DMG, then sign, notarize and staple
# the DMG too (#198). Same environment as sign.sh.
#
#   desktop/macos/make-dmg.sh "<path>/GT7 Datalogger.app" <out.dmg>
set -euo pipefail

APP="${1:?usage: make-dmg.sh <App.app> <out.dmg>}"
OUT="${2:?usage: make-dmg.sh <App.app> <out.dmg>}"
ID="${MACOS_SIGN_IDENTITY:?set MACOS_SIGN_IDENTITY (or - for ad-hoc)}"

STAGE="$(mktemp -d)"
ditto "$APP" "$STAGE/$(basename "$APP")"
ln -s /Applications "$STAGE/Applications"
rm -f "$OUT"
hdiutil create -volname "GT7 Datalogger" -srcfolder "$STAGE" -fs HFS+ \
  -format UDZO -imagekey zlib-level=9 -ov "$OUT"
rm -rf "$STAGE"

if [[ "$ID" == "-" ]]; then
  codesign --force --sign - "$OUT"
  exit 0
fi
codesign --force --timestamp --sign "$ID" "$OUT"
if [[ -n "${ASC_KEY_PATH:-}" ]]; then
  xcrun notarytool submit "$OUT" --key "$ASC_KEY_PATH" --key-id "$ASC_KEY_ID" \
    --issuer "$ASC_ISSUER_ID" --wait --timeout 30m
  xcrun stapler staple "$OUT"
  spctl --assess --type open --context context:primary-signature --verbose=4 "$OUT"
fi
