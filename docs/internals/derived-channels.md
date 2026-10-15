# Derived channels & metrics

Every lap stores a full 60 Hz sample series. Some columns are raw packet values; others
are computed at capture time. This page lists every stored channel with its exact
formula, then the per-lap aggregates, then the channels the frontend derives on the fly.

## Stored sample columns

One value per column per tick (~60/s):

| Column | Formula | Unit |
| --- | --- | --- |
| `t` | `tick_index × 1/60`, from half the boundary gap ([why](lap-detection.md#where-a-lap-begins)) | s |
| `dist` | running `Σ speed_mps × 1/60`, from half the boundary gap | m |
| `speed` | `speed_mps × 3.6` | km/h |
| `throttle` | `raw_byte ÷ 2.55` | % (0–100) |
| `brake` | `raw_byte ÷ 2.55` | % |
| `coast` | `1` if throttle < 1 % **and** brake < 1 % | flag |
| `gear` | low nibble of the gear byte (15 = neutral) | — |
| `suggested_gear` | high nibble of the gear byte, as broadcast: the gear GT7 suggests, **15 = no suggestion**. A lap on which GT7 suggested nothing has no such column, and nor do laps recorded before it was kept ([below](#suggested-gear)) | — |
| `rpm` | raw | rpm |
| `boost` | `raw − 1.0` (packet stores bar + 1) | bar |
| `tire_slip` | mean of the four wheel slips | ratio |
| `yaw_rate` | `abs(angular_velocity_y)` | rad/s |
| `pos_x`, `pos_z` | raw world coordinates | m |
| `pos_y` | raw world elevation. Laps stored before it existed have no such column; the survey judge then places them on plan alone, which where the road crosses over itself means against both levels at once | m |
| `body_height` | `raw × 1000` | mm |
| `fuel` | raw fuel level | L |
| `slip_fl/fr/rl/rr` | `abs(wheel_rad_per_s) × tire_radius ÷ speed_mps` | ratio |
| `tt_fl/fr/rl/rr` | raw tire temps | °C |
| `sus_fl/fr/rl/rr` | `suspension_travel × 1000` | mm |
| `aids` | bitmask: TCS=1, ASM=2, handbrake=4, rev limiter=8 | mask |
| `surface` | per-wheel surface codes, 4 bits each, FL in the lowest nibble (0 = no data, 1 = tarmac, 2 = kerb, 3 = dirt, 4 = grass, 5 = sand, 6 = snow, 7 = other) — packet C only | mask |

### Optional columns

These need an extended packet format, and a lap that did not carry one from start to
finish **does not have the column at all** rather than a column of zeros. A zero-filled
steering trace reads as "the driver never turned"; an absent panel says nothing false.
The rule is enforced once, at lap completion (`prune_optional` in
`app/processing/laps.py`), so it also covers a recording whose packet format changed
mid-lap.

| Column | Needs | Formula | Unit |
| --- | --- | --- | --- |
| `steer` | packet B | `wheel_rotation` as broadcast | rad |
| `acc_lat` | packet B | `sway`, raw | see below |
| `acc_long` | packet B | `surge`, raw | see below |
| `acc_vert` | packet B | `heave`, raw | see below |
| `throttle_f` | packet ~ | `throttle_filtered ÷ 2.55` | % |
| `brake_f` | packet ~ | `brake_filtered ÷ 2.55` | % |
| `race_pos` | a race (any format) | `race_position`, only while GT7 reports one — it sends −1 outside races | position |
| `body_slip` | a source that sends the car's orientation (any format) | angle between the car's nose and its velocity, [below](#body-slip-angle) | ° |

**Filtered pedals** are the pedal position *after* the aids acted on it. The gap to the
raw `throttle` / `brake` column is the intervention itself — TCS trimming throttle, ABS
bleeding brake pressure — measured rather than inferred from the aids bitmask, which
only says whether an aid was active at all.

### Accelerometer units are calibrated, not assumed

GT7 documents neither a unit nor a sign convention for `sway`/`heave`/`surge`, and the
simulator source cannot prove what a real console sends. The channels are therefore
stored raw, and `analysis.accel_calibration()` fits each axis against physics the same
lap already recorded:

| Axis | Reference | Recovers |
| --- | --- | --- |
| lateral | `v × ω`, with `ω` the **signed** yaw rate taken from the driven path (the stored `yaw_rate` column is absolute, so it cannot settle the sign) | unit and sign |
| longitudinal | `dv/dt` from the speed trace | unit and sign |
| vertical | — | nothing; no independent reference exists |

Each fit is a least-squares slope forced through the origin (zero acceleration must mean
zero reading), graded by R² **about zero** rather than a mean-centred correlation — a
long constant-rate braking zone makes both series near-constant, where Pearson *r*
collapses into numerical noise. A fit needs ≥ 150 qualifying samples and R² ≥ 0.75; below
that the axis reports `fitted: false`, falls back to assuming m/s², and the g-g panel
says the scale is unverified.

The result is delivered as `g_per_unit` — multiply the raw channel by it to get g, sign
included — on the `accel` key of `/api/analysis/compare`, fitted on the reference lap and
applied to every lap in the comparison so they share one axis.

### Body slip angle

The angle between where the car **points** and where it is **going**: nothing on a
straight, a degree or so in a corner taken cleanly, tens of degrees in a slide. It is
what understeer and oversteer are, measured and not inferred from steering against yaw
rate.

The packet has carried what it takes all along, under names that say something else.
The community layout calls offset 0x1C *rotation (pitch, yaw, roll)* and 0x28 *relative
orientation to north*. They are neither three angles nor a heading: together they are
**one unit quaternion**, `x, y, z` and then `w`. Checked against 793 packets of a real
capture (the surface survey's log, which keeps the raw fields), 466 of them at 15 m/s
or more, which are the ones a direction of travel can be taken from:

| Question | Answer from the capture |
| --- | --- |
| Are the four floats a quaternion? | Their squared sum is 1.00000 in every packet |
| Which way is the car's nose? | Its local **−Z**: turned by the quaternion, −Z lies along the velocity with a median difference of 0.00° and 90 % of packets inside ±1.2°. +Z is 180° out, ±X 90° |
| Is "orientation to north" a heading? | No. As an angle (`2·acos`) it is 29° out on average, because it is the quaternion's `w` and has lost the sign |
| Which side is the car's right? | Its local **+X** |

So the velocity is turned into the car's own frame by the inverse of the orientation,
and the angle is taken between its two horizontal parts there:

```
v_car     = q⁻¹ · v · q
body_slip = atan2(−v_car.x, −v_car.z)      # degrees
```

In the car's frame, and not as a difference of two headings on the map, so that banking
and gradient do not read as slip. **Positive is the nose to the right of travel** — ISO
8855's sign, and the sign of a right-hander everywhere else in the app. In a
right-hander that is the nose turned further into the corner than the car is moving; in
a left-hander it is the nose pointing out of it. The
[corner report card](../guide/analysis-view.md#balance) turns the sign per corner.

Below **8 m/s** (29 km/h) the column holds 0: a car barely moving has no direction of
travel to measure from. Over the capture the angle's median is 0.2–0.5° in every speed
band above that and 1.6°, 4.6° and 10.6° in the three below. The gate is on the car's
velocity, not the broadcast `speed`: the two differ by up to 11 m/s in the capture.

A packet whose four floats are not a unit quaternion (the squared sum more than 0.05
from 1) carries no orientation, and the lap has no column. Laps recorded before 0.7.0
have none either: a stored lap kept the car's position and not its heading, so the
angle cannot be worked out for them after the fact.

**Wheel slip** is a slip-ratio proxy: wheel surface speed divided by car speed. `< 1`
under braking means the wheel is locking; `> 1` under power means it's spinning. Below
1 m/s car speed the ratio is meaningless, so all four are pinned to `1.0`.

### Suggested gear

The gear byte at 0x90 carries two nibbles: the gear the car is in, and the gear GT7
suggests on its HUD. Every packet format has both, so `suggested_gear` is recorded on
every tick like `gear`, and is not an [optional column](#optional-columns). It is kept as
broadcast, **15 where GT7 suggests nothing**: a sample column holds numbers, and neither
a lap file nor the importer has a way to say "no value" for one tick. Both nibbles
count gears from 1; anything that compares them takes a value outside **1–14** as no
gear (0 is reverse, 15 neutral or no suggestion).

A lap on which no tick holds a suggestion would carry a column of 15s, about 36 KB of
a two-minute lap that says nothing, so the column is dropped when the lap completes
(`prune_unsuggested` in `app/processing/laps.py`, which runs on import too). Absent
therefore means "GT7 suggested nothing on this lap", or a lap recorded before the
column was kept.

Resampled onto the comparison grid it takes the **nearest** sample's value, like the
`aids` and `surface` bitmasks: interpolated, halfway between a suggested third and no
suggestion would be a suggested ninth. `gear` itself is still interpolated, so the
frontend rounds it wherever it compares the two.

Per corner (`app/processing/corner_metrics.py`, on the reference lap's corners and
axis):

| Figure | Is |
| --- | --- |
| suggested gear | the **last** suggestion in 1–14 from the start of the corner's approach — 250 m before its entry, never back past the previous corner's apex — up to and including its slowest point. Held rather than read at the slowest point, because when GT7 clears the hint has not been checked against a console |
| gear high | the gear at the slowest point (the sample that gives the minimum speed) is higher than that suggestion. Unknown unless both are known and the gear is in 1–14, so a car in neutral or reverse there is not judged |

Neither is worked out for a corner across the start line. GT7's real behaviour — when
it fills the nibble, on which cars and modes, whether it ever suggests an upshift and
whether a HUD setting turns it off — has **not** been checked against a console
recording; the simulator's suggestion is a stand-in.

## Per-lap aggregates

Computed once when the lap is saved (`n` = number of samples):

| Metric | Formula |
| --- | --- |
| Fuel consumed | `max(0, fuel_at_lap_start − fuel_at_lap_end)` L |
| Full throttle % | `100 × count(throttle ≥ 98 %) ÷ n` |
| Full brake % | `100 × count(brake ≥ 98 %) ÷ n` |
| Coasting % | `100 × count(coast flag set) ÷ n` |
| Tire spin % | `100 × count(tire_slip ≥ 1.1) ÷ n` |
| Max speed | `max(speed)` km/h |
| Min body height | `min(body_height)` mm |
| TCS / ASM active % | `100 × count(aid bit set) ÷ n` |
| Off-track count | excursions where ≥ 3 wheels sat on a loose surface for ≥ 6 ticks; `-1` = unknown (no surface data) |
| Clean lap | `off_track_count == 0`; `null` = unknown — distinct from `counts_for_best`, which flags partial pit out-laps |

**Engine health** is tracked as running per-lap extremes rather than per-tick columns
(an engine's temperatures move over minutes, not corners):

- `max_water_temp`, `max_oil_temp` — running maxima (°C)
- `min_oil_pressure` — running minimum (bar), **sampled only while RPM > 1200** so idling
  in the pits doesn't record a misleading low; `−1` means unknown

GT7 appears to hold the two temperatures fixed. Every lap recorded from a console so far
— 446 laps in 28 cars, July to September 2026 — reads a maximum water temperature of
exactly 85 °C and oil of 110 °C; only the simulator varies them. So nothing charts them
across a session (a lap-on-lap trend would be a flat line, #105), and the Tuning panel's
figures and the alerts' limits matter only if a GT7 update makes them move. Oil pressure
does vary on real laps, but with revs and incidents (a spin pulls it down) rather than
with the engine's health.

**Gearing** is captured once per lap from the lap-boundary packet: the non-zero gear
ratios, GT7's calculated top speed for the car (`calculated_max_speed`, km/h), and the
redline (`rpm_alert_max`). Laps recorded before 0.7.0 hold `transmission_top_speed` in
`top_speed` instead, a ratio; see [Gearing panel math](#gearing-panel-math).

## Frontend-derived channels

These are computed in the browser from the stored columns, never persisted:

| Channel | Formula |
| --- | --- |
| Slip front / rear avg | `(slip_fl + slip_fr) ÷ 2`, `(slip_rl + slip_rr) ÷ 2` |
| Tire temp front / rear avg | mean of the two front / rear corners |
| **Tire temp F−R balance** | `mean(tt_fl, tt_fr) − mean(tt_rl, tt_rr)` °C — positive = fronts running hotter (push/understeer working the fronts) |
| Susp travel front / rear avg | mean of the two front / rear corners |
| **TCS cut** | `max(0, throttle − throttle_f)` % — how much throttle traction control took away |
| **ABS release** | `max(0, brake − brake_f)` % — how much brake pressure ABS gave back |
| Lateral / longitudinal / vertical g | `acc_* ÷ 9.80665` as a trace (assumes m/s²; the g-g panel uses the fitted scale instead) |

Laps recorded before per-corner channels existed simply skip these lines rather than
erroring.

**Units**: speed is the only channel with a display transform (km/h → mph × 0.621371
when imperial units are selected). Everything else displays in its stored unit.

## Gearing panel math

Road speed in a gear is engine speed through that gear, the final drive and the tyre:

```
speed_g(rpm) = K × rpm ÷ ratio_g          km/h
```

`K` folds the final drive and the tyre's circumference into one figure per car and
setup, the same for every gear, and it is the one thing the stored gearing lacks: GT7
broadcasts the gearbox ratios and the redline, and the tire radius on every tick, but
never the final drive, and a lap keeps no tire radius. The
[Gearing](../guide/analysis-view.md#gearing) panel draws `speed_g` for each gear from
0 rpm to the redline, and its speed at redline is where each line ends:

```
at_redline_g = K × rpm_alert ÷ ratio_g
```

**K is fitted from the lap.** Over the comparison's series (a sample every 5 m), a
sample is clean when:

- its gear is within 0.01 of a whole gear from 1 to the number of ratios. The series
  interpolate gear like any other channel, so a point between two ticks either side of
  a shift reads 2.4 and is dropped, never rounded into a gear; 0 (reverse) and 15
  (neutral) are out of range;
- rpm is at least 0.35 × `rpm_alert` and speed at least 30 km/h, which leaves out the
  clutch and the idle at a crawl;
- `tire_slip` is between 0.95 and 1.05, when the lap has the channel: the driven wheels
  turning with the road. Wheelspin and lockups break the link between rpm and speed.

`K` is the median of `speed × ratio_g ÷ rpm` over the clean samples. The fit is
accepted only when:

- there are at least 30 clean samples;
- at least two gears have 10 or more each (one, on a car with a single gear), so there
  is something to check the scale against. A lap driven all the way round in one gear
  is not fitted;
- those gears agree: the largest of their medians is at most 1.15 × the smallest. A
  gear with fewer than 10 samples, a short burst of first out of a hairpin, still
  counts towards the median but has no say in this check.

On recorded laps the gears' medians agree to within 13 %. The simulator's rpm does not
follow its ratios, and its gears come out about 1.5 to 1.95 times apart, which is what the
check is for.

**Fallback: GT7's top speed.** When the fit is refused, top gear is taken to reach the
stored top speed at the redline, where `ratio_top` is the smallest ratio:

```
K = top_speed × ratio_top ÷ rpm_alert
```

This applies only when `top_speed` is a speed: 30 km/h or more. Laps recorded before
0.7.0 (#128) store GT7's `transmission_top_speed` there, a unitless figure between
about 2 and 6. It is not a speed, and it is not the final drive either: the same car on
two setups stores two different values and fits the same `K`. On those laps a refused
fit leaves no lines, the table shows "—", and the top speed is not shown.

**The dots** are the same series' samples in whole gears, at most 1,500 a lap. Over
that, the two samples either side of every gear change are always kept, so the
sawtooth's tips and drops survive, and evenly spaced samples fill the rest.

**Same gearbox.** The reference lap is drawn behind the focus lap only when their
gearboxes differ: a different number of gears, any ratio more than 0.001 apart, or a
different redline.

## A note on "tiers"

If you browse the code or the commit history you'll see channels grouped into tiers.
That's a **roadmap grouping** from the project's original feature list (what is left of
it now lives in the GitHub issues), not a runtime concept: Tier 1 = surface data already in the packet
(per-corner slip/temps/suspension, aids, engine health, gearing — all implemented),
Tier 2 = derived analytics like sector splits (sectors still future work; the g-g
diagram is implemented from the broadcast accelerometer rather than derived from speed
deltas), Tier 3+ = larger features (overlay, webhooks, strategy — implemented). There is
no brake-temp channel — GT7 doesn't send one and it isn't synthesized. The body slip
angle is not sent either, but the velocity and the orientation it is the angle between
are.
