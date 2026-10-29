// Sector boundaries from a lap's split times (#22). Some race screens show
// the sector times as the lap is driven; the lap then says where each
// sector ends with no clicking about on the map — a 1:00 lap whose S1 read
// 0:25 passed the first boundary 25 s in, and the recording knows where the
// car was 25 s in. The times are typed as the game showed them, one per
// sector; the last sector is whatever is left of the lap.

import { t } from "@/i18n";
import { positionAtTime } from "@/lib/playback";

/** "25.123", "0:25.123", "1:02.5" or "25" as milliseconds; null when it
 *  is not a time. */
export function parseClock(text: string): number | null {
  const m = /^\s*(?:(\d+):)?(\d+)(?:[.,](\d{1,3}))?\s*$/.exec(text);
  if (!m) return null;
  const minutes = m[1] ? Number(m[1]) : 0;
  const seconds = Number(m[2]);
  const millis = m[3] ? Number(m[3].padEnd(3, "0")) : 0;
  return minutes * 60_000 + seconds * 1000 + millis;
}

export function formatClock(ms: number): string {
  const total = Math.round(ms);
  const minutes = Math.floor(total / 60_000);
  const seconds = ((total % 60_000) / 1000).toFixed(3);
  return `${minutes}:${seconds.padStart(6, "0")}`;
}

export interface SplitLap {
  /** The recording's samples: the lap's clock in seconds and its path. */
  t: number[];
  pos_x: number[];
  pos_z: number[];
  /** The lap time as the game reported it, in ms. */
  time_ms: number;
}

export type SplitResult =
  | {
      ok: true;
      /** Where each sector ends, in sector order, with the lap's clock there. */
      boundaries: { x: number; z: number; at_ms: number }[];
      /** What is left for the last sector. */
      remainder_ms: number;
    }
  | { ok: false; error: string };

// The game's sector clock and the recording's lap clock start at the same
// line but are read at different moments: a split can land up to a tick
// (1/60 s) off, and a lap time rounds to the millisecond. Nothing here
// needs better than a car length.

/** The boundary positions for the sector times of one lap, each time the
 *  length of that sector as the game shows it. */
export function boundariesFromSplits(lap: SplitLap, sectorsMs: number[]): SplitResult {
  if (sectorsMs.length === 0) return { ok: false, error: t("cornerEditor.splits.errNone") };
  if (lap.t.length < 2 || lap.pos_x.length < 2) {
    return { ok: false, error: t("cornerEditor.splits.errNoPath") };
  }
  if (!(lap.time_ms > 0)) return { ok: false, error: t("cornerEditor.splits.errNoTime") };
  const boundaries: { x: number; z: number; at_ms: number }[] = [];
  let at = 0;
  for (const [i, ms] of sectorsMs.entries()) {
    if (!(ms > 0)) return { ok: false, error: t("cornerEditor.splits.errSectorTime", { n: String(i + 1) }) };
    at += ms;
    if (at >= lap.time_ms) {
      return {
        ok: false,
        error: t("cornerEditor.splits.errPast", {
          at: formatClock(at),
          n: String(i + 1),
          lap: formatClock(lap.time_ms),
        }),
      };
    }
    // The stored clock is anchored on the line, as the game's is: t = 0 is
    // the crossing and the first sample sits half a frame past it, so a
    // split is read exactly where it falls.
    const position = positionAtTime(lap, at / 1000);
    if (!position) return { ok: false, error: t("cornerEditor.splits.errNoPosition", { at: formatClock(at) }) };
    boundaries.push({ x: Math.round(position[0] * 10) / 10, z: Math.round(position[1] * 10) / 10, at_ms: at });
  }
  return { ok: true, boundaries, remainder_ms: lap.time_ms - at };
}
