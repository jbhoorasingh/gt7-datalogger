// The chart stack: the time delta against the reference on top, then one
// strip per chosen channel, all sharing one distance axis, one cursor and one
// zoom. Hovering any strip reports the distance up to the view, which is what
// keeps the race-line dot, the corner panels and the playback transport on the
// same point of the lap.
//
// The canvas sizes itself from the number of strips — 110 px each — so a cell
// only has to give it a width. The channel sets come from lib/channels rather
// than hand-written literals: `DEFAULT_CHANNEL_KEYS` is the stack the Analysis
// view opens with, and `COMPARE_FULL` is the same three laps carrying every
// channel the recording holds.

import {
  CHANNEL_BY_KEY,
  DEFAULT_CHANNEL_KEYS,
  StackedCharts,
  type ChannelDef,
  type Units,
} from "gt7-datalogger-frontend";
import { COMPARE, REF_LAP, SELECTED } from "../fixtures/analysis";
import { COMPARE_FULL } from "../fixtures/analysis-full";
import { Panel, Surface, lapColors } from "../preview-shell";

const METRIC: Units = "metric";
const channels = (keys: string[]): ChannelDef[] =>
  keys.map((k) => CHANNEL_BY_KEY[k]).filter(Boolean);

// The stack the Analysis view opens with, straight from lib/channels.
const FULL_STACK = channels([...DEFAULT_CHANNEL_KEYS]);
// The first four of it: what a braking point and a corner are read on, and
// what the picker is usually trimmed to.
const DRIVING = channels(DEFAULT_CHANNEL_KEYS.slice(0, 3).concat("gear"));
// The balance stack, which needs the per-wheel columns.
const BALANCE = channels([
  "speed",
  "brake",
  "slip_front",
  "slip_rear",
  "tt_front",
  "tt_rear",
  "tt_balance",
  "sus_front",
  "sus_rear",
]);
// Steering beside yaw rate and the two accelerometers: plenty of lock with
// little rotation is understeer, rotation with little lock is oversteer.
const ROTATION = channels(["speed", "steer", "yaw_rate", "acc_lat", "acc_long"]);

const COLORS = lapColors([...SELECTED], REF_LAP);
const LABELS: Record<string, string> = {
  "1063": "L2 · 0:58.963 (ref)",
  "1065": "L4 · 1:00.235",
  "1068": "L7 · 1:00.207",
};

const REF_REPORT = COMPARE.laps[String(REF_LAP)].corner_report ?? [];
const TURN_FIVE = REF_REPORT.find((r) => r.n === 5);
// Mid-way through the braking zone for the hairpin: brake at 100%, speed
// falling, the delta trace pulling away from zero.
const IN_THE_BRAKING_ZONE =
  TURN_FIVE?.brake_on != null && TURN_FIVE.brake_off != null
    ? (TURN_FIVE.brake_on + TURN_FIVE.brake_off) / 2
    : 1152;
const CORNERS = COMPARE.laps[String(REF_LAP)].corners ?? [];
const FIVE = CORNERS.find((c) => c.n === 5);
const TURN_FIVE_RANGE: [number, number] = FIVE
  ? [FIVE.entry_dist - 80, FIVE.exit_dist + 40]
  : [1088, 1374];

export function Session() {
  // The stack the Analysis view opens with: the delta against the reference
  // plus all nine classic channels, three Tsukuba laps in an AE86 over the
  // whole 2 km lap. The delta panel at the top is where the lap is being won
  // and lost; the nine strips under it say why.
  return (
    <Surface width={720}>
      <Panel>
        <StackedCharts
          data={COMPARE}
          lapLabels={LABELS}
          lapColors={COLORS}
          units={METRIC}
          channels={FULL_STACK}
          zoomRange={null}
          cursorDist={null}
          refLapId={String(REF_LAP)}
        />
      </Panel>
    </Surface>
  );
}

export function AtTheCursor() {
  // The shared playhead parked in the braking zone for the hairpin, on a stack
  // trimmed to the four channels a braking point is read on. Each strip prints
  // the reference lap's value at the cursor beside its title, so the whole
  // stack reads as one moment of the lap.
  return (
    <Surface width={720}>
      <Panel>
        <StackedCharts
          data={COMPARE}
          lapLabels={LABELS}
          lapColors={COLORS}
          units={METRIC}
          channels={DRIVING}
          zoomRange={null}
          cursorDist={IN_THE_BRAKING_ZONE}
          refLapId={String(REF_LAP)}
        />
      </Panel>
    </Surface>
  );
}

export function ZoomedToACorner() {
  // The same four strips over one corner's window, zoomed with every other
  // panel on the page. At this scale the three laps' brake points, minimum
  // speeds and gear choices separate into three different corners.
  return (
    <Surface width={720}>
      <Panel>
        <StackedCharts
          data={COMPARE}
          lapLabels={LABELS}
          lapColors={COLORS}
          units={METRIC}
          channels={DRIVING}
          zoomRange={TURN_FIVE_RANGE}
          cursorDist={null}
          refLapId={String(REF_LAP)}
        />
      </Panel>
    </Surface>
  );
}

export function SteeringAndRotation() {
  // Channels the classic stack leaves out, on the same three laps: the wheel's
  // own rotation beside the yaw rate it produced, with both accelerometers
  // under them. Plenty of lock and little rotation is understeer; rotation
  // with little lock is oversteer — the comparison only reads side by side.
  return (
    <Surface width={720}>
      <Panel>
        <StackedCharts
          data={COMPARE_FULL}
          lapLabels={LABELS}
          lapColors={COLORS}
          units={METRIC}
          channels={ROTATION}
          zoomRange={null}
          cursorDist={null}
          refLapId={String(REF_LAP)}
        />
      </Panel>
    </Surface>
  );
}

export function PerWheelChannels() {
  // The balance stack: front and rear slip ratio, front and rear tyre
  // temperature with the balance between them, and suspension travel at each
  // axle, over the same three laps. This is the read for where the car is
  // pushing, where it is loose and what the tyres did about it.
  return (
    <Surface width={720}>
      <Panel>
        <StackedCharts
          data={COMPARE_FULL}
          lapLabels={LABELS}
          lapColors={COLORS}
          units={METRIC}
          channels={BALANCE}
          zoomRange={null}
          cursorDist={null}
          refLapId={String(REF_LAP)}
        />
      </Panel>
    </Surface>
  );
}
