// The race line's Layers menu: one switch per thing the map can draw besides
// the reference line, in three groups. A layer is listed only when the laps
// on the map have something to put on it — a switch for a layer the laps
// cannot fill (a recording from before the channel existed, a circuit never
// surveyed) is a switch that does nothing.

import { useEffect, useRef, useState } from "react";
import { useT } from "@/i18n";
import { MAP_LAYER_GROUPS, type MapLayerKey, type MapLayers } from "@/lib/mapLayers";

export function LayersMenu({
  layers,
  available,
  onToggle,
}: {
  layers: MapLayers;
  /** Which layers the laps on the map can fill. */
  available: Set<MapLayerKey>;
  onToggle: (key: MapLayerKey, on: boolean) => void;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      // Esc closes the menu first; the view's Esc (which resets the zoom)
      // listens on the window and only hears an Esc nobody else took.
      e.stopPropagation();
      setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const groups = MAP_LAYER_GROUPS.map((g) => ({
    ...g,
    layers: g.layers.filter((l) => available.has(l.key)),
  })).filter((g) => g.layers.length > 0);
  const count = groups.reduce((n, g) => n + g.layers.filter((l) => layers[l.key]).length, 0);

  return (
    <div ref={root} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-haspopup="menu"
        className={`rounded border px-2.5 py-0.5 text-[10.5px] transition-colors ${
          open ? "border-accent text-ink" : "border-edge text-ink-dim hover:text-ink"
        }`}
        title={t("map.layers.title")}
      >
        {t("map.layers.label")} · <span className="font-tabular">{count}</span> ▾
      </button>
      {open && (
        <div
          role="menu"
          className="elevated absolute right-0 top-full z-20 mt-1.5 max-h-[420px] w-[252px] overflow-y-auto rounded-md bg-panel py-1 pb-1.5"
        >
          {groups.map((g) => (
            <div key={g.titleKey}>
              <div className="section-header px-3 pb-0.5 pt-2">{t(g.titleKey)}</div>
              {g.layers.map((l) => {
                const on = layers[l.key];
                return (
                  <button
                    key={l.key}
                    role="menuitemcheckbox"
                    aria-checked={on}
                    onClick={() => onToggle(l.key, !on)}
                    className="flex w-full items-center gap-2.5 px-3 py-1.5 text-left text-ink transition-colors hover:bg-panel-2"
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block text-xs">{t(l.labelKey)}</span>
                      <span className="block text-[10.5px] text-ink-faint">{t(l.helpKey)}</span>
                    </span>
                    <span
                      className={`relative h-4 w-[30px] shrink-0 rounded-full transition-colors ${
                        on ? "bg-accent-700" : "bg-edge"
                      }`}
                    >
                      <span
                        className={`absolute top-0.5 h-3 w-3 rounded-full transition-[left] duration-150 ${
                          on ? "left-4 bg-accent-200" : "left-0.5 bg-ink-faint"
                        }`}
                      />
                    </span>
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
