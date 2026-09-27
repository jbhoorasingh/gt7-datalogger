// The Analysis toolbar's channel picker: the button that says how many
// channels are on the chart stack, and the grouped popover behind it.
//
// The popover is the component's own state, opened by a click — so each open
// cell clicks the trigger on mount, and reserves the height the popover needs
// so the app's ground sits behind it rather than white paper.

import { ChannelPicker, DEFAULT_CHANNEL_KEYS } from "gt7-datalogger-frontend";
import { useEffect, useRef, useState } from "react";
import { Surface } from "../preview-shell";

// The classic panel stack the Analysis view opens with, straight from
// lib/channels rather than repeated here.
const DEFAULTS = [...DEFAULT_CHANNEL_KEYS];

// A balance problem is read on the per-wheel channels, not the driving ones.
const BALANCE = [
  "speed",
  "brake",
  "body_slip",
  "tire_slip",
  "slip_front",
  "slip_rear",
  "tt_front",
  "tt_rear",
  "tt_balance",
  "sus_front",
  "sus_rear",
  "body_height",
];

function Picker({
  initial,
  open = true,
  selectAll = false,
}: {
  initial: string[];
  open?: boolean;
  /** Presses the popover's own "All" preset, so the selection is the real one. */
  selectAll?: boolean;
}) {
  const [selected, setSelected] = useState(initial);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    root.current?.querySelector<HTMLButtonElement>("button[aria-expanded]")?.click();
  }, [open]);

  useEffect(() => {
    if (!selectAll) return;
    const frame = requestAnimationFrame(() => {
      const buttons = [...(root.current?.querySelectorAll("button") ?? [])];
      (
        buttons.find((b) => b.textContent?.startsWith("All ·")) as HTMLButtonElement | undefined
      )?.click();
    });
    return () => cancelAnimationFrame(frame);
  }, [selectAll]);

  return (
    <Surface width={open ? 680 : 200}>
      <div ref={root} style={open ? { height: "calc(70vh + 44px)" } : undefined}>
        <ChannelPicker selected={selected} onChange={setSelected} onHelp={() => {}} />
      </div>
    </Surface>
  );
}

export function Default() {
  // What the Analysis view opens with: nine channels ticked across Driving,
  // Engine, Chassis and Tires.
  return <Picker initial={DEFAULTS} />;
}

export function TiresAndChassis() {
  // The selection for chasing a balance complaint — the Tires and Chassis
  // columns ticked, most of Driving off.
  return <Picker initial={BALANCE} />;
}

export function AllChannels() {
  // The "All" preset: every channel the app knows, ticked.
  return <Picker initial={DEFAULTS} selectAll />;
}

export function Closed() {
  // The resting state in the toolbar — a count, not a list.
  return <Picker initial={DEFAULTS} open={false} />;
}
