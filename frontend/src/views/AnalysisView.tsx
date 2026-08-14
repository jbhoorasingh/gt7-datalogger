// Analysis view: multi-lap comparison with synced cursors, race line map,
// consistency (deviation), fuel strategy, and tuning info. The lap selection
// can arrive via deep link (#/analysis?session=…&laps=…&ref=…) from the
// Sessions or Live views, and is mirrored back into the URL for sharing.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChannelPicker } from "@/components/analysis/ChannelPicker";
import { CornerDetail, type CornerLap } from "@/components/analysis/CornerDetail";
import { DeviationChart } from "@/components/analysis/DeviationChart";
import { FuelMapPanel } from "@/components/analysis/FuelMapPanel";
import { GearingPanel } from "@/components/analysis/GearingPanel";
import { GGDiagram, ggLap, type GGLap } from "@/components/analysis/GGDiagram";
import { RaceLineMap, type MapLap } from "@/components/analysis/RaceLineMap";
import { StackedCharts } from "@/components/analysis/StackedCharts";
import { Select } from "@/components/ui/Select";
import { Tip } from "@/components/ui/Tooltip";
import { api } from "@/lib/api";
import {
  CHANNEL_BY_KEY,
  columnsForChannels,
  isDefaultChannelSet,
  loadChannelKeys,
  saveChannelKeys,
} from "@/lib/channels";
import { lapColor, lapColorMap } from "@/lib/colors";
import { formatLapTime, formatSpeed } from "@/lib/format";
import {
  openInAnalysis,
  reflectAnalysisSelection,
  type AnalysisRequest,
} from "@/lib/router";
import type {
  CategoryBest,
  CompareResult,
  DeviationResult,
  LapSummary,
  SessionSummary,
  TrackOutline,
} from "@/lib/types";
import { useAnalysisSelection } from "@/store/analysis";
import { useSettings } from "@/store/settings";
import { useTelemetry } from "@/store/telemetry";

// The Corner Detail widget always needs the per-corner columns, whatever the
// chart picker says.
const CORNER_COLUMNS = [
  "slip_fl", "slip_fr", "slip_rl", "slip_rr",
  "tt_fl", "tt_fr", "tt_rl", "tt_rr",
  "sus_fl", "sus_fr", "sus_rl", "sus_rr",
  "throttle", "brake",
];

// The race-line map shades where wheels touched kerb/grass/gravel whenever
// the lap carries the per-tick surface column (packet C recordings).
const MAP_COLUMNS = ["surface"];

// The g-g diagram is always shown when the recording has an accelerometer, so
// its columns ride along regardless of the chart picker (#16).
const GG_COLUMNS = ["acc_lat", "acc_long"];

