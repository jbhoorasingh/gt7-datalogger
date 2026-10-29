// Settings › Data › Restore (#161): the parts an uploaded backup holds, which
// are ticked to begin with, and what each would do to this install.

import { currentLanguage, t, type MessageKey } from "@/i18n";
import type { RestoreItem, RestoreReport, RestoreSetting, RestoreUpload } from "@/lib/types";
import { mb } from "./parts";

export interface RestorePartInfo {
  id: RestoreItem;
  label: MessageKey;
}

// In the order the server applies them.
export const RESTORE_PARTS: RestorePartInfo[] = [
  { id: "data", label: "backup.part.data" },
  { id: "surveys", label: "backup.part.surveys" },
  { id: "source_id", label: "restore.part.sourceId" },
  { id: "config", label: "backup.part.config" },
  { id: "layouts", label: "backup.part.layouts" },
  { id: "tokens", label: "backup.part.tokens" },
];

const TOKEN_NAMES: Record<string, MessageKey> = {
  webhook_url: "restore.token.webhook_url",
  sync_token: "restore.token.sync_token",
};

function tokenName(key: string): string {
  const name = TOKEN_NAMES[key];
  return name ? t(name) : key;
}

// "a, b, c" the way the interface language lists things.
const list = (items: string[]) =>
  new Intl.ListFormat(currentLanguage(), { type: "conjunction", style: "narrow" }).format(items);

/** The credentials a backup holds, in the order the server listed them. */
function heldTokens(keys: string[]): string {
  const hook = keys.includes("webhook_url");
  const sync = keys.includes("sync_token");
  if (hook && sync) {
    return keys.indexOf("sync_token") < keys.indexOf("webhook_url")
      ? t("backup.tokens.heldSyncHook")
      : t("backup.tokens.heldHookSync");
  }
  if (hook) return t("backup.tokens.heldHook");
  if (sync) return t("backup.tokens.heldSync");
  return t("restore.detail.noTokens");
}

/** The parts to list: the ones the backup holds. */
export function heldParts(up: RestoreUpload): RestorePartInfo[] {
  return RESTORE_PARTS.filter((p) => up.parts[p.id].available);
}

/** Whether a part can be ticked: the backup holds it and this install can take it. */
export function restorable(id: RestoreItem, up: RestoreUpload): boolean {
  const part = up.parts[id];
  return part.available && !part.refused;
}

/** Whether this install has recorded anything: a lap, since a session opens
 * at the first packet and one that never gets a lap is dropped. */
export function hasRecordings(up: RestoreUpload): boolean {
  return up.here.laps > 0;
}

/** The parts ticked when a backup is first listed: everything it holds that
 * this install can take, but two. The install identity only when this install
 * has no recordings yet — a move to new hardware wants it, while a second
 * install that keeps its own would then count as the same source in the
 * survey votes. And the tokens only when none are saved here to replace. */
export function defaultItems(up: RestoreUpload): RestoreItem[] {
  return RESTORE_PARTS.map((p) => p.id).filter((id) => {
    if (!restorable(id, up)) return false;
    if (id === "source_id") return !hasRecordings(up);
    if (id === "tokens") return up.here.tokens.length === 0;
    return true;
  });
}

/** The Config part's per-machine settings: listed under it, none ticked to begin with. */
export function machineSettings(up: RestoreUpload): RestoreSetting[] {
  return (up.parts.config.settings ?? []).filter((s) => s.machine);
}

/** What the restore asks for: the ticked parts this install can take, and the
 * machine settings ticked under Config, which mean nothing without it. */
export function restoreRequest(
  selected: ReadonlySet<RestoreItem>,
  machine: ReadonlySet<string>,
  up: RestoreUpload,
): { items: RestoreItem[]; machineSettings: string[] } {
  const items = RESTORE_PARTS.map((p) => p.id).filter((id) => selected.has(id) && restorable(id, up));
  const keys = items.includes("config")
    ? machineSettings(up)
        .map((s) => s.key)
        .filter((k) => machine.has(k))
    : [];
  return { items, machineSettings: keys };
}

