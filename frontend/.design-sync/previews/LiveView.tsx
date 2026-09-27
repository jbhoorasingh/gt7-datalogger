// LiveView — the Live/Race screen.
//
// LiveView reads nothing but `liveFrameRef`, the plain ref the telemetry
// WebSocket writes 30 Hz frames into, sampled on the animation clock by
// useLiveFrame. There is no socket behind a preview card, so the cell writes
// the frame into that same ref: the view then runs its real render path over a
// real frame. `useLiveFrame(false)` keeps showing the last frame once it goes
// stale, so one assignment is enough and the screenshot is deterministic.
//
// The lap rail and the fuel strategy come from the telemetry store's
// `recentLaps`, seeded with the 911 GT3 R's Mount Panorama stint — the one
// exported session whose laps actually burned fuel. projectStrategy drops laps
// recorded in a different car, so the frame carries that car's id.

import {
  LiveView,
  StatusBar,
  demoFrame,
  liveFrameRef,
  useTelemetry,
} from "gt7-datalogger-frontend";
import type { ReactNode } from "react";
import { FUEL_LAPS, SESSIONS, STATUS } from "../fixtures/app";

// --- the API, as the app's own fetch layer sees it ---------------------------

type Handler = (url: string) => unknown;
let ROUTES: [RegExp, Handler][] = [];

const realFetch = window.fetch.bind(window);
window.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
  const url =
    typeof input === "string" ? input : input instanceof URL ? input.href : (input as Request).url;
  if (!url.includes("/api/")) return realFetch(input as RequestInfo, init);
  for (const [re, handler] of ROUTES) {
    if (re.test(url)) {
      return Promise.resolve(
        new Response(JSON.stringify(handler(url)), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    }
  }
  return Promise.resolve(new Response("no fixture", { status: 404 }));
}) as typeof window.fetch;

// --- the page ---------------------------------------------------------------

function Page({ children }: { children: ReactNode }) {
  return (
    <>
      <style>
        {`body{background:var(--color-surface);color:var(--color-ink);margin:0;padding:0}
          .ds-single{height:100vh}`}
      </style>
      <div className="flex h-full flex-col">
        <StatusBar view="live" />
        <main className="min-h-0 flex-1 overflow-y-auto px-5 pb-7 pt-3.5">{children}</main>
      </div>
    </>
  );
}

// The Mount Panorama session, as the sessions list describes it.
const PORSCHE = SESSIONS.find((s) => s.id === 194)!;

/** The demo lap's frame at one moment, driven by the car the seeded laps were
 *  actually set in — fuel strategy is projected per car and silently drops
 *  laps from another one. */
function frameAt(ms: number, patch: Record<string, unknown> = {}) {
  return {
    ...demoFrame(ms),
    car_id: FUEL_LAPS[0].car_id ?? 3600,
    car_name: PORSCHE.car_name,
    track_name: PORSCHE.track_name,
    ...patch,
  };
}

function live(frame: unknown | null, status = STATUS) {
  liveFrameRef.current = frame as never;
  liveFrameRef.at = performance.now();
  useTelemetry.setState({ status, wsConnected: true, recentLaps: frame ? FUEL_LAPS : [] });
  // StatusBar re-fetches /api/status on mount and would otherwise overwrite
  // the seeded state with a 404.
  ROUTES = [[/\/api\/status$/, () => status]];
}

// --- cells ------------------------------------------------------------------

export function Racing() {
  // t = 8 s in the demo lap: flat out in sixth, full throttle, 232 km/h.
  live(frameAt(8_000));
  return (
    <Page>
      <LiveView />
    </Page>
  );
}

export function Braking() {
  // t = 3 s: hardest braking of the lap — off the throttle, full brake, a
  // downshift suggested, and the live delta at its worst.
  live(frameAt(3_000, { fuel_level: 8.4, current_lap: 4 }));
  return (
    <Page>
      <LiveView />
    </Page>
  );
}

export function WaitingForTelemetry() {
  // No frame has ever arrived: the view's own empty state, and the first thing
  // anyone sees on a fresh install with the console IP still wrong.
  live(null, { ...STATUS, connected: false, session_id: null, track_name: "" });
  return (
    <Page>
      <LiveView />
    </Page>
  );
}
