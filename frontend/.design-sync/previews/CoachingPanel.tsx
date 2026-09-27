// The race engineer's post-lap notes, as the Analysis view's bottom rack
// shows them: the findings replayed from the stored session, newest lap first,
// with the ones that name a corner clickable to zoom every panel to it.

import { CoachingPanel } from "gt7-datalogger-frontend";
import { COACHING, COMPARE, REF_LAP, SELECTED } from "../fixtures/analysis";
import { Panel, Surface, lapColors } from "../preview-shell";

// The reference lap carries the corner windows a finding's corner number is
// resolved against — exactly what AnalysisView passes as `refEntry.corners`.
const CORNERS = COMPARE.laps[String(REF_LAP)].corners ?? [];

const SESSION_COLORS = lapColors([...SELECTED], REF_LAP);
// Lap 6 alone: the colours the view would assign with it against the best.
const LATEST_COLORS = lapColors([REF_LAP, 1067], REF_LAP);

export function Session() {
  // Five laps of findings from one Tsukuba session — bottoming at turns two
  // and seven, time lost in turn two, a late brake into turn five. The two
  // laps that are not in the comparison are dimmed and carry no colour dot.
  return (
    <Surface width={430}>
      <Panel title="Race engineer — post-lap notes">
        <CoachingPanel
          notes={COACHING.laps}
          selected={SELECTED}
          lapColors={SESSION_COLORS}
          corners={CORNERS}
          onZoom={() => {}}
        />
      </Panel>
    </Surface>
  );
}

export function LatestLap() {
  // What the panel looks like the moment a lap ends: one lap, one finding.
  return (
    <Surface width={430}>
      <Panel title="Race engineer — post-lap notes">
        <CoachingPanel
          notes={COACHING.laps.filter((l) => l.lap_id === 1067)}
          selected={[1067]}
          lapColors={LATEST_COLORS}
          corners={CORNERS}
          onZoom={() => {}}
        />
      </Panel>
    </Surface>
  );
}

export function NoneInComparison() {
  // The notes are the session's, the comparison is somebody's choice of three
  // laps: with none of these laps charted every group dims and loses its dot.
  return (
    <Surface width={430}>
      <Panel title="Race engineer — post-lap notes">
        <CoachingPanel
          notes={COACHING.laps}
          selected={[]}
          lapColors={{}}
          corners={CORNERS}
          onZoom={() => {}}
        />
      </Panel>
    </Surface>
  );
}