/** What a part holds, in a few words. */
export function restoreDetail(id: RestoreItem, up: RestoreUpload): string {
  const p = up.parts;
  switch (id) {
    case "data":
      return [
        t("count.sessions", { count: p.data.sessions ?? 0 }),
        t("count.laps", { count: p.data.laps ?? 0 }),
        mb(p.data.bytes ?? 0),
      ].join(" · ");
    case "surveys": {
      const s = p.surveys;
      const bits = [t("backup.count.circuits", { count: s.bundles ?? 0 })];
      if (s.corrections) bits.push(t("backup.count.corrections", { count: s.corrections }));
      if (s.logs) bits.push(t("backup.count.logs", { count: s.logs }));
      if (s.car_widths) bits.push(t("restore.detail.carWidths"));
      bits.push(mb(s.bytes ?? 0));
      return t("restore.detail.surveys", { held: bits.join(" · ") });
    }
    case "source_id":
      return t("restore.detail.sourceId");
    case "config": {
      const all = p.config.settings ?? [];
      const machine = all.filter((s) => s.machine).length;
      const shared = all.length - machine;
      const bits = [t("restore.count.savedSettings", { count: shared })];
      if (machine) bits.push(t("restore.detail.machine", { count: machine }));
      if (p.config.skipped?.length) bits.push(t("restore.detail.skipped", { count: p.config.skipped.length }));
      return bits.join(" · ");
    }
    case "layouts": {
      const n = t("backup.count.layouts", { count: p.layouts.layouts?.length ?? 0 });
      const skipped = p.layouts.skipped?.length ?? 0;
      return skipped ? `${n} · ${t("restore.detail.skipped", { count: skipped })}` : n;
    }
    case "tokens":
      return heldTokens(p.tokens.keys ?? []);
  }
}

/** What ticking a part does to what is here, beyond adding to it: shown
 * under the part while it is ticked. Empty when it only adds. */
export function restoreWarning(id: RestoreItem, up: RestoreUpload): string {
  const here = up.here;
  switch (id) {
    case "data":
      return hasRecordings(up)
        ? t("restore.warn.data", {
            sessions: t("count.sessions", { count: here.sessions }),
            laps: t("count.laps", { count: here.laps }),
          })
        : "";
    case "source_id":
      return hasRecordings(up) ? t("restore.warn.sourceId") : "";
    case "layouts": {
      const replaced = (up.parts.layouts.layouts ?? []).filter((l) => l.replaces).map((l) => l.name);
      return replaced.length ? t("restore.warn.layouts", { names: list(replaced) }) : "";
    }
    case "tokens": {
      const replaced = (up.parts.tokens.keys ?? []).filter((k) => here.tokens.includes(k));
      const hook = replaced.includes("webhook_url");
      const sync = replaced.includes("sync_token");
      if (hook && sync) return t("restore.warn.tokensBoth");
      if (hook) return t("restore.warn.tokensHook");
      return sync ? t("restore.warn.tokensSync") : "";
    }
    default:
      return "";
  }
}

/** A machine setting's value as a person reads it. */
export function settingValue(s: RestoreSetting): string {
  if (Array.isArray(s.value)) return s.value.length ? list(s.value) : t("restore.value.none");
  if (typeof s.value === "boolean") return s.value ? t("common.on") : t("common.off");
  return s.value === "" ? t("restore.value.empty") : String(s.value);
}

/** The toast once a restore is done. */
export function restoreSummary(report: RestoreReport): string {
  const bits: string[] = [];
  if (report.data) bits.push(t("count.sessions", { count: report.data.sessions }));
  if (report.surveys) bits.push(t("backup.count.circuits", { count: report.surveys.bundles.length }));
  if (report.source_id?.source_id) bits.push(t("restore.summary.sourceId"));
  if (report.config) bits.push(t("restore.count.settings", { count: report.config.applied.length }));
  if (report.layouts) {
    bits.push(t("backup.count.layouts", { count: report.layouts.created.length + report.layouts.replaced.length }));
  }
  for (const key of report.tokens?.applied ?? []) bits.push(tokenName(key));
  return bits.length ? t("restore.summary", { parts: list(bits) }) : t("restore.summaryEmpty");
}

/** What did not go back, one line each: shown after the restore, so a
 * skipped setting or a bundle that did not validate is not lost in a toast. */
export function restoreNotes(report: RestoreReport): string[] {
  const notes: string[] = [];
  if (report.data) {
    notes.push(t("restore.note.kept", { file: report.data.kept }));
    if (report.data.upgraded_from) notes.push(t("restore.note.upgraded", { revision: report.data.upgraded_from }));
  }
  // The reasons are the server's words, in English like any API error.
  for (const f of report.surveys?.failed ?? []) notes.push(`${f.file}: ${f.error}`);
  if (report.source_id?.error) notes.push(`${t("restore.part.sourceId")}: ${report.source_id.error}`);
  for (const s of report.config?.skipped ?? []) notes.push(`${s.key}: ${s.reason}`);
  if (report.config?.left.length) notes.push(t("restore.note.left", { keys: list(report.config.left) }));
  for (const s of report.layouts?.skipped ?? []) {
    notes.push(t("restore.note.layout", { name: s.name || t("restore.note.unnamed"), reason: s.reason }));
  }
  for (const s of report.tokens?.skipped ?? []) notes.push(`${tokenName(s.key)}: ${s.reason}`);
  if (report.rejudging.length) notes.push(t("restore.note.rejudging", { count: report.rejudging.length }));
  return notes;
}
