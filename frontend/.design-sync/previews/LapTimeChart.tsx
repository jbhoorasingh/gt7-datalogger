import { LapTimeChart } from "gt7-datalogger-frontend";
import { LAPS } from "../fixtures/app";
import { REF_LAP, SELECTED } from "../fixtures/analysis";
import { ChartBox, Panel, Surface, lapColors } from "../preview-shell";

// Eight real Tsukuba laps in an AE86 — the spread a session actually has, with
// the out-lap and one scruffy lap well off the pace.
const colors = lapColors([...SELECTED], REF_LAP);

export function Session() {
  return (
    <Surface>
      <Panel title="Lap times">
        <ChartBox>
          <LapTimeChart laps={LAPS} selected={SELECTED} lapColors={colors} />
        </ChartBox>
      </Panel>
    </Surface>
  );
}

export function NothingSelected() {
  // Before a lap is picked the points are all neutral — the state the Analysis
  // view opens in.
  return (
    <Surface>
      <Panel title="Lap times">
        <ChartBox>
          <LapTimeChart laps={LAPS} selected={[]} lapColors={{}} />
        </ChartBox>
      </Panel>
    </Surface>
  );
}

export function SingleLap() {
  return (
    <Surface>
      <Panel title="Lap times">
        <ChartBox h={180}>
          <LapTimeChart laps={LAPS.slice(0, 2)} selected={[REF_LAP]} lapColors={colors} />
        </ChartBox>
      </Panel>
    </Surface>
  );
}
