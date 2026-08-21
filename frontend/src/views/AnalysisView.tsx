// Analysis view: multi-lap comparison with synced cursors, race line map,
// consistency (deviation), fuel strategy, and tuning info. The lap selection
// can arrive via deep link (#/analysis?session=…&laps=…&ref=…) from the
// Sessions or Live views, and is mirrored back into the URL for sharing.
// Selected laps need not belong to the chosen session (#26): laps from other
// sessions ride along as "guests", so today's stint can be compared against
// last week's — or against the class benchmark — on one set of charts.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChannelPicker } from "@/components/analysis/ChannelPicker";
import { CoachingPanel } from "@/components/analysis/CoachingPanel";
import { CornerDetail, type CornerLap } from "@/components/analysis/CornerDetail";
import { CornerReport, type ReportLap } from "@/components/analysis/CornerReport";
import { DeviationChart } from "@/components/analysis/DeviationChart";
import { FuelMapPanel } from "@/components/analysis/FuelMapPanel";
import { GearingPanel } from "@/components/analysis/GearingPanel";
import { GGDiagram, ggLap, type GGLap } from "@/components/analysis/GGDiagram";
import { PlaybackBar } from "@/components/analysis/PlaybackBar";
import { RaceLineMap, type MapLap } from "@/components/analysis/RaceLineMap";
import { StackedCharts } from "@/components/analysis/StackedCharts";
import { LargeDialog } from "@/components/ui/Dialog";
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
import { formatLapTime, formatSpeed, formatTime } from "@/lib/format";
import {
  openInAnalysis,
  reflectAnalysisSelection,
  type AnalysisRequest,
} from "@/lib/router";
import type {
  CategoryBest,
  CoachingNotes,
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

// Playback (#59) synthesizes a LiveFrame at the playhead for the transport's
// widget strip, so its channels ride along whatever the chart picker says.
// Optional columns (steer, race_pos) simply come back absent on recordings
// without them.
const PLAYBACK_COLUMNS = [
  "speed", "throttle", "brake", "gear", "rpm", "boost", "tire_slip",
  "steer", "race_pos",
];

export function AnalysisView({ request }: { request: AnalysisRequest }) {
  const units = useSettings((s) => s.units);
  const lapEpoch = useTelemetry((s) => s.lapEpoch);

  // Seed from the shared selection so switching tabs doesn't reset the view.
  // Exception: a session-only navigation ("Analyze" on a DIFFERENT session)
  // must not carry the stored selection along — those ids belong to another
  // session and would ride into the new one as guests.
  const stored = useRef(useAnalysisSelection.getState()).current;
  const staleStore =
    request.session != null &&
    request.laps == null &&
    request.ref == null &&
    stored.sessionId != null &&
    stored.sessionId !== request.session;
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);
  const [sessionId, setSessionId] = useState<number | null>(request.session ?? stored.sessionId);
  const [laps, setLaps] = useState<LapSummary[]>([]);
  // The session whose laps the `laps` state currently describes. The guest
  // resolver waits for it to match sessionId, so a mount-time deep link never
  // fetches the session's OWN laps as guests while `laps` is still empty.
  const [lapsFor, setLapsFor] = useState<number | null>(null);
  // Guest laps (#26): laps from OTHER sessions co-selected alongside this
  // session's own — the cross-session comparison. The ref mirrors the state
  // (assigned every render, so it is never behind) so effects can consult the
  // current guests without listing them as a dependency and re-running.
  const [guests, setGuests] = useState<LapSummary[]>([]);
  const guestsRef = useRef(guests);
  guestsRef.current = guests;
  // Guest bookkeeping, kept apart on purpose: an id whose fetch is merely
  // IN FLIGHT must not read as failed, or the session-laps response racing
  // ahead of lapDetail would prune every cross-session deep link on arrival.
  // Only ids in `failed` are dropped; `pending` just prevents double fetches.
  const failedGuestIds = useRef(new Set<number>());
  const pendingGuestIds = useRef(new Set<number>());
  const [selected, setSelected] = useState<number[]>(
    staleStore ? [] : (request.laps ?? stored.selectedLapIds),
  );
  const [refLap, setRefLap] = useState<number | null>(
    staleStore ? null : (request.ref ?? stored.refLapId),
  );
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
    !staleStore &&
      ((request.laps ?? stored.selectedLapIds).length > 0 ||
        (request.ref ?? stored.refLapId) != null),
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
    } else if (request.session != null) {
      // Session-only navigation ("Analyze" from Sessions): the current
      // selection belongs to whatever was open before and must not ride
      // along into the new session as guests — nor may an in-flight guest
      // fetch it started land after the switch.
      manualSelection.current = false;
      pendingGuestIds.current.clear();
      setSelected([]);
      setRefLap(null);
      setGuests([]);
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
      setLapsFor(sessionId);
      if (ls.length === 0) return;
      const best = [...ls].sort((a, b) => a.time_ms - b.time_ms)[0];
      const latest = ls[0]; // list is newest-first
      // A selected id missing from this session's laps is not necessarily
      // stale: it may be a guest from another session, or a cross-session
      // deep link (#/analysis?session=A&laps=idFromB) the resolver below has
      // not fetched yet — including one whose fetch is in flight RIGHT NOW.
      // Only ids whose fetch actually failed are dropped.
      const keep = (id: number) =>
        ls.some((l) => l.id === id) ||
        guestsRef.current.some((g) => g.id === id) ||
        !failedGuestIds.current.has(id);
      setSelected((cur) => {
        const stillValid = cur.filter(keep);
        return manualSelection.current && stillValid.length > 0
          ? stillValid
          : [...new Set([latest.id, best.id])];
      });
      setRefLap((cur) =>
        manualSelection.current && cur != null && keep(cur) ? cur : best.id,
      );
    }).catch(() => setError("Could not load laps"));
  }, [sessionId, lapEpoch]);

  // Resolve any selected id (or reference) that belongs to no loaded list
  // into a guest. This is what makes cross-session deep links
  // (#/analysis?session=A&laps=idFromB) work: the id survives pruning as
  // "not yet resolved", its summary is fetched here (samples excluded — the
  // compare endpoint pulls those), and it either joins the guests or, on
  // 404/failure, leaves the selection for good. Waits until the CURRENT
  // session's laps have loaded: before that, `laps` is empty (or another
  // session's), and every selected id — the session's own included — would
  // be fetched as a guest and rendered twice.
  useEffect(() => {
    if (lapsFor !== sessionId) return;
    const wanted =
      refLap != null && !selected.includes(refLap) ? [...selected, refLap] : selected;
    for (const id of wanted) {
      if (laps.some((l) => l.id === id)) continue;
      if (guestsRef.current.some((g) => g.id === id)) continue;
      if (pendingGuestIds.current.has(id) || failedGuestIds.current.has(id)) continue;
      pendingGuestIds.current.add(id);
      api.lapDetail(id, false)
        .then((lap) => {
          // delete() doubles as a staleness check: a session switch clears
          // the pending set, and a fetch it abandoned must not add its
          // foreign lap as a guest of whatever is open now.
          if (!pendingGuestIds.current.delete(id)) return;
          setGuests((cur) => (cur.some((g) => g.id === lap.id) ? cur : [...cur, lap]));
        })
        .catch(() => {
          if (!pendingGuestIds.current.delete(id)) return;
          failedGuestIds.current.add(id);
          setSelected((cur) => cur.filter((x) => x !== id));
          // A dead reference falls back to the session's best rather than
          // leaving the whole comparison blank.
          setRefLap((cur) =>
            cur === id
              ? [...laps].sort((a, b) => a.time_ms - b.time_ms)[0]?.id ?? null
              : cur,
          );
        });
    }
  }, [selected, refLap, laps, lapsFor, sessionId]);

  // A guest lives exactly as long as it is looked at: toggled off (and not
  // the reference) it leaves entirely — re-adding it later just resolves
  // fresh. A guest that turns out to be one of the session's OWN laps (a
  // race the resolver's gating should prevent) is dropped too, so no lap is
  // ever rendered twice.
  useEffect(() => {
    setGuests((cur) => {
      const next = cur.filter(
        (g) =>
          (selected.includes(g.id) || g.id === refLap) && !laps.some((l) => l.id === g.id),
      );
      return next.length === cur.length ? cur : next;
    });
  }, [selected, refLap, laps]);

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
        ...PLAYBACK_COLUMNS,
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

  // The engineer's post-lap notes for the whole session (#23). Cleared before
  // the fetch so a slow reply never shows one session's coaching against
  // another session's laps.
  const [coaching, setCoaching] = useState<CoachingNotes | null>(null);
  useEffect(() => {
    setCoaching(null);
    if (sessionId == null) return;
    let live = true;
    api.coachingNotes(sessionId)
      .then((n) => live && setCoaching(n))
      .catch(() => live && setCoaching(null));
    return () => {
      live = false;
    };
  }, [sessionId, lapEpoch]);

  // The surveyed road under the race line (#51). Keyed on the SESSION'S
  // circuit rather than the reference lap: the lap only ever resolved to its
  // session's circuit anyway, so this is the same answer without refetching
  // every time the reference lap changes — and it means the outline is cleared
  // exactly when the circuit changes, never leaving one track's road drawn
  // under another track's lap while the replacement is in flight.
  const [outline, setOutline] = useState<TrackOutline | null>(null);
  const outlineTrack = sessions?.find((s) => s.id === sessionId)?.track_name ?? "";
  useEffect(() => {
    setOutline(null);
    if (!outlineTrack) return;
    let live = true;
    api.trackOutline(outlineTrack)
      .then((o) => live && setOutline(o))
      .catch(() => live && setOutline(null));
    return () => {
      live = false;
    };
  }, [outlineTrack]);

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

  // While playback runs, the playhead owns the cursor: chart hover would
  // otherwise fight it frame by frame (and every mouse-leave would blank the
  // dot mid-lap). Paused or stopped, hover works exactly as before.
  const playbackActive = useRef(false);
  const onPlayingChange = useCallback((playing: boolean) => {
    playbackActive.current = playing;
  }, []);
  const onHoverCursorDist = useCallback(
    (d: number | null) => {
      if (playbackActive.current) return;
      onCursorDist(d);
    },
    [onCursorDist],
  );

  const lapLabels = useMemo(() => {
    const labels: Record<string, string> = {};
    for (const lap of laps) {
      labels[String(lap.id)] = `L${lap.number} · ${formatLapTime(lap.time_ms)}${
        lap.id === refLap ? " (ref)" : ""
      }`;
    }
    // Guests carry their session in the label: two "L2"s from different
    // stints must stay tellable apart everywhere the label shows up (charts,
    // map, corner panels).
    for (const lap of guests) {
      labels[String(lap.id)] = `S${lap.session_id}·L${lap.number} · ${formatLapTime(lap.time_ms)}${
        lap.id === refLap ? " (ref)" : ""
      }`;
    }
    return labels;
  }, [laps, guests, refLap]);

  const refEntry = compare?.laps[String(refLap)];
  // The reference may be a guest — the tuning panel and the class-benchmark
  // row must keep working when a lap from another session is the yardstick.
  const refSummary =
    laps.find((l) => l.id === refLap) ?? guests.find((l) => l.id === refLap);
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
    // Cleared before the request, not after it: otherwise switching sessions
    // shows the previous circuit's benchmark against the new reference lap's
    // time until the replacement lands, and the "open" link goes to a lap from
    // somewhere else entirely.
    setCategoryBest(null);
    if (!bestTrack || !bestCategory) return;
    let live = true;
    api.categoryBest(bestTrack, bestCategory)
      .then((b) => live && setCategoryBest(b))
      .catch(() => live && setCategoryBest(null));
    return () => {
      live = false;
    };
  }, [bestTrack, bestCategory, lapEpoch]);

  // The cross-session lap picker (#26): every lap recorded at this circuit
  // in OTHER sessions, fastest first. Needs a named circuit — with no
  // track_name there is no "same circuit" to list laps from. Fetched per
  // open so the list sees laps recorded while the view was already mounted.
  const [addOpen, setAddOpen] = useState(false);
  const [addChoices, setAddChoices] = useState<LapSummary[] | null>(null);
  useEffect(() => {
    if (!addOpen || !bestTrack) return;
    setAddChoices(null);
    let live = true;
    api.laps(bestTrack)
      .then(
        (all) =>
          live &&
          setAddChoices(
            all
              // Same phantom filter as the session picker: a lap without
              // samples has nothing to chart.
              .filter((l) => l.session_id !== sessionId && (l.total_ticks ?? 0) > 0)
              .sort((a, b) => a.time_ms - b.time_ms),
          ),
      )
      .catch(() => live && setAddChoices([]));
    return () => {
      live = false;
    };
  }, [addOpen, bestTrack, sessionId]);

  // Any guest add is a deliberate selection — the live "latest vs best"
  // follower must not overwrite it on the next lap.
  const addGuest = (lap: LapSummary) => {
    manualSelection.current = true;
    setGuests((cur) => (cur.some((g) => g.id === lap.id) ? cur : [...cur, lap]));
    setSelected((cur) => (cur.includes(lap.id) ? cur : [...cur, lap.id]));
  };

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

  // Laps that have a per-corner report (#21), for the report card table.
  const reportLaps = useMemo<ReportLap[]>(() => {
    if (!compare) return [];
    return Object.keys(compare.laps)
      .map((id) => ({
        id,
        label: lapLabels[id] ?? `Lap ${id}`,
        color: lapColors[id] ?? lapColor(Number(id)),
        isRef: id === String(refLap),
        report: compare.laps[id].corner_report ?? [],
      }))
      .filter((l) => l.report.length > 0);
  }, [compare, lapLabels, refLap, lapColors]);

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

  // Longest distance across the compared laps — the denominator for the
  // sector buttons and the zoom readout in the map header.
  const maxDist = useMemo(() => {
    if (!compare) return 0;
    let m = 0;
    for (const lap of Object.values(compare.laps)) {
      const d = lap.series.dist;
      if (d && d.length > 0) m = Math.max(m, d[d.length - 1]);
    }
    return m;
  }, [compare]);

  const SECTORS: [string, number, number][] = [
    ["Full", 0, 1],
    ["S1", 0, 0.33],
    ["S2", 0.33, 0.66],
    ["S3", 0.66, 1],
  ];

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
      <div className="mx-auto flex max-w-[1440px] flex-col gap-3">
        <div className="skeleton h-9" />
        <div className="skeleton h-[380px]" />
        <div className="skeleton h-12" />
        <div className="skeleton h-96" />
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

  const lapChip = (lap: LapSummary, label: string) => {
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
          className={`flex shrink-0 items-center rounded border px-2.5 py-1 font-tabular text-[11.5px] transition-colors ${
            isRef
              ? "border-accent bg-accent/12 text-accent-300"
              : active
                ? "border-edge bg-panel-2 text-ink-soft"
                : "border-edge text-ink-faint hover:border-ink-ghost hover:text-ink"
          }`}
        >
          {active && (
            <span
              className="mr-1.5 inline-block h-[7px] w-[7px] rounded-full"
              style={{ backgroundColor: colorOf(lap.id) }}
            />
          )}
          {label}
        </button>
      </Tip>
    );
  };

  return (
    <div className="mx-auto flex max-w-[1440px] flex-col gap-3">
      {/* 1 — toolbar: session, laps, channels, reference */}
      <div className="flex flex-wrap items-center gap-2">
        <Select
          ariaLabel="Session"
          value={String(sessionId ?? "")}
          onValueChange={(v) => {
            manualSelection.current = false;
            // A guest fetch started under the old session must not land
            // its lap as a guest of the new one.
            pendingGuestIds.current.clear();
            setSessionId(Number(v));
          }}
          options={sessions.map((s) => ({
            value: String(s.id),
            // The circuit goes first: it is what you are looking for when
            // picking a session to analyse, and since #58 most sessions
            // have one. Omitted rather than shown blank when they do not,
            // so an unidentified session reads as unidentified instead of
            // as a formatting glitch.
            label: `#${s.id}${s.track_name ? ` · ${s.track_name}` : ""} · ${
              s.car_name
            } · ${s.lap_count} laps`,
          }))}
          className="px-2.5 py-[5px] text-xs"
        />

        <div className="h-[18px] w-px shrink-0 bg-divider" />

        {/* Scrolls horizontally on narrow screens instead of overflowing */}
        <div className="flex min-w-0 max-w-full gap-2 overflow-x-auto pb-1 sm:flex-wrap sm:overflow-x-visible sm:pb-0">
          {laps.map((lap) => lapChip(lap, `L${lap.number} ${formatLapTime(lap.time_ms)}`))}
          {/* Guest laps from other sessions (#26), after the session's own —
              same toggle/reference behavior, session-qualified label. */}
          {guests.map((lap) =>
            lapChip(lap, `S${lap.session_id}·L${lap.number} ${formatLapTime(lap.time_ms)}`),
          )}
          {bestTrack && (
            <Tip content="Add a lap from another session at this circuit to the comparison">
              <button
                className="shrink-0 rounded border border-dashed border-edge px-2.5 py-1 text-[11.5px] text-ink-dim transition-colors hover:border-accent hover:text-accent"
                onClick={() => setAddOpen(true)}
              >
                + Add lap…
              </button>
            </Tip>
          )}
        </div>

        {/* 2 — channel picker popover hangs off this button */}
        <ChannelPicker selected={channelKeys} onChange={setChannelKeys} />

        {refLap != null && (
          <div className="ml-auto flex items-center gap-1.5 text-[11.5px] text-ink-dim">
            <span>ref</span>
            <Select
              ariaLabel="Reference lap"
              value={String(refLap)}
              onValueChange={(v) => {
                manualSelection.current = true;
                setRefLap(Number(v));
              }}
              options={[
                ...laps.map((lap) => ({
                  value: String(lap.id),
                  label: `L${lap.number} · ${formatLapTime(lap.time_ms)}`,
                })),
                // Guests are eligible references too — comparing today's
                // laps against another day's benchmark is the point (#26).
                ...guests.map((lap) => ({
                  value: String(lap.id),
                  label: `S${lap.session_id}·L${lap.number} · ${formatLapTime(lap.time_ms)}`,
                })),
              ]}
              variant="bare"
              className="px-1 font-tabular text-[11.5px]"
            />
          </div>
        )}
      </div>

      {error && <div className="rounded-md bg-brake/10 p-2 text-sm text-brake">{error}</div>}
      {loading && !compare && <div className="skeleton h-[380px]" />}

      {/* 3 — race line map hero */}
      {refEntry && (
        <div
          className="overflow-hidden rounded-panel"
          style={{
            background:
              "linear-gradient(180deg, var(--color-panel), color-mix(in srgb, var(--color-panel) 82%, #0c0d16))",
            boxShadow: "0 0 0 1px var(--color-hairline)",
          }}
        >
          <div className="flex flex-wrap items-center gap-3.5 px-4 py-2.5">
            <span className="section-header">
              Race line{bestTrack ? ` — ${bestTrack}` : ""}
            </span>
            <span className="flex flex-wrap gap-3 text-[10.5px] text-ink-dim">
              <span>
                <i className="mr-1.5 inline-block h-0.5 w-3.5 bg-throttle align-middle" />
                throttle
              </span>
              <span>
                <i className="mr-1.5 inline-block h-0.5 w-3.5 bg-brake align-middle" />
                brake
              </span>
              <span>
                <i className="mr-1.5 inline-block h-0.5 w-3.5 bg-coast align-middle" />
                coast
              </span>
              {outline && (
                <span>
                  <i className="mr-1.5 inline-block h-0.5 w-3.5 bg-ink-dim align-middle" />
                  surveyed borders
                </span>
              )}
            </span>
            <div className="ml-auto flex items-center gap-1">
              <Tip content="Drag across a chart to zoom · double-click to reset">
                <span className="mr-1.5 font-tabular text-[10.5px] text-ink-faint">
                  {zoomRange
                    ? `${zoomRange[0].toFixed(0)}–${zoomRange[1].toFixed(0)} m`
                    : maxDist > 0
                      ? `0–${maxDist.toFixed(0)} m`
                      : "full lap"}
                </span>
              </Tip>
              {SECTORS.map(([label, a, b]) => {
                // "Full" is the null range; the others compare against the
                // window they would set, so the active button survives a
                // round-trip through the charts.
                const isFull = a === 0 && b === 1;
                const range: [number, number] = [maxDist * a, maxDist * b];
                const on = isFull
                  ? zoomRange == null
                  : zoomRange != null &&
                    Math.abs(zoomRange[0] - range[0]) < 1 &&
                    Math.abs(zoomRange[1] - range[1]) < 1;
                return (
                  <button
                    key={label}
                    onClick={() => setZoomRange(isFull ? null : range)}
                    aria-pressed={on}
                    className={`rounded border px-2.5 py-0.5 text-[10.5px] transition-colors ${
                      on
                        ? "border-accent bg-accent/14 text-accent-300"
                        : "border-edge text-ink-dim hover:border-accent hover:text-accent"
                    }`}
                  >
                    {label}
                  </button>
                );
              })}
            </div>
          </div>
          <RaceLineMap
            hero
            laps={mapLaps}
            cursorDist={cursorDist}
            step={compare!.step}
            zoomRange={zoomRange}
            outline={outline}
            onZoomChange={setZoomRange}
          />
        </div>
      )}

      {compare && (
        <>
          {/* 4 — playback transport, keyed on the reference lap so switching
              it rewinds the clock */}
          {refEntry && (
            <PlaybackBar
              key={refLap}
              series={refEntry.series}
              lap={refSummary}
              onCursorDist={onCursorDist}
              onPlayingChange={onPlayingChange}
            />
          )}

          {/* 5 — chart stack */}
          <div className="panel px-4 py-3">
            <StackedCharts
              data={compare}
              lapLabels={lapLabels}
              lapColors={lapColors}
              units={units}
              channels={channelDefs}
              onCursorDist={onHoverCursorDist}
              zoomRange={zoomRange}
              onZoomChange={setZoomRange}
              cursorDist={cursorDist}
              refLapId={refLap != null ? String(refLap) : null}
            />
          </div>
        </>
      )}

      {/* 6 — bottom grid. auto-fit means every analysis panel the session has
          data for flows into the same card rack, three-up on a wide screen. */}
      <div className="grid grid-cols-[repeat(auto-fit,minmax(300px,1fr))] gap-3">
        {cornerLaps.length > 0 && (
          <Panel title="Corner detail — cursor synced">
            <CornerDetail
              laps={cornerLaps}
              cursorDist={cursorDist}
              step={compare!.step}
              trackCorners={refEntry?.corners}
            />
          </Panel>
        )}
        {refSummary && (
          <Panel title="Tuning — reference lap">
            <div className="grid grid-cols-[auto_1fr_auto_1fr] gap-x-3.5 gap-y-1.5 px-3.5 py-3 font-tabular text-[11.5px]">
              <Info k="Max speed" v={formatSpeed(refSummary.max_speed, units)} />
              <Info k="Min height" v={`${refSummary.min_body_height.toFixed(0)} mm`} />
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
                  span
                  v={Object.entries(refSummary.event_counts)
                    .map(([type, n]) => `${n} ${type}`)
                    .join(" · ")}
                />
              )}
            </div>
            {categoryBest && (
              <>
                <div className="rule" />
                <CategoryBestRow
                  best={categoryBest}
                  refTimeMs={refSummary.time_ms}
                  refIsFullLap={refSummary.counts_for_best !== false}
                  onOpen={() =>
                    openInAnalysis({
                      session: categoryBest.session_id,
                      laps: [categoryBest.lap_id],
                      ref: categoryBest.lap_id,
                    })
                  }
                  // Pull the benchmark INTO the current comparison as a guest —
                  // selecting it is enough, the resolver fetches the summary.
                  // Pointless (hence hidden) when it is already on screen.
                  onCompare={
                    selected.includes(categoryBest.lap_id) || categoryBest.lap_id === refLap
                      ? null
                      : () => {
                          manualSelection.current = true;
                          setSelected((cur) =>
                            cur.includes(categoryBest.lap_id)
                              ? cur
                              : [...cur, categoryBest.lap_id],
                          );
                        }
                  }
                />
              </>
            )}
          </Panel>
        )}
        {refLap != null && (
          <Panel title="Fuel strategy">
            <FuelMapPanel lapId={refLap} />
          </Panel>
        )}
        {ggLaps.length > 0 && (
          <Panel title="Traction circle — g-g">
            <GGDiagram
              laps={ggLaps}
              accel={compare!.accel}
              cursorDist={cursorDist}
              step={compare!.step}
            />
          </Panel>
        )}
        {refLap != null && (
          <Panel title="Gearing — reference lap">
            <GearingPanel lapId={refLap} units={units} />
          </Panel>
        )}
        {deviation && deviation.dist.length > 0 && (
          <Panel title={`Consistency — best ${deviation.lap_ids.length} laps`}>
            <DeviationChart data={deviation} units={units} zoomRange={zoomRange} />
          </Panel>
        )}
        {coaching && coaching.laps.length > 0 && (
          <Panel title="Race engineer — post-lap notes">
            <CoachingPanel
              notes={coaching.laps}
              selected={selected}
              lapColors={lapColors}
              corners={refEntry?.corners ?? []}
              onZoom={setZoomRange}
            />
          </Panel>
        )}
      </div>

      {/* The corner report is a wide table — it gets the full width rather
          than a 300px grid cell. */}
      {reportLaps.length > 0 && refEntry?.corners && refEntry.corners.length > 0 && (
        <Panel title="Corner report card">
          <CornerReport
            corners={refEntry.corners}
            laps={reportLaps}
            units={units}
            onZoom={setZoomRange}
          />
        </Panel>
      )}

      <LargeDialog
        open={addOpen}
        title={`Add a lap from another session — ${bestTrack}`}
        onClose={() => setAddOpen(false)}
      >
        <div className="h-full overflow-y-auto p-3">
          {addChoices == null && (
            <div className="space-y-2">
              {[0, 1, 2].map((i) => (
                <div key={i} className="skeleton h-9" />
              ))}
            </div>
          )}
          {addChoices != null && addChoices.length === 0 && (
            <div className="p-8 text-center text-sm text-ink-dim">
              No laps from other sessions at this circuit yet.
            </div>
          )}
          {addChoices != null && addChoices.length > 0 && (
            <div className="space-y-1">
              {addChoices.map((lap) => {
                const added = selected.includes(lap.id) || lap.id === refLap;
                return (
                  <button
                    key={lap.id}
                    disabled={added}
                    onClick={() => {
                      addGuest(lap);
                      setAddOpen(false);
                    }}
                    className={`flex w-full items-baseline gap-2 rounded-md border border-edge px-3 py-2 text-left text-xs transition-colors ${
                      added
                        ? "text-ink-ghost"
                        : "text-ink hover:border-accent hover:bg-panel-2"
                    }`}
                  >
                    <span className="shrink-0 font-tabular">
                      S#{lap.session_id} · L{lap.number} · {formatLapTime(lap.time_ms)}
                    </span>
                    <span className="min-w-0 truncate text-ink-dim">
                      · {lap.car_name ?? "–"} · {formatTime(lap.finished_at ?? "")}
                    </span>
                    {lap.salvaged && (
                      <span
                        className="ml-auto shrink-0 text-ink-dim"
                        title="Salvaged from a stream that ended at the line (replay ending) — the time is GT7's own"
                      >
                        ⟲ salvaged
                      </span>
                    )}
                    {lap.counts_for_best === false && (
                      <span
                        className={`shrink-0 text-warn ${lap.salvaged ? "" : "ml-auto"}`}
                        title="Partial lap (pit out-lap) — its time is not a lap time"
                      >
                        partial
                      </span>
                    )}
                    {added && <span className="ml-auto shrink-0">already selected</span>}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </LargeDialog>
    </div>
  );
}

// The class benchmark for this circuit, and how far off the reference lap is.
// Silent about which session it came from beyond the car — the point is the
// target, and clicking through is how you go and look at it.
function CategoryBestRow({
  best,
  refTimeMs,
  refIsFullLap,
  onOpen,
  onCompare,
}: {
  best: CategoryBest;
  refTimeMs: number;
  /** Partial laps (pit out-laps) are excluded from the benchmark itself, so
   *  their GT7-reported time — short precisely because it is not a lap — must
   *  not be measured against it, let alone allowed to beat it. */
  refIsFullLap: boolean;
  onOpen: () => void;
  /** Adds the benchmark to the CURRENT selection as a guest (#26) — the
   *  alternative to `open`, which switches to the benchmark's own session.
   *  null when the lap is already in the comparison. */
  onCompare: (() => void) | null;
}) {
  const gap = refTimeMs - best.time_ms;
  const isBest = refIsFullLap && gap <= 0;
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
          {!refIsFullLap ? (
            <span title="This reference lap is a partial lap, so its time is not a lap time">
              partial lap — no comparison
            </span>
          ) : isBest ? (
            <span className="text-throttle">this lap is the best</span>
          ) : (
            <span className="text-brake">+{(gap / 1000).toFixed(3)}</span>
          )}
        </span>
        {onCompare && (
          <button className="shrink-0 text-ink-dim hover:text-accent" onClick={onCompare}>
            compare
          </button>
        )}
        {!isBest && (
          <button className="shrink-0 text-ink-dim hover:text-accent" onClick={onOpen}>
            open
          </button>
        )}
      </div>
    </div>
  );
}

// One card in the bottom grid: a section header over whatever the panel
// draws. The header is the only chrome — the panel's hairline ring separates
// it from its neighbours.
function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="panel min-w-0">
      <div className="section-header px-3.5 py-2.5">{title}</div>
      <div className="rule" />
      {children}
    </div>
  );
}

// One key/value pair in the tuning grid. `span` runs the value across the
// grid's remaining columns, for values too long for a half-width cell.
function Info({ k, v, span = false }: { k: string; v: string; span?: boolean }) {
  return (
    <>
      <span className="text-ink-faint">{k}</span>
      <span className={`text-right ${span ? "col-span-3" : ""}`}>{v}</span>
    </>
  );
}
