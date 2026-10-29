import { useT } from "@/i18n";
import { formatDelta } from "@/lib/format";
import type { WidgetRenderProps } from "@/lib/widgetMeta";
import { Caption, liveDelta } from "./shared";

// Full deflection of the bar variant, either side of zero.
const DELTA_BAR_MS = 2000;

export function DeltaWidget({ frame, variant }: WidgetRenderProps) {
  const t = useT();
  const delta = liveDelta(frame);
  const caption =
    delta == null || delta.live ? t("widget.delta.caption") : t("widget.delta.captionLastLap");

  if (variant === "bar") {
    const frac =
      delta == null ? 0 : Math.max(-1, Math.min(1, delta.ms / DELTA_BAR_MS));
    const width = Math.abs(frac) * 50;
    return (
      <div className="flex w-full min-w-40 flex-col items-center justify-center gap-1">
        <div
          className={`text-sm font-semibold leading-none ${
            delta == null ? "text-ink-dim" : delta.ms <= 0 ? "text-throttle" : "text-brake"
          }`}
        >
          {delta == null ? "–" : formatDelta(delta.ms)}
        </div>
        <div className="relative h-3 w-full overflow-hidden rounded-full bg-white/10">
          <div className="absolute inset-y-0 left-1/2 w-px bg-white/30" />
          <div
            className={`absolute inset-y-0 ${frac <= 0 ? "right-1/2 bg-throttle" : "left-1/2 bg-brake"}`}
            style={{ width: `${width}%` }}
          />
        </div>
        <Caption>{caption}</Caption>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center justify-center">
      <div
        className={`text-3xl font-bold leading-none ${
          delta == null ? "text-ink-dim" : delta.ms <= 0 ? "text-throttle" : "text-brake"
        }`}
      >
        {delta == null ? "–" : formatDelta(delta.ms)}
      </div>
      <Caption>{caption}</Caption>
    </div>
  );
}
