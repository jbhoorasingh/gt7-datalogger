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
  { id: "tracks", label: "Tracks" },
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
  // The brand dot is decorative; reachability rides on the console-IP
  // readout beside it, which the layout already reserves space for.
  const sourceLabel =
    status?.source === "sim" ? "Simulated source" : status?.console_ip || "auto-discover";
  const sourceTitle = telemetryUp
    ? `Receiving telemetry (${status?.console_ip})`
    : wsConnected
      ? "Server up, no telemetry — check console IP / UDP 33740"
      : "Disconnected from server";
  const sourceColor = telemetryUp ? "text-ink-faint" : wsConnected ? "text-warn" : "text-brake";

  return (
    <>
      {/* Below sm the nav drops to its own full-width row (order-last) — a
          single non-wrapping row clips tab names on phones and leaves
          Sessions/Admin unreachable. At sm+ this is the design's 46px bar. */}
      <header className="flex flex-shrink-0 flex-wrap items-center gap-x-6 gap-y-1 px-5 py-2 sm:h-[46px] sm:flex-nowrap sm:py-0">
        <div className="flex items-center gap-2">
          <span
            className="h-[7px] w-[7px] rounded-full bg-accent"
            style={{ boxShadow: "0 0 8px var(--color-accent)" }}
          />
          <span className="whitespace-nowrap text-[13px] font-semibold tracking-[0.01em]">
            GT7 Datalogger
          </span>
        </div>

        <nav className="order-last flex w-full gap-0.5 self-stretch overflow-x-auto sm:order-none sm:w-auto">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => navigate(t.id)}
              aria-current={view === t.id ? "page" : undefined}
              className={`shrink-0 border-y-2 border-transparent px-3 text-[12.5px] transition-colors ${
                view === t.id
                  ? "border-b-accent text-ink"
                  : "text-ink-faint hover:text-ink"
              }`}
            >
              {t.label}
            </button>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-2.5">
          {status && (
            <>
              <Tip content={sourceTitle}>
                <span className={`font-tabular text-[11px] ${sourceColor}`}>{sourceLabel}</span>
              </Tip>
              <Tip content="Toggle lap recording">
                <button
                  onClick={() =>
                    api.setRecording(!status.recording).then(setStatus).catch(() => {})
                  }
                  className={`inline-flex items-center gap-1.5 rounded px-2.5 py-0.5 text-[11px] transition-colors ${
                    status.recording
                      ? "border border-brake/55 font-semibold text-brake"
                      : "border border-edge text-ink-faint hover:text-ink"
                  }`}
                >
                  {status.recording ? (
                    <>
                      <span className="h-1.5 w-1.5 animate-pulse-dot rounded-full bg-brake" />
                      REC
                    </>
                  ) : (
                    "paused"
                  )}
                </button>
              </Tip>
            </>
          )}
          <Tip content="Toggle speed units">
            <button
              onClick={() => setUnits(units === "metric" ? "imperial" : "metric")}
              className="rounded border border-edge px-2.5 py-0.5 text-[11px] text-ink-muted transition-colors hover:border-edge-bright hover:text-ink"
            >
              {units === "metric" ? "km/h" : "mph"}
            </button>
          </Tip>
        </div>
      </header>
      <div className="rule" />
    </>
  );
}
