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
// So the page is built around the DISAGREEMENTS: a bundle with no named
// track, a named track nobody has surveyed, a layout suggestion nobody
// confirmed, a bundle at 4 % elevation because it predates elevation capture
// and only re-driving fills it in. The "Needs you" list gathers the ones that
// block auto-ID, sync or coaching; the table shows every circuit's readiness
// at a glance; the detail rail spells out the selected one, fix by fix.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CornerEditor } from "@/components/tracks/CornerEditor";
import { ConfirmDialog, LargeDialog, PromptDialog } from "@/components/ui/Dialog";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { Tip } from "@/components/ui/Tooltip";
import { api } from "@/lib/api";
import { getAdminToken, type SharedBundles } from "@/lib/api";
import { openSettings } from "@/lib/router";
import type { SurveyLog, TrackOverview, TrackOverviewRow, TrackSyncStatus } from "@/lib/types";
import { toast } from "@/store/toasts";

const toastSuccess = (text: string) => toast(text, "success");
const toastError = (text: string) => toast(text, "error");

// Survey stays its own tab; every "go and drive it" fix links there.
const SURVEY_HREF = "#/survey";

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

// "5 wk ago" — dense enough for a table sub-line; the exact time is one
// hover away wherever it matters.
function ago(iso: string): string {
  const t = new Date(iso).getTime();
  if (!iso || Number.isNaN(t)) return "—";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  if (s < 86400 * 14) return `${Math.round(s / 86400)} d ago`;
  if (s < 86400 * 60) return `${Math.round(s / 86400 / 7)} wk ago`;
  if (s < 86400 * 365) return `${Math.round(s / 86400 / 30)} mo ago`;
  return `${Math.round(s / 86400 / 365)} y ago`;
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

// --- Readiness ---------------------------------------------------------------

// The four steps from "a name" to "coaching works", in the order they unlock
// each other. A step is either done or it is the thing to do next.
const STEP_NAMES = ["identifies sessions", "surveyed", "official layout", "corners labelled"];
const STEP_NEXT = ["identify", "survey it", "confirm layout", "label corners"];

function turnsOf(row: TrackOverviewRow): number | null {
  return row.official?.turns || null;
}

function steps(row: TrackOverviewRow): boolean[] {
  const b = row.bundle;
  const turns = turnsOf(row);
  return [
    row.named || b != null,
    b != null,
    row.official != null,
    // Without an official turn count there is nothing to be complete
    // against; one labelled corner is the best evidence there is.
    b != null && (turns ? b.corners >= turns : b.corners > 0),
  ];
}

function readiness(row: TrackOverviewRow): { ready: boolean; label: string; cls: string } {
  const miss = steps(row).findIndex((x) => !x);
  if (miss < 0) return { ready: true, label: "Ready", cls: "text-throttle" };
  return {
    ready: false,
    label: `Next: ${STEP_NEXT[miss]}`,
    // An unsurveyed circuit is a gap, not a fault — nothing is broken yet.
    cls: miss === 1 ? "text-ink-faint" : "text-warn",
  };
}

type Filter = "all" | "attention" | "ready" | "unsurveyed";

function matches(row: TrackOverviewRow, filter: Filter): boolean {
  const { ready } = readiness(row);
  switch (filter) {
    case "all":
      return true;
    case "ready":
      return ready;
    case "attention":
      return !ready && row.bundle != null;
    case "unsurveyed":
      return row.bundle == null;
  }
}

// Why a circuit identifies itself (or doesn't). The same words the auto-ID
// chip carried before the revamp.
function identifyWhy(row: TrackOverviewRow): string {
  return row.provenance === "user"
    ? "You named this circuit, so sessions here identify themselves — and your name outranks any shipped signature"
    : row.provenance === "seed"
      ? "Identified by a signature that shipped with the app, not one you made. Naming it yourself from a lap in Sessions replaces it."
      : row.bundle != null
        ? "Sessions here identify themselves by matching the surveyed road — no signature needed"
        : "Neither a geometry signature nor a survey — sessions here will NOT be identified. Name it from a lap in Sessions, or survey it.";
}

// --- Sync --------------------------------------------------------------------

/** Where a bundle stands with the sync service (#79). Only drawn when the
 * tracks type is active; the row says nothing about sync otherwise. */
function inWords(seconds: number): string {
  return seconds < 90 ? `${seconds} s` : `${Math.round(seconds / 60)} min`;
}

function syncInfo(
  sync: TrackSyncStatus,
): { label: string; short: string; title: string; cls: string } | null {
  const due = sync.due_in_s != null && sync.due_in_s > 0 ? inWords(sync.due_in_s) : "";
  let cls = "border border-dashed border-edge text-ink-faint";
  let label: string;
  let short: string;
  let title: string;
  switch (sync.status) {
    case "unconfirmed":
      label = short = "not synced — confirm layout";
      title =
        "Only bundles with a confirmed official layout are sent: the sync service files uploads by layout, and an unconfirmed one cannot be filed. Confirm the suggestion below to queue it.";
      break;
    case "imported":
      label = short = "not synced — nothing of yours";
      title =
        "Every metre in this bundle was pulled or imported from elsewhere. Only evidence this installation recorded is uploaded — the service files each installation under one account — so there is nothing to send until you survey the circuit here.";
      break;
    case "queued":
      label = short = due ? `sync in ${due}` : "sync queued";
      title =
        "Changed since the last upload. It goes once it has been left alone for ten minutes — a running survey keeps resetting that clock — or at once from Settings › Sync › Sync now.";
      break;
    case "uploading":
      cls = "bg-accent/14 text-accent";
      label = short = "syncing…";
      title = "Uploading now";
      break;
    case "synced":
      cls = "bg-throttle/14 text-throttle";
      label = `synced ${when(sync.uploaded_at ?? "")}`;
      short = "synced";
      if (sync.remote_status && sync.remote_status !== "pending") {
        label += ` · ${sync.remote_status}`;
        short += ` · ${sync.remote_status}`;
      }
      title =
        `Accepted by the sync service${sync.upload_id ? ` as upload ${sync.upload_id}` : ""}` +
        ` — ${sync.remote_status === "pending" || !sync.remote_status ? "waiting for the next merge run" : sync.remote_status}`;
      break;
    case "rejected":
      cls = "bg-brake/14 text-brake";
      label = short = "sync rejected";
      title = `${sync.error || "refused by the server"} — not retried until the bundle changes`;
      break;
    case "error":
      cls = "bg-warn/14 text-warn";
      label = short = "sync error";
      title = `${sync.error || "upload failed"}${due ? ` — retrying in ${due}` : ""}`;
      break;
    default:
      return null;
  }
  return { label, short, title, cls };
}

function SyncChip({ sync, compact = false }: { sync: TrackSyncStatus; compact?: boolean }) {
  const info = syncInfo(sync);
  if (!info) return null;
  return (
    <Tip content={compact ? `${info.label}. ${info.title}` : info.title}>
      <span className={`whitespace-nowrap rounded-[9px] px-2 py-px text-[10px] ${info.cls}`}>
        {compact ? info.short : info.label}
      </span>
    </Tip>
  );
}

// --- Small pieces ------------------------------------------------------------

// 36×5 coverage bar — the share of one border the survey has established.
// Green when the evidence is close to complete, warn while it is not.
function CoverageBar({ label, pct }: { label: string; pct: number }) {
  const good = pct >= 90;
  return (
    <span className="inline-flex items-center gap-1">
      <span className="text-[10px] text-ink-ghost">{label}</span>
      <span className="inline-block h-[5px] w-9 overflow-hidden rounded-[3px] bg-panel-2">
        <span
          className={`block h-full ${good ? "bg-throttle" : "bg-warn"}`}
          style={{ width: `${Math.max(0, Math.min(100, pct))}%` }}
        />
      </span>
      <span className={`text-[10.5px] ${good ? "text-ink-dim" : "text-warn"}`}>
        {Math.round(pct)}%
      </span>
    </span>
  );
}

function ReadyPips({ row }: { row: TrackOverviewRow }) {
  const st = steps(row);
  const r = readiness(row);
  return (
    <Tip
      content={
        <span className="whitespace-pre-line">
          {st.map((x, i) => `${x ? "✓" : "✗"} ${STEP_NAMES[i]}`).join("\n")}
        </span>
      }
    >
      <span className="inline-flex flex-col">
        <span className="inline-flex gap-[3px]">
          {st.map((x, i) => (
            <span
              key={i}
              className={`h-[5px] w-3.5 rounded-sm ${x ? "bg-throttle" : "bg-edge"}`}
            />
          ))}
        </span>
        <span className={`mt-[3px] text-[10.5px] ${r.cls}`}>{r.label}</span>
      </span>
    </Tip>
  );
}

/** A button that opens a short list of actions — the header's Import ▾. */
function Menu({
  label,
  disabled,
  items,
}: {
  label: string;
  disabled?: boolean;
  items: { label: string; hint?: string; disabled?: boolean; onSelect: () => void }[];
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  return (
    <div ref={root} className="relative">
      <button
        className="btn px-3 py-[5px] text-[11.5px]"
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
      >
        {label} ▾
      </button>
      {open && (
        <div
          role="menu"
          className="elevated absolute right-0 top-full z-30 mt-1 flex w-64 flex-col rounded-md bg-panel py-1"
        >
          {items.map((it) => (
            <button
              key={it.label}
              role="menuitem"
              disabled={it.disabled}
              className="px-3 py-1.5 text-left hover:bg-panel-2 disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:bg-transparent"
              onClick={() => {
                setOpen(false);
                it.onSelect();
              }}
            >
              <span className="block text-[12px] text-ink">{it.label}</span>
              {it.hint && (
                <span className="mt-0.5 block text-[10.5px] leading-snug text-ink-faint">
                  {it.hint}
                </span>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// An action attached to a "Needs you" row or a checklist row: either a button
// or a link (the Survey tab, a download).
interface Action {
  label: string;
  onClick?: () => void;
  href?: string;
  download?: string;
  disabled?: boolean;
  title?: string;
}

function ActionButton({
  action,
  primary = false,
  className = "",
}: {
  action: Action;
  primary?: boolean;
  className?: string;
}) {
  const cls = `btn whitespace-nowrap ${primary ? "btn-primary" : ""} ${action.title ? "" : className}`;
  const el = action.href ? (
    <a className={cls} href={action.href} download={action.download}>
      {action.label}
    </a>
  ) : (
    <button className={cls} disabled={action.disabled} onClick={action.onClick}>
      {action.label}
    </button>
  );
  if (!action.title) return el;
  // A disabled button swallows pointer events, so the tooltip hangs off a
  // wrapper that still receives them.
  return (
    <Tip content={action.title}>
      <span className={`inline-flex ${className}`}>{el}</span>
    </Tip>
  );
}

interface Todo {
  key: string;
  dot: string; // bg-* class
  track: string;
  what: string;
  why: React.ReactNode;
  primary: Action;
  secondary?: Action;
}

interface Check {
  key: string;
  tone: "ok" | "warn" | "todo";
  label: string;
  detail: React.ReactNode;
  tip?: string;
  action?: Action;
}

const CHECK_ICON = {
  ok: { icon: "✓", cls: "text-throttle" },
  warn: { icon: "!", cls: "text-warn" },
  todo: { icon: "○", cls: "text-ink-faint" },
} as const;

// "Not this" on a layout suggestion has no server-side counterpart — the
// suggestion is recomputed on every overview — so it only quiets the
// "Needs you" row, in this browser. The suggestion itself stays visible in
// the detail rail.
const DISMISSED_KEY = "gt7.tracks.dismissedSuggestions";

function loadDismissed(): string[] {
  try {
    const raw = localStorage.getItem(DISMISSED_KEY);
    const v: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

function saveDismissed(keys: string[]) {
  try {
    localStorage.setItem(DISMISSED_KEY, JSON.stringify(keys));
  } catch {
    // Private windows and blocked storage: the dismissal lasts this visit.
  }
}

export function TracksView() {
  const [data, setData] = useState<TrackOverview | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<TrackOverviewRow | null>(null);
  const [renaming, setRenaming] = useState<TrackOverviewRow | null>(null);
  const [deleting, setDeleting] = useState<TrackOverviewRow | null>(null);
  const [assigning, setAssigning] = useState<SurveyLog | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [dismissed, setDismissed] = useState<string[]>(loadDismissed);
  const [sharedOpen, setSharedOpen] = useState(false);
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
      if (result.corrections) {
        const c = result.corrections;
        const parts = [
          c.areas > 0 && `${c.areas} area${c.areas === 1 ? "" : "s"} kept off the map`,
          c.drawn > 0 && `${c.drawn} m drawn in`,
          c.smooth_borders != null && `smoothing ${c.smooth_borders ? "on" : "off"}`,
        ].filter(Boolean);
        toastSuccess(`Applied the repo's corrections: ${parts.join(", ")}`);
      }
    });
  };

  // Not routed through `run` either: the count of verdicts that changed IS
  // the result, and zero is the usual, reassuring answer rather than a
  // failure. Nothing on the row itself changes, so no refresh.
  const onRejudge = async (row: TrackOverviewRow) => {
    setBusy(true);
    try {
      const r = await api.bundles.rejudge(row.slug);
      const laps = `${r.laps} lap${r.laps === 1 ? "" : "s"}`;
      const verdicts = `${r.changed} verdict${r.changed === 1 ? "" : "s"}`;
      if (r.laps === 0) {
        toast(`No laps recorded on ${row.name}`);
      } else if (!r.judged) {
        toast(
          `${row.name} has no usable survey — ` +
            (r.changed ? `${verdicts} set back to unknown` : `${laps} left unknown`),
        );
      } else {
        toastSuccess(
          `Re-checked ${laps} on ${row.name} — ` +
            (r.changed ? `${verdicts} changed` : "every verdict stands"),
        );
      }
    } catch (e) {
      toastError((e as Error).message);
    } finally {
      setBusy(false);
    }
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

  const onConfirmLayout = (row: TrackOverviewRow) =>
    void run("Layout confirmed", () => {
      const s = row.suggestion!;
      // The suggestion's reasoning is for the human reading it; what gets
      // stored is the match itself.
      return api.bundles.setOfficial(row.slug, {
        track: s.track,
        layout: s.layout,
        official_id: s.official_id,
        official_name: s.official_name,
        turns: s.turns,
        length_m: s.length_m,
        reverse: s.reverse,
      });
    });

  const confirmAction = (row: TrackOverviewRow): Action => ({
    label: "Confirm",
    disabled: busy || !row.bundle,
    title: row.bundle
      ? "Confirm the match — nothing is inferred silently, because GT7 broadcasts no track identifier"
      : "Needs a survey bundle to record the match in",
    onClick: () => onConfirmLayout(row),
  });

  const rows = useMemo(() => data?.tracks ?? [], [data]);
  const orphans = (data?.logs ?? []).filter((l) => l.orphaned);
  const knownNames = rows.map((r) => r.name);
  // Identification needs something to match against; with no bundle at all
  // the button would only ever be able to say "nothing to compare with".
  const bundleCount = rows.filter((r) => r.bundle != null).length;
  const readyCount = rows.filter((r) => readiness(r).ready).length;
  const syncActive = data?.sync_tracks.active ?? false;

  const needle = query.trim().toLowerCase();
  const visible = rows.filter(
    (r) => matches(r, filter) && (!needle || r.name.toLowerCase().includes(needle)),
  );
  const current =
    rows.find((r) => r.slug === selected) ?? visible[0] ?? rows[0] ?? null;

  if (editing) {
    return (
      <div className="mx-auto max-w-[1200px]">
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

  // --- Needs you -------------------------------------------------------------
  const todos: Todo[] = [];
  if (data?.sync_tracks.active && data.sync_tracks.state === "error") {
    todos.push({
      key: "sync",
      dot: "bg-brake",
      track: "Track sync",
      what: "not reaching the server",
      why: `${data.sync_tracks.error || "unknown error"}. Uploads are retried with backoff; check Settings › Sync.`,
      primary: { label: "Open Settings › Sync", onClick: () => openSettings("sync") },
    });
  }
  for (const row of rows) {
    const s = row.suggestion;
    if (row.official || !s) continue;
    const dismissKey = `${row.slug}:${s.official_id}`;
    if (dismissed.includes(dismissKey)) continue;
    todos.push({
      key: `confirm:${row.slug}`,
      dot: "bg-warn",
      track: row.name,
      what: "confirm the layout",
      why:
        `Looks like ${s.official_name} — ${s.turns} turns, ${s.length_m.toLocaleString()} m — ${s.why}.` +
        (row.bundle ? "" : " Survey it first: the match is recorded in its bundle."),
      primary: row.bundle
        ? confirmAction(row)
        : { label: "Survey this track", href: SURVEY_HREF },
      secondary: {
        label: "Not this",
        title: "Hide this suggestion here. It stays in the circuit's checklist.",
        onClick: () => {
          const next = [...dismissed, dismissKey];
          setDismissed(next);
          saveDismissed(next);
        },
      },
    });
  }
  for (const log of orphans) {
    todos.push({
      key: `orphan:${log.name}`,
      dot: "bg-warn",
      track: `Survey run ${when(log.started_at)}`,
      what: "went nowhere",
      why: (
        <Tip content="A survey with no circuit label saves no bundle at all — these runs exist only as their logs. The log is a complete record, so assigning one now merges it exactly as if the circuit had been named while driving.">
          <span>
            {log.marks.toLocaleString()} marks · {log.transitions.toLocaleString()} transitions ·{" "}
            {bytes(log.bytes)} · saved as a log only, no circuit named.
          </span>
        </Tip>
      ),
      primary: { label: "Assign to a track…", disabled: busy, onClick: () => setAssigning(log) },
      secondary: {
        label: "Download",
        href: api.survey.logDownloadUrl(log.name),
        download: log.name,
      },
    });
  }
  for (const row of rows) {
    const cov = row.bundle?.coverage;
    if (!cov || cov.road_pct >= 50) continue;
    todos.push({
      key: `coverage:${row.slug}`,
      dot: "bg-warn",
      track: row.name,
      what: `road coverage ${cov.road_pct}%`,
      why: `${plural(row.bundle!.runs, "run")} so far. Drive the full lap in survey mode to fill in the missing road.`,
      primary: { label: "Survey again", href: SURVEY_HREF },
      secondary: { label: "Details", onClick: () => setSelected(row.slug) },
    });
  }
  const unlabelled = rows.filter((r) => r.bundle != null && r.bundle.corners === 0);
  if (unlabelled.length > 0) {
    const first = unlabelled[0];
    todos.push({
      key: "corners",
      dot: "bg-ink-faint",
      track: unlabelled.length === 1 ? first.name : plural(unlabelled.length, "circuit"),
      what: unlabelled.length === 1 ? "has no corners labelled" : "have no corners labelled",
      why:
        (unlabelled.length === 1 ? "" : `${unlabelled.map((r) => r.name).join(", ")} — `) +
        "corner reports and braking callouts stay empty until they are.",
      primary: {
        label: unlabelled.length === 1 ? "Label corners" : "Label next",
        onClick: () => setEditing(first),
      },
    });
  }

  // --- Detail checklist -------------------------------------------------------
  const checks: Check[] = [];
  if (current) {
    const b = current.bundle;
    const turns = turnsOf(current);
    checks.push({
      key: "identify",
      tone: current.named || b ? "ok" : "warn",
      label: "Identifies sessions",
      detail: identifyWhy(current),
    });
    if (b) {
      checks.push({
        key: "survey",
        tone: "ok",
        label: "Survey",
        detail:
          `${b.points.toLocaleString()} m of border from ${plural(b.runs, "run")}` +
          (b.sources > 1 ? ` · ${b.sources} sources` : "") +
          ` · updated ${when(b.updated_at)}`,
        // Border metres, NOT lap length — one record per metre per side.
        tip: "Metres of border evidence — one record per metre per side, so a lap's worth of track is roughly twice its length",
      });
      const cov = b.coverage;
      if (cov) {
        const closed = cov.L.closed && cov.R.closed;
        const pcts = `L ${cov.L.pct}% · R ${cov.R.pct}% · road ${cov.road_pct}%`;
        checks.push({
          key: "borders",
          tone: closed ? "ok" : "warn",
          label: "Borders",
          detail: closed
            ? `${pcts} · both loops closed.`
            : `${pcts} — ${cov.road_pct < 50 ? "most of the road is missing." : "small gaps; loops not closed."}`,
          tip: "How much of each border the evidence establishes, measured against the compiled boundary — gaps count against it, and ✓closed means both borders form complete loops",
          action:
            cov.road_pct < 50 ? { label: "Survey again", href: SURVEY_HREF } : undefined,
        });
      } else {
        checks.push({
          key: "borders",
          tone: "todo",
          label: "Borders",
          detail: "Not compiled yet — coverage appears once the bundle compiles.",
        });
      }
      checks.push(
        b.elevation_pct >= 50
          ? { key: "elev", tone: "ok", label: "Elevation", detail: `${b.elevation_pct}% captured.` }
          : {
              key: "elev",
              tone: "warn",
              label: "Elevation",
              detail: `${b.elevation_pct}% captured. Recorded before elevation capture — one more lap on it fills it in.`,
              tip: "Elevation only fills in by RE-DRIVING a metre: bundles started before elevation capture sit near 0 % until their ground is driven again",
              action: { label: "Drive a lap", href: SURVEY_HREF },
            },
      );
      checks.push({
        key: "finish",
        tone: b.finish_crossings > 0 ? "ok" : "warn",
        label: "Finish line",
        detail:
          b.finish_crossings > 0
            ? "Located from lap crossings."
            : "Not located yet — needs repeat crossings to be confident.",
        tip: "Start/finish line located from lap rollovers; needs repeat crossings to be confident",
      });
      const done = turns ? b.corners >= turns : b.corners > 0;
      checks.push({
        key: "corners",
        tone: done ? "ok" : "todo",
        label: "Corners",
        detail: turns
          ? done
            ? `${b.corners} of ${turns} labelled — coaching and corner reports work.`
            : `${b.corners} of ${turns} labelled. Needed for corner reports and braking callouts.`
          : `${plural(b.corners, "corner")} labelled.`,
        action: {
          label: done ? "Edit corners" : "Label corners",
          title: "Label this circuit's corners",
          onClick: () => setEditing(current),
        },
      });
    } else {
      checks.push({
        key: "survey",
        tone: "todo",
        label: "Survey",
        detail: "Nothing surveyed here yet — the map, coverage and sync all need one.",
        action: { label: "Survey this track", href: SURVEY_HREF },
      });
    }
    const o = current.official;
    const s = current.suggestion;
    checks.push(
      o
        ? {
            key: "official",
            tone: "ok",
            label: "Official layout",
            detail: `Confirmed as ${o.official_name} · ${o.turns} turns · ${o.length_m.toLocaleString()} m.`,
          }
        : s
          ? {
              key: "official",
              tone: "warn",
              label: "Official layout",
              detail: `Looks like ${s.official_name} · ${s.turns} turns · ${s.length_m.toLocaleString()} m — ${s.why}.`,
              action: confirmAction(current),
            }
          : {
              key: "official",
              tone: "todo",
              label: "Official layout",
              detail:
                "Not matched to an official GT7 layout — GT7 broadcasts no track id, so this is a human decision.",
            },
    );
    if (!b) {
      checks.push({
        key: "corners",
        tone: "todo",
        label: "Corners",
        detail: "Placed on the map once surveyed.",
      });
    }
    const info = b && syncActive && current.sync ? syncInfo(current.sync) : null;
    if (info && current.sync) {
      const st = current.sync.status;
      checks.push({
        key: "sync",
        tone: st === "synced" ? "ok" : st === "rejected" || st === "error" ? "warn" : "todo",
        label: "Sync",
        detail: (
          <span className="flex flex-col items-start gap-1">
            <SyncChip sync={current.sync} />
            <span>{info.title}</span>
          </span>
        ),
        action:
          st === "queued" || st === "error"
            ? {
                label: "Sync now",
                disabled: busy,
                onClick: () => void run("Track sync started", () => api.admin.syncPush("tracks")),
              }
            : undefined,
      });
    }
  }

  const counts: Record<Filter, number> = {
    all: rows.length,
    attention: rows.filter((r) => matches(r, "attention")).length,
    ready: readyCount,
    unsurveyed: rows.filter((r) => matches(r, "unsurveyed")).length,
  };

  const officialLine = (row: TrackOverviewRow) => {
    const sessions = row.sessions ? plural(row.sessions, "session") : "no sessions";
    if (row.official) {
      return `Official GT7 layout · ${row.official.turns} turns · ${row.official.length_m.toLocaleString()} m · ${sessions}`;
    }
    return row.bundle
      ? `No official layout confirmed · ${sessions}`
      : `${sessions} · not surveyed`;
  };

  const subLine = (row: TrackOverviewRow) => {
    const b = row.bundle;
    if (b) {
      const len = row.official?.length_m ?? row.length_m;
      return [
        row.official && `${row.official.turns} turns`,
        len != null && `${Math.round(len).toLocaleString()} m`,
        `updated ${ago(b.updated_at)}`,
      ]
        .filter(Boolean)
        .join(" · ");
    }
    if (row.provenance === "seed") return "named by shipped signature";
    if (row.provenance === "user") return "named by you from a lap";
    return "";
  };

  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-3">
      <div className="flex flex-wrap items-baseline gap-3">
        <h2 className="text-[17px] font-medium">Tracks</h2>
        {data && (
          <span className="font-tabular text-[11.5px] text-ink-faint">
            {plural(rows.length, "circuit")} · {bundleCount} surveyed · {readyCount} fully ready
          </span>
        )}
        <div className="ml-auto flex items-center gap-2">
          <Tip content="Match every unlabelled session against the surveyed circuits and name the ones that were driven on them. New sessions do this by themselves.">
            <span className="inline-flex">
              <button
                className="btn btn-primary px-3 py-[5px] text-[11.5px]"
                disabled={busy || bundleCount === 0}
                onClick={onIdentify}
              >
                Identify sessions
              </button>
            </span>
          </Tip>
          <Menu
            label="Import"
            disabled={busy}
            items={[
              {
                label: "Import bundle…",
                hint: "A track bundle JSON from another installation, merged by circuit name.",
                onSelect: () => {
                  importTarget.current = undefined;
                  fileInput.current?.click();
                },
              },
              {
                label: "Upload survey log…",
                hint: "Land a survey run's raw JSONL from another installation. It appears in the log list — assign it to a circuit to merge its evidence.",
                onSelect: () => logInput.current?.click(),
              },
              {
                label: "Pull from shared",
                hint: shared?.configured
                  ? "Contributed bundles from the configured shared repo."
                  : sharedError
                    ? "The shared repo could not be read."
                    : "No shared repo is configured.",
                disabled: !shared?.configured && !sharedError,
                onSelect: () => setSharedOpen(true),
              },
            ]}
          />
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

      {error && <div className="panel p-3 text-sm text-brake">{error}</div>}

      {data && (
        <div className="panel">
          <div className="flex items-baseline gap-2 px-4 py-2.5">
            <span className="section-header text-warn!">Needs you</span>
            {todos.length > 0 && (
              <span className="text-[10.5px] text-ink-faint">
                {todos.length} {todos.length === 1 ? "thing blocks" : "things block"} auto-ID,
                sync or coaching
              </span>
            )}
          </div>
          <div className="rule" />
          {todos.length === 0 ? (
            <div className="px-4 py-3 text-xs text-throttle">Nothing waiting on you.</div>
          ) : (
            todos.map((td) => (
              <div
                key={td.key}
                className="rule-row grid grid-cols-[18px_minmax(0,1fr)_auto] items-center gap-3 px-4 py-2.5"
              >
                <span className={`h-[7px] w-[7px] rounded-full ${td.dot}`} />
                <div className="min-w-0">
                  <div className="text-[12.5px]">
                    <span className="font-medium">{td.track}</span>{" "}
                    <span className="text-ink-dim">— {td.what}</span>
                  </div>
                  <div className="mt-0.5 text-[11px] text-ink-faint">{td.why}</div>
                </div>
                <div className="flex flex-wrap justify-end gap-1.5">
                  <ActionButton action={td.primary} primary />
                  {td.secondary && <ActionButton action={td.secondary} />}
                </div>
              </div>
            ))
          )}
        </div>
      )}

      <div className="grid items-start gap-3.5 lg:grid-cols-[minmax(0,1fr)_minmax(300px,380px)]">
        <div className="panel overflow-hidden">
          <div className="flex flex-wrap items-center gap-2.5 px-4 py-2">
            <SegmentedControl<Filter>
              size="sm"
              ariaLabel="Filter circuits"
              value={filter}
              onValueChange={setFilter}
              options={[
                { value: "all", label: `All ${counts.all}` },
                { value: "attention", label: `Needs work ${counts.attention}` },
                { value: "ready", label: `Ready ${counts.ready}` },
                { value: "unsurveyed", label: `Not surveyed ${counts.unsurveyed}` },
              ]}
            />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Find a circuit"
              aria-label="Find a circuit"
              className="ml-auto w-[180px] rounded-md border border-edge bg-panel-2 px-2.5 py-1 text-xs text-ink placeholder:text-ink-ghost"
            />
          </div>
          <div className="rule" />
          {!data && !error && <div className="p-6 text-center text-xs text-ink-dim">Loading…</div>}
          {data && rows.length === 0 && (
            <div className="p-8 text-center text-sm text-ink-dim">
              Nothing yet. Name a track from a lap in Sessions, or run a survey.
            </div>
          )}
          {rows.length > 0 && visible.length === 0 && (
            <div className="p-6 text-center text-xs text-ink-dim">No circuit matches.</div>
          )}
          {visible.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse font-tabular text-xs">
                <thead>
                  <tr className="text-left text-[11px] text-ink-faint">
                    <th className="px-4 py-2 font-normal">Circuit</th>
                    <th className="px-2 py-2 font-normal">Ready</th>
                    <th className="px-2 py-2 font-normal">
                      <Tip content="How much of each border the evidence establishes, measured against the compiled boundary — gaps count against it, and ✓closed means both borders form complete loops">
                        <span>Coverage L · R · road</span>
                      </Tip>
                    </th>
                    <th className="px-2 py-2 font-normal">Corners</th>
                    <th className="px-2 py-2 font-normal">Sessions</th>
                    {syncActive && <th className="px-4 py-2 font-normal">Sync</th>}
                  </tr>
                </thead>
                <tbody>
                  {visible.map((row) => {
                    const b = row.bundle;
                    const turns = turnsOf(row);
                    const isSel = current?.slug === row.slug;
                    const cornersDone = b != null && turns != null && b.corners >= turns;
                    return (
                      <tr
                        key={row.slug}
                        onClick={() => setSelected(row.slug)}
                        aria-selected={isSel}
                        className={`rule-row cursor-pointer ${
                          isSel
                            ? "bg-accent/7 shadow-[inset_2px_0_0_var(--color-accent)]"
                            : "hover:bg-panel-2/70"
                        }`}
                      >
                        <td className="min-w-[200px] px-4 py-[9px]">
                          <button
                            className="text-left font-sans text-[12.5px] font-medium text-ink"
                            onClick={() => setSelected(row.slug)}
                          >
                            {row.name}
                          </button>
                          <div className="text-[10.5px] text-ink-faint">{subLine(row)}</div>
                        </td>
                        <td className="px-2 py-[9px]">
                          <ReadyPips row={row} />
                        </td>
                        <td className="whitespace-nowrap px-2 py-[9px]">
                          {b?.coverage ? (
                            <span className="inline-flex items-center gap-2">
                              <CoverageBar label="L" pct={b.coverage.L.pct} />
                              <CoverageBar label="R" pct={b.coverage.R.pct} />
                              <CoverageBar label="road" pct={b.coverage.road_pct} />
                            </span>
                          ) : (
                            <span className="text-[11px] text-ink-ghost">
                              {b ? "not compiled" : "not surveyed"}
                            </span>
                          )}
                        </td>
                        <td
                          className={`px-2 py-[9px] ${
                            !b ? "text-ink-ghost" : cornersDone ? "text-throttle" : "text-ink-dim"
                          }`}
                        >
                          {b ? (turns ? `${b.corners}/${turns}` : b.corners) : "—"}
                        </td>
                        <td className="px-2 py-[9px] text-ink-dim">{row.sessions || "—"}</td>
                        {syncActive && (
                          <td className="whitespace-nowrap px-4 py-[9px]">
                            {b && row.sync ? (
                              <SyncChip sync={row.sync} compact />
                            ) : (
                              <span className="text-[10px] text-ink-ghost">—</span>
                            )}
                          </td>
                        )}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {current && (
          <aside className="panel lg:sticky lg:top-3">
            <div className="flex flex-col gap-1 px-4 pb-3 pt-3.5">
              <span className="text-[15px] font-medium">{current.name}</span>
              <span className="font-tabular text-[11px] text-ink-faint">
                {officialLine(current)}
              </span>
            </div>
            <div className="rule" />
            <div className="py-1.5">
              {checks.map((ck) => {
                const icon = CHECK_ICON[ck.tone];
                const label = <div className="text-[12.5px]">{ck.label}</div>;
                return (
                  <div
                    key={ck.key}
                    className="grid grid-cols-[16px_minmax(0,1fr)_auto] items-start gap-2.5 px-4 py-2"
                  >
                    <span className={`mt-[3px] text-[11px] leading-none ${icon.cls}`}>
                      {icon.icon}
                    </span>
                    <div className="min-w-0">
                      {ck.tip ? (
                        <Tip content={ck.tip}>
                          <span className="inline-block">{label}</span>
                        </Tip>
                      ) : (
                        label
                      )}
                      <div className="mt-0.5 font-tabular text-[11px] leading-[1.45] text-ink-dim">
                        {ck.detail}
                      </div>
                    </div>
                    {ck.action ? <ActionButton action={ck.action} /> : <span />}
                  </div>
                );
              })}
            </div>
            <div className="rule" />
            <div className="flex flex-wrap gap-1.5 px-4 py-3">
              <a
                className={`btn ${current.bundle ? "" : "pointer-events-none opacity-50"}`}
                href={current.bundle ? api.bundles.downloadUrl(current.slug) : undefined}
                download={`${current.slug}.json`}
              >
                Export bundle
              </a>
              <ActionButton
                action={{
                  label: "Merge into…",
                  disabled: !current.bundle || busy,
                  title:
                    "Merge another bundle of this circuit into it — a friend's, or your own from another machine",
                  onClick: () => {
                    importTarget.current = current.name;
                    fileInput.current?.click();
                  },
                }}
              />
              <ActionButton
                action={{
                  label: "Rename…",
                  disabled: !current.bundle || busy,
                  title:
                    "Rename — and if the new name is another bundle, merge into it. Two spellings of one circuit are one circuit.",
                  onClick: () => setRenaming(current),
                }}
              />
              <ActionButton
                action={{
                  label: "Re-check laps",
                  disabled: busy || current.sessions === 0,
                  title:
                    current.sessions > 0
                      ? "Judge every lap driven here against the survey as it is now. This happens by itself after a survey stops or a bundle is merged, renamed or deleted; forcing it also says how many verdicts changed."
                      : "No sessions recorded here — nothing to re-check",
                  onClick: () => void onRejudge(current),
                }}
              />
              {current.bundle && (
                <button
                  className="btn btn-danger ml-auto"
                  disabled={busy}
                  onClick={() => setDeleting(current)}
                >
                  Delete bundle…
                </button>
              )}
            </div>
          </aside>
        )}
      </div>

      {(data?.logs.length ?? 0) > 0 && (
        <details className="panel px-4 py-3">
          <summary className="section-header cursor-pointer">
            Survey logs ({data!.logs.length})
          </summary>
          <p className="mt-1.5 text-[10.5px] text-ink-faint">
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
                {log.orphaned && (
                  <button className="btn" disabled={busy} onClick={() => setAssigning(log)}>
                    Assign to a track…
                  </button>
                )}
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
          {!getAdminToken() && " Actions may need an admin token (Settings › Access)."}
        </p>
      )}

      <LargeDialog
        open={sharedOpen}
        title="Shared bundles"
        size="medium"
        onClose={() => setSharedOpen(false)}
      >
        <div className="h-full overflow-y-auto px-4 py-3">
          <p className="text-[11px] text-ink-faint">
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
                  {entry.corrections && (
                    <Tip content="The repo keeps a corrections file for this circuit — areas kept off the map, borders drawn in. A pull brings it along and the map here is compiled the way the repo's is.">
                      <span className="rounded-[9px] bg-accent/14 px-2 py-px text-[10px] text-accent">
                        corrected
                      </span>
                    </Tip>
                  )}
                  <span className="text-ink-dim">
                    {local
                      ? `· you have ${local.points.toLocaleString()} m locally`
                      : "· not surveyed here"}
                  </span>
                  <ActionButton
                    className="ml-auto"
                    action={{
                      label: local ? "Pull & merge" : "Pull",
                      disabled: busy,
                      title: local
                        ? "Merge the shared evidence into your bundle — both sides' observations survive"
                        : "Fetch this circuit's bundle and start from everyone else's survey work",
                      onClick: () => void onPullShared(entry.slug),
                    }}
                  />
                </li>
              );
            })}
          </ul>
        </div>
      </LargeDialog>

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
