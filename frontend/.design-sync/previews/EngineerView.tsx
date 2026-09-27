// EngineerView — the standalone Race Engineer page (/engineer): voice output
// on its own, for a phone beside the wheel or a second browser source.
//
// It reads no HTTP endpoint at all. Everything on it comes from the engineer
// store: this device's voice preferences, the server's status pushed over the
// WebSocket, and the callouts that have arrived in this page load. So the
// cells seed that store — no socket, no stubbing, the real component over real
// state.
//
// The callout feed is the one thing with no fixture of its own: callouts are
// never persisted, they only exist while a browser is connected. The texts
// below are the race engineer's own recorded sentences for session 228
// (Tsukuba, AE86), as /api/analysis/coaching replays them — the same strings
// the voice would have spoken, wrapped back into the callout shape the feed
// renders. Nothing here is invented copy.

import { EngineerView, clientId, useEngineer, useTelemetry } from "gt7-datalogger-frontend";
import { STATUS } from "../fixtures/app";

const realFetch = window.fetch.bind(window);
window.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
  const url =
    typeof input === "string" ? input : input instanceof URL ? input.href : (input as Request).url;
  if (!url.includes("/api/")) return realFetch(input as RequestInfo, init);
  return Promise.resolve(new Response("no fixture", { status: 404 }));
}) as typeof window.fetch;

function DarkFullPage() {
  // /engineer puts `overlay-page` on the body; the card's own page style sets
  // white after the app's stylesheet, so the cell restates the ground.
  return (
    <style>
      {`body{background:var(--color-surface);color:var(--color-ink);margin:0;padding:0}
        .ds-single{min-height:100vh}`}
    </style>
  );
}

const T0 = 1_716_811_200_000;

function callout(
  n: number,
  category: string,
  priority: number,
  event_type: string,
  text: string,
) {
  return {
    id: `c${n}`,
    event_type,
    text,
    category,
    priority,
    created_at_ms: T0 + n * 60_000,
    expires_at_ms: T0 + n * 60_000 + 20_000,
    ttl_ms: 20_000,
    interrupt: false,
  };
}

// Newest first, as handleCallout unshifts them.
const HISTORY = [
  callout(5, "coaching", 55, "braking_late", "You are braking late into turn five, about twenty-seven meters."),
  callout(4, "lap", 40, "lap_complete", "Lap six, one minute one point two seven three. Two tenths off your best."),
  callout(3, "coaching", 60, "corner_time_loss", "You lost four tenths in turn two. You braked five meters later and carried seven kilometers per hour less at the apex."),
  callout(2, "chassis", 65, "repeated_bottoming", "The car is bottoming out at turn two."),
  callout(1, "pace", 45, "personal_best", "Best lap. Fifty-eight point nine six three."),
];

const SERVER_STATUS = {
  enabled: true,
  active: true,
  verbosity: "coach",
  categories: [
    "system", "lap", "pace", "race", "position",
    "fuel", "strategy", "engine", "tires", "chassis", "coaching",
  ],
  coaching_ready: true,
  active_client_id: "",
  clients: [],
};

// --- cells ------------------------------------------------------------------

export function Speaking() {
  // This device holds the speaker role and has been talking: five callouts in
  // the feed, the status block green, the header saying "speaking here".
  const me = clientId();
  useEngineer.setState({
    enabled: true,
    audioReady: true,
    supported: true,
    wantsSpeaker: true,
    page: "engineer",
    activeClientId: me,
    serverStatus: { ...SERVER_STATUS, active_client_id: me } as never,
    history: HISTORY as never,
    lastSpoken: HISTORY[0] as never,
    spokenCount: 5,
    failedCount: 0,
    speechError: null,
    queue: [],
    speaking: null,
  });
  useTelemetry.setState({ status: STATUS, wsConnected: true });
  return (
    <>
      <DarkFullPage />
      <EngineerView />
    </>
  );
}

export function Listening() {
  // The page as it opens on a second device: connected, receiving everything,
  // but not the one speaking — and nothing has come through yet. Voice stays
  // off until it is enabled in this page load, because a stored preference
  // does not carry a browser's autoplay permission across a restart.
  useEngineer.setState({
    enabled: false,
    audioReady: false,
    supported: true,
    wantsSpeaker: false,
    page: "engineer",
    activeClientId: "other-device",
    serverStatus: { ...SERVER_STATUS, active: false, active_client_id: "other-device" } as never,
    history: [],
    lastSpoken: null,
    spokenCount: 0,
    failedCount: 0,
    speechError: null,
    queue: [],
    speaking: null,
  });
  useTelemetry.setState({ status: STATUS, wsConnected: true });
  return (
    <>
      <DarkFullPage />
      <EngineerView />
    </>
  );
}
