// Which laps the Analysis view compares once a session's laps have loaded.
//
// Pulled out of the view because the rule has a case the view got wrong for
// as long as it lived inline: a session with NO laps. The loader returned
// before touching the selection, so whatever had been selected for the session
// open before stayed selected — and since a selected lap that is not one of
// the session's own is, by design, a guest from another session (#26), the
// previous session's laps were adopted as guests of the new one. The title
// said #260 and the map, the charts and the address bar said #259. Every
// session is in that state until its first lap is finished, which is exactly
// when someone who has just started driving looks.

import type { LapSummary, SessionSummary } from "@/lib/types";

/** The lap a session is measured against: its quickest that counts towards
 *  the bests, or its quickest of any kind when none does. */
export function quickestCounting(laps: LapSummary[]): LapSummary | undefined {
  const counting = laps.filter((l) => l.counts_for_best !== false);
  return [...(counting.length > 0 ? counting : laps)].sort((a, b) => a.time_ms - b.time_ms)[0];
}

export interface SelectionRule {
  /** The session's own chartable laps, newest first. May be empty. */
  laps: LapSummary[];
  /** Whether the current selection was CHOSEN — by the user toggling laps or
   *  by a deep link — rather than following "latest vs best" by itself. */
  manual: boolean;
  /** Whether an id may stay selected: one of the session's own laps, a guest,
   *  or an id not yet resolved into either. */
  keep: (id: number) => boolean;
}

/**
 * The selected laps for a session whose laps have just loaded.
 *
 * A chosen selection stands for as long as any of it is still valid. Anything
 * else follows the session: its latest lap against its best, or nothing at
 * all when it has no laps — never what was selected for another session.
 */
export function resolveSelected(current: number[], rule: SelectionRule): number[] {
  const stillValid = current.filter(rule.keep);
  if (rule.manual && stillValid.length > 0) return stillValid;
  const latest = rule.laps[0];
  const best = quickestCounting(rule.laps);
  if (latest == null || best == null) return [];
  return [...new Set([latest.id, best.id])];
}

/** The reference lap, by the same rule: a chosen one stands while it is
 *  valid, otherwise the session's best, otherwise none. */
export function resolveReference(current: number | null, rule: SelectionRule): number | null {
  if (rule.manual && current != null && rule.keep(current)) return current;
  return quickestCounting(rule.laps)?.id ?? null;
}

/**
 * The session Analysis opens on, once the list has loaded: the one it had,
 * if that session still exists, otherwise the newest with laps to chart.
 *
 * "Still exists" is the part that was missing. The tab remembers the last
 * session across visits, and a session deleted since (or wiped by "Delete
 * all recorded data") left the view on an id with nothing behind it, so a
 * newer session never appeared until it was opened from Sessions.
 */
export function openingSession(current: number | null, sessions: SessionSummary[]): number | null {
  if (current != null && sessions.some((s) => s.id === current)) return current;
  return sessions.find((s) => s.lap_count > 0)?.id ?? sessions[0]?.id ?? null;
}
