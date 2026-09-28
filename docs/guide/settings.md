# Settings

`#/settings` — runtime configuration, diagnostics, and data management. Each section
has its own address, `#/settings/{section}` (`connection`, `sync`, `race-engineer`,
`notifications`, `overlays`, `health`, `logs`, `access`, `data`), so a bookmark or a
link lands on the right one. Old `#/admin` links still open this page.

The page has three parts:

- A **health strip** across the top — Telemetry, Recording, Sync and Storage at a
  glance. Click a tile to open the section behind it.
- A **section rail** on the left, grouped *Capture* (Connection, Sync), *Race day*
  (Race Engineer, Notifications), *Streaming* (Overlays & dashboards) and *System*
  (Health, Logs, Access, Data). Each entry carries a short status: *receiving*,
  *3 queued*, *2 errors*, *protected*, the database size.
- **Find a setting…** — a search box that filters the rail by name and keyword
  (`token`, `webhook`, `format`); Enter opens the first match.

**Edits are buffered.** Changing a setting does not send it: a bar appears at the
bottom listing what differs from the server (*2 changes · Packet format · Webhook
URL*), with **Discard** and **Apply changes**. Apply sends every change in one
request; they take effect **immediately, no restart**, persist in the database, and
override environment variables on the next start. Actions — Find console, Send test,
Restart, Compact, the sync Connect and Disconnect buttons — still act at once.

The status bar's telemetry pill (*Receiving* / *No telemetry* / *Offline*) is a link
to **Settings › Connection** from any page, and the **Settings** tab carries a dot when
something there needs attention — dropped telemetry frames, or a sync error.

## Connection

- **Telemetry source** — switch between **PlayStation** (UDP capture) and **Simulated**
  (the built-in synthetic 60 Hz source) live. This is the in-app equivalent of
  `GT7_SOURCE=sim` — everything (live view, recording, analysis, overlay) works against
  the simulator.
- **Console** — the PlayStation's IP address; leave empty for broadcast
  auto-discovery. Applying resets discovery so the change takes effect at once.
  **Find console** broadcasts on your network and fills in the IP of the console that
  answers — press Apply to save it. It needs the source set to PlayStation, the
  console on the same network and GT7 running. Under the field a status line says
  whether telemetry is arriving, from where, at what rate, and on which ports.
- **Packet format** — which telemetry format to request from the console: **A**
  (base, 296 B), **B** (adds steering/motion), **~** (adds filtered inputs, torque
  vectors), or **C** (adds surface type, live lap timer — needs GT7 v1.68+, the
  default). Applied on the next heartbeat, within ~2 s. Use **A** if an older game
  version stops sending data.

## Sync

