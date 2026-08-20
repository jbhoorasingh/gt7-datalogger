import type { LayoutConfig, LayoutSummary } from "./layout";
import type {
  AdminSettings,
  AdminStats,
  AuthoredCorner,
  AuthoredSection,
  CategoryBest,
  CoachingNotes,
  CompareResult,
  ConnectionStatus,
  DeviationResult,
  FuelMapResult,
  LapSummary,
  LogRecord,
  OfficialMatch,
  PersonalBest,
  RaceEngineerDiagnostics,
  SessionSummary,
  SurveyEdge,
  SurveyLog,
  SurveyStatus,
  Track,
  TrackBundleInfo,
  TrackCatalog,
  TrackOutline,
  TrackOverview,
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

// A whole bundle document, as exported and as import consumes it.
export interface TrackBundleDoc {
  format: string;
  version: number;
  meta: { track: string; runs: number; source_runs: Record<string, number>;
          updated_at: string; official: OfficialMatch | null };
  edges: SurveyEdge[];
  finish_crossings: { x: number; z: number; hx: number; hz: number; lap: number }[];
  corners: AuthoredCorner[];
  sections: AuthoredSection[];
}

export interface BundleMergeResult {
  track: string;
  slug: string;
  points: number;
  added_points: number;
  runs: number;
  sources: number;
  corners_kept?: boolean;
}

// One bundle a shared repo offers (#47). points/runs/updated_at are the
// index's advisory numbers — the truth is whatever the pull validates.
export interface SharedBundleEntry {
  track: string;
  slug: string;
  url: string;
  points?: number;
  runs?: number;
  updated_at?: string;
}

export interface SharedBundles {
  configured: boolean;
  url?: string;
  bundles: SharedBundleEntry[];
}

// Error carrying the HTTP status, so callers can distinguish auth failures
// (401/403 — a token problem) from an unreachable backend (502 from the proxy).
export class ApiError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message);
    this.name = "ApiError";
  }
}

