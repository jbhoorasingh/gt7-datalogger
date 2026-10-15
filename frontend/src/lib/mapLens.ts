// How the race line is coloured (#102): by what the driver was doing
// (throttle / brake / coast — the classic view), by speed, by where the
// compared lap gains or loses time against the reference, by how hot the
// hottest tire runs, or by which axle's tires run hotter. One colouring at a
// time; the same classes colour the lap strip under the map, so the strip is
// the line unrolled.
//
// Pure functions over the compare endpoint's series, kept out of the map so
// they can be tested without a chart. Colours are literals because a canvas
// cannot read CSS custom properties; they match the tokens in index.css.

import { THRESHOLDS } from "@/lib/alerts";
import { speedUnit, speedValue, type Units } from "@/lib/format";
import type { Samples } from "@/lib/types";

export type MapLens = "inputs" | "speed" | "gain" | "tire_temp" | "tire_balance";

export interface LensOption {
  value: MapLens;
  label: string;
  /** Why the control greys the lens out when the comparison cannot draw it.
   *  Inputs can always be drawn. */
  needs?: string;
}

const TIRE_TEMPS_NEEDED =
  "Needs tire temperatures — this lap was recorded before they were captured";

export const LENS_OPTIONS: LensOption[] = [
  { value: "inputs", label: "Inputs" },
  { value: "speed", label: "Speed", needs: "This recording has no speed channel" },
  {
    value: "gain",
    label: "Gain / loss",
    needs: "Needs a second lap with a time delta against the reference",
  },
  { value: "tire_temp", label: "Tire temp", needs: TIRE_TEMPS_NEEDED },
  { value: "tire_balance", label: "F−R temp", needs: TIRE_TEMPS_NEEDED },
];

/** The lens the map starts on, and the one a link leaves unsaid. */
export const DEFAULT_LENS: MapLens = "inputs";

/** Whether a value from outside the code — a link's lens=, the device's
 *  stored settings — names a lens this build can draw. */
export function isMapLens(value: unknown): value is MapLens {
  return LENS_OPTIONS.some((o) => o.value === value);
}

const THROTTLE = "#4ade80";
const BRAKE = "#f47272";
const COAST = "#5b93f5";
const WARN = "#f5b14e";
const EVEN = "#6b7482"; // --color-ink-faint

// Input zones, indexed as zoneOf returns them: brake, coast, throttle.
export const INPUT_COLORS = [BRAKE, COAST, THROTTLE];
// Five speed bands, slowest first: brake red through amber to throttle
// green, the two in-between mixed half and half.
export const SPEED_COLORS = [BRAKE, "#f49160", WARN, "#9fc767", THROTTLE];
// Gain / loss, indexed as gainClass returns them: loses, even, gains.
export const GAIN_COLORS = [BRAKE, EVEN, THROTTLE];
// The hottest tire, indexed as tireTempBand returns them: cold, working,
// hot, in the colours the Live tiles tint the same bands with.
export const TIRE_TEMP_COLORS = [COAST, THROTTLE, BRAKE];
// Front-minus-rear tire temperature, indexed as tireBalanceClass returns
// them: blue where the rears run hotter and red where the fronts do, as
// Corner Detail colours its F/R balance, grey between, and the two
// in-between classes mixed half and half with the grey.
export const TIRE_BALANCE_COLORS = [COAST, "#6383bb", EVEN, "#af737a", BRAKE];

export const SPEED_BANDS = 5;

// Time lost or gained per metre of track before a stretch counts as one or
// the other: 0.3 ms per metre is 9 ms across the 30 m window it is measured
// over, about the smallest difference the delta chart shows as a slope.
export const GAIN_THRESHOLD_S_PER_M = 0.0003;
// Half the window the slope is measured over, in metres.
export const GAIN_HALF_WINDOW_M = 15;

// Within this many °C either way the axles read even. The figure is Corner
// Detail's, whose F/R balance readout (which reads this constant too) turns
// red or blue only beyond it, so exactly 3 °C is still even.
export const TIRE_BALANCE_EVEN_C = 3;
// From this many °C either way the colour is full: the race engineer's
// figure for calling out one axle running hotter (TIRE_IMBALANCE_C in
// backend/app/race_engineer/thresholds.py, 8 °C and up). The engineer
// measures it on lap averages; the lens applies it to every sample, so the
// map shows where on the lap the imbalance builds.
export const TIRE_BALANCE_STRONG_C = 8;

const TIRE_COLUMNS = ["tt_fl", "tt_fr", "tt_rl", "tt_rr"];

export function zoneOf(throttle: number, brake: number): number {
  if (brake >= 1) return 0;
  if (throttle >= 1) return 2;
  return 1;
}

export interface LensLegendEntry {
  label: string;
  color: string;
}

