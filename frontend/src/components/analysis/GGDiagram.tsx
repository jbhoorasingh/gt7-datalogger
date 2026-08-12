// G-G diagram (traction circle) from the broadcast accelerometer (#16).
//
// Every point is one moment of the lap plotted as lateral g against
// longitudinal g: braking sits at the bottom, power at the top, corners out
// to the sides, and the combined phases — trail-braking into a corner,
// picking up throttle while still turning — fill the diagonals. How much of
// the ring gets used is the whole story; an empty middle-left/middle-right is
// a car that never brakes and turns at the same time.
//
// The axes are calibrated, not assumed: GT7 documents neither the unit nor
// the sign of sway/surge, so the server fits them against physics the lap
// already recorded (see analysis.accel_calibration) and hands over a
// multiplier to g. When that fit is too weak the panel says so rather than
// drawing a confident-looking circle on an unproven scale.

import type * as echarts from "echarts";
import type { EChartsOption, SeriesOption } from "echarts";
import { useEffect, useMemo, useRef } from "react";
import type { MapLap } from "@/components/analysis/RaceLineMap";
import { CHART_COLORS, EChart } from "@/components/EChart";
import type { AccelCalibration } from "@/lib/types";

const ZONE_COLORS = [CHART_COLORS.brake, CHART_COLORS.coast, CHART_COLORS.throttle];
const RING_STEP_G = 0.5;
const RING_POINTS = 72;
const MIN_AXIS_G = 1.5;

function zoneOf(throttle: number, brake: number): number {
  if (brake >= 1) return 0;
  if (throttle >= 1) return 2;
  return 1;
}

function ring(radius: number): [number, number][] {
  return Array.from({ length: RING_POINTS + 1 }, (_, i) => {
    const a = (i / RING_POINTS) * 2 * Math.PI;
    return [radius * Math.cos(a), radius * Math.sin(a)] as [number, number];
  });
}

export interface GGLap extends MapLap {
  lat: number[]; // g, positive = right-hander as the map draws it
  long: number[]; // g, positive = accelerating
}

// Convert a lap's raw accelerometer series into g using the fitted scale.
// Returns null for laps recorded before packet B, which simply don't appear.
export function ggLap(lap: MapLap, accel: AccelCalibration): GGLap | null {
  const s = lap.entry.series;
  const latRaw = s.acc_lat;
  const longRaw = s.acc_long;
  if (!latRaw?.length || !longRaw?.length) return null;
  const kLat = accel.lateral?.g_per_unit ?? 0;
  const kLong = accel.longitudinal?.g_per_unit ?? 0;
  if (!kLat || !kLong) return null;
  const n = Math.min(latRaw.length, longRaw.length);
  return {
    ...lap,
    lat: Array.from({ length: n }, (_, i) => latRaw[i] * kLat),
    long: Array.from({ length: n }, (_, i) => longRaw[i] * kLong),
  };
}

