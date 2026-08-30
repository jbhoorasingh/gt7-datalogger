# Telemetry capture

GT7 streams encrypted telemetry over UDP at roughly **60 Hz** while driving is on
screen. This page describes exactly how the datalogger captures and decodes it.

## The handshake

The console only streams to a client that keeps asking for data:

1. The datalogger binds UDP port **33740** and sends a **heartbeat** — a single
   character — to the console's port **33739** every **1.6 s**. (GT7 stops sending
   after ~100 packets without a heartbeat.) The character selects the packet format:
   `A`, `B`, `~`, or `C` (see [packet formats](#packet-formats) below); the
   datalogger sends `C` by default (`GT7_PACKET_FORMAT`, changeable live in Admin).
2. The heartbeat is addressed to `GT7_PS_IP` if configured. If not, it is
   **broadcast** (`255.255.255.255`), and the console's address is learned from the
   source address of the first telemetry packet that arrives — that's the
   auto-discovery mechanism.
3. If no packet arrives for **5 s** the connection is considered stale and the status
   indicator drops to "waiting for telemetry" while heartbeats continue.

## Packet formats

Four packet formats exist, each a strict superset of the previous. Parsing keys off
the datagram length, so whatever the console answers is decoded correctly:

| Heartbeat | Size | Adds |
| --- | --- | --- |
| `A` | 296 B | base telemetry (everything in the table below) |
| `B` | 316 B | steering wheel rotation, sway / heave / surge accelerations |
| `~` | 344 B | filtered throttle & brake, per-wheel torque vectors, energy recovery |
| `C` | 368 B | per-wheel surface type, live lap timer, front-wheel steering angles, wheelbase, car category (needs GT7 **v1.68+**) |

The extended fields are parsed into the typed packet model (as `None` when the
console sends a smaller format). Older game versions only answer the `A` heartbeat —
set **Packet format** to `A` in the Admin view if no data arrives.

## Decryption

Each datagram is encrypted with **Salsa20**:

- **Key** — the first 32 bytes of the ASCII string
  `Simulator Interface Packet GT7 ver 0.0` (i.e. `Simulator Interface Packet GT7 v`).
- **Nonce** — a 4-byte little-endian seed `iv1` is read *unencrypted* at offset `0x40`
  of the datagram. A second value is derived as `iv2 = iv1 XOR <constant>`, and the
  8-byte Salsa20 nonce is `iv2` followed by `iv1` (both little-endian). The XOR
  constant depends on the packet format: `0xDEADBEAF` for `A` (yes, `BEAF` — that is
  the real GT7 constant), `0xDEADBEEF` for `B` and `C`, `0x55FABB4F` for `~`. The
  decoder picks the constant matching the datagram size and falls back to trying the
  others.
- **Validation** — after decryption, the first four bytes must equal the magic
  `0x47375330` (`"G7S0"`). Anything else is counted as a decode error and dropped.

## Decoded fields

Every packet decodes to a typed structure with ~50 fields. The important ones:

| Offset | Field | Units / notes |
| --- | --- | --- |
| 0x04 | position X/Y/Z | m, GT7 world coordinates |
| 0x10 | velocity X/Y/Z | m/s |
| 0x2C | angular velocity X/Y/Z | rad/s (Y = yaw rate) |
| 0x38 | body height | m |
| 0x3C | engine RPM | rpm |
| 0x44 | fuel level / capacity | L (capacity 0 for EVs) |
| 0x4C | speed | m/s |
| 0x50 | boost | raw − 1.0 → bar |
| 0x54–0x5C | oil pressure, water temp, oil temp | bar, °C, °C |
| 0x60 | tire temps FL/FR/RL/RR | °C |
| 0x74 | current lap / total laps | total = 0 in time trial |
| 0x78 | best / last lap time | ms, −1 when unset |
| 0x80 | in-game time of day | ms |
| 0x84 | race position / total | |
| 0x88 | RPM alert min/max | shift-light thresholds |
| 0x8E | flags | bitmask, see below |
| 0x90 | current gear (low nibble) / suggested gear (high nibble) | 15 = neutral |
| 0x91 | throttle / brake | 0–255 → % via ÷2.55 |
| 0xA4 | wheel angular speed FL/FR/RL/RR | rad/s, signed |
| 0xB4 | tire radius FL/FR/RL/RR | m |
| 0xC4 | suspension travel FL/FR/RL/RR | m |
| 0xF4 | clutch, clutch engagement, RPM after clutch | |
| 0x104 | gear ratios (8) + transmission top speed | |
| 0x124 | car ID | maps to the car inventory (`app/data/cars.json`) |

**Flags bitmask** — `CAR_ON_TRACK` (bit 0), `PAUSED` (1), `LOADING` (2), `IN_GEAR` (3),
`HAS_TURBO` (4), `REV_LIMITER` (5), `HANDBRAKE` (6), lights (7–9), `ASM_ACTIVE` (10),
`TCS_ACTIVE` (11).

For storage, the aid flags are remapped into a stable, compact **aids bitmask** persisted
with every sample: `TCS=1`, `ASM=2`, `HANDBRAKE=4`, `REV_LIMITER=8`. This keeps recorded
laps immune to any future changes in GT7's flag layout.

### Wheel slip (computed at decode time)

GT7 doesn't send slip directly; it sends wheel angular speed and tire radius. Per wheel:

```
slip = |wheel_rad_per_s| × tire_radius / car_speed_mps
```

- `slip < 1` under braking → the wheel is turning slower than the car is moving —
  **locking**
- `slip > 1` under power → the wheel is spinning faster than the car — **wheelspin**
- Below 1 m/s car speed the ratio is meaningless, so all four are forced to `1.0`.

This is a slip-*ratio proxy* (wheel surface speed ÷ car speed), unsigned — not a signed
SAE slip ratio — but it is exactly what's needed to spot lockups and wheelspin.

## From packet to dashboard

Decoded packets flow through a bounded queue (600 packets ≈ 10 s) into a single
ordered consumer:

- if recording is on, the packet feeds [lap detection](lap-detection.md);
- the latest frame is broadcast to all WebSocket clients, throttled to `GT7_WS_RATE`
  (default **30 Hz**) — capture stays at 60 Hz regardless.

If the queue ever fills (slow disk), the **oldest** packet is dropped so the live view
never lags behind reality. Received/dropped/decode-error counters are visible in
`GET /api/status` and the Admin view.

!!! info "Protocol references"
    The GT7 telemetry format is community-reverse-engineered. Useful companion
    projects: [snipem/gt7dashboard](https://github.com/snipem/gt7dashboard) (Python
    dashboard this project reached parity with) and
    [MacManley/gt7-udp](https://github.com/MacManley/gt7-udp) (a C++ parser for
    ESP32 / ESP8266 boards with well-documented packet offsets).

## The simulated source

`GT7_SOURCE=sim` swaps the UDP listener for a synthetic source that emits the same
packet stream at exactly 60 Hz: a 3,200 m circuit, ~223 km/h baseline with two braking
zones, deliberate front lockups, rear wheelspin on slow launches, a kerb strike at ~21 %
of the lap, and TCS/ASM/rev-limiter activity. It uses a fixed random seed, so demo data
is reproducible.
