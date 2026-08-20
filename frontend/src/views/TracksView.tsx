// Track & survey management (#46): what do I have, and what is missing.
//
// Three sources of track knowledge exist — the DB's named tracks (which is
// what makes auto-identification work), the survey bundles under
// data/track-bundles, and the bundled official GT7 catalog — and until this
// view they were never shown together. The Survey tab listed bundles as
// resume chips, Sessions let you name a track, and the catalog only ever
// appeared as autocomplete, so nothing on any screen said that having a
// bundle and having auto-identification are different things. That gap is how
// a survey ran for ~55 minutes attached to no circuit at all (#45) with
// nothing reporting it.
//
// So the table is built around the DISAGREEMENTS: a bundle with no named
// track, a named track nobody has surveyed, two near-miss spellings of one
// circuit, a bundle at 4 % elevation because it predates elevation capture and
// only re-driving fills it in. Every one of those has an action next to it.

import { useCallback, useEffect, useRef, useState } from "react";
import { CornerEditor } from "@/components/tracks/CornerEditor";
import { ConfirmDialog, PromptDialog } from "@/components/ui/Dialog";
import { Tip } from "@/components/ui/Tooltip";
import { api } from "@/lib/api";
import { getAdminToken, type SharedBundles } from "@/lib/api";
import type { SurveyLog, TrackOverview, TrackOverviewRow } from "@/lib/types";
import { toast } from "@/store/toasts";

const toastSuccess = (text: string) => toast(text, "success");
const toastError = (text: string) => toast(text, "error");

