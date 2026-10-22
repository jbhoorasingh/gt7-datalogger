// Settings › Data › Back up (#161): the parts one backup file can hold, and
// how each is described from what the server says it would hold now.

import type { BackupInventory, BackupPart, PreparedBackup } from "@/lib/types";
import { mb } from "./parts";

export interface BackupPartInfo {
  id: BackupPart;
  label: string;
  /** What the part is, when the server has not said what it holds. */
  blurb: string;
  /** Ticked unless the person unticks it. Tokens are not: see tokensWarning. */
  defaultOn: boolean;
}

export const BACKUP_PARTS: BackupPartInfo[] = [
  { id: "data", label: "Data", blurb: "Every session and lap", defaultOn: true },
  {
    id: "surveys",
    label: "Surveys",
    blurb: "Track bundles, corrections, survey logs, car widths",
    defaultOn: true,
  },
  { id: "config", label: "Config", blurb: "Settings saved here, without credentials", defaultOn: true },
  { id: "layouts", label: "Dash/OBS", blurb: "Overlay and dash layouts", defaultOn: true },
  { id: "tokens", label: "Tokens", blurb: "Webhook URL and sync token", defaultOn: false },
];

/** What ticking Tokens hands to anyone holding the file, for the credentials saved. */
export function tokensWarning(inv: BackupInventory | null): string {
  const keys = inv?.tokens.keys ?? ["webhook_url", "sync_token"];
  const risks = [
    keys.includes("webhook_url") && "post to your webhook",
    keys.includes("sync_token") && "sign in to your sync account",
  ].filter(Boolean);
  const what = risks.length > 1 ? "both" : "it";
  return `The file then carries ${what}: anyone holding it can ${risks.join(" and ")}.`;
}

export const DEFAULT_PARTS: BackupPart[] = BACKUP_PARTS.filter((p) => p.defaultOn).map((p) => p.id);

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString()} ${n === 1 ? one : many}`;
}

/** What a part holds right now, in a few words; its blurb until the server says. */
export function partDetail(id: BackupPart, inv: BackupInventory | null): string {
  const info = BACKUP_PARTS.find((p) => p.id === id);
  if (!inv) return info?.blurb ?? "";
  switch (id) {
    case "data":
      return `${plural(inv.data.sessions, "session")} · ${plural(inv.data.laps, "lap")} · ${mb(inv.data.bytes)}`;
    case "surveys": {
      const s = inv.surveys;
      if (!s.available) return "Nothing surveyed or pulled yet";
      const bits = [plural(s.bundles, "circuit")];
      if (s.corrections) bits.push(plural(s.corrections, "correction"));
      if (s.logs) bits.push(plural(s.logs, "survey log"));
      return `${bits.join(" · ")} · ${mb(s.bytes)}`;
    }
    case "config":
      return inv.config.available
        ? `${plural(inv.config.settings, "saved setting")}, without credentials`
        : "No settings saved here yet";
    case "layouts":
      return inv.layouts.available ? plural(inv.layouts.layouts, "layout") : "No layouts saved yet";
    case "tokens": {
      const names: Record<string, string> = { webhook_url: "webhook URL", sync_token: "sync token" };
      const keys = inv.tokens.keys.map((k) => names[k] ?? k);
      return keys.length ? `The ${keys.join(" and ")}` : "No webhook URL or sync token saved";
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
  if (!parts.length) throw new Error("Nothing to back up: the parts switched on hold nothing yet");
  return { made: await client.makeBackup(parts), inventory };
}
