// What the server has said is wrong, under the status bar on every view:
// the telemetry listener could not start (UDP 33740 taken…), the heartbeat
// keeps failing (a macOS Local Network denial, a firewall — #181), laps that
// could not be saved (#207). Before, these lived only in the log. Plus a
// small banner when a newer version is out (#196), which in the desktop app
// follows its download and offers Restart and update once it is ready.

import { useEffect, useState } from "react";
import { useT, type Translate } from "@/i18n";
import { api } from "@/lib/api";
import { openSettings } from "@/lib/router";
import type { ConnectionStatus, UpdateStatus } from "@/lib/types";
import {
  installUpdate,
  refusal,
  UPDATING_EVENT,
  updateMessage,
  updatePhase,
  updatingTo,
  updateVersion,
} from "@/lib/update";
import { useTelemetry } from "@/store/telemetry";
import { toast } from "@/store/toasts";

// The status pushed over the socket does not follow every heartbeat error,
// so it is asked for now and then as well.
const STATUS_POLL_MS = 10_000;
const UPDATE_POLL_MS = 30 * 60_000;
// While the desktop app downloads or installs an update, so the banner keeps up.
const UPDATE_BUSY_POLL_MS = 3000;
const DISMISSED_KEY = "gt7.updateDismissed";

export interface ServerProblem {
  key: string;
  tone: "bad" | "warn";
  text: string;
}

export function serverProblems(
  status: ConnectionStatus | null,
  t: Translate,
): ServerProblem[] {
  if (!status) return [];
  const out: ServerProblem[] = [];
  if (status.listener_error) {
    out.push({ key: "listener", tone: "bad", text: t("alert.listener", { error: status.listener_error }) });
  } else if (status.heartbeat_error && status.source !== "sim") {
    out.push({ key: "heartbeat", tone: "warn", text: t("alert.heartbeat", { reason: status.heartbeat_error.reason }) });
  }
  const failed = status.save_errors ?? 0;
  if (failed > 0) {
    out.push({
      key: "save",
      tone: "bad",
      text: t("alert.saveErrors", { count: failed, error: status.last_save_error?.error ?? "" }),
    });
  }
  return out;
}

function readDismissed(): string {
  try {
    return localStorage.getItem(DISMISSED_KEY) ?? "";
  } catch {
    return "";
  }
}

export function ServerAlerts() {
  const t = useT();
  const status = useTelemetry((s) => s.status);
  const setStatus = useTelemetry((s) => s.setStatus);
  const [update, setUpdate] = useState<UpdateStatus | null>(null);
  // Restart and update is offered where Quit is: the desktop app, from its
  // own computer (stats.can_quit).
  const [canInstall, setCanInstall] = useState(false);
  const [dismissed, setDismissed] = useState(readDismissed);
  // Restart and update clicked here or in Settings › About: installing now,
  // whatever the last poll said.
  const [installingTo, setInstallingTo] = useState(updatingTo);
  useEffect(() => {
    const on = () => setInstallingTo(updatingTo());
    window.addEventListener(UPDATING_EVENT, on);
    return () => window.removeEventListener(UPDATING_EVENT, on);
  }, []);
  const polled = updatePhase(update);
  const phase = polled === "ready" && installingTo === updateVersion(update) ? "installing" : polled;
  const busy = phase === "downloading" || phase === "installing";

  useEffect(() => {
    const poll = () => api.status().then(setStatus).catch(() => {});
    const id = window.setInterval(poll, STATUS_POLL_MS);
    return () => window.clearInterval(id);
  }, [setStatus]);

  useEffect(() => {
    // Stats is an admin endpoint: a tablet without the token simply sees no banner.
    const check = () =>
      api.admin
        .stats()
        .then((s) => {
          setUpdate(s.update ?? null);
          setCanInstall(Boolean(s.can_quit));
          // The mark lapses: re-read it, so a stalled install shows as ready again.
          setInstallingTo(updatingTo());
        })
        .catch(() => {});
    check();
    const id = window.setInterval(check, busy ? UPDATE_BUSY_POLL_MS : UPDATE_POLL_MS);
    return () => window.clearInterval(id);
  }, [busy]);

  const problems = serverProblems(status, t);
  // The server refuses to restart under a session (409); say so up front.
  const recording = Boolean(status?.connected && status.recording && status.session_id != null);
  const version = updateVersion(update);
  const showUpdate = phase !== "none" && version !== dismissed;

  async function install(current: UpdateStatus) {
    try {
      await installUpdate(current);
    } catch (e) {
      toast(refusal(e, t("update.installFailed")), "error");
    }
  }
  if (problems.length === 0 && !showUpdate) return null;

  return (
    <div className="flex flex-shrink-0 flex-col gap-1 px-5 pb-1">
      {problems.map((p) => (
        <div
          key={p.key}
          role="alert"
          className={`flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border px-3 py-1.5 text-[12px] ${
            p.tone === "bad" ? "border-brake/40 bg-brake/10 text-brake" : "border-warn/40 bg-warn/8 text-warn"
          }`}
        >
          <span className="min-w-0 flex-1">{p.text}</span>
          <button className="text-[11.5px] underline-offset-2 hover:underline" onClick={() => openSettings(p.key === "save" ? "health" : "connection")}>
            {p.key === "save" ? t("alert.openHealth") : t("alert.openConnection")}
          </button>
        </div>
      ))}
      {showUpdate && update && (
        <div
          role="status"
          className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-accent/40 bg-accent/8 px-3 py-1.5 text-[12px] text-accent-200"
        >
          <span className="min-w-0 flex-1">
            {phase === "installing" ? t("update.installing", { version }) : updateMessage(update, t)}
          </span>
          {phase === "ready" && canInstall && (
            <button
              className="font-medium hover:underline disabled:opacity-50 disabled:hover:no-underline"
              disabled={recording}
              title={recording ? t("update.recording") : undefined}
              onClick={() => void install(update)}
            >
              {t("update.install")}
            </button>
          )}
          {phase === "failed" && (
            <button className="hover:underline" onClick={() => void api.admin.updateDownload().then(setUpdate).catch(() => {})}>
              {t("update.retry")}
            </button>
          )}
          {(phase === "notify" || phase === "failed") && update.url && (
            <a className="hover:underline" href={update.url} target="_blank" rel="noreferrer">
              {t("common.download")}
            </a>
          )}
          <button
            className="text-ink-faint hover:text-ink"
            onClick={() => {
              const v = version;
              setDismissed(v);
              try {
                localStorage.setItem(DISMISSED_KEY, v);
              } catch {
                // remembered for this page only
              }
            }}
          >
            {t("update.dismiss")}
          </button>
        </div>
      )}
    </div>
  );
}
