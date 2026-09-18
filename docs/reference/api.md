# REST & WebSocket API

Everything the dashboard does goes through this API, so anything the UI can do, a
script can too. All REST routes live under `/api`; responses are JSON.

## Status & health

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | `{"status": "ok"}` |
| GET | `/api/status` | source kind, connection state, packet/error counters, recording flag, current session id, track name. Also the lap-clock cross-check (diagnostic): `lap_clock_drift_ms` (current lap, signed), `lap_clock_drift_worst_ms` (session worst) and `lap_clock_samples` — how far the integrated time axis has drifted from GT7's own packet-C lap clock, 0/absent below packet C |

## Sessions & laps

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/sessions` | all sessions, newest first, with lap count, best lap, the user-set `note` and `tags` (see PATCH below), and the `bests_excluded` flag. Race sessions carry their result: `final_position` / `final_total_positions` (−1 = no result — a time trial, or a stream that ended before the flag; deliberately distinct from finishing last), `race_laps` (0 = not a lapped race) and `race_time_ms` (sum of the stored lap times, `null` unless every lap 1..`race_laps` is present). `?category=Gr.3` filters by car class; blank returns everything, which is also the only way to reach recordings made before packet C. A session whose own category is empty (its first packet was a narrower format) inherits its laps' category. Every row also carries its car's own record, denormalised from the [car inventory](../getting-started/configuration.md#the-car-database) (#57): `car_manufacturer`, `car_year`, `car_drivetrain`, `car_aspiration`, `car_full_name`, `car_displacement_cc`, `car_power_bhp`, `car_torque_kgfm`, `car_weight_kg`, `car_length_mm` / `car_width_mm` / `car_height_mm` and `car_performance_points` — empty or 0 where GT7 publishes nothing, which is normal (an EV has no displacement, a race car no model year) |
| PATCH | `/api/sessions/{id}` | *(admin)* `{note?, tags?, bests_excluded?}` → `{"status": "updated"}`; 404 for a session that doesn't exist, **422 for any unknown field** and **400 for an empty patch** — a typo'd `bests_exclude` must fail loudly, not answer "updated" while the replay keeps owning the board. `note` is free text (≤ 500 chars); `tags` is a list of labels (≤ 20 of ≤ 40 chars, no commas, deduped case-insensitively — an **empty list clears them**, absent leaves them alone). `bests_excluded` pulls the session's laps out of the [Bests board](../guide/bests-view.md) (`/api/laps/bests`) and the class benchmark (`/api/laps/best`) while keeping them selectable for comparison — the fix for [replays](../internals/lap-detection.md#replay-salvage), which record other drivers' laps that telemetry cannot tell from yours |
| DELETE | `/api/sessions/{id}` | delete a session and its laps |
| GET | `/api/sessions/{id}/laps` | lap summaries for one session |
| GET | `/api/sessions/{id}/export.zip` | the whole session as one ZIP (#76): a `gt7-session-{id}/` folder holding `session.json` and `laps/lap-{number}-{id}.json` per lap. The lap files are byte-for-byte what `/api/laps/{id}/export` serves, so each imports on its own today; `session.json` carries the session row exactly as `/api/sessions` lists it (car, circuit, tags, note, bests exclusion, race result) and an index of the lap files. 404 for a session that doesn't exist. See [Lap file format](lap-file-format.md#session-archive-zip) |
| GET | `/api/laps` | all lap summaries, newest first. `?track=` filters by the session's circuit, `?category=` by the lap's car class, and `?manufacturer=` / `?drivetrain=` by the car — the last two read the session's denormalised columns through the join the query already makes, which is why laps store no copy of them (#57). Summaries carry `track_name`, the car's `car_manufacturer` / `car_year` / `car_drivetrain` / `car_aspiration`, `race_position` (position when the lap completed; −1 = no position reporting), a `salvaged` flag ([replay-salvaged](../internals/lap-detection.md#replay-salvage) laps stay traceable everywhere their time shows up), and whether the lap counts toward bests: `counts_for_best` is the verdict every aggregate uses, `full_lap` the [span guard's](../internals/lap-detection.md#best-lap-tracking) half of it, and `best_override` / `exclude_reason` the user's ruling on top (see PATCH below; `null` / `''` when none was made). The list never reads the 60 Hz sample blobs, so querying the whole archive — which is what the Bests board and the Analysis *Add lap…* picker do — stays cheap however large it grows |
| GET | `/api/laps/best` | `track`, `category` → the fastest **counting** lap ever recorded at that circuit in that class, or `null`. Partial pit out-laps and laps excluded by hand are left out, for the same reason they never own a session best — as are sessions flagged `bests_excluded` |
| GET | `/api/laps/bests` | → `{"bests": [...]}` — the personal-bests board: one row per circuit + car, the fastest *counting* lap that car ever set there, ordered by circuit then time. Each row: `track_name`, `car_id` / `car_name` / `car_category` (`''` when unknown), `lap_id`, `session_id`, `number`, `time_ms`, `finished_at`, `clean_lap` (`null` = unknown), `off_survey_count` (`-1` = unknown), `salvaged`, `lap_count` — how many counting laps stand behind the row — and `excluded_faster`: every quicker lap of that circuit and car that was excluded by hand, fastest first, as `{lap_id, time_ms, reason}`, so the board can say why a remembered time is missing. `?category=` keeps only rows of that class — it filters the finished board rows, never re-ranking within a class, so a filtered row is always a row the unfiltered board shows too. Excluded, so a row is always a claim worth believing: laps that don't count (`counts_for_best` false — partial, or excluded by hand), sessions with no circuit name, and sessions flagged `bests_excluded`. A circuit and car whose every lap is excluded has no row |
| GET | `/api/laps/{id}?samples=true` | full lap detail: metrics, events, gearing, and (optionally) the 60 Hz samples |
| PATCH | `/api/laps/{id}` | *(admin)* rule a lap in or out of the bests (#74) → the updated lap summary. `{"best_override": false, "exclude_reason": "contact"}` excludes it from every best — session best, Bests board, class benchmark, the Race Engineer's pace and coaching; `true` counts a lap the span guard called partial; `null` hands it back to the span guard, which keeps judging it underneath either way. `exclude_reason` is one of `off-track`, `contact`, `restart`, `dirty`, `pit-out` (422 otherwise) and is kept only on an exclusion — sending it beside `true`/`null` is a 400, and sending it alone re-words an existing exclusion (400 if the lap isn't excluded). 404 for a lap that doesn't exist, 422 for any unknown field, 400 for an empty patch. A lap of the session being driven also moves the live session best, the delta reference and the engineer's reference on the spot |
| DELETE | `/api/laps/{id}` | delete a lap |
| GET | `/api/laps/{id}/export` | JSON export envelope (see [Lap file format](lap-file-format.md)) |
| GET | `/api/laps/{id}/export.csv` | MoTeC-compatible CSV (one row per tick, up to 34 channels with units — the optional channels appear only when the recording carried them) |
| POST | `/api/laps/import` | import an exported lap file. The lap keeps the verdicts it was exported with — `salvaged`, the span guard's `full_lap` (`counts_for_best` in files written before #74) and any `best_override` / `exclude_reason` — so a partial out-lap or an excluded lap does not become a best by changing machines |

## Tracks

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/tracks` | stored track signatures |
| POST | `/api/tracks` | `{name, lap_id}` — name a circuit from a lap's geometry |
| DELETE | `/api/tracks/{id}` | remove a signature (session data untouched) |
| POST | `/api/tracks/identify` | name every unlabelled session that was driven on a [surveyed circuit](../internals/track-identification.md#matching-against-a-survey-bundle) → `{checked, identified, tracks}`. New sessions do this for themselves; this is for history recorded before the bundles existed. Sessions with no confident match are left alone. The laps of every session named are then judged against the survey in the background — they were saved with no circuit to judge against. 409 when nothing has been surveyed |

## Overlay / dashboard layouts

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/layouts` | all saved layouts with their grid configs |
| GET | `/api/layouts/{ref}` | one layout by numeric id or name (id wins if both match) |
| POST | `/api/layouts` | `{name, kind: "overlay"\|"dash", config}` — 409 on duplicate name |
| PUT | `/api/layouts/{id}` | `{name?, config?}` — rename and/or replace the config |
| DELETE | `/api/layouts/{id}` | delete the layout |

Configs are v2 grid layouts (`{version: 2, grid, cells, …}`) as produced by the Admin
builder; the server only checks the version and a 64 KB size cap.

## Analysis

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/analysis/compare` | `laps` (CSV ids), `ref` (id), `step` (m, default 5, 0.5–50), `channels` (optional CSV) → per-lap distance-resampled series, speed peaks/valleys, events, and delta-vs-reference. Every lap is on the **reference lap's distance axis**: the others are lined up with it by where they were on track ([how](../internals/analysis-math.md#lining-laps-up-by-place-on-track)), so their `series.dist`, `delta`, `events` distances, `peaks_valleys` and `corner_report` all mean "metres along the reference lap"; `aligned: false` marks a lap that couldn't be followed along the reference's path and kept its own distance. Series end on the lap's exact length, so the last step is usually shorter than `step`. Each lap also has `track` — `{t, pos_x, pos_z}` every 50 ms of its own clock, independent of alignment, for placing the car by time. The reference lap also carries `corners`: the circuit's [authored corners](../guide/tracks-view.md#labelling-corners) when it has them (`authored: true`, and a `name` when given), otherwise detected from that lap's curvature. When corners exist, **every** lap gets `corner_report`: entry/min/exit speed and time-through per corner, all measured through the reference's corner windows so the times are comparable — the per-corner report card is `lap.time_ms − ref.time_ms` sorted descending. Top-level `accel` is the broadcast accelerometer's [unit + sign calibration](../internals/derived-channels.md#accelerometer-units-are-calibrated-not-assumed), fitted on the reference lap and applied to all of them; each lap with the channels also gets `gg`, its peak g in each direction taken from the raw ticks |
| GET | `/api/analysis/deviation` | `session_id`, `count` (2–20, default 5) → median speed + standard deviation by distance across the best N **counting** laps, lined up on the fastest one's path |
| GET | `/api/analysis/coaching` | `session_id` → the race engineer's post-lap coaching notes, grouped per lap: `{lap_id, number, findings: [{type, text, corner}]}`. **Replayed** from the stored laps through the live CoachingDetector — same thresholds, same reference-so-far, same wording — rather than recorded from speech, so notes exist for sessions driven with voice off. Each distinct observation appears once |
| GET | `/api/analysis/fuel` | `lap_id` → relative fuel-map table for settings −5…+5 |

Default compare channels: `t, speed, throttle, brake, coast, gear, rpm, boost,
tire_slip, yaw_rate, pos_x, pos_z`. Any other stored column can be requested via
`channels=`; `t`, `pos_x`, `pos_z` are always included (the delta and map need them).
Delta values are milliseconds, **positive = slower than the reference**.

## Controls

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/control/recording` | `{"recording": bool}` — pause/resume lap recording |
| POST | `/api/control/log-lap-now` | save the in-progress lap immediately (409 if none) |

## Surface survey

Runs the [surface survey](../internals/surface-survey.md) capture in the 60 Hz
packet path (packet format C required for surface data).

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/survey/start` | `{track_width_m?, track?}` — start a run; resets counters, opens a new JSONL log; 409 if a run is already active. `track` labels which circuit the samples describe (default: the session's identified track; picked up mid-run if identification happens later, and a user-typed label is never auto-overwritten) |
| POST | `/api/survey/stop` | stop and close the log |
| POST | `/api/survey/track` | `{track}` — name the circuit a **running** survey is describing; 409 if none is running, 422 if the name is blank. A run started before the track was known keeps everything it has gathered and merges it into that circuit's bundle (a survey with no label saves no bundle at all). The label is marked as the driver's own, so auto-identification will not override it. Re-assigning an already-labelled run is a *circuit change*, not a correction: its evidence is flushed to the previous circuit first — to fix a wrong label, rebuild from the JSONL with `backend/scripts/jsonl_to_bundle.py` |
| GET | `/api/survey/status` | track/session the run is tied to, per-wheel char histogram, unknown chars, recent transitions, log path, undocumented flag-bit activity, `lap`/`lap_votes`/`run_votes`/`discards` (what a discard would take away, and what has gone), the finish line located from lap rollovers (`finish`: mean crossing point/heading, crossing count, spread, confidence), and the track width in use — the entered assumption until enough out-and-back edge rides have measured the real axle width (`width_estimate_m`, `width_samples`) |
| GET | `/api/survey/trail` | breadcrumb of the path driven (`since`/`epoch` for incremental fetch; the epoch bumps when the trail is decimated) |
| GET | `/api/survey/edges` | every border-edge point of the run — the track taking shape (`since`/`epoch` incremental; append-only within a run). Kinds: `auto` (surface-flip contacts), `straddle` (sampled continuously while one side's wheels are held off the tarmac), `edge`/`runoff`/`wall` (manual marking) |
| POST | `/api/survey/mark` | `{side: "L"\|"R"\|null, kind: "edge"\|"runoff"\|"wall"}` — arm manual boundary marking: while armed, the survey samples edge points from that side's wheel line every ~2 m, which is how boundaries invisible to the surface chars (walls, and track edges with paved run-off beyond) get mapped |
| POST | `/api/survey/discard` | *(admin)* `{scope: "lap"\|"run"}` — throw away border evidence the running survey gathered: the current lap's so far, or the whole run's (#98). The run keeps going. Only this run's votes go — a metre another run or another installation evidenced keeps that evidence and loses this run's vote on it — and what the autosave already wrote is backed out of the bundle, which re-judges the circuit's laps as any bundle write does. Answers with the status plus `discarded: {scope, lap, records, votes}`; 409 with no run |
| GET | `/api/survey/packet` | the latest raw telemetry packet, fully decoded — the Survey view's field inspector polls this |
| GET | `/api/survey/logs` | every survey run's JSONL, summarised (track, marks, transitions, finish crossings, size) and flagged `orphaned` when it gathered evidence and never reached a circuit — a run that saved no bundle at all and exists only as this file |
| POST | `/api/survey/logs/{name}/assign` | `{track}` — rebuild an orphaned run from its log and merge it into that circuit through the normal voting path, exactly as if it had been named while driving. The run is credited to the installation that **recorded** the log (its id is in the log's meta header), not the one replaying it. 409 while that run is still going, and 409 if it has already been assigned to that circuit — re-assigning to a *different* one is the mis-label correction. 404 for a name that is not a log in the data directory |
| GET | `/api/survey/logs/{name}/download` | the raw JSONL file itself — how a run recorded on one machine reaches another (upload it there, then assign). 404 for a name that is not a log |
| POST | `/api/survey/logs/upload` | *(admin)* accept a survey JSONL from another installation. The body is validated line by line against the log format (header shape, mark and transition records) before anything is kept — a bundle document or a truncated file is refused with the offending line number. Names are sanitised to the `surface_survey_*.jsonl` scheme and an existing file is never overwritten (a `_2` suffix is claimed atomically instead). 413 over 64 MB |
| GET | `/api/survey/export.jsonl` | full log of the current/last run (404 if none). First line is a `{"meta": ...}` header (track, session id, track-width assumption, wheel order); transition records carry the session id and lap, so they join back to the laps recorded during the same drive. Interleaved lines: `{"mark": ...}` for manually-marked boundary points, `{"track": ...}` when the circuit is identified mid-run, and `{"discard": {scope, lap, since_pid, pid, records, votes}}` when the driver voided a lap or the run — a replay of the log (assigning it later) skips every mark and transition in that packet range, exactly as the live run dropped them |

## Track bundles & management

The joined view of the three sources of track knowledge, and the export /
import path for [track bundles](track-bundle-format.md). See the
[Tracks view](../guide/tracks-view.md).

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/track-catalog` | official GT7 track/layout metadata (bundled `data/tracks.json`: 41 tracks, 85 layouts, lengths, corner counts, reverse configs) |
| GET | `/api/track-overview` | one row per circuit, merged across the DB's named tracks, the survey bundles and the official catalog: `named` (auto-identification will work), bundle stats (points, runs, sources, elevation %, stacked cells where the road crosses over itself, finish crossings, corners), the compiled `coverage` score (per-side boundary % + `closed` + `road_pct`) and `compiled_at` when the bundle compiles, session count, the confirmed `official` match and — only when there is none — a `suggestion` with its confidence and reasoning. Also lists the survey logs and this installation's source id. `provenance` says whether a circuit's name came from you (`user`) or shipped with the app (`seed`); `seeded_signatures` counts the shipped signatures **not** listed, i.e. circuits waiting to be driven. With track sync on, each row's `sync` says where its bundle stands with the sync service (`{status: queued|uploading|synced|rejected|error|unconfirmed, remote_status?, upload_id?, uploaded_at?, error?, attempts?, due_in_s?}`; null when sync is off. `due_in_s` is the rest of the ten-minute settle window after a change, or of the backoff after a failure) and `sync_tracks` carries the type's `{active, state, error}` |
| GET | `/api/track-outline` | `lap_id` (resolves the circuit from the lap's session) or `track` → the surveyed road, from the [compiled geometry](track-bundle-format.md#compiled-geometry-derived-not-part-of-the-bundle): `road` (a contiguous quad strip `[x1,z1,…,x4,z4]` between the ordered border curves), `edges` and `walls` (segments `[x1,z1,x2,z2]`), `finish`, `gaps` (unsurveyed spans the ordering had to bridge — drawn dashed, never as road) and `coverage` (per-side boundary coverage + `road_pct`). Answers with an **empty** outline rather than 404 when the circuit has no bundle — never having been surveyed is the common case, not an error. Compiled off the event loop and recompiled per bundle revision: a bundle is up to 50,000 records and the browser must not download it |
| GET | `/api/track-bundles` | every circuit's bundle, with the same stats and coverage |
| GET | `/api/track-bundles/shared` | what the configured shared repo (`GT7_SHARED_BUNDLES_URL`) has on offer: `{configured, url, bundles: [{track, slug, url, points?, runs?, corners?, updated_at?, official_name?}]}`. `configured: false` when no repo is set (the UI hides the feature); 502 when the repo is set but unreachable or serves a malformed index. Counts are the index's advisory numbers — the truth is whatever a pull validates |
| POST | `/api/track-bundles/shared/{slug}/pull` | fetch that bundle from the shared repo and merge it through exactly the import path below (same validation, same voting merge, same `corners_kept` protection). The server re-reads the index and follows only the URL it maps the slug to — the client never supplies one. `?track=` overrides the label, same as import. Repo-side failures — unreachable host, malformed index, a bundle that fails validation — are all 502; 4xx is reserved for the caller's own inputs (404 unknown slug, 400 blank override) |
| GET | `/api/track-bundles/{slug}` | one bundle document — the export unit, and what import consumes |
| POST | `/api/track-bundles/import` | merge a bundle document from elsewhere. `?track=` overrides the document's own label, which is how a near-miss name lands on the right circuit. Every field is validated and rebuilt before anything is merged; versions 1–5 are accepted and upgraded. Your own authored corners and confirmed layout match are never overwritten (`corners_kept` says when incoming ones were dropped). 400 on any malformed document; 413 over 64 MB, enforced while reading rather than after buffering |
| PATCH | `/api/track-bundles/{slug}` | `{track?}` renames — **merging** when the new name is an existing bundle, which is the fix for one circuit living under two spellings. `{official, set_official: true}` records the confirmed official layout (`official: null` clears it). 409 while a survey is running on that circuit: it holds the old name in memory, so its next save would recreate the bundle just moved |
| DELETE | `/api/track-bundles/{slug}` | remove a bundle (the survey JSONL logs are untouched). Laps judged against it go back to unknown (see below). 409 while a survey is running on that circuit |
| POST | `/api/track-bundles/{slug}/rejudge` | *(admin)* re-judge every lap driven on the circuit against its survey as it now is → `{slug, labels, laps, changed, judged}`: the session labels covered (every spelling that slugifies to this circuit), how many laps were read, how many verdicts moved, and whether there was a usable survey to judge by — with none, verdicts go back to unknown (`off_survey_count: -1`) and `clean_lap` falls back to the surface flags. Waits for the pass, which reads every lap's telemetry. Works without a bundle, so the circuit is the slug rather than a bundle that must exist; an unknown one answers with zero laps, not 404 |
| GET | `/api/track-bundles/{slug}/corners` | the circuit's authored corners and sections |
| PUT | `/api/track-bundles/{slug}/corners` | `{corners?, sections?}` — replace them; omitted lists are left alone. Renumbered from list order. 404 when the circuit has no bundle: corners are anchored to positions on a surveyed map |

Every write that can move the surveyed road — a survey's save, a log assigned,
a shared bundle pulled, an import, a rename (both the old label and the new),
a delete — queues the same re-judge for the circuit in the background (#91).
An explicit edit runs it at once; a survey's writes wait until the bundle has
been left alone for two minutes, so a running survey's once-a-minute autosave
never triggers a pass over the whole history. Corner edits do not queue one:
the road did not move. `clean_lap` is re-derived from both counts each time,
so a lap flagged by a survey that was later corrected reads clean again (#92);
one flagged by GT7's own surface flags stays dirty whatever the survey says.
Where the circuit crosses over itself, a lap's elevation trace (`pos_y`)
places it on the deck or on the road beneath (#96); a lap recorded before
that column existed is judged on plan alone there, against both levels at
once, and gets the benefit of the doubt.

## Admin

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/admin/settings` | current runtime settings |
| PUT | `/api/admin/settings` | `{ps_ip?, source?, log_level?, webhook_url?, webhook_events?, packet_format?, race_engineer?, race_engineer_verbosity?, race_engineer_categories?, race_engineer_units?, sync_url?, sync_token?, sync_enabled?, sync_tracks?, sync_sessions?, sync_live?}` — applied live, persisted to the DB. `sync_url` is the server address as typed — a bare host is read as `https://`, `http://` and `gt7sync+http://` are kept as http, empty restores the default server — or a whole `gt7sync://host/?token=…` connection string, which sets the token too and is never stored or echoed as-is; `sync_token` sets the account token on its own (one printable line; empty forgets it). GET answers with `sync_url`, `sync_token_set` and a `sync_token_hint` (`…a1b2`), never the token. 400 for a malformed address, token or string. Changing the server drops the capabilities and the record of what it accepted; the token is left alone |
| GET | `/api/admin/sync` | where the [sync client](../guide/admin.md#sync) stands: `{configured, url, token_hint, enabled, capabilities, capabilities_error, checked_at, types: {<type>: {description, enabled, offered, supported, active, state, error, last_ok_at, last_attempt_at, uploads, queued, due_in_s?, …}}}`. `offered` is null until the server has been asked; `state` is `off`, `idle`, `syncing`, `connected` (live: the socket is up), `error` or `unsupported` (the server offers a type this build cannot send). Per type: `tracks` carries `tracks: {<status>: count}`; `sessions` carries `sessions: {synced, pending, closed, laps_rejected, current: {local_id, remote_id, laps_synced, laps_queued, closed} \| null}`; `live` carries `live: {connected, streaming, hz, spectators, frames, user_id, spectate_url, started_at, recording, last_frame_at}` |
| POST | `/api/admin/sync/test` | ask the server what it accepts (`GET /v1/capabilities`, no token sent) and answer with the status document above; 502 with the reason when it cannot be reached or refuses |
| POST | `/api/admin/sync/push?type=` | send what is waiting now, clearing any retry backoff: every eligible bundle without waiting for it to settle, the queued laps and totals, the live stream's next connection attempt. `type` names one data type; blank means every active one. Bundles and laps the server already holds unchanged are still skipped. 400 unless the named type (or, blank, any type) is active |
| POST | `/api/admin/test-webhook` | send a test notification |
| GET | `/api/admin/race-engineer` | Race Engineer diagnostics: counters, detector state, connected voice clients, last callout |
| POST | `/api/admin/race-engineer/test` | `{text?, event_type?}` — inject a callout into every connected browser |
| GET | `/api/admin/logs` | `limit` (≤2000), `level` — recent log records from the ring buffer |
| DELETE | `/api/admin/logs` | clear the ring buffer |
| GET | `/api/admin/stats` | uptime, DB stats, source stats, client count, LAN IP |
| POST | `/api/admin/restart-source` | stop/start the telemetry source |
| POST | `/api/admin/clear-data` | delete **all** sessions and laps (settings/tracks kept) |
| POST | `/api/admin/vacuum` | SQLite `VACUUM` |
| POST | `/api/admin/update-cars` | download the community car list and reload the lookup |

## WebSocket — `/ws/live`

One endpoint. Traffic is overwhelmingly server-push; the only client → server
messages are the Race Engineer voice protocol (below), and anything else a client
sends is ignored. Every message is `{"type": ..., "data": ...}`.

Server → browser:

- **`telemetry`** — the live frame, throttled to `GT7_WS_RATE` (default 30 Hz):
  speed, RPM + redline, gear + suggested gear, throttle/brake %, boost, fuel level and
  capacity, lap counters, best/last lap, session best and previous best, race position,
  tire temps (FL/FR/RL/RR), tire slip, water/oil temps, oil pressure, driver-aids
  bitmask (TCS=1, ASM=2, handbrake=4, rev limiter=8), packed per-wheel surface
  codes (4 bits per wheel, FL lowest; 0 = no data), car id/name, world position,
  in-game time of day, track name, on-track/paused flags.
- **`lap`** — sent when a lap is saved: id, session, number, time, per-lap metrics, and
  event counts. The UI uses this to refresh lists live.
- **`session`** — sent on new session, track identification, or track naming.
- **`survey`** — one per-wheel surface transition while a survey is running:
  from/to chars, changed wheels, position, velocity, heading, raw rotation
  floats, and derived wheel-contact points (sent at full 60 Hz resolution —
  these are single-tick events the throttled telemetry frames would miss).
- **`status`** — sent on connect and whenever the source or console IP changes.
- **`voice_callout`** — a Race Engineer callout: `id`, `event_type`, `text`,
  `category`, `priority` (0–100), `created_at_ms`, `expires_at_ms`, `ttl_ms`,
  `interrupt`, `dedupe_key`, `message_key`/`message_args` (localization) and
  `metadata`. Sent to every client; only the active speaker should voice it.
- **`voice_output_status`** — `{active_client_id}`: which browser may speak.
- **`race_engineer_status`** — feature enabled/active, verbosity, emitted categories,
  and the connected voice-capable clients.

Browser → server (Race Engineer only; never token-gated, and unparseable messages are
ignored so older pages keep working):

| Message | Data |
| --- | --- |
| `client_capabilities` | `client_id`, `page`, `voice_supported`, `voice_enabled` |
| `claim_voice_output` | `client_id` |
| `release_voice_output` | `client_id` |
| `voice_callout_ack` | `callout_id`, `client_id`, `status`, `spoken_at_ms`, `reason?` |

Ack statuses: `spoken`, `expired`, `duplicate`, `disabled`, `category_disabled`,
`not_active_speaker`, `interrupted` (stopped on purpose — a critical callout, a
disconnect, or the user pressing Test voice) and `speech_error`, which carries the
engine's own `reason`. Acks are diagnostics only and never gate the pipeline.

On connect the client immediately receives a `status` and a `race_engineer_status`
message — never past callouts, which are live events. The frontend auto-reconnects
every 2 s if the socket closes.

!!! note "CORS"
    CORS is wide open (`*`) — the API is designed for a trusted home network, not
    public exposure. Don't port-forward it to the internet.
