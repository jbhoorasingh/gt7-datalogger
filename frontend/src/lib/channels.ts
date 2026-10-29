// Channel catalog for the Analysis stacked charts (Tier 1).
//
// A channel is one panel: one line per lap. Raw sample columns map 1:1;
// derived channels (front/rear averages, F−R balance) declare the raw columns
// they need and a per-index derive function, so the compare API only fetches
// what the visible panels actually use. The default set is the classic
// pre-Tier-1 panel stack; the picker persists to localStorage and the URL.

import { t, type MessageKey } from "@/i18n";
import type { Units } from "@/lib/format";
import { formatInt, speedUnit, speedValue } from "@/lib/format";
import type { Samples } from "@/lib/types";

export type ChannelGroup = "driving" | "tires" | "chassis" | "engine" | "race";

// Display order of the groups, shared by the picker and the Analysis guide.
export const CHANNEL_GROUPS: { id: ChannelGroup; labelKey: MessageKey }[] = [
  { id: "driving", labelKey: "channel.group.driving" },
  { id: "engine", labelKey: "channel.group.engine" },
  { id: "chassis", labelKey: "channel.group.chassis" },
  { id: "tires", labelKey: "channel.group.tires" },
  { id: "race", labelKey: "channel.group.race" },
];

// Broadcast accelerometer channels carry no documented unit; the compare
// endpoint fits them against physics and returns a multiplier to g. This is
// the fallback the stacked charts use before that fit lands (and when it is
// too weak to trust): assume m/s², which is what the fit usually confirms.
const G = 9.80665;

