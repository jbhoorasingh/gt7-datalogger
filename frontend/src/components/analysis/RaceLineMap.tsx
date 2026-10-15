// Track map built from lap positions. The reference lap is a line coloured
// along its length by a lens (lib/mapLens): what the driver was doing
// (throttle / brake / coast), speed, where the compared lap gains or loses
// time, the hottest tire, or which axle's tires run hotter. Every other selected lap is overlaid as a thin line in its
// chart colour — like GT7's own Data Logger, but with a synced cursor dot per
// lap showing the spatial gap at the hovered distance.
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
// * **Zooming into a stretch.** The corners are already known (detected, or
//   authored in the track bundle), so they are the natural unit to look at
//   one at a time: click a number. Any other stretch is a drag along the
//   line, or ⌘/Ctrl + wheel about the pointer. Either way the view drives
//   the SHARED zoom, so the charts and the lap strip follow the map into the
//   corner rather than the three disagreeing. The window animates there, a
//   locator inset shows where on the lap it is, and Esc or a double-click
//   comes back out.
// * **A maximized view.** A 4.5 km circuit in a 360 px rail is a squiggle;
//   the same map at full screen is a track.
//
// Everything besides the reference line is a layer with a switch in the
// Layers menu (lib/mapLayers): the surveyed road, the corner numbers, the
// other laps with their apex speeds and brake points (#110), and the
// markers — speed peaks, kerb and off-track contact, chassis events (#103)
// and driver-aid rings (#104), each in the lap's own colour so several laps
// can be read at once.
//
// The cursor dots are distance-locked by default, like every other panel.
// Time sync (#75) moves the non-reference dots to where each lap was at the
// reference's elapsed time instead, which turns a time gap into a visible
// gap on track while a lap plays back.

import type * as echarts from "echarts";
import type { EChartsOption, SeriesOption } from "echarts";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CHART_COLORS, EChart } from "@/components/EChart";
import { LargeDialog } from "@/components/ui/Dialog";
import { cornerRange, selectedCorner, stepCorner } from "@/lib/cornerZoom";
import { speedUnit, speedValue, type Units } from "@/lib/format";
import { lineDragRange, wheelRange } from "@/lib/lapStrip";
import {
  aidPoints,
  brakeDeltaText,
  type BrakeMarker,
  DEFAULT_MAP_LAYERS,
  EVENT_LABELS,
  eventMarkers,
  type MapLayers,
  severityText,
  wheelsText,
} from "@/lib/mapLayers";
import { INPUT_COLORS, type LensSeries, zoneOf } from "@/lib/mapLens";
import { borderPolylines } from "@/lib/outline";
import { positionAtDist, positionAtTime, timeAtDist } from "@/lib/playback";
import {
  AIDS_ASM,
  AIDS_TCS,
  type CompareLapEntry,
  type Corner,
  type EventType,
  kerbWheelCount,
  looseWheelCount,
  type TrackOutline,
} from "@/lib/types";

// Surface-contact halos under the reference line (packet-C recordings):
// kerb strikes in yellow, wheels on grass/gravel/dirt in orange.
const KERB_COLOR = "#eab308";
const LOOSE_COLOR = "#f97316";
const PEAK_COLOR = "#facc15";
const VALLEY_COLOR = "#c084fc";

// The surveyed road beneath everything (#51). Deliberately quiet: it is the
// backdrop the lap is read against, not a thing to read on its own — a fill
// bright enough to compete with the line would bury it.
const ROAD_FILL = "#252b34";
const BORDER_COLOR = "#4b5563";
const WALL_COLOR = "#7f1d1d";
// Survey gaps (#44): boundary the compile knows about but nobody has driven.
// Dashed like the Survey view draws them, in the app's warn color, and dimmer
// than the borders — a hole in the backdrop, not a feature of the lap.
const GAP_COLOR = "#f59e0b";

// Canvas cannot read CSS custom properties: these mirror index.css.
const INK = "#e6e9ee";
const INK_SOFT = "#cdd4dd";
const INK_DIM = "#8b93a1";
const INK_FAINT = "#6b7482";
const INK_GHOST = "#4d5560";
const ACCENT = "#3ec9ff";
const ACCENT_300 = "#9fdfff";
const PANEL_2 = "#1b1f26";
const HAIRLINE = "#22262e";
const CURRENT_CORNER_BG = "rgba(62, 201, 255, 0.18)";
const BADGE_BG = "rgba(11, 13, 16, 0.85)";

// One glyph per event type, filled with the lap's colour. Shapes rather than
// colours carry the type because colour is already spoken for: it says which
// lap. Triangles are taken by the speed peaks and valleys.
// Arms a third of the glyph wide: any thinner and the dark outline that lifts
// a marker off the line eats the cross at map sizes.
const PLUS_PATH = "path://M3.3,0H6.7V3.3H10V6.7H6.7V10H3.3V6.7H0V3.3H3.3Z";
const EVENT_GLYPHS: Record<EventType, { symbol: string; rotate: number; legend: string }> = {
  lockup: { symbol: "diamond", rotate: 0, legend: "◆" },
  wheelspin: { symbol: "circle", rotate: 0, legend: "●" },
  bottoming: { symbol: PLUS_PATH, rotate: 0, legend: "✚" },
  kerb: { symbol: PLUS_PATH, rotate: 45, legend: "✖" },
};
const EVENT_ORDER: EventType[] = ["lockup", "wheelspin", "bottoming", "kerb"];

// Aid activity is a hollow ring around the line, so the colour of the sample
// underneath stays readable through it.
const AID_LAYERS = [
  { key: "tcs", bit: AIDS_TCS, symbol: "circle", label: "TCS active", legend: "○" },
  { key: "asm", bit: AIDS_ASM, symbol: "rect", label: "ASM active", legend: "□" },
] as const;

// Track either side of an event when a marker is clicked: enough to see the
// approach that caused it.
const EVENT_PAD_M = 60;

const NO_MARKS: BrakeMarker[] = [];

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

// Corner numbers are readable up to about this many corners in view; beyond
// that they collapse to dots. The maximized view has the room for far more.
const MAX_NUMBERED_CORNERS = 30;
const MAX_NUMBERED_CORNERS_LARGE = 90;

// A corner's number sits this far outside the apex, off the line; the apex
// speed badge the same distance inside it. Further out when zoomed, where
// the line is thicker and there is room.
const LABEL_OFFSET_PX = 17;
const LABEL_OFFSET_ZOOMED_PX = 26;
const BADGE_INSET_PX = 58;
// Samples either side of the apex that say which way is "outside".
const OUTWARD_SPAN = 8;

// How far before a corner's window a brake point still belongs in the frame
// with it: as far back as a braking zone may begin (BRAKE_SEARCH_M in
// processing/corner_metrics.py) and the window's own lead.
const BRAKE_FRAME_M = 250 + 30;

// Chart margin, subtracted when measuring the plotting area's pixel aspect.
const GRID_PAD = 8;

