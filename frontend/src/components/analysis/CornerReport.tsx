// Corner report card (#21): "where am I actually losing the lap", answered
// per corner and sorted by time lost. Every lap is measured through the
// REFERENCE lap's corner windows (the backend's corner_report), so the Δ
// column is the same comparison the delta chart makes — just summed corner by
// corner instead of drawn along distance. Clicking a row zooms the charts and
// map to that corner, using the map's own corner-window convention so its
// corner bar lights up in agreement.

import { useMemo, useState } from "react";
import { cornerRange } from "@/components/analysis/RaceLineMap";
import { Tip } from "@/components/ui/Tooltip";
import { speedUnit, speedValue, type Units } from "@/lib/format";
import type { Corner, CornerReportRow } from "@/lib/types";

export interface ReportLap {
  id: string;
  label: string;
  color: string;
  isRef: boolean;
  report: CornerReportRow[];
}

function fmtDelta(ms: number): string {
  const s = ms / 1000;
  return `${s >= 0 ? "+" : "−"}${Math.abs(s).toFixed(2)}`;
}

export function CornerReport({
  corners,
  laps,
  units,
  onZoom,
}: {
  corners: Corner[];
  laps: ReportLap[];
  units: Units;
  onZoom?: (range: [number, number]) => void;
}) {
  const ref = laps.find((l) => l.isRef) ?? null;
  const candidates = laps.filter((l) => !l.isRef && l.report.length > 0);
  const [focusId, setFocusId] = useState<string | null>(null);
  // With no comparison lap the card still reads as the reference's own
  // report (speeds and time per corner), just without a Δ to sort by.
  const focus = candidates.find((l) => l.id === focusId) ?? candidates[0] ?? ref;

  const rows = useMemo(() => {
    if (!focus) return [];
    const refByN = new Map((ref?.report ?? []).map((r) => [r.n, r]));
    const built = focus.report.map((r) => {
      const base = refByN.get(r.n);
      return {
        corner: corners.find((c) => c.n === r.n),
        row: r,
        refRow: base,
        // Time lost vs the reference through this corner; null against itself.
        lost: base && focus.id !== ref?.id ? r.time_ms - base.time_ms : null,
      };
    });
    // The point of the card: the biggest loss first. Rows without a Δ (no
    // reference coverage, or the ref-only case) keep track order below.
    return built.sort((a, b) =>
      a.lost != null && b.lost != null
        ? b.lost - a.lost
        : a.lost != null
          ? -1
          : b.lost != null
            ? 1
            : a.row.n - b.row.n,
    );
  }, [focus, ref, corners]);

  if (!focus || rows.length === 0) return null;
  const hasDelta = rows.some((r) => r.lost != null);
  const totalLost = rows.reduce((sum, r) => sum + (r.lost ?? 0), 0);
  const spd = (kmh: number) => Math.round(speedValue(kmh, units));

  const speedCell = (value: number, refValue: number | undefined) => (
    <td className="px-2 py-1 text-right">
      {spd(value)}
      {refValue != null && focus.id !== ref?.id && (
        <span className="ml-1 text-[10px] text-ink-dim">{spd(refValue)}</span>
      )}
    </td>
  );

  return (
    <div className="p-1">
      {candidates.length > 1 && (
        <div className="mb-1.5 flex flex-wrap gap-1 px-2 pt-1">
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
      <div className="overflow-x-auto">
        <table className="w-full font-tabular text-xs">
          <thead>
            <tr className="text-[10px] uppercase text-ink-dim">
              <th className="px-2 py-1 text-left font-normal">Corner</th>
              <th className="px-2 py-1 text-right font-normal">Entry</th>
              <th className="px-2 py-1 text-right font-normal">Min</th>
              <th className="px-2 py-1 text-right font-normal">Exit</th>
              <th className="px-2 py-1 text-right font-normal">Time</th>
              {hasDelta && (
                <th className="px-2 py-1 text-right font-normal">
                  <Tip content="Time through this corner vs the reference lap. Positive = lost here.">
                    <span>Δ s</span>
                  </Tip>
                </th>
              )}
            </tr>
          </thead>
          <tbody>
            {rows.map(({ corner, row, refRow, lost }) => (
              <tr
                key={row.n}
                onClick={corner && onZoom ? () => onZoom(cornerRange(corner)) : undefined}
                className={`border-t border-edge/40 ${
                  corner && onZoom ? "cursor-pointer hover:bg-panel-2" : ""
                }`}
                title={corner && onZoom ? "Zoom the charts and map to this corner" : undefined}
              >
                <td className="max-w-40 truncate px-2 py-1 text-left">
                  T{row.n}
                  {corner && (
                    <span className="ml-1 text-[10px] text-ink-dim">
                      {corner.direction}
                      {corner.name ? ` · ${corner.name}` : ""}
                    </span>
                  )}
                </td>
                {speedCell(row.entry_speed, refRow?.entry_speed)}
                {speedCell(row.min_speed, refRow?.min_speed)}
                {speedCell(row.exit_speed, refRow?.exit_speed)}
                <td className="px-2 py-1 text-right">{(row.time_ms / 1000).toFixed(2)}</td>
                {hasDelta && (
                  <td
                    className={`px-2 py-1 text-right ${
                      lost == null ? "text-ink-dim" : lost > 0 ? "text-brake" : "text-throttle"
                    }`}
                  >
                    {lost == null ? "–" : fmtDelta(lost)}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
          {hasDelta && (
            <tfoot>
              <tr className="border-t border-edge">
                <td className="px-2 py-1 text-left text-[10px] uppercase text-ink-dim" colSpan={4}>
                  In corners vs ref
                </td>
                <td />
                <td
                  className={`px-2 py-1 text-right ${totalLost > 0 ? "text-brake" : "text-throttle"}`}
                >
                  {fmtDelta(totalLost)}
                </td>
              </tr>
            </tfoot>
          )}
        </table>
      </div>
      <div className="px-2 pb-1 pt-0.5 text-[10px] text-ink-dim">
        Speeds in {speedUnit(units)}
        {hasDelta && ", small figures = ref · sorted by time lost"}
      </div>
    </div>
  );
}
