import { useT } from "@/i18n";
import { THRESHOLDS } from "@/lib/alerts";
import type { WidgetRenderProps } from "@/lib/widgetMeta";
import { Caption } from "./shared";

function tempClass(value: number, warn: number, critical: number): string {
  return value > critical ? "text-brake" : value > warn ? "text-warn" : "";
}

function Row({ label, value, className }: { label: string; value: string; className?: string }) {
  return (
    <div className="flex justify-between gap-3">
      <span className="text-ink-dim">{label}</span>
      <span className={className}>{value}</span>
    </div>
  );
}

export function EngineWidget({ frame, variant }: WidgetRenderProps) {
  const t = useT();
  const lowOilP =
    frame.oil_pressure >= 0 &&
    frame.oil_pressure < THRESHOLDS.oilPressureMin &&
    frame.rpm > 2000;
  return (
    <div className="flex flex-col justify-center gap-0.5 text-xs leading-4">
      <Row
        label={t("widget.engine.water")}
        value={`${Math.round(frame.water_temp)}°`}
        className={tempClass(frame.water_temp, THRESHOLDS.waterWarn, THRESHOLDS.waterCritical)}
      />
      <Row
        label={t("widget.engine.oil")}
        value={`${Math.round(frame.oil_temp)}°`}
        className={tempClass(frame.oil_temp, THRESHOLDS.oilTempWarn, THRESHOLDS.oilTempCritical)}
      />
      {variant === "detailed" && (
        <>
          <Row
            label={t("widget.engine.oilPressure")}
            value={frame.oil_pressure < 0 ? "–" : `${frame.oil_pressure.toFixed(1)} bar`}
            className={lowOilP ? "text-brake" : ""}
          />
          <Row label={t("widget.engine.boost")} value={`${frame.boost.toFixed(2)} bar`} />
        </>
      )}
      <Caption>{t("widget.engine.caption")}</Caption>
    </div>
  );
}
