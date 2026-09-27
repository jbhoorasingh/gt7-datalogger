// Corner Detail: the top-down car with a cell per wheel, scrubbed by the chart
// cursor. Tyre temperature is the cell fill, suspension travel the bar beside
// it, the reference lap the small figures and the dashed marks on the bars.
//
// Driven by COMPARE_FULL — the same three Tsukuba laps in the AE86 the rest of
// the analysis family uses, read with all 30 channels, so the per-wheel
// temperature, suspension travel and slip ratio here are recorded rather than
// derived. Three laps means two comparison candidates, so the focus chips are
// on: lap 4 is the focus and the reference (lap 2, the session's best) is the
// ghost throughout.

import { CornerDetail, type CornerLap } from "gt7-datalogger-frontend";
import { COMPARE_FULL } from "../fixtures/analysis-full";
import { Panel, Surface, lapColors } from "../preview-shell";

const REF = String(COMPARE_FULL.ref);
const IDS = Object.keys(COMPARE_FULL.laps);
const CORNERS = COMPARE_FULL.laps[REF].corners ?? [];
const COLORS = lapColors(IDS.map(Number), COMPARE_FULL.ref);
// As AnalysisView labels them: lap number, lap time, and the reference marked.
const LABELS: Record<string, string> = {
  "1063": "L2 · 0:58.963 (ref)",
  "1065": "L4 · 1:00.235",
  "1068": "L7 · 1:00.207",
};

// Exactly AnalysisView's `cornerLaps`, on the lap entries as they arrive.
const LAPS: CornerLap[] = IDS.map((id) => ({
  id,
  label: LABELS[id] ?? `Lap ${id}`,
  color: COLORS[id],
  isRef: id === REF,
  series: COMPARE_FULL.laps[id].series,
}));

function Card({ cursorDist }: { cursorDist: number | null }) {
  return (
    <Surface width={300}>
      <Panel title="Corner detail — cursor synced">
        <CornerDetail laps={LAPS} cursorDist={cursorDist} trackCorners={CORNERS} />
      </Panel>
    </Surface>
  );
}

export function BrakingIntoTheHairpin() {
  // 1190 m, hard into turn five: the right front down at 0.84 slip under full
  // brake, so that cell takes LOCK. Three of the four are 6-9 °C over what the
  // reference lap carried here and ring in brake red.
  return <Card cursorDist={1190} />;
}

export function WheelspinOnExit() {
  // 880 m, 88 % throttle out of turn three: both driven rears at 1.14 — SPIN
  // on each, and the left rear the hottest corner of the car at 87 °C.
  return <Card cursorDist={880} />;
}

export function RearAxleRunningHot() {
  // 1640 m, flat out at 210 km/h down the back straight: no lock, no spin,
  // just the thermal picture. The rear axle is 9.3 °C hotter than the front
  // and every corner is warmer than the reference was at the same place.
  return <Card cursorDist={1640} />;
}

export function NoCursor() {
  // Nothing hovered yet: the panel sits on the first sample and says how to
  // scrub it.
  return <Card cursorDist={null} />;
}
