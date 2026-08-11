# Changelog

Notable changes to GT7 Datalogger. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **Per-tick surface data is now recorded** (packet C): each lap stores a
  packed per-wheel `surface` sample column. Every lap gets an honest
  track-limits verdict — an off-track excursion count (3+ wheels on
  grass/gravel/dirt for ≥ 0.1 s) and a `clean_lap` flag, shown as an
  **Off-track** column in the Sessions lap table. The Analysis race-line map
  shades where wheels touched kerbs (yellow) or left the road (orange).
  Laps recorded before this release, or without packet format C, show "–"
  (unknown) rather than pretending to be clean. (#17)
- **Survey view** (new tab): validates GT7's `surface_types` encoding and the
  wheel-contact derivation on a real PS5, from any browser on the LAN while
  driving. Capture runs server-side in the 60 Hz packet path (the ~30 Hz live
  stream would miss single-tick transitions); the view shows the live
  per-wheel surface, the char histogram with a loud banner for unknown chars,
  and every transition's derived contact points on a scatter map. The full
  transition log (raw rotation floats included) is downloadable as JSONL,
  feeding the findings note at `docs/internals/surface-survey.md`. Runs are
  tied to the live session — lap recording continues alongside — and labeled
  with the circuit being surveyed (picked from the official GT7 track
  catalog, or auto-identified — even mid-run, when the label is appended to
  the log as a `track` line). The JSONL meta header carries the label and
  width assumption; every record carries the session id and lap.
  The survey map draws the track taking shape lap by lap: border evidence
  accumulates server-side for the whole run (left/right perimeters draw
  themselves as edge ticks, the road fills in wherever opposite borders
  face each other), driving with one side's wheels held off the track
  traces that border continuously (straddle sampling — not just at the
  crossing moments), manual marking buttons trace boundaries surface data
  cannot see (walls, paved run-off limits) from the driven wheel line, a
  raw-packet inspector shows every decoded field live with changes
  highlighted, a completeness card grades each perimeter against the driven
  loop (percent, largest gap, closed ✓) and locates the finish line from
  lap rollovers, per-circuit **track bundles** persist everything across
  runs and app restarts (grid-deduped so they converge; resumed
  automatically, downloadable via `/api/track-bundles`), and the
  track-width assumption calibrates
  itself: riding all four wheels over an edge and back measures the real
  axle width, which replaces the assumption once three rides agree. Live
  frames now carry the packed `surface` value. (#37)

- **Car category is a first-class dimension.** Packet C broadcasts the
  category ("Gr.3", "Gr.4", "N300"…) and it was decoded and thrown away.
  It is now stored on the session and, denormalised like `car_id`, on every
  lap — so grouping by it never needs a join — and served by the sessions
  and laps APIs. The Sessions list shows it as a badge and offers category
  filter chips, which appear only when a category is actually present, so a
  history recorded before packet C looks unchanged. Laps without packet C
  keep a blank category rather than being lumped into a real one. (#19)

- **Border records carry elevation, and measured axle track is remembered
  per car.** GT7 broadcasts position on all three axes and the car id
  (`carCode`), both of which were being thrown away by the survey. Bundles
  are now format v3 with a `y` on every border record — without it a bundle
  can only ever describe a flat track, and there is no reconstructing it
  later for straddle and manual points, which are the overwhelming majority.
  Records mapped before v3 carry `null` and are filled in by re-driving that
  metre, so it is recoverable rather than lost; v1 and v2 bundles upgrade in
  place on load. Axle track measured from cornering is now saved per car to
  `data/car-widths.json` and applied from the first tick of the next run in
  that car, instead of every run laying its opening points at the 1.6 m
  assumption until the first corner. Swapping cars mid-run discards the
  previous car's samples, and a short run never overwrites a
  better-evidenced measurement. (#38)

- **Axle track width now measures itself from ordinary cornering.** Every
  corner is a measurement: the outer wheels cover a larger arc, so their
  rolling speeds differ by exactly the yaw rate times the axle track
  (`|v_outer - v_inner| = |yaw| * width`, from the broadcast `wheel_rps`,
  `tire_radius` and `angular_velocity_y`). It needs no deliberate driving and
  settles in seconds — on real hardware it reached a trusted figure of
  **1.74 m within ~12 seconds of normal laps**, where the existing
  ride-an-edge-out-and-back estimator had accepted **zero** samples across an
  entire session of heavy edge riding. Both axles are offered each tick and
  the plausible range decides which one spoke, so a spool or locked
  differential — this test car's rear wheels report identical speeds even
  coasting — costs nothing and no drivetrain layout has to be declared.
  Braking ticks are skipped (ABS modulates wheels individually and threw up
  1.22 / 2.03 / 4.87 m readings); throttle is not gated, because wheelspin
  already shows up as an axle mean that disagrees with the car's speed, and
  gating it would have discarded most of a racing lap. The Survey view says
  which number it is serving — cornering, edge ride, or assumption — and
  when nothing has landed yet it names the gate doing the rejecting instead
  of sitting silently on the assumption. (#38)

### Fixed

- **Survey coverage no longer invents gaps on ground that is already
  mapped.** Border evidence recorded travelling the opposite direction was
  ignored at any distance, on the theory that it had to belong to the other
  leg of a hairpin. On a real East End run that put a 127 m "go touch this"
  gap on *both* borders while the car drove down the middle of a fully
  mapped 12.7 m road, with evidence 5.6 m to starboard and 7.8 m to port. A
  border belongs to the road, not to the direction it was first seen from,
  so opposite-heading evidence now counts within a short radius — closer
  than anything on a neighbouring leg can be, so the ghost gaps that gate
  was guarding against stay guarded. The same run's gaps drop from 127 m to
  6 m, below the drawing threshold. (#39)
- **Gap beacons point at the border instead of at tarmac.** They were drawn
  a fixed 4 m off the driven line, which on a 13 m road placed them ~2.5 m
  *inside* the road, between the two borders. The offset now follows the
  road's own half-width, measured from where evidence actually sits on the
  stretches that have it. (#39)
- **Hand-marked boundaries no longer lose to the surface reader.** Track
  bundles keyed evidence by kind as well as position, so marking a meter as
  a run-off limit stored a second point beside the automatic one instead of
  correcting it — and because the map only drops `runoff` points from the
  road fill, the surviving twin kept that meter drawn as road. The mark
  looked accepted and changed nothing. Across the author's real bundles this
  hit 105 meters at Lago Maggiore Centre and 6 at East End. (#38)

### Changed

- **Track bundles are now format v3: one voted record per meter of border.**
  Kinds observed at a meter are votes on what it is, resolved with
  hand-marked kinds beating inferred ones (the surface chars cannot see a
  wall or paved run-off, so an automatic point there is not evidence against
  a mark) and majority inside each tier — which is also how a mis-mark is
  undone. Re-driving mapped ground can now correct the map instead of only
  extending it. Votes count runs rather than samples, so the periodic
  autosave cannot inflate them. Records carry provenance (`run`, and the
  axle track width `tw` they were derived with), which leaves the door open
  to correcting straddle points offline once a better width is known.
  Existing bundles upgrade in place the first time they are read; nothing
  needs re-driving. (v2 introduced the voting; v3 added the elevation
  field above — they ship together.) Files grow roughly 30% for the added
  evidence. (#38)

## [0.4.1] - 2026-08-07

### Fixed

- **Race Engineer voice now works from other devices on the LAN.** Opening the
  dashboard from a plain-HTTP address (`http://<pi-ip>:8000` — the normal way to
  reach a Raspberry Pi) broke voice output: the browser only provides
  `crypto.randomUUID` on HTTPS or localhost, so registering the device and claiming
  **Use this device for voice output** failed silently. The client id now falls back
  to `crypto.getRandomValues`, which works everywhere.
- **Phone navigation.** The header nav drops to its own row on narrow screens
  instead of clipping tab names — Sessions and Admin are reachable on a phone again.
- **Lap colours no longer collide in Analysis.** Colours are keyed to the lap id
  six-wise, so laps 6 apart — routinely the "latest vs best" pair — rendered
  identically in the charts, map and chips. Laps compared together now always get
  distinct colours; a lap only changes colour when it collides, and keeps its
  canonical colour everywhere else.
- **`/dash` and `/engineer` have a way back** to the main app (a small home link);
  the OBS overlay stays chrome-less.
- **`#/overlay` now routes to the overlay** like its `#/dash` and `#/engineer`
  siblings, alongside the existing `/overlay` and `#overlay` forms.
- Cross-view links into Analysis keep the chart channel selection (`ch=` was
  declared but never written), and the S1/S2/S3 zoom buttons' hover style renders
  (its colour token was never defined).

## [0.4.0] - 2026-08-06

### Added

- **Race Engineer — spoken callouts.** The datalogger now talks. Conditions are
  detected on the backend and spoken by the browser (`/dash`, or the new standalone
  `/engineer` page) with the Web Speech API, so no audio device, text-to-speech
  package or cloud service is needed on the Raspberry Pi, NAS or Docker host, and
  nothing leaves the machine.
  - **Race**: lap times, personal bests, sustained pace loss, final lap and halfway,
    positions gained and lost.
  - **Strategy**: fuel range, fuel shortage against the race distance, and the pit
    window — the same projection the dashboard widgets use, ported to the backend.
  - **Vehicle health**: water and oil temperature, oil pressure, tire temperature and
    balance, with the same limits the dashboard colours use.
  - **Coaching**: braking points against your best lap ("You are braking early into
    turn four, about fifteen meters"), repeated lockups and wheelspin by corner,
    bottoming out, and where a lap lost its time ("You lost three tenths in turn six.
    You braked eighteen meters earlier and carried five kilometers per hour less at
    the apex").
  - Reliability before frequency: persistence windows, hysteresis, per-event
    cooldowns, semantic deduplication, severity escalation that bypasses a cooldown
    when something gets worse, and an expiry on every message — a stale callout is
    dropped rather than spoken. Around one to three messages a lap.
  - Numbers are spelled out for speech ("1:32.487" → "one minute thirty-two point
    five"), in metric or imperial (`GT7_RACE_ENGINEER_UNITS`), so the wording is
    identical on every browser and voice.
  - **One device speaks.** Pages register over the WebSocket and claim voice output,
    so a laptop, a phone and several OBS sources can all be open without a chorus.
    The OBS overlay may never claim it.
  - **Verbosity and categories per device** (minimal / race / coach, plus eleven
    individual categories), under a server-side ceiling in **Admin → Race Engineer**.
    The panel lists what every category says, in the callouts' own words.
  - **It says why it is silent**: whether the browser has permission, whether this
    device is the speaker, whether the backend is producing anything, and — when the
    speech engine refuses — the engine's own reason, in the panel, in the admin
    diagnostics and in the server log. Where speech is unavailable the dashboard is
    unaffected and callouts appear as on-screen captions.
  - Coaching waits until several laps agree on the track's distance, so braking
    points and corner losses are never computed against a half-recorded lap.
  - Fully opt-in: detection does not run until a browser enables voice, so the
    feature costs nothing for anyone who never turns it on.
- **Simulator scenarios** (`GT7_SIM_SCENARIO`): `race`, `fuel_shortage`,
  `overheating` and `oil_pressure` stage situations for testing callouts without a
  console. The default `practice` behaviour is unchanged.

### Fixed

- **A lap the logger only half-saw could win the session.** Capture starting mid-lap,
  or a lap out of the garage, still gets a lap time from GT7 — a *short* one — so it
  became the session best, the live-delta reference and the basis of every lap
  comparison. The 0.3.1 guard compared each lap against the longest lap of the session
  at 85 %, which let a lap covering 88 % of the track through.
  Recalibrated against 850 recorded laps: laps are judged against the **median** span
  of recent laps at **97 %** (98 % of real laps sit within 0.5 % of that median, while
  the partials measured 40-95 %). The yardstick is no longer the longest lap — 12 of
  those laps ran *longer* than their session median, one by 44 %, and a single such
  lap made every normal lap after it look partial. Every lap of a session is now
  re-judged as new laps arrive (in both directions), and dropping a partial lap
  promotes the fastest remaining full lap instead of blanking the best until the next
  one arrives.

## [0.3.1] - 2026-08-05

### Added

- **Auto-numbered corners on the track map**: corners are detected from the
  reference lap's racing-line signed curvature (hysteresis segmentation,
  direction-aware split/merge, start/finish wrap stitching, apex at the
  curvature-weighted centroid) and numbered from the start line, GT7 Data
  Logger-style. Numbered circles while ≤ 30 corners are in view, dots beyond;
  the Corner Detail widget shows the current corner (e.g. `T5 R`) while
  scrubbing. Detection parameters were tuned against real GT7 laps for
  identical counts and < 30 m apex drift across laps of the same track.

### Fixed

- **Pit out-laps no longer poison the session best / live delta**: a short out-lap
  (GT7 reports a time for it, but it covers only part of the track with a
  pit-exit-anchored distance axis) could become the delta reference — the live
  delta then glitched for the first fraction of the next lap and froze on a bogus
  ~lap-sized fallback value. Laps now only count for best when their distance span
  matches the session's longest lap (85 %), and a full lap invalidates a partial
  "best" retroactively — including the already-saved rows (`counts_for_best`
  column, migrated automatically), so the Sessions list and session-summary
  webhook agree with the live view.
- **Fuel projection survives race restarts**: recent laps are filtered by car
  (`car_id` first, name fallback — not recording session), so a restart keeps the
  previous stint's consumption data — you get a range estimate from the first
  meters instead of after a full lap, which matters in races with aggressive fuel
  multipliers. Partial-lap outliers (a lap consuming < 50 % of the window max,
  i.e. pit out-laps) are excluded from the average.

## [0.3.0] - 2026-08-04

### Added

- **Extended telemetry packet support (B / `~` / C)**: the listener can request any
  of GT7's four packet formats via the heartbeat character, decrypts each format's
  distinct Salsa20 IV constant, and parses the extra fields — steering wheel
  rotation, sway/heave/surge, filtered inputs, per-wheel torque vectors, energy
  recovery, per-wheel surface type, the live lap timer, front-wheel steering angles,
  wheelbase, and car category. Default is now packet **C** (GT7 v1.68+);
  configurable via `GT7_PACKET_FORMAT` or live in the Admin view. The simulated
  source emits packet C.
- **Race-event webhooks**: in addition to personal bests and session summaries, the
  webhook can now announce **overtakes**, **positions lost**, and **off-road
  excursions** (3+ wheels on a loose surface — requires packet format C). Position
  changes must hold ~1 s before firing so side-by-side battles don't spam. Every
  event type has a toggle in Admin → Notifications (`GT7_WEBHOOK_EVENTS` for
  env-based setups).
- **Opt-in admin auth**: set `GT7_ADMIN_TOKEN` to require a token (`X-API-Key`
  header) for the Admin API and every destructive/mutating endpoint; overlay, dash,
  and read endpoints stay open. The UI stores the token per browser and prompts on
  401. Unset = fully open, as before.
- `GT7_CORS_ORIGINS` for cross-origin API consumers (see Breaking below).
- `frames_dropped` counter in `/api/status` and the Admin diagnostics — 60 Hz frames
  the console numbered but the network lost.
- Admin view polish: per-event notification toggles with plain-language hints, and
  descriptive subtitles on every panel.

### Changed

- **Lap timing is robust to packet loss**: the time/distance axes integrate the
  console's packet counter (gaps clamped to 1 s) instead of assuming a perfect
  60 Hz stream, and input percentages are time-weighted accordingly.
- **Per-client WebSocket queues**: a slow or stalled viewer (browser, OBS) can no
  longer stall telemetry capture — it just misses intermediate frames; lap and
  session events are never dropped.
- Lap CSV export is written with a proper CSV writer and neutralizes spreadsheet
  formula injection in text cells.
- The sessions list is a single aggregate query (was one query per session).
- `dev.sh` rebuilds the frontend on every start, so `:8000` always serves the
  current UI instead of a stale `dist`.

### Fixed

- **Fuel strategy widgets no longer mix sessions**: on a car change or race restart
  the lap feed is pruned to the new session, so "laps of fuel" / "pit before" no
  longer average fuel consumption from the previous car or track for the first laps
  of a session.
- Lap imports are validated (required columns, equal lengths, finite numbers, size
  cap) and return a clear 400 instead of storing a file that breaks analysis with a
  500 later; a rejected import no longer creates an empty session.
- Restarting or switching the telemetry source fully awaits task shutdown and port
  release — no more races when rebinding UDP 33740.

### Security

- Webhook requests never follow redirects, and the webhook trust model is
  documented (LAN targets are intentional; the admin token guards configuration).

### Breaking

- The API no longer sends wildcard CORS headers. The bundled UI is unaffected
  (same-origin in both dev and prod). Separate cross-origin consumers must set
  `GT7_CORS_ORIGINS`.

## [0.2.1] - 2026-07-31

### Added

- Docs: a full **widget reference** page (`guide/widgets.md`) covering every widget's
  styles, color thresholds, alert triggers, and behavior under each track condition
  (paused, menus, first lap, past the reference lap, race finished, unlimited
  sessions, lost telemetry, placeholder mode).

### Changed

- The delta widget (overlay, `/dash`, Live view) now updates **live during the lap**:
  it compares your current track position against the session-best lap's trace
  (positive = slower). Before a reference lap exists it falls back to the end-of-lap
  comparison, labeled *Δ best (last lap)*. Live frames gain `delta_ms` and
  `lap_elapsed_ms` fields.

## [0.2.0] - 2026-07-31

### Added

- **Grid overlay builder** (Admin view): drag-and-drop widget placement on a snapping
  grid with per-widget footprints (1×1 up to 4×4 cells), ghost-outline collision
  feedback, and corner-handle resizing.
- **Widget style variants** — choose how each metric looks: speed as digits / bar / arc
  gauge; RPM as bar / shift-light LED strip / gauge / digits; fuel as percent / bar /
  laps-remaining; lap times as list / big-last / big-best; delta as big number /
  centered ± bar; and more.
- **New widgets** from previously unexposed telemetry: engine temps (water / oil / oil
  pressure / boost), driver-aid badges (TCS / ASM / handbrake / rev limiter), boost,
  and race alerts.
- **Server-saved named layouts** (`layouts` table, `/api/layouts` CRUD): OBS browser
  sources use short stable URLs (`/overlay?layout=<name>`) that keep working while the
  layout is edited. JSON export/import and one-click migration of old browser-stored
  presets.
- **Driver dashboard** at `/dash`: full-screen race-engineer screen for a second
  display, with built-in *Race engineer* and *Endurance* presets, a fullscreen toggle,
  and a connection status dot.
- **Race alert engine**: low fuel (warning < 3 laps, pulsing critical < 1.5), pit-window
  callouts, water/oil overheat, low oil pressure, and hot tires — suppressed while
  paused or off-track, and shared with the engine/tire widget color thresholds.
- Docs: new *Driver dashboard* guide, rewritten *Overlay & streaming* guide, layouts
  API reference, and fresh builder/dashboard screenshots.

### Changed

- Demo/placeholder mode now slowly drains fuel so strategy and alert widgets can be
  designed without driving.
- The Admin overlay builder's URL-parameter workflow is replaced by saved layouts;
  existing URL-parameter overlays (`/overlay?w=…`) keep rendering pixel-identical.

### Fixed

- The GitHub Pages docs deploy job no longer runs (and fails) on pull requests.
- Concurrent layout create/rename with a duplicate name returns 409 instead of 500.

## [0.1.0] - 2026-07-23

Initial release.

### Added

- **Telemetry capture** from a PlayStation on the same network: Salsa20 decryption,
  heartbeat keep-alive, console auto-discovery via UDP broadcast, automatic reconnect,
  and a built-in **simulated telemetry source** for development without a console.
- **Per-lap recording** at 60 Hz across ~28 channels (per-wheel slip, per-corner tire
  temps, suspension travel, driver-aids bitmask, …) with per-lap aggregates: aid usage,
  engine health, gearing metadata, and chassis events (lockup / wheelspin / bottoming /
  kerb detection).
- **Live view**: real-time dashboard with race readouts, driver-aid pills, fuel
  strategy projection, and a clickable recent-lap feed, streamed over WebSocket.
- **Analysis view**: multi-lap comparison against a selectable reference lap with time
  delta, synced cursors, channel picker, event bands, race line map, corner detail,
  consistency (deviation) view, fuel map, and gearing panel.
- **Sessions view**: lap-time sparklines, per-lap metrics with event counts, JSON
  export/import, CSV / MoTeC-compatible export, and record on/off + log-lap-now
  controls.
- **Track auto-identification** from lap geometry — name a circuit once and future
  sessions are tagged automatically.
- **OBS overlay** at `/overlay` with a URL-encoded builder: strip / stack / grid
  layouts, exact-pixel canvas sizes, transparent / green-screen / dark page modes,
  per-widget scaling, browser-stored presets, and placeholder demo data.
- **Admin view**: connection settings, runtime source switching, log viewer, webhook
  notifications (Discord-aware), car database updater, and data management.
- **Deployment**: single Docker image (amd64/arm64) serving API + UI on one port,
  Raspberry Pi guide, and a MkDocs Material documentation site on GitHub Pages.
