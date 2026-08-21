// Channel picker for the stacked charts: grouped checkbox popover.
// The selection is persisted (localStorage) and mirrored into the analysis
// URL by the parent, so a shared deep link reproduces the same panel set.

import { useEffect, useRef, useState } from "react";
import { CHANNELS, DEFAULT_CHANNEL_KEYS, type ChannelGroup } from "@/lib/channels";

const GROUPS: ChannelGroup[] = ["Driving", "Race", "Tires & wheels", "Chassis", "Engine"];

export function ChannelPicker({
  selected,
  onChange,
}: {
  selected: string[];
  onChange: (keys: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  function toggle(key: string) {
    onChange(
      selected.includes(key) ? selected.filter((k) => k !== key) : [...selected, key],
    );
  }

  return (
    <div ref={root} className="relative">
      <button
        className="btn"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        title="Choose which telemetry channels are charted"
      >
        Channels ({selected.length})
      </button>
      {open && (
        <div className="absolute left-0 top-full z-30 mt-1 max-h-96 w-64 overflow-y-auto rounded-lg border border-edge bg-panel p-2 shadow-xl shadow-black/40">
          {GROUPS.map((group) => (
            <div key={group} className="mb-2">
              <div className="px-1 pb-1 text-[10px] font-semibold uppercase tracking-widest text-ink-dim">
                {group}
              </div>
              {CHANNELS.filter((c) => c.group === group).map((c) => (
                <label
                  key={c.key}
                  className="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 text-xs hover:bg-panel-2"
                >
                  <input
                    type="checkbox"
                    checked={selected.includes(c.key)}
                    onChange={() => toggle(c.key)}
                    className="accent-[#38bdf8]"
                  />
                  {c.title}
                </label>
              ))}
            </div>
          ))}
          <div className="flex justify-between border-t border-edge pt-2">
            <button
              className="text-xs text-ink-dim hover:text-ink"
              onClick={() => onChange([...DEFAULT_CHANNEL_KEYS])}
            >
              Reset to default
            </button>
            <button className="text-xs text-accent" onClick={() => setOpen(false)}>
              Done
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
