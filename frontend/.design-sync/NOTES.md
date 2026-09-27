# design-sync notes — gt7-datalogger-frontend

Repo-specific things a future sync should know. Config lives beside this file.

## Shape: an app, not a library

This package is the app itself, so there is no `dist/` entry and no shipped
`.d.ts` tree. `.design-sync/build-inputs.sh` (`cfg.buildCmd`) manufactures all
three inputs the converter needs, and must run before every build:

- `gen-entry.mjs` writes `.design-sync/ds-entry.tsx` (`cfg.entry`) — `export *`
  over every `.tsx` under `src/` except `main.tsx` (which mounts the real app
  and would run on bundle load) and `App.tsx` (default export only, which
  `export *` doesn't carry), plus the stores, the fixtures and the formatters
  listed in its `EXTRA` array. A new component needs no edit here.
- `tsc -p .design-sync/tsconfig.dts.json` emits declarations into `ds-types/`,
  and `gen-entry.mjs` also writes a root `index.d.ts` barrel over them. That
  barrel is what makes component discovery work: `findTypesRoot` falls through
  to the package root, and `exportedNames` reads `<pkg>/index.d.ts`.
- `fix-dts-aliases.mjs` rewrites the `@/…` specifiers tsc leaves in those
  declarations to relative ones. **Without it every component whose props are
  an imported interface (all 16 widgets take `WidgetRenderProps`) loses its
  props to `[key: string]: unknown`** — the converter's ts-morph project has no
  paths mapping, so an unresolvable import makes the prop type `any`.
- Tailwind is compiled by the repo's own version (`@tailwindcss/cli`, staged in
  `.ds-sync/`) from `.design-sync/tailwind-entry.css` into
  `.design-sync/.cache/compiled.css` (`cfg.cssEntry`). The app's `index.css` is
  a Tailwind *source*, not a stylesheet — pointing `cssEntry` at it ships no
  utilities at all.

`.ds-sync/` therefore carries two extra deps beyond the skill's install line:
`npm i esbuild ts-morph @types/react @tailwindcss/cli@4.3.3 @fontsource/inter playwright`.

## Fonts

`index.css` asks for Inter and the app never shipped a webface (it relies on a
locally installed copy, else system sans). Jermaine chose to ship it: the build
copies four weights of `@fontsource/inter` (SIL OFL) into `.design-sync/fonts/`
with a generated `inter.css`, wired through `cfg.extraFonts`. `[FONT_MISSING]`
is resolved, not accepted.

## Preview conventions

- **Cards render on a white page by contract and this system is dark.** Every
  cell wraps its content in `Surface` from `../preview-shell`; components that
  portal to `<body>` (dialogs, toasts) render `<DarkPage/>` instead, which
  restyles the page itself. A fixed-position wrapper does *not* work: the
  card's `.ds-single` has a transform, so it becomes the containing block for
  fixed children while the portal escapes to the body.
- **`Surface` for a component that sits *in* a page, `DarkPage` for one that
  *is* the page.** `Surface` is `inline-block` with padding, so it shrink-wraps
  a full-width tool and spends frame width on its own gutter; the wide editors
  and every view use `DarkPage` instead.
- `../preview-shell.tsx` also has `Panel` (the `.panel` container), `WidgetCard`
  (a dashboard cell as `GridRenderer` draws it), `Row`/`Labelled` for variant
  rows, `ChartBox` (ECharts needs a laid-out height), and `lapColors()` (lap →
  series colour exactly as `AnalysisView` assigns them).
- **Fixtures are real laps**, exported from the user's own database through the
  app's API — see "Fixtures" below. Import only the module you need:
  `../fixtures/analysis` is 126 KB and is inlined into every preview that
  touches it.
- Cell exports must be PascalCase zero-argument components; each one is a card
  cell, and the export name is its label.
- **All cells of one card share the page, so they share the stores.** A
  component whose cells differ only by store state (`StatusBar`) must use
  `cfg.overrides.<Name>.cardMode = "single"`: the card then shows the primary
  story while every cell is still captured and graded on its own via `?story=`.
- Anything wider than a ~320px grid cell wants `cardMode: "column"`.

## Orchestrating the fan-out

- **Apply `cfg.overrides` changes only between waves, never while agents are
  working.** Changing an override (or `titleMap`) makes `preview-rebuild.mjs`
  refuse with `[CONFIG_STALE]` — only a full `package-build.mjs` re-stamps the
  grade keys, and that rewrites the shared bundle, which races every capturing
  agent. Collect the override asks from the learnings files, apply them in the
  fold-in step, run one full build, then re-capture and regrade whatever the
  change re-keyed (an explicit `viewport` is part of the grade key; `cardMode`
  alone is not).
