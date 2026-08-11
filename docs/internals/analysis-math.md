# Lap comparison math

The Analysis view compares laps of different lengths and speeds on one set of axes.
This page explains the alignment, delta, consistency, and map math behind it.

## Distance resampling — how laps are aligned

Laps are aligned **by distance traveled**, not by time. Every lap's sample series is
resampled onto a uniform distance grid:

- grid points at `0, step, 2×step, …` up to the lap's total distance (default **step =
  5 m**; the API accepts 0.5–50 m);
- each channel is **linearly interpolated** onto the grid, with edge clamping (values
  before the first / after the last sample take the boundary value).

Two laps resampled this way have directly comparable values at every grid index: "what
was each lap doing 850 m into the lap?" This is also a read-time downsample — a 2-minute
lap goes from ~7,200 ticks to a few hundred grid points per channel.

## Time delta

For each compared lap, at every grid distance `d` up to the shorter of the two laps:

```
delta_ms(d) = t_lap(d) − t_ref(d)      # both via interpolation of dist → t
```

**Positive = slower than the reference** at that point. The curve's *slope* is the
insight: rising = losing time right here, flat = holding the gap, falling = gaining.
The reference lap compared with itself is exactly zero, so it isn't drawn.

## Speed deviation (consistency chart)

Across the session's best N laps (default 5), on the common distance grid (cut to the
shortest lap):

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
are visible spatially. The chart cursor maps distance → grid index → coordinates, which
is how hovering a chart moves the dots on the map.

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
