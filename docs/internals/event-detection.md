# Chassis event detection

When a lap is saved (and when one is imported), the sample series is scanned once for
four kinds of chassis events. They are stored with the lap, shaded onto the analysis
charts, counted in the Sessions table (`2L·1S·4B·1K`), and summarized in the tuning
panel.

Each event records its type, start/end distance, the wheels involved, and a severity
score.

## Lockup

A wheel turning slower than the car is moving, under braking:

- **Active tick**: `brake ≥ 20 %` **and** any wheel's slip `< 0.9`
- **Minimum duration**: 6 consecutive ticks (~0.1 s) — filters single-tick noise
- **Wheels**: every wheel that crossed the threshold anywhere in the run
- **Severity**: the *minimum* slip seen during the run (lower = harder lock; 0 = fully
  locked)

## Wheelspin

A driven wheel overspeeding under power:

- **Active tick**: `throttle ≥ 40 %` **and** any wheel's slip `> 1.1`
- **Minimum duration**: 6 consecutive ticks
- **Severity**: the *maximum* slip seen during the run (higher = more spin)

## Suspension bottoming

Absolute suspension travel varies by car and tune, so bottoming is detected on the
lap-normalized range per wheel:

- compute that wheel's min/max travel over the lap; skip if the range is ~0
- **Active tick**: travel within the top **2 %** of the lap's range
  (`≥ min + 0.98 × range`)
- **Minimum duration**: 3 consecutive ticks
- **Severity**: how far into the range the peak reached (0–1)

## Kerb strike

A kerb shows up as a single-tick spike in suspension travel rather than a sustained
compression:

- for each tick, compare against the average of its neighbors two ticks away:
  `neighbors = (travel[i−2] + travel[i+2]) ÷ 2`
- **Fires** when `travel[i] − neighbors > 35 %` of the lap's travel range
- Recorded as a zero-length event (`start_dist = end_dist`)

## Caps and robustness

- At most **40 events per type** are stored per lap, so a spin-fest can't bloat the
  database.
- Laps imported from old (v1) files that lack per-corner columns simply produce no
  events instead of erroring; events are **recomputed** on import when the data is
  present.

## How events are displayed

- **Analysis charts** — each event shades the panel that explains it: lockups on the
  Brake panel, wheelspin on Throttle, bottoming and kerbs on the suspension panels. The
  band is tinted in the lap's color at 14 % opacity, and single-tick kerb strikes are
  widened to a minimum of 2 m so they stay visible.
- **Driver aids** — TCS-active stretches shade the Throttle panel and ASM-active
  stretches shade the Speed panel, using the same mechanism.
- **Race line map** — a marker in the lap's color where each event began, and rings on
  the samples where TCS or ASM was active; see
  [Events and driver aids](../guide/analysis-view.md#events-and-driver-aids).
- **Sessions table** — the compact per-lap code `2L·1S·3B·1K` = 2 lockups, 1 wheelspin,
  3 bottomings, 1 kerb strike.
- **Corner Detail widget** — live `LOCK` / `SPIN` badges are re-derived per corner as
  you scrub, using the same thresholds (brake ≥ 20 % & slip < 0.9; throttle ≥ 40 % &
  slip > 1.1).
