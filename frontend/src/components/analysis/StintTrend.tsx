// Stint trend (#111): lap time and tyre temperature lap over lap, on one
// chart, so that tyres going away and the pace going with them can be seen
// together. The lap-time chart beside the consistency panel says how much the
// laps vary; this says which way they are heading, and what the tyres were
// doing while they did.
//
// GT7 broadcasts no tyre wear. Temperature and the drift in lap time are the
// only proxies there are, and the panel says so under the chart.
//
// The session is split into stints at pit stops, and each stint has its own
// drift: the step from worn tyres to new ones is in neither. A lap that does
// not count — partial, or ruled out by hand — and a lap with a stop in it
// are hollow markers off the line: there, and no part of the trend.

import type * as echarts from "echarts";
import type { EChartsOption, SeriesOption } from "echarts";
import { useMemo, useRef, useState } from "react";
import { CHART_COLORS, EChart } from "@/components/EChart";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { Tip } from "@/components/ui/Tooltip";
import { useT, type MessageKey, type Translate } from "@/i18n";
import { formatLapTime } from "@/lib/format";
import { formatDrift, formatWarming, stintScale, stintSpan, type StintPoint } from "@/lib/stint";
import { excludeReasonLabel, type StintLap, type StintTrend } from "@/lib/types";

const DIM = "#5b6370";
// Two warm colours for two temperatures. Not the blue the rest of the app
// would reach for: the fitted drift is drawn in the accent, and a blue line
// beside a blue dashed one is one line too many.
const FRONT = CHART_COLORS.warn;
const REAR = CHART_COLORS.brake;
const FUEL = CHART_COLORS.throttle;

const WHEELS: readonly {
  key: "fl" | "fr" | "rl" | "rr";
  labelKey: MessageKey;
  color: string;
  dash: boolean;
}[] = [
  { key: "fl", labelKey: "common.wheel.fl", color: FRONT, dash: false },
  { key: "fr", labelKey: "common.wheel.fr", color: FRONT, dash: true },
  { key: "rl", labelKey: "common.wheel.rl", color: REAR, dash: false },
  { key: "rr", labelKey: "common.wheel.rr", color: REAR, dash: true },
];

type TempMode = "axle" | "wheel";

interface Props {
  trend: StintTrend;
  selected: number[];
  lapColors: Record<string, string>;
  /** Click a lap to add it to the comparison, or take it back out. */
  onToggleLap?: (lapId: number) => void;
}

interface PointDatum {
  value: [number, number];
  lapId: number;
}

function whyNot(lap: StintLap, t: Translate): string {
  if (lap.pit) return t("stint.why.pit");
  if (lap.exclude_reason) return t("stint.why.excluded", { reason: excludeReasonLabel(lap.exclude_reason) });
  return lap.counts ? "" : t("stint.why.partial");
}

