import { demoFrame, DEMO_LAPS, SteeringWidget } from "gt7-datalogger-frontend";
import { Surface, WidgetCard } from "../preview-shell";

// steer_rad is GT7's absolute wheel angle: positive turns right. 4 s is winding
// on right-hand lock into the first corner, 6.6 s is the opposite lock out of it.
const turningRight = { frame: demoFrame(4_000), laps: DEMO_LAPS, options: {} };
const turningLeft = { frame: demoFrame(6_600), laps: DEMO_LAPS, options: {} };
// Packet A streams carry no steering: the wheel ghosts out rather than lying.
const noSteering = {
  frame: { ...demoFrame(6_600), steer_rad: null },
  laps: DEMO_LAPS,
  options: {},
};

export function WheelRight() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={120} h={120}>
        <SteeringWidget {...turningRight} variant="wheel" w={1} h={1} />
      </WidgetCard>
    </Surface>
  );
}

export function WheelLeft() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={120} h={120}>
        <SteeringWidget {...turningLeft} variant="wheel" w={1} h={1} />
      </WidgetCard>
    </Surface>
  );
}

export function WheelOnly() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={120} h={120}>
        <SteeringWidget {...turningLeft} variant="plain" w={1} h={1} />
      </WidgetCard>
    </Surface>
  );
}

export function NoSteeringData() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={120} h={120}>
        <SteeringWidget {...noSteering} variant="wheel" w={1} h={1} />
      </WidgetCard>
    </Surface>
  );
}
