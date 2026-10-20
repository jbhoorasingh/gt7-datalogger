# Live client protocol

How a native app — the phone client — joins the live stream. It is the same
WebSocket the browser UI uses, `/ws/live`, with one extra exchange at the start that
lets a client choose its own telemetry rate, frame and events. A client that skips it
gets exactly what the browser gets.

The machine-readable contract is published beside this page and generated from the
server's own models, so it cannot drift from what the server sends:

- [`schemas/ws/client-message.schema.json`](schemas/ws/client-message.schema.json):
  every message a client may send
- [`schemas/ws/server-message.schema.json`](schemas/ws/server-message.schema.json):
  every message the server sends
- [`openapi.json`](openapi.json): the REST API

Regenerate them after changing the protocol or a route with
`python backend/scripts/export_schemas.py`; a test fails until you do.

## Finding the server

The server announces itself over mDNS (Bonjour) as **`_gt7dl._tcp`** unless
`GT7_MDNS` is off. The service's address and port are the server's, and its TXT
record carries:

| Key | Value |
| --- | --- |
| `protocol` | the live client protocol version, `1` |
| `version` | the server's release, e.g. `0.7.0` |
| `ws` | the live stream's path, `/ws/live` |
| `api` | the REST API's prefix, `/api` |

Browse for it from a shell with `dns-sd -B _gt7dl._tcp` (macOS) or
`avahi-browse -r _gt7dl._tcp` (Linux).

Multicast does not leave a Docker bridge network, and some routers drop it between
Wi-Fi and wired. **Settings › Phone app** shows the server's address as a QR code for
those cases. A server listening on `127.0.0.1` only (uvicorn's command line without
`--host`) does not announce itself, and the QR card says why: the LAN address would
not answer on that port.

## Connecting

The server is on the LAN, plain HTTP, port 8000 unless configured otherwise. There
is no authentication on the live stream or on reads; only admin and mutating REST
calls take the `X-API-Key` header, and only when `GT7_ADMIN_TOKEN` is set.

```text
ws://<datalogger>:8000/ws/live
```

Every message, in both directions, is one JSON text frame: `{"type": ..., "data": {...}}`.

## Hello and welcome

Send `hello` as the first message:

```json
{
  "type": "hello",
  "data": {
    "protocol": 1,
    "client_id": "6f1c…",
    "app": "gt7-phone",
    "app_version": "0.1.0",
    "subscribe": {
      "telemetry_hz": 60,
      "frame": "full",
      "events": ["lap", "session", "status"]
    }
  }
}
```

| Field | Meaning |
| --- | --- |
| `protocol` | The protocol version the client speaks. Required. |
| `client_id`, `app`, `app_version` | For the server log. Optional. |
| `subscribe.telemetry_hz` | Telemetry frames per second, 1–60. Omitted = the server's `GT7_WS_RATE` (default 30). |
| `subscribe.frame` | `compact` (default): the browser's frame. `full`: the compact frame plus `packet`, every parsed field of the newest packet, unrounded. |
| `subscribe.events` | Event types to receive; omitted = all of them. `status`, `welcome` and `error` always arrive. |

The server answers with `welcome`:

```json
{
  "type": "welcome",
  "data": {
    "protocol": 1,
    "server": "gt7-datalogger",
    "server_version": "0.7.0",
    "lan_ip": "192.168.1.20",
    "http_port": 8000,
    "capabilities": {
      "max_telemetry_hz": 60,
      "default_telemetry_hz": 30,
      "frames": ["compact", "full"],
      "events": ["lap", "session", "status", "survey", "voice_callout", "voice_output_status", "race_engineer_status"]
    },
    "subscribe": {"telemetry_hz": 60, "frame": "full", "events": ["lap", "session", "status"]}
  }
}
```

`subscribe` in the reply is the subscription in effect: event types the server does
not know are dropped from it.

A `hello` or `subscribe` the server cannot accept, such as a missing `protocol` or a
rate above 60, is answered with `error` and changes nothing:

```json
{"type": "error", "data": {"request": "hello", "message": "subscribe.telemetry_hz: Input should be less than or equal to 60"}}
```

## Changing the subscription

Send `subscribe` at any time with the same fields as `hello.subscribe`. It replaces
the whole subscription, so a field left out returns to its default. For example, the
phone drops to 10 Hz compact frames when its dash is in the background:

```json
{"type": "subscribe", "data": {"telemetry_hz": 10, "frame": "compact"}}
```

## What the server sends

- **`status`** arrives on connect, before the welcome, and again whenever the source or
  the console changes. **`session`** has the same payload and arrives when a session
  starts or its track is identified or named. Both carry `protocol`, so a client can
  check the server's version before it says hello.
- **`telemetry`** is the [compact frame](api.md#websocket-wslive). With
  `frame: "full"` it also carries `packet`. Frames are latest-wins: a client that
  reads slowly skips frames, it never builds up a backlog.
- **`lap`**, **`survey`**, **`voice_callout`**, **`voice_output_status`** and
  **`race_engineer_status`** are as in the [API reference](api.md#websocket-wslive).
  Events are queued, and a client that stops reading for minutes is disconnected
  (close code 1013) rather than silently missing one. The exception is `survey`:
  transitions can burst at 60 Hz over kerbs, so when a slow client's queue is full they
  are dropped for that client instead. Read the survey's state back over REST
  (`/api/survey/status`, `/api/survey/edges`) rather than relying on every transition
  arriving.

A native app that runs its own Race Engineer should leave the `voice_*` and
`race_engineer_status` events out of its subscription. The server's Race Engineer only
runs while a browser has registered for voice (`client_capabilities`), so a native app
that never sends that costs the server nothing.

## Reconnecting

The server keeps nothing about a client between connections. After a reconnect, send
`hello` again, then fetch over REST whatever happened in the meantime. For example,
`GET /api/sessions/{id}/laps` for the session in the new `status`.

## Keeping a copy of the history

A client that stores sessions on the device asks only for what changed:

1. `GET /api/sessions/changes?since=0` the first time; store `revision`.
2. For each session in `sessions`, download `GET /api/sessions/{id}/export.zip` (every
   lap with its samples, plus `session.json`) and replace the copy held. Use
   `GET /api/sessions/{id}/analysis.json` for the analysis document alone.
3. Delete any held session whose id is not in `ids`.
4. Next time, `GET /api/sessions/changes?since=<stored revision>`.

A session being recorded changes with every lap, so it comes back on each call
until the drive ends. Pull after a `session` event, or on a timer while the app is
open, rather than after every `lap`.

## Versioning

- `protocol` goes up only for a change an existing client would misread: a field that
  changes meaning or type, or a message that is removed.
- Adding a message type, a field, an event type, a frame kind or a capability does
  **not** change it. Clients must ignore types and fields they don't know.
- A client that receives a `welcome` with a lower `protocol` than its own should fall
  back to what that version offers, or tell the user to update the datalogger.
- The live stream is unversioned for clients that don't say hello. It stays exactly as
  the browser UI expects it.
