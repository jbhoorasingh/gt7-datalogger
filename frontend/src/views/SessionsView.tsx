// Sessions view: browse historical sessions, inspect and manage laps,
// export/import laps as JSON, manual "log lap now", and jump into the
// Analysis view with a session or lap pre-selected.
//
// Also hosts the personal-bests board as a sub-tab (#26) — same history,
// two readings of it — so the category filter and the top-level actions are
// shared rather than duplicated across two nav entries.

import { useCallback, useEffect, useRef, useState } from "react";
import { BestsBoard } from "@/components/BestsBoard";
import { LapSparkline } from "@/components/LapSparkline";
import { ConfirmDialog, PromptDialog } from "@/components/ui/Dialog";
import { Tip } from "@/components/ui/Tooltip";
import { api } from "@/lib/api";
import { lapColor } from "@/lib/colors";
import { formatLapTime, formatSpeed, formatTime, formatTimeShort } from "@/lib/format";
import { openInAnalysis } from "@/lib/router";
import type { LapSummary, PersonalBest, SessionSummary } from "@/lib/types";
import { useSettings } from "@/store/settings";
import { useTelemetry } from "@/store/telemetry";
import { toast } from "@/store/toasts";

type SubTab = "sessions" | "bests";

// Session row and its header share one template so the columns line up:
// # · car/circuit · started · trend · laps · best · analyze · chevron.
const ROW_COLS =
  "44px minmax(220px,1.6fr) minmax(96px,150px) 100px 52px 76px 72px 18px";

