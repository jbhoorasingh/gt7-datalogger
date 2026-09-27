import { useRef } from "react";
import { api, ApiError } from "@/lib/api";
import { migrateOverlayConfig, normalizeLayout, type LayoutConfig, type LayoutSummary } from "@/lib/layout";
import { DEFAULT_CONFIG, type OverlayConfig } from "@/lib/overlay";
import { toast } from "@/store/toasts";
import { SectionPanel } from "./parts";

export function layoutPath(l: LayoutSummary): string {
  return `${l.kind === "dash" ? "/dash" : "/overlay"}?layout=${encodeURIComponent(l.name)}`;
}

function canvas(l: LayoutSummary): string {
  const s = l.config.size;
  return s ? `${s.width}×${s.height}` : "fills screen";
}

// Same reading as the builder's own Import: v2 files as they are, v1 exports
// (a widgets array, no version) through the migration.
function parseLayoutFile(text: string): LayoutConfig | null {
  try {
    const parsed = JSON.parse(text) as Record<string, unknown>;
    if (parsed.version === 2) return normalizeLayout(parsed);
    if (Array.isArray(parsed.widgets)) {
      return migrateOverlayConfig({ ...DEFAULT_CONFIG, ...parsed } as OverlayConfig);
    }
  } catch {
    // fall through
  }
  return null;
}

export function OverlaysSection({
  layouts,
  reload,
}: {
  layouts: LayoutSummary[] | null;
  reload: () => void;
}) {
  const fileInput = useRef<HTMLInputElement>(null);

  // An exported file carries no name or kind: it becomes an overlay named
  // after the file, with a suffix if that name is taken.
  async function importFile(file: File) {
    const config = parseLayoutFile(await file.text());
    if (!config) {
      toast("Import failed — not a valid layout file", "error");
      return;
    }
    const base = file.name.replace(/\.json$/i, "").replace(/^gt7-layout-/, "") || "Imported layout";
    for (let n = 1; n <= 20; n++) {
      const name = n === 1 ? base : `${base} (${n})`;
      try {
        await api.layouts.create(name, "overlay", config);
        toast(`Imported "${name}"`, "success");
        reload();
        return;
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) continue;
        toast(e instanceof Error ? e.message : "Import failed", "error");
        return;
      }
    }
    toast("Import failed — too many layouts with that name", "error");
  }

  return (
    <SectionPanel
      title="Overlays & dashboards"
      description="Saved layouts. Edit one and every OBS source using its URL updates."
      actions={
        <>
          <a className="btn btn-primary no-underline" href="#/overlays">
            Open builder
          </a>
          <button className="btn" onClick={() => fileInput.current?.click()}>
            Import JSON…
          </button>
          <input
            ref={fileInput}
            type="file"
            accept="application/json,.json"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              e.target.value = "";
              if (f) void importFile(f);
            }}
          />
        </>
      }
    >
      {layouts === null ? (
        <div className="px-[18px] py-3.5 text-sm text-ink-dim">Loading…</div>
      ) : layouts.length === 0 ? (
        <div className="px-[18px] py-3.5 text-xs text-ink-dim">
          No saved layouts yet. Build one in the Overlays tab, or import a layout file.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse font-tabular text-xs">
            <thead>
              <tr className="text-left text-[11px] text-ink-faint">
                <th className="px-[18px] py-2 font-normal">Layout</th>
                <th className="p-2 font-normal">Kind</th>
                <th className="p-2 font-normal">Canvas</th>
                <th className="p-2 font-normal">Widgets</th>
                <th className="p-2 font-normal">URL</th>
                <th className="px-[18px] py-2" />
              </tr>
            </thead>
            <tbody>
              {layouts.map((l) => {
                const path = layoutPath(l);
                return (
                  <tr key={l.id} className="rule-row">
                    <td className="px-[18px] py-[9px]">{l.name}</td>
                    <td className="px-2 py-[9px] text-ink-dim">{l.kind === "dash" ? "dashboard" : "overlay"}</td>
                    <td className="px-2 py-[9px] text-ink-dim">{canvas(l)}</td>
                    <td className="px-2 py-[9px] text-ink-dim">{l.config.cells.length}</td>
                    <td className="whitespace-nowrap px-2 py-[9px] text-accent-300">{path}</td>
                    <td className="whitespace-nowrap px-[18px] py-1.5 text-right">
                      <div className="inline-flex gap-1.5">
                        <button
                          className="btn"
                          onClick={() =>
                            navigator.clipboard
                              .writeText(`${window.location.origin}${path}`)
                              .then(() => toast("URL copied", "success"))
                              .catch(() => toast("Copy failed — select the URL manually", "error"))
                          }
                        >
                          Copy URL
                        </button>
                        <a className="btn no-underline" href={`#/overlays?layout=${l.id}`}>
                          Edit
                        </a>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </SectionPanel>
  );
}
