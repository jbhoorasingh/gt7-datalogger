// Sessions view: browse historical sessions, inspect and manage laps,
// export/import laps as JSON, manual "log lap now", and jump into the
// Analysis view with a session or lap pre-selected.

import { useCallback, useEffect, useRef, useState } from "react";
import { LapSparkline } from "@/components/LapSparkline";
import { ConfirmDialog, PromptDialog } from "@/components/ui/Dialog";
import { Tip } from "@/components/ui/Tooltip";
import { api } from "@/lib/api";
import { lapColor } from "@/lib/colors";
import { formatLapTime, formatSpeed, formatTime } from "@/lib/format";
import { openInAnalysis } from "@/lib/router";
import type { LapSummary, SessionSummary } from "@/lib/types";
import { useSettings } from "@/store/settings";
import { useTelemetry } from "@/store/telemetry";
import { toast } from "@/store/toasts";

export function SessionsView() {
  const units = useSettings((s) => s.units);
  const lapEpoch = useTelemetry((s) => s.lapEpoch);
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);
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

  // Only offer categories actually present, so the control disappears
  // entirely on a history recorded before packet C.
  const categories = [
    ...new Set((sessions ?? []).map((s) => s.car_category).filter(Boolean)),
  ].sort();
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

  const refresh = useCallback(() => {
    api.sessions()
      .then(setSessions)
      .catch(() => toast("Could not load sessions", "error"));
  }, []);

  useEffect(refresh, [refresh, lapEpoch]);

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

  // Open a session in the Analysis view (default latest-vs-best selection).
  function analyzeSession(s: SessionSummary) {
    openInAnalysis({ session: s.id });
  }

  return (
    <div className="mx-auto max-w-6xl p-3">
      <div className="mb-3 flex items-center gap-2">
        <h2 className="text-lg font-semibold">Sessions</h2>
        <div className="ml-auto flex gap-2">
          <button onClick={logLapNow} className="btn">Log lap now</button>
          <button onClick={() => fileInput.current?.click()} className="btn">Import lap…</button>
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

      {sessions == null && (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <div key={i} className="skeleton h-12" />
          ))}
        </div>
      )}

      {sessions != null && sessions.length === 0 && (
        <div className="rounded-xl bg-panel p-8 text-center text-ink-dim">
          <div className="mb-1 text-lg text-ink">No sessions recorded yet</div>
          Laps are recorded automatically while you drive — or import a lap file above.
        </div>
      )}

      {categories.length > 0 && (
        <div className="mb-2 flex flex-wrap items-center gap-1">
          <span className="mr-1 text-xs text-ink-dim">Category</span>
          {["", ...categories].map((c) => (
            <button
              key={c || "all"}
              onClick={() => setCategory(c)}
              aria-pressed={active === c}
              className={`rounded-full px-3 py-1 text-xs transition-colors ${
                active === c
                  ? "bg-accent/15 text-accent"
                  : "text-ink-dim hover:bg-panel-2 hover:text-ink"
              }`}
            >
              {c || "All"}
            </button>
          ))}
        </div>
      )}

      {allTags.length > 0 && (
        <div className="mb-2 flex flex-wrap items-center gap-1">
          <span className="mr-1 text-xs text-ink-dim">Tag</span>
          {["", ...allTags].map((t) => (
            <button
              key={t || "all"}
              onClick={() => setTag(t)}
              aria-pressed={activeTag === t}
              className={`rounded-full px-3 py-1 text-xs transition-colors ${
                activeTag === t
                  ? "bg-accent/15 text-accent"
                  : "text-ink-dim hover:bg-panel-2 hover:text-ink"
              }`}
            >
              {t || "All"}
            </button>
          ))}
        </div>
      )}

      <div className="space-y-2">
        {visible.map((s) => (
          <div key={s.id} className="rounded-xl bg-panel">
            <div className="flex w-full items-center gap-4 px-4 py-3">
              {/* Click-to-toggle convenience area. Deliberately a div, not a
                  button: it contains the "name track…" button, and interactive
                  elements must not nest. The chevron button below is the
                  accessible toggle. */}
              <div
                onClick={() => setExpanded(expanded === s.id ? null : s.id)}
                className="flex min-w-0 flex-1 cursor-pointer items-center gap-4 text-left"
              >
                <span className="font-tabular text-sm text-ink-dim">#{s.id}</span>
                <span className="truncate font-medium">{s.car_name}</span>
                {s.car_category && (
                  <span className="shrink-0 rounded-full bg-panel-2 px-2 py-0.5 text-xs text-ink-dim">
                    {s.car_category}
                  </span>
                )}
                {s.bests_excluded && (
                  <span className="shrink-0 rounded-full border border-dashed border-edge px-2 py-0.5 text-xs text-ink-dim">
                    excluded from bests
                  </span>
                )}
                {s.final_position >= 1 && (
                  <Tip
                    content={`Finished P${s.final_position} of ${s.final_total_positions} — ${s.race_laps}-lap race${
                      s.race_time_ms != null
                        ? `, total ${formatLapTime(s.race_time_ms)}`
                        : ""
                    }`}
                  >
                    <span className="shrink-0 rounded-full bg-throttle/10 px-2 py-0.5 font-tabular text-xs text-throttle">
                      P{s.final_position}/{s.final_total_positions}
                    </span>
                  </Tip>
                )}
                {s.track_name ? (
                  <span className="rounded-full bg-accent/10 px-2 py-0.5 text-xs text-accent">
                    {s.track_name}
                  </span>
                ) : (
                  s.lap_count > 0 && (
                    <button
                      className="rounded-full border border-dashed border-edge px-2 py-0.5 text-xs text-ink-dim hover:text-ink"
                      onClick={(e) => {
                        e.stopPropagation();
                        setNaming(s.id);
                      }}
                    >
                      name track…
                    </button>
                  )
                )}
                {(s.tags ?? []).map((t) => (
                  <span
                    key={t}
                    className="shrink-0 rounded-full bg-panel-2 px-2 py-0.5 text-xs text-ink-dim"
                  >
                    #{t}
                  </span>
                ))}
                {s.note && (
                  <Tip content={s.note}>
                    <span className="shrink-0 text-xs text-ink-dim" aria-label="Session note">
                      ✎
                    </span>
                  </Tip>
                )}
                <span className="text-xs text-ink-dim">{formatTime(s.started_at)}</span>
                <span className="ml-auto flex items-center gap-3">
                  {s.lap_count > 1 && <LapSparkline sessionId={s.id} lapCount={s.lap_count} />}
                  <span className="font-tabular text-sm">
                    {s.lap_count} laps
                    {s.best_lap_time_ms != null && (
                      <span className="ml-3 text-accent">{formatLapTime(s.best_lap_time_ms)}</span>
                    )}
                  </span>
                </span>
              </div>
              {s.lap_count > 0 && (
                <Tip content="Open this session in the Analysis view">
                  <button className="btn shrink-0" onClick={() => analyzeSession(s)}>
                    Analyze
                  </button>
                </Tip>
              )}
              <button
                onClick={() => setExpanded(expanded === s.id ? null : s.id)}
                className="text-ink-dim"
                aria-expanded={expanded === s.id}
                aria-label={expanded === s.id ? "Collapse session" : "Expand session"}
              >
                {expanded === s.id ? "▾" : "▸"}
              </button>
            </div>

            {expanded === s.id && (
              <div className="border-t border-edge px-2 pb-2">
                <LapTable
                  laps={laps[s.id] ?? []}
                  units={units}
                  bestMs={s.best_lap_time_ms}
                  onExport={exportLap}
                  onDelete={(id) => setDeletingLap({ sessionId: s.id, lapId: id })}
                  onCompare={(id, refId) =>
                    openInAnalysis({
                      session: s.id,
                      laps: refId != null && refId !== id ? [id, refId] : [id],
                      ref: refId ?? id,
                    })
                  }
                />
                <NotesEditor key={s.id} session={s} onSaved={refresh} />
                <div className="flex justify-end gap-2 px-2 pt-2">
                  <Tip content="Replay recordings and other drivers' laps are indistinguishable from your own driving in telemetry — keeping them off the Bests board is a manual call.">
                    <button className="btn" onClick={() => toggleBestsExcluded(s)}>
                      {s.bests_excluded ? "Include in bests" : "Exclude from bests"}
                    </button>
                  </Tip>
                  <button className="btn-danger" onClick={() => setDeletingSession(s.id)}>
                    Delete session
                  </button>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>

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
    <div className="mx-2 mt-2 space-y-2 rounded-lg bg-panel-2/40 p-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-xs text-ink-dim">Tags</span>
        {tags.map((t) => (
          <span
            key={t}
            className="flex items-center gap-1 rounded-full bg-panel-2 px-2 py-0.5 text-xs"
          >
            #{t}
            <button
              className="text-ink-dim hover:text-brake"
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
          className="w-28 rounded-md border border-edge bg-transparent px-2 py-1 text-xs outline-none placeholder:text-ink-dim/60 focus:border-accent/50"
        />
        {newTag.trim() && (
          <button className="text-xs text-ink-dim hover:text-accent" onClick={addTag}>
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
          className="min-w-0 flex-1 resize-y rounded-md border border-edge bg-transparent px-2 py-1.5 text-xs outline-none placeholder:text-ink-dim/60 focus:border-accent/50"
        />
        <button
          className="btn shrink-0"
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
  if (laps.length === 0) return <div className="p-4 text-sm text-ink-dim">No laps.</div>;
  const bestId = laps.reduce((a, b) => (b.time_ms < a.time_ms ? b : a)).id;
  // Position per lap (#60): only worth a column when the session was a race
  // — a time trial would show a column of dashes.
  const hasPositions = laps.some((l) => (l.race_position ?? -1) >= 1);
  return (
    <div className="overflow-x-auto">
      <table className="w-full font-tabular text-xs">
        <thead>
          <tr className="text-left text-ink-dim">
            {[
              "Lap",
              "Time",
              "Δ best",
              ...(hasPositions ? ["Pos"] : []),
              "Fuel", "Full thr.", "Full brake", "Coast", "Spin", "Events", "Off-track", "Max spd", "",
            ].map((h) => (
              <th key={h} className="px-2 py-2 font-normal">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {laps.map((lap) => {
            const isBest = bestMs != null && lap.time_ms === bestMs;
            const diff = bestMs != null ? lap.time_ms - bestMs : null;
            return (
              <tr key={lap.id} className="border-t border-edge/50 hover:bg-panel-2/50">
                <td className="px-2 py-1.5 text-ink-dim">
                  <span className="flex items-center gap-1.5">
                    <span
                      className="h-2 w-2 shrink-0 rounded-full"
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
                </td>
                <td className={`px-2 py-1.5 ${isBest ? "text-accent" : ""}`}>
                  {formatLapTime(lap.time_ms)}
                </td>
                <td
                  className={`px-2 py-1.5 ${
                    diff == null ? "text-ink-dim" : diff === 0 ? "text-throttle" : "text-brake"
                  }`}
                >
                  {diff == null ? "–" : diff === 0 ? "best" : `+${(diff / 1000).toFixed(3)}`}
                </td>
                {hasPositions && (
                  <td className="px-2 py-1.5">
                    {(lap.race_position ?? -1) >= 1 ? `P${lap.race_position}` : "–"}
                  </td>
                )}
                <td className="px-2 py-1.5">{lap.fuel_consumed.toFixed(2)} L</td>
                <td className="px-2 py-1.5">{lap.full_throttle_pct.toFixed(0)}%</td>
                <td className="px-2 py-1.5">{lap.full_brake_pct.toFixed(0)}%</td>
                <td className="px-2 py-1.5">{lap.coasting_pct.toFixed(0)}%</td>
                <td className="px-2 py-1.5">{lap.tire_spin_pct.toFixed(0)}%</td>
                <td className="px-2 py-1.5 text-ink-dim" title="lockups · spins · bottoming · kerbs">
                  {formatEventCounts(lap.event_counts)}
                </td>
                <td
                  className={`px-2 py-1.5 whitespace-nowrap ${lap.clean_lap === false ? "text-brake" : "text-ink-dim"}`}
                >
                  <span title="Off-track excursions from surface flags (3+ wheels on grass/gravel/dirt) — dash when the lap was recorded without surface data">
                    {lap.off_track_count == null || lap.off_track_count < 0
                      ? "–"
                      : lap.off_track_count > 0
                        ? `${lap.off_track_count} ⚠`
                        : (lap.off_survey_count ?? -1) > 0
                          ? "0"
                          : "clean"}
                  </span>
                  {(lap.off_survey_count ?? -1) > 0 && (
                    <span title="Excursions beyond the surveyed road edge — the car left the mapped road surface (paved runoff counts)">
                      {` · ${lap.off_survey_count} ⚠`}
                    </span>
                  )}
                </td>
                <td className="px-2 py-1.5">{formatSpeed(lap.max_speed, units)}</td>
                <td className="px-2 py-1.5 text-right whitespace-nowrap">
                  <Tip content="Compare against the session's best lap in Analysis">
                    <button
                      className="mr-2 text-ink-dim hover:text-accent"
                      onClick={() => onCompare(lap.id, lap.id === bestId ? null : bestId)}
                    >
                      compare
                    </button>
                  </Tip>
                  <Tip content="Open Analysis with this lap as the reference">
                    <button
                      className="mr-2 text-ink-dim hover:text-accent"
                      onClick={() => onCompare(lap.id, lap.id)}
                    >
                      set ref
                    </button>
                  </Tip>
                  <button className="mr-2 text-ink-dim hover:text-ink" onClick={() => onExport(lap.id)}>
                    json
                  </button>
                  <a
                    className="mr-2 text-ink-dim hover:text-ink"
                    href={api.lapCsvUrl(lap.id)}
                    download
                    title="MoTeC-compatible CSV"
                  >
                    csv
                  </a>
                  <button className="text-ink-dim hover:text-brake" onClick={() => onDelete(lap.id)}>
                    delete
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
