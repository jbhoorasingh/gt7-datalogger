// The race line: the lap drawn where it was driven, coloured by what the
// pedals were doing — green on the throttle, red on the brakes, blue coasting
// — with the speed peaks and valleys pinned along it, the detected chassis
// events in each lap's own colour, the stretches a driver aid was intervening
// ringed, and a corner bar under the map that takes every other panel to a
// corner when one is picked.
//
// All of it is the Tsukuba session's own telemetry, including the aid bits
// behind the TCS rings and the brake points, which come from `brakeMarkers`
// in lib/mapLayers rather than a copy of it. `outline` is left null, which is
// this circuit's real state: it has not been surveyed in this database, so
// the map draws the driven lines alone.

import {
  AIDS_ASM,
  AIDS_TCS,
  RaceLineMap,
  brakeMarkers,
  cornerRange,
  hasAid,
  type MapLap,
  type MapLayers,
} from "gt7-datalogger-frontend";
import { COMPARE_FULL } from "../fixtures/analysis-full";
import { REF_LAP, SELECTED } from "../fixtures/analysis";
import { Panel, Surface, lapColors } from "../preview-shell";

const IDS = Object.keys(COMPARE_FULL.laps);
const COLORS = lapColors([...SELECTED], REF_LAP);
const LABELS: Record<string, string> = {
  "1063": "L2 · 0:58.963 (ref)",
  "1065": "L4 · 1:00.235",
  "1068": "L7 · 1:00.207",
};

// Exactly AnalysisView's `mapLaps`.
const LAPS: MapLap[] = IDS.map((id) => ({
  id,
  entry: COMPARE_FULL.laps[id],
  color: COLORS[id],
  label: LABELS[id] ?? `Lap ${id}`,
  isRef: id === String(REF_LAP),
}));

// Which toggles the view would even offer for this selection: these laps have
// events and traction control, and never touched stability management.
const HAS_TCS = LAPS.some((lap) => hasAid(lap.entry.series, AIDS_TCS));
const HAS_ASM = LAPS.some((lap) => hasAid(lap.entry.series, AIDS_ASM));

const NO_LAYERS: MapLayers = { events: false, tcs: false, asm: false };
const EVENTS: MapLayers = { ...NO_LAYERS, events: true };
const AIDS: MapLayers = { ...NO_LAYERS, tcs: HAS_TCS, asm: HAS_ASM };

const CORNERS = COMPARE_FULL.laps[String(REF_LAP)].corners ?? [];
// Turn 5, the hairpin at the end of the back straight — the corner the report
// card sorts to the top and the one the view zooms to when a row is clicked.
const TURN_FIVE = CORNERS.find((c) => c.n === 5) ?? CORNERS[0];
const TURN_FIVE_RANGE = cornerRange(TURN_FIVE);

// The reference's brake point for that corner and the compared lap's, exactly
// as AnalysisView builds them.
const BRAKE_MARKS = brakeMarkers(
  LAPS.filter((lap) => lap.isRef || lap.id === "1065"),
  TURN_FIVE.n,
);
// Both laps get on the brakes ~60 m before the corner window opens, so the
// brake-point cell frames the braking zone as well as the corner.
const BRAKE_RANGE: [number, number] = [
  Math.min(...BRAKE_MARKS.map((m) => m.dist)) - 30,
  TURN_FIVE_RANGE[1],
];

// The apex of that hairpin: where the cursor-synced dots are worth seeing.
const APEX = TURN_FIVE.apex_dist;

export function Hero() {
  // The map as the Analysis view opens it: full-bleed across the page, three
  // laps overlaid, events on. The pedal key sits in the panel header in hero
  // mode, so it is drawn here the way the view draws it.
  return (
    <Surface width={720}>
      <div className="panel">
        <div className="flex items-center gap-3 px-3.5 pt-2.5 text-[10px] text-ink-dim">
          <span className="section-header">Race line</span>
          <span className="ml-auto flex gap-3">
            <span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-throttle" />throttle</span>
            <span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-brake" />brake</span>
            <span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-coast" />coast</span>
          </span>
        </div>
        <RaceLineMap
          hero
          sync="position"
          layers={EVENTS}
          laps={LAPS}
          cursorDist={null}
          zoomRange={null}
          outline={null}
          onZoomChange={() => {}}
        />
      </div>
    </Surface>
  );
}

export function InTheRail() {
  // The squared-off variant the map has when it is not the page's hero. Same
  // lap, no overlays, and the full key below it: the throttle / brake / coast
  // colouring the line is drawn in, and the ▲ ▼ speed peaks and valleys
  // pinned where the lap was quickest and slowest.
  return (
    <Surface width={360}>
      <Panel title="Race line">
        <RaceLineMap
          layers={NO_LAYERS}
          laps={LAPS.filter((lap) => lap.isRef)}
          cursorDist={null}
          zoomRange={null}
          outline={null}
        />
      </Panel>
    </Surface>
  );
}

export function TractionControl() {
  // The TCS layer on: a ring on every sample where traction control was
  // cutting power, in the lap's own colour. The same exit ringed on every lap
  // is a corner the car is over the limit in — which is the reading the layer
  // exists for. These laps never triggered stability management, so the view
  // would not offer an ASM toggle at all.
  return (
    <Surface width={360}>
      <Panel title="Race line">
        <RaceLineMap
          layers={AIDS}
          laps={LAPS}
          cursorDist={null}
          zoomRange={null}
          outline={null}
        />
      </Panel>
    </Surface>
  );
}

export function ZoomedToACorner() {
  // Turn 5 picked from the corner bar: the map zooms with every other panel,
  // the bar's 5 lights up, and at this scale the three laps' lines separate
  // into three different ways through the same hairpin.
  return (
    <Surface width={360}>
      <Panel title="Race line">
        <RaceLineMap
          layers={NO_LAYERS}
          laps={LAPS}
          cursorDist={null}
          zoomRange={TURN_FIVE_RANGE}
          outline={null}
          onZoomChange={() => {}}
        />
      </Panel>
    </Surface>
  );
}

export function FollowingTheCar() {
  // What the map does while playback runs: instead of framing the circuit it
  // frames a fixed window of track around the reference car at the playhead,
  // so the corner being driven fills the panel.
  return (
    <Surface width={360}>
      <Panel title="Race line">
        <RaceLineMap
          follow
          layers={EVENTS}
          laps={LAPS}
          cursorDist={APEX}
          zoomRange={null}
          outline={null}
        />
      </Panel>
    </Surface>
  );
}

export function BrakePoints() {
  // Where each lap put the brakes on for the corner under the pointer in the
  // corner report — the reference's pin and the compared lap's, in their own
  // colours, so a few metres of difference at one corner is a place on the
  // track rather than a number in a table.
  return (
    <Surface width={360}>
      <Panel title="Race line">
        <RaceLineMap
          layers={NO_LAYERS}
          laps={LAPS}
          brakeMarks={BRAKE_MARKS}
          cursorDist={null}
          zoomRange={BRAKE_RANGE}
          outline={null}
          onZoomChange={() => {}}
        />
      </Panel>
    </Surface>
  );
}
