// Track map built from lap positions. The reference lap is colored by input
// zone (throttle green / brake red / coast blue) with speed peaks & valleys;
// every other selected lap is overlaid as a solid line in its chart color —
// like GT7's own Data Logger, but with a synced cursor dot per lap showing
// the spatial gap at the hovered distance.
//
// Three things make it usable rather than merely present:
//
// * **True geometry.** The axis spans follow the plotting area's pixel aspect,
//   so a metre across is a metre down and a corner's shape on screen is its
//   shape on the track. Letting each axis scale to its own data stretches the
//   map by whatever the circuit's aspect ratio happens to be — 8 % at Lago
//   Maggiore Centre, nearly 3x at Deep Forest — which is a strange thing for a
//   map to do to a racing line. (Equal spans are not enough on their own: they
//   are only true on a square plot, and the maximized view is widescreen.)
// * **Corner navigation.** The corners are already known (detected, or
//   authored in the track bundle), so they are the natural unit to look at
//   one at a time. Picking one drives the SHARED zoom, so the charts follow
//   the map into the corner rather than the two disagreeing.
// * **A maximized view.** A 4.5 km circuit in a 360 px rail is a squiggle;
//   the same map at full screen is a track.

import type * as echarts from "echarts";
import type { EChartsOption, SeriesOption } from "echarts";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CHART_COLORS, EChart } from "@/components/EChart";
import { LargeDialog } from "@/components/ui/Dialog";
import { Tip } from "@/components/ui/Tooltip";
import {
  type CompareLapEntry,
  type Corner,
  kerbWheelCount,
  looseWheelCount,
  type TrackOutline,
} from "@/lib/types";

const ZONE_COLORS = [CHART_COLORS.brake, CHART_COLORS.coast, CHART_COLORS.throttle];

// Surface-contact halos under the reference line (packet-C recordings):
// kerb strikes in yellow, wheels on grass/gravel/dirt in orange.
const KERB_COLOR = "#eab308";
const LOOSE_COLOR = "#f97316";

// The surveyed road beneath everything (#51). Deliberately quiet: it is the
// backdrop the lap is read against, not a thing to read on its own — a fill
// bright enough to compete with the input-zone dots would bury them.
const ROAD_FILL = "#252b34";
const BORDER_COLOR = "#4b5563";
const WALL_COLOR = "#7f1d1d";
// Survey gaps (#44): boundary the compile knows about but nobody has driven.
// Dashed like the Survey view draws them, in the app's warn color, and dimmer
// than the borders — a hole in the backdrop, not a feature of the lap.
const GAP_COLOR = "#f59e0b";

// Numbered circles are readable up to about this many corners in view; beyond
// that they collapse to dots. The maximized view has the room for far more.
const MAX_NUMBERED_CORNERS = 30;
const MAX_NUMBERED_CORNERS_LARGE = 90;

// Track either side of a corner's own extent, so it arrives in context —
// the braking zone into it and the exit out of it — rather than cropped to
// the arc itself.
const CORNER_PAD_M = 40;

// Chart margin, subtracted when measuring the plotting area's pixel aspect.
const GRID_PAD = 8;

// Metres of track across the longer axis while the follow camera is on. Wide
// enough to hold a whole corner and its entry, tight enough that the car is
// visibly moving rather than crawling.
const FOLLOW_SPAN_M = 220;

function zoneOf(throttle: number, brake: number): number {
  if (brake >= 1) return 0;
  if (throttle >= 1) return 2;
  return 1;
}

/** The distance window a corner is shown in. Also the identity used to tell
 *  whether the current zoom IS this corner, so it must be deterministic. */
export function cornerRange(c: Corner): [number, number] {
  return [
    Math.max(0, c.entry_dist - CORNER_PAD_M),
    Math.max(c.exit_dist, c.entry_dist) + CORNER_PAD_M,
  ];
}

/** Which corner the current zoom is showing, if it is showing one. Derived
 *  rather than remembered: the charts can change the zoom too (drag, sector
 *  buttons, reset), and a remembered selection would go stale behind them. */
