// The corner report card: where the lap is actually being lost, measured
// corner by corner through the reference lap's windows and sorted by the time
// lost. Every figure is the backend's — the brake point against the reference,
// the peak pedal and the length of the zone, entry / minimum / exit speed, the
// time through the corner and the delta.
//
// The table is wider than a grid cell; the view gives it the full width.

import { CornerReport, type ReportLap, type Units } from "gt7-datalogger-frontend";
import { useState } from "react";
import { COMPARE, REF_LAP } from "../fixtures/analysis";
import { Panel, Surface, lapColors } from "../preview-shell";

const IDS = Object.keys(COMPARE.laps);
const CORNERS = COMPARE.laps[String(REF_LAP)].corners ?? [];
const COLORS = lapColors(IDS.map(Number), REF_LAP);
const LABELS: Record<string, string> = {
  "1063": "L2 · 0:58.963 (ref)",
  "1065": "L4 · 1:00.235",
  "1068": "L7 · 1:00.207",
};

// Exactly AnalysisView's `reportLaps`: every compared lap that has a report.
const LAPS: ReportLap[] = IDS.map((id) => ({
  id,
  label: LABELS[id] ?? `Lap ${id}`,
  color: COLORS[id],
  isRef: id === String(REF_LAP),
  report: COMPARE.laps[id].corner_report ?? [],
})).filter((l) => l.report.length > 0);

const METRIC: Units = "metric";
const IMPERIAL: Units = "imperial";

function Card({
  laps,
  units = METRIC,
  initialFocus = null,
}: {
  laps: ReportLap[];
  units?: Units;
  initialFocus?: string | null;
}) {
  // The focused lap is the view's state, shared with the brake pins on the map.
  const [focusId, setFocusId] = useState<string | null>(initialFocus);
  return (
    <Surface width={700}>
      <Panel title="Corner report card">
        <CornerReport
          corners={CORNERS}
          laps={laps}
          units={units}
          focusId={focusId}
          onFocusChange={setFocusId}
          onZoom={() => {}}
          onHoverCorner={() => {}}
        />
      </Panel>
    </Surface>
  );
}

export function Session() {
  // Three Tsukuba laps, the best as reference: lap 4 against it, six corners
  // sorted by the time lost, and the total at the foot. This session has no
  // body-slip channel, so the two slip columns are left out rather than drawn
  // empty.
  return <Card laps={LAPS} />;
}

export function OtherLapFocused() {
  // The second chip picked: the same corners measured for lap 7, which loses
  // far less — the chips are how one comparison lap is swapped for another.
  return <Card laps={LAPS} initialFocus="1068" />;
}

export function ReferenceOnly() {
  // Only the best lap selected. The card still reads as that lap's own report
  // — speeds, braking and time per corner — with no delta to sort by and no
  // chips to pick between.
  return <Card laps={LAPS.filter((l) => l.isRef)} />;
}

export function Imperial() {
  // The same card with the speed unit set to mph.
  return <Card laps={LAPS} units={IMPERIAL} />;
}
