# Widget reference

Every widget available on the [overlay](overlay.md) grid and the
[driver dashboard](dash.md), with its styles, color rules, and how it behaves in each
track condition. Sizes are grid-cell footprints (w × h); the first listed size is the
default when a widget is added in the builder.

## Driving

### Gear — `gear`

| Style | Shows |
| --- | --- |
| Digit + suggested (default) | current gear, with `→ n` hint when GT7 suggests a downshift |
| Digit only | current gear, no hint |

Sizes 1×1 · 1×2 · 2×2 · 4×4. Special values: **R** = reverse, **N** = neutral. The
suggested-gear hint disappears when GT7 isn't suggesting one.

### Speed — `speed`

| Style | Shows |
| --- | --- |
| Digits (default) | speed in your units (km/h / mph, set in the header) |
| Bar | digits + horizontal bar, full scale 320 km/h |
| Gauge | 240° arc gauge, full scale 320 km/h |

Sizes 1×1 · 2×1 · 1×2 · 2×2 · 4×4.

### RPM — `rpm`

| Style | Shows |
| --- | --- |
| Bar (default) | fill vs the car's redline; turns red with a **SHIFT** cue at ≥ 95 % |
| Shift lights | 10-LED strip filling from 55 % of redline (green → amber → red), all LEDs flash on the limiter |
| Gauge | arc gauge in ×1000 rpm, red near the limit |
| Digits | raw rpm, red near the limit |

Sizes 2×1 · 4×1 · 2×2 · 4×2 · 4×4. The redline comes from the car itself
(`rpm_alert`), so the thresholds adapt per car.

### Throttle / brake — `inputs`

| Style | Shows |
| --- | --- |
| Horizontal bars (default) | throttle (green) over brake (red), 0–100 % |
| Vertical bars | side-by-side T/B columns |

Sizes 2×1 · 4×1 · 1×2 · 2×2.

### Steering wheel — `steering`

| Style | Shows |
| --- | --- |
| Wheel + angle (default) | a wheel graphic rotated by the broadcast steering angle, degrees readout below |
| Wheel only | the wheel, no readout |

Sizes 1×1 · 1×2 · 2×2 · 4×4. GT7 broadcasts the wheel's rotation in radians as an
**absolute angle** (packet **B** or wider — see
[Configuration](../getting-started/configuration.md)), so the graphic turns exactly as
far as the driver's wheel does; no lock-to-lock assumption is baked in. On a plain
packet-A stream the wheel dims and the readout shows `–`. The same widget renders a
stored lap on the Analysis view's [playback strip](analysis-view.md#lap-playback).

### Boost — `boost`

| Style | Shows |
| --- | --- |
| Digits (default) | manifold boost in bar, 2 decimals |
| Gauge | arc gauge, full scale 2.0 bar |

Sizes 1×1 · 2×1 · 2×2. Naturally-aspirated cars sit at ~0.

## Timing

### Lap times — `times`

| Style | Shows |
| --- | --- |
| Lap / best / last (default) | lap counter, session best (blue), last lap with its Δ vs the best before it (green/red) |
| Last lap (big) | last lap time large, Δ in the caption |
| Best lap (big) | best lap time large, in the accent color |

Sizes 2×1 · 2×2 · 4×2. `–:––.–––` until the first lap completes.

### Delta — `delta`

| Style | Shows |
| --- | --- |
| Big number (default) | the live gap, green (gaining) / red (losing), signed to 3 dp |
| Centered bar | the same number over a ± bar, full deflection at 2.0 s |

