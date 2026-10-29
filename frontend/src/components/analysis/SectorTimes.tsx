// Sector times (#22): every lap of the session, split at the circuit's
// sectors — its own when it has boundaries authored, thirds of the lap when
// not — with the best of each sector and the best lap time in purple, the
// timing-screen convention. Only laps that count can hold a best; a pit
// out-lap is listed, faint, for completeness.

import { t as translate, useT } from "@/i18n";
import { FASTEST_COLOR } from "@/lib/colors";
import { formatLapTime } from "@/lib/format";
import { formatSplit, sectorBests } from "@/lib/sectorTimes";
import type { SectorTimes as SectorTimesData } from "@/lib/types";

export function sectorNote(data: SectorTimesData): string {
  return data.source === "track"
    ? translate("sectors.noteTrack", { count: data.sectors.length })
    : translate("sectors.noteThirds");
}

export function SectorTimes({
  data,
  lapColors,
  selected,
  refLapId,
}: {
  data: SectorTimesData;
  /** The comparison's colours, keyed by lap id; a lap outside it gets a
   *  neutral dot. */
  lapColors: Record<string, string>;
  selected: number[];
  refLapId: number | null;
}) {
  const t = useT();
  const count = data.sectors.length;
  const bests = sectorBests(data.laps, count);
  const laps = [...data.laps].sort((a, b) => a.number - b.number || a.id - b.id);
  const purple = { color: FASTEST_COLOR, fontWeight: 600 };
  return (
    <div className="max-h-[330px] overflow-y-auto px-1 pb-1.5 pt-0.5">
      <table className="w-full font-tabular text-xs">
        <thead>
          <tr className="text-[10px] uppercase text-ink-dim">
            <th className="px-2 py-1 text-left font-normal">{t("common.lap")}</th>
            {data.sectors.map((s) => (
              <th key={s.n} className="px-1.5 py-1 text-right font-normal" title={s.name || undefined}>
                S{s.n}
              </th>
            ))}
            <th className="px-2 py-1 text-right font-normal">{t("sectors.lapTime")}</th>
          </tr>
        </thead>
        <tbody>
          {laps.map((lap) => {
            const inComparison = selected.includes(lap.id) || lap.id === refLapId;
            const tag =
              lap.id === refLapId ? t("sectors.tag.ref") : !lap.counts_for_best ? t("sectors.tag.out") : "";
            const bestLap = lap.counts_for_best && bests.lap != null && lap.time_ms === bests.lap;
            return (
              <tr
                key={lap.id}
                className={`border-t border-edge/40 ${lap.counts_for_best ? "text-ink" : "text-ink-faint"}`}
              >
                <td className="whitespace-nowrap px-2 py-[3px] text-left">
                  <span className="inline-flex items-center gap-1.5">
                    <span
                      className="h-1.5 w-1.5 rounded-full"
                      style={{
                        backgroundColor: inComparison
                          ? (lapColors[String(lap.id)] ?? "var(--color-edge-bright)")
                          : "var(--color-edge-bright)",
                      }}
                    />
                    {t("lapChart.lapTick", { n: String(lap.number) })}
                    {tag && <span className="text-[10px] text-ink-faint">{tag}</span>}
                  </span>
                </td>
                {data.sectors.map((s, k) => {
                  const v = lap.splits_ms?.[k];
                  const best =
                    v != null && lap.counts_for_best && bests.splits[k] != null && Math.abs(v - bests.splits[k]!) < 0.05;
                  return (
                    <td key={s.n} className="px-1.5 py-[3px] text-right" style={best ? purple : undefined}>
                      {v == null ? <span className="text-ink-ghost">–</span> : formatSplit(v)}
                    </td>
                  );
                })}
                <td className="px-2 py-[3px] text-right" style={bestLap ? purple : undefined}>
                  {formatLapTime(lap.time_ms)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
