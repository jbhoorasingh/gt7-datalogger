# Changelog

Notable changes to GT7 Datalogger. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed

- **Web UI usability revamp.** Admin is renamed **Settings**. It now has a
  section rail, a health strip, and a single Apply bar that sends every
  buffered edit in one request, replacing seven separate Apply buttons. The
  overlay builder moves to its own **Overlays** tab. Sessions becomes a
  master–detail view with a lap-time chart, a bulk-action bar and
  auto-hidden constant columns. Tracks gains a "Needs you" list and a
  readiness checklist. Live is denser: shift lights, a Δ card and a pinned
  in-progress lap. `/engineer` and `/dash` are rebuilt for touch. New
  route-level pages cover 404, a locked server, server errors, an
  unreachable server and a silent console. The status bar shows telemetry
  as a labelled dot. The fastest lap is purple everywhere. `#/admin` links
  still work.

### Fixed

- **Menus, pit stops and pauses no longer merge or split sessions.** (#120,
  contributed by @NikiforovG) GT7 keeps streaming while the car is not being
  driven, and those packets carry lap counters of their own: 0 or -1 in a
  menu, 0 in the pits. The lap processor read them as it read driving. A
  race that ended at lap 12 and went through a menu at -1 left the counter
  parked there, so the next race's lap 1 was not seen as a reset and the two
  races were recorded as one session; a pit interruption that read
  `6 → 0 → 6` was seen as a reset and split one race in two. A car change or
  a lap reset is now confirmed only by a packet that is on track, unpaused
  and has a lap number of 0 or more. Inactive packets keep the last driving
  lap and its buffer, and never open a session by themselves. Two ways of
  finishing a lap still accept them: the time-validated replay salvage, and
  the final lap's `+1` step from the same car, which records the race result
  even when the finishing packet has already cleared the on-track flag. The
  packet counter is followed through loading screens as well, so their
  frames add nothing to the lap clock. A new session logs why it began.
