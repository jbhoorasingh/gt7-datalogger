// Analysis view: multi-lap comparison with synced cursors, race line map,
// consistency (deviation), fuel strategy, and tuning info. The lap selection
// can arrive via deep link (#/analysis?session=…&laps=…&ref=…) from the
// Sessions or Live views, and is mirrored back into the URL for sharing.
// Selected laps need not belong to the chosen session (#26): laps from other
// sessions ride along as "guests", so today's stint can be compared against
// last week's — or against the class benchmark — on one set of charts.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnalysisGuide, type GuideTab } from "@/components/analysis/AnalysisGuide";
import { ChannelPicker } from "@/components/analysis/ChannelPicker";
import { CoachingPanel } from "@/components/analysis/CoachingPanel";
import { CornerDetail, type CornerLap } from "@/components/analysis/CornerDetail";
import {
  CornerReport,
  REPORT_SORT_OPTIONS,
  reportFocus,
  type ReportLap,
  type ReportSort,
} from "@/components/analysis/CornerReport";
import { DeviationChart } from "@/components/analysis/DeviationChart";
import { FuelMapPanel } from "@/components/analysis/FuelMapPanel";
import { GearingPanel, type GearingLap } from "@/components/analysis/GearingPanel";
import { GGDiagram, ggLap, type GGLap } from "@/components/analysis/GGDiagram";
import { LapStrip } from "@/components/analysis/LapStrip";
import { LapTimeChart } from "@/components/analysis/LapTimeChart";
import { LayersMenu } from "@/components/analysis/LayersMenu";
import { PlaybackBar } from "@/components/analysis/PlaybackBar";
import { layerKey, RaceLineMap, type MapLap } from "@/components/analysis/RaceLineMap";
import { SectorTimes, sectorNote } from "@/components/analysis/SectorTimes";
import { StackedCharts } from "@/components/analysis/StackedCharts";
import { StintTrendChart } from "@/components/analysis/StintTrend";
import { LargeDialog } from "@/components/ui/Dialog";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { Select } from "@/components/ui/Select";
import { Tip } from "@/components/ui/Tooltip";
import { openingSession, quickestCounting, resolveReference, resolveSelected } from "@/lib/analysisSelection";
import { api } from "@/lib/api";
import {
  CHANNEL_BY_KEY,
  CHANNELS,
  columnsForChannels,
  isDefaultChannelSet,
  loadChannelKeys,
  saveChannelKeys,
} from "@/lib/channels";
import { lapColor, lapColorMap } from "@/lib/colors";
import { cornerRange, selectedCorner, stepCorner } from "@/lib/cornerZoom";
import { gearHighCount } from "@/lib/gearChoice";
import { brakeMarkers, hasAid, type MapLayerKey } from "@/lib/mapLayers";
import { canDraw, LENS_OPTIONS, lensRuns, lensSeries, type MapLens } from "@/lib/mapLens";
import { hasTrend } from "@/lib/stint";
import { formatLapTime, formatSpeed, speedUnit, speedValue, formatTime } from "@/lib/format";
import {
  openInAnalysis,
  reflectAnalysisSelection,
  type AnalysisRequest,
} from "@/lib/router";
import {
  AIDS_ASM,
  AIDS_TCS,
  notCountingLabel,
  type CategoryBest,
  type CoachingNotes,
  type CompareResult,
  type Corner,
  type DeviationResult,
  type LapSummary,
  type Sector,
  type SectorTimes as SectorTimesData,
  type SessionSummary,
  type StintTrend,
  type TrackOutline,
} from "@/lib/types";
import { useAnalysisSelection } from "@/store/analysis";
import { useSettings } from "@/store/settings";
import { useTelemetry } from "@/store/telemetry";

// The Corner Detail widget always needs the per-corner columns, whatever the
// chart picker says.
// Lap chips shown before the row collapses — about two rows at a typical
// width, so an ordinary session never collapses at all.
const LAP_CHIP_BUDGET = 12;

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
// without them, and so does the suggested gear on a lap GT7 suggested nothing
// on.
const PLAYBACK_COLUMNS = [
  "speed", "throttle", "brake", "gear", "suggested_gear", "rpm", "boost", "tire_slip",
  "steer", "race_pos",
];

/** The session's default reference: its quickest lap that counts. A pit
 *  out-lap's short "time", or a lap the driver excluded (#74), is no
 *  yardstick — unless nothing else is left. */
// The link whose lens was last applied. App hands the view the request it
// parsed from the last hash it navigated to, and the view rewrites the hash
// in place as the lens changes, so a view mounted again on that request (as
// after the server-unreachable page) would put the link's lens back over the
// one picked since. A lens is applied once per link, across mounts.
let lensAppliedFrom: AnalysisRequest | null = null;

