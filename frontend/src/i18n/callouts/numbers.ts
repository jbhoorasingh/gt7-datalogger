// Language-neutral number handling for callouts.
//
// English callouts spell every number out (backend formatter.py) because
// voices read "1:32.487" as "one colon thirty-two point four eight seven".
// Other languages get digits in the form their voices read naturally — "32,5"
// for Spanish and Portuguese, "1分32秒5" for Japanese — so the work here is
// only rounding the same way the server does, then formatting per language.

import type { Places, Rounding } from "./types";

export const METERS_PER_FOOT = 0.3048;
export const KMH_PER_MPH = 1.609344;

/**
 * A duration quantized to `places` decimals of a second, as whole minutes,
 * whole seconds and the fraction digits. Rounding happens once on the
 * milliseconds and then carries, so 59.96 s at one place is 1:00.0.
 */
export interface SplitTime {
  minutes: number;
  seconds: number;
  /** The fraction, zero-padded to `places` digits ("487", "05"). */
  fraction: string;
  /** Total in seconds after quantizing, for plural choice and "level". */
  total: number;
}

export function quantize(ms: number, places: Places, rounding: Rounding): number {
  const unit = 10 ** (3 - places); // milliseconds per last digit
  const magnitude = Math.abs(ms) / unit;
  // ROUND_HALF_UP like the server's Decimal quantize; the small epsilon keeps
  // 92.45 (stored as 92.4499999…) from rounding down.
  const steps = rounding === "truncate" ? Math.floor(magnitude + 1e-9) : Math.floor(magnitude + 0.5 + 1e-9);
  return steps / 10 ** places;
}

export function splitTime(ms: number, places: Places, rounding: Rounding): SplitTime {
  const total = quantize(ms, places, rounding);
  const scaled = Math.round(total * 10 ** places);
  const wholeSeconds = Math.floor(scaled / 10 ** places);
  const fraction = String(scaled % 10 ** places).padStart(places, "0");
  return {
    minutes: Math.floor(wholeSeconds / 60),
    seconds: wholeSeconds % 60,
    fraction,
    total,
  };
}

/** A decimal with the language's separator; a zero fraction is dropped. */
export function decimal(lang: string, value: number, places = 1): string {
  return new Intl.NumberFormat(lang, {
    maximumFractionDigits: places,
    minimumFractionDigits: 0,
    useGrouping: false,
  }).format(Math.round(value * 10 ** places) / 10 ** places);
}

/** A decimal with exactly `places` digits after the separator. */
export function fixed(lang: string, value: number, places: number): string {
  return new Intl.NumberFormat(lang, {
    maximumFractionDigits: places,
    minimumFractionDigits: places,
    useGrouping: false,
  }).format(value);
}

/** Tenths of a second in a sub-second gap, never zero (see spoken_gap). */
export function tenths(ms: number): number {
  return Math.max(1, Math.round(Math.abs(ms) / 100));
}

/**
 * A gap said without a set precision: whole tenths under a second, else
 * seconds to one decimal. It is rounded before the unit is chosen, so 970 ms
 * is ten tenths and carries to one second — the way quantize() carries for
 * the sector-voice precision — rather than coming out as "10 tenths".
 */
export function spokenGap(ms: number): { unit: "tenths" | "seconds"; value: number } {
  const n = tenths(ms);
  return n < 10 ? { unit: "tenths", value: n } : { unit: "seconds", value: n / 10 };
}

export function feet(meters: number): number {
  return Math.round(meters / METERS_PER_FOOT);
}

export function mph(kmh: number): number {
  return Math.round(kmh / KMH_PER_MPH);
}

const rules = new Map<string, Intl.PluralRules>();

/** CLDR plural category of a number as it will be said. */
export function pluralOf(lang: string, value: number): Intl.LDMLPluralRule {
  let r = rules.get(lang);
  if (!r) {
    r = new Intl.PluralRules(lang);
    rules.set(lang, r);
  }
  return r.select(value);
}

/** Whether the text a phrasebook produced is fit to speak. */
export function speakable(text: unknown): text is string {
  return typeof text === "string" && text.trim() !== "" && !/undefined|NaN|null|\[object/.test(text);
}
