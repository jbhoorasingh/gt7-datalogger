# REST & WebSocket API

Everything the dashboard does goes through this API, so anything the UI can do, a
script can too. All REST routes live under `/api`; responses are JSON.

## Status & health

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | `{"status": "ok"}` |
| GET | `/api/status` | source kind, connection state, packet/error counters, recording flag, current session id, track name |

## Sessions & laps

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/sessions` | all sessions, newest first, with lap count and best lap |
| DELETE | `/api/sessions/{id}` | delete a session and its laps |
| GET | `/api/sessions/{id}/laps` | lap summaries for one session |
| GET | `/api/laps` | all lap summaries, newest first |
| GET | `/api/laps/{id}?samples=true` | full lap detail: metrics, events, gearing, and (optionally) the 60 Hz samples |
| DELETE | `/api/laps/{id}` | delete a lap |
| GET | `/api/laps/{id}/export` | JSON export envelope (see [Lap file format](lap-file-format.md)) |
| GET | `/api/laps/{id}/export.csv` | MoTeC-compatible CSV (one row per tick, 27 channels with units) |
| POST | `/api/laps/import` | import an exported lap file |

## Tracks

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/tracks` | stored track signatures |
| POST | `/api/tracks` | `{name, lap_id}` — name a circuit from a lap's geometry |
| DELETE | `/api/tracks/{id}` | remove a signature (session data untouched) |

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
| GET | `/api/analysis/compare` | `laps` (CSV ids), `ref` (id), `step` (m, default 5, 0.5–50), `channels` (optional CSV) → per-lap distance-resampled series, speed peaks/valleys, events, and delta-vs-reference |
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
| GET | `/api/track-catalog` | official GT7 track/layout metadata (bundled `data/tracks.json`: 41 tracks, 85 layouts, lengths, corner counts, reverse configs) |
| POST | `/api/survey/start` | `{track_width_m?, track?}` — start a run; resets counters, opens a new JSONL log; 409 if a run is already active. `track` labels which circuit the samples describe (default: the session's identified track; picked up mid-run if identification happens later, and a user-typed label is never auto-overwritten) |
| POST | `/api/survey/stop` | stop and close the log |
| GET | `/api/survey/status` | track/session the run is tied to, per-wheel char histogram, unknown chars, recent transitions, log path, undocumented flag-bit activity, the finish line located from lap rollovers (`finish`: mean crossing point/heading, crossing count, spread, confidence), and the track width in use — the entered assumption until enough out-and-back edge rides have measured the real axle width (`width_estimate_m`, `width_samples`) |
| GET | `/api/survey/trail` | breadcrumb of the path driven (`since`/`epoch` for incremental fetch; the epoch bumps when the trail is decimated) |
| GET | `/api/survey/edges` | every border-edge point of the run — the track taking shape (`since`/`epoch` incremental; append-only within a run). Kinds: `auto` (surface-flip contacts), `straddle` (sampled continuously while one side's wheels are held off the tarmac), `edge`/`runoff`/`wall` (manual marking) |
| POST | `/api/survey/mark` | `{side: "L"\|"R"\|null, kind: "edge"\|"runoff"\|"wall"}` — arm manual boundary marking: while armed, the survey samples edge points from that side's wheel line every ~2 m, which is how boundaries invisible to the surface chars (walls, and track edges with paved run-off beyond) get mapped |
| GET | `/api/survey/packet` | the latest raw telemetry packet, fully decoded — the Survey view's field inspector polls this |
| GET | `/api/track-bundles` | every circuit's accumulated survey bundle: track, slug, points, runs, finish crossings, updated_at |
| GET | `/api/track-bundles/{slug}` | one bundle document (versioned `gt7-datalogger-track-bundle` JSON: perimeter edge points grid-deduped to 1 m, finish crossings) — the export unit for a future track-data repo |
| GET | `/api/survey/export.jsonl` | full log of the current/last run (404 if none). First line is a `{"meta": ...}` header (track, session id, track-width assumption, wheel order); transition records carry the session id and lap, so they join back to the laps recorded during the same drive. Interleaved lines: `{"mark": ...}` for manually-marked boundary points and `{"track": ...}` when the circuit is identified mid-run |

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
