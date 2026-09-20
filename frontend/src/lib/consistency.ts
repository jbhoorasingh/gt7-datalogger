// How consistent a session was, as one figure (#112).
//
// The speed-deviation chart says WHERE on the lap a driver varies; this says
// how much the lap time itself moves from one lap to the next. It is measured
// over the laps that count toward bests and no others — the same verdict the
// bests board acts on — because a pit out-lap, a race's lap 1 from the grid or
// a lap the driver ruled out by hand is not an attempt at a lap time, and
// thirty seconds of out-lap would swamp the tenths the figure is there to show.

import type { LapSummary } from "@/lib/types";

// Two laps make a difference, not a spread.
export const MIN_CONSISTENCY_LAPS = 3;

export interface Consistency {
  /** Laps the figures are taken over. */
  laps: number;
  bestMs: number;
  medianMs: number;
  /** Sample standard deviation of the lap times. */
  stdMs: number;
  /** The same spread as a percentage of the median, so circuits compare. */
  pct: number;
}

export type ConsistencyBand = "tight" | "steady" | "loose";

export function countingLaps(laps: LapSummary[]): LapSummary[] {
  return laps.filter((lap) => lap.counts_for_best !== false && lap.time_ms > 0);
}

export function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = sorted.length >> 1;
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

/** Null until there are enough counting laps for a spread to mean anything. */
export function lapConsistency(laps: LapSummary[]): Consistency | null {
  const times = countingLaps(laps).map((lap) => lap.time_ms);
  if (times.length < MIN_CONSISTENCY_LAPS) return null;
  const mean = times.reduce((sum, t) => sum + t, 0) / times.length;
  const variance = times.reduce((sum, t) => sum + (t - mean) ** 2, 0) / (times.length - 1);
  const stdMs = Math.sqrt(variance);
  const medianMs = median(times);
  return {
    laps: times.length,
    bestMs: Math.min(...times),
    medianMs,
    stdMs,
    pct: (stdMs / medianMs) * 100,
  };
}

/**
 * A rough reading of the percentage, for colour only. Half a percent is a
 * few tenths on a ninety-second lap, which is a driver repeating themselves;
 * past one and a half the laps are different laps.
 */
export function consistencyBand(pct: number): ConsistencyBand {
  if (pct < 0.5) return "tight";
  if (pct < 1.5) return "steady";
  return "loose";
}

/** "±0.42 s" — seconds to two places below ten seconds, one above. */
export function formatSpread(stdMs: number): string {
  const s = stdMs / 1000;
  return `±${s < 10 ? s.toFixed(2) : s.toFixed(1)} s`;
}
