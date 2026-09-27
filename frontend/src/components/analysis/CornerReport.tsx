// Corner report card (#21): "where am I actually losing the lap", answered
// per corner and sorted by time lost. Every lap is measured through the
// REFERENCE lap's corner windows (the backend's corner_report), so the Δ
// column is the same comparison the delta chart makes — just summed corner by
// corner instead of drawn along distance. Clicking a row zooms the charts and
// map to that corner, using the map's own corner-window convention so its
// corner bar lights up in agreement.
//
// Beside the speeds and the time, each corner says how it was braked for
// (#110) — where the brake went on against the reference, how hard, for how
// far — and how far the car was rotated through it (#109). Those are the
// backend's figures too, the ones the race engineer speaks; nothing here
// defines a braking point. Hovering a row marks the brake points on the map.

import { useMemo } from "react";
import { cornerRange } from "@/components/analysis/RaceLineMap";
import { Tip } from "@/components/ui/Tooltip";
import { speedUnit, speedValue, type Units } from "@/lib/format";
import { brakeDeltaText } from "@/lib/mapLayers";
import type { Corner, CornerReportRow } from "@/lib/types";

export interface ReportLap {
  id: string;
  label: string;
  color: string;
  isRef: boolean;
  report: CornerReportRow[];
}

// A brake point this close to the reference's is the same brake point: the
// floor the race engineer puts under what it will mention
// (CORNER_BRAKE_DIFF_MIN_M in race_engineer/thresholds.py).
const BRAKE_NOISE_M = 5;

function fmtDelta(ms: number): string {
  const s = ms / 1000;
  return `${s >= 0 ? "+" : "−"}${Math.abs(s).toFixed(2)}`;
}

function fmtSigned(value: number, digits = 0): string {
  const text = Math.abs(value).toFixed(digits);
  if (Number(text) === 0) return text;
  return `${value > 0 ? "+" : "−"}${text}`;
}

/** The lap the card is about: the one picked, else the first that is not
 *  the reference, else the reference by itself. Shared with the view, which
 *  marks the same lap's brake point on the map. */
export function reportFocus(laps: ReportLap[], focusId: string | null): ReportLap | null {
  const candidates = laps.filter((l) => !l.isRef && l.report.length > 0);
  return (
    candidates.find((l) => l.id === focusId) ??
    candidates[0] ??
    laps.find((l) => l.isRef) ??
    null
  );
}

