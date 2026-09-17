# Lap comparison math

The Analysis view compares laps of different lengths and speeds on one set of axes.
This page explains the alignment, delta, consistency, and map math behind it.

## Lining laps up — by place on track

Laps are compared **by where they were on track**, on one shared axis: the
**reference lap's own distance**.

A lap's `dist` is integrated from its own speed, so two laps reach the same metre mark
at different places — a wider line is a longer lap, and a slide or a run of dropped
frames moves the car further than its speed says. Measured over 141 real lap pairs,
"the same distance" was a median 2.9 m and a p99 64 m apart on track, so a delta taken
at equal distance compared the laps at different points of a corner.

So every other lap is walked along the reference lap's driven path
(`app/processing/alignment.py`):

1. The reference's positions are resampled every **2.5 m** of its distance into a
   polyline (a chord that short is 8 cm off the arc in a 10 m-radius hairpin).
2. Each sample of the other lap is projected onto that polyline — the perpendicular
   foot, as a distance along the reference — searching only **5 m behind to 10 m ahead**
   of the previous sample's answer (plus twice the lap's own advance since, which
   covers dropped frames). A circuit that crosses itself, like Suzuka, can't capture the
   car on the wrong branch.
3. When the best answer in that window is more than **50 m** off the path, the car has
   been moved (GT7 resetting it, sometimes kilometres in one tick) and the whole path
   is searched; the result is taken only if it is at least twice as close, so a big
   excursion into a run-off area keeps its place.
4. The axis never runs backwards: a spin or a rewind holds it until the car is past
   where it had been.
5. The first sample is looked for within 60 m of the line and 25 m of the path; the
   first and last segments extend up to 150 m, so a lap that began a little behind the
   line starts at a negative distance.

The lap's `dist` is replaced by that axis and everything downstream — resampling, the
delta, the corner report, peak markers, event bands — lines the laps up by place with
no further change. A lap that can't be placed (it began nowhere near the path, or more
than **10 %** of its samples were over 50 m off it) keeps its own distance and is
reported with `aligned: false`. Over those 141 pairs every lap aligned, and "the same
distance" came out a median 0.00 m and a p99 0.15 m apart.

## Distance resampling

Every lap's (aligned) sample series is then resampled onto a distance grid:

- grid points at `0, step, 2×step, …` and then the lap's **exact end** (default **step
  = 5 m**; the API accepts 0.5–50 m) — a lap rarely ends on a whole step, and without
  the last point every series stopped up to a step short of the line;
- each channel is **linearly interpolated** onto the grid, with edge clamping (values
  before the first / after the last sample take the boundary value).

Two laps resampled this way have directly comparable values at every grid distance:
"what was each lap doing 850 m into the lap?" This is also a read-time downsample — a
2-minute lap goes from ~7,200 ticks to a few hundred grid points per channel. Panels
read a series at the grid point nearest the cursor, never at `index × step`, because
the last step is shorter.

Each lap also carries a **clock track**: its positions every **50 ms** of its own time,
which is what the map's time sync reads — the aligned distance axis folds a spin onto
one point, the clock never does.

## Time delta

