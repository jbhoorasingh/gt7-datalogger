// Every lap of the session as a point: lap number across, lap time up (#112).
// The companion to the speed-deviation chart beside it — that one says where
// on the lap the driver varies, this one says which laps did, and whether the
// session was settling down or falling away.
//
// The scale belongs to the laps that count. A pit out-lap forty seconds off
// the pace would otherwise set the axis and flatten every real lap into one
// row of dots, so laps slower than the window are pinned to its top edge as
// hollow arrows: still there, still clickable, no longer in charge.

import type * as echarts from "echarts";
import type { EChartsOption, SeriesOption } from "echarts";
import { useMemo, useRef } from "react";
import { CHART_COLORS, EChart } from "@/components/EChart";
import { countingLaps, formatSpread, lapConsistency } from "@/lib/consistency";
import { formatLapTime } from "@/lib/format";
import { type LapSummary, notCountingLabel } from "@/lib/types";

const DIM = "#5b6370";

interface Props {
  laps: LapSummary[];
  selected: number[];
  lapColors: Record<string, string>;
  /** Click a point to add the lap to the comparison, or take it back out. */
  onToggleLap?: (lapId: number) => void;
}

interface Point {
  value: [number, number];
  lapId: number;
  tip: string;
}

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

export function LapTimeChart({ laps, selected, lapColors, onToggleLap }: Props) {
  const consistency = useMemo(() => lapConsistency(laps), [laps]);
  const toggleRef = useRef(onToggleLap);
  toggleRef.current = onToggleLap;

  const option = useMemo<EChartsOption | null>(() => {
    const counting = countingLaps(laps);
    if (counting.length === 0) return null;
    const times = counting.map((lap) => lap.time_ms / 1000);
    const lo = Math.min(...times);
    const hi = Math.max(...times);
    const pad = Math.max((hi - lo) * 0.12, 0.25);
    const yMin = lo - pad;
    const yMax = hi + pad;
    const bestId = counting.reduce((a, b) => (b.time_ms < a.time_ms ? b : a)).id;

    const chrono = [...laps].sort((a, b) => a.number - b.number);
    const line: [number, number][] = [];
    const counted: Point[] = [];
    const uncounted: Point[] = [];
    const clipped: Point[] = [];
    for (const lap of chrono) {
      if (lap.time_ms <= 0) continue;
      const seconds = lap.time_ms / 1000;
      const why = notCountingLabel(lap);
      const tip =
        `<b>Lap ${lap.number}</b> · ${formatLapTime(lap.time_ms)}` +
        (lap.id === bestId ? " · session best" : "") +
        (why ? `<br/>${escapeHtml(why)} — not in the spread` : "") +
        (consistency && !why
          ? `<br/>${lap.time_ms >= consistency.medianMs ? "+" : "−"}${(
              Math.abs(lap.time_ms - consistency.medianMs) / 1000
            ).toFixed(3)} s against the median`
          : "");
      const point: Point = { value: [lap.number, Math.min(seconds, yMax)], lapId: lap.id, tip };
      if (seconds > yMax) clipped.push(point);
      else if (why) uncounted.push(point);
      else {
        counted.push(point);
        line.push([lap.number, seconds]);
      }
    }

    const colorOf = (p: Point, fallback: string) =>
      selected.includes(p.lapId) ? (lapColors[String(p.lapId)] ?? fallback) : fallback;
    const sizeOf = (p: Point) => (selected.includes(p.lapId) ? 10 : p.lapId === bestId ? 9 : 6);

    const series: SeriesOption[] = [
      {
        // The thread through the counting laps, which is what makes a trend
        // readable: a session tightening up, or tyres going away.
        id: "trend",
        type: "line",
        data: line,
        showSymbol: false,
        lineStyle: { color: CHART_COLORS.axis, width: 1 },
        silent: true,
        z: 1,
        markLine: consistency
          ? {
              silent: true,
              symbol: "none",
              label: {
                position: "insideEndTop",
                color: CHART_COLORS.label,
                fontSize: 9,
                formatter: "median",
              },
              lineStyle: { color: CHART_COLORS.label, type: "dashed", width: 1 },
              data: [{ yAxis: consistency.medianMs / 1000 }],
            }
          : undefined,
        markArea: consistency
          ? {
              silent: true,
              itemStyle: { color: CHART_COLORS.accent, opacity: 0.07 },
              data: [
                [
                  { yAxis: Math.max(yMin, (consistency.medianMs - consistency.stdMs) / 1000) },
                  { yAxis: Math.min(yMax, (consistency.medianMs + consistency.stdMs) / 1000) },
                ],
              ],
            }
          : undefined,
      },
      {
        id: "counted",
        type: "scatter",
        data: counted.map((p) => ({
          ...p,
          symbolSize: sizeOf(p),
          itemStyle: {
            color: colorOf(p, p.lapId === bestId ? CHART_COLORS.accent : CHART_COLORS.value),
          },
        })),
        cursor: "pointer",
        z: 3,
      },
      {
        id: "uncounted",
        type: "scatter",
        data: uncounted.map((p) => ({
          ...p,
          symbolSize: sizeOf(p),
          itemStyle: { color: "transparent", borderColor: colorOf(p, DIM), borderWidth: 1.5 },
        })),
        cursor: "pointer",
        z: 2,
      },
      {
        id: "clipped",
        type: "scatter",
        symbol: "triangle",
        data: clipped.map((p) => ({
          ...p,
          symbolSize: 8,
          itemStyle: { color: "transparent", borderColor: colorOf(p, DIM), borderWidth: 1.5 },
        })),
        cursor: "pointer",
        z: 2,
      },
    ];

    return {
      animation: false,
      grid: { left: 58, right: 14, top: 12, bottom: 24 },
      xAxis: {
        type: "value",
        min: chrono[0].number - 0.5,
        max: chrono[chrono.length - 1].number + 0.5,
        minInterval: 1,
        axisLabel: {
          color: CHART_COLORS.label,
          fontSize: 10,
          // The half-lap margins either end put ticks at 0.5 and N + 0.5.
          formatter: (v: number) => (Number.isInteger(v) ? `L${v}` : ""),
        },
        axisLine: { lineStyle: { color: CHART_COLORS.axis } },
        splitLine: { show: false },
      },
      yAxis: {
        type: "value",
        min: yMin,
        max: yMax,
        axisLabel: {
          color: CHART_COLORS.label,
          fontSize: 9,
          formatter: (v: number) => formatLapTime(v * 1000).slice(0, -2),
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
          return (item?.data as Point | undefined)?.tip ?? "";
        },
      },
      series,
    };
  }, [laps, selected, lapColors, consistency]);

  if (!option) return null;

  return (
    <div>
      <EChart
        option={option}
        className="h-44 w-full"
        onInit={(chart: echarts.ECharts) => {
          chart.on("click", (e) => {
            const lapId = (e.data as Point | undefined)?.lapId;
            if (lapId != null) toggleRef.current?.(lapId);
          });
        }}
      />
      <div className="flex flex-wrap gap-x-3 gap-y-1 px-3.5 pb-2.5 font-tabular text-[10.5px] text-ink-dim">
        {consistency ? (
          <>
            <span>
              spread <span className="text-ink">{formatSpread(consistency.stdMs)}</span>
              <span className="text-ink-faint"> · {consistency.pct.toFixed(2)}% of median</span>
            </span>
            <span>
              median <span className="text-ink">{formatLapTime(consistency.medianMs)}</span>
            </span>
            <span>
              best <span className="text-accent">{formatLapTime(consistency.bestMs)}</span>
            </span>
            <span className="text-ink-faint">{consistency.laps} laps counted</span>
          </>
        ) : (
          <span className="text-ink-faint">Spread needs three laps that count</span>
        )}
        <span className="ml-auto text-ink-faint">○ not counted · △ off the scale</span>
      </div>
    </div>
  );
}