async function fail(url: string, resp: Response): Promise<never> {
  if (resp.status === 401 || resp.status === 403) {
    throw new ApiError(
      resp.status === 401
        ? "admin token required — set it in Admin → Connection"
        : "admin token rejected — check it in Admin → Connection",
      resp.status,
    );
  }
  throw new ApiError(`${url}: ${resp.status} ${await resp.text()}`, resp.status);
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
  // `category` is the packet-C car class ("Gr.3", "N300"…); blank = every
  // session, which is also the only way to reach pre-packet-C recordings.
  sessions: (category = "") =>
    get<SessionSummary[]>(
      `/api/sessions${category ? `?category=${encodeURIComponent(category)}` : ""}`,
    ),
  // Fastest full lap at a circuit in a car category (#19); null when nothing
  // has been recorded there in that class yet.
  categoryBest: (track: string, category: string) =>
    get<CategoryBest | null>(
      `/api/laps/best?track=${encodeURIComponent(track)}&category=${encodeURIComponent(category)}`,
    ),
  // The personal-bests board (#26): fastest counting lap per (circuit, car),
  // ordered by circuit then time. Excludes unnamed circuits and sessions the
  // user has ruled out via bests_excluded (replay recordings look exactly
  // like driving in telemetry — see updateSession).
  personalBests: (category = "") =>
    get<{ bests: PersonalBest[] }>(
      `/api/laps/bests${category ? `?category=${encodeURIComponent(category)}` : ""}`,
    ),
  // note / bests_excluded are the only session fields a human is allowed to
  // rewrite after the fact; everything else is what the telemetry said.
  updateSession: (id: number, patch: { note?: string; bests_excluded?: boolean }) =>
    send<{ status: string }>(`/api/sessions/${id}`, "PATCH", patch),
  sessionLaps: (id: number) => get<LapSummary[]>(`/api/sessions/${id}/laps`),
  // `track` narrows to one circuit's laps across every session — what the
  // Analysis "+ Add lap" picker feeds on (#26).
  laps: (track = "", category = "") => {
    const q = new URLSearchParams();
    if (track) q.set("track", track);
    if (category) q.set("category", category);
    const qs = q.toString();
    return get<LapSummary[]>(`/api/laps${qs ? `?${qs}` : ""}`);
  },
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
  // The race engineer's post-lap notes, replayed from the stored session —
  // present whether or not voice was ever enabled (#23).
  coachingNotes: (sessionId: number) =>
    get<CoachingNotes>(`/api/analysis/coaching?session_id=${sessionId}`),
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
    // Every run's JSONL, and whether it ever reached a circuit. A run that
    // went nowhere exists only as this file.
    logs: () => get<SurveyLog[]>("/api/survey/logs"),
    assignLog: (name: string, track: string) =>
      send<Record<string, unknown>>(
        `/api/survey/logs/${encodeURIComponent(name)}/assign`,
        "POST",
        { track },
      ),
    // Raw JSONL transport (#40): a log downloaded here can be uploaded — or
    // assigned — on any other installation, moving the run itself.
    logDownloadUrl: (name: string) =>
      `/api/survey/logs/${encodeURIComponent(name)}/download`,
    uploadLog: async (file: File): Promise<SurveyLog> => {
      const url = `/api/survey/logs/upload?name=${encodeURIComponent(file.name)}`;
      const resp = await fetch(url, {
        method: "POST",
        headers: { ...authHeaders(), "Content-Type": "application/jsonl" },
        body: file,
      });
      if (!resp.ok) await fail(url, resp);
      return resp.json() as Promise<SurveyLog>;
    },
  },

  // The surveyed road for a circuit, compiled server-side (#51). Always
  // resolves — a circuit with no bundle answers with an empty outline.
  trackOutline: (track: string) =>
    get<TrackOutline>(`/api/track-outline?track=${encodeURIComponent(track)}`),

  tracks: () => get<Track[]>("/api/tracks"),
  trackCatalog: () => get<TrackCatalog>("/api/track-catalog"),
  trackBundles: () => get<TrackBundleInfo[]>("/api/track-bundles"),
  trackOverview: () => get<TrackOverview>("/api/track-overview"),
  // Name every unlabelled session that was driven on a surveyed circuit (#41).
  // New sessions do this for themselves as they are recorded; this is for the
  // history that predates the bundles.
  identifySessions: () =>
    send<{ checked: number; identified: number; tracks: Record<string, number> }>(
      "/api/tracks/identify",
      "POST",
    ),
  createTrack: (name: string, lapId: number) =>
    send<{ id: number; name: string }>("/api/tracks", "POST", { name, lap_id: lapId }),
  deleteTrack: (id: number) => send<{ status: string }>(`/api/tracks/${id}`, "DELETE"),
  lapCsvUrl: (lapId: number) => `/api/laps/${lapId}/export.csv`,

  bundles: {
    // The configured shared repo's offerings; `configured: false` hides the
    // feature. Pulling merges through exactly the same path as import (#47).
    shared: () => get<SharedBundles>("/api/track-bundles/shared"),
    pullShared: (slug: string, track?: string) =>
      send<BundleMergeResult>(
        `/api/track-bundles/shared/${slug}/pull${track ? `?track=${encodeURIComponent(track)}` : ""}`,
        "POST",
      ),
    get: (slug: string) => get<TrackBundleDoc>(`/api/track-bundles/${slug}`),
    downloadUrl: (slug: string) => `/api/track-bundles/${slug}`,
    // `track` collapses a near-miss name onto an existing circuit rather than
    // importing it as a second bundle of the same tarmac.
    import: (doc: unknown, track?: string) =>
      send<BundleMergeResult>(
        `/api/track-bundles/import${track ? `?track=${encodeURIComponent(track)}` : ""}`,
        "POST",
        doc,
      ),
    rename: (slug: string, track: string) =>
      send<BundleMergeResult>(`/api/track-bundles/${slug}`, "PATCH", { track }),
    setOfficial: (slug: string, official: OfficialMatch | null) =>
      send<{ slug: string }>(`/api/track-bundles/${slug}`, "PATCH", {
        official,
        set_official: true,
      }),
    remove: (slug: string) => send<{ status: string }>(`/api/track-bundles/${slug}`, "DELETE"),
    corners: (slug: string) =>
      get<{
        track: string;
        corners: AuthoredCorner[];
        sections: AuthoredSection[];
        official: OfficialMatch | null;
      }>(`/api/track-bundles/${slug}/corners`),
    setCorners: (
      slug: string,
      body: { corners?: AuthoredCorner[]; sections?: AuthoredSection[] },
    ) =>
      send<{ track: string; corners: AuthoredCorner[]; sections: AuthoredSection[] }>(
        `/api/track-bundles/${slug}/corners`,
        "PUT",
        body,
      ),
  },

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
