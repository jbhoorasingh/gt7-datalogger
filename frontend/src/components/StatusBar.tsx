import { useEffect, useState } from "react";
import { Tip } from "@/components/ui/Tooltip";
import { api } from "@/lib/api";
import { navigate, openSettings, type View } from "@/lib/router";
import { useSettings } from "@/store/settings";
import { useTelemetry } from "@/store/telemetry";

const TABS: { id: View; label: string }[] = [
  { id: "live", label: "Live" },
  { id: "analysis", label: "Analysis" },
  { id: "sessions", label: "Sessions" },
  { id: "tracks", label: "Tracks" },
  { id: "survey", label: "Survey" },
  { id: "overlays", label: "Overlays" },
  { id: "settings", label: "Settings" },
];

// How often the Settings tab re-checks the sync service for an error.
const SYNC_POLL_MS = 60_000;

// Whether anything in Settings wants a look: console frames dropped in
// transit, or the sync service failing. Sync needs the admin API, so a
// locked server simply shows no sync hint.
function useSettingsAttention(framesDropped: number): boolean {
  const [syncError, setSyncError] = useState(false);
  useEffect(() => {
    let alive = true;
    const check = () =>
      api.admin
        .sync()
        .then((s) => {
          if (!alive) return;
          const failing =
            s.enabled &&
            (s.capabilities_error !== "" ||
              Object.values(s.types).some((t) => t.enabled && t.state === "error"));
          setSyncError(failing);
        })
        .catch(() => {});
    check();
    const id = window.setInterval(check, SYNC_POLL_MS);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);
  return framesDropped > 0 || syncError;
}

export function StatusBar({ view }: { view: View }) {
  const { status, wsConnected, setStatus } = useTelemetry();
  const { units, setUnits } = useSettings();

  useEffect(() => {
    api.status().then(setStatus).catch(() => {});
  }, [setStatus]);

  const settingsAttention = useSettingsAttention(status?.frames_dropped ?? 0);

  const telemetryUp = wsConnected && (status?.connected ?? false);
  // A labelled dot, not colour alone: the words carry the state.
  const telemetry = telemetryUp
    ? {
        label: status?.source === "sim" ? "Receiving · simulated" : "Receiving",
        dot: "bg-throttle",
        text: "text-throttle",
        title: `Receiving telemetry from ${status?.source === "sim" ? "the simulator" : status?.console_ip}`,
      }
    : wsConnected
      ? {
          label: "No telemetry",
          dot: "bg-warn",
          text: "text-warn",
          title: "Server up, no telemetry — check console IP / UDP 33740",
        }
      : {
          label: "Offline",
          dot: "bg-brake",
          text: "text-brake",
          title: "Disconnected from the datalogger server",
        };

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
              {t.id === "settings" && settingsAttention && (
                <span
                  aria-label="needs attention"
                  className="ml-1.5 inline-block h-[5px] w-[5px] -translate-y-px rounded-full bg-warn align-middle"
                />
              )}
            </button>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-2.5">
          <Tip content={`${telemetry.title} — open Settings › Connection`}>
            <button
              onClick={() => openSettings("connection")}
              className={`inline-flex items-center gap-1.5 text-[11px] ${telemetry.text} hover:underline`}
            >
              <span className={`h-1.5 w-1.5 rounded-full ${telemetry.dot}`} />
              {telemetry.label}
            </button>
          </Tip>
          {status && (
            <>
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
