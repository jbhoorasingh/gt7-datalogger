// GT7's suggested gear against the gear the car was in (#108), as the gear
// panel draws it and the Tuning panel counts it. Kept out of the components
// so it can be tested without a chart.
//
// The judgement is not made here. Which gear GT7 suggested for a corner — the
// last suggestion between the start of its approach and its slowest point,
// held because when GT7 clears the hint has not been checked — and whether
// the lap was in a higher gear at that point are the backend's
// (corner_metrics), carried on every corner_report row. Reading them, rather
// than working them out again from the resampled series, is what keeps the
// shading, the count and the corner report in agreement.

import type { Corner, CornerReportRow, Samples } from "@/lib/types";

/** The suggested-gear nibble when GT7 suggests nothing. */
export const NO_SUGGESTION = 15;

/** Whether a gear number names a forward gear: both nibbles of the gear byte
 *  count from 1, and 0 is reverse, 15 neutral or no suggestion. */
export function isForwardGear(gear: number): boolean {
  return gear >= 1 && gear <= 14;
}

/**
 * The suggestion as a chart line: null wherever GT7 suggested nothing, so the
 * line breaks there instead of climbing to 15 and stretching the gear axis.
 * Null when the lap has no suggestions at all.
 */
export function suggestionValues(series: Samples): (number | null)[] | null {
  const raw = series.suggested_gear;
  if (!Array.isArray(raw) || raw.length === 0) return null;
  return raw.map((v) => (isForwardGear(v) ? v : null));
}

/**
 * [from, to] stretches of the corners a lap took a gear high: inside each
 * such corner's window, from the entry up to the lap's slowest point in it
 * (where the suggestion is judged), wherever it was in a higher gear than GT7
 * suggested. Only corners the backend judged a gear high are shaded: braking
 * from the straight's gear, every lap sits above the suggestion for a while
 * as it comes down through the box, and a corner it got down in time for is
 * no mistake. Past the slowest point the car is accelerating, and the next
 * gear up is no mistake either.
 *
 * `corners` are the reference lap's (CompareLapEntry.corners) and `rows` this
 * lap's corner report, which carries its held suggestion and verdict per
 * corner. The resampled gear is fractional where a shift falls between grid
 * points, so it is rounded first. A corner across the start line is never
 * judged.
 */
export function gearHighBands(
  series: Samples,
  corners: Corner[] | undefined,
  rows: CornerReportRow[] | undefined,
): [number, number][] {
  const dist = series.dist;
  const gear = series.gear;
  const speed = series.speed;
  if (!dist || !gear || !corners || !rows) return [];
  const held = new Map<number, number>();
  for (const row of rows) {
    if (row.gear_high === true && row.suggested_gear != null) held.set(row.n, row.suggested_gear);
  }
  const n = Math.min(dist.length, gear.length);
  const bands: [number, number][] = [];
  for (const corner of corners) {
    const suggested = held.get(corner.n);
    if (suggested == null || corner.entry_dist > corner.exit_dist) continue;
    let lo = 0;
    while (lo < n && dist[lo] < corner.entry_dist) lo++;
    let hi = lo;
    while (hi + 1 < n && dist[hi + 1] <= corner.exit_dist) hi++;
    if (lo >= n || dist[lo] > corner.exit_dist) continue;
    // The first of the slowest samples, as the backend picks it; without a
    // speed series the whole window.
    let slowest = hi;
    if (speed && speed.length >= n) {
      slowest = lo;
      for (let i = lo + 1; i <= hi; i++) if (speed[i] < speed[slowest]) slowest = i;
    }
    let start: number | null = null;
    for (let i = lo; i <= slowest; i++) {
      const g = Math.round(gear[i]);
      const high = isForwardGear(g) && g > suggested;
      if (high && start === null) start = dist[i];
      if (!high && start !== null) {
        bands.push([start, dist[i]]);
        start = null;
      }
    }
    // Still high at the slowest point: the band takes that sample's step in.
    if (start !== null) bands.push([start, dist[Math.min(slowest + 1, n - 1)]]);
  }
  return bands;
}

/** Corners judged against a suggestion, and how many were taken a gear high. */
export function gearHighCount(rows: CornerReportRow[] | undefined): {
  high: number;
  judged: number;
} {
  let high = 0;
  let judged = 0;
  for (const row of rows ?? []) {
    if (row.gear_high == null) continue;
    judged++;
    if (row.gear_high) high++;
  }
  return { high, judged };
}
