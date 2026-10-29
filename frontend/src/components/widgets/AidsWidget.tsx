import { useT, type MessageKey } from "@/i18n";
import type { WidgetRenderProps } from "@/lib/widgetMeta";
import { AIDS_ASM, AIDS_HANDBRAKE, AIDS_REV_LIMITER, AIDS_TCS } from "@/lib/types";
import { Caption } from "./shared";

const BADGES: { bit: number; labelKey: MessageKey; activeClass: string }[] = [
  { bit: AIDS_TCS, labelKey: "widget.aids.tcs", activeClass: "border-warn bg-warn/25 text-warn" },
  { bit: AIDS_ASM, labelKey: "widget.aids.asm", activeClass: "border-warn bg-warn/25 text-warn" },
  { bit: AIDS_HANDBRAKE, labelKey: "widget.aids.handbrake", activeClass: "border-accent bg-accent/25 text-accent" },
  { bit: AIDS_REV_LIMITER, labelKey: "widget.aids.limiter", activeClass: "border-brake bg-brake/25 text-brake" },
];

export function AidsWidget({ frame }: WidgetRenderProps) {
  const t = useT();
  return (
    <div className="flex flex-col items-center justify-center gap-1">
      <div className="flex gap-1">
        {BADGES.map(({ bit, labelKey, activeClass }) => {
          const on = (frame.aids & bit) !== 0;
          return (
            <span
              key={bit}
              className={`rounded border px-1.5 py-0.5 text-[9px] font-semibold leading-none ${
                on ? activeClass : "border-edge text-ink-ghost"
              }`}
            >
              {t(labelKey)}
            </span>
          );
        })}
      </div>
      <Caption>{t("widget.aids.caption")}</Caption>
    </div>
  );
}
