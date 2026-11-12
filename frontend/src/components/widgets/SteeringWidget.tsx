// A steering wheel that turns (#59). `steer_rad` is the wheel's rotation in
// radians — an absolute angle, not a normalised −1..1 input — so the graphic
// simply rotates by it and the readout shows degrees. GT7 signs it positive
// to the left; the graphic and the readout turn it round so right is
// clockwise and positive (lib/steering). No lock-to-lock assumption is baked
// in: GT7 doesn't broadcast the car's lock, and a wrong guess would draw a
// wheel that stops turning while the driver's hands keep going. GT7 does stop
// the value at half a turn; on packet C the backend fills in the rest from
// the road wheels (#233), so the angle here can pass ±180°.

import { useT } from "@/i18n";
import { steeringDegrees } from "@/lib/steering";
import type { WidgetRenderProps } from "@/lib/widgetMeta";
import { Caption } from "./shared";

export function SteeringWidget({ frame, variant }: WidgetRenderProps) {
  const t = useT();
  const rad = frame.steer_rad;
  const deg = rad != null ? steeringDegrees(rad) : null;
  return (
    <div className="flex flex-col items-center justify-center gap-0.5">
      <svg
        viewBox="0 0 100 100"
        className="w-16"
        role="img"
        aria-label={
          deg != null
            ? t("widget.steering.aria", { deg: String(Math.round(deg)) })
            : t("widget.steering.ariaUnknown")
        }
      >
        <g
          transform={`rotate(${deg ?? 0} 50 50)`}
          opacity={rad == null ? 0.3 : 1}
        >
          <circle
            cx={50}
            cy={50}
            r={42}
            fill="none"
            stroke="currentColor"
            strokeWidth={9}
          />
          {/* Spokes: left, right, and down — the top gap marks the wheel's
              center so rotation is readable at a glance. */}
          <path
            d="M 12 50 H 88 M 50 50 V 88"
            stroke="currentColor"
            strokeWidth={8}
            strokeLinecap="round"
          />
          <circle cx={50} cy={50} r={11} fill="currentColor" />
          <circle cx={50} cy={14} r={4.5} fill="var(--color-accent)" />
        </g>
      </svg>
      {variant !== "plain" && (
        <Caption>
          {deg != null ? `${deg > 0 ? "+" : ""}${deg.toFixed(0)}°` : t("widget.steering.unknown")}
        </Caption>
      )}
    </div>
  );
}
