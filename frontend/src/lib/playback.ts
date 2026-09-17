// Lap playback for the Analysis view (#59): a clock that drives the existing
// cursorDist instead of the pointer, plus a LiveFrame synthesized at the
// playhead so the live-dashboard widgets render a stored lap unchanged.
//
// Distance-locked: the playhead is a position on the shared distance axis
// (every compared lap sits at the same metre mark, so the delta under the
// cursor stays directly readable), but it ADVANCES on the reference lap's own
// clock — the compare series carries `t` per distance step, and inverting
// that mapping is what makes the cursor dwell in slow corners and sweep down
// straights instead of gliding at constant metres per second.
//
// The race-line map can instead be synced on lap TIME (#75): the reference
// car stays at the playhead and every other lap's car is drawn where it had
// got to after the same number of seconds — the gap as distance on track,
// which is how a gap reads when you watch it. positionAtTime is that lookup.
//
// Everything here is pure and frame-rate agnostic; PlaybackBar owns the rAF
// loop. NOT to be confused with race_engineer/replay.py, which recomputes
// coaching notes from stored laps.

import type { CompareLapEntry, LapSummary, LiveFrame } from "./types";

export const PLAYBACK_SPEEDS = [0.25, 0.5, 1, 2, 4] as const;

export type PlaybackSeries = CompareLapEntry["series"];

/** Anything with positions against a clock: a compare series, or a lap's
 *  time-sampled track (CompareLapEntry.track). */
export type TimedPositions = {
  t?: number[];
  pos_x?: number[];
  pos_z?: number[];
  pos_y?: number[]; // elevation; absent on laps recorded before it was stored
  dist?: number[];
  speed?: number[];
};

/** Seconds the series covers (its last `t` sample); 0 when unplayable. */
export function playbackEnd(series: PlaybackSeries): number {
  const t = series.t;
  return t && t.length > 1 ? t[t.length - 1] : 0;
}

/** Advance the playhead: monotone, speed-scaled, clamped to the lap end. */
export function advancePlayhead(
  tS: number,
  dtMs: number,
  speed: number,
  endS: number,
): { t: number; ended: boolean } {
  const next = tS + Math.max(0, dtMs / 1000) * speed;
  return next >= endS ? { t: endS, ended: true } : { t: next, ended: false };
}

// Index i and fraction into [i, i+1] such that values[i..i+1] brackets v.
// `values` is monotone non-decreasing (both t and dist are).
function locate(values: number[], v: number): [number, number] {
  const n = values.length;
  if (n === 0) return [0, 0];
  if (v <= values[0]) return [0, 0];
  if (v >= values[n - 1]) return [n - 2 >= 0 ? n - 2 : 0, n >= 2 ? 1 : 0];
  let lo = 0;
  let hi = n - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (values[mid] <= v) lo = mid;
    else hi = mid;
  }
  const span = values[hi] - values[lo];
  return [lo, span > 0 ? (v - values[lo]) / span : 0];
}

/** Index of the value nearest `v` in a non-decreasing array (0 when empty).
 *  How a panel reads a series at the cursor: series end on a shorter last
 *  step (the lap's exact end), so index × step is not a distance. */
export function nearestIndex(values: number[], v: number): number {
  if (values.length === 0) return 0;
  const [i, f] = locate(values, v);
  return Math.min(values.length - 1, f < 0.5 ? i : i + 1);
}

function lerpAt(col: number[] | undefined, i: number, f: number): number | null {
  if (!col || col.length === 0) return null;
  const a = col[Math.min(i, col.length - 1)];
  const b = col[Math.min(i + 1, col.length - 1)];
  return a + (b - a) * f;
}

function stepAt(col: number[] | undefined, i: number, f: number): number | null {
  if (!col || col.length === 0) return null;
  return col[Math.min(f < 0.5 ? i : i + 1, col.length - 1)];
}

/** Distance (m) the reference lap had covered `tS` seconds in. */
export function distAtTime(series: PlaybackSeries, tS: number): number {
  const [i, f] = locate(series.t ?? [], tS);
  return lerpAt(series.dist, i, f) ?? 0;
}

/** Seconds into the reference lap at distance `dist` — the scrub inverse. */
export function timeAtDist(series: PlaybackSeries, dist: number): number {
  const [i, f] = locate(series.dist ?? [], dist);
  return lerpAt(series.t, i, f) ?? 0;
}

// Consecutive distance steps whose positions lie further apart than this many
// times the distance between them were not driven: GT7 moved the car — a
// reset to the track after a stop, or a rewind — while the distance counter
// barely moved. Measured over 1,212 recorded laps (948k steps), driven steps
// reached 7.4× (a sliding car, or a run of dropped frames, covers more ground
// than its integrated speed says), while resets of 45 m and more ran from 9×
// to 805× (a 4 km jump in one tick). Smaller resets (15-30 m) sit among the
// driven steps and simply glide, which at that size is the lesser mistake.
const TELEPORT_RATIO = 10;

// When, as a fraction of a reset step's duration, the car was moved. The
// grid cannot say, so it is estimated from the step's endpoint speeds: the
// time to drive the step at those speeds is split evenly either side of the
// reset, and whatever time is left over was spent (nearly) standing still —
// on the slower side, since that is where a car sits. Scored on 36 resets in
// real recordings, this leaves the dot on the wrong side of the jump for a
// median 0.05 s (p90 0.20 s), against 0.14 s / 0.55 s for jumping at the
// midpoint and 0.13 s / 0.96 s for holding to the end of the step.
function resetFraction(series: TimedPositions, a: number, b: number): number {
  const t = series.t ?? [];
  const duration = (t[b] ?? 0) - (t[a] ?? 0);
  const va = series.speed?.[a];
  const vb = series.speed?.[b];
  if (!(duration > 0) || va == null || vb == null) return 0.5;
  const mps = Math.max((va + vb) / 2 / 3.6, 0.5);
  const stepM = Math.abs((series.dist?.[b] ?? 0) - (series.dist?.[a] ?? 0));
  const moving = Math.min(duration, stepM / mps);
  const standing = duration - moving;
  const before = moving / 2 + (va < vb ? standing : 0);
  return Math.min(1, Math.max(0, before / duration));
}

