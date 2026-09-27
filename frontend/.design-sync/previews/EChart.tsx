// EChart is the one charting primitive in the app: every analysis chart is an
// ECharts `option` handed to it. So these cells are the option shapes the repo
// actually builds — `baseGrid()` + `baseAxis()` for a distance-axis trace,
// `CHART_COLORS` for everything the canvas cannot read from CSS — over real
// Tsukuba laps out of ../fixtures/analysis.
//
// EChart renders into a bare <div> (default `h-48 w-full`), so it draws nothing
// at all unless something above it has a laid-out height. That is why the floor
// card showed it blank; every cell here gives it a box.

import { CHART_COLORS, EChart, baseAxis, baseGrid } from "gt7-datalogger-frontend";
import { COMPARE, REF_LAP, SELECTED } from "../fixtures/analysis";
import { ChartBox, Panel, Surface, lapColors } from "../preview-shell";

const COLORS = lapColors([...SELECTED], REF_LAP);

// 1063 is the reference (lap 2, 0:58.963); 1065 is lap 4 and 1068 lap 7.
const LABELS: Record<string, string> = { "1063": "Lap 2 (ref)", "1065": "Lap 4", "1068": "Lap 7" };
const ORDER = ["1063", "1065", "1068"];

const ref = COMPARE.laps[String(REF_LAP)];
// The reference lap's own distance is the axis every compared lap is resampled
// onto, so it is also where the plot should end — ECharts' own "nice" maximum
// would leave a fifth of the panel empty past the finish line.
const LAP_M = Math.ceil(ref.series.dist[ref.series.dist.length - 1]);

/** dist[] zipped with one channel, as every distance-axis chart in the app does. */
function against(id: string, channel: string): [number, number][] {
  const lap = COMPARE.laps[id];
  const y = lap.series[channel] ?? [];
  return lap.series.dist.map((d, i) => [d, y[i]] as [number, number]);
}

const speedOption = {
  animation: false,
  grid: baseGrid(),
  legend: {
    top: 2,
    right: 8,
    itemWidth: 14,
    itemHeight: 2,
    textStyle: { color: CHART_COLORS.label, fontSize: 10 },
    data: ORDER.map((id) => LABELS[id]),
  },
  xAxis: { ...baseAxis("m"), min: 0, max: LAP_M, nameTextStyle: { color: CHART_COLORS.label } },
  yAxis: { ...baseAxis("km/h"), nameGap: 12, nameTextStyle: { color: CHART_COLORS.label, align: "left" } },
  series: ORDER.map((id) => ({
    id: `speed-${id}`,
    name: LABELS[id],
    type: "line",
    data: against(id, "speed"),
    showSymbol: false,
    lineStyle: { color: COLORS[id], width: id === String(REF_LAP) ? 1.8 : 1.2 },
    itemStyle: { color: COLORS[id] },
    z: id === String(REF_LAP) ? 3 : 2,
  })),
};

// The time-diff graph: how far ahead or behind the reference each lap is at
// every metre. The zero line is the reference itself, so it gets a markLine.
const deltaOption = {
  animation: false,
  grid: baseGrid(),
  legend: {
    top: 2,
    right: 8,
    itemWidth: 14,
    itemHeight: 2,
    textStyle: { color: CHART_COLORS.label, fontSize: 10 },
    data: ["Lap 4", "Lap 7"],
  },
  xAxis: { ...baseAxis("m"), min: 0, max: LAP_M, nameTextStyle: { color: CHART_COLORS.label } },
  yAxis: { ...baseAxis("Δ s"), nameGap: 12, nameTextStyle: { color: CHART_COLORS.label, align: "left" } },
  series: ["1065", "1068"].map((id, i) => ({
    id: `delta-${id}`,
    name: LABELS[id],
    type: "line",
    data: (COMPARE.laps[id].delta?.dist ?? []).map((d, j) => [
      d,
      (COMPARE.laps[id].delta?.delta_ms[j] ?? 0) / 1000,
    ]),
    showSymbol: false,
    lineStyle: { color: COLORS[id], width: 1.4 },
    itemStyle: { color: COLORS[id] },
    areaStyle: { color: COLORS[id], opacity: 0.1 },
    ...(i === 0
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
  })),
};

// The other chart the app draws through EChart: a scatter race line, one dot
// per sample, coloured by what the driver was doing there — the same
// throttle / brake / coast split RaceLineMap uses.
function zoneColor(i: number): string {
  if (ref.series.brake[i] >= 1) return CHART_COLORS.brake;
  if (ref.series.throttle[i] >= 1) return CHART_COLORS.throttle;
  return CHART_COLORS.coast;
}

const lineOption = {
  animation: false,
  grid: { left: 8, right: 8, top: 8, bottom: 8 },
  xAxis: { type: "value", show: false, scale: true },
  // GT7's own view renders z inverted, so every map in the app does too.
  yAxis: { type: "value", show: false, scale: true, inverse: true },
  series: [
    {
      id: "line",
      type: "scatter",
      data: ref.series.pos_x.map((x, i) => ({
        value: [x, ref.series.pos_z[i]],
        itemStyle: { color: zoneColor(i) },
      })),
      symbolSize: 5,
      silent: true,
      z: 2,
    },
    {
      id: "peaks",
      type: "scatter",
      data: ref.peaks_valleys.peaks.map((p) => [p.x, p.z]),
      symbolSize: 10,
      itemStyle: { color: "#facc15" },
      silent: true,
      z: 3,
    },
    {
      id: "valleys",
      type: "scatter",
      data: ref.peaks_valleys.valleys.map((p) => [p.x, p.z]),
      symbolSize: 10,
      itemStyle: { color: "#c084fc" },
      silent: true,
      z: 3,
    },
  ],
};

// Gear is a step trace, not a curve — the one place the app asks for
// `step: "middle"` instead of a smooth line.
const gearOption = {
  animation: false,
  grid: { ...baseGrid(), top: 16, bottom: 26 },
  xAxis: { ...baseAxis(), min: 0, max: LAP_M },
  yAxis: { ...baseAxis(), min: 1, max: 6, interval: 1, splitNumber: 5 },
  series: ORDER.map((id) => ({
    id: `gear-${id}`,
    name: LABELS[id],
    type: "line",
    step: "middle",
    data: against(id, "gear"),
    showSymbol: false,
    lineStyle: { color: COLORS[id], width: 1.3 },
    itemStyle: { color: COLORS[id] },
  })),
};

export function SpeedTrace() {
  return (
    <Surface>
      <Panel title="Speed vs distance">
        <ChartBox w={640} h={260}>
          <EChart option={speedOption} className="h-full w-full" />
        </ChartBox>
      </Panel>
    </Surface>
  );
}

export function TimeDelta() {
  return (
    <Surface>
      <Panel title="Time delta to reference">
        <ChartBox w={640} h={240}>
          <EChart option={deltaOption} className="h-full w-full" />
        </ChartBox>
      </Panel>
    </Surface>
  );
}

export function RaceLine() {
  return (
    <Surface>
      <Panel title="Race line · throttle / brake / coast">
        <ChartBox w={420} h={420}>
          <EChart option={lineOption} className="h-full w-full" />
        </ChartBox>
      </Panel>
    </Surface>
  );
}

export function GearTrace() {
  // The short panel height the stacked analysis charts stack a channel into.
  return (
    <Surface>
      <Panel title="Gear">
        <ChartBox w={640} h={150}>
          <EChart option={gearOption} className="h-full w-full" />
        </ChartBox>
      </Panel>
    </Surface>
  );
}
