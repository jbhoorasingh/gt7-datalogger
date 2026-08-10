// Track map built from lap positions. The reference lap is colored by input
// zone (throttle green / brake red / coast blue) with speed peaks & valleys;
// every other selected lap is overlaid as a solid line in its chart color —
// like GT7's own Data Logger, but with a synced cursor dot per lap showing
// the spatial gap at the hovered distance.

import type * as echarts from "echarts";
import type { EChartsOption, SeriesOption } from "echarts";
import { useEffect, useMemo, useRef } from "react";
import { CHART_COLORS, EChart } from "@/components/EChart";
import { type CompareLapEntry, kerbWheelCount, looseWheelCount } from "@/lib/types";

const ZONE_COLORS = [CHART_COLORS.brake, CHART_COLORS.coast, CHART_COLORS.throttle];

// Surface-contact halos under the reference line (packet-C recordings):
// kerb strikes in yellow, wheels on grass/gravel/dirt in orange.
const KERB_COLOR = "#eab308";
const LOOSE_COLOR = "#f97316";

// Numbered circles are readable up to about this many corners in view;
// beyond that (or fully zoomed out on a long track) they collapse to dots.
const MAX_NUMBERED_CORNERS = 30;

function zoneOf(throttle: number, brake: number): number {
  if (brake >= 1) return 0;
  if (throttle >= 1) return 2;
  return 1;
}

export interface MapLap {
  id: string;
  entry: CompareLapEntry;
  color: string; // chart series color for this lap
  label: string;
  isRef: boolean;
}

