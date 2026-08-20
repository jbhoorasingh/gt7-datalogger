// Visual builder for grid overlays and driver dashboards: drag widgets on a
// snapping canvas, pick a variant per widget, and save named layouts to the
// server so OBS/phones get short stable URLs (/overlay?layout=<name>).

import { useEffect, useRef, useState } from "react";
import { GridCanvas } from "@/components/GridCanvas";
import { ConfirmDialog, PromptDialog } from "@/components/ui/Dialog";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { Select } from "@/components/ui/Select";
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
import { DEFAULT_CONFIG, SIZE_PRESETS, type OverlayConfig } from "@/lib/overlay";
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

type LayoutKind = "overlay" | "dash";

interface Draft {
  layout: LayoutConfig;
  id: number | null;
  name: string | null;
  kind: LayoutKind;
}

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

function newCellId(cells: LayoutCell[]): string {
  let n = cells.length + 1;
  while (cells.some((c) => c.id === `c${n}`)) n += 1;
  return `c${n}`;
}

// First free position (row-major) where the footprint fits; null if the grid
// is full.
function findFreeSpot(
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

export function LayoutBuilder({ flash }: { flash: (text: string) => void }) {
  const [draft, setDraft] = useState<Draft>(loadDraft);
  const [dirty, setDirty] = useState(false);
  const [saved, setSaved] = useState<LayoutSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [savingAs, setSavingAs] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [legacyPresets, setLegacyPresets] = useState(loadLegacyPresets);
  const importFile = useRef<HTMLInputElement>(null);

  const { layout } = draft;

  // The builder mounts inside Admin, usually below the fold — its frame loop
  // must not run while it is scrolled out of sight (#32).
  const rootRef = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const observer = new IntersectionObserver(([entry]) =>
      setVisible(entry.isIntersecting),
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const { frame, laps } = useLiveFrame(layout.demo, visible);

  useEffect(() => {
    localStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
  }, [draft]);

  const refreshList = () =>
    api.layouts
      .list()
      .then(setSaved)
      .catch(() => {});
  useEffect(() => {
    void refreshList();
  }, []);

  // LAN URL for other devices (OBS on another PC, phone on the wall).
  const [lan, setLan] = useState<{ ip: string; port: number } | null>(null);
  useEffect(() => {
    api.admin
      .stats()
      .then((s) => s.lan_ip && setLan({ ip: s.lan_ip, port: s.http_port }))
      .catch(() => {});
  }, []);

  function setLayout(next: LayoutConfig) {
    setDraft((d) => ({ ...d, layout: next }));
    setDirty(true);
  }

  function patchLayout(patch: Partial<LayoutConfig>) {
    setLayout({ ...layout, ...patch });
  }

  function loadSavedLayout(s: LayoutSummary) {
    setDraft({
      layout: normalizeLayout(s.config),
      id: s.id,
      name: s.name,
      kind: s.kind,
    });
    setDirty(false);
    setSelected(null);
    flash(`Layout "${s.name}" loaded`);
  }

  function startFrom(next: LayoutConfig, kind: LayoutKind, label: string) {
    setDraft({ layout: normalizeLayout(next), id: null, name: null, kind });
    setDirty(true);
    setSelected(null);
    flash(`Started from ${label}`);
  }

  async function save() {
    if (draft.id == null) {
      setSavingAs(true);
      return;
    }
    try {
      await api.layouts.update(draft.id, { config: layout });
      setDirty(false);
      await refreshList();
      flash(`Layout "${draft.name}" saved`);
    } catch (e) {
      toast(String(e), "error");
    }
  }

  async function saveAs(name: string) {
    setSavingAs(false);
    try {
      const created = await api.layouts.create(name, draft.kind, layout);
      setDraft((d) => ({ ...d, id: created.id, name: created.name }));
      setDirty(false);
      await refreshList();
      flash(`Layout "${name}" saved`);
    } catch (e) {
      toast(
        String(e).includes("409")
          ? `A layout named "${name}" already exists`
          : String(e),
        "error",
      );
    }
  }

  async function rename(name: string) {
    setRenaming(false);
    if (draft.id == null) return;
    try {
      await api.layouts.update(draft.id, { name });
      setDraft((d) => ({ ...d, name }));
      await refreshList();
      flash(`Renamed to "${name}"`);
    } catch (e) {
      toast(
        String(e).includes("409")
          ? `A layout named "${name}" already exists`
          : String(e),
        "error",
      );
    }
  }

  async function removeLayout() {
    setDeleting(false);
    if (draft.id == null) return;
    try {
      await api.layouts.remove(draft.id);
      setDraft((d) => ({ ...d, id: null, name: null }));
      await refreshList();
      flash("Layout deleted — the draft stays here until you save again");
    } catch (e) {
      toast(String(e), "error");
    }
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

  function updateCell(id: string, patch: Partial<LayoutCell>) {
    patchLayout({
      cells: layout.cells.map((c) => (c.id === id ? { ...c, ...patch } : c)),
    });
  }

  function removeCell(id: string) {
    patchLayout({ cells: layout.cells.filter((c) => c.id !== id) });
    setSelected(null);
  }

  async function migrateLegacyPresets() {
    if (!legacyPresets) return;
    const existing = new Set(saved.map((s) => s.name));
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
    flash(`Migrated ${migrated} legacy preset${migrated === 1 ? "" : "s"} to server layouts`);
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
      setLayout(next);
      setSelected(null);
      flash(`Imported layout from ${file.name}`);
    } catch {
      toast("Import failed — not a valid layout file", "error");
    }
  }

  const path = draft.kind === "dash" ? "/dash" : "/overlay";
  const urlFor = (origin: string) =>
    draft.name != null
      ? `${origin}${path}?layout=${encodeURIComponent(draft.name)}`
      : null;
  const url = urlFor(window.location.origin);
  const lanUrl = (() => {
    if (!lan) return null;
    const origin = `http://${lan.ip}:${lan.port}`;
    return origin === window.location.origin ? null : urlFor(origin);
  })();

  const selectedCell = layout.cells.find((c) => c.id === selected) ?? null;
  const groups = Object.keys(WIDGET_GROUP_LABELS) as WidgetGroup[];

  const activeSizeLabel = layout.size
    ? SIZE_PRESETS.find(
        (p) => p.size.width === layout.size!.width && p.size.height === layout.size!.height,
      )?.label ?? "custom"
    : "fill";

  return (
    <div ref={rootRef} className="space-y-3 p-4">
      {legacyPresets && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 text-xs">
          <span>
            You have {Object.keys(legacyPresets).length} overlay preset
            {Object.keys(legacyPresets).length === 1 ? "" : "s"} from the old builder stored in
            this browser.
          </span>
          <button className="btn" onClick={() => void migrateLegacyPresets()}>
            Import as server layouts
          </button>
          <button
            className="text-ink-dim underline hover:text-ink"
            onClick={() => {
              localStorage.setItem(LEGACY_MIGRATED_KEY, "1");
              setLegacyPresets(null);
            }}
          >
            dismiss
          </button>
        </div>
      )}

      {/* Starting points + saved layouts */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-ink-dim">Start from:</span>
        <button className="btn" onClick={() => startFrom(DEFAULT_LAYOUT, "overlay", "the OBS strip")}>
          OBS strip
        </button>
        {Object.entries(DASH_PRESETS).map(([key, p]) => (
          <button
            key={key}
            className="btn"
            onClick={() => startFrom(p.layout, "dash", `the ${p.label} dashboard`)}
          >
            {p.label} dash
          </button>
        ))}
        <span className="mx-2 h-4 w-px bg-edge" />
        <span className="text-xs text-ink-dim">Saved layouts:</span>
        {saved.length === 0 && <span className="text-xs text-ink-dim/60">none yet</span>}
        {saved.map((s) => (
          <button
            key={s.id}
            className={`rounded-md border px-2 py-1 text-xs ${
              s.id === draft.id
                ? "border-accent bg-accent/15 text-accent"
                : "border-edge text-ink-dim hover:text-ink"
            }`}
            title={`Load "${s.name}" (${s.kind})`}
            onClick={() => loadSavedLayout(s)}
          >
            {s.name}
          </button>
        ))}
      </div>

      {/* Save / file actions */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-tabular text-xs">
          {draft.name != null ? (
            <>
              Editing <span className="text-accent">{draft.name}</span>
              {dirty && <span className="text-warn"> — unsaved changes</span>}
            </>
          ) : (
            <span className="text-ink-dim">Unsaved draft</span>
          )}
        </span>
        <button className="btn" onClick={() => void save()}>
          {draft.id == null ? "Save as…" : "Save"}
        </button>
        {draft.id != null && (
          <>
            <button className="btn" onClick={() => setSavingAs(true)}>
              Save copy…
            </button>
            <button className="btn" onClick={() => setRenaming(true)}>
              Rename…
            </button>
            <button className="btn-danger" onClick={() => setDeleting(true)}>
              Delete
            </button>
          </>
        )}
        <span className="mx-1 h-4 w-px bg-edge" />
        <SegmentedControl
          ariaLabel="Layout kind"
          value={draft.kind}
          onValueChange={(kind) => {
            setDraft((d) => ({ ...d, kind }));
            setDirty(true);
          }}
          options={[
            { value: "overlay", label: "OBS overlay" },
            { value: "dash", label: "Driver dash" },
          ]}
        />
        <button className="btn" onClick={exportLayout} title="Download this layout as JSON">
          Export
        </button>
        <button
          className="btn"
          onClick={() => importFile.current?.click()}
          title="Load a layout JSON (old overlay configs are converted)"
        >
          Import…
        </button>
        <input
          ref={importFile}
          type="file"
          accept=".json"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void importLayout(f);
            e.target.value = "";
          }}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[280px_1fr]">
        {/* Left rail: canvas options, palette, selected widget */}
        <div className="space-y-3">
          <div>
            <span className="mb-1 block text-xs text-ink-dim">Canvas size</span>
            <div className="flex flex-wrap gap-1.5">
              <button
                onClick={() => patchLayout({ size: null })}
                className={`rounded-md border px-2 py-1 text-xs ${
                  layout.size == null
                    ? "border-accent bg-accent/15 text-accent"
                    : "border-edge text-ink-dim hover:text-ink"
                }`}
                title="Fill whatever size the browser source / screen has"
              >
                Fill screen
              </button>
              {SIZE_PRESETS.map((p) => {
                const active =
                  layout.size?.width === p.size.width && layout.size?.height === p.size.height;
                return (
                  <button
                    key={p.label}
                    onClick={() => patchLayout({ size: { ...p.size } })}
                    className={`rounded-md border px-2 py-1 font-tabular text-xs ${
                      active
                        ? "border-accent bg-accent/15 text-accent"
                        : "border-edge text-ink-dim hover:text-ink"
                    }`}
                  >
                    {p.size.width} × {p.size.height} ({p.label})
                  </button>
                );
              })}
            </div>
            <div className="mt-1.5 flex items-center gap-2 text-xs text-ink-dim">
              Custom
              <input
                type="number"
                min={100}
                max={7680}
                value={layout.size?.width ?? ""}
                placeholder="W"
                onChange={(e) => {
                  const width = Number(e.target.value);
                  if (!width) return;
                  patchLayout({ size: { width, height: layout.size?.height ?? 1080 } });
                }}
                className="w-20 rounded-md border border-edge bg-panel-2 px-2 py-1 font-tabular text-xs text-ink"
              />
              ×
              <input
                type="number"
                min={100}
                max={7680}
                value={layout.size?.height ?? ""}
                placeholder="H"
                onChange={(e) => {
                  const height = Number(e.target.value);
                  if (!height) return;
                  patchLayout({ size: { width: layout.size?.width ?? 1920, height } });
                }}
                className="w-20 rounded-md border border-edge bg-panel-2 px-2 py-1 font-tabular text-xs text-ink"
              />
              px
            </div>
          </div>

          <div className="flex items-center gap-2 text-xs text-ink-dim">
            Grid
            <input
              type="number"
              min={1}
              max={MAX_GRID_DIM}
              value={layout.grid.cols}
              onChange={(e) =>
                setLayout(
                  normalizeLayout({
                    ...layout,
                    grid: { ...layout.grid, cols: Number(e.target.value) || 1 },
                  }),
                )
              }
              className="w-14 rounded-md border border-edge bg-panel-2 px-2 py-1 font-tabular text-xs text-ink"
              title="Columns"
            />
            ×
            <input
              type="number"
              min={1}
              max={MAX_GRID_DIM}
              value={layout.grid.rows}
              onChange={(e) =>
                setLayout(
                  normalizeLayout({
                    ...layout,
                    grid: { ...layout.grid, rows: Number(e.target.value) || 1 },
                  }),
                )
              }
              className="w-14 rounded-md border border-edge bg-panel-2 px-2 py-1 font-tabular text-xs text-ink"
              title="Rows"
            />
            cells, gap
            <input
              type="number"
              min={0}
              max={64}
              value={layout.grid.gap}
              onChange={(e) =>
                patchLayout({
                  grid: { ...layout.grid, gap: Math.max(0, Number(e.target.value) || 0) },
                })
              }
              className="w-14 rounded-md border border-edge bg-panel-2 px-2 py-1 font-tabular text-xs text-ink"
            />
            px
          </div>

          <div className="flex items-center gap-2 text-xs text-ink-dim">
            Edge padding
            <input
              type="number"
              min={0}
              max={200}
              value={layout.padX}
              onChange={(e) => patchLayout({ padX: Math.max(0, Number(e.target.value) || 0) })}
              className="w-16 rounded-md border border-edge bg-panel-2 px-2 py-1 font-tabular text-xs text-ink"
              title="Horizontal padding (px)"
            />
            ×
            <input
              type="number"
              min={0}
              max={200}
              value={layout.padY}
              onChange={(e) => patchLayout({ padY: Math.max(0, Number(e.target.value) || 0) })}
              className="w-16 rounded-md border border-edge bg-panel-2 px-2 py-1 font-tabular text-xs text-ink"
              title="Vertical padding (px)"
            />
            px
          </div>

          <div>
            <span className="mb-1 block text-xs text-ink-dim">Page behind the widgets</span>
            <SegmentedControl
              ariaLabel="Page behind the widgets"
              value={layout.page}
              onValueChange={(page) => patchLayout({ page })}
              options={[
                { value: "transparent", label: "Transparent" },
                { value: "green", label: "Green screen" },
                { value: "dark", label: "Solid dark" },
              ]}
            />
          </div>

          <label className="flex items-center gap-2 text-xs text-ink-dim">
            Background {layout.bg}%
            <input
              type="range"
              min={0}
              max={100}
              step={5}
              value={layout.bg}
              onChange={(e) => patchLayout({ bg: Number(e.target.value) })}
              className="flex-1 accent-[#38bdf8]"
            />
          </label>

          <label className="flex items-center gap-2 text-xs text-ink-dim">
            <input
              type="checkbox"
              checked={layout.demo}
              onChange={(e) => patchLayout({ demo: e.target.checked })}
              className="accent-[#38bdf8]"
            />
            Placeholder data when no telemetry (animated fake lap; the fuel
            slowly drains so the alerts widget fires too)
          </label>

          {/* Widget palette */}
          <div>
            <span className="mb-1 block text-xs text-ink-dim">Add widgets</span>
            <div className="space-y-1.5">
              {groups.map((group) => {
                const ids = (Object.keys(WIDGET_META) as (keyof typeof WIDGET_META)[]).filter(
                  (id) => WIDGET_META[id].group === group,
                );
                if (ids.length === 0) return null;
                return (
                  <div key={group} className="flex flex-wrap items-center gap-1">
                    <span className="w-16 text-[10px] uppercase tracking-wider text-ink-dim/70">
                      {WIDGET_GROUP_LABELS[group]}
                    </span>
                    {ids.map((id) => (
                      <button
                        key={id}
                        className="rounded-md border border-edge px-1.5 py-0.5 text-[11px] text-ink-dim hover:border-accent/50 hover:text-ink"
                        onClick={() => addWidget(id)}
                        title={`Add ${WIDGET_META[id].label}`}
                      >
                        + {WIDGET_META[id].label}
                      </button>
                    ))}
                  </div>
                );
              })}
            </div>
          </div>

          {/* Selected widget */}
          <div className="rounded-lg border border-edge p-2">
            {selectedCell ? (
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold">
                    {WIDGET_META[selectedCell.widget].label}
                  </span>
                  <button
                    className="text-xs text-ink-dim hover:text-brake"
                    onClick={() => removeCell(selectedCell.id)}
                  >
                    remove
                  </button>
                </div>
                {WIDGET_META[selectedCell.widget].variants.length > 1 && (
                  <label className="flex items-center justify-between gap-2 text-xs text-ink-dim">
                    Style
                    <Select
                      ariaLabel="Widget style"
                      value={selectedCell.variant}
                      onValueChange={(variant) => updateCell(selectedCell.id, { variant })}
                      options={WIDGET_META[selectedCell.widget].variants.map((v) => ({
                        value: v.key,
                        label: v.label,
                      }))}
                      className="px-2 py-1 text-xs"
                    />
                  </label>
                )}
                <label className="flex items-center justify-between gap-2 text-xs text-ink-dim">
                  Size
                  <Select
                    ariaLabel="Widget size"
                    value={`${selectedCell.w}x${selectedCell.h}`}
                    onValueChange={(v) => {
                      const [w, h] = v.split("x").map(Number);
                      const fits =
                        selectedCell.x + w <= layout.grid.cols &&
                        selectedCell.y + h <= layout.grid.rows &&
                        !layout.cells.some(
                          (c) =>
                            c.id !== selectedCell.id &&
                            c.x < selectedCell.x + w &&
                            selectedCell.x < c.x + c.w &&
                            c.y < selectedCell.y + h &&
                            selectedCell.y < c.y + c.h,
                        );
                      if (!fits) {
                        toast("That size doesn't fit here — move the widget first", "error");
                        return;
                      }
                      updateCell(selectedCell.id, { w, h });
                    }}
                    options={WIDGET_META[selectedCell.widget].sizes.map(([w, h]) => ({
                      value: `${w}x${h}`,
                      label: `${w} × ${h}`,
                    }))}
                    className="px-2 py-1 text-xs"
                  />
                </label>
                <label className="flex items-center justify-between gap-2 text-xs text-ink-dim">
                  Fine scale
                  <Select
                    ariaLabel="Widget scale"
                    value={String(
                      typeof selectedCell.options?.scale === "number"
                        ? selectedCell.options.scale
                        : 1,
                    )}
                    onValueChange={(v) =>
                      updateCell(selectedCell.id, {
                        options: { ...selectedCell.options, scale: Number(v) },
                      })
                    }
                    options={SCALE_STEPS.map((s) => ({
                      value: String(s),
                      label: `${s * 100}%`,
                    }))}
                    className="px-2 py-1 text-xs"
                  />
                </label>
              </div>
            ) : (
              <p className="text-xs text-ink-dim">
                Click a widget on the canvas to change its style, size, or scale. Drag to move;
                drag the corner handle to resize.
              </p>
            )}
          </div>
        </div>

        {/* Canvas */}
        <div className="min-w-0">
          <div className="mb-1 flex items-center gap-3 text-xs text-ink-dim">
            <span>
              Canvas
              {layout.size && (
                <span className="ml-2 font-tabular">
                  {layout.size.width} × {layout.size.height} ({activeSizeLabel})
                </span>
              )}
            </span>
            {!frame && !layout.demo && (
              <span className="text-warn">
                no telemetry — enable placeholder data to see the widgets
              </span>
            )}
          </div>
          <GridCanvas
            layout={layout}
            frame={frame}
            laps={laps}
            selected={selected}
            onSelect={setSelected}
            onCellsChange={(cells) => patchLayout({ cells })}
          />
          <p className="mt-1 text-[11px] text-ink-dim">
            {draft.kind === "dash"
              ? "Open the URL below full-screen on the driver's second display."
              : layout.size
                ? `Add as an OBS Browser source sized ${layout.size.width} × ${layout.size.height}.`
                : "Add as an OBS Browser source; the overlay fills whatever size the source has."}
          </p>
        </div>
      </div>

      {/* URLs */}
      {url ? (
        <>
          {dirty && (
            <p className="text-[11px] text-warn">
              Unsaved changes — the URLs below still serve the last saved version.
            </p>
          )}
          <UrlRow label="This device" url={url} flash={flash} />
          {lanUrl && (
            <UrlRow label="Other devices (OBS PC, phone, second screen)" url={lanUrl} flash={flash} />
          )}
        </>
      ) : (
        <p className="text-xs text-ink-dim">
          Save the layout to get a short URL for OBS / the driver screen.
        </p>
      )}

      <PromptDialog
        open={savingAs}
        title="Save layout"
        label="Name this layout — the name becomes part of its URL."
        placeholder="e.g. race-strip, endurance-dash"
        onSubmit={(name) => void saveAs(name)}
        onCancel={() => setSavingAs(false)}
      />
      <PromptDialog
        open={renaming}
        title="Rename layout"
        label="Existing OBS sources using the old name will need the new URL."
        initialValue={draft.name ?? ""}
        onSubmit={(name) => void rename(name)}
        onCancel={() => setRenaming(false)}
      />
      <ConfirmDialog
        open={deleting}
        title={`Delete layout "${draft.name}"?`}
        body="OBS sources and dashboards using its URL will stop rendering."
        confirmLabel="Delete"
        danger
        onConfirm={() => void removeLayout()}
        onCancel={() => setDeleting(false)}
      />
    </div>
  );
}

function UrlRow({
  label,
  url,
  flash,
}: {
  label: string;
  url: string;
  flash: (text: string) => void;
}) {
  return (
    <div>
      <div className="mb-1 text-[10px] uppercase tracking-widest text-ink-dim">{label}</div>
      <div className="flex gap-2">
        <code className="min-w-0 flex-1 truncate rounded-md border border-edge bg-panel-2 px-3 py-1.5 font-tabular text-xs leading-6">
          {url}
        </code>
        <button
          className="btn shrink-0"
          onClick={() =>
            navigator.clipboard
              .writeText(url)
              .then(() => flash("URL copied"))
              .catch(() => flash("Copy failed — select the URL manually"))
          }
        >
          Copy
        </button>
        <a className="btn shrink-0" href={url} target="_blank" rel="noreferrer">
          Open
        </a>
      </div>
    </div>
  );
}