Push this installation's data to a **sync service** — the hosted one at
`sync.gt7-datalogger.com`, or one you run yourself. Three data types, each with its
own toggle: **tracks** (your surveys, contributed to the community outlines),
**sessions** (your own laps, as a private cloud copy) and **live** (where the car is,
a few times a second, for the service's spectate page). Pulling shared bundles
(Tracks → **Import ▾** → *Pull from shared*) needs no account and is unchanged; tracks
sync is the other direction: the bundles you survey go up, a job in the track-data repo merges what
everybody sent and opens the pull requests, and GitHub stays the source of truth.

- **Connect** — the section opens on three steps: sign in at
  `sync.gt7-datalogger.com` (Profile → Tokens → Create token; it is shown once), paste
  the token, choose what to send. **Connect** saves the token and tests it straight
  away. The token identifies your account; it is stored apart from the address, masked
  from then on (`…a1b2`), sent only as a `Bearer` header, never in a URL and never to
  the logs. Pasting the whole `gt7sync://…?token=…` connection string the portal shows
  works too: it is split into address and token, and the string itself is not kept.
- **Use your own server** — a disclosure under the token field for a server on your
  LAN, or anywhere other than the hosted service. It holds an **address** and a
  **token**: a bare host name (`sync.example.com`) is read as `https://`; write
  `http://` for a LAN server. A connection string (`gt7sync+http://host:port/?token=…`)
  carries both, so the token field can stay empty. Changing the address forgets what
  the previous server had accepted and re-asks the new one what it offers. Connecting
  with the main token field switches back to the hosted service.
- **Once connected** — a row shows the server, its version, the token hint, when it
  was last checked and which data types it accepts. **Test connection** re-asks the
  server at any time; **Disconnect…** forgets the token (the service only reveals a
  token once, so you will need a new one). If the server rejects the token, a red
  strip says so, with **Paste a new token**.
- **Sync enabled** — the master switch, top right of the section. On its own it sends
  nothing. It and the per-type toggles below are ordinary settings: they wait for
  **Apply changes**.
- **What to send** — one toggle per data type, each with a one-line description of what
  leaves the machine and a status line: `off`, `idle`, `syncing…`, `connected`, or
  `error: <reason>`, followed by that type's counters (bundles synced and queued, laps
  sent, sessions on the server, frames streamed).
  A toggle is only enabled when the server advertises that type *and* this build can
  send it; a type the server offers that this release cannot send yet says so. All
  toggles default off, and enabling one never enables another.
    - **tracks** — the survey bundle of every circuit whose official layout you have
      confirmed, reduced to **this installation's own evidence**: the border votes it
      cast, with the finish crossings, corner labels and sections. Evidence pulled
      from the shared repo or imported from a friend's file stays here and never goes
      up — the service files every installation under one account and refuses a
      document naming somebody else's, so an upload carries only what was recorded
      here. A bundle is uploaded once it has been **left alone for ten minutes**.
      Every write restarts that clock — the survey's once-a-minute autosave, the save
      when it stops, an import, a pull, a rename, a layout confirmation, a corner
      edit — so a running survey never uploads mid-run: the run goes up once, after
      it has stopped and the corner labelling that usually follows is done, and one
      upload carries all of it. Nothing is sent when your own evidence has not changed
      since the server last accepted it (a pull that only adds other people's is not
      a change), and never more than once a minute per bundle. Bundles with no
      confirmed layout are never sent — the Tracks view marks them *not synced —
      confirm layout* — and neither is one you pulled but have not surveyed here
      (*not synced — nothing of yours*). Nothing else is uploaded under this toggle:
      not your laps, not your settings, not the installation id file, not the repo's
      corrections.
    - **sessions** — your own driving, one lap at a time as you drive: each lap goes
      as the same `gt7-datalogger-lap` document **Export** writes (lap time, the
      full 60 Hz samples, your racing line, whether it counts toward bests), against
      a session the service opens for the drive. The session is announced with its
      **first lap**, not at the start — a stint that never completes a lap is dropped
      here and never reaches the server, and the circuit is identified one lap in, so
      the summary carries the track and its layout id. When the drive ends (the next
      stint starts, or the logger stops) the totals go. Laps queue while the service
      is away and flush in order when it answers again, or at the next start; the
      queue is in `data/sync-sessions.json` and holds at most 500 laps (the local
      database keeps every lap regardless). A lap you rule in or out of the bests by
      hand is sent again with its new verdict. Sessions are **private by default**
      on the service — visible to your account and its administrators — and the
      service's portal is where you make one public or delete it; nothing from them
      feeds a track outline.

      After the totals goes the session's **lap analysis** — the
      [document](../reference/lap-analysis-format.md) **Export analysis** downloads:
      every lap measured corner by corner against the session's best, about 100 KB
      where the laps themselves are megabytes. It needs no toggle of its own, and it
      is sent **only to a service that says it takes it** (`analysis_version` among
      the `sessions` hints of its capabilities): against a service that predates
      it, nothing about a drive's sync is different and nothing is asked of the
      server that it does not know. A lap ruled in or out by hand after the drive
      sends the document again, since it may have changed the reference lap. If the
      server refuses the document, that is recorded against the document alone —
      the session and its laps are untouched — and the status line counts it.
    - **live** — where the car is, `GT7_SYNC_LIVE_HZ` times a second (4 by default;
      the server states its own ceiling and the lower wins): position, speed, gear,
      lap and lap time, over one WebSocket to the service, while the car is **on
      track**. The socket opens on the first on-track packet and closes after five
      minutes without one; leaving the track or pausing sends one last frame saying
      so and then nothing. Frames are downsampled from the 60 Hz feed, never queued
      — a spectator wants to know where the car *is* — and are not stored by the
      service beyond its 30 s replay buffer unless your account asks for a recording.
      The status line shows the **spectate URL** once the stream has connected, and
      how many people are watching; whether strangers may watch is your account's
      setting in the service's portal, not one here. A stream an administrator
      closes, or that another logger on the same account replaces, is held for
      fifteen minutes and says why rather than reconnecting at once.
