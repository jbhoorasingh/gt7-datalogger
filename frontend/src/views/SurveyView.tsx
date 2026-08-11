// Surface survey view (issue #37, the seed of #38/#39): validate GT7's
// per-wheel surface_types encoding and the wheel-contact derivation on real
// hardware, from any browser on the LAN while driving.
//
// The capture itself runs server-side in the 60 Hz packet path
// (backend/app/processing/survey.py) — surface transitions are single-tick
// events the ~30 Hz live stream would miss. This view starts/stops a run,
// shows the live per-wheel surface, the char histogram (with a loud banner
// for chars the mapping doesn't know), and plots each transition's derived
// wheel-contact points so a pass over a known kerb makes the positional
// error visible immediately.

import type * as echarts from "echarts";
import type { EChartsOption, SeriesOption } from "echarts";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { EChart } from "@/components/EChart";
import { api } from "@/lib/api";
import {
  borderCoverage,
  directionArrows,
  roadQuads,
  TRAIL_JUMP_M,
} from "@/lib/surveyGeometry";
import {
  SURVEY_WHEELS,
  surfaceWheelCodes,
  type SurveyEdge,
  type SurveyStatus,
  type SurveyTransition,
  type TrackBundleInfo,
} from "@/lib/types";
import { subscribeWs } from "@/lib/wsBus";
import { liveFrameRef, useTelemetry } from "@/store/telemetry";
import { toast } from "@/store/toasts";

const CHAR_TO_CODE: Record<string, number> = { T: 1, C: 2, D: 3, G: 4, S: 5, s: 6 };

const CODE_META: Record<number, { label: string; color: string }> = {
  0: { label: "no data", color: "#3a414c" },
  1: { label: "tarmac", color: "#6b7280" },
  2: { label: "kerb", color: "#eab308" },
  3: { label: "dirt", color: "#b45309" },
  4: { label: "grass", color: "#22c55e" },
  5: { label: "sand", color: "#f59e0b" },
  6: { label: "snow", color: "#e5e7eb" },
  7: { label: "unknown", color: "#ef4444" },
};

function charMeta(ch: string): { label: string; color: string } {
  return CODE_META[CHAR_TO_CODE[ch] ?? 7];
}

const MAX_TRANSITIONS = 500;

// Perimeter rendering: border points draw as short edge ticks along the
// local travel direction; matched left/right pairs fill the road between.
const LEFT_BORDER_COLOR = "#60a5fa";
const RIGHT_BORDER_COLOR = "#f472b6";
const RUNOFF_COLOR = "#a855f7";
const WALL_COLOR = "#ef4444";
const ROAD_FILL = "rgba(148, 163, 184, 0.16)";
const TICK_HALF_LENGTH_M = 2;
// Tick stroke by encoded class: 0 = left, 1 = right, 2 = run-off edge,
// 3 = wall (manual kinds override the side color so they stand out).
const TICK_COLORS = [LEFT_BORDER_COLOR, RIGHT_BORDER_COLOR, RUNOFF_COLOR, WALL_COLOR];

function tickClass(e: SurveyEdge): number {
  if (e.kind === "runoff") return 2;
  if (e.kind === "wall") return 3;
  return e.side === "R" ? 1 : 0;
}

const MARK_KINDS = ["edge", "runoff", "wall"] as const;

// All three tags mark the SAME thing — the edge of the racing surface,
// where the surface chars cannot see it. They differ only in what lies
// beyond, which is what lap-validity judging needs to know: running wide
// onto pavement is not running wide into gravel.
const MARK_HELP: Record<(typeof MARK_KINDS)[number], { what: string; when: string }> = {
  edge: {
    what: "Track edge, with nothing notable beyond it.",
    when: "The default: a painted line or kerb edge the surface chars cannot see because there is tarmac either side.",
  },
  runoff: {
    what: "Track edge, with paved run-off beyond it.",
    when: "Same boundary as 'edge' — the tag records that going wide here puts you on pavement, not grass. Mark the edge itself, not the far side of the run-off.",
  },
  wall: {
    what: "Track edge, with a wall, barrier or fence beyond it.",
    when: "Use where there is no off-surface to run onto at all.",
  },
};

// Why no cornering sample has landed yet. The old edge-ride estimator could
// sit at "assumed" for an entire session without ever saying what it was
// waiting for; naming the gate that rejected the most ticks turns a silent
// stall into something the driver can act on.
const WIDTH_STALL: Record<string, string> = {
  straight: "no corner yet — needs sustained steering",
  on_pedals: "always on throttle or brakes — needs a coasting corner",
  slip: "wheels slipping — needs a corner with grip",
  slow: "too slow",
  implausible: "readings out of range",
};

function WidthStall({ rejects }: { rejects: SurveyStatus["yaw_rejects"] }) {
  const entries = Object.entries(rejects) as [string, number][];
  if (!entries.length) return null;
  const [why] = entries.sort((a, b) => b[1] - a[1])[0];
  const hint = WIDTH_STALL[why];
  return hint ? <> · {hint}</> : null;
}

// The same transition can arrive twice — once in a status poll's `recent`
// ring and once over the WebSocket, in either order — so state is always
// merged by the run-unique record number instead of blindly appended.
function mergeTransitions(
  prev: SurveyTransition[],
  incoming: SurveyTransition[],
): SurveyTransition[] {
  if (incoming.length === 0) return prev;
  const byN = new Map<number, SurveyTransition>();
  for (const t of prev) byN.set(t.n, t);
  for (const t of incoming) byN.set(t.n, t);
  return [...byN.values()].sort((a, b) => a.n - b.n).slice(-MAX_TRANSITIONS);
}

