// On-screen caption for the most recent callout. This is the fallback that
// keeps Race Engineer useful when speech is unavailable (unsupported browser,
// muted tab, no voices installed), so it renders from the callout feed rather
// than from what was actually spoken.

import { useEffect, useState } from "react";
import { useT } from "@/i18n";
import type { VoiceCallout } from "@/lib/types";
import { useCalloutWording, useEngineer } from "@/store/engineer";

// Long enough to read at a glance, short enough not to sit over the track map.
const VISIBLE_MS = 6000;

export function CalloutBanner() {
  const t = useT();
  const word = useCalloutWording();
  const captions = useEngineer((s) => s.captions);
  const latest = useEngineer((s) => s.history[0] ?? null);
  const [shown, setShown] = useState<VoiceCallout | null>(null);

  useEffect(() => {
    if (!latest) return;
    setShown(latest);
    const timer = window.setTimeout(() => setShown(null), VISIBLE_MS);
    return () => window.clearTimeout(timer);
  }, [latest]);

  if (!captions || !shown) return null;
  const critical = shown.priority >= 90;
  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-3 flex justify-center px-3">
      <div
        className={`max-w-2xl rounded-lg border px-3 py-1.5 text-center text-sm shadow-lg backdrop-blur ${
          critical
            ? "border-brake/60 bg-brake/20 text-ink"
            : "border-edge bg-black/70 text-ink"
        }`}
      >
        <span className="mr-2 text-[10px] uppercase tracking-widest text-ink-dim">
          {t(`category.${shown.category}`)}
        </span>
        {word(shown)}
      </div>
    </div>
  );
}
