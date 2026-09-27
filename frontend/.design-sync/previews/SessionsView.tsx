// SessionsView — a whole application screen.
//
// Views differ from every other component in this system: they mount their own
// data. SessionsView asks /api/sessions on mount, /api/sessions/<id>/laps for
// every row's sparkline, and again when a row is expanded. With nothing behind
// the card those requests fail and the view honestly shows its skeleton — so
// the preview puts the datalogger's own API back, answering with the payloads
// the real endpoints really returned (../fixtures/app, exported from the
// user's own database through the running backend). Nothing here draws the UI:
// every pixel is the view rendering real sessions.

import { SessionsView, StatusBar, useTelemetry } from "gt7-datalogger-frontend";
import { useEffect, useRef, type ReactNode } from "react";
import { FUEL_LAPS, LAPS, PERSONAL_BESTS, SESSIONS, STATUS } from "../fixtures/app";

// --- the API, as the app's own fetch layer sees it ---------------------------
//
// api.ts goes through the global `fetch`, so one patch serves every endpoint.
// `ROUTES` is assigned in each cell's body (synchronously, before its effects
// run), because all cells of a card share one page.

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
  // Endpoints the fixtures don't cover answer 404, which is what the view's
  // own error handling is written against.
  return Promise.resolve(new Response("no fixture", { status: 404 }));
}) as typeof window.fetch;

// Laps per session id: 228 is the Tsukuba AE86 stint, 194 the Mount Panorama
// 911 with fuel. The other three sessions in the list have no exported laps,
// so their rows show no sparkline — exactly as a row whose laps have not been
// fetched yet does.
const LAPS_BY_SESSION: Record<string, unknown> = { "228": LAPS, "194": FUEL_LAPS };

const SESSION_ROUTES: [RegExp, Handler][] = [
  [/\/api\/status$/, () => STATUS],
  [
    /\/api\/sessions\/(\d+)\/laps$/,
    (url) => LAPS_BY_SESSION[/sessions\/(\d+)\/laps/.exec(url)![1]] ?? [],
  ],
  [/\/api\/sessions/, () => SESSIONS],
  // The personal-bests board: the fastest counting lap per circuit and car.
  [/\/api\/laps\/bests/, () => ({ bests: PERSONAL_BESTS })],
];

// --- the page ---------------------------------------------------------------
//
// A view fills the window, so the card's page is the app's page: the same
// dark ground, the same StatusBar over the same scrolling <main> as App.tsx.

function Page({ view, children }: { view: "sessions" | "live"; children: ReactNode }) {
  return (
    <>
      <style>
        {`body{background:var(--color-surface);color:var(--color-ink);margin:0;padding:0}
          .ds-single{height:100vh}`}
      </style>
      <div className="flex h-full flex-col">
        <StatusBar view={view} />
        <main className="min-h-0 flex-1 overflow-y-auto px-5 pb-7 pt-3.5">{children}</main>
      </div>
    </>
  );
}

function seedChrome() {
  // The status bar reads the telemetry store as well as /api/status; seeding
  // it is what makes the connection readout show a connected server.
  useTelemetry.setState({ status: STATUS, wsConnected: true });
}

/** Press one of the view's own controls once its data has arrived.
 *  The lap table only exists behind a row's expand chevron and the component
 *  takes no `expanded` prop, so the cell clicks rather than fakes it. */
function useClickWhenReady(selector: string, index = 0) {
  const done = useRef(false);
  useEffect(() => {
    let frames = 0;
    let raf = 0;
    const tick = () => {
      if (done.current) return;
      const els = document.querySelectorAll<HTMLElement>(selector);
      if (els[index]) {
        done.current = true;
        els[index].click();
        return;
      }
      if (frames++ < 240) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [selector, index]);
}

// --- cells ------------------------------------------------------------------

export function Sessions() {
  ROUTES = SESSION_ROUTES;
  seedChrome();
  return (
    <Page view="sessions">
      <SessionsView />
    </Page>
  );
}

export function SessionOpen() {
  ROUTES = SESSION_ROUTES;
  seedChrome();
  // Row 4 is session #228 (Tsukuba, AE86) — the one whose eight laps are in
  // the fixtures, so the lap table below it is that session's real laps.
  useClickWhenReady('button[aria-label="Expand session"]', 3);
  return (
    <Page view="sessions">
      <SessionsView />
    </Page>
  );
}

export function Bests() {
  // The same history read the other way: the bests board, folded into this
  // view as a sub-tab (#26) so the category filter and the top-level actions
  // are shared. Seven circuits, each with its fastest lap that counts.
  ROUTES = SESSION_ROUTES;
  seedChrome();
  return (
    <Page view="sessions">
      <SessionsView subTab="bests" />
    </Page>
  );
}

export function NoSessions() {
  // A fresh install: the server answers, there is simply nothing recorded.
  const idle = { ...STATUS, recording: false, session_id: null, track_name: "" };
  ROUTES = [
    [/\/api\/status$/, () => idle],
    [/\/api\/sessions/, () => []],
  ];
  useTelemetry.setState({ status: idle, wsConnected: true });
  return (
    <Page view="sessions">
      <SessionsView />
    </Page>
  );
}
