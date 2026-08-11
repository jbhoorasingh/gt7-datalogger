// Corner Detail widget: top-down car with four corner cells, synced to the
// chart cursor / map dot. Scrubbing a corner replays the load-transfer story:
// tire temp as cell fill, suspension compression as a bar, LOCK/SPIN badges.
// One focus lap at a time; the reference lap is always the ghost (secondary
// numbers + hollow bars), so focus-vs-ref stays visible without switching.

import { useMemo, useState } from "react";
import type { Corner as TrackCorner, Samples } from "@/lib/types";

export interface CornerLap {
  id: string;
  label: string;
  color: string;
  isRef: boolean;
  series: Samples & { dist: number[] };
}

const CORNERS = ["fl", "fr", "rl", "rr"] as const;
type Corner = (typeof CORNERS)[number];

const REQUIRED = ["tt_fl", "sus_fl", "slip_fl"];

function tempColor(t: number): string {
  if (t < 55) return "rgba(59, 130, 246, 0.35)"; // cold — coast blue
  if (t < 95) return "rgba(34, 197, 94, 0.30)"; // optimal — throttle green
  return "rgba(239, 68, 68, 0.40)"; // hot — brake red
}

function at(series: Samples & { dist: number[] }, col: string, i: number): number | null {
  const arr = series[col];
  if (!Array.isArray(arr) || arr.length === 0) return null;
  return arr[Math.min(i, arr.length - 1)];
}

