// All comparison series in ONE ECharts instance with stacked grids and a
// linked axis pointer: hovering any panel shows the cursor at the same
// distance in every panel. Far cheaper than N connected chart instances.

import type * as echarts from "echarts";
import type { EChartsOption, SeriesOption } from "echarts";
import { useCallback, useEffect, useMemo, useRef } from "react";
import { CHART_COLORS, EChart } from "@/components/EChart";
import { channelValues, type ChannelDef } from "@/lib/channels";
import { lapColor } from "@/lib/colors";
import { speedUnit, type Units } from "@/lib/format";
import { AIDS_ASM, AIDS_TCS, type CompareResult, type LapEvent } from "@/lib/types";

interface PanelDef {
  key: string;
  title: string;
  height: number; // relative weight
  transform?: (v: number, units: Units) => number;
  step?: boolean;
  def?: ChannelDef; // channel-backed panels; absent for the delta panel
}

// The time-diff panel is the anchor of the view and always first;
// every other panel comes from the channel picker.
// Channel-aware value formatting, shared by the tooltip and the per-strip
// playhead readouts.
function formatValueFor(units: Units) {
  return (key: string, v: number): string => {
      if (key.startsWith("slip_") || key === "tire_slip") return `${v.toFixed(2)}×`;
      if (key.startsWith("tt_")) {
        const sign = key === "tt_balance" && v > 0 ? "+" : "";
        return `${sign}${v.toFixed(1)} °C`;
      }
      if (key.startsWith("sus_") || key === "body_height") return `${v.toFixed(1)} mm`;
      switch (key) {
        case "delta":
          return `${v >= 0 ? "+" : ""}${v.toFixed(3)} s`;
        case "speed":
          return `${Math.round(v)} ${speedUnit(units)}`;
        case "throttle":
        case "brake":
          return `${Math.round(v)}%`;
        case "coast":
          return v >= 0.5 ? "coasting" : "—";
        case "gear":
          return `${Math.round(v)}`;
        case "rpm":
          return `${Math.round(v).toLocaleString()} rpm`;
        case "boost":
          return `${v.toFixed(2)} bar`;
        case "yaw_rate":
          return `${v.toFixed(2)} rad/s`;
        default:
          return v.toFixed(2);
      }
  };
}

const DELTA_PANEL: PanelDef = { key: "delta", title: "Time diff (s)", height: 1.2 };

type MarkAreaBand = [{ xAxis: number }, { xAxis: number }];
type MarkAreaData = MarkAreaBand[];

// Contiguous [startDist, endDist] runs where the aids bit is set.
function aidBands(dist: number[], aids: number[] | undefined, bit: number): MarkAreaData {
  if (!aids || aids.length === 0) return [];
  const bands: MarkAreaData = [];
  let start: number | null = null;
  const n = Math.min(dist.length, aids.length);
  for (let i = 0; i < n; i++) {
    const on = (aids[i] & bit) !== 0;
    if (on && start === null) start = dist[i];
    if (!on && start !== null) {
      bands.push([{ xAxis: start }, { xAxis: dist[i] }]);
      start = null;
    }
  }
  if (start !== null) bands.push([{ xAxis: start }, { xAxis: dist[n - 1] }]);
  return bands;
}

// Which event types shade which panel. Suspension events land on either
// suspension channel; slip events on the pedal that caused them.
function eventBandsFor(panelKey: string, events: LapEvent[]): MarkAreaData {
  const wanted =
    panelKey === "brake"
      ? ["lockup"]
      : panelKey === "throttle"
        ? ["wheelspin"]
        : panelKey.startsWith("sus_")
          ? ["bottoming", "kerb"]
          : [];
  if (wanted.length === 0) return [];
  return events
    .filter((e) => wanted.includes(e.type))
    .map(
      (e): MarkAreaBand => [
        { xAxis: e.start_dist },
        { xAxis: Math.max(e.end_dist, e.start_dist + 2) },
      ],
    );
}

const TOP_PAD = 20;
const PANEL_GAP = 26;
// Every component whose COUNT follows the panel list. A plain merge keeps
// whatever the previous option had beyond the end of the new arrays, so
// picking fewer channels left the dropped panels' titles painted over the ones
// that remained — a ghost "Yaw rate (rad/s)" sitting on top of a real label,
// with orphaned grids and axes behind it. Stable identity: an inline literal
// would re-trigger the chart's setOption effect on every render (hover
// re-renders at cursor rate).
const REPLACE_PANEL_PARTS = ["series", "title", "grid", "xAxis", "yAxis"];

