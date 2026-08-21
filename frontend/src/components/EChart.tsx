// Thin ECharts wrapper: theme defaults, resize handling, optional group connect.
//
// ECharts is imported modularly (#33): only the chart and component types the
// app actually renders are registered, instead of the ~1.1 MB whole-library
// import. Adding a new series or option feature to any chart may need its
// module registered here — a missing one fails loudly at render with an
// "is not loaded" error. Type-only imports still come from "echarts"; they
// are erased at build time and cost nothing.

import type * as echartsTypes from "echarts";
import { CustomChart, EffectScatterChart, LineChart, ScatterChart } from "echarts/charts";
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TitleComponent,
  ToolboxComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";
import { SERIES_COLORS } from "@/lib/colors";

echarts.use([
  LineChart,
  ScatterChart,
  CustomChart,
  EffectScatterChart, // survey map's gap beacons
  GridComponent,
  TitleComponent, // StackedCharts per-panel titles
  TooltipComponent,
  DataZoomComponent,
  ToolboxComponent, // StackedCharts zoom controls
  LegendComponent,
  MarkLineComponent,
  MarkAreaComponent, // delta zero-line + event bands
  CanvasRenderer,
]);

export const CHART_COLORS = {
  axis: "#3a414c",
  label: "#8b93a1", // --color-ink-dim
  value: "#aeb6c2", // --color-ink-muted, for readouts beside a label
  split: "#1e232b",
  series: [...SERIES_COLORS] as string[],
  throttle: "#4ade80",
  brake: "#f47272",
  coast: "#5b93f5",
  warn: "#f5b14e",
};

export function baseGrid(): echartsTypes.GridComponentOption {
  return { left: 52, right: 16, top: 28, bottom: 24, containLabel: false };
}

export function baseAxis(name?: string): echartsTypes.XAXisComponentOption {
  return {
    type: "value",
    name,
    axisLine: { lineStyle: { color: CHART_COLORS.axis } },
    axisLabel: { color: CHART_COLORS.label, fontSize: 10 },
    splitLine: { lineStyle: { color: CHART_COLORS.split } },
  };
}

interface Props {
  option: echartsTypes.EChartsOption;
  group?: string;
  className?: string;
  onInit?: (chart: echartsTypes.ECharts) => void;
  notMerge?: boolean;
  // In merge mode, replace these components wholesale (matched by id) so
  // entries dropped from the option are removed instead of lingering.
  replaceMerge?: string[];
}

export function EChart({ option, group, className, onInit, notMerge, replaceMerge }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echartsTypes.ECharts | null>(null);

  useEffect(() => {
    if (!el.current) return;
    const chart = echarts.init(el.current, undefined, { renderer: "canvas" });
    chartRef.current = chart;
    if (group) {
      chart.group = group;
      echarts.connect(group);
    }
    onInit?.(chart);
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(el.current);
    return () => {
      observer.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
    // Deliberately depends only on `group`: init/dispose must not re-run on option changes.
  }, [group]);

  useEffect(() => {
    chartRef.current?.setOption(option, {
      notMerge: notMerge ?? true,
      replaceMerge,
      lazyUpdate: true,
    });
  }, [option, notMerge, replaceMerge]);

  return <div ref={el} className={className ?? "h-48 w-full"} />;
}
