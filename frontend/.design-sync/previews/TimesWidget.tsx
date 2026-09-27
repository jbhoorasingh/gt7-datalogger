import { demoFrame, DEMO_LAPS, TimesWidget } from "gt7-datalogger-frontend";
import { Surface, WidgetCard } from "../preview-shell";

// An AE86 session at Tsukuba: a 58.963 best, a 1:00.207 last lap that gave
// 0.786 back. The frame is the app's own demo telemetry with the lap clocks
// replaced, so nothing else about it is invented.
const tsukuba = {
  ...demoFrame(8_000),
  car_name: "Corolla Levin 1600GT APEX (AE86) '83",
  track_name: "Tsukuba Circuit",
  current_lap: 7,
  total_laps: 8,
  best_lap_ms: 58_963,
  session_best_ms: 58_963,
  last_lap_ms: 60_207,
  prev_best_ms: 59_421,
};
// The lap that became the session best: last lap 0.458 under the old one.
const personalBest = {
  ...tsukuba,
  last_lap_ms: 58_963,
  best_lap_ms: 58_963,
  prev_best_ms: 59_421,
};

const lapping = { frame: tsukuba, laps: DEMO_LAPS, options: {} };
const improved = { frame: personalBest, laps: DEMO_LAPS, options: {} };

export function List() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={192} h={104}>
        <TimesWidget {...lapping} variant="list" w={2} h={1} />
      </WidgetCard>
    </Surface>
  );
}

export function ListPersonalBest() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={192} h={104}>
        <TimesWidget {...improved} variant="list" w={2} h={1} />
      </WidgetCard>
    </Surface>
  );
}

export function LastLapBig() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={208} h={128}>
        <TimesWidget {...lapping} variant="last" w={2} h={2} />
      </WidgetCard>
    </Surface>
  );
}

export function BestLapBig() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={208} h={128}>
        <TimesWidget {...lapping} variant="best" w={2} h={2} />
      </WidgetCard>
    </Surface>
  );
}
