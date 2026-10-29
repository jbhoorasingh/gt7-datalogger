import { useRef } from "react";
import { t as translate, useT, withLanguage } from "@/i18n";
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
  return s ? `${s.width}×${s.height}` : translate("settings.overlays.fills");
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
  const t = useT();
  const fileInput = useRef<HTMLInputElement>(null);

  // An exported file carries no name or kind: it becomes an overlay named
  // after the file, with a suffix if that name is taken.
  async function importFile(file: File) {
    const config = parseLayoutFile(await file.text());
    if (!config) {
      toast(t("settings.overlays.invalid"), "error");
      return;
    }
    const base =
      file.name.replace(/\.json$/i, "").replace(/^gt7-layout-/, "") || t("settings.overlays.importedName");
    for (let n = 1; n <= 20; n++) {
      const name = n === 1 ? base : `${base} (${n})`;
      try {
        await api.layouts.create(name, "overlay", config);
        toast(t("settings.overlays.imported", { name }), "success");
        reload();
        return;
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) continue;
        toast(e instanceof Error ? e.message : t("settings.overlays.importFailed"), "error");
        return;
      }
    }
    toast(t("settings.overlays.tooMany"), "error");
  }

  return (
    <SectionPanel
      title={t("settings.section.overlays")}
      description={t("settings.overlays.desc")}
      actions={
        <>
          <a className="btn btn-primary no-underline" href="#/overlays">
            {t("settings.overlays.openBuilder")}
          </a>
          <button className="btn" onClick={() => fileInput.current?.click()}>
            {t("settings.overlays.import")}
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
        <div className="px-[18px] py-3.5 text-sm text-ink-dim">{t("common.loading")}</div>
      ) : layouts.length === 0 ? (
        <div className="px-[18px] py-3.5 text-xs text-ink-dim">{t("settings.overlays.none")}</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse font-tabular text-xs">
            <thead>
              <tr className="text-left text-[11px] text-ink-faint">
                <th className="px-[18px] py-2 font-normal">{t("settings.overlays.layout")}</th>
                <th className="p-2 font-normal">{t("settings.overlays.kind")}</th>
                <th className="p-2 font-normal">{t("settings.overlays.canvas")}</th>
                <th className="p-2 font-normal">{t("settings.overlays.widgets")}</th>
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
                    <td className="px-2 py-[9px] text-ink-dim">
                      {l.kind === "dash" ? t("settings.overlays.dashboard") : t("settings.overlays.overlay")}
                    </td>
                    <td className="px-2 py-[9px] text-ink-dim">{canvas(l)}</td>
                    <td className="px-2 py-[9px] text-ink-dim">{l.config.cells.length}</td>
                    <td className="whitespace-nowrap px-2 py-[9px] text-accent-300">{path}</td>
                    <td className="whitespace-nowrap px-[18px] py-1.5 text-right">
                      <div className="inline-flex gap-1.5">
                        <button
                          className="btn"
                          onClick={() =>
                            navigator.clipboard
                              .writeText(withLanguage(`${window.location.origin}${path}`))
                              .then(() => toast(t("settings.overlays.copied"), "success"))
                              .catch(() => toast(t("settings.overlays.copyFailed"), "error"))
                          }
                        >
                          {t("settings.overlays.copy")}
                        </button>
                        <a className="btn no-underline" href={`#/overlays?layout=${l.id}`}>
                          {t("settings.overlays.edit")}
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
