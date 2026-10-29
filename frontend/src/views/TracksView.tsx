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
import { currentLanguage, rich, useT, type MessageKey, type Translate } from "@/i18n";
import { api } from "@/lib/api";
import { getAdminToken, type SharedBundles } from "@/lib/api";
import { formatInt } from "@/lib/format";
import { openSettings } from "@/lib/router";
import type {
  OfficialSuggestion,
  SurveyLog,
  TrackOverview,
  TrackOverviewRow,
  TrackSyncStatus,
} from "@/lib/types";
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
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString(currentLanguage());
}

// "5w ago" — dense enough for a table sub-line; the exact time is one
// hover away wherever it matters.
function ago(iso: string, t: Translate): string {
  const ms = new Date(iso).getTime();
  if (!iso || Number.isNaN(ms)) return "—";
  const s = Math.max(0, (Date.now() - ms) / 1000);
  if (s < 60) return t("tracks.justNow");
  const rtf = new Intl.RelativeTimeFormat(currentLanguage(), { style: "narrow" });
  if (s < 3600) return rtf.format(-Math.round(s / 60), "minute");
  if (s < 86400) return rtf.format(-Math.round(s / 3600), "hour");
  if (s < 86400 * 14) return rtf.format(-Math.round(s / 86400), "day");
  if (s < 86400 * 60) return rtf.format(-Math.round(s / 86400 / 7), "week");
  if (s < 86400 * 365) return rtf.format(-Math.round(s / 86400 / 30), "month");
  return rtf.format(-Math.round(s / 86400 / 365), "year");
}

// "a, b, c" the way the interface language lists things.
const list = (items: string[]) =>
  new Intl.ListFormat(currentLanguage(), { type: "conjunction", style: "narrow" }).format(items);

// --- Readiness ---------------------------------------------------------------

// The four steps from "a name" to "coaching works", in the order they unlock
// each other. A step is either done or it is the thing to do next.
const STEP_NAME_KEYS: MessageKey[] = [
  "tracks.step.identify",
  "tracks.step.survey",
  "tracks.step.official",
  "tracks.step.corners",
];
const STEP_NEXT_KEYS: MessageKey[] = [
  "tracks.next.identify",
  "tracks.next.survey",
  "tracks.next.official",
  "tracks.next.corners",
];

// Why a layout was suggested, worded from the server's reasoning; its English
// `why` from a server that sends no reasoning.
function suggestionWhy(t: Translate, s: OfficialSuggestion): string {
  if (!s.match) return s.why;
  const name = t(s.match.name === "exact" ? "tracks.why.nameExact" : "tracks.why.nameClose");
  if (s.match.length == null || s.match.measured_m == null) return name;
  return t(s.match.length === "agrees" ? "tracks.why.lengthAgrees" : "tracks.why.lengthDisagrees", {
    name,
    length: formatInt(s.match.measured_m),
  });
}

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

