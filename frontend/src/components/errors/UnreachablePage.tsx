// 1d · Server unreachable — the WebSocket is closed and /api/status fails.
// Replaces the view (not the StatusBar) until a check gets through; the
// socket reconnects on its own and the view comes back with it.

import { useEffect, useState } from "react";
import { formatDuration } from "@/lib/format";
import { liveFrameRef } from "@/store/telemetry";
import { ErrorPage, TROUBLESHOOTING_URL } from "./ErrorPage";
import { FLAGS } from "./illustrations";
import type { Reachability } from "./useServerReachability";

export function UnreachablePage({ reach }: { reach: Reachability }) {
  // The countdown and the offline clock tick once a second.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const retryIn = reach.checking
    ? "…"
    : reach.nextCheckAt != null
      ? `${Math.max(0, Math.ceil((reach.nextCheckAt - now) / 1000))} s`
      : "–";
  const offline = reach.offlineSince != null ? formatDuration(now - reach.offlineSince) : "–";

  const proto = location.protocol === "https:" ? "wss" : "ws";
  // liveFrameRef.at is on the performance clock; carry it to wall time.
  const lastFrame =
    liveFrameRef.at > 0
      ? `last frame ${new Date(Date.now() - (performance.now() - liveFrameRef.at)).toLocaleTimeString()}`
      : "no frames this visit";

  return (
    <ErrorPage
      flag={FLAGS.yellow}
      kicker="Yellow flag · hold position"
      code="—"
      title="Can’t reach the datalogger server."
      body="This browser lost the connection. Nothing is recorded until the server is back — check the Docker container or the machine it runs on. We keep retrying."
      primary={{
        label: reach.checking ? "Retrying…" : "Retry now",
        onClick: reach.retryNow,
        disabled: reach.checking,
      }}
      secondary={{ label: "Troubleshooting guide", href: TROUBLESHOOTING_URL }}
      diag={`${proto}://${location.host}/ws/live · closed · ${lastFrame}`}
      hazard
    >
      <div className="grid grid-cols-3 gap-px overflow-hidden rounded-md bg-hairline">
        <Cell k="retry in" v={retryIn} />
        <Cell k="attempts" v={String(reach.attempts)} />
        <Cell k="offline" v={offline} warn />
      </div>
    </ErrorPage>
  );
}

function Cell({ k, v, warn }: { k: string; v: string; warn?: boolean }) {
  return (
    <div className="flex min-w-[110px] flex-col gap-0.5 bg-panel px-3 py-2.5">
      <span className={`whitespace-nowrap font-tabular text-2xl ${warn ? "text-warn" : "text-ink"}`}>
        {v}
      </span>
      <span className="section-header">{k}</span>
    </div>
  );
}