export function StintTrendChart({ trend, selected, lapColors, onToggleLap }: Props) {
  const t = useT();
  const [mode, setMode] = useState<TempMode>("axle");
  const [showFuel, setShowFuel] = useState(false);
  const toggleRef = useRef(onToggleLap);
  toggleRef.current = onToggleLap;

  const hasTemps = trend.laps.some((lap) => lap.tt_front != null || lap.tt_rear != null);
  const hasFuel = useMemo(() => {
    const levels = trend.laps.map((lap) => lap.fuel_start).filter((v): v is number => v != null);
    // A session with fuel consumption off never moves: a flat line explains
    // nothing about the pace.
    return levels.length > 1 && Math.max(...levels) - Math.min(...levels) > 0.05;
  }, [trend]);
  const fuelOn = showFuel && hasFuel;

  const option = useMemo<EChartsOption | null>(() => {
    const scale = stintScale(trend.laps);
    if (!scale) return null;
    const byNumber = new Map(trend.laps.map((lap) => [lap.number, lap]));
    const numbers = trend.laps.map((lap) => lap.number);
    const stintOf = new Map(trend.stints.map((s) => [s.n, s]));

    const colorOf = (lapId: number, fallback: string) =>
      selected.includes(lapId) ? (lapColors[String(lapId)] ?? fallback) : fallback;
    const datum = (p: StintPoint): PointDatum => ({
      value: [p.lap.number, p.seconds],
      lapId: p.lap.id,
    });

    const series: SeriesOption[] = [];
    const pitLaps = trend.laps.filter((lap) => lap.pit).map((lap) => lap.number);

    for (const stint of trend.stints) {
      const points = scale.points.filter((p) => p.lap.stint === stint.n);
      if (points.length === 0) continue;
      series.push({
        // The thread through the stint's counting laps. Clipped laps are
        // left out of it: a line up to the top edge and back says the lap
        // was that slow, which it was not — it was slower.
        id: `pace-${stint.n}`,
        type: "line",
        yAxisIndex: 0,
        data: points.filter((p) => !p.clipped).map((p) => [p.lap.number, p.seconds]),
        showSymbol: false,
        lineStyle: { color: CHART_COLORS.axis, width: 1 },
        silent: true,
        z: 2,
        // The stops, once: drawn from the first stint's series only.
        markLine:
          stint.n === trend.stints[0].n && pitLaps.length > 0
            ? {
                silent: true,
                symbol: "none",
                label: {
                  position: "insideEndTop",
                  color: CHART_COLORS.label,
                  fontSize: 9,
                  formatter: t("stint.pitMark"),
                },
                lineStyle: { color: CHART_COLORS.label, type: "dashed", width: 1 },
                data: pitLaps.map((n) => ({ xAxis: n })),
              }
            : undefined,
      });
      if (stint.pace_fit && stint.pace_ms_per_lap != null) {
        const [from, to] = stint.pace_fit;
        series.push({
          id: `fit-${stint.n}`,
          type: "line",
          yAxisIndex: 0,
          data: [
            [from[0], from[1] / 1000],
            [to[0], to[1] / 1000],
          ],
          showSymbol: false,
          lineStyle: { color: CHART_COLORS.accent, width: 1.5, type: "dashed", opacity: 0.9 },
          endLabel: {
            show: true,
            formatter: formatDrift(stint.pace_ms_per_lap),
            color: CHART_COLORS.accent,
            fontSize: 10,
            distance: 4,
          },
          silent: true,
          z: 3,
        });
      }
    }

    series.push(
      {
        id: "counted",
        type: "scatter",
        yAxisIndex: 0,
        data: scale.points
          .filter((p) => !p.clipped)
          .map((p) => ({
            ...datum(p),
            symbolSize: selected.includes(p.lap.id) ? 10 : 6,
            itemStyle: { color: colorOf(p.lap.id, CHART_COLORS.value) },
          })),
        cursor: "pointer",
        z: 5,
      },
      {
        id: "gaps",
        type: "scatter",
        yAxisIndex: 0,
        data: scale.gaps
          .filter((p) => !p.clipped)
          .map((p) => ({
            ...datum(p),
            symbol: p.lap.pit ? "rect" : "circle",
            symbolSize: selected.includes(p.lap.id) ? 10 : 7,
            itemStyle: {
              color: "transparent",
              borderColor: colorOf(p.lap.id, DIM),
              borderWidth: 1.5,
            },
          })),
        cursor: "pointer",
        z: 4,
      },
      {
        id: "clipped",
        type: "scatter",
        yAxisIndex: 0,
        symbol: "triangle",
        data: [...scale.points, ...scale.gaps]
          .filter((p) => p.clipped)
          .map((p) => ({
            ...datum(p),
            symbolSize: 8,
            itemStyle: {
              color: "transparent",
              borderColor: colorOf(p.lap.id, DIM),
              borderWidth: 1.5,
            },
          })),
        cursor: "pointer",
        z: 4,
      },
    );

    // Temperatures: a line per stint, so that the step from one set of
    // tyres to the next is not drawn as a slope between them.
    if (hasTemps) {
      const lines =
        mode === "axle"
          ? [
              { id: "front", color: FRONT, dash: false, of: (lap: StintLap) => lap.tt_front },
              { id: "rear", color: REAR, dash: false, of: (lap: StintLap) => lap.tt_rear },
            ]
          : WHEELS.map((w) => ({
              id: w.key,
              color: w.color,
              dash: w.dash,
              of: (lap: StintLap) => lap.tt[w.key]?.avg ?? null,
            }));
      for (const stint of trend.stints) {
        const laps = trend.laps.filter((lap) => lap.stint === stint.n && lap.counts);
        for (const line of lines) {
          const data = laps
            .map((lap) => [lap.number, line.of(lap)] as [number, number | null])
            .filter((d): d is [number, number] => d[1] != null);
          if (data.length === 0) continue;
          series.push({
            id: `tt-${line.id}-${stint.n}`,
            type: "line",
            yAxisIndex: 1,
            data,
            showSymbol: data.length === 1,
            symbolSize: 4,
            lineStyle: {
              color: line.color,
              width: 1.6,
              type: line.dash ? "dashed" : "solid",
            },
            itemStyle: { color: line.color },
            silent: true,
            z: 1,
          });
        }
      }
    }

    if (fuelOn) {
      series.push({
        id: "fuel",
        type: "line",
        yAxisIndex: 2,
        data: trend.laps
          .filter((lap) => lap.fuel_start != null)
          .map((lap) => [lap.number, lap.fuel_start as number]),
        showSymbol: false,
        lineStyle: { color: FUEL, width: 1.2, type: "dotted" },
        silent: true,
        z: 1,
      });
    }

    const axisLabel = { color: CHART_COLORS.label, fontSize: 9 };
    return {
      animation: false,
      grid: { left: 58, right: fuelOn ? 86 : 44, top: 14, bottom: 24 },
      xAxis: {
        type: "value",
        min: Math.min(...numbers) - 0.5,
        max: Math.max(...numbers) + 0.5,
        minInterval: 1,
        axisLabel: {
          color: CHART_COLORS.label,
          fontSize: 10,
          // The half-lap margins either end put ticks at 0.5 and N + 0.5.
          formatter: (v: number) => (Number.isInteger(v) ? t("lapChart.lapTick", { n: String(v) }) : ""),
        },
        axisLine: { lineStyle: { color: CHART_COLORS.axis } },
        splitLine: { show: false },
        axisPointer: { show: true, snap: true, lineStyle: { color: CHART_COLORS.axis } },
      },
      yAxis: [
        {
          type: "value",
          min: scale.yMin,
          max: scale.yMax,
          axisLabel: {
            ...axisLabel,
            formatter: (v: number) => formatLapTime(v * 1000).slice(0, -2),
          },
          splitLine: { lineStyle: { color: CHART_COLORS.split } },
        },
        {
          type: "value",
          scale: true,
          show: hasTemps,
          position: "right",
          axisLabel: { ...axisLabel, formatter: "{value}°" },
          splitLine: { show: false },
        },
        {
          type: "value",
          scale: true,
          show: fuelOn,
          position: "right",
          offset: 42,
          axisLabel: { ...axisLabel, color: FUEL },
          axisLine: { show: false },
          splitLine: { show: false },
        },
      ],
      tooltip: {
        trigger: "axis",
        confine: true,
        backgroundColor: "#1b1f26",
        borderColor: "#262b33",
        textStyle: { color: "#e6e9ee", fontSize: 11 },
        formatter: (params) => {
          const first = Array.isArray(params) ? params[0] : params;
          const at = Number((first as { axisValue?: number | string })?.axisValue);
          const lap = byNumber.get(Math.round(at));
          if (!lap) return "";
          const stint = lap.stint != null ? stintOf.get(lap.stint) : undefined;
          const why = whyNot(lap, t);
          const lines = [
            `<b>${t("lapChart.lapN", { n: String(lap.number) })}</b> · ${formatLapTime(lap.time_ms)}` +
              (stint && trend.stints.length > 1 ? ` · ${t("stint.label", { n: String(stint.n) })}` : ""),
          ];
          if (why) lines.push(t("stint.tip.notInTrend", { why }));
          if (lap.tt_front != null && lap.tt_rear != null) {
            lines.push(
              `<span style="color:${FRONT}">${t("stint.front", { value: `${lap.tt_front.toFixed(1)} °C` })}</span> · ` +
                `<span style="color:${REAR}">${t("stint.rear", { value: `${lap.tt_rear.toFixed(1)} °C` })}</span>`,
            );
            const wheels = WHEELS.map((w) => {
              const temp = lap.tt[w.key];
              return temp ? `${t(w.labelKey)} ${temp.avg.toFixed(0)}/${temp.max.toFixed(0)}` : null;
            }).filter(Boolean);
            if (wheels.length === 4) {
              lines.push(
                `<span style="color:${CHART_COLORS.label}">${t("stint.tip.avgMax", { wheels: wheels.join(" · ") })}</span>`,
              );
            }
          }
          if (lap.fuel_start != null && hasFuel) {
            lines.push(
              `<span style="color:${FUEL}">${t("stint.tip.fuel", { value: lap.fuel_start.toFixed(1) })}</span>`,
            );
          }
          return lines.join("<br/>");
        },
      },
      series,
    };
  }, [trend, selected, lapColors, mode, fuelOn, hasTemps, hasFuel, t]);

  if (!option) return null;

  const drifting = trend.stints.filter((s) => s.pace_ms_per_lap != null);

  return (
    <div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3.5 pt-2.5 text-[10.5px] text-ink-dim">
        {hasTemps && (
          <span className="inline-flex items-center gap-1.5">
            {t("stint.tyres")}
            <SegmentedControl
              ariaLabel={t("stint.tyreTemps")}
              size="sm"
              value={mode}
              onValueChange={setMode}
              options={[
                { value: "axle", label: t("stint.mode.axle") },
                { value: "wheel", label: t("stint.mode.wheel") },
              ]}
            />
          </span>
        )}
        {hasFuel && (
          <Tip content={t("stint.fuelTip")}>
            <button
              onClick={() => setShowFuel((on) => !on)}
              aria-pressed={showFuel}
              className={`rounded border px-2.5 py-0.5 transition-colors ${
                showFuel
                  ? "border-accent bg-accent/14 text-accent-300"
                  : "border-edge text-ink-dim hover:border-accent hover:text-accent"
              }`}
            >
              {t("common.fuel")}
            </button>
          </Tip>
        )}
        <span className="ml-auto flex flex-wrap gap-x-3 gap-y-1 [&>span]:whitespace-nowrap">
          <span>
            <i className="mr-1.5 inline-block h-1.5 w-1.5 rounded-full bg-ink-muted align-middle" />
            {t("stint.legend.lapTime")}
          </span>
          {hasTemps && mode === "axle" && (
            <>
              <span>
                <i
                  className="mr-1.5 inline-block h-0.5 w-3.5 align-middle"
                  style={{ backgroundColor: FRONT }}
                />
                {t("stint.legend.front")}
              </span>
              <span>
                <i
                  className="mr-1.5 inline-block h-0.5 w-3.5 align-middle"
                  style={{ backgroundColor: REAR }}
                />
                {t("stint.legend.rear")}
              </span>
            </>
          )}
          {hasTemps &&
            mode === "wheel" &&
            WHEELS.map((w) => (
              <span key={w.key}>
                <i
                  className="mr-1.5 inline-block w-3.5 border-t-2 align-middle"
                  style={{ borderColor: w.color, borderStyle: w.dash ? "dashed" : "solid" }}
                />
                {t(w.labelKey)}
              </span>
            ))}
          {fuelOn && (
            <span>
              <i
                className="mr-1.5 inline-block w-3.5 border-t-2 border-dotted align-middle"
                style={{ borderColor: FUEL }}
              />
              {t("stint.legend.fuel")}
            </span>
          )}
        </span>
      </div>
      <EChart
        option={option}
        className="h-56 w-full"
        onInit={(chart: echarts.ECharts) => {
          chart.on("click", (e) => {
            const lapId = (e.data as PointDatum | undefined)?.lapId;
            if (lapId != null) toggleRef.current?.(lapId);
          });
        }}
      />
      <div className="flex flex-wrap gap-x-4 gap-y-1 px-3.5 pb-1 font-tabular text-[10.5px] text-ink-dim">
        {drifting.length === 0 && (
          <span className="text-ink-faint">{t("stint.needsLaps")}</span>
        )}
        {drifting.map((s) => (
          <span key={s.n}>
            {trend.stints.length > 1 && (
              <span className="text-ink-faint">{t("stint.label", { n: String(s.n) })} · </span>
            )}
            {stintSpan(s)}{" "}
            <span className="text-accent">{formatDrift(s.pace_ms_per_lap as number)}</span>
            {s.tt_front_per_lap != null && (
              <>
                {" · "}
                <span style={{ color: FRONT }}>{t("stint.front", { value: formatWarming(s.tt_front_per_lap) })}</span>
              </>
            )}
            {s.tt_rear_per_lap != null && (
              <>
                {" · "}
                <span style={{ color: REAR }}>{t("stint.rear", { value: formatWarming(s.tt_rear_per_lap) })}</span>
              </>
            )}
          </span>
        ))}
        <span className="ml-auto text-ink-faint">{t("stint.key")}</span>
      </div>
      <div className="px-3.5 pb-2.5 text-[10px] text-ink-faint">
        {t("stint.note")}
      </div>
    </div>
  );
}
