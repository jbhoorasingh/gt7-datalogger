// Settings page model: the rail's sections, and the pending-changes buffer.
// Edits are kept as a partial patch over the server's copy; a key whose
// edited value matches the server again drops out, so the pending bar only
// ever counts real differences and one PUT carries exactly the diff.

import type { AdminSettingsPatch } from "@/lib/api";
import type { AdminSettings } from "@/lib/types";

export type SectionId =
  | "connection"
  | "sync"
  | "race-engineer"
  | "notifications"
  | "overlays"
  | "health"
  | "logs"
  | "access"
  | "data";

export interface SectionDef {
  id: SectionId;
  label: string;
  keywords: string;
}

export const SECTION_GROUPS: { group: string; items: SectionDef[] }[] = [
  {
    group: "Capture",
    items: [
      { id: "connection", label: "Connection", keywords: "connection ip console packet format source simulated udp playstation" },
      { id: "sync", label: "Sync", keywords: "sync token server cloud upload live spectate connection string" },
    ],
  },
  {
    group: "Race day",
    items: [
      { id: "race-engineer", label: "Race Engineer", keywords: "voice callouts verbosity units engineer categories" },
      { id: "notifications", label: "Notifications", keywords: "webhook discord notify events" },
    ],
  },
  {
    group: "Streaming",
    items: [
      { id: "overlays", label: "Overlays & dashboards", keywords: "overlay obs dash layout builder widget import" },
    ],
  },
  {
    group: "System",
    items: [
      { id: "health", label: "Health", keywords: "diagnostics packets uptime errors restart car database dropped" },
      { id: "logs", label: "Logs", keywords: "log level debug warning error" },
      { id: "access", label: "Access", keywords: "admin token password api key" },
      { id: "data", label: "Data", keywords: "delete compact vacuum export backup clear" },
    ],
  },
];

const SECTION_IDS = SECTION_GROUPS.flatMap((g) => g.items.map((i) => i.id));

// Short aliases (the design's own ids) land on the canonical slug.
const SECTION_ALIASES: Record<string, SectionId> = {
  engineer: "race-engineer",
  notify: "notifications",
  diagnostics: "health",
};

export function resolveSection(section: string | null | undefined): SectionId {
  if (!section) return "connection";
  if ((SECTION_IDS as string[]).includes(section)) return section as SectionId;
  return SECTION_ALIASES[section] ?? "connection";
}

/** The rail's groups with only the items matching `query` (label or keywords). */
export function filterSections(query: string) {
  const q = query.trim().toLowerCase();
  return SECTION_GROUPS.map((g) => ({
    group: g.group,
    items: g.items.filter((i) => !q || `${i.label} ${i.keywords}`.toLowerCase().includes(q)),
  })).filter((g) => g.items.length > 0);
}

// Every setting the page buffers, with the words the pending bar uses.
export const SETTING_LABELS = {
  source: "Telemetry source",
  ps_ip: "Console IP",
  packet_format: "Packet format",
  sync_enabled: "Sync",
  sync_tracks: "Sync tracks",
  sync_sessions: "Sync sessions",
  sync_live: "Sync live",
  race_engineer: "Race Engineer",
  race_engineer_verbosity: "Verbosity",
  race_engineer_units: "Spoken units",
  race_engineer_categories: "Callout categories",
  webhook_url: "Webhook URL",
  webhook_events: "Notification events",
  log_level: "Server log level",
} as const satisfies Partial<Record<keyof AdminSettings, string>>;

export type EditableKey = keyof typeof SETTING_LABELS;
export type SettingsEdits = Partial<Pick<AdminSettings, EditableKey>>;

// Lists are sets here: the server may hand categories back in another order.
export function sameValue(a: unknown, b: unknown): boolean {
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((x) => b.includes(x));
  }
  return a === b;
}

/** Fold `patch` into `edits`, dropping any key that is back to the saved value. */
export function mergeEdits(
  saved: AdminSettings,
  edits: SettingsEdits,
  patch: SettingsEdits,
): SettingsEdits {
  const next: Record<string, unknown> = { ...edits, ...patch };
  for (const k of Object.keys(next)) {
    if (sameValue(next[k], saved[k as EditableKey])) delete next[k];
  }
  return next as SettingsEdits;
}

/** The keys that still differ from the server, in label order. */
export function pendingKeys(saved: AdminSettings, edits: SettingsEdits): EditableKey[] {
  return (Object.keys(SETTING_LABELS) as EditableKey[]).filter(
    (k) => k in edits && !sameValue(edits[k], saved[k]),
  );
}

export function pendingPatch(saved: AdminSettings, edits: SettingsEdits): AdminSettingsPatch {
  const patch: Record<string, unknown> = {};
  for (const k of pendingKeys(saved, edits)) patch[k] = edits[k];
  return patch as AdminSettingsPatch;
}
