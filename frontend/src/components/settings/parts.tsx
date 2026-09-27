// Building blocks every Settings section shares: the panel with its header
// block, the label | control setting row, and the status dot.

import type { AdminSettingsPatch } from "@/lib/api";
import type { AdminSettings } from "@/lib/types";
import type { SettingsEdits } from "./model";

// What every section receives from SettingsView.
export interface SectionProps {
  /** The server's copy. */
  saved: AdminSettings;
  /** The server's copy with this browser's pending edits on top. */
  draft: AdminSettings;
  edit: (patch: SettingsEdits) => void;
  busy: string | null;
  /** Run an immediate action (Test, Restart, Compact…) with a toast either way. */
  run: (label: string, fn: () => Promise<unknown>, done?: (r: unknown) => string) => Promise<void>;
  /** Apply a patch now, bypassing the pending bar (sync Connect/Disconnect). */
  applyNow: (patch: AdminSettingsPatch, label: string) => Promise<AdminSettings | null>;
}

export type Tone = "good" | "warn" | "bad" | "accent" | "faint" | "ink";

export const TONE_TEXT: Record<Tone, string> = {
  good: "text-throttle",
  warn: "text-warn",
  bad: "text-brake",
  accent: "text-accent",
  faint: "text-ink-faint",
  ink: "text-ink",
};

export const TONE_BG: Record<Tone, string> = {
  good: "bg-throttle",
  warn: "bg-warn",
  bad: "bg-brake",
  accent: "bg-accent",
  faint: "bg-ink-faint",
  ink: "bg-ink",
};

export function Dot({ tone, size = 6 }: { tone: Tone; size?: 5 | 6 | 7 }) {
  const cls = size === 5 ? "h-[5px] w-[5px]" : size === 7 ? "h-[7px] w-[7px]" : "h-1.5 w-1.5";
  return <span className={`${cls} shrink-0 rounded-full ${TONE_BG[tone]}`} />;
}

export function SectionPanel({
  title,
  description,
  actions,
  children,
}: {
  title: string;
  description: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="panel min-w-0">
      <div className="flex flex-wrap items-start gap-x-4 gap-y-2 px-[18px] pb-3 pt-3.5">
        <div className="min-w-[240px] flex-1">
          <div className="text-[15px] font-medium">{title}</div>
          <div className="mt-0.5 text-[11.5px] text-ink-dim">{description}</div>
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2.5">{actions}</div>}
      </div>
      <div className="rule" />
      {children}
    </div>
  );
}

export function SettingRow({
  label,
  help,
  last = false,
  htmlFor,
  children,
}: {
  label: string;
  help?: React.ReactNode;
  /** The last row of a group carries no rule under it. */
  last?: boolean;
  htmlFor?: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className={`grid grid-cols-1 gap-x-6 gap-y-2 px-[18px] py-3.5 sm:grid-cols-[minmax(0,240px)_minmax(0,1fr)] ${
        last ? "" : "rule-row"
      }`}
    >
      <div>
        {htmlFor ? (
          <label htmlFor={htmlFor} className="text-[13px]">
            {label}
          </label>
        ) : (
          <div className="text-[13px]">{label}</div>
        )}
        {help && <div className="mt-0.5 text-[11px] text-ink-dim">{help}</div>}
      </div>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

export const INPUT_BASE =
  "rounded-md border border-edge bg-panel-2 px-3 py-1.5 font-tabular text-ink placeholder:text-ink-ghost focus:border-accent focus:outline-none";

// A text field that takes the rest of its row.
export const INPUT_CLS = `${INPUT_BASE} min-w-0 flex-1 text-[13px]`;

export function mb(bytes: number): string {
  return `${(bytes / 1048576).toFixed(1)} MB`;
}
