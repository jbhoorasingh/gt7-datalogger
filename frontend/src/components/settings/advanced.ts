// Settings › Advanced model: every setting the server has, edited as a
// buffer of new values plus a set of keys to hand back to the environment,
// sent as one PUT /api/admin/config.

import type { ConfigSetting, ConfigValue } from "@/lib/types";

export interface ConfigEdits {
  values: Record<string, ConfigValue>;
  reset: string[];
}

export const NO_EDITS: ConfigEdits = { values: {}, reset: [] };

/** The docs' markdown as one plain sentence: link text kept, marks dropped. */
export function plainDoc(md: string): string {
  return md
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/`([^`]+)`/g, "$1");
}

function same(a: ConfigValue | null, b: ConfigValue | null): boolean {
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((x) => b.includes(x));
  }
  return a === b;
}

/** Fold a new value in; one that matches the server again drops out. */
export function setValue(edits: ConfigEdits, row: ConfigSetting, value: ConfigValue): ConfigEdits {
  const values = { ...edits.values };
  // A secret has no value to compare against: typing one always counts.
  if (!row.secret && same(value, row.value)) delete values[row.key];
  else values[row.key] = value;
  return { values, reset: edits.reset.filter((k) => k !== row.key) };
}

/** Mark a saved setting for reset, dropping any value typed for it. */
export function resetKey(edits: ConfigEdits, key: string): ConfigEdits {
  const values = { ...edits.values };
  delete values[key];
  return { values, reset: edits.reset.includes(key) ? edits.reset : [...edits.reset, key] };
}

export function pendingCount(edits: ConfigEdits): number {
  return Object.keys(edits.values).length + edits.reset.length;
}

/** What the control shows: the pending value, else the server's. */
export function shownValue(row: ConfigSetting, edits: ConfigEdits): ConfigValue | null {
  if (row.key in edits.values) return edits.values[row.key];
  if (edits.reset.includes(row.key)) return row.secret ? "" : row.default;
  return row.secret ? "" : row.value;
}

/** A number field's text as a value, or null while it is not a number yet. */
export function parseNumber(row: ConfigSetting, text: string): number | null {
  if (text.trim() === "") return null;
  const n = row.kind === "int" ? Number.parseInt(text, 10) : Number.parseFloat(text);
  return Number.isFinite(n) ? n : null;
}

/** "next start" settings saved but not yet in effect. */
export function restartPending(rows: ConfigSetting[]): ConfigSetting[] {
  return rows.filter((r) => r.restart_pending);
}
