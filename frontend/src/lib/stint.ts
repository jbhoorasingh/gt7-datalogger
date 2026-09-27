// The stint trend's chart, as numbers (#111): which laps are points, which
// are gaps, and what the lap-time axis spans. Kept out of the component so it
// can be tested without a chart.
//
// The scale belongs to the laps that make the trend. One lap spent facing the
// wrong way is twenty seconds off the pace and tyre wear is two tenths a lap;
// an axis that has to reach the first flattens the second into a straight
// line. So the window is set by the bulk of the counting laps, and a lap
// slower than it is pinned to the top edge — still there, no longer in
// charge. The drift itself is not touched by any of this: it is the
// server's, a median of slopes, and one slow lap does not move a median.

import { median } from "@/lib/consistency";
import type { Stint, StintLap } from "@/lib/types";

// How far above the median a lap may be and still set the scale, in spreads
// (median absolute deviations). Wide enough to keep a whole stint of wear.
const WINDOW_SPREADS = 6;
// The least the window reaches above the median, however tight the laps.
const WINDOW_MIN_S = 1.5;

export interface StintPoint {
  lap: StintLap;
  /** Seconds, clamped to the top of the window when `clipped`. */
  seconds: number;
  clipped: boolean;
}

export interface StintScale {
  yMin: number;
  yMax: number;
  /** Counting laps inside a stint: the trend's points, in driving order. */
  points: StintPoint[];
  /** Laps that are gaps in the trend: not counting, or a pit lap. */
  gaps: StintPoint[];
}

export function stintScale(laps: StintLap[]): StintScale | null {
  const timed = laps.filter((lap) => lap.time_ms > 0);
  const counting = timed.filter((lap) => lap.counts && !lap.pit);
  if (counting.length === 0) return null;
  const seconds = counting.map((lap) => lap.time_ms / 1000);
  const mid = median(seconds);
  const spread = median(seconds.map((s) => Math.abs(s - mid)));
  const ceiling = mid + Math.max(WINDOW_SPREADS * spread, WINDOW_MIN_S);
  const inside = seconds.filter((s) => s <= ceiling);
  const lo = Math.min(...inside);
  const hi = Math.max(...inside);
  const pad = Math.max((hi - lo) * 0.12, 0.25);
  const yMin = lo - pad;
  const yMax = hi + pad;

  const points: StintPoint[] = [];
  const gaps: StintPoint[] = [];
  for (const lap of timed) {
    const s = lap.time_ms / 1000;
    const point = { lap, seconds: Math.min(s, yMax), clipped: s > yMax };
    (lap.counts && !lap.pit ? points : gaps).push(point);
  }
  return { yMin, yMax, points, gaps };
}

/** "+0.18 s/lap" — two places below a second a lap, one above. */
export function formatDrift(msPerLap: number): string {
  const s = msPerLap / 1000;
  const digits = Math.abs(s) < 1 ? 2 : 1;
  const text = Math.abs(s).toFixed(digits);
  // A drift that rounds to nothing has no direction.
  if (Number(text) === 0) return `±${text} s/lap`;
  return `${s > 0 ? "+" : "−"}${text} s/lap`;
}

/** "+1.2 °C/lap" */
export function formatWarming(perLap: number): string {
  const text = Math.abs(perLap).toFixed(1);
  if (Number(text) === 0) return `±${text} °C/lap`;
  return `${perLap > 0 ? "+" : "−"}${text} °C/lap`;
}

/** "L3–L14", or "L3" for a stint of one lap. */
export function stintSpan(stint: Stint): string {
  return stint.first_lap === stint.last_lap
    ? `L${stint.first_lap}`
    : `L${stint.first_lap}–L${stint.last_lap}`;
}

/** Whether the session has anything a trend can be read from: two laps that
 *  count, at least. One lap is a point. */
export function hasTrend(laps: StintLap[]): boolean {
  return laps.filter((lap) => lap.counts && !lap.pit && lap.time_ms > 0).length >= 2;
}
