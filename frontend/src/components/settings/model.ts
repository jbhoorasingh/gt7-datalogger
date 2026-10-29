// Settings page model: the rail's sections, and the pending-changes buffer.
// Edits are kept as a partial patch over the server's copy; a key whose
// edited value matches the server again drops out, so the pending bar only
// ever counts real differences and one PUT carries exactly the diff.

import { t as translate, type MessageKey, type Translate } from "@/i18n";
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
  | "data"
  | "advanced"
  | "phone";

export interface SectionDef {
  id: SectionId;
  label: MessageKey;
  /** English search words; they match in every language, as setting names do. */
  keywords: string;
}

export const SECTION_GROUPS: { group: MessageKey; items: SectionDef[] }[] = [
  {
    group: "settings.group.capture",
    items: [
      { id: "connection", label: "settings.section.connection", keywords: "connection ip console packet format source simulated udp playstation" },
      { id: "sync", label: "settings.section.sync", keywords: "sync token server cloud upload live spectate connection string" },
    ],
  },
  {
    group: "settings.group.raceDay",
    items: [
      { id: "race-engineer", label: "settings.section.raceEngineer", keywords: "voice callouts verbosity units engineer categories" },
      { id: "notifications", label: "settings.section.notifications", keywords: "webhook discord notify events" },
    ],
  },
  {
    group: "settings.group.streaming",
    items: [
      { id: "overlays", label: "settings.section.overlays", keywords: "overlay obs dash layout builder widget import" },
      { id: "phone", label: "settings.section.phone", keywords: "phone mobile app qr code mdns bonjour discover network connect" },
    ],
  },
  {
    group: "settings.group.system",
    items: [
      { id: "health", label: "settings.section.health", keywords: "diagnostics packets uptime errors restart car database dropped" },
      { id: "logs", label: "settings.section.logs", keywords: "log level debug warning error" },
      { id: "access", label: "settings.section.access", keywords: "admin token password api key" },
      { id: "data", label: "settings.section.data", keywords: "delete compact vacuum export backup clear move restore surveys layouts" },
      {
        id: "advanced",
        label: "settings.section.advanced",
        keywords:
          "environment variables env config all settings default port cors rate hz scenario cars tracks signatures restart",
      },
    ],
  },
];

const SECTION_IDS = SECTION_GROUPS.flatMap((g) => g.items.map((i) => i.id));

// Short aliases (the design's own ids) land on the canonical slug.
const SECTION_ALIASES: Record<string, SectionId> = {
  engineer: "race-engineer",
  notify: "notifications",
  diagnostics: "health",
  config: "advanced",
};

export function resolveSection(section: string | null | undefined): SectionId {
  if (!section) return "connection";
  if ((SECTION_IDS as string[]).includes(section)) return section as SectionId;
  return SECTION_ALIASES[section] ?? "connection";
}

/** The rail's groups with only the items matching `query` (label or keywords). */
export function filterSections(query: string, t: Translate = translate) {
  const q = query.trim().toLowerCase();
  return SECTION_GROUPS.map((g) => ({
    group: g.group,
    items: g.items.filter((i) => !q || `${t(i.label)} ${i.keywords}`.toLowerCase().includes(q)),
  })).filter((g) => g.items.length > 0);
}

// Every setting the page buffers, with the words the pending bar uses.
export const SETTING_LABELS = {
  source: "settings.label.source",
  ps_ip: "settings.label.ps_ip",
  packet_format: "settings.label.packet_format",
  sync_enabled: "settings.label.sync_enabled",
  sync_tracks: "settings.label.sync_tracks",
  sync_sessions: "settings.label.sync_sessions",
  sync_live: "settings.label.sync_live",
  race_engineer: "settings.label.race_engineer",
  race_engineer_verbosity: "settings.label.race_engineer_verbosity",
  race_engineer_units: "settings.label.race_engineer_units",
  race_engineer_sector_voice_enabled: "settings.label.race_engineer_sector_voice_enabled",
  race_engineer_sector_voice_decimals: "settings.label.race_engineer_sector_voice_decimals",
  race_engineer_sector_voice_rounding: "settings.label.race_engineer_sector_voice_rounding",
  race_engineer_sector_voice_content: "settings.label.race_engineer_sector_voice_content",
  race_engineer_sector_voice_comparison: "settings.label.race_engineer_sector_voice_comparison",
  race_engineer_categories: "settings.label.race_engineer_categories",
  webhook_url: "settings.label.webhook_url",
  webhook_events: "settings.label.webhook_events",
  log_level: "settings.label.log_level",
} as const satisfies Partial<Record<keyof AdminSettings, MessageKey>>;

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
