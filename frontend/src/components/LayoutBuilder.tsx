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
import { rich, t as translate, useT, withLanguage, type MessageKey, type Translate } from "@/i18n";
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
  WIDGET_GROUP_LABEL_KEYS,
  WIDGET_META,
  type WidgetGroup,
} from "@/lib/widgetMeta";
import { toast } from "@/store/toasts";

const DRAFT_KEY = "gt7-layout-draft";
const LEGACY_PRESETS_KEY = "gt7-overlay-presets";
const LEGACY_MIGRATED_KEY = "gt7-overlay-migrated";

const SCALE_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 2];

// Canvas sizes offered in the toolbar; anything else is set with the custom
// W × H inputs under "Canvas & page". A null labelKey shows the bare size.
const CANVAS_SIZES: { key: string; labelKey: MessageKey | null; size: OverlaySize | null }[] = [
  { key: "fill", labelKey: "layout.size.fill", size: null },
  { key: "1920x1080", labelKey: null, size: { width: 1920, height: 1080 } },
  { key: "1920x260", labelKey: "layout.size.strip", size: { width: 1920, height: 260 } },
  { key: "1080x1920", labelKey: null, size: { width: 1080, height: 1920 } },
  { key: "720x1280", labelKey: null, size: { width: 720, height: 1280 } },
  { key: "1280x800", labelKey: "layout.size.tablet", size: { width: 1280, height: 800 } },
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
  return size ? `${size.width}×${size.height}` : translate("layout.meta.fill");
}

function conflictMessage(e: unknown, name: string): string {
  return String(e).includes("409") ? translate("layout.toast.nameTaken", { name }) : String(e);
}

type DialogKind = "saveAs" | "saveCopy" | "rename" | "delete" | "discardNew";

// `requested` is the ?layout=<id or name> route param: once the server list
// is in, that layout is opened (the Settings page's Edit links land here).
export function useLayoutBuilder(requested: string | null) {
  const t = useT();
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
    else toast(t("layout.toast.notFound", { name: requested }), "error");
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
    toast(t("layout.toast.started", { source: label }), "success");
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
    if (draft.id == null) {
      setDialog("saveAs");
      return;
    }
    try {
      // Kind rides along with the config. It only picks which list (overlay
      // or dash) the layout shows up in — /overlay?layout= and /dash?layout=
      // render any layout — so flipping it breaks no URL already in use.
      const updated = await api.layouts.update(draft.id, { kind: draft.kind, config: layout });
      mergeSaved(updated);
      await refreshList();
      toast(t("layout.toast.saved", { name: draft.name ?? "" }), "success");
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
      toast(t("layout.toast.saved", { name }), "success");
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
      toast(t("layout.toast.renamed", { name }), "success");
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
      toast(t("layout.toast.deleted"), "success");
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
    toast(t("layout.toast.noRoom"), "error");
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
    toast(t("layout.toast.noRoomCopy"), "error");
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
      toast(t("layout.toast.sizeNoFit"), "error");
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
    const target = e.target as HTMLElement | null;
    if (target?.closest("input, textarea, select, [contenteditable], [role=dialog]")) return;
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
    toast(t("layout.toast.migrated", { count: migrated }), "success");
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
      toast(t("settings.overlays.invalid"), "error");
    }
  }

  const path = draft.kind === "dash" ? "/dash" : "/overlay";
  const urlFor = (origin: string) =>
    draft.name != null ? withLanguage(`${origin}${path}?layout=${encodeURIComponent(draft.name)}`) : null;
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
  const t = useT();
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
            title={t("layout.rail.unsavedChanges")}
          />
        )}
      </span>
      <span className="font-tabular text-[10.5px] text-ink-faint">{meta}</span>
    </button>
  );
}

function railMeta(t: Translate, d: { kind: LayoutKind; config: LayoutConfig }): string {
  const kind = d.kind === "dash" ? t("layout.kind.dash") : t("layout.kind.overlay");
  const widgets = t("layout.count.widgets", { count: d.config.cells.length });
  return `${kind} · ${sizeLabel(d.config.size)} · ${widgets}`;
}

