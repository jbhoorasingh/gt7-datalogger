// Minimal hash router: #/live, #/analysis, #/sessions, #/tracks, #/admin, with optional
// query params (#/analysis?session=3&laps=12,15&ref=15). The URL is the single
// source of truth for cross-view handoff (Sessions/Live → Analysis) and makes
// every view bookmarkable. The /overlay path is handled separately (lib/overlay).

export type View = "live" | "analysis" | "sessions" | "survey" | "tracks" | "admin";

const VIEWS: readonly View[] = [
  "live",
  "analysis",
  "sessions",
  "survey",
  "tracks",
  "admin",
];

export interface Route {
  view: View;
  params: URLSearchParams;
}

export function parseHash(hash: string): Route {
  const stripped = hash.replace(/^#\/?/, "");
  const qIndex = stripped.indexOf("?");
  const path = qIndex >= 0 ? stripped.slice(0, qIndex) : stripped;
  const query = qIndex >= 0 ? stripped.slice(qIndex + 1) : "";
  const params = new URLSearchParams(query);
  // Bests folded into Sessions as a sub-tab; keep old #/bests links working.
  if (path === "bests") {
    params.set("sub", "bests");
    return { view: "sessions", params };
  }
  const view = (VIEWS as readonly string[]).includes(path) ? (path as View) : "live";
  return { view, params };
}

export function routeHash(view: View, params?: Record<string, string>): string {
  const q = params ? new URLSearchParams(params).toString() : "";
  return `#/${view}${q ? `?${q}` : ""}`;
}

export function navigate(view: View, params?: Record<string, string>): void {
  window.location.hash = routeHash(view, params);
}

// Selection handed to the Analysis view via URL params.
export interface AnalysisRequest {
  session?: number;
  laps?: number[];
  ref?: number;
  channels?: string[]; // stacked-chart channel keys (ch=), non-default sets only
}

export function openInAnalysis(req: AnalysisRequest): void {
  const params: Record<string, string> = {};
  if (req.session != null) params.session = String(req.session);
  if (req.laps && req.laps.length > 0) params.laps = req.laps.join(",");
  if (req.ref != null) params.ref = String(req.ref);
  if (req.channels && req.channels.length > 0) params.ch = req.channels.join(",");
  navigate("analysis", params);
}

export function parseAnalysisParams(params: URLSearchParams): AnalysisRequest {
  const session = Number(params.get("session"));
  const ref = Number(params.get("ref"));
  const laps = (params.get("laps") ?? "")
    .split(",")
    .map(Number)
    .filter((n) => Number.isFinite(n) && n > 0);
  const channels = (params.get("ch") ?? "").split(",").filter((c) => c.length > 0);
  return {
    session: Number.isFinite(session) && session > 0 ? session : undefined,
    laps: laps.length > 0 ? laps : undefined,
    ref: Number.isFinite(ref) && ref > 0 ? ref : undefined,
    channels: channels.length > 0 ? channels : undefined,
  };
}

// Mirror the current Analysis selection into the address bar without pushing
// history entries (so back doesn't replay every lap toggle).
export function reflectAnalysisSelection(req: AnalysisRequest): void {
  if (parseHash(window.location.hash).view !== "analysis") return;
  const params: Record<string, string> = {};
  if (req.session != null) params.session = String(req.session);
  if (req.laps && req.laps.length > 0) params.laps = req.laps.join(",");
  if (req.ref != null) params.ref = String(req.ref);
  if (req.channels && req.channels.length > 0) params.ch = req.channels.join(",");
  history.replaceState(null, "", routeHash("analysis", params));
}
