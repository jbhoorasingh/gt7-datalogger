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
| GET | `/api/sessions` | all sessions, newest first, with lap count and best lap. `?category=Gr.3` filters by car class; blank returns everything, which is also the only way to reach recordings made before packet C. A session whose own category is empty (its first packet was a narrower format) inherits its laps' category |
| DELETE | `/api/sessions/{id}` | delete a session and its laps |
| GET | `/api/sessions/{id}/laps` | lap summaries for one session |
| GET | `/api/laps` | all lap summaries, newest first |
| GET | `/api/laps/best` | `track`, `category` → the fastest **full** lap ever recorded at that circuit in that class, or `null`. Partial pit out-laps are excluded, for the same reason they never own a session best |
| GET | `/api/laps/{id}?samples=true` | full lap detail: metrics, events, gearing, and (optionally) the 60 Hz samples |
| DELETE | `/api/laps/{id}` | delete a lap |
| GET | `/api/laps/{id}/export` | JSON export envelope (see [Lap file format](lap-file-format.md)) |
| GET | `/api/laps/{id}/export.csv` | MoTeC-compatible CSV (one row per tick, up to 34 channels with units — the optional channels appear only when the recording carried them) |
| POST | `/api/laps/import` | import an exported lap file |

## Tracks

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/tracks` | stored track signatures |
| POST | `/api/tracks` | `{name, lap_id}` — name a circuit from a lap's geometry |
| DELETE | `/api/tracks/{id}` | remove a signature (session data untouched) |
| POST | `/api/tracks/identify` | name every unlabelled session that was driven on a [surveyed circuit](../internals/track-identification.md#matching-against-a-survey-bundle) → `{checked, identified, tracks}`. New sessions do this for themselves; this is for history recorded before the bundles existed. Sessions with no confident match are left alone. 409 when nothing has been surveyed |

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
| GET | `/api/analysis/compare` | `laps` (CSV ids), `ref` (id), `step` (m, default 5, 0.5–50), `channels` (optional CSV) → per-lap distance-resampled series, speed peaks/valleys, events, and delta-vs-reference. The reference lap also carries `corners`: the circuit's [authored corners](../guide/tracks-view.md#labelling-corners) when it has them (`authored: true`, and a `name` when given), otherwise detected from that lap's curvature. Top-level `accel` is the broadcast accelerometer's [unit + sign calibration](../internals/derived-channels.md#accelerometer-units-are-calibrated-not-assumed), fitted on the reference lap and applied to all of them; each lap with the channels also gets `gg`, its peak g in each direction taken from the raw ticks |
| GET | `/api/analysis/deviation` | `session_id`, `count` (2–20, default 5) → median speed + standard deviation by distance across the best N laps |
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
| GET | `/api/survey/status` | track/session the run is tied to, per-wheel char histogram, unknown chars, recent transitions, log path, undocumented flag-bit activity, the finish line located from lap rollovers (`finish`: mean crossing point/heading, crossing count, spread, confidence), and the track width in use — the entered assumption until enough out-and-back edge rides have measured the real axle width (`width_estimate_m`, `width_samples`) |
| GET | `/api/survey/trail` | breadcrumb of the path driven (`since`/`epoch` for incremental fetch; the epoch bumps when the trail is decimated) |
| GET | `/api/survey/edges` | every border-edge point of the run — the track taking shape (`since`/`epoch` incremental; append-only within a run). Kinds: `auto` (surface-flip contacts), `straddle` (sampled continuously while one side's wheels are held off the tarmac), `edge`/`runoff`/`wall` (manual marking) |
| POST | `/api/survey/mark` | `{side: "L"\|"R"\|null, kind: "edge"\|"runoff"\|"wall"}` — arm manual boundary marking: while armed, the survey samples edge points from that side's wheel line every ~2 m, which is how boundaries invisible to the surface chars (walls, and track edges with paved run-off beyond) get mapped |
| GET | `/api/survey/packet` | the latest raw telemetry packet, fully decoded — the Survey view's field inspector polls this |
| GET | `/api/survey/logs` | every survey run's JSONL, summarised (track, marks, transitions, finish crossings, size) and flagged `orphaned` when it gathered evidence and never reached a circuit — a run that saved no bundle at all and exists only as this file |
| POST | `/api/survey/logs/{name}/assign` | `{track}` — rebuild an orphaned run from its log and merge it into that circuit through the normal voting path, exactly as if it had been named while driving. The run is credited to the installation that **recorded** the log (its id is in the log's meta header), not the one replaying it. 409 while that run is still going, and 409 if it has already been assigned to that circuit — re-assigning to a *different* one is the mis-label correction. 404 for a name that is not a log in the data directory |
| GET | `/api/survey/logs/{name}/download` | the raw JSONL file itself — how a run recorded on one machine reaches another (upload it there, then assign). 404 for a name that is not a log |
| POST | `/api/survey/logs/upload` | *(admin)* accept a survey JSONL from another installation. The body is validated line by line against the log format (header shape, mark and transition records) before anything is kept — a bundle document or a truncated file is refused with the offending line number. Names are sanitised to the `surface_survey_*.jsonl` scheme and an existing file is never overwritten (a `_2` suffix is claimed atomically instead). 413 over 64 MB |
| GET | `/api/survey/export.jsonl` | full log of the current/last run (404 if none). First line is a `{"meta": ...}` header (track, session id, track-width assumption, wheel order); transition records carry the session id and lap, so they join back to the laps recorded during the same drive. Interleaved lines: `{"mark": ...}` for manually-marked boundary points and `{"track": ...}` when the circuit is identified mid-run |

## Track bundles & management

The joined view of the three sources of track knowledge, and the export /
import path for [track bundles](track-bundle-format.md). See the
[Tracks view](../guide/tracks-view.md).

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/track-catalog` | official GT7 track/layout metadata (bundled `data/tracks.json`: 41 tracks, 85 layouts, lengths, corner counts, reverse configs) |
| GET | `/api/track-overview` | one row per circuit, merged across the DB's named tracks, the survey bundles and the official catalog: `named` (auto-identification will work), bundle stats (points, runs, sources, elevation %, finish crossings, corners), the compiled `coverage` score (per-side boundary % + `closed` + `road_pct`) and `compiled_at` when the bundle compiles, session count, the confirmed `official` match and — only when there is none — a `suggestion` with its confidence and reasoning. Also lists the survey logs and this installation's source id |
| GET | `/api/track-outline` | `lap_id` (resolves the circuit from the lap's session) or `track` → the surveyed road, from the [compiled geometry](track-bundle-format.md#compiled-geometry-derived-not-part-of-the-bundle): `road` (a contiguous quad strip `[x1,z1,…,x4,z4]` between the ordered border curves), `edges` and `walls` (segments `[x1,z1,x2,z2]`), `finish`, `gaps` (unsurveyed spans the ordering had to bridge — drawn dashed, never as road) and `coverage` (per-side boundary coverage + `road_pct`). Answers with an **empty** outline rather than 404 when the circuit has no bundle — never having been surveyed is the common case, not an error. Compiled off the event loop and recompiled per bundle revision: a bundle is up to 50,000 records and the browser must not download it |
| GET | `/api/track-bundles` | every circuit's bundle, with the same stats and coverage |
| GET | `/api/track-bundles/{slug}` | one bundle document — the export unit, and what import consumes |
| POST | `/api/track-bundles/import` | merge a bundle document from elsewhere. `?track=` overrides the document's own label, which is how a near-miss name lands on the right circuit. Every field is validated and rebuilt before anything is merged; versions 1–4 are accepted and upgraded. Your own authored corners and confirmed layout match are never overwritten (`corners_kept` says when incoming ones were dropped). 400 on any malformed document; 413 over 64 MB, enforced while reading rather than after buffering |
| PATCH | `/api/track-bundles/{slug}` | `{track?}` renames — **merging** when the new name is an existing bundle, which is the fix for one circuit living under two spellings. `{official, set_official: true}` records the confirmed official layout (`official: null` clears it). 409 while a survey is running on that circuit: it holds the old name in memory, so its next save would recreate the bundle just moved |
| DELETE | `/api/track-bundles/{slug}` | remove a bundle (the survey JSONL logs are untouched). 409 while a survey is running on that circuit |
| GET | `/api/track-bundles/{slug}/corners` | the circuit's authored corners and sections |
| PUT | `/api/track-bundles/{slug}/corners` | `{corners?, sections?}` — replace them; omitted lists are left alone. Renumbered from list order. 404 when the circuit has no bundle: corners are anchored to positions on a surveyed map |

## Admin

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/admin/settings` | current runtime settings |
| PUT | `/api/admin/settings` | `{ps_ip?, source?, log_level?, webhook_url?, webhook_events?, packet_format?, race_engineer?, race_engineer_verbosity?, race_engineer_categories?, race_engineer_units?}` — applied live, persisted to the DB |
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
