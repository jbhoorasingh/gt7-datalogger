import { useT } from "@/i18n";
import { THRESHOLDS } from "@/lib/alerts";
import type { WidgetRenderProps } from "@/lib/widgetMeta";
import { Caption } from "./shared";

// Solid blocks with dark digits: on a stream the colour has to carry the
// reading when the digits are too small to make out, and dark-on-solid keeps
// its contrast over any footage. Amber fills the gap between the top of the
// working window and the hot-tire alert, so red means the alert is firing.
function tireColor(temp: number): string {
  if (temp < THRESHOLDS.tireTempCold) return "bg-coast";
  if (temp < THRESHOLDS.tireTempHot) return "bg-throttle";
  if (temp <= THRESHOLDS.tireTempWarn) return "bg-warn";
  return "bg-brake";
}

export function TiresWidget({ frame, variant }: WidgetRenderProps) {
  const t = useT();
  const slipping = frame.tire_slip > 1.1;
  // The slip line shares the same design height, so its blocks give way.
  const block = variant === "temps-slip" ? "h-[26px] leading-[26px]" : "h-8 leading-8";
  return (
    <div className="flex flex-col items-center justify-center gap-1">
      {/* Auto columns: grid-cols-2 is minmax(0, 1fr), which in the strip
          shrinks to the caption's width and overlaps the blocks. */}
      <div className="grid grid-cols-[auto_auto] gap-x-2 gap-y-[5px]">
        {frame.tire_temps.map((temp, i) => (
          <div
            key={i}
            className={`w-10 rounded-md text-center text-lg font-bold text-surface ${block} ${tireColor(temp)}`}
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