export function AnalysisView({ request }: { request: AnalysisRequest }) {
  const units = useSettings((s) => s.units);
  const splitView = useSettings((s) => s.splitView);
  const setSplitView = useSettings((s) => s.setSplitView);
  const splitChannels = useSettings((s) => s.splitChannels);
  const setSplitChannel = useSettings((s) => s.setSplitChannel);
  const mapFollow = useSettings((s) => s.mapFollow);
  const setMapFollow = useSettings((s) => s.setMapFollow);
  const mapSync = useSettings((s) => s.mapSync);
  const setMapSync = useSettings((s) => s.setMapSync);
  const mapLayers = useSettings((s) => s.mapLayers);
  const setMapLayer = useSettings((s) => s.setMapLayer);
  // How the race line and the lap strip are coloured: remembered on this
  // device, and carried in the link so a shared one shows the same view.
  const mapLens = useSettings((s) => s.mapLens);
  const setMapLens = useSettings((s) => s.setMapLens);
  const lapEpoch = useTelemetry((s) => s.lapEpoch);
  const recordingSessionId = useTelemetry((s) => s.status?.session_id ?? null);

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

  // Leaving a session takes everything that was showing of it along: the
  // selection, the reference, the guests and the session's own lap list.
  // What is selected is otherwise read, under the next session's title, as a
  // set of guest laps from another session — which is a feature (#26), and
  // so nothing downstream questions it. Bumping the epoch reloads the laps
  // even when the "next" session is the one already open.
  const [sessionEpoch, setSessionEpoch] = useState(0);
  const leaveSession = useCallback(() => {
    manualSelection.current = false;
    // A guest fetch started under the old session must not land its lap as
    // a guest of the new one.
    pendingGuestIds.current.clear();
    setSelected([]);
    setRefLap(null);
    setGuests([]);
    setLaps([]);
    setLapsFor(null);
    setSessionEpoch((n) => n + 1);
  }, []);

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
      leaveSession();
    }
    if (request.channels != null) {
      setChannelKeys(request.channels.filter((k) => k in CHANNEL_BY_KEY));
    }
    // Deliberately keyed on the serialized request only — re-running on every
    // object identity change would clobber in-view selection edits.
  }, [requestKey]);

  // A link that names a lens applies it, at mount and when one is pasted,
  // and the device remembers it as it does a link's channels. An effect of
  // its own: the one above skips the mount, and ignores a link that names no
  // session, lap or reference. Keyed on the request, which App rebuilds once
  // per new link, not on the lens alone: a second link with the same lens as
  // the first must still win over a lens picked in the view since.
  useEffect(() => {
    if (request === lensAppliedFrom) return;
    lensAppliedFrom = request;
    if (request.lens != null) setMapLens(request.lens);
  }, [request, setMapLens]);

  // Load sessions (refreshed when a new lap arrives live)
  useEffect(() => {
    api.sessions().then((s) => {
      setSessions(s);
      setSessionId((cur) => openingSession(cur, s));
    }).catch(() => setError("Could not load sessions"));
  }, [lapEpoch]);

  // Load laps for the chosen session.
  useEffect(() => {
    if (sessionId == null) return;
    // A reply for a session that is no longer the one open is dropped: the
    // replies to two quick switches can arrive in either order, and the
    // slower one must not put its session's laps under the other's title.
    let live = true;
    api.sessionLaps(sessionId).then((all) => {
      if (!live) return;
      // Laps without samples (phantoms from menu/replay flicker in old
      // recordings) have nothing to chart — keep them out of the picker.
      // Unknown tick counts are treated as empty, not as chartable.
      const ls = all.filter((lap) => (lap.total_ticks ?? 0) > 0); // newest first
      setLaps(ls);
      setLapsFor(sessionId);
      // A selected id missing from this session's laps is not necessarily
      // stale: it may be a guest from another session, or a cross-session
      // deep link (#/analysis?session=A&laps=idFromB) the resolver below has
      // not fetched yet — including one whose fetch is in flight RIGHT NOW.
      // Only ids whose fetch actually failed are dropped.
      const keep = (id: number) =>
        ls.some((l) => l.id === id) ||
        guestsRef.current.some((g) => g.id === id) ||
        !failedGuestIds.current.has(id);
      // A session with no laps resolves too — to nothing (see
      // lib/analysisSelection). Returning early here is what used to leave
      // the previous session's laps on screen under this one's title.
      const rule = { laps: ls, manual: manualSelection.current, keep };
      // The same ids keep the same array: the zoom window resets whenever
      // the selection changes, and a lap arriving live in SOME session
      // (this list is refetched on every lap) must not throw away the
      // stretch being looked at in this one.
      setSelected((cur) => {
        const next = resolveSelected(cur, rule);
        const same = cur.length === next.length && cur.every((id, i) => id === next[i]);
        return same ? cur : next;
      });
      setRefLap((cur) => resolveReference(cur, rule));
    }).catch(() => live && setError("Could not load laps"));
    return () => {
      live = false;
    };
  }, [sessionId, lapEpoch, sessionEpoch]);

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
          setRefLap((cur) => (cur === id ? (quickestCounting(laps)?.id ?? null) : cur));
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
    if (sessionId == null) return;
    const channels = isDefaultChannelSet(channelKeys) ? undefined : channelKeys;
    if (refLap == null || selected.length === 0) {
      // Nothing to compare. Once the session's laps have loaded and there
      // are none, that IS the selection — published so that the address bar
      // and the next visit to this tab name this session and no laps, rather
      // than keeping the last session's.
      const settled =
        lapsFor === sessionId && laps.length === 0 && selected.length === 0 && refLap == null;
      if (!settled) return;
      setSharedSelection({ sessionId, selectedLapIds: [], refLapId: null });
      reflectAnalysisSelection({ session: sessionId, channels, lens: mapLens });
      return;
    }
    setSharedSelection({ sessionId, selectedLapIds: selected, refLapId: refLap });
    // The chosen lens, not the one drawn: a link to a lens this comparison
    // cannot draw yet still names it.
    reflectAnalysisSelection({
      session: sessionId,
      laps: selected,
      ref: refLap,
      channels,
      lens: mapLens,
    });
  }, [sessionId, selected, refLap, laps, lapsFor, channelKeys, mapLens, setSharedSelection]);

  // Fetch comparison + deviation when the selection or channel set changes.
  // The request always carries the per-corner columns for the Corner Detail
  // widget on top of whatever the picked panels need.
  const requestColumns = useMemo(
    () => [
      ...new Set([
        ...columnsForChannels(channelKeys),
        // The split view's charts pick from every channel, not the stack's.
        ...(splitView ? columnsForChannels(splitChannels) : []),
        ...CORNER_COLUMNS,
        ...MAP_COLUMNS,
        ...GG_COLUMNS,
        ...PLAYBACK_COLUMNS,
      ]),
    ],
    [channelKeys, splitView, splitChannels],
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

  // Cleared when the SESSION changes, so one session's consistency chart is
  // never drawn under another's laps, but not when a lap arrives: the chart
  // would blink on every lap of the session being driven.
  const deviationFor = useRef<number | null>(null);
  useEffect(() => {
    if (sessionId == null) return;
    if (deviationFor.current !== sessionId) {
      deviationFor.current = sessionId;
      setDeviation(null);
    }
    let live = true;
    api.deviation(sessionId)
      .then((d) => live && setDeviation(d))
      .catch(() => live && setDeviation(null));
    return () => {
      live = false;
    };
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

  // Lap time and tyre temperature across the session (#111). Cleared with
  // the session like the consistency chart, and for the same reason kept
  // while a lap arrives: the panel would blink on every lap being driven.
  const [stintTrend, setStintTrend] = useState<StintTrend | null>(null);
  const stintFor = useRef<number | null>(null);
  useEffect(() => {
    if (sessionId == null) return;
    if (stintFor.current !== sessionId) {
      stintFor.current = sessionId;
      setStintTrend(null);
    }
    let live = true;
    api.stintTrend(sessionId)
      .then((t) => live && setStintTrend(t))
      .catch(() => live && setStintTrend(null));
    return () => {
      live = false;
    };
  }, [sessionId, lapEpoch]);

  // Every lap of the session split at the circuit's sectors (#22). Cleared
  // with the session, kept while a lap arrives, like the stint trend.
  const [sectorTimes, setSectorTimes] = useState<SectorTimesData | null>(null);
  const sectorsFor = useRef<number | null>(null);
  useEffect(() => {
    if (sessionId == null) return;
    if (sectorsFor.current !== sessionId) {
      sectorsFor.current = sessionId;
      setSectorTimes(null);
    }
    let live = true;
    api.sectorTimes(sessionId)
      .then((t) => live && setSectorTimes(t))
      .catch(() => live && setSectorTimes(null));
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
  // The window while a strip drag is in progress. The map and the strips
  // follow it frame by frame; the charts, heavier to re-zoom, take the
  // committed window on release.
  const [liveZoom, setLiveZoom] = useState<[number, number] | null>(null);
  const shownZoom = liveZoom ?? zoomRange;

  // Reset zoom window when session or lap selection changes
  useEffect(() => {
    setZoomRange(null);
    setLiveZoom(null);
  }, [sessionId, selected, refLap]);

  // The corner report's order. View state, not settings: a way of looking
  // at this comparison rather than a preference.
  const [reportSort, setReportSort] = useState<ReportSort>("track");
  const [mapMaximized, setMapMaximized] = useState(false);

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
  // Which moved the cursor last. The follow camera tracks the playhead only:
  // a hover is a look at one point, and zooming the map in on it dropped the
  // circuit from view whenever the mouse crossed a chart — in split view, the
  // charts right beside the map.
  const [playheadCursor, setPlayheadCursor] = useState(false);
  const onPlayheadCursorDist = useCallback(
    (d: number | null) => {
      setPlayheadCursor(true);
      onCursorDist(d);
    },
    [onCursorDist],
  );
  const onHoverCursorDist = useCallback(
    (d: number | null) => {
      if (playbackActive.current) return;
      setPlayheadCursor(false);
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
  // Split view needs a map to sit beside; on a narrow screen it is off, as
  // the charts would only stack above the same charts again.
  const split = splitView && refEntry != null && compare != null;
  // The reference may be a guest — the tuning panel and the class-benchmark
  // row must keep working when a lap from another session is the yardstick.
  const refSummary =
    laps.find((l) => l.id === refLap) ?? guests.find((l) => l.id === refLap);
  // Corners the reference took a gear higher than GT7 suggested (#108), from
  // the corner report the gear panel's shading reads too.
  const gearChoice = gearHighCount(refEntry?.corner_report);
  const session = sessions?.find((s) => s.id === sessionId) ?? null;
  // The open session's laps have loaded and there are none, and nothing from
  // another session was asked for either.
  const sessionEmpty =
    lapsFor === sessionId && laps.length === 0 && selected.length === 0 && guests.length === 0;

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
  // The in-app guide, and the section it is showing (null = closed).
  const [guide, setGuide] = useState<GuideTab | null>(null);
  const [showAllLaps, setShowAllLaps] = useState(false);
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

  // The quickest lap currently on screen. Pinned to purple below, so the
  // benchmark is identifiable at a glance among several moving cursor dots.
  const fastestLapId = useMemo(() => {
    let bestId: number | null = null;
    let bestMs = Infinity;
    for (const lap of [...laps, ...guests]) {
      if (!selected.includes(lap.id) && lap.id !== refLap) continue;
      // A partial lap's time is not a lap time (pit out-lap), and an excluded
      // one is not a time the driver stands behind (#74), so neither can be
      // the fastest — a pit out-lap would otherwise win every comparison.
      if (lap.counts_for_best === false) continue;
      if (lap.time_ms > 0 && lap.time_ms < bestMs) {
        bestMs = lap.time_ms;
        bestId = lap.id;
      }
    }
    return bestId;
  }, [laps, guests, selected, refLap]);

  // One color assignment for everything that shows the compared laps together
  // (chips, chart series, map, corner detail): id-keyed, but two selected laps
  // never share a color (laps 6 apart otherwise would — latest vs best hits it),
  // and the fastest of them always takes purple.
  const lapColors = useMemo<Record<string, string>>(() => {
    const ids = new Set<number>(selected);
    if (refLap != null) ids.add(refLap);
    for (const id of Object.keys(compare?.laps ?? {})) ids.add(Number(id));
    return Object.fromEntries(
      [...lapColorMap(ids, fastestLapId)].map(([id, color]) => [String(id), color]),
    );
  }, [selected, refLap, compare, fastestLapId]);
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

  // Which map layers have anything to show for this selection. A switch for
  // a layer the laps cannot fill (a recording from before the channel
  // existed, a lap with no events, a circuit never surveyed) is a switch
  // that does nothing, so the Layers menu lists only these.
  const availableLayers = useMemo(() => {
    const keys = new Set<MapLayerKey>(["peaks"]);
    const ref = mapLaps.find((lap) => lap.isRef);
    if ((outline?.road.length ?? 0) > 0 || (outline?.edges.length ?? 0) > 0) keys.add("road");
    if ((ref?.entry.corners?.length ?? 0) > 0) {
      keys.add("numbers");
      keys.add("apex");
      if (mapLaps.some((lap) => lap.entry.corner_report?.some((r) => r.brake_on != null))) {
        keys.add("brakes");
      }
    }
    if (mapLaps.length > 1) keys.add("others");
    if (ref?.entry.series.surface?.some((v) => v > 0)) keys.add("kerbs");
    if (mapLaps.some((lap) => (lap.entry.events?.length ?? 0) > 0)) keys.add("events");
    if (mapLaps.some((lap) => hasAid(lap.entry.series, AIDS_TCS))) keys.add("tcs");
    if (mapLaps.some((lap) => hasAid(lap.entry.series, AIDS_ASM))) keys.add("asm");
    return keys;
  }, [mapLaps, outline]);

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

  // The reference lap's length: the lap strip's, the fallback sectors'
  // denominator and the zoom's far end. The reference's, not the longest
  // compared lap's — a lap that could not be lined up keeps its own
  // distance axis, and a longer one would leave the strip a blank tail,
  // stretch the thirds and let the zoom run past the reference's line.
  const refLength = useMemo(() => {
    const d = refEntry?.series.dist;
    return d && d.length > 0 ? d[d.length - 1] : 0;
  }, [refEntry]);

  // The reference's corners with where it braked for each (#110), so that
  // every zoom window — the map's numbers, the strip, the report card, the
  // notes — starts at the braking zone and agrees with the others.
  const refCorners = useMemo<Corner[]>(() => {
    const corners = refEntry?.corners ?? [];
    const report = new Map((refEntry?.corner_report ?? []).map((r) => [r.n, r]));
    return corners.map((c) => ({ ...c, brake_on: report.get(c.n)?.brake_on ?? null }));
  }, [refEntry]);
  const currentCorner = selectedCorner(refCorners, shownZoom);

  // The circuit's sectors on the reference's axis, for the strips. A server
  // older than #22 sends none; thirds of the lap stand in, which is what the
  // old S1/S2/S3 buttons were.
  const sectors = useMemo<Sector[]>(() => {
    if (refEntry?.sectors && refEntry.sectors.length > 0) return refEntry.sectors;
    if (refLength <= 0) return [];
    return [1, 2, 3].map((n) => ({
      n,
      name: "",
      start_dist: Math.round(((n - 1) * refLength) / 3),
      end_dist: n === 3 ? refLength : Math.round((n * refLength) / 3),
      authored: false,
    }));
  }, [refEntry, refLength]);

  // The reference line's colouring under the chosen lens. Gain / loss needs
  // a lap to compare: the report card's focus lap. A lens with nothing to
  // colour by (no speed channel, no comparison lap) draws inputs instead.
  const [reportFocusId, setReportFocusId] = useState<string | null>(null);
  const focusLap = useMemo(() => {
    const others = mapLaps.filter((lap) => !lap.isRef);
    return others.find((lap) => lap.id === reportFocusId) ?? others[0] ?? null;
  }, [mapLaps, reportFocusId]);
  const gainSource = useMemo(
    () =>
      focusLap?.entry.delta != null
        ? { label: focusLap.label, dist: focusLap.entry.delta.dist, delta_ms: focusLap.entry.delta.delta_ms }
        : null,
    [focusLap],
  );
  // Which lenses this comparison can draw: Speed needs the channel (a
  // packet-A recording has none), Gain / loss a lap to compare, the tire
  // lenses a reference recorded with tire temperatures. A lens the map
  // cannot draw is disabled in the control, and a choice that is not
  // drawable — the comparison lap toggled off, or a link's lens on an older
  // lap — shows as Inputs, so the control never names a lens the map is not
  // drawing. The choice itself is kept, and takes over once it can be drawn.
  const lensAvailable = useMemo(
    () =>
      Object.fromEntries(
        LENS_OPTIONS.map((o) => [
          o.value,
          refEntry != null && canDraw(o.value, refEntry.series, units, gainSource),
        ]),
      ) as Record<MapLens, boolean>,
    [refEntry, units, gainSource],
  );
  const lensShown: MapLens = lensAvailable[mapLens] ? mapLens : "inputs";
  const lensData = useMemo(() => {
    if (!refEntry) return null;
    return (
      lensSeries(lensShown, refEntry.series, units, gainSource) ??
      lensSeries("inputs", refEntry.series, units, null)
    );
  }, [refEntry, lensShown, units, gainSource]);
  const stripRuns = useMemo(
    () => (lensData && refEntry ? lensRuns(lensData, refEntry.series.dist) : []),
    [lensData, refEntry],
  );

  // The gearing panel follows the same focus lap, or the reference when no
  // other lap is loaded, which until the comparison arrives is always: the
  // ratio table needs only the lap's id, the sawtooth its series. Beside a
  // focus lap the reference rides along, for the panel to draw behind it
  // when its gearbox differs.
  const gearingLap = useMemo<GearingLap | null>(() => {
    if (refLap == null) return null;
    const lap = focusLap ?? mapLaps.find((l) => l.isRef);
    if (!lap) {
      return { id: refLap, label: lapLabels[String(refLap)] ?? `Lap ${refLap}`, series: null };
    }
    return { id: Number(lap.id), label: lap.label, series: lap.entry.series };
  }, [refLap, focusLap, mapLaps, lapLabels]);
  const gearingRef = useMemo<GearingLap | null>(() => {
    const ref = focusLap ? mapLaps.find((l) => l.isRef) : undefined;
    return ref ? { id: Number(ref.id), label: ref.label, series: ref.entry.series } : null;
  }, [focusLap, mapLaps]);

  // What the strips and the charts' header say the window is.
  const zoomLabel = currentCorner
    ? `T${currentCorner.n} · ${currentCorner.direction === "L" ? "left" : "right"} · ${Math.round(
        speedValue(currentCorner.min_speed, units),
      )} ${speedUnit(units)} min`
    : shownZoom
      ? "Custom stretch"
      : "Full lap";
  const zoomText = `${Math.round(shownZoom?.[0] ?? 0).toLocaleString()}–${Math.round(
    shownZoom?.[1] ?? refLength,
  ).toLocaleString()} m`;

  // [ and ] step through the corners, Esc comes back to the full lap — an
  // open menu takes Esc first, and a field being typed in takes every key.
  const keyState = useRef({ refCorners, currentCorner });
  keyState.current = { refCorners, currentCorner };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (target && (/^(input|textarea|select)$/i.test(target.tagName) || target.isContentEditable)) {
        return;
      }
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const { refCorners: corners, currentCorner: current } = keyState.current;
      const forward = e.key === "]" || e.code === "BracketRight";
      if (forward || e.key === "[" || e.code === "BracketLeft") {
        const next = stepCorner(corners, current, forward ? 1 : -1);
        if (next) {
          e.preventDefault();
          setZoomRange(cornerRange(next));
        }
      } else if (e.key === "Escape") {
        setZoomRange(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Brake points on the map (#110): the reference's and the report's lap,
  // for the corner under the pointer in the report card — or, with the
  // pointer elsewhere, the corner the view is zoomed to. The second is what
  // makes them reachable at all on a screen where the card and the map do
  // not fit together: click the row, and the pins are there when the map
  // comes back into view.
  const [hoverCorner, setHoverCorner] = useState<number | null>(null);
  // A table that goes away under the pointer never reports it leaving.
  useEffect(() => setHoverCorner(null), [compare]);
  const brakeMarks = useMemo(() => {
    const corner = hoverCorner ?? selectedCorner(refCorners, zoomRange)?.n ?? null;
    const focus = reportFocus(reportLaps, reportFocusId);
    return brakeMarkers(
      mapLaps.filter((lap) => lap.isRef || lap.id === focus?.id),
      corner,
    );
  }, [hoverCorner, refCorners, zoomRange, reportLaps, reportFocusId, mapLaps]);
  // The key for the header: the lens's own, then the marker layers drawn.
  const mapKey = useMemo(
    () => layerKey(mapLaps, mapLayers, shownZoom, mapLayers.brakes ? brakeMarks : []),
    [mapLaps, mapLayers, shownZoom, brakeMarks],
  );

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

  // A long session lists a chip per lap, which on a 300-lap stint wrapped to
  // dozens of rows and pushed the map off the screen. Collapsed, the row shows
  // the most recent laps plus whatever is actually in the comparison, however
  // far back it sits; expanded, it scrolls rather than growing without bound.
  const visibleLaps = useMemo(() => {
    if (showAllLaps || laps.length <= LAP_CHIP_BUDGET) return laps;
    const head = laps.slice(0, LAP_CHIP_BUDGET);
    const keptBack = laps
      .slice(LAP_CHIP_BUDGET)
      .filter((lap) => selected.includes(lap.id) || lap.id === refLap);
    return [...head, ...keptBack];
  }, [laps, showAllLaps, selected, refLap]);
  const hiddenLaps = laps.length - visibleLaps.length;

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
            leaveSession();
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
          // Capped at the toolbar's width: a long circuit and car name
          // truncates instead of pushing the page sideways on a phone.
          className="max-w-full px-2.5 py-[5px] text-xs"
        />

        <div className="h-[18px] w-px shrink-0 bg-divider" />

        {/* Scrolls horizontally on narrow screens instead of overflowing, and
            vertically once expanded, so the map is always still on screen. */}
        <div
          className={`flex min-w-0 max-w-full gap-2 overflow-x-auto pb-1 sm:flex-wrap sm:overflow-x-visible sm:pb-0 ${
            showAllLaps ? "max-h-32 overflow-y-auto sm:pr-1" : ""
          }`}
        >
          {visibleLaps.map((lap) => lapChip(lap, `L${lap.number} ${formatLapTime(lap.time_ms)}`))}
          {/* Guest laps from other sessions (#26), after the session's own —
              same toggle/reference behavior, session-qualified label. */}
          {guests.map((lap) =>
            lapChip(lap, `S${lap.session_id}·L${lap.number} ${formatLapTime(lap.time_ms)}`),
          )}
        </div>

        {(hiddenLaps > 0 || showAllLaps) && (
          <button
            className="shrink-0 rounded border border-dashed border-edge px-2.5 py-1 text-[11.5px] text-ink-dim transition-colors hover:border-accent hover:text-accent"
            onClick={() => setShowAllLaps((open) => !open)}
          >
            {showAllLaps ? "show fewer" : `+${hiddenLaps} more`}
          </button>
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

        {/* 2 — channel picker popover hangs off this button */}
        <ChannelPicker
          selected={channelKeys}
          onChange={setChannelKeys}
          onHelp={() => setGuide("channels")}
        />
        <Tip content="What each chart channel and feature of this view shows">
          <button
            onClick={() => setGuide("features")}
            aria-label="Open the Analysis guide"
            className="flex h-[26px] w-[26px] shrink-0 items-center justify-center rounded-full border border-edge text-[11.5px] text-ink-dim transition-colors hover:border-accent hover:text-accent"
          >
            ?
          </button>
        </Tip>

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
      {sessionEmpty && (
        <div
          role="status"
          className="flex h-64 flex-col items-center justify-center gap-1 rounded-panel border border-edge px-4 text-center text-ink-dim"
        >
          <div className="text-lg">No completed laps in this session yet</div>
          <div className="text-sm">
            {recordingSessionId === sessionId
              ? "It is being recorded now — each lap appears here as soon as it is finished."
              : "Nothing in it was recorded as a lap, so there is nothing to chart."}
          </div>
        </div>
      )}

      {/* 3 — race line map hero, and in split view four charts beside it */}
      <div className={split ? "grid items-start gap-3 lg:grid-cols-2" : ""}>
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
              <Tip content="How the reference line and the lap strip are coloured, remembered on this device and carried in the link: by pedal (throttle, brake, coast), by speed, by where the compared lap gains and loses time against the reference, by the hottest tire (cold, working, hot), or by which axle's tires run hotter">
                {/* Five lenses are wider than a phone's map panel: there the
                    control scrolls sideways rather than being cut off. */}
                <span className="inline-flex max-w-full overflow-x-auto">
                  <SegmentedControl
                    ariaLabel="Map colouring"
                    size="sm"
                    value={lensShown}
                    onValueChange={setMapLens}
                    options={LENS_OPTIONS.map((o) => ({
                      value: o.value,
                      label: o.label,
                      disabled: !lensAvailable[o.value],
                      title: lensAvailable[o.value] ? undefined : o.needs,
                    }))}
                  />
                </span>
              </Tip>
              <span className="flex flex-wrap gap-3 text-[10.5px] text-ink-dim [&>span]:whitespace-nowrap">
                {(lensData?.legend ?? []).map((entry) => (
                  <span key={entry.label} className="inline-flex items-center gap-1.5">
                    <i className="inline-block h-0.5 w-3.5" style={{ backgroundColor: entry.color }} />
                    {entry.label}
                  </span>
                ))}
                {mapKey.map((entry) => (
                  <span
                    key={entry.label}
                    className="inline-flex items-center gap-1.5 text-ink-faint"
                    title={entry.title}
                  >
                    {entry.glyph === "pin" ? (
                      <i className="inline-block h-2 w-2 rotate-45 rounded-full rounded-br-none bg-ink-dim" />
                    ) : (
                      <span style={{ color: entry.color ?? "var(--color-ink)" }}>{entry.glyph}</span>
                    )}
                    {entry.label}
                  </span>
                ))}
              </span>
              <div className="ml-auto flex flex-wrap items-center justify-end gap-1.5">
                {/* Only meaningful with something to sync against. */}
                {mapLaps.length > 1 && (
                  <Tip content="Where the other laps' cars are drawn: where each was after the same lap time as the reference (time — the gap, as distance on track), or level with the reference car (position — where the charts compare them)">
                    <span className="mr-1 inline-flex items-center gap-1.5 text-[10.5px] text-ink-faint">
                      sync
                      <SegmentedControl
                        ariaLabel="Race line sync"
                        size="sm"
                        value={mapSync}
                        onValueChange={setMapSync}
                        options={[
                          { value: "time", label: "Time" },
                          { value: "position", label: "Position" },
                        ]}
                      />
                    </span>
                  </Tip>
                )}
                <LayersMenu layers={mapLayers} available={availableLayers} onToggle={setMapLayer} />
                <Tip content="Four charts of your choosing beside the map instead of below it, on a screen wide enough for both. The full chart stack stays below">
                  <button
                    onClick={() => setSplitView(!splitView)}
                    aria-pressed={splitView}
                    className={`hidden rounded border px-2.5 py-0.5 text-[10.5px] transition-colors lg:inline-block ${
                      splitView
                        ? "border-accent bg-accent/14 text-accent-300"
                        : "border-edge text-ink-dim hover:border-accent hover:text-accent"
                    }`}
                  >
                    Split
                  </button>
                </Tip>
                <Tip content="While playback runs, zoom the map in and pan with the car instead of framing the whole circuit">
                  <button
                    onClick={() => setMapFollow(!mapFollow)}
                    aria-pressed={mapFollow}
                    className={`rounded border px-2.5 py-0.5 text-[10.5px] transition-colors ${
                      mapFollow
                        ? "border-accent bg-accent/14 text-accent-300"
                        : "border-edge text-ink-dim hover:border-accent hover:text-accent"
                    }`}
                  >
                    Follow
                  </button>
                </Tip>
                <Tip content="Open the map full screen">
                  <button
                    onClick={() => setMapMaximized(true)}
                    aria-label="Maximize the race line map"
                    className="rounded border border-edge px-[7px] py-0.5 text-[11px] text-ink-dim transition-colors hover:border-edge-bright hover:text-ink"
                  >
                    ⤢
                  </button>
                </Tip>
              </div>
            </div>
            <RaceLineMap
              hero
              follow={mapFollow && playheadCursor}
              sync={mapSync}
              layers={mapLayers}
              brakeMarks={brakeMarks}
              laps={mapLaps}
              cursorDist={cursorDist}
              zoomRange={shownZoom}
              animate={liveZoom == null}
              outline={outline}
              onZoomChange={setZoomRange}
              onCursorDist={onHoverCursorDist}
              lens={lensData}
              corners={refCorners}
              focusId={focusLap?.id ?? null}
              units={units}
              maximized={mapMaximized}
              onMaximizedChange={setMapMaximized}
            />
            {/* The lap strip: the line unrolled, with the track's sectors and
                the corners on it, and the zoom window to drag about. */}
            <div className="border-t border-hairline px-4 pb-1 pt-2">
              <LapStrip
                length={refLength}
                runs={stripRuns}
                sectors={sectors}
                corners={refCorners}
                zoomRange={shownZoom}
                onZoomChange={setZoomRange}
                onLiveZoom={setLiveZoom}
                cursorDist={cursorDist}
                onCursorDist={onHoverCursorDist}
                units={units}
              />
            </div>
          </div>
        )}

        {split && (
          <div className="panel hidden min-w-0 lg:block">
            <div className="flex flex-wrap items-center gap-2 px-4 py-2.5">
              <span className="section-header">Beside the map</span>
              <span className="ml-auto flex min-w-0 flex-wrap justify-end gap-1.5">
                {splitChannels.map((key, index) => (
                  <Select
                    key={index}
                    ariaLabel={`Chart ${index + 1} beside the map`}
                    value={key}
                    onValueChange={(value) => setSplitChannel(index, value)}
                    // No channel twice: each option is free or this chart's own.
                    options={CHANNELS.filter(
                      (channel) => channel.key === key || !splitChannels.includes(channel.key),
                    ).map((channel) => ({ value: channel.key, label: channel.title }))}
                    className="max-w-[11rem] px-2 py-0.5 text-[11px]"
                  />
                ))}
              </span>
            </div>
            <div className="border-t border-hairline px-4 pb-3">
              <StackedCharts
                data={compare}
                lapLabels={lapLabels}
                lapColors={lapColors}
                units={units}
                channels={splitChannels.map((key) => CHANNEL_BY_KEY[key]).filter(Boolean)}
                onCursorDist={onHoverCursorDist}
                zoomRange={zoomRange}
                onZoomChange={setZoomRange}
                cursorDist={cursorDist}
                refLapId={refLap != null ? String(refLap) : null}
              />
            </div>
          </div>
        )}
      </div>

      {compare && (
        <>
          {/* 4 — playback transport, keyed on the reference lap so switching
              it rewinds the clock */}
          {refEntry && (
            <PlaybackBar
              key={refLap}
              series={refEntry.series}
              lap={refSummary}
              onCursorDist={onPlayheadCursorDist}
              onPlayingChange={onPlayingChange}
            />
          )}

          {/* 5 — chart stack, with a compact copy of the lap strip pinned
              over it: with every channel on, the zoom stays in reach while
              the stack scrolls. */}
          <div className="panel px-4 pb-3">
            {refEntry && (
              <div className="sticky top-0 z-[5] -mx-4 mb-1 rounded-t-panel border-b border-hairline bg-panel px-4 pb-0.5 pt-2">
                <div className="flex flex-wrap items-baseline gap-2.5 text-[11px]">
                  <span className="section-header">Channels · {channelKeys.length}</span>
                  <span className="text-ink-soft">{zoomLabel}</span>
                  <span className="font-tabular text-ink-faint">{zoomText}</span>
                  {shownZoom && (
                    <button
                      onClick={() => setZoomRange(null)}
                      className="rounded border border-edge px-2 text-[10.5px] text-ink-dim transition-colors hover:border-accent hover:text-accent"
                    >
                      Full lap
                    </button>
                  )}
                  <span className="ml-auto flex flex-wrap gap-3 font-tabular text-[10.5px] text-ink-dim">
                    {mapLaps.map((lap) => (
                      <span key={lap.id} className="inline-flex items-center gap-1.5 whitespace-nowrap">
                        <i className="inline-block h-0.5 w-3.5" style={{ backgroundColor: lap.color }} />
                        {lap.label}
                      </span>
                    ))}
                  </span>
                </div>
                <LapStrip
                  compact
                  length={refLength}
                  runs={stripRuns}
                  sectors={sectors}
                  corners={refCorners}
                  zoomRange={shownZoom}
                  onZoomChange={setZoomRange}
                  onLiveZoom={setLiveZoom}
                  cursorDist={cursorDist}
                  onCursorDist={onHoverCursorDist}
                  units={units}
                />
              </div>
            )}
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
              {gearChoice.judged > 0 && (
                <Info k="Corners a gear high" v={`${gearChoice.high} of ${gearChoice.judged}`} />
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
                  refNotCounting={notCountingLabel(refSummary)}
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
            />
          </Panel>
        )}
        {gearingLap && (
          <Panel title={`Gearing — ${gearingLap.label}`}>
            <GearingPanel
              lap={gearingLap}
              reference={gearingRef}
              units={units}
              cursorDist={cursorDist}
            />
          </Panel>
        )}
        {deviation && deviation.dist.length > 0 && (
          <Panel title={`Consistency — best ${deviation.lap_ids.length} laps`}>
            <DeviationChart data={deviation} units={units} zoomRange={zoomRange} />
          </Panel>
        )}
        {laps.length > 1 && (
          <Panel title="Lap times — this session">
            <LapTimeChart
              laps={laps}
              selected={selected}
              lapColors={lapColors}
              onToggleLap={(id) => {
                manualSelection.current = true;
                setSelected((cur) =>
                  cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id],
                );
              }}
            />
          </Panel>
        )}
        {coaching && coaching.laps.length > 0 && (
          <Panel title="Race engineer — post-lap notes">
            <CoachingPanel
              notes={coaching.laps}
              selected={selected}
              lapColors={lapColors}
              corners={refCorners}
              onZoom={setZoomRange}
            />
          </Panel>
        )}
        {sectorTimes && sectorTimes.laps.some((lap) => lap.splits_ms != null) && (
          <Panel title="Sector times" meta={sectorNote(sectorTimes)}>
            <SectorTimes
              data={sectorTimes}
              lapColors={lapColors}
              selected={selected}
              refLapId={refLap}
            />
          </Panel>
        )}
      </div>

      {/* A stint is read along its length, so the trend gets the full width
          as well: thirty laps in a 300px cell are thirty dots in a row. */}
      {stintTrend && hasTrend(stintTrend.laps) && (
        <Panel title="Stint trend — lap time and tyre temperature">
          <StintTrendChart
            trend={stintTrend}
            selected={selected}
            lapColors={lapColors}
            onToggleLap={(id) => {
              manualSelection.current = true;
              setSelected((cur) =>
                cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id],
              );
            }}
          />
        </Panel>
      )}

      {/* The corner report is a wide table — it gets the full width rather
          than a 300px grid cell. */}
      {reportLaps.length > 0 && refCorners.length > 0 && (
        <Panel
          title="Corner report card"
          actions={
            <span className="inline-flex items-center gap-1.5 text-[10.5px] text-ink-faint">
              sort
              <SegmentedControl
                ariaLabel="Sort corners"
                size="sm"
                value={reportSort}
                onValueChange={setReportSort}
                options={REPORT_SORT_OPTIONS}
              />
            </span>
          }
        >
          <CornerReport
            corners={refCorners}
            laps={reportLaps}
            units={units}
            focusId={reportFocusId}
            onFocusChange={setReportFocusId}
            onZoom={setZoomRange}
            onHoverCorner={setHoverCorner}
            sort={reportSort}
          />
        </Panel>
      )}

      <AnalysisGuide
        open={guide != null}
        tab={guide ?? "features"}
        onTabChange={setGuide}
        charted={channelKeys}
        onClose={() => setGuide(null)}
      />

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
                        title={
                          lap.best_override === false
                            ? "Excluded from bests by hand"
                            : "Partial lap (a pit out-lap, or a race's lap 1 from the grid) — its time is not a lap time"
                        }
                      >
                        {notCountingLabel(lap)}
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
  refNotCounting,
  onOpen,
  onCompare,
}: {
  best: CategoryBest;
  refTimeMs: number;
  /** Why the reference lap does not count, or null when it does. Laps that
   *  do not count are excluded from the benchmark itself, so their time — a
   *  pit out-lap's is short precisely because it is not a lap — must not be
   *  measured against it, let alone allowed to beat it. */
  refNotCounting: string | null;
  onOpen: () => void;
  /** Adds the benchmark to the CURRENT selection as a guest (#26) — the
   *  alternative to `open`, which switches to the benchmark's own session.
   *  null when the lap is already in the comparison. */
  onCompare: (() => void) | null;
}) {
  const gap = refTimeMs - best.time_ms;
  const isBest = refNotCounting == null && gap <= 0;
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
          {refNotCounting != null ? (
            <span title="This reference lap does not count toward bests, so its time is not compared">
              {refNotCounting} — no comparison
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
// draws, with room on the right for a note or a control. The header is the
// only chrome — the panel's hairline ring separates it from its neighbours.
function Panel({
  title,
  meta,
  actions,
  children,
}: {
  title: string;
  meta?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="panel min-w-0">
      <div className="flex flex-wrap items-center gap-2.5 px-3.5 py-2">
        <span className="section-header">{title}</span>
        {meta && <span className="ml-auto text-[10px] text-ink-faint">{meta}</span>}
        {actions && <span className="ml-auto">{actions}</span>}
      </div>
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
