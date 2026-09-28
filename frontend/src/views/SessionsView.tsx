// Sessions view: browse historical sessions, inspect and manage laps,
// export/import laps as JSON, manual "log lap now", and jump into the
// Analysis view with a session or lap pre-selected.
//
// Master–detail: a date-grouped list of sessions on the left, and the chosen
// session on the right — its header (tags and note edited in place), a stat
// strip, the lap-time chart and the lap table. The chart and the table share
// one set of ticked laps; ticking is for acting on laps in bulk (export,
// exclude, delete), never for choosing what Analysis shows — Analysis always
// opens the whole session and the comparison is picked there.
//
// Also hosts the personal-bests board as a sub-tab (#26) — same history,
// two readings of it — so the category filter and the top-level actions are
// shared rather than duplicated across two nav entries.

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { BestsBoard } from "@/components/BestsBoard";
import { LapTimeChart } from "@/components/analysis/LapTimeChart";
import { ConfirmDialog, PromptDialog } from "@/components/ui/Dialog";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { Select } from "@/components/ui/Select";
import { Tip } from "@/components/ui/Tooltip";
import { api, ApiError } from "@/lib/api";
import { FASTEST_COLOR, lapColorMap } from "@/lib/colors";
import { countingLaps, formatSpread, lapConsistency } from "@/lib/consistency";
import { formatDelta, formatLapTime, formatSpeed, formatTime } from "@/lib/format";
import { openInAnalysis } from "@/lib/router";
import {
  constantColumns,
  eventParts,
  formatEventCounts,
  groupByDate,
  LAP_COLUMNS,
  type LapColumn,
  matchesSessionFilter,
  sessionStarted,
  sessionWhen,
} from "@/lib/sessionList";
import {
  EXCLUDE_REASONS,
  type ExcludeReason,
  type LapSummary,
  notCountingLabel,
  type PersonalBest,
  type SessionSummary,
} from "@/lib/types";
import { useSettings } from "@/store/settings";
import { liveFrameRef, useTelemetry } from "@/store/telemetry";
import { toast } from "@/store/toasts";

type SubTab = "sessions" | "bests";
type LapSort = "newest" | "fastest";

// What the lap table may change about a lap: its ruling on the bests (#74).
type LapRuling = { best_override: boolean | null; exclude_reason?: ExcludeReason };

// Per-column show/hide the user chose under "Columns…"; a column with no
// choice is shown unless it reads the same on every lap.
type ColumnPrefs = Partial<Record<LapColumn, boolean>>;
const COLUMN_PREFS_KEY = "gt7.sessions.columns";

function loadColumnPrefs(): ColumnPrefs {
  try {
    return JSON.parse(localStorage.getItem(COLUMN_PREFS_KEY) ?? "{}") as ColumnPrefs;
  } catch {
    return {};
  }
}

function saveColumnPrefs(prefs: ColumnPrefs) {
  try {
    localStorage.setItem(COLUMN_PREFS_KEY, JSON.stringify(prefs));
  } catch {
    // private window / storage off: the choice lasts until reload
  }
}

// Ticking a lap to match what the distance check already says clears the
// ruling instead of restating it, so the lap follows the check again.
function flipRuling(lap: LapSummary): LapRuling {
  const want = lap.counts_for_best === false;
  return { best_override: want === (lap.full_lap ?? true) ? null : want };
}

function download(href: string, filename?: string) {
  const a = document.createElement("a");
  a.href = href;
  a.download = filename ?? "";
  a.click();
}

