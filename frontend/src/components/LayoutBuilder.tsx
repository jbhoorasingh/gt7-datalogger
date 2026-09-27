// Visual builder for grid overlays and driver dashboards: drag widgets on a
// snapping canvas, pick a variant per widget, and save named layouts to the
// server so OBS/phones get short stable URLs (/overlay?layout=<name>).
//
// useLayoutBuilder owns the state (the localStorage draft, the server copies,
// edits parked per layout, the selected cell); the panels below each render
// one piece of it and OverlaysView arranges them into the page.

import { useEffect, useMemo, useRef, useState } from "react";
import { GridCanvas } from "@/components/GridCanvas";
import { ConfirmDialog, PromptDialog } from "@/components/ui/Dialog";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { Select } from "@/components/ui/Select";
import { Toggle } from "@/components/ui/Toggle";
import { api } from "@/lib/api";
import { DASH_PRESETS } from "@/lib/dashPresets";
import {
  DEFAULT_LAYOUT,
  MAX_GRID_DIM,
  migrateOverlayConfig,
  normalizeLayout,
  type LayoutCell,
  type LayoutConfig,
  type LayoutSummary,
} from "@/lib/layout";
import { DEFAULT_CONFIG, type OverlayConfig, type OverlaySize } from "@/lib/overlay";
import { parseHash, routeHash } from "@/lib/router";
import { useLiveFrame } from "@/lib/useLiveFrame";
import {
  defaultSize,
  WIDGET_GROUP_LABELS,
  WIDGET_META,
  type WidgetGroup,
} from "@/lib/widgetMeta";
import { toast } from "@/store/toasts";

const DRAFT_KEY = "gt7-layout-draft";
const LEGACY_PRESETS_KEY = "gt7-overlay-presets";
const LEGACY_MIGRATED_KEY = "gt7-overlay-migrated";

const SCALE_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 2];

// Canvas sizes offered in the toolbar; anything else is set with the custom
// W × H inputs under "Canvas & page".
const CANVAS_SIZES: { key: string; label: string; size: OverlaySize | null }[] = [
  { key: "fill", label: "Fill", size: null },
  { key: "1920x1080", label: "1920×1080", size: { width: 1920, height: 1080 } },
  { key: "1920x260", label: "Strip 1920×260", size: { width: 1920, height: 260 } },
  { key: "1080x1920", label: "1080×1920", size: { width: 1080, height: 1920 } },
  { key: "720x1280", label: "720×1280", size: { width: 720, height: 1280 } },
  { key: "1280x800", label: "Tablet 1280×800", size: { width: 1280, height: 800 } },
];

// What "New" starts from: an empty full-HD grid.
const BLANK_LAYOUT: LayoutConfig = {
  ...DEFAULT_LAYOUT,
  grid: { cols: 12, rows: 8, gap: 8 },
  cells: [],
  size: { width: 1920, height: 1080 },
};

export type LayoutKind = "overlay" | "dash";

export interface Draft {
  layout: LayoutConfig;
  id: number | null;
  name: string | null;
  kind: LayoutKind;
}

// Parked edits are keyed by layout id; the one unsaved (never-saved) draft
// uses this key.
const NEW_KEY = "new";
const keyOf = (d: Draft) => (d.id == null ? NEW_KEY : String(d.id));

function loadDraft(): Draft {
  try {
    const raw = localStorage.getItem(DRAFT_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<Draft>;
      return {
        layout: normalizeLayout(parsed.layout),
        id: typeof parsed.id === "number" ? parsed.id : null,
        name: typeof parsed.name === "string" ? parsed.name : null,
        kind: parsed.kind === "dash" ? "dash" : "overlay",
      };
    }
  } catch {
    // corrupt draft — start fresh
  }
  return { layout: DEFAULT_LAYOUT, id: null, name: null, kind: "overlay" };
}