export function AnalysisView({ request }: { request: AnalysisRequest }) {
  const units = useSettings((s) => s.units);
  const lapEpoch = useTelemetry((s) => s.lapEpoch);

  // Seed from the shared selection so switching tabs doesn't reset the view.
  const stored = useRef(useAnalysisSelection.getState()).current;
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);
  const [sessionId, setSessionId] = useState<number | null>(request.session ?? stored.sessionId);
  const [laps, setLaps] = useState<LapSummary[]>([]);
  const [selected, setSelected] = useState<number[]>(request.laps ?? stored.selectedLapIds);
  const [refLap, setRefLap] = useState<number | null>(request.ref ?? stored.refLapId);
  const [compare, setCompare] = useState<CompareResult | null>(null);
  const [deviation, setDeviation] = useState<DeviationResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [channelKeys, setChannelKeys] = useState<string[]>(
    () => request.channels?.filter((k) => k in CHANNEL_BY_KEY) ?? loadChannelKeys(),
  );
  useEffect(() => saveChannelKeys(channelKeys), [channelKeys]);
  const channelDefs = useMemo(
    () => channelKeys.map((k) => CHANNEL_BY_KEY[k]).filter(Boolean),
    [channelKeys],
  );

  // Until the user (or a deep link) picks laps, selection follows
  // "latest vs best" as new laps arrive live.
  const manualSelection = useRef(
    (request.laps ?? stored.selectedLapIds).length > 0 || (request.ref ?? stored.refLapId) != null,
  );

  // Apply a new deep link while the view is mounted (pasted URL).
  const requestKey = `${request.session ?? ""}|${(request.laps ?? []).join(",")}|${request.ref ?? ""}|${(request.channels ?? []).join(",")}`;
  const firstRequest = useRef(true);
  useEffect(() => {
    if (firstRequest.current) {
      firstRequest.current = false; // initial state already covers the mount
      return;
    }
    if (request.session == null && request.laps == null && request.ref == null) return;
    if (request.session != null) setSessionId(request.session);
    if (request.laps != null || request.ref != null) {
      manualSelection.current = true;
      const lapIds = request.laps ?? [];
      const withRef =
        request.ref != null && !lapIds.includes(request.ref) ? [...lapIds, request.ref] : lapIds;
      if (withRef.length > 0) setSelected(withRef);
      if (request.ref != null) setRefLap(request.ref);
    }
    if (request.channels != null) {
      setChannelKeys(request.channels.filter((k) => k in CHANNEL_BY_KEY));
    }
    // Deliberately keyed on the serialized request only — re-running on every
    // object identity change would clobber in-view selection edits.
  }, [requestKey]);

  // Load sessions (refreshed when a new lap arrives live)
  useEffect(() => {
    api.sessions().then((s) => {
      setSessions(s);
      // Default to the newest session that actually has laps to chart.
      setSessionId((cur) => cur ?? s.find((x) => x.lap_count > 0)?.id ?? s[0]?.id ?? null);
    }).catch(() => setError("Could not load sessions"));
  }, [lapEpoch]);

  // Load laps for the chosen session.
  useEffect(() => {
    if (sessionId == null) return;
    api.sessionLaps(sessionId).then((all) => {
      // Laps without samples (phantoms from menu/replay flicker in old
      // recordings) have nothing to chart — keep them out of the picker.
      // Unknown tick counts are treated as empty, not as chartable.
      const ls = all.filter((lap) => (lap.total_ticks ?? 0) > 0);
      setLaps(ls);
      if (ls.length === 0) return;
      const best = [...ls].sort((a, b) => a.time_ms - b.time_ms)[0];
      const latest = ls[0]; // list is newest-first
      setSelected((cur) => {
        const stillValid = cur.filter((id) => ls.some((l) => l.id === id));
        return manualSelection.current && stillValid.length > 0
          ? stillValid
          : [...new Set([latest.id, best.id])];
      });
      setRefLap((cur) =>
        manualSelection.current && cur && ls.some((l) => l.id === cur) ? cur : best.id,
      );
    }).catch(() => setError("Could not load laps"));
  }, [sessionId, lapEpoch]);

  // Publish the resolved selection: shared store (tab switches) + URL (sharing).
  const setSharedSelection = useAnalysisSelection((s) => s.setSelection);
  useEffect(() => {
    if (sessionId == null || refLap == null || selected.length === 0) return;
    setSharedSelection({ sessionId, selectedLapIds: selected, refLapId: refLap });
    reflectAnalysisSelection({
      session: sessionId,
      laps: selected,
      ref: refLap,
      channels: isDefaultChannelSet(channelKeys) ? undefined : channelKeys,
    });
  }, [sessionId, selected, refLap, channelKeys, setSharedSelection]);

  // Fetch comparison + deviation when the selection or channel set changes.
  // The request always carries the per-corner columns for the Corner Detail
  // widget on top of whatever the picked panels need.
  const requestColumns = useMemo(
    () => [
      ...new Set([
        ...columnsForChannels(channelKeys),
        ...CORNER_COLUMNS,
        ...MAP_COLUMNS,
        ...GG_COLUMNS,
      ]),
    ],
    [channelKeys],
  );
  useEffect(() => {
    if (refLap == null || selected.length === 0) {
      setCompare(null);
      return;
    }
    setLoading(true);
    api.compare(selected, refLap, requestColumns)
      .then((c) => {
        setCompare(c);
        setError(null);
      })
      .catch(() => setError("Comparison failed"))
      .finally(() => setLoading(false));
  }, [selected, refLap, requestColumns]);

  useEffect(() => {
    if (sessionId == null) return;
    api.deviation(sessionId).then(setDeviation).catch(() => setDeviation(null));
  }, [sessionId, lapEpoch]);

  // The surveyed road under the race line (#51). Keyed on the reference lap
  // because that is what resolves the circuit; a track that was never
  // surveyed answers with an empty outline and the map draws as it always did.
  const [outline, setOutline] = useState<TrackOutline | null>(null);
  useEffect(() => {
    if (refLap == null) {
      setOutline(null);
      return;
    }
    let live = true;
    api.trackOutline(refLap)
      .then((o) => live && setOutline(o))
      .catch(() => live && setOutline(null));
    return () => {
      live = false;
    };
  }, [refLap]);

  // Synchronized zoom state across all charts (minDist, maxDist in meters)
  const [zoomRange, setZoomRange] = useState<[number, number] | null>(null);

  // Reset zoom window when session or lap selection changes
  useEffect(() => {
    setZoomRange(null);
  }, [sessionId, selected, refLap]);

  // Cursor sync (rAF-throttled to keep hover smooth)
  const [cursorDist, setCursorDist] = useState<number | null>(null);
  const pendingCursor = useRef<number | null>(null);
  const rafId = useRef(0);
  const onCursorDist = useCallback((d: number | null) => {
    pendingCursor.current = d;
    if (!rafId.current) {
      rafId.current = requestAnimationFrame(() => {
        rafId.current = 0;
        setCursorDist(pendingCursor.current);
      });
    }
  }, []);

  const lapLabels = useMemo(() => {
    const labels: Record<string, string> = {};
    for (const lap of laps) {
      labels[String(lap.id)] = `L${lap.number} · ${formatLapTime(lap.time_ms)}${
        lap.id === refLap ? " (ref)" : ""
      }`;
    }
    return labels;
  }, [laps, refLap]);

  const refEntry = compare?.laps[String(refLap)];
  const refSummary = laps.find((l) => l.id === refLap);
  const session = sessions?.find((s) => s.id === sessionId) ?? null;

  // Category best at this circuit (#19): a lap is worth judging against the
  // fastest one ever set here IN THE SAME CLASS — a Gr.3 time and an N100
  // time around the same corners are not the same achievement. Needs both a
  // named circuit and a category, so it stays absent on unnamed tracks and on
  // recordings made before packet C.
  const [categoryBest, setCategoryBest] = useState<CategoryBest | null>(null);
  const bestTrack = session?.track_name ?? "";
  const bestCategory = session?.car_category ?? "";
  useEffect(() => {
    if (!bestTrack || !bestCategory) {
      setCategoryBest(null);
      return;
    }
    let live = true;
    api.categoryBest(bestTrack, bestCategory)
      .then((b) => live && setCategoryBest(b))
      .catch(() => live && setCategoryBest(null));
    return () => {
      live = false;
    };
  }, [bestTrack, bestCategory, lapEpoch]);

  // One color assignment for everything that shows the compared laps together
  // (chips, chart series, map, corner detail): id-keyed, but two selected laps
  // never share a color (laps 6 apart otherwise would — latest vs best hits it).
  const lapColors = useMemo<Record<string, string>>(() => {
    const ids = new Set<number>(selected);
    if (refLap != null) ids.add(refLap);
    for (const id of Object.keys(compare?.laps ?? {})) ids.add(Number(id));
    return Object.fromEntries(
      [...lapColorMap(ids)].map(([id, color]) => [String(id), color]),
    );
  }, [selected, refLap, compare]);
  const colorOf = (id: string | number) =>
    lapColors[String(id)] ?? lapColor(Number(id));

  // Laps for the track map, colored exactly like the chart series.
  const mapLaps = useMemo<MapLap[]>(() => {
    if (!compare) return [];
    return Object.keys(compare.laps).map((id) => ({
      id,
      entry: compare.laps[id],
      color: lapColors[id] ?? lapColor(Number(id)),
      label: lapLabels[id] ?? `Lap ${id}`,
      isRef: id === String(refLap),
    }));
  }, [compare, lapLabels, refLap, lapColors]);

  // Same laps again, converted to g for the traction circle. Empty whenever
  // the recording predates the accelerometer (packet A) — the panel then
  // never mounts rather than drawing an empty ring.
  const ggLaps = useMemo<GGLap[]>(() => {
    const accel = compare?.accel;
    if (!accel?.available) return [];
    return mapLaps.map((lap) => ggLap(lap, accel)).filter((l): l is GGLap => l != null);
  }, [mapLaps, compare]);

  // Same laps, shaped for the Corner Detail widget (cursor-synced with the
  // charts and the map dot).
  const cornerLaps = useMemo<CornerLap[]>(() => {
    if (!compare) return [];
    return Object.keys(compare.laps).map((id) => ({
      id,
      label: lapLabels[id] ?? `Lap ${id}`,
      color: lapColors[id] ?? lapColor(Number(id)),
      isRef: id === String(refLap),
      series: compare.laps[id].series,
    }));
  }, [compare, lapLabels, refLap, lapColors]);

  if (sessions == null) {
    // Failed fetch would otherwise leave the skeleton up forever.
    if (error) {
      return (
        <div className="flex h-64 flex-col items-center justify-center gap-1 text-ink-dim">
          <div className="text-lg text-brake">{error}</div>
          <div className="text-sm">Check that the server is running, then reload.</div>
        </div>
      );
    }
    return (
      <div className="grid grid-cols-1 gap-3 p-3 xl:grid-cols-[1fr_360px]">
        <div className="space-y-3">
          <div className="skeleton h-14" />
          <div className="skeleton h-96" />
        </div>
        <div className="hidden space-y-3 xl:block">
          <div className="skeleton h-64" />
          <div className="skeleton h-40" />
        </div>
      </div>
    );
  }

  if (sessions.length === 0) {
    return (
      <div className="flex h-64 flex-col items-center justify-center gap-1 text-ink-dim">
        <div className="text-lg">No sessions yet</div>
        <div className="text-sm">Drive some laps first — they'll show up here for comparison.</div>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-3 p-3 xl:grid-cols-[1fr_360px]">
      {/* Left: selector + stacked charts */}
      <div className="min-w-0">
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-xl bg-panel p-3">
          <Select
            ariaLabel="Session"
            value={String(sessionId ?? "")}
            onValueChange={(v) => {
              manualSelection.current = false;
              setSessionId(Number(v));
            }}
            options={sessions.map((s) => ({
              value: String(s.id),
              label: `#${s.id} · ${s.car_name} · ${s.lap_count} laps`,
            }))}
            className="px-2 py-1.5 text-sm"
          />
          {/* Scrolls horizontally on narrow screens instead of overflowing */}
          <div className="flex min-w-0 max-w-full gap-1.5 overflow-x-auto pb-1 sm:flex-wrap sm:overflow-x-visible sm:pb-0">
            {laps.map((lap) => {
              const active = selected.includes(lap.id);
              const isRef = lap.id === refLap;
              return (
                <Tip key={lap.id} content="Click to toggle, double-click to set as reference">
                  <button
                    onClick={() => {
                      manualSelection.current = true;
                      setSelected((cur) =>
                        active ? cur.filter((id) => id !== lap.id) : [...cur, lap.id],
                      );
                    }}
                    onDoubleClick={() => {
                      manualSelection.current = true;
                      setRefLap(lap.id);
                    }}
                    className={`flex shrink-0 items-center gap-1.5 rounded-md border px-2 py-1 font-tabular text-xs transition-colors ${
                      isRef
                        ? "border-accent bg-accent/15 text-accent"
                        : active
                          ? "border-edge bg-panel-2 text-ink"
                          : "border-edge text-ink-dim hover:text-ink"
                    }`}
                  >
                    {active && (
                      <span
                        className="h-2 w-2 rounded-full"
                        style={{ backgroundColor: colorOf(lap.id) }}
                      />
                    )}
                    L{lap.number} {formatLapTime(lap.time_ms)}
                  </button>
                </Tip>
              );
            })}
          </div>
          <ChannelPicker selected={channelKeys} onChange={setChannelKeys} />
          {refLap != null && (
            <div className="ml-auto">
              <Select
                ariaLabel="Reference lap"
                value={String(refLap)}
                onValueChange={(v) => {
                  manualSelection.current = true;
                  setRefLap(Number(v));
                }}
                options={laps.map((lap) => ({
                  value: String(lap.id),
                  label: `ref: L${lap.number} ${formatLapTime(lap.time_ms)}`,
                }))}
                className="px-2 py-1.5 text-xs"
              />
            </div>
          )}
        </div>

        {error && <div className="mb-3 rounded-md bg-brake/10 p-2 text-sm text-brake">{error}</div>}
        {loading && !compare && <div className="skeleton h-96" />}
        {compare && (
          <div className="rounded-xl bg-panel p-2">
            <StackedCharts
              data={compare}
              lapLabels={lapLabels}
              lapColors={lapColors}
              units={units}
              channels={channelDefs}
              onCursorDist={onCursorDist}
              zoomRange={zoomRange}
              onZoomChange={setZoomRange}
            />
          </div>
        )}
      </div>

      {/* Right: race line, deviation, fuel, tuning */}
      <div className="flex min-w-0 flex-col gap-3">
        {refEntry && (
          <SidePanel
            title={mapLaps.length > 1 ? "Race lines — selected laps" : "Race line (reference lap)"}
          >
            <RaceLineMap
              laps={mapLaps}
              cursorDist={cursorDist}
              step={compare!.step}
              zoomRange={zoomRange}
              outline={outline}
              onZoomChange={setZoomRange}
            />
          </SidePanel>
        )}
        {ggLaps.length > 0 && (
          <SidePanel title="Traction circle — g-g">
            <GGDiagram
              laps={ggLaps}
              accel={compare!.accel}
              cursorDist={cursorDist}
              step={compare!.step}
            />
          </SidePanel>
        )}
        {cornerLaps.length > 0 && (
          <SidePanel title="Corner detail — cursor synced">
            <CornerDetail
              laps={cornerLaps}
              cursorDist={cursorDist}
              step={compare!.step}
              trackCorners={refEntry?.corners}
            />
          </SidePanel>
        )}
        {refLap != null && (
          <SidePanel title="Gearing (reference lap)">
            <GearingPanel lapId={refLap} units={units} />
          </SidePanel>
        )}
        {deviation && deviation.dist.length > 0 && (
          <SidePanel title={`Consistency — best ${deviation.lap_ids.length} laps`}>
            <DeviationChart data={deviation} units={units} zoomRange={zoomRange} />
          </SidePanel>
        )}
        {refLap != null && (
          <SidePanel title="Fuel strategy">
            <FuelMapPanel lapId={refLap} />
          </SidePanel>
        )}
        {refSummary && (
          <SidePanel title="Tuning info (reference lap)">
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 p-3 font-tabular text-xs">
              <Info k="Max speed" v={formatSpeed(refSummary.max_speed, units)} />
              <Info k="Min body height" v={`${refSummary.min_body_height.toFixed(0)} mm`} />
              <Info k="Full throttle" v={`${refSummary.full_throttle_pct.toFixed(1)}%`} />
              <Info k="Full brake" v={`${refSummary.full_brake_pct.toFixed(1)}%`} />
              <Info k="Coasting" v={`${refSummary.coasting_pct.toFixed(1)}%`} />
              <Info k="Tire spin" v={`${refSummary.tire_spin_pct.toFixed(1)}%`} />
              <Info k="Fuel used" v={`${refSummary.fuel_consumed.toFixed(2)} L`} />
              <Info k="Car" v={refSummary.car_name ?? "–"} />
              {bestCategory && <Info k="Category" v={bestCategory} />}
              {refSummary.tcs_active_pct != null && (
                <Info k="TCS active" v={`${refSummary.tcs_active_pct.toFixed(1)}%`} />
              )}
              {refSummary.asm_active_pct != null && (
                <Info k="ASM active" v={`${refSummary.asm_active_pct.toFixed(1)}%`} />
              )}
              {(refSummary.max_water_temp ?? 0) > 0 && (
                <Info k="Max water" v={`${refSummary.max_water_temp!.toFixed(0)}°C`} />
              )}
              {(refSummary.max_oil_temp ?? 0) > 0 && (
                <Info k="Max oil" v={`${refSummary.max_oil_temp!.toFixed(0)}°C`} />
              )}
              {(refSummary.min_oil_pressure ?? -1) >= 0 && (
                <Info k="Min oil press." v={`${refSummary.min_oil_pressure!.toFixed(1)} bar`} />
              )}
              {refSummary.event_counts && Object.keys(refSummary.event_counts).length > 0 && (
                <Info
                  k="Events"
                  v={Object.entries(refSummary.event_counts)
                    .map(([type, n]) => `${n} ${type}`)
                    .join(" · ")}
                />
              )}
            </div>
            {categoryBest && (
              <CategoryBestRow
                best={categoryBest}
                refTimeMs={refSummary.time_ms}
                onOpen={() =>
                  openInAnalysis({
                    session: categoryBest.session_id,
                    laps: [categoryBest.lap_id],
                    ref: categoryBest.lap_id,
                  })
                }
              />
            )}
          </SidePanel>
        )}
      </div>
    </div>
  );
}

