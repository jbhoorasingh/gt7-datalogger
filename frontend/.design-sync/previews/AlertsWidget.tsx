import { AlertsWidget, demoFrame } from "gt7-datalogger-frontend";
import { FUEL_LAPS } from "../fixtures/app";
import { Surface } from "../preview-shell";

// The alerts widget is frameless: it draws nothing at all when the car is
// healthy, so every cell here has to be a frame that actually trips a
// threshold. The base is lap 12 of a 26-lap race at Mount Panorama, with the
// real 911 GT3 R laps from the fixtures behind the fuel projection.
const stint = {
  ...demoFrame(8_000),
  car_id: 3600,
  car_name: "911 GT3 R (992) '22",
  track_name: "Mount Panorama Motor Racing Circuit",
  fuel_capacity: 100,
  current_lap: 12,
  total_laps: 26,
};

// 9.2 L against ~11.8 a lap: under a lap of fuel, and the stop is due now.
const outOfFuel = { frame: { ...stint, fuel_level: 9.2 }, laps: FUEL_LAPS, options: {} };
// Two and a half laps left — a warning, not yet an emergency.
const fuelWarning = { frame: { ...stint, fuel_level: 30 }, laps: FUEL_LAPS, options: {} };
// Cooling gone at the same time as the tires: two severities stacked.
const overheating = {
  frame: {
    ...stint,
    fuel_level: 93.5,
    water_temp: 124,
    tire_temps: [118, 121, 103, 106] as [number, number, number, number],
  },
  laps: FUEL_LAPS,
  options: {},
};
// Everything at once, which is what the list variant is for.
const everything = {
  frame: {
    ...stint,
    fuel_level: 18.4,
    water_temp: 124,
    oil_temp: 143,
    tire_temps: [118, 121, 103, 106] as [number, number, number, number],
  },
  laps: FUEL_LAPS,
  options: {},
};

export function BannerFuelCritical() {
  return (
    <Surface className="font-tabular" width={300}>
      <AlertsWidget {...outOfFuel} variant="banner" w={4} h={1} />
    </Surface>
  );
}

export function BannerFuelWarning() {
  return (
    <Surface className="font-tabular" width={300}>
      <AlertsWidget {...fuelWarning} variant="banner" w={4} h={1} />
    </Surface>
  );
}

export function BannerStack() {
  return (
    <Surface className="font-tabular" width={300}>
      <AlertsWidget {...overheating} variant="banner" w={4} h={2} />
    </Surface>
  );
}

export function List() {
  return (
    <Surface className="font-tabular" width={240}>
      <AlertsWidget {...everything} variant="list" w={2} h={2} />
    </Surface>
  );
}
