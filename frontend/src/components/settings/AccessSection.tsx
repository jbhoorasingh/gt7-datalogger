import { useState } from "react";
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
export function TokenField({ label = "Save", placeholder }: { label?: string; placeholder: string }) {
  const [token, setToken] = useState(getAdminToken());
  const save = () => {
    setAdminToken(token.trim());
    window.location.reload();
  };
  return (
    <div className="flex gap-2">
      <input
        type="password"
        aria-label="Admin token"
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
        {label}
      </button>
    </div>
  );
}

export function AccessSection({ protection, locked }: { protection: Protection; locked: string | null }) {
  return (
    <SectionPanel
      title="Access"
      description="Who may change settings. Live, overlay and dash pages never need a token."
    >
      <SettingRow
        label="Server protection"
        help={
          <>
            Set by <span className="font-tabular">GT7_ADMIN_TOKEN</span> on the server.
          </>
        }
      >
        {protection === "open" ? (
          <span className="flex items-center gap-1.5 text-xs text-warn">
            <Dot tone="warn" />
            Open — anyone on your network can change settings
          </span>
        ) : protection === "protected" ? (
          <span className="flex items-center gap-1.5 text-xs text-throttle">
            <Dot tone="good" />
            Protected — changes need the admin token
          </span>
        ) : (
          <span className="text-xs text-ink-faint">Unknown — the server did not answer.</span>
        )}
      </SettingRow>
      <SettingRow
        label="Admin token in this browser"
        help="Stored locally, sent as X-API-Key."
        last
      >
        <div className="flex flex-col gap-1.5">
          <TokenField
            placeholder={protection === "open" ? "not needed — server is open" : "the server's GT7_ADMIN_TOKEN"}
          />
          {locked && <span className="text-[11px] text-warn">{locked}</span>}
        </div>
      </SettingRow>
    </SectionPanel>
  );
}
