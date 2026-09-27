import { demoFrame, FuelWidget } from "gt7-datalogger-frontend";
import { FUEL_LAPS } from "../fixtures/app";
import { Surface, WidgetCard } from "../preview-shell";

// A real stint: seven laps of Mount Panorama in a 911 GT3 R out of the app's
// own database, burning ~11.8 L a lap. The frame is the demo telemetry with
// the car and the fuel state swapped to match those laps, so projectStrategy
// accepts them (it refuses laps from another car).
const stint = {
  ...demoFrame(8_000),
  car_id: 3600,
  car_name: "911 GT3 R (992) '22",
  track_name: "Mount Panorama Motor Racing Circuit",
  fuel_capacity: 100,
  current_lap: 12,
  total_laps: 26,
  best_lap_ms: 131_077,
  last_lap_ms: 132_145,
  prev_best_ms: 131_544,
};

const halfTank = { frame: { ...stint, fuel_level: 42.8 }, laps: FUEL_LAPS, options: {} };
const nearlyDry = { frame: { ...stint, fuel_level: 9.2 }, laps: FUEL_LAPS, options: {} };

export function Percent() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={120} h={112}>
        <FuelWidget {...halfTank} variant="percent" w={1} h={1} />
      </WidgetCard>
    </Surface>
  );
}

export function PercentLow() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={120} h={112}>
        <FuelWidget {...nearlyDry} variant="percent" w={1} h={1} />
      </WidgetCard>
    </Surface>
  );
}

export function Bar() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={208} h={88}>
        <FuelWidget {...halfTank} variant="bar" w={2} h={1} />
      </WidgetCard>
    </Surface>
  );
}

export function LapsRemaining() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={168} h={112}>
        <FuelWidget {...halfTank} variant="laps" w={2} h={2} />
      </WidgetCard>
    </Surface>
  );
}

export function LapsRemainingCritical() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={168} h={112}>
        <FuelWidget {...nearlyDry} variant="laps" w={2} h={2} />
      </WidgetCard>
    </Surface>
  );
}