function readiness(row: TrackOverviewRow): { ready: boolean; labelKey: MessageKey; cls: string } {
  const miss = steps(row).findIndex((x) => !x);
  if (miss < 0) return { ready: true, labelKey: "tracks.ready", cls: "text-throttle" };
  return {
    ready: false,
    labelKey: STEP_NEXT_KEYS[miss],
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
function identifyWhyKey(row: TrackOverviewRow): MessageKey {
  return row.provenance === "user"
    ? "tracks.identify.user"
    : row.provenance === "seed"
      ? "tracks.identify.seed"
      : row.bundle != null
        ? "tracks.identify.bundle"
        : "tracks.identify.none";
}

// --- Sync --------------------------------------------------------------------

/** Where a bundle stands with the sync service (#79). Only drawn when the
 * tracks type is active; the row says nothing about sync otherwise. */
function inWords(seconds: number): string {
  return seconds < 90 ? `${seconds} s` : `${Math.round(seconds / 60)} min`;
}

function syncInfo(
  sync: TrackSyncStatus,
  t: Translate,
): { label: string; short: string; title: string; cls: string } | null {
  const due = sync.due_in_s != null && sync.due_in_s > 0 ? inWords(sync.due_in_s) : "";
  let cls = "border border-dashed border-edge text-ink-faint";
  let label: string;
  let short: string;
  let title: string;
  switch (sync.status) {
    case "unconfirmed":
      label = short = t("tracks.sync.unconfirmed");
      title = t("tracks.sync.unconfirmedTitle");
      break;
    case "imported":
      label = short = t("tracks.sync.imported");
      title = t("tracks.sync.importedTitle");
      break;
    case "queued":
      label = short = due ? t("tracks.sync.dueIn", { due }) : t("tracks.sync.queued");
      title = t("tracks.sync.queuedTitle");
      break;
    case "uploading":
      cls = "bg-accent/14 text-accent";
      label = short = t("tracks.sync.uploading");
      title = t("tracks.sync.uploadingTitle");
      break;
    case "synced": {
      cls = "bg-throttle/14 text-throttle";
      label = t("tracks.sync.syncedAt", { when: when(sync.uploaded_at ?? "") });
      short = t("tracks.sync.synced");
      if (sync.remote_status && sync.remote_status !== "pending") {
        label += ` · ${sync.remote_status}`;
        short += ` · ${sync.remote_status}`;
      }
      const state =
        sync.remote_status === "pending" || !sync.remote_status
          ? t("tracks.sync.waitingMerge")
          : sync.remote_status;
      title = sync.upload_id
        ? t("tracks.sync.acceptedAs", { id: sync.upload_id, state })
        : t("tracks.sync.accepted", { state });
      break;
    }
    case "rejected":
      cls = "bg-brake/14 text-brake";
      label = short = t("tracks.sync.rejected");
      title = t("tracks.sync.rejectedTitle", { error: sync.error || t("tracks.sync.refused") });
      break;
    case "error": {
      cls = "bg-warn/14 text-warn";
      label = short = t("tracks.sync.error");
      const error = sync.error || t("tracks.sync.uploadFailed");
      title = due ? t("tracks.sync.retrying", { error, due }) : error;
      break;
    }
    default:
      return null;
  }
  return { label, short, title, cls };
}

function SyncChip({ sync, compact = false }: { sync: TrackSyncStatus; compact?: boolean }) {
  const t = useT();
  const info = syncInfo(sync, t);
  if (!info) return null;
  return (
    <Tip content={compact ? t("tracks.sync.compactTip", { label: info.label, title: info.title }) : info.title}>
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
  const t = useT();
  const st = steps(row);
  const r = readiness(row);
  return (
    <Tip
      content={
        <span className="whitespace-pre-line">
          {st.map((x, i) => `${x ? "✓" : "✗"} ${t(STEP_NAME_KEYS[i])}`).join("\n")}
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
        <span className={`mt-[3px] text-[10.5px] ${r.cls}`}>{t(r.labelKey)}</span>
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
  secondary?: Action;
}

const CHECK_ICON = {
  ok: { icon: "✓", cls: "text-throttle" },
  warn: { icon: "!", cls: "text-warn" },
  todo: { icon: "○", cls: "text-ink-faint" },
} as const;

export function TracksView() {
  const t = useT();
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
      const breakdown = list(Object.entries(r.tracks).map(([track, n]) => `${n}× ${track}`));
      if (r.identified === 0) {
        toast(t("tracks.identify.noMatch", { count: r.checked }));
      } else {
        toastSuccess(
          t("tracks.identify.named", { identified: r.identified, count: r.checked, breakdown }),
        );
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
      toastError(t("tracks.import.notJson", { name: file.name }));
      return;
    }
    await run(t("tracks.toast.bundleMerged"), async () => {
      const result = await api.bundles.import(doc, target);
      toastSuccess(
        t("tracks.toast.borderAdded", {
          track: result.track,
          added: formatInt(result.added_points),
          points: formatInt(result.points),
          count: result.sources,
        }),
      );
      if (result.corners_kept) {
        toastSuccess(t("tracks.toast.keptCornersImported"));
      }
    });
  };

  // Every circuit the repo offers, in one go. Not routed through `run`: how
  // many came and which failed is the result.
  const onPullAllShared = async () => {
    setBusy(true);
    try {
      const result = await api.bundles.pullAllShared();
      const fresh = result.pulled.filter((p) => p.added_points > 0).length;
      toastSuccess(
        fresh < result.pulled.length
          ? t("tracks.toast.pulledFresh", { count: result.pulled.length, fresh })
          : t("tracks.toast.pulled", { count: result.pulled.length }),
      );
      for (const f of result.failed) toastError(`${f.track}: ${f.error}`);
      refresh();
    } catch (e) {
      toastError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const onPullShared = async (slug: string) => {
    await run(t("tracks.toast.bundlePulled"), async () => {
      const result = await api.bundles.pullShared(slug);
      toastSuccess(
        t("tracks.toast.borderAdded", {
          track: result.track,
          added: formatInt(result.added_points),
          points: formatInt(result.points),
          count: result.sources,
        }),
      );
      if (result.corners_kept) {
        toastSuccess(t("tracks.toast.keptCornersPulled"));
      }
      if (result.corrections) {
        const c = result.corrections;
        const parts = [
          c.areas > 0 && t("tracks.corrections.areas", { count: c.areas }),
          c.drawn > 0 && t("tracks.corrections.drawn", { m: formatInt(c.drawn) }),
          c.smooth_borders != null &&
            t(c.smooth_borders ? "tracks.corrections.smoothingOn" : "tracks.corrections.smoothingOff"),
        ].filter((p): p is string => Boolean(p));
        toastSuccess(t("tracks.toast.corrections", { parts: list(parts) }));
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
      const laps = t("count.laps", { count: r.laps });
      if (r.laps === 0) {
        toast(t("tracks.rejudge.noLaps", { name: row.name }));
      } else if (!r.judged) {
        toast(
          r.changed
            ? t("tracks.rejudge.noSurveyReset", { name: row.name, count: r.changed })
            : t("tracks.rejudge.noSurveyLeft", { name: row.name, count: r.laps }),
        );
      } else {
        toastSuccess(
          r.changed
            ? t("tracks.rejudge.changed", { laps, name: row.name, count: r.changed })
            : t("tracks.rejudge.stands", { laps, name: row.name }),
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
    await run(t("tracks.toast.logUploaded"), async () => {
      const r = await api.survey.uploadLog(file);
      const counts = t("tracks.logCounts", {
        marks: formatInt(r.marks),
        transitions: formatInt(r.transitions),
      });
      toastSuccess(`${r.name}: ${counts} · ${r.track || t("tracks.unassigned")}`);
    });
  };

  const onConfirmLayout = (row: TrackOverviewRow) =>
    void run(t("tracks.toast.layoutConfirmed"), () => {
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

  // "Not this" is stored server-side, so the refresh `run` does afterwards
  // brings back the runner-up — or no suggestion — in both the "Needs you"
  // list and the checklist, on every device, rather than hiding one row in
  // one browser while the server kept offering the same guess.
  const rejectAction = (row: TrackOverviewRow, s: OfficialSuggestion): Action => ({
    label: t("tracks.reject.label"),
    disabled: busy,
    title: t("tracks.reject.title", { name: s.official_name }),
    onClick: () =>
      void run(t("tracks.toast.suggestionRuledOut"), () =>
        api.rejectSuggestion(row.name, s.official_id),
      ),
  });

  const confirmAction = (row: TrackOverviewRow): Action => ({
    label: t("common.confirm"),
    disabled: busy || !row.bundle,
    title: row.bundle ? t("tracks.confirm.title") : t("tracks.confirm.needsBundle"),
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
      track: t("tracks.todo.trackSync"),
      what: t("tracks.todo.syncWhat"),
      why: t("tracks.todo.syncWhy", {
        error: data.sync_tracks.error || t("tracks.todo.unknownError"),
      }),
      primary: { label: t("tracks.todo.openSync"), onClick: () => openSettings("sync") },
    });
  }
  for (const row of rows) {
    const s = row.suggestion;
    if (row.official || !s) continue;
    todos.push({
      key: `confirm:${row.slug}`,
      dot: "bg-warn",
      track: row.name,
      what: t("tracks.todo.confirmWhat"),
      why: t(row.bundle ? "tracks.todo.confirmWhy" : "tracks.todo.confirmWhyNoBundle", {
        name: s.official_name,
        turns: t("tracks.turns", { count: s.turns }),
        length: formatInt(s.length_m),
        why: suggestionWhy(t, s),
      }),
      primary: row.bundle
        ? confirmAction(row)
        : { label: t("tracks.surveyThis"), href: SURVEY_HREF },
      secondary: rejectAction(row, s),
    });
  }
  for (const log of orphans) {
    todos.push({
      key: `orphan:${log.name}`,
      dot: "bg-warn",
      track: t("tracks.todo.surveyRun", { when: when(log.started_at) }),
      what: t("tracks.todo.orphanWhat"),
      why: (
        <Tip content={t("tracks.todo.orphanTip")}>
          <span>
            {t("tracks.todo.orphanWhy", {
              counts: t("tracks.logCounts", {
                marks: formatInt(log.marks),
                transitions: formatInt(log.transitions),
              }),
              size: bytes(log.bytes),
            })}
          </span>
        </Tip>
      ),
      primary: { label: t("tracks.assign"), disabled: busy, onClick: () => setAssigning(log) },
      secondary: {
        label: t("common.download"),
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
      what: t("tracks.todo.coverageWhat", { pct: String(cov.road_pct) }),
      why: t("tracks.todo.coverageWhy", { count: row.bundle!.runs }),
      primary: { label: t("tracks.surveyAgain"), href: SURVEY_HREF },
      secondary: { label: t("tracks.details"), onClick: () => setSelected(row.slug) },
    });
  }
  const unlabelled = rows.filter((r) => r.bundle != null && r.bundle.corners === 0);
  if (unlabelled.length > 0) {
    const first = unlabelled[0];
    todos.push({
      key: "corners",
      dot: "bg-ink-faint",
      track:
        unlabelled.length === 1
          ? first.name
          : t("tracks.circuits", { count: unlabelled.length }),
      what: t("tracks.todo.noCorners", { count: unlabelled.length }),
      why:
        unlabelled.length === 1
          ? t("tracks.todo.cornersWhy")
          : t("tracks.todo.cornersWhyMany", { names: list(unlabelled.map((r) => r.name)) }),
      primary: {
        label: unlabelled.length === 1 ? t("tracks.labelCorners") : t("tracks.todo.labelNext"),
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
      label: t("tracks.check.identify"),
      detail: t(identifyWhyKey(current)),
    });
    if (b) {
      checks.push({
        key: "survey",
        tone: "ok",
        label: t("tracks.label.survey"),
        detail: [
          t("tracks.check.surveyBorder", { points: formatInt(b.points), count: b.runs }),
          b.sources > 1 && t("tracks.sources", { count: b.sources }),
          t("tracks.updatedAt", { when: when(b.updated_at) }),
        ]
          .filter(Boolean)
          .join(" · "),
        // Border metres, NOT lap length — one record per metre per side.
        tip: t("tracks.check.surveyTip"),
      });
      const cov = b.coverage;
      if (cov) {
        const closed = cov.L.closed && cov.R.closed;
        const pcts = t("tracks.check.pcts", {
          l: String(cov.L.pct),
          r: String(cov.R.pct),
          road: String(cov.road_pct),
        });
        checks.push({
          key: "borders",
          tone: closed ? "ok" : "warn",
          label: t("tracks.check.borders"),
          detail: t(
            closed
              ? "tracks.check.bordersClosed"
              : cov.road_pct < 50
                ? "tracks.check.bordersMissing"
                : "tracks.check.bordersGaps",
            { pcts },
          ),
          tip: t("tracks.coverageTip"),
          action:
            cov.road_pct < 50 ? { label: t("tracks.surveyAgain"), href: SURVEY_HREF } : undefined,
        });
      } else {
        checks.push({
          key: "borders",
          tone: "todo",
          label: t("tracks.check.borders"),
          detail: t("tracks.check.notCompiled"),
        });
      }
      checks.push(
        b.elevation_pct >= 50
          ? {
              key: "elev",
              tone: "ok",
              label: t("tracks.check.elevation"),
              detail: t("tracks.check.elevationCaptured", { pct: String(b.elevation_pct) }),
            }
          : {
              key: "elev",
              tone: "warn",
              label: t("tracks.check.elevation"),
              detail: t("tracks.check.elevationOld", { pct: String(b.elevation_pct) }),
              tip: t("tracks.check.elevationTip"),
              action: { label: t("tracks.check.driveLap"), href: SURVEY_HREF },
            },
      );
      checks.push({
        key: "finish",
        tone: b.finish_crossings > 0 ? "ok" : "warn",
        label: t("tracks.check.finish"),
        detail:
          b.finish_crossings > 0
            ? t("tracks.check.finishLocated")
            : t("tracks.check.finishMissing"),
        tip: t("tracks.check.finishTip"),
      });
      const done = turns ? b.corners >= turns : b.corners > 0;
      checks.push({
        key: "corners",
        tone: done ? "ok" : "todo",
        label: t("tracks.label.corners"),
        detail: turns
          ? t(done ? "tracks.check.cornersDone" : "tracks.check.cornersPartial", {
              corners: String(b.corners),
              turns: String(turns),
            })
          : t("tracks.check.cornersLabelled", { count: b.corners }),
        action: {
          label: done ? t("tracks.check.editCorners") : t("tracks.labelCorners"),
          title: t("tracks.check.labelCornersTitle"),
          onClick: () => setEditing(current),
        },
      });
    } else {
      checks.push({
        key: "survey",
        tone: "todo",
        label: t("tracks.label.survey"),
        detail: t("tracks.check.notSurveyed"),
        action: { label: t("tracks.surveyThis"), href: SURVEY_HREF },
      });
    }
    const o = current.official;
    const s = current.suggestion;
    const ruledOut = current.suggestion_rejected;
    checks.push(
      o
        ? {
            key: "official",
            tone: "ok",
            label: t("tracks.check.official"),
            detail: t("tracks.check.officialConfirmed", {
              name: o.official_name,
              turns: t("tracks.turns", { count: o.turns }),
              length: formatInt(o.length_m),
            }),
          }
        : s
          ? {
              key: "official",
              tone: "warn",
              label: t("tracks.check.official"),
              detail: t(
                ruledOut ? "tracks.check.officialLooksLikeRuledOut" : "tracks.check.officialLooksLike",
                {
                  name: s.official_name,
                  turns: t("tracks.turns", { count: s.turns }),
                  length: formatInt(s.length_m),
                  why: suggestionWhy(t, s),
                  ...(ruledOut ? { count: ruledOut } : {}),
                },
              ),
              action: confirmAction(current),
              secondary: rejectAction(current, s),
            }
          : {
              key: "official",
              tone: "todo",
              label: t("tracks.check.official"),
              detail: ruledOut
                ? t("tracks.check.noSuggestionLeft", { count: ruledOut })
                : t("tracks.check.notMatched"),
              // The way back from a mis-click, or from a circuit renamed so
              // the ruled-out guess now fits after all.
              action: ruledOut
                ? {
                    label: t("tracks.check.suggestAgain"),
                    disabled: busy,
                    title: t("tracks.check.suggestAgainTitle"),
                    onClick: () =>
                      void run(t("tracks.toast.suggestionsRestored"), () =>
                        api.clearRejectedSuggestions(current.name),
                      ),
                  }
                : undefined,
            },
    );
    if (!b) {
      checks.push({
        key: "corners",
        tone: "todo",
        label: t("tracks.label.corners"),
        detail: t("tracks.check.cornersLater"),
      });
    }
    const sectorBoundaries = b?.sectors ?? 0;
    checks.push({
      key: "sectors",
      tone: sectorBoundaries > 0 ? "ok" : "todo",
      label: t("tracks.label.sectors"),
      detail:
        sectorBoundaries > 0
          ? t("tracks.check.sectorsConfigured", {
              sectors: t("count.sectors", { count: sectorBoundaries + 1 }),
              count: sectorBoundaries,
            })
          : b
            ? t("tracks.check.sectorsNone")
            : t("tracks.check.sectorsNeedSurvey"),
      action: b
        ? {
            label:
              sectorBoundaries > 0 ? t("tracks.check.editSectors") : t("tracks.check.configureSectors"),
            title: t("tracks.check.configureSectorsTitle"),
            onClick: () => setEditing(current),
          }
        : undefined,
    });
    const info = b && syncActive && current.sync ? syncInfo(current.sync, t) : null;
    if (info && current.sync) {
      const st = current.sync.status;
      checks.push({
        key: "sync",
        tone: st === "synced" ? "ok" : st === "rejected" || st === "error" ? "warn" : "todo",
        label: t("tracks.label.sync"),
        detail: (
          <span className="flex flex-col items-start gap-1">
            <SyncChip sync={current.sync} />
            <span>{info.title}</span>
          </span>
        ),
        action:
          st === "queued" || st === "error"
            ? {
                label: t("sync.push.tracks"),
                disabled: busy,
                onClick: () =>
                  void run(t("tracks.toast.syncStarted"), () => api.admin.syncPush("tracks")),
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
    const sessions = row.sessions
      ? t("count.sessions", { count: row.sessions })
      : t("tracks.noSessions");
    if (row.official) {
      return t("tracks.official.line", {
        turns: t("tracks.turns", { count: row.official.turns }),
        length: formatInt(row.official.length_m),
        sessions,
      });
    }
    return row.bundle
      ? t("tracks.official.none", { sessions })
      : t("tracks.official.notSurveyed", { sessions });
  };

  const subLine = (row: TrackOverviewRow) => {
    const b = row.bundle;
    if (b) {
      const len = row.official?.length_m ?? row.length_m;
      return [
        row.official && t("tracks.turns", { count: row.official.turns }),
        len != null && `${formatInt(len)} m`,
        t("tracks.updatedAt", { when: ago(b.updated_at, t) }),
      ]
        .filter(Boolean)
        .join(" · ");
    }
    if (row.provenance === "seed") return t("tracks.namedBySeed");
    if (row.provenance === "user") return t("tracks.namedByUser");
    return "";
  };

  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-3">
      <div className="flex flex-wrap items-baseline gap-3">
        <h2 className="text-[17px] font-medium">{t("nav.tracks")}</h2>
        {data && (
          <span className="font-tabular text-[11.5px] text-ink-faint">
            {t("tracks.summary", {
              circuits: t("tracks.circuits", { count: rows.length }),
              surveyed: bundleCount,
              ready: readyCount,
            })}
          </span>
        )}
        <div className="ml-auto flex items-center gap-2">
          <Tip content={t("tracks.identifyTip")}>
            <span className="inline-flex">
              <button
                className="btn btn-primary px-3 py-[5px] text-[11.5px]"
                disabled={busy || bundleCount === 0}
                onClick={onIdentify}
              >
                {t("tracks.identifySessions")}
              </button>
            </span>
          </Tip>
          <Menu
            label={t("common.import")}
            disabled={busy}
            items={[
              {
                label: t("tracks.menu.importBundle"),
                hint: t("tracks.menu.importBundleHint"),
                onSelect: () => {
                  importTarget.current = undefined;
                  fileInput.current?.click();
                },
              },
              {
                label: t("tracks.menu.uploadLog"),
                hint: t("tracks.menu.uploadLogHint"),
                onSelect: () => logInput.current?.click(),
              },
              {
                label: t("tracks.menu.pullShared"),
                hint: shared?.configured
                  ? t("tracks.menu.pullSharedHint")
                  : sharedError
                    ? t("tracks.menu.sharedUnreadable")
                    : t("tracks.menu.noShared"),
                disabled: !shared?.configured && !sharedError,
                onSelect: () => setSharedOpen(true),
              },
              {
                label: t("tracks.menu.pullAll"),
                hint: t("tracks.menu.pullAllHint"),
                disabled: !shared?.configured || busy,
                onSelect: () => void onPullAllShared(),
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
            <span className="section-header text-warn!">{t("tracks.needs.title")}</span>
            {todos.length > 0 && (
              <span className="text-[10.5px] text-ink-faint">
                {t("tracks.needs.count", { count: todos.length })}
              </span>
            )}
          </div>
          <div className="rule" />
          {todos.length === 0 ? (
            <div className="px-4 py-3 text-xs text-throttle">{t("tracks.needs.none")}</div>
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
              ariaLabel={t("tracks.filter.aria")}
              value={filter}
              onValueChange={setFilter}
              options={[
                { value: "all", label: t("tracks.filter.all", { n: counts.all }) },
                { value: "attention", label: t("tracks.filter.attention", { n: counts.attention }) },
                { value: "ready", label: t("tracks.filter.ready", { n: counts.ready }) },
                { value: "unsurveyed", label: t("tracks.filter.unsurveyed", { n: counts.unsurveyed }) },
              ]}
            />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("tracks.find")}
              aria-label={t("tracks.find")}
              className="ml-auto w-[180px] rounded-md border border-edge bg-panel-2 px-2.5 py-1 text-xs text-ink placeholder:text-ink-ghost"
            />
          </div>
          <div className="rule" />
          {!data && !error && (
            <div className="p-6 text-center text-xs text-ink-dim">{t("common.loading")}</div>
          )}
          {data && rows.length === 0 && (
            <div className="p-8 text-center text-sm text-ink-dim">
              {t("tracks.empty")}
              {shared?.configured && (
                <div className="mt-3">
                  <button className="btn btn-primary" disabled={busy} onClick={() => void onPullAllShared()}>
                    {t("tracks.pullAllCircuits")}
                  </button>
                </div>
              )}
            </div>
          )}
          {rows.length > 0 && visible.length === 0 && (
            <div className="p-6 text-center text-xs text-ink-dim">{t("tracks.noMatch")}</div>
          )}
          {visible.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse font-tabular text-xs">
                <thead>
                  <tr className="text-left text-[11px] text-ink-faint">
                    <th className="px-4 py-2 font-normal">{t("tracks.col.circuit")}</th>
                    <th className="px-2 py-2 font-normal">{t("tracks.ready")}</th>
                    <th className="px-2 py-2 font-normal">
                      <Tip content={t("tracks.coverageTip")}>
                        <span>{t("tracks.col.coverage")}</span>
                      </Tip>
                    </th>
                    <th className="px-2 py-2 font-normal">{t("tracks.label.corners")}</th>
                    <th className="px-2 py-2 font-normal">{t("nav.sessions")}</th>
                    {syncActive && <th className="px-4 py-2 font-normal">{t("tracks.label.sync")}</th>}
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
                              <CoverageBar label={t("tracks.coverage.left")} pct={b.coverage.L.pct} />
                              <CoverageBar label={t("tracks.coverage.right")} pct={b.coverage.R.pct} />
                              <CoverageBar label={t("tracks.coverage.road")} pct={b.coverage.road_pct} />
                            </span>
                          ) : (
                            <span className="text-[11px] text-ink-ghost">
                              {b ? t("tracks.notCompiled") : t("tracks.notSurveyed")}
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
                    {ck.action ? (
                      <div className="flex gap-1.5">
                        <ActionButton action={ck.action} />
                        {ck.secondary && <ActionButton action={ck.secondary} />}
                      </div>
                    ) : (
                      <span />
                    )}
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
                {t("tracks.exportBundle")}
              </a>
              <ActionButton
                action={{
                  label: t("tracks.mergeInto"),
                  disabled: !current.bundle || busy,
                  title: t("tracks.mergeIntoTitle"),
                  onClick: () => {
                    importTarget.current = current.name;
                    fileInput.current?.click();
                  },
                }}
              />
              <ActionButton
                action={{
                  label: t("tracks.renameAction"),
                  disabled: !current.bundle || busy,
                  title: t("tracks.renameTitle"),
                  onClick: () => setRenaming(current),
                }}
              />
              <ActionButton
                action={{
                  label: t("tracks.recheck"),
                  disabled: busy || current.sessions === 0,
                  title:
                    current.sessions > 0 ? t("tracks.recheckTitle") : t("tracks.recheckNone"),
                  onClick: () => void onRejudge(current),
                }}
              />
              {current.bundle && (
                <button
                  className="btn btn-danger ml-auto"
                  disabled={busy}
                  onClick={() => setDeleting(current)}
                >
                  {t("tracks.deleteBundle")}
                </button>
              )}
            </div>
          </aside>
        )}
      </div>

      {(data?.logs.length ?? 0) > 0 && (
        <details className="panel px-4 py-3">
          <summary className="section-header cursor-pointer">
            {t("tracks.logs.title", { n: data!.logs.length })}
          </summary>
          <p className="mt-1.5 text-[10.5px] text-ink-faint">{t("tracks.logs.blurb")}</p>
          <ul className="mt-2 space-y-1.5">
            {data!.logs.map((log) => (
              <li
                key={log.name}
                className="flex flex-wrap items-center gap-2 rounded-lg border border-edge px-2 py-1.5 text-xs"
              >
                <span className="font-tabular">{log.name}</span>
                <span className="text-ink-dim">
                  {log.track || t("tracks.unassigned")} ·{" "}
                  {t("tracks.logCounts", {
                    marks: formatInt(log.marks),
                    transitions: formatInt(log.transitions),
                  })}{" "}
                  · {bytes(log.bytes)}
                </span>
                <a
                  className="btn ml-auto"
                  href={api.survey.logDownloadUrl(log.name)}
                  download={log.name}
                >
                  {t("common.download")}
                </a>
                {log.orphaned && (
                  <button className="btn" disabled={busy} onClick={() => setAssigning(log)}>
                    {t("tracks.assign")}
                  </button>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}

      {data && (
        <p className="px-1 text-xs text-ink-dim">
          {t("tracks.footer.configs", { count: data.catalog_configs })} ·{" "}
          {data.seeded_signatures > 0 && (
            <>{t("tracks.footer.seeded", { count: data.seeded_signatures })} · </>
          )}
          {rich(t("tracks.footer.installation"), {
            source: <span className="font-tabular">{data.source}</span>,
          })}
          {!getAdminToken() && ` ${t("tracks.footer.adminToken")}`}
        </p>
      )}

      <LargeDialog
        open={sharedOpen}
        title={t("tracks.shared.title")}
        size="medium"
        onClose={() => setSharedOpen(false)}
      >
        <div className="h-full overflow-y-auto px-4 py-3">
          {shared?.configured && shared.bundles.length > 0 && (
            <div className="mb-2 flex items-center justify-between gap-2">
              <span className="text-xs text-ink-dim">
                {t("tracks.shared.onOffer", { count: shared.bundles.length })}
              </span>
              <button className="btn btn-primary" disabled={busy} onClick={() => void onPullAllShared()}>
                {t("tracks.shared.pullAll")}
              </button>
            </div>
          )}
          <p className="text-[11px] text-ink-faint">{t("tracks.shared.blurb")}</p>
          {sharedError && (
            <div className="mt-2 text-xs text-brake">
              {t("tracks.shared.readError", { error: sharedError })}
            </div>
          )}
          {shared?.configured && shared.bundles.length === 0 && (
            <div className="mt-2 text-xs text-ink-dim">{t("tracks.shared.empty")}</div>
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
                    {entry.points != null &&
                      t("tracks.borderMetres", { points: formatInt(entry.points) })}
                    {entry.runs != null && ` · ${t("tracks.runs", { count: entry.runs })}`}
                    {entry.updated_at &&
                      ` · ${t("tracks.updatedAt", { when: when(entry.updated_at) })}`}
                  </span>
                  {entry.corrections && (
                    <Tip content={t("tracks.shared.correctionsTip")}>
                      <span className="rounded-[9px] bg-accent/14 px-2 py-px text-[10px] text-accent">
                        {t("tracks.shared.corrected")}
                      </span>
                    </Tip>
                  )}
                  <span className="text-ink-dim">
                    ·{" "}
                    {local
                      ? t("tracks.shared.local", { points: formatInt(local.points) })
                      : t("tracks.shared.notHere")}
                  </span>
                  <ActionButton
                    className="ml-auto"
                    action={{
                      label: local ? t("tracks.shared.pullMerge") : t("tracks.shared.pull"),
                      disabled: busy,
                      title: local ? t("tracks.shared.pullMergeTitle") : t("tracks.shared.pullTitle"),
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
        title={t("tracks.delete.title", { name: deleting?.name ?? "" })}
        body={t("tracks.delete.body")}
        confirmLabel={t("common.delete")}
        danger
        onCancel={() => setDeleting(null)}
        onConfirm={() => {
          const row = deleting;
          setDeleting(null);
          if (row) void run(t("tracks.toast.bundleDeleted"), () => api.bundles.remove(row.slug));
        }}
      />

      <PromptDialog
        open={renaming != null}
        title={t("tracks.rename.title", { name: renaming?.name ?? "" })}
        label={t("tracks.rename.label")}
        placeholder={t("tracks.trackNamePlaceholder")}
        submitLabel={t("common.rename")}
        initialValue={renaming?.name ?? ""}
        suggestions={knownNames}
        onCancel={() => setRenaming(null)}
        onSubmit={(name) => {
          const row = renaming;
          setRenaming(null);
          if (row) void run(t("tracks.toast.renamed"), () => api.bundles.rename(row.slug, name));
        }}
      />

      <PromptDialog
        open={assigning != null}
        title={t("tracks.assign.title")}
        label={t("tracks.assign.label")}
        placeholder={t("tracks.trackNamePlaceholder")}
        submitLabel={t("tracks.assign.submit")}
        suggestions={knownNames}
        onCancel={() => setAssigning(null)}
        onSubmit={(name) => {
          const log = assigning;
          setAssigning(null);
          if (log) void run(t("tracks.toast.runRecovered"), () => api.survey.assignLog(log.name, name));
        }}
      />
    </div>
  );
}
