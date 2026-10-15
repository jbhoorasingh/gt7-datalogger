// Gearing panel: per-lap transmission metadata (captured once per lap).
// Ratio table with the speed each gear reaches at the redline, and under it
// the sawtooth (#107): speed across, rpm up, one line per gear from
// standstill to the redline, with the lap's own samples over them coloured
// by gear. The samples climb a gear's line to where the driver shifted and
// drop to the next one, so the shift points against the redline, the rpm
// lost across each ratio gap and whether top gear's line is ever reached
// are all read off one picture.
//
// The lines' scale is lib/gearing's: fitted to the lap's own samples, or
// GT7's top speed where they give no consistent fit. The table's speed at
// redline is where each line meets the redline, so the two always agree.
// The reference lap is drawn behind in grey when its gearbox differs — a
// guest lap in another car, or another setup.

import type * as echarts from "echarts";
import type { EChartsOption, SeriesOption } from "echarts";
import { useEffect, useMemo, useRef, useState } from "react";
import { CHART_COLORS, EChart } from "@/components/EChart";
import { api } from "@/lib/api";
import { speedUnit, speedValue, type Units } from "@/lib/format";
import {
  gearColor,
  gearScale,
  gearSpeed,
  plausibleTopSpeed,
  redlineSpeeds,
  sameGearing,
  scatterPoints,
  wholeGear,
  type GearScale,
} from "@/lib/gearing";
import { nearestIndex } from "@/lib/playback";
import type { LapGearing, Samples } from "@/lib/types";
import { useTelemetry } from "@/store/telemetry";

type Series = Samples & { dist: number[] };

/** A lap as the panel needs it: the comparison's series, null until the
 *  comparison has loaded. */
export interface GearingLap {
  id: number;
  label: string;
  series: Series | null;
}

// The reference as the chart draws it behind the focus lap.
interface Ghost {
  label: string;
  series: Series;
  gearing: LapGearing;
  scale: GearScale | null;
}

const GHOST = CHART_COLORS.label;

// Each lap's gearing as last fetched, and the lapEpoch it was fetched in.
type GearingCache = Map<number, { epoch: number; gearing: LapGearing | null }>;

/**
 * A lap's gearing: undefined while it first loads, null when the lap has none.
 *
 * Gearing never changes once a lap is saved, so the panel keeps what it has
 * fetched: switching the focus lap back and forth, or the reference coming
 * and going as a ghost, asks the server nothing new. The cache lives as long
 * as the panel, not the page, because lap ids are reused — a lap recorded or
 * imported after the newest ones were deleted takes a freed id — and both
 * happen in Sessions, which this view is left for. A new lap recorded
 * meanwhile arrives with a lapEpoch bump, after which each lap shown is
 * fetched once more, the old answer staying up until the new one lands.
 */
function useLapGearing(lapId: number | null, cache: GearingCache): LapGearing | null | undefined {
  const epoch = useTelemetry((s) => s.lapEpoch);
  // A re-render once an answer lands; what is shown is read from the cache.
  const [, setLanded] = useState(0);

  useEffect(() => {
    if (lapId == null || cache.get(lapId)?.epoch === epoch) return;
    let cancelled = false;
    api.lapDetail(lapId, false)
      .then((lap) => {
        // An answer from an older epoch landing late never replaces a newer one.
        if ((cache.get(lapId)?.epoch ?? -1) <= epoch) {
          cache.set(lapId, { epoch, gearing: (lap.gearing as LapGearing | null) ?? null });
        }
      })
      .catch(() => {
        // Keep an older answer rather than blanking the panel over one
        // failed refresh; a lap never answered for has no gearing.
        if (!cache.has(lapId)) cache.set(lapId, { epoch, gearing: null });
      })
      .finally(() => {
        if (!cancelled) setLanded((n) => n + 1);
      });
    return () => {
      cancelled = true;
    };
  }, [lapId, epoch, cache]);

  if (lapId == null) return null;
  const hit = cache.get(lapId);
  return hit ? hit.gearing : undefined;
}

// What the speeds and lines rest on, in a few words. Until the comparison
// arrives there are no samples to fit, so the table shows GT7's top speed or
// nothing, and the caption says so rather than calling the fit failed.
function caption(scale: GearScale | null, haveSamples: boolean): string {
  if (scale?.source === "fit") return "Lines fitted to this lap's samples.";
  if (!haveSamples) {
    return scale
      ? "Speeds from GT7's top speed until the lap's samples load."
      : "Speeds appear once the lap's samples load.";
  }
  return scale
    ? "Lines from GT7's top speed: this lap's samples gave no consistent fit."
    : "No lines: no consistent fit, and no top speed recorded for this lap.";
}

