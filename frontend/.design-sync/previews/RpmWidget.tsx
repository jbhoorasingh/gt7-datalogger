import { demoFrame, DEMO_LAPS, RpmWidget } from "gt7-datalogger-frontend";
import { Surface, WidgetCard } from "../preview-shell";

// 3 s into the demo loop the engine is at 7,685 of an 8,600 alert — high in
// the band without tripping the shift light.
const pulling = { frame: demoFrame(3_000), laps: DEMO_LAPS, options: {} };
const cruising = { frame: demoFrame(8_000), laps: DEMO_LAPS, options: {} };
// The demo loop never quite reaches the limiter, so this frame is the real one
// with the rpm pushed past rpm_alert: every LED lit, pulsing, SHIFT called.
const onLimiter = {
  frame: { ...demoFrame(3_000), rpm: 8_640 },
  laps: DEMO_LAPS,
  options: {},
};

export function Bar() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={208}>
        <RpmWidget {...pulling} variant="bar" w={2} h={1} />
      </WidgetCard>
    </Surface>
  );
}

export function BarAtLimit() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={208}>
        <RpmWidget {...onLimiter} variant="bar" w={2} h={1} />
      </WidgetCard>
    </Surface>
  );
}

export function ShiftLights() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={232}>
        <RpmWidget {...pulling} variant="shift-lights" w={4} h={1} />
      </WidgetCard>
    </Surface>
  );
}

export function ShiftLightsAtLimit() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={232}>
        <RpmWidget {...onLimiter} variant="shift-lights" w={4} h={1} />
      </WidgetCard>
    </Surface>
  );
}

export function GaugeArc() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={168} h={168}>
        <RpmWidget {...pulling} variant="gauge" w={2} h={2} />
      </WidgetCard>
    </Surface>
  );
}

export function Digits() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={168}>
        <RpmWidget {...cruising} variant="digits" w={2} h={1} />
      </WidgetCard>
    </Surface>
  );
}
