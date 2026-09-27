// AdminView — settings, diagnostics, sync, the overlay/dashboard builder,
// the server log and data management, in one long column of panels.
//
// Every panel loads itself from /api/admin/*, so with nothing behind the card
// the screen is a stack of "could not load" notices. The preview answers with
// the payloads those endpoints really returned (../fixtures/app), sanitised of
// host details by the fixture exporter. The screen is several viewports tall,
// so the cells are the same screen at the two places worth looking at, plus
// the state an installation with GT7_ADMIN_TOKEN set shows before the token
// is entered.

import { AdminView, StatusBar, useTelemetry } from "gt7-datalogger-frontend";
import { useEffect, useRef, type ReactNode } from "react";
import { ADMIN_SETTINGS, ADMIN_STATS, ADMIN_SYNC, RACE_ENGINEER, STATUS } from "../fixtures/app";

// --- the API, as the app's own fetch layer sees it ---------------------------

type Handler = (url: string) => unknown;
let ROUTES: [RegExp, Handler][] = [];
let STATUS_CODE = 200;

const realFetch = window.fetch.bind(window);
window.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
  const url =
    typeof input === "string" ? input : input instanceof URL ? input.href : (input as Request).url;
  if (!url.includes("/api/")) return realFetch(input as RequestInfo, init);
  if (STATUS_CODE !== 200) {
    return Promise.resolve(new Response("admin token required", { status: STATUS_CODE }));
  }
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

const ADMIN_ROUTES: [RegExp, Handler][] = [
  [/\/api\/admin\/settings/, () => ADMIN_SETTINGS],
  [/\/api\/admin\/stats/, () => ADMIN_STATS],
  [/\/api\/admin\/sync/, () => ADMIN_SYNC],
  [/\/api\/admin\/race-engineer/, () => RACE_ENGINEER],
  // No log records and no saved layouts were exported, and both panels are
  // written for an installation that has neither.
  [/\/api\/admin\/logs/, () => []],
  [/\/api\/layouts/, () => []],
  [/\/api\/status$/, () => STATUS],
];

// --- the page ---------------------------------------------------------------

function Page({ children }: { children: ReactNode }) {
  return (
    <>
      <style>
        {`body{background:var(--color-surface);color:var(--color-ink);margin:0;padding:0}
          .ds-single{height:100vh}`}
      </style>
      <div className="flex h-full flex-col">
        <StatusBar view="admin" />
        <main id="ds-main" className="min-h-0 flex-1 overflow-y-auto px-5 pb-7 pt-3.5">
          {children}
        </main>
      </div>
    </>
  );
}

/** Scroll the app's own <main> so a named panel sits at the top. Keyed on the
 *  panel's own section header rather than a pixel offset, so the cell lands on
 *  the same panel whatever width the card is captured at. */
function useScrollToSection(label: string) {
  const done = useRef(false);
  useEffect(() => {
    let frames = 0;
    let raf = 0;
    const tick = () => {
      if (done.current) return;
      const main = document.getElementById("ds-main");
      const head = [...document.querySelectorAll<HTMLElement>(".section-header")].find(
        (el) => el.textContent?.trim() === label,
      );
      if (main && head) {
        done.current = true;
        main.scrollTop +=
          head.getBoundingClientRect().top - main.getBoundingClientRect().top - 10;
        return;
      }
      if (frames++ < 240) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [label]);
}

function seed() {
  useTelemetry.setState({ status: STATUS, wsConnected: true });
}

// --- cells ------------------------------------------------------------------

export function Settings() {
  // The top of the screen: how telemetry reaches the datalogger, beside the
  // live health figures that refresh every five seconds.
  STATUS_CODE = 200;
  ROUTES = ADMIN_ROUTES;
  seed();
  return (
    <Page>
      <AdminView />
    </Page>
  );
}

export function Sync() {
  // Further down the same screen: where this installation stands with the
  // sync service, and the per-data-type switches for what it sends.
  STATUS_CODE = 200;
  ROUTES = ADMIN_ROUTES;
  seed();
  useScrollToSection("Sync");
  return (
    <Page>
      <AdminView />
    </Page>
  );
}

export function TokenRequired() {
  // An installation started with GT7_ADMIN_TOKEN set, opened from a device
  // that has not been given the token: every admin endpoint answers 401 and
  // the Connection panel becomes the place to enter it.
  STATUS_CODE = 401;
  ROUTES = [];
  seed();
  return (
    <Page>
      <AdminView />
    </Page>
  );
}