export function StackedCharts({
  data,
  lapLabels,
  lapColors,
  units,
  channels,
  onCursorDist,
  zoomRange,
  onZoomChange,
  cursorDist,
  refLapId,
}: {
  data: CompareResult;
  lapLabels: Record<string, string>;
  /** Collision-resolved per-set colors (see lapColorMap); lapColor fallback. */
  lapColors?: Record<string, string>;
  units: Units;
  channels: ChannelDef[]; // ordered, from the channel picker
  onCursorDist?: (dist: number | null) => void;
  zoomRange?: [number, number] | null;
  onZoomChange?: (range: [number, number] | null) => void;
  /** Shared playhead, echoed as a per-strip value readout. */
  cursorDist?: number | null;
  /** Which lap the readouts report — the reference lap. */
  refLapId?: string | null;
}) {
  const chartRef = useRef<echarts.ECharts | null>(null);

  const panels = useMemo<PanelDef[]>(
    () => [
      DELTA_PANEL,
      ...channels.map((c) => ({
        key: c.key,
        title: c.title,
        height: c.height,
        transform: c.transform,
        step: c.step,
        def: c,
      })),
    ],
    [channels],
  );

  // Panel count changes with the picker; the canvas height must follow.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    chart.getDom().style.height = `${panels.length * 110 + TOP_PAD + PANEL_GAP}px`;
    chart.resize();
  }, [panels.length]);
  // True while WE dispatch a zoom action, so the resulting dataZoom event
  // isn't echoed back through onZoomChange (which would loop).
  const applyingZoom = useRef(false);

  const maxDist = useMemo(() => {
    let m = 0;
    for (const lap of Object.values(data.laps)) {
      const dists = lap.series.dist;
      if (dists && dists.length > 0) {
        m = Math.max(m, dists[dists.length - 1]);
      }
    }
    return m;
  }, [data]);
  // The chart's event handlers are bound once (onInit) — read maxDist through
  // a ref so they never see a stale value after the lap selection changes.
  const maxDistRef = useRef(maxDist);
  maxDistRef.current = maxDist;

  // Drag-select zoom uses ECharts' native toolbox mechanism ("dataZoomSelect")
  // instead of hand-rolled pixel math: activating the global cursor makes a
  // plain left-drag draw the selection box and emit a dataZoom event.
  const activateDragZoom = useCallback(() => {
    chartRef.current?.dispatchAction({
      type: "takeGlobalCursor",
      key: "dataZoomSelect",
      dataZoomSelectActive: true,
    });
  }, []);

  // Apply zoomRange (from drag, sector buttons, or reset) to all axes.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    applyingZoom.current = true;
    try {
      if (zoomRange) {
        chart.dispatchAction({
          type: "dataZoom",
          startValue: zoomRange[0],
          endValue: zoomRange[1],
        });
      } else {
        chart.dispatchAction({ type: "dataZoom", start: 0, end: 100 });
      }
    } finally {
      applyingZoom.current = false;
    }
    activateDragZoom(); // option/action updates can drop the select cursor
  }, [zoomRange, activateDragZoom, data]);

  const option = useMemo<EChartsOption>(() => {
    const lapIds = Object.keys(data.laps);
    const heights = panels.map((p) => p.height);
    const totalWeight = heights.reduce((a, b) => a + b, 0);
    const usable = 100 - 8; // percent, minus bottom margin for slider

    const grids: NonNullable<EChartsOption["grid"]> = [];
    const xAxes: object[] = [];
    const yAxes: object[] = [];
    const series: SeriesOption[] = [];
    const titles: object[] = [];
    // Tooltip metadata keyed by series id ("panel|lap") — ids survive merge
    // updates, unlike series indexes, so values can't pair with the wrong
    // panel after the lap selection changes.
    const seriesMeta = new Map<string, { panelKey: string; panelTitle: string; lapId: string }>();

    const formatValue = formatValueFor(units);

    let cursor = 2;
    panels.forEach((panel, gi) => {
      const h = (panel.height / totalWeight) * usable;
      grids.push({
        left: 56,
        right: 12,
        top: `${cursor + 2.2}%`,
        height: `${h - 3}%`,
      });
      titles.push({
        text: panel.title.toUpperCase(),
        left: 56,
        top: `${cursor - 0.4}%`,
        textStyle: { color: CHART_COLORS.label, fontSize: 10, fontWeight: 600 },
      });
      // Right-aligned live value at the playhead. Text is filled in by the
      // cursor effect below rather than here, so moving the playhead does not
      // rebuild the whole option (and with it every series' data). Two titles
      // per panel, so panel i's readout is title 2i+1.
      titles.push({
        text: "",
        right: 12,
        top: `${cursor - 0.4}%`,
        textAlign: "right",
        textStyle: { color: CHART_COLORS.value, fontSize: 10.5, fontWeight: 400 },
      });
      cursor += h;
      xAxes.push({
        type: "value",
        gridIndex: gi,
        min: 0,
        max: "dataMax",
        axisLabel:
          gi === panels.length - 1
            ? { color: CHART_COLORS.label, fontSize: 10, formatter: (v: number) => `${v} m` }
            : { show: false },
        axisLine: { lineStyle: { color: CHART_COLORS.axis } },
        splitLine: { show: false },
        axisTick: { show: gi === panels.length - 1 },
      });
      yAxes.push({
        type: "value",
        gridIndex: gi,
        scale: panel.key !== "throttle" && panel.key !== "brake",
        axisLabel: { color: CHART_COLORS.label, fontSize: 9 },
        splitLine: { lineStyle: { color: CHART_COLORS.split } },
        splitNumber: 3,
      });

      lapIds.forEach((lapId) => {
        const entry = data.laps[lapId];
        const isDelta = panel.key === "delta";
        if (isDelta && !entry.delta) return; // reference lap has no delta
        const dist = isDelta ? entry.delta!.dist : entry.series.dist;
        const raw = isDelta
          ? entry.delta!.delta_ms.map((v) => v / 1000)
          : panel.def
            ? channelValues(panel.def, entry.series)
            : entry.series[panel.key] ?? [];
        if (raw === null) return; // lap predates this channel — skip gracefully
        const values = panel.transform ? raw.map((v) => panel.transform!(v, units)) : raw;
        const seriesId = `${panel.key}|${lapId}`;
        seriesMeta.set(seriesId, { panelKey: panel.key, panelTitle: panel.title, lapId });

        // Shaded context bands: detected events on the causing panel, plus
        // TCS activation on throttle and ASM on speed (aids bitmask runs).
        let bands: MarkAreaData = eventBandsFor(panel.key, entry.events ?? []);
        if (panel.key === "throttle") {
          bands = bands.concat(aidBands(entry.series.dist, entry.series.aids, AIDS_TCS));
        } else if (panel.key === "speed") {
          bands = bands.concat(aidBands(entry.series.dist, entry.series.aids, AIDS_ASM));
        }

        series.push({
          type: "line",
          id: seriesId,
          name: lapLabels[lapId] ?? `Lap ${lapId}`,
          xAxisIndex: gi,
          yAxisIndex: gi,
          data: dist.map((d, i) => [d, values[i]]),
          // Perceptual downsampling to the canvas width (#33): many laps ×
          // many panels can put ~184k points in one chart; drawing them all
          // buys nothing visually and stalls a Pi. Zooming re-samples, so
          // detail comes back where the viewport looks.
          sampling: "lttb",
          showSymbol: false,
          step: panel.step ? "end" : undefined,
          lineStyle: { width: 1.4 },
          color: lapColors?.[lapId] ?? lapColor(Number(lapId)),
          ...(isDelta
            ? {
                markLine: {
                  silent: true,
                  symbol: "none",
                  label: { show: false },
                  lineStyle: { color: CHART_COLORS.label, type: "dashed", width: 1 },
                  data: [{ yAxis: 0 }],
                },
              }
            : {}),
          ...(bands.length > 0
            ? {
                markArea: {
                  silent: true,
                  itemStyle: {
                    color: lapColors?.[lapId] ?? lapColor(Number(lapId)),
                    opacity: 0.14,
                  },
                  data: bands,
                },
              }
            : {}),
        });
      });
    });

    const allXAxisIndices = panels.map((_, i) => i);

    const dataZoom: EChartsOption["dataZoom"] = [
      {
        type: "inside",
        xAxisIndex: allXAxisIndices,
        filterMode: "none",
        zoomOnMouseWheel: false, // Disable scroll wheel zoom so web page scrolling is natural
        moveOnMouseMove: false,
        moveOnMouseWheel: false,
      },
      {
        type: "slider",
        xAxisIndex: allXAxisIndices,
        filterMode: "none",
        bottom: 2,
        height: 18,
        borderColor: CHART_COLORS.axis,
        backgroundColor: "#16191e",
        dataBackground: {
          lineStyle: { color: CHART_COLORS.axis },
          areaStyle: { color: CHART_COLORS.split },
        },
        selectedDataBackground: {
          lineStyle: { color: "#38bdf8" },
          areaStyle: { color: "#38bdf8", opacity: 0.2 },
        },
        fillerColor: "rgba(56, 189, 248, 0.15)",
        handleStyle: { color: "#38bdf8", borderColor: "#38bdf8" },
        moveHandleStyle: { color: "#38bdf8" },
        textStyle: { color: CHART_COLORS.label, fontSize: 10 },
        labelFormatter: (value: number) => `${Math.round(value)}m`,
        // Window state is applied via dispatchAction (single source of truth);
        // baking start/end into the option here fights the merge updates.
      },
    ];

    return {
      animation: false,
      backgroundColor: "transparent",
      title: titles,
      grid: grids,
      xAxis: xAxes as EChartsOption["xAxis"],
      yAxis: yAxes as EChartsOption["yAxis"],
      series,
      dataZoom,
      // Declares the native drag-select zoom feature; its cursor is activated
      // by dispatchAction(takeGlobalCursor) so no icon click is needed. The
      // toolbox itself is parked off-screen.
      toolbox: {
        top: -100,
        feature: {
          dataZoom: {
            xAxisIndex: allXAxisIndices,
            yAxisIndex: false,
            filterMode: "none",
            brushStyle: {
              color: "rgba(56, 189, 248, 0.15)",
              borderColor: "#38bdf8",
              borderWidth: 1,
            },
          },
        },
      },
      legend: {
        top: 0,
        right: 8,
        textStyle: { color: CHART_COLORS.label, fontSize: 11 },
        icon: "roundRect",
        itemWidth: 12,
        itemHeight: 3,
      },
      axisPointer: {
        type: "cross",
        link: [{ xAxisIndex: "all" }],
        lineStyle: { color: "#38bdf8", width: 1, type: "dashed" },
        crossStyle: { color: "#38bdf8", width: 1, type: "dashed" },
        label: { backgroundColor: "#1e232b", color: "#38bdf8", fontSize: 10, padding: [2, 5] },
      },
      tooltip: {
        trigger: "axis",
        backgroundColor: "#1b1f26",
        borderColor: "#262b33",
        textStyle: { color: "#e6e9ee", fontSize: 11 },
        // One distance header, then a table with one labeled row per metric
        // and one right-aligned column per lap — the default repeats the
        // distance for every panel and shows unlabeled numbers. Values are
        // matched to panels via series id, never index.
        formatter: (params: unknown) => {
          const list = (Array.isArray(params) ? params : [params]) as {
            seriesId?: string;
            axisValue: number | string;
            value: [number, number] | number;
            marker: string;
          }[];
          if (list.length === 0) return "";
          const dist = Number(list[0].axisValue);
          const multiLap = lapIds.length > 1;

          const cellValue = new Map<string, string>(); // series id -> formatted value
          const lapMarker = new Map<string, string>(); // lap id -> colored dot html
          for (const p of list) {
            const meta = p.seriesId ? seriesMeta.get(p.seriesId) : undefined;
            if (!meta) continue; // not one of ours (e.g. a stale series)
            const v = Array.isArray(p.value) ? p.value[1] : p.value;
            if (v == null || Number.isNaN(v)) continue;
            cellValue.set(p.seriesId!, formatValue(meta.panelKey, v));
            if (!lapMarker.has(meta.lapId)) lapMarker.set(meta.lapId, p.marker);
          }
          if (cellValue.size === 0) return "";

          const cols = lapIds.filter((id) => lapMarker.has(id));
          const header = multiLap
            ? `<tr><td></td>${cols
                .map(
                  (id) =>
                    `<td style="text-align:right;padding:0 0 3px 14px;color:#8b93a1">${lapMarker.get(id)}${(lapLabels[id] ?? `Lap ${id}`).split(" ")[0]}</td>`,
                )
                .join("")}</tr>`
            : "";
          const rows = panels.map((panel) => {
            const cells = cols.map((id) => cellValue.get(`${panel.key}|${id}`));
            if (cells.every((c) => c == null)) return "";
            const label = panel.title.replace(/ \(.*\)| %/, "");
            return `<tr>
              <td style="color:#8b93a1;padding:1px 0;line-height:1.6">${label}</td>
              ${cells
                .map(
                  (c) =>
                    `<td style="text-align:right;padding-left:14px;font-variant-numeric:tabular-nums">${c ?? '<span style="color:#8b93a1">–</span>'}</td>`,
                )
                .join("")}
            </tr>`;
          }).join("");
          return `<div style="font-weight:600;margin-bottom:4px">${Math.round(dist).toLocaleString()} m</div>
            <table style="border-collapse:collapse">${header}${rows}</table>`;
        },
      },
    };
  }, [data, lapLabels, lapColors, units, panels]);

  // Per-strip value readout at the playhead. Updated with a title-only
  // setOption so the series data is never rebuilt as the cursor moves.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const fmt = formatValueFor(units);
    const refEntry = refLapId != null ? data.laps[refLapId] : undefined;
    const titles = panels.map((panel, i) => {
      const base = { index: i * 2 + 1 };
      if (cursorDist == null) return { ...base, text: "" };
      // The reference lap has no delta of its own, so the delta strip reads
      // out the first lap that does — the one the chart actually draws.
      const entry =
        panel.key === "delta"
          ? Object.values(data.laps).find((l) => l.delta)
          : refEntry;
      if (!entry) return { ...base, text: "" };
      const dist = panel.key === "delta" ? entry.delta!.dist : entry.series.dist;
      const raw =
        panel.key === "delta"
          ? entry.delta!.delta_ms.map((v) => v / 1000)
          : panel.def
            ? channelValues(panel.def, entry.series)
            : entry.series[panel.key] ?? [];
      if (!raw || !dist || dist.length === 0) return { ...base, text: "" };
      // Nearest sample to the playhead; dist is monotonic so a scan from the
      // proportional guess would do, but these arrays are short enough.
      let lo = 0;
      let hi = dist.length - 1;
      while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (dist[mid] < cursorDist) lo = mid + 1;
        else hi = mid;
      }
      const i0 = lo > 0 && Math.abs(dist[lo - 1] - cursorDist) < Math.abs(dist[lo] - cursorDist)
        ? lo - 1
        : lo;
      const v = raw[i0];
      if (v == null || !Number.isFinite(v)) return { ...base, text: "" };
      const shown = panel.transform ? panel.transform(v, units) : v;
      return { ...base, text: fmt(panel.key, shown) };
    });
    const current = (chart.getOption().title ?? []) as Record<string, unknown>[];
    if (current.length === 0) return;
    const next = current.map((t, i) => {
      const hit = titles.find((x) => x.index === i);
      return hit ? { ...t, text: hit.text } : t;
    });
    chart.setOption({ title: next });
  }, [cursorDist, refLapId, data, panels, units]);

  return (
    <div className="flex flex-col">
      <div className="relative w-full select-none" onDoubleClick={() => onZoomChange?.(null)}>
        <EChart
          option={option}
          className="w-full"
          notMerge={false}
          replaceMerge={REPLACE_PANEL_PARTS}
          onInit={(chart) => {
            chartRef.current = chart;
            chart.getDom().style.height = `${panels.length * 110 + TOP_PAD + PANEL_GAP}px`;
            chart.resize();
            chart.on("updateAxisPointer", (e) => {
              const info = (e as { axesInfo?: { axisDim: string; value: number }[] }).axesInfo;
              const x = info?.find((a) => a.axisDim === "x");
              onCursorDist?.(x ? x.value : null);
            });
            // Fired by the native drag-select box and by the bottom slider.
            chart.on("dataZoom", (raw) => {
              const e = raw as {
                batch?: { startValue?: number; endValue?: number; start?: number; end?: number }[];
                startValue?: number;
                endValue?: number;
                start?: number;
                end?: number;
              };
              if (applyingZoom.current) return; // our own dispatch echoing back
              let startVal: number | undefined;
              let endVal: number | undefined;

              if (e.batch && e.batch[0]) {
                startVal = e.batch[0].startValue;
                endVal = e.batch[0].endValue;
              } else if (e.startValue != null && e.endValue != null) {
                startVal = e.startValue;
                endVal = e.endValue;
              }

              if (startVal != null && endVal != null) {
                onZoomChange?.([startVal, endVal]);
              } else {
                const startPct = e.batch?.[0]?.start ?? e.start ?? 0;
                const endPct = e.batch?.[0]?.end ?? e.end ?? 100;
                if (startPct <= 1 && endPct >= 99) {
                  onZoomChange?.(null);
                } else {
                  const minD = (startPct / 100) * maxDistRef.current;
                  const maxD = (endPct / 100) * maxDistRef.current;
                  onZoomChange?.([minD, maxD]);
                }
              }
            });
            chart.getZr().on("globalout", () => onCursorDist?.(null));
            activateDragZoom();
          }}
        />
      </div>
    </div>
  );
}
