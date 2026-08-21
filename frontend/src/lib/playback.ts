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
// Everything here is pure and frame-rate agnostic; PlaybackBar owns the rAF
// loop. NOT to be confused with race_engineer/replay.py, which recomputes
// coaching notes from stored laps.

import type { CompareLapEntry, LapSummary, LiveFrame } from "./types";

export const PLAYBACK_SPEEDS = [0.25, 0.5, 1, 2, 4] as const;

export type PlaybackSeries = CompareLapEntry["series"];

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
