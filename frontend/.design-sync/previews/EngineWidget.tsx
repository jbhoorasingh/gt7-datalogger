import { demoFrame, DEMO_LAPS, EngineWidget } from "gt7-datalogger-frontend";
import { Surface, WidgetCard } from "../preview-shell";

// Healthy: 85 water, 102 oil, 5.4 bar of pressure.
const healthy = { frame: demoFrame(8_000), laps: DEMO_LAPS, options: {} };
// A long stint with the radiator taped over: water past the 120 critical, oil
// past 140, and the pressure falling away with it.
const overheating = {
  frame: {
    ...demoFrame(8_000),
    water_temp: 124,
    oil_temp: 143,
    oil_pressure: 1.6,
  },
  laps: DEMO_LAPS,
  options: {},
};
// The middle band: water over the 110 warn line but not yet critical.
const running_hot = {
  frame: { ...demoFrame(8_000), water_temp: 113, oil_temp: 134 },
  laps: DEMO_LAPS,
  options: {},
};

export function Compact() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={168} h={88}>
        <EngineWidget {...healthy} variant="compact" w={2} h={1} />
      </WidgetCard>
    </Surface>
  );
}

export function CompactRunningHot() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={168} h={88}>
        <EngineWidget {...running_hot} variant="compact" w={2} h={1} />
      </WidgetCard>
    </Surface>
  );
}

export function Detailed() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={184} h={120}>
        <EngineWidget {...healthy} variant="detailed" w={2} h={2} />
      </WidgetCard>
    </Surface>
  );
}

export function DetailedOverheating() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={184} h={120}>
        <EngineWidget {...overheating} variant="detailed" w={2} h={2} />
      </WidgetCard>
    </Surface>
  );
}
