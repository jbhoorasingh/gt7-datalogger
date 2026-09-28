import { formatDelta, formatLapTime } from "@/lib/format";
import type { WidgetRenderProps } from "@/lib/widgetMeta";
import { Caption, lapLabel, lastVsPrevBest } from "./shared";

export function TimesWidget({ frame, variant }: WidgetRenderProps) {
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
          {isLast ? "last lap" : "best lap"}
          {isLast && delta !== null ? ` ${formatDelta(delta)}` : ""}
        </Caption>
      </div>
    );
  }

  return (
    <div className="flex flex-col justify-center text-xs leading-5">
      <div>
        <span className="text-ink-dim">LAP </span>
        {lapLabel(frame)}
      </div>
      <div>
        <span className="text-ink-dim">BEST </span>
        <span className="text-fastest">{formatLapTime(frame.best_lap_ms)}</span>
      </div>
      <div>
        <span className="text-ink-dim">LAST </span>
        {formatLapTime(frame.last_lap_ms)}
        {delta !== null && (
          <span className={delta <= 0 ? "text-throttle" : "text-brake"}> {formatDelta(delta)}</span>
        )}
      </div>
    </div>
  );
}
