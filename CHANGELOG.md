# Changelog

Notable changes to GT7 Datalogger. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **Per-corner report card, sorted by time lost.** (#21) The Analysis view
  answers "where am I actually losing the lap" as a table under the charts:
  per corner, entry/minimum/exit speed and the time spent through it for the
  focused lap against the reference, sorted so the most expensive corner is
  the first row, with the total time lost in corners in the footer. Every lap
  is measured through the *reference lap's* corner windows — the same
  distance-from-start convention the time-diff chart uses — so the times are
  comparable, and a corner a lap never fully drove is omitted rather than
  reported against part of its extent. Clicking a row zooms every chart and
  the map to that corner.
- **The race engineer's coaching now exists in writing.** (#23) The findings
  CoachingDetector could only ever speak — repeated lockups on a named wheel
  into a named corner, a braking-point habit, where a slower lap actually
  lost its time and how — appear as a per-lap notes panel in Analysis, in the
  exact wording voice would have used. The notes are replayed from the stored
  session through the same detector (same thresholds, same
  reference-as-it-stood, same repetition windows) rather than recorded from
  speech, so every recorded session has them: sessions driven before voice
  existed, machines where voice is off, sessions someone else recorded. Each
  distinct observation is noted once, notes that name a corner zoom the
  charts and map to it, and the compared laps are highlighted.
- **Shared bundles can be pulled from inside the app.** (#47) The Tracks view
  now lists what the shared track-data repo offers (its published
  `index.json`, `GT7_SHARED_BUNDLES_URL`, defaulting to the project's own
  gt7-datalogger-track-data site) alongside what you hold locally, and
  **Pull** fetches a circuit's bundle and merges it through exactly the
  import path: full field-by-field validation, the voting merge that keeps
  every source's evidence a census, and your own corner labels never
  overwritten. The server only ever fetches the index and the file the index
  maps the requested slug to — clients never supply URLs — with size caps
  enforced while reading. Setting the URL empty hides the feature; nothing is
  fetched until the Tracks view is opened.
- **Laps from other sessions can join a comparison.** (#26) Analysis could only
  ever overlay laps from one session, which made the interesting comparisons
  impossible: today's stint against last week's, this car against that one at
  the same circuit, your line against the leaderboard leader's salvaged replay
  (below). **+ Add lap…** now lists every lap recorded at the session's circuit
  — fastest first, because that is the one you are looking for — and a picked
  lap joins as a guest chip labelled `S12·L3`. The class-best benchmark row
  gains a **compare** action that pulls the benchmark lap straight into the
  current comparison. Nothing downstream needed convincing: the compare API
  always spoke global lap ids, and alignment is distance-from-start, which
  holds across sessions on the same circuit — which is also why the picker
  offers only the session's own circuit, never another one. Cross-session deep
  links (`#/analysis?session=3&laps=208`) now resolve instead of being pruned.
- **A personal-bests board.** (#26) New **Bests** tab: for every circuit, the
  fastest *counting* lap per car across every session ever recorded — time,
  Δ to the circuit's outright best, category chip, how many counting laps
  stand behind the row, when it was set, and one click into Analysis. Served
  by `GET /api/laps/bests` (one row per circuit + car; optional `category=`
  filter). Partial laps never own a row, for the same reason they never own a
  session best: the logger only saw part of them, so their reported time is
  shorter than any real lap's.
- **Sessions can be excluded from bests.** (#26) Replay capture (below) has a
  consequence: a replay records *another driver's* laps, and no telemetry
  field tells them apart from your own driving. So a session can be flagged
  **excluded from bests** in the Sessions view: it keeps every lap, and its
  laps stay selectable for comparison — overlaying your line on the leader's
  is the whole point of capturing the replay — but it never owns a Bests row
  and never provides the class-best benchmark (`/api/laps/best`). Backed by
  `PATCH /api/sessions/{id}` (admin-gated) and a new `bests_excluded` column
  (Alembic revision 0003).
- **New simulator scenario `leader_replay`** (`GT7_SIM_SCENARIO=leader_replay`):
  pre-roll footage, one flying lap streamed as lap 0 with a running packet-C
  lap clock, then `LOADING` — a single-lap TT-leader replay, staged, so the
  salvage path below can be exercised end to end without a console.

### Fixed

- **Watching the time-trial leader's replay now leaves a lap you can
  analyze.** GT7 streams a replay exactly like driving — no replay flag exists
  — but a single-lap replay ends *at* the finish line, so the lap-counter step
  to `prev+1` that commits a lap was never observed: the fully-driven lap sat
  in the buffer, the stream cut to `LOADING`, the buffer was discarded, and
  the now-empty session was deleted. The replay watched specifically to study
  a lap left no trace at all. Now, when the stream breaks off (menu/`LOADING`
  transition, lap-counter reset, car change) with a full lap in the buffer,
  the lap is saved **if GT7's own reported lap time agrees with the integrated
  duration within max(500 ms, 0.5 %)** — the one piece of evidence that the
  buffer covers exactly one lap — so aborted half-laps are still discarded.
  Replays streamed as "lap 0" are buffered too (real out-laps still never
  commit, and the lap-0 buffer is capped at 15 minutes against menu noise),
  and with packet C the footage around the lap — pre-roll before the line,
  and any post-line stub when the stream runs past the crossing — is trimmed
  off using GT7's own lap clock, so the saved lap's distance axis starts at
  the start line and aligns with driven laps in comparison. Salvaged laps
  keep GT7's time, pass the same span guard as every other lap, carry a
  stored `salvaged` marker shown wherever the lap appears (lap tables, the
  Bests board, the Add lap… picker), and **end their session**: whatever
  streams next — another replay, your own driving in the same car — opens a
  fresh one, which is what keeps a replay separable from the laps you then
  drive yourself. A discarded buffer of plausible size logs its tick count
  and candidate times for diagnosis. Multi-lap race replays already recorded like driving — the
  salvage additionally rescues their *final* lap when the replay runs to the
  flag, while a final lap the replay cuts short still fails the time check and
  is discarded. (#26 is what made this matter: the lap most worth co-selecting
  in a cross-session comparison is the leader's.)

### Changed

- **Lap lists no longer drag the telemetry along.** (#26) Every lap-summary
  query loaded each lap's full 60 Hz sample blob only to show a row of
  aggregate numbers — the storage hotspot #26 flagged, and one that grew with
  the archive. The summary queries now leave the blobs unread, which is what
  makes querying the *entire* archive — the Bests board, the Add lap… picker —
  cheap. `GET /api/laps` also gained `track=` and `category=` filters, and lap
  summaries now carry `track_name`, so both features are one request each.

## [0.5.0] - 2026-08-17

### Added

- **The survey compiles into an actual track.** (#38, #40) The bundle store
  keeps border evidence as an unordered cloud of voted metre-cells; everything
  downstream wanted them in *order*. A new compile pass walks each side's
  cells into ordered polylines — 96–99 % of cells chain on the surveyed
  circuits, generally into one closed loop per side — and derives the
  centerline with measured road width and elevation, the road surface as a
  contiguous quad strip, and the finish line. The Analysis map now draws that
  road: on Deep Forest the fill went from 9 paired spans to ~940 quads,
  because the compile pairs a border against the *opposite border's curve*
  rather than hunting for a cell directly across (which almost never exists —
  the two sides are surveyed on different laps). Unsurveyed stretches are
  never invented: they are flagged as gaps, drawn dashed, and **coverage is
  now measured against the boundary itself** — surveyed metres over total
  boundary metres, gaps and loop-closure in the denominator — shown per
  bundle in the Tracks view. The compiled document is derived data
  (`data/track-bundles/compiled/`, rebuilt automatically whenever the bundle
  changes), with JSON Schemas published for it and for bundle v4 in the docs.
  Raw survey JSONL logs can now also be downloaded and uploaded, so a run
  recorded on one machine can be replayed into another's bundles.
- **Laps are judged against the surveyed edges, not just the surface
  flags.** (#41) The surface characters are blind to paved run-off — running
  wide over asphalt reads as tarmac and stays "clean". When a circuit's
  survey resolves at least half its road, each lap's positions are now also
  classified against the compiled road surface: sustained excursions beyond
  the surveyed border count separately (`off_survey_count`), appear as a
  second figure in the Sessions table, and spoil `clean_lap`. Unsurveyed
  ground never counts against a lap, and laps recorded before the session
  was identified are re-judged the moment identification names it.
- **The integrated clock now checks itself against GT7's.** (#20) Elapsed
  time is integrated from packet ids; packet C also broadcasts the game's own
  live lap clock, decoded and until now unused. The processor tracks how far
  the two drift apart within each lap — surfaced in `GET /status` next to
  `frames_dropped`, with a log warning when a lap drifts past 100 ms.
  Diagnostic only: it validates the time axis every chart is drawn from.
- **The race-line map is something you can actually look at.** It was a
  360-pixel thumbnail that only the charts could drive, which is not enough
  map for a 5 km circuit. Now: **⤢ opens it full screen**, with scroll-to-zoom
  and drag-to-pan (kept out of the rail, where a wheel that sometimes scrolls
  the page and sometimes zooms is worse than one that always scrolls); a
  **corner strip** under the map takes you to any corner in one click — as does
  clicking the numbered circle on the map — and drives the *shared* zoom, so
  the charts follow the map into the corner instead of the two disagreeing;
  and the reference lap is drawn as a **continuous zone-coloured line** rather
  than a dotted trail, because samples land every 5 m and that reads as a line
  across a whole circuit but as scattered dots exactly where you have zoomed in
  to look closely.

    The map is also **no longer stretched**. A metre across is now a metre
  down — the axis ranges follow the plotting area's pixel aspect, so the shape
  holds in the rail and at full screen alike. Letting each axis fill the box
  independently distorted it by the circuit's aspect ratio: 8 % at Lago
  Maggiore Centre and nearly 3x at Deep Forest.
- **A surveyed circuit now recognises itself.** Auto-identification only ever
  matched against signatures written by naming a track by hand, which left a
  hole big enough to make surveying feel broken: survey three circuits, never
  use *name track…*, and the app has a metre-accurate map of each while still
  failing to recognise the next session driven there — so the track badge, the
  outline under the race line, category bests and corner labels all stay empty
  on a circuit it has mapped in detail. Having surveyed a track and having
  named it were two separate facts and nothing joined them.

    A lap with no matching signature is now compared against the survey
  bundles, which are a strictly better fingerprint than a bounding box — they
  are the road, not a rectangle around it. Matching asks the only question that
  matters, *did this lap drive on this surveyed tarmac?*, and needs both a
  coverage floor and a clear margin over the runner-up: two configurations of
  one venue share tarmac, and a thin margin means the evidence does not
  actually distinguish them, so the session stays unnamed rather than being
  given a wrong name silently. Thresholds calibrated against 321 real sessions,
  where the scores turn out sharply bimodal with an empty band to put the cut
  in. A signature someone typed still wins outright. (#41)
- **Sessions recorded before a circuit was surveyed can be named in bulk.**
  New sessions identify themselves, but history already on disk never got the
  chance — which, for anyone who surveyed a circuit before this shipped, is all
  of it. **Tracks → Identify sessions** re-runs the match over every unlabelled
  session, reading each one's *shortest* usable lap rather than a whole
  session's telemetry. (#41)
- **The surveyed road now sits under the race line in Analysis.** A racing line
  only means something against the road it was driven on — whether the apex was
  clipped, how much kerb was used, whether there was tarmac left on the exit —
  and until now the map drew the lap floating in empty space. When the
  session's circuit is named and surveyed, the map draws the track beneath
  every lap: road surface, both borders, hand-marked walls in their own colour,
  and the start/finish line. The geometry is compiled server-side
  (`/api/track-outline`) and cached per bundle revision, because a bundle is up
  to 50,000 border records and the browser has no business downloading a
  circuit's whole survey history to draw a map. Circuits with no bundle answer
  with an empty outline — never having been surveyed is the common case, not an
  error — and the map falls back to exactly what it drew before. (#51, carved
  out of #41)
- **Traction circle (g-g diagram)** in the Analysis side rail, from the
  accelerometer GT7 has been broadcasting and this app has been throwing away.
  Every moment of the lap plotted as lateral against longitudinal g, coloured by
  input zone: how much of the ring gets used is the reading, and an empty
  middle-left/middle-right is a car that never brakes and turns at the same
  time. Compared laps overlay as faint dots, the cursor is synced with the
  charts and the map, and the peak g in each direction is called out.

    **The scale is checked, not assumed.** GT7 documents neither a unit nor a
    sign convention for `sway`/`heave`/`surge`, and a simulator cannot prove
    what a real console sends — which is why #16 said to validate before
    building any UI. So the app validates, per lap, against physics the same
    lap already recorded: lateral against `v × ω` (with the signed yaw rate
    taken from the driven path, since the stored yaw column is absolute),
    longitudinal against `dv/dt`. A least-squares slope through the origin
    recovers the unit *and* the sign at once, so the diagram comes out upright
    whichever way the console counts. When a lap gave too little steady
    cornering or braking to check, the panel says **scale unverified** rather
    than drawing a confident-looking circle on an unproven scale. (#16)
- **Steering-angle channel.** `wheel_rotation` has been decoded since packet B
  support landed and dropped on the floor ever since. It is now a stored column
  and a chart panel: next to the yaw-rate trace it is what makes understeer
  legible — more lock, no more rotation — along with corrections and
  catch-and-release oversteer. (#15)
- **ABS and TCS intervention, measured instead of inferred.** The `~` packet
  format carries the pedal positions *after* the aids acted on them; plotted
  against the raw pedal the two lines separate exactly where an aid stepped in.
  New channels: **Throttle applied**, **Brake applied**, and the two gaps on
  their own — **TCS cut** and **ABS release**. The aids bitmask only ever said
  *whether* an aid was active; this says how much it took. (#18)
- **Car category as a real dimension** (#19). The class was already stored and
  already filtered the Sessions list; it is now a server-side filter
  (`/api/sessions?category=Gr.3`), it survives a session whose first packet
  arrived in a narrower format (the session inherits its laps' class rather than
  sitting outside its own filter), and Analysis shows the **class benchmark** —
  the fastest full lap ever recorded at this circuit in the same category, the
  gap to the reference lap, and a link to open it (`/api/laps/best`). Scoped by
  class deliberately: a Gr.3 time and an N100 time around the same corners are
  not the same achievement.
- **Tracks view** (new tab): the three sources of track knowledge, joined —
  the DB's named tracks (which is what makes auto-identification work), the
  survey bundles, and the bundled official GT7 catalog. Having one is not
  having the others, and until now nothing said so: that gap is how a survey
  ran for ~55 minutes attached to no circuit at all. Each row shows what is
  present and what is missing (auto-ID, survey, official layout, metres
  mapped, runs and contributing sources, elevation completeness — which only
  fills in by re-driving — finish line located, corners labelled against the
  official turn count) with the action for each gap beside it: assign an
  orphaned run, rename (which **merges** when the new name is an existing
  bundle, fixing one circuit living under two near-miss spellings), confirm
  the official layout, label corners, export, import, delete. One endpoint
  (`/api/track-overview`) does the join, because the interesting rows are the
  ones where the sources disagree. (#46)
- **Orphaned survey runs are now visible and recoverable from the browser.** A
  survey with no circuit label saves no bundle at all, so such a run existed
  only as its JSONL. The Tracks view lists every one of them and assigns it to
  a circuit, replaying the log through the normal merge path — the same job
  `scripts/jsonl_to_bundle.py` does, which now shares the code. (#45, #46)
- **Bundle import and cross-machine merge**, so survey work moves between
  machines and people and fidelity accumulates instead of one copy winning.
  The prerequisite was a **source id**: run ordinals are local, so my run 7
  and your run 7 are unrelated facts, and merging on the ordinal alone would
  double-count one and silently drop the other depending on which ordinals
  collided. Every installation now stamps a per-installation id (generated
  once into `data/source-id.json`) on every vote it casts; a merge advances
  each source's own highest run, so two people who each drove a metre once
  have seen it twice between them and re-importing the same shared bundle
  changes nothing. Imports are validated field by field before anything is
  merged — an import writes into the same store the app surveys into — and
  versions 1 through 4 are accepted and upgraded. The format is now published
  as a schema (`docs/reference/track-bundle-format.md`), since the point is
  other tools reading it — and contributed bundles now have a home at
  [gt7-datalogger-track-data](https://github.com/jbhoorasingh/gt7-datalogger-track-data),
  which lists every GT7 configuration, publishes a downloadable pack, and
  [draws each surveyed circuit](https://jbhoorasingh.github.io/gt7-datalogger-track-data/)
  so a contribution can be eyeballed before it is merged. (#47)
- **Authored corners and sections, labelled by hand and stored in the
  bundle** (format v4), with a refine view: open a surveyed circuit's map and
  click your way around it, naming corners and optionally marking turn-in and
  exit. Authored data outranks derived data, the same principle the border
  voting already follows. `detect_corners()` runs *per lap* off the racing
  line, so a driver who straightlines an S drops it below the significance
  threshold and every corner after it renumbers — "turn 4" meaning different
  tarmac from one lap to the next is no foundation for a per-corner report
  card (#21) or real sectors (#22). Labelled corners are anchored to world
  positions (distance depends on the line taken), so each lap resolves its own
  distances while the numbering holds still. They take over in the analysis
  endpoint and in Race Engineer callouts, which now speak the name — "you lost
  three tenths in the Parabolica" rather than "in turn four" — and they travel
  with export/import, which is a large part of what makes a shared bundle
  worth pulling. Sections are the input real sectors need, since GT7
  broadcasts none. (#48)
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

- **A running survey can be assigned to a track, and a past one rebuilt from
  its log.** A survey left running through a race gathered 1,087 border metres
  against no circuit at all — and a survey with no label writes no bundle, so
  stopping it would have discarded the lot. The track field is now live during
  a run with an explicit Assign action (`POST /api/survey/track`), which keeps
  everything already gathered and merges it into that circuit's bundle;
  reassigning an already-labelled run still flushes to the previous circuit
  first, so one track's driving can never land in another's. Naming the
  circuit from the current session (Sessions -> "name track...") now labels a
  running survey too. For runs that ended unlabelled,
  `backend/scripts/jsonl_to_bundle.py` rebuilds a bundle from the JSONL —
  `mark` lines carry straddle and manual edges verbatim, transitions carry the
  contact points `auto` edges reconstruct from — and merges it under a chosen
  track. Used to recover the run above: 1,529 samples collapsing to exactly
  the 1,087 cells the live survey held, with elevation on all of them.
  Re-importing the same log adds nothing, so it is safe to re-run. (#45)

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

- **Dropping a channel no longer leaves its panel title behind.** The
  Analysis charts merge their new layout into the old one, replacing only
  the series — so picking fewer channels left the removed panels' titles
  painted on top of the ones that remained (and orphaned grids and axes
  behind them). Everything whose count follows the panel list is now
  replaced wholesale.
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

- **Schema changes are Alembic revisions now, not a growing list of
  `ALTER TABLE ADD COLUMN`.** The old mechanism could only ever *add* a
  column, was SQLite-only, and grew by one entry per feature; nearly
  everything on the roadmap adds columns. Alembic was adopted while the
  schema is still small, with the existing list folded into a baseline
  revision. **Existing databases upgrade in place and lose nothing**: a
  database that predates migrations is brought to the baseline shape by the
  old list — now frozen, and kept for exactly this — and then stamped, so an
  install from the first release and a fresh one converge on the same schema
  and everything after this is an ordinary revision. `init_db` runs
  migrations to head on every startup; the test suite asserts the upgrade
  path against a first-release database, lap rows and all. (#14)
- **The simulator's car is now internally consistent.** Its broadcast yaw
  rate is the actual turn rate of the line it draws, its accelerometer is
  `v × ω` and the real speed delta rather than arbitrary multiples, and its
  filtered pedals differ from the raw ones only while an aid is
  intervening. Features that check one channel against another — the g-g
  calibration, the intervention traces — could otherwise never be exercised
  without a console, because the synthetic data would fail the same check
  real data has to pass. (#16, #18)
- **Optional sample columns are absent rather than zero-filled.** The
  channels that need an extended packet format (steering, accelerometer,
  filtered pedals) are only recorded on the ticks that carried them, and a
  lap that did not carry one from start to finish drops it entirely. A
  zero-filled steering trace reads as "the driver never turned"; a missing
  panel says nothing false. This also covers a recording whose packet format
  changed mid-lap. (#15, #16, #18)
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
