// A steering wheel that turns (#59). `steer_rad` is the wheel's rotation in
// radians exactly as GT7 broadcasts it — an absolute angle, not a normalised
// −1..1 input — so the graphic simply rotates by it and the readout shows
// degrees. No lock-to-lock assumption is baked in: GT7 doesn't broadcast the
// car's lock, and a wrong guess would draw a wheel that stops turning while
// the driver's hands keep going.

import type { WidgetRenderProps } from "@/lib/widgetMeta";
import { Caption } from "./shared";

export function SteeringWidget({ frame, variant }: WidgetRenderProps) {
  const rad = frame.steer_rad;
  const deg = rad != null ? (rad * 180) / Math.PI : null;
  return (
    <div className="flex flex-col items-center justify-center gap-0.5">
      <svg
        viewBox="0 0 100 100"
        className="w-16"
        role="img"
        aria-label={deg != null ? `Steering ${Math.round(deg)} degrees` : "Steering unknown"}
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
        <Caption>{deg != null ? `${deg > 0 ? "+" : ""}${deg.toFixed(0)}°` : "steering –"}</Caption>
      )}
    </div>
  );
}