// Position at fraction f between steps i and i+1. Across a teleport there is
// nothing to interpolate — the car was never anywhere in between — so the
// position holds and then jumps: at the estimated moment of the reset on the
// time axis, halfway on the distance axis (where the nearest step would).
function positionBetween(
  series: TimedPositions,
  i: number,
  f: number,
  axis: "time" | "dist",
): [number, number] | null {
  const xs = series.pos_x ?? [];
  const zs = series.pos_z ?? [];
  const n = Math.min(xs.length, zs.length);
  if (n === 0) return null;
  const a = Math.min(i, n - 1);
  const b = Math.min(i + 1, n - 1);
  const stepM = Math.abs((series.dist?.[b] ?? 0) - (series.dist?.[a] ?? 0));
  let g = f;
  if (Math.hypot(xs[b] - xs[a], zs[b] - zs[a]) > TELEPORT_RATIO * Math.max(stepM, 1)) {
    const jumpAt = axis === "time" ? resetFraction(series, a, b) : 0.5;
    g = f >= jumpAt ? 1 : 0;
  }
  const x = xs[a] + (xs[b] - xs[a]) * g;
  const z = zs[a] + (zs[b] - zs[a]) * g;
  return Number.isFinite(x) && Number.isFinite(z) ? [x, z] : null;
}

/**
 * Where a lap's car was `tS` seconds in, as [x, z] — interpolated between
 * samples so a time-synced dot glides rather than hopping. Pass the lap's
 * time-sampled `track` where there is one: a lap lined up with a reference
 * has a distance axis that never runs backwards, so a spin or a rewind folds
 * onto one point of its distance series, but never of its clock.
 * Clamped to the lap like distAtTime: before the start it waits on the line,
 * after its own finish it parks at its last step (which the distance grid
 * places up to one step short of the line). Across a reset it holds, then
 * jumps (see resetFraction). Null without position samples.
 */
export function positionAtTime(series: TimedPositions, tS: number): [number, number] | null {
  const [i, f] = locate(series.t ?? [], tS);
  return positionBetween(series, i, f, "time");
}

/**
 * Where a lap's car was `dist` metres in, as [x, z] — interpolated, so the
 * reference dot sits exactly at the cursor rather than on the nearest 5 m
 * step (up to 2.5 m off, which time sync would show as a false gap). Across a
 * reset it jumps halfway through the step, as the nearest step would.
 */
export function positionAtDist(series: PlaybackSeries, dist: number): [number, number] | null {
  const [i, f] = locate(series.dist ?? [], dist);
  return positionBetween(series, i, f, "dist");
}

/**
 * A LiveFrame-shaped snapshot of the lap `tS` seconds in, so the existing
 * widget registry renders stored laps without a second data source. Channels
 * the comparison didn't fetch (or the recording never had) degrade to the
 * same "no data" values the live frame uses.
 */
export function frameAtTime(
  series: PlaybackSeries,
  tS: number,
  lap?: LapSummary,
): LiveFrame {
  const [i, f] = locate(series.t ?? [], tS);
  const lerp = (col: number[] | undefined) => lerpAt(col, i, f);
  const step = (col: number[] | undefined) => stepAt(col, i, f);
  const steer = lerp(series.steer);
  const racePos = step(series.race_pos);
  return {
    on_track: true,
    paused: false,
    speed_kmh: lerp(series.speed) ?? 0,
    rpm: Math.round(lerp(series.rpm) ?? 0),
    // Not in the samples; a plausible redline keeps the RPM bar proportioned.
    rpm_alert: 8000,
    gear: step(series.gear) ?? 0,
    suggested_gear: 15,
    throttle: lerp(series.throttle) ?? 0,
    brake: lerp(series.brake) ?? 0,
    boost: lerp(series.boost) ?? 0,
    fuel_level: lerp(series.fuel) ?? 0,
    fuel_capacity: 100,
    current_lap: lap?.number ?? 0,
    total_laps: 0,
    best_lap_ms: -1,
    last_lap_ms: -1,
    position: racePos ?? -1,
    total_positions: 0,
    tire_temps: [
      lerp(series.tt_fl) ?? 0,
      lerp(series.tt_fr) ?? 0,
      lerp(series.tt_rl) ?? 0,
      lerp(series.tt_rr) ?? 0,
    ],
    tire_slip: lerp(series.tire_slip) ?? 1,
    water_temp: 0,
    oil_temp: 0,
    oil_pressure: 0,
    aids: step(series.aids) ?? 0,
    surface: step(series.surface) ?? 0,
    car_id: lap?.car_id ?? 0,
    car_name: lap?.car_name ?? "",
    session_best_ms: -1,
    prev_best_ms: -1,
    delta_ms: null,
    lap_elapsed_ms: Math.round(tS * 1000),
    pos_x: lerp(series.pos_x) ?? 0,
    pos_z: lerp(series.pos_z) ?? 0,
    tod_ms: lap?.tod_ms ?? -1,
    track_name: lap?.track_name ?? "",
    steer_rad: steer,
  };
}