- A subagent that reports being blocked by that rule is behaving correctly:
  the full build is the orchestrator's job.

## demoFrame timings

`demoFrame(ms)` is the app's own placeholder telemetry — a 20 s loop. Useful
moments, for widget previews:

| t (ms) | state |
|---|---|
| 0, 8000 | flat out, 232 km/h, 6th, throttle 100 |
| 3000–4500 | hard braking, throttle 0, brake 100, suggested gear set (4→3) |
| 5000, 13500 | apex, part throttle, no suggested gear |
| 7000 | wheelspin on exit: `tire_slip` 1.18, TCS bit in `aids` |
| 2800 | worst live delta, `delta_ms` +308 (behind, red) |
| 15000 | best live delta, `delta_ms` -400 (ahead, green) |
| 4000 / 6600 | steering extremes, +20.6° and -44° |
| 3000 | 7,685 rpm, hardest braking at 206 km/h |

`DEMO_LAPS` is its two-lap companion.

**What the loop never reaches**, and so must be overridden by spreading the
frame (`{ ...demoFrame(3_000), rpm: 8_640 }`):

- the rev limiter — rpm tops out at ~7,821 against `rpm_alert` 8600 (0.91,
  under the 0.95 `nearLimit` cut), so SHIFT, the red bar and all ten shift
  LEDs are unreachable from the loop alone;
- `boost`, pinned at 0.42, so the boost gauge never sweeps;
- `delta_ms: null`, the only route to the "Δ best (last lap)" caption.

## Composing widgets

- **`projectStrategy` silently drops laps from another car.** `FuelWidget`,
  `StrategyWidget` and `AlertsWidget` route through it, and it discards any lap
  whose `car_id` differs from the frame's. `demoFrame()` is `car_id: 0` while
  `FUEL_LAPS` (the only fixture laps that burned fuel) are `car_id: 3600`, so
  pairing them straight gives `null` and the widgets render "–" or nothing. Set
  the car on the frame to match. `DEMO_LAPS` carries no car id, so it pairs with
  any frame.
- **`AlertsWidget` returns `null` when the car is healthy** (it is
  `frameless`). Thresholds live in `src/lib/alerts.ts`: fuel against ~11.8 L/lap
  (9.2 critical, 30 warn), `water_temp` 124, `oil_temp` 143, tyres over 110.
  Being frameless it wants `Surface width=` rather than `WidgetCard` — the
  banner variant is `w-full`.
- Every widget cell wraps in `Surface className="font-tabular"`, because
  `GridRenderer` puts `font-tabular` on the grid itself. Size `WidgetCard` to
  the variant's `baseW` from `WIDGET_META` — bar and shift-light variants need
  ~208–232px or they clip.
- `Caption` and `Gauge` live in `src/components/widgets/shared.tsx` but capture
  as `general__*.png`, not `widgets__*.png`.

## Traps that cost a debugging cycle

- **The compiled Tailwind carries only the utilities `src/` actually uses, and
  a missing one fails silently.** `h-40`, `max-h-[600px]` and `max-h-[580px]`
  are absent; `gap-6`, `w-80`, `max-h-[80vh]` are present. A dropped height
  collapsed a preview's stage to a hairline and its `absolute bottom-3` child
  then painted above the box, over white paper. Frame dimensions in a preview
  belong in `style={{…}}`; keep class names to ones a view already uses, and
  grep `.design-sync/.cache/compiled.css` before trusting a utility. (The build
  now safelists a layout/spacing/sizing set — see `tailwind-entry.css` — but the
  rule still holds for anything exotic.)
- **`localStorage` is shared by every `?story=` capture** — one browser context,
  one origin — and `useEngineer` is a `persist` store. A cell that seeds only
  part of the persisted set inherits the rest from whichever story ran before
  it. Seed the *whole* preference set, not the field you care about.
- **A component that fetches needs the same treatment as one that reads a
  store.** `LapSparkline` calls `api.sessionLaps` and renders `null` until it
  answers; its preview installs a module-scope `window.fetch` shim for that one
  route. That is the data equivalent of `setState`, not a faked render.
- **`<details>` state and scroll position are state too** — `CalloutReference`
  ships folded and its list only differs from itself near the end, so its cells
  open the element and set `scrollTop = scrollHeight` in an effect.
- **Headless browsers have no speech voices**, so `RaceEngineerPanel` shows its
  "No speech voices found" warning in every cell unless the preview stubs
  `window.speechSynthesis.getVoices` with a realistic list. The component is
  right; the capture browser is the odd one.
