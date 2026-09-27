// The stint trend: lap time and tyre temperature lap over lap on one chart,
// so that tyres going away and the pace going with them can be read together.
// GT7 broadcasts no tyre wear, so temperature and the drift in lap time are
// the only proxies there are — which the panel says under the chart.
//
// The data is the Mount Panorama session in a 911 GT3 R: seven laps, one
// stint, a real fuel load coming down from 99.6 L and per-wheel temperatures
// on every lap. Lap 7 is a partial, so it draws as a hollow marker off the
// trend line rather than dragging the drift down with it.
//
// The temperature mode and the fuel overlay are the component's own state with
// no props behind them, so the cells that want them press the real controls a
// frame after mount.

import { StintTrendChart, type StintTrend } from "gt7-datalogger-frontend";
import { useEffect, useRef } from "react";
import { FUEL_LAP, STINT } from "../fixtures/app";
import { Panel, Surface, lapColors } from "../preview-shell";

// The two laps under comparison in Analysis — the session's best and the one
// after it. Everything else stays in the neutral trend colour.
const SELECTED = [FUEL_LAP, 941];
const COLORS = lapColors(SELECTED, FUEL_LAP);

// The same session as a pre-Tier-1 recording would have arrived: lap times and
// fuel, no per-wheel columns at all.
const NO_TEMPS: StintTrend = {
  ...STINT,
  laps: STINT.laps.map((lap) => ({
    ...lap,
    tt_front: null,
    tt_rear: null,
    tt: undefined,
  })),
  stints: STINT.stints.map((s) => ({
    ...s,
    tt_front_per_lap: null,
    tt_rear_per_lap: null,
  })),
};

function Chart({
  trend = STINT,
  press,
}: {
  trend?: StintTrend;
  /** Exact label of the control to click once the chart is up. */
  press?: string;
}) {
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!press) return;
    const frame = requestAnimationFrame(() => {
      const buttons = [...(root.current?.querySelectorAll("button") ?? [])];
      buttons.find((b) => b.textContent?.trim() === press)?.click();
    });
    return () => cancelAnimationFrame(frame);
  }, [press]);

  return (
    <Surface width={720}>
      <Panel title="Stint trend — lap time and tyre temperature">
        <div ref={root}>
          <StintTrendChart trend={trend} selected={SELECTED} lapColors={COLORS} />
        </div>
      </Panel>
    </Surface>
  );
}

export function Session() {
  // The panel as the Analysis view mounts it. Lap time against lap number with
  // the fitted drift through it, front and rear tyre temperature on their own
  // axis, the two compared laps picked out in their chart colours, and the
  // stint's numbers spelled out underneath.
  return <Chart />;
}

export function EachWheel() {
  // The temperature mode switched from axle averages to all four wheels:
  // front and rear keep their colours and the left/right wheel of each pair is
  // told apart by a dashed line, which is how a car heating one side more than
  // the other shows up.
  return <Chart press="Each wheel" />;
}

export function WithFuel() {
  // The fuel overlay on: the load on board at the start of each lap, dotted,
  // falling from 99.6 L to 26 L over the stint. A lighter car is a quicker one,
  // so this is what hides part of what the tyres are costing.
  return <Chart press="Fuel" />;
}

export function NoTyreTemperatures() {
  // The same stint as a recording from before the per-wheel columns: the
  // temperature control and both legend swatches are gone, the temperature
  // axis with them, and the panel falls back to the lap-time trend alone.
  return <Chart trend={NO_TEMPS} />;
}
