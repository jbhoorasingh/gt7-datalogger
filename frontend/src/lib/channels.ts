// Channel catalog for the Analysis stacked charts (Tier 1).
//
// A channel is one panel: one line per lap. Raw sample columns map 1:1;
// derived channels (front/rear averages, F−R balance) declare the raw columns
// they need and a per-index derive function, so the compare API only fetches
// what the visible panels actually use. The default set is the classic
// pre-Tier-1 panel stack; the picker persists to localStorage and the URL.

import type { Units } from "@/lib/format";
import { speedValue } from "@/lib/format";
import type { Samples } from "@/lib/types";

export type ChannelGroup = "Driving" | "Tires & wheels" | "Chassis" | "Engine";

// Broadcast accelerometer channels carry no documented unit; the compare
// endpoint fits them against physics and returns a multiplier to g. This is
// the fallback the stacked charts use before that fit lands (and when it is
// too weak to trust): assume m/s², which is what the fit usually confirms.
const G = 9.80665;

export interface ChannelDef {
  key: string;
  title: string;
  group: ChannelGroup;
  height: number; // relative panel weight
  step?: boolean;
  transform?: (v: number, units: Units) => number;
  // Raw sample columns this channel needs (defaults to [key])
  columns?: string[];
  // Derived channel: computed per resampled index from the raw series
  derive?: (series: Samples, i: number) => number;
  format?: (v: number, units: Units) => string;
}

const avg2 = (a: number | undefined, b: number | undefined): number =>
  ((a ?? 0) + (b ?? 0)) / 2;

const CORNER = ["fl", "fr", "rl", "rr"] as const;

export const CHANNELS: ChannelDef[] = [
  // --- Driving (the classic set) ---
  { key: "speed", title: "Speed", group: "Driving", height: 1.6, transform: speedValue },
  { key: "throttle", title: "Throttle %", group: "Driving", height: 0.8 },
  { key: "brake", title: "Brake %", group: "Driving", height: 0.8 },
  { key: "coast", title: "Coasting", group: "Driving", height: 0.5, step: true },
  { key: "gear", title: "Gear", group: "Driving", height: 0.7, step: true },
  { key: "rpm", title: "RPM", group: "Engine", height: 1 },
  { key: "boost", title: "Boost (bar)", group: "Engine", height: 0.7 },
  { key: "yaw_rate", title: "Yaw rate (rad/s)", group: "Driving", height: 0.8 },
  // Steering, at last (#15): understeer is more lock with no more rotation,
  // and that only shows next to the yaw-rate trace above.
  { key: "steer", title: "Steering (rad)", group: "Driving", height: 0.8 },

  // --- Driver aids, measured rather than inferred (#18) ---
  // The pedal AFTER the aids acted on it. Plotted against the raw pedal these
  // two lines separate exactly where ABS/TCS intervened; the derived channels
  // below are that separation on its own.
  { key: "throttle_f", title: "Throttle applied %", group: "Driving", height: 0.8 },
  { key: "brake_f", title: "Brake applied %", group: "Driving", height: 0.8 },
  {
    key: "tcs_cut",
    title: "TCS cut (% throttle)",
    group: "Driving",
    height: 0.7,
    columns: ["throttle", "throttle_f"],
    derive: (s, i) => Math.max(0, (s.throttle?.[i] ?? 0) - (s.throttle_f?.[i] ?? 0)),
  },
  {
    key: "abs_release",
    title: "ABS release (% brake)",
    group: "Driving",
    height: 0.7,
    columns: ["brake", "brake_f"],
    derive: (s, i) => Math.max(0, (s.brake?.[i] ?? 0) - (s.brake_f?.[i] ?? 0)),
  },

  // --- Accelerometer (#16). The g-g panel calibrates these properly; as
  // traces they assume m/s², which is what the fit reports on every capture
  // seen so far. Vertical has no independent reference to check it against.
  {
    key: "acc_lat",
    title: "Lateral g",
    group: "Chassis",
    height: 0.9,
    derive: (s, i) => (s.acc_lat?.[i] ?? 0) / G,
    columns: ["acc_lat"],
  },
  {
    key: "acc_long",
    title: "Longitudinal g",
    group: "Chassis",
    height: 0.9,
    derive: (s, i) => (s.acc_long?.[i] ?? 0) / G,
    columns: ["acc_long"],
  },
  {
    key: "acc_vert",
    title: "Vertical g",
    group: "Chassis",
    height: 0.7,
    derive: (s, i) => (s.acc_vert?.[i] ?? 0) / G,
    columns: ["acc_vert"],
  },

  // --- Tires & wheels ---
  { key: "tire_slip", title: "Tire spd / car spd", group: "Tires & wheels", height: 0.8 },
  {
    key: "slip_front",
    title: "Slip — front avg",
    group: "Tires & wheels",
    height: 0.8,
    columns: ["slip_fl", "slip_fr"],
    derive: (s, i) => avg2(s.slip_fl?.[i], s.slip_fr?.[i]),
  },
  {
    key: "slip_rear",
    title: "Slip — rear avg",
    group: "Tires & wheels",
    height: 0.8,
    columns: ["slip_rl", "slip_rr"],
    derive: (s, i) => avg2(s.slip_rl?.[i], s.slip_rr?.[i]),
  },
  ...CORNER.map(
    (w): ChannelDef => ({
      key: `slip_${w}`,
      title: `Slip — ${w.toUpperCase()}`,
      group: "Tires & wheels",
      height: 0.8,
    }),
  ),
  {
    key: "tt_front",
    title: "Tire temp — front avg (°C)",
    group: "Tires & wheels",
    height: 0.8,
    columns: ["tt_fl", "tt_fr"],
    derive: (s, i) => avg2(s.tt_fl?.[i], s.tt_fr?.[i]),
  },
  {
    key: "tt_rear",
    title: "Tire temp — rear avg (°C)",
    group: "Tires & wheels",
    height: 0.8,
    columns: ["tt_rl", "tt_rr"],
    derive: (s, i) => avg2(s.tt_rl?.[i], s.tt_rr?.[i]),
  },
  {
    // One curve that answers "understeer-hot or oversteer-hot?" directly
    key: "tt_balance",
    title: "Tire temp — F−R balance (°C)",
    group: "Tires & wheels",
    height: 0.8,
    columns: ["tt_fl", "tt_fr", "tt_rl", "tt_rr"],
    derive: (s, i) =>
      avg2(s.tt_fl?.[i], s.tt_fr?.[i]) - avg2(s.tt_rl?.[i], s.tt_rr?.[i]),
  },

  // --- Chassis ---
  { key: "body_height", title: "Ride height (mm)", group: "Chassis", height: 0.7 },
  {
    key: "sus_front",
    title: "Susp travel — front avg (mm)",
    group: "Chassis",
    height: 0.8,
    columns: ["sus_fl", "sus_fr"],
    derive: (s, i) => avg2(s.sus_fl?.[i], s.sus_fr?.[i]),
  },
  {
    key: "sus_rear",
    title: "Susp travel — rear avg (mm)",
    group: "Chassis",
    height: 0.8,
    columns: ["sus_rl", "sus_rr"],
    derive: (s, i) => avg2(s.sus_rl?.[i], s.sus_rr?.[i]),
  },
];