Sizes 1×1 · 2×1 · 2×2 · 4×4. **Live**: once a session-best lap exists, the value is
your gap *right now* — current elapsed time vs where the best lap was at the same
distance, updated every frame. Positive = slower. Falls back to the end-of-lap
comparison (caption *Δ best (last lap)*) when the live gap is unavailable — see
[track conditions](#track-conditions) below.

## Race

### Race position — `position`

| Style | Shows |
| --- | --- |
| Big (default) | `P n` with `/total` |
| Compact | one-line `P n/total` |

Sizes 1×1 · 2×1 · 2×2. Only meaningful in races (time trials report P1/1).

### In-game clock — `clock`

Single style: the in-game time of day (GT7's day progression), `HH:MM`. Useful in
endurance races with accelerated day cycles. Sizes 1×1 · 2×1.

### Race alerts — `alerts`

| Style | Shows |
| --- | --- |
| Banner (default) | stacked full-width callouts, most critical first |
| List | compact text lines |

Sizes 4×1 · 8×1 · 2×1 · 4×2 · 2×2. Severity styling: **critical** pulses red,
**warning** is amber, **info** is blue. Renders *nothing* while all is well — the cell
stays transparent in OBS. Every trigger:

| Alert | Severity | Fires when |
| --- | --- | --- |
| `FUEL n LAPS` | warning / critical | projected fuel < 3 laps / < 1.5 laps |
| `PIT THIS LAP` / `PIT NEXT LAP` | info | the projected pit lap is upon you (lapped races only, and only when the fuel doesn't last to the end) |
| `Water n °C` | warning / critical | > 110 °C / > 120 °C |
| `Oil n °C` | warning / critical | > 130 °C / > 140 °C |
| `OIL PRESSURE n bar` | critical | < 2.0 bar with the engine above 2000 rpm |
| `Tires hot n °C` | warning | any tire > 110 °C |

All alerts are suppressed while paused or off-track, so menus stay quiet.

## Car health

### Tire temps — `tires`

| Style | Shows |
| --- | --- |
| Temps (default) | 2×2 per-corner grid (FL FR / RL RR), °C |
| Temps + slip | the grid plus the combined slip ratio, red when > ×1.10 (wheelspin/lockup) |

Sizes 1×1 · 1×2 · 2×2 · 4×4. Color coding: **blue** < 55 °C (cold), **green**
55–95 °C (working range), **red** > 95 °C (overheating).

### Engine temps — `engine`

| Style | Shows |
| --- | --- |
| Water / oil (default) | water and oil temperature |
| Detailed | + oil pressure and boost |

Sizes 1×1 · 2×1 · 2×2 · 4×2. Values color amber/red at the same thresholds the alerts
fire at (water 110/120 °C, oil 130/140 °C, oil pressure < 2.0 bar under load). Oil
pressure shows `–` when the car doesn't report it.

### Driver aids — `aids`

Single style: TCS / ASM / HB / LIM badges that light while the aid is actively
intervening — **TCS**/**ASM** amber, **HB** (handbrake) blue, **LIM** (rev limiter)
red; dim when inactive. Sizes 2×1 · 1×1 · 4×1.

## Strategy

### Fuel — `fuel`

| Style | Shows |
| --- | --- |
| Percent (default) | tank percentage + mini bar, red below 15 % |
| Bar | wide bar with the percentage |
| Laps remaining | projected laps of fuel left, amber < 3 / red < 1.5 |

Sizes 1×1 · 1×2 · 2×1 · 2×2 · 4×2. *Laps remaining* needs at least one completed lap
with measurable fuel use (shows `–` until then).

### Fuel strategy — `strategy`

| Style | Shows |
| --- | --- |
| Summary (default) | laps of fuel left and `PIT ≤ L<n>` |
| Pit window | the pit-before lap large, amber when it's this lap or the next |

Sizes 2×1 · 2×2. Projections use the rolling average of the last 3 laps' fuel use;
until a lap completes both styles show *fuel: need a lap*.

## Track conditions

The telemetry flags every state the game can be in; this is how the widgets respond.
(GT7's UDP feed does **not** broadcast weather or track wetness, so conditions here
are the drive states the telemetry actually exposes.)

| Condition | What happens |
| --- | --- |
| **On track, driving** | everything live; the lap recorder samples at 60 Hz |
| **Paused** | recording and the lap timer freeze; the live delta shows its fallback; alerts are suppressed |
| **In menus (off track)** | same as paused — no samples, no alerts, delta falls back |
| **Replay** | streams like driving, and the widgets treat it that way — GT7 sends no replay flag, so nothing can tell a replay from your own lap. Laps record too: race replays through the normal path, a single-lap leaderboard replay via [replay salvage](../internals/lap-detection.md#replay-salvage) |
| **First lap of a session** | no reference lap yet: delta shows `–` (or *Δ best (last lap)* once a lap has completed but not yet been beaten into a reference), fuel projections show *need a lap* |
| **Session best set** | that lap's trace becomes the live-delta reference; a new best replaces it from the next lap on |
| **Past the reference's end** | near the finish line your lap can out-distance the best lap's recording; the delta blanks instead of showing an inflated value |
| **New session** (car change or lap counter reset) | best lap, delta reference, and strategy history reset; a session that never completes a lap is dropped |
| **Race finished** (`current_lap > total_laps`) | lap counter shows **FIN**; sampling stops |
| **Unlimited session** (`total_laps = 0`, e.g. practice / time trial) | lap counter shows just `n`; pit-window alerts stay off (no race end to strategize against) |
| **No telemetry** (game closed, connection lost) | after 3 s the overlay renders nothing (clean OBS source) and the dashboard shows *Waiting for telemetry…* with a red status dot |
| **Placeholder mode** (`demo=1`) | while telemetry is absent, an animated fake lap drives every widget (fuel drains so alerts fire); an amber tag/dot marks it, and real data takes over automatically |
