import { useT } from "@/i18n";
import { THRESHOLDS } from "@/lib/alerts";
import { projectStrategy } from "@/lib/strategy";
import type { WidgetRenderProps } from "@/lib/widgetMeta";
import { Caption } from "./shared";

export function StrategyWidget({ frame, laps, variant }: WidgetRenderProps) {
  const t = useT();
  const proj = projectStrategy(frame, laps);

  if (variant === "pit-window") {
    const urgent = proj != null && proj.pitBeforeLap <= frame.current_lap + 1;
    return (
      <div className="flex flex-col items-center justify-center">
        <div
          className={`text-3xl font-bold leading-none ${
            proj == null ? "text-ink-dim" : urgent ? "text-warn" : ""
          }`}
        >
          {proj == null ? "–" : t("widget.strategy.lap", { lap: String(proj.pitBeforeLap) })}
        </div>
        <Caption>{t("widget.strategy.pitBefore")}</Caption>
        {proj != null && (
          <div className="mt-0.5 text-[10px] text-ink-dim">
            {t("widget.strategy.fuelLaps", { laps: proj.lapsToEmpty.toFixed(1) })}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="flex flex-col justify-center text-xs leading-5">
      {proj == null ? (
        <span className="text-ink-dim">{t("widget.strategy.needLap")}</span>
      ) : (
        <>
          <div>
            <span className="text-ink-dim">{t("widget.strategy.fuel")} </span>
            <span
              className={
                proj.lapsToEmpty < THRESHOLDS.fuelLapsCritical
                  ? "text-brake"
                  : proj.lapsToEmpty < THRESHOLDS.fuelLapsWarn + 1
                    ? "text-warn"
                    : ""
              }
            >
              {t("widget.strategy.laps", { laps: proj.lapsToEmpty.toFixed(1) })}
            </span>
          </div>
          <div>
            <span className="text-ink-dim">{t("widget.strategy.pitBy")}</span>
            {proj.pitBeforeLap}
          </div>
        </>
      )}
    </div>
  );
}
