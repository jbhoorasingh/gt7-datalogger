// Corner report card (#21): "where am I actually losing the lap", answered
// per corner. Every lap is measured through the REFERENCE lap's corner
// windows (the backend's corner_report), so the Δ column is the same
// comparison the delta chart makes — just summed corner by corner instead of
// drawn along distance. Read in track order by default, which is how a lap
// is remembered; sorted by time lost on request, which is where it was lost.
// Clicking a row zooms the charts and map to that corner, using the shared
// corner-window convention so the map and the lap strip light up in
// agreement.
//
// Beside the speeds and the time, each corner says how it was braked for
// (#110) — where the brake went on against the reference, how hard, for how
// far — and how far the car was rotated through it (#109). Those are the
// backend's figures too, the ones the race engineer speaks; nothing here
// defines a braking point. Hovering a row marks the brake points on the map.

import { useMemo } from "react";
import { Tip } from "@/components/ui/Tooltip";
import { type MessageKey, useT } from "@/i18n";
import { cornerRange } from "@/lib/cornerZoom";
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

/** Row order: the circuit's (T1, T2 …) or the biggest loss first. */
export type ReportSort = "track" | "lost";

export const REPORT_SORT_OPTIONS: { value: ReportSort; labelKey: MessageKey }[] = [
  { value: "track", labelKey: "cornerReport.sort.track" },
  { value: "lost", labelKey: "cornerReport.sort.lost" },
];

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
  sort = "track",
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
  sort?: ReportSort;
}) {
  const t = useT();
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
    if (sort === "track") return built.sort((a, b) => a.row.n - b.row.n);
    // Biggest loss first. Rows without a Δ (no reference coverage, or the
    // ref-only case) keep track order below.
    return built.sort((a, b) =>
      a.lost != null && b.lost != null
        ? b.lost - a.lost
        : a.lost != null
          ? -1
          : b.lost != null
            ? 1
            : a.row.n - b.row.n,
    );
  }, [focus, ref, corners, sort]);

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
  // Kinematic balance (#155) needs packet C; the lock column only once some
  // corner of either lap actually ran out of lock.
  const hasYaw = rows.some((r) => r.row.yaw_ratio != null);
  const hasLock = rows.some((r) => (r.row.lock_m ?? 0) > 0 || (r.refRow?.lock_m ?? 0) > 0);

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
          title={t("cornerReport.braked", { delta: brakeDeltaText(delta, t) })}
        >
          {fmtSigned(delta)}
        </td>
      );
    }
    // No figure is not nothing to say: one lap braking where the other did
    // not is the finding.
    const mine = row.brake_off != null;
    const theirs = refRow?.brake_off != null;
    const text = mine === theirs ? "–" : mine ? t("cornerReport.refFlat") : t("cornerReport.noBrake");
    const title =
      mine === theirs
        ? mine
          ? t("cornerReport.brakeAlreadyOn")
          : t("cornerReport.neitherBraked")
        : mine
          ? t("cornerReport.onlyThisBraked")
          : t("cornerReport.onlyRefBraked");
    return (
      <td className="px-2 py-1 text-right text-ink-dim" title={title}>
        {text}
      </td>
    );
  };

  const columnCount =
    5 +
    (hasBraking ? (comparing ? 3 : 2) : 0) +
    (hasSlip ? 2 : 0) +
    (hasYaw ? 1 : 0) +
    (hasLock ? 1 : 0) +
    (hasDelta ? 1 : 0);

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
              <th className="px-2 py-1 text-left font-normal">{t("common.corner")}</th>
              {hasBraking && comparing && (
                <th className="px-2 py-1 text-right font-normal">
                  <Tip content={t("cornerReport.col.brakeDelta.tip")}>
                    <span>{t("cornerReport.col.brakeDelta")}</span>
                  </Tip>
                </th>
              )}
              {hasBraking && (
                <>
                  <th className="px-2 py-1 text-right font-normal">
                    <Tip content={t("cornerReport.col.peak.tip")}>
                      <span>{t("cornerReport.col.peak")}</span>
                    </Tip>
                  </th>
                  <th className="px-2 py-1 text-right font-normal">
                    <Tip content={t("cornerReport.col.zone.tip")}>
                      <span>{t("cornerReport.col.zone")}</span>
                    </Tip>
                  </th>
                </>
              )}
              <th className="px-2 py-1 text-right font-normal">{t("cornerReport.col.entry")}</th>
              <th className="px-2 py-1 text-right font-normal">{t("cornerReport.col.min")}</th>
              <th className="px-2 py-1 text-right font-normal">{t("cornerReport.col.exit")}</th>
              {hasSlip && (
                <>
                  <th className="px-2 py-1 text-right font-normal">
                    <Tip content={t("cornerReport.col.slipPeak.tip")}>
                      <span>{t("cornerReport.col.slipPeak")}</span>
                    </Tip>
                  </th>
                  <th className="px-2 py-1 text-right font-normal">
                    <Tip content={t("cornerReport.col.slipMean.tip")}>
                      <span>{t("cornerReport.col.slipMean")}</span>
                    </Tip>
                  </th>
                </>
              )}
              {hasYaw && (
                <th className="px-2 py-1 text-right font-normal">
                  <Tip content={t("cornerReport.col.yaw.tip")}>
                    <span>{t("cornerReport.col.yaw")}</span>
                  </Tip>
                </th>
              )}
              {hasLock && (
                <th className="px-2 py-1 text-right font-normal">
                  <Tip content={t("cornerReport.col.lock.tip")}>
                    <span>{t("cornerReport.col.lock")}</span>
                  </Tip>
                </th>
              )}
              <th className="px-2 py-1 text-right font-normal">{t("common.time")}</th>
              {hasDelta && (
                <th className="px-2 py-1 text-right font-normal">
                  <Tip content={t("cornerReport.col.delta.tip")}>
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
                title={corner && onZoom ? t("cornerReport.zoomRow") : undefined}
              >
                <td className="max-w-40 truncate px-2 py-1 text-left">
                  T{row.n}
                  {corner && (
                    <span className="ml-1 text-[10px] text-ink-dim">
                      {t(corner.direction === "L" ? "map.dir.l" : "map.dir.r")}
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
                {hasYaw && figureCell(row.yaw_ratio, refRow?.yaw_ratio, 2)}
                {hasLock && figureCell(row.lock_m, refRow?.lock_m, 0)}
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
                  {t("cornerReport.total")}
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
        {t("cornerReport.speedsIn", { unit: speedUnit(units) })}
        {comparing && t("cornerReport.smallRef")}
        {hasDelta && sort === "lost" ? t("cornerReport.lossFirst") : t("cornerReport.trackOrder")}
        {hasBraking && onHoverCorner && t("cornerReport.hoverHint")}
      </div>
    </div>
  );
}