// The class benchmark for this circuit, and how far off the reference lap is.
// Silent about which session it came from beyond the car — the point is the
// target, and clicking through is how you go and look at it.
function CategoryBestRow({
  best,
  refTimeMs,
  onOpen,
}: {
  best: CategoryBest;
  refTimeMs: number;
  onOpen: () => void;
}) {
  const gap = refTimeMs - best.time_ms;
  return (
    <div className="border-t border-edge px-3 py-2 text-xs">
      <div className="mb-1 flex items-baseline gap-2">
        <span className="text-ink-dim">
          {best.car_category} best at {best.track_name}
        </span>
        <span className="ml-auto font-tabular text-accent">{formatLapTime(best.time_ms)}</span>
      </div>
      <div className="flex items-baseline gap-2 text-ink-dim">
        <span className="truncate">{best.car_name}</span>
        <span className="ml-auto shrink-0 font-tabular">
          {gap <= 0 ? (
            <span className="text-throttle">this lap is the best</span>
          ) : (
            <span className="text-brake">+{(gap / 1000).toFixed(3)}</span>
          )}
        </span>
        {gap > 0 && (
          <button className="shrink-0 text-ink-dim hover:text-accent" onClick={onOpen}>
            open
          </button>
        )}
      </div>
    </div>
  );
}

function SidePanel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl bg-panel">
      <div className="border-b border-edge px-3 py-2 text-[10px] font-semibold uppercase tracking-widest text-ink-dim">
        {title}
      </div>
      {children}
    </div>
  );
}

function Info({ k, v }: { k: string; v: string }) {
  return (
    <>
      <span className="text-ink-dim">{k}</span>
      <span className="text-right">{v}</span>
    </>
  );
}
