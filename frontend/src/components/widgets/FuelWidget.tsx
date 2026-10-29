import { useT } from "@/i18n";
import { THRESHOLDS } from "@/lib/alerts";
import { projectStrategy } from "@/lib/strategy";
import type { WidgetRenderProps } from "@/lib/widgetMeta";
import { Caption } from "./shared";

export function FuelWidget({ frame, laps, variant }: WidgetRenderProps) {
  const t = useT();
  const pct = (frame.fuel_level / Math.max(1, frame.fuel_capacity)) * 100;

  if (variant === "laps") {
    const proj = projectStrategy(frame, laps);
    const color =
      proj == null
        ? "text-ink-dim"
        : proj.lapsToEmpty < THRESHOLDS.fuelLapsCritical
          ? "text-brake"
          : proj.lapsToEmpty < THRESHOLDS.fuelLapsWarn
            ? "text-warn"
            : "";
    return (
      <div className="flex flex-col items-center justify-center">
        <div className={`text-3xl font-bold leading-none ${color}`}>
          {proj == null ? "–" : proj.lapsToEmpty.toFixed(1)}
        </div>
        <Caption>{t("widget.fuel.lapsCaption")}</Caption>
      </div>
    );
  }

  if (variant === "bar") {
    return (
      <div className="flex w-full min-w-36 flex-col justify-center gap-1">
        <div className="flex items-baseline justify-between">
          <Caption>{t("widget.fuel.caption")}</Caption>
          <span className={`text-sm font-semibold leading-none ${pct < 15 ? "text-brake" : ""}`}>
            {pct.toFixed(0)}%
          </span>
        </div>
        <div className="h-2.5 overflow-hidden rounded-full bg-white/10">
          <div
            className={`h-full ${pct < 15 ? "bg-brake" : "bg-warn"}`}
            style={{ width: `${Math.min(100, pct)}%` }}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center justify-center">
      <div className={`text-2xl font-semibold leading-none ${pct < 15 ? "text-brake" : ""}`}>
        {pct.toFixed(0)}%
      </div>
      <div className="mt-1 h-1.5 w-14 overflow-hidden rounded-full bg-white/10">
        <div
          className={`h-full ${pct < 15 ? "bg-brake" : "bg-warn"}`}
          style={{ width: `${Math.min(100, pct)}%` }}
        />
      </div>
      <Caption>{t("widget.fuel.caption")}</Caption>
    </div>
  );
}
