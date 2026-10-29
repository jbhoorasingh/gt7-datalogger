import { useT } from "@/i18n";
import type { WidgetRenderProps } from "@/lib/widgetMeta";
import { Caption, isBig } from "./shared";

export function GearWidget(props: WidgetRenderProps) {
  const t = useT();
  const { frame, variant } = props;
  const big = isBig(props);
  return (
    <div className="flex flex-col items-center justify-center">
      <span className={`${big ? "text-7xl" : "text-6xl"} font-bold leading-none text-accent`}>
        {frame.gear === 0 ? "R" : frame.gear === 15 ? "N" : frame.gear}
      </span>
      <Caption>
        {variant !== "plain" && frame.suggested_gear !== 15
          ? t("widget.gear.suggested", { gear: String(frame.suggested_gear) })
          : t("widget.gear.caption")}
      </Caption>
    </div>
  );
}