function loadLegacyPresets(): Record<string, unknown> | null {
  try {
    if (localStorage.getItem(LEGACY_MIGRATED_KEY)) return null;
    const raw = localStorage.getItem(LEGACY_PRESETS_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    return Object.keys(parsed).length > 0 ? parsed : null;
  } catch {
    return null;
  }
}

function fromSummary(s: LayoutSummary): Draft {
  return { layout: normalizeLayout(s.config), id: s.id, name: s.name, kind: s.kind };
}

// Edits differ from the server copy when the kind or the normalized config
// does. A draft that was never saved always counts as unsaved.
function differs(d: Draft, server: LayoutSummary | undefined): boolean {
  if (!server) return true;
  return (
    server.kind !== d.kind ||
    JSON.stringify(normalizeLayout(server.config)) !== JSON.stringify(normalizeLayout(d.layout))
  );
}

function newCellId(cells: LayoutCell[]): string {
  let n = cells.length + 1;
  while (cells.some((c) => c.id === `c${n}`)) n += 1;
  return `c${n}`;
}

// First free position (row-major) where the footprint fits; null if the grid
// is full.
export function findFreeSpot(
  layout: LayoutConfig,
  w: number,
  h: number,
): { x: number; y: number } | null {
  for (let y = 0; y + h <= layout.grid.rows; y++) {
    for (let x = 0; x + w <= layout.grid.cols; x++) {
      const collides = layout.cells.some(
        (c) => c.x < x + w && x < c.x + c.w && c.y < y + h && y < c.y + c.h,
      );
      if (!collides) return { x, y };
    }
  }
  return null;
}

function fitsAt(layout: LayoutConfig, skipId: string, x: number, y: number, w: number, h: number) {
  return (
    x >= 0 &&
    y >= 0 &&
    x + w <= layout.grid.cols &&
    y + h <= layout.grid.rows &&
    !layout.cells.some(
      (c) => c.id !== skipId && c.x < x + w && x < c.x + c.w && c.y < y + h && y < c.y + c.h,
    )
  );
}

export function sizeLabel(size: OverlaySize | null): string {
  return size ? `${size.width}×${size.height}` : "fill";
}

function conflictMessage(e: unknown, name: string): string {
  return String(e).includes("409") ? `A layout named "${name}" already exists` : String(e);
}

type DialogKind = "saveAs" | "saveCopy" | "rename" | "delete" | "discardNew";

// `requested` is the ?layout=<id or name> route param: once the server list
// is in, that layout is opened (the Settings page's Edit links land here).
export function useLayoutBuilder(requested: string | null) {
  const [draft, setDraft] = useState<Draft>(loadDraft);
  const [saved, setSaved] = useState<LayoutSummary[] | null>(null);
  // Edits to layouts that aren't open right now, so switching doesn't lose them.
  const [parked, setParked] = useState<Record<string, Draft>>({});
  const [selected, setSelected] = useState<string | null>(null);
  const [dialog, setDialog] = useState<DialogKind | null>(null);
  // A preset/New/import waiting on "replace the unsaved draft?".
  const [pendingStart, setPendingStart] = useState<{ draft: Draft; label: string } | null>(null);
  const [legacyPresets, setLegacyPresets] = useState(loadLegacyPresets);
  const importFile = useRef<HTMLInputElement>(null);

  const { layout } = draft;

  useEffect(() => {
    localStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
  }, [draft]);

  const refreshList = () =>
    api.layouts
      .list()
      .then(setSaved)
      .catch(() => setSaved((s) => s ?? []));
  useEffect(() => {
    void refreshList();
  }, []);

  // LAN URL for other devices (OBS on another PC, phone on the wall). Stats
  // needs the admin token when one is set; without it, this page's own host.
  const [lan, setLan] = useState<{ ip: string; port: number } | null>(null);
  useEffect(() => {
    api.admin
      .stats()
      .then((s) => s.lan_ip && setLan({ ip: s.lan_ip, port: s.http_port }))
      .catch(() => {});
  }, []);

  const serverCopy = (id: number | null) =>
    id == null ? undefined : saved?.find((s) => s.id === id);

  // A layout deleted elsewhere leaves this draft pointing at nothing — it
  // becomes an unsaved draft rather than silently saving to a missing id.
  useEffect(() => {
    if (saved && draft.id != null && !saved.some((s) => s.id === draft.id)) {
      setDraft((d) => ({ ...d, id: null, name: null }));
    }
  }, [saved, draft.id]);

  const dirty = useMemo(
    () => (draft.id == null ? true : saved == null ? false : differs(draft, serverCopy(draft.id))),
    [draft, saved],
  );
  const kindChanged = draft.id != null && serverCopy(draft.id)?.kind !== draft.kind && saved != null;

  // Keep the address bar on the open layout so a reload or bookmark reopens it.
  function reflect(id: number | null) {
    if (parseHash(window.location.hash).view !== "overlays") return;
    history.replaceState(
      null,
      "",
      routeHash("overlays", id != null ? { layout: String(id) } : undefined),
    );
  }

  // Switch the editor to `next`, parking the current edits if it has any.
  function open(next: Draft) {
    const curKey = keyOf(draft);
    setParked((p) => {
      const copy = { ...p };
      if (dirty && !(draft.id == null && draft.layout.cells.length === 0)) copy[curKey] = draft;
      else delete copy[curKey];
      delete copy[keyOf(next)];
      return copy;
    });
    setDraft(next);
    setSelected(null);
    reflect(next.id);
  }

  function openSaved(s: LayoutSummary) {
    if (s.id === draft.id) return;
    open(parked[String(s.id)] ?? fromSummary(s));
  }

  function openUnsaved() {
    const d = parked[NEW_KEY];
    if (d && draft.id != null) open(d);
  }

  // The ?layout= param, applied once per value after the list loads.
  const applied = useRef<string | null>(null);
  const openSavedRef = useRef(openSaved);
  openSavedRef.current = openSaved;
  useEffect(() => {
    if (!requested || !saved || applied.current === requested) return;
    applied.current = requested;
    const hit =
      saved.find((s) => String(s.id) === requested) ?? saved.find((s) => s.name === requested);
    if (hit) openSavedRef.current(hit);
    else toast(`No saved layout "${requested}"`, "error");
  }, [requested, saved]);

  // An unsaved draft with widgets on it would be lost by starting over.
  const unsavedAtRisk =
    (draft.id == null && draft.layout.cells.length > 0) ||
    (parked[NEW_KEY]?.layout.cells.length ?? 0) > 0;

  function startFrom(next: LayoutConfig, kind: LayoutKind, label: string) {
    const d: Draft = { layout: normalizeLayout(next), id: null, name: null, kind };
    if (unsavedAtRisk) setPendingStart({ draft: d, label });
    else commitStart(d, label);
  }

  function commitStart(d: Draft, label: string) {
    setPendingStart(null);
    // The old unsaved draft is being replaced, not parked.
    setParked((p) => {
      const copy = { ...p };
      delete copy[NEW_KEY];
      if (draft.id != null && dirty) copy[String(draft.id)] = draft;
      return copy;
    });
    setDraft(d);
    setSelected(null);
    reflect(null);
    toast(`Started from ${label}`, "success");
  }

  function setLayout(next: LayoutConfig) {
    setDraft((d) => ({ ...d, layout: next }));
  }

  function patchLayout(patch: Partial<LayoutConfig>) {
    setLayout({ ...layout, ...patch });
  }

  function setKind(kind: LayoutKind) {
    setDraft((d) => ({ ...d, kind }));
  }

  function mergeSaved(s: LayoutSummary) {
    setSaved((list) => {
      const rest = (list ?? []).filter((x) => x.id !== s.id);
      return [...rest, s].sort((a, b) => a.name.localeCompare(b.name));
    });
  }

  async function save() {
    // The server can't change a saved layout's kind — that takes a new layout.
    if (draft.id == null || kindChanged) {
      setDialog("saveAs");
      return;
    }
    try {
      const updated = await api.layouts.update(draft.id, { config: layout });
      mergeSaved(updated);
      await refreshList();
      toast(`Layout "${draft.name}" saved`, "success");
    } catch (e) {
      toast(String(e), "error");
    }
  }

  async function saveAs(name: string) {
    setDialog(null);
    try {
      const created = await api.layouts.create(name, draft.kind, layout);
      // A saved layout's edits went into the new one; the original stays as saved.
      mergeSaved(created);
      setDraft((d) => ({ ...d, id: created.id, name: created.name }));
      reflect(created.id);
      await refreshList();
      toast(`Layout "${name}" saved`, "success");
    } catch (e) {
      toast(conflictMessage(e, name), "error");
    }
  }

  async function rename(name: string) {
    setDialog(null);
    if (draft.id == null) return;
    try {
      const updated = await api.layouts.update(draft.id, { name });
      mergeSaved(updated);
      setDraft((d) => ({ ...d, name: updated.name }));
      await refreshList();
      toast(`Renamed to "${name}"`, "success");
    } catch (e) {
      toast(conflictMessage(e, name), "error");
    }
  }

  async function removeLayout() {
    setDialog(null);
    if (draft.id == null) return;
    const id = draft.id;
    try {
      await api.layouts.remove(id);
      setParked((p) => {
        const copy = { ...p };
        delete copy[String(id)];
        return copy;
      });
      setDraft((d) => ({ ...d, id: null, name: null }));
      setSaved((list) => (list ?? []).filter((s) => s.id !== id));
      reflect(null);
      await refreshList();
      toast("Layout deleted — the draft stays here until you save again", "success");
    } catch (e) {
      toast(String(e), "error");
    }
  }

  function discard() {
    const server = serverCopy(draft.id);
    if (draft.id == null || !server) {
      setDialog("discardNew");
      return;
    }
    setDraft(fromSummary(server));
    setSelected(null);
  }

  // Dropping the unsaved draft opens the first saved layout, or a blank one.
  function discardNew() {
    setDialog(null);
    setParked((p) => {
      const copy = { ...p };
      delete copy[NEW_KEY];
      return copy;
    });
    const first = saved?.[0];
    if (first) {
      setDraft(parked[String(first.id)] ?? fromSummary(first));
      reflect(first.id);
    } else {
      setDraft({ layout: BLANK_LAYOUT, id: null, name: null, kind: "overlay" });
    }
    setSelected(null);
  }

  function addWidget(id: keyof typeof WIDGET_META) {
    const meta = WIDGET_META[id];
    // Try the default footprint first, then any smaller allowed one.
    for (const [w, h] of [defaultSize(id), ...meta.sizes]) {
      const spot = findFreeSpot(layout, w, h);
      if (spot) {
        const cell: LayoutCell = {
          id: newCellId(layout.cells),
          widget: id,
          variant: meta.defaultVariant,
          x: spot.x,
          y: spot.y,
          w,
          h,
        };
        patchLayout({ cells: [...layout.cells, cell] });
        setSelected(cell.id);
        return;
      }
    }
    toast("No room on the grid — enlarge it or remove a widget", "error");
  }

  function duplicateCell(id: string) {
    const cell = layout.cells.find((c) => c.id === id);
    if (!cell) return;
    for (const [w, h] of [[cell.w, cell.h], ...WIDGET_META[cell.widget].sizes]) {
      const spot = findFreeSpot(layout, w, h);
      if (spot) {
        const copy: LayoutCell = { ...cell, id: newCellId(layout.cells), ...spot, w, h };
        patchLayout({ cells: [...layout.cells, copy] });
        setSelected(copy.id);
        return;
      }
    }
    toast("No room on the grid for a copy — enlarge it or remove a widget", "error");
  }

  function updateCell(id: string, patch: Partial<LayoutCell>) {
    patchLayout({
      cells: layout.cells.map((c) => (c.id === id ? { ...c, ...patch } : c)),
    });
  }

  function resizeCell(id: string, w: number, h: number) {
    const cell = layout.cells.find((c) => c.id === id);
    if (!cell) return;
    if (!fitsAt(layout, id, cell.x, cell.y, w, h)) {
      toast("That size doesn't fit here — move the widget first", "error");
      return;
    }
    updateCell(id, { w, h });
  }

  function removeCell(id: string) {
    patchLayout({ cells: layout.cells.filter((c) => c.id !== id) });
    setSelected(null);
  }

  // Arrow keys nudge the selected widget one cell; Delete removes it.
  const keyHandler = useRef<(e: KeyboardEvent) => void>(() => {});
  keyHandler.current = (e: KeyboardEvent) => {
    if (!selected || dialog || pendingStart || e.metaKey || e.ctrlKey || e.altKey) return;
    const t = e.target as HTMLElement | null;
    if (t?.closest("input, textarea, select, [contenteditable], [role=dialog]")) return;
    const cell = layout.cells.find((c) => c.id === selected);
    if (!cell) return;
    if (e.key === "Delete" || e.key === "Backspace") {
      e.preventDefault();
      removeCell(cell.id);
      return;
    }
    if (e.key === "Escape") {
      setSelected(null);
      return;
    }
    const step: Record<string, [number, number]> = {
      ArrowLeft: [-1, 0],
      ArrowRight: [1, 0],
      ArrowUp: [0, -1],
      ArrowDown: [0, 1],
    };
    const d = step[e.key];
    if (!d) return;
    e.preventDefault();
    const x = cell.x + d[0];
    const y = cell.y + d[1];
    if (fitsAt(layout, cell.id, x, y, cell.w, cell.h)) updateCell(cell.id, { x, y });
  };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => keyHandler.current(e);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  async function migrateLegacyPresets() {
    if (!legacyPresets) return;
    const existing = new Set((saved ?? []).map((s) => s.name));
    let migrated = 0;
    for (const [name, cfg] of Object.entries(legacyPresets)) {
      const v1 = {
        ...DEFAULT_CONFIG,
        ...(typeof cfg === "object" && cfg !== null ? cfg : {}),
      } as OverlayConfig;
      let candidate = name;
      let i = 2;
      while (existing.has(candidate)) candidate = `${name}-${i++}`;
      try {
        await api.layouts.create(candidate, "overlay", migrateOverlayConfig(v1));
        existing.add(candidate);
        migrated += 1;
      } catch {
        // skip presets the server rejects; the rest still migrate
      }
    }
    localStorage.setItem(LEGACY_MIGRATED_KEY, "1");
    setLegacyPresets(null);
    await refreshList();
    toast(`Migrated ${migrated} legacy preset${migrated === 1 ? "" : "s"} to server layouts`, "success");
  }

  function dismissLegacy() {
    localStorage.setItem(LEGACY_MIGRATED_KEY, "1");
    setLegacyPresets(null);
  }

  function exportLayout() {
    const blob = new Blob([JSON.stringify(layout, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `gt7-layout-${draft.name ?? "draft"}.json`;
    a.click();
    // Revoking synchronously can cancel the download in some browsers.
    window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
  }

  // An imported file starts a new unsaved draft, like a preset.
  async function importLayout(file: File) {
    try {
      const parsed = JSON.parse(await file.text()) as Record<string, unknown>;
      // v1 exports (from the old URL builder) have a widgets array and no
      // version field; run them through the migration instead.
      const next =
        parsed.version === 2
          ? normalizeLayout(parsed)
          : Array.isArray(parsed.widgets)
            ? migrateOverlayConfig({ ...DEFAULT_CONFIG, ...parsed } as OverlayConfig)
            : null;
      if (!next) throw new Error("bad file");
      startFrom(next, draft.kind, file.name);
    } catch {
      toast("Import failed — not a valid layout file", "error");
    }
  }

  const path = draft.kind === "dash" ? "/dash" : "/overlay";
  const urlFor = (origin: string) =>
    draft.name != null ? `${origin}${path}?layout=${encodeURIComponent(draft.name)}` : null;
  const shareUrl = urlFor(lan ? `http://${lan.ip}:${lan.port}` : window.location.origin);
  const previewUrl = urlFor(window.location.origin);

  // Parked edits per layout key, plus the open one, for the rail's dots.
  const dirtyKeys = new Set(Object.keys(parked));
  if (dirty) dirtyKeys.add(keyOf(draft));

  return {
    draft,
    layout,
    saved,
    parked,
    dirty,
    dirtyKeys,
    kindChanged,
    selected,
    setSelected,
    dialog,
    setDialog,
    pendingStart,
    setPendingStart,
    legacyPresets,
    importFile,
    shareUrl,
    previewUrl,
    openSaved,
    openUnsaved,
    startFrom,
    commitStart,
    setLayout,
    patchLayout,
    setKind,
    save,
    saveAs,
    rename,
    removeLayout,
    discard,
    discardNew,
    addWidget,
    duplicateCell,
    updateCell,
    resizeCell,
    removeCell,
    migrateLegacyPresets,
    dismissLegacy,
    exportLayout,
    importLayout,
  };
}

export type LayoutBuilderState = ReturnType<typeof useLayoutBuilder>;

// ---------------------------------------------------------------- panels

function PanelHead({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <>
      <div className="flex items-center gap-2 px-3 py-2.5">
        <span className="section-header">{title}</span>
        {children}
      </div>
      <div className="rule" />
    </>
  );
}

function RailRow({
  active,
  dirty,
  name,
  meta,
  onClick,
}: {
  active: boolean;
  dirty: boolean;
  name: string;
  meta: string;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      aria-current={active || undefined}
      className={`flex flex-col gap-0.5 rounded px-2.5 py-2 text-left transition-colors ${
        active ? "bg-accent/16 text-accent-300" : "text-ink hover:bg-panel-2"
      }`}
    >
      <span className="flex min-w-0 items-center gap-1.5 text-[12.5px]">
        <span className="truncate">{name}</span>
        {dirty && (
          <span
            className="h-[5px] w-[5px] shrink-0 rounded-full bg-warn"
            title="Unsaved changes"
          />
        )}
      </span>
      <span className="font-tabular text-[10.5px] text-ink-faint">{meta}</span>
    </button>
  );
}

const railMeta = (d: { kind: LayoutKind; config: LayoutConfig }) =>
  `${d.kind === "dash" ? "dash" : "overlay"} · ${sizeLabel(d.config.size)} · ${d.config.cells.length} widgets`;

export function LayoutsPanel({ b }: { b: LayoutBuilderState }) {
  const { draft, saved, parked, dirtyKeys } = b;
  const unsaved = draft.id == null ? draft : parked[NEW_KEY];
  const live = (s: LayoutSummary) =>
    s.id === draft.id ? draft : (parked[String(s.id)] ?? fromSummary(s));
  return (
    <div className="panel">
      <PanelHead title="Layouts">
        <button
          className="btn ml-auto px-2 py-0.5"
          onClick={() => b.startFrom(BLANK_LAYOUT, draft.kind, "a blank canvas")}
        >
          New
        </button>
      </PanelHead>
      <div className="flex flex-col p-1.5">
        {unsaved && (
          <RailRow
            active={draft.id == null}
            dirty
            name="Unsaved draft"
            meta={railMeta({ kind: unsaved.kind, config: unsaved.layout })}
            onClick={b.openUnsaved}
          />
        )}
        {saved == null && <span className="px-2.5 py-2 text-[11px] text-ink-faint">Loading…</span>}
        {saved?.length === 0 && !unsaved && (
          <span className="px-2.5 py-2 text-[11px] text-ink-faint">No saved layouts yet.</span>
        )}
        {saved?.map((s) => {
          const d = live(s);
          return (
            <RailRow
              key={s.id}
              active={s.id === draft.id}
              dirty={dirtyKeys.has(String(s.id))}
              name={s.name}
              meta={railMeta({ kind: d.kind, config: d.layout })}
              onClick={() => b.openSaved(s)}
            />
          );
        })}
      </div>
      <div className="rule" />
      <div className="flex flex-wrap gap-1.5 px-3 py-2.5">
        {draft.id != null && (
          <>
            <button className="btn px-2 py-0.5" onClick={() => b.setDialog("rename")}>
              Rename…
            </button>
            <button className="btn px-2 py-0.5" onClick={() => b.setDialog("saveCopy")}>
              Save copy…
            </button>
          </>
        )}
        <button
          className="btn px-2 py-0.5"
          onClick={b.exportLayout}
          title="Download this layout as JSON"
        >
          Export JSON
        </button>
        {draft.id != null && (
          <button className="btn btn-danger px-2 py-0.5" onClick={() => b.setDialog("delete")}>
            Delete…
          </button>
        )}
      </div>
    </div>
  );
}

export function PresetsPanel({ b }: { b: LayoutBuilderState }) {
  return (
    <div className="panel">
      <PanelHead title="Start from a preset" />
      <div className="flex flex-wrap gap-1.5 px-3 py-2.5">
        {Object.entries(DASH_PRESETS).map(([key, p]) => (
          <button
            key={key}
            className="btn"
            onClick={() => b.startFrom(p.layout, "dash", `the ${p.label} dashboard`)}
          >
            {p.label}
          </button>
        ))}
        <button
          className="btn"
          onClick={() => b.startFrom(DEFAULT_LAYOUT, "overlay", "the minimal OBS strip")}
        >
          Minimal strip
        </button>
        <button
          className="btn"
          onClick={() => b.importFile.current?.click()}
          title="Load a layout JSON as a new draft (old overlay configs are converted)"
        >
          Import JSON…
        </button>
        <input
          ref={b.importFile}
          type="file"
          accept=".json"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void b.importLayout(f);
            e.target.value = "";
          }}
        />
      </div>
    </div>
  );
}

export function LegacyPresetsBanner({ b }: { b: LayoutBuilderState }) {
  if (!b.legacyPresets) return null;
  const n = Object.keys(b.legacyPresets).length;
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-panel border border-warn/40 bg-warn/10 px-3 py-2 text-xs">
      <span>
        You have {n} overlay preset{n === 1 ? "" : "s"} from the old builder stored in this
        browser.
      </span>
      <button className="btn" onClick={() => void b.migrateLegacyPresets()}>
        Import as server layouts
      </button>
      <button className="text-ink-dim underline hover:text-ink" onClick={b.dismissLegacy}>
        dismiss
      </button>
    </div>
  );
}

export function BuilderToolbar({ b }: { b: LayoutBuilderState }) {
  const { draft, layout, dirty } = b;
  const sizeKey = layout.size ? `${layout.size.width}x${layout.size.height}` : "fill";
  const target = draft.kind === "dash" ? "the dash" : "OBS";
  const status =
    draft.id == null
      ? { text: "Not saved yet — save it to get a URL", tone: "warn" }
      : dirty
        ? { text: `Unsaved — ${target} still shows the saved version`, tone: "warn" }
        : { text: `Saved · live in ${target}`, tone: "ok" };
  return (
    <div className="panel flex flex-wrap items-center gap-2.5 px-3 py-2">
      <span className="text-[15px] font-medium">{draft.name ?? "Unsaved draft"}</span>
      <SegmentedControl
        size="sm"
        ariaLabel="Layout kind"
        value={draft.kind}
        onValueChange={b.setKind}
        options={[
          { value: "overlay", label: "OBS overlay" },
          { value: "dash", label: "Driver dash" },
        ]}
      />
      <SegmentedControl
        size="sm"
        ariaLabel="Canvas size"
        value={CANVAS_SIZES.some((s) => s.key === sizeKey) ? sizeKey : "custom"}
        onValueChange={(key) => {
          const preset = CANVAS_SIZES.find((s) => s.key === key);
          if (preset) b.patchLayout({ size: preset.size ? { ...preset.size } : null });
        }}
        options={CANVAS_SIZES.map((s) => ({ value: s.key, label: s.label }))}
      />
      <span
        className={`ml-auto flex items-center gap-1.5 text-[11px] ${
          status.tone === "ok" ? "text-throttle" : "text-warn"
        }`}
      >
        <span className="h-[5px] w-[5px] rounded-full bg-current" />
        {status.text}
      </span>
      <button className="btn" onClick={b.discard} disabled={draft.id != null && !dirty}>
        Discard
      </button>
      <button
        className="btn btn-primary"
        onClick={() => void b.save()}
        disabled={draft.id != null && !dirty}
      >
        {draft.id == null ? "Save layout…" : "Save layout"}
      </button>
    </div>
  );
}

export function CanvasPanel({ b }: { b: LayoutBuilderState }) {
  const { layout } = b;
  // The frame loop pauses while the canvas is scrolled out of sight (#32).
  const rootRef = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  // The builder always previews with the demo lap when nothing is live; the
  // layout's own `demo` switch decides what OBS/the dash show.
  const { frame, laps, placeholder } = useLiveFrame(true, visible);

  return (
    <div ref={rootRef} className="panel px-[18px] pb-[18px] pt-[18px]">
      <GridCanvas
        layout={layout}
        frame={frame}
        laps={laps}
        selected={b.selected}
        onSelect={b.setSelected}
        onCellsChange={(cells) => b.patchLayout({ cells })}
        footer={(scale) => (
          <div className="mt-2.5 flex flex-wrap justify-center gap-x-4 gap-y-1 font-tabular text-[11px] text-ink-faint">
            <span>
              {layout.size
                ? `${layout.size.width}×${layout.size.height} px`
                : "Fills the screen (previewed at 1280×720)"}{" "}
              · grid {layout.grid.cols}×{layout.grid.rows} · shown at {Math.round(scale * 100)}%
            </span>
            <span>Drag to move · drag corner to resize · arrows nudge · Del removes</span>
            <span>{placeholder ? "Demo telemetry" : "Live telemetry"}</span>
          </div>
        )}
      />
    </div>
  );
}

export function UsePanel({ b }: { b: LayoutBuilderState }) {
  const { draft, layout, shareUrl, previewUrl } = b;
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const t = window.setTimeout(() => setCopied(false), 1500);
    return () => window.clearTimeout(t);
  }, [copied]);

  const hint =
    draft.kind === "dash"
      ? "Open on a phone or tablet on the same network. Add to home screen for full-screen."
      : layout.size
        ? `OBS → Browser source → this URL at ${layout.size.width}×${layout.size.height}. Transparent background; add &page=green for apps without alpha.`
        : "OBS → Browser source → this URL; the overlay fills whatever size the source has. Transparent background; add &page=green for apps without alpha.";

  return (
    <div className="panel flex flex-wrap items-center gap-2.5 px-3 py-2.5">
      <span className="section-header">Use it</span>
      <span
        className={`min-w-[260px] flex-1 truncate rounded-md border border-edge bg-panel-2 px-2.5 py-[5px] font-tabular text-xs ${
          shareUrl ? "text-accent-300" : "text-ink-faint"
        }`}
      >
        {shareUrl ?? "Save the layout to get its URL"}
      </span>
      <button
        className="btn"
        disabled={!shareUrl}
        onClick={() =>
          shareUrl &&
          navigator.clipboard
            .writeText(shareUrl)
            .then(() => setCopied(true))
            .catch(() => toast("Copy failed — select the URL manually", "error"))
        }
      >
        {copied ? "Copied" : "Copy URL"}
      </button>
      {previewUrl ? (
        <a className="btn" href={previewUrl} target="_blank" rel="noreferrer">
          Open preview
        </a>
      ) : (
        <button className="btn" disabled>
          Open preview
        </button>
      )}
      <span className="w-full text-[11px] text-ink-faint">{hint}</span>
    </div>
  );
}

