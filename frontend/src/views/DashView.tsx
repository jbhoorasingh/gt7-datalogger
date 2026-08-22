// Full-screen driver dashboard / race-engineer screen for a second display.
// Renders a built-in preset or a saved server layout on the shared grid.

import { useEffect, useState } from "react";
import { CalloutBanner } from "@/components/CalloutBanner";
import { computeAlerts } from "@/lib/alerts";
import { GridRenderer } from "@/components/GridRenderer";
import { RaceEngineerPanel } from "@/components/RaceEngineerPanel";
import { api } from "@/lib/api";
import type { DashParams } from "@/lib/dash";
import { DASH_PRESETS, DEFAULT_DASH_PRESET } from "@/lib/dashPresets";
import { normalizeLayout, type LayoutConfig } from "@/lib/layout";
import { useLiveFrame } from "@/lib/useLiveFrame";
import { useVoiceClient } from "@/lib/useVoiceClient";
import { clientId, useEngineer } from "@/store/engineer";

export function DashView({ params }: { params: DashParams }) {
  const [serverLayout, setServerLayout] = useState<LayoutConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [engineerOpen, setEngineerOpen] = useState(false);
  // The dashboard is the primary driver-facing surface, so it is one of the
  // two pages allowed to speak (see lib/useVoiceClient).
  useVoiceClient("dash");
  const voiceEnabled = useEngineer((s) => s.enabled && s.audioReady);
  const isSpeaker = useEngineer((s) => s.activeClientId !== "" && s.activeClientId === clientId());

  useEffect(() => {
    document.body.classList.add("overlay-page");
    return () => document.body.classList.remove("overlay-page");
  }, []);

  useEffect(() => {
    setServerLayout(null);
    setError(null);
    if (!params.layout) return;
    let cancelled = false;
    api.layouts
      .get(params.layout)
      .then((l) => {
        if (!cancelled) setServerLayout(normalizeLayout(l.config));
      })
      .catch(() => {
        if (!cancelled) setError(`Layout "${params.layout}" not found`);
      });
    return () => {
      cancelled = true;
    };
  }, [params.layout]);

  const preset = DASH_PRESETS[params.preset ?? ""] ?? DASH_PRESETS[DEFAULT_DASH_PRESET];
  const layout = params.layout ? serverLayout : preset.layout;
  // Force the dashboard shape regardless of source: fill the screen, dark page.
  const resolved: LayoutConfig | null = layout
    ? { ...layout, size: null, page: "dark" }
    : null;

  const demo = params.demo || (resolved?.demo ?? false);
  const { frame, laps, placeholder } = useLiveFrame(demo);

  const status = placeholder ? "placeholder" : frame ? "live" : "waiting";

  const alerts = frame ? computeAlerts(frame, laps) : [];
  const topAlert = alerts[0] ?? null;
  const presetLabel = params.layout ?? preset.label;

  return (
    <div className="relative flex h-full w-full flex-col gap-2.5 p-3 font-tabular">
      {/* Meta row: what this screen is, and the three controls worth having
          within reach while driving. */}
      <div className="flex flex-shrink-0 flex-wrap items-center gap-2.5 text-[10.5px] text-ink-faint">
        <span className="section-header">Driver dashboard</span>
        <span>/dash · {presetLabel} · second display</span>
        <span className="ml-auto flex items-center gap-1.5">
          <button
            className={`rounded border px-2.5 py-0.5 transition-colors ${
              voiceEnabled && isSpeaker
                ? "border-throttle/50 text-throttle"
                : "border-edge text-ink-muted hover:border-accent hover:text-accent"
            }`}
            title={
              voiceEnabled && isSpeaker
                ? "Race Engineer: speaking on this device"
                : "Race Engineer voice settings"
            }
            onClick={() => setEngineerOpen((open) => !open)}
          >
            voice
          </button>
          <button
            className="rounded border border-edge px-2.5 py-0.5 text-ink-muted transition-colors hover:border-accent hover:text-accent"
            title="Toggle fullscreen"
            onClick={() => {
              if (document.fullscreenElement) void document.exitFullscreen();
              else void document.documentElement.requestFullscreen();
            }}
          >
            full screen
          </button>
          {/* /#/live (not #/live): /dash may be a path, where a bare hash
              change would still match isDashLocation and go nowhere. */}
          <a
            href="/#/live"
            className="rounded border border-edge px-2.5 py-0.5 text-ink-muted transition-colors hover:border-accent hover:text-accent"
            title="Back to the main app"
          >
            ⌂ home
          </a>
          <span
            title={status}
            className={`ml-0.5 h-2 w-2 rounded-full ${
              status === "live"
                ? "bg-throttle"
                : status === "placeholder"
                  ? "bg-warn"
                  : "bg-brake"
            }`}
          />
        </span>
      </div>

      {/* Alerts get the full width above the tiles rather than a grid cell:
          low fuel, pit window, overheating and oil pressure are the things
          that must be seen without looking for them. */}
      {topAlert && (
        <div
          className={`flex flex-shrink-0 flex-wrap items-center gap-3 rounded-panel border px-4 py-2.5 ${
            topAlert.severity === "critical"
              ? "border-brake/50 bg-brake/10"
              : "border-warn/50 bg-warn/[0.09]"
          }`}
        >
          <span
            className={`h-2 w-2 animate-pulse-dot rounded-full ${
              topAlert.severity === "critical" ? "bg-brake" : "bg-warn"
            }`}
          />
          <span
            className={`text-sm font-semibold tracking-[0.08em] ${
              topAlert.severity === "critical" ? "text-brake" : "text-warn"
            }`}
          >
            {topAlert.message}
          </span>
          <span className="ml-auto text-[10.5px] text-ink-faint">
            alerts flash here: low fuel · pit window · overheating · oil pressure
          </span>
        </div>
      )}

      <div className="relative min-h-0 flex-1">
        {error ? (
          <div className="flex h-full items-center justify-center text-sm text-ink-dim">
            {error} — save it in the Admin builder first.
          </div>
        ) : !resolved ? null : !frame ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-sm text-ink-dim">
            <div>Waiting for telemetry…</div>
            <div className="text-xs">
              add <code className="text-ink">?demo=1</code> to preview with placeholder data
            </div>
          </div>
        ) : (
          <GridRenderer layout={resolved} frame={frame} laps={laps} />
        )}

        <CalloutBanner />

        {engineerOpen && (
          <div className="elevated absolute right-0 top-0 z-10 max-h-[80vh] w-80 overflow-y-auto rounded-panel bg-panel/95 backdrop-blur">
            <div className="flex items-baseline justify-between px-3.5 py-2.5">
              <span className="section-header">Race Engineer</span>
              <button
                className="text-xs text-ink-dim hover:text-ink"
                onClick={() => setEngineerOpen(false)}
              >
                close
              </button>
            </div>
            <div className="rule" />
            <RaceEngineerPanel compact />
          </div>
        )}
      </div>
    </div>
  );
}
