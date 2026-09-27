import { demoFrame, DEMO_LAPS, SpeedWidget } from "gt7-datalogger-frontend";
import { Labelled, Row, Surface, WidgetCard } from "../preview-shell";

// Moments out of the app's own 20 s demo loop: 8 s is flat out at 232 km/h in
// sixth, 15 s is the run out of the second corner at 203, 5 s is the apex at 84.
const flatOut = { frame: demoFrame(8_000), laps: DEMO_LAPS, options: {} };
const corner = { frame: demoFrame(15_000), laps: DEMO_LAPS, options: {} };
const apex = { frame: demoFrame(5_000), laps: DEMO_LAPS, options: {} };

export function Digits() {
  return (
    <Surface className="font-tabular">
      <WidgetCard>
        <SpeedWidget {...flatOut} variant="digits" w={1} h={1} />
      </WidgetCard>
    </Surface>
  );
}

export function Bar() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={208}>
        <SpeedWidget {...corner} variant="bar" w={2} h={1} />
      </WidgetCard>
    </Surface>
  );
}

export function GaugeArc() {
  return (
    <Surface className="font-tabular">
      <Row>
        <Labelled label="232 km/h">
          <WidgetCard w={168} h={168}>
            <SpeedWidget {...flatOut} variant="gauge" w={2} h={2} />
          </WidgetCard>
        </Labelled>
        <Labelled label="apex, 84 km/h">
          <WidgetCard w={168} h={168}>
            <SpeedWidget {...apex} variant="gauge" w={2} h={2} />
          </WidgetCard>
        </Labelled>
      </Row>
    </Surface>
  );
}

export function BigReadout() {
  return (
    <Surface className="font-tabular">
      <Row>
        <Labelled label="1x1">
          <WidgetCard w={104} h={104}>
            <SpeedWidget {...flatOut} variant="digits" w={1} h={1} />
          </WidgetCard>
        </Labelled>
        <Labelled label="2x2 (big)">
          <WidgetCard w={200} h={168}>
            <SpeedWidget {...flatOut} variant="digits" w={2} h={2} />
          </WidgetCard>
        </Labelled>
      </Row>
    </Surface>
  );
}
