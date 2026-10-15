// The gearing panel's numbers (#107): the speed every gear reaches at any
// rpm, the lap's own (speed, rpm) samples to plot against those lines, and
// whether two laps share a gearbox. Kept out of the panel so they can be
// tested without a chart.
//
// Road speed is engine speed through the gearbox, the final drive and the
// tyre: v = K × rpm ÷ ratio, with one constant K per car and setup. The
// stored gearing has the ratios and the redline but neither the final drive
// nor the tyre, so K is read off the lap itself: the median of
// speed × ratio ÷ rpm over the stretches where the wheels turn with the
// road. Where the lap gives no consistent answer, GT7's top speed stands in,
// as top gear's speed at the redline. Laps recorded before #128 hold a ratio
// in that field rather than a speed, so for them the lap's own samples are
// the only source.

import { SERIES_COLORS } from "@/lib/colors";
import { median } from "@/lib/consistency";
import type { LapGearing, Samples } from "@/lib/types";

// Below this a stored top speed is not a speed: laps recorded before #128
// hold GT7's transmission_top_speed there, a ratio of about 2 to 6.
export const MIN_TOP_SPEED_KMH = 30;

// A resampled gear this close to a whole number is that gear. The compare
// series interpolate gear like any channel, so a point between two ticks
// either side of a shift reads 2.4: neither gear, so it is dropped rather
// than rounded into one.
export const GEAR_TOLERANCE = 0.01;

// What makes a sample clean enough to fit K from: in gear, revving at least
// this share of the redline, at least this fast, and with the driven wheels
// turning at road speed — wheelspin and lockups break the link between rpm
// and speed, and so do the clutch and the idle at a crawl.
export const FIT_MIN_RPM_SHARE = 0.35;
export const FIT_MIN_SPEED_KMH = 30;
export const FIT_SLIP_MIN = 0.95;
export const FIT_SLIP_MAX = 1.05;

// How much evidence a fit needs. Every gear with enough clean samples must
// agree on K: on recorded laps the gears' medians sit within 13 % of each
// other, while the simulator, whose rpm ignores its ratios, puts them about
// 1.5 to 1.95 times apart. One clean gear is a fit only for a car that has one gear;
// anything else must agree across two.
export const FIT_MIN_SAMPLES = 30;
export const FIT_MIN_GEAR_SAMPLES = 10;
export const FIT_MAX_SPREAD = 1.15;

// The scatter's budget per lap. A Nürburgring lap is about 5,000 points at
// the compare step; drawn twice with the ghost, that is more than a card
// needs to show where each gear ran.
export const MAX_SCATTER_POINTS = 1500;

// Ratios are stored to four places from the same float32 every lap, so a
// setup that did not change compares exactly; this only absorbs rounding.
const RATIO_TOLERANCE = 1e-3;

/** Whether the stored top speed is a speed (km/h), recorded since #128. */
export function plausibleTopSpeed(gearing: LapGearing): boolean {
  return gearing.top_speed >= MIN_TOP_SPEED_KMH;
}

/** The gear a resampled value stands for, or null for one caught mid-shift
 *  or outside the box — 0 is reverse and 15 neutral. */
export function wholeGear(value: number, gearCount: number): number | null {
  const gear = Math.round(value);
  if (Math.abs(value - gear) > GEAR_TOLERANCE) return null;
  return gear >= 1 && gear <= gearCount ? gear : null;
}

/** K in km/h per rpm × ratio, fitted from the lap's clean samples; null when
 *  there are too few, or the gears disagree. */
export function fitScale(series: Samples, gearing: LapGearing): number | null {
  const { ratios, rpm_alert: redline } = gearing;
  const { speed, rpm, gear, tire_slip: slip } = series;
  if (!speed || !rpm || !gear || ratios.length === 0 || !(redline > 0)) return null;
  const minRpm = FIT_MIN_RPM_SHARE * redline;
  const byGear: number[][] = ratios.map(() => []);
  const n = Math.min(speed.length, rpm.length, gear.length);
  for (let i = 0; i < n; i++) {
    const g = wholeGear(gear[i], ratios.length);
    if (g == null || rpm[i] < minRpm || speed[i] < FIT_MIN_SPEED_KMH) continue;
    // A recording without the slip channel fits from the other filters alone.
    if (slip && (slip[i] < FIT_SLIP_MIN || slip[i] > FIT_SLIP_MAX)) continue;
    byGear[g - 1].push((speed[i] * ratios[g - 1]) / rpm[i]);
  }
  const all = byGear.flat();
  if (all.length < FIT_MIN_SAMPLES) return null;
  // A gear the lap barely used (a few samples of first out of a hairpin)
  // has no median worth checking the others against.
  const medians = byGear.filter((ks) => ks.length >= FIT_MIN_GEAR_SAMPLES).map(median);
  if (medians.length < Math.min(2, ratios.length)) return null;
  if (Math.max(...medians) / Math.min(...medians) > FIT_MAX_SPREAD) return null;
  return median(all);
}