function NumberInput({
  value,
  onChange,
  min,
  max,
  title,
  placeholder,
  width = "w-14",
}: {
  value: number | "";
  onChange: (n: number) => void;
  min: number;
  max: number;
  title?: string;
  placeholder?: string;
  width?: string;
}) {
  return (
    <input
      type="number"
      min={min}
      max={max}
      value={value}
      title={title}
      placeholder={placeholder}
      onChange={(e) => onChange(Number(e.target.value))}
      className={`${width} rounded-md border border-edge bg-panel-2 px-2 py-1 font-tabular text-xs text-ink`}
    />
  );
}

// The canvas options that don't fit the toolbar: exact size, grid, padding,
// page and card background, and the placeholder-data switch.
export function CanvasOptionsPanel({ b }: { b: LayoutBuilderState }) {
  const { layout } = b;
  const row = "rule-row flex flex-wrap items-center gap-2 px-3 py-2.5 text-xs text-ink-dim";
  return (
    <div className="panel">
      <PanelHead title="Canvas & page" />
      <div className={row}>
        <span className="w-28 shrink-0">Custom size</span>
        <NumberInput
          width="w-20"
          min={100}
          max={7680}
          value={layout.size?.width ?? ""}
          placeholder="W"
          onChange={(width) =>
            width && b.patchLayout({ size: { width, height: layout.size?.height ?? 1080 } })
          }
        />
        ×
        <NumberInput
          width="w-20"
          min={100}
          max={7680}
          value={layout.size?.height ?? ""}
          placeholder="H"
          onChange={(height) =>
            height && b.patchLayout({ size: { width: layout.size?.width ?? 1920, height } })
          }
        />
        px
      </div>
      <div className={row}>
        <span className="w-28 shrink-0">Grid</span>
        <NumberInput
          min={1}
          max={MAX_GRID_DIM}
          value={layout.grid.cols}
          title="Columns"
          onChange={(cols) =>
            b.setLayout(normalizeLayout({ ...layout, grid: { ...layout.grid, cols: cols || 1 } }))
          }
        />
        ×
        <NumberInput
          min={1}
          max={MAX_GRID_DIM}
          value={layout.grid.rows}
          title="Rows"
          onChange={(rows) =>
            b.setLayout(normalizeLayout({ ...layout, grid: { ...layout.grid, rows: rows || 1 } }))
          }
        />
        cells, gap
        <NumberInput
          min={0}
          max={64}
          value={layout.grid.gap}
          onChange={(gap) => b.patchLayout({ grid: { ...layout.grid, gap: Math.max(0, gap || 0) } })}
        />
        px
      </div>
      <div className={row}>
        <span className="w-28 shrink-0">Edge padding</span>
        <NumberInput
          width="w-16"
          min={0}
          max={200}
          value={layout.padX}
          title="Horizontal padding (px)"
          onChange={(padX) => b.patchLayout({ padX: Math.max(0, padX || 0) })}
        />
        ×
        <NumberInput
          width="w-16"
          min={0}
          max={200}
          value={layout.padY}
          title="Vertical padding (px)"
          onChange={(padY) => b.patchLayout({ padY: Math.max(0, padY || 0) })}
        />
        px
      </div>
      <div className={row}>
        <span className="w-28 shrink-0">Page behind</span>
        <SegmentedControl
          size="sm"
          ariaLabel="Page behind the widgets"
          value={layout.page}
          onValueChange={(page) => b.patchLayout({ page })}
          options={[
            { value: "transparent", label: "Transparent" },
            { value: "green", label: "Green screen" },
            { value: "dark", label: "Solid dark" },
          ]}
        />
      </div>
      <label className={row}>
        <span className="w-28 shrink-0">Card background</span>
        <input
          type="range"
          min={0}
          max={100}
          step={5}
          value={layout.bg}
          onChange={(e) => b.patchLayout({ bg: Number(e.target.value) })}
          className="min-w-24 flex-1 accent-accent"
        />
        <span className="w-9 text-right font-tabular text-ink">{layout.bg}%</span>
      </label>
      <div className="flex items-center gap-2 px-3 py-2.5 text-xs text-ink-dim">
        <Toggle
          checked={layout.demo}
          onCheckedChange={(demo) => b.patchLayout({ demo })}
          ariaLabel="Placeholder data when no telemetry"
        />
        <span>
          <span className="text-ink">Placeholder data when no telemetry</span> — the saved{" "}
          {b.draft.kind === "dash" ? "dash" : "overlay"} plays an animated fake lap (the fuel
          slowly drains so the alerts widget fires too)
        </span>
      </div>
    </div>
  );
}