export function CornerDetail({
  laps,
  cursorDist,
  step,
  trackCorners,
}: {
  laps: CornerLap[];
  cursorDist: number | null;
  step: number;
  trackCorners?: TrackCorner[];
}) {
  const candidates = laps.filter((l) => !l.isRef);
  const [focusId, setFocusId] = useState<string | null>(null);
  const focus =
    laps.find((l) => l.id === focusId && !l.isRef) ?? candidates[0] ?? laps[0] ?? null;
  const ref = laps.find((l) => l.isRef) ?? null;
  const ghost = ref && focus && ref.id !== focus.id ? ref : null;

  // Per-wheel suspension range over the focus lap, for bar normalization
  const susRange = useMemo(() => {
    const out: Record<Corner, [number, number]> = {
      fl: [0, 1], fr: [0, 1], rl: [0, 1], rr: [0, 1],
    };
    if (!focus) return out;
    for (const w of CORNERS) {
      const col = focus.series[`sus_${w}`];
      if (Array.isArray(col) && col.length > 0) {
        out[w] = [Math.min(...col), Math.max(...col)];
      }
    }
    return out;
  }, [focus]);

  if (!focus) return null;
  const hasData = REQUIRED.every((c) => Array.isArray(focus.series[c]) && focus.series[c].length > 0);
  if (!hasData) {
    return (
      <div className="p-3 text-xs text-ink-dim">
        No per-corner data for this lap (recorded before Tier 1, or channels not loaded).
      </div>
    );
  }

  const i = cursorDist != null ? Math.max(0, Math.round(cursorDist / step)) : 0;

  const cell = (w: Corner) => {
    const temp = at(focus.series, `tt_${w}`, i);
    const sus = at(focus.series, `sus_${w}`, i);
    const slip = at(focus.series, `slip_${w}`, i);
    const throttle = at(focus.series, "throttle", i) ?? 0;
    const brake = at(focus.series, "brake", i) ?? 0;
    const gTemp = ghost ? at(ghost.series, `tt_${w}`, i) : null;
    const gSus = ghost ? at(ghost.series, `sus_${w}`, i) : null;
    const [lo, hi] = susRange[w];
    const susPct = sus != null && hi > lo ? ((sus - lo) / (hi - lo)) * 100 : 0;
    const gSusPct = gSus != null && hi > lo ? Math.min(100, ((gSus - lo) / (hi - lo)) * 100) : null;
    const badge =
      slip != null && brake >= 20 && slip < 0.9
        ? "LOCK"
        : slip != null && throttle >= 40 && slip > 1.1
          ? "SPIN"
          : null;
    // Tint toward red/blue when hotter/cooler than the ref at the same spot
    const diff = temp != null && gTemp != null ? temp - gTemp : 0;
    const ring =
      diff > 3 ? "ring-1 ring-brake/60" : diff < -3 ? "ring-1 ring-coast/60" : "";

    return (
      <div
        key={w}
        className={`relative flex h-16 flex-col justify-between rounded-md p-1.5 font-tabular ${ring}`}
        style={{ backgroundColor: temp != null ? tempColor(temp) : "transparent" }}
      >
        <div className="flex items-start justify-between text-[9px] uppercase text-ink-dim">
          {w}
          {badge && (
            <span
              className={`rounded px-1 font-bold text-white ${
                badge === "LOCK" ? "bg-brake" : "bg-warn"
              }`}
            >
              {badge}
            </span>
          )}
        </div>
        <div className="flex items-end justify-between">
          <div className="text-sm leading-none">
            {temp != null ? `${Math.round(temp)}°` : "–"}
            {gTemp != null && (
              <span className="ml-1 text-[10px] text-ink-dim">{Math.round(gTemp)}°</span>
            )}
          </div>
          {/* Suspension compression bar, normalized to the lap's travel range */}
          <div className="relative h-10 w-2 overflow-hidden rounded-sm bg-black/30">
            <div
              className="absolute bottom-0 w-full bg-ink/80"
              style={{ height: `${susPct}%` }}
              title={sus != null ? `${sus.toFixed(1)} mm` : undefined}
            />
            {gSusPct != null && (
              <div
                className="absolute w-full border-t border-dashed border-ink-dim"
                style={{ bottom: `${gSusPct}%` }}
              />
            )}
          </div>
        </div>
      </div>
    );
  };

  const favg = ((at(focus.series, "tt_fl", i) ?? 0) + (at(focus.series, "tt_fr", i) ?? 0)) / 2;
  const ravg = ((at(focus.series, "tt_rl", i) ?? 0) + (at(focus.series, "tt_rr", i) ?? 0)) / 2;
  const balance = favg - ravg;

  // Which auto-numbered corner (if any) the cursor is currently inside.
  // A start/finish corner's extent wraps the lap boundary (entry > exit).
  const inCorner =
    cursorDist != null
      ? trackCorners?.find((c) =>
          c.entry_dist <= c.exit_dist
            ? cursorDist >= c.entry_dist && cursorDist <= c.exit_dist
            : cursorDist >= c.entry_dist || cursorDist <= c.exit_dist,
        )
      : undefined;

  return (
    <div className="p-3">
      {/* Focus chips — hidden in the common 2-lap case (focus is the non-ref) */}
      {candidates.length > 1 && (
        <div className="mb-2 flex flex-wrap gap-1">
          {candidates.map((l) => (
            <button
              key={l.id}
              onClick={() => setFocusId(l.id)}
              className={`flex items-center gap-1 rounded border px-1.5 py-0.5 font-tabular text-[10px] ${
                l.id === focus.id
                  ? "border-accent text-accent"
                  : "border-edge text-ink-dim hover:text-ink"
              }`}
            >
              <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: l.color }} />
              {l.label.split(" ")[0]}
            </button>
          ))}
        </div>
      )}
      <div className="mx-auto grid w-40 grid-cols-2 gap-1.5 rounded-xl border border-edge/60 p-2">
        {cell("fl")}
        {cell("fr")}
        {cell("rl")}
        {cell("rr")}
      </div>
      <div className="mt-2 text-center font-tabular text-xs text-ink-dim">
        {inCorner && (
          <span
            className="mr-2 rounded border border-edge px-1 py-0.5 text-[10px] text-ink"
            title={
              inCorner.authored
                ? "Labelled by hand in this circuit's bundle — the same number every lap and every session"
                : "Detected from this lap's curvature; the numbering can differ between laps"
            }
          >
            T{inCorner.n} {inCorner.direction}
            {inCorner.name ? ` · ${inCorner.name}` : ""}
          </span>
        )}
        F/R balance{" "}
        <span className={balance > 3 ? "text-brake" : balance < -3 ? "text-coast" : "text-ink"}>
          {balance >= 0 ? "F +" : "R +"}
          {Math.abs(balance).toFixed(1)} °C
        </span>
        {ghost && <span className="ml-2 text-[10px]">(small figures = ref)</span>}
        {cursorDist == null && (
          <div className="mt-1 text-[10px]">Hover a chart to scrub through the lap.</div>
        )}
      </div>
    </div>
  );
}
