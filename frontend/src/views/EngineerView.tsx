// Standalone Race Engineer page (/engineer): voice output without the
// dashboard. Deliberately plain — it is often opened on a phone or in an OBS
// browser source, where the useful information is "is it going to speak?".

import { useEffect } from "react";
import { RaceEngineerPanel, RaceEngineerStatus } from "@/components/RaceEngineerPanel";
import { useVoiceClient } from "@/lib/useVoiceClient";
import { clientId, useEngineer } from "@/store/engineer";
import { useTelemetry } from "@/store/telemetry";

export function EngineerView() {
  useVoiceClient("engineer");
  const s = useEngineer();
  const wsConnected = useTelemetry((st) => st.wsConnected);
  const isSpeaker = s.activeClientId !== "" && s.activeClientId === clientId();

  useEffect(() => {
    document.body.classList.add("overlay-page");
    return () => document.body.classList.remove("overlay-page");
  }, []);

  return (
    <div className="mx-auto flex max-w-[1100px] flex-col gap-3 p-3">
      <div className="flex flex-wrap items-baseline gap-3">
        <h1 className="text-[17px] font-medium">Race Engineer</h1>
        <span className="text-[11px] text-ink-faint">
          /engineer · voice output for any device
        </span>
        <span className="ml-auto flex items-center gap-2.5 text-[11px]">
          <span
            className={`flex items-center gap-1.5 ${
              !wsConnected ? "text-brake" : isSpeaker ? "text-throttle" : "text-warn"
            }`}
          >
            <span
              className={`h-[7px] w-[7px] rounded-full ${
                !wsConnected ? "bg-brake" : isSpeaker ? "bg-throttle" : "bg-warn"
              }`}
            />
            {!wsConnected ? "offline" : isSpeaker ? "speaking here" : "connected"}
          </span>
          {/* /#/live: this page has no other way back (no header, no nav). */}
          <a
            href="/#/live"
            className="rounded border border-edge px-2.5 py-0.5 text-[10.5px] text-ink-muted transition-colors hover:border-accent hover:text-accent"
            title="Back to the main app"
          >
            ⌂ home
          </a>
        </span>
      </div>

      <div className="grid items-start gap-3 lg:grid-cols-[1fr_360px]">
        <div className="panel min-w-0">
          <div className="section-header px-3.5 py-2.5">Voice output</div>
          <div className="rule" />
          <RaceEngineerPanel hideStatus />
        </div>

        <div className="flex flex-col gap-3">
          <div className="panel px-3.5 py-3">
            <div className="section-header mb-2.5">Status</div>
            <RaceEngineerStatus />
          </div>

          <div className="panel">
            <div className="flex flex-wrap items-baseline gap-1.5 px-3.5 py-2.5">
              <span className="section-header">Recent callouts</span>
              <span className="text-[10.5px] text-ink-faint">
                whether or not this device speaks
              </span>
            </div>
            <div className="rule" />
            {s.history.length === 0 ? (
              <p className="px-3.5 py-3 text-[11px] text-ink-faint">
                Nothing yet. Callouts appear here as they arrive, whether or not this
                device is the one speaking.
              </p>
            ) : (
              <div className="flex flex-col px-3.5 pb-3 pt-1">
                {s.history.map((callout) => (
                  <div key={callout.id} className="rule-row flex flex-col gap-1 py-2">
                    <div className="flex items-center gap-2 font-tabular text-[10px] text-ink-faint">
                      <span>{callout.priority}</span>
                      <span
                        className={`rounded-[9px] px-2 py-px ${
                          CATEGORY_PILL[callout.category] ?? "bg-edge text-ink-dim"
                        }`}
                      >
                        {callout.category}
                      </span>
                    </div>
                    <div className="text-xs text-ink">{callout.text}</div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// Callout categories carry their own colour so the feed can be skimmed: what
// the engineer is talking about is legible before the sentence is read.
const CATEGORY_PILL: Record<string, string> = {
  strategy: "bg-warn/15 text-warn",
  pace: "bg-accent/15 text-accent-300",
  chassis: "bg-brake/15 text-brake",
  engine: "bg-brake/15 text-brake",
  lap: "bg-edge text-ink-soft",
};
