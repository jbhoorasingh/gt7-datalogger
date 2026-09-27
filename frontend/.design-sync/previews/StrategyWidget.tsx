import { demoFrame, StrategyWidget } from "gt7-datalogger-frontend";
import { FUEL_LAPS } from "../fixtures/app";
import { Surface, WidgetCard } from "../preview-shell";

// Lap 12 of a 26-lap race at Mount Panorama. The projection is rolled off the
// real 911 GT3 R laps in the fixtures (~11.8 L a lap), so every number here is
// one the strategy code actually derives.
const stint = {
  ...demoFrame(8_000),
  car_id: 3600,
  car_name: "911 GT3 R (992) '22",
  track_name: "Mount Panorama Motor Racing Circuit",
  fuel_capacity: 100,
  current_lap: 12,
  total_laps: 26,
};

const brimmed = { frame: { ...stint, fuel_level: 93.5 }, laps: FUEL_LAPS, options: {} };
const gettingShort = { frame: { ...stint, fuel_level: 42.8 }, laps: FUEL_LAPS, options: {} };
const pitNow = { frame: { ...stint, fuel_level: 18.4 }, laps: FUEL_LAPS, options: {} };

export function Summary() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={184} h={88}>
        <StrategyWidget {...brimmed} variant="summary" w={2} h={1} />
      </WidgetCard>
    </Surface>
  );
}

export function SummaryShortOnFuel() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={184} h={88}>
        <StrategyWidget {...gettingShort} variant="summary" w={2} h={1} />
      </WidgetCard>
    </Surface>
  );
}

export function PitWindow() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={168} h={128}>
        <StrategyWidget {...gettingShort} variant="pit-window" w={2} h={2} />
      </WidgetCard>
    </Surface>
  );
}

export function PitWindowUrgent() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={168} h={128}>
        <StrategyWidget {...pitNow} variant="pit-window" w={2} h={2} />
      </WidgetCard>
    </Surface>
  );
}
