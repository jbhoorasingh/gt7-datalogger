// Minimal hash router: #/live, #/analysis, #/sessions, #/tracks, #/settings, with optional
// query params (#/analysis?session=3&laps=12,15&ref=15). The URL is the single
// source of truth for cross-view handoff (Sessions/Live → Analysis) and makes
// every view bookmarkable. The /overlay path is handled separately (lib/overlay).
// A path that names no view parses to "notfound" (the 404 page) rather than
// quietly landing on Live; "notfound" has no tab and is never navigated to.

import { DEFAULT_LENS, isMapLens, type MapLens } from "@/lib/mapLens";

export type View =
  | "notfound"
  | "live"
  | "analysis"
  | "sessions"
  | "survey"
  | "tracks"
  | "overlays"
  | "settings";

const VIEWS: readonly Exclude<View, "notfound">[] = [
  "live",
  "analysis",
  "sessions",
  "survey",
  "tracks",
  "overlays",
  "settings",
];

export interface Route {
  view: View;
  params: URLSearchParams;
}

export function parseHash(hash: string): Route {
  const stripped = hash.replace(/^#\/?/, "");
  const qIndex = stripped.indexOf("?");
  // A trailing slash (#/sessions/) names the same view.
  const path = (qIndex >= 0 ? stripped.slice(0, qIndex) : stripped).replace(/\/+$/, "");
  const query = qIndex >= 0 ? stripped.slice(qIndex + 1) : "";
  const params = new URLSearchParams(query);
  // Settings sections live in the path (#/settings/sync); Admin was renamed
  // Settings, so old #/admin links land there too.
  const [head, section] = path.split("/", 2);
  if (head === "settings" || head === "admin") {
    if (section) params.set("section", section);
    return { view: "settings", params };
  }
  // Bests folded into Sessions as a sub-tab; keep old #/bests links working.
  if (path === "bests") {
    params.set("sub", "bests");
    return { view: "sessions", params };
  }
  // The bare origin (no hash, "#", "#/") is Live.
  if (path === "") return { view: "live", params };
  const view = (VIEWS as readonly string[]).includes(path) ? (path as View) : "notfound";
  return { view, params };
}

export function routeHash(view: View, params?: Record<string, string>): string {
  const q = params ? new URLSearchParams(params).toString() : "";
  return `#/${view}${q ? `?${q}` : ""}`;
}

export function navigate(view: View, params?: Record<string, string>): void {
  window.location.hash = routeHash(view, params);
}

export function settingsHash(section?: string): string {
  return section ? `#/settings/${section}` : "#/settings";
}

export function openSettings(section?: string): void {
  window.location.hash = settingsHash(section);
}

// Selection handed to the Analysis view via URL params.
export interface AnalysisRequest {
  session?: number;
  laps?: number[];
  ref?: number;
  channels?: string[]; // stacked-chart channel keys (ch=), non-default sets only
  lens?: MapLens; // race line lens (lens=), written only when it is not Inputs
}

// The query a selection is written as, by the links into Analysis and by
// the view mirroring itself into the address bar alike.
export function analysisParams(req: AnalysisRequest): Record<string, string> {
  const params: Record<string, string> = {};
  if (req.session != null) params.session = String(req.session);
  if (req.laps && req.laps.length > 0) params.laps = req.laps.join(",");
  if (req.ref != null) params.ref = String(req.ref);
  if (req.channels && req.channels.length > 0) params.ch = req.channels.join(",");
  if (req.lens != null && req.lens !== DEFAULT_LENS) params.lens = req.lens;
  return params;
}

export function openInAnalysis(req: AnalysisRequest): void {
  navigate("analysis", analysisParams(req));
}

export function parseAnalysisParams(params: URLSearchParams): AnalysisRequest {
  const session = Number(params.get("session"));
  const ref = Number(params.get("ref"));
  const laps = (params.get("laps") ?? "")
    .split(",")
    .map(Number)
    .filter((n) => Number.isFinite(n) && n > 0);
  const channels = (params.get("ch") ?? "").split(",").filter((c) => c.length > 0);
  // A lens this build does not know is ignored, not shown as Inputs: the
  // view keeps whichever lens it already had.
  const lens = params.get("lens");
  return {
    session: Number.isFinite(session) && session > 0 ? session : undefined,
    laps: laps.length > 0 ? laps : undefined,
    ref: Number.isFinite(ref) && ref > 0 ? ref : undefined,
    channels: channels.length > 0 ? channels : undefined,
    lens: isMapLens(lens) ? lens : undefined,
  };
}

// Mirror the current Analysis selection into the address bar without pushing
// history entries (so back doesn't replay every lap toggle).
export function reflectAnalysisSelection(req: AnalysisRequest): void {
  if (parseHash(window.location.hash).view !== "analysis") return;
  history.replaceState(null, "", routeHash("analysis", analysisParams(req)));
}