export function GGDiagram({
  laps,
  accel,
  cursorDist,
  step,
}: {
  laps: GGLap[];
  accel: AccelCalibration;
  cursorDist: number | null;
  step: number;
}) {
  const chartRef = useRef<echarts.ECharts | null>(null);
  const ref = laps.find((lap) => lap.isRef) ?? laps[0];

  const option = useMemo<EChartsOption>(() => {
    // Both axes share one range so a constant-g ring renders as a circle
    // rather than an ellipse — the shape IS the reading here.
    let peak = MIN_AXIS_G;
    for (const lap of laps) {
      for (const v of lap.lat) peak = Math.max(peak, Math.abs(v));
      for (const v of lap.long) peak = Math.max(peak, Math.abs(v));
    }
    const limit = Math.ceil(peak / RING_STEP_G) * RING_STEP_G;
    const rings: number[] = [];
    for (let r = RING_STEP_G; r <= limit + 1e-9; r += RING_STEP_G) rings.push(r);

    const series: SeriesOption[] = [
      {
        id: "rings",
        type: "custom",
        data: rings,
        renderItem: (params, apiCustom) => ({
          type: "polyline",
          shape: {
            points: ring(rings[params.dataIndex as number]).map((p) => apiCustom.coord(p)),
          },
          style: { stroke: CHART_COLORS.axis, fill: "none", lineWidth: 1, opacity: 0.7 },
        }),
        silent: true,
        z: 1,
      },
      {
        id: "axes",
        type: "custom",
        data: [0, 1],
        renderItem: (params, apiCustom) => {
          const horizontal = params.dataIndex === 0;
          const a = apiCustom.coord(horizontal ? [-limit, 0] : [0, -limit]);
          const b = apiCustom.coord(horizontal ? [limit, 0] : [0, limit]);
          return {
            type: "line",
            shape: { x1: a[0], y1: a[1], x2: b[0], y2: b[1] },
            style: { stroke: CHART_COLORS.axis, lineWidth: 1, opacity: 0.9 },
          };
        },
        silent: true,
        z: 1,
      },
    ];

    // Comparison laps first, beneath the reference: faint dots in their own
    // chart colour, enough to show a smaller or lopsided envelope.
    for (const lap of laps) {
      if (lap.isRef) continue;
      series.push({
        id: `gg-${lap.id}`,
        type: "scatter",
        data: lap.lat.map((v, i) => [v, lap.long[i]]),
        symbolSize: 3,
        itemStyle: { color: lap.color, opacity: 0.35 },
        large: true,
        largeThreshold: 1000,
        silent: true,
        z: 2,
      });
    }

    if (ref) {
      const s = ref.entry.series;
      series.push({
        id: `gg-ref`,
        type: "scatter",
        data: ref.lat.map((v, i) => ({
          value: [v, ref.long[i]],
          itemStyle: {
            color: ZONE_COLORS[zoneOf(s.throttle?.[i] ?? 0, s.brake?.[i] ?? 0)],
            opacity: 0.75,
          },
        })),
        symbolSize: 4,
        silent: true,
        z: 3,
      });
    }

    for (const lap of laps) {
      series.push({
        id: `gg-cursor-${lap.id}`,
        type: "scatter",
        data: [] as number[][],
        symbolSize: lap.isRef ? 11 : 8,
        itemStyle: lap.isRef
          ? { color: "#fff", borderColor: CHART_COLORS.series[0], borderWidth: 3 }
          : { color: lap.color, borderColor: "#fff", borderWidth: 1.5 },
        silent: true,
        z: 10,
      });
    }

    const axis = {
      type: "value" as const,
      min: -limit,
      max: limit,
      splitLine: { show: false },
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { show: false },
    };
    return {
      animation: false,
      grid: { left: 10, right: 10, top: 10, bottom: 10 },
      xAxis: axis,
      yAxis: axis,
      tooltip: { show: false },
      series,
    };
  }, [laps, ref]);

  // Cursor dots merge in by series id, exactly like the race-line map.
  useEffect(() => {
    const updates: SeriesOption[] = laps.map((lap) => {
      let data: number[][] = [];
      if (cursorDist != null && step > 0 && lap.lat.length > 0) {
        const i = Math.min(lap.lat.length - 1, Math.max(0, Math.round(cursorDist / step)));
        if (Number.isFinite(i)) data = [[lap.lat[i], lap.long[i]]];
      }
      return { id: `gg-cursor-${lap.id}`, data } as SeriesOption;
    });
    chartRef.current?.setOption({ series: updates }, { notMerge: false, lazyUpdate: true });
  }, [laps, cursorDist, step]);

  const peaks = ref?.entry.gg;
  const lat = accel.lateral;
  const long = accel.longitudinal;
  const proven = !!lat?.fitted && !!long?.fitted;

  return (
    <div>
      <div className="relative">
        <EChart
          option={option}
          className="aspect-square w-full"
          onInit={(chart) => {
            chartRef.current = chart;
          }}
        />
        <span className="pointer-events-none absolute left-1/2 top-2 -translate-x-1/2 text-[10px] text-ink-dim">
          accelerating
        </span>
        <span className="pointer-events-none absolute bottom-2 left-1/2 -translate-x-1/2 text-[10px] text-ink-dim">
          braking
        </span>
        <span className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-[10px] text-ink-dim">
          left
        </span>
        <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-[10px] text-ink-dim">
          right
        </span>
      </div>
      {peaks && (
        <div className="grid grid-cols-4 gap-1 px-3 pb-1 text-center font-tabular text-[11px]">
          <Peak label="brake" value={peaks.braking} />
          <Peak label="left" value={peaks.lat_left} />
          <Peak label="right" value={peaks.lat_right} />
          <Peak label="accel" value={peaks.accel} />
        </div>
      )}
      <div className="px-3 pb-3 pt-1 text-[10px] leading-relaxed text-ink-dim">
        {proven ? (
          <>
            Scale checked against the lap's own physics: lateral vs v·ω
            (R²&nbsp;{lat!.r2.toFixed(2)}), longitudinal vs dv/dt (R²&nbsp;
            {long!.r2.toFixed(2)}). GT7 broadcasts these as{" "}
            <span className="text-ink">{accel.unit}</span>.
          </>
        ) : (
          <>
            <span className="text-warn">Scale unverified</span> — this lap gave
            too little steady cornering or braking to check the broadcast
            channels against v·ω and dv/dt, so g is assumed to be m/s². Shapes
            are still comparable between laps; the numbers may not be.
          </>
        )}
      </div>
    </div>
  );
}

function Peak({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <div className="text-ink">{value.toFixed(2)}g</div>
      <div className="text-ink-dim">{label}</div>
    </div>
  );
}
