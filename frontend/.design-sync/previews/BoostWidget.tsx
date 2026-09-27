import { BoostWidget, demoFrame, DEMO_LAPS } from "gt7-datalogger-frontend";
import { Surface, WidgetCard } from "../preview-shell";

// The demo loop holds a steady 0.42 bar; the gauge runs to 2.00 bar full scale,
// so a Gr.3 turbo on full boost is the frame with that one field pushed up.
const partBoost = { frame: demoFrame(8_000), laps: DEMO_LAPS, options: {} };
const fullBoost = {
  frame: { ...demoFrame(8_000), boost: 1.38 },
  laps: DEMO_LAPS,
  options: {},
};

export function Digits() {
  return (
    <Surface className="font-tabular">
      <WidgetCard>
        <BoostWidget {...partBoost} variant="digits" w={1} h={1} />
      </WidgetCard>
    </Surface>
  );
}

export function DigitsFullBoost() {
  return (
    <Surface className="font-tabular">
      <WidgetCard>
        <BoostWidget {...fullBoost} variant="digits" w={1} h={1} />
      </WidgetCard>
    </Surface>
  );
}

export function GaugeArc() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={168} h={168}>
        <BoostWidget {...partBoost} variant="gauge" w={2} h={2} />
      </WidgetCard>
    </Surface>
  );
}

export function GaugeFullBoost() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={168} h={168}>
        <BoostWidget {...fullBoost} variant="gauge" w={2} h={2} />
      </WidgetCard>
    </Surface>
  );
}
