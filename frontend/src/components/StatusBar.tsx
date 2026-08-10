import { useEffect } from "react";
import { Tip } from "@/components/ui/Tooltip";
import { api } from "@/lib/api";
import { navigate, type View } from "@/lib/router";
import { useSettings } from "@/store/settings";
import { useTelemetry } from "@/store/telemetry";

const TABS: { id: View; label: string }[] = [
  { id: "live", label: "Live" },
  { id: "analysis", label: "Analysis" },
  { id: "sessions", label: "Sessions" },
  { id: "survey", label: "Survey" },
  { id: "admin", label: "Admin" },
];

export function StatusBar({ view }: { view: View }) {
  const { status, wsConnected, setStatus } = useTelemetry();
  const { units, setUnits } = useSettings();

  useEffect(() => {
    api.status().then(setStatus).catch(() => {});
  }, [setStatus]);

  const telemetryUp = wsConnected && (status?.connected ?? false);
  const dotColor = telemetryUp ? "bg-throttle" : wsConnected ? "bg-warn" : "bg-brake";
  const dotTitle = telemetryUp
    ? `Receiving telemetry (${status?.console_ip})`
    : wsConnected
      ? "Server up, no telemetry — check console IP / UDP 33740"
      : "Disconnected from server";

  return (
    // Below sm the nav drops to its own full-width row (order-last) — a single
    // non-wrapping row clips tab names on phones and leaves Sessions/Admin
    // unreachable.
    <header className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-edge bg-panel px-4 py-2">
      <div className="flex items-center gap-2" title={dotTitle}>
        <span className={`h-2.5 w-2.5 rounded-full ${dotColor} ${telemetryUp ? "animate-pulse" : ""}`} />
        <h1 className="whitespace-nowrap text-sm font-semibold tracking-wide">GT7 Datalogger</h1>
      </div>

      <nav className="order-last flex w-full gap-1 overflow-x-auto sm:order-none sm:w-auto">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => navigate(t.id)}
            aria-current={view === t.id ? "page" : undefined}
            className={`shrink-0 rounded-md px-3 py-1 text-sm transition-colors ${
              view === t.id
                ? "bg-panel-2 text-ink"
                : "text-ink-dim hover:bg-panel-2/60 hover:text-ink"
            }`}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <div className="ml-auto flex items-center gap-3 text-xs text-ink-dim">
        {status && (
          <>
            <span className="hidden sm:inline">
              {status.source === "sim" ? "Simulated source" : status.console_ip || "auto-discover"}
            </span>
            <Tip content="Toggle lap recording">
              <button
                onClick={() =>
                  api.setRecording(!status.recording).then(setStatus).catch(() => {})
                }
                className={`rounded-md border px-2 py-1 ${
                  status.recording
                    ? "border-brake/50 text-brake"
                    : "border-edge text-ink-dim hover:text-ink"
                }`}
              >
                {status.recording ? "● REC" : "○ Paused"}
              </button>
            </Tip>
          </>
        )}
        <Tip content="Toggle speed units">
          <button
            onClick={() => setUnits(units === "metric" ? "imperial" : "metric")}
            className="rounded-md border border-edge px-2 py-1 hover:text-ink"
          >
            {units === "metric" ? "km/h" : "mph"}
          </button>
        </Tip>
      </div>
    </header>
  );
}
