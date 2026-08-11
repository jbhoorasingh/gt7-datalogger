# Surface survey spike (findings note)

Phase 0 of the track-survey work ([#35](https://github.com/jbhoorasingh/gt7-datalogger/issues/35),
spike [#37](https://github.com/jbhoorasingh/gt7-datalogger/issues/37)): validate
the `surface_types` encoding and the wheel-contact derivation on a **real PS5**
before designing the per-track classification grid
([#38](https://github.com/jbhoorasingh/gt7-datalogger/issues/38)). The bundled
simulator proves nothing here — it only ever emits `T` and `C`.

**Status: template — run the spike on real hardware and fill in the tables.**

## How to run it

Open the **Survey** tab in the app (with the console on packet format C, the
default), pick the circuit being surveyed (defaults to the session's
auto-identified track) and press **Start survey**. The capture runs
server-side in the 60 Hz packet path (`app/processing/survey.py`) — the
browser's ~30 Hz live stream would miss single-tick transitions — so any
phone or tablet on the LAN works as the display while you drive. Lap
recording continues alongside: laps saved during the run keep their per-tick
surface column, and the JSONL records carry the session id and lap number so
both datasets join up offline.

Drive a few laps that deliberately touch every surface the track offers —
kerbs on both sides, painted run-off, grass, gravel traps, sand if the track
has it. The view shows the live per-wheel surface, the char histogram, a
loud banner for any char the mapping doesn't know, and each transition's
derived wheel-contact points on a scatter map. **Download JSONL** grabs the
full transition log (position, velocity, raw orientation floats, per-wheel
contact points) for offline analysis; the server also keeps it next to the
database as `data/surface_survey_<timestamp>.jsonl`.

Suggested tracks for coverage: one kerb-heavy road course (e.g. Suzuka or
Interlagos), one with gravel traps (Monza / Red Bull Ring), one dirt track
(Fisherman's Ranch) and, if snow chars are real, Lake Louise.

## 1. Surface enum (fill in)

The decoder ([`packet.py`](https://github.com/jbhoorasingh/gt7-datalogger/blob/main/backend/app/telemetry/packet.py))
reads 4 ASCII chars, one per wheel, FL FR RL RR. Community docs claim
`T / C / D / G / S / s`; `app/processing/surface.py` maps exactly those and
flags anything else as `SURFACE_OTHER`.

| Char | Assumed meaning | Confirmed on PS5? | Where seen (track / feature) |
|------|-----------------|-------------------|------------------------------|
| `T`  | tarmac          | ☐                 |                              |
| `C`  | curb / kerb     | ☐                 |                              |
| `D`  | dirt            | ☐                 |                              |
| `G`  | grass           | ☐                 |                              |
| `S`  | sand            | ☐                 |                              |
| `s`  | snow            | ☐                 |                              |
| ?    | *(new chars the spike reports)* | | |

Open questions to answer while driving:

- Does **painted** run-off / painted kerb flat report `T`, `C`, or something new?
- Gravel trap vs dirt vs sand — distinct chars or all one?
- Is the per-wheel ordering really FL FR RL RR? (Clip one specific wheel over
  a kerb and check which position flips.)
- **Does GT7 broadcast its own track-limits judgment anywhere?** No packet
  field is documented for it; the only place one could hide is the four
  undocumented upper bits of the 16-bit flags field. The survey raises a
  banner if any of bits 12–15 ever activate, and every transition record
  carries the raw flags — trigger an in-game track-limits warning (time
  invalidated / penalty) during the run and check whether a bit correlates.

## 2. Wheel-contact derivation (fill in)

Contact point = car position + heading rotation of (±wheelbase/2, ±track/2).
Wheelbase is broadcast in packet C; **track width is not broadcast directly
— but it is derivable, and every corner derives it.** The outer wheels of an
axle cover a larger arc than the inner ones, so their rolling speeds differ
by exactly the yaw rate times the axle track:

```
|v_outer - v_inner| = |yaw rate| * track_width       v = wheel_rps * tire_radius
```

`wheel_rps`, `tire_radius` and `angular_velocity_y` are all broadcast and
were already decoded, so this costs nothing and needs no special driving. It
reached a trusted 1.74 m within ~12 seconds of ordinary laps on real
hardware. Taking magnitudes means GT7's yaw sign convention never has to be
pinned down.

Two things learned doing it on hardware:

- **A locked/spool differential makes its axle useless here.** The test car's
  rear wheels report identical speeds to the centimetre even coasting
  (`-82.31 / -82.31` at zero throttle), so the rear axle answers ~0. Both
  axles are therefore offered each tick and the plausible range picks the
  free one — no drivetrain layout ever has to be declared, and a locked axle
  self-rejects.
- **Braking corrupts it; throttle does not.** ABS modulates wheels
  individually: the same capture that gave a steady 1.7–1.8 m produced 1.22,
  2.03 and 4.87 m under brake pressure. Throttle needs no gate, because
  wheelspin lifts an axle's *mean* off the car's speed and is caught by the
  slip check — gating throttle would discard most of a racing lap.

The older fallback still exists: ride all four wheels over one edge and back.
The same wheel's out/back crossings pin the edge's direction, opposite-side
crossings of the same line fix the width, and remaining same-side crossings
must agree the points are collinear (which rejects two-edged strips, curved
kerbs and mid-corner crossings). It is exact when it fires, but it demands a
deliberate manoeuvre and across a full real session of heavy edge riding it
accepted **zero** samples — which is why cornering outranks it. The status
line names whichever is in force, and every JSONL record carries the `tw_m`
it was derived with.

**Scale check, before anyone plans a backfill:** the measured 1.74 m against
the 1.6 m assumption is a 0.14 m width error, so points laid under the
assumption sit **7 cm** off laterally — 7% of the 1 m dedup cell. Recording
`tw` per point keeps correction possible, but at this magnitude the grid
cannot represent the correction and re-deriving old points is not worth
doing. It would take a width error above ~2 m to move a point a full cell.
Heading comes from ground-plane velocity; the raw rotation floats +
`rel_orientation_to_north` are logged for offline comparison.

Method: drive slowly over a kerb whose edge is visible on the race-line map,
one wheel at a time, from both directions. The transition record pins where
GT7 thinks the wheel met the kerb; measure the offset between the derived
contact point and the kerb edge across passes.

| Question | Finding |
|----------|---------|
| Is the position field the car's midpoint (or an axle/CoG)? | |
| Velocity-heading vs `rel_orientation_to_north` agreement | |
| Right-vector sign `(f_z, -f_x)` correct, or mirrored? | |
| Positional error at ~1.6 m assumed track width (narrow car) | ± __ m |
| Positional error at ~1.6 m assumed track width (wide car, e.g. Gr.3) | ± __ m |
| Lag between visible contact and the char flipping | __ ticks |

## Border tagging and the paved-runoff problem

Transitions where one side's wheels touch kerb/loose while the whole other
side stays on tarmac are tagged `border: L/R` (relative to travel direction;
left/right stays consistent lap after lap where inner/outer would swap at
every corner). The survey map draws each border contact as a short edge
tick along the local travel direction (blue = left, pink = right) — the two
perimeters trace themselves — and wherever a left point has a right point
directly across from it (perpendicular to travel, plausible road width
apart, same direction of driving), the span between them is filled as
confirmed road.

Edge points accumulate **server-side for the whole run** and stream to the
map incrementally, so the track genuinely appears lap by lap — reloading the
page or surveying for an hour loses nothing (50k-point backstop; the JSONL
always has everything).

Track knowledge also outlives the run: each circuit's perimeter evidence and
finish crossings merge into a **track bundle**
(`data/track-bundles/<slug>.json`, one record per meter per side so it
converges instead of growing). A new survey on the same circuit resumes from
its bundle — the map opens with everything ever mapped — and saves back on
stop and on circuit changes (a run's evidence is flushed and cleared when
the label changes, so one circuit can never pollute another's bundle).
Bundles are versioned, self-describing documents downloadable via
`/api/track-bundles/{slug}`, designed to graduate into their own repo and be
imported at build time like `data/tracks.json`. Width calibration stays out:
it belongs to the car, not the circuit.

A meter of border is **one fact, voted on** (format v2). Each record carries
`votes[kind] = [count, last_run]`, and the kind it resolves to follows one
rule: **hand-marked kinds beat inferred ones outright**, majority inside
each tier. That is not a tie-break preference — the surface chars are
*blind* to walls and paved run-off (both read as plain `T`), so an
auto/straddle point at a marked meter is not evidence against the mark, only
evidence that the char stream could not see it. Majority within the manual
tier is the way back from a mis-mark (mark it correctly twice and it wins).

Format v1 keyed on kind as well, so contradictions were stored side by side
instead of resolved, and the consumer kept both: it drops `runoff` points
from the road fill, but the co-located twin survived the filter and held the
meter in the road anyway. Measured on the author's real bundles, v1 →v2
found 892 of 4634 contested cells at Lago Centre (19%), including **105
meters where a hand-marked run-off limit had been silently overruled** by an
auto/straddle point. Bundles upgrade in place on load, voting everything v1
recorded as run 0 so the next real run outranks it.

Votes count **runs, not samples** — the ~60 s autosave re-merges the same
run's evidence repeatedly, and without the run stamp a long session would
inflate its own votes by however many times it happened to autosave.
(Open for [#40](https://github.com/jbhoorasingh/gt7-datalogger/issues/40):
run ordinals are local to one installation, so merging two people's bundles
needs a source id before these counts mean anything across them.)

Records also carry the provenance needed to second-guess them: `run` (which
run first evidenced the meter) and `tw` (the axle track width in use when it
was laid). `tw` earns its bytes because straddle points — 52% of Lago
Centre, 88% of East End — sit at ±tw/2 from the car centre and carry the
whole width-estimate error; recording it keeps open the option of correcting
their lateral offset offline once a better width is known. Position itself
stays first-seen, which keeps file diffs small.

The **Track completeness** card answers "is it ready?": per-border coverage
of the driven loop (percent + the largest remaining gap, i.e. where to
drive next), road coverage where both borders are known, and the finish
line — located from lap rollovers (GT7 increments the lap counter exactly
on the line), drawn dashed after one crossing and solid once repeat
crossings agree within meters.

**Straddle tracing** is the workhorse: surface flips only pin the border at
crossing moments, so a lap driven with one side's wheels HELD off the track
would otherwise leave the border untraced between them. Instead, whenever
both wheels of one side sit off the tarmac while the other side is fully on
it, the survey samples a border point from the off-side wheel line every
~2 m automatically. Survey recipe: one lap hugging the left edge with the
left wheels just off, one lap mirrored on the right — both perimeters trace
themselves continuously and the road fill appears between them.

**Manual boundary marking** covers what surface chars cannot see: arm
*Mark boundary: left/right* + a kind (*edge*, *run-off limit*, *wall*) and
drive along the boundary — the survey records an edge point from that side's
wheel line every ~2 m. Wall-lined track produces no surface transitions at
all, and the outer limit of paved run-off reads as plain tarmac, so the
driven line is the only reliable source for both. Marked points render in
their own colors (purple = run-off limit, red = wall), are stamped into the
JSONL as `mark` records, and run-off limits are excluded from the road fill
(they bound the run-off, not the road).

**Raw packet inspector**: the collapsible panel at the bottom of the Survey
view shows every decoded packet field live (2 Hz), highlighting values that
changed between samples — drive over anything interesting and call out what
moves. Single-tick blips can slip between samples; the undocumented flag
bits are watched server-side at 60 Hz regardless.

**Paved runoff reads as plain `T`**, indistinguishable per tick from the
racing surface. Things to establish on hardware, in order of leverage:

1. **Does paint get its own char?** If the white boundary line or painted
   run-off reports something other than `T`, the track edge is directly
   observable and the problem mostly disappears. (Open question above.)
2. If not: runoff tarmac is still *topologically* separated from racing
   tarmac by the kerb/paint/loose band the border-tagged points trace. A
   live per-wheel "crossed the band vs bounced back" verdict is genuinely
   ambiguous (driving straight across an angled kerb produces zero lateral
   displacement in the car frame — there is no local signal to project
   against), so the honest solution is offline in the grid build (#38):
   flood-fill tarmac cells from the racing corridor; tarmac regions
   reachable only across a border band are runoff.

## 3. Verdict → survey grid design (fill in)

- Error bound on a wheel-contact sample: **± __ m** (lateral), **± __ m**
  (longitudinal).
- Recommended survey grid cell size for #38: **__ m** (expected 0.25–0.5 m —
  it should comfortably exceed the error bound above).
- Surface chars worth separate grid classes: ______
- Encoding changes needed in `app/processing/surface.py`: ______
