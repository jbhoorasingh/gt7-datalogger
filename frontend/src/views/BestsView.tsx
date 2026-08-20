// Personal-bests board (#26): one section per circuit, one row per car,
// fastest first. Answers the driver question directly — "what's my best
// here, in this car, and how far off is everything else?" — rather than
// dumping lap rows; the full telemetry is one "open" click away in Analysis.

import { useEffect, useState } from "react";
import { Tip } from "@/components/ui/Tooltip";
import { api } from "@/lib/api";
import { formatLapTime, formatTime } from "@/lib/format";
import { openInAnalysis } from "@/lib/router";
import type { PersonalBest } from "@/lib/types";
import { useTelemetry } from "@/store/telemetry";
import { toast } from "@/store/toasts";

export function BestsView() {
  const lapEpoch = useTelemetry((s) => s.lapEpoch);
  const [bests, setBests] = useState<PersonalBest[] | null>(null);
  // Car category filter, same semantics as SessionsView: "" = everything,
  // which is also the only way to reach pre-packet-C bests (they have no
  // category). Filtered client-side so the chips always list every category
  // that actually holds a best.
  const [category, setCategory] = useState("");

  useEffect(() => {
    api.personalBests()
      .then((r) => setBests(r.bests))
      .catch(() => toast("Could not load bests", "error"));
  }, [lapEpoch]);

  // Only offer categories actually present, so the control disappears
  // entirely on a history recorded before packet C.
  const categories = [
    ...new Set((bests ?? []).map((b) => b.car_category).filter(Boolean)),
  ].sort();
  // A stale selection (its last best re-ruled out, or the filter outliving a
  // data refresh) would show a blank board with no active chip — fall back to
  // unfiltered whenever the selection stops existing.
  const active = categories.includes(category) ? category : "";
  const visible = (bests ?? []).filter((b) => !active || b.car_category === active);

  // Rows arrive ordered by circuit then time, so grouping preserves both the
  // circuit order and fastest-first within each section.
  const groups: [string, PersonalBest[]][] = [];
  for (const row of visible) {
    const last = groups[groups.length - 1];
    if (last && last[0] === row.track_name) last[1].push(row);
    else groups.push([row.track_name, [row]]);
  }

  return (
    <div className="mx-auto max-w-6xl p-3">
      <div className="mb-3 flex items-center gap-2">
        <h2 className="text-lg font-semibold">Personal bests</h2>
      </div>

      {bests == null && (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <div key={i} className="skeleton h-24" />
          ))}
        </div>
      )}

      {bests != null && bests.length === 0 && (
        <div className="rounded-xl bg-panel p-8 text-center text-ink-dim">
          <div className="mb-1 text-lg text-ink">No bests yet</div>
          Best laps appear once a circuit is identified — name the track on a
          session, or drive on a surveyed circuit.
        </div>
      )}

      {categories.length > 0 && (
        <div className="mb-2 flex flex-wrap items-center gap-1">
          <span className="mr-1 text-xs text-ink-dim">Category</span>
          {["", ...categories].map((c) => (
            <button
              key={c || "all"}
              onClick={() => setCategory(c)}
              aria-pressed={active === c}
              className={`rounded-full px-3 py-1 text-xs transition-colors ${
                active === c
                  ? "bg-accent/15 text-accent"
                  : "text-ink-dim hover:bg-panel-2 hover:text-ink"
              }`}
            >
              {c || "All"}
            </button>
          ))}
        </div>
      )}

      <div className="space-y-2">
        {groups.map(([track, rows]) => (
          <CircuitBests key={track} track={track} rows={rows} />
        ))}
      </div>
    </div>
  );
}

// One circuit's board: the outright fastest time in the header, every car's
// best beneath it with its gap to that time — the "how far off is everything
// else" answer, per car.
function CircuitBests({ track, rows }: { track: string; rows: PersonalBest[] }) {
  const fastest = rows[0].time_ms;
  return (
    <div className="rounded-xl bg-panel">
      <div className="flex items-baseline gap-3 border-b border-edge px-4 py-2.5">
        <span className="font-medium">{track}</span>
        <span className="ml-auto font-tabular text-sm text-accent">
          {formatLapTime(fastest)}
        </span>
      </div>
      <div className="px-2 pb-1 pt-1">
        {rows.map((row) => {
          const gap = row.time_ms - fastest;
          return (
            <div
              key={`${row.car_id}`}
              className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 rounded-md px-2 py-1.5 text-xs hover:bg-panel-2/50"
            >
              <span className="min-w-0 truncate font-medium text-ink">{row.car_name}</span>
              {row.car_category && (
                <span className="shrink-0 rounded-full bg-panel-2 px-2 py-0.5 text-ink-dim">
                  {row.car_category}
                </span>
              )}
              <span className="ml-auto flex shrink-0 items-baseline gap-3 font-tabular">
                {row.salvaged && (
                  <span
                    className="text-ink-dim"
                    title="Salvaged from a stream that ended at the line (replay ending) — if this was someone else's replay, exclude its session from bests in the Sessions view"
                  >
                    ⟲ salvaged
                  </span>
                )}
                <CleanHint row={row} />
                <span className="text-ink-dim">of {row.lap_count} laps</span>
                <span className="hidden text-ink-dim sm:inline">
                  {formatTime(row.finished_at)}
                </span>
                <span className={gap === 0 ? "text-throttle" : "text-brake"}>
                  {gap === 0 ? "best" : `+${(gap / 1000).toFixed(3)}`}
                </span>
                <span className={gap === 0 ? "text-accent" : "text-ink"}>
                  {formatLapTime(row.time_ms)}
                </span>
                <Tip content="Open this lap in the Analysis view">
                  <button
                    className="text-ink-dim hover:text-accent"
                    onClick={() =>
                      openInAnalysis({
                        session: row.session_id,
                        laps: [row.lap_id],
                        ref: row.lap_id,
                      })
                    }
                  >
                    open
                  </button>
                </Tip>
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// Track-limits hint, same conventions as the Sessions lap table: surface
// flags decide clean/dirty (dash when the lap predates surface data), and
// surveyed-edge excursions get their own count — a lap can be "clean" by
// GT7's surfaces yet have left the mapped road onto paved runoff.
function CleanHint({ row }: { row: PersonalBest }) {
  return (
    <span className={row.clean_lap === false ? "text-brake" : "text-ink-dim"}>
      <span title="Track limits from surface flags (3+ wheels on grass/gravel/dirt) — dash when the lap was recorded without surface data">
        {row.clean_lap == null ? "–" : row.clean_lap ? "clean" : "⚠"}
      </span>
      {row.off_survey_count > 0 && (
        <span title="Excursions beyond the surveyed road edge — the car left the mapped road surface (paved runoff counts)">
          {` · ${row.off_survey_count} ⚠`}
        </span>
      )}
    </span>
  );
}