export function LayoutsPanel({ b }: { b: LayoutBuilderState }) {
  const t = useT();
  const { draft, saved, parked, dirtyKeys } = b;
  const unsaved = draft.id == null ? draft : parked[NEW_KEY];
  const live = (s: LayoutSummary) =>
    s.id === draft.id ? draft : (parked[String(s.id)] ?? fromSummary(s));
  return (
    <div className="panel">
      <PanelHead title={t("layout.panel.layouts")}>
        <button
          className="btn ml-auto px-2 py-0.5"
          onClick={() => b.startFrom(BLANK_LAYOUT, draft.kind, t("layout.start.blank"))}
        >
          {t("layout.new")}
        </button>
      </PanelHead>
      <div className="flex flex-col p-1.5">
        {unsaved && (
          <RailRow
            active={draft.id == null}
            dirty
            name={t("layout.unsavedDraft")}
            meta={railMeta(t, { kind: unsaved.kind, config: unsaved.layout })}
            onClick={b.openUnsaved}
          />
        )}
        {saved == null && (
          <span className="px-2.5 py-2 text-[11px] text-ink-faint">{t("common.loading")}</span>
        )}
        {saved?.length === 0 && !unsaved && (
          <span className="px-2.5 py-2 text-[11px] text-ink-faint">{t("layout.rail.none")}</span>
        )}
        {saved?.map((s) => {
          const d = live(s);
          return (
            <RailRow
              key={s.id}
              active={s.id === draft.id}
              dirty={dirtyKeys.has(String(s.id))}
              name={s.name}
              meta={railMeta(t, { kind: d.kind, config: d.layout })}
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
              {t("layout.action.rename")}
            </button>
            <button className="btn px-2 py-0.5" onClick={() => b.setDialog("saveCopy")}>
              {t("layout.action.saveCopy")}
            </button>
          </>
        )}
        <button
          className="btn px-2 py-0.5"
          onClick={b.exportLayout}
          title={t("layout.action.exportTitle")}
        >
          {t("layout.action.export")}
        </button>
        {draft.id != null && (
          <button className="btn btn-danger px-2 py-0.5" onClick={() => b.setDialog("delete")}>
            {t("layout.action.delete")}
          </button>
        )}
      </div>
    </div>
  );
}

export function PresetsPanel({ b }: { b: LayoutBuilderState }) {
  const t = useT();
  return (
    <div className="panel">
      <PanelHead title={t("layout.panel.presets")} />
      <div className="flex flex-wrap gap-1.5 px-3 py-2.5">
        {Object.entries(DASH_PRESETS).map(([key, p]) => (
          <button
            key={key}
            className="btn"
            onClick={() =>
              b.startFrom(p.layout, "dash", t("layout.start.preset", { name: t(p.labelKey) }))
            }
          >
            {t(p.labelKey)}
          </button>
        ))}
        <button
          className="btn"
          onClick={() => b.startFrom(DEFAULT_LAYOUT, "overlay", t("layout.start.minimal"))}
        >
          {t("layout.preset.minimal")}
        </button>
        <button
          className="btn"
          onClick={() => b.importFile.current?.click()}
          title={t("layout.action.importTitle")}
        >
          {t("settings.overlays.import")}
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
  const t = useT();
  if (!b.legacyPresets) return null;
  const n = Object.keys(b.legacyPresets).length;
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-panel border border-warn/40 bg-warn/10 px-3 py-2 text-xs">
      <span>{t("layout.legacy.body", { count: n })}</span>
      <button className="btn" onClick={() => void b.migrateLegacyPresets()}>
        {t("layout.legacy.import")}
      </button>
      <button className="text-ink-dim underline hover:text-ink" onClick={b.dismissLegacy}>
        {t("layout.legacy.dismiss")}
      </button>
    </div>
  );
}

export function BuilderToolbar({ b }: { b: LayoutBuilderState }) {
  const t = useT();
  const { draft, layout, dirty } = b;
  const sizeKey = layout.size ? `${layout.size.width}x${layout.size.height}` : "fill";
  const dash = draft.kind === "dash";
  const status =
    draft.id == null
      ? { text: t("layout.status.new"), tone: "warn" }
      : dirty
        ? { text: dash ? t("layout.status.unsavedDash") : t("layout.status.unsavedObs"), tone: "warn" }
        : { text: dash ? t("layout.status.savedDash") : t("layout.status.savedObs"), tone: "ok" };
  return (
    <div className="panel flex flex-wrap items-center gap-2.5 px-3 py-2">
      <span className="text-[15px] font-medium">{draft.name ?? t("layout.unsavedDraft")}</span>
      <SegmentedControl
        size="sm"
        ariaLabel={t("layout.kind.label")}
        value={draft.kind}
        onValueChange={b.setKind}
        options={[
          { value: "overlay", label: t("layout.kind.obsOverlay") },
          { value: "dash", label: t("layout.kind.driverDash") },
        ]}
      />
      <SegmentedControl
        size="sm"
        ariaLabel={t("layout.canvasSize")}
        value={CANVAS_SIZES.some((s) => s.key === sizeKey) ? sizeKey : "custom"}
        onValueChange={(key) => {
          const preset = CANVAS_SIZES.find((s) => s.key === key);
          if (preset) b.patchLayout({ size: preset.size ? { ...preset.size } : null });
        }}
        options={CANVAS_SIZES.map((s) => {
          const dims = s.size ? `${s.size.width}×${s.size.height}` : "";
          return { value: s.key, label: s.labelKey ? t(s.labelKey, { size: dims }) : dims };
        })}
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
        {t("layout.discard")}
      </button>
      <button
        className="btn btn-primary"
        onClick={() => void b.save()}
        disabled={draft.id != null && !dirty}
      >
        {draft.id == null ? t("layout.saveAs") : t("layout.save")}
      </button>
    </div>
  );
}

export function CanvasPanel({ b }: { b: LayoutBuilderState }) {
  const t = useT();
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
                : t("layout.footer.fills")}{" "}
              ·{" "}
              {t("layout.footer.grid", {
                cols: layout.grid.cols,
                rows: layout.grid.rows,
                pct: Math.round(scale * 100),
              })}
            </span>
            <span>{t("layout.footer.help")}</span>
            <span>{placeholder ? t("layout.footer.demo") : t("layout.footer.live")}</span>
          </div>
        )}
      />
    </div>
  );
}

