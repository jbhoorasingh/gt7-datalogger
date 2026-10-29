import { useT } from "@/i18n";
import { formatInt } from "@/lib/format";
import type { WidgetRenderProps } from "@/lib/widgetMeta";
import { Caption, Gauge } from "./shared";

const LED_COUNT = 10;
// Shift lights start filling at this fraction of rpm_alert.
const LED_START = 0.55;

export function RpmWidget(props: WidgetRenderProps) {
  const t = useT();
  const { frame, variant } = props;
  const alert = Math.max(1, frame.rpm_alert);
  const frac = frame.rpm / alert;
  const nearLimit = frac >= 0.95;

  if (variant === "shift-lights") {
    const lit = Math.max(
      0,
      Math.min(LED_COUNT, Math.ceil(((frac - LED_START) / (1 - LED_START)) * LED_COUNT)),
    );
    const atLimit = frac >= 1;
    return (
      <div className="flex w-full min-w-40 flex-col items-center justify-center gap-1">
        <div className={`flex gap-1 ${atLimit ? "animate-pulse" : ""}`}>
          {Array.from({ length: LED_COUNT }, (_, i) => {
            const on = i < lit;
            const color =
              i < 4 ? "bg-throttle" : i < 7 ? "bg-warn" : "bg-brake";
            return (
              <span
                key={i}
                className={`h-3 w-3 rounded-full ${on ? color : "bg-white/10"}`}
              />
            );
          })}
        </div>
        <div className="flex w-full justify-between text-[10px] text-ink-dim">
          <span>{formatInt(frame.rpm)} rpm</span>
          {nearLimit && <span className="text-brake">{t("widget.rpm.shift")}</span>}
        </div>
      </div>
    );
  }

  if (variant === "gauge") {
    return (
      <div className="flex flex-col items-center justify-center">
        <Gauge
          value={frame.rpm / (alert * 1.05)}
          text={(frame.rpm / 1000).toFixed(1)}
          caption="× 1000 rpm"
          color={nearLimit ? "var(--color-brake)" : "var(--color-accent)"}
        />
      </div>
    );
  }

  if (variant === "digits") {
    return (
      <div className="flex flex-col items-center justify-center">
        <div
          className={`text-3xl font-bold leading-none ${nearLimit ? "text-brake" : ""}`}
        >
          {formatInt(frame.rpm)}
        </div>
        <Caption>rpm</Caption>
      </div>
    );
  }

  const pct = Math.min(100, frac * 100);
  return (
    <div className="flex w-full min-w-40 flex-col justify-center gap-1">
      <div className="h-2.5 overflow-hidden rounded-full bg-white/10">
        <div
          className={`h-full ${nearLimit ? "bg-brake" : "bg-accent"}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="flex justify-between text-[10px] text-ink-dim">
        <span>{formatInt(frame.rpm)} rpm</span>
        {nearLimit && <span className="text-brake">{t("widget.rpm.shift")}</span>}
      </div>
    </div>
  );
}