function Chip({
  active,
  onClick,
  children,
  tabular,
  block,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
  tabular?: boolean;
  block?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={`rounded border text-left transition-colors ${
        block ? "px-2.5 py-1.5 text-xs" : "px-[9px] py-1 text-[11px]"
      } ${tabular ? "font-tabular" : ""} ${
        active
          ? "border-accent/55 bg-accent/10 text-accent-300"
          : "border-edge text-ink-muted hover:border-edge-bright hover:text-ink"
      }`}
    >
      {children}
    </button>
  );
}

export function SelectedWidgetPanel({ b }: { b: LayoutBuilderState }) {
  const cell = b.layout.cells.find((c) => c.id === b.selected) ?? null;
  return (
    <div className="panel">
      <PanelHead title="Selected widget" />
      {cell ? (
        <div className="flex flex-col gap-3 p-3">
          <div className="flex items-baseline gap-2">
            <span className="text-sm">{WIDGET_META[cell.widget].label}</span>
            <span className="font-tabular text-[11px] text-ink-faint">
              at {cell.x},{cell.y}
            </span>
          </div>
          <div>
            <span className="mb-[5px] block text-[11px] text-ink-dim">Style</span>
            <div className="flex flex-col gap-1">
              {WIDGET_META[cell.widget].variants.map((v) => (
                <Chip
                  key={v.key}
                  block
                  active={cell.variant === v.key}
                  onClick={() => b.updateCell(cell.id, { variant: v.key })}
                >
                  {v.label}
                </Chip>
              ))}
            </div>
          </div>
          <div>
            <span className="mb-[5px] block text-[11px] text-ink-dim">Size (cells)</span>
            <div className="flex flex-wrap gap-1">
              {WIDGET_META[cell.widget].sizes.map(([w, h]) => (
                <Chip
                  key={`${w}x${h}`}
                  tabular
                  active={cell.w === w && cell.h === h}
                  onClick={() => b.resizeCell(cell.id, w, h)}
                >
                  {w}×{h}
                </Chip>
              ))}
            </div>
          </div>
          <label className="flex items-center justify-between gap-2 text-[11px] text-ink-dim">
            Fine scale
            <Select
              ariaLabel="Widget scale"
              value={String(
                typeof cell.options?.scale === "number" ? cell.options.scale : 1,
              )}
              onValueChange={(v) =>
                b.updateCell(cell.id, { options: { ...cell.options, scale: Number(v) } })
              }
              options={SCALE_STEPS.map((s) => ({ value: String(s), label: `${s * 100}%` }))}
              className="px-2 py-1 text-xs"
            />
          </label>
          <div className="flex gap-1.5">
            <button className="btn" onClick={() => b.duplicateCell(cell.id)}>
              Duplicate
            </button>
            <button className="btn btn-danger" onClick={() => b.removeCell(cell.id)}>
              Remove
            </button>
          </div>
        </div>
      ) : (
        <p className="px-3 py-3.5 text-[11.5px] text-ink-faint">
          Click a widget on the canvas to change its style or size.
        </p>
      )}
    </div>
  );
}

