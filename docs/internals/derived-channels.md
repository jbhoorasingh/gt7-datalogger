# Derived channels & metrics

Every lap stores a full 60 Hz sample series. Some columns are raw packet values; others
are computed at capture time. This page lists every stored channel with its exact
formula, then the per-lap aggregates, then the channels the frontend derives on the fly.

## Stored sample columns

One value per column per tick (~60/s):

| Column | Formula | Unit |
| --- | --- | --- |
| `t` | `tick_index × 1/60` | s |
| `dist` | running `Σ speed_mps × 1/60` | m |
| `speed` | `speed_mps × 3.6` | km/h |
| `throttle` | `raw_byte ÷ 2.55` | % (0–100) |
| `brake` | `raw_byte ÷ 2.55` | % |
| `coast` | `1` if throttle < 1 % **and** brake < 1 % | flag |
| `gear` | low nibble of the gear byte (15 = neutral) | — |
| `rpm` | raw | rpm |
| `boost` | `raw − 1.0` (packet stores bar + 1) | bar |
| `tire_slip` | mean of the four wheel slips | ratio |
| `yaw_rate` | `abs(angular_velocity_y)` | rad/s |
| `pos_x`, `pos_z` | raw world coordinates | m |
| `body_height` | `raw × 1000` | mm |
| `fuel` | raw fuel level | L |
| `slip_fl/fr/rl/rr` | `abs(wheel_rad_per_s) × tire_radius ÷ speed_mps` | ratio |
| `tt_fl/fr/rl/rr` | raw tire temps | °C |
| `sus_fl/fr/rl/rr` | `suspension_travel × 1000` | mm |
| `aids` | bitmask: TCS=1, ASM=2, handbrake=4, rev limiter=8 | mask |
| `surface` | per-wheel surface codes, 4 bits each, FL in the lowest nibble (0 = no data, 1 = tarmac, 2 = kerb, 3 = dirt, 4 = grass, 5 = sand, 6 = snow, 7 = other) — packet C only | mask |

**Wheel slip** is a slip-ratio proxy: wheel surface speed divided by car speed. `< 1`
under braking means the wheel is locking; `> 1` under power means it's spinning. Below
1 m/s car speed the ratio is meaningless, so all four are pinned to `1.0`.

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
(these values drift over minutes, not corners):

- `max_water_temp`, `max_oil_temp` — running maxima (°C)
- `min_oil_pressure` — running minimum (bar), **sampled only while RPM > 1200** so idling
  in the pits doesn't record a misleading low; `−1` means unknown

**Gearing** is captured once per lap from the lap-boundary packet: the non-zero gear
ratios, the tune's transmission top speed, and the redline (`rpm_alert_max`).

## Frontend-derived channels

These are computed in the browser from the stored columns, never persisted:

| Channel | Formula |
| --- | --- |
| Slip front / rear avg | `(slip_fl + slip_fr) ÷ 2`, `(slip_rl + slip_rr) ÷ 2` |
| Tire temp front / rear avg | mean of the two front / rear corners |
| **Tire temp F−R balance** | `mean(tt_fl, tt_fr) − mean(tt_rl, tt_rr)` °C — positive = fronts running hotter (push/understeer working the fronts) |
| Susp travel front / rear avg | mean of the two front / rear corners |

Laps recorded before per-corner channels existed simply skip these lines rather than
erroring.

**Units**: speed is the only channel with a display transform (km/h → mph × 0.621371
when imperial units are selected). Everything else displays in its stored unit.

## Gearing panel math

The estimated speed at redline for gear *i* assumes the tune's top speed is reached in
top gear, then scales by ratio:

```
speed_i = top_speed × (ratio_top ÷ ratio_i)
```

## A note on "tiers"

If you browse the code or `TODO.md` you'll see channels grouped into tiers. That's a
**roadmap grouping**, not a runtime concept: Tier 1 = surface data already in the packet
(per-corner slip/temps/suspension, aids, engine health, gearing — all implemented),
Tier 2 = derived analytics like sector splits and g-g diagrams (largely future work),
Tier 3+ = larger features (overlay, webhooks, strategy — implemented). There is no
lateral-G, brake-temp, or slip-angle channel — GT7 doesn't send them and they aren't
synthesized.
