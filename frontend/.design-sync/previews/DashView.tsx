// DashView — the full-screen driver dashboard (/dash), a second display for a
// tablet or a spare monitor.
//
// The view takes its whole configuration as a prop, so the cells are just the
// routes the product itself offers: the two built-in presets, and the
// telemetry-less state a screen sits in before the console starts talking.
//
// Frames arrive through `liveFrameRef`, the ref the WebSocket writes into.
// Pinning one frame there (rather than using ?demo=1, whose 20 s loop would
// screenshot at an arbitrary moment) makes the readouts deterministic and puts
// the connection dot on "live" instead of "placeholder". The fuel and strategy
// tiles project per car, so the frame carries the car the seeded laps were set
// in — the Mount Panorama 911 stint, the one exported session that burned fuel.

import {
  DASH_PRESETS,
  DEFAULT_DASH_PRESET,
  DashView,
  demoFrame,
  liveFrameRef,
  useTelemetry,
} from "gt7-datalogger-frontend";
import { FUEL_LAPS, SESSIONS, STATUS } from "../fixtures/app";

const realFetch = window.fetch.bind(window);
window.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
  const url =
    typeof input === "string" ? input : input instanceof URL ? input.href : (input as Request).url;
  // /dash only calls the API for a saved server layout; the preset cells never
  // do. A 404 is what an unsaved layout name really answers with.
  if (!url.includes("/api/")) return realFetch(input as RequestInfo, init);
  return Promise.resolve(new Response("no fixture", { status: 404 }));
}) as typeof window.fetch;

const PORSCHE = SESSIONS.find((s) => s.id === 194)!;

function DarkFullPage() {
  // /dash puts `overlay-page` on the body (solid dark, no chrome). The card's
  // own page style sets a white background after the app's stylesheet, so the
  // cell restates it, and gives the 100 %-height layout something to fill.
  return (
    <style>
      {`body{background:var(--color-surface);color:var(--color-ink);margin:0;padding:0}
        .ds-single{height:100vh}`}
    </style>
  );
}

function live(ms: number | null, patch: Record<string, unknown> = {}) {
  liveFrameRef.current =
    ms == null
      ? null
      : ({
          ...demoFrame(ms),
          car_id: FUEL_LAPS[0].car_id,
          car_name: PORSCHE.car_name,
          track_name: PORSCHE.track_name,
          ...patch,
        } as never);
  liveFrameRef.at = performance.now();
  useTelemetry.setState({
    status: STATUS,
    wsConnected: true,
    recentLaps: ms == null ? [] : FUEL_LAPS,
  });
}

// The two built-ins, named from the app's own preset table rather than typed
// out as URL keys.
const [RACE_ENGINEER_KEY, ENDURANCE_KEY] = [
  DEFAULT_DASH_PRESET,
  Object.keys(DASH_PRESETS).find((k) => k !== DEFAULT_DASH_PRESET)!,
];

const NO_PARAMS = { layout: null, preset: RACE_ENGINEER_KEY, demo: false };

// --- cells ------------------------------------------------------------------

export function RaceEngineer() {
  // The default preset: alerts across the top, then fuel, pit window, delta
  // and timing, with car health and race context below. Twenty-five litres
  // against the 911's ~10.9 L/lap is two and a bit laps of fuel — inside the
  // warn threshold, so the row the layout reserves for alerts is doing its
  // job rather than sitting empty.
  live(8_000, { fuel_level: 25, current_lap: 12, total_laps: 20 });
  return (
    <>
      <DarkFullPage />
      <DashView params={NO_PARAMS} />
    </>
  );
}

export function Endurance() {
  // The second built-in: fuel is the hero and engine health gets the wide
  // readout. Eight litres left is inside the low-fuel threshold, so the alert
  // banner the layout reserves its top row for is actually doing its job.
  live(13_500, { fuel_level: 8.2, current_lap: 21, total_laps: 24 });
  return (
    <>
      <DarkFullPage />
      <DashView params={{ layout: null, preset: ENDURANCE_KEY, demo: false }} />
    </>
  );
}

export function WaitingForTelemetry() {
  // A dashboard opened before the console is talking — what the second screen
  // shows for as long as nothing is arriving, including the way out of it.
  live(null);
  return (
    <>
      <DarkFullPage />
      <DashView params={NO_PARAMS} />
    </>
  );
}