// Metres of track across the longer axis while the follow camera is on. Wide
// enough to hold a whole corner and its entry, tight enough that the car is
// visibly moving rather than crawling.
const FOLLOW_SPAN_M = 220;

// A pointer this close to the line takes hold of it; a little further still
// moves the shared cursor along it.
const LINE_GRAB_PX = 24;
const LINE_HOVER_PX = 48;
// The window animates into a corner over this long, easing out.
const ZOOM_ANIM_MS = 280;

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
  zoomRange?: [number, number] | null;
  // The circuit's surveyed road, when it has been surveyed. Null/empty draws
  // exactly what this map drew before it existed.
  outline?: TrackOutline | null;
  // Lets the map drive the zoom every panel shares, so picking a corner here
  // takes the charts there too.
  onZoomChange?: (range: [number, number] | null) => void;
  // Hovering the line moves the shared cursor along it.
  onCursorDist?: (dist: number | null) => void;
  // Hero mode: the map runs full-bleed across the page at a fixed height
  // instead of squaring off inside a rail; the key lives in the panel header
  // and the lap strip under the map, so neither is drawn here.
  hero?: boolean;
  // Follow camera: frame a fixed window of track around the reference car at
  // the cursor instead of the whole circuit. Driven by playback, so at a
  // racing pace the map reads as a moving view of the corner being driven.
  follow?: boolean;
  /** Width of that window, in metres of track across the longer axis. */
  followSpanM?: number;
  // "position" (default): every dot at the cursor's distance. "time": the
  // reference dot at the cursor, the others where their lap was at the
  // reference's lap time there.
  sync?: "position" | "time";
  // What to draw besides the reference line. Omitted draws the defaults.
  layers?: MapLayers;
  // Where each lap began braking for the corner being looked at (#110).
  // They change as the pointer moves down the corner report, so they are
  // merged into the chart like the cursor dots, never rebuilt with it.
  brakeMarks?: BrakeMarker[];
  // The reference line's colouring. Omitted colours by input zone.
  lens?: LensSeries | null;
  // The reference's corners, with `brake_on` filled in so the zoom windows
  // here agree with the strip's and the report card's. Defaults to the
  // reference entry's own corners.
  corners?: Corner[];
  // The lap the apex badges and the corner callout set against the
  // reference: the report card's focus. Null compares nothing.
  focusId?: string | null;
  units?: Units;
  // Whether a change of window animates. Off while a strip drag drives the
  // window live, frame by frame.
  animate?: boolean;
  // The full-screen dialog, controlled by the header's ⤢ when the parent
  // owns it; left out, the map keeps its own state.
  maximized?: boolean;
  onMaximizedChange?: (open: boolean) => void;
}

interface Axes {
  xMin: number;
  xMax: number;
  zMin: number;
  zMax: number;
}

// The follow camera's window: a fixed span of metres centred on the reference
// car. Uses the same pixel-aspect rule as the full view, so a metre across
// stays a metre down and corner shapes survive the zoom. Centred on the
// interpolated position, exactly where the reference dot is drawn — the
// nearest 5 m step would hop the whole view along every few frames.
function followWindow(
  ref: MapLap | undefined,
  cursorDist: number | null,
  aspect: number,
  spanM: number,
): Axes | null {
  if (!ref || cursorDist == null) return null;
  const at = positionAtDist(ref.entry.series, cursorDist);
  if (!at) return null;
  const [x, z] = at;
  const spanX = spanM * Math.max(1, aspect);
  const spanZ = spanM * Math.max(1, 1 / aspect);
  return {
    xMin: x - spanX / 2,
    xMax: x + spanX / 2,
    zMin: z - spanZ / 2,
    zMax: z + spanZ / 2,
  };
}

function sameAxes(a: Axes | null, b: Axes | null): boolean {
  if (!a || !b) return a === b;
  return a.xMin === b.xMin && a.xMax === b.xMax && a.zMin === b.zMin && a.zMax === b.zMax;
}

/** Index of the reference sample nearest a distance along the lap. */
function indexAtDist(dist: number[], d: number): number {
  let lo = 0;
  let hi = dist.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (dist[mid] < d) lo = mid + 1;
    else hi = mid;
  }
  if (lo > 0 && Math.abs(dist[lo - 1] - d) < Math.abs(dist[lo] - d)) return lo - 1;
  return lo;
}

/** Which way is "outside" a corner: from the midpoint of the line a few
 *  samples either side of the apex, out through the apex. The label goes
 *  that way, the apex badge the other. Unit vector in map pixels — the map
 *  draws z downwards, so no flip is needed. */
function outwardAt(series: CompareLapEntry["series"], apexDist: number): [number, number] {
  const { dist, pos_x: xs, pos_z: zs } = series;
  if (!dist || !xs || !zs || dist.length === 0) return [0, -1];
  const i = indexAtDist(dist, apexDist);
  const a = Math.max(0, i - OUTWARD_SPAN);
  const b = Math.min(dist.length - 1, i + OUTWARD_SPAN);
  const mx = (xs[a] + xs[b]) / 2;
  const mz = (zs[a] + zs[b]) / 2;
  const ux = xs[i] - mx;
  const uz = zs[i] - mz;
  const len = Math.hypot(ux, uz);
  if (!(len > 0.5)) return [0, -1];
  return [ux / len, uz / len];
}

export interface LegendGlyph {
  glyph: string;
  label: string;
  color?: string;
  title?: string;
}

/**
 * The key for what the layers are drawing in the current window, for the
 * panel header. Lists what is actually on the map, not what is switched on:
 * a layer with nothing to draw adds no entry.
 */
export function layerKey(
  laps: MapLap[],
  layers: MapLayers,
  zoomRange: [number, number] | null | undefined,
  brakeMarks: BrakeMarker[],
): LegendGlyph[] {
  const ref = laps.find((lap) => lap.isRef);
  // The key lists what is drawn, and a hidden lap's markers are not.
  const drawn = layers.others ? laps : laps.filter((lap) => lap.isRef);
  const out: LegendGlyph[] = [];
  const inZoom = (d: number) => !zoomRange || (d >= zoomRange[0] && d <= zoomRange[1]);
  if (layers.peaks && ref) {
    const pv = ref.entry.peaks_valleys;
    if (pv.peaks.some((p) => inZoom(p.dist))) out.push({ glyph: "▲", label: "peak", color: PEAK_COLOR });
    if (pv.valleys.some((p) => inZoom(p.dist))) {
      out.push({ glyph: "▼", label: "valley", color: VALLEY_COLOR });
    }
  }
  if (layers.kerbs && ref?.entry.series.surface?.some((v) => v > 0)) {
    out.push({ glyph: "●", label: "kerb", color: KERB_COLOR });
    out.push({ glyph: "●", label: "off-track", color: LOOSE_COLOR });
  }
  if (layers.events) {
    const types = new Set<EventType>();
    for (const lap of drawn) {
      for (const { event } of eventMarkers(lap.entry, zoomRange)) types.add(event.type);
    }
    for (const type of EVENT_ORDER) {
      if (types.has(type)) {
        out.push({
          glyph: EVENT_GLYPHS[type].legend,
          label: EVENT_LABELS[type].toLowerCase(),
          title: "In the lap's colour · click a marker to zoom every panel to it",
        });
      }
    }
  }
  for (const aid of AID_LAYERS) {
    if (!layers[aid.key]) continue;
    if (drawn.some((lap) => aidPoints(lap.entry.series, aid.bit, zoomRange).length > 0)) {
      out.push({
        glyph: aid.legend,
        label: aid.label,
        title: "A ring on every sample the aid was intervening, in the lap's colour",
      });
    }
  }
  if (layers.brakes && brakeMarks.length > 0) {
    out.push({
      glyph: "pin",
      label: "brake point",
      title: "Where each lap's brake went on for this corner, in the lap's colour",
    });
  }
  return out;
}

