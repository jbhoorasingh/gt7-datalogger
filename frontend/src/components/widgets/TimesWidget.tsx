import { useT } from "@/i18n";
import { formatDelta, formatLapTime } from "@/lib/format";
import type { WidgetRenderProps } from "@/lib/widgetMeta";
import { Caption, lapLabel, lastVsPrevBest } from "./shared";

export function TimesWidget({ frame, variant }: WidgetRenderProps) {
  const t = useT();
  const delta = lastVsPrevBest(frame);

  if (variant === "last" || variant === "best") {
    const isLast = variant === "last";
    const ms = isLast ? frame.last_lap_ms : frame.best_lap_ms;
    return (
      <div className="flex flex-col items-center justify-center">
        <div
          className={`text-3xl font-bold leading-none ${isLast ? "" : "text-fastest"}`}
        >
          {formatLapTime(ms)}
        </div>
        <Caption>
          {isLast ? t("widget.times.lastLap") : t("widget.times.bestLap")}
          {isLast && delta !== null ? ` ${formatDelta(delta)}` : ""}
        </Caption>
      </div>
    );
  }

  return (
    <div className="flex flex-col justify-center text-xs leading-5">
      <div>
        <span className="text-ink-dim">{t("widget.times.lap")} </span>
        {lapLabel(frame)}
      </div>
      <div>
        <span className="text-ink-dim">{t("widget.times.best")} </span>
        <span className="text-fastest">{formatLapTime(frame.best_lap_ms)}</span>
      </div>
      <div>
        <span className="text-ink-dim">{t("widget.times.last")} </span>
        {formatLapTime(frame.last_lap_ms)}
        {delta !== null && (
          <span className={delta <= 0 ? "text-throttle" : "text-brake"}> {formatDelta(delta)}</span>
        )}
      </div>
    </div>
  );
}
