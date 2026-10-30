// First-run setup (#191): when the bare origin goes to #/welcome, which
// network prompt this computer will show, and what a listener or heartbeat
// error means for the person at it. Pure, so the rules are tested apart
// from the wizard that uses them.

import type { HeartbeatError, InstallKind, SetupState } from "./types";

export const WELCOME_HASH = "#/welcome";

/**
 * Whether opening the app should land on the wizard. Only the bare origin
 * (no hash, "#", "#/") ever redirects — a bookmark or a deep link is never
 * taken over — and only while setup has not been completed. Even then an
 * install that predates the marker keeps its normal start page: the wizard is
 * for the desktop app, or an install that has recorded nothing yet.
 */
export function wantsWelcome(
  location: { pathname: string; hash: string },
  setup: SetupState | null,
  installKind: InstallKind | null,
): boolean {
  if (!setup || setup.completed) return false;
  if (location.pathname !== "/" && location.pathname !== "/index.html") return false;
  if (!/^(#\/?)?$/.test(location.hash)) return false;
  return installKind === "desktop" || setup.sessions === 0;
}

export type Os = "windows" | "mac" | "linux" | "other";

/** The OS this browser runs on: which network prompt to expect. */
export function detectOs(nav: { userAgent?: string; platform?: string } | undefined): Os {
  const ua = `${nav?.platform ?? ""} ${nav?.userAgent ?? ""}`.toLowerCase();
  // Phones first: an iPad says "Macintosh" and Android says "Linux".
  if (/iphone|ipad|ipod|android/.test(ua)) return "other";
  if (ua.includes("win")) return "windows";
  if (ua.includes("mac")) return "mac";
  if (ua.includes("linux") || ua.includes("x11")) return "linux";
  return "other";
}

export type Problem =
  | "portBusy"
  | "listenerOther"
  | "localNetwork"
  | "firewall"
  | "refused"
  | "noRoute"
  | "heartbeatOther";

/**
 * What stands between the console and this server, from the listener and
 * heartbeat errors /api/status and /api/admin/setup report (#181). Read
 * from the server's own wording, which names the cause in plain words.
 */
export function classifyProblem(
  listenerError: string | null | undefined,
  heartbeatError: HeartbeatError | null | undefined,
): Problem | null {
  if (listenerError) {
    return /in use/i.test(listenerError) ? "portBusy" : "listenerOther";
  }
  if (!heartbeatError) return null;
  const reason = heartbeatError.reason.toLowerCase();
  if (reason.includes("local network")) return "localNetwork";
  if (reason.includes("firewall") || reason.includes("refused to send")) return "firewall";
  if (reason.includes("refused the heartbeat")) return "refused";
  if (reason.includes("no route")) return "noRoute";
  return "heartbeatOther";
}

/**
 * The body POST /api/admin/setup/complete gets. Only the desktop app asks
 * the phone question: a Docker or source install already serves the LAN, and
 * answering for it would change who may connect (and create a token).
 */
// Countries whose roads are signed in miles: the Race Engineer speaks
// distances and speeds, so these get imperial by default (#191).
const IMPERIAL_REGIONS = new Set(["US", "GB", "LR", "MM"]);

/** Spoken units the browser's locale suggests: "en-US" → imperial, "de-DE" → metric. */
export function unitsForLocale(locale: string | undefined): "metric" | "imperial" {
  const region = (locale ?? "").split(/[-_]/)[1]?.toUpperCase();
  return region && IMPERIAL_REGIONS.has(region) ? "imperial" : "metric";
}

/** The settings the wizard's Preferences step writes, as a config patch. */
export function preferencesPatch(p: {
  units: "metric" | "imperial";
  updateCheck: boolean;
  carRefresh: boolean;
}): Record<string, string | boolean> {
  return {
    race_engineer_units: p.units,
    update_check: p.updateCheck,
    car_refresh: p.carRefresh,
  };
}

export function completionBody(
  installKind: InstallKind | null,
  lanAccess: boolean,
): { lan_access: boolean } | undefined {
  return installKind === "desktop" ? { lan_access: lanAccess } : undefined;
}
