// Personal-bests board (#26): one card per circuit, one row per car,
// fastest first. Answers the driver question directly — "what's my best
// here, in this car, and how far off is everything else?" — rather than
// dumping lap rows; the full telemetry is one "open" click away in Analysis.
//
// Lives under the Sessions view's "Bests" sub-tab, which owns the category
// filter shared by both boards.

import { Tip } from "@/components/ui/Tooltip";
import { formatLapTime, formatTime, formatTimeShort } from "@/lib/format";
import { openInAnalysis } from "@/lib/router";
import type { PersonalBest } from "@/lib/types";

export function BestsBoard({ bests }: { bests: PersonalBest[] | null }) {
  if (bests == null) {
    return (
      <div className="flex flex-col gap-2.5">
        {[0, 1, 2].map((i) => (
          <div key={i} className="skeleton h-24" />
        ))}
      </div>
    );
  }

  if (bests.length === 0) {
    return (
      <div className="panel p-8 text-center text-ink-dim">
        <div className="mb-1 text-base text-ink">No bests yet</div>
        Best laps appear once a circuit is identified — name the track on a
        session, or drive on a surveyed circuit.
      </div>
    );
  }

  // Rows arrive ordered by circuit then time, so grouping preserves both the
  // circuit order and fastest-first within each section.
  const groups: [string, PersonalBest[]][] = [];
  for (const row of bests) {
    const last = groups[groups.length - 1];
    if (last && last[0] === row.track_name) last[1].push(row);
    else groups.push([row.track_name, [row]]);
  }

  return (
    <div className="flex flex-col gap-2.5">
      {groups.map(([track, rows]) => (
        <CircuitBests key={track} track={track} rows={rows} />
      ))}
    </div>
  );
}

// One circuit's card: the outright fastest time in the header, every car's
// best beneath it with its gap to that time — the "how far off is everything
// else" answer, per car.
function CircuitBests({ track, rows }: { track: string; rows: PersonalBest[] }) {
  const fastest = rows[0].time_ms;
  return (
    <div className="panel">
      <div className="flex items-baseline gap-2.5 px-4 py-2.5">
        <span className="text-[13.5px] font-medium">{track}</span>
        <span className="ml-auto font-tabular text-[13px] text-accent">
          {formatLapTime(fastest)}
        </span>
      </div>
      <div className="rule" />
      <div className="px-2 py-1.5">
        {rows.map((row) => {
          const gap = row.time_ms - fastest;
          return (
            <div
              key={row.car_id}
              className="flex flex-wrap items-baseline gap-x-3 gap-y-1 rounded-[5px] px-2 py-1.5 text-[11.5px] transition-colors hover:bg-panel-2"
            >
              <span className="min-w-0 truncate text-xs font-medium text-ink">{row.car_name}</span>
              {row.car_category && (
                <span className="shrink-0 rounded-[9px] border border-edge px-[7px] text-[9.5px] text-ink-dim">
                  {row.car_category}
                </span>
              )}
              <span className="ml-auto flex shrink-0 items-baseline gap-3 font-tabular">
                {row.salvaged && (
                  <span
                    className="text-ink-faint"
                    title="Salvaged from a stream that ended at the line (replay ending) — if this was someone else's replay, exclude its session from bests in the Sessions view"
                  >
                    ⟲ salvaged
                  </span>
                )}
                <CleanHint row={row} />
                <span className="text-ink-faint">
                  of {row.lap_count} laps
                  <span className="hidden sm:inline" title={formatTime(row.finished_at)}>
                    {" · "}
                    {formatTimeShort(row.finished_at)}
                  </span>
                </span>
                <span className={gap === 0 ? "text-throttle" : "text-brake"}>
                  {gap === 0 ? "best" : `+${(gap / 1000).toFixed(3)}`}
                </span>
                <span className={gap === 0 ? "text-accent" : "text-ink"}>
                  {formatLapTime(row.time_ms)}
                </span>
                <Tip content="Open this lap in the Analysis view">
                  <button
                    className="text-[11px] text-accent transition-colors hover:text-accent-300"
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
    <span className={row.clean_lap === false ? "text-brake" : "text-ink-faint"}>
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