export function RaceLineMap({
  laps,
  cursorDist,
  step,
  zoomRange,
}: {
  laps: MapLap[];
  cursorDist: number | null;
  step: number;
  zoomRange?: [number, number] | null;
}) {
  const chartRef = useRef<echarts.ECharts | null>(null);
  const ref = laps.find((lap) => lap.isRef);

  const option = useMemo<EChartsOption>(() => {
    const series: SeriesOption[] = [];

    let xMin: number | undefined;
    let xMax: number | undefined;
    let zMin: number | undefined;
    let zMax: number | undefined;

    if (zoomRange && ref) {
      const s = ref.entry.series;
      let minX = Infinity;
      let maxX = -Infinity;
      let minZ = Infinity;
      let maxZ = -Infinity;
      let count = 0;

      for (let i = 0; i < s.dist.length; i++) {
        const d = s.dist[i];
        if (d >= zoomRange[0] && d <= zoomRange[1]) {
          const x = s.pos_x[i];
          const z = s.pos_z[i];
          if (x != null && z != null) {
            if (x < minX) minX = x;
            if (x > maxX) maxX = x;
            if (z < minZ) minZ = z;
            if (z > maxZ) maxZ = z;
            count++;
          }
        }
      }

      if (count > 0 && isFinite(minX) && isFinite(maxX) && isFinite(minZ) && isFinite(maxZ)) {
        const padX = Math.max((maxX - minX) * 0.15, 8);
        const padZ = Math.max((maxZ - minZ) * 0.15, 8);
        xMin = minX - padX;
        xMax = maxX + padX;
        zMin = minZ - padZ;
        zMax = maxZ + padZ;
      }
    }

    // Comparison laps first (under the reference), as solid colored lines.
    // Per-point itemStyle only affects symbols, never the line stroke, so
    // zoom-dimming needs two series: a dim full-lap line plus a bright
    // overlay covering only the zoomed section.
    for (const lap of laps) {
      if (lap.isRef) continue;
      const s = lap.entry.series;
      series.push({
        id: `line-${lap.id}`,
        type: "line",
        data: s.dist.map((_, i) => [s.pos_x[i], s.pos_z[i]]),
        showSymbol: false,
        lineStyle: { color: lap.color, width: 1.6, opacity: zoomRange ? 0.15 : 0.9 },
        silent: true,
        z: 2,
      });
      if (zoomRange) {
        const inZoom: [number, number][] = [];
        for (let i = 0; i < s.dist.length; i++) {
          if (s.dist[i] >= zoomRange[0] && s.dist[i] <= zoomRange[1]) {
            inZoom.push([s.pos_x[i], s.pos_z[i]]);
          }
        }
        series.push({
          id: `line-zoom-${lap.id}`,
          type: "line",
          data: inZoom,
          showSymbol: false,
          lineStyle: { color: lap.color, width: 2, opacity: 0.9 },
          silent: true,
          z: 2,
        });
      }
    }

    if (ref) {
      const s = ref.entry.series;
      const points = s.dist.map((d, i) => {
        const inZoom = zoomRange ? d >= zoomRange[0] && d <= zoomRange[1] : true;
        return {
          value: [s.pos_x[i], s.pos_z[i]],
          symbolSize: inZoom ? 4 : 2,
          itemStyle: {
            color: ZONE_COLORS[zoneOf(s.throttle[i], s.brake[i])],
            opacity: inZoom ? 1 : 0.15,
          },
        };
      });
      // Surface halos, drawn beneath the input-zone dots. A kerb-only touch
      // is routine; any loose-surface wheel is the interesting one, so loose
      // wins when a sample has both (two wheels on the kerb, two on grass).
      const surface = s.surface;
      if (surface?.some((v) => v > 0)) {
        const kerbPts: Array<{ value: number[]; itemStyle: { opacity: number } }> = [];
        const loosePts: Array<{ value: number[]; itemStyle: { opacity: number } }> = [];
        for (let i = 0; i < s.dist.length; i++) {
          const v = surface[i] ?? 0;
          const bucket =
            looseWheelCount(v) > 0 ? loosePts : kerbWheelCount(v) > 0 ? kerbPts : null;
          if (!bucket) continue;
          const inZoom = zoomRange
            ? s.dist[i] >= zoomRange[0] && s.dist[i] <= zoomRange[1]
            : true;
          bucket.push({
            value: [s.pos_x[i], s.pos_z[i]],
            itemStyle: { opacity: inZoom ? 0.55 : 0.1 },
          });
        }
        series.push(
          {
            id: "surface-kerb",
            type: "scatter",
            data: kerbPts,
            symbolSize: 8,
            itemStyle: { color: KERB_COLOR },
            silent: true,
            z: 2.5,
          },
          {
            id: "surface-loose",
            type: "scatter",
            data: loosePts,
            symbolSize: 9,
            itemStyle: { color: LOOSE_COLOR },
            silent: true,
            z: 2.6,
          },
        );
      }

      const pv = ref.entry.peaks_valleys;
      const peaks = pv.peaks.filter(
        (p) => !zoomRange || (p.dist >= zoomRange[0] && p.dist <= zoomRange[1]),
      );
      const valleys = pv.valleys.filter(
        (p) => !zoomRange || (p.dist >= zoomRange[0] && p.dist <= zoomRange[1]),
      );

      series.push(
        { type: "scatter", data: points, symbolSize: 3.5, silent: true, z: 3 },
        {
          type: "scatter",
          data: peaks.map((p) => [p.x, p.z]),
          symbol: "triangle",
          symbolSize: 9,
          itemStyle: { color: "#facc15" },
          silent: true,
          z: 5,
        },
        {
          type: "scatter",
          data: valleys.map((p) => [p.x, p.z]),
          symbol: "triangle",
          symbolRotate: 180,
          symbolSize: 9,
          itemStyle: { color: "#c084fc" },
          silent: true,
          z: 5,
        },
      );

      // Auto-numbered corners (detected on the reference lap). Numbered
      // circles while the view shows a readable amount; plain dots otherwise.
      const corners = ref.entry.corners ?? [];
      const cornersInView = corners.filter(
        (c) => !zoomRange || (c.apex_dist >= zoomRange[0] && c.apex_dist <= zoomRange[1]),
      );
      const numbered =
        cornersInView.length > 0 && cornersInView.length <= MAX_NUMBERED_CORNERS;
      if (cornersInView.length > 0) {
        series.push({
          id: "corners",
          type: "scatter",
          data: cornersInView.map((c) => ({
            value: [c.apex_x, c.apex_z],
            name: String(c.n),
          })),
          symbolSize: numbered ? 15 : 5,
          itemStyle: numbered
            ? { color: "#14171c", borderColor: CHART_COLORS.label, borderWidth: 1 }
            : { color: CHART_COLORS.label, opacity: 0.85 },
          label: {
            show: numbered,
            position: "inside",
            formatter: "{b}",
            color: "#e5e7eb",
            fontSize: 9,
            fontWeight: "bold",
          },
          silent: true,
          z: 4, // above the race line dots, below peak/valley markers & cursors
        });
      }
    }

    // One synced cursor dot per lap, in the lap's color (reference white).
    for (const lap of laps) {
      series.push({
        id: `cursor-${lap.id}`,
        type: "scatter",
        data: [] as number[][],
        symbolSize: lap.isRef ? 12 : 9,
        itemStyle: lap.isRef
          ? { color: "#fff", borderColor: CHART_COLORS.series[0], borderWidth: 3 }
          : { color: lap.color, borderColor: "#fff", borderWidth: 1.5 },
        z: 10,
        silent: true,
      });
    }

    return {
      animation: false,
      grid: { left: 8, right: 8, top: 8, bottom: 8 },
      xAxis: {
        type: "value",
        show: false,
        scale: true,
        ...(xMin != null ? { min: xMin, max: xMax } : {}),
      },
      yAxis: {
        type: "value",
        show: false,
        scale: true,
        inverse: true,
        ...(zMin != null ? { min: zMin, max: zMax } : {}),
      },
      tooltip: { show: false },
      series,
    };
    // Deliberately depends only on laps/zoomRange: cursor updates merge separately below.
  }, [laps, zoomRange]);

  // Cursor updates merge into the existing chart by series id — no rebuild.
  useEffect(() => {
    const updates: SeriesOption[] = laps.map((lap) => {
      const s = lap.entry.series;
      let data: number[][] = [];
      if (cursorDist != null && s.dist.length > 0 && step > 0) {
        const i = Math.min(s.dist.length - 1, Math.max(0, Math.round(cursorDist / step)));
        if (Number.isFinite(i) && s.pos_x[i] != null && s.pos_z[i] != null) {
          data = [[s.pos_x[i], s.pos_z[i]]];
        }
      }
      return { id: `cursor-${lap.id}`, data } as SeriesOption;
    });
    chartRef.current?.setOption({ series: updates }, { notMerge: false, lazyUpdate: true });
  }, [laps, cursorDist, step]);

  const others = laps.filter((lap) => !lap.isRef);
  const hasSurface = !!ref?.entry.series.surface?.some((v) => v > 0);

  return (
    <div className="relative">
      <EChart
        option={option}
        className="aspect-square w-full"
        onInit={(chart) => {
          chartRef.current = chart;
        }}
      />
      {others.length > 0 && (
        <div className="absolute right-2 top-2 space-y-0.5 text-[10px]">
          {others.map((lap) => (
            <div key={lap.id} className="flex items-center justify-end gap-1.5 text-ink-dim">
              {lap.label}
              <i className="inline-block h-0.5 w-4" style={{ backgroundColor: lap.color }} />
            </div>
          ))}
        </div>
      )}
      <div className="absolute bottom-2 left-2 flex gap-3 text-[10px] text-ink-dim">
        <span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-throttle" />throttle</span>
        <span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-brake" />brake</span>
        <span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-coast" />coast</span>
        <span className="text-warn">▲ peak</span>
        <span className="text-[#c084fc]">▼ valley</span>
        {hasSurface && (
          <>
            <span>
              <i
                className="mr-1 inline-block h-2 w-2 rounded-full"
                style={{ backgroundColor: KERB_COLOR }}
              />
              kerb
            </span>
            <span>
              <i
                className="mr-1 inline-block h-2 w-2 rounded-full"
                style={{ backgroundColor: LOOSE_COLOR }}
              />
              off-track
            </span>
          </>
        )}
        {(ref?.entry.corners?.length ?? 0) > 0 && (
          <span>
            <i className="mr-1 inline-block h-2.5 w-2.5 rounded-full border border-ink-dim text-center align-middle text-[7px] leading-[9px]">
              1
            </i>
            corner
          </span>
        )}
      </div>
    </div>
  );
}
