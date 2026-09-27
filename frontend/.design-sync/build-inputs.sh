#!/bin/sh
# cfg.buildCmd — regenerates everything the converter reads out of this app:
# the bundle entry, the declaration tree the .d.ts contracts are extracted
# from, and the compiled stylesheet. Needs the staged .ds-sync/ deps (the
# Tailwind CLI lives there, isolated from this repo's lockfile).
set -e
cd "$(dirname "$0")/.."
node .design-sync/gen-entry.mjs
node_modules/.bin/tsc -p .design-sync/tsconfig.dts.json
node .design-sync/fix-dts-aliases.mjs
mkdir -p .design-sync/.cache
.ds-sync/node_modules/.bin/tailwindcss \
  -i .design-sync/tailwind-entry.css \
  -o .design-sync/.cache/compiled.css

# Inter (SIL OFL) — index.css asks for it but the app never shipped a webface,
# so designs built from this library would fall back to system sans. Copied
# from the staged @fontsource/inter at the four weights the UI uses.
mkdir -p .design-sync/fonts
: > .design-sync/fonts/inter.css
for w in 400 500 600 700; do
  cp ".ds-sync/node_modules/@fontsource/inter/files/inter-latin-$w-normal.woff2" .design-sync/fonts/
  cat >> .design-sync/fonts/inter.css <<CSS
@font-face {
  font-family: "Inter";
  font-style: normal;
  font-weight: $w;
  font-display: swap;
  src: url("./inter-latin-$w-normal.woff2") format("woff2");
}
CSS
done
