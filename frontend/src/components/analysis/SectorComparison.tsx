import { FASTEST_COLOR } from "@/lib/colors";
import { formatLapTime } from "@/lib/format";
import { sectorComparison } from "@/lib/sectorComparison";
import { formatSplit } from "@/lib/sectorTimes";
import type { SectorTimes as SectorTimesData } from "@/lib/types";

function signedDelta(ms: number): string {
  if (ms === 0) return "±0.000";
  return `${ms < 0 ? "-" : "+"}${formatSplit(Math.abs(ms))}`;
}

function deltaColor(ms: number | null): string {
  if (ms == null) return "text-ink-ghost";
  if (ms < 0) return "text-emerald-400";
  if (ms > 0) return "text-rose-400";
  return "text-ink-dim";
}

export function SectorComparison({
  data,
  refLapId,
}: {
  data: SectorTimesData;
  refLapId: number | null;
}) {
  const comparison = sectorComparison(data, refLapId);

  if (!comparison) {
    return (
      <div className="px-3 py-5 text-xs text-ink-dim">
        Select a reference lap from this session to compare its sectors
        with the session's best.
      </div>
    );
  }

  const {
    referenceLapNumber,
    referenceLapMs,
    sectors,
    theoreticalBestMs,
    theoreticalDeltaMs,
  } = comparison;

  const bestStyle = { color: FASTEST_COLOR, fontWeight: 600 };

  return (
    <div className="px-1 pb-3 pt-0.5">
      <div className="overflow-x-auto">
        <table className="w-full font-tabular text-xs">
          <thead>
            <tr className="text-[10px] uppercase text-ink-dim">
              <th className="whitespace-nowrap px-2 py-2 text-left font-normal">
                Lap
              </th>
              {sectors.map((sector) => (
                <th
                  key={sector.n}
                  className="px-1.5 py-2 text-right font-normal"
                  title={sector.name || undefined}
                >
                  S{sector.n}
                </th>
              ))}
              <th className="px-2 py-2 text-right font-normal">
                Lap time
              </th>
            </tr>
          </thead>

          <tbody>
            <tr className="border-t border-edge/40 text-ink">
              <th className="whitespace-nowrap px-2 py-2 text-left font-normal">
                Reference L{referenceLapNumber}
              </th>
              {sectors.map((sector) => (
                <td key={sector.n} className="px-1.5 py-2 text-right">
                  {sector.referenceMs == null
                    ? <span className="text-ink-ghost">—</span>
                    : formatSplit(sector.referenceMs)}
                </td>
              ))}
              <td className="px-2 py-2 text-right">
                {formatLapTime(referenceLapMs)}
              </td>
            </tr>

            <tr className="border-t border-edge/40">
              <th className="px-2 py-2 text-left font-normal text-ink-soft">
                Session best sectors
              </th>
              {sectors.map((sector) => (
                <td
                  key={sector.n}
                  className="px-1.5 py-2 text-right"
                  style={bestStyle}
                >
                  {sector.bestMs == null
                    ? <span className="text-ink-ghost">—</span>
                    : formatSplit(sector.bestMs)}
                </td>
              ))}
              <td className="px-2 py-2 text-right" style={bestStyle}>
                {theoreticalBestMs == null
                  ? <span className="text-ink-ghost">—</span>
                  : formatLapTime(theoreticalBestMs)}
              </td>
            </tr>

            <tr className="border-y border-edge/40 bg-white/[0.025]">
              <th className="px-2 py-2 text-left font-semibold text-ink">
                Delta
              </th>
              {sectors.map((sector) => (
                <td
                  key={sector.n}
                  className={`px-1.5 py-2 text-right font-semibold ${deltaColor(sector.deltaMs)}`}
                >
                  {sector.deltaMs == null
                    ? "—"
                    : signedDelta(sector.deltaMs)}
                </td>
              ))}
              <td
                className={`px-2 py-2 text-right font-semibold ${deltaColor(theoreticalDeltaMs)}`}
              >
                {theoreticalDeltaMs == null
                  ? "—"
                  : signedDelta(theoreticalDeltaMs)}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div className="mx-2 mt-4 grid grid-cols-2 gap-3 border-t border-edge pt-4">
        <div className="min-w-0">
          <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-ink-dim">
            Theoretical best
          </div>
          <div
            className="mt-1 whitespace-nowrap font-tabular text-xl font-bold tracking-tight sm:text-2xl"
            style={{ color: FASTEST_COLOR }}
          >
            {theoreticalBestMs == null
              ? "—"
              : formatLapTime(theoreticalBestMs)}
          </div>
        </div>

        <div className="min-w-0 border-l border-edge pl-3 text-right">
          <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-ink-dim">
            Vs reference
          </div>
          <div
            className={`mt-1 whitespace-nowrap font-tabular text-xl font-bold tracking-tight sm:text-2xl ${deltaColor(theoreticalDeltaMs)}`}
          >
            {theoreticalDeltaMs == null
              ? "—"
              : signedDelta(theoreticalDeltaMs)}
          </div>
        </div>
      </div>

      {data.source === "thirds" && (
        <div className="mx-2 mt-3 text-[10px] text-ink-faint">
          Estimated using thirds of the lap; circuit sector boundaries
          are not configured.
        </div>
      )}
    </div>
  );
}