export function RaceLineMap(props: MapProps) {
  const [ownMaximized, setOwnMaximized] = useState(false);
  const maximized = props.maximized ?? ownMaximized;
  const setMaximized = props.onMaximizedChange ?? setOwnMaximized;
  return (
    <>
      {/* While the dialog is up the inline chart is behind an opaque overlay,
          so it is swapped for a placeholder of the same size: two live
          instances would both re-render the outline's thousands of segments,
          and only one of them would be visible. */}
      {maximized ? (
        <div className={props.hero ? "h-[440px] w-full" : "aspect-square w-full"} />
      ) : (
        <MapBody {...props} onMaximize={props.hero ? undefined : () => setMaximized(true)} />
      )}
      <LargeDialog open={maximized} title="Race line" onClose={() => setMaximized(false)}>
        <MapBody {...props} maximized />
      </LargeDialog>
    </>
  );
}

function MapBody({
  laps,
  cursorDist,
  zoomRange,
  outline,
  onZoomChange,
  onCursorDist,
  onMaximize,
  hero = false,
  follow = false,
  followSpanM = FOLLOW_SPAN_M,
  sync = "position",
  layers = DEFAULT_MAP_LAYERS,
  brakeMarks = NO_MARKS,
  lens = null,
  corners: cornersProp,
  focusId = null,
  units = "metric",
  animate = true,
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
    const measure = () => {
      const { width, height } = el.getBoundingClientRect();
      const plotW = width - 2 * GRID_PAD;
      const plotH = height - 2 * GRID_PAD;
      if (plotW <= 0 || plotH <= 0) return;
      // Quantized: a resize drag fires this continuously, and every distinct
      // value rebuilds the whole option.
      setAspect((was) => (Math.abs(plotW / plotH - was) > 0.005 ? plotW / plotH : was));
    };
    // Measured once right away: the observer's first notification waits
    // for a rendering step, and a tab that is not being painted (hidden,
    // or a preview pane) can go a while without one — during which the
    // first frame would be drawn at the wrong aspect.
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  const ref = laps.find((lap) => lap.isRef);
  const focus = focusId != null ? laps.find((lap) => lap.id === focusId && !lap.isRef) : undefined;
  const hasOutline = (outline?.road.length ?? 0) > 0 || (outline?.edges.length ?? 0) > 0;
  const corners = useMemo(
    () => cornersProp ?? ref?.entry.corners ?? [],
    [cornersProp, ref],
  );
  const current = selectedCorner(corners, zoomRange ?? null);
  const zoomed = !!zoomRange;

  // The whole lap's length, for the wheel zoom's bounds.
  const length = ref ? (ref.entry.series.dist[ref.entry.series.dist.length - 1] ?? 0) : 0;

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
  const rangeRef = useRef(onZoomChange);
  rangeRef.current = onZoomChange;

  // Brake points that belong to the corner in view are part of what the view
  // has to show: a braking zone begins well before the corner's own window,
  // and a pin outside the frame marks nothing. Held as a string so that a
  // pointer moving over OTHER corners' rows, whose pins are elsewhere,
  // changes nothing here and rebuilds nothing below.
  const shownMarks = layers.brakes ? brakeMarks : NO_MARKS;
  const framedMarks = zoomRange
    ? shownMarks
        .filter((m) => m.dist >= zoomRange[0] - BRAKE_FRAME_M && m.dist <= zoomRange[1])
        .map((m) => `${m.x.toFixed(1)},${m.z.toFixed(1)}`)
        .join(";")
    : "";

  // The framing of the whole view, kept OUT of the option memo: the camera
  // effect animates between windows and the follow camera swaps the axes
  // every animation frame, neither of which may rebuild the option (and with
  // it every outline segment).
  const baseAxis = useMemo<Axes | null>(() => {
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
      for (const point of framedMarks ? framedMarks.split(";") : []) {
        const [x, z] = point.split(",").map(Number);
        see(x, z);
      }
    } else {
      for (const lap of laps) {
        // A hidden lap is not in the frame: with "Other laps" off, a lap
        // that could not be lined up and sits off on its own axis must not
        // keep the reference line small.
        if (!lap.isRef && !layers.others) continue;
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
      if (layers.road) {
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
    }

    // A metre across must be a metre down, or the map lies about every
    // corner's shape. Equal spans would do that only on a square plot; the
    // spans instead follow the plotting area's pixel aspect, so the wider
    // dimension simply shows more track.
    if (!(isFinite(minX) && isFinite(maxX) && isFinite(minZ) && isFinite(maxZ))) return null;
    const pad = Math.max(Math.max(maxX - minX, maxZ - minZ) * (zoomRange ? 0.16 : 0.06), 8);
    // A zoomed window is never smaller than a corner: a stretch of straight
    // framed to its own 2 m of width would be a line across a blank map.
    const span = Math.max(maxX - minX, maxZ - minZ, zoomRange ? 60 : 0) + pad * 2;
    const spanX = span * Math.max(1, aspect);
    const spanZ = span * Math.max(1, 1 / aspect);
    const cx = (minX + maxX) / 2;
    const cz = (minZ + maxZ) / 2;
    return {
      xMin: cx - spanX / 2,
      xMax: cx + spanX / 2,
      zMin: cz - spanZ / 2,
      zMax: cz + spanZ / 2,
    };
  }, [laps, outline, ref, zoomRange, aspect, framedMarks, layers.road, layers.others]);

  // The borders as simplified polylines — a few hundred points instead of
  // thousands of segments, and cached per outline so a zoom costs nothing.
  const borders = useMemo(
    () => (outline && outline.edges.length > 0 ? borderPolylines(outline.edges) : null),
    [outline],
  );

  // The reference line's class per sample under the active lens — input
  // zones unless the parent supplied another colouring.
  const colouring = useMemo<LensSeries | null>(() => {
    if (!ref) return null;
    if (lens) return lens;
    const s = ref.entry.series;
    const throttle = s.throttle ?? [];
    const brake = s.brake ?? [];
    return {
      keys: s.dist.map((_, i) => zoneOf(throttle[i] ?? 0, brake[i] ?? 0)),
      palette: INPUT_COLORS,
      legend: [],
    };
  }, [ref, lens]);

  // Which way is outside each corner, for its number and its apex badge.
  const outward = useMemo(() => {
    const out = new Map<number, [number, number]>();
    if (!ref) return out;
    for (const c of corners) out.set(c.n, outwardAt(ref.entry.series, c.apex_dist));
    return out;
  }, [ref, corners]);

  const option = useMemo<EChartsOption>(() => {
    const series: SeriesOption[] = [];
    // Zoomed in there is room for every mark to be a little larger.
    const dot = zoomRange ? (maximized ? 7 : 5) : maximized ? 4.5 : 3.5;

    // Surveyed road first, under every lap line: fill, then borders, then the
    // start/finish line. Quad corners are pre-computed server-side, so
    // renderItem only has to project them.
    if (outline && layers.road) {
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
      // The borders as polylines: one line series per chained border.
      for (const [i, line] of (borders?.lines ?? []).entries()) {
        series.push({
          id: `outline-border-${i}`,
          type: "line",
          data: line,
          showSymbol: false,
          lineStyle: { color: BORDER_COLOR, width: 1.4, opacity: 0.7 },
          silent: true,
          z: 0.8,
        });
      }
      // Gaps sit between the road fill and the borders: part of the backdrop,
      // never over a border that was actually surveyed. Loose ticks are the
      // legacy outline's borders, drawn a segment at a time as before.
      for (const [id, data, color, width, dash, opacity, z] of [
        ["outline-gaps", outline.gaps ?? [], GAP_COLOR, 1.4, [8, 6], 0.55, 0.7],
        ["outline-edges", borders?.loose ?? [], BORDER_COLOR, 1.4, null, 0.7, 0.8],
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

    // Comparison laps under the reference, as thin solid lines: faint across
    // the whole lap, and, when zoomed, bright along the stretch in view.
    if (layers.others) {
      for (const lap of laps) {
        if (lap.isRef) continue;
        const s = lap.entry.series;
        series.push({
          id: `line-${lap.id}`,
          type: "line",
          data: s.dist.map((_, i) => [s.pos_x[i], s.pos_z[i]]),
          showSymbol: false,
          lineStyle: { color: lap.color, width: 1.2, opacity: zoomRange ? 0.15 : 0.5 },
          silent: true,
          z: 2,
        });
        if (zoomRange) {
          const inZoom: [number, number][] = [];
          for (let i = 0; i < s.dist.length; i++) {
            if (s.dist[i] >= zoomRange[0] - 30 && s.dist[i] <= zoomRange[1] + 30) {
              inZoom.push([s.pos_x[i], s.pos_z[i]]);
            }
          }
          series.push({
            id: `line-zoom-${lap.id}`,
            type: "line",
            data: inZoom,
            showSymbol: false,
            lineStyle: { color: lap.color, width: 1.9, opacity: 0.95 },
            silent: true,
            z: 2,
          });
        }
      }
    }

    if (ref && colouring) {
      const s = ref.entry.series;
      const inZoomAt = (i: number) =>
        !zoomRange || (s.dist[i] >= zoomRange[0] && s.dist[i] <= zoomRange[1]);

      // The whole lap stays under a zoomed window as a faint line, so the
      // stretch in view is read in its place on the circuit.
      if (zoomRange) {
        series.push({
          id: "ref-line-dim",
          type: "line",
          data: s.dist.map((_, i) => [s.pos_x[i], s.pos_z[i]]),
          showSymbol: false,
          lineStyle: { color: INK_GHOST, width: 1.4, opacity: 0.55 },
          silent: true,
          z: 1.8,
        });
      }

      // The reference lap as ONE series: a polyline per run of samples of
      // one lens class, each in its class's colour, consecutive runs
      // sharing their joining sample so the line never breaks. One series
      // with a hundred short polylines, not one series per class with the
      // others nulled out — the line is drawn once, and a lens switch
      // changes a palette. (A visualMap cannot do this: ECharts colours a
      // line's stroke by a coordinate axis only, never by a third value.)
      // Samples arrive on a 5 m grid; the stretch in view is extended one
      // sample either way so the line runs to the edge of the window.
      const runs: { points: [number, number][]; color: string }[] = [];
      let run: { points: [number, number][]; color: string } | null = null;
      let runKey = -1;
      for (let i = 0; i < s.dist.length; i++) {
        const show =
          inZoomAt(i) ||
          (i > 0 && inZoomAt(i - 1)) ||
          (i + 1 < s.dist.length && inZoomAt(i + 1));
        if (!show || s.pos_x[i] == null || s.pos_z[i] == null) {
          run = null;
          continue;
        }
        const key = colouring.keys[i] ?? 0;
        const point: [number, number] = [s.pos_x[i], s.pos_z[i]];
        if (run && key === runKey) {
          run.points.push(point);
          continue;
        }
        if (run) run.points.push(point); // the join belongs to both runs
        run = { points: [point], color: colouring.palette[key] ?? colouring.palette[0] };
        runKey = key;
        runs.push(run);
      }
      const refWidth = zoomRange ? 3.4 : 2.2;
      series.push({
        id: "ref-line",
        type: "custom",
        data: runs.map((_, i) => [i]),
        renderItem: (params, apiCustom) => {
          const piece = runs[params.dataIndex];
          if (!piece) return null;
          return {
            type: "polyline",
            shape: { points: piece.points.map((p) => apiCustom.coord(p)) },
            style: {
              stroke: piece.color,
              fill: "none",
              lineWidth: refWidth,
              opacity: 0.95,
              lineJoin: "round",
              lineCap: "round",
            },
          } as never;
        },
        progressive: 0,
        silent: true,
        z: 2.8,
      });

      // Surface halos, drawn beneath the line. A kerb-only touch is routine;
      // any loose-surface wheel is the interesting one, so loose wins when a
      // sample has both (two wheels on the kerb, two on grass).
      const surface = s.surface;
      if (layers.kerbs && surface?.some((v) => v > 0)) {
        const kerbPts: Array<{ value: number[]; itemStyle: { opacity: number } }> = [];
        const loosePts: Array<{ value: number[]; itemStyle: { opacity: number } }> = [];
        for (let i = 0; i < s.dist.length; i++) {
          const v = surface[i] ?? 0;
          const bucket =
            looseWheelCount(v) > 0 ? loosePts : kerbWheelCount(v) > 0 ? kerbPts : null;
          if (!bucket) continue;
          bucket.push({
            value: [s.pos_x[i], s.pos_z[i]],
            itemStyle: { opacity: inZoomAt(i) ? 0.55 : 0.1 },
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

      if (layers.peaks) {
        const pv = ref.entry.peaks_valleys;
        const pvInZoom = (d: number) => !zoomRange || (d >= zoomRange[0] && d <= zoomRange[1]);
        series.push(
          {
            id: "peaks",
            type: "scatter",
            data: pv.peaks.filter((p) => pvInZoom(p.dist)).map((p) => [p.x, p.z]),
            symbol: "triangle",
            symbolSize: maximized ? 12 : 9,
            itemStyle: { color: PEAK_COLOR, borderColor: "#0d0f13", borderWidth: 1 },
            silent: true,
            z: 5,
          },
          {
            id: "valleys",
            type: "scatter",
            data: pv.valleys.filter((p) => pvInZoom(p.dist)).map((p) => [p.x, p.z]),
            symbol: "triangle",
            symbolRotate: 180,
            symbolSize: maximized ? 12 : 9,
            itemStyle: { color: VALLEY_COLOR, borderColor: "#0d0f13", borderWidth: 1 },
            silent: true,
            z: 5,
          },
        );
      }

      // Corners (detected on the reference lap, or authored for the circuit).
      // Bare numbers set just outside the apex while the view shows a
      // readable amount; plain dots otherwise. Clickable: they are the
      // fastest way into a corner. The number sits in an invisible rounded
      // box, which is what takes the click and what lights up for the
      // current corner.
      const cornersInView = corners.filter(
        (c) => !zoomRange || (c.apex_dist >= zoomRange[0] && c.apex_dist <= zoomRange[1]),
      );
      const numbered =
        cornersInView.length > 0 &&
        cornersInView.length <= (maximized ? MAX_NUMBERED_CORNERS_LARGE : MAX_NUMBERED_CORNERS);
      if (layers.numbers && cornersInView.length > 0) {
        const off = zoomRange ? LABEL_OFFSET_ZOOMED_PX : LABEL_OFFSET_PX;
        const fontSize = maximized ? 12 : 10.5;
        series.push({
          id: "corners",
          type: "scatter",
          data: cornersInView.map((c) => {
            const [ux, uz] = outward.get(c.n) ?? [0, -1];
            const isCurrent = current?.n === c.n;
            return {
              value: [c.apex_x, c.apex_z],
              name: String(c.n),
              symbol: numbered ? "roundRect" : "circle",
              symbolSize: numbered ? [String(c.n).length > 1 ? 26 : 20, 18] : 5,
              symbolOffset: numbered ? [ux * off, uz * off] : [0, 0],
              itemStyle: numbered
                ? { color: isCurrent ? CURRENT_CORNER_BG : "transparent" }
                : { color: INK_DIM, opacity: 0.85 },
              label: {
                show: numbered,
                position: "inside",
                formatter: "{b}",
                color: isCurrent ? ACCENT_300 : zoomRange ? INK_SOFT : INK_DIM,
                fontSize,
                fontWeight: 600,
                fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif",
              },
            };
          }),
          emphasis: {
            itemStyle: { color: PANEL_2 },
            label: { color: INK },
          },
          cursor: "pointer",
          tooltip: { show: false },
          z: 4, // above the line, below the markers and cursors
        });
      }

      // Apex badges (zoomed only): the minimum speed through each corner in
      // view, the reference's and the compared laps', in lap colours.
      if (zoomRange && layers.apex && cornersInView.length > 0) {
        const others = laps.filter((lap) => !lap.isRef && lap.entry.corner_report).slice(0, 3);
        const rich: Record<string, { color: string }> = { k: { color: INK_FAINT }, r: { color: ref.color } };
        others.forEach((lap, i) => {
          rich[`c${i}`] = { color: lap.color };
        });
        const unit = speedUnit(units);
        series.push({
          id: "apex-badges",
          type: "scatter",
          data: cornersInView.map((c) => {
            const [ux, uz] = outward.get(c.n) ?? [0, -1];
            const parts = [`{k|min}`, `{r|${Math.round(speedValue(c.min_speed, units))}}`];
            others.forEach((lap, i) => {
              const row = lap.entry.corner_report?.find((r) => r.n === c.n);
              if (row) parts.push(`{c${i}|${Math.round(speedValue(row.min_speed, units))}}`);
            });
            parts.push(`{k|${unit}}`);
            return {
              value: [c.apex_x, c.apex_z],
              label: {
                show: true,
                position: "inside",
                offset: [-ux * BADGE_INSET_PX, -uz * BADGE_INSET_PX],
                formatter: parts.join(" "),
                backgroundColor: BADGE_BG,
                borderColor: HAIRLINE,
                borderWidth: 1,
                borderRadius: 4,
                padding: [2, 7],
                fontSize: 10.5,
                fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif",
                rich,
              },
            };
          }),
          symbolSize: 0.01,
          silent: true,
          z: 4.5,
        });
      }
    }

    // Aid rings, every selected lap. Above the line, below the corner
    // numbers — they are context for the line, not a thing to aim the
    // pointer at.
    for (const lap of laps) {
      if (!lap.isRef && !layers.others) continue;
      for (const aid of AID_LAYERS) {
        if (!layers[aid.key]) continue;
        const data = aidPoints(lap.entry.series, aid.bit, zoomRange);
        if (data.length === 0) continue;
        series.push({
          id: `aid-${aid.key}-${lap.id}`,
          type: "scatter",
          data,
          symbol: aid.symbol,
          symbolSize: dot * 2.4,
          itemStyle: { color: "transparent", borderColor: lap.color, borderWidth: 1.5 },
          silent: true,
          z: 3.5,
        });
      }
    }

    // Event markers, every selected lap. The only series on the map with a
    // tooltip, and clickable: a marker takes every panel to the event.
    if (layers.events) {
      for (const lap of laps) {
        if (!lap.isRef && !layers.others) continue;
        const markers = eventMarkers(lap.entry, zoomRange);
        if (markers.length === 0) continue;
        series.push({
          id: `events-${lap.id}`,
          type: "scatter",
          data: markers.map(({ x, z, event }) => ({
            value: [x, z],
            symbol: EVENT_GLYPHS[event.type].symbol,
            symbolRotate: EVENT_GLYPHS[event.type].rotate,
            range: [Math.max(0, event.start_dist - EVENT_PAD_M), event.end_dist + EVENT_PAD_M],
            tip:
              `<b>${EVENT_LABELS[event.type]}</b> · ${escapeHtml(lap.label)}<br/>` +
              `${escapeHtml(wheelsText(event.wheels))}<br/>` +
              `${escapeHtml(severityText(event))}<br/>` +
              `<span style="color:${CHART_COLORS.label}">at ${event.start_dist.toFixed(0)} m · click to zoom</span>`,
          })),
          symbolSize: maximized ? 14 : 11,
          itemStyle: { color: lap.color, borderColor: "#0d0f13", borderWidth: 1.2 },
          cursor: "pointer",
          z: 6, // above the peak/valley triangles, below the cursors
        });
      }
    }

    // A drag along the line, filled in by the pointer handlers below: a
    // wide translucent band over the stretch being picked.
    series.push({
      id: "brush",
      type: "line",
      data: [],
      showSymbol: false,
      lineStyle: { color: ACCENT, width: 14, opacity: 0.28, join: "round", cap: "round" },
      silent: true,
      z: 2.7,
    });

    // Brake points, filled in by the effect below. A pin and not a dot: the
    // dots on this map are cars and samples, and a pin's tip — which is
    // where ECharts anchors the symbol — says "here".
    series.push({
      id: "brake-marks",
      type: "scatter",
      data: [],
      symbol: "pin",
      symbolSize: maximized ? 30 : 24,
      z: 7, // above the event markers, below the cursors
    });

    // One synced cursor dot per lap, in the lap's color (reference white).
    for (const lap of laps) {
      if (!lap.isRef && !layers.others) continue;
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
      grid: { left: GRID_PAD, right: GRID_PAD, top: GRID_PAD, bottom: GRID_PAD },
      // The window is owned by the camera effect, never by the option: an
      // option rebuild (a lens switch, a layer) must not jump the view.
      xAxis: { type: "value", show: false, scale: true },
      yAxis: { type: "value", show: false, inverse: true, scale: true },
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
      // Item-triggered, and every series but the event markers is silent or
      // opts out, so this is the markers' tooltip and nothing else's.
      tooltip: {
        trigger: "item",
        confine: true,
        backgroundColor: "#16191e",
        borderColor: CHART_COLORS.axis,
        padding: [6, 9],
        textStyle: { color: "#e5e7eb", fontSize: 11 },
        formatter: (params) => {
          const item = Array.isArray(params) ? params[0] : params;
          return (item?.data as { tip?: string } | undefined)?.tip ?? "";
        },
      },
      series,
    };
  }, [laps, zoomRange, outline, borders, corners, current, maximized, ref, layers, colouring, outward, units]);

  // The camera: which window the axes show. The option never sets one (see
  // above), so this effect does, after every option change and every change
  // of target. A new zoom window animates there over ZOOM_ANIM_MS, easing
  // out; the follow camera, and a strip drag driving the window live, snap
  // every frame. `shown` is what is on screen right now, the start of the
  // next animation.
  const shown = useRef<Axes | null>(null);
  const animation = useRef(0);
  const animatingTo = useRef<Axes | null>(null);
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const followed = follow ? followWindow(ref, cursorDist, aspect, followSpanM) : null;
    const target = followed ?? baseAxis;
    if (!target) return;
    const apply = (axes: Axes) => {
      // The first window goes in right away, so the first frame ever drawn
      // is framed; the rest ride the next frame like the cursor dots.
      const lazy = shown.current != null;
      chart.setOption(
        {
          xAxis: { min: axes.xMin, max: axes.xMax },
          yAxis: { min: axes.zMin, max: axes.zMax },
        },
        { notMerge: false, lazyUpdate: lazy },
      );
      shown.current = axes;
    };
    if (animatingTo.current && sameAxes(animatingTo.current, target)) return;
    cancelAnimationFrame(animation.current);
    animatingTo.current = null;
    const from = shown.current;
    if (!animate || followed || !from || sameAxes(from, target) || maximized) {
      apply(target);
      return;
    }
    animatingTo.current = target;
    // The starting window goes back in right away: the option this effect
    // follows was just replaced wholesale, and a frame drawn before the
    // first animation step would fit each axis to its own data — the map
    // stretched to the box for one frame, every zoom.
    apply(from);
    const t0 = performance.now();
    const step = (now: number) => {
      const p = Math.min(1, (now - t0) / ZOOM_ANIM_MS);
      const e = 1 - Math.pow(1 - p, 3);
      const mix = (k: keyof Axes) => from[k] + (target[k] - from[k]) * e;
      apply({ xMin: mix("xMin"), xMax: mix("xMax"), zMin: mix("zMin"), zMax: mix("zMax") });
      if (p < 1) animation.current = requestAnimationFrame(step);
      else animatingTo.current = null;
    };
    animation.current = requestAnimationFrame(step);
  }, [baseAxis, follow, followSpanM, cursorDist, ref, aspect, animate, maximized, option]);
  useEffect(() => () => cancelAnimationFrame(animation.current), []);

  // Cursor updates merge into the existing chart by series id — no rebuild.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    // The reference lap's clock at the cursor, for time sync.
    const tRef =
      sync === "time" && ref && cursorDist != null
        ? timeAtDist(ref.entry.series, cursorDist)
        : null;
    // Every dot is interpolated, the reference's included: in time sync the
    // gap between them is the reading, and a reference dot snapped to its
    // nearest step would add up to half a step of false gap.
    // Only the dots the option has a series for: with "Other laps" off the
    // other laps have none, and a patch naming a series the chart does not
    // hold would add a typeless one.
    const updates: SeriesOption[] = laps
      .filter((lap) => lap.isRef || layers.others)
      .map((lap) => {
        const s = lap.entry.series;
        let at: [number, number] | null = null;
        if (tRef != null && !lap.isRef) at = positionAtTime(lap.entry.track ?? s, tRef);
        else if (cursorDist != null) at = positionAtDist(s, cursorDist);
        return { id: `cursor-${lap.id}`, data: at ? [at] : [] } as SeriesOption;
      });
    chart.setOption({ series: updates }, { notMerge: false, lazyUpdate: true });
  }, [laps, cursorDist, ref, sync, option, layers.others]);

  // Brake points merge in by series id as well. Keyed on the option too:
  // rebuilding it (a new zoom, a new selection) puts the empty series back.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const data = shownMarks.map((mark) => ({
      value: [mark.x, mark.z],
      itemStyle: { color: mark.color, borderColor: "#0d0f13", borderWidth: 1.2 },
      label: {
        show: true,
        // Over the pin and under the road, so two pins a few metres apart
        // keep their names apart.
        position: mark.isRef ? "bottom" : "top",
        distance: mark.isRef ? 4 : 2,
        formatter: mark.isRef ? "ref" : mark.label.split(" ")[0],
        color: "#e5e7eb",
        fontSize: maximized ? 11 : 10,
        textBorderColor: "#0d0f13",
        textBorderWidth: 2,
      },
      tip:
        `<b>Brake point</b> · ${escapeHtml(mark.label)}<br/>` +
        (mark.deltaM != null ? `${escapeHtml(brakeDeltaText(mark.deltaM))}<br/>` : "") +
        `<span style="color:${CHART_COLORS.label}">at ${mark.dist.toFixed(0)} m</span>`,
    }));
    chart.setOption(
      { series: [{ id: "brake-marks", data }] },
      { notMerge: false, lazyUpdate: true },
    );
  }, [shownMarks, option, maximized]);

  // --- gestures on the line (hero only: the maximized view pans) ------------

  // A drag along the line, as a stretch of the reference lap's distance.
  const [brush, setBrush] = useState<[number, number] | null>(null);
  const grab = useRef<{ start: number; moved: boolean } | null>(null);
  const brushRef = useRef(brush);
  brushRef.current = brush;
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !ref) return;
    const s = ref.entry.series;
    const data: [number, number][] = [];
    if (brush) {
      for (let i = 0; i < s.dist.length; i++) {
        if (s.dist[i] >= brush[0] && s.dist[i] <= brush[1]) data.push([s.pos_x[i], s.pos_z[i]]);
      }
    }
    chart.setOption({ series: [{ id: "brush", data }] }, { notMerge: false, lazyUpdate: true });
  }, [brush, ref, option]);

  /** The reference sample nearest a pointer, and how far off it is in
   *  pixels — null off the lap, or before the chart has an axis. */
  const nearest = useCallback(
    (px: number, py: number): { index: number; dist: number; offPx: number } | null => {
      const chart = chartRef.current;
      if (!chart || !ref) return null;
      const at = chart.convertFromPixel({ gridIndex: 0 }, [px, py]) as number[] | undefined;
      const step = chart.convertFromPixel({ gridIndex: 0 }, [px + 1, py]) as number[] | undefined;
      if (!at || !step || !Number.isFinite(at[0])) return null;
      const mPerPx = Math.abs(step[0] - at[0]) || 1;
      const s = ref.entry.series;
      let best = -1;
      let bestD2 = Infinity;
      for (let i = 0; i < s.dist.length; i++) {
        const x = s.pos_x[i];
        const z = s.pos_z[i];
        if (x == null || z == null) continue;
        const d2 = (x - at[0]) ** 2 + (z - at[1]) ** 2;
        if (d2 < bestD2) {
          bestD2 = d2;
          best = i;
        }
      }
      if (best < 0) return null;
      return { index: best, dist: s.dist[best], offPx: Math.sqrt(bestD2) / mPerPx };
    },
    [ref],
  );

  const gestures = hero && !maximized && !!ref;
  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!gestures || e.button !== 0) return;
    const hit = nearest(e.nativeEvent.offsetX, e.nativeEvent.offsetY);
    if (!hit || hit.offPx > LINE_GRAB_PX) return;
    // Not captured yet: a pointer that goes down on a corner number or an
    // event marker and comes straight up is a click, and the click has to
    // reach the chart. Capture starts with the drag.
    grab.current = { start: hit.dist, moved: false };
  };
  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!gestures) return;
    const hit = nearest(e.nativeEvent.offsetX, e.nativeEvent.offsetY);
    const g = grab.current;
    if (g && hit) {
      const next: [number, number] = [Math.min(g.start, hit.dist), Math.max(g.start, hit.dist)];
      if (!g.moved && next[1] - next[0] > 2) {
        g.moved = true;
        e.currentTarget.setPointerCapture(e.pointerId);
      }
      if (g.moved) setBrush(next);
      onCursorDist?.(hit.dist);
      return;
    }
    onCursorDist?.(hit && hit.offPx <= LINE_HOVER_PX ? hit.dist : null);
  };
  const onPointerUp = () => {
    const g = grab.current;
    grab.current = null;
    if (!g) return;
    const picked = brushRef.current;
    setBrush(null);
    if (!g.moved || !picked) return;
    const range = lineDragRange(picked, length);
    if (range) onZoomChange?.(range);
  };
  const onPointerLeave = () => {
    if (!gestures || grab.current) return;
    onCursorDist?.(null);
  };

  // ⌘/Ctrl + wheel zooms about the pointer; a plain wheel still scrolls the
  // page. Bound by hand because React's onWheel is passive and cannot
  // preventDefault the browser's pinch-zoom.
  const wheelState = useRef({ zoomRange, length, gestures });
  wheelState.current = { zoomRange, length, gestures };
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      const { zoomRange: range, length: lapLength, gestures: on } = wheelState.current;
      if (!on || !(e.ctrlKey || e.metaKey) || lapLength <= 0) return;
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const hit = nearest(e.clientX - rect.left, e.clientY - rect.top);
      const about = hit && hit.offPx <= LINE_HOVER_PX ? hit.dist : null;
      rangeRef.current?.(wheelRange(range ?? null, lapLength, about, e.deltaY > 0));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [nearest]);

  // --- overlays ---------------------------------------------------------------

  // The locator inset: the whole lap, with the stretch in view picked out.
  const locator = useMemo(() => {
    if (!zoomed || !ref || !hero) return null;
    const s = ref.entry.series;
    let minX = Infinity;
    let maxX = -Infinity;
    let minZ = Infinity;
    let maxZ = -Infinity;
    for (let i = 0; i < s.dist.length; i++) {
      const x = s.pos_x[i];
      const z = s.pos_z[i];
      if (x == null || z == null) continue;
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (z < minZ) minZ = z;
      if (z > maxZ) maxZ = z;
    }
    if (!isFinite(minX)) return null;
    const W = 150;
    const H = 104;
    const scale = Math.min((W - 12) / Math.max(maxX - minX, 1), (H - 12) / Math.max(maxZ - minZ, 1));
    const ox = (W - (maxX - minX) * scale) / 2 - minX * scale;
    const oz = (H - (maxZ - minZ) * scale) / 2 - minZ * scale;
    const pt = (i: number) => `${(s.pos_x[i] * scale + ox).toFixed(1)} ${(s.pos_z[i] * scale + oz).toFixed(1)}`;
    let full = "";
    let part = "";
    for (let i = 0; i < s.dist.length; i++) {
      if (s.pos_x[i] == null || s.pos_z[i] == null) continue;
      full += `${full ? "L" : "M"}${pt(i)}`;
      if (s.dist[i] >= zoomRange![0] && s.dist[i] <= zoomRange![1]) part += `${part ? "L" : "M"}${pt(i)}`;
    }
    return { full: `${full}Z`, part };
  }, [zoomed, ref, hero, zoomRange]);

  // The corner callout: what the compared lap did here against the reference.
  const callout = useMemo(() => {
    if (!current || !ref || !hero) return null;
    const dir = current.direction === "L" ? "left" : "right";
    const refRow = ref.entry.corner_report?.find((r) => r.n === current.n);
    const row = focus?.entry.corner_report?.find((r) => r.n === current.n);
    if (!focus || !row || !refRow) {
      return { name: `T${current.n}`, dir, note: `${Math.round(speedValue(current.min_speed, units))} ${speedUnit(units)} min` };
    }
    const lost = row.time_ms - refRow.time_ms;
    const delta = `${lost >= 0 ? "+" : "−"}${(Math.abs(lost) / 1000).toFixed(3)} s`;
    const deltaColor = lost > 20 ? "text-brake" : lost < -20 ? "text-throttle" : "text-ink-dim";
    const bd = row.brake_delta_m;
    const brake =
      bd == null
        ? null
        : Math.round(Math.abs(bd)) === 0
          ? "brakes level with ref"
          : `brakes ${Math.round(Math.abs(bd))} m ${bd < 0 ? "earlier" : "later"}`;
    return { name: `T${current.n}`, dir, who: focus.label.split(" ")[0], delta, deltaColor, brake };
  }, [current, ref, focus, hero, units]);

  const others = laps.filter((lap) => !lap.isRef && layers.others);
  const key = useMemo(
    () => layerKey(laps, layers, zoomRange, shownMarks),
    [laps, layers, zoomRange, shownMarks],
  );

  return (
    <div className={maximized ? "flex h-full flex-col" : undefined}>
      <div
        ref={boxRef}
        className={`relative select-none ${maximized ? "min-h-0 flex-1" : ""}`}
        style={gestures ? { touchAction: "none", cursor: brush ? "ew-resize" : "crosshair" } : undefined}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onPointerLeave={onPointerLeave}
        onDoubleClick={gestures ? () => onZoomChange?.(null) : undefined}
      >
        <EChart
          option={option}
          className={
            maximized ? "h-full w-full" : hero ? "h-[440px] w-full" : "aspect-square w-full"
          }
          onInit={(chart) => {
            chartRef.current = chart;
            shown.current = null;
            chart.on("click", (e) => {
              if (e.seriesId?.startsWith("events-")) {
                const range = (e.data as { range?: [number, number] } | undefined)?.range;
                if (range) rangeRef.current?.(range);
                return;
              }
              if (e.seriesId !== "corners") return;
              const corner = cornersRef.current.find((c) => String(c.n) === e.name);
              if (corner) zoomRef.current(corner);
            });
          }}
        />
        {onMaximize && (
          <button
            onClick={onMaximize}
            aria-label="Maximize the race line map"
            title="Open the map full screen"
            className="absolute right-2 top-2 rounded border border-edge bg-panel/80 px-1.5 py-0.5 text-xs text-ink-dim backdrop-blur transition-colors hover:border-edge-bright hover:text-ink"
          >
            ⤢
          </button>
        )}
        {locator && (
          <button
            onPointerDown={(e) => e.stopPropagation()}
            onClick={() => onZoomChange?.(null)}
            title="Back to the full lap"
            className="absolute left-3 top-2.5 h-[104px] w-[150px] rounded-md border-0 p-0"
            style={{ background: "rgba(11,13,16,.78)", boxShadow: "0 0 0 1px var(--color-hairline)" }}
          >
            <svg width="150" height="104" className="block">
              <path d={locator.full} fill="none" stroke={INK_GHOST} strokeWidth={2} strokeLinejoin="round" />
              <path d={locator.part} fill="none" stroke={ACCENT} strokeWidth={3.5} strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            <span className="absolute bottom-1 right-1.5 text-[9.5px] text-ink-faint">Esc · full lap</span>
          </button>
        )}
        {callout && (
          <div
            className="pointer-events-none absolute bottom-3 left-3 flex flex-wrap items-baseline gap-2.5 rounded-md px-3 py-[7px] font-tabular text-[11.5px]"
            style={{ background: BADGE_BG, boxShadow: "0 0 0 1px var(--color-hairline)" }}
          >
            <span className="text-[13px] font-semibold">{callout.name}</span>
            <span className="text-ink-dim">{callout.dir}</span>
            {"who" in callout && (
              <>
                <span className="text-ink-faint">·</span>
                <span className="text-ink-dim">{callout.who} here</span>
                <span className={callout.deltaColor}>{callout.delta}</span>
                {callout.brake && (
                  <>
                    <span className="text-ink-faint">·</span>
                    <span className="text-ink-dim">{callout.brake}</span>
                  </>
                )}
              </>
            )}
            {"note" in callout && (
              <>
                <span className="text-ink-faint">·</span>
                <span className="text-ink-dim">{callout.note}</span>
              </>
            )}
          </div>
        )}
        {gestures && (
          <span className="pointer-events-none absolute bottom-2.5 right-3.5 text-[10px] text-ink-faint">
            drag along the line to zoom · ⌘/ctrl + scroll · double-click resets
          </span>
        )}
      </div>

      {maximized && onZoomChange && corners.length > 0 && (
        <CornerBar corners={corners} current={current} onPick={zoomToCorner} />
      )}

      {/* The key, in the full-screen view only: in hero mode it sits in the
          panel header, where the lens and the Layers menu are. */}
      {maximized && (
        <div className="flex flex-wrap gap-x-3 gap-y-1 px-3 pb-2 text-[10px] text-ink-dim [&>span]:whitespace-nowrap">
          {others.map((lap) => (
            <span key={lap.id} className="flex items-center gap-1.5">
              <i className="inline-block h-0.5 w-4" style={{ backgroundColor: lap.color }} />
              {lap.label}
            </span>
          ))}
          {(lens?.legend ?? [
            { label: "throttle", color: INPUT_COLORS[2] },
            { label: "brake", color: INPUT_COLORS[0] },
            { label: "coast", color: INPUT_COLORS[1] },
          ]).map((entry) => (
            <span key={entry.label} className="flex items-center gap-1.5">
              <i className="inline-block h-0.5 w-3.5" style={{ backgroundColor: entry.color }} />
              {entry.label}
            </span>
          ))}
          {key.map((entry) => (
            <span key={entry.label} title={entry.title}>
              <span className="mr-1" style={{ color: entry.color ?? INK }}>
                {entry.glyph === "pin" ? "⬤" : entry.glyph}
              </span>
              {entry.label}
            </span>
          ))}
          {corners.length > 0 && layers.numbers && <span>corner numbers — click to zoom</span>}
          {hasOutline && layers.road && (
            <span title={`Surveyed over ${outline!.runs} run${outline!.runs === 1 ? "" : "s"}`}>
              <i
                className="mr-1 inline-block h-2 w-2 rounded-sm"
                style={{ backgroundColor: ROAD_FILL, outline: `1px solid ${BORDER_COLOR}` }}
              />
              surveyed road
            </span>
          )}
          {(outline?.walls.length ?? 0) > 0 && layers.road && (
            <span>
              <i className="mr-1 inline-block h-2 w-2 rounded-sm" style={{ backgroundColor: WALL_COLOR }} />
              wall
            </span>
          )}
          {(outline?.gaps?.length ?? 0) > 0 && layers.road && (
            <span title="Boundary the survey has not driven yet">
              <i
                className="mr-1 inline-block w-4 border-t-2 border-dashed align-middle"
                style={{ borderColor: GAP_COLOR }}
              />
              unsurveyed gap
            </span>
          )}
          {sync === "time" && others.length > 0 && (
            <span title="Each lap's dot sits where that lap was at the reference lap's elapsed time">
              dots synced on lap time
            </span>
          )}
          <span className="ml-auto">scroll to zoom · drag to pan</span>
        </div>
      )}
    </div>
  );
}

/** Corner picker for the full-screen view. Every corner as a chip, the
 *  selected one called out with what it is — because "corner 7" alone is not
 *  something anyone remembers, and the direction plus minimum speed is how
 *  you recognise it. */
function CornerBar({
  corners,
  current,
  onPick,
}: {
  corners: Corner[];
  current: Corner | null;
  onPick: (c: Corner | null) => void;
}) {
  return (
    <div className="flex items-center gap-1.5 border-t border-edge px-3 py-1.5 text-[11px]">
      <button
        onClick={() => onPick(stepCorner(corners, current, -1))}
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
        onClick={() => onPick(stepCorner(corners, current, 1))}
        aria-label="Next corner"
        className="shrink-0 rounded border border-edge px-1.5 text-ink-dim transition-colors hover:border-edge-bright hover:text-ink"
      >
        ›
      </button>
      {current && (
        <span className="shrink-0 truncate pl-1 text-ink-dim">
          {current.name || `T${current.n}`}
          {current.direction && ` · ${current.direction === "L" ? "left" : "right"}`}
          {` · ${current.min_speed.toFixed(0)} km/h · ${current.angle_deg.toFixed(0)}°`}
        </span>
      )}
    </div>
  );
}