export interface ChannelDef {
  key: string;
  titleKey: MessageKey;
  // What the channel shows and how to read it, in a sentence or two — the
  // picker's tooltip and the Analysis guide both show it. Required, so a new
  // channel cannot ship unexplained.
  descriptionKey: MessageKey;
  // What a recording needs to carry this channel at all, when not every one
  // does (a wider packet format, a race). Absent = every recording has it.
  needsKey?: MessageKey;
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
const CORNER_SLIP: Record<(typeof CORNER)[number], { titleKey: MessageKey; descriptionKey: MessageKey }> = {
  fl: { titleKey: "channel.slipFl.title", descriptionKey: "channel.slipFl.description" },
  fr: { titleKey: "channel.slipFr.title", descriptionKey: "channel.slipFr.description" },
  rl: { titleKey: "channel.slipRl.title", descriptionKey: "channel.slipRl.description" },
  rr: { titleKey: "channel.slipRr.title", descriptionKey: "channel.slipRr.description" },
};

// Optional sample columns and what a recording needs to have them (see
// OPTIONAL_COLUMNS in backend/app/processing/laps.py).
const PACKET_B: MessageKey = "channel.needs.packetB";
const PACKET_TILDE: MessageKey = "channel.needs.packetTilde";
const PACKET_C: MessageKey = "channel.needs.packetC";

export const CHANNELS: ChannelDef[] = [
  // --- Driving (the classic set) ---
  {
    key: "speed",
    titleKey: "channel.speed.title",
    descriptionKey: "channel.speed.description",
    group: "driving",
    height: 1.6,
    transform: speedValue,
  },
  {
    key: "throttle",
    titleKey: "channel.throttle.title",
    descriptionKey: "channel.throttle.description",
    group: "driving",
    height: 0.8,
  },
  {
    key: "brake",
    titleKey: "channel.brake.title",
    descriptionKey: "channel.brake.description",
    group: "driving",
    height: 0.8,
  },
  {
    key: "coast",
    titleKey: "channel.coast.title",
    descriptionKey: "channel.coast.description",
    group: "driving",
    height: 0.5,
    step: true,
  },
  {
    key: "gear",
    titleKey: "channel.gear.title",
    descriptionKey: "channel.gear.description",
    group: "driving",
    height: 0.7,
    step: true,
    // The suggestion rides along for the dashed line; the panel's own line
    // is still `gear`.
    columns: ["gear", "suggested_gear"],
  },
  {
    key: "rpm",
    titleKey: "channel.rpm.title",
    descriptionKey: "channel.rpm.description",
    group: "engine",
    height: 1,
  },
  {
    key: "boost",
    titleKey: "channel.boost.title",
    descriptionKey: "channel.boost.description",
    group: "engine",
    height: 0.7,
  },
  // Delivered engine torque (#150), normalized as GT7 broadcasts it, so a
  // torque trace sits under the throttle trace without any car data.
  {
    key: "engine_torque",
    titleKey: "channel.engineTorque.title",
    descriptionKey: "channel.engineTorque.description",
    needsKey: PACKET_TILDE,
    group: "engine",
    height: 0.8,
  },
  {
    key: "yaw_rate",
    titleKey: "channel.yawRate.title",
    descriptionKey: "channel.yawRate.description",
    group: "driving",
    height: 0.8,
  },
  // Steering, at last (#15): understeer is more lock with no more rotation,
  // and that only shows next to the yaw-rate trace above.
  {
    key: "steer",
    titleKey: "channel.steer.title",
    descriptionKey: "channel.steer.description",
    needsKey: PACKET_B,
    group: "driving",
    height: 0.8,
  },
  // What the road wheels did with that input (#155). Beside Steering, the
  // point where the input keeps rising and this stops is the steering lock.
  {
    key: "wheel_angle",
    titleKey: "channel.wheelAngle.title",
    descriptionKey: "channel.wheelAngle.description",
    needsKey: PACKET_C,
    group: "driving",
    height: 0.8,
  },

  // --- Race ---
  // Only recorded while GT7 reports positions (#60): absent on time trials,
  // so the panel simply stays empty there. Steps, never slopes — you are
  // P4 or P3, not P3.5.
  {
    key: "race_pos",
    titleKey: "channel.racePos.title",
    descriptionKey: "channel.racePos.description",
    needsKey: "channel.needs.race",
    group: "race",
    height: 0.6,
    step: true,
  },

  // --- Driver aids, measured rather than inferred (#18) ---
  // The pedal AFTER the aids acted on it. Plotted against the raw pedal these
  // two lines separate exactly where ABS/TCS intervened; the derived channels
  // below are that separation on its own.
  {
    key: "throttle_f",
    titleKey: "channel.throttleF.title",
    descriptionKey: "channel.throttleF.description",
    needsKey: PACKET_TILDE,
    group: "driving",
    height: 0.8,
  },
  {
    key: "brake_f",
    titleKey: "channel.brakeF.title",
    descriptionKey: "channel.brakeF.description",
    needsKey: PACKET_TILDE,
    group: "driving",
    height: 0.8,
  },
  {
    key: "tcs_cut",
    titleKey: "channel.tcsCut.title",
    descriptionKey: "channel.tcsCut.description",
    needsKey: PACKET_TILDE,
    group: "driving",
    height: 0.7,
    columns: ["throttle", "throttle_f"],
    derive: (s, i) => Math.max(0, (s.throttle?.[i] ?? 0) - (s.throttle_f?.[i] ?? 0)),
  },
  {
    key: "abs_release",
    titleKey: "channel.absRelease.title",
    descriptionKey: "channel.absRelease.description",
    needsKey: PACKET_TILDE,
    group: "driving",
    height: 0.7,
    columns: ["brake", "brake_f"],
    derive: (s, i) => Math.max(0, (s.brake?.[i] ?? 0) - (s.brake_f?.[i] ?? 0)),
  },

  // --- Accelerometer (#16). The g-g panel calibrates these properly; as
  // traces they assume m/s², which is what the fit reports on every capture
  // seen so far. Vertical has no independent reference to check it against.
  {
    key: "acc_lat",
    titleKey: "channel.accLat.title",
    descriptionKey: "channel.accLat.description",
    needsKey: PACKET_B,
    group: "chassis",
    height: 0.9,
    derive: (s, i) => (s.acc_lat?.[i] ?? 0) / G,
    columns: ["acc_lat"],
  },
  {
    key: "acc_long",
    titleKey: "channel.accLong.title",
    descriptionKey: "channel.accLong.description",
    needsKey: PACKET_B,
    group: "chassis",
    height: 0.9,
    derive: (s, i) => (s.acc_long?.[i] ?? 0) / G,
    columns: ["acc_long"],
  },
  {
    key: "acc_vert",
    titleKey: "channel.accVert.title",
    descriptionKey: "channel.accVert.description",
    needsKey: PACKET_B,
    group: "chassis",
    height: 0.7,
    derive: (s, i) => (s.acc_vert?.[i] ?? 0) / G,
    columns: ["acc_vert"],
  },

  // --- Balance (#109). Signed, so the two directions of a slide are told
  // apart; which of them is "into the corner" depends on the corner, and the
  // corner report card turns the sign to say so.
  {
    key: "body_slip",
    titleKey: "channel.bodySlip.title",
    descriptionKey: "channel.bodySlip.description",
    needsKey: "channel.needs.v070",
    group: "chassis",
    height: 0.9,
  },
  // Kinematic balance (#155), derived server-side from the full-rate ticks:
  // speed × front wheel angle ÷ wheelbase is the yaw the wheels asked for.
  {
    key: "kin_yaw",
    titleKey: "channel.kinYaw.title",
    descriptionKey: "channel.kinYaw.description",
    needsKey: PACKET_C,
    group: "chassis",
    height: 0.8,
  },
  {
    key: "yaw_balance",
    titleKey: "channel.yawBalance.title",
    descriptionKey: "channel.yawBalance.description",
    needsKey: PACKET_C,
    group: "chassis",
    height: 0.9,
  },

  // --- Tires & wheels ---
  {
    key: "tire_slip",
    titleKey: "channel.tireSlip.title",
    descriptionKey: "channel.tireSlip.description",
    group: "tires",
    height: 0.8,
  },
  {
    key: "slip_front",
    titleKey: "channel.slipFront.title",
    descriptionKey: "channel.slipFront.description",
    group: "tires",
    height: 0.8,
    columns: ["slip_fl", "slip_fr"],
    derive: (s, i) => avg2(s.slip_fl?.[i], s.slip_fr?.[i]),
  },
  {
    key: "slip_rear",
    titleKey: "channel.slipRear.title",
    descriptionKey: "channel.slipRear.description",
    group: "tires",
    height: 0.8,
    columns: ["slip_rl", "slip_rr"],
    derive: (s, i) => avg2(s.slip_rl?.[i], s.slip_rr?.[i]),
  },
  ...CORNER.map(
    (w): ChannelDef => ({
      key: `slip_${w}`,
      ...CORNER_SLIP[w],
      group: "tires",
      height: 0.8,
    }),
  ),
  {
    key: "tt_front",
    titleKey: "channel.ttFront.title",
    descriptionKey: "channel.ttFront.description",
    group: "tires",
    height: 0.8,
    columns: ["tt_fl", "tt_fr"],
    derive: (s, i) => avg2(s.tt_fl?.[i], s.tt_fr?.[i]),
  },
  {
    key: "tt_rear",
    titleKey: "channel.ttRear.title",
    descriptionKey: "channel.ttRear.description",
    group: "tires",
    height: 0.8,
    columns: ["tt_rl", "tt_rr"],
    derive: (s, i) => avg2(s.tt_rl?.[i], s.tt_rr?.[i]),
  },
  {
    // One curve that answers "understeer-hot or oversteer-hot?" directly
    key: "tt_balance",
    titleKey: "channel.ttBalance.title",
    descriptionKey: "channel.ttBalance.description",
    group: "tires",
    height: 0.8,
    columns: ["tt_fl", "tt_fr", "tt_rl", "tt_rr"],
    derive: (s, i) =>
      avg2(s.tt_fl?.[i], s.tt_fr?.[i]) - avg2(s.tt_rl?.[i], s.tt_rr?.[i]),
  },

  // --- Chassis ---
  {
    key: "body_height",
    titleKey: "channel.bodyHeight.title",
    descriptionKey: "channel.bodyHeight.description",
    group: "chassis",
    height: 0.7,
  },
  {
    key: "sus_front",
    titleKey: "channel.susFront.title",
    descriptionKey: "channel.susFront.description",
    group: "chassis",
    height: 0.8,
    columns: ["sus_fl", "sus_fr"],
    derive: (s, i) => avg2(s.sus_fl?.[i], s.sus_fr?.[i]),
  },
  {
    key: "sus_rear",
    titleKey: "channel.susRear.title",
    descriptionKey: "channel.susRear.description",
    group: "chassis",
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

// Channel-aware value formatting, shared by the chart tooltip, the per-strip
// playhead readouts and the race line map's hover readout. Keyed by panel, so
// the time diff and GT7's suggested gear format too.
export function formatValueFor(units: Units) {
  return (key: string, v: number): string => {
      if (key.startsWith("slip_") || key === "tire_slip") return `${v.toFixed(2)}×`;
      if (key.startsWith("tt_")) {
        const sign = key === "tt_balance" && v > 0 ? "+" : "";
        return `${sign}${v.toFixed(1)} °C`;
      }
      if (key.startsWith("sus_") || key === "body_height") return `${v.toFixed(1)} mm`;
      switch (key) {
        case "delta":
          return `${v >= 0 ? "+" : ""}${v.toFixed(3)} s`;
        case "speed":
          return `${Math.round(v)} ${speedUnit(units)}`;
        case "throttle":
        case "brake":
          return `${Math.round(v)}%`;
        case "coast":
          return v >= 0.5 ? t("channel.coast.on") : "—";
        case "gear":
        case "suggested_gear":
          return `${Math.round(v)}`;
        case "rpm":
          return `${formatInt(v)} rpm`;
        case "boost":
          return `${v.toFixed(2)} bar`;
        case "yaw_rate":
        case "kin_yaw":
        case "yaw_balance":
          return `${v.toFixed(2)} rad/s`;
        case "wheel_angle":
          // A road wheel turns a few hundredths of a radian.
          return `${v.toFixed(3)} rad`;
        default:
          return v.toFixed(2);
      }
  };
}

// A readout's rows for these panels: one per panel, labelled without the
// unit its values already carry, and under Gear the gear GT7 suggested. The
// chart tooltip and the map's hover readout both list these, so they read
// alike.
export function readoutLines(panels: { key: string; titleKey: MessageKey }[]): { key: string; label: string }[] {
  return panels.flatMap((panel) => {
    const line = { key: panel.key, label: t(panel.titleKey).replace(/ \(.*\)| %/, "") };
    return panel.key === "gear" ? [line, { key: "suggested_gear", label: t("channel.gear.suggested") }] : [line];
  });
}
