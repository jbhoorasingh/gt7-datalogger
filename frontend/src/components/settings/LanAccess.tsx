// LAN access off (#186, #195): the server answers only this computer, so a
// LAN address for a phone, a tablet or OBS on another PC would lead nowhere.
// Shown in place of such an address, with the switch that turns it on.

import { useState } from "react";
import { useT } from "@/i18n";
import { api } from "@/lib/api";
import type { AdminStats } from "@/lib/types";
import { toast } from "@/store/toasts";
import { Dot } from "./parts";

/** Whether the stats say a LAN address is usable: an older server has no
 * lan_reachable and always served the LAN. */
export function lanOff(stats: AdminStats | null): boolean {
  return stats != null && stats.lan_reachable === false && !stats.loopback_only;
}

export async function setLanAccess(on: boolean): Promise<void> {
  await api.admin.updateConfig({ values: { lan_access: on } });
}

export function LanOffNotice({
  onChanged,
  button = true,
}: {
  onChanged?: () => void;
  /** False where the switch is already next to it (Settings › Phone). */
  button?: boolean;
}) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  return (
    <div className="flex flex-col gap-1.5" role="status">
      <span className="flex items-center gap-1.5 text-[12px] text-warn">
        <Dot tone="warn" />
        {t("lan.off.title")}
      </span>
      <span className="text-[11px] text-ink-dim">{t("lan.off.body")}</span>
      {button && (
      <div>
        <button
          className="btn"
          disabled={busy}
          onClick={() => {
            setBusy(true);
            setLanAccess(true)
              .then(() => {
                toast(t("lan.on.done"), "success");
                onChanged?.();
              })
              .catch((e) => toast(e instanceof Error ? e.message : t("lan.on.failed"), "error"))
              .finally(() => setBusy(false));
          }}
        >
          {t("lan.toggle")}
        </button>
      </div>
      )}
    </div>
  );
}
