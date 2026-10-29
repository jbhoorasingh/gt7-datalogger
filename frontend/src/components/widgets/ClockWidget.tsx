import { useT } from "@/i18n";
import { formatTimeOfDay } from "@/lib/format";
import type { WidgetRenderProps } from "@/lib/widgetMeta";
import { Caption } from "./shared";

export function ClockWidget({ frame }: WidgetRenderProps) {
  const t = useT();
  return (
    <div className="flex flex-col items-center justify-center">
      <div className="text-2xl font-semibold leading-none">{formatTimeOfDay(frame.tod_ms)}</div>
      <Caption>{t("widget.clock.caption")}</Caption>
    </div>
  );
}