export function AddWidgetPanel({ b }: { b: LayoutBuilderState }) {
  const used = new Set(b.layout.cells.map((c) => c.widget));
  const groups = Object.keys(WIDGET_GROUP_LABELS) as WidgetGroup[];
  return (
    <div className="panel">
      <div className="flex items-baseline gap-2 px-3 py-2.5">
        <span className="section-header">Add widget</span>
        <span className="text-[10.5px] text-ink-faint">placed in the first free spot</span>
      </div>
      <div className="rule" />
      <div className="flex flex-col gap-2.5 px-3 py-2.5">
        {groups.map((group) => {
          const ids = (Object.keys(WIDGET_META) as (keyof typeof WIDGET_META)[]).filter(
            (id) => WIDGET_META[id].group === group,
          );
          if (ids.length === 0) return null;
          return (
            <div key={group}>
              <span className="mb-[5px] block text-[10.5px] text-ink-faint">
                {WIDGET_GROUP_LABELS[group]}
              </span>
              <div className="flex flex-wrap gap-1">
                {ids.map((id) => (
                  <button
                    key={id}
                    className="btn px-[9px] py-[3px]"
                    disabled={used.has(id)}
                    onClick={() => b.addWidget(id)}
                    title={
                      used.has(id)
                        ? "Already on the canvas — select it and Duplicate to add another"
                        : `Add ${WIDGET_META[id].label}`
                    }
                  >
                    + {WIDGET_META[id].label}
                  </button>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function BuilderDialogs({ b }: { b: LayoutBuilderState }) {
  const { dialog, draft } = b;
  const close = () => b.setDialog(null);
  return (
    <>
      <PromptDialog
        open={dialog === "saveAs" || dialog === "saveCopy"}
        title={dialog === "saveCopy" ? "Save a copy" : "Save layout"}
        label={
          b.kindChanged && dialog === "saveAs"
            ? `A saved layout can't change kind — save this as a new ${
                draft.kind === "dash" ? "driver dash" : "OBS overlay"
              }. The name becomes part of its URL.`
            : "Name this layout — the name becomes part of its URL."
        }
        placeholder="e.g. race-strip, endurance-dash"
        onSubmit={(name) => void b.saveAs(name)}
        onCancel={close}
      />
      <PromptDialog
        open={dialog === "rename"}
        title="Rename layout"
        label="Existing OBS sources using the old name will need the new URL."
        initialValue={draft.name ?? ""}
        onSubmit={(name) => void b.rename(name)}
        onCancel={close}
      />
      <ConfirmDialog
        open={dialog === "delete"}
        title={`Delete layout "${draft.name}"?`}
        body="OBS sources and dashboards using its URL will stop rendering."
        confirmLabel="Delete"
        danger
        onConfirm={() => void b.removeLayout()}
        onCancel={close}
      />
      <ConfirmDialog
        open={dialog === "discardNew"}
        title="Discard this draft?"
        body="It has never been saved, so there is nothing to go back to."
        confirmLabel="Discard"
        danger
        onConfirm={b.discardNew}
        onCancel={close}
      />
      <ConfirmDialog
        open={b.pendingStart != null}
        title="Replace the unsaved draft?"
        body={`Starting from ${b.pendingStart?.label ?? ""} discards the draft you haven't saved.`}
        confirmLabel="Replace"
        danger
        onConfirm={() => b.pendingStart && b.commitStart(b.pendingStart.draft, b.pendingStart.label)}
        onCancel={() => b.setPendingStart(null)}
      />
    </>
  );
}