- **The session being recorded cannot be deleted from under the recorder.**
  (#121, contributed by @NikiforovG) Deleting the current session removed its
  row while the recorder kept its id: laps driven afterwards were saved
  against a session that no longer existed, and a race's finishing result
  was lost without a word. `DELETE /api/sessions/{id}` now answers **409**
  for the recorder's current session, paused or not, and the Sessions view
  says why. The check is serialized with session creation, so a session
  cannot be deleted in the moment between its row being committed and the
  recorder adopting its id. Every other session deletes as before. The
  current session becomes deletable once the next one has started, or after
  a restart.
- **Analysis no longer shows the previous session under a new session's
  name.** Picking a session with no completed lap yet from the Analysis
  dropdown — which is every session until its first lap is finished, so
  exactly the one you have just started driving — changed the title and
  nothing else: the lap chips, the map, the charts and the address bar all
  stayed on the session that had been open, its laps relabelled `S256·L2` as
  though they had been added from another session on purpose. The lap loader
  returned before touching the selection when a session had no laps, and a
  selected lap that is not the session's own is, by design, a guest. Leaving a
  session now takes its selection, reference, guests and lap list with it, a
  session with no laps resolves to an empty selection
  (`lib/analysisSelection`), and the view says *No completed laps in this
  session yet* in place of the map until the first lap arrives, when it fills
  in by itself. The address bar and the selection remembered between tabs
  name the session alone. A lap reply that arrives for a session already
  left is dropped, so two quick switches cannot land out of order, and the
  consistency chart is cleared with the session it belonged to. Laps from
  another session named in a link still load as guests, into an empty
  session too.
- **Pinning the old car CSV no longer wipes the car details off your
  sessions.** An install that still sets `GT7_CARS_CSV` loads a file that
  knows a car's id and name and nothing else. At the next start after a
  background refresh had filled the sessions in, the startup backfill found
  the rows were last built from a different inventory, ran again from the
  CSV, and wrote its blanks over every figure: manufacturer, year,
  drivetrain, power, weight and PP all went back to empty, and stayed empty
  until the next refresh a week later. The backfill now writes the figures
  only from a record that has some (`Repository.backfill_session_cars`); a
  record that is a name alone still names a session showing `Car #1234`,
  which is what the pin is kept for, and erases nothing. A record with any
  detail is written whole as before, noughts included, so an electric car's
  displacement is still 0 and a corrected figure still replaces a wrong one.
  Sessions blanked before this release are filled in again by the next
  refresh, or at once by starting without the pin. `data/cars.json`, where
  the refresh writes, is now ignored by git.

- **Pulling a shared bundle no longer breaks sync for that circuit.** A pull
  merges every contributor's votes into your bundle, source ids and all, and
  the tracks adapter then uploaded the whole document. The sync service binds
  every source id an upload names to the uploading account and refuses one
  bound elsewhere, all or nothing — so the next upload after a pull was a 409
  `source_conflict`, the row read *sync rejected*, an error-severity issue
  was raised in the service's admin panel and its alert webhook fired, and
  the circuit stayed rejected until the bundle changed and failed again. An
  upload now carries **this installation's own evidence** alone
  (`track_bundle.own_evidence`): the votes cast under its source id, each
  record's kind re-resolved from those, the metres it never drove dropped,
  its own run count — with the corner labels, sections, finish crossings and
  confirmed layout travelling whole. That is what the merge job wanted in the
  first place (the metres you added), and it is smaller. The change digest is
  taken on the same projection, so a pull that only adds other people's
  evidence does not queue an upload, and a bundle you pulled but never
  surveyed here reads *not synced — nothing of yours* rather than sending a
  stranger's work. Every bundle is re-sent once after upgrading, since the
  remembered digests were of whole documents.

### Added

- **Back up every lap in one download.** Settings › Data has **Export all
  laps (JSON)** and **CSV (all laps)**. Each gives one ZIP with a folder per
  session. The JSON archive holds each lap's export file, the same file
  `GET /api/laps/{id}/export` serves, so each lap can be imported again. It
  also holds the session's `session.json`. The CSV archive holds each lap's
  CSV, the same file `GET /api/laps/{id}/export.csv` serves. Both archives
  are streamed a lap at a time, so a large database starts downloading at
  once and never sits whole in memory or in a temporary file. The endpoints
  are `GET /api/export/laps.zip` and `GET /api/export/laps-csv.zip`. Like
  the single-lap exports, they do not need the admin token. **Compact
  database** now says roughly how much it would reclaim, once that is 1 MB
  or more. `GET /api/admin/stats` reports the figure as
  `db.reclaimable_bytes`.
- **The corner report card says where you braked against the reference.**
  (#110) "Braked 14 m earlier into T3" is a number a driver can act on next
  lap, and the card did not have it. Three columns now say how each corner
  was braked for: **Brake Δ m**, where the brake went on against the
  reference lap in metres along the track (negative is earlier, dimmed under
  5 m, which is the same brake point), **Peak %**, the most pedal the zone
  saw, and **Zone m**, its length. A corner taken without braking reports
  nothing and not zero; where one lap braked and the other did not, the
  column says which. **Hovering a row pins the two brake points on the
  race-line map** in the laps' colours, and the pins stay for the corner the
  view is zoomed to, so they are still there after clicking a row and
  scrolling up; the map's frame widens to take them in. The braking point is
  not the card's: it is `processing/corner_metrics`' answer, the one the race
  engineer speaks and the lap analysis document carries, on the reference
  lap's distance axis. `corner_report` rows on `GET /api/analysis/compare`
  gain `brake_on`, `brake_off`, `brake_peak`, `brake_dist` and
  `brake_delta_m`, `null` where the lap did not brake.
- **Body slip angle: understeer and oversteer, measured.** (#109) The angle
  between where the car points and where it is going was only inferable, from
  yaw rate, steering and the g-g diagram read together. It is now recorded
  per tick as the `body_slip` column, in degrees, positive with the nose to
  the right of travel, and zero below 29 km/h where the angle is noise. It is
  a **Body slip** channel in the Chassis group of the channel picker (off by
  default) and a column in the CSV export, and the corner report card gains
  **Slip pk °** and **Slip avg °** per corner against the reference, turned
  so that positive is the nose pointing into the corner whichever way it
  goes: more than the reference is more rotation. What GT7 sends for the
  car's heading was the open question, and a real capture settled it: the
  packet's "rotation" and "orientation to north" are neither three angles nor
  a heading but one unit quaternion (its length is 1.00000 in all 793
  captured packets), the car's nose is its local −Z, and turned by it the
  nose lies along the velocity to within 1.2° in nine packets of ten. The
  angle is taken in the car's own frame, so banking and gradient do not read
  as slip. An optional column: a recording from a source that sends no
  orientation has none, and neither do laps recorded before this, which kept
  the car's position and not its heading. The lap file's format version is
  unchanged. See `docs/internals/derived-channels.md`.
- **Stint trend: tyre temperature and lap time, lap over lap.** (#111) Over
  a stint the tyres go away and the lap times follow, and nothing showed the
  two together. A new full-width panel in Analysis draws every lap of the
  session: lap time as points, the tyres' average temperature as lines
  (front and rear, or each wheel), and the fuel on board as an optional
  third. The session is split into stints at pit stops, and each stint has
  its drift as a figure — `+0.18 s/lap`, with the tyres' own in °C a lap —
  and as a dashed line through its laps. A lap the car was in the pits on
  belongs to neither stint, laps that do not count are gaps and not points,
  and a lap too slow for the scale is pinned to its top edge. The drift is a
  Theil–Sen slope, the median of the slopes between every pair of laps: one
  spin moves a least-squares line from +0.18 to +0.80 s a lap and the median
  not at all. GT7 sends no tyre wear, and the panel says that temperature
  and pace drift are the only proxies. `GET /api/analysis/stint` works it
  out from the stored laps, one lap in memory at a time, so it covers every
  session already recorded; nothing is added to the database. GT7 flags no
  pit stop either, so a lap is taken for one by what a stop leaves behind:
  fuel that went up, all four tyres changing temperature at once, or lap
  time the car was not being driven for. **None of the sessions this was
  checked on contains a pit stop**; the rules are tested on laps made to
  carry each sign. See `docs/internals/analysis-math.md`.
- **Lap analysis: a session as a few hundred labelled numbers.** (#115) A
  lap is stored and exported as its 60 Hz recording, which is the right shape
  for a chart and the wrong one for anything that has to reason about the
  driving. **Sessions → Export analysis** (`GET
  /api/sessions/{id}/analysis.json`) compiles the session into one
  `gt7-datalogger-lap-analysis` v1 document: every lap measured per corner
  against the session's best counting lap, on that lap's distance axis, through
  the circuit's authored corners where it has them (the document says which).
  Per corner of each lap: the braking zone (where it began and ended, peak
  pedal, length, lockups, metres later or earlier than the reference),
  turn-in and how far the brake was carried past it, minimum speed and where
  it sat against the apex, throttle pick-up and full throttle, wheelspin and
  TCS on the exit, time lost in the corner **and on the approach to it**, the
  line against the reference at entry, apex and exit, and on a surveyed
  circuit the road left to each edge. Per lap: fuel, tyre temperatures, aids
  use, event counts, upshift RPM by gear. The car's power, weight and PP are
  the inventory's and are written under `car.stock`, because GT7 does not
  broadcast them for the car as tuned; the gear ratios are the session's own. Per authored section: time and a
  speed trap. Per session: each corner's spread in braking point, minimum
  speed and time across the clean laps. No samples and no positions — about
  100 KB for ten laps, compiled in a few tenths of a second, one lap in
  memory at a time. The same file is in the session ZIP as `analysis.json`.
  See `docs/reference/lap-analysis-format.md`.
- **The lap analysis goes to the sync service with the session.** (#115) With
  `sessions` sync on, a drive's lap analysis is sent after its totals
  (`PUT /v1/sessions/{id}/analysis`), compiled at that moment from the
  database, and sent again when a lap ruled in or out by hand has changed
  the reference. No new toggle. It is sent **only to a service that says it
  takes it** — `analysis_version` among the `sessions` hints of its
  capabilities, naming a version at least the document's — because a service
  without the endpoint answers 404, and a 404 on an open session is how the
  adapter learns a session was deleted. Against a service that predates the
  document a drive syncs with exactly the requests it always did. Whatever
  the server says about the document is recorded against the document
  alone: a refusal, a 404 included, never closes the session or touches its
  laps. A document larger than the server's `analysis_max_bytes`, or of a
  newer version than it reads, is held back here. Drives already on file
  when the logger is upgraded are not sent one after the fact. The Admin
  status line counts the analyses on the server, and the refused ones.
- **The shared repo's corrections come with a pull, and the map is compiled
  from them.** The repo's editor never deletes evidence; it keeps a
  `corrections/<slug>.json` beside each corrected bundle — areas the map
  should not draw (a pit wall recorded as the border), borders drawn in (a
  bridge across a gap nobody has driven), the circuit's own smoothing answer
  — and its merge job applies that file when it compiles the published map.
  The app now reads the same format (`track_corrections`, held to the repo's
  `tools/corrections.py`): a pull fetches and validates the file the index
  names beside the bundle, before merging anything, and keeps it at
  `data/track-bundles/corrections/<slug>.json`; the compiler applies it and
  recompiles when it changes, exactly as when the bundle does; the lap judge
  sees the corrected road. Replaced whole on every pull, never merged, removed
  when the repo stops correcting the circuit, never uploaded and never
  written into the bundle. `PUT` / `GET` / `DELETE
  /api/track-bundles/{slug}/corrections` take a file that arrived another way
  (the track-data pack's import script). The compiled document is version 4
  and says what was done under `corrections`; the Shared bundles panel marks
  a circuit the repo corrects, and the pull's toast says what was applied.

### Changed

- **The simulated car has a heading.** The simulator sent every packet with
  a velocity of `(speed, 0, 0)` and no orientation, whichever way the car was
  going. It now sends the velocity along the path it is driving and the
  car's orientation as GT7 does, turned from the path by a body slip that
  points the nose out of a slow corner and into a fast one, so the body slip
  channel can be seen in sim mode. `build_packet` takes an `orientation`.
- **The Race Engineer's coaching compares laps by place, and knows which
  corner a braking zone belongs to.** (#110) Two things were wrong with
  "you braked twelve metres earlier into turn five". The laps were compared
  at equal *distance*, and a lap's own distance drifts from another's by a
  median 2.9 m and up to 64 m — the Analysis view has lined laps up by
  position since 0.6, the engineer had not. And the braking point was "the
  first brake application in the 250 m before the corner's entry", which
  through a sequence is often the corner before's: on the best laps of nine
  stored sessions it disagreed with where the corner's braking zone began at
  41 of 105 corners. Coaching now puts every lap on the reference lap's
  axis before comparing (off the event loop, at the lap boundary), and takes
  what a lap did at a corner from `processing/corner_metrics` — each brake
  application belongs to one corner, the first apex its midpoint has not
  reached, and a corner's braking zone is the one that took the most speed
  off. The lap analysis document uses the same definitions, so the two
  cannot disagree. Replayed over 21 stored sessions the number of coaching
  notes is unchanged (120) and 59 are word for word the same; gone are the
  likes of "you are braking early into turn ten, about two hundred thirteen
  meters". Thresholds for what is worth saying are unchanged.
- **SQLAlchemy 2.1 or newer is required.** The repository layer's `Select`
  and `Row` annotations follow 2.1's variadic generics (a five-column
  projection is `Row[int, int, int, bool, bool]`; a row of any shape is
  `Row[*tuple[Any, ...]]`). No query changed.
- **Compiled borders are smoothed.** A border record sits on a 1 m grid, and
  an `edge` record and a `straddle` record of the same kerb disagree by up to
  a metre about where it is, so an ordered border stepped sideways wherever
  the kind changed — nicks in the drawn line, and a road whose edge moved a
  metre in a metre. The compiler now smooths each surveyed run with Taubin's
  λ|μ pass, which does not pull curves towards their inside the way an
  average does: against corners whose true border is known it stays within
  0.35 m on everything from a 3 m kerb to an 80 m sweeper, where a ±8 m
  moving average cuts 2 m off the kerb. Ends of a run stay where the survey
  stopped, nothing is smoothed across a gap, no vertex moves more than
  0.75 m from its record (under `track_limits`' edge margin), and the bundle
  itself is never rewritten. Over the 24 circuits in the shared map: 3 103
  kinks to 19, every gap span unchanged, coverage within 0.3 of a point.
  `compile_bundle(doc, smooth=False)` compiles the evidence exactly as
  recorded, and the document's new `smoothing` key says which it got.
  Compiled format 3, so every stored compile is rebuilt once.

## [0.6.2] - 2026-09-19

### Added

- **Events and driver aids on the race-line map.** (#103, #104) The map in
  Analysis gains two layers, each with a toggle in its header that only
  appears when the selected laps can fill it, remembered on the device.
  **Events** marks where each detected lockup ◆, wheelspin ●, bottoming ✚
  and kerb strike ✖ began, filled with the lap's colour so the same braking
  zone going wrong on three laps is visible as three marks in one place.
  Hovering a marker names the lap, the wheels and the severity in words
  ("slowest wheel at 62% of road speed"); clicking one zooms every panel to
  the event with 60 m either side. Suspension events are detected per wheel,
  so same-type events beginning within 6 m of each other draw as one marker
  that names all the wheels. **TCS** rings every sample where traction
  control was cutting power, in the lap's colour and hollow so the input
  zone underneath stays readable; **ASM** does the same with squares and
  starts off. Both follow the zoom, draw in the full-screen map, and the
  key under the map lists only what is drawn in the current window.
- **Session consistency, as a figure and a chart.** (#112) Each session row
  carries its consistency under the lap-time sparkline — `±0.42 s · 0.4%`,
  the standard deviation of the lap times and that as a share of the median
  lap, green under 0.5 % and amber from 1.5 %. Analysis gains a **Lap times
  — this session** panel: every lap as a point against lap number, a line
  through the laps that count, the median dashed and a one-sigma band
  around it, with the spread, median and best beneath. Both are taken over
  the laps that count toward bests and no others, the same verdict the bests
  board acts on, and need three of them: a pit out-lap, a race's lap 1 and
  laps excluded by hand are drawn hollow and left out of the figure, and a
  lap too slow for the scale is pinned to the top edge so one out-lap cannot
  flatten the rest. Points in the current comparison take their chart
  colour, and clicking a point adds the lap to the comparison or removes it.

- **Sessions and live sync.** (#79) The sync client now sends the other two
  data types the service takes, each behind its own toggle in Admin → Sync,
  off by default and offered only when the server lists it. **Sessions**
  sends your own laps as you drive — each lap as the `gt7-datalogger-lap`
  document Export writes, against a session the service opens for the
  drive — and the totals when the drive ends. The session is announced with
  its first lap, not at its start, so a stint that never completes a lap
  never reaches the server and the summary carries the circuit and its
  layout id, which are only known one lap in. Laps queue in
  `data/sync-sessions.json` while the service is away and flush in order
  when it answers or at the next start; a lap ruled in or out of the bests
  by hand is sent again with its new verdict; a session the service no
  longer has (deleted in its portal, or expired) is closed here and said
  so. **Live** holds one WebSocket to the service while the car is on track
  and sends where it is about four times a second (`GT7_SYNC_LIVE_HZ`, no
  faster than the server's ceiling): position, speed, gear, lap and lap
  time, downsampled from the 60 Hz feed and never queued. The socket opens
  on the first on-track packet and closes after five minutes without one;
  the Sync panel shows the spectate URL and how many are watching. A
  stream an administrator closes, or another logger on the account
  replaces, is held for a quarter of an hour rather than fought over. The
  Admin push now takes `?type=` and, blank, flushes every active type.
  `GT7_SYNC_SESSIONS`, `GT7_SYNC_LIVE`, `GT7_SYNC_LIVE_HZ`.

- **Discard a lap or the run while surveying.** (#98) A spin that laid
  border points across the gravel, or a boundary marked on the wrong side
  for half a lap, used to be in the bundle for good — the merge only adds,
  and the only way back was to out-vote the bad metres by re-driving them
  twice. The Survey view has **Discard lap**, which throws away what this
  run has surveyed in the current lap so far and carries on, and **Discard
  run…**, which throws away everything the run has gathered; neither stops
  the survey. Only this run's votes go: a metre another run or another
  installation had mapped keeps that evidence and loses this run's vote on
  it. Evidence the autosave already wrote is backed out of the bundle, the
  circuit's laps are re-judged as after any bundle write, and the run's
  JSONL records the discard so a log assigned later does not bring the lap
  back. New endpoint `POST /api/survey/discard`; the survey status carries
  `lap`, `lap_votes`, `run_votes` and `discards`.

### Fixed

- **ASM coming on no longer reads as traction control.** The `aids` column
  is a bitmask and was resampled onto the comparison's 5 m grid by linear
  interpolation like every other channel, so ASM switching on (`0 → 2`)
  passed through `1`, the TCS bit, and the Throttle panel shaded a sliver of
  TCS at every ASM onset; TCS together with the rev limiter (`9`) falling
  back to nothing could pass through the handbrake bit the same way. `aids`
  now takes the nearest sample's value, as `surface` already did. Stored
  laps are unaffected — the per-lap TCS and ASM percentages were always
  computed from the raw ticks.

- **Where a circuit crosses over itself, both levels are surveyed and
  judged.** (#96) A border record was identified by its plan cell alone, so
  Suzuka's bridge and the road beneath it were one metre of road: whichever
  level was surveyed first kept the cell, the other's geometry was thrown
  away, its votes were counted against the survivor, the compiled borders
  zigzagged between the two, and a lap over the bridge was judged against
  whichever level had won — flagged beyond the surveyed edge while on the
  road. Bundle format **v5**: one record per metre per side *per road
  level*, levels told apart by an elevation difference over 3 m. The figure
  comes from the collected bundles — 0.72 m is the widest elevation spread
  between same-side neighbours, 0.18 m between raw survey marks in one
  cell; banking is 5.5 m across the road at Daytona but is never compared
  within a cell. A record without elevation merges as it always did. The
  border ordering refuses a next cell the road could not have climbed to,
  the across-the-road pairing prefers its own level, each road quad carries
  its elevation envelope (compiled geometry v2) and the survey judge places
  a sample by its elevation: on the deck is on the deck, and a car on an
  unsurveyed road under a surveyed bridge reads unknown, never off. Laps now
  store `pos_y` beside `pos_x`/`pos_z`. **Laps recorded before this release
  carry no elevation** and are judged on plan alone, which at a crossover
  means against both levels at once — they get the benefit of the doubt
  there and cannot be re-judged any tighter. A v4 bundle upgrades untouched;
  a crossover it merged before v5 has already lost its second level, which
  only re-driving or a replace import (#93) brings back. `stacked_cells` in
  the bundle stats counts the metres where both levels are present. The
  data repo's copy of the format moves to v5 alongside.

### Changed

- **`TODO.md` is gone.** The feature list the project started from had been
  overtaken by the issue tracker: most of it shipped, and what had not is
  now filed as issues (#22, #101–#112). The "tiers" note in the
  derived-channels docs points there instead.

## [0.6.1] - 2026-09-17

### Fixed

- **Laps are re-judged when a track's survey changes.** (#91) A lap's
  verdict against the surveyed edges — `off_survey_count`, and the
  `clean_lap` it feeds — was decided once, when the lap was saved, against
  the bundle as it was then. Fixing a bad survey afterwards changed nothing:
  the only re-judge covered the live session, and only at the moment its
  circuit was identified. Every bundle write now queues a pass over every
  lap ever driven on the circuit, across sessions and across spellings of
  its name: a survey run's saves (two minutes after the last one, so a
  running survey's autosave never triggers a pass over the whole history),
  a log assigned, a shared bundle pulled, an import, a rename (the label
  left behind too), a delete — after which the verdicts go back to unknown
  rather than keeping a judgement from geometry that no longer exists.
  Identifying old sessions judges their laps too. The pass runs in the
  background; **Re-check laps** on a Tracks row forces it and says how many
  verdicts changed. New endpoint `POST /api/track-bundles/{slug}/rejudge`.
- **A survey-caused "not clean" flag can be cleared again.** (#92)
  `clean_lap` was folded one-way: an excursion past the surveyed edge set it
  false, and a re-judge that found none carried the stored value forward, so
  a lap a bad survey spoiled stayed spoiled whatever was corrected later. It
  is now derived from both counts every time — clean when GT7's surface
  flags saw nothing and nothing ran past the surveyed edge — by one function
  the live lap and the re-judge share. A lap the old flag left behind (count
  zero, clean false) is repaired by the next pass.

## [0.6.0] - 2026-09-16

### Added

- **A sync client for contributing surveys.** (#79) Pulling shared bundles
  has worked since #47, but sending a survey back was a clone-the-repo-and-
  open-a-PR job. **Admin → Sync** now takes a server address and a token
  (the address field also accepts the `gt7sync://…?token=…` connection
  string the service issues, split into the two), keeps the token on its
  own, masked in the UI, sent only as a `Bearer` header and never logged,
  asks the server what data types it accepts, and shows a toggle per type
  under a master switch, all off by default. The one type this release sends
  is **tracks**: every survey bundle whose official layout has been
  confirmed — corner labels included — uploaded once it has been left alone
  for ten minutes after a change. A running survey's once-a-minute autosave
  keeps resetting that clock, so a run goes up once, after it stops and the
  labelling that follows it; Sync now skips the wait. Nothing is re-sent
  when the evidence has not changed since the server last accepted it. Uploads run in the background with retry and exponential
  backoff; a refused document is remembered with its reason and not retried
  until the bundle changes; a server that stops accepting tracks flips the
  toggle off and says so. Each row of the Tracks view carries a chip — *not
  synced — confirm layout*, *queued*, *syncing*, *synced* with the upload id,
  *rejected*, *error*. The client is one transport plus one adapter per data
  type, so `sessions` and `live` reuse the plumbing when their server halves
  land. New settings `GT7_SYNC_URL`, `GT7_SYNC_TOKEN`, `GT7_SYNC_ENABLED`,
  `GT7_SYNC_TRACKS`; new endpoints `GET /api/admin/sync`, `POST
  /api/admin/sync/test`, `POST /api/admin/sync/push`; `/api/track-overview`
  rows gain a `sync` field.
- **An in-app guide to the Analysis view.** The view had grown to thirty chart
  channels and a dozen features, and none of them said what they were: a panel
  called "Tire spd / car spd" or a switch called "sync" was left to be
  guessed at. A **?** button in the toolbar now opens a short guide — a
  Features tab and a Channels tab, a sentence or two each on what it shows and
  how to read it, what a recording needs to have a channel, which channels are
  on the chart now, a search across both, and a link from every entry to its
  section of the documentation, which stays the long form. The channel picker
  shows the same description on hover and links to the list. The descriptions
  live beside each channel's definition and are required there, and a test
  fails if any documentation link stops landing on a real heading, so the guide
  can't quietly fall behind the app.
- **Laps can be excluded from bests by hand.** (#74) Only the partial-lap guard
  could keep a lap off the bests, and it only ever asks one question — did the
  lap cover the whole track? An off-track lap, a lap with contact, or an out-lap
  the guard accepted all went on owning the session best and the Bests board.
  The Sessions lap table now has a **Counts** checkbox per lap, with a reason
  picker (off-track, contact, restart, dirty, pit-out) beside an excluded one,
  and `PATCH /api/laps/{id}` behind it. The ruling works both ways — a lap the
  guard wrongly called partial can be counted — and it sits beside the guard's
  verdict rather than over it: the guard keeps re-judging the session as laps
  arrive and can never overwrite a ruling, and clearing one hands the lap back
  to whatever the guard said last. Every consumer of "does this lap count"
  already filtered on one flag, which is now computed from both in SQL, so the
  session best, the Bests board, the class benchmark, the session-summary
  webhook and the Race Engineer's pace and coaching all follow a ruling without
  a change of their own. On the session being driven, the live session best, the
  Δ-best reference and the engineer's coaching reference move on the spot. The
  Bests board says why a quicker time is missing (**⊘ 1:28.422 off-track**).
  Rulings travel with exported lap files, and so does the guard's verdict: an
  imported pit out-lap stays partial instead of becoming a best on the machine
  it lands on.
- **Time-synced ghosts on the race line.** (#75) Every dot on the Analysis map
  sat at the reference car's distance — right for reading the chart delta under
  the cursor, but it never showed where the other car physically was at the same
  moment, which is what makes a gap legible while a lap plays back. The map now
  syncs on **time** by default: the reference dot stays at the playhead and
  every other lap's dot is drawn where that lap had got to after the same
  elapsed time, interpolated between samples so it glides rather than hops. A
  slower lap trails, a quicker one leads, and the distance between them is the
  gap. **sync: Time | Position** in the map header switches back to dots level
  with the reference — which, now that compared laps are lined up by place (see
  Fixed), sit on top of it apart from the line each lap took. The choice is
  remembered per device, like Follow; the charts and the other cursor-synced
  panels stay on distance.
  Checked against the raw 60 Hz positions of 1,215 recorded laps (27.7 h), a
  time-synced dot sits a median 7 mm and a p99 25 cm from where the car
  actually was. Where GT7 reset a car mid-lap — a jump of up to 4 km in one
  tick, which the 5 m distance grid cannot represent — the dot holds and then
  jumps, at a moment estimated from the step's speeds, instead of sliding
  across the infield. Every map dot is now placed at the exact cursor rather
  than the nearest 5 m step (that was up to 2.5 m of false gap between the
  reference and a time-synced dot), which also stops the Follow camera
  hopping 5 m at a time during playback.
- **Export a whole session as a ZIP.** (#76) Backing up or handing over a
  session meant clicking **json** on every lap. **Export session** (and
  `GET /api/sessions/{id}/export.zip`) now packs every lap's export file and a
  `session.json` — the session row as the sessions list gives it: car, circuit,
  tags, note, bests exclusion and race result — into one archive. The lap files
  are the single-lap exports unchanged, so each one imports today. Laps are
  read, compressed and written one at a time, off the event loop, and the
  archive spools to disk past 16 MB, so a long endurance stint doesn't have to
  fit in a Raspberry Pi's memory. Importing the archive back as a session is
  the follow-up.

- **Cars describe themselves, and keep themselves current.** (#57) The bundled
  car data was 575 rows of `id,name` — the app could print a name and answer
  nothing else — and it only ever changed when somebody found the admin button,
  which downloaded a third-party mirror of the same list. Cars are now a real
  inventory, generated from Polyphony's own car list and shipped inside the
  package: manufacturer, model year, Gr. category, drivetrain, aspiration,
  displacement, power, torque, weight and dimensions, for 584 cars. It resolves
  from the package rather than the working directory, so a fresh install names
  every car it ships knowing about on its first packet, with no network and no
  setup step. On top of that the app refreshes itself — on first run and weekly
  after that, in the background, never blocking or failing a start — so cars
  added by a GT7 content update arrive on their own. Being offline is not an
  error: the bundled inventory is the floor, and a failed check simply keeps
  it. A refresh only ever adds and updates, so the ten cars GT7 no longer
  publishes keep their names for the sessions that reference them. Sessions
  carry the manufacturer, year, drivetrain and aspiration on the row beside the
  car name — the full published name, model year, category, drivetrain,
  aspiration, displacement, power, torque, weight, dimensions and PP — and
  sessions recorded before this, or before a car was known, are filled in as
  soon as the inventory can answer for them. Laps deliberately store no copy:
  every lap query already joins its session, so lap listings, the Bests board
  and lap detail read the car across a join they were making anyway, and
  `GET /api/laps` gained `manufacturer=` and `drivetrain=` filters on the back
  of it.
- **Circuits name themselves on a fresh install.** (#58) Track identification
  needed data only you could produce — a signature exists once somebody names a
  circuit, a survey bundle once somebody maps one — so a new install recognised
  nothing, and every session stayed unnamed with no hint that naming one circuit
  would light up the track badge, the outline, category bests and corner labels.
  The app now ships signatures for 78 GT7 configurations — nine from circuits we
  have surveyed ourselves, the rest from published circuit captures — generated
  offline and vendored so the first packet resolves with no network. They live in the same `tracks` table as your own and are marked as
  shipped, which is what keeps the two apart: **a circuit you named always wins**,
  and a re-sync replaces every shipped row and none of yours. The Tracks view
  labels them, and keeps undriven ones out of the table so 78 rows restating the
  catalog cannot bury the rows that mean something.
- **Reverse layouts are told apart from their forward twin.** (#58) A reverse
  layout has exactly the same bounding box and length as the layout it reverses,
  so a shipped signature would have named reverse laps after the forward
  configuration — and because bests key on the circuit name, forward and reverse
  times would have pooled and competed for the same personal best. Each shipped
  signature now carries the racing line in driving order, and a lap is walked
  against it to see which way round it went. Not a clockwise test: signed area
  does not survive a crossover, and Suzuka is a figure-eight. Measured over 896
  recorded laps, this moved 10 laps of Deep Forest Reverse off Deep Forest
  Raceway, where they had been sitting alongside 6 genuine forward laps.
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

- **Compared laps are lined up by where they were, not how far they had
  gone.** A lap's distance is integrated from its own speed, so two laps reach
  the same metre mark at different places — a wider line is a longer lap, and
  a slide or a run of dropped frames moves the car further than its speed says.
  Every Analysis comparison was made at equal distance: measured over 141 real
  lap pairs, "the same distance" was a median 2.9 m and a p99 64 m apart on
  track, so the time diff compared the laps at different points of a corner,
  and the race line's position-synced dots sat apart when the cars had been side
  by side. Every lap is now walked along the reference lap's driven path and
  put on its distance axis by place — searched locally so a circuit that
  crosses itself can't capture it on the wrong branch, re-found when GT7 resets
  the car, and never allowed to run backwards. The same 141 pairs now agree to a
  median 0.00 m and a p99 0.15 m, and the time diff, charts, event bands, corner
  report and consistency chart all compare the same places. A lap that can't be
  followed keeps its own distance and says so (`aligned: false`). The live Δ
  widget had the same fault while driving — replayed through it, real laps read a
  median 100 ms and a p99 2.8 s away from the place-aligned delta — and now
  tracks the lap in progress the same way, to within a centimetre of the full
  alignment. Time sync on the map reads a new per-lap clock track instead of the
  distance series, so a spin or a rewind can't fold its ghost onto one point.
- **Lap 1 of a race no longer counts as a lap time.** GT7 steps the lap counter
  when the race starts, wherever the grid is — 120 m past the line at Red Bull
  Ring, 125 m at Spa, 68 m to the side at Daytona's road course — so lap 1 was
  timed grid-to-line. At under 3 % of the lap the span guard couldn't see it,
  and it could be the fastest lap of the session (session 224's was). A lap now
  counts only if it began at the line, judged against the first sample of the
  lap after it: along the track to within the frames dropped at each boundary
  plus 3 m, across it to within 25 m. Stored laps are checked once, in the
  background, on the first start with this release; on the author's history
  that marked 30 grid starts and three laps from sessions where GT7 moved the
  car kilometres at the line, and left every ruling a user had made alone.
- **A lap's clock and distance start from the same instant.** A lap's first
  sample lands somewhere inside the gap since the previous one, but the clock
  started at 0 while the distance started a whole gap in — so a lap whose line
  crossing fell across dropped frames ran its clock up to ~0.07 s late against
  its own distance (about 5 m at 290 km/h in a time comparison, and a constant
  offset in the live delta). Both now start half the gap in. Laps recorded
  before are recognisable and converted when read, so nothing stored is
  rewritten; imported files are converted the same way.
- **Every chart, playback and map dot now reaches the line.** Series were
  resampled on whole 5 m steps and stopped up to 5 m (a median 2.4 m) short of
  the lap's end. They now end on the lap's exact length; Corner Detail and the
  traction circle read the nearest point rather than index × step, which the
  shorter last step would have thrown off.
- **The consistency chart ignored whether a lap counted.** It took the
  session's quickest laps by time, so a pit out-lap's short time ranked first.
  It now takes the quickest counting laps, lined up like a comparison.
- **Imported laps kept losing their salvage marker.** The importer validates a
  file into a fixed model before storing it, and `salvaged` was never part of
  that model, so a replay-salvaged lap arrived as an ordinary lap however the
  file described it — the opposite of what the import code's own comment
  promised. It is carried now, along with the lap-count verdicts from #74.
- **Analysis no longer defaults to a lap that doesn't count as the reference.**
  "Latest vs best" picked the quickest lap of the session outright, so a pit
  out-lap's short reported time — or, now, a lap excluded by hand — could
  become the yardstick everything else was measured against. It picks the
  quickest *counting* lap, and so does the Sessions lap table's **compare**.
- **The lap table's Δ best could read "+-43.000".** A partial lap can be
  quicker than the session best, and the gap was printed with a hard-coded
  plus sign. It is signed properly now, and dimmed for laps that don't count.
- **Identify sessions now uses the shipped signatures.** (#58) It only ever
  matched against survey bundles, which was right when a signature existed
  only because somebody had typed a name — there was nothing to backfill from.
  With 78 signatures shipping, history sat unnamed at circuits the app
  recognises on sight: 29 of 44 unnamed sessions on the author's install. It
  now tries signatures first and bundles second, the same order as a live lap,
  and passes the lap's positions so a reverse lap in your history is named
  after the reverse configuration rather than its forward twin.
- **Identification refuses to guess between two circuits that look alike.** (#58)
  Signature matching returned the first row that fitted, which was only ever
  correct because the table could not hold two rows that both matched — you name
  the circuit you are driving, once. It can now, and a bounding box cannot
  separate Lago Maggiore Full Course from Suzuka, nor Road Atlanta from Watkins
  Glen; 24 of 146 real laps matched two. An ambiguous match now produces no name
  rather than a silent wrong one, the discipline survey-bundle matching already
  had. This also fixes the same latent bug for anyone who had named two circuits
  with similar geometry.
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

- **One definition of "update cars", and one of "read GT7's data".** (#57)
  `scripts/update_cars.py` and `POST /api/admin/update-cars` each had their own
  copy of the same download-and-map against the same hardcoded third-party URL;
  both now call the same refresh as the background check, so the three cannot
  drift apart, and none of them depends on that mirror any more. The car and
  track scrapers likewise share the module that walks gran-turismo.com's
  hash-stamped JS bundles instead of each carrying their own. The admin button
  is no longer a step you have to know about after installing — it is the "do
  it now" for a refresh that otherwise happens on its own.
- **Shipped data actually ships.** (#57) `PACKAGE_DATA` pointed one directory
  above the package, so nothing under it was included in a wheel — the track
  signatures added in #58 among them — and the defaults only resolved when the
  process was started from `backend/`. The read-only data now lives in
  `app/data/` and is declared as package data, which is what lets the Docker
  image drop its `GT7_CARS_CSV` override and the track-catalog endpoint drop
  its repo-root fallback. `GT7_CARS_CSV` still works and still wins if you set
  it, reading the old CSV shape for one release; `GT7_CARS_JSON` replaces it.
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