export function SessionsView({ subTab = "sessions" }: { subTab?: SubTab }) {
  const units = useSettings((s) => s.units);
  const lapEpoch = useTelemetry((s) => s.lapEpoch);
  const [sub, setSub] = useState<SubTab>(subTab);
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);
  const [bests, setBests] = useState<PersonalBest[] | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [laps, setLaps] = useState<Record<number, LapSummary[]>>({});
  const [naming, setNaming] = useState<number | null>(null); // session id
  const [deletingSession, setDeletingSession] = useState<number | null>(null);
  const [deletingLap, setDeletingLap] = useState<{ sessionId: number; lapId: number } | null>(null);
  // Car category (packet C) as a grouping key: "show me only the Gr.3 runs".
  // Empty string = no filter; sessions recorded without packet C have no
  // category and are only reachable from "All".
  const [category, setCategory] = useState("");
  // User-set session tags as a second, orthogonal filter (#25).
  const [tag, setTag] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);

  // A pasted #/bests link arrives as a prop; keep following it if the URL
  // changes under us, while in-page clicks drive the local state.
  useEffect(() => setSub(subTab), [subTab]);

  // Only offer categories actually present in the board being shown, so the
  // control disappears entirely on a history recorded before packet C.
  const source = sub === "bests" ? (bests ?? []) : (sessions ?? []);
  const categories = [...new Set(source.map((s) => s.car_category).filter(Boolean))].sort();
  const allTags = [...new Set((sessions ?? []).flatMap((s) => s.tags ?? []))].sort();
  // Deleting the last session of the filtered category would otherwise leave
  // the filter set to a value with no chip and no rows — a blank list with no
  // way back. Fall back to unfiltered whenever the selection stops existing.
  // Same for a tag removed from its last session.
  const active = categories.includes(category) ? category : "";
  const activeTag = allTags.includes(tag) ? tag : "";
  const visible = (sessions ?? []).filter(
    (s) =>
      (!active || s.car_category === active) &&
      (!activeTag || (s.tags ?? []).includes(activeTag)),
  );
  const visibleBests = bests?.filter((b) => !active || b.car_category === active) ?? null;

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

  useEffect(() => {
    if (expanded == null) return;
    api.sessionLaps(expanded).then((ls) => setLaps((cur) => ({ ...cur, [expanded]: ls })));
  }, [expanded, lapEpoch]);

  async function exportLap(id: number) {
    const data = await api.exportLap(id);
    const blob = new Blob([JSON.stringify(data)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `gt7-lap-${id}.json`;
    a.click();
    // Revoking synchronously can cancel the download in some browsers.
    window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
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
      const ls = laps[sessionId] ?? (await api.sessionLaps(sessionId));
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

  return (
    <div className="mx-auto flex max-w-[1200px] flex-col gap-3">
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

      {/* Tags are a second, orthogonal filter — only rendered once any
          session actually carries one. */}
      {sub === "sessions" && allTags.length > 0 && (
        <div className="-mt-1 flex flex-wrap items-center gap-1.5 text-[11px]">
          <span className="section-header mr-1">Tag</span>
          {["", ...allTags].map((t) => (
            <button
              key={t || "all"}
              onClick={() => setTag(t)}
              aria-pressed={activeTag === t}
              className={`rounded-[11px] px-2.5 py-0.5 transition-colors ${
                activeTag === t ? "bg-accent/15 text-accent-300" : "text-ink-faint hover:text-ink"
              }`}
            >
              {t || "All"}
            </button>
          ))}
        </div>
      )}

      {sub === "bests" ? (
        <BestsBoard bests={visibleBests} />
      ) : (
        <>
          {sessions == null && (
            <div className="flex flex-col gap-1.5">
              {[0, 1, 2].map((i) => (
                <div key={i} className="skeleton h-[46px]" />
              ))}
            </div>
          )}

          {sessions != null && sessions.length === 0 && (
            <div className="panel p-8 text-center text-ink-dim">
              <div className="mb-1 text-base text-ink">No sessions recorded yet</div>
              Laps are recorded automatically while you drive — or import a lap file above.
            </div>
          )}

          {visible.length > 0 && (
            <div className="flex flex-col">
              <div
                className="section-header grid gap-3 px-3.5 py-1.5 text-[9.5px] tracking-[0.12em]"
                style={{ gridTemplateColumns: ROW_COLS }}
              >
                <span>#</span>
                <span>Car · circuit</span>
                <span>Started</span>
                <span>Trend</span>
                <span className="text-right">Laps</span>
                <span className="text-right">Best</span>
                <span />
                <span />
              </div>

              {visible.map((s) => (
                <SessionRow
                  key={s.id}
                  session={s}
                  units={units}
                  open={expanded === s.id}
                  laps={laps[s.id] ?? []}
                  onToggle={() => setExpanded(expanded === s.id ? null : s.id)}
                  onNameTrack={() => setNaming(s.id)}
                  onExportLap={exportLap}
                  onDeleteLap={(lapId) => setDeletingLap({ sessionId: s.id, lapId })}
                  onDeleteSession={() => setDeletingSession(s.id)}
                  onToggleExcluded={() => toggleBestsExcluded(s)}
                  onSaved={refresh}
                />
              ))}
            </div>
          )}
        </>
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
          await api.deleteSession(id);
          setExpanded((cur) => (cur === id ? null : cur));
          toast(`Session #${id} deleted`, "success");
          refresh();
        }}
        onCancel={() => setDeletingSession(null)}
      />

      <ConfirmDialog
        open={deletingLap != null}
        title="Delete lap?"
        body="The lap and its telemetry samples will be removed. This cannot be undone."
        confirmLabel="Delete lap"
        danger
        onConfirm={async () => {
          const { sessionId, lapId } = deletingLap!;
          setDeletingLap(null);
          await api.deleteLap(lapId);
          setLaps((cur) => ({
            ...cur,
            [sessionId]: (cur[sessionId] ?? []).filter((l) => l.id !== lapId),
          }));
          toast("Lap deleted", "success");
          refresh();
        }}
        onCancel={() => setDeletingLap(null)}
      />
    </div>
  );
}

function SessionRow({
  session: s,
  units,
  open,
  laps,
  onToggle,
  onNameTrack,
  onExportLap,
  onDeleteLap,
  onDeleteSession,
  onToggleExcluded,
  onSaved,
}: {
  session: SessionSummary;
  units: "metric" | "imperial";
  open: boolean;
  laps: LapSummary[];
  onToggle: () => void;
  onNameTrack: () => void;
  onExportLap: (id: number) => void;
  onDeleteLap: (id: number) => void;
  onDeleteSession: () => void;
  onToggleExcluded: () => void;
  onSaved: () => void;
}) {
  return (
    <div className="panel mb-1.5 overflow-hidden rounded-[7px]">
      {/* Click-to-toggle convenience area. Deliberately a div, not a button:
          it contains the "name track…" and "Analyze" buttons, and interactive
          elements must not nest. The chevron below is the accessible toggle. */}
      <div
        onClick={onToggle}
        className="grid cursor-pointer items-center gap-3 px-3.5 py-2.5 transition-colors hover:bg-panel-2/70"
        style={{ gridTemplateColumns: ROW_COLS }}
      >
        <span className="font-tabular text-xs text-ink-faint">#{s.id}</span>

        <span className="flex min-w-0 flex-col items-start gap-1">
          <span className="text-[12.5px] font-medium">{s.car_name}</span>
          <span className="flex flex-wrap gap-1.5">
            {s.car_category && (
              <span className="rounded-[9px] border border-edge px-2 py-px text-[10px] text-ink-dim">
                {s.car_category}
              </span>
            )}
            {s.track_name ? (
              <span className="whitespace-nowrap rounded-[9px] border border-accent/38 bg-accent/22 px-2.5 py-px text-[10px] font-medium text-accent-200">
                {s.track_name}
              </span>
            ) : (
              s.lap_count > 0 && (
                <button
                  className="rounded-[9px] border border-dashed border-edge px-2 py-px text-[10px] text-ink-faint transition-colors hover:border-accent hover:text-accent"
                  onClick={(e) => {
                    e.stopPropagation();
                    onNameTrack();
                  }}
                >
                  name track…
                </button>
              )
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
            {(s.tags ?? []).map((t) => (
              <span
                key={t}
                className="rounded-[9px] bg-edge px-2 py-px text-[10px] text-ink-soft"
              >
                #{t}
              </span>
            ))}
            {s.note && (
              <Tip content={s.note}>
                <span className="text-[10px] text-ink-faint" aria-label="Session note">
                  ✎
                </span>
              </Tip>
            )}
          </span>
        </span>

        <span className="font-tabular text-[11px] text-ink-faint" title={formatTime(s.started_at)}>
          {formatTimeShort(s.started_at)}
        </span>

        <span>{s.lap_count > 1 && <LapSparkline sessionId={s.id} lapCount={s.lap_count} />}</span>

        <span className="text-right font-tabular text-xs">{s.lap_count}</span>
        <span className="text-right font-tabular text-xs text-accent">
          {s.best_lap_time_ms != null ? formatLapTime(s.best_lap_time_ms) : "–"}
        </span>

        <span className="text-right">
          {s.lap_count > 0 && (
            <Tip content="Open this session in the Analysis view">
              <button
                className="btn px-2.5 py-[3px] text-[11px] hover:border-accent hover:text-accent"
                onClick={(e) => {
                  e.stopPropagation();
                  openInAnalysis({ session: s.id });
                }}
              >
                Analyze
              </button>
            </Tip>
          )}
        </span>

        <button
          onClick={(e) => {
            e.stopPropagation();
            onToggle();
          }}
          className="text-center text-[10px] text-ink-faint"
          aria-expanded={open}
          aria-label={open ? "Collapse session" : "Expand session"}
        >
          {open ? "▾" : "▸"}
        </button>
      </div>

      {open && (
        <>
          <div className="rule" />
          <div className="flex flex-col gap-2.5 px-3.5 py-2.5">
            <LapTable
              laps={laps}
              units={units}
              bestMs={s.best_lap_time_ms}
              onExport={onExportLap}
              onDelete={onDeleteLap}
              onCompare={(id, refId) =>
                openInAnalysis({
                  session: s.id,
                  laps: refId != null && refId !== id ? [id, refId] : [id],
                  ref: refId ?? id,
                })
              }
            />
            <NotesEditor key={s.id} session={s} onSaved={onSaved} />
            <div className="flex justify-end gap-2">
              <Tip content="Replay recordings and other drivers' laps are indistinguishable from your own driving in telemetry — keeping them off the Bests board is a manual call.">
                <button
                  className="btn px-3 py-1 hover:border-accent hover:text-accent"
                  onClick={onToggleExcluded}
                >
                  {s.bests_excluded ? "Include in bests" : "Exclude from bests"}
                </button>
              </Tip>
              <button className="btn btn-danger px-3 py-1" onClick={onDeleteSession}>
                Delete session
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// Note + tag editor for one expanded session (#25). The note is a local
// draft saved explicitly; tags save on every add/remove (each is one small,
// deliberate edit). key={session.id} remounts it per session, so a draft
// never bleeds from one session into another.
function NotesEditor({
  session,
  onSaved,
}: {
  session: SessionSummary;
  onSaved: () => void;
}) {
  const [note, setNote] = useState(session.note);
  const [newTag, setNewTag] = useState("");
  const tags = session.tags ?? [];

  async function save(patch: { note?: string; tags?: string[] }) {
    try {
      await api.updateSession(session.id, patch);
      onSaved();
    } catch {
      toast("Could not update session", "error");
    }
  }

  function addTag() {
    const t = newTag.trim();
    if (!t) return;
    if (t.includes(",")) {
      toast("Tags cannot contain commas", "error");
      return;
    }
    setNewTag("");
    // Same case-insensitive dedupe the server applies, so the UI never shows
    // an add that the PATCH would collapse.
    if (tags.some((x) => x.toLowerCase() === t.toLowerCase())) return;
    void save({ tags: [...tags, t] });
  }

  return (
    <div className="flex flex-col gap-2 rounded-md bg-panel-2 px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        <span className="text-ink-faint">Tags</span>
        {tags.map((t) => (
          <span
            key={t}
            className="flex items-center gap-1 rounded-[9px] bg-edge px-2.5 py-px text-ink-soft"
          >
            #{t}
            <button
              className="text-ink-faint transition-colors hover:text-brake"
              aria-label={`Remove tag ${t}`}
              onClick={() => void save({ tags: tags.filter((x) => x !== t) })}
            >
              ×
            </button>
          </span>
        ))}
        <input
          value={newTag}
          onChange={(e) => setNewTag(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") addTag();
          }}
          maxLength={40}
          placeholder="add tag…"
          className="w-28 rounded-[5px] border border-edge bg-transparent px-2.5 py-[3px] text-[11px] outline-none placeholder:text-ink-ghost focus:border-accent"
        />
        {newTag.trim() && (
          <button className="text-[11px] text-ink-faint hover:text-accent" onClick={addTag}>
            add
          </button>
        )}
      </div>
      <div className="flex items-start gap-2">
        <textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={2}
          maxLength={500}
          placeholder="Notes — setup changes, conditions, what to try next…"
          className="min-h-[34px] min-w-0 flex-1 resize-y rounded-[5px] border border-edge bg-transparent px-2.5 py-[7px] text-[11.5px] outline-none placeholder:text-ink-ghost focus:border-accent"
        />
        <button
          className="btn shrink-0 px-3 py-[5px]"
          disabled={note === session.note}
          onClick={() => void save({ note })}
        >
          Save note
        </button>
      </div>
    </div>
  );
}

// "2L·1S·3B" — lockups, wheelspins, bottoming, kerbs; dash when clean/unknown
function formatEventCounts(counts?: Record<string, number>): string {
  if (!counts) return "–";
  const parts = (
    [["lockup", "L"], ["wheelspin", "S"], ["bottoming", "B"], ["kerb", "K"]] as const
  )
    .filter(([type]) => (counts[type] ?? 0) > 0)
    .map(([type, letter]) => `${counts[type]}${letter}`);
  return parts.length > 0 ? parts.join("·") : "–";
}

function LapTable({
  laps,
  units,
  bestMs,
  onExport,
  onDelete,
  onCompare,
}: {
  laps: LapSummary[];
  units: "metric" | "imperial";
  bestMs: number | null;
  onExport: (id: number) => void;
  onDelete: (id: number) => void;
  onCompare: (id: number, refId: number | null) => void;
}) {
  if (laps.length === 0) return <div className="text-[11.5px] text-ink-faint">No laps.</div>;
  const bestId = laps.reduce((a, b) => (b.time_ms < a.time_ms ? b : a)).id;
  // Position per lap (#60): only worth a column when the session was a race
  // — a time trial would show a column of dashes.
  const hasPositions = laps.some((l) => (l.race_position ?? -1) >= 1);
  const cols = `40px 84px 66px ${hasPositions ? "46px " : ""}60px 56px 56px 52px 46px 64px 66px 1fr`;

  return (
    <div className="overflow-x-auto">
      <div className="min-w-[860px]">
        <div
          className="section-header grid gap-2 py-1 text-[9.5px] tracking-[0.1em]"
          style={{ gridTemplateColumns: cols }}
        >
          <span>Lap</span>
          <span>Time</span>
          <span>Δ best</span>
          {hasPositions && <span>Pos</span>}
          <span>Fuel</span>
          <span>Full thr.</span>
          <span>Full brk</span>
          <span>Coast</span>
          <span>Spin</span>
          <span>Events</span>
          <span>Max spd</span>
          <span />
        </div>

        {laps.map((lap) => {
          const isBest = bestMs != null && lap.time_ms === bestMs;
          const diff = bestMs != null ? lap.time_ms - bestMs : null;
          const offTrack = lap.off_track_count ?? -1;
          const offSurvey = lap.off_survey_count ?? -1;
          return (
            <div
              key={lap.id}
              className="rule-row grid items-baseline gap-2 py-[5px] font-tabular text-[11.5px] transition-colors hover:bg-panel-2"
              style={{ gridTemplateColumns: cols }}
            >
              <span className="flex items-center gap-1.5 text-ink-faint">
                <span
                  className="h-[7px] w-[7px] shrink-0 rounded-full"
                  style={{ backgroundColor: lapColor(lap.id) }}
                  title="This lap's color in charts and maps"
                />
                {lap.number}
                {lap.salvaged && (
                  <span title="Salvaged from a stream that ended at the line (replay ending) — the time is GT7's own">
                    ⟲
                  </span>
                )}
              </span>
              <span className={isBest ? "text-accent" : ""}>{formatLapTime(lap.time_ms)}</span>
              <span
                className={
                  diff == null ? "text-ink-faint" : diff === 0 ? "text-throttle" : "text-brake"
                }
              >
                {diff == null ? "–" : diff === 0 ? "best" : `+${(diff / 1000).toFixed(3)}`}
              </span>
              {hasPositions && (
                <span>{(lap.race_position ?? -1) >= 1 ? `P${lap.race_position}` : "–"}</span>
              )}
              <span>{lap.fuel_consumed.toFixed(2)} L</span>
              <span>{lap.full_throttle_pct.toFixed(0)}%</span>
              <span>{lap.full_brake_pct.toFixed(0)}%</span>
              <span>{lap.coasting_pct.toFixed(0)}%</span>
              <span>{lap.tire_spin_pct.toFixed(0)}%</span>
              {/* Off-track excursions ride along with the event code — they
                  are the same kind of "what went wrong this lap" count, and
                  the tooltip spells the letters out. */}
              <span
                className={offTrack > 0 || offSurvey > 0 ? "text-brake" : "text-ink-faint"}
                title="lockups · spins · bottoming · kerbs, then off-track excursions (surface flags · beyond the surveyed edge)"
              >
                {formatEventCounts(lap.event_counts)}
                {offTrack > 0 && ` ${offTrack}⚠`}
                {offSurvey > 0 && `·${offSurvey}⚠`}
              </span>
              <span>{formatSpeed(lap.max_speed, units)}</span>
              <span className="whitespace-nowrap text-right text-[10.5px] text-ink-faint">
                <Tip content="Compare against the session's best lap in Analysis">
                  <button
                    className="transition-colors hover:text-accent"
                    onClick={() => onCompare(lap.id, lap.id === bestId ? null : bestId)}
                  >
                    compare
                  </button>
                </Tip>
                {" · "}
                <Tip content="Open Analysis with this lap as the reference">
                  <button
                    className="transition-colors hover:text-accent"
                    onClick={() => onCompare(lap.id, lap.id)}
                  >
                    set ref
                  </button>
                </Tip>
                {" · "}
                <button
                  className="transition-colors hover:text-accent"
                  onClick={() => onExport(lap.id)}
                >
                  json
                </button>
                {" · "}
                <a
                  className="transition-colors hover:text-accent"
                  href={api.lapCsvUrl(lap.id)}
                  download
                  title="MoTeC-compatible CSV"
                >
                  csv
                </a>
                {" · "}
                <button
                  className="transition-colors hover:text-brake"
                  onClick={() => onDelete(lap.id)}
                >
                  delete
                </button>
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