- **Send it now** — each type's row has a button: **Sync now** (tracks) uploads every
  eligible bundle rather than waiting for it to settle, **Flush now** (sessions) sends
  the queued laps rather than waiting out a backoff. Unchanged bundles and laps the
  server already holds are still skipped. **Reconnect** (live) is only offered when
  the stream is being held after a failure; otherwise it reconnects by itself while
  the car is on track.

The **How and when each type is sent** disclosure under the rows repeats the short
version of the above.

Uploads run in the background with retry and exponential backoff and never block
recording or the UI. A document the server refuses (with its reason) is not retried
until it changes. If the server answers `403 type_disabled`, that type's toggle is
switched off here and the status line says *server no longer accepts this*. What was
uploaded is remembered in `data/sync-state.json` (bundles) and
`data/sync-sessions.json` (sessions), so a restart does not re-send what the server
already has; a different server starts from nothing.

Per-track status — *synced*, *queued*, *rejected*, *error* — is on each row of the
[Tracks view](tracks-view.md#sync-status).

## Race Engineer

Server-side control of the spoken callouts: the **Callouts on** switch, the **maximum
verbosity** (Minimal / Race / Coach), the **spoken units** (Metres · km/h or Feet ·
mph, used by the braking-point and apex-speed coaching), and which callout
**categories** the backend emits at all.

Verbosity and categories are a **ceiling**, not a default: a browser picks its own
verbosity and categories underneath them, but can never exceed them, so anything
switched off here never reaches any device. Out of the box the ceiling is *Coach* —
the server produces everything and each device decides what it wants. Voice, volume,
rate and per-device toggles belong to each browser, on `/dash` or `/engineer`.

A diagnostics strip at the top of the section shows whether detection is running (it
only runs while a browser has voice enabled), which page is speaking, how many
callouts were emitted, and speech failures — with a red line when a browser receives
callouts but cannot play them. **More diagnostics** adds the voice-capable clients,
the suppressed counters (cooldown, duplicate, category), spoken acks and the last
callout emitted — the fastest way to tell "nothing was detected" from "it was
suppressed by a cooldown" from "nobody is listening".

**Send test callout** pushes a callout to every connected browser, which proves the
whole path end to end without driving.

Full details: [Race Engineer](race-engineer.md).

## Notifications

Set a **webhook URL** and pick which events to be notified about under **Notify me
when…** — each has its own toggle, enabled once a URL is set. An empty URL turns every
notification off.

| Event | Fires when | JSON `event` |
| --- | --- | --- |
| **Personal bests** | a session best is beaten (never on the first lap), with lap, improvement, car, track | `personal_best` |
| **Session summaries** | a session ends, with car, track, lap count, best lap, fuel used | `session_summary` |
| **Overtakes** | your race position improves (e.g. P3 → P2) | `overtake` |
| **Positions lost** | your race position drops | `position_lost` |
| **Off-road excursions** | 3+ wheels are on grass/dirt/sand/snow at speed | `off_road` |

Notes on the race events:

- **Position events** need GT7 to report a live race position — it only does in some
  race types (elsewhere the field reads −1 and nothing fires). A change must **hold
  for ~1 s** before it counts, so side-by-side battles don't spam your channel.
- **Off-road** needs **packet format C** (the default), the only format carrying
  per-wheel surface data. Kerbs and two-wheels-over-the-line don't count; one
  excursion sends one event, re-arming after ~2 s back on tarmac.

**Discord** webhook URLs get a rich embed; **any other URL** receives plain JSON
(snake-cased fields plus the `event` name above), so n8n / Home Assistant–style
automations work out of the box. **Send test** sends a test event so you can verify
delivery — it ignores the toggles, and it uses the saved URL, so apply a new URL
before testing it. Notifications are fire-and-forget — a
failed delivery logs a warning and never blocks capture.

Trust model: the webhook URL may deliberately point at LAN services (Home
Assistant, n8n) — private addresses are not blocked. Redirects are never followed,
and setting `GT7_ADMIN_TOKEN` ensures only you can change the URL.

## Overlays & dashboards

A table of your saved layouts: name, kind (overlay or dashboard), canvas size, widget
count and URL, with **Copy URL** and **Edit** on each row. **Open builder** goes to
the [Overlays](overlay.md) tab, where layouts are built; **Import JSON…** turns a
layout file into a new saved overlay named after the file.

## Health

Live stats, refreshed every 5 s, in three groups:

- **Telemetry** — status, console, packets received, packet rate, decode errors,
  frames dropped, packet format.
- **Server** — uptime, connected live clients, car names loaded, the date of the car
  list, recording on/off.
- **Database** — sessions, laps, size.

Problems are also spelled out below the numbers: no telemetry (check the console IP,
that GT7 is in a session, and that UDP 33740 reaches this machine), dropped frames
(usually Wi-Fi), undecodable packets (check the packet format).

Two actions:

- **Restart telemetry source** — stop/start the current source (rebinds the UDP socket,
  restarts discovery).
- **Update car database** — refreshes the car inventory from GT7's own car list, now
  rather than waiting for the weekly background check. You do not need to run this after
  installing: every car GT7 publishes ships with the app. Use it when a content update
  has just added cars you want named today. Cars the list no longer publishes keep their
  names either way.

## Logs

A live viewer over the server's in-memory log ring buffer, refreshed every 2 s:
severity filter (**All**, **INFO+**, **WARN+**, **ERROR**), **Pause**/**Resume**,
**Download** (the entries shown, as a `.log` file), **Clear**, and auto-scroll that
only follows when you're already at the bottom. This is the first place to look when
telemetry isn't arriving.

**Server log level** (DEBUG / INFO / WARNING / ERROR) sits on the same toolbar; like
every setting, it applies with **Apply changes**.

## Access

- **Server protection** — whether the server sets `GT7_ADMIN_TOKEN`: *Open* (anyone
  on your network can change settings) or *Protected* (changes need the admin token).
- **Admin token in this browser** — only relevant when the server is protected. Enter
  the token here once per browser (it's stored in that browser's localStorage and sent
  as `X-API-Key`); saving reloads the page. Without it, Settings, the recording toggle,
  session/lap deletes, imports, and layout saves return 401; the Live view, overlays,
  and the driver dash never need it. When the server is protected and no valid token
  is stored, the other sections show an **Unlock settings** field instead.

## Data

The section header shows how many sessions and laps are stored and the size on disk.

- **Back up** — **Export all laps (JSON)** downloads a ZIP with each lap's JSON export
  in a folder per session (each importable again, one lap at a time); **CSV (all
  laps)** is the same with each lap's CSV export.
- **Compact database** — SQLite `VACUUM` to reclaim space after deleting laps. The
  space it would reclaim is shown next to **Compact** once it passes a megabyte.
- **Delete all recorded data** — removes **every session and lap**. Type `DELETE` to
  enable the button. Settings, tracks and layouts are kept; it cannot be undone, so
  back up first.