- `page.clock.setFixedTime` does not stop timers. `CalloutBanner` hides its
  caption after 6 s and the capture lands well inside that, but a component with
  a shorter self-hiding timer would photograph empty.
- **There is no bests fixture** — `/api/bests` reads the user's own database —
  so `BestsBoard`'s rows are hand-built from the fixture sessions' real best
  laps (Tsukuba 0:58.963 in the AE86, Bathurst 2:11.077 in the 911 GT3 R).

## Composing controls and overlays

- **Seed the toast store with `useToasts.setState`, never `push`** — all cells
  of a card share one store.
- `Toasts` is `position: fixed` but *not* portalled, so it anchors to the card's
  transformed `.ds-single` root instead of the viewport and captures with the
  stack sliced off. Its preview carries a local `.ds-single{transform:none}`
  style tag; `cardMode`/`viewport` do **not** fix this, because they change the
  mount mode and page size, not the containing block. `.ds-cell` also has
  `overflow:hidden`, which is a second reason it must be single-mode.
- `Row` from the shell is `items-start`; mixed-height toolbars want a plain
  `flex items-center` instead.

## Fixtures

`.design-sync/fixtures/*.ts` are committed and generated by
`export-fixtures.mjs` against a running backend. To regenerate, build a scratch
DB (the recipe is in the user's own notes: `init_db(make_engine(Path(...)))`,
then `attach database 'file:<main>/data/gt7.db?mode=ro' as src` over a
`uri=True` connection and insert `sessions`/`laps`/`tracks` for the session ids
you want — no `settings` rows, so sim mode holds), then:

```sh
GT7_DB_PATH=<scratch.db> GT7_SOURCE=sim GT7_CARS_CSV= \
  backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --port 8009
node .design-sync/fixtures/export-fixtures.mjs --port 8009 \
  --analysis-session 228 --fuel-session 194 --sessions 194,228,232,239
```

Sessions 228 (Tsukuba, AE86, 8 laps) and 194 (Mount Panorama, 911 GT3 R, fuel
and stints) were picked because the AE86 session has no fuel consumption at all
and would leave the fuel map empty.

The exporter **sanitises** `lan_ip` and the database path out of
`/api/admin/stats`. These files are committed to a public repository — keep any
new payload free of host details, tokens and the console's real IP.

## Driving a component's own state from a cell

Several components hold the interesting state internally with no prop for it.
Reaching it through the component's own UI is legitimate and reusable; faking
the markup is not.

- **Click the real control.** `rAF`-poll until the element exists, then
  `.click()` — a popover trigger (`button[aria-expanded]`), a row's expand
  chevron, a `SegmentedControl` option by its exact `textContent`.
- **Type through React's native setter**:
  `Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set` then
  `dispatchEvent(new Event("input",{bubbles:true}))`, inside a
  `requestAnimationFrame` so it lands after the component's own mount effects.
  The same trick scrubs a range input (a playback playhead).
- **Stub `window.fetch` at preview module scope** for components that fetch, and
  key the stub on the id in the URL so one shim serves every cell its own
  answer — populated, empty, 404, and (a promise that never settles) loading.
  Fall through to the real `fetch` for anything not matched, or bundle requests
  break. Assign the route table in the cell body, not at module scope.
- **A popover needs reserved height in the cell**, or `Surface` collapses to the
  trigger and the popover paints over white paper.
- `<details>` state and scroll position count as state: open the element and set
  `scrollTop` in an effect.
- **Prefer scrolling to a section header over a pixel offset.** Offsets break
  the moment a card's viewport changes, and several did.
- **A media query cannot be faked from inside a preview** — not with `zoom`, not
  with transforms. Only the capture viewport moves it, which is why the two
  wide tools carry one.
- **Hover-only affordances are not gradeable cells.** A state that is invisible
  in a still (a cursor hint, a drag ghost) should be left out, not shipped as a
  cell that duplicates another.

## Card geometry

- **`viewport` is only honoured on single-mode cards** (`package-capture`
  matches it against the card head), so pairing it with `cardMode: "column"`
  silently does nothing.
- The contact sheet grades at `520 / viewport_height`, so an over-tall viewport
  spends the card's legibility on empty page. Size it to the content.
- Full-screen cells (the views) must restate the page themselves: the card's own
  `<style>` comes after the app's stylesheet, so `body{@apply bg-surface}` loses,
  and `html,body,#root{height:100%}` never applies because there is no `#root`.
  Each view cell sets `.ds-single{height:100vh}` and mirrors `App.tsx`'s
  `flex h-full flex-col` + `<main class="min-h-0 flex-1 overflow-y-auto …">`.
  `DarkPage` does not clear the 24px gutter — a view is the page, so it must.

