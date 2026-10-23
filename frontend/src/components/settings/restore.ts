// Settings › Data › Restore (#161): the parts an uploaded backup holds, which
// are ticked to begin with, and what each would do to this install.

import type { RestoreItem, RestoreReport, RestoreSetting, RestoreUpload } from "@/lib/types";
import { plural } from "./backup";
import { mb } from "./parts";

export interface RestorePartInfo {
  id: RestoreItem;
  label: string;
}

// In the order the server applies them.
export const RESTORE_PARTS: RestorePartInfo[] = [
  { id: "data", label: "Data" },
  { id: "surveys", label: "Surveys" },
  { id: "source_id", label: "Install identity" },
  { id: "config", label: "Config" },
  { id: "layouts", label: "Dash/OBS" },
  { id: "tokens", label: "Tokens" },
];

const TOKEN_NAMES: Record<string, string> = { webhook_url: "webhook URL", sync_token: "sync token" };

function names(keys: string[]): string {
  return keys.map((k) => TOKEN_NAMES[k] ?? k).join(" and ");
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
      return `${plural(p.data.sessions ?? 0, "session")} · ${plural(p.data.laps ?? 0, "lap")} · ${mb(p.data.bytes ?? 0)}`;
    case "surveys": {
      const s = p.surveys;
      const bits = [plural(s.bundles ?? 0, "circuit")];
      if (s.corrections) bits.push(plural(s.corrections, "correction"));
      if (s.logs) bits.push(plural(s.logs, "survey log"));
      if (s.car_widths) bits.push("car widths");
      return `${bits.join(" · ")} · ${mb(s.bytes ?? 0)}. Merged with what is here`;
    }
    case "source_id":
      return "Who made the survey votes in the backup. For a machine that replaces the one it came from";
    case "config": {
      const all = p.config.settings ?? [];
      const machine = all.filter((s) => s.machine).length;
      const shared = all.length - machine;
      const bits = [plural(shared, "saved setting")];
      if (machine) bits.push(`${machine} for the machine, below`);
      if (p.config.skipped?.length) bits.push(`${p.config.skipped.length} this version cannot take`);
      return bits.join(" · ");
    }
    case "layouts": {
      const n = p.layouts.layouts?.length ?? 0;
      const skipped = p.layouts.skipped?.length ?? 0;
      return skipped ? `${plural(n, "layout")} · ${skipped} this version cannot take` : plural(n, "layout");
    }
    case "tokens": {
      const keys = p.tokens.keys ?? [];
      return keys.length ? `The ${names(keys)}` : "No credentials";
    }
  }
}

/** What ticking a part does to what is here, beyond adding to it: shown
 * under the part while it is ticked. Empty when it only adds. */
export function restoreWarning(id: RestoreItem, up: RestoreUpload): string {
  const here = up.here;
  switch (id) {
    case "data":
      return hasRecordings(up)
        ? `Replaces the ${plural(here.sessions, "session")} and ${plural(here.laps, "lap")} here, which are kept beside the database as gt7.db.before-restore-<time>.`
        : "";
    case "source_id":
      return hasRecordings(up)
        ? "This install has recordings of its own: its survey votes and the backup's would count as one source."
        : "";
    case "layouts": {
      const replaced = (up.parts.layouts.layouts ?? []).filter((l) => l.replaces).map((l) => l.name);
      return replaced.length ? `Replaces ${replaced.join(", ")} here.` : "";
    }
    case "tokens": {
      const replaced = (up.parts.tokens.keys ?? []).filter((k) => here.tokens.includes(k));
      return replaced.length ? `Replaces the ${names(replaced)} saved here.` : "";
    }
    default:
      return "";
  }
}

/** A machine setting's value as a person reads it. */
export function settingValue(s: RestoreSetting): string {
  if (Array.isArray(s.value)) return s.value.join(", ") || "none";
  if (typeof s.value === "boolean") return s.value ? "on" : "off";
  return s.value === "" ? "empty" : String(s.value);
}

/** The toast once a restore is done. */
export function restoreSummary(report: RestoreReport): string {
  const bits: string[] = [];
  if (report.data) bits.push(plural(report.data.sessions, "session"));
  if (report.surveys) bits.push(plural(report.surveys.bundles.length, "circuit"));
  if (report.source_id?.source_id) bits.push("the install identity");
  if (report.config) bits.push(plural(report.config.applied.length, "setting"));
  if (report.layouts) bits.push(plural(report.layouts.created.length + report.layouts.replaced.length, "layout"));
  if (report.tokens?.applied.length) bits.push(names(report.tokens.applied));
  return bits.length ? `Restored ${bits.join(", ")}` : "Restored";
}

/** What did not go back, one line each: shown after the restore, so a
 * skipped setting or a bundle that did not validate is not lost in a toast. */
export function restoreNotes(report: RestoreReport): string[] {
  const notes: string[] = [];
  if (report.data) {
    notes.push(`The recordings it replaced are kept as ${report.data.kept}; delete that file once you are happy.`);
    if (report.data.upgraded_from) notes.push(`Its database was upgraded from ${report.data.upgraded_from}.`);
  }
  for (const f of report.surveys?.failed ?? []) notes.push(`${f.file}: ${f.error}`);
  if (report.source_id?.error) notes.push(`Install identity: ${report.source_id.error}`);
  for (const s of report.config?.skipped ?? []) notes.push(`${s.key}: ${s.reason}`);
  if (report.config?.left.length) notes.push(`Left as they are here: ${report.config.left.join(", ")}.`);
  for (const s of report.layouts?.skipped ?? []) notes.push(`Layout ${s.name || "(unnamed)"}: ${s.reason}`);
  for (const s of report.tokens?.skipped ?? []) notes.push(`${TOKEN_NAMES[s.key] ?? s.key}: ${s.reason}`);
  if (report.rejudging.length) notes.push(`Re-judging the laps on ${plural(report.rejudging.length, "circuit")} in the background.`);
  return notes;
}