export function SessionsView({ subTab = "sessions" }: { subTab?: SubTab }) {
  const units = useSettings((s) => s.units);
  const lapEpoch = useTelemetry((s) => s.lapEpoch);
  const status = useTelemetry((s) => s.status);
  const [sub, setSub] = useState<SubTab>(subTab);
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);
  const [bests, setBests] = useState<PersonalBest[] | null>(null);
  const [pickedId, setPickedId] = useState<number | null>(null);
  // Laps of the session on show, tagged with whose they are so a slow answer
  // for the previous session is never shown under the new one.
  const [lapsOf, setLapsOf] = useState<{ sessionId: number; laps: LapSummary[] } | null>(null);
  // Ticked laps, likewise tagged: switching session drops the selection.
  const [ticked, setTicked] = useState<{ sessionId: number; ids: Set<number> } | null>(null);
  const [sort, setSort] = useState<LapSort>("newest");
  const [columnPrefs, setColumnPrefs] = useState<ColumnPrefs>(loadColumnPrefs);
  const [naming, setNaming] = useState<number | null>(null); // session id
  const [deletingSession, setDeletingSession] = useState<number | null>(null);
  const [deletingLaps, setDeletingLaps] = useState<number[] | null>(null);
  // Car category (packet C) as a grouping key: "show me only the Gr.3 runs".
  // Empty string = no filter; sessions recorded without packet C have no
  // category and are only reachable from "All".
  const [category, setCategory] = useState("");
  // Free text over car, circuit and tags (#25); "#wet" is a tag.
  const [query, setQuery] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);

  // A pasted #/bests link arrives as a prop; keep following it if the URL
  // changes under us, while in-page clicks drive the local state.
  useEffect(() => setSub(subTab), [subTab]);

  // Only offer categories actually present in the board being shown, so the
  // control disappears entirely on a history recorded before packet C.
  const source = sub === "bests" ? (bests ?? []) : (sessions ?? []);
  const categories = [...new Set(source.map((s) => s.car_category).filter(Boolean))].sort();
  // Deleting the last session of the filtered category would otherwise leave
  // the filter set to a value with no chip and no rows — a blank list with no
  // way back. Fall back to unfiltered whenever the selection stops existing.
  const active = categories.includes(category) ? category : "";
  const visible = (sessions ?? [])
    .filter((s) => (!active || s.car_category === active) && matchesSessionFilter(s, query))
    .sort((a, b) => b.started_at.localeCompare(a.started_at) || b.id - a.id);
  const visibleBests = bests?.filter((b) => !active || b.car_category === active) ?? null;
  // The picked session while it is still listed, else the newest one.
  const current = visible.find((s) => s.id === pickedId) ?? visible[0] ?? null;
  const currentId = current?.id ?? null;
  const laps = lapsOf && lapsOf.sessionId === currentId ? lapsOf.laps : null;
  const lapIds = new Set((laps ?? []).map((l) => l.id));
  const sel =
    ticked && ticked.sessionId === currentId
      ? new Set([...ticked.ids].filter((id) => lapIds.has(id)))
      : new Set<number>();
  const recordingId = status?.recording ? status.session_id : null;

  const refresh = useCallback(() => {
    api.sessions()
      .then(setSessions)
      .catch(() => toast("Could not load sessions", "error"));
  }, []);

  useEffect(refresh, [refresh, lapEpoch]);

  // Bests are only fetched once the board is actually opened — the Sessions
  // sub-tab is the landing view and does not need them.
  useEffect(() => {
    if (sub !== "bests") return;
    api.personalBests()
      .then((r) => setBests(r.bests))
      .catch(() => toast("Could not load bests", "error"));
  }, [sub, lapEpoch]);

  const reloadLaps = useCallback((sessionId: number) => {
    api.sessionLaps(sessionId)
      .then((ls) => setLapsOf({ sessionId, laps: ls }))
      .catch(() => toast("Could not load laps", "error"));
  }, []);

  useEffect(() => {
    if (currentId != null) reloadLaps(currentId);
  }, [currentId, lapEpoch, reloadLaps]);

  function toggleLap(id: number) {
    if (currentId == null) return;
    const next = new Set(sel);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setTicked({ sessionId: currentId, ids: next });
  }

  function setSelection(ids: number[]) {
    if (currentId != null) setTicked({ sessionId: currentId, ids: new Set(ids) });
  }

  function setColumn(col: LapColumn, shown: boolean | undefined) {
    const next = { ...columnPrefs };
    if (shown === undefined) delete next[col];
    else next[col] = shown;
    setColumnPrefs(next);
    saveColumnPrefs(next);
  }

  async function exportLap(id: number) {
    const data = await api.exportLap(id);
    const blob = new Blob([JSON.stringify(data)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    download(url, `gt7-lap-${id}.json`);
    // Revoking synchronously can cancel the download in some browsers.
    window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
  }

  // One lap file per lap, in turn: the import side takes a single lap per
  // file, so a bundle would be a format nothing reads back.
  async function exportLaps(ids: number[]) {
    try {
      for (const id of ids) await exportLap(id);
      toast(`Exported ${ids.length} lap${ids.length === 1 ? "" : "s"}`, "success");
    } catch {
      toast("Export failed", "error");
    }
  }

  function exportCsvs(ids: number[]) {
    ids.forEach((id, i) => window.setTimeout(() => download(api.lapCsvUrl(id)), i * 250));
  }

  async function importLap(file: File) {
    try {
      const payload = JSON.parse(await file.text());
      await api.importLap(payload);
      toast(`Imported ${file.name}`, "success");
      refresh();
    } catch {
      toast("Import failed — not a valid lap file", "error");
    }
  }

  async function nameTrack(sessionId: number, name: string) {
    try {
      const ls = lapsOf?.sessionId === sessionId ? lapsOf.laps : await api.sessionLaps(sessionId);
      if (ls.length === 0) {
        toast("Session has no laps to identify the track from", "error");
        return;
      }
      await api.createTrack(name, ls[0].id);
      toast(`Track saved as "${name}"`, "success");
      refresh();
    } catch {
      toast("Could not save track", "error");
    }
  }

  async function logLapNow() {
    try {
      const res = await api.logLapNow();
      toast(`Saved in-progress lap #${res.id}`, "success");
      refresh();
    } catch {
      toast("No lap in progress", "error");
    }
  }

  function putLap(sessionId: number, next: LapSummary) {
    setLapsOf((cur) =>
      cur && cur.sessionId === sessionId
        ? { sessionId, laps: cur.laps.map((l) => (l.id === next.id ? next : l)) }
        : cur,
    );
  }

  // Rule one lap in or out of the bests (#74). Optimistic, then replaced by
  // the server's summary — which carries the recomputed counts_for_best — and
  // the sessions list is refreshed for the session best the ruling may have
  // moved.
  async function ruleLap(sessionId: number, lap: LapSummary, ruling: LapRuling) {
    putLap(sessionId, {
      ...lap,
      best_override: ruling.best_override,
      counts_for_best: ruling.best_override ?? lap.full_lap ?? true,
      exclude_reason: ruling.best_override === false ? (ruling.exclude_reason ?? "") : "",
    });
    try {
      // Merged over the listed summary, which carries fields (track_name)
      // the single-lap answer does not.
      putLap(sessionId, { ...lap, ...(await api.updateLap(lap.id, ruling)) });
      refresh();
    } catch {
      putLap(sessionId, lap);
      toast("Could not update lap", "error");
    }
  }

  // Bulk "Exclude from bests": only the ticked laps that still count.
  async function excludeLaps(sessionId: number, targets: LapSummary[]) {
    const counting = targets.filter((l) => l.counts_for_best !== false);
    if (counting.length === 0) {
      toast("None of those laps count toward bests", "info");
      return;
    }
    try {
      await Promise.all(counting.map((l) => api.updateLap(l.id, flipRuling(l))));
      toast(
        `Excluded ${counting.length} lap${counting.length === 1 ? "" : "s"} from bests`,
        "success",
      );
    } catch {
      toast("Could not update every lap", "error");
    }
    reloadLaps(sessionId);
    refresh();
  }

  // Rule a session's laps in or out of the personal-bests board (#26). A
  // human decision by design: a replay recording or another driver's stint
  // produces telemetry indistinguishable from own driving.
  async function toggleBestsExcluded(s: SessionSummary) {
    const excluded = !s.bests_excluded;
    const flip = (value: boolean) =>
      setSessions((cur) =>
        cur?.map((x) => (x.id === s.id ? { ...x, bests_excluded: value } : x)) ?? cur,
      );
    // Optimistic, BEFORE the request: a quick second click must read the
    // flipped row and toggle back — flipping only after the PATCH resolves
    // leaves the whole round-trip as a window where it re-sends the SAME
    // value instead. Reverted if the server refuses.
    flip(excluded);
    try {
      await api.updateSession(s.id, { bests_excluded: excluded });
      toast(
        excluded
          ? `Session #${s.id} excluded from bests`
          : `Session #${s.id} counts for bests again`,
        "success",
      );
      refresh();
    } catch {
      flip(s.bests_excluded);
      toast("Could not update session", "error");
    }
  }

  const selectedLaps = (laps ?? []).filter((l) => sel.has(l.id));

  return (
    <div className={`mx-auto flex max-w-[1400px] flex-col gap-3 ${sel.size > 0 ? "pb-16" : ""}`}>
      <div className="flex flex-wrap items-center gap-3.5">
        <div className="flex overflow-hidden rounded-md border border-edge">
          {(["sessions", "bests"] as const).map((id) => (
            <button
              key={id}
              onClick={() => setSub(id)}
              aria-pressed={sub === id}
              className={`px-4 py-1.5 text-xs capitalize transition-colors ${
                sub === id ? "bg-accent/15 text-accent-300" : "text-ink-dim hover:text-ink"
              }`}
            >
              {id}
            </button>
          ))}
        </div>

        {categories.length > 0 && (
          <div className="flex flex-wrap gap-1.5 text-[11px]">
            {["", ...categories].map((c) => (
              <button
                key={c || "all"}
                onClick={() => setCategory(c)}
                aria-pressed={active === c}
                className={`rounded-[11px] px-2.5 py-0.5 transition-colors ${
                  active === c
                    ? "bg-accent/15 text-accent-300"
                    : "text-ink-faint hover:text-ink"
                }`}
              >
                {c || "All"}
              </button>
            ))}
          </div>
        )}

        {sub === "sessions" && (
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter by car, circuit or #tag"
            aria-label="Filter sessions"
            className="w-60 rounded-md border border-edge bg-panel-2 px-2.5 py-[5px] text-xs text-ink outline-none placeholder:text-ink-ghost focus:border-accent"
          />
        )}

        <div className="ml-auto flex gap-2">
          <button onClick={logLapNow} className="btn btn-primary px-3 py-[5px] text-[11.5px]">
            Log lap now
          </button>
          <button
            onClick={() => fileInput.current?.click()}
            className="btn px-3 py-[5px] text-[11.5px]"
          >
            Import lap…
          </button>
          <input
            ref={fileInput}
            type="file"
            accept=".json"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) importLap(f);
              e.target.value = "";
            }}
          />
        </div>
      </div>

      {sub === "bests" ? (
        <BestsBoard bests={visibleBests} />
      ) : sessions == null ? (
        <div className="grid grid-cols-[minmax(260px,320px)_minmax(0,1fr)] items-start gap-3.5">
          <div className="skeleton h-72" />
          <div className="skeleton h-72" />
        </div>
      ) : sessions.length === 0 ? (
        <div className="panel p-8 text-center text-ink-dim">
          <div className="mb-1 text-base text-ink">No sessions recorded yet</div>
          Laps are recorded automatically while you drive — or import a lap file above.
        </div>
      ) : (
        <div className="grid grid-cols-1 items-start gap-3.5 md:grid-cols-[minmax(260px,320px)_minmax(0,1fr)]">
          <SessionList
            sessions={visible}
            currentId={currentId}
            recordingId={recordingId}
            query={query}
            onPick={setPickedId}
          />

          {current ? (
            <section className="flex min-w-0 flex-col gap-3">
              <SessionHeader
                key={current.id}
                session={current}
                laps={laps}
                recording={recordingId === current.id}
                onSaved={refresh}
                onFilterTag={(t) => setQuery(`#${t}`)}
                onNameTrack={() => setNaming(current.id)}
                onToggleExcluded={() => toggleBestsExcluded(current)}
                onDelete={() => setDeletingSession(current.id)}
                onExportCsvs={() => exportCsvs((laps ?? []).map((l) => l.id))}
              />

              {laps == null ? (
                <div className="skeleton h-60" />
              ) : laps.length === 0 ? (
                <div className="panel p-6 text-center text-[12px] text-ink-dim">
                  No laps in this session yet.
                </div>
              ) : (
                <>
                  {laps.length > 1 && countingLaps(laps).length > 0 && (
                    <div className="panel">
                      <div className="flex flex-wrap items-baseline gap-2 px-4 py-2.5">
                        <span className="section-header">Lap time by lap</span>
                        <span className="text-[10.5px] text-ink-faint">
                          lower is quicker · hover a point for its time
                        </span>
                      </div>
                      <div className="rule" />
                      <LapTimeChart
                        laps={laps}
                        selected={[...sel]}
                        lapColors={Object.fromEntries(
                          lapColorMap(
                            laps.map((l) => l.id),
                            bestLapId(laps),
                          ),
                        )}
                        onToggleLap={toggleLap}
                      />
                    </div>
                  )}

                  <LapTable
                    session={current}
                    laps={laps}
                    units={units}
                    sort={sort}
                    onSort={setSort}
                    selected={sel}
                    onToggle={toggleLap}
                    onSelectAll={setSelection}
                    columnPrefs={columnPrefs}
                    onColumn={setColumn}
                    onExport={exportLap}
                    onDelete={(id) => setDeletingLaps([id])}
                    onRule={(lap, ruling) => ruleLap(current.id, lap, ruling)}
                    live={recordingId === current.id}
                  />
                </>
              )}
            </section>
          ) : (
            <div className="panel p-8 text-center text-[12px] text-ink-dim">
              No session matches “{query}”.
            </div>
          )}
        </div>
      )}

      {sub === "sessions" && current && selectedLaps.length > 0 && (
        <BulkBar
          laps={selectedLaps}
          onClear={() => setSelection([])}
          onExclude={() => excludeLaps(current.id, selectedLaps)}
          onExport={() => exportLaps(selectedLaps.map((l) => l.id))}
          onDelete={() => setDeletingLaps(selectedLaps.map((l) => l.id))}
        />
      )}

      <PromptDialog
        open={naming != null}
        title="Name this track"
        label="Future sessions on this track will be identified automatically."
        placeholder="e.g. Suzuka Circuit"
        onSubmit={(name) => {
          const id = naming!;
          setNaming(null);
          nameTrack(id, name);
        }}
        onCancel={() => setNaming(null)}
      />

      <ConfirmDialog
        open={deletingSession != null}
        title={`Delete session #${deletingSession ?? ""}?`}
        body="The session and all its laps will be removed. This cannot be undone."
        confirmLabel="Delete session"
        danger
        onConfirm={async () => {
          const id = deletingSession!;
          setDeletingSession(null);
          try {
            await api.deleteSession(id);
            setPickedId((cur) => (cur === id ? null : cur));
            toast(`Session #${id} deleted`, "success");
            refresh();
          } catch (error) {
            toast(
              error instanceof ApiError && error.status === 409
                ? "Cannot delete the current session. Start a new session first."
                : "Could not delete session",
              "error",
            );
          }
        }}
        onCancel={() => setDeletingSession(null)}
      />

      <ConfirmDialog
        open={deletingLaps != null}
        title={
          deletingLaps && deletingLaps.length > 1
            ? `Delete ${deletingLaps.length} laps?`
            : "Delete lap?"
        }
        body={
          deletingLaps && deletingLaps.length > 1
            ? "The laps and their telemetry samples will be removed. This cannot be undone."
            : "The lap and its telemetry samples will be removed. This cannot be undone."
        }
        confirmLabel={deletingLaps && deletingLaps.length > 1 ? "Delete laps" : "Delete lap"}
        danger
        onConfirm={async () => {
          const ids = deletingLaps!;
          setDeletingLaps(null);
          const results = await Promise.allSettled(ids.map((id) => api.deleteLap(id)));
          const failed = results.filter((r) => r.status === "rejected").length;
          if (failed > 0) toast(`Could not delete ${failed} of ${ids.length} laps`, "error");
          else toast(ids.length > 1 ? `${ids.length} laps deleted` : "Lap deleted", "success");
          if (currentId != null) reloadLaps(currentId);
          refresh();
        }}
        onCancel={() => setDeletingLaps(null)}
      />
    </div>
  );
}