function selectedCorner(corners: Corner[], zoom: [number, number] | null): Corner | null {
  if (!zoom) return null;
  return (
    corners.find((c) => {
      const [lo, hi] = cornerRange(c);
      return Math.abs(lo - zoom[0]) < 1 && Math.abs(hi - zoom[1]) < 1;
    }) ?? null
  );
}

export interface MapLap {
  id: string;
  entry: CompareLapEntry;
  color: string; // chart series color for this lap
  label: string;
  isRef: boolean;
}

interface MapProps {
  laps: MapLap[];
  cursorDist: number | null;
  step: number;
  zoomRange?: [number, number] | null;
  // The circuit's surveyed road, when it has been surveyed. Null/empty draws
  // exactly what this map drew before it existed.
  outline?: TrackOutline | null;
  // Lets the map drive the zoom every panel shares, so picking a corner here
  // takes the charts there too.
  onZoomChange?: (range: [number, number] | null) => void;
  // Hero mode: the map runs full-bleed across the page at a fixed height
  // instead of squaring off inside a rail, and the throttle/brake/coast key
  // moves up into the panel header, so it is dropped from the row below.
  hero?: boolean;
  // Follow camera: frame a fixed window of track around the reference car at
  // the cursor instead of the whole circuit. Driven by playback, so at a
  // racing pace the map reads as a moving view of the corner being driven.
  follow?: boolean;
  /** Width of that window, in metres of track across the longer axis. */
  followSpanM?: number;
}

// The follow camera's window: a fixed span of metres centred on the reference
// car. Uses the same pixel-aspect rule as the full view, so a metre across
// stays a metre down and corner shapes survive the zoom.
function followWindow(
  ref: MapLap | undefined,
  cursorDist: number | null,
  step: number,
  aspect: number,
  spanM: number,
): { xMin: number; xMax: number; zMin: number; zMax: number } | null {
  if (!ref || cursorDist == null || step <= 0) return null;
  const s = ref.entry.series;
  if (s.dist.length === 0) return null;
  const i = Math.min(s.dist.length - 1, Math.max(0, Math.round(cursorDist / step)));
  const x = s.pos_x[i];
  const z = s.pos_z[i];
  if (x == null || z == null || !isFinite(x) || !isFinite(z)) return null;
  const spanX = spanM * Math.max(1, aspect);
  const spanZ = spanM * Math.max(1, 1 / aspect);
  return {
    xMin: x - spanX / 2,
    xMax: x + spanX / 2,
    zMin: z - spanZ / 2,
    zMax: z + spanZ / 2,
  };
}

export function RaceLineMap(props: MapProps) {
  const [maximized, setMaximized] = useState(false);
  return (
    <>
      {/* While the dialog is up the inline chart is behind an opaque overlay,
          so it is swapped for a placeholder of the same size: two live
          instances would both re-render the outline's thousands of segments,
          and only one of them would be visible. */}
      {maximized ? (
        <div className={props.hero ? "h-[380px] w-full" : "aspect-square w-full"} />
      ) : (
        <MapBody {...props} onMaximize={() => setMaximized(true)} />
      )}
      <LargeDialog
        open={maximized}
        title="Race line"
        onClose={() => setMaximized(false)}
      >
        <MapBody {...props} maximized />
      </LargeDialog>
    </>
  );
}