export function UsePanel({ b }: { b: LayoutBuilderState }) {
  const t = useT();
  const { draft, layout, shareUrl, previewUrl } = b;
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1500);
    return () => window.clearTimeout(timer);
  }, [copied]);

  const hint =
    draft.kind === "dash"
      ? t("layout.use.dashHint")
      : layout.size
        ? t("layout.use.obsSizedHint", { size: `${layout.size.width}×${layout.size.height}` })
        : t("layout.use.obsFillHint");

  return (
    <div className="panel flex flex-wrap items-center gap-2.5 px-3 py-2.5">
      <span className="section-header">{t("layout.use.title")}</span>
      <span
        className={`min-w-[260px] flex-1 truncate rounded-md border border-edge bg-panel-2 px-2.5 py-[5px] font-tabular text-xs ${
          shareUrl ? "text-accent-300" : "text-ink-faint"
        }`}
      >
        {shareUrl ?? t("layout.use.noUrl")}
      </span>
      <button
        className="btn"
        disabled={!shareUrl}
        onClick={() =>
          shareUrl &&
          navigator.clipboard
            .writeText(shareUrl)
            .then(() => setCopied(true))
            .catch(() => toast(t("settings.overlays.copyFailed"), "error"))
        }
      >
        {copied ? t("common.copied") : t("settings.overlays.copy")}
      </button>
      {previewUrl ? (
        <a className="btn" href={previewUrl} target="_blank" rel="noreferrer">
          {t("layout.use.preview")}
        </a>
      ) : (
        <button className="btn" disabled>
          {t("layout.use.preview")}
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
  const t = useT();
  const { layout } = b;
  const row = "rule-row flex flex-wrap items-center gap-2 px-3 py-2.5 text-xs text-ink-dim";
  return (
    <div className="panel">
      <PanelHead title={t("layout.options.title")} />
      <div className={row}>
        <span className="w-28 shrink-0">{t("layout.options.customSize")}</span>
        <NumberInput
          width="w-20"
          min={100}
          max={7680}
          value={layout.size?.width ?? ""}
          placeholder={t("layout.options.widthShort")}
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
          placeholder={t("layout.options.heightShort")}
          onChange={(height) =>
            height && b.patchLayout({ size: { width: layout.size?.width ?? 1920, height } })
          }
        />
        px
      </div>
      <div className={row}>
        <span className="w-28 shrink-0">{t("layout.options.grid")}</span>
        <NumberInput
          min={1}
          max={MAX_GRID_DIM}
          value={layout.grid.cols}
          title={t("layout.options.columns")}
          onChange={(cols) =>
            b.setLayout(normalizeLayout({ ...layout, grid: { ...layout.grid, cols: cols || 1 } }))
          }
        />
        ×
        <NumberInput
          min={1}
          max={MAX_GRID_DIM}
          value={layout.grid.rows}
          title={t("layout.options.rows")}
          onChange={(rows) =>
            b.setLayout(normalizeLayout({ ...layout, grid: { ...layout.grid, rows: rows || 1 } }))
          }
        />
        {t("layout.options.cellsGap")}
        <NumberInput
          min={0}
          max={64}
          value={layout.grid.gap}
          onChange={(gap) => b.patchLayout({ grid: { ...layout.grid, gap: Math.max(0, gap || 0) } })}
        />
        px
      </div>
      <div className={row}>
        <span className="w-28 shrink-0">{t("layout.options.padding")}</span>
        <NumberInput
          width="w-16"
          min={0}
          max={200}
          value={layout.padX}
          title={t("layout.options.padX")}
          onChange={(padX) => b.patchLayout({ padX: Math.max(0, padX || 0) })}
        />
        ×
        <NumberInput
          width="w-16"
          min={0}
          max={200}
          value={layout.padY}
          title={t("layout.options.padY")}
          onChange={(padY) => b.patchLayout({ padY: Math.max(0, padY || 0) })}
        />
        px
      </div>
      <div className={row}>
        <span className="w-28 shrink-0">{t("layout.options.page")}</span>
        <SegmentedControl
          size="sm"
          ariaLabel={t("layout.options.pageAria")}
          value={layout.page}
          onValueChange={(page) => b.patchLayout({ page })}
          options={[
            { value: "transparent", label: t("layout.options.pageTransparent") },
            { value: "green", label: t("layout.options.pageGreen") },
            { value: "dark", label: t("layout.options.pageDark") },
          ]}
        />
      </div>
      <label className={row}>
        <span className="w-28 shrink-0">{t("layout.options.cardBg")}</span>
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
          ariaLabel={t("layout.options.demo")}
        />
        <span>
          {rich(
            b.draft.kind === "dash" ? t("layout.options.demoDash") : t("layout.options.demoOverlay"),
            { label: <span className="text-ink">{t("layout.options.demo")}</span> },
          )}
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
  const t = useT();
  const cell = b.layout.cells.find((c) => c.id === b.selected) ?? null;
  return (
    <div className="panel">
      <PanelHead title={t("layout.selected.title")} />
      {cell ? (
        <div className="flex flex-col gap-3 p-3">
          <div className="flex items-baseline gap-2">
            <span className="text-sm">{t(WIDGET_META[cell.widget].labelKey)}</span>
            <span className="font-tabular text-[11px] text-ink-faint">
              {t("layout.selected.at", { x: String(cell.x), y: String(cell.y) })}
            </span>
          </div>
          <div>
            <span className="mb-[5px] block text-[11px] text-ink-dim">{t("layout.selected.style")}</span>
            <div className="flex flex-col gap-1">
              {WIDGET_META[cell.widget].variants.map((v) => (
                <Chip
                  key={v.key}
                  block
                  active={cell.variant === v.key}
                  onClick={() => b.updateCell(cell.id, { variant: v.key })}
                >
                  {t(v.labelKey)}
                </Chip>
              ))}
            </div>
          </div>
          <div>
            <span className="mb-[5px] block text-[11px] text-ink-dim">{t("layout.selected.size")}</span>
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
            {t("layout.selected.scale")}
            <Select
              ariaLabel={t("layout.selected.scaleAria")}
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
              {t("layout.selected.duplicate")}
            </button>
            <button className="btn btn-danger" onClick={() => b.removeCell(cell.id)}>
              {t("common.remove")}
            </button>
          </div>
        </div>
      ) : (
        <p className="px-3 py-3.5 text-[11.5px] text-ink-faint">
          {t("layout.selected.none")}
        </p>
      )}
    </div>
  );
}

export function AddWidgetPanel({ b }: { b: LayoutBuilderState }) {
  const t = useT();
  const used = new Set(b.layout.cells.map((c) => c.widget));
  const groups = Object.keys(WIDGET_GROUP_LABEL_KEYS) as WidgetGroup[];
  return (
    <div className="panel">
      <div className="flex items-baseline gap-2 px-3 py-2.5">
        <span className="section-header">{t("layout.add.title")}</span>
        <span className="text-[10.5px] text-ink-faint">{t("layout.add.hint")}</span>
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
                {t(WIDGET_GROUP_LABEL_KEYS[group])}
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
                        ? t("layout.add.used")
                        : t("layout.add.one", { name: t(WIDGET_META[id].labelKey) })
                    }
                  >
                    + {t(WIDGET_META[id].labelKey)}
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
  const t = useT();
  const { dialog, draft } = b;
  const close = () => b.setDialog(null);
  return (
    <>
      <PromptDialog
        open={dialog === "saveAs" || dialog === "saveCopy"}
        title={dialog === "saveCopy" ? t("layout.dialog.saveCopy") : t("layout.save")}
        label={t("layout.dialog.nameLabel")}
        placeholder={t("layout.dialog.namePlaceholder")}
        onSubmit={(name) => void b.saveAs(name)}
        onCancel={close}
      />
      <PromptDialog
        open={dialog === "rename"}
        title={t("layout.dialog.rename")}
        label={t("layout.dialog.renameLabel")}
        initialValue={draft.name ?? ""}
        onSubmit={(name) => void b.rename(name)}
        onCancel={close}
      />
      <ConfirmDialog
        open={dialog === "delete"}
        title={t("layout.dialog.delete", { name: draft.name ?? "" })}
        body={t("layout.dialog.deleteBody")}
        confirmLabel={t("common.delete")}
        danger
        onConfirm={() => void b.removeLayout()}
        onCancel={close}
      />
      <ConfirmDialog
        open={dialog === "discardNew"}
        title={t("layout.dialog.discard")}
        body={t("layout.dialog.discardBody")}
        confirmLabel={t("layout.discard")}
        danger
        onConfirm={b.discardNew}
        onCancel={close}
      />
      <ConfirmDialog
        open={b.pendingStart != null}
        title={t("layout.dialog.replace")}
        body={t("layout.dialog.replaceBody", { source: b.pendingStart?.label ?? "" })}
        confirmLabel={t("layout.dialog.replaceConfirm")}
        danger
        onConfirm={() => b.pendingStart && b.commitStart(b.pendingStart.draft, b.pendingStart.label)}
        onCancel={() => b.setPendingStart(null)}
      />
    </>
  );
}
