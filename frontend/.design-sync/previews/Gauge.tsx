import { Gauge } from "gt7-datalogger-frontend";
import { Labelled, Row, Surface, WidgetCard } from "../preview-shell";

// The 240 degree arc behind every "gauge" widget variant: a hand-drawn SVG,
// not a chart library. value is a 0..1 fraction of full scale; the colour is
// the caller's, which is how the rpm gauge turns red near the limiter.

export function Speed() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={152} h={144}>
        <Gauge value={232 / 320} text="232" caption="km/h" />
      </WidgetCard>
    </Surface>
  );
}

export function RpmNearLimiter() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={152} h={144}>
        <Gauge
          value={8_400 / (8_600 * 1.05)}
          text="8.4"
          caption="× 1000 rpm"
          color="var(--color-brake)"
        />
      </WidgetCard>
    </Surface>
  );
}

export function Boost() {
  return (
    <Surface className="font-tabular">
      <WidgetCard w={152} h={144}>
        <Gauge value={1.38 / 2} text="1.38" caption="boost bar" />
      </WidgetCard>
    </Surface>
  );
}

export function Sweep() {
  return (
    <Surface className="font-tabular">
      <Row>
        {[
          { value: 0, text: "0", label: "empty" },
          { value: 0.35, text: "112", label: "35%" },
          { value: 0.72, text: "232", label: "72%" },
          { value: 1, text: "320", label: "full scale" },
        ].map((g) => (
          <Labelled key={g.label} label={g.label}>
            <WidgetCard w={128} h={128}>
              <Gauge value={g.value} text={g.text} caption="km/h" />
            </WidgetCard>
          </Labelled>
        ))}
      </Row>
    </Surface>
  );
}