export function SurveyView() {
  const wsConnected = useTelemetry((s) => s.wsConnected);
  const connStatus = useTelemetry((s) => s.status);
  const [status, setStatus] = useState<SurveyStatus | null>(null);
  const [transitions, setTransitions] = useState<SurveyTransition[]>([]);
  const [trackWidth, setTrackWidth] = useState("1.6");
  const [track, setTrack] = useState("");
  const [trackTouched, setTrackTouched] = useState(false);
  const [knownTracks, setKnownTracks] = useState<string[]>([]);
  const [bundles, setBundles] = useState<TrackBundleInfo[]>([]);
  const [busy, setBusy] = useState(false);
  const [wheelValue, setWheelValue] = useState(0);
  const chartRef = useRef<echarts.ECharts | null>(null);

  // Track picker suggestions: the user's own named tracks first (these match
  // the session auto-identification labels), then every official GT7 layout
  // (+ reverse configs) from the bundled catalog. The default value is the
  // current session's identified circuit, until the user types their own.
  useEffect(() => {
    void (async () => {
      const [named, catalog, bundleList] = await Promise.all([
        api.tracks().catch(() => []),
        api.trackCatalog().catch(() => null),
        api.trackBundles().catch(() => []),
      ]);
      setBundles(bundleList);
      const official =
        catalog?.tracks.flatMap((t) =>
          t.layouts.flatMap((l) => [
            l.official_name,
            ...(l.reverse ? [`${l.official_name} (Reverse)`] : []),
          ]),
        ) ?? [];
      const names = [
        ...new Set([...bundleList.map((b) => b.track), ...named.map((t) => t.name)]),
      ];
      setKnownTracks([...names, ...official.sort().filter((n) => !names.includes(n))]);
    })();
  }, []);
  const identified = connStatus?.track_name ?? "";
  useEffect(() => {
    if (!trackTouched) setTrack(identified);
  }, [identified, trackTouched]);

  // Driven-path breadcrumb + the run's full border-edge set, both fetched
  // incrementally from the server (the browser only ever holds a window of
  // recent transitions, but edges are the track taking shape — every lap's
  // evidence must stay on the map).
  //
  // All lengths/epochs live in refs so `refresh` keeps ONE identity: keying
  // it on state would re-register the poll effect on every append and turn
  // the 3 s interval into a continuous request loop against the Pi.
  const [trail, setTrail] = useState<[number, number][]>([]);
  const trailEpoch = useRef(-1);
  const trailLen = useRef(0);
  const trailBusy = useRef(false);
  const [edges, setEdges] = useState<SurveyEdge[]>([]);
  const edgesEpoch = useRef(-1);
  const edgesLen = useRef(0);
  const edgesBusy = useRef(false);
  // Responses requested before the latest Start are a different run's data;
  // the generation guard discards them wholesale.
  const fetchGen = useRef(0);
  const acceptAfter = useRef(0);
  // Run identity: when the server's run changes (any client — a phone used
  // as the pit display never clicks Start), local transitions are stale.
  const runStartedAt = useRef<string | null>(null);

  const applyStatus = useCallback((st: SurveyStatus) => {
    setStatus(st);
    if (st.started_at !== runStartedAt.current) {
      runStartedAt.current = st.started_at;
      setTransitions(st.recent); // new run: the server ring is the truth
    } else {
      setTransitions((prev) => mergeTransitions(prev, st.recent));
    }
    const gen = fetchGen.current;
    if (
      !trailBusy.current &&
      (st.trail_epoch !== trailEpoch.current || st.trail_points > trailLen.current)
    ) {
      trailBusy.current = true;
      const since = st.trail_epoch === trailEpoch.current ? trailLen.current : 0;
      api.survey
        .trail(since, trailEpoch.current)
        .then((t) => {
          if (gen <= acceptAfter.current) return;
          trailEpoch.current = t.epoch;
          setTrail((prev) => {
            const next = t.since === 0 ? t.points : [...prev, ...t.points];
            trailLen.current = next.length;
            return next;
          });
        })
        .catch(() => {})
        .finally(() => {
          trailBusy.current = false;
        });
    }
    if (
      !edgesBusy.current &&
      (st.edges_epoch !== edgesEpoch.current || st.edge_points > edgesLen.current)
    ) {
      edgesBusy.current = true;
      const since = st.edges_epoch === edgesEpoch.current ? edgesLen.current : 0;
      api.survey
        .edges(since, edgesEpoch.current)
        .then((e) => {
          if (gen <= acceptAfter.current) return;
          edgesEpoch.current = e.epoch;
          setEdges((prev) => {
            const next = e.since === 0 ? e.points : [...prev, ...e.points];
            edgesLen.current = next.length;
            return next;
          });
        })
        .catch(() => {})
        .finally(() => {
          edgesBusy.current = false;
        });
    }
  }, []);

  const refresh = useCallback(() => {
    const gen = ++fetchGen.current;
    api.survey
      .status()
      .then((st) => {
        if (gen <= acceptAfter.current) return; // requested before last Start
        applyStatus(st);
      })
      .catch(() => {});
  }, [applyStatus]);

  useEffect(() => {
    refresh();
    const id = window.setInterval(refresh, 3000);
    return () => window.clearInterval(id);
  }, [refresh]);

  useEffect(
    () =>
      subscribeWs((msg) => {
        if (msg.type === "survey") {
          setTransitions((prev) => mergeTransitions(prev, [msg.data]));
        }
      }),
    [],
  );

  // Live per-wheel surface + car dot, off the 30 Hz frame ref (display only —
  // detection happens server-side at 60 Hz).
  useEffect(() => {
    const id = window.setInterval(() => {
      const f = liveFrameRef.current;
      setWheelValue(f?.surface ?? 0);
      chartRef.current?.setOption(
        { series: [{ id: "car", data: f ? [[f.pos_x, f.pos_z]] : [] } as SeriesOption] },
        { notMerge: false, lazyUpdate: true },
      );
    }, 150);
    return () => window.clearInterval(id);
  }, []);

  async function setMark(side: "L" | "R" | null, kind: "edge" | "runoff" | "wall") {
    try {
      setStatus(await api.survey.mark(side, kind));
    } catch (e) {
      toast(e instanceof Error ? e.message : "Could not set marking", "error");
    }
  }

  // Raw packet inspector: poll the latest decoded packet while open and
  // highlight fields that changed since the previous sample, so anything
  // moving is easy to call out. (Single-tick blips can slip between the
  // 500 ms samples — the undocumented flag bits are watched server-side.)
  const [rawOpen, setRawOpen] = useState(false);
  const [packet, setPacket] = useState<Record<string, unknown> | null>(null);
  const [changedKeys, setChangedKeys] = useState<Set<string>>(new Set());
  const prevPacket = useRef<Record<string, unknown> | null>(null);
  useEffect(() => {
    if (!rawOpen) return;
    const id = window.setInterval(() => {
      void api.survey
        .packet()
        .then(({ packet: p }) => {
          if (!p) return;
          const prev = prevPacket.current;
          const diff = new Set<string>();
          if (prev) {
            for (const key of Object.keys(p)) {
              if (JSON.stringify(p[key]) !== JSON.stringify(prev[key])) diff.add(key);
            }
          }
          prevPacket.current = p;
          setPacket(p);
          setChangedKeys(diff);
        })
        .catch(() => {});
    }, 500);
    return () => window.clearInterval(id);
  }, [rawOpen]);

  async function start() {
    const width = Number(trackWidth);
    if (!Number.isFinite(width) || width <= 0.5 || width >= 3) {
      toast("Track width must be between 0.5 and 3 m", "error");
      return;
    }
    setBusy(true);
    try {
      const st = await api.survey.start(width, track.trim());
      // Everything requested before this moment belongs to the old run —
      // and the new run's own fetches must be a NEWER generation, or the
      // guard discards them too and the resumed bundle waits a poll cycle.
      acceptAfter.current = fetchGen.current;
      fetchGen.current += 1;
      setTrail([]);
      trailLen.current = 0;
      setEdges([]);
      edgesLen.current = 0;
      applyStatus(st); // new started_at resets transitions + refetches
      toast("Survey started — go touch some kerbs", "success");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Could not start survey", "error");
    } finally {
      setBusy(false);
    }
  }

  async function stop() {
    setBusy(true);
    try {
      applyStatus(await api.survey.stop());
      void api.trackBundles().then(setBundles).catch(() => {});
      toast("Survey stopped — JSONL log saved on the server", "success");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Could not stop survey", "error");
    } finally {
      setBusy(false);
    }
  }

  // How much of the driven loop has each border established — the "is the
  // track ready?" answer, plus where the largest hole is.
  const coverage = useMemo(() => borderCoverage(trail, edges), [trail, edges]);
  const arrows = useMemo(() => directionArrows(trail), [trail]);
  const finish = status?.finish ?? null;

  // The map redraws from a snapshot of the transition feed taken at most
  // every 2 s: kerb chatter delivers WS transitions in bursts (up to 60/s),
  // and rebuilding the chart for each one kept the outline in a permanent
  // redraw. The table below still consumes the live feed.
  const [mapTransitions, setMapTransitions] = useState<SurveyTransition[]>([]);
  const liveTransitions = useRef<SurveyTransition[]>([]);
  liveTransitions.current = transitions;
  useEffect(() => {
    const id = window.setInterval(() => {
      setMapTransitions((prev) =>
        prev === liveTransitions.current ? prev : liveTransitions.current,
      );
    }, 2000);
    return () => window.clearInterval(id);
  }, []);

  // Teleports (pit returns, restarts) must break the trail polyline — a NaN
  // point stops ECharts joining the two locations with a chord.
  const trailPlot = useMemo(() => {
    const out: [number, number][] = [];
    let prev: [number, number] | null = null;
    for (const p of trail) {
      if (prev && Math.hypot(p[0] - prev[0], p[1] - prev[1]) > TRAIL_JUMP_M) {
        out.push([NaN, NaN]);
      }
      out.push(p);
      prev = p;
    }
    return out;
  }, [trail]);

  // Fixed, EQUAL x/z axis spans on the square canvas — the map's aspect
  // ratio is true at every zoom level. Quantized generously so live data
  // growth almost never moves the extents (auto-fitting axes remapped the
  // zoom window on every 3 s update, which is what made zooming feel
  // broken), and padded so quantizing the center can't clip the track.
  const extents = useMemo(() => {
    let minX = Infinity;
    let maxX = -Infinity;
    let minZ = Infinity;
    let maxZ = -Infinity;
    const eat = (x: number, z: number) => {
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (z < minZ) minZ = z;
      if (z > maxZ) maxZ = z;
    };
    for (const [x, z] of trail) eat(x, z);
    for (const e of edges) eat(e.x, e.z);
    if (!Number.isFinite(minX)) return null;
    const QUANT = 250;
    const cx = Math.round((minX + maxX) / 2 / QUANT) * QUANT;
    const cz = Math.round((minZ + maxZ) / 2 / QUANT) * QUANT;
    const reach = Math.max(
      Math.abs(maxX - cx), Math.abs(minX - cx),
      Math.abs(maxZ - cz), Math.abs(minZ - cz),
    );
    const half = Math.max(Math.ceil((reach * 1.15) / QUANT) * QUANT, QUANT);
    return { x: [cx - half, cx + half], z: [cz - half, cz + half] };
  }, [trail, edges]);

  // Fly the map to a point — this is how an 88 m gap on a 4.5 km track
  // stops being a needle in a haystack. Setting the windows via setOption
  // (matched by dataZoom id) is the reliable path; later option merges
  // carry no start/end so the window sticks until the user moves it.
  const zoomTo = useCallback((x: number, z: number, windowM = 400) => {
    chartRef.current?.setOption({
      dataZoom: [
        { id: "zoom-x", startValue: x - windowM / 2, endValue: x + windowM / 2 },
        { id: "zoom-z", startValue: z - windowM / 2, endValue: z + windowM / 2 },
      ],
    });
  }, []);

  // Contact points of every transition, bucketed by the surface the wheel
  // moved ONTO; the symbol marks which track border the point belongs to
  // (circle = left, diamond = right, relative to travel direction). A pass
  // over a known kerb should paint its outline.
  const option = useMemo<EChartsOption>(() => {
    type Point = { value: [number, number]; symbol: string; symbolSize: number };
    const buckets = new Map<number, Point[]>();
    for (const t of mapTransitions) {
      const contacts = t.contacts;
      if (!contacts) continue;
      for (const w of t.changed) {
        const wi = SURVEY_WHEELS.indexOf(w as (typeof SURVEY_WHEELS)[number]);
        const point = contacts[w];
        if (wi < 0 || !point) continue;
        const code = CHAR_TO_CODE[t.to[wi]] ?? 7;
        const bucket = buckets.get(code) ?? [];
        bucket.push({
          value: point,
          symbol: t.border === "R" ? "diamond" : "circle",
          symbolSize: t.border === "R" ? 6 : 5,
        });
        buckets.set(code, bucket);
      }
    }
    const quads = roadQuads(edges);
    const series: SeriesOption[] = [
      {
        // Paths driven so far — coverage forming under the contact points.
        // progressive: 0 everywhere data can grow large: chunked rendering
        // clears-then-repaints across frames, which reads as flashing.
        id: "trail",
        type: "line",
        data: trailPlot,
        showSymbol: false,
        lineStyle: { color: "#3a414c", width: 1.2, opacity: 0.9 },
        progressive: 0,
        silent: true,
        z: 1,
      },
      {
        // Confirmed road: filled wherever a left border point has a right
        // border point directly across from it.
        id: "road-fill",
        type: "custom",
        data: quads,
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
        z: 1.5,
      },
      {
        // Perimeter ticks: every edge point of the run drawn as a short
        // segment along the travel direction — left/right borders in their
        // side colors, manually-marked run-off edges and walls in theirs.
        id: "border-ticks",
        type: "custom",
        data: edges.map((p) => [p.x, p.z, p.hx, p.hz, tickClass(p)]),
        renderItem: (_params, apiCustom) => {
          const x = Number(apiCustom.value(0));
          const z = Number(apiCustom.value(1));
          const hx = Number(apiCustom.value(2)) * TICK_HALF_LENGTH_M;
          const hz = Number(apiCustom.value(3)) * TICK_HALF_LENGTH_M;
          const p1 = apiCustom.coord([x - hx, z - hz]);
          const p2 = apiCustom.coord([x + hx, z + hz]);
          return {
            type: "line",
            shape: { x1: p1[0], y1: p1[1], x2: p2[0], y2: p2[1] },
            style: {
              stroke: TICK_COLORS[Number(apiCustom.value(4))] ?? LEFT_BORDER_COLOR,
              lineWidth: 1.6,
              opacity: 0.9,
            },
          };
        },
        progressive: 0,
        silent: true,
        z: 2,
      },
      // One series per surface class, ALWAYS present (empty when unused):
      // the chart updates in place instead of tearing series down and
      // recreating them, which made the whole map flicker every refresh.
      ...[1, 2, 3, 4, 5, 6, 7].map(
        (code): SeriesOption => ({
          id: `contacts-${code}`,
          type: "scatter",
          data: buckets.get(code) ?? [],
          symbolSize: 5,
          itemStyle: { color: CODE_META[code].color, opacity: 0.85 },
          silent: true,
          z: 3,
        }),
      ),
    ];
    // Travel-direction arrows along the driven path: "left border" means
    // left OF TRAVEL, which can sit on either screen side — without these,
    // a correct gap marker can look like it's on the wrong border.
    series.push({
      id: "direction-arrows",
      type: "custom",
      data: arrows.map((_, i) => i),
      renderItem: (params, apiCustom) => {
        const a = arrows[params.dataIndex as number];
        const c = apiCustom.coord([a.x, a.z]);
        const t = apiCustom.coord([a.x + a.dx * 4, a.z + a.dz * 4]);
        let vx = t[0] - c[0];
        let vy = t[1] - c[1];
        const len = Math.hypot(vx, vy) || 1;
        vx = (vx / len) * 7;
        vy = (vy / len) * 7;
        const wx = -vy * 0.5;
        const wy = vx * 0.5;
        return {
          type: "polygon",
          shape: {
            points: [
              [c[0] + vx, c[1] + vy],
              [c[0] - vx * 0.7 + wx, c[1] - vy * 0.7 + wy],
              [c[0] - vx * 0.7 - wx, c[1] - vy * 0.7 - wy],
            ],
          },
          style: { fill: "#8b93a1", opacity: 0.85 },
        };
      },
      progressive: 0,
      silent: true,
      z: 1.9,
    });

    // Coverage gaps, drawn where they ARE: dashed stretches beside the
    // trail in the missing border's color, with a labeled marker on each
    // side's largest gap so "87%, gap ~200 m" is drivable-to, not a riddle.
    // A closed border gets NO overlay — dashes on a finished perimeter are
    // clutter — and every series exists regardless so updates merge in
    // place instead of flickering.
    for (const [side, data, color] of [
      ["L", coverage?.left, LEFT_BORDER_COLOR],
      ["R", coverage?.right, RIGHT_BORDER_COLOR],
    ] as const) {
      const segments = data == null || data.closed ? [] : data.gapSegments;
      series.push({
        id: `gaps-${side}`,
        type: "custom",
        data: segments.map((_, i) => i),
        renderItem: (params, apiCustom) => ({
          type: "polyline",
          shape: {
            points: segments[params.dataIndex as number].map((p) =>
              apiCustom.coord(p),
            ),
          },
          style: {
            stroke: color,
            fill: "none",
            lineWidth: 2.5,
            lineDash: [8, 6],
            opacity: 0.8,
          },
        }),
        progressive: 0,
        silent: true,
        z: 2.4,
      });
      const marker =
        data != null && !data.closed && data.largestGapAt != null
          ? [data.largestGapAt]
          : [];
      series.push({
        // Rippling beacon — findable at full-track scale, where the gap
        // itself may be a couple dozen pixels of dashes.
        id: `gap-marker-${side}`,
        type: "effectScatter",
        data: marker,
        symbolSize: 10,
        rippleEffect: { scale: 3.5, brushType: "stroke" },
        itemStyle: { color },
        label: {
          show: true,
          position: side === "L" ? "top" : "bottom",
          formatter: `gap ~${Math.round(data?.largestGapM ?? 0)} m`,
          color,
          fontSize: 10,
          fontWeight: "bold",
          backgroundColor: "#14171c",
          padding: [2, 4],
          borderRadius: 3,
        },
        silent: true,
        z: 6,
      });
    }

    {
      // Start/finish line: perpendicular to the crossing direction, dashed
      // until repeat crossings agree. Always present; empty until located.
      const rx = finish?.hz ?? 0;
      const rz = -(finish?.hx ?? 0);
      const half = 15;
      series.push({
        id: "finish-line",
        type: "custom",
        data: finish
          ? [[finish.x - rx * half, finish.z - rz * half,
              finish.x + rx * half, finish.z + rz * half]]
          : [],
        renderItem: (_params, apiCustom) => {
          const p1 = apiCustom.coord([Number(apiCustom.value(0)), Number(apiCustom.value(1))]);
          const p2 = apiCustom.coord([Number(apiCustom.value(2)), Number(apiCustom.value(3))]);
          return {
            type: "line",
            shape: { x1: p1[0], y1: p1[1], x2: p2[0], y2: p2[1] },
            style: {
              stroke: "#e5e7eb",
              lineWidth: 3,
              lineDash: finish?.confident ? undefined : [6, 4],
              opacity: 0.95,
            },
          };
        },
        silent: true,
        z: 5,
      });
    }
    series.push({
      id: "car",
      type: "scatter",
      data: [] as number[][],
      symbolSize: 9,
      itemStyle: { color: "#fff", borderColor: "#6b7280", borderWidth: 1.5 },
      silent: true,
      z: 10,
    });
    return {
      animation: false,
      grid: { left: 8, right: 8, top: 8, bottom: 8 },
      xAxis: {
        type: "value",
        show: false,
        ...(extents ? { min: extents.x[0], max: extents.x[1] } : { scale: true }),
      },
      yAxis: {
        type: "value",
        show: false,
        inverse: true,
        ...(extents ? { min: extents.z[0], max: extents.z[1] } : { scale: true }),
      },
      // Wheel zoom + drag pan; filterMode none keeps polylines continuous
      // at the viewport edges. Stable ids let zoomTo() target the windows.
      dataZoom: [
        { id: "zoom-x", type: "inside", xAxisIndex: 0, filterMode: "none" },
        { id: "zoom-z", type: "inside", yAxisIndex: 0, filterMode: "none" },
      ],
      tooltip: { show: false },
      series,
    };
  }, [mapTransitions, trailPlot, edges, finish, coverage, arrows, extents]);

  const wheels = surfaceWheelCodes(wheelValue);
  const unknown = Object.keys(status?.unknown_chars ?? {});
  const active = status?.active ?? false;

  return (
    <div className="mx-auto max-w-6xl space-y-3 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-lg font-semibold">Surface survey</h2>
        <div className="ml-auto flex items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs text-ink-dim">
            track
            <input
              list="survey-tracks"
              value={track}
              disabled={active}
              placeholder="unidentified"
              onChange={(e) => {
                setTrack(e.target.value);
                setTrackTouched(true);
              }}
              className="w-40 rounded border border-edge bg-panel-2 px-1.5 py-1 text-xs text-ink"
              title="Which circuit these samples describe — defaults to the session's identified track"
            />
            <datalist id="survey-tracks">
              {knownTracks.map((name) => (
                <option key={name} value={name} />
              ))}
            </datalist>
          </label>
          <label
            className="flex items-center gap-1.5 text-xs text-ink-dim"
            title="Starting assumption for the car's axle track width — replaced automatically once enough 4-wheel edge crossings have measured the real value"
          >
            width
            <input
              type="number"
              step="0.05"
              min="0.5"
              max="3"
              value={trackWidth}
              disabled={active}
              onChange={(e) => setTrackWidth(e.target.value)}
              className="w-16 rounded border border-edge bg-panel-2 px-1.5 py-1 text-right font-tabular text-xs text-ink"
            />
            m
          </label>
          {active ? (
            <button className="btn-danger" onClick={stop} disabled={busy}>
              Stop survey
            </button>
          ) : (
            <button className="btn" onClick={start} disabled={busy || !wsConnected}>
              Start survey
            </button>
          )}
          <a
            className={`btn ${status?.log_path ? "" : "pointer-events-none opacity-40"}`}
            href={api.survey.exportUrl}
            download
            title="Full transition log (positions, velocity, raw rotation floats, contact points)"
          >
            Download JSONL
          </a>
        </div>
      </div>

      {!active && bundles.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-ink-dim">
          <span title="Persistent per-circuit survey bundles — pick one and start to resume exactly where you left off">
            Mapped circuits:
          </span>
          {bundles.map((b) => (
            <button
              key={b.slug}
              className="rounded-full border border-edge px-2 py-0.5 hover:border-accent hover:text-accent"
              title={`Resume ${b.track}: ${b.points} points from ${b.runs} run${
                b.runs === 1 ? "" : "s"
              }`}
              onClick={() => {
                setTrack(b.track);
                setTrackTouched(true);
              }}
            >
              {b.track} · {b.points.toLocaleString()} pts
            </button>
          ))}
        </div>
      )}

      {active && status != null && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg bg-panel px-3 py-2 text-xs">
          <span
            className="text-ink-dim"
            title="Where surface data is silent — walls, paved run-off — declare the boundary you are driving along and the survey records it from your wheel line"
          >
            Mark boundary:
          </span>
          {([null, "L", "R"] as const).map((side) => (
            <button
              key={side ?? "off"}
              className={`rounded border px-2 py-0.5 ${
                status.mark_side === side
                  ? "border-accent text-accent"
                  : "border-edge text-ink-dim hover:text-ink"
              }`}
              onClick={() => setMark(side, status.mark_kind)}
            >
              {side === null ? "off" : side === "L" ? "left" : "right"}
            </button>
          ))}
          <span className="ml-2 text-ink-dim">as</span>
          {MARK_KINDS.map((kind) => (
            <button
              key={kind}
              className={`rounded border px-2 py-0.5 ${
                status.mark_kind === kind
                  ? "border-accent text-accent"
                  : "border-edge text-ink-dim hover:text-ink"
              }`}
              onClick={() => setMark(status.mark_side, kind)}
            >
              {kind === "runoff" ? "run-off edge" : kind}
            </button>
          ))}
          {status.mark_side != null && (
            <span className="ml-auto font-medium text-warn">
              MARKING {status.mark_side === "L" ? "left" : "right"} {status.mark_kind} —
              drive along the boundary
            </span>
          )}

          {/* Guidance for the selected tag. Always visible rather than a
              tooltip: the choice is made while driving, and the run-off vs
              edge distinction is the one that actually gets mis-tagged. */}
          <div className="w-full border-t border-edge pt-2 text-[11px] leading-relaxed text-ink-dim">
            <span className="text-ink">
              {status.mark_kind === "runoff" ? "run-off edge" : status.mark_kind}
            </span>{" "}
            — {MARK_HELP[status.mark_kind as keyof typeof MARK_HELP]?.what}{" "}
            <span className="text-ink-dim/80">
              {MARK_HELP[status.mark_kind as keyof typeof MARK_HELP]?.when}
            </span>
          </div>
        </div>
      )}

      {active && status != null && (
        <div className="text-xs text-ink-dim">
          Surveying{" "}
          <span className="text-ink">{status.track || "unidentified track"}</span>
          {status.session_id != null && <> · session #{status.session_id}</>}
          {status.bundle != null && (
            <>
              {" "}
              ·{" "}
              <span
                className="text-accent"
                title="Track knowledge persists per circuit — every run resumes from and improves the bundle"
              >
                resumed from bundle ({status.bundle.points} pts, {status.bundle.runs}{" "}
                {status.bundle.runs === 1 ? "run" : "runs"})
              </span>
            </>
          )}{" "}
          · width
          in use{" "}
          <span className="text-ink">{status.width_in_use_m.toFixed(2)} m</span>{" "}
          {status.width_source === "cornering" ? (
            <span className="text-throttle">
              (measured from {status.yaw_samples} cornering samples)
            </span>
          ) : status.width_source === "car-memory" ? (
            <span className="text-throttle">
              (measured for this car on an earlier run
              {status.yaw_samples > 0 &&
                ` — re-checking: ${status.yaw_samples}/${status.yaw_needed}`}
              )
            </span>
          ) : status.width_source === "edge-ride" ? (
            <span className="text-throttle">
              (measured from {status.width_samples} edge crossings)
            </span>
          ) : (
            <>
              (assumed — measuring from cornering: {status.yaw_samples}/
              {status.yaw_needed}
              {status.yaw_samples === 0 && <WidthStall rejects={status.yaw_rejects} />})
            </>
          )}{" "}
          · lap recording continues alongside — laps saved during the run keep
          their per-tick surface column
        </div>
      )}

      {active && status != null && status.no_surface_packets > 0 && (
        <div className="rounded-lg border border-brake/50 bg-brake/10 px-3 py-2 text-sm text-brake">
          Packets carry no surface data — the console isn't answering on packet
          format C. Check Admin → Connection (game v1.68+ required).
        </div>
      )}
      {Object.keys(status?.unknown_flag_bits ?? {}).length > 0 && (
        <div className="rounded-lg border border-warn/50 bg-warn/10 px-3 py-2 text-sm text-warn">
          Undocumented packet flag bit(s) active:{" "}
          {Object.entries(status?.unknown_flag_bits ?? {})
            .map(([bit, n]) => `bit ${bit} (×${n})`)
            .join(", ")}{" "}
          — GT7 has no known track-limits field, so note what you were doing
          when these flipped (the JSONL records carry the raw flags).
        </div>
      )}
      {unknown.length > 0 && (
        <div className="rounded-lg border border-brake/50 bg-brake/10 px-3 py-2 text-sm text-brake">
          NEW surface chars seen: {unknown.map((c) => `'${c}'`).join(", ")} — not in
          the known mapping (T/C/D/G/S/s). Note where they occurred and add them to
          backend/app/processing/surface.py.
        </div>
      )}

      <div className="grid gap-3 lg:grid-cols-2">
        <div className="rounded-xl bg-panel p-3">
          <div className="mb-2 flex items-baseline justify-between text-xs text-ink-dim">
            <span>
              The track so far · scroll to zoom, drag to pan · arrows = travel
              direction
            </span>
            <span className="flex items-center gap-2 font-tabular">
              {status?.edge_points ?? 0} edge points · {status?.transitions ?? 0}{" "}
              transitions · {status?.packets ?? 0} packets
              <button
                className="rounded border border-edge px-1.5 text-ink-dim hover:text-ink"
                onClick={() => chartRef.current?.dispatchAction({ type: "restore" })}
              >
                reset view
              </button>
            </span>
          </div>
          {/* Merge mode keeps the zoom alive across the 3 s data refreshes
              (full replace reset the dataZoom windows — zoom "snapped
              back"), and because every series always exists (empty data
              when unused) updates apply IN PLACE: replace-merge tore the
              series down and rebuilt them each refresh, which flickered. */}
          <EChart
            option={option}
            notMerge={false}
            className="aspect-square w-full"
            onInit={(chart) => {
              chartRef.current = chart;
            }}
          />
          <div className="mt-1 flex flex-wrap gap-3 text-[10px] text-ink-dim">
            <span>
              <i className="mr-1 inline-block h-0.5 w-4 bg-edge align-middle" />
              driven
            </span>
            <span className="text-ink-dim">▸ travel direction</span>
            <span title="Which track border a contact belongs to, relative to travel direction — follow the arrows">
              <i
                className="mr-1 inline-block h-0.5 w-4 align-middle"
                style={{ backgroundColor: LEFT_BORDER_COLOR }}
              />
              left border
            </span>
            <span>
              <i
                className="mr-1 inline-block h-0.5 w-4 align-middle"
                style={{ backgroundColor: RIGHT_BORDER_COLOR }}
              />
              right border
            </span>
            <span title="Unmapped stretch of that border, offset toward its side — drive it to close the loop">
              <i className="mr-1 inline-block w-4 border-t-2 border-dashed border-ink-dim align-middle" />
              gap
            </span>
            <span>
              <i
                className="mr-1 inline-block h-0.5 w-4 align-middle"
                style={{ backgroundColor: RUNOFF_COLOR }}
              />
              run-off edge
            </span>
            <span>
              <i
                className="mr-1 inline-block h-0.5 w-4 align-middle"
                style={{ backgroundColor: WALL_COLOR }}
              />
              wall
            </span>
            <span title="Filled where a left border point has a right border point directly across">
              <i
                className="mr-1 inline-block h-2.5 w-4 align-middle"
                style={{ backgroundColor: ROAD_FILL }}
              />
              road
            </span>
            {finish != null && (
              <span title="From lap rollovers; dashed until repeat crossings agree">
                <i className="mr-1 inline-block h-0.5 w-4 bg-[#e5e7eb] align-middle" />
                finish line
              </span>
            )}
            {Object.entries(CODE_META)
              .filter(([code]) => Number(code) >= 1)
              .map(([code, meta]) => (
                <span key={code}>
                  <i
                    className="mr-1 inline-block h-2 w-2 rounded-full"
                    style={{ backgroundColor: meta.color }}
                  />
                  {meta.label}
                </span>
              ))}
          </div>
        </div>

        <div className="space-y-3">
          <div className="rounded-xl bg-panel p-3">
            <div
              className="mb-2 text-xs text-ink-dim"
              title="How much of the driven loop has each border established — closed means the perimeter is complete and the largest gap says where to drive next"
            >
              Track completeness
            </div>
            {coverage == null ? (
              <div className="text-sm text-ink-dim">
                Gathering data — drive a lap and touch the edges.
              </div>
            ) : (
              <div className="space-y-1.5 font-tabular text-xs">
                {(
                  [
                    ["Left border", coverage.left, LEFT_BORDER_COLOR],
                    ["Right border", coverage.right, RIGHT_BORDER_COLOR],
                  ] as const
                ).map(([label, side, color]) => (
                  <div key={label} className="flex items-center gap-2">
                    <span
                      className="w-20 shrink-0 text-ink-dim"
                      title="Left/right of the DIRECTION OF TRAVEL, not of the screen — follow the arrows on the map"
                    >
                      {label}
                    </span>
                    <div className="h-1.5 flex-1 rounded bg-panel-2">
                      <div
                        className="h-full rounded"
                        style={{
                          width: `${Math.min(100, side.pct).toFixed(0)}%`,
                          backgroundColor: color,
                        }}
                      />
                    </div>
                    <span className="w-9 shrink-0 text-right">{side.pct.toFixed(0)}%</span>
                    {side.closed ? (
                      <span className="w-28 shrink-0 text-throttle">closed ✓</span>
                    ) : side.largestGapAt != null ? (
                      <button
                        className="w-28 shrink-0 text-left text-accent hover:underline"
                        title="Zoom the map to this gap (it pulses)"
                        onClick={() =>
                          side.largestGapAt &&
                          zoomTo(side.largestGapAt[0], side.largestGapAt[1])
                        }
                      >
                        gap ~{Math.round(side.largestGapM)} m ⌖ zoom
                      </button>
                    ) : (
                      <span className="w-28 shrink-0 text-ink-dim">
                        gap ~{Math.round(side.largestGapM)} m
                      </span>
                    )}
                  </div>
                ))}
                <div className="flex items-center gap-2">
                  <span className="w-20 shrink-0 text-ink-dim">Road</span>
                  <div className="h-1.5 flex-1 rounded bg-panel-2">
                    <div
                      className="h-full rounded bg-ink-dim"
                      style={{ width: `${Math.min(100, coverage.roadPct).toFixed(0)}%` }}
                    />
                  </div>
                  <span className="w-9 shrink-0 text-right">
                    {coverage.roadPct.toFixed(0)}%
                  </span>
                  <span className="w-28 shrink-0 text-ink-dim">both borders</span>
                </div>
                <div className="flex items-center gap-2 pt-0.5">
                  <span className="w-20 shrink-0 text-ink-dim">Finish line</span>
                  {finish == null ? (
                    <span className="text-ink-dim">not yet — complete a lap</span>
                  ) : finish.confident ? (
                    <span className="text-throttle">
                      located ✓ ({finish.crossings} crossings, ±{finish.spread_m} m)
                    </span>
                  ) : (
                    <span className="text-warn">
                      provisional ({finish.crossings} crossing
                      {finish.crossings === 1 ? "" : "s"}) — another lap confirms it
                    </span>
                  )}
                </div>
              </div>
            )}
          </div>

          <div className="rounded-xl bg-panel p-3">
            <div className="mb-2 text-xs text-ink-dim">Live wheel surface</div>
            <div className="grid grid-cols-2 gap-2">
              {SURVEY_WHEELS.map((w, i) => {
                const meta = CODE_META[wheels[i]] ?? CODE_META[7];
                return (
                  <div
                    key={w}
                    className="flex items-center justify-between rounded-lg border border-edge px-3 py-2"
                    style={{ backgroundColor: `${meta.color}22` }}
                  >
                    <span className="text-xs text-ink-dim">{w}</span>
                    <span className="font-medium" style={{ color: meta.color }}>
                      {meta.label}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>

          <div className="rounded-xl bg-panel p-3">
            <div className="mb-2 text-xs text-ink-dim">Chars seen per wheel</div>
            {status == null || status.chars_seen.length === 0 ? (
              <div className="text-sm text-ink-dim">
                Nothing yet — start a survey and get on track.
              </div>
            ) : (
              <table className="w-full font-tabular text-xs">
                <tbody>
                  {SURVEY_WHEELS.map((w) => (
                    <tr key={w} className="border-t border-edge/50 first:border-0">
                      <td className="py-1 pr-2 text-ink-dim">{w}</td>
                      <td className="py-1">
                        {Object.entries(status.histogram[w] ?? {}).map(([ch, n]) => (
                          <span key={ch} className="mr-3">
                            <span style={{ color: charMeta(ch).color }}>{ch}</span>
                            <span className="text-ink-dim"> ×{n}</span>
                          </span>
                        ))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          <div className="rounded-xl bg-panel p-3">
            <div className="mb-2 text-xs text-ink-dim">
              Latest transitions (newest first)
            </div>
            <div className="max-h-64 overflow-y-auto">
              {transitions.length === 0 ? (
                <div className="text-sm text-ink-dim">No transitions yet.</div>
              ) : (
                <table className="w-full font-tabular text-xs">
                  <tbody>
                    {[...transitions].reverse().slice(0, 100).map((t) => (
                      <tr key={t.n} className="border-t border-edge/50 first:border-0">
                        <td className="py-1 pr-2 text-ink-dim">#{t.n}</td>
                        <td className="py-1 pr-2 text-ink-dim">L{t.lap}</td>
                        <td className="py-1 pr-2">
                          {t.from.split("").map((ch, i) => (
                            <span key={i} style={{ color: charMeta(ch).color }}>{ch}</span>
                          ))}
                          <span className="text-ink-dim"> → </span>
                          {t.to.split("").map((ch, i) => (
                            <span key={i} style={{ color: charMeta(ch).color }}>{ch}</span>
                          ))}
                        </td>
                        <td className="py-1 pr-2 text-ink-dim">{t.changed.join(" ")}</td>
                        <td
                          className="py-1 pr-2 text-ink-dim"
                          title="Track border this contact belongs to (relative to travel direction) · manual marking active at the time"
                        >
                          {t.border ?? "–"}
                          {t.mark ? ` · ${t.mark}` : ""}
                        </td>
                        <td className="py-1 text-right text-ink-dim">
                          {(t.speed_mps * 3.6).toFixed(0)} km/h
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        </div>
      </div>

      <div className="rounded-xl bg-panel p-3">
        <button
          className="flex w-full items-center justify-between text-left text-xs text-ink-dim hover:text-ink"
          onClick={() => setRawOpen((open) => !open)}
          aria-expanded={rawOpen}
        >
          <span>
            Raw packet inspector — every decoded field, live; changed values
            highlight so anything reacting to what you do on track is easy to spot
          </span>
          <span>{rawOpen ? "▾" : "▸"}</span>
        </button>
        {rawOpen && packet == null && (
          <div className="pt-2 text-sm text-ink-dim">Waiting for a packet…</div>
        )}
        {rawOpen && packet != null && (
          <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 pt-2 font-tabular text-[11px] sm:grid-cols-3 lg:grid-cols-4">
            {Object.keys(packet)
              .sort()
              .map((key) => (
                <div
                  key={key}
                  className={`flex justify-between gap-2 rounded px-1 ${
                    changedKeys.has(key) ? "bg-warn/15 text-warn" : "text-ink-dim"
                  }`}
                >
                  <span className="truncate">{key}</span>
                  <span className="truncate text-right text-ink">
                    {formatPacketValue(key, packet[key])}
                  </span>
                </div>
              ))}
          </div>
        )}
      </div>
    </div>
  );
}

// Compact one-line rendering for the packet inspector; flags also show
// binary so individual (possibly undocumented) bits are readable.
function formatPacketValue(key: string, value: unknown): string {
  if (key === "flags" && typeof value === "number") {
    return `${value} (0b${value.toString(2).padStart(16, "0")})`;
  }
  if (typeof value === "number") {
    return Number.isInteger(value) ? String(value) : value.toFixed(4);
  }
  if (Array.isArray(value)) {
    return value
      .map((v) => (typeof v === "number" && !Number.isInteger(v) ? v.toFixed(3) : String(v)))
      .join(", ");
  }
  if (value === null || value === undefined) return "–";
  return String(value);
}
