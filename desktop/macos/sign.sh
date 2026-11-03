#!/usr/bin/env bash
# Sign, notarize and staple GT7 Datalogger.app (#198).
#
#   desktop/macos/sign.sh "<path>/GT7 Datalogger.app"
#
# MACOS_SIGN_IDENTITY  "Developer ID Application: … (TEAMID)", or "-" for an
#                      ad-hoc local test (no timestamp, no notarization)
# ASC_KEY_PATH, ASC_KEY_ID, ASC_ISSUER_ID   App Store Connect API key for
#                      notarytool (CI)
# NOTARY_PROFILE       or a `notarytool store-credentials` keychain profile
#                      (a local build); notarization is skipped when neither
#
# Inside out, never --deep: every Mach-O inside the bundle first, then the
# app. Hardened runtime, no entitlements: the frozen app needs none.
set -euo pipefail

APP="${1:?usage: sign.sh <App.app>}"
ID="${MACOS_SIGN_IDENTITY:?set MACOS_SIGN_IDENTITY (or - for ad-hoc)}"
FLAGS=(--force --sign "$ID")
# Hardened runtime only with a real identity: its library validation needs
# every file to carry the same Team ID, which an ad-hoc signature has none of
# (the Python framework then fails to load: "different Team IDs").
if [[ "$ID" != "-" ]]; then FLAGS+=(--options runtime --timestamp); fi

is_macho() { file -b "$1" | grep -q 'Mach-O'; }

# Deepest paths first, so a framework's contents are signed before it.
while IFS= read -r -d '' f; do
  if is_macho "$f"; then codesign "${FLAGS[@]}" "$f"; fi
done < <(find "$APP/Contents" -type f ! -path "*/Contents/MacOS/*" -print0 | sort -rz)
while IFS= read -r -d '' fw; do
  codesign "${FLAGS[@]}" "$fw"
done < <(find "$APP/Contents" -type d -name "*.framework" -print0 | sort -rz)
codesign "${FLAGS[@]}" "$APP"

codesign --verify --deep --strict --verbose=2 "$APP"
if [[ "$ID" == "-" ]]; then
  echo "ad-hoc signed; skipping Gatekeeper assessment and notarization"
  exit 0
fi
spctl --assess --type execute --verbose=4 "$APP" || true  # not notarized yet

if [[ -n "${NOTARY_PROFILE:-}" ]]; then
  NOTARY=(--keychain-profile "$NOTARY_PROFILE")
elif [[ -n "${ASC_KEY_PATH:-}" ]]; then
  NOTARY=(--key "$ASC_KEY_PATH" --key-id "$ASC_KEY_ID" --issuer "$ASC_ISSUER_ID")
fi
if [[ -n "${NOTARY+set}" ]]; then
  ZIP="$(mktemp -d)/app.zip"
  ditto -c -k --keepParent "$APP" "$ZIP"
  xcrun notarytool submit "$ZIP" "${NOTARY[@]}" --wait --timeout 30m
  xcrun stapler staple "$APP"
  spctl --assess --type execute --verbose=4 "$APP"
fi
