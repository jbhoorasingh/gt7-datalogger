// What every callout category actually says, folded away under the category
// checkboxes in the Race Engineer panel. Reference material, so it ships closed
// — the cells that show its contents open the <details> after mount, the way a
// click would (same convention as the AnalysisGuide cells).
//
// Its two props come from the panel around it: this browser's verbosity, and the
// categories the server will ever emit (null until the status arrives). Both
// dim rows, for different reasons, and the difference is the point of the panel.

import { CalloutReference } from "gt7-datalogger-frontend";
import type { CalloutCategory, Verbosity } from "gt7-datalogger-frontend";
import { useEffect, useRef } from "react";
import { Surface } from "../preview-shell";

// What the server emits at `race` verbosity — no coaching, chassis or tires.
const SERVER_AT_RACE: CalloutCategory[] = [
  "system",
  "lap",
  "pace",
  "race",
  "position",
  "fuel",
  "strategy",
  "engine",
];

function Reference({
  verbosity,
  serverCategories = null,
  open = true,
  scroll = "top",
}: {
  verbosity: Verbosity;
  serverCategories?: CalloutCategory[] | null;
  open?: boolean;
  /** The categories that get dimmed are the last three, so the cells whose
   *  story is a dimmed row read the list where the driver would: at the end. */
  scroll?: "top" | "bottom";
}) {
  const host = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const box = host.current;
    const details = box?.querySelector("details");
    if (details) details.open = true;
    if (box && scroll === "bottom") box.scrollTop = box.scrollHeight;
  }, [open, scroll]);

  return (
    <Surface width={340}>
      {/* The block it sits in, inside the panel's Categories section. */}
      <div className="mb-1.5 text-[11px] text-ink-dim">Categories</div>
      {/* Capped inline: the compiled Tailwind only carries utilities src/ uses,
          and an arbitrary max-h-[…] of this size is not among them. The panel
          that hosts this list scrolls the same way (DashView's drawer). */}
      <div ref={host} className="overflow-y-auto" style={{ maxHeight: 560 }}>
        <CalloutReference verbosity={verbosity} serverCategories={serverCategories} />
      </div>
    </Surface>
  );
}

export function Folded() {
  // How it ships: one line under the checkboxes, nothing shouting.
  return <Reference verbosity="race" open={false} />;
}

export function AtCoachVerbosity() {
  // Open at the top: the catalogue itself, in the order the checkboxes are in.
  // Coach leaves every category in play, so nothing here is dimmed.
  return <Reference verbosity="coach" />;
}

export function AtRaceVerbosity() {
  // The default verbosity, read at the end of the list — tires, chassis and
  // coaching are still listed, greyed, each saying it is off at race verbosity.
  return <Reference verbosity="race" scroll="bottom" />;
}

export function ServerNarrowed() {
  // This browser asks for coach while the server is set to race: tires, chassis
  // and coaching will never be produced, so those say so in amber and point at
  // the Admin setting — a different fault from "off at this verbosity".
  return <Reference verbosity="coach" serverCategories={SERVER_AT_RACE} scroll="bottom" />;
}