export function CornerReport({
  corners,
  laps,
  units,
  focusId,
  onFocusChange,
  onZoom,
  onHoverCorner,
}: {
  corners: Corner[];
  laps: ReportLap[];
  units: Units;
  /** The lap compared against the reference; null takes the first. */
  focusId: string | null;
  onFocusChange: (id: string) => void;
  onZoom?: (range: [number, number]) => void;
  /** The corner under the pointer, or null once it has left the table. */
  onHoverCorner?: (n: number | null) => void;
}) {
  const ref = laps.find((l) => l.isRef) ?? null;
  const candidates = laps.filter((l) => !l.isRef && l.report.length > 0);
  // With no comparison lap the card still reads as the reference's own
  // report (speeds and time per corner), just without a Δ to sort by.
  const focus = reportFocus(laps, focusId);

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
  const comparing = focus.id !== ref?.id;
  const hasDelta = rows.some((r) => r.lost != null);
  const totalLost = rows.reduce((sum, r) => sum + (r.lost ?? 0), 0);
  const spd = (kmh: number) => Math.round(speedValue(kmh, units));
  // Columns a recording cannot fill are left out, not drawn empty: braking
  // on a lap that never braked (an oval, flat), slip on a recording from
  // before the channel.
  const hasBraking = rows.some((r) => r.row.brake_off != null || r.refRow?.brake_off != null);
  const hasSlip = rows.some((r) => r.row.slip_peak != null);

  const small = (text: string) => <span className="ml-1 text-[10px] text-ink-dim">{text}</span>;

  const speedCell = (value: number, refValue: number | undefined) => (
    <td className="px-2 py-1 text-right">
      {spd(value)}
      {refValue != null && comparing && small(String(spd(refValue)))}
    </td>
  );

  // A figure the lap may not have, with the reference's beside it.
  const figureCell = (
    value: number | null | undefined,
    refValue: number | null | undefined,
    digits: number,
  ) => (
    <td className="px-2 py-1 text-right">
      {value == null ? <span className="text-ink-dim">–</span> : value.toFixed(digits)}
      {comparing && refValue != null && small(refValue.toFixed(digits))}
    </td>
  );

  const brakePointCell = (row: CornerReportRow, refRow: CornerReportRow | undefined) => {
    const delta = row.brake_delta_m;
    if (delta != null) {
      return (
        <td
          className={`px-2 py-1 text-right ${Math.abs(delta) < BRAKE_NOISE_M ? "text-ink-dim" : ""}`}
          title={`Braked ${brakeDeltaText(delta)}`}
        >
          {fmtSigned(delta)}
        </td>
      );
    }
    // No figure is not nothing to say: one lap braking where the other did
    // not is the finding.
    const mine = row.brake_off != null;
    const theirs = refRow?.brake_off != null;
    const text = mine === theirs ? "–" : mine ? "ref flat" : "no brake";
    const title =
      mine === theirs
        ? mine
          ? "The brake was already on when the lap began, so where it went on is not in this lap"
          : "Neither lap braked for this corner"
        : mine
          ? "This lap braked here; the reference did not"
          : "The reference braked here; this lap did not";
    return (
      <td className="px-2 py-1 text-right text-ink-dim" title={title}>
        {text}
      </td>
    );
  };

  const columnCount =
    5 + (hasBraking ? (comparing ? 3 : 2) : 0) + (hasSlip ? 2 : 0) + (hasDelta ? 1 : 0);

  return (
    <div className="p-1">
      {candidates.length > 1 && (
        <div className="mb-1.5 flex flex-wrap gap-1 px-2 pt-1">
          {candidates.map((l) => (
            <button
              key={l.id}
              onClick={() => onFocusChange(l.id)}
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
        <table
          className="w-full font-tabular text-xs"
          onMouseLeave={onHoverCorner ? () => onHoverCorner(null) : undefined}
        >
          <thead>
            <tr className="text-[10px] uppercase text-ink-dim">
              <th className="px-2 py-1 text-left font-normal">Corner</th>
              {hasBraking && comparing && (
                <th className="px-2 py-1 text-right font-normal">
                  <Tip content="Where the brake went on against the reference lap, in metres along the track. Negative = earlier, positive = later (deeper). Under 5 m is the same brake point.">
                    <span>Brake Δ m</span>
                  </Tip>
                </th>
              )}
              {hasBraking && (
                <>
                  <th className="px-2 py-1 text-right font-normal">
                    <Tip content="The most brake pedal the corner's braking zone saw, in percent.">
                      <span>Peak %</span>
                    </Tip>
                  </th>
                  <th className="px-2 py-1 text-right font-normal">
                    <Tip content="Metres from the brake going on to its release.">
                      <span>Zone m</span>
                    </Tip>
                  </th>
                </>
              )}
              <th className="px-2 py-1 text-right font-normal">Entry</th>
              <th className="px-2 py-1 text-right font-normal">Min</th>
              <th className="px-2 py-1 text-right font-normal">Exit</th>
              {hasSlip && (
                <>
                  <th className="px-2 py-1 text-right font-normal">
                    <Tip content="The most the car was rotated into the corner: degrees between where the nose pointed and where the car was going. More than the reference is more rotation — towards oversteer; less, or negative, is the nose pushing wide.">
                      <span>Slip pk °</span>
                    </Tip>
                  </th>
                  <th className="px-2 py-1 text-right font-normal">
                    <Tip content="The same angle averaged from the corner's entry to its exit.">
                      <span>Slip avg °</span>
                    </Tip>
                  </th>
                </>
              )}
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
                onMouseEnter={onHoverCorner ? () => onHoverCorner(row.n) : undefined}
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
                {hasBraking && comparing && brakePointCell(row, refRow)}
                {hasBraking && (
                  <>
                    {figureCell(row.brake_peak, refRow?.brake_peak, 0)}
                    {figureCell(row.brake_dist, refRow?.brake_dist, 0)}
                  </>
                )}
                {speedCell(row.entry_speed, refRow?.entry_speed)}
                {speedCell(row.min_speed, refRow?.min_speed)}
                {speedCell(row.exit_speed, refRow?.exit_speed)}
                {hasSlip && (
                  <>
                    {figureCell(row.slip_peak, refRow?.slip_peak, 1)}
                    {figureCell(row.slip_mean, refRow?.slip_mean, 1)}
                  </>
                )}
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
                <td
                  className="px-2 py-1 text-left text-[10px] uppercase text-ink-dim"
                  colSpan={columnCount - 1}
                >
                  In corners vs ref
                </td>
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
        {hasBraking && onHoverCorner && " · hover a row to mark the brake points on the map"}
      </div>
    </div>
  );
}
