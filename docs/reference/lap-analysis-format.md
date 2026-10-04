# Lap analysis format

A lap is stored and exported as its full 60 Hz recording — a megabyte or two of
numbers. That is the right shape for a chart and the wrong one for anything that has
to *reason* about the driving. The **lap analysis document** is the same session as a
few hundred labelled numbers: for every lap and every corner, where the brake went on,
how slow the car got, when the throttle came back, what line it took and what all of
that cost against the session's best lap.

One JSON document per session, served by `GET /api/sessions/{id}/analysis.json`
(**Sessions → Export ▾ → Session analysis**) and included in the
[session archive](lap-file-format.md#session-archive-zip) as `analysis.json`. With
the `sessions` [sync](../guide/settings.md#sync) on, it is also sent to the sync service when
a drive ends, if the service says it takes it. It is compiled from the stored laps
each time it is asked for and never stored itself: a lap ruled in or out of the bests
changes the reference lap, and with it every figure.

Format `gt7-datalogger-lap-analysis`, current version **1**. A ten-lap session on a
fifteen-corner circuit is about 100 KB. It holds no samples, no world positions and
no resampled series — all of that is in the lap files.

## How it is measured

- **Against a reference lap**: the fastest lap of the session that counts towards the
  bests. `reference` names it. A session with no counting lap has `reference: null`
  and its laps carry their own figures only.
- **On the reference lap's distance axis.** A lap's own distance is integrated from
  its own speed, so two laps reach the same metre mark at different places on the
  road. Every lap is lined up with the reference by *where it was*
  ([how](../internals/analysis-math.md)); a lap that could not be has
  `aligned: false`, keeps its own distance and has no `line`.
- **Per corner, through the reference's corner windows.** The corners are the
  circuit's authored ones when it has them — corner 5 is then corner 5 in every
  session — and the ones detected on the reference lap otherwise. `circuit.corners`
  says which: `authored`, `detected` or `none`. **Detected corner numbers are not
  comparable between sessions**: they are numbered on one lap, and a different
  reference lap may find a different set.
- **With one definition of each measurement.** Where a lap braked for a corner is the
  answer the Race Engineer speaks; time through a corner is the Analysis view's
  report card. See [what is measured at a corner](#what-is-measured-at-a-corner).

## Conventions

The document repeats these in its own `conventions` block, so it can be read without
this page.

| | |
| --- | --- |
| distances | metres along the reference lap's driven line |
| `*_from_apex_m` | metres from the corner's apex; **negative is before it** |
| `*_vs_ref` | this lap minus the reference: a positive **time** is time lost, a positive **speed** is speed gained, a positive **distance** is later on the road (braking deeper) |
| `line` | metres off the reference lap's line at the same place; **positive is tighter** (towards the inside of the corner) |
| `edges` | metres from the car's centre to the surveyed road edge |
| `car.stock` | the car **as it leaves the showroom**; a tuned or balanced car differs, and its own figures are not recorded |
| speeds, times, pedals, temperatures | km/h, ms, percent of travel, °C |
| an absent key | the lap did not do the thing, or the recording cannot say — **never zero** |

The reference lap's own entry carries no `*_vs_ref` keys and no `line`.

## The document

The example is put together to show every key. A real session has the ones that
apply to it: no `race` outside a race, no `sections` on a circuit with none authored,
no corner names where nobody has named the corners.

```json
{
  "format": "gt7-datalogger-lap-analysis",
  "version": 1,
  "compiled_at": "2026-09-26T18:04:11.532+00:00",
  "app_version": "0.7.0",
  "conventions": { "distance": "metres along the reference lap's driven line", "...": "..." },
  "session": { "id": 194, "started_at": "2026-08-30T20:16:50+00:00", "laps": 7,
               "counting_laps": 6, "tags": ["race"], "note": "...",
               "race": { "position": 3, "of": 16, "laps": 7, "time_ms": 931872 } },
  "car": { "id": 3600, "name": "911 GT3 R (992) '22", "category": "GR3",
           "manufacturer": "Porsche", "year": 2022, "drivetrain": "RR",
           "stock": { "aspiration": "NA", "displacement_cc": 4194, "power_bhp": 557,
                      "torque_kgfm": 51.0, "weight_kg": 1250,
                      "performance_points": 731.91 },
           "gearing": { "ratios": [3.046, 2.198, 1.715, 1.403, 1.203, 1.082],
                        "rpm_alert": 10000.0 } },
  "circuit": { "name": "Mount Panorama Motor Racing Circuit", "official_id": "ec1eb6",
               "lap_length_m": 6168.1, "corners": "authored", "sections": "none",
               "surveyed": true },
  "reference": { "lap_id": 940, "number": 5, "time_ms": 131077, "scope": "session",
                 "why": "the fastest lap of this session that counts towards the bests" },
  "corners": [ { "n": 2, "name": "Griffins Bend", "direction": "R", "entry_m": 1294.3,
                 "apex_m": 1369.3, "exit_m": 1444.3, "angle_deg": 91.8 } ],
  "sections": [ { "n": 1, "name": "Conrod Straight", "start_m": 4020.5, "end_m": 4950.0,
                  "length_m": 929.5 } ],
  "laps": [ "... one entry per lap, below ..." ],
  "consistency": { "...": "below" }
}
```

| Key | |
| --- | --- |
| `session` | what was driven: lap counts, the note and tags, and the race result when it was a race |
| `car` | see [where the car's figures come from](#where-the-cars-figures-come-from) |
| `circuit` | `lap_length_m` is the reference lap's. `surveyed` says whether the road's edges are known, which is what `edges` needs |
| `reference.scope` | `session` — the reference is this session's best. A best across sessions (#26) will be a second scope |
| `corners` | where each corner is on the reference lap. `crosses_start: true` marks a corner stitched across the start line: it is timed, but has no braking, turn-in, throttle, line or approach |
| `sections` | the circuit's named stretches (straights, mostly), where it has any |

### Where the car's figures come from

Three sources, and the keys are laid out so that they cannot be mistaken for one
another:

| Keys | Source | What it describes |
| --- | --- | --- |
| `id`, `name` | the car id GT7 broadcasts, named from the car inventory | the car driven |
| `category` | GT7's telemetry, where the session's laps carried one (`GR3`, `GR4`, `GRN`, `GRX`…, as broadcast); the inventory's otherwise (`Gr.3`, `Gr.N`…) | the class the car ran in |
| `manufacturer`, `year`, `drivetrain` | the **car inventory**: Polyphony's own published car list, shipped with the app and refreshed from gran-turismo.com | facts about the car that no tuning changes |
| `stock`: `aspiration`, `displacement_cc`, `power_bhp`, `torque_kgfm`, `weight_kg`, `performance_points` | the car inventory | the car **as it leaves the showroom** |
| `gearing` | GT7's telemetry, from the session's first lap that carried it | the gearbox **as it was set up** for the session |

GT7 does not broadcast a car's power, weight or Performance Points, so the app never
knows them for the car as it was driven. Everything under `stock` is the showroom
car's: for one that was tuned, ballasted, given another engine or run under Balance
of Performance, the session's own figures are different and are not in the document.
Read `stock` as "what kind of car this is", not as what it had on the day.

The gear ratios and `rpm_alert` *are* the session's own; `rpm_alert` is what the
upshifts in each lap's `shifts` are to be read against.

A key is absent where its source has no answer: a car that is not in the inventory
has an `id`, a `name` and a `category` only, an electric car has no
`displacement_cc`, and a lap recorded before gearing was captured has no `gearing`.

The inventory's answers are written onto a session as it opens, and filled in on
stored sessions whenever a newer inventory is loaded. An install that still pins the
old two-column `GT7_CARS_CSV` has names and nothing else, as it does everywhere in
the app.

### A lap

```json
{
  "id": 936, "number": 1, "time_ms": 135929, "time_vs_ref": 4852,
  "counts_for_best": true, "clean": true, "aligned": true, "race_position": 17,
  "fuel": { "start": 99.63, "end": 86.52, "used": 13.108 },
  "pedals": { "full_throttle_pct": 52.4, "full_brake_pct": 2.3, "coasting_pct": 5.5 },
  "aids": { "tcs_pct": 3.8, "asm_pct": 0.0 },
  "tire_spin_pct": 0.0, "max_speed": 313.4,
  "tyre_temp": { "fl": { "avg": 65.7, "max": 76.5, "end": 62.6 }, "fr": {}, "rl": {}, "rr": {} },
  "events": { "lockup": 5, "wheelspin": 2, "bottoming": 4 },
  "off_track": 0, "off_survey": 1,
  "shifts": [ { "from_gear": 2, "count": 10, "rpm_avg": 8668, "rpm_min": 7994, "rpm_max": 9128 } ],
  "corners": [ "... below ..." ],
  "sections": [ "... below ..." ],
  "to_line": { "length_m": 197.7, "time_ms": 4106.1, "time_vs_ref": -165.3, "max_speed": 205.6 }
}
```

| Key | |
| --- | --- |
| `reference` | `true` on the reference lap, absent on every other |
| `counts_for_best` | `false` for a partial lap, a detected race opening lap (`exclude_reason: "race-start"`), or one ruled out by hand; `exclude_reason` says why when a reason was given |
| `clean` | `true` / `false`, or `null` when neither GT7's surface flags nor the survey could judge the lap |
| `off_track`, `off_survey` | excursions counted by GT7's surface flags and by the surveyed edges; **absent** when that judge had nothing to go on |
| `shifts` | each gear's upshifts over the lap, and the engine speed they were made at |
| `to_line` | from the last corner's exit to the finish |

### A corner of a lap

```json
{
  "n": 2, "time_ms": 5354.7, "time_vs_ref": 769.1,
  "speed": { "entry": 124.4, "min": 91.2, "exit": 112.9, "min_vs_ref": -1.3,
             "exit_vs_ref": -24.2, "min_from_apex_m": -44.4, "min_gear": 1 },
  "braking": { "on_from_apex_m": -127.5, "on_vs_ref": 37.5, "length_m": 73.7,
               "off_from_apex_m": -53.8, "peak_pct": 100.0, "speed_on": 200.6,
               "speed_off": 95.2, "lockups": 1 },
  "turn_in": { "from_apex_m": -61.8, "brake_pct": 55.7, "trail_m": 8.0 },
  "throttle": { "flat": false, "on_from_apex_m": -44.4, "full_from_apex_m": 80.4,
                "wheelspin": 1, "tcs_pct": 87.6 },
  "line": { "entry_m": 0.9, "apex_m": 1.82, "exit_m": 1.33 },
  "edges": { "entry": { "inside_m": 2.78, "outside_m": 3.7 },
             "apex": { "inside_m": 0.26, "outside_m": 8.87 },
             "exit": { "inside_m": 2.54, "outside_m": 5.93 } },
  "approach": { "length_m": 988.2, "time_ms": 16237.9, "time_vs_ref": 634.1,
                "max_speed": 278.9 }
}
```

Read: this lap braked 37.5 m **later** than the reference and was still 1.3 km/h
slower at the slowest point, got back to full throttle 80 m past the apex with
traction control working most of the way out, left the corner 24 km/h down and lost
0.77 s in it — and another 0.63 s on the straight before it.

A corner the lap did not drive the whole of (a partial lap) is left out of its list.

| Block | |
| --- | --- |
| `time_ms` | time through the corner's window, entry to exit |
| `speed` | at the entry, at the slowest point and at the exit; where the slowest point was, and the gear there |
| `braking` | the braking zone: where it began and ended, how long and how hard, the speed at each end, and lockups in it. **Absent** when neither this lap nor the reference braked for the corner; `{"none": true}` when the reference did and this lap did not. `on_from_apex_m`, `on_vs_ref` and `length_m` are absent when the brake was already on at the lap's first sample |
| `turn_in` | where the car began to turn, the brake pedal at that moment, and `trail_m` — how far the brake was carried past it (0 when it was released first). Absent when the corner runs on from the one before |
| `throttle` | `flat: true` when the throttle never came off (no pick-up points then); otherwise where it came back and where it reached full, wheelspin on the way out, and `tcs_pct` — the share of the apex-to-exit stretch with traction control acting |
| `line` | lateral offset from the reference lap's line at the corner's three marks |
| `edges` | road left on each side at the three marks, on surveyed circuits. A mark is absent where the survey has a gap |
| `approach` | the road between the corner before and this one. What a poor exit costs is lost here, not in either corner's window |

The corners, the approaches and `to_line` tile the lap: on a lap driven whole, on a
circuit with no corner across the start line, their `time_vs_ref` add up to the lap's
to within a tick or two of the recording (a few tens of milliseconds). A race's first lap is the exception — it began on the
grid, not at the line.

### A section of a lap

```json
{ "n": 1, "time_ms": 12401.6, "time_vs_ref": 85.2, "entry_speed": 171.3,
  "top": { "speed": 298.4, "from_end_m": -12.0, "gear": 6, "rpm": 8420 },
  "end": { "speed": 291.0, "gear": 6, "rpm": 8212 } }
```

`top` is the speed trap: the fastest the car went in the section, where, and what the
engine was doing. `end` is the same at the section's end.

### Consistency

```json
{
  "lap_time": { "laps": 6, "best_ms": 131077, "median_ms": 133112, "std_ms": 2082.5,
                "pct": 1.564 },
  "corners": {
    "basis": "clean", "laps": [1, 2, 3, 4, 5, 6],
    "units": { "brake_on": "m", "min_speed": "km/h", "time": "ms" },
    "by_corner": [ { "n": 1,
                     "brake_on": { "laps": 6, "std": 9.3, "spread": 27.2 },
                     "min_speed": { "laps": 6, "std": 4.9, "spread": 13.0 },
                     "time": { "laps": 6, "std": 247.4, "spread": 723.5 } } ]
  }
}
```

- `lap_time` is the Sessions view's consistency figure, over the same laps: every lap
  that counts towards the bests, three at least.
- `corners` is each corner's spread — sample standard deviation, and highest minus
  lowest — over laps that count, were lined up with the reference and were driven
  cleanly. `basis` is `clean` when three or more such laps were verified clean. When
  fewer were, the laps no judge could rule on stand in and `basis` is `counting`; a
  lap known to have left the road is never used.
- `null` when the session has too few laps for either.

## What is measured at a corner

These are `app/processing/corner_metrics.py`'s definitions, shared with the
[Race Engineer](../internals/race-engineer.md#coaching).

| Measurement | Definition |
| --- | --- |
| Brake application | the pedal at or above **20 %** for at least **0.1 s**. Two applications less than **10 m** apart are one |
| Braking zone | each application belongs to one corner: the first apex its **midpoint** has not yet reached, provided it began no more than **250 m** before that corner's entry. A corner's braking zone is the application, of those, that took the most speed off |
| Turn-in | walking back from the corner's peak yaw rate, the point where the yaw rate first reaches **25 %** of that peak (never below 0.03 rad/s), looked for no further than **200 m** before the apex and not past the apex before |
| Minimum speed | the lowest speed between the corner's entry and exit |
| Throttle pick-up, full throttle | after the slowest point, the first sample at or above **20 %** and at or above **98 %**, looked for up to **100 m** past the exit and never past the next apex |
| Flat | the throttle never came under 20 % between the braking zone's start (or the entry) and the slowest point |

## Versioning

`version` is bumped when a key changes meaning or is removed. Keys may be **added**
without a bump, so a reader should ignore what it does not know. Absent keys are part
of the format at every version.
