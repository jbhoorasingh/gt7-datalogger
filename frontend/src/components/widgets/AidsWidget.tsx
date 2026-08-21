import type { WidgetRenderProps } from "@/lib/widgetMeta";
import { AIDS_ASM, AIDS_HANDBRAKE, AIDS_REV_LIMITER, AIDS_TCS } from "@/lib/types";
import { Caption } from "./shared";

const BADGES: { bit: number; label: string; activeClass: string }[] = [
  { bit: AIDS_TCS, label: "TCS", activeClass: "border-warn bg-warn/25 text-warn" },
  { bit: AIDS_ASM, label: "ASM", activeClass: "border-warn bg-warn/25 text-warn" },
  { bit: AIDS_HANDBRAKE, label: "HB", activeClass: "border-accent bg-accent/25 text-accent" },
  { bit: AIDS_REV_LIMITER, label: "LIM", activeClass: "border-brake bg-brake/25 text-brake" },
];

export function AidsWidget({ frame }: WidgetRenderProps) {
  return (
    <div className="flex flex-col items-center justify-center gap-1">
      <div className="flex gap-1">
        {BADGES.map(({ bit, label, activeClass }) => {
          const on = (frame.aids & bit) !== 0;
          return (
            <span
              key={label}
              className={`rounded border px-1.5 py-0.5 text-[9px] font-semibold leading-none ${
                on ? activeClass : "border-edge text-ink-ghost"
              }`}
            >
              {label}
            </span>
          );
        })}
      </div>
      <Caption>aids</Caption>
    </div>
  );
}
