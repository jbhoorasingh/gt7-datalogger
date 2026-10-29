// Settings › Data › Back up (#161): the parts one backup file can hold, and
// how each is described from what the server says it would hold now.

import { t, type MessageKey } from "@/i18n";
import type { BackupInventory, BackupPart, PreparedBackup } from "@/lib/types";
import { mb } from "./parts";

export interface BackupPartInfo {
  id: BackupPart;
  label: MessageKey;
  /** What the part is, when the server has not said what it holds. */
  blurb: MessageKey;
  /** Ticked unless the person unticks it. Tokens are not: see tokensWarning. */
  defaultOn: boolean;
}

export const BACKUP_PARTS: BackupPartInfo[] = [
  { id: "data", label: "backup.part.data", blurb: "backup.blurb.data", defaultOn: true },
  { id: "surveys", label: "backup.part.surveys", blurb: "backup.blurb.surveys", defaultOn: true },
  { id: "config", label: "backup.part.config", blurb: "backup.blurb.config", defaultOn: true },
  { id: "layouts", label: "backup.part.layouts", blurb: "backup.blurb.layouts", defaultOn: true },
  { id: "tokens", label: "backup.part.tokens", blurb: "backup.blurb.tokens", defaultOn: false },
];

/** What ticking Tokens hands to anyone holding the file, for the credentials saved. */
export function tokensWarning(inv: BackupInventory | null): string {
  const keys = inv?.tokens.keys ?? ["webhook_url", "sync_token"];
  const hook = keys.includes("webhook_url");
  const sync = keys.includes("sync_token");
  if (hook && sync) return t("backup.tokens.both");
  return hook ? t("backup.tokens.webhook") : t("backup.tokens.sync");
}

export const DEFAULT_PARTS: BackupPart[] = BACKUP_PARTS.filter((p) => p.defaultOn).map((p) => p.id);

/** What a part holds right now, in a few words; its blurb until the server says. */
export function partDetail(id: BackupPart, inv: BackupInventory | null): string {
  const info = BACKUP_PARTS.find((p) => p.id === id);
  if (!inv) return info ? t(info.blurb) : "";
  switch (id) {
    case "data":
      return [
        t("count.sessions", { count: inv.data.sessions }),
        t("count.laps", { count: inv.data.laps }),
        mb(inv.data.bytes),
      ].join(" · ");
    case "surveys": {
      const s = inv.surveys;
      if (!s.available) return t("backup.surveys.none");
      const bits = [t("backup.count.circuits", { count: s.bundles })];
      if (s.corrections) bits.push(t("backup.count.corrections", { count: s.corrections }));
      if (s.logs) bits.push(t("backup.count.logs", { count: s.logs }));
      return `${bits.join(" · ")} · ${mb(s.bytes)}`;
    }
    case "config":
      return inv.config.available
        ? t("backup.config.saved", { count: inv.config.settings })
        : t("backup.config.none");
    case "layouts":
      return inv.layouts.available
        ? t("backup.count.layouts", { count: inv.layouts.layouts })
        : t("backup.layouts.none");
    case "tokens": {
      const hook = inv.tokens.keys.includes("webhook_url");
      const sync = inv.tokens.keys.includes("sync_token");
      if (hook && sync) {
        // In the order the server listed them, as before.
        return inv.tokens.keys.indexOf("sync_token") < inv.tokens.keys.indexOf("webhook_url")
          ? t("backup.tokens.heldSyncHook")
          : t("backup.tokens.heldHookSync");
      }
      if (hook) return t("backup.tokens.heldHook");
      if (sync) return t("backup.tokens.heldSync");
      return t("backup.tokens.none");
    }
  }
}

/** The parts to ask for: ticked, and holding something when the server has said. */
export function chosenParts(selected: ReadonlySet<BackupPart>, inv: BackupInventory | null): BackupPart[] {
  return BACKUP_PARTS.map((p) => p.id).filter((id) => selected.has(id) && (!inv || inv[id].available));
}

export interface BackupClient {
  backupInventory: () => Promise<BackupInventory>;
  makeBackup: (parts: BackupPart[]) => Promise<PreparedBackup>;
}

/** Make a backup of the ticked parts, judged against what the server holds
 * at the click rather than when the section last asked: settings applied from
 * the pending bar, or a layout saved in another tab, change that without a
 * lap, and a part left out on a stale answer would be missing from the file. */
export async function makeBackup(
  selected: ReadonlySet<BackupPart>,
  client: BackupClient,
): Promise<{ made: PreparedBackup; inventory: BackupInventory }> {
  const inventory = await client.backupInventory();
  const parts = chosenParts(selected, inventory);
  if (!parts.length) throw new Error(t("backup.nothing"));
  return { made: await client.makeBackup(parts), inventory };
}
