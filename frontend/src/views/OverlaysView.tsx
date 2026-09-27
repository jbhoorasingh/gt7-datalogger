import { useCallback } from "react";
import { LayoutBuilder } from "@/components/LayoutBuilder";
import { toast } from "@/store/toasts";

// Overlays and driver dashboards, moved out of Settings into their own tab.
export function OverlaysView() {
  const flash = useCallback((text: string) => toast(text, "success"), []);
  return (
    <div className="mx-auto max-w-[1400px]">
      <div className="panel min-w-0">
        <div className="flex items-baseline gap-2 px-4 py-2.5">
          <span className="section-header">Overlay & dashboard builder</span>
          <span className="text-[10.5px] text-ink-faint">design OBS overlays and driver dashboards</span>
        </div>
        <div className="rule" />
        <LayoutBuilder flash={flash} />
      </div>
    </div>
  );
}