export interface LensSeries {
  /** One class per sample of the reference series, indexing `palette`. */
  keys: number[];
  palette: string[];
  legend: LensLegendEntry[];
}

/** A comparison lap's time-delta series against the reference, as the
 *  compare endpoint returns it, plus the name the legend calls the lap. */
export interface GainSource {
  label: string;
  dist: number[];
  delta_ms: number[];
}

function interpolate(xs: number[], ys: number[], x: number): number {
  if (xs.length === 0) return 0;
  if (x <= xs[0]) return ys[0];
  if (x >= xs[xs.length - 1]) return ys[ys.length - 1];
  let lo = 0;
  let hi = xs.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (xs[mid] <= x) lo = mid;
    else hi = mid;
  }
  const span = xs[hi] - xs[lo];
  if (span <= 0) return ys[lo];
  return ys[lo] + ((ys[hi] - ys[lo]) * (x - xs[lo])) / span;
}

/** Gain / loss class at a distance: 0 loses, 1 even, 2 gains — read off the
 *  slope of the delta over a short window either side. */
export function gainClass(source: GainSource, dist: number): number {
  const before = interpolate(source.dist, source.delta_ms, dist - GAIN_HALF_WINDOW_M);
  const after = interpolate(source.dist, source.delta_ms, dist + GAIN_HALF_WINDOW_M);
  const slope = (after - before) / 1000 / (2 * GAIN_HALF_WINDOW_M);
  if (slope > GAIN_THRESHOLD_S_PER_M) return 0; // the gap grows: losing
  if (slope < -GAIN_THRESHOLD_S_PER_M) return 2;
  return 1;
}

/** Speed band edges for a lap: five equal bands across its speed range, in
 *  the user's units and rounded to the nearest 5, so the legend reads as
 *  round figures. Null on a lap without a speed channel. */
export function speedBands(speed: number[] | undefined, units: Units): number[] | null {
  if (!speed || speed.length === 0) return null;
  let lo = Infinity;
  let hi = -Infinity;
  for (const v of speed) {
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi - lo < 1) return null;
  const edges: number[] = [];
  for (let k = 1; k < SPEED_BANDS; k++) {
    const kmh = lo + ((hi - lo) * k) / SPEED_BANDS;
    edges.push(Math.round(speedValue(kmh, units) / 5) * 5);
  }
  return edges;
}

// The band a value falls in: how many of the ascending edges it has
// reached, so a value on an edge belongs to the band above it.
function bandOf(value: number, edges: number[]): number {
  let band = 0;
  while (band < edges.length && value >= edges[band]) band++;
  return band;
}

/** Tire temperature band: 0 cold, 1 working, 2 hot. The tiles read
 *  `t < 55` as cold and `t < 95` as working, so 55 °C is working and
 *  95 °C is hot here too. */
export function tireTempBand(celsius: number): number {
  return bandOf(celsius, [THRESHOLDS.tireTempCold, THRESHOLDS.tireTempHot]);
}

/** Front-rear balance class, rears hotter first: 0 the rears hotter by
 *  TIRE_BALANCE_STRONG_C or more, 1 by more than TIRE_BALANCE_EVEN_C, 2
 *  even, 3 and 4 the same for the fronts. */
export function tireBalanceClass(balance: number): number {
  if (balance >= TIRE_BALANCE_STRONG_C) return 4;
  if (balance > TIRE_BALANCE_EVEN_C) return 3;
  if (balance <= -TIRE_BALANCE_STRONG_C) return 0;
  if (balance < -TIRE_BALANCE_EVEN_C) return 1;
  return 2;
}

// Laps recorded before the per-wheel channels carry no tire temperatures;
// both tire lenses need all four.
function hasTireTemps(series: Samples): boolean {
  return TIRE_COLUMNS.every((c) => Array.isArray(series[c]) && series[c].length > 0);
}

/**
 * Whether a lens has anything to colour by: speed needs the channel, gain /
 * loss a comparison lap with a delta to read a slope from, the tire lenses
 * all four tire temperatures. The one answer the control (which greys a
 * lens out) and the renderer (which falls back to inputs) both go by, so
 * they can never disagree.
 */
export function canDraw(
  lens: MapLens,
  series: Samples & { dist: number[] },
  units: Units,
  gain: GainSource | null,
): boolean {
  if (lens === "speed") return speedBands(series.speed, units) != null;
  if (lens === "gain") return gain != null && gain.dist.length >= 2;
  if (lens === "tire_temp" || lens === "tire_balance") return hasTireTemps(series);
  return true;
}

/**
 * The reference series classified under one lens. Null when the lens has
 * nothing to colour by (see canDraw), so the caller falls back to inputs.
 */
