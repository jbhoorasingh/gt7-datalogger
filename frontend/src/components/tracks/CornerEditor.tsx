// The refine view (#48): walk a surveyed track and label its corners.
//
// `detect_corners()` re-infers corners from every lap's curvature, so a lap
// that carries less speed through a shallow bend may not register it at all —
// and every corner after it renumbers. That makes "turn 4" mean a different
// piece of tarmac from one lap to the next, which is no foundation for a
// per-corner report card (#21) or real sectors (#22). Labelling them once, by
// hand, against the surveyed map fixes them in place: authored corners are
// anchored to world POSITIONS (distance depends on the racing line taken) and
// live in the bundle, so they travel with export/import.
//
// The map is drawn from the bundle's own border points — the same geometry the
// Survey view builds — because that is the only picture of the circuit that
// exists before a corner has a number.

import type * as echarts from "echarts";
import type { EChartsOption, SeriesOption } from "echarts";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { EChart } from "@/components/EChart";
import { Select } from "@/components/ui/Select";
import { rich, useT, type MessageKey } from "@/i18n";
import { api, type TrackBundleDoc } from "@/lib/api";
import { formatLapTime } from "@/lib/format";
import { boundariesFromSplits, formatClock, parseClock } from "@/lib/sectorSplits";
import { roadQuads } from "@/lib/surveyGeometry";
import type {
  AuthoredCorner,
  AuthoredSection,
  AuthoredSector,
  LapSummary,
  OfficialMatch,
} from "@/lib/types";
import { toast } from "@/store/toasts";

const toastSuccess = (text: string) => toast(text, "success");
const toastError = (text: string) => toast(text, "error");

const LEFT_BORDER_COLOR = "#60a5fa";
const RIGHT_BORDER_COLOR = "#f472b6";
const ROAD_FILL = "rgba(148, 163, 184, 0.16)";
const APEX_COLOR = "#facc15";
const SELECTED_COLOR = "#f97316";
const SECTION_COLOR = "#34d399";
const SECTOR_COLOR = "#3ec9ff";
const ENTRY_EXIT_COLOR = "#94a3b8";

// What the next click on the map does. Placing is modal because a corner has
// three anchors and a phone has one finger — a drag-handle affordance would
// need a mouse the pit-wall tablet does not have.
type PlaceMode =
  | "apex"
  | "move-apex"
  | "entry"
  | "exit"
  | "section-start"
  | "section-end"
  | "sector-end";

const MODE_HELP_KEY: Record<PlaceMode, MessageKey> = {
  apex: "cornerEditor.mode.apex",
  "move-apex": "cornerEditor.mode.moveApex",
  entry: "cornerEditor.mode.entry",
  exit: "cornerEditor.mode.exit",
  "section-start": "cornerEditor.mode.sectionStart",
  "section-end": "cornerEditor.mode.sectionEnd",
  "sector-end": "cornerEditor.mode.sectorEnd",
};

function blank(n: number): AuthoredCorner {
  return { n, name: "", direction: null, apex: { x: 0, z: 0 }, entry: null, exit: null, note: "" };
}

