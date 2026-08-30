# Admin view

`#/admin` — runtime configuration, diagnostics, the overlay builder, and data
management. Settings changed here apply **immediately, no restart**, persist in the
database, and override environment variables on the next start.

## Connection

- **PlayStation IP address** — set or change the console IP at runtime; leave empty for
  broadcast auto-discovery. Applying resets discovery so the change takes effect at
  once.
- **Telemetry source** — switch between **PlayStation** (UDP capture) and **Simulated**
  (the built-in synthetic 60 Hz source) live. This is the in-app equivalent of
  `GT7_SOURCE=sim` — everything (live view, recording, analysis, overlay) works against
  the simulator.
- **Packet format** — which telemetry format to request from the console: **A**
  (base, 296 B), **B** (adds steering/motion), **~** (adds filtered inputs, torque
  vectors), or **C** (adds surface type, live lap timer — needs GT7 v1.68+, the
  default). Applied on the next heartbeat, within ~2 s. Use **A** if an older game
  version stops sending data.
- **Log level** — DEBUG / INFO / WARNING / ERROR, applied server-side immediately.
- **Admin token** — only relevant when the server sets `GT7_ADMIN_TOKEN`. Enter the
  token here once per browser (it's stored in that browser's localStorage and sent as
  `X-API-Key`). Without it, the Admin pages, the recording toggle, session/lap
  deletes, imports, and layout saves return 401; the Live view, overlays, and the
  driver dash never need it.

## Diagnostics

Auto-refreshing stats: telemetry connection state, console IP, packets received,
decode errors (amber when non-zero), server uptime, connected live clients,
session/lap counts, database size, and loaded car names. Two actions:

- **Restart telemetry source** — stop/start the current source (rebinds the UDP socket,
  restarts discovery).
- **Update car database** — refreshes the car inventory from GT7's own car list, now
  rather than waiting for the weekly background check. You do not need to run this after
  installing: every car GT7 publishes ships with the app. Use it when a content update
  has just added cars you want named today. Cars the list no longer publishes keep their
  names either way.

## Notifications

Set a **webhook URL** and pick which events to be notified about — each has its own
toggle in the panel:

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
automations work out of the box. The **Test** button sends a test event so you can
verify delivery — it ignores the toggles. Notifications are fire-and-forget — a
failed delivery logs a warning and never blocks capture.

Trust model: the webhook URL may deliberately point at LAN services (Home
Assistant, n8n) — private addresses are not blocked. Redirects are never followed,
and setting `GT7_ADMIN_TOKEN` ensures only you can change the URL.

## Race Engineer

Server-side control of the spoken callouts: the feature switch, the **maximum
verbosity**, the **spoken units** (meters/km-h or feet/mph, used by the braking-point
and apex-speed coaching), and which callout **categories** the backend emits at all.

Both settings are a **ceiling**, not a default: a browser picks its own verbosity and
categories underneath them, but can never exceed them, so anything switched off here
never reaches any device. Out of the box the ceiling is *Coach* — the server produces
everything and each device decides what it wants. Voice, volume, rate and per-device
toggles belong to each browser, on `/dash` or `/engineer`.

The diagnostics block shows whether detection is running (it only runs while a
browser has voice enabled), how many voice-capable clients are connected, which one
is speaking, and the emitted/suppressed counters — the fastest way to tell "nothing
was detected" from "it was suppressed by a cooldown" from "nobody is listening".

**Send test callout** pushes a callout to every connected browser, which proves the
whole path end to end without driving.

Full details: [Race Engineer](race-engineer.md).

## Overlay & dashboard builder

Documented on its own page: [Overlay & streaming](overlay.md).

## Logs

A live viewer over the server's in-memory log ring buffer (last 2,000 records):
severity filter (`DEBUG+` … `ERROR+`), pause/resume, clear, auto-scroll that only
follows when you're already at the bottom. This is the first place to look when
telemetry isn't arriving.

## Data management

- **Compact database** — SQLite `VACUUM` to reclaim space after deleting laps.
- **Delete all recorded data** — removes **every session and lap** (confirmed). Settings
  and track signatures are kept. Export any laps you want to keep first
  (Sessions → json).
