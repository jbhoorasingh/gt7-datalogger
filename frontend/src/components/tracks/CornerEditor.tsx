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
import { api, type TrackBundleDoc } from "@/lib/api";
import { roadQuads } from "@/lib/surveyGeometry";
import type { AuthoredCorner, AuthoredSection, OfficialMatch } from "@/lib/types";
import { toast } from "@/store/toasts";

const toastSuccess = (text: string) => toast(text, "success");
const toastError = (text: string) => toast(text, "error");

const LEFT_BORDER_COLOR = "#60a5fa";
const RIGHT_BORDER_COLOR = "#f472b6";
const ROAD_FILL = "rgba(148, 163, 184, 0.16)";
const APEX_COLOR = "#facc15";
const SELECTED_COLOR = "#f97316";
const SECTION_COLOR = "#34d399";
const ENTRY_EXIT_COLOR = "#94a3b8";

// What the next click on the map does. Placing is modal because a corner has
// three anchors and a phone has one finger — a drag-handle affordance would
// need a mouse the pit-wall tablet does not have.
type PlaceMode = "apex" | "move-apex" | "entry" | "exit" | "section-start" | "section-end";

const MODE_HELP: Record<PlaceMode, string> = {
  apex: "Click the map for each corner, in track order. Name them as you go.",
  "move-apex": "Click where this corner's apex should be.",
  entry: "Click where the corner starts — turn-in.",
  exit: "Click where the corner ends — the exit.",
  "section-start": "Click where this section begins.",
  "section-end": "Click where this section ends.",
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
  const [doc, setDoc] = useState<TrackBundleDoc | null>(null);
  const [error, setError] = useState("");
  const [corners, setCorners] = useState<AuthoredCorner[]>([]);
  const [sections, setSections] = useState<AuthoredSection[]>([]);
  const [selected, setSelected] = useState(-1);
  const [selectedSection, setSelectedSection] = useState(-1);
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
    if (mode === "section-start" || mode === "section-end") {
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
  }, [doc, corners, sections, selected, extents]);

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
      });
      setCorners(result.corners);
      setSections(result.sections);
      setDirty(false);
      toastSuccess(`Saved ${result.corners.length} corners on ${result.track}`);
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
        <h3 className="text-sm font-semibold">Corners — {trackName}</h3>
        {turns > 0 && (
          <span
            className={`rounded-full px-2 py-0.5 text-xs ${
              labelled >= turns ? "bg-throttle/15 text-throttle" : "bg-panel-2 text-ink-dim"
            }`}
            title="The official catalog knows this layout's turn count; it is the only outside check on whether the labelling is finished"
          >
            {labelled} of {turns} labelled
          </span>
        )}
        {turns === 0 && (
          <span className="text-xs text-ink-dim">{labelled} labelled</span>
        )}
        <div className="ml-auto flex items-center gap-2">
          <button className="btn" disabled={!dirty || saving} onClick={() => void save()}>
            {saving ? "Saving…" : "Save"}
          </button>
          <button className="btn" onClick={onClose}>
            Close
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
              {mode === "apex" ? "Placing apexes…" : "Place corners"}
            </button>
            <span>
              {mode
                ? MODE_HELP[mode]
                : "Scroll to zoom, drag to pan. Pick a corner to edit it."}
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
              No corners yet. Hit <b>Place corners</b> and click your way around the
              map, starting with the first corner after the start line.
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
                  placeholder="name (optional)"
                  onChange={(e) => update(i, { name: e.target.value })}
                  className="min-w-0 flex-1 rounded border border-edge bg-panel-2 px-1.5 py-1 text-xs text-ink"
                />
                <button
                  className="rounded border border-edge px-1.5 py-1 text-xs text-ink-dim hover:text-ink"
                  title={c.direction === "L" ? "left-hander" : c.direction === "R" ? "right-hander" : "direction unset"}
                  onClick={() =>
                    update(i, {
                      direction: c.direction === null ? "L" : c.direction === "L" ? "R" : null,
                    })
                  }
                >
                  {c.direction ?? "–"}
                </button>
              </div>
              {i === selected && (
                <div className="mt-1.5 flex flex-wrap items-center gap-1 text-xs">
                  <button
                    className={`btn ${mode === "move-apex" ? "border-accent text-accent" : ""}`}
                    title="Click the map to move this corner's apex"
                    onClick={() => setMode(mode === "move-apex" ? null : "move-apex")}
                  >
                    apex
                  </button>
                  <button
                    className={`btn ${mode === "entry" ? "border-accent text-accent" : ""}`}
                    onClick={() => setMode(mode === "entry" ? null : "entry")}
                  >
                    {c.entry ? "entry ✓" : "entry"}
                  </button>
                  <button
                    className={`btn ${mode === "exit" ? "border-accent text-accent" : ""}`}
                    onClick={() => setMode(mode === "exit" ? null : "exit")}
                  >
                    {c.exit ? "exit ✓" : "exit"}
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
                    Remove
                  </button>
                </div>
              )}
            </div>
          ))}

          <div className="border-t border-edge pt-2">
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold">Sections</span>
              <span className="text-xs text-ink-dim" title="Real sectors (#22) need somewhere to come from; GT7 broadcasts none">
                optional
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
                Add
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
                    placeholder={`section ${i + 1}`}
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
                    start
                  </button>
                  <button
                    className={`btn ${mode === "section-end" && i === selectedSection ? "border-accent text-accent" : ""}`}
                    onClick={() => {
                      setSelectedSection(i);
                      setMode("section-end");
                    }}
                  >
                    end
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
        </div>
      </div>
    </div>
  );
}
