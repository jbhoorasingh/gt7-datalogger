import { useT } from "@/i18n";
import type { WidgetRenderProps } from "@/lib/widgetMeta";

export function InputsWidget({ frame, variant }: WidgetRenderProps) {
  const t = useT();
  if (variant === "bars-v") {
    return (
      <div className="flex items-end justify-center gap-2">
        {(
          [
            [t("widget.inputs.throttle"), frame.throttle, "bg-throttle"],
            [t("widget.inputs.brake"), frame.brake, "bg-brake"],
          ] as const
        ).map(([label, value, color]) => (
          <div key={color} className="flex flex-col items-center gap-1">
            <div className="flex h-16 w-3 items-end overflow-hidden rounded-full bg-white/10">
              <div className={`w-full ${color}`} style={{ height: `${value}%` }} />
            </div>
            <span className="text-[9px] text-ink-dim">{label}</span>
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="flex w-full min-w-36 flex-col justify-center gap-1.5">
      <div className="h-2 overflow-hidden rounded-full bg-white/10">
        <div className="h-full bg-throttle" style={{ width: `${frame.throttle}%` }} />
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-white/10">
        <div className="h-full bg-brake" style={{ width: `${frame.brake}%` }} />
      </div>
    </div>
  );
}
