import { demoFrame, DEMO_LAPS, TiresWidget } from "gt7-datalogger-frontend";
import { Surface, WidgetCard } from "../preview-shell";

// In the working window: low 80s front, high 70s rear.
const workingWindow = { frame: demoFrame(8_000), laps: DEMO_LAPS, options: {} };
// 7 s is the demo loop's wheelspin on corner exit — tire_slip 1.18.
const wheelspin = { frame: demoFrame(7_000), laps: DEMO_LAPS, options: {} };
// An out-lap on cold tires: every corner under the 55 degree floor.
const outLap = {
  frame: { ...demoFrame(8_000), tire_temps: [43, 41, 38, 39] as [number, number, number, number] },
  laps: DEMO_LAPS,
  options: {},
};
// Overheating after a long stint: the fronts are past the alert threshold.
const overheating = {
  frame: {
    ...demoFrame(8_000),
    tire_temps: [118, 121, 103, 106] as [number, number, number, number],
    tire_slip: 1.24,
  },
  laps: DEMO_LAPS,
  options: {},
};

export function Temps() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={128} h={120}>
        <TiresWidget {...workingWindow} variant="temps" w={1} h={2} />
      </WidgetCard>
    </Surface>
  );
}

export function TempsWithSlip() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={128} h={128}>
        <TiresWidget {...wheelspin} variant="temps-slip" w={1} h={2} />
      </WidgetCard>
    </Surface>
  );
}

export function ColdOutLap() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={128} h={120}>
        <TiresWidget {...outLap} variant="temps" w={1} h={2} />
      </WidgetCard>
    </Surface>
  );
}

export function Overheating() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={128} h={128}>
        <TiresWidget {...overheating} variant="temps-slip" w={2} h={2} />
      </WidgetCard>
    </Surface>
  );
}