For each compared lap, at every grid distance `d` up to the shorter of the two laps
(both on the reference's axis, see above):

```
delta_ms(d) = t_lap(d) − t_ref(d)      # both via interpolation of dist → t
```

Both clocks start at the same instant relative to the line: a lap's first sample sits
**half its boundary gap** past it on both `t` and `dist` (see
[lap detection](lap-detection.md#where-a-lap-begins)), so frames dropped at the line
can't offset one lap's clock against another's.

The **live Δ** widget is the same comparison made while driving: the lap in progress is
tracked along the session-best lap's path with the same tracker, fed the latest sample
at the live-frame rate. Replaying 80 real lap pairs through it at 20 Hz put it within
1 cm of the full alignment; comparing by the laps' own distances had been a median
100 ms and a p99 2.8 s away from the place-aligned delta.

**Positive = slower than the reference** at that point. The curve's *slope* is the
insight: rising = losing time right here, flat = holding the gap, falling = gaining.
The reference lap compared with itself is exactly zero, so it isn't drawn.

## Speed deviation (consistency chart)

Across the session's best N **counting** laps (default 5 — a pit out-lap's short time
would otherwise rank first), all lined up on the fastest one's path, on the common
distance grid (cut to the shortest lap):

- **median speed** at each grid point (middle value, or mean of the two middles);
- **population standard deviation** `sqrt(Σ(v − mean)² ÷ n)` at each grid point.

A spike in the deviation band marks a corner where your speed varies lap to lap — the
first place to look for consistency gains.

## Race line map

The map is a raw top-down plot of the recorded world coordinates (`pos_x`, `pos_z`) —
no projection or rotation, GT7's coordinates are used as-is. Each reference-lap point is
classified into an input zone:

| Zone | Condition | Color |
| --- | --- | --- |
| Braking | brake ≥ 1 % | red |
| Throttle | else throttle ≥ 1 % | green |
| Coasting | otherwise | blue |

Other selected laps overlay as solid lines in their chart colors, so line differences
are visible spatially. The chart cursor maps distance → coordinates (interpolated
between grid points), which is how hovering a chart moves the dots on the map — and
because the laps share the reference's axis, **position sync** puts every dot level
with the reference car. **Time sync** places the other dots from their clock tracks
instead, at the reference lap's elapsed time.

## Speed peaks & valleys

The ▲/▼ markers on the map are local speed extrema, found with a sliding window:

- a point is a **peak** if it is the maximum of the surrounding ±30 ticks (~0.5 s each
  side), a **valley** if it is the minimum;
- consecutive markers of the same kind must be at least **100 m** apart.

Valleys approximate apexes (minimum corner speed) and peaks approximate the end of
acceleration zones — without needing full corner detection.

## Auto-numbered corners

The numbered circles on the map are corners detected from the **reference lap's**
racing-line geometry (one canonical set, so every overlaid lap shares the same
numbering). The detector was tuned empirically against real GT7 laps — 5 sessions
across road courses and a banked oval — with one acceptance criterion: **identical
corner counts and < 30 m apex drift across laps of the same track**. Pipeline:

1. Resample positions onto a uniform **2 m** distance grid (strictly-increasing
   distances only), decoupling curvature from the 60 Hz speed-dependent spacing.
2. **Signed curvature** at each point: the wrapped angle between the chord headings
   of the 16 m windows before and after, divided by the span.
3. **Hysteresis segmentation** with thresholds anchored to the lap's own
   curvature noise floor (the p85 of frame-to-frame curvature jitter): on real
   GT7 telemetry (jitter ~0.0004) they land on the validated 0.0030/0.0022
   rad/m enter/stay pair — sweeping lower flips the counts between laps; on
   smooth low-curvature data (jitter < 0.0001, e.g. the simulator's sweeping
   circuit) they relax to 0.0020/0.0013 so broad-radius corners still
   register — sweeping lower grew a phantom corner on a banked oval. A
   segment ends after 40 m below the stay threshold; strong opposite
   curvature splits immediately — an S-section is two corners even when the
   magnitude never dips.
4. Arcs turning less than **12°** are noise and are dropped *before* merging —
   a surviving opposite blip would block a merge on some laps only, which was the
   dominant instability in early tuning.
5. Same-direction arcs within **90 m** merge: a hairpin or double-apex complex
   whose curvature relaxes mid-arc stays one corner (real complexes contain
   50–80 m low-curvature interludes).
6. A lap that starts mid-corner has that corner split across the start/finish
   line — the two edge arcs are stitched back into one (each half within 45 m
   of its lap edge, matching the mid-lap merge distance). The stitched
   corner's extent **wraps the lap boundary** (`entry_dist > exit_dist`),
   min speed covers both halves, and the apex comes from whichever half turns
   more. Stitching runs *before* the significance filter so a split corner is
   judged on its combined angle.
7. Keep arcs turning **25°–300°**. Below is a kink; above is a spin, not a corner.
8. **Apex = the curvature-weighted centroid** of the segment, *not* the
   minimum-speed point: min speed sits at the segment edge (braking for the next
   corner) and wanders 60–110 m between laps, while the centroid stays within
   ~25 m. Minimum corner speed is still reported per corner as a stat.

The thresholds are deliberately a narrow band: raising the entry threshold above
~0.0035 loses banked/high-speed corners entirely (a 300 m-radius banked turn peaks
at |κ| ≈ 0.004), and dropping the stay threshold below ~0.002 sinks into the
road-noise floor and bleeds adjacent corners together.

Display rule: numbered circles while ≤ 30 corners are in view (the zoomed section
or the whole lap); beyond that they collapse to small dots. The Corner Detail
widget shows the current corner (`T5 R`) while the cursor is inside one.

### Authored corners outrank detection

Detection is a fallback. It has to run per lap and it works off the **racing
line**, so a driver who straightlines an S takes the same tarmac on a
shallower arc, the arc drops below the 25° significance threshold, and every
corner after it renumbers — "turn 4" then means different tarmac from one lap
to the next, which is no foundation for a per-corner report card or real
sectors.

Once a circuit's corners have been labelled by hand in the
[Tracks view](../guide/tracks-view.md), they replace detection everywhere
(`corners_for_lap`). Authored corners are anchored to world **positions**, not
lap distances, because distance depends on the line taken; each lap resolves
its own `apex_dist`/`entry_dist`/`exit_dist` by finding where it passed the
anchor. An anchor further than **60 m** from anything the lap drove is not on
this lap and is dropped — and if that leaves nothing, the lap falls back to
detection, because a bundle describing a different layout should not cost the
lap its corners entirely. Where a corner has no marked entry/exit, the extent
is ±75 m around the apex, clipped at the midpoint to its neighbours.

`angle_deg` and an unset `direction` are still measured from the lap itself:
they describe what this lap did through a corner whose identity is already
settled. So a driver who straightlined turn 7 gets a small angle *against
turn 7*, rather than turn 7 disappearing.

## Cursor synchronization

All the "synced" behavior is one shared value: the cursor's grid index
(`round(distance ÷ step)`). Every consumer — each chart panel, the race line map dots,
the Corner Detail widget — reads the same index into its own resampled arrays, which is
why everything stays in lockstep as you scrub.
