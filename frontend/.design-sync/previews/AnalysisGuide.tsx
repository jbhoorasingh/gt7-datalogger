// The Analysis view's guide, exactly as its toolbar opens it: a reading-sized
// dialog over the page, Features and Channels behind a segmented control, and
// the channel stack the view is currently charting marked in the list.
//
// The dialog portals to <body>, so the cell paints the page dark rather than
// wrapping the content — see the note in NOTES.md.

import { AnalysisGuide, DEFAULT_CHANNEL_KEYS } from "gt7-datalogger-frontend";
import { useEffect, useState } from "react";
import { DarkPage } from "../preview-shell";

// What the view opens with: lib/channels' DEFAULT_CHANNEL_KEYS.
const CHARTED = [...DEFAULT_CHANNEL_KEYS];

function Guide({ tab, query }: { tab: "features" | "channels"; query?: string }) {
  // The tab is the parent's state in the product (the "?" button opens
  // Features, the picker's "What are these?" opens Channels), so the cell
  // holds it too and the control stays live.
  const [current, setCurrent] = useState(tab);

  // The search box keeps its own state and the guide clears it on open, so a
  // searched cell drives the field the way a keystroke would, after mount.
  useEffect(() => {
    if (!query) return;
    const frame = requestAnimationFrame(() => {
      const input = document.querySelector<HTMLInputElement>(
        'input[aria-label="Search the guide"]',
      );
      if (!input) return;
      const setValue = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        "value",
      )?.set;
      setValue?.call(input, query);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    return () => cancelAnimationFrame(frame);
  }, [query]);

  return (
    <>
      <DarkPage />
      <AnalysisGuide
        open
        tab={current}
        onTabChange={setCurrent}
        charted={CHARTED}
        onClose={() => {}}
      />
    </>
  );
}

export function Features() {
  // The toolbar's "?" — what every feature of the view is, in a sentence.
  return <Guide tab="features" />;
}

export function Channels() {
  // The channel list, grouped as the picker groups it. The nine default
  // channels carry the "charted" badge; the ones a recording has to have a
  // channel for carry what they need.
  return <Guide tab="channels" />;
}

export function Searched() {
  // Searching narrows both tabs at once and the counts in the control say so:
  // a driver chasing a lockup types the word rather than reading the list.
  return <Guide tab="channels" query="brake" />;
}
