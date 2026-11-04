// A newer release (#196) as the banner and Settings › About show it, and the
// desktop app's in-place install: where it is (downloading, ready…), what
// Restart and update does, and the page reload once the new version answers.

import { useEffect, useRef } from "react";
import type { Translate } from "@/i18n";
import { api } from "./api";
import { loadHealth, useHealth } from "./health";
import type { UpdateStatus } from "./types";

/**
 * - none: no newer release, or the check is off;
 * - notify: a newer release, with a download link only (Docker, source,
 *   the portable zip, the tarball, or before the download starts);
 * - downloading, ready, installing, failed: the desktop app's install.
 */
export type UpdatePhase = "none" | "notify" | "downloading" | "ready" | "installing" | "failed";

export function updatePhase(update: UpdateStatus | null | undefined): UpdatePhase {
  if (!update?.available) return "none";
  const install = update.install;
  if (!install || install.state === "idle") return "notify";
  return install.state;
}

/** The version a phase is about: the install's own, else the offered one. */
export function updateVersion(update: UpdateStatus | null | undefined): string {
  return update?.install?.version ?? update?.version ?? "?";
}

/** Whole percent of the download, 0 to 100. */
export function downloadPercent(update: UpdateStatus | null | undefined): number {
  const p = update?.install?.progress ?? 0;
  return Math.max(0, Math.min(100, Math.floor(p * 100)));
}

/** The one line the banner and Settings › About show for a phase. */
export function updateMessage(update: UpdateStatus | null | undefined, t: Translate): string {
  const version = updateVersion(update);
  switch (updatePhase(update)) {
    case "downloading":
      return t("update.downloading", { version, percent: downloadPercent(update) });
    case "ready":
      return t("update.ready", { version });
    case "installing":
      return t("update.installing", { version });
    case "failed":
      return t("update.failed", { version, error: update?.install?.error ?? "" });
    default:
      return t("update.available", { version });
  }
}

/** The server's reason for refusing (a 409's detail), or the fallback. */
export function refusal(e: unknown, fallback: string): string {
  const message = e instanceof Error ? e.message : "";
  const body = message.match(/\{.*\}$/s)?.[0];
  if (body) {
    try {
      const detail = (JSON.parse(body) as { detail?: unknown }).detail;
      if (typeof detail === "string" && detail) return detail;
    } catch {
      // not the server's JSON
    }
  }
  return fallback;
}

// Restart and update, remembered for this tab, so the page shown while the
// server is away says it is installing (rather than that it vanished). It
// lapses after a while: on Windows the server comes back once before Setup
// stops it, and Setup may never run if its approval prompt is refused.
const KEY = "gt7.updatingTo";
const MARK_LASTS_MS = 10 * 60_000;
/** Fired on window when Restart and update was asked for, from any button. */
export const UPDATING_EVENT = "gt7:updating";

export function markUpdating(version: string, now = Date.now()): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify({ version, at: now }));
  } catch {
    // storage blocked: the unreachable page shows instead
  }
  if (typeof window !== "undefined") window.dispatchEvent(new Event(UPDATING_EVENT));
}

export function updatingTo(now = Date.now()): string | null {
  try {
    const mark = JSON.parse(sessionStorage.getItem(KEY) ?? "null") as { version?: unknown; at?: unknown } | null;
    if (!mark || typeof mark.version !== "string" || typeof mark.at !== "number") return null;
    return now - mark.at < MARK_LASTS_MS ? mark.version : null;
  } catch {
    return null;
  }
}

export function clearUpdating(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    // nothing to clear
  }
}

/** Restart and update; resolves once the server has taken the request. */
export async function installUpdate(update: UpdateStatus): Promise<void> {
  await api.admin.updateInstall();
  markUpdating(updateVersion(update));
}

/**
 * Reload when the server comes back as another version: the page's own
 * code is the old release's, and its lazily loaded views may no longer exist
 * on the new one. Asked each time the live socket reconnects.
 */
export function useReloadOnNewVersion(connected: boolean): void {
  const was = useRef(connected);
  // The version this page started with; overlays and /dash ask nothing else.
  // When that first request fails, the first reconnect that answers sets it.
  const baseline = useRef<string | null>(null);
  useEffect(() => {
    void loadHealth().then((health) => {
      baseline.current ??= health?.version ?? null;
    });
  }, []);
  useEffect(() => {
    const back = connected && !was.current;
    was.current = connected;
    if (!back) return;
    void api
      .health()
      .then((now) => {
        const before = baseline.current ?? useHealth.getState().health?.version ?? null;
        if (before == null) {
          baseline.current = now.version;
        } else if (now.version !== before) {
          clearUpdating();
          window.location.reload();
        }
      })
      .catch(() => {});
  }, [connected]);
}
