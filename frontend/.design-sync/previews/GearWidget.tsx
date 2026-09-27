import { demoFrame, DEMO_LAPS, GearWidget } from "gt7-datalogger-frontend";
import { Labelled, Row, Surface, WidgetCard } from "../preview-shell";

// demoFrame is the app's own placeholder telemetry: a 20 s lap loop with two
// braking zones. These times pick moments out of it — 8 s is flat out in sixth,
// 3.5 s is hard on the brakes in fourth, where third is suggested.
const onPower = { frame: demoFrame(8_000), laps: DEMO_LAPS, options: {} };
const braking = { frame: demoFrame(3_500), laps: DEMO_LAPS, options: {} };

export function Digits() {
  return (
    <Surface>
      <WidgetCard>
        <GearWidget {...braking} variant="digits" w={1} h={1} />
      </WidgetCard>
    </Surface>
  );
}

export function DigitOnly() {
  return (
    <Surface>
      <WidgetCard>
        <GearWidget {...onPower} variant="plain" w={1} h={1} />
      </WidgetCard>
    </Surface>
  );
}

export function Footprints() {
  return (
    <Surface>
      <Row>
        <Labelled label="1x1">
          <WidgetCard w={104} h={104}>
            <GearWidget {...onPower} variant="digits" w={1} h={1} />
          </WidgetCard>
        </Labelled>
        <Labelled label="2x2">
          <WidgetCard w={168} h={168}>
            <GearWidget {...braking} variant="digits" w={2} h={2} />
          </WidgetCard>
        </Labelled>
      </Row>
    </Surface>
  );
}
