// SurveyView — the surface survey: drive a circuit and the view records where
// the tarmac ends, building the track's borders out of wheel-surface
// transitions.
//
// What can honestly be shown here is limited by the fixtures. /api/survey/status
// was captured with no run going (active: false, 0 edge points), and the two
// endpoints that carry the geometry — /api/survey/trail and /api/survey/edges —
// were never exported, so there is no surveyed road to draw. The view's map is
// therefore empty in every cell, and inventing border points would be inventing
// the one thing this screen exists to show.
//
// So there is one cell: the idle screen exactly as the fixture describes it,
// which is a complete screen in its own right — the controls that start a run,
// the completeness readout and the live per-wheel surface. A second cell for a
// RUNNING survey was written and then dropped: switching `active` on renders
// the map as a 420 px empty chart, because the borders it exists to draw have
// no fixture. Faking border points would be faking the only thing this screen
// is for.

import { StatusBar, SurveyView, useTelemetry } from "gt7-datalogger-frontend";
import type { ReactNode } from "react";
import { STATUS, SURVEY_STATUS } from "../fixtures/app";
import { TRACK_CATALOG } from "../fixtures/tracks";

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

function routes(status: unknown): [RegExp, Handler][] {
  return [
    [/\/api\/status$/, () => STATUS],
    [/\/api\/survey\/status/, () => status],
    // The track field autocompletes from the user's named tracks first, then
    // every official GT7 layout in the bundled catalog.
    [/\/api\/tracks$/, () => []],
    [/\/api\/track-catalog/, () => TRACK_CATALOG],
    [/\/api\/track-bundles$/, () => []],
    // No trail and no edges: this run has surveyed nothing yet.
    [/\/api\/survey\/(trail|edges)/, (url) => ({
      epoch: 0,
      since: Number(/since=(\d+)/.exec(url)?.[1] ?? 0),
      points: [],
      total: 0,
    })],
  ];
}

// --- the page ---------------------------------------------------------------

function Page({ children }: { children: ReactNode }) {
  return (
    <>
      <style>
        {`body{background:var(--color-surface);color:var(--color-ink);margin:0;padding:0}
          .ds-single{height:100vh}`}
      </style>
      <div className="flex h-full flex-col">
        <StatusBar view="survey" />
        <main className="min-h-0 flex-1 overflow-y-auto px-5 pb-7 pt-3.5">{children}</main>
      </div>
    </>
  );
}

// --- cells ------------------------------------------------------------------

export function Idle() {
  // No run going: the controls that start one, and the surface-character
  // readout that says whether the console is sending packet format C at all.
  ROUTES = routes(SURVEY_STATUS);
  useTelemetry.setState({ status: STATUS, wsConnected: true });
  return (
    <Page>
      <SurveyView />
    </Page>
  );
}
