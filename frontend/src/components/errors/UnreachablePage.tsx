// 1d · Server unreachable — the WebSocket is closed and /api/status fails.
// Replaces the view (not the StatusBar) until a check gets through; the
// socket reconnects on its own and the view comes back with it.

import { useEffect, useState } from "react";
import { currentLanguage, useT } from "@/i18n";
import { formatDuration } from "@/lib/format";
import { useInstallKind } from "@/lib/health";
import { clearQuit, quitRequested } from "@/lib/quit";
import { updatingTo } from "@/lib/update";
import { liveFrameRef } from "@/store/telemetry";
import { ErrorPage, TROUBLESHOOTING_URL } from "./ErrorPage";
import { FLAGS } from "./illustrations";
import type { Reachability } from "./useServerReachability";

export function UnreachablePage({ reach }: { reach: Reachability }) {
  const t = useT();
  // Kept from before the server went away (lib/health): where to look
  // depends on how it was installed.
  const installKind = useInstallKind();
  // The countdown and the offline clock tick once a second.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  // Quit from Settings › About: the server went because it was asked to. The
  // page goes once it answers again, and the mark with it.
  const [stopped] = useState(quitRequested);
  useEffect(() => clearQuit, []);
  // Restart and update: the server went to come back as a new version, and
  // lib/update reloads the page once it does. Read on every render (the clock
  // above ticks each second), so the mark's expiry brings back the usual page.
  const updating = updatingTo(now);

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
      ? t("error.unreachable.lastFrame", {
          time: new Date(Date.now() - (performance.now() - liveFrameRef.at)).toLocaleTimeString(currentLanguage()),
        })
      : t("error.unreachable.noFrames");

  if (updating) {
    return (
      <ErrorPage
        flag={FLAGS.white}
        kicker={t("error.updating.kicker")}
        code="—"
        title={t("error.updating.title", { version: updating })}
        body={t("error.updating.body")}
        primary={{
          label: reach.checking ? t("error.unreachable.retrying") : t("error.stopped.check"),
          onClick: reach.retryNow,
          disabled: reach.checking,
        }}
      />
    );
  }

  if (stopped) {
    return (
      <ErrorPage
        flag={FLAGS.chequered}
        kicker={t("error.stopped.kicker")}
        code="—"
        title={t("error.stopped.title")}
        body={t("error.stopped.body")}
        primary={{
          label: reach.checking ? t("error.unreachable.retrying") : t("error.stopped.check"),
          onClick: reach.retryNow,
          disabled: reach.checking,
        }}
      />
    );
  }

  return (
    <ErrorPage
      flag={FLAGS.yellow}
      kicker={t("error.unreachable.kicker")}
      code="—"
      title={t("error.unreachable.title")}
      body={t(
        installKind === "desktop"
          ? "error.unreachable.bodyDesktop"
          : installKind === "docker"
            ? "error.unreachable.body"
            : "error.unreachable.bodySource",
      )}
      primary={{
        label: reach.checking ? t("error.unreachable.retrying") : t("error.unreachable.retryNow"),
        onClick: reach.retryNow,
        disabled: reach.checking,
      }}
      secondary={{ label: t("error.unreachable.guide"), href: TROUBLESHOOTING_URL }}
      diag={`${proto}://${location.host}/ws/live · ${t("error.unreachable.closed")} · ${lastFrame}`}
      hazard
    >
      <div className="grid grid-cols-3 gap-px overflow-hidden rounded-md bg-hairline">
        <Cell k={t("error.unreachable.retryIn")} v={retryIn} />
        <Cell k={t("error.unreachable.attempts")} v={String(reach.attempts)} />
        <Cell k={t("error.unreachable.offline")} v={offline} warn />
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
