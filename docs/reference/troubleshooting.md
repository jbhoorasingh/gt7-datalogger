# Troubleshooting

## "Server up, no telemetry" (amber status dot)

The backend is running but no packets are arriving from the console.

- Check `GT7_PS_IP` is the PlayStation's current IP (it can change after a router
  reboot — consider a DHCP reservation).
- Make sure the PlayStation and the server are on the **same network / subnet**.
- Check that UDP port **33740** is not blocked by a firewall on the server.
- If you rely on broadcast auto-discovery inside Docker, note that the default bridge
  network cannot see LAN broadcasts — set `GT7_PS_IP` explicitly or use
  `network_mode: host` (Linux only).
- You must be **in a race, time trial, or replay** — GT7 only streams telemetry while
  driving is on screen, not in menus.
- The default **packet format C** needs GT7 **v1.68+** (and the `~` format doesn't
  stream during replays). On an older game version, set **Packet format** to `A` in
  the Admin view.

## Wrong or garbled data (decode errors in `/api/status`)

- Another tool on the network may be consuming or interfering with the stream.
- The packet format may have changed after a game update — check for a newer release of
  the datalogger.

## No laps recorded

- Laps are only recorded while the car is **on track and not paused** — menu time is
  ignored.
- **Replays do record.** GT7 streams a replay exactly like driving (there is no
  replay flag), so a race replay records its laps normally, and a single-lap
  leaderboard replay — which ends *at* the line, before a lap would normally commit —
  is [salvaged](../internals/lap-detection.md#replay-salvage) when GT7's own reported
  lap time matches the recording. Two consequences: a race replay that cuts off
  *before* the flag still discards its final partial lap (no matching time, no lap),
  and replays capture *other drivers'* laps into your history — see
  [excluding a session from bests](../guide/sessions-view.md#excluding-a-session-from-bests).
  The `~` packet format doesn't stream during replays; the default format C does.
- The first (out) lap completes when you cross the start line — until then nothing is
  saved.
- Check that recording hasn't been toggled off in the **Sessions** view.

## Dashboard loads but shows "demo" data

The frontend falls back to a demo frame when it cannot reach the backend WebSocket.
Check that the backend is running and reachable at the same host/port you loaded the
page from.

## Overlay shows a black background in OBS

Use a **Browser source** in OBS and make sure the overlay layout is set to the
transparent strip; alternatively use the green-screen page mode and add a chroma-key
filter for apps without alpha support. See [Overlay & streaming](../guide/overlay.md).

## Webhook notifications not arriving

- Check the URL with the **Test** button in Admin → Notifications (the test ignores
  the per-event toggles).
- Each event type has its own toggle — make sure the one you expect is enabled.
- **Overtake / position lost** need GT7 to report a live race position; it only does
  so in some race types (the field reads −1 elsewhere, and nothing fires). Changes
  also must hold for ~1 s before they count.
- **Off-road** needs **packet format C** — check Admin → Connection.

## 401 / 403 "admin token" errors

The server has `GT7_ADMIN_TOKEN` set. Enter the same token under **Admin →
Connection → Admin token** (once per browser). 403 means the entered token doesn't
match the server's. Read-only pages (Live, overlays, `/dash`) never need the token.

## Cross-origin (CORS) errors after upgrading

Wildcard CORS was removed — the bundled UI is same-origin and unaffected, but if you
built a separate app that calls this API from another origin, set
`GT7_CORS_ORIGINS=http://your-app-host:port` (comma-separated for several).

## Getting more detail

- **Admin → Logs** shows the live backend log with level filtering.
- `GET /api/status` reports connection state, packet rates, and decode errors — useful
  when filing an issue.
- Database growing large? **Admin → Database** shows stats and offers compact/clear
  actions.

Still stuck? [Open an issue](https://github.com/jbhoorasingh/gt7-datalogger/issues) with
your setup (Docker/native, network layout) and the output of `/api/status`.
