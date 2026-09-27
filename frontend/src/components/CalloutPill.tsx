// Category pill for Race Engineer callouts (/engineer feed, /dash caption).
// Categories carry their own colour so a feed can be skimmed: what the
// engineer is talking about is legible before the sentence is read.

import type { CalloutCategory } from "@/lib/types";

export const CATEGORY_PILL: Partial<Record<CalloutCategory, string>> = {
  strategy: "bg-warn/15 text-warn",
  pace: "bg-accent/15 text-accent-300",
  chassis: "bg-brake/15 text-brake",
  engine: "bg-brake/15 text-brake",
  lap: "bg-edge text-ink-soft",
};

export function CalloutPill({ category }: { category: CalloutCategory }) {
  return (
    <span
      className={`shrink-0 rounded-[9px] px-2 py-px text-[10px] ${
        CATEGORY_PILL[category] ?? "bg-edge text-ink-soft"
      }`}
    >
      {category}
    </span>
  );
}