function MapBody({
  laps,
  cursorDist,
  step,
  zoomRange,
  outline,
  onZoomChange,
  onMaximize,
  hero = false,
  follow = false,
  followSpanM = FOLLOW_SPAN_M,
  maximized = false,
}: MapProps & { onMaximize?: () => void; maximized?: boolean }) {
  const chartRef = useRef<echarts.ECharts | null>(null);
  // Equal axis spans only keep the geometry honest if the PLOTTING AREA is
  // square, which the rail's aspect-square box is and the maximized dialog —
  // a widescreen rectangle — very much is not. So the spans follow the box:
  // the axis with more pixels gets proportionally more metres, and a metre
  // stays a metre either way. Measured rather than assumed, because the same
  // component renders in both.
  const boxRef = useRef<HTMLDivElement>(null);
  const [aspect, setAspect] = useState(1);
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const observer = new ResizeObserver(() => {
      const { width, height } = el.getBoundingClientRect();
      const plotW = width - 2 * GRID_PAD;
      const plotH = height - 2 * GRID_PAD;
      if (plotW <= 0 || plotH <= 0) return;
      // Quantized: a resize drag fires this continuously, and every distinct
      // value rebuilds the whole option.
      setAspect((was) => (Math.abs(plotW / plotH - was) > 0.005 ? plotW / plotH : was));
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  const ref = laps.find((lap) => lap.isRef);
  const hasOutline = (outline?.road.length ?? 0) > 0 || (outline?.edges.length ?? 0) > 0;
  const corners = useMemo(() => ref?.entry.corners ?? [], [ref]);
  const current = selectedCorner(corners, zoomRange ?? null);

  const zoomToCorner = useCallback(
    (c: Corner | null) => onZoomChange?.(c ? cornerRange(c) : null),
    [onZoomChange],
  );

  // Bound once on the chart, so it reads the corner list through a ref rather
  // than capturing whichever one existed at mount.
  const cornersRef = useRef(corners);
  cornersRef.current = corners;
  const zoomRef = useRef(zoomToCorner);
  zoomRef.current = zoomToCorner;

  // The framing of the whole view, kept OUT of the option memo: the follow
  // camera swaps the axes every animation frame and needs a value to restore
  // without rebuilding the option (and with it every outline segment).
  const baseAxis = useMemo(() => {
    // The window to show: the zoomed section of the reference lap, or
    // everything there is. Collected from every drawn layer so the surveyed
    // road cannot fall outside the frame.
    let minX = Infinity;
    let maxX = -Infinity;
    let minZ = Infinity;
    let maxZ = -Infinity;
    const see = (x: number, z: number) => {
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (z < minZ) minZ = z;
      if (z > maxZ) maxZ = z;
    };

    if (zoomRange && ref) {
      const s = ref.entry.series;
      for (let i = 0; i < s.dist.length; i++) {
        const d = s.dist[i];
        if (d >= zoomRange[0] && d <= zoomRange[1] && s.pos_x[i] != null && s.pos_z[i] != null) {
          see(s.pos_x[i], s.pos_z[i]);
        }
      }
    } else {
      for (const lap of laps) {
        const s = lap.entry.series;
        for (let i = 0; i < s.dist.length; i++) {
          if (s.pos_x[i] != null && s.pos_z[i] != null) see(s.pos_x[i], s.pos_z[i]);
        }
      }
      // EVERY vertex of every layer. Sampling one corner of each quad and one
      // end of each segment leaves whatever lies past it outside the explicit
      // axis limits, and the things most likely to be out there are the ones
      // worth seeing: a wall beyond the ordinary border, or a finish line
      // reaching 12 m either side of the road.
      for (const quad of outline?.road ?? []) {
        for (let i = 0; i + 1 < quad.length; i += 2) see(quad[i], quad[i + 1]);
      }
      for (const segments of [outline?.edges ?? [], outline?.walls ?? [], outline?.gaps ?? []]) {
        for (const seg of segments) {
          see(seg[0], seg[1]);
          see(seg[2], seg[3]);
        }
      }
      if (outline?.finish) {
        see(outline.finish[0], outline.finish[1]);
        see(outline.finish[2], outline.finish[3]);
      }
    }

    // A metre across must be a metre down, or the map lies about every
    // corner's shape. Equal spans would do that only on a square plot; the
    // spans instead follow the plotting area's pixel aspect, so the wider
    // dimension simply shows more track.
    let axis: { xMin: number; xMax: number; zMin: number; zMax: number } | null = null;
    if (isFinite(minX) && isFinite(maxX) && isFinite(minZ) && isFinite(maxZ)) {
      const pad = Math.max(Math.max(maxX - minX, maxZ - minZ) * 0.06, 8);
      const span = Math.max(maxX - minX, maxZ - minZ) + pad * 2;
      const spanX = span * Math.max(1, aspect);
      const spanZ = span * Math.max(1, 1 / aspect);
      const cx = (minX + maxX) / 2;
      const cz = (minZ + maxZ) / 2;
      axis = {
        xMin: cx - spanX / 2,
        xMax: cx + spanX / 2,
        zMin: cz - spanZ / 2,
        zMax: cz + spanZ / 2,
      };
    }

    return axis;
  }, [laps, outline, ref, zoomRange, aspect]);

  const option = useMemo<EChartsOption>(() => {
    const series: SeriesOption[] = [];

    // Surveyed road first, under every lap line: fill, then borders, then the
    // start/finish line. Segment endpoints are pre-computed server-side, so
    // renderItem only has to project them.
    if (outline) {
      if (outline.road.length > 0) {
        series.push({
          id: "outline-road",
          type: "custom",
          data: outline.road,
          renderItem: (_params, apiCustom) => ({
            type: "polygon",
            shape: {
              points: [0, 1, 2, 3].map((i) =>
                apiCustom.coord([
                  Number(apiCustom.value(i * 2)),
                  Number(apiCustom.value(i * 2 + 1)),
                ]),
              ),
            },
            style: { fill: ROAD_FILL },
          }),
          progressive: 0,
          silent: true,
          z: 0.5,
        });
      }
      // Gaps sit between the road fill and the borders: part of the backdrop,
      // never over a border that was actually surveyed.
      for (const [id, data, color, width, dash, opacity, z] of [
        ["outline-gaps", outline.gaps ?? [], GAP_COLOR, 1.4, [8, 6], 0.55, 0.7],
        ["outline-edges", outline.edges, BORDER_COLOR, 1.4, null, 0.85, 0.8],
        ["outline-walls", outline.walls, WALL_COLOR, 2, null, 0.85, 0.8],
      ] as const) {
        if (data.length === 0) continue;
        series.push({
          id,
          type: "custom",
          data: data as unknown as number[][],
          renderItem: (_params, apiCustom) => {
            const p1 = apiCustom.coord([Number(apiCustom.value(0)), Number(apiCustom.value(1))]);
            const p2 = apiCustom.coord([Number(apiCustom.value(2)), Number(apiCustom.value(3))]);
            return {
              type: "line",
              shape: { x1: p1[0], y1: p1[1], x2: p2[0], y2: p2[1] },
              style: {
                stroke: color,
                lineWidth: width,
                opacity,
                ...(dash ? { lineDash: dash as unknown as number[] } : {}),
              },
            };
          },
          progressive: 0,
          silent: true,
          z,
        });
      }
      if (outline.finish) {
        const [fx1, fz1, fx2, fz2] = outline.finish;
        series.push({
          id: "outline-finish",
          type: "line",
          data: [
            [fx1, fz1],
            [fx2, fz2],
          ],
          showSymbol: false,
          lineStyle: { color: "#e5e7eb", width: 2.5, opacity: 0.8 },
          silent: true,
          z: 0.9,
        });
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
      // Zoomed in there is room for the line to be a line rather than a dotted
      // trail, and the input zones read better for it.
      const dot = zoomRange ? (maximized ? 7 : 5) : maximized ? 4.5 : 3.5;

      // The reference lap as a CONTINUOUS line, one series per input zone
      // with the other zones nulled out. Samples arrive on a 5 m grid, which
      // reads as a solid line across a whole circuit and as scattered dots
      // the moment you zoom into a corner — which is exactly where the line
      // matters most. Each run is extended one sample backwards so
      // consecutive zones meet instead of leaving a gap at every transition.
      const inZoomAt = (i: number) =>
        !zoomRange || (s.dist[i] >= zoomRange[0] && s.dist[i] <= zoomRange[1]);
      const zoneAt = (i: number) => zoneOf(s.throttle[i], s.brake[i]);
      if (zoomRange) {
        series.push({
          id: "ref-line-dim",
          type: "line",
          data: s.dist.map((_, i) => [s.pos_x[i], s.pos_z[i]]),
          showSymbol: false,
          lineStyle: { color: CHART_COLORS.label, width: 1.2, opacity: 0.18 },
          silent: true,
          z: 1.8,
        });
      }
      for (let zone = 0; zone < 3; zone++) {
        series.push({
          id: `ref-zone-${zone}`,
          type: "line",
          data: s.dist.map((_, i) =>
            inZoomAt(i) && (zoneAt(i) === zone || (i > 0 && zoneAt(i - 1) === zone))
              ? [s.pos_x[i], s.pos_z[i]]
              : [null, null],
          ),
          showSymbol: false,
          connectNulls: false,
          lineStyle: { color: ZONE_COLORS[zone], width: zoomRange ? 3 : 2, opacity: 0.95 },
          silent: true,
          z: 2.8,
        });
      }

      const points = s.dist.map((d, i) => {
        const inZoom = zoomRange ? d >= zoomRange[0] && d <= zoomRange[1] : true;
        return {
          value: [s.pos_x[i], s.pos_z[i]],
          symbolSize: inZoom ? dot : dot * 0.55,
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
            symbolSize: dot * 2.3,
            itemStyle: { color: KERB_COLOR },
            silent: true,
            z: 2.5,
          },
          {
            id: "surface-loose",
            type: "scatter",
            data: loosePts,
            symbolSize: dot * 2.6,
            itemStyle: { color: LOOSE_COLOR },
            silent: true,
            z: 2.6,
          },
        );
      }

      const pv = ref.entry.peaks_valleys;
      const pvInZoom = (d: number) => !zoomRange || (d >= zoomRange[0] && d <= zoomRange[1]);

      series.push(
        { type: "scatter", data: points, symbolSize: dot, silent: true, z: 3 },
        {
          type: "scatter",
          data: pv.peaks.filter((p) => pvInZoom(p.dist)).map((p) => [p.x, p.z]),
          symbol: "triangle",
          symbolSize: maximized ? 12 : 9,
          itemStyle: { color: "#facc15" },
          silent: true,
          z: 5,
        },
        {
          type: "scatter",
          data: pv.valleys.filter((p) => pvInZoom(p.dist)).map((p) => [p.x, p.z]),
          symbol: "triangle",
          symbolRotate: 180,
          symbolSize: maximized ? 12 : 9,
          itemStyle: { color: "#c084fc" },
          silent: true,
          z: 5,
        },
      );

      // Corners (detected on the reference lap, or authored for the circuit).
      // Numbered circles while the view shows a readable amount; plain dots
      // otherwise. Clickable: they are the fastest way into a corner.
      const cornersInView = corners.filter(
        (c) => !zoomRange || (c.apex_dist >= zoomRange[0] && c.apex_dist <= zoomRange[1]),
      );
      const numbered =
        cornersInView.length > 0 &&
        cornersInView.length <= (maximized ? MAX_NUMBERED_CORNERS_LARGE : MAX_NUMBERED_CORNERS);
      if (cornersInView.length > 0) {
        const size = maximized ? 20 : 15;
        series.push({
          id: "corners",
          type: "scatter",
          data: cornersInView.map((c) => ({
            value: [c.apex_x, c.apex_z],
            name: String(c.n),
          })),
          symbolSize: numbered ? size : 5,
          itemStyle: numbered
            ? {
                color: "#14171c",
                borderColor: current ? CHART_COLORS.series[0] : CHART_COLORS.label,
                borderWidth: current ? 2 : 1,
              }
            : { color: CHART_COLORS.label, opacity: 0.85 },
          label: {
            show: numbered,
            position: "inside",
            formatter: "{b}",
            color: "#e5e7eb",
            fontSize: maximized ? 11 : 9,
            fontWeight: "bold",
          },
          cursor: "pointer",
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
        symbolSize: (lap.isRef ? 12 : 9) * (maximized ? 1.3 : 1),
        itemStyle: {
          color: lap.color,
          borderColor: "#fff",
          borderWidth: lap.isRef ? 3 : 1.5,
        },
        z: lap.isRef ? 11 : 10,
        silent: true,
      });
    }

    return {
      animation: false,
      grid: { left: 8, right: 8, top: 8, bottom: 8 },
      xAxis: {
        type: "value",
        show: false,
        ...(baseAxis ? { min: baseAxis.xMin, max: baseAxis.xMax } : { scale: true }),
      },
      yAxis: {
        type: "value",
        show: false,
        inverse: true,
        ...(baseAxis ? { min: baseAxis.zMin, max: baseAxis.zMax } : { scale: true }),
      },
      // Free pan and wheel-zoom, but only where it cannot fight the page:
      // in the rail the map sits inside a scrolling column, and a wheel that
      // sometimes scrolls and sometimes zooms is worse than one that always
      // scrolls. The window resets whenever the option is rebuilt (a new
      // selection, a new corner), which is the moment it should.
      ...(maximized
        ? {
            dataZoom: [
              { type: "inside", xAxisIndex: 0, filterMode: "none" },
              { type: "inside", yAxisIndex: 0, filterMode: "none" },
            ],
          }
        : {}),
      tooltip: { show: false },
      series,
    };
  }, [laps, zoomRange, outline, corners, current, maximized, ref, baseAxis]);

  // Whether the axes are currently displaced by the follow camera, so the
  // full view is restored exactly once when it switches off — pushing the
  // base axis on every frame would fight the dialog's own pan and zoom.
  const following = useRef(false);

  // Cursor updates merge into the existing chart by series id — no rebuild.
  // The follow camera rides along in the SAME setOption: this runs every
  // animation frame during playback, and rebuilding the option (thousands of
  // outline segments) at that rate would stall the page.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
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

    const patch: Record<string, unknown> = { series: updates };
    const window = follow ? followWindow(ref, cursorDist, step, aspect, followSpanM) : null;
    if (window) {
      patch.xAxis = { min: window.xMin, max: window.xMax };
      patch.yAxis = { min: window.zMin, max: window.zMax };
      following.current = true;
    } else if (following.current && baseAxis) {
      patch.xAxis = { min: baseAxis.xMin, max: baseAxis.xMax };
      patch.yAxis = { min: baseAxis.zMin, max: baseAxis.zMax };
      following.current = false;
    }
    chart.setOption(patch, { notMerge: false, lazyUpdate: true });
  }, [laps, cursorDist, step, follow, followSpanM, ref, aspect, baseAxis]);

  const others = laps.filter((lap) => !lap.isRef);
  const hasSurface = !!ref?.entry.series.surface?.some((v) => v > 0);

  return (
    <div className={maximized ? "flex h-full flex-col" : undefined}>
      <div ref={boxRef} className={maximized ? "relative min-h-0 flex-1" : "relative"}>
        <EChart
          option={option}
          className={
            maximized ? "h-full w-full" : hero ? "h-[380px] w-full" : "aspect-square w-full"
          }
          onInit={(chart) => {
            chartRef.current = chart;
            chart.on("click", (e) => {
              if (e.seriesId !== "corners") return;
              const corner = cornersRef.current.find((c) => String(c.n) === e.name);
              if (corner) zoomRef.current(corner);
            });
          }}
        />
        {onMaximize && (
          <Tip content="Open the map full screen">
            <button
              onClick={onMaximize}
              aria-label="Maximize the race line map"
              className="absolute right-2 top-2 rounded border border-edge bg-panel/80 px-1.5 py-0.5 text-xs text-ink-dim backdrop-blur transition-colors hover:border-edge-bright hover:text-ink"
            >
              ⤢
            </button>
          </Tip>
        )}
      </div>

      {onZoomChange && corners.length > 0 && (
        <CornerBar
          corners={corners}
          current={current}
          onPick={zoomToCorner}
          maximized={maximized}
        />
      )}

      {/* Below the map, not floating over it: the key grew past what fits on
          one overlaid row once the surveyed road joined it, and legend text
          wrapping across the track is worse than a couple of rows of space. */}
      <div className="flex flex-wrap gap-x-3 gap-y-1 px-3 pb-2 text-[10px] text-ink-dim [&>span]:whitespace-nowrap">
        {others.map((lap) => (
          <span key={lap.id} className="flex items-center gap-1.5">
            <i className="inline-block h-0.5 w-4" style={{ backgroundColor: lap.color }} />
            {lap.label}
          </span>
        ))}
        {/* In hero mode these three sit in the panel header instead. */}
        {!hero && (
          <>
            <span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-throttle" />throttle</span>
            <span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-brake" />brake</span>
            <span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-coast" />coast</span>
          </>
        )}
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
        {corners.length > 0 && (
          <span>
            <i className="mr-1 inline-block h-2.5 w-2.5 rounded-full border border-ink-dim text-center align-middle text-[7px] leading-[9px]">
              1
            </i>
            corner — click to zoom
          </span>
        )}
        {hasOutline && (
          <span title={`Surveyed over ${outline!.runs} run${outline!.runs === 1 ? "" : "s"}`}>
            <i
              className="mr-1 inline-block h-2 w-2 rounded-sm"
              style={{ backgroundColor: ROAD_FILL, outline: `1px solid ${BORDER_COLOR}` }}
            />
            surveyed road
          </span>
        )}
        {(outline?.walls.length ?? 0) > 0 && (
          <span>
            <i
              className="mr-1 inline-block h-2 w-2 rounded-sm"
              style={{ backgroundColor: WALL_COLOR }}
            />
            wall
          </span>
        )}
        {(outline?.gaps?.length ?? 0) > 0 && (
          <span title="Boundary the survey has not driven yet">
            <i
              className="mr-1 inline-block w-4 border-t-2 border-dashed align-middle"
              style={{ borderColor: GAP_COLOR }}
            />
            unsurveyed gap
          </span>
        )}
        {maximized && <span className="ml-auto">scroll to zoom · drag to pan</span>}
      </div>
    </div>
  );
}

/** Corner picker. Every corner as a chip, the selected one called out with
 *  what it is — because "corner 7" alone is not something anyone remembers,
 *  and the direction plus minimum speed is how you recognise it. */
function CornerBar({
  corners,
  current,
  onPick,
  maximized,
}: {
  corners: Corner[];
  current: Corner | null;
  onPick: (c: Corner | null) => void;
  maximized: boolean;
}) {
  const index = current ? corners.indexOf(current) : -1;
  const step = (by: number) => {
    if (corners.length === 0) return;
    // Wraps, because the last corner's exit is the first one's approach.
    const next = index < 0 ? (by > 0 ? 0 : corners.length - 1) : (index + by + corners.length) % corners.length;
    onPick(corners[next]);
  };

  return (
    <div className="flex items-center gap-1.5 border-t border-edge px-3 py-1.5 text-[11px]">
      <button
        onClick={() => step(-1)}
        aria-label="Previous corner"
        className="shrink-0 rounded border border-edge px-1.5 text-ink-dim transition-colors hover:border-edge-bright hover:text-ink"
      >
        ‹
      </button>
      <div className="flex min-w-0 flex-1 gap-1 overflow-x-auto">
        <button
          onClick={() => onPick(null)}
          className={`shrink-0 rounded px-1.5 font-tabular transition-colors ${
            current ? "text-ink-dim hover:text-ink" : "bg-accent/15 text-accent"
          }`}
        >
          lap
        </button>
        {corners.map((c) => (
          <button
            key={c.n}
            onClick={() => onPick(c)}
            title={c.name || `Corner ${c.n}`}
            className={`shrink-0 rounded px-1.5 font-tabular transition-colors ${
              current?.n === c.n
                ? "bg-accent/15 text-accent"
                : "text-ink-dim hover:bg-panel-2 hover:text-ink"
            }`}
          >
            {c.n}
          </button>
        ))}
      </div>
      <button
        onClick={() => step(1)}
        aria-label="Next corner"
        className="shrink-0 rounded border border-edge px-1.5 text-ink-dim transition-colors hover:border-edge-bright hover:text-ink"
      >
        ›
      </button>
      {current && (
        <span className="shrink-0 truncate pl-1 text-ink-dim">
          {current.name || `T${current.n}`}
          {current.direction && ` · ${current.direction === "L" ? "left" : "right"}`}
          {maximized && ` · ${current.min_speed.toFixed(0)} km/h · ${current.angle_deg.toFixed(0)}°`}
        </span>
      )}
    </div>
  );
}
