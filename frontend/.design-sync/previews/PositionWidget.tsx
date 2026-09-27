import { demoFrame, DEMO_LAPS, PositionWidget } from "gt7-datalogger-frontend";
import { Surface, WidgetCard } from "../preview-shell";

// A 16-car grid, third on the road.
const midfield = { frame: demoFrame(8_000), laps: DEMO_LAPS, options: {} };
// Two places later, out front — the readout a driver checks after an overtake.
const leading = {
  frame: { ...demoFrame(8_000), position: 1 },
  laps: DEMO_LAPS,
  options: {},
};

export function Big() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={168} h={120}>
        <PositionWidget {...midfield} variant="big" w={2} h={2} />
      </WidgetCard>
    </Surface>
  );
}

export function BigLeading() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={168} h={120}>
        <PositionWidget {...leading} variant="big" w={2} h={2} />
      </WidgetCard>
    </Surface>
  );
}

export function Compact() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={120} h={80}>
        <PositionWidget {...midfield} variant="compact" w={1} h={1} />
      </WidgetCard>
    </Surface>
  );
}
