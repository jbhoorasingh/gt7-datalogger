// Pure helpers behind the Sessions view: the date groups of the session list,
// the free-text filter, and which lap-table columns say nothing because they
// read the same on every lap.

import type { LapSummary, SessionSummary } from "@/lib/types";

const DAY_MS = 86_400_000;

function startOfDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

/** Whole calendar days between `iso` and `now`, 0 = today; null if unparseable. */
function daysAgo(iso: string, now: Date): number | null {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return null;
  // Rounded rather than floored: a DST change makes one day 23 or 25 hours.
  return Math.round((startOfDay(now) - startOfDay(d)) / DAY_MS);
}

/** "Today" / "Yesterday" / "Earlier this week" / "Earlier this month" / "August 2026". */
export function dateGroupLabel(iso: string, now: Date = new Date()): string {
  const days = daysAgo(iso, now);
  if (days == null) return "Undated";
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return "Earlier this week";
  const d = new Date(iso);
  if (d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth()) {
    return "Earlier this month";
  }
  return d.toLocaleDateString(undefined, { month: "long", year: "numeric" });
}

/** Consecutive runs of the (newest-first) list under their date label. */
export function groupByDate<T extends { started_at: string }>(
  items: T[],
  now: Date = new Date(),
): { label: string; items: T[] }[] {
  const groups: { label: string; items: T[] }[] = [];
  for (const item of items) {
    const label = dateGroupLabel(item.started_at, now);
    const last = groups[groups.length - 1];
    if (last && last.label === label) last.items.push(item);
    else groups.push({ label, items: [item] });
  }
  return groups;
}

/** The short "when" beside a session: a clock time today and yesterday, a
 *  weekday for the rest of the week, a date before that. */
export function sessionWhen(iso: string, now: Date = new Date()): string {
  const days = daysAgo(iso, now);
  if (days == null) return iso;
  const d = new Date(iso);
  if (days <= 1) return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  if (days < 7) return d.toLocaleDateString(undefined, { weekday: "short" });
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** "today 12:58" / "yesterday 21:40" / "Tue 12 Sep 12:58" — for the detail kicker. */
export function sessionStarted(iso: string, now: Date = new Date()): string {
  const days = daysAgo(iso, now);
  if (days == null) return iso;
  const d = new Date(iso);
  const time = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  if (days <= 0) return `today ${time}`;
  if (days === 1) return `yesterday ${time}`;
  const date = d.toLocaleDateString(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
    ...(d.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }),
  });
  return `${date} ${time}`;
}

/**
 * The toolbar's filter box: every word must match. A word starting with "#"
 * matches a tag (by prefix); any other word matches the car, its maker, the
 * circuit, the category or a tag, anywhere in them.
 */
export function matchesSessionFilter(s: SessionSummary, query: string): boolean {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return true;
  const tags = (s.tags ?? []).map((t) => t.toLowerCase());
  const text = [s.car_name, s.car_full_name, s.car_manufacturer, s.track_name, s.car_category]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return words.every((w) => {
    if (w.startsWith("#")) {
      const t = w.slice(1);
      return t === "" || tags.some((x) => x.startsWith(t));
    }
    return text.includes(w) || tags.some((x) => x.includes(w));
  });
}

// --- Lap table ---------------------------------------------------------------

/** Columns the lap table may leave out, in display order. */
export type LapColumn = "pos" | "fuel" | "throttle" | "brake" | "coast" | "spin" | "events" | "speed";

export interface LapColumnDef {
  id: LapColumn;
  label: string;
  /** As shown in the "Same on every lap, hidden:" line. */
  short: string;
  /** The value as the cell renders it: two laps that print the same ARE the same. */
  text: (lap: LapSummary) => string;
}

// Kept free of units so the helper stays pure; the speed column prints its
// own unit in the view and compares on whole km/h here.
export const LAP_COLUMNS: LapColumnDef[] = [
  {
    id: "pos",
    label: "Pos",
    short: "position",
    text: (l) => ((l.race_position ?? -1) >= 1 ? `P${l.race_position}` : "–"),
  },
  { id: "fuel", label: "Fuel", short: "fuel", text: (l) => `${l.fuel_consumed.toFixed(2)} L` },
  {
    id: "throttle",
    label: "Full thr.",
    short: "full throttle",
    text: (l) => `${l.full_throttle_pct.toFixed(0)}%`,
  },
  {
    id: "brake",
    label: "Full brk",
    short: "full brake",
    text: (l) => `${l.full_brake_pct.toFixed(0)}%`,
  },
  { id: "coast", label: "Coast", short: "coast", text: (l) => `${l.coasting_pct.toFixed(0)}%` },
  { id: "spin", label: "Spin", short: "spin", text: (l) => `${l.tire_spin_pct.toFixed(0)}%` },
  { id: "events", label: "Events", short: "events", text: (l) => formatEventCounts(l.event_counts) },
  { id: "speed", label: "Max spd", short: "max speed", text: (l) => `${Math.round(l.max_speed)}` },
];

/**
 * Columns whose value prints the same on every counting lap, with that value.
 * Laps that don't count are left out of the judgement — an out-lap's fuel or
 * throttle says nothing about the run. Fewer than two counting laps decide
 * nothing, so nothing is hidden. Position is only a column in a race, and an
 * all-dash column is "constant" too.
 */
export function constantColumns(laps: LapSummary[]): Map<LapColumn, string> {
  const counting = laps.filter((l) => l.counts_for_best !== false);
  const out = new Map<LapColumn, string>();
  if (counting.length < 2) {
    // Still drop a position column no lap has anything for.
    if (!laps.some((l) => (l.race_position ?? -1) >= 1)) out.set("pos", "–");
    return out;
  }
  for (const col of LAP_COLUMNS) {
    const first = col.text(counting[0]);
    if (counting.every((l) => col.text(l) === first)) out.set(col.id, first);
  }
  return out;
}

/** "2L·1S·3B" — lockups, wheelspins, bottoming, kerbs; dash when clean/unknown. */
export function formatEventCounts(counts?: Record<string, number>): string {
  return eventParts(counts).map((p) => `${p.count}${p.letter}`).join("·") || "–";
}

export const EVENT_LETTERS = [
  ["lockup", "L"],
  ["wheelspin", "S"],
  ["bottoming", "B"],
  ["kerb", "K"],
] as const;

export function eventParts(
  counts?: Record<string, number>,
): { type: string; letter: string; count: number }[] {
  if (!counts) return [];
  return EVENT_LETTERS.filter(([type]) => (counts[type] ?? 0) > 0).map(([type, letter]) => ({
    type,
    letter,
    count: counts[type],
  }));
}
