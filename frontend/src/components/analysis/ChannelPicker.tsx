// Channel picker for the stacked charts: a wide grouped popover, three
// columns of toggles. The selection is persisted (localStorage) and mirrored
// into the analysis URL by the parent, so a shared deep link reproduces the
// same panel set.

import { useEffect, useRef, useState } from "react";
import { CHANNELS, DEFAULT_CHANNEL_KEYS, type ChannelGroup } from "@/lib/channels";

const GROUPS: ChannelGroup[] = ["Driving", "Engine", "Chassis", "Tires & wheels", "Race"];

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
    onChange(selected.includes(key) ? selected.filter((k) => k !== key) : [...selected, key]);
  }

  return (
    <div ref={root} className="relative">
      <button
        className={`rounded border px-2.5 py-1 text-[11.5px] transition-colors ${
          open
            ? "border-accent bg-accent/12 text-accent-300"
            : "border-edge text-ink-muted hover:border-accent"
        }`}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        title="Choose which telemetry channels are charted"
      >
        Channels · {selected.length}
      </button>
      {open && (
        // Anchored left, but flipped to hug the right edge on viewports too
        // narrow for 620px so the popover never runs off-screen.
        <div className="elevated absolute left-0 top-[calc(100%+6px)] z-50 max-h-[70vh] w-[620px] max-w-[calc(100vw-2rem)] overflow-y-auto rounded-panel bg-surface px-4 py-3.5">
          <div className="grid grid-cols-2 gap-3.5 sm:grid-cols-3">
            {GROUPS.map((group) => {
              const items = CHANNELS.filter((c) => c.group === group);
              if (items.length === 0) return null;
              return (
                <div key={group}>
                  <div className="mb-1.5 text-[9.5px] font-semibold uppercase tracking-[0.12em] text-ink-faint">
                    {group}
                  </div>
                  <div className="flex flex-col gap-1">
                    {items.map((c) => {
                      const on = selected.includes(c.key);
                      return (
                        <button
                          key={c.key}
                          onClick={() => toggle(c.key)}
                          aria-pressed={on}
                          className={`flex items-center py-0.5 text-left text-[11.5px] transition-colors ${
                            on ? "text-ink" : "text-ink-faint hover:text-ink"
                          }`}
                        >
                          <span
                            className={`mr-2 inline-block h-[11px] w-[11px] shrink-0 rounded-[3px] border ${
                              on ? "border-accent bg-accent/30" : "border-ink-ghost"
                            }`}
                          />
                          {c.title}
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-divider pt-2.5">
            <button
              className="btn px-2.5 py-[3px] hover:border-accent hover:text-accent"
              onClick={() => onChange([...DEFAULT_CHANNEL_KEYS])}
            >
              Default · {DEFAULT_CHANNEL_KEYS.length}
            </button>
            <button
              className="btn px-2.5 py-[3px] hover:border-accent hover:text-accent"
              onClick={() => onChange(CHANNELS.map((c) => c.key))}
            >
              All · {CHANNELS.length}
            </button>
            <span className="text-[10px] text-ink-faint">
              persists to the URL — a shared link reproduces the exact view
            </span>
            <button
              className="btn btn-primary ml-auto px-3 py-[3px]"
              onClick={() => setOpen(false)}
            >
              Done
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
