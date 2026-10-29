import { useT } from "@/i18n";
import { THRESHOLDS } from "@/lib/alerts";
import type { WidgetRenderProps } from "@/lib/widgetMeta";
import { Caption } from "./shared";

export function TiresWidget({ frame, variant }: WidgetRenderProps) {
  const t = useT();
  const slipping = frame.tire_slip > 1.1;
  return (
    <div className="flex flex-col items-center justify-center gap-1">
      <div className="grid grid-cols-2 gap-1">
        {frame.tire_temps.map((temp, i) => (
          <div
            key={i}
            className={`h-4 w-8 rounded-sm text-center text-[9px] leading-4 ${
              temp < THRESHOLDS.tireTempCold
                ? "bg-coast/40"
                : temp < THRESHOLDS.tireTempHot
                  ? "bg-throttle/40"
                  : "bg-brake/50"
            }`}
          >
            {Math.round(temp)}
          </div>
        ))}
      </div>
      {variant === "temps-slip" ? (
        <div className={`text-[10px] leading-none ${slipping ? "text-brake" : "text-ink-dim"}`}>
          {t("widget.tires.slip", { slip: frame.tire_slip.toFixed(2) })}
        </div>
      ) : null}
      <Caption>{t("widget.tires.caption")}</Caption>
    </div>
  );
}
