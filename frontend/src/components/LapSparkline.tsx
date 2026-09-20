// Mini lap-time sparkline for a session row: quick visual identity for the
// session (consistency, where the best lap fell). Fetches laps lazily with a
// small cache keyed on lap_count so live updates refresh it.
//
// Under it, the session's consistency as a figure (#112): the spread of the
// lap times that count. The line shows the shape of a session, out-laps and
// all; the figure is the part of that shape worth comparing between sessions.

import { useEffect, useState } from "react";
import { Tip } from "@/components/ui/Tooltip";
import { api } from "@/lib/api";
import { consistencyBand, formatSpread, lapConsistency } from "@/lib/consistency";
import { formatLapTime } from "@/lib/format";
import type { LapSummary } from "@/lib/types";

const BAND_CLASS = { tight: "text-throttle", steady: "text-ink-dim", loose: "text-warn" } as const;

const cache = new Map<string, LapSummary[]>();

export function LapSparkline({ sessionId, lapCount }: { sessionId: number; lapCount: number }) {
  const key = `${sessionId}:${lapCount}`;
  const [laps, setLaps] = useState<LapSummary[] | null>(cache.get(key) ?? null);

  useEffect(() => {
    const hit = cache.get(key);
    if (hit) {
      setLaps(hit);
      return;
    }
    let cancelled = false;
    api.sessionLaps(sessionId)
      .then((ls) => {
        cache.set(key, ls);
        if (!cancelled) setLaps(ls);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [key, sessionId]);

  if (!laps || laps.length < 2) return null;

  const times = [...laps].reverse().map((l) => l.time_ms); // chronological
  const min = Math.min(...times);
  const max = Math.max(...times);
  const span = Math.max(1, max - min);
  const W = 100;
  const H = 24;
  const PAD = 3;
  const x = (i: number) => PAD + (i / (times.length - 1)) * (W - 2 * PAD);
  const y = (t: number) => PAD + ((t - min) / span) * (H - 2 * PAD);
  const points = times.map((t, i) => `${x(i).toFixed(1)},${y(t).toFixed(1)}`).join(" ");
  const bestIdx = times.indexOf(min);
  const consistency = lapConsistency(laps);

  return (
    <span className="hidden shrink-0 flex-col md:flex">
      <svg
        width={W}
        height={H}
        viewBox={`0 0 ${W} ${H}`}
        aria-hidden="true"
      >
        <title>Lap times (lower is faster)</title>
        <polyline
          points={points}
          fill="none"
          stroke="var(--color-ink-faint)"
          strokeWidth="1.5"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        <circle cx={x(bestIdx)} cy={y(min)} r="2.5" fill="var(--color-accent)" />
      </svg>
      {consistency && (
        <Tip
          content={`Consistency: lap times vary by ${formatSpread(consistency.stdMs)} — ${consistency.pct.toFixed(2)}% of the ${formatLapTime(consistency.medianMs)} median — over the ${consistency.laps} laps that count toward bests`}
        >
          <span
            className={`pl-[3px] font-tabular text-[10px] leading-tight ${BAND_CLASS[consistencyBand(consistency.pct)]}`}
          >
            {formatSpread(consistency.stdMs)}
            <span className="text-ink-faint"> · {consistency.pct.toFixed(1)}%</span>
          </span>
        </Tip>
      )}
    </span>
  );
}
