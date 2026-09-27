import { DeltaWidget, demoFrame, DEMO_LAPS } from "gt7-datalogger-frontend";
import { Surface, WidgetCard } from "../preview-shell";

// The demo loop's live delta swings either side of the reference lap: 15 s is
// four tenths up, 2.8 s is three tenths down.
const ahead = { frame: demoFrame(15_000), laps: DEMO_LAPS, options: {} };
const behind = { frame: demoFrame(2_800), laps: DEMO_LAPS, options: {} };
// With no reference lap under the car the widget falls back to last vs the
// previous best and says so in the caption.
const endOfLap = {
  frame: { ...demoFrame(15_000), delta_ms: null },
  laps: DEMO_LAPS,
  options: {},
};

export function BigAhead() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={168}>
        <DeltaWidget {...ahead} variant="big" w={2} h={1} />
      </WidgetCard>
    </Surface>
  );
}

export function BigBehind() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={168}>
        <DeltaWidget {...behind} variant="big" w={2} h={1} />
      </WidgetCard>
    </Surface>
  );
}

export function BarAhead() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={216} h={120}>
        <DeltaWidget {...ahead} variant="bar" w={2} h={2} />
      </WidgetCard>
    </Surface>
  );
}

export function BarBehind() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={216} h={120}>
        <DeltaWidget {...behind} variant="bar" w={2} h={2} />
      </WidgetCard>
    </Surface>
  );
}

export function EndOfLapFallback() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={216} h={120}>
        <DeltaWidget {...endOfLap} variant="bar" w={2} h={2} />
      </WidgetCard>
    </Surface>
  );
}
