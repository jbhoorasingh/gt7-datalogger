# Lap file format

Laps export and import as JSON (**Sessions → json** / **Import lap…**) so they can be
shared or backed up, and export as CSV for MoTeC i2 or Excel.

## JSON export (v2)

```json
{
  "format": "gt7-datalogger-lap",
  "version": 2,
  "lap": {
    "number": 4,
    "time_ms": 92450,
    "car_id": 3298,
    "fuel_start": 42.1,
    "fuel_end": 40.3,
    "...": "per-lap metrics, engine health, tod_ms",
    "counts_for_best": false,
    "full_lap": true,
    "best_override": false,
    "exclude_reason": "contact",
    "salvaged": false,
    "events": [ { "type": "lockup", "start_dist": 812.4, "end_dist": 818.0,
                  "wheels": ["fl"], "severity": 0.71 } ],
    "gearing": { "ratios": [3.21, 2.44, 1.88, 1.51, 1.24, 1.03],
                 "top_speed": 289.0, "rpm_alert": 7500 },
    "samples": { "t": [...], "dist": [...], "speed": [...], "...": "..." }
  }
}
```

`counts_for_best` says whether the lap counts toward bests; `full_lap` is the
[span guard's](../internals/lap-detection.md#best-lap-tracking) verdict and
`best_override` the user's ruling on top of it (`null` = none), with
`exclude_reason` saying why when the ruling is an exclusion — one of `off-track`,
`contact`, `restart`, `dirty`, `pit-out`. `salvaged` marks a lap recovered from a
[replay ending](../internals/lap-detection.md#replay-salvage). All five are additive;
the format version is unchanged.

The `samples` object holds the **full 60 Hz series** as parallel arrays, one per
channel — see [Derived channels & metrics](../internals/derived-channels.md) for the
complete column list with formulas and units (~28 columns: time, distance, speed,
inputs, gear, RPM, boost, per-wheel slip, per-corner tire temps and suspension travel,
world position, fuel, driver-aids bitmask, …). `t` and `dist` both start half a frame
gap past the line ([why](../internals/lap-detection.md#where-a-lap-begins)); files
written by earlier versions start `t` at exactly 0 and are converted when imported (and
when exported again).

### Importing

`POST /api/laps/import` (or the **Import lap…** button) accepts the envelope above:

- If no session is active, an `imported` session is created to hold the lap.
- **Events and aid-usage metrics are recomputed** from the samples on import (so
  imports benefit from detector improvements), while engine-health aggregates and
  gearing are carried over verbatim.
- **The clock anchor is brought up to date**: a file from before the half-gap anchor
  (`t` from exactly 0) is converted on import, like a stored lap is on read.
- **Verdicts are carried, not re-derived**: `salvaged`, `full_lap` and any
  `best_override` / `exclude_reason`. The span guard needs the rest of the session to
  judge a lap, which an import doesn't have — so a pit out-lap stays partial and an
  excluded lap stays excluded. Files written before these fields existed fall back to
  `counts_for_best`, which was the span guard's verdict at the time.
- **v1 files** (from older versions) import cleanly — the newer per-corner channels are
  simply absent and the charts skip them; events stay empty since the columns they need
  aren't there.

## Session archive (ZIP)

`GET /api/sessions/{id}/export.zip` (**Sessions → Export session**) packs a whole
session into one file:

```text
gt7-session-12/
  session.json
  laps/lap-001-345.json
  laps/lap-002-346.json
```

Each `laps/` file is the JSON export above, unchanged — lap number first so a file
browser lists them in driving order, lap id after it because a session can hold two
laps with one number (GT7 re-reporting a lap after a rewind). Every one of them
imports on its own with **Import lap…**.

`session.json` describes what they belong to:

```json
{
  "format": "gt7-datalogger-session",
  "version": 1,
  "exported_at": "2026-09-16T16:38:23+00:00",
  "session": { "id": 12, "car_name": "...", "track_name": "...", "tags": ["wet"],
               "note": "...", "bests_excluded": false, "final_position": 3, "...": "..." },
  "laps": [ { "file": "laps/lap-001-345.json", "id": 345, "number": 1,
              "time_ms": 92450, "counts_for_best": true } ]
}
```

`session` is the row exactly as `GET /api/sessions` lists it. Importing a whole
archive back — recreating the session rather than adding its laps to the current
one — is not supported yet; import the lap files one at a time for now.

## CSV export (MoTeC-compatible)

`GET /api/laps/{id}/export.csv` — a CSV that MoTeC i2's *CSV file* import (and Excel)
understands:

- Header rows: `Format`, `Device`, `Vehicle` (the car name), `Comment`, `Log Date`, and
  `Sample Rate: 60.000`
- Then a channel-name row and a unit row, followed by one row per tick

27 channels with explicit units: Time (s), Distance (m), Ground Speed (km/h), Throttle
Pos (%), Brake Pos (%), Gear, Engine RPM (rpm), Boost Pressure (bar), Tyre Slip Ratio,
Yaw Rate (rad/s), Pos X/Z (m), Ride Height (mm), Fuel Level (L), Tyre Slip FL/FR/RL/RR,
Tyre Temp FL/FR/RL/RR (C), Susp Travel FL/FR/RL/RR (mm), and Driver Aids (bitmask).

!!! tip
    In MoTeC i2, create a new workspace, then *File → Import* and choose the CSV. The
    distance channel makes distance-based overlays work the same way they do in the
    built-in Analysis view.
