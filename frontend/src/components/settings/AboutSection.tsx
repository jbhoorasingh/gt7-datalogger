// Settings › About (#190): which build this is, how it was installed, where
// it keeps its data and its log, whether a newer version is out (#196), and
// that it is nobody's official product.

import { useEffect, useState } from "react";
import { currentLanguage, useT, type MessageKey } from "@/i18n";
import { api } from "@/lib/api";
import { loadHealth, useHealth } from "@/lib/health";
import type { AdminStats, ConfigDoc, InstallKind } from "@/lib/types";
import { version as uiVersion } from "../../../package.json";
import { Dot, SectionPanel, SettingRow } from "./parts";

const KIND_LABEL: Record<InstallKind, MessageKey> = {
  desktop: "about.kind.desktop",
  docker: "about.kind.docker",
  source: "about.kind.source",
};

/** The folder a file path sits in, whichever separator the server uses. */
export function folderOf(path: string): string {
  const cut = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  return cut > 0 ? path.slice(0, cut) : path;
}

function configValue(doc: ConfigDoc | null, key: string): string {
  const v = doc?.settings.find((s) => s.key === key)?.value;
  return typeof v === "string" ? v : "";
}

function checkedAt(at: string | number | null | undefined): string {
  if (at == null || at === "") return "";
  const date = typeof at === "number" ? new Date(at * 1000) : new Date(at);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString(currentLanguage());
}

export function AboutSection({ stats }: { stats: AdminStats | null }) {
  const t = useT();
  const health = useHealth((s) => s.health);
  const [doc, setDoc] = useState<ConfigDoc | null>(null);
  useEffect(() => {
    void loadHealth();
    api.admin.config().then(setDoc).catch(() => {});
  }, []);

  const dataFolder = configValue(doc, "data_dir") || (stats ? folderOf(stats.db.path) : "");
  const logFile = configValue(doc, "log_file");
  const update = stats?.update;
  const when = checkedAt(update?.checked_at);

  return (
    <SectionPanel title={t("settings.section.about")} description={t("about.desc")}>
      <SettingRow label={t("about.version")}>
        <span className="font-tabular text-[13px]">
          {health?.version ?? uiVersion}
          {health && health.version !== uiVersion && (
            <span className="ml-2 text-[11px] text-ink-faint">{t("about.uiVersion", { version: uiVersion })}</span>
          )}
        </span>
      </SettingRow>
      <SettingRow label={t("about.runtime")}>
        <span className="text-[13px]">
          {health ? t(KIND_LABEL[health.install_kind]) : t("common.unknown")}
        </span>
      </SettingRow>
      <SettingRow label={t("about.dataFolder")} help={stats ? t("about.database", { path: stats.db.path }) : undefined}>
        <span className="break-all font-tabular text-[12.5px]">{dataFolder || t("common.loading")}</span>
      </SettingRow>
      <SettingRow label={t("about.logFile")}>
        <span className="break-all font-tabular text-[12.5px]">
          {logFile || <span className="text-ink-faint">{t("about.noLogFile")}</span>}
        </span>
      </SettingRow>
      <SettingRow label={t("about.updates")}>
        {update == null ? (
          <span className="text-[12px] text-ink-faint">{t("about.noUpdateCheck")}</span>
        ) : update.available ? (
          <span className="flex flex-wrap items-center gap-2 text-[12.5px]">
            <Dot tone="accent" />
            {t("update.available", { version: update.version ?? "?" })}
            {update.url && (
              <a className="text-accent hover:underline" href={update.url} target="_blank" rel="noreferrer">
                {t("common.download")}
              </a>
            )}
          </span>
        ) : (
          <span className="flex items-center gap-2 text-[12.5px]">
            <Dot tone="good" />
            {when ? t("about.upToDateChecked", { when }) : t("about.upToDate")}
          </span>
        )}
      </SettingRow>
      <SettingRow label={t("about.legal")} last>
        <span className="text-[12px] text-ink-dim">{t("about.unofficial")}</span>
      </SettingRow>
    </SectionPanel>
  );
}
