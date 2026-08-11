import type { LayoutConfig, LayoutSummary } from "./layout";
import type {
  AdminSettings,
  AdminStats,
  CompareResult,
  ConnectionStatus,
  DeviationResult,
  FuelMapResult,
  LapSummary,
  LogRecord,
  RaceEngineerDiagnostics,
  SessionSummary,
  SurveyEdge,
  SurveyStatus,
  Track,
  TrackBundleInfo,
  TrackCatalog,
  VoiceCallout,
} from "./types";

// Admin token (only needed when the server sets GT7_ADMIN_TOKEN). Stored per
// device; sent on every request — open endpoints simply ignore it.
const TOKEN_KEY = "gt7.adminToken";

export function getAdminToken(): string {
  return localStorage.getItem(TOKEN_KEY) ?? "";
}

export function setAdminToken(token: string): void {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

function authHeaders(): Record<string, string> {
  const t = getAdminToken();
  return t ? { "X-API-Key": t } : {};
}

async function fail(url: string, resp: Response): Promise<never> {
  if (resp.status === 401 || resp.status === 403) {
    throw new Error(
      resp.status === 401
        ? "admin token required — set it in Admin → Connection"
        : "admin token rejected — check it in Admin → Connection",
    );
  }
  throw new Error(`${url}: ${resp.status} ${await resp.text()}`);
}

async function get<T>(url: string): Promise<T> {
  const resp = await fetch(url, { headers: authHeaders() });
  if (!resp.ok) await fail(url, resp);
  return resp.json() as Promise<T>;
}

async function send<T>(url: string, method: string, body?: unknown): Promise<T> {
  const resp = await fetch(url, {
    method,
    headers: {
      ...authHeaders(),
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!resp.ok) await fail(url, resp);
  return resp.json() as Promise<T>;
}

export const api = {
  status: () => get<ConnectionStatus>("/api/status"),
  sessions: () => get<SessionSummary[]>("/api/sessions"),
  sessionLaps: (id: number) => get<LapSummary[]>(`/api/sessions/${id}/laps`),
  laps: () => get<LapSummary[]>("/api/laps"),
  lapDetail: (id: number, withSamples = true) =>
    get<LapSummary & Record<string, unknown>>(`/api/laps/${id}${withSamples ? "" : "?samples=0"}`),
  deleteSession: (id: number) => send<{ status: string }>(`/api/sessions/${id}`, "DELETE"),
  deleteLap: (id: number) => send<{ status: string }>(`/api/laps/${id}`, "DELETE"),
  exportLap: (id: number) => get<Record<string, unknown>>(`/api/laps/${id}/export`),
  importLap: (payload: unknown) => send<{ id: number }>("/api/laps/import", "POST", payload),
  compare: (lapIds: number[], ref: number, channels?: string[]) =>
    get<CompareResult>(
      `/api/analysis/compare?laps=${lapIds.join(",")}&ref=${ref}` +
        (channels && channels.length > 0 ? `&channels=${channels.join(",")}` : ""),
    ),
  deviation: (sessionId: number, count = 5) =>
    get<DeviationResult>(`/api/analysis/deviation?session_id=${sessionId}&count=${count}`),
  fuelMap: (lapId: number) => get<FuelMapResult>(`/api/analysis/fuel?lap_id=${lapId}`),
  setRecording: (recording: boolean) =>
    send<ConnectionStatus>("/api/control/recording", "POST", { recording }),
  logLapNow: () => send<{ id: number }>("/api/control/log-lap-now", "POST"),

  survey: {
    status: () => get<SurveyStatus>("/api/survey/status"),
    start: (trackWidthM: number, track: string) =>
      send<SurveyStatus>("/api/survey/start", "POST", {
        track_width_m: trackWidthM,
        track,
      }),
    stop: () => send<SurveyStatus>("/api/survey/stop", "POST"),
    setTrack: (track: string) =>
      send<SurveyStatus>("/api/survey/track", "POST", { track }),
    trail: (since: number, epoch: number) =>
      get<{ epoch: number; since: number; points: [number, number][]; total: number }>(
        `/api/survey/trail?since=${since}&epoch=${epoch}`,
      ),
    edges: (since: number, epoch: number) =>
      get<{ epoch: number; since: number; points: SurveyEdge[]; total: number }>(
        `/api/survey/edges?since=${since}&epoch=${epoch}`,
      ),
    mark: (side: "L" | "R" | null, kind: "edge" | "runoff" | "wall") =>
      send<SurveyStatus>("/api/survey/mark", "POST", { side, kind }),
    packet: () => get<{ packet: Record<string, unknown> | null }>("/api/survey/packet"),
    exportUrl: "/api/survey/export.jsonl",
  },

  tracks: () => get<Track[]>("/api/tracks"),
  trackCatalog: () => get<TrackCatalog>("/api/track-catalog"),
  trackBundles: () => get<TrackBundleInfo[]>("/api/track-bundles"),
  createTrack: (name: string, lapId: number) =>
    send<{ id: number; name: string }>("/api/tracks", "POST", { name, lap_id: lapId }),
  deleteTrack: (id: number) => send<{ status: string }>(`/api/tracks/${id}`, "DELETE"),
  lapCsvUrl: (lapId: number) => `/api/laps/${lapId}/export.csv`,

  layouts: {
    list: () => get<LayoutSummary[]>("/api/layouts"),
    get: (ref: string | number) =>
      get<LayoutSummary>(`/api/layouts/${encodeURIComponent(String(ref))}`),
    create: (name: string, kind: "overlay" | "dash", config: LayoutConfig) =>
      send<LayoutSummary>("/api/layouts", "POST", { name, kind, config }),
    update: (id: number, patch: { name?: string; config?: LayoutConfig }) =>
      send<LayoutSummary>(`/api/layouts/${id}`, "PUT", patch),
    remove: (id: number) => send<{ status: string }>(`/api/layouts/${id}`, "DELETE"),
  },

  admin: {
    settings: () => get<AdminSettings>("/api/admin/settings"),
    updateSettings: (
      patch: Partial<
        Pick<
          AdminSettings,
          | "ps_ip"
          | "source"
          | "log_level"
          | "webhook_url"
          | "webhook_events"
          | "packet_format"
          | "race_engineer"
          | "race_engineer_verbosity"
          | "race_engineer_categories"
          | "race_engineer_units"
        >
      >,
    ) => send<AdminSettings>("/api/admin/settings", "PUT", patch),
    testWebhook: () => send<{ status: string }>("/api/admin/test-webhook", "POST"),
    raceEngineer: () => get<RaceEngineerDiagnostics>("/api/admin/race-engineer"),
    testCallout: (text: string) =>
      send<VoiceCallout>("/api/admin/race-engineer/test", "POST", { text }),
    logs: (limit = 300, level?: string) =>
      get<LogRecord[]>(`/api/admin/logs?limit=${limit}${level ? `&level=${level}` : ""}`),
    clearLogs: () => send<{ status: string }>("/api/admin/logs", "DELETE"),
    stats: () => get<AdminStats>("/api/admin/stats"),
    restartSource: () => send<ConnectionStatus>("/api/admin/restart-source", "POST"),
    clearData: () => send<{ status: string }>("/api/admin/clear-data", "POST"),
    vacuum: () => send<{ status: string }>("/api/admin/vacuum", "POST"),
    updateCars: () => send<{ cars: number }>("/api/admin/update-cars", "POST"),
  },
};