/** The quickest lap that COUNTS: an excluded or partial lap is not the one to
 *  compare against, nor the one that takes purple. Falls back to the quickest
 *  of all when none count. */
function bestLapId(laps: LapSummary[]): number | null {
  if (laps.length === 0) return null;
  const counting = laps.filter((l) => l.counts_for_best !== false && l.time_ms > 0);
  return (counting.length > 0 ? counting : laps).reduce((a, b) => (b.time_ms < a.time_ms ? b : a))
    .id;
}

// ---------------------------------------------------------------------------
// Session list

function TrackChip({ name, size = "sm" }: { name: string; size?: "sm" | "md" }) {
  return (
    <span
      className={`min-w-0 truncate whitespace-nowrap rounded-[9px] border border-accent/38 bg-accent/22 font-medium text-accent-200 ${
        size === "md" ? "px-2.5 py-px text-[11px]" : "px-2 py-px text-[10px]"
      }`}
    >
      {name}
    </span>
  );
}

function RecordingDot() {
  return (
    <span
      className="animate-pulse-dot h-1.5 w-1.5 shrink-0 rounded-full bg-brake"
      aria-label="Recording"
    />
  );
}

function SessionList({
  sessions,
  currentId,
  recordingId,
  query,
  onPick,
}: {
  sessions: SessionSummary[];
  currentId: number | null;
  recordingId: number | null;
  query: string;
  onPick: (id: number) => void;
}) {
  const now = new Date();
  return (
    <aside className="panel overflow-hidden md:sticky md:top-0 md:max-h-[calc(100vh-7.5rem)] md:overflow-y-auto">
      {sessions.length === 0 && (
        <div className="px-3.5 py-6 text-center text-[11.5px] text-ink-faint">
          No session matches “{query}”.
        </div>
      )}
      {groupByDate(sessions, now).map((g) => (
        <div key={g.label}>
          <div className="px-3.5 pb-1 pt-2.5">
            <span className="section-header">{g.label}</span>
          </div>
          {g.items.map((s) => {
            const on = s.id === currentId;
            return (
              <button
                key={s.id}
                onClick={() => onPick(s.id)}
                aria-current={on ? "true" : undefined}
                title={formatTime(s.started_at)}
                className={`rule-row grid w-full grid-cols-[minmax(0,1fr)_auto] gap-x-2.5 gap-y-1 px-3.5 py-2.5 text-left transition-colors ${
                  on ? "bg-accent/8 shadow-[inset_2px_0_0_var(--color-accent)]" : "hover:bg-panel-2"
                }`}
              >
                <span className="flex min-w-0 items-center gap-1.5">
                  {recordingId === s.id && <RecordingDot />}
                  <span className="truncate text-[12.5px] font-medium">{s.car_name}</span>
                </span>
                <span className="text-right font-tabular text-xs text-fastest">
                  {s.best_lap_time_ms != null ? formatLapTime(s.best_lap_time_ms) : "–"}
                </span>
                <span className="flex min-w-0 items-center gap-1.5">
                  {s.track_name ? (
                    <TrackChip name={s.track_name} />
                  ) : (
                    <span className="whitespace-nowrap text-[10.5px] italic text-ink-ghost">
                      unnamed circuit
                    </span>
                  )}
                  <span className="whitespace-nowrap font-tabular text-[10.5px] text-ink-faint">
                    #{s.id} · {sessionWhen(s.started_at, now)}
                  </span>
                </span>
                <span className="text-right font-tabular text-[10.5px] text-ink-faint">
                  {s.lap_count} {s.lap_count === 1 ? "lap" : "laps"}
                </span>
              </button>
            );
          })}
        </div>
      ))}
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Detail header

/** The car's published figures beyond the headline three, for the spec
 *  line's tooltip.
 *
 * A figure of 0 is "not published" rather than zero — every EV has no
 * displacement, some cars no measured torque — so those pairs are dropped
 * instead of rendering "0 cc". Sessions recorded before #57 have all of them
 * empty until the startup backfill fills them in. */
function carSpecs(s: SessionSummary): [string, string][] {
  const specs: [string, string][] = [];
  if (s.car_power_bhp) specs.push(["Power", `${s.car_power_bhp} BHP`]);
  if (s.car_torque_kgfm) specs.push(["Torque", `${s.car_torque_kgfm} kgfm`]);
  if (s.car_weight_kg) specs.push(["Weight", `${s.car_weight_kg.toLocaleString()} kg`]);
  if (s.car_displacement_cc) specs.push(["Displacement", `${s.car_displacement_cc} cc`]);
  if (s.car_performance_points) specs.push(["PP", s.car_performance_points.toFixed(2)]);
  if (s.car_length_mm && s.car_width_mm && s.car_height_mm) {
    specs.push(["L×W×H", `${s.car_length_mm}×${s.car_width_mm}×${s.car_height_mm} mm`]);
  }
  return specs;
}

const HEADLINE_SPECS = new Set(["Power", "Weight", "PP"]);

function SessionHeader({
  session: s,
  laps,
  recording,
  onSaved,
  onFilterTag,
  onNameTrack,
  onToggleExcluded,
  onDelete,
  onExportCsvs,
}: {
  session: SessionSummary;
  laps: LapSummary[] | null;
  recording: boolean;
  onSaved: () => void;
  onFilterTag: (tag: string) => void;
  onNameTrack: () => void;
  onToggleExcluded: () => void;
  onDelete: () => void;
  onExportCsvs: () => void;
}) {
  // The note and the new tag are local drafts. The header is keyed by
  // session, so a draft never bleeds from one session into another.
  const [editingNote, setEditingNote] = useState(false);
  const [note, setNote] = useState(s.note);
  const [addingTag, setAddingTag] = useState(false);
  const [newTag, setNewTag] = useState("");
  const tags = s.tags ?? [];
  const specs = carSpecs(s);
  // "Nissan · FR · TC" — whatever the inventory knows. Sessions recorded
  // before #57, and cars GT7's list does not describe, have these empty.
  const detail = [s.car_manufacturer, s.car_drivetrain, s.car_aspiration].filter(Boolean);
  const headline = specs.filter(([label]) => HEADLINE_SPECS.has(label));

  async function save(patch: { note?: string; tags?: string[] }): Promise<boolean> {
    try {
      await api.updateSession(s.id, patch);
      onSaved();
      return true;
    } catch {
      toast("Could not update session", "error");
      return false;
    }
  }

  function addTag() {
    const t = newTag.trim();
    setAddingTag(false);
    setNewTag("");
    if (!t) return;
    if (t.includes(",")) {
      toast("Tags cannot contain commas", "error");
      return;
    }
    // Same case-insensitive dedupe the server applies, so the UI never shows
    // an add that the PATCH would collapse.
    if (tags.some((x) => x.toLowerCase() === t.toLowerCase())) return;
    void save({ tags: [...tags, t] });
  }

  return (
    <div className="panel flex flex-col gap-2.5 px-4 py-3.5">
      <div className="flex flex-wrap items-start gap-3.5">
        <div className="flex min-w-[260px] flex-1 flex-col gap-1.5">
          <span className="flex items-center gap-1.5 font-tabular text-[11px] text-ink-faint">
            {recording && <RecordingDot />}#{s.id} · {sessionStarted(s.started_at)}
            {recording ? " · recording now" : ""}
          </span>

          <div className="flex flex-wrap items-center gap-2.5">
            <span className="text-xl font-medium" title={s.car_full_name || undefined}>
              {s.car_name}
            </span>
            {s.track_name ? (
              <TrackChip name={s.track_name} size="md" />
            ) : (
              s.lap_count > 0 && (
                <button
                  className="rounded-[9px] border border-dashed border-edge px-2 py-px text-[10px] text-ink-faint transition-colors hover:border-accent hover:text-accent"
                  onClick={onNameTrack}
                >
                  name track…
                </button>
              )
            )}
            {s.car_category && (
              <span className="rounded-[9px] border border-edge px-2 py-px text-[10px] text-ink-dim">
                {s.car_category}
              </span>
            )}
            {s.final_position >= 1 && (
              <Tip
                content={`Finished P${s.final_position} of ${s.final_total_positions} — ${s.race_laps}-lap race${
                  s.race_time_ms != null ? `, total ${formatLapTime(s.race_time_ms)}` : ""
                }`}
              >
                <span className="whitespace-nowrap rounded-[9px] border border-throttle/38 bg-throttle/15 px-2 py-px font-tabular text-[10px] text-throttle">
                  P{s.final_position}/{s.final_total_positions}
                </span>
              </Tip>
            )}
            {s.bests_excluded && (
              <span className="whitespace-nowrap rounded-[9px] border border-dashed border-edge px-2 py-px text-[10px] text-ink-faint">
                excluded from bests
              </span>
            )}
            {tags.map((t) => (
              <span
                key={t}
                className="group flex items-center gap-1 rounded-[9px] bg-edge px-2 py-px text-[10px] text-ink-soft"
              >
                <button
                  className="hover:text-accent"
                  title={`Show sessions tagged #${t}`}
                  onClick={() => onFilterTag(t)}
                >
                  #{t}
                </button>
                <button
                  className="text-ink-faint opacity-0 transition-opacity hover:text-brake focus-visible:opacity-100 group-hover:opacity-100"
                  aria-label={`Remove tag ${t}`}
                  onClick={() => void save({ tags: tags.filter((x) => x !== t) })}
                >
                  ×
                </button>
              </span>
            ))}
            {addingTag ? (
              <input
                autoFocus
                value={newTag}
                onChange={(e) => setNewTag(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") addTag();
                  if (e.key === "Escape") {
                    setAddingTag(false);
                    setNewTag("");
                  }
                }}
                onBlur={addTag}
                maxLength={40}
                placeholder="tag"
                aria-label="New tag"
                className="w-24 rounded-[9px] border border-accent bg-transparent px-2 py-px text-[10px] outline-none placeholder:text-ink-ghost"
              />
            ) : (
              <button
                className="rounded-[9px] border border-dashed border-edge px-2 py-px text-[10px] text-ink-faint transition-colors hover:border-accent hover:text-accent"
                onClick={() => setAddingTag(true)}
              >
                + tag
              </button>
            )}
            {!s.note && !editingNote && (
              <button
                className="text-[10.5px] text-ink-faint transition-colors hover:text-accent"
                onClick={() => setEditingNote(true)}
              >
                + note
              </button>
            )}
          </div>

          {(detail.length > 0 || headline.length > 0) && (
            <Tip
              content={
                <span className="flex flex-col gap-0.5 font-tabular">
                  <span className="font-medium">
                    {s.car_full_name || s.car_name}
                    {s.car_year ? ` · ${s.car_year}` : ""}
                  </span>
                  {specs.map(([label, value]) => (
                    <span key={label} className="text-ink-dim">
                      {label} <span className="text-ink">{value}</span>
                    </span>
                  ))}
                </span>
              }
            >
              <span className="w-fit font-tabular text-[11px] text-ink-faint" tabIndex={0}>
                {[
                  ...detail.map((d) => <span key={d}>{d}</span>),
                  ...headline.map(([label, value]) => (
                    <span key={label}>
                      {label} <span className="text-ink-dim">{value}</span>
                    </span>
                  )),
                ].map((part, i) => (
                  <span key={i}>
                    {i > 0 && " · "}
                    {part}
                  </span>
                ))}
              </span>
            </Tip>
          )}

          {editingNote ? (
            <div className="mt-1 flex items-start gap-2">
              <textarea
                autoFocus
                value={note}
                onChange={(e) => setNote(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Escape") {
                    setNote(s.note);
                    setEditingNote(false);
                  }
                }}
                rows={2}
                maxLength={500}
                placeholder="Notes — setup changes, conditions, what to try next…"
                className="min-h-[34px] min-w-0 flex-1 resize-y rounded-[5px] border border-edge bg-transparent px-2.5 py-[7px] text-[11.5px] outline-none placeholder:text-ink-ghost focus:border-accent"
              />
              <button
                className="btn btn-primary shrink-0 px-3 py-[5px]"
                disabled={note === s.note}
                onClick={async () => {
                  if (await save({ note })) setEditingNote(false);
                }}
              >
                Save note
              </button>
              <button
                className="btn shrink-0 px-3 py-[5px]"
                onClick={() => {
                  setNote(s.note);
                  setEditingNote(false);
                }}
              >
                Cancel
              </button>
            </div>
          ) : (
            s.note && (
              <button
                className="w-fit max-w-full whitespace-pre-wrap text-left text-[11.5px] text-ink-dim transition-colors hover:text-ink"
                title="Edit note"
                onClick={() => setEditingNote(true)}
              >
                {s.note} <span className="text-[10px] text-ink-faint">✎</span>
              </button>
            )
          )}
        </div>

        <div className="flex flex-wrap gap-2">
          <Tip content="Open this session in the Analysis view — pick the laps to compare there">
            <button
              className="btn btn-primary px-3 py-[5px] text-[11.5px]"
              disabled={s.lap_count === 0}
              onClick={() => openInAnalysis({ session: s.id })}
            >
              Analyze session
            </button>
          </Tip>
          <Menu
            label="Export ▾"
            ariaLabel="Export session"
            className="btn px-3 py-[5px] text-[11.5px]"
            disabled={s.lap_count === 0}
            items={[
              {
                label: "Session ZIP",
                hint: "Every lap as a lap file, with the session's details",
                href: api.sessionZipUrl(s.id),
                download: true,
              },
              {
                label: "Session analysis",
                hint: "Every lap measured corner by corner against the session's best — braking points, minimum speeds, throttle and time lost — as one small JSON file",
                href: api.sessionAnalysisUrl(s.id),
                download: `gt7-session-${s.id}-analysis.json`,
              },
              {
                label: "All laps · CSV",
                hint: "One MoTeC-compatible CSV per lap",
                onSelect: onExportCsvs,
                disabled: !laps || laps.length === 0,
              },
            ]}
          />
          <Menu
            label="⋯"
            ariaLabel="More session actions"
            className="btn px-2.5 py-[5px] text-[11.5px]"
            items={[
              {
                label: s.bests_excluded ? "Include in bests" : "Exclude from bests",
                hint: "Replay recordings and other drivers' laps are indistinguishable from your own driving in telemetry — keeping them off the Bests board is a manual call.",
                onSelect: onToggleExcluded,
              },
              ...(!s.track_name && s.lap_count > 0
                ? [{ label: "Name track…", onSelect: onNameTrack }]
                : []),
              "separator" as const,
              { label: "Delete session…", danger: true, onSelect: onDelete },
            ]}
          />
        </div>
      </div>

      {laps && laps.length > 0 && <StatStrip laps={laps} recording={recording} />}
    </div>
  );
}

function StatStrip({ laps, recording }: { laps: LapSummary[]; recording: boolean }) {
  const counting = countingLaps(laps);
  const bestId = bestLapId(laps);
  const best = laps.find((l) => l.id === bestId) ?? null;
  const consistency = lapConsistency(laps);
  const mean =
    counting.length > 0 ? counting.reduce((a, l) => a + l.time_ms, 0) / counting.length : null;
  const nearBest =
    best && counting.length > 0
      ? counting.filter((l) => l.time_ms - best.time_ms <= 500).length
      : null;
  const fuelLaps = counting.filter((l) => l.fuel_consumed > 0);
  const fuelPerLap =
    fuelLaps.length > 0
      ? fuelLaps.reduce((a, l) => a + l.fuel_consumed, 0) / fuelLaps.length
      : null;
  // Fuel left is only known for the session being driven right now.
  const frame = recording ? liveFrameRef.current : null;
  const fuelLeft = frame && frame.fuel_capacity > 0 ? frame.fuel_level : null;

  const stats: { k: string; v: string; m: string; color?: string }[] = [
    {
      k: "Best",
      v: best ? formatLapTime(best.time_ms) : "–",
      m: best ? `lap ${best.number}` : "",
      color: FASTEST_COLOR,
    },
    {
      k: "Average",
      v: mean != null ? formatLapTime(mean) : "–",
      m: `${counting.length} counting lap${counting.length === 1 ? "" : "s"}`,
    },
    {
      k: "Consistency",
      v: consistency ? formatSpread(consistency.stdMs).replace("±", "± ") : "–",
      m: consistency ? "σ, counting laps only" : "needs three counting laps",
    },
    {
      k: "Within 0.5 s",
      v: nearBest != null ? String(nearBest) : "–",
      m: "laps near best",
      color: "var(--color-throttle)",
    },
    {
      k: "Fuel",
      v: fuelPerLap != null ? `${fuelPerLap.toFixed(2)} L` : "–",
      m:
        fuelPerLap == null
          ? "no fuel use recorded"
          : `per lap${fuelLeft != null ? ` · ${fuelLeft.toFixed(1)} L left` : ""}`,
    },
  ];

  return (
    <div className="grid grid-cols-[repeat(auto-fit,minmax(130px,1fr))] gap-px overflow-hidden rounded-md bg-hairline">
      {stats.map((st) => (
        <div key={st.k} className="flex flex-col gap-0.5 bg-panel-2 px-3 py-2.5">
          <span className="section-header">{st.k}</span>
          <span className="font-tabular text-xl" style={st.color ? { color: st.color } : undefined}>
            {st.v}
          </span>
          <span className="font-tabular text-[10.5px] text-ink-faint">{st.m || " "}</span>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Lap table

/** Tooltip for a lap's bests ruling, in words: what decided it. */
function countsHint(lap: LapSummary): string {
  if (lap.best_override === false) {
    return "Excluded from bests by hand — count it again to put it back";
  }
  if (lap.best_override === true && lap.full_lap === false) {
    return "Counted by hand, although its distance says it is a partial lap — exclude it to leave it to the distance check";
  }
  if (lap.full_lap === false) {
    return "Partial lap — a pit out-lap, or a race's first lap from the grid — so its time is not a lap time; count it anyway if it is one";
  }
  return "Counts toward session and personal bests — exclude it for an off-track, contact…";
}

const EVENT_COLORS: Record<string, string> = {
  L: "text-brake",
  S: "text-warn",
  B: "text-coast",
  K: "text-ink-dim",
};

function LapTable({
  session,
  laps,
  units,
  sort,
  onSort,
  selected,
  onToggle,
  onSelectAll,
  columnPrefs,
  onColumn,
  onExport,
  onDelete,
  onRule,
  live,
}: {
  session: SessionSummary;
  laps: LapSummary[];
  units: "metric" | "imperial";
  sort: LapSort;
  onSort: (sort: LapSort) => void;
  selected: Set<number>;
  onToggle: (id: number) => void;
  onSelectAll: (ids: number[]) => void;
  columnPrefs: ColumnPrefs;
  onColumn: (col: LapColumn, shown: boolean | undefined) => void;
  onExport: (id: number) => void;
  onDelete: (id: number) => void;
  onRule: (lap: LapSummary, ruling: LapRuling) => void;
  // This is the session being recorded right now: pin the lap in progress.
  live: boolean;
}) {
  const bestId = bestLapId(laps);
  const best = laps.find((l) => l.id === bestId);
  // The session's quickest lap takes purple, and no other lap in the table can
  // land on it — the same convention the Analysis charts and map use.
  const colors = lapColorMap(
    laps.map((l) => l.id),
    bestId,
  );
  const constant = constantColumns(laps);
  const shown = (col: LapColumn) => columnPrefs[col] ?? !constant.has(col);
  const columns = LAP_COLUMNS.filter((c) => shown(c.id));
  // Listed only when hidden automatically, and never position: an all-dash
  // position column just means it was not a race.
  const autoHidden = LAP_COLUMNS.filter(
    (c) => c.id !== "pos" && columnPrefs[c.id] === undefined && constant.has(c.id),
  );
  const rows = [...laps].sort((a, b) =>
    sort === "fastest"
      ? Number(a.counts_for_best === false) - Number(b.counts_for_best === false) ||
        a.time_ms - b.time_ms
      : b.number - a.number,
  );
  const allTicked = laps.length > 0 && laps.every((l) => selected.has(l.id));
  const someTicked = selected.size > 0 && !allTicked;

  return (
    <div className="panel overflow-hidden">
      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5 px-4 py-2">
        <SegmentedControl
          size="sm"
          ariaLabel="Lap order"
          value={sort}
          onValueChange={onSort}
          options={[
            { value: "newest", label: "Newest first" },
            { value: "fastest", label: "Fastest first" },
          ]}
        />
        <span className="text-[11px] text-ink-faint">
          Tick laps to export, exclude or delete them. Analysis always opens the whole session —
          pick laps to compare there.
        </span>
        <Menu
          label="Columns…"
          ariaLabel="Choose lap table columns"
          className="ml-auto text-[11px] text-accent hover:text-accent-300"
          items={[
            ...LAP_COLUMNS.filter((c) => c.id !== "pos" || !constant.has("pos") || columnPrefs.pos)
              .map((c) => ({
                label: (
                  <span className="flex flex-1 items-center gap-3">
                    <span className="flex-1">{c.label}</span>
                    {columnPrefs[c.id] === undefined && constant.has(c.id) && (
                      <span className="text-[10px] text-ink-ghost">same on every lap</span>
                    )}
                  </span>
                ),
                checked: shown(c.id),
                keepOpen: true,
                onSelect: () => onColumn(c.id, !shown(c.id)),
              })),
            "separator" as const,
            {
              label: "Automatic — hide what never changes",
              disabled: Object.keys(columnPrefs).length === 0,
              onSelect: () => LAP_COLUMNS.forEach((c) => onColumn(c.id, undefined)),
            },
          ]}
        />
        {autoHidden.length > 0 && (
          <span className="w-full font-tabular text-[11px] text-ink-faint">
            Same on every lap, hidden:{" "}
            {autoHidden
              .map((c) => {
                const v = constant.get(c.id)!;
                return `${c.short} ${c.id === "speed" ? formatSpeed(Number(v), units) : v}`;
              })
              .join(" · ")}
          </span>
        )}
      </div>
      <div className="rule" />
      <div className="overflow-x-auto">
        <table className="w-full border-collapse font-tabular text-xs">
          <thead>
            <tr className="text-left text-[11px] text-ink-faint">
              <th className="w-9 py-2 pl-4 pr-0">
                <input
                  type="checkbox"
                  checked={allTicked}
                  ref={(el) => {
                    if (el) el.indeterminate = someTicked;
                  }}
                  onChange={() => onSelectAll(selected.size > 0 ? [] : laps.map((l) => l.id))}
                  aria-label={selected.size > 0 ? "Untick all laps" : "Tick all laps"}
                  className="h-[13px] w-[13px] cursor-pointer accent-accent align-middle"
                />
              </th>
              <th className="p-2 font-normal">Lap</th>
              <th className="p-2 font-normal">Time</th>
              <th className="p-2 font-normal">Δ best</th>
              {columns.map((c) => (
                <th
                  key={c.id}
                  className={`whitespace-nowrap p-2 font-normal ${c.id === "speed" ? "text-right" : ""}`}
                >
                  {c.label}
                  {c.id === "events" && (
                    <span className="text-ink-ghost">
                      {" · "}
                      <span className="text-brake">L</span>ockup{" "}
                      <span className="text-warn">S</span>pin{" "}
                      <span className="text-coast">B</span>ottom{" "}
                      <span className="text-ink-dim">K</span>erb
                    </span>
                  )}
                </th>
              ))}
              <th className="px-4 py-2">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {live && <InProgressRow laps={laps} trailing={columns.length + 1} />}
            {rows.map((lap) => {
              const counts = lap.counts_for_best !== false;
              const isBest = lap.id === bestId && counts;
              const diff = best ? lap.time_ms - best.time_ms : null;
              const on = selected.has(lap.id);
              const why = notCountingLabel(lap);
              const kept = lap.best_override === true && lap.full_lap === false;
              return (
                <tr
                  key={lap.id}
                  onClick={() => onToggle(lap.id)}
                  className={`rule-row cursor-pointer transition-colors ${
                    on ? "bg-accent/7" : "hover:bg-panel-2/70"
                  } ${counts ? "" : "opacity-60"}`}
                >
                  <td className="py-[7px] pl-4 pr-0">
                    <input
                      type="checkbox"
                      checked={on}
                      onClick={(e) => e.stopPropagation()}
                      onChange={() => onToggle(lap.id)}
                      aria-label={`Tick lap ${lap.number}`}
                      className="h-[13px] w-[13px] cursor-pointer accent-accent align-middle"
                    />
                  </td>
                  <td className="whitespace-nowrap px-2 py-[7px]">
                    <span className="inline-flex items-center gap-1.5">
                      <span
                        className="h-1.5 w-1.5 shrink-0 rounded-full"
                        style={{ backgroundColor: colors.get(lap.id) }}
                        title="This lap's color in charts and maps"
                      />
                      {lap.number}
                      {lap.salvaged && (
                        <span
                          className="text-ink-faint"
                          title="Salvaged from a stream that ended at the line (replay ending) — the time is GT7's own"
                        >
                          ⟲
                        </span>
                      )}
                      {isBest ? (
                        <span
                          className="rounded-lg border px-1.5 text-[10px]"
                          style={{
                            color: FASTEST_COLOR,
                            borderColor: `color-mix(in srgb, ${FASTEST_COLOR} 50%, transparent)`,
                          }}
                        >
                          best
                        </span>
                      ) : lap.best_override === false ? (
                        // Why it was ruled out: kept in the row so the reason
                        // can still be given after the fact.
                        <span onClick={(e) => e.stopPropagation()}>
                          <Select
                            ariaLabel={`Why lap ${lap.number} is excluded`}
                            value={lap.exclude_reason ?? ""}
                            placeholder="excluded · why?"
                            options={EXCLUDE_REASONS.map((r) => ({
                              value: r,
                              label: `excluded · ${r}`,
                            }))}
                            onValueChange={(r) =>
                              onRule(lap, {
                                best_override: false,
                                exclude_reason: r as ExcludeReason,
                              })
                            }
                            className="rounded-lg px-1.5 py-0 font-sans text-[10px]"
                          />
                        </span>
                      ) : why || kept ? (
                        <Tip content={countsHint(lap)}>
                          <span className="rounded-lg border border-edge px-1.5 text-[10px] text-ink-faint">
                            {kept ? "kept" : why}
                          </span>
                        </Tip>
                      ) : null}
                    </span>
                  </td>
                  <td
                    className={`px-2 py-[7px] ${counts ? "text-ink" : "text-ink-faint"}`}
                    style={isBest ? { color: FASTEST_COLOR } : undefined}
                  >
                    {formatLapTime(lap.time_ms)}
                  </td>
                  <td
                    className={`px-2 py-[7px] ${
                      !counts || diff == null
                        ? "text-ink-ghost"
                        : diff <= 300
                          ? "text-throttle"
                          : "text-ink-dim"
                    }`}
                    style={isBest ? { color: FASTEST_COLOR } : undefined}
                  >
                    {!counts || diff == null
                      ? "—"
                      : isBest
                        ? "best"
                        : `+${(Math.max(0, diff) / 1000).toFixed(3)}`}
                  </td>
                  {columns.map((c) => (
                    <td
                      key={c.id}
                      className={`whitespace-nowrap px-2 py-[7px] ${
                        c.id === "speed" ? "text-right text-ink-dim" : "text-ink-dim"
                      }`}
                    >
                      {c.id === "events" ? (
                        <EventCell lap={lap} />
                      ) : c.id === "speed" ? (
                        formatSpeed(lap.max_speed, units)
                      ) : (
                        c.text(lap)
                      )}
                    </td>
                  ))}
                  <td
                    className="whitespace-nowrap px-4 py-1 text-right"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <Menu
                      label="⋯"
                      ariaLabel={`Lap ${lap.number} actions`}
                      className="btn px-2 py-0.5"
                      items={[
                        {
                          label: "Open in Analysis",
                          hint: "Compare against the session's best lap in Analysis",
                          onSelect: () =>
                            openInAnalysis({
                              session: session.id,
                              laps:
                                bestId != null && bestId !== lap.id ? [lap.id, bestId] : [lap.id],
                              ref: bestId ?? lap.id,
                            }),
                        },
                        {
                          label: "Set as reference",
                          hint: "Open Analysis with this lap as the reference",
                          onSelect: () =>
                            openInAnalysis({ session: session.id, laps: [lap.id], ref: lap.id }),
                        },
                        { label: "Export JSON", onSelect: () => onExport(lap.id) },
                        {
                          label: "Export CSV",
                          hint: "MoTeC-compatible CSV",
                          href: api.lapCsvUrl(lap.id),
                          download: true,
                        },
                        "separator",
                        {
                          label: counts ? "Exclude from bests" : "Count for bests",
                          hint: countsHint(lap),
                          onSelect: () => onRule(lap, flipRuling(lap)),
                        },
                        { label: "Delete…", danger: true, onSelect: () => onDelete(lap.id) },
                      ]}
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// No frame for this long and the lap in progress is dropped from the table —
// the same grace the Live view gives before it hands over to "no telemetry".
const IN_PROGRESS_STALL_MS = 5000;

// The lap being driven, pinned above the completed laps whatever the sort —
// the Sessions twin of the Live view's laps rail. It is not a lap yet (no id,
// nothing to tick, export or rule on), so it has no checkbox and no menu.
//
// Time and delta change at frame rate, so they are written straight into the
// DOM from an animation-frame loop over liveFrameRef; React state holds only
// the lap number, which changes once a lap, so the table never re-renders at
// frame rate. The row drops out when the stream stalls, when the car leaves
// the track, and once its lap number is in the table: the lapEpoch refresh
// that brings the completed lap in hands the row on to the next one, and a
// finished race (GT7 stops counting laps) leaves nothing to pin.
function InProgressRow({ laps, trailing }: { laps: LapSummary[]; trailing: number }) {
  const lastDone = laps.reduce((n, l) => Math.max(n, l.number), 0);
  const [lap, setLap] = useState<number | null>(null);
  const timeRef = useRef<HTMLTableCellElement>(null);
  const deltaRef = useRef<HTMLTableCellElement>(null);

  useEffect(() => {
    let raf = 0;
    let written = -1; // liveFrameRef.at of the frame last written out
    const tick = () => {
      raf = requestAnimationFrame(tick);
      const f = liveFrameRef.current;
      const showing =
        f != null &&
        performance.now() - liveFrameRef.at <= IN_PROGRESS_STALL_MS &&
        f.on_track &&
        f.lap_elapsed_ms >= 0 &&
        f.current_lap > lastDone;
      // Bails out without a render while the number is unchanged.
      setLap(showing ? f.current_lap : null);
      if (!showing || liveFrameRef.at === written) return;
      written = liveFrameRef.at;
      if (timeRef.current) timeRef.current.textContent = formatLapTime(f.lap_elapsed_ms);
      if (deltaRef.current) {
        deltaRef.current.textContent = f.delta_ms == null ? "—" : formatDelta(f.delta_ms);
        deltaRef.current.className = `px-2 py-[7px] ${liveDeltaColor(f.delta_ms)}`;
      }
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [lastDone]);

  if (lap == null) return null;
  // First paint straight from the ref; the loop takes over from the next frame.
  const f = liveFrameRef.current;
  const delta = f?.delta_ms ?? null;
  return (
    <tr
      className="rule-row bg-accent/5"
      title="The lap being driven — it joins the table when it completes"
    >
      <td className="py-[7px] pl-4 pr-0" />
      <td className="whitespace-nowrap px-2 py-[7px]">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-1.5 w-1.5 shrink-0 animate-pulse-dot rounded-full bg-accent" />
          {lap}
          <span className="rounded-lg border border-edge px-1.5 text-[10px] text-ink-faint">
            in progress
          </span>
        </span>
      </td>
      <td ref={timeRef} className="px-2 py-[7px] text-ink">
        {formatLapTime(f?.lap_elapsed_ms)}
      </td>
      <td ref={deltaRef} className={`px-2 py-[7px] ${liveDeltaColor(delta)}`}>
        {delta == null ? "—" : formatDelta(delta)}
      </td>
      <td colSpan={trailing} />
    </tr>
  );
}

// Live gap to the session best: green while ahead, red while behind — the
// Live view's convention, not the table's "+0.3 s is near enough" shading.
function liveDeltaColor(ms: number | null): string {
  return ms == null ? "text-ink-ghost" : ms <= 0 ? "text-throttle" : "text-brake";
}

// Off-track excursions ride along with the event code — they are the same
// kind of "what went wrong this lap" count, and the tooltip spells them out.
function EventCell({ lap }: { lap: LapSummary }) {
  const parts = eventParts(lap.event_counts);
  const offTrack = lap.off_track_count ?? -1;
  const offSurvey = lap.off_survey_count ?? -1;
  return (
    <span
      className="inline-flex gap-2"
      title={`${formatEventCounts(lap.event_counts)}${
        offTrack > 0 ? ` · ${offTrack} off-track` : ""
      }${offSurvey > 0 ? ` · ${offSurvey} beyond the surveyed edge` : ""} — lockups · spins · bottoming · kerbs`}
    >
      {parts.length === 0 && <span className="text-ink-ghost">–</span>}
      {parts.map((p) => (
        <span key={p.letter} className={EVENT_COLORS[p.letter]}>
          {p.count}
          {p.letter}
        </span>
      ))}
      {offTrack > 0 && <span className="text-brake">{offTrack}⚠</span>}
      {offSurvey > 0 && <span className="text-brake">·{offSurvey}⚠</span>}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Bulk bar

function BulkBar({
  laps,
  onClear,
  onExclude,
  onExport,
  onDelete,
}: {
  laps: LapSummary[];
  onClear: () => void;
  onExclude: () => void;
  onExport: () => void;
  onDelete: () => void;
}) {
  const numbers = laps.map((l) => l.number).sort((a, b) => b - a);
  return (
    <div className="fixed bottom-[18px] left-1/2 z-30 w-[min(760px,calc(100%-40px))] -translate-x-1/2">
      <div
        className="elevated flex flex-wrap items-center gap-2.5 rounded-lg bg-panel px-3.5 py-2.5"
        role="region"
        aria-label="Selected laps"
      >
        <span className="font-tabular text-[12.5px]">
          {laps.length === 1 ? "1 lap selected" : `${laps.length} laps selected`}
        </span>
        <span className="min-w-0 truncate font-tabular text-[11px] text-ink-faint">
          {numbers
            .slice(0, 6)
            .map((n) => `L${n}`)
            .join(" · ")}
          {numbers.length > 6 ? " …" : ""}
        </span>
        <span className="ml-auto flex flex-wrap gap-1.5">
          <button className="btn" onClick={onClear}>
            Clear
          </button>
          <button className="btn" onClick={onExclude}>
            Exclude from bests
          </button>
          <button className="btn btn-primary" onClick={onExport}>
            Export laps
          </button>
          <button className="btn btn-danger" onClick={onDelete}>
            Delete…
          </button>
        </span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Menu: a small dropdown for the "Export ▾" / "⋯" buttons. Rendered in a
// portal at a fixed position so the lap table's scroll container cannot clip
// it; closes on outside click, Escape, or the page scrolling under it.

type MenuItem =
  | "separator"
  | {
      label: React.ReactNode;
      hint?: string;
      onSelect?: () => void;
      href?: string;
      download?: boolean | string;
      danger?: boolean;
      disabled?: boolean;
      /** A show/hide choice: drawn with a switch. */
      checked?: boolean;
      /** Leave the menu open after selecting (toggles). */
      keepOpen?: boolean;
    };

const ITEM_SELECTOR = "[role^=menuitem]:not(:disabled)";

function Menu({
  label,
  ariaLabel,
  className,
  items,
  disabled = false,
}: {
  label: React.ReactNode;
  ariaLabel: string;
  className: string;
  items: MenuItem[];
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<React.CSSProperties>({});
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    if (!open || !trigger.current) return;
    const r = trigger.current.getBoundingClientRect();
    const height = panel.current?.offsetHeight ?? 0;
    const below = r.bottom + 4 + height <= window.innerHeight;
    setPos({
      right: Math.max(8, window.innerWidth - r.right),
      ...(below ? { top: r.bottom + 4 } : { bottom: window.innerHeight - r.top + 4 }),
    });
  }, [open]);

  const placed = "top" in pos || "bottom" in pos;
  useEffect(() => {
    // After placing: an element still hidden for measuring cannot take focus.
    if (open && placed) panel.current?.querySelector<HTMLElement>(ITEM_SELECTOR)?.focus();
  }, [open, placed]);

  useEffect(() => {
    if (!open) return;
    const close = (e: Event) => {
      if (e.target instanceof Node && panel.current?.contains(e.target)) return;
      if (e.target instanceof Node && trigger.current?.contains(e.target)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        trigger.current?.focus();
      }
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        const els = [
          ...(panel.current?.querySelectorAll<HTMLElement>(ITEM_SELECTOR) ?? []),
        ];
        const i = els.indexOf(document.activeElement as HTMLElement);
        els[(i + (e.key === "ArrowDown" ? 1 : els.length - 1)) % els.length]?.focus();
        e.preventDefault();
      }
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("scroll", close, true);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("scroll", close, true);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const itemClass = (danger?: boolean) =>
    `flex w-full items-center px-3 py-1.5 text-left text-[11.5px] transition-colors hover:bg-panel-2 focus:bg-panel-2 focus:outline-none disabled:cursor-not-allowed disabled:opacity-45 ${
      danger ? "text-brake" : "text-ink-soft"
    }`;

  return (
    <>
      <button
        ref={trigger}
        className={className}
        disabled={disabled}
        aria-label={ariaLabel}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => {
          setPos({});
          setOpen((o) => !o);
        }}
      >
        {label}
      </button>
      {open &&
        createPortal(
          <div
            ref={panel}
            role="menu"
            aria-label={ariaLabel}
            className="elevated fixed z-50 min-w-44 max-w-72 rounded-panel bg-panel py-1"
            style={{ ...pos, visibility: placed ? "visible" : "hidden" }}
          >
            {items.map((item, i) => {
              if (item === "separator") return <div key={i} className="rule my-1" />;
              const select = () => {
                item.onSelect?.();
                if (!item.keepOpen) setOpen(false);
              };
              return item.href ? (
                <a
                  key={i}
                  role="menuitem"
                  href={item.href}
                  download={item.download === true ? "" : item.download || undefined}
                  title={item.hint}
                  onClick={select}
                  className={itemClass(item.danger)}
                >
                  {item.label}
                </a>
              ) : (
                <button
                  key={i}
                  role={item.checked === undefined ? "menuitem" : "menuitemcheckbox"}
                  aria-checked={item.checked}
                  disabled={item.disabled}
                  title={item.hint}
                  onClick={select}
                  className={itemClass(item.danger)}
                >
                  {item.label}
                  {item.checked !== undefined && (
                    // The Toggle switch's look; not the component, which is a
                    // button of its own and cannot sit inside this one.
                    <span
                      aria-hidden
                      className={`relative ml-3 inline-block h-4 w-[30px] shrink-0 rounded-full transition-colors ${
                        item.checked ? "bg-accent-700" : "bg-edge"
                      }`}
                    >
                      <span
                        className={`absolute top-0.5 h-3 w-3 rounded-full transition-[left] duration-150 ${
                          item.checked ? "left-4 bg-accent-200" : "left-0.5 bg-ink-faint"
                        }`}
                      />
                    </span>
                  )}
                </button>
              );
            })}
          </div>,
          document.body,
        )}
    </>
  );
}