export function lensSeries(
  lens: MapLens,
  series: Samples & { dist: number[] },
  units: Units,
  gain: GainSource | null,
): LensSeries | null {
  if (!canDraw(lens, series, units, gain)) return null;
  const n = series.dist.length;
  if (lens === "speed") {
    const edges = speedBands(series.speed, units)!;
    const unit = speedUnit(units);
    const keys = new Array<number>(n);
    for (let i = 0; i < n; i++) {
      keys[i] = bandOf(speedValue(series.speed[i], units), edges);
    }
    return {
      keys,
      palette: SPEED_COLORS,
      legend: [
        { label: `< ${edges[0]}`, color: SPEED_COLORS[0] },
        ...edges.slice(1).map((edge, i) => ({ label: String(edge), color: SPEED_COLORS[i + 1] })),
        { label: `> ${edges[edges.length - 1]} ${unit}`, color: SPEED_COLORS[SPEED_BANDS - 1] },
      ],
    };
  }
  if (lens === "gain" && gain) {
    const keys = new Array<number>(n);
    for (let i = 0; i < n; i++) keys[i] = gainClass(gain, series.dist[i]);
    const name = gain.label.split(" ")[0];
    return {
      keys,
      palette: GAIN_COLORS,
      legend: [
        { label: `${name} gains`, color: GAIN_COLORS[2] },
        { label: `${name} loses`, color: GAIN_COLORS[0] },
        { label: "even", color: GAIN_COLORS[1] },
      ],
    };
  }
  if (lens === "tire_temp") {
    const { tt_fl, tt_fr, tt_rl, tt_rr } = series;
    const keys = new Array<number>(n);
    for (let i = 0; i < n; i++) {
      keys[i] = tireTempBand(Math.max(tt_fl[i], tt_fr[i], tt_rl[i], tt_rr[i]));
    }
    const { tireTempCold: cold, tireTempHot: hot } = THRESHOLDS;
    return {
      keys,
      palette: TIRE_TEMP_COLORS,
      legend: [
        { label: `< ${cold}`, color: TIRE_TEMP_COLORS[0] },
        { label: `${cold}–${hot}`, color: TIRE_TEMP_COLORS[1] },
        { label: `≥ ${hot} °C`, color: TIRE_TEMP_COLORS[2] },
      ],
    };
  }
  if (lens === "tire_balance") {
    const { tt_fl, tt_fr, tt_rl, tt_rr } = series;
    const keys = new Array<number>(n);
    for (let i = 0; i < n; i++) {
      // Front average minus rear average, as the charts' F−R balance channel.
      keys[i] = tireBalanceClass((tt_fl[i] + tt_fr[i]) / 2 - (tt_rl[i] + tt_rr[i]) / 2);
    }
    const even = TIRE_BALANCE_EVEN_C;
    const strong = TIRE_BALANCE_STRONG_C;
    return {
      keys,
      palette: TIRE_BALANCE_COLORS,
      legend: [
        { label: `rears ≥ ${strong}`, color: TIRE_BALANCE_COLORS[0] },
        { label: `rears ${even}–${strong}`, color: TIRE_BALANCE_COLORS[1] },
        { label: "even", color: TIRE_BALANCE_COLORS[2] },
        { label: `fronts ${even}–${strong}`, color: TIRE_BALANCE_COLORS[3] },
        { label: `fronts ≥ ${strong} °C`, color: TIRE_BALANCE_COLORS[4] },
      ],
    };
  }
  const throttle = series.throttle ?? [];
  const brake = series.brake ?? [];
  const keys = new Array<number>(n);
  for (let i = 0; i < n; i++) keys[i] = zoneOf(throttle[i] ?? 0, brake[i] ?? 0);
  return {
    keys,
    palette: INPUT_COLORS,
    legend: [
      { label: "throttle", color: THROTTLE },
      { label: "brake", color: BRAKE },
      { label: "coast", color: COAST },
    ],
  };
}

export interface LensRun {
  /** Metres along the lap where the run starts and ends. */
  start: number;
  end: number;
  color: string;
}

/** Contiguous stretches of one class, for drawing the lap strip as a few
 *  rectangles rather than one per sample. Each run ends where the next
 *  begins; the last ends on the lap's length. */
export function lensRuns(lens: LensSeries, dist: number[]): LensRun[] {
  const out: LensRun[] = [];
  const n = Math.min(lens.keys.length, dist.length);
  if (n === 0) return out;
  let start = dist[0];
  let key = lens.keys[0];
  for (let i = 1; i < n; i++) {
    if (lens.keys[i] === key) continue;
    out.push({ start, end: dist[i], color: lens.palette[key] });
    start = dist[i];
    key = lens.keys[i];
  }
  out.push({ start, end: dist[n - 1], color: lens.palette[key] });
  return out;
}