## Fixture facts worth knowing

- `COMPARE` / `COMPARE_FULL`: laps 1063 (reference, L2, 0:58.963), 1065 (L4) and
  1068 (L7), all `aligned: true`. `corners` is on the reference lap only (six at
  Tsukuba, unnamed; Turn 5 is the hairpin at 1,168–1,334 m). `COACHING` covers
  laps 1063–1067, so **1068 genuinely has no notes**.
- **The AE86 is heavily tuned**: 9,000 rpm on the limiter, 207 km/h at Tsukuba.
  Numbers that look implausible for a stock AE86 are correct for this one — the
  car row's 127 bhp / PP 381 is GT7's stock inventory, not what was driven.
- `STINT` is one stint of seven laps; there is **no multi-stint fixture and no
  lap with `pit: true`**, so the "stint 1 · stint 2" caption and the pit marker
  are never exercised.
- `TRACK_OUTLINE` is Mount Panorama while the comparison laps are Tsukuba, so
  the surveyed-road rendering under a race line is still uncovered. A Mount
  Panorama `COMPARE` (`--analysis-session 194`) would close it.
- Every circuit's `sync` is `null` and `sync_tracks` is off, so **none of
  `SyncChip`'s seven states render anywhere**. The corner *editor* dialog also
  needs a `TrackBundleDoc` fixture (`api.bundles.get(slug)`) that does not exist.

## Known render warns

- The three floor-card warns from the first build (`[RENDER_BLANK] EChart`,
  `[RENDER_THIN] Gauge`, `[RENDER_BLANK] InputsWidget`) are **all resolved** —
  every component is authored as of this run. If one reappears, it means a
  preview stopped compiling, not that the component regressed.
- Critical alert banners and at-limit shift lights use `animate-pulse`, so a
  screenshot catches them mid-cycle at partial opacity. Real behaviour, not a
  render failure.

## Component gaps found while previewing

- **`.skeleton` is invisible.** It fills with `--color-panel`, the exact colour
  of the `.panel` it always sits inside, and `animate-pulse` only varies
  opacity — so every loading placeholder in the product is unreadable, not just
  in a capture. All nine uses in `src/` are inside panels (`AnalysisView` ×5,
  `SessionsView`, `BestsBoard`). `--color-panel-2` is the obvious fix. A
  `Loading` cell was dropped from GearingPanel because of it.

These are defects in the app, not in the sync — each was hit by rendering the
real component:

- **`gearing.top_speed` was a ratio, not a speed** — fixed in this run on
  branch `gearing-top-speed-decode` (`calculated_max_speed` is what GT7 carries
  the km/h in). Laps recorded before that fix still store the ratio, so
  `GEARING` must come from a post-fix lap (`--gearing-lap`); the two-car
  five-speed/six-speed contrast returns once a real lap is recorded through it.
- **`SegmentedControl`'s `disabled` prop has no styling of its own**: a disabled
  control is pixel-identical to an enabled one. `.btn` already has the
  `disabled:opacity-45` idiom to match.
- **Two states cannot be shown statically and were deliberately not faked**:
  `Tip`'s bubble (hover/focus only, no `open` prop) and `Select`'s listbox (the
  wrapper forwards no `open`). Their cards show the real triggers.

## Re-sync risks

- **`cfg.entry`, `index.d.ts` and `ds-types/` are generated and gitignored.** A
  fresh clone must run `.design-sync/build-inputs.sh` (after staging `.ds-sync/`
  and installing its deps) before the converter, or discovery finds nothing and
  the run degrades to a tokens-only DS.
- **The fixtures are a snapshot.** If a backend payload changes shape
  (`CompareResult`, `StintTrend`, `AdminStats`), the committed fixture still
  type-checks against the old shape and previews may render stale or wrong
  states. Re-export them when an API type changes.
- `dtsPropsFor.SegmentedControl` is hand-written because the component is
  generic in its value type (`<T extends string>`), which the extractor emits as
  a bare `T`. If its API changes, that entry has to change with it.
- The widget `.d.ts` bodies reference `LiveFrame`/`LapSummary` without
  definitions in the emitted file (valid syntax, so validate passes). The shapes
  are documented centrally in `conventions.md` instead of being inlined 16
  times.
- Tailwind's compiled output only carries the utilities `src/` actually uses. A
  design agent writing its own arbitrary utility classes may find them missing —
  `conventions.md` tells it to stay in the documented vocabulary.