export const CHANNEL_BY_KEY: Record<string, ChannelDef> = Object.fromEntries(
  CHANNELS.map((c) => [c.key, c]),
);

// The classic pre-Tier-1 panel stack (delta is always shown and not a channel)
export const DEFAULT_CHANNEL_KEYS = [
  "speed", "throttle", "brake", "coast", "gear", "rpm", "boost", "tire_slip", "yaw_rate",
];

const STORAGE_KEY = "gt7-analysis-channels";

export function loadChannelKeys(): string[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed: unknown = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        const valid = parsed.filter((k): k is string => typeof k === "string" && k in CHANNEL_BY_KEY);
        if (valid.length > 0) return valid;
      }
    }
  } catch {
    // fall through
  }
  return [...DEFAULT_CHANNEL_KEYS];
}

export function saveChannelKeys(keys: string[]): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(keys));
}

export function isDefaultChannelSet(keys: string[]): boolean {
  return (
    keys.length === DEFAULT_CHANNEL_KEYS.length &&
    keys.every((k, i) => k === DEFAULT_CHANNEL_KEYS[i])
  );
}

// Raw sample columns the compare API must return for these channels.
// "aids" rides along so TCS/ASM overlay bands and event context are available.
export function columnsForChannels(keys: string[]): string[] {
  const cols = new Set<string>(["aids"]);
  for (const key of keys) {
    const def = CHANNEL_BY_KEY[key];
    if (!def) continue;
    for (const c of def.columns ?? [key]) cols.add(c);
  }
  return [...cols];
}

// Values for one lap's panel line; null when the lap lacks the data
// (e.g. pre-Tier-1 recordings).
export function channelValues(def: ChannelDef, series: Samples): number[] | null {
  if (def.derive) {
    const needed = def.columns ?? [];
    if (!needed.every((c) => Array.isArray(series[c]) && series[c].length > 0)) return null;
    const n = Math.min(...needed.map((c) => series[c].length));
    return Array.from({ length: n }, (_, i) => def.derive!(series, i));
  }
  const raw = series[def.key];
  return Array.isArray(raw) && raw.length > 0 ? raw : null;
}
