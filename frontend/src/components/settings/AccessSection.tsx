import { useState } from "react";
import { useT } from "@/i18n";
import { getAdminToken, setAdminToken } from "@/lib/api";
import { Dot, INPUT_CLS, SectionPanel, SettingRow } from "./parts";

export type Protection = "open" | "protected" | null;

/** Whether the server gates admin endpoints: ask without a token. */
export async function probeProtection(): Promise<Protection> {
  try {
    const resp = await fetch("/api/admin/stats");
    if (resp.ok) return "open";
    if (resp.status === 401 || resp.status === 403) return "protected";
  } catch {
    // unreachable: say nothing rather than guess
  }
  return null;
}

/** The admin token in this browser. Saving reloads so every poll picks it up. */
export function TokenField({ label, placeholder }: { label?: string; placeholder: string }) {
  const t = useT();
  const [token, setToken] = useState(getAdminToken());
  const save = () => {
    setAdminToken(token.trim());
    window.location.reload();
  };
  return (
    <div className="flex gap-2">
      <input
        type="password"
        aria-label={t("settings.access.token")}
        autoComplete="off"
        value={token}
        onChange={(e) => setToken(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && token !== getAdminToken()) save();
        }}
        placeholder={placeholder}
        className={INPUT_CLS}
      />
      <button className="btn shrink-0" disabled={token === getAdminToken()} onClick={save}>
        {label ?? t("settings.access.save")}
      </button>
    </div>
  );
}

export function AccessSection({ protection, locked }: { protection: Protection; locked: string | null }) {
  const t = useT();
  return (
    <SectionPanel title={t("settings.section.access")} description={t("settings.access.desc")}>
      <SettingRow
        label={t("settings.access.protection")}
        help={t("settings.access.protectionHelp", { env: "GT7_ADMIN_TOKEN" })}
      >
        {protection === "open" ? (
          <span className="flex items-center gap-1.5 text-xs text-warn">
            <Dot tone="warn" />
            {t("settings.access.open")}
          </span>
        ) : protection === "protected" ? (
          <span className="flex items-center gap-1.5 text-xs text-throttle">
            <Dot tone="good" />
            {t("settings.access.protected")}
          </span>
        ) : (
          <span className="text-xs text-ink-faint">{t("settings.access.unknown")}</span>
        )}
      </SettingRow>
      <SettingRow label={t("settings.access.browserToken")} help={t("settings.access.browserTokenHelp")} last>
        <div className="flex flex-col gap-1.5">
          <TokenField
            placeholder={
              protection === "open" ? t("settings.access.notNeeded") : t("settings.locked.placeholder")
            }
          />
          {locked && <span className="text-[11px] text-warn">{locked}</span>}
        </div>
      </SettingRow>
    </SectionPanel>
  );
}
