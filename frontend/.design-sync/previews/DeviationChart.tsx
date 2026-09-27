// Speed consistency across the session's best laps: the median speed along the
// lap with the lap-to-lap deviation on its own axis underneath. A tall
// deviation is a corner the driver is not repeating.

import { DeviationChart, type Units } from "gt7-datalogger-frontend";
import { COMPARE, DEVIATION, REF_LAP } from "../fixtures/analysis";
import { ChartBox, Panel, Surface } from "../preview-shell";

const METRIC: Units = "metric";
const IMPERIAL: Units = "imperial";

// The hairpin at the end of the back straight — the corner the view zooms to
// when a corner-report row or a map marker is clicked.
const TURN_FIVE = COMPARE.laps[String(REF_LAP)].corners?.find((c) => c.n === 5);
const TURN_FIVE_RANGE: [number, number] = TURN_FIVE
  ? [TURN_FIVE.entry_dist, TURN_FIVE.exit_dist]
  : [1168, 1334];

export function BestLaps() {
  // The five quickest laps of the Tsukuba session: the median tops 200 km/h on
  // the straight and the deviation spikes at the corners the driver is least
  // repeatable through.
  return (
    <Surface width={470}>
      <Panel title={`Consistency — best ${DEVIATION.lap_ids.length} laps`}>
        <ChartBox w="100%" h={190}>
          <DeviationChart data={DEVIATION} units={METRIC} />
        </ChartBox>
      </Panel>
    </Surface>
  );
}

export function ZoomedToTurnFive() {
  // Zoomed with the rest of the view: the same chart over one corner's window,
  // where a couple of km/h of scatter is worth reading.
  return (
    <Surface width={470}>
      <Panel title={`Consistency — best ${DEVIATION.lap_ids.length} laps`}>
        <ChartBox w="100%" h={190}>
          <DeviationChart data={DEVIATION} units={METRIC} zoomRange={TURN_FIVE_RANGE} />
        </ChartBox>
      </Panel>
    </Surface>
  );
}

export function Imperial() {
  // Both axes follow the unit setting — median and deviation alike in mph.
  return (
    <Surface width={470}>
      <Panel title={`Consistency — best ${DEVIATION.lap_ids.length} laps`}>
        <ChartBox w="100%" h={190}>
          <DeviationChart data={DEVIATION} units={IMPERIAL} />
        </ChartBox>
      </Panel>
    </Surface>
  );
}