function bytes(n: number): string {
  if (n > 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  if (n > 1024) return `${Math.round(n / 1024)} kB`;
  return `${n} B`;
}

function when(iso: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

/** A yes/no fact about a track, coloured by whether it is a gap. */
function Flag({ ok, label, title }: { ok: boolean; label: string; title: string }) {
  return (
    <Tip content={title}>
      <span
        className={`rounded-full px-2 py-0.5 text-xs ${
          ok ? "bg-throttle/15 text-throttle" : "border border-dashed border-edge text-ink-dim"
        }`}
      >
        {ok ? label : `no ${label}`}
      </span>
    </Tip>
  );
}

export function TracksView() {
  const [data, setData] = useState<TrackOverview | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<TrackOverviewRow | null>(null);
  const [renaming, setRenaming] = useState<TrackOverviewRow | null>(null);
  const [deleting, setDeleting] = useState<TrackOverviewRow | null>(null);
  const [assigning, setAssigning] = useState<SurveyLog | null>(null);
  // The shared repo's offerings (#47). Null until answered; an unreachable
  // repo shows AS unreachable rather than as "not configured".
  const [shared, setShared] = useState<SharedBundles | null>(null);
  const [sharedError, setSharedError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const logInput = useRef<HTMLInputElement>(null);
  const importTarget = useRef<string | undefined>(undefined);

  const refresh = useCallback(() => {
    api
      .trackOverview()
      .then((d) => {
        setData(d);
        setError("");
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(refresh, [refresh]);

  useEffect(() => {
    let live = true;
    api.bundles
      .shared()
      .then((s) => {
        if (!live) return;
        setShared(s);
        setSharedError("");
      })
      .catch((e: Error) => live && setSharedError(e.message));
    return () => {
      live = false;
    };
  }, []);

  const run = async (what: string, fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
      toastSuccess(what);
      refresh();
    } catch (e) {
      toastError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  // Not routed through `run`: the interesting part of the result is WHICH
  // circuits were recognised and how many sessions each of them claimed, and
  // "no match at all" is a perfectly good outcome rather than a failure.
  const onIdentify = async () => {
    setBusy(true);
    try {
      const r = await api.identifySessions();
      const breakdown = Object.entries(r.tracks)
        .map(([track, n]) => `${n}× ${track}`)
        .join(", ");
      if (r.identified === 0) {
        toast(`No surveyed circuit matched any of the ${r.checked} unlabelled sessions`);
      } else {
        toastSuccess(`Named ${r.identified} of ${r.checked} sessions — ${breakdown}`);
      }
      refresh();
    } catch (e) {
      toastError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const onImportFile = async (file: File) => {
    const target = importTarget.current;
    importTarget.current = undefined;
    let doc: unknown;
    try {
      doc = JSON.parse(await file.text());
    } catch {
      toastError(`${file.name} is not JSON`);
      return;
    }
    await run("Bundle merged", async () => {
      const result = await api.bundles.import(doc, target);
      toastSuccess(
        `${result.track}: +${result.added_points} m of border ` +
          `(${result.points} total, ${result.sources} source${result.sources === 1 ? "" : "s"})`,
      );
      if (result.corners_kept) {
        toastSuccess("Kept your own corner labels — the imported ones were dropped");
      }
    });
  };

  const onPullShared = async (slug: string) => {
    await run("Bundle pulled", async () => {
      const result = await api.bundles.pullShared(slug);
      toastSuccess(
        `${result.track}: +${result.added_points} m of border ` +
          `(${result.points} total, ${result.sources} source${result.sources === 1 ? "" : "s"})`,
      );
      if (result.corners_kept) {
        toastSuccess("Kept your own corner labels — the pulled ones were dropped");
      }
    });
  };

  // Uploading only lands the file; the run still gets merged the normal way —
  // by assigning it to a circuit, exactly as an orphaned local run would be.
  const onUploadLog = async (file: File) => {
    await run("Log uploaded", async () => {
      const r = await api.survey.uploadLog(file);
      toastSuccess(
        `${r.name}: ${r.marks.toLocaleString()} marks · ` +
          `${r.transitions.toLocaleString()} transitions` +
          (r.track ? ` · ${r.track}` : " · unassigned"),
      );
    });
  };

  if (editing) {
    return (
      <div className="mx-auto max-w-6xl p-3">
        <CornerEditor
          slug={editing.slug}
          trackName={editing.name}
          official={editing.official}
          onClose={() => setEditing(null)}
          onSaved={refresh}
        />
      </div>
    );
  }

  const rows = data?.tracks ?? [];
  const orphans = (data?.logs ?? []).filter((l) => l.orphaned);
  const knownNames = rows.map((r) => r.name);
  // Identification needs something to match against; with no bundle at all
  // the button would only ever be able to say "nothing to compare with".
  const bundleCount = rows.filter((r) => r.bundle != null).length;

  return (
    <div className="mx-auto max-w-6xl space-y-3 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-lg font-semibold">Tracks</h2>
        <span className="text-xs text-ink-dim">
          named tracks, survey bundles and the official catalog, in one place
        </span>
        <div className="ml-auto flex items-center gap-2">
          <Tip content="Match every unlabelled session against the surveyed circuits and name the ones that were driven on them. New sessions do this by themselves.">
            <button
              className="btn"
              disabled={busy || bundleCount === 0}
              onClick={onIdentify}
            >
              Identify sessions
            </button>
          </Tip>
          <button
            className="btn"
            disabled={busy}
            onClick={() => {
              importTarget.current = undefined;
              fileInput.current?.click();
            }}
          >
            Import bundle…
          </button>
          <input
            ref={fileInput}
            type="file"
            accept="application/json,.json"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (file) void onImportFile(file);
            }}
          />
          <Tip content="Land a survey run's raw JSONL from another installation. It appears in the log list — assign it to a circuit to merge its evidence.">
            <button
              className="btn"
              disabled={busy}
              onClick={() => logInput.current?.click()}
            >
              Upload survey log…
            </button>
          </Tip>
          <input
            ref={logInput}
            type="file"
            accept=".jsonl,application/jsonl"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (file) void onUploadLog(file);
            }}
          />
        </div>
      </div>

      {error && <div className="rounded-xl bg-panel p-3 text-sm text-brake">{error}</div>}

      {orphans.length > 0 && (
        <div className="rounded-xl border border-warn/40 bg-panel p-3">
          <h3 className="text-sm font-semibold text-warn">
            {orphans.length} survey run{orphans.length === 1 ? "" : "s"} went nowhere
          </h3>
          <p className="mt-1 text-xs text-ink-dim">
            A survey with no circuit label saves no bundle at all — these runs exist only
            as their logs. The log is a complete record, so assigning one now merges it
            exactly as if the circuit had been named while driving.
          </p>
          <ul className="mt-2 space-y-1.5">
            {orphans.map((log) => (
              <li
                key={log.name}
                className="flex flex-wrap items-center gap-2 rounded-lg border border-edge px-2 py-1.5 text-xs"
              >
                <span className="font-tabular">{when(log.started_at)}</span>
                <span className="text-ink-dim">
                  {log.marks.toLocaleString()} marks · {log.transitions.toLocaleString()}{" "}
                  transitions · {bytes(log.bytes)}
                </span>
                <a
                  className="btn ml-auto"
                  href={api.survey.logDownloadUrl(log.name)}
                  download={log.name}
                >
                  Download
                </a>
                <button className="btn" disabled={busy} onClick={() => setAssigning(log)}>
                  Assign to a track…
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {data && rows.length === 0 && (
        <div className="rounded-xl bg-panel p-8 text-center text-sm text-ink-dim">
          Nothing yet. Name a track from a lap in Sessions, or run a survey.
        </div>
      )}

      <div className="space-y-2">
        {rows.map((row) => {
          const b = row.bundle;
          return (
            <div key={row.slug} className="rounded-xl bg-panel p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-semibold">{row.name}</span>
                {/* Either source identifies a session, so the chip reflects
                    whether identification WORKS here — not whether one
                    particular mechanism is present. Saying "no auto-ID" on a
                    surveyed circuit would send someone off to name a track
                    that already recognises itself. */}
                <Flag
                  ok={row.named || b != null}
                  label="auto-ID"
                  title={
                    row.provenance === "user"
                      ? "You named this circuit, so sessions here identify themselves — and your name outranks any shipped signature"
                      : row.provenance === "seed"
                        ? "Identified by a signature that shipped with the app, not one you made. Naming it yourself from a lap in Sessions replaces it."
                        : b != null
                          ? "Sessions here identify themselves by matching the surveyed road — no signature needed"
                          : "Neither a geometry signature nor a survey — sessions here will NOT be identified. Name it from a lap in Sessions, or survey it."
                  }
                />
                {/* Only worth its own chip when it is the ONLY thing naming
                    the circuit: once you have named or surveyed it, where the
                    shipped signature came from stops being your problem. */}
                {row.provenance === "seed" && b == null && (
                  <span
                    className="rounded-full bg-panel-2 px-2 py-0.5 text-[11px] text-ink-dim"
                    title="This name came from the shipped signature set, computed from a published lap — not from anything driven or surveyed on this installation."
                  >
                    shipped
                  </span>
                )}
                <Flag
                  ok={b != null}
                  label="survey"
                  title={
                    b
                      ? `${b.points.toLocaleString()} m of border from ${b.runs} run(s)`
                      : "Nothing surveyed here yet"
                  }
                />
                <Flag
                  ok={row.official != null}
                  label="official layout"
                  title={
                    row.official
                      ? `Confirmed as ${row.official.official_name} (${row.official.turns} turns)`
                      : "Not matched to an official GT7 layout — GT7 broadcasts no track id, so this is a human decision"
                  }
                />
                {row.sessions > 0 && (
                  <span className="text-xs text-ink-dim">
                    {row.sessions} session{row.sessions === 1 ? "" : "s"}
                  </span>
                )}
              </div>

              {b && (
                <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 font-tabular text-xs text-ink-dim">
                  {/* Border metres, NOT lap length — one record per metre per
                      side. Sitting next to an official layout length ("8
                      turns, 1,706 m") the bare word "mapped" reads as a
                      contradiction. */}
                  <Tip content="Metres of border evidence — one record per metre per side, so a lap's worth of track is roughly twice its length">
                    <span>{b.points.toLocaleString()} m of border</span>
                  </Tip>
                  <span>
                    {b.runs} run{b.runs === 1 ? "" : "s"}
                    {b.sources > 1 && ` · ${b.sources} sources`}
                  </span>
                  <Tip content="Elevation only fills in by RE-DRIVING a metre: bundles started before elevation capture sit near 0 % until their ground is driven again">
                    <span className={b.elevation_pct < 50 ? "text-warn" : undefined}>
                      {b.elevation_pct}% elevation
                    </span>
                  </Tip>
                  <Tip content="Start/finish line located from lap rollovers; needs repeat crossings to be confident">
                    <span className={b.finish_crossings === 0 ? "text-warn" : undefined}>
                      {b.finish_crossings > 0 ? "finish line located" : "no finish line"}
                    </span>
                  </Tip>
                  {b.coverage && (
                    <Tip content="How much of each border the evidence establishes, measured against the compiled boundary — gaps count against it, and ✓closed means both borders form complete loops">
                      <span
                        className={
                          b.coverage.L.closed && b.coverage.R.closed
                            ? undefined
                            : "text-warn"
                        }
                      >
                        borders L {b.coverage.L.pct}% · R {b.coverage.R.pct}% · road{" "}
                        {b.coverage.road_pct}%
                        {b.coverage.L.closed && b.coverage.R.closed && " ✓closed"}
                      </span>
                    </Tip>
                  )}
                  <span>
                    {b.corners} corner{b.corners === 1 ? "" : "s"} labelled
                    {row.official?.turns ? ` of ${row.official.turns}` : ""}
                  </span>
                  <span>updated {when(b.updated_at)}</span>
                </div>
              )}

              {!row.official && row.suggestion && (
                <div className="mt-2 flex flex-wrap items-center gap-2 rounded-lg border border-dashed border-edge px-2 py-1.5 text-xs">
                  <span className="text-ink-dim">Looks like</span>
                  <span>{row.suggestion.official_name}</span>
                  <span className="text-ink-dim">
                    ({row.suggestion.turns} turns, {row.suggestion.length_m} m —{" "}
                    {row.suggestion.why})
                  </span>
                  <button
                    className="btn ml-auto"
                    disabled={busy || !b}
                    title={
                      b
                        ? "Confirm the match — nothing is inferred silently, because GT7 broadcasts no track identifier"
                        : "Needs a survey bundle to record the match in"
                    }
                    onClick={() =>
                      void run("Layout confirmed", () => {
                        const s = row.suggestion!;
                        // The suggestion's reasoning is for the human reading
                        // it; what gets stored is the match itself.
                        return api.bundles.setOfficial(row.slug, {
                          track: s.track,
                          layout: s.layout,
                          official_id: s.official_id,
                          official_name: s.official_name,
                          turns: s.turns,
                          length_m: s.length_m,
                          reverse: s.reverse,
                        });
                      })
                    }
                  >
                    Confirm
                  </button>
                </div>
              )}

              <div className="mt-2 flex flex-wrap items-center gap-2">
                <button
                  className="btn"
                  disabled={!b}
                  title={b ? "Label this circuit's corners" : "Survey it first — corners are placed on the map"}
                  onClick={() => setEditing(row)}
                >
                  Corners…
                </button>
                <a
                  className={`btn ${b ? "" : "pointer-events-none opacity-50"}`}
                  href={b ? api.bundles.downloadUrl(row.slug) : undefined}
                  download={`${row.slug}.json`}
                >
                  Export
                </a>
                <button
                  className="btn"
                  disabled={!b || busy}
                  title="Merge another bundle of this circuit into it — a friend's, or your own from another machine"
                  onClick={() => {
                    importTarget.current = row.name;
                    fileInput.current?.click();
                  }}
                >
                  Merge into…
                </button>
                <button
                  className="btn"
                  disabled={!b || busy}
                  title="Rename — and if the new name is another bundle, merge into it. Two spellings of one circuit are one circuit."
                  onClick={() => setRenaming(row)}
                >
                  Rename…
                </button>
                {b && (
                  <button
                    className="btn-danger ml-auto"
                    disabled={busy}
                    onClick={() => setDeleting(row)}
                  >
                    Delete bundle
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {(shared?.configured || sharedError) && (
        <div className="rounded-xl bg-panel p-3">
          <h3 className="text-sm font-semibold">Shared bundles</h3>
          <p className="mt-1 text-xs text-ink-dim">
            Contributed track bundles offered by the configured shared repo. Pulling one
            merges it through the same validation and voting path as an imported file, so
            evidence accumulates and your own corner labels are never overwritten.
          </p>
          {sharedError && (
            <div className="mt-2 text-xs text-brake">
              The shared repo could not be read: {sharedError}
            </div>
          )}
          {shared?.configured && shared.bundles.length === 0 && (
            <div className="mt-2 text-xs text-ink-dim">The repo lists no bundles yet.</div>
          )}
          <ul className="mt-2 space-y-1.5">
            {shared?.bundles.map((entry) => {
              const local = rows.find((r) => r.slug === entry.slug)?.bundle ?? null;
              return (
                <li
                  key={entry.slug}
                  className="flex flex-wrap items-center gap-2 rounded-lg border border-edge px-2 py-1.5 text-xs"
                >
                  <span className="font-semibold">{entry.track}</span>
                  <span className="font-tabular text-ink-dim">
                    {entry.points != null && `${entry.points.toLocaleString()} m of border`}
                    {entry.runs != null && ` · ${entry.runs} run${entry.runs === 1 ? "" : "s"}`}
                    {entry.updated_at && ` · updated ${when(entry.updated_at)}`}
                  </span>
                  <span className="text-ink-dim">
                    {local
                      ? `· you have ${local.points.toLocaleString()} m locally`
                      : "· not surveyed here"}
                  </span>
                  <button
                    className="btn ml-auto"
                    disabled={busy}
                    title={
                      local
                        ? "Merge the shared evidence into your bundle — both sides' observations survive"
                        : "Fetch this circuit's bundle and start from everyone else's survey work"
                    }
                    onClick={() => void onPullShared(entry.slug)}
                  >
                    {local ? "Pull & merge" : "Pull"}
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {(data?.logs.length ?? 0) > 0 && (
        <details className="rounded-xl bg-panel p-3">
          <summary className="cursor-pointer text-sm font-semibold">
            Survey logs ({data!.logs.length})
          </summary>
          <p className="mt-1 text-xs text-ink-dim">
            Every run's raw JSONL — the complete, transportable record. Download one to
            move the run to another installation; it merges there by being assigned to a
            circuit, exactly like a local run.
          </p>
          <ul className="mt-2 space-y-1.5">
            {data!.logs.map((log) => (
              <li
                key={log.name}
                className="flex flex-wrap items-center gap-2 rounded-lg border border-edge px-2 py-1.5 text-xs"
              >
                <span className="font-tabular">{log.name}</span>
                <span className="text-ink-dim">
                  {log.track || "unassigned"} · {log.marks.toLocaleString()} marks ·{" "}
                  {log.transitions.toLocaleString()} transitions · {bytes(log.bytes)}
                </span>
                <a
                  className="btn ml-auto"
                  href={api.survey.logDownloadUrl(log.name)}
                  download={log.name}
                >
                  Download
                </a>
              </li>
            ))}
          </ul>
        </details>
      )}

      {data && (
        <p className="px-1 text-xs text-ink-dim">
          {data.catalog_configs} official configurations known ·{" "}
          {data.seeded_signatures > 0 && (
            <>
              {data.seeded_signatures} shipped signatures waiting for a circuit to be
              driven, listed here only once one has been ·{" "}
            </>
          )}
          this installation is{" "}
          <span className="font-tabular">{data.source}</span>, the id stamped on every vote
          it casts so merged bundles can tell whose evidence is whose.
          {!getAdminToken() && " Actions may need an admin token (Admin → Connection)."}
        </p>
      )}

      <ConfirmDialog
        open={deleting != null}
        title={`Delete the bundle for ${deleting?.name ?? ""}?`}
        body="Every border point, finish crossing and corner label for this circuit is removed. The survey JSONL logs are untouched, so a run can be rebuilt from one."
        confirmLabel="Delete"
        danger
        onCancel={() => setDeleting(null)}
        onConfirm={() => {
          const row = deleting;
          setDeleting(null);
          if (row) void run("Bundle deleted", () => api.bundles.remove(row.slug));
        }}
      />

      <PromptDialog
        open={renaming != null}
        title={`Rename ${renaming?.name ?? ""}`}
        label="Renaming onto an existing bundle merges the two — which is the fix for one circuit living under two near-miss spellings."
        placeholder="track name"
        submitLabel="Rename"
        initialValue={renaming?.name ?? ""}
        suggestions={knownNames}
        onCancel={() => setRenaming(null)}
        onSubmit={(name) => {
          const row = renaming;
          setRenaming(null);
          if (row) void run("Renamed", () => api.bundles.rename(row.slug, name));
        }}
      />

      <PromptDialog
        open={assigning != null}
        title="Assign this run to a track"
        label="The log is replayed through the normal merge path, so the result is the same as having named the circuit while driving."
        placeholder="track name"
        submitLabel="Assign"
        suggestions={knownNames}
        onCancel={() => setAssigning(null)}
        onSubmit={(name) => {
          const log = assigning;
          setAssigning(null);
          if (log) void run("Run recovered", () => api.survey.assignLog(log.name, name));
        }}
      />
    </div>
  );
}
