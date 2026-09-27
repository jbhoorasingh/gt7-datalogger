import { demoFrame, DEMO_LAPS, InputsWidget } from "gt7-datalogger-frontend";
import { Surface, WidgetCard } from "../preview-shell";

// 8 s is flat out (throttle pinned, brake released), 3 s is the first braking
// zone (throttle shut, brake 100), 4.5 s is easing off the pedal at 74%, 5 s is
// the apex on part throttle.
const onPower = { frame: demoFrame(8_000), laps: DEMO_LAPS, options: {} };
const braking = { frame: demoFrame(3_000), laps: DEMO_LAPS, options: {} };
const releasing = { frame: demoFrame(4_500), laps: DEMO_LAPS, options: {} };
const apex = { frame: demoFrame(5_000), laps: DEMO_LAPS, options: {} };

export function HorizontalBarsOnPower() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={208} h={96}>
        <InputsWidget {...onPower} variant="bars-h" w={2} h={1} />
      </WidgetCard>
    </Surface>
  );
}

export function HorizontalBarsBraking() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={208} h={96}>
        <InputsWidget {...braking} variant="bars-h" w={2} h={1} />
      </WidgetCard>
    </Surface>
  );
}

export function HorizontalBarsPartThrottle() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={208} h={96}>
        <InputsWidget {...apex} variant="bars-h" w={2} h={1} />
      </WidgetCard>
    </Surface>
  );
}

export function VerticalBars() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={120} h={140}>
        <InputsWidget {...releasing} variant="bars-v" w={1} h={2} />
      </WidgetCard>
    </Surface>
  );
}

export function VerticalBarsOnPower() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={120} h={140}>
        <InputsWidget {...onPower} variant="bars-v" w={1} h={2} />
      </WidgetCard>
    </Surface>
  );
}