export function GearingPanel({
  lap,
  reference,
  units,
  cursorDist,
}: {
  lap: GearingLap;
  /** The reference lap when it is not `lap`: drawn behind if its gearbox differs. */
  reference: GearingLap | null;
  units: Units;
  cursorDist: number | null;
}) {
  const [cache] = useState<GearingCache>(() => new Map());
  const gearing = useLapGearing(lap.id, cache);
  const refGearing = useLapGearing(reference?.id ?? null, cache);

  const scale = useMemo(
    () => (gearing ? gearScale(lap.series, gearing) : null),
    [lap.series, gearing],
  );
  // On the same gearbox the reference's lines are the focus lap's own, and a
  // second cloud of dots over them is clutter.
  const ghost = useMemo<Ghost | null>(() => {
    if (!reference?.series || !gearing || !refGearing?.ratios.length) return null;
    if (sameGearing(gearing, refGearing)) return null;
    return {
      label: reference.label,
      series: reference.series,
      gearing: refGearing,
      scale: gearScale(reference.series, refGearing),
    };
  }, [reference, refGearing, gearing]);

  if (gearing === undefined) return <div className="skeleton m-3 h-16" />;
  if (gearing === null || gearing.ratios.length === 0) {
    return (
      <div className="p-3 text-xs text-ink-dim">
        No gearing data for this lap (recorded before Tier 1).
      </div>
    );
  }

  const atRedline = scale ? redlineSpeeds(gearing, scale.k) : null;
  return (
    <div>
      <div className="p-3">
        <table className="w-full font-tabular text-xs">
          <thead>
            <tr className="text-left text-ink-dim">
              <th className="pb-1 font-normal">Gear</th>
              <th className="pb-1 font-normal">Ratio</th>
              <th className="pb-1 text-right font-normal">est. @ redline</th>
            </tr>
          </thead>
          <tbody>
            {gearing.ratios.map((ratio, i) => (
              <tr key={i} className="border-t border-edge/50">
                <td className="py-1 text-ink-dim">
                  <i
                    className="mr-1.5 inline-block h-1.5 w-1.5 rounded-full align-middle"
                    style={{ backgroundColor: gearColor(i + 1) }}
                  />
                  {i + 1}
                </td>
                <td className="py-1">{ratio.toFixed(3)}</td>
                <td className="py-1 text-right">
                  {atRedline
                    ? `${Math.round(speedValue(atRedline[i], units))} ${speedUnit(units)}`
                    : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="mt-2 flex justify-between font-tabular text-[11px] text-ink-dim">
          {plausibleTopSpeed(gearing) && (
            <span>
              Top speed (tune) {Math.round(speedValue(gearing.top_speed, units))} {speedUnit(units)}
            </span>
          )}
          <span className="ml-auto">Redline {Math.round(gearing.rpm_alert).toLocaleString()} rpm</span>
        </div>
      </div>
      {lap.series && (
        <Sawtooth
          series={lap.series}
          gearing={gearing}
          scale={scale}
          ghost={ghost}
          units={units}
          cursorDist={cursorDist}
        />
      )}
      <div className="px-3.5 pb-2.5 text-[10px] leading-relaxed text-ink-faint">
        {caption(scale, lap.series != null)}
        {ghost && (
          <>
            {" "}
            {ghost.scale ? "Grey and dashed" : "Grey"}: {ghost.label}, another gearbox.
          </>
        )}
      </div>
    </div>
  );
}

function Sawtooth({
  series,
  gearing,
  scale,
  ghost,
  units,
  cursorDist,
}: {
  series: Series;
  gearing: LapGearing;
  scale: GearScale | null;
  ghost: Ghost | null;
  units: Units;
  cursorDist: number | null;
}) {
  const chartRef = useRef<echarts.ECharts | null>(null);

  const option = useMemo<EChartsOption>(() => {
    const x = (kmh: number) => speedValue(kmh, units);
    const unit = speedUnit(units);
    const out: SeriesOption[] = [];
    let topRpm = gearing.rpm_alert;

    // The ghost first, beneath everything: grey dots and dashed lines, enough
    // to show another gearbox's spacing without competing with the focus lap.
    if (ghost) {
      if (ghost.scale) {
        const k = ghost.scale.k;
        ghost.gearing.ratios.forEach((ratio, i) => {
          out.push({
            id: `ghost-line-${i + 1}`,
            type: "line",
            data: [
              [0, 0],
              [x(gearSpeed(k, ratio, ghost.gearing.rpm_alert)), ghost.gearing.rpm_alert],
            ],
            showSymbol: false,
            lineStyle: { color: GHOST, width: 1, type: "dashed", opacity: 0.6 },
            silent: true,
            z: 1,
          });
        });
      }
      const points = scatterPoints(ghost.series, ghost.gearing.ratios.length);
      for (const p of points) topRpm = Math.max(topRpm, p.rpm);
      topRpm = Math.max(topRpm, ghost.gearing.rpm_alert);
      out.push({
        id: "ghost-points",
        type: "scatter",
        data: points.map((p) => [x(p.speed), p.rpm]),
        symbolSize: 2.5,
        itemStyle: { color: GHOST, opacity: 0.3 },
        large: true,
        largeThreshold: 1000,
        silent: true,
        z: 1,
      });
    }

    // One line per gear from standstill to the redline, labelled with the
    // gear where it meets it: the table's speed at redline, drawn.
    if (scale) {
      gearing.ratios.forEach((ratio, i) => {
        const gear = i + 1;
        out.push({
          id: `line-${gear}`,
          type: "line",
          data: [
            [0, 0],
            [x(gearSpeed(scale.k, ratio, gearing.rpm_alert)), gearing.rpm_alert],
          ],
          showSymbol: false,
          lineStyle: { color: gearColor(gear), width: 1.25, opacity: 0.85 },
          endLabel: {
            show: true,
            formatter: String(gear),
            color: gearColor(gear),
            fontSize: 10,
            distance: 3,
          },
          silent: true,
          z: 2,
        });
      });
    }

    const byGear = new Map<number, number[][]>();
    for (const p of scatterPoints(series, gearing.ratios.length)) {
      topRpm = Math.max(topRpm, p.rpm);
      const list = byGear.get(p.gear) ?? [];
      list.push([x(p.speed), p.rpm, p.gear]);
      byGear.set(p.gear, list);
    }
    for (const [gear, data] of byGear) {
      out.push({
        id: `gear-${gear}`,
        type: "scatter",
        data,
        symbolSize: 3,
        itemStyle: { color: gearColor(gear), opacity: 0.8 },
        z: 3,
      });
    }

    if (gearing.rpm_alert > 0) {
      out.push({
        id: "redline",
        type: "line",
        data: [],
        silent: true,
        markLine: {
          silent: true,
          symbol: "none",
          label: {
            position: "insideStartTop",
            color: CHART_COLORS.brake,
            fontSize: 9,
            formatter: "redline",
          },
          lineStyle: { color: CHART_COLORS.brake, type: "dashed", width: 1 },
          data: [{ yAxis: gearing.rpm_alert }],
        },
      });
    }

    out.push({
      id: "cursor",
      type: "scatter",
      data: [] as number[][],
      symbolSize: 9,
      itemStyle: { color: "#fff", borderColor: GHOST, borderWidth: 2.5 },
      silent: true,
      z: 10,
    });

    return {
      animation: false,
      grid: { left: 36, right: 14, top: 22, bottom: 34 },
      xAxis: {
        type: "value",
        min: 0,
        name: unit,
        nameLocation: "middle",
        nameGap: 20,
        nameTextStyle: { color: CHART_COLORS.label, fontSize: 9 },
        axisLabel: { color: CHART_COLORS.label, fontSize: 10 },
        axisLine: { lineStyle: { color: CHART_COLORS.axis } },
        splitLine: { show: false },
      },
      yAxis: {
        type: "value",
        min: 0,
        // Room above the redline for its label and the gears' numbers.
        max: Math.ceil((topRpm * 1.04) / 1000) * 1000,
        name: "rpm",
        nameTextStyle: { color: CHART_COLORS.label, fontSize: 9 },
        axisLabel: {
          color: CHART_COLORS.label,
          fontSize: 9,
          formatter: (v: number) => (v >= 1000 ? `${v / 1000}k` : String(v)),
        },
        splitLine: { lineStyle: { color: CHART_COLORS.split } },
      },
      tooltip: {
        trigger: "item",
        confine: true,
        backgroundColor: "#1b1f26",
        borderColor: "#262b33",
        textStyle: { color: "#e6e9ee", fontSize: 11 },
        formatter: (params) => {
          const item = Array.isArray(params) ? params[0] : params;
          const [speed, rpm, gear] = item.value as number[];
          return `<b>Gear ${gear}</b><br/>${Math.round(speed)} ${unit} · ${Math.round(rpm).toLocaleString()} rpm`;
        },
      },
      series: out,
    };
  }, [series, gearing, scale, ghost, units]);

  // The cursor dot merges in by series id, as on the traction circle, and
  // again after every new option, which starts it empty. Its ring takes the
  // colour of the gear the car was in, grey mid-shift.
  useEffect(() => {
    let data: number[][] = [];
    let ring = GHOST;
    if (cursorDist != null && series.dist.length > 0) {
      const i = nearestIndex(series.dist, cursorDist);
      const speed = series.speed?.[i];
      const rpm = series.rpm?.[i];
      if (speed != null && rpm != null) {
        data = [[speedValue(speed, units), rpm]];
        const gear = wholeGear(series.gear?.[i] ?? 0, gearing.ratios.length);
        if (gear != null) ring = gearColor(gear);
      }
    }
    chartRef.current?.setOption(
      { series: [{ id: "cursor", data, itemStyle: { borderColor: ring } }] },
      { notMerge: false, lazyUpdate: true },
    );
  }, [option, series, gearing, units, cursorDist]);

  return (
    <EChart
      option={option}
      className="h-56 w-full"
      onInit={(chart) => {
        chartRef.current = chart;
      }}
    />
  );
}