export function CornerEditor({
  slug,
  trackName,
  official,
  onClose,
  onSaved,
}: {
  slug: string;
  trackName: string;
  official: OfficialMatch | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const t = useT();
  const [doc, setDoc] = useState<TrackBundleDoc | null>(null);
  const [error, setError] = useState("");
  const [corners, setCorners] = useState<AuthoredCorner[]>([]);
  const [sections, setSections] = useState<AuthoredSection[]>([]);
  // Sector boundaries (#22): where each sector ends; the last runs to the
  // line. The sector times in Analysis are read off them.
  const [sectors, setSectors] = useState<AuthoredSector[]>([]);
  const [selected, setSelected] = useState(-1);
  const [selectedSection, setSelectedSection] = useState(-1);
  const [selectedSector, setSelectedSector] = useState(-1);
  // Boundaries from a lap's split times: the race screen showed S1 as
  // 0:25 on a 1:00 lap, so the first sector ends where that lap was 25 s
  // in. Typed as the game shows them, one per sector but the last.
  const [splitsOpen, setSplitsOpen] = useState(false);
  const [splitLaps, setSplitLaps] = useState<LapSummary[] | null>(null);
  const [splitLapId, setSplitLapId] = useState<number | null>(null);
  const [splitTimes, setSplitTimes] = useState<string[]>(["", ""]);
  const [splitBusy, setSplitBusy] = useState(false);
  const [splitError, setSplitError] = useState("");

  useEffect(() => {
    if (!splitsOpen || splitLaps != null) return;
    let cancelled = false;
    api.laps(trackName)
      .then((all) => {
        if (cancelled) return;
        // Newest first; a lap without samples has no path to place on.
        const laps = all
          .filter((lap) => (lap.total_ticks ?? 0) > 0 && lap.time_ms > 0)
          .sort((a, b) => b.id - a.id)
          .slice(0, 40);
        setSplitLaps(laps);
        setSplitLapId((cur) => cur ?? laps[0]?.id ?? null);
      })
      .catch(() => !cancelled && setSplitLaps([]));
    return () => {
      cancelled = true;
    };
  }, [splitsOpen, splitLaps, trackName]);

  const splitLap = splitLaps?.find((lap) => lap.id === splitLapId) ?? null;
  const splitMs = splitTimes.map((text) => (text.trim() ? parseClock(text) : null));
  const splitTotal = splitMs.reduce<number>((sum, ms) => sum + (ms ?? 0), 0);

  const placeFromSplits = async () => {
    if (splitLap == null) return;
    const times = splitMs.map((ms) => ms ?? 0);
    if (times.some((ms) => !(ms > 0))) {
      setSplitError(t("cornerEditor.splits.needTimes"));
      return;
    }
    setSplitBusy(true);
    setSplitError("");
    try {
      const detail = await api.lapDetail(splitLap.id);
      const samples = (detail.samples ?? {}) as Record<string, number[]>;
      const result = boundariesFromSplits(
        {
          t: samples.t ?? [],
          pos_x: samples.pos_x ?? [],
          pos_z: samples.pos_z ?? [],
          time_ms: splitLap.time_ms,
        },
        times,
      );
      if (!result.ok) {
        setSplitError(result.error);
        return;
      }
      // Replaces the list: these are the circuit's sectors now. Names
      // typed for the old ones carry over by position.
      setSectors((prev) =>
        result.boundaries.map((b, i) => ({
          n: i + 1,
          name: prev[i]?.name ?? "",
          end: { x: b.x, z: b.z },
        })),
      );
      setSelectedSector(-1);
      setMode(null);
      setDirty(true);
      setSplitsOpen(false);
      toastSuccess(
        t("cornerEditor.splits.placed", {
          count: result.boundaries.length + 1,
          lap: String(splitLap.number),
          last: formatClock(result.remainder_ms),
        }),
      );
    } catch (e) {
      setSplitError((e as Error).message);
    } finally {
      setSplitBusy(false);
    }
  };
  const [mode, setMode] = useState<PlaceMode | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.bundles
      .get(slug)
      .then((d) => {
        if (cancelled) return;
        setDoc(d);
        setCorners(d.corners);
        setSections(d.sections);
        setSectors(d.sectors ?? []);
      })
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [slug]);

  // Placement handler lives in a ref: registering the ECharts click listener
  // once and reading current state through the ref avoids re-subscribing (and
  // double-firing) on every keystroke in the name field.
  const place = useRef<(x: number, z: number) => void>(() => {});
  place.current = (x: number, z: number) => {
    if (mode === null) return;
    const at = { x: Math.round(x * 10) / 10, z: Math.round(z * 10) / 10 };
    setDirty(true);
    if (mode === "sector-end") {
      setSectors((prev) => prev.map((s, i) => (i !== selectedSector ? s : { ...s, end: at })));
    } else if (mode === "section-start" || mode === "section-end") {
      setSections((prev) =>
        prev.map((s, i) =>
          i !== selectedSection
            ? s
            : { ...s, [mode === "section-start" ? "start" : "end"]: at },
        ),
      );
    } else if (mode === "apex") {
      // Apex mode stays armed and always APPENDS: labelling a track is one
      // trip around the map, and a click that silently moved the corner you
      // just placed would make that impossible. Repositioning is its own
      // one-shot mode, on the corner you picked.
      setCorners((prev) => {
        const next = [...prev, { ...blank(prev.length + 1), apex: at }];
        setSelected(next.length - 1);
        return next;
      });
      return;
    } else if (selected >= 0) {
      setCorners((prev) =>
        prev.map((c, i) =>
          i !== selected
            ? c
            : mode === "move-apex"
              ? { ...c, apex: at }
              : { ...c, [mode]: at },
        ),
      );
    }
    setMode(null); // one-shot: everything except placing new corners
  };

  const onInit = useCallback((chart: echarts.ECharts) => {
    chartRef.current = chart;
    // zr-level click, not series click: most of the map is empty space and
    // that is exactly where a corner apex usually needs to go.
    chart.getZr().on("click", (event) => {
      const point = chart.convertFromPixel({ gridIndex: 0 }, [event.offsetX, event.offsetY]);
      if (point) place.current(point[0], point[1]);
    });
  }, []);

  const extents = useMemo(() => {
    if (!doc || doc.edges.length === 0) return null;
    let minX = Infinity;
    let maxX = -Infinity;
    let minZ = Infinity;
    let maxZ = -Infinity;
    for (const e of doc.edges) {
      minX = Math.min(minX, e.x);
      maxX = Math.max(maxX, e.x);
      minZ = Math.min(minZ, e.z);
      maxZ = Math.max(maxZ, e.z);
    }
    const cx = (minX + maxX) / 2;
    const cz = (minZ + maxZ) / 2;
    const half = Math.max(maxX - cx, maxZ - cz, 50) * 1.1;
    return { x: [cx - half, cx + half], z: [cz - half, cz + half] };
  }, [doc]);

  const option = useMemo<EChartsOption>(() => {
    const edges = doc?.edges ?? [];
    const series: SeriesOption[] = [
      {
        id: "road-fill",
        type: "custom",
        data: roadQuads(edges),
        renderItem: (_p, apiCustom) => ({
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
        z: 1,
      },
      {
        id: "borders",
        type: "scatter",
        data: edges.map((e) => [e.x, e.z, e.side === "R" ? 1 : 0]),
        symbolSize: 2,
        itemStyle: {
          color: (p) =>
            (p.value as number[])[2] === 1 ? RIGHT_BORDER_COLOR : LEFT_BORDER_COLOR,
        },
        progressive: 0,
        silent: true,
        z: 2,
      },
      {
        // Entry/exit anchors of the selected corner, joined to its apex.
        id: "anchors",
        type: "scatter",
        data: (selected >= 0 && corners[selected]
          ? [corners[selected].entry, corners[selected].exit]
          : []
        )
          .filter((p): p is { x: number; z: number } => p != null)
          .map((p) => [p.x, p.z]),
        symbol: "rect",
        symbolSize: 7,
        itemStyle: { color: ENTRY_EXIT_COLOR },
        silent: true,
        z: 4,
      },
      {
        id: "sections",
        type: "scatter",
        data: sections.flatMap((s) => [
          [s.start.x, s.start.z],
          [s.end.x, s.end.z],
        ]),
        symbol: "triangle",
        symbolSize: 9,
        itemStyle: { color: SECTION_COLOR },
        silent: true,
        z: 4,
      },
      {
        // Sector boundaries: a bar across the road where each sector ends.
        id: "sectors",
        type: "scatter",
        data: sectors.map((s, i) => ({
          value: [s.end.x, s.end.z],
          itemStyle: { color: i === selectedSector ? SELECTED_COLOR : SECTOR_COLOR },
        })),
        symbol: "rect",
        symbolSize: [4, 16],
        label: {
          show: true,
          position: "top",
          color: "#e5e7eb",
          fontSize: 10,
          fontWeight: "bold",
          formatter: (p) => `S${p.dataIndex + 1}`,
        },
        silent: true,
        z: 4,
      },
      {
        id: "apexes",
        type: "scatter",
        data: corners.map((c, i) => ({
          value: [c.apex.x, c.apex.z],
          itemStyle: { color: i === selected ? SELECTED_COLOR : APEX_COLOR },
        })),
        symbolSize: 12,
        label: {
          show: true,
          position: "top",
          color: "#e5e7eb",
          fontSize: 10,
          fontWeight: "bold",
          formatter: (p) => {
            const c = corners[p.dataIndex];
            return c.name ? `${c.n} ${c.name}` : String(c.n);
          },
        },
        z: 5,
      },
    ];
    if (doc?.finish_crossings.length) {
      const f = doc.finish_crossings[0];
      series.push({
        id: "finish",
        type: "scatter",
        data: [[f.x, f.z]],
        symbol: "diamond",
        symbolSize: 12,
        itemStyle: { color: "#e5e7eb" },
        label: { show: true, position: "bottom", formatter: "S/F", color: "#e5e7eb",
                 fontSize: 10 },
        silent: true,
        z: 4,
      });
    }
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
        inverse: true, // GT7's own view renders z inverted
        ...(extents ? { min: extents.z[0], max: extents.z[1] } : { scale: true }),
      },
      dataZoom: [
        { id: "zoom-x", type: "inside", xAxisIndex: 0, filterMode: "none" },
        { id: "zoom-z", type: "inside", yAxisIndex: 0, filterMode: "none" },
      ],
      tooltip: { show: false },
      series,
    };
  }, [doc, corners, sections, sectors, selected, selectedSector, extents]);

  const update = (i: number, patch: Partial<AuthoredCorner>) => {
    setCorners((prev) => prev.map((c, j) => (j === i ? { ...c, ...patch } : c)));
    setDirty(true);
  };

  const move = (i: number, delta: number) => {
    const j = i + delta;
    if (j < 0 || j >= corners.length) return;
    const next = [...corners];
    [next[i], next[j]] = [next[j], next[i]];
    setCorners(next.map((c, k) => ({ ...c, n: k + 1 })));
    setSelected(j);
    setDirty(true);
  };

  const save = async () => {
    setSaving(true);
    try {
      // Renumbered from list order on the way out: corner 1 is the first
      // after the start line, and the list is what the user just ordered.
      const result = await api.bundles.setCorners(slug, {
        corners: corners.map((c, i) => ({ ...c, n: i + 1 })),
        sections: sections.map((s, i) => ({ ...s, n: i + 1 })),
        sectors: sectors.map((s, i) => ({ ...s, n: i + 1 })),
      });
      setCorners(result.corners);
      setSections(result.sections);
      setSectors(result.sectors ?? []);
      setDirty(false);
      const saved = result.sectors ?? [];
      const cornerCount = t("count.corners", { count: result.corners.length });
      toastSuccess(
        saved.length > 0
          ? t("cornerEditor.savedWithSectors", {
              corners: cornerCount,
              sectors: t("count.sectors", { count: saved.length + 1 }),
              track: result.track,
            })
          : t("cornerEditor.saved", { corners: cornerCount, track: result.track }),
      );
      onSaved();
    } catch (e) {
      toastError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const labelled = corners.length;
  const turns = official?.turns ?? 0;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">{t("cornerEditor.title", { track: trackName })}</h3>
        {turns > 0 && (
          <span
            className={`rounded-full px-2 py-0.5 text-xs ${
              labelled >= turns ? "bg-throttle/15 text-throttle" : "bg-panel-2 text-ink-dim"
            }`}
            title={t("cornerEditor.turnCountTitle")}
          >
            {t("cornerEditor.labelledOf", { labelled, turns })}
          </span>
        )}
        {turns === 0 && (
          <span className="text-xs text-ink-dim">{t("cornerEditor.labelled", { count: labelled })}</span>
        )}
        <div className="ml-auto flex items-center gap-2">
          <button className="btn" disabled={!dirty || saving} onClick={() => void save()}>
            {saving ? t("common.saving") : t("common.save")}
          </button>
          <button className="btn" onClick={onClose}>
            {t("common.close")}
          </button>
        </div>
      </div>

      {error && <div className="text-xs text-brake">{error}</div>}

      <div className="grid gap-3 lg:grid-cols-[1fr_20rem]">
        <div className="panel p-2">
          {/* The instruction sits ABOVE the map: the map is square and eats a
              screen's worth of height, so anything under it is off-screen at
              the moment it is needed. */}
          <div className="flex flex-wrap items-center gap-2 px-1 pb-2 text-xs text-ink-dim">
            <button
              className={`btn ${mode === "apex" ? "border-accent text-accent" : ""}`}
              onClick={() => {
                setMode(mode === "apex" ? null : "apex");
                if (mode !== "apex") setSelected(-1);
              }}
            >
              {mode === "apex" ? t("cornerEditor.placingApexes") : t("cornerEditor.placeCorners")}
            </button>
            <span>
              {mode ? t(MODE_HELP_KEY[mode]) : t("cornerEditor.mapHelp")}
            </span>
          </div>
          {/* Square, because the axes carry equal x/z spans — a stretched box
              would render the circuit out of shape. Capped so the controls
              stay on screen next to it. */}
          <EChart
            option={option}
            onInit={onInit}
            notMerge={false}
            replaceMerge={["series"]}
            className="mx-auto aspect-square w-full max-w-[min(34rem,60vh)]"
          />
        </div>

        <div className="max-h-[32rem] space-y-2 overflow-y-auto panel p-2">
          {corners.length === 0 && (
            <p className="p-2 text-xs text-ink-dim">
              {rich(t("cornerEditor.empty"), { place: <b>{t("cornerEditor.placeCorners")}</b> })}
            </p>
          )}
          {corners.map((c, i) => (
            <div
              key={i}
              className={`rounded-lg border p-2 ${
                i === selected ? "border-accent/60 bg-panel-2" : "border-edge"
              }`}
              onClick={() => setSelected(i)}
            >
              <div className="flex items-center gap-1.5">
                <span className="w-6 shrink-0 font-tabular text-sm">{i + 1}</span>
                <input
                  value={c.name}
                  placeholder={t("cornerEditor.namePlaceholder")}
                  onChange={(e) => update(i, { name: e.target.value })}
                  className="min-w-0 flex-1 rounded border border-edge bg-panel-2 px-1.5 py-1 text-xs text-ink"
                />
                <button
                  className="rounded border border-edge px-1.5 py-1 text-xs text-ink-dim hover:text-ink"
                  title={t(
                    c.direction === "L"
                      ? "cornerEditor.dir.left"
                      : c.direction === "R"
                        ? "cornerEditor.dir.right"
                        : "cornerEditor.dir.unset",
                  )}
                  onClick={() =>
                    update(i, {
                      direction: c.direction === null ? "L" : c.direction === "L" ? "R" : null,
                    })
                  }
                >
                  {c.direction === "L"
                    ? t("cornerEditor.dir.leftShort")
                    : c.direction === "R"
                      ? t("cornerEditor.dir.rightShort")
                      : "–"}
                </button>
              </div>
              {i === selected && (
                <div className="mt-1.5 flex flex-wrap items-center gap-1 text-xs">
                  <button
                    className={`btn ${mode === "move-apex" ? "border-accent text-accent" : ""}`}
                    title={t("cornerEditor.apexTitle")}
                    onClick={() => setMode(mode === "move-apex" ? null : "move-apex")}
                  >
                    {t("cornerEditor.apex")}
                  </button>
                  <button
                    className={`btn ${mode === "entry" ? "border-accent text-accent" : ""}`}
                    onClick={() => setMode(mode === "entry" ? null : "entry")}
                  >
                    {c.entry ? t("cornerEditor.entrySet") : t("cornerEditor.entry")}
                  </button>
                  <button
                    className={`btn ${mode === "exit" ? "border-accent text-accent" : ""}`}
                    onClick={() => setMode(mode === "exit" ? null : "exit")}
                  >
                    {c.exit ? t("cornerEditor.exitSet") : t("cornerEditor.exit")}
                  </button>
                  <button className="btn" onClick={() => move(i, -1)}>
                    ↑
                  </button>
                  <button className="btn" onClick={() => move(i, 1)}>
                    ↓
                  </button>
                  <button
                    className="btn btn-danger ml-auto"
                    // The row selects on click, so without this the parent
                    // re-selects index i straight after the removal — which is
                    // now a DIFFERENT corner, and an armed entry/exit mode
                    // would place its next anchor on the wrong one.
                    onClick={(e) => {
                      e.stopPropagation();
                      setCorners((prev) =>
                        prev.filter((_, j) => j !== i).map((x, k) => ({ ...x, n: k + 1 })),
                      );
                      setSelected(-1);
                      setMode(null);
                      setDirty(true);
                    }}
                  >
                    {t("common.remove")}
                  </button>
                </div>
              )}
            </div>
          ))}

          <div className="border-t border-edge pt-2">
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold">{t("cornerEditor.sections")}</span>
              <span className="text-xs text-ink-dim" title={t("cornerEditor.sectionsTitle")}>
                {t("common.optional")} · {t("cornerEditor.namedStraights")}
              </span>
              <button
                className="btn ml-auto"
                onClick={() => {
                  setSections((prev) => [
                    ...prev,
                    { n: prev.length + 1, name: "", start: { x: 0, z: 0 }, end: { x: 0, z: 0 } },
                  ]);
                  setSelectedSection(sections.length);
                  setMode("section-start");
                  setDirty(true);
                }}
              >
                {t("common.add")}
              </button>
            </div>
            {sections.map((s, i) => (
              <div
                key={i}
                className={`mt-1.5 rounded-lg border p-2 ${
                  i === selectedSection ? "border-accent/60 bg-panel-2" : "border-edge"
                }`}
                onClick={() => setSelectedSection(i)}
              >
                <div className="flex items-center gap-1.5">
                  <input
                    value={s.name}
                    placeholder={t("cornerEditor.sectionN", { n: String(i + 1) })}
                    onChange={(e) => {
                      setSections((prev) =>
                        prev.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)),
                      );
                      setDirty(true);
                    }}
                    className="min-w-0 flex-1 rounded border border-edge bg-panel-2 px-1.5 py-1 text-xs text-ink"
                  />
                  <button
                    className={`btn ${mode === "section-start" && i === selectedSection ? "border-accent text-accent" : ""}`}
                    onClick={() => {
                      setSelectedSection(i);
                      setMode("section-start");
                    }}
                  >
                    {t("cornerEditor.start")}
                  </button>
                  <button
                    className={`btn ${mode === "section-end" && i === selectedSection ? "border-accent text-accent" : ""}`}
                    onClick={() => {
                      setSelectedSection(i);
                      setMode("section-end");
                    }}
                  >
                    {t("cornerEditor.end")}
                  </button>
                  <button
                    className="btn btn-danger"
                    onClick={(e) => {
                      e.stopPropagation();  // as above: the row selects on click
                      setSections((prev) => prev.filter((_, j) => j !== i));
                      setSelectedSection(-1);
                      setMode(null);
                      setDirty(true);
                    }}
                  >
                    ✕
                  </button>
                </div>
              </div>
            ))}
          </div>

          <div className="border-t border-edge pt-2">
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold">{t("tracks.label.sectors")}</span>
              <span className="text-xs text-ink-dim" title={t("cornerEditor.sectorsTitle")}>
                {t("common.optional")} ·{" "}
                {sectors.length === 0
                  ? t("cornerEditor.thirds")
                  : t("count.sectors", { count: sectors.length + 1 })}
              </span>
              <button
                className={`btn ml-auto ${splitsOpen ? "border-accent text-accent" : ""}`}
                title={t("cornerEditor.fromSplitsTitle")}
                onClick={() => setSplitsOpen((open) => !open)}
              >
                {t("cornerEditor.fromSplits")}
              </button>
              <button
                className="btn"
                onClick={() => {
                  setSectors((prev) => [...prev, { n: prev.length + 1, name: "", end: { x: 0, z: 0 } }]);
                  setSelectedSector(sectors.length);
                  setMode("sector-end");
                  setDirty(true);
                }}
              >
                {t("common.add")}
              </button>
            </div>
            {splitsOpen && (
              <div className="mt-1.5 space-y-2 rounded-lg border border-accent/40 bg-panel-2 p-2 text-xs">
                <p className="text-ink-dim">{t("cornerEditor.splits.blurb")}</p>
                {splitLaps == null && <div className="skeleton h-7" />}
                {splitLaps != null && splitLaps.length === 0 && (
                  <div className="text-ink-dim">{t("cornerEditor.splits.noLaps")}</div>
                )}
                {splitLaps != null && splitLaps.length > 0 && (
                  <>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-ink-dim">{t("cornerEditor.splits.lap")}</span>
                      <Select
                        ariaLabel={t("cornerEditor.splits.lapAria")}
                        value={String(splitLapId ?? "")}
                        onValueChange={(v) => setSplitLapId(Number(v))}
                        options={splitLaps.map((lap) => ({
                          value: String(lap.id),
                          label: `${t("cornerEditor.splits.lapOption", {
                            session: String(lap.session_id),
                            lap: String(lap.number),
                            time: formatLapTime(lap.time_ms),
                          })}${lap.car_name ? ` · ${lap.car_name}` : ""}`,
                        }))}
                        className="max-w-full px-2 py-1 text-xs"
                      />
                    </div>
                    <div className="flex flex-wrap items-center gap-1.5">
                      {splitTimes.map((text, i) => (
                        <label key={i} className="inline-flex items-center gap-1">
                          <span className="font-tabular text-ink-dim">S{i + 1}</span>
                          <input
                            value={text}
                            placeholder="0:25.123"
                            inputMode="decimal"
                            onChange={(e) =>
                              setSplitTimes((prev) =>
                                prev.map((x, j) => (j === i ? e.target.value : x)),
                              )
                            }
                            className={`w-[88px] rounded border bg-panel px-1.5 py-1 font-tabular text-xs text-ink ${
                              text.trim() && splitMs[i] == null ? "border-brake" : "border-edge"
                            }`}
                          />
                        </label>
                      ))}
                      <button
                        className="btn"
                        onClick={() => setSplitTimes((prev) => [...prev, ""])}
                        title={t("cornerEditor.splits.addSectorTitle")}
                      >
                        {t("cornerEditor.splits.addSector")}
                      </button>
                      {splitTimes.length > 1 && (
                        <button
                          className="btn"
                          onClick={() => setSplitTimes((prev) => prev.slice(0, -1))}
                        >
                          {t("cornerEditor.splits.removeSector")}
                        </button>
                      )}
                    </div>
                    <div className="font-tabular text-ink-dim">
                      {splitLap && splitTotal > 0
                        ? splitTotal < splitLap.time_ms
                          ? t("cornerEditor.splits.remainder", {
                              n: String(splitTimes.length + 1),
                              left: formatClock(splitLap.time_ms - splitTotal),
                              lap: formatLapTime(splitLap.time_ms),
                            })
                          : t("cornerEditor.splits.over", {
                              total: formatClock(splitTotal),
                              lap: formatLapTime(splitLap.time_ms),
                            })
                        : t("count.sectors", { count: splitTimes.length + 1 })}
                    </div>
                    {splitError && <div className="text-brake">{splitError}</div>}
                    <div className="flex gap-2">
                      <button
                        className="btn btn-primary"
                        disabled={splitBusy || splitLap == null || splitMs.some((ms) => !(ms != null && ms > 0))}
                        onClick={() => void placeFromSplits()}
                      >
                        {splitBusy
                          ? t("cornerEditor.splits.placing")
                          : sectors.length > 0
                            ? t("cornerEditor.splits.replace")
                            : t("cornerEditor.splits.place")}
                      </button>
                      <button className="btn" onClick={() => setSplitsOpen(false)}>
                        {t("common.cancel")}
                      </button>
                    </div>
                  </>
                )}
              </div>
            )}
            {sectors.map((s, i) => (
              <div
                key={i}
                className={`mt-1.5 rounded-lg border p-2 ${
                  i === selectedSector ? "border-accent/60 bg-panel-2" : "border-edge"
                }`}
                onClick={() => setSelectedSector(i)}
              >
                <div className="flex items-center gap-1.5">
                  <span className="w-6 shrink-0 font-tabular text-xs">S{i + 1}</span>
                  <input
                    value={s.name}
                    placeholder={t("cornerEditor.namePlaceholder")}
                    onChange={(e) => {
                      setSectors((prev) =>
                        prev.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)),
                      );
                      setDirty(true);
                    }}
                    className="min-w-0 flex-1 rounded border border-edge bg-panel-2 px-1.5 py-1 text-xs text-ink"
                  />
                  <button
                    className={`btn ${mode === "sector-end" && i === selectedSector ? "border-accent text-accent" : ""}`}
                    title={t("cornerEditor.sectorEndTitle")}
                    onClick={() => {
                      setSelectedSector(i);
                      setMode("sector-end");
                    }}
                  >
                    {s.end.x === 0 && s.end.z === 0 ? t("cornerEditor.endsAt") : t("cornerEditor.endsSet")}
                  </button>
                  <button
                    className="btn btn-danger"
                    onClick={(e) => {
                      e.stopPropagation(); // as above: the row selects on click
                      setSectors((prev) => prev.filter((_, j) => j !== i));
                      setSelectedSector(-1);
                      setMode(null);
                      setDirty(true);
                    }}
                  >
                    ✕
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
