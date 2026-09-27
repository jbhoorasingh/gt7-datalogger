import { Caption } from "gt7-datalogger-frontend";
import { Row, Surface, WidgetCard } from "../preview-shell";

// Caption is the one label every readout widget sits on: 9px, uppercase, wide
// tracking, ink-faint. It is never a heading — it names the unit or the
// quantity under a number.

export function UnderAReadout() {
  return (
    <Surface className="font-tabular">
      <WidgetCard>
        <div className="flex flex-col items-center justify-center">
          <div className="text-4xl font-bold leading-none">232</div>
          <Caption>km/h</Caption>
        </div>
      </WidgetCard>
    </Surface>
  );
}

export function UnderAColouredReadout() {
  return (
    <Surface className="font-tabular">
      <WidgetCard>
        <div className="flex flex-col items-center justify-center">
          <div className="text-3xl font-bold leading-none text-throttle">−0.400</div>
          <Caption>Δ best</Caption>
        </div>
      </WidgetCard>
    </Surface>
  );
}

// The captions the dashboard actually uses, each under the readout it names —
// which is the only place this component is ever allowed to appear.
const STRIP: { value: string; caption: string; className?: string }[] = [
  { value: "232", caption: "km/h" },
  { value: "6", caption: "gear → 5", className: "text-accent" },
  { value: "−0.400", caption: "Δ best (last lap)", className: "text-throttle" },
  { value: "3.6", caption: "laps of fuel" },
];

export function TheVocabulary() {
  return (
    <Surface className="font-tabular">
      <Row className="gap-6">
        {STRIP.map((item) => (
          <WidgetCard key={item.caption} w={152} h={88}>
            <div className="flex flex-col items-center justify-center">
              <div className={`text-2xl font-bold leading-none ${item.className ?? ""}`}>
                {item.value}
              </div>
              <Caption>{item.caption}</Caption>
            </div>
          </WidgetCard>
        ))}
      </Row>
    </Surface>
  );
}
