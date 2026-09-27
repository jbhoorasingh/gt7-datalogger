import {
  AIDS_ASM,
  AIDS_HANDBRAKE,
  AIDS_REV_LIMITER,
  AIDS_TCS,
  AidsWidget,
  demoFrame,
  DEMO_LAPS,
} from "gt7-datalogger-frontend";
import { Surface, WidgetCard } from "../preview-shell";

// Nothing intervening: every badge sits in its resting outline.
const clean = { frame: demoFrame(8_000), laps: DEMO_LAPS, options: {} };
// 7 s into the demo loop the rears light up and GT7 sets the TCS bit.
const tcsCutting = { frame: demoFrame(7_000), laps: DEMO_LAPS, options: {} };
// Stability control catching a slide while the engine sits on the limiter.
const asmAndLimiter = {
  frame: { ...demoFrame(7_000), aids: AIDS_ASM | AIDS_REV_LIMITER },
  laps: DEMO_LAPS,
  options: {},
};
// Handbrake pulled with traction control still cutting — a spin in progress.
const handbrake = {
  frame: { ...demoFrame(7_000), aids: AIDS_HANDBRAKE | AIDS_TCS },
  laps: DEMO_LAPS,
  options: {},
};

export function AllClear() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={184} h={88}>
        <AidsWidget {...clean} variant="badges" w={2} h={1} />
      </WidgetCard>
    </Surface>
  );
}

export function TractionControlCutting() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={184} h={88}>
        <AidsWidget {...tcsCutting} variant="badges" w={2} h={1} />
      </WidgetCard>
    </Surface>
  );
}

export function StabilityAndLimiter() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={184} h={88}>
        <AidsWidget {...asmAndLimiter} variant="badges" w={2} h={1} />
      </WidgetCard>
    </Surface>
  );
}

export function Handbrake() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={184} h={88}>
        <AidsWidget {...handbrake} variant="badges" w={2} h={1} />
      </WidgetCard>
    </Surface>
  );
}
