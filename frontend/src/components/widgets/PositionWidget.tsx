import type { WidgetRenderProps } from "@/lib/widgetMeta";
import { Caption } from "./shared";

export function PositionWidget({ frame, variant }: WidgetRenderProps) {
  // GT7 sends -1 (and a field of 0) outside a race — practice, time trial,
  // a menu. "P-1/0" is not a position; a dash says so with a dash.
  if (frame.position < 1 || frame.total_positions < 2) {
    return (
      <div className="flex flex-col items-center justify-center">
        <div className={`${variant === "compact" ? "text-base" : "text-3xl"} font-bold leading-none text-ink-ghost`}>
          —
        </div>
        {variant !== "compact" && <Caption>position</Caption>}
      </div>
    );
  }

  if (variant === "compact") {
    return (
      <div className="flex items-baseline justify-center gap-1">
        <span className="text-base font-bold leading-none">P{frame.position}</span>
        <span className="text-xs text-ink-dim">/{frame.total_positions}</span>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center justify-center">
      <div className="text-3xl font-bold leading-none">
        P{frame.position}
        <span className="text-lg text-ink-dim">/{frame.total_positions}</span>
      </div>
      <Caption>position</Caption>
    </div>
  );
}
