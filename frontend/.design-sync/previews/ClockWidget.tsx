import { ClockWidget, demoFrame, DEMO_LAPS } from "gt7-datalogger-frontend";
import { Labelled, Row, Surface, WidgetCard } from "../preview-shell";

// GT7's in-game time of day, not the wall clock: an afternoon race start.
const afternoon = { frame: demoFrame(0), laps: DEMO_LAPS, options: {} };
// The same widget deep into a Le Mans-style stint, when the light is going.
const dusk = {
  frame: { ...demoFrame(0), tod_ms: (18 * 3600 + 42 * 60) * 1000 },
  laps: DEMO_LAPS,
  options: {},
};

export function Digits() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={120} h={88}>
        <ClockWidget {...afternoon} variant="digits" w={1} h={1} />
      </WidgetCard>
    </Surface>
  );
}

export function DuskStint() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={120} h={88}>
        <ClockWidget {...dusk} variant="digits" w={1} h={1} />
      </WidgetCard>
    </Surface>
  );
}

export function Footprints() {
  return (
    <Surface className="font-tabular">
      <Row>
        <Labelled label="1x1">
          <WidgetCard w={104} h={88}>
            <ClockWidget {...afternoon} variant="digits" w={1} h={1} />
          </WidgetCard>
        </Labelled>
        <Labelled label="2x1">
          <WidgetCard w={176} h={88}>
            <ClockWidget {...afternoon} variant="digits" w={2} h={1} />
          </WidgetCard>
        </Labelled>
      </Row>
    </Surface>
  );
}