export type ScaleSource = "fit" | "top_speed";

export interface GearScale {
  /** km/h per rpm × ratio: gear g runs at k × rpm ÷ ratios[g − 1]. */
  k: number;
  source: ScaleSource;
}

/** The scale the panel draws with: the lap's own fit, else GT7's top speed
 *  taken as top gear at the redline, else nothing. `series` is null until
 *  the comparison has loaded. */
export function gearScale(series: Samples | null, gearing: LapGearing): GearScale | null {
  const fitted = series ? fitScale(series, gearing) : null;
  if (fitted != null) return { k: fitted, source: "fit" };
  if (!plausibleTopSpeed(gearing) || gearing.ratios.length === 0 || !(gearing.rpm_alert > 0)) {
    return null;
  }
  const top = Math.min(...gearing.ratios);
  return { k: (gearing.top_speed * top) / gearing.rpm_alert, source: "top_speed" };
}

/** Road speed (km/h) in a gear of this ratio at this rpm. */
export function gearSpeed(k: number, ratio: number, rpm: number): number {
  return (k * rpm) / ratio;
}

/** Each gear's speed (km/h) at the redline, first gear first. */
export function redlineSpeeds(gearing: LapGearing, k: number): number[] {
  return gearing.ratios.map((ratio) => gearSpeed(k, ratio, gearing.rpm_alert));
}

/** A gear's colour: the series palette in slot order, whose neighbours are
 *  checked to stay apart for colourblind eyes — and neighbouring gears are
 *  exactly the ones that meet at every shift. */
export function gearColor(gear: number): string {
  return SERIES_COLORS[(gear - 1) % SERIES_COLORS.length];
}

export interface GearPoint {
  speed: number; // km/h
  rpm: number;
  gear: number;
}

/**
 * The lap's (speed, rpm) samples in whole gears, in lap order, at most `max`
 * of them. Over budget, the two samples either side of each gear change —
 * the last in the old gear and the first in the new — are always kept, so
 * the tips and drops of the sawtooth survive the thinning; evenly spaced
 * samples fill the rest.
 */
export function scatterPoints(
  series: Samples,
  gearCount: number,
  max = MAX_SCATTER_POINTS,
): GearPoint[] {
  const { speed, rpm, gear } = series;
  if (!speed || !rpm || !gear) return [];
  const points: GearPoint[] = [];
  const n = Math.min(speed.length, rpm.length, gear.length);
  for (let i = 0; i < n; i++) {
    const g = wholeGear(gear[i], gearCount);
    if (g != null) points.push({ speed: speed[i], rpm: rpm[i], gear: g });
  }
  if (points.length <= max) return points;

  const keep = new Uint8Array(points.length);
  for (let i = 1; i < points.length; i++) {
    if (points[i].gear !== points[i - 1].gear) keep[i - 1] = keep[i] = 1;
  }
  // The rest of the budget goes to evenly spaced samples. The stride is
  // fractional so the budget is spent: a whole one rounds up and can leave
  // a fifth of it unused.
  const budget = Math.max(1, max - keep.reduce((sum, k) => sum + k, 0));
  for (let j = 0; j < budget; j++) keep[Math.floor((j * points.length) / budget)] = 1;
  return points.filter((_, i) => keep[i] === 1);
}

/** Same gearbox: as many gears, each ratio equal to rounding, same redline. */
export function sameGearing(a: LapGearing, b: LapGearing): boolean {
  return (
    a.ratios.length === b.ratios.length &&
    a.ratios.every((ratio, i) => Math.abs(ratio - b.ratios[i]) <= RATIO_TOLERANCE) &&
    a.rpm_alert === b.rpm_alert
  );
}
