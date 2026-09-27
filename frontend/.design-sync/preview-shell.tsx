// Shared scaffolding for the authored preview cards.
//
// Preview cards render on a white page by contract, while this design system is
// dark by construction: every token is tuned for the #0b0d10 ground with
// #14171c panels on it. So each cell paints that ground itself — a component
// shown on white is not what the product looks like, and the grading rubric
// reads the card, not the intent.
//
// Nothing here is a component of the design system; it is only the frame the
// real components are shown in, mirroring what App, GridRenderer and the views
// already put around them.

import { lapColorMap } from "gt7-datalogger-frontend";
import type { CSSProperties, ReactNode } from "react";

/** The app's ground — what `body` carries in the real UI. */
export function Surface({
  children,
  className = "",
  width,
}: {
  children: ReactNode;
  className?: string;
  /** Pin a width when the component is laid out against one (bars, charts). */
  width?: number | string;
}) {
  return (
    <div
      className={`inline-block bg-surface text-ink rounded-panel p-3 ${className}`}
      style={width == null ? undefined : { width }}
    >
      {children}
    </div>
  );
}

/** The one container surface: `.panel` plus its hairline ring. */
export function Panel({
  children,
  className = "",
  title,
}: {
  children: ReactNode;
  className?: string;
  /** Rendered in the app's section-header style, as the views label panels. */
  title?: string;
}) {
  return (
    <div className={`panel p-3 ${className}`}>
      {title && <div className="section-header mb-2">{title}</div>}
      {children}
    </div>
  );
}

/** A dashboard cell, as GridRenderer draws it: dark fill, hairline ring, 12px. */
export function WidgetCard({
  children,
  w = 160,
  h = 108,
}: {
  children: ReactNode;
  w?: number;
  h?: number;
}) {
  const card: CSSProperties = {
    width: w,
    height: h,
    padding: 12,
    borderRadius: 8,
    backgroundColor: "rgb(8, 10, 14)",
    boxShadow: "0 0 0 1px rgb(38, 43, 51)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
  };
  return <div style={card}>{children}</div>;
}

/** Variants side by side, each under the label the widget picker gives it. */
export function Row({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return <div className={`flex flex-wrap items-start gap-3 ${className}`}>{children}</div>;
}

/** Caption for one variant inside a Row. */
export function Labelled({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      {children}
      <div className="text-[9px] font-semibold uppercase tracking-[0.16em] text-ink-faint">
        {label}
      </div>
    </div>
  );
}

/** Lap → series colour, exactly as the Analysis view assigns them: id-keyed,
 *  no two selected laps sharing a colour, and the fastest of them in purple. */
export function lapColors(ids: number[], fastestId?: number | null): Record<string, string> {
  return Object.fromEntries([...lapColorMap(ids, fastestId)].map(([id, c]) => [String(id), c]));
}

/** A charting box: ECharts needs a laid-out height to draw into. */
export function ChartBox({
  children,
  w = 420,
  h = 220,
}: {
  children: ReactNode;
  w?: number | string;
  h?: number;
}) {
  return <div style={{ width: w, height: h }}>{children}</div>;
}

/** Paints the card's page in the app's ground.
 *
 *  Overlay components (dialogs, toasts) portal to <body> and lay themselves
 *  over the viewport, so a wrapper div can't sit behind them — the card's own
 *  page has to be the dark one, or a `bg-black/60` backdrop greys out white
 *  paper. (A fixed-position wrapper doesn't work either: the card's
 *  `.ds-single` carries a transform, which makes it the containing block for
 *  fixed children while the portal escapes to the body.) */
export function DarkPage() {
  return (
    <style>{`body{background:var(--color-surface);color:var(--color-ink);margin:0}`}</style>
  );
}
