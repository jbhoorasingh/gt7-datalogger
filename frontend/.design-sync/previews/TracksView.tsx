// TracksView — track and survey management: what circuits this installation
// knows, which of them have a surveyed bundle, and where the two disagree.
//
// Everything on the screen comes from one endpoint, /api/track-overview, plus
// the optional shared-repo listing. The preview answers both with the real
// payloads (../fixtures/tracks), so every row and gap badge is the view
// reading this installation's actual track knowledge.
//
// Ten of the sixteen circuits carry a real survey bundle, so the half of the
// view that matters is live: per-border coverage meters, point and run counts,
// the confirmed official layout, the corner and section counts, and the
// enabled Corners… entry into the editor. The six without one are the view's
// thesis — the disagreement between what is named and what is surveyed — and
// the second cell is scrolled to where the two meet.

import { StatusBar, TracksView, useTelemetry } from "gt7-datalogger-frontend";
import { useEffect, useRef, type ReactNode } from "react";
import { STATUS } from "../fixtures/app";
import { TRACK_OVERVIEW } from "../fixtures/tracks";

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

const TRACK_ROUTES: [RegExp, Handler][] = [
  [/\/api\/status$/, () => STATUS],
  [/\/api\/track-overview/, () => TRACK_OVERVIEW],
  // No shared bundle repo is configured on this installation, which is what
  // hides the "pull from the shared repo" section entirely.
  [/\/api\/track-bundles\/shared/, () => ({ configured: false, bundles: [] })],
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
        <StatusBar view="tracks" />
        <main id="ds-main" className="min-h-0 flex-1 overflow-y-auto px-5 pb-7 pt-3.5">
          {children}
        </main>
      </div>
    </>
  );
}

/** Scroll the app's own <main> until a circuit's row is at the top. Keyed on
 *  the name the row prints, so it lands on the same circuit at any width. */
function useScrollToRow(name: string) {
  const done = useRef(false);
  useEffect(() => {
    let frames = 0;
    let raf = 0;
    const tick = () => {
      if (done.current) return;
      const main = document.getElementById("ds-main");
      const row = [...document.querySelectorAll<HTMLElement>("h3, .panel h3, span")].find(
        (el) => el.textContent?.trim() === name,
      );
      if (main && row) {
        done.current = true;
        main.scrollTop +=
          row.getBoundingClientRect().top - main.getBoundingClientRect().top - 12;
        return;
      }
      if (frames++ < 240) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [name]);
}

// --- cells ------------------------------------------------------------------

export function Tracks() {
  ROUTES = TRACK_ROUTES;
  useTelemetry.setState({ status: STATUS, wsConnected: true });
  return (
    <Page>
      <TracksView />
    </Page>
  );
}

export function SurveyedAndNot() {
  // Where the surveyed circuits give way to the ones that are only a name:
  // Mount Panorama with 6,642 points, 23 corners and both borders at ~96 %,
  // against Sim Ring and Suzuka with "no survey" and nothing to export.
  ROUTES = TRACK_ROUTES;
  useTelemetry.setState({ status: STATUS, wsConnected: true });
  useScrollToRow("Mount Panorama Motor Racing Circuit");
  return (
    <Page>
      <TracksView />
    </Page>
  );
}

export function NothingSurveyed() {
  // A fresh install: no named tracks, no bundles, no survey runs. The whole
  // point of the view is the gap between what is known and what is surveyed,
  // and here it is entirely gap.
  ROUTES = [
    [/\/api\/status$/, () => ({ ...STATUS, session_id: null, track_name: "" })],
    [/\/api\/track-overview/, () => ({ ...TRACK_OVERVIEW, tracks: [], logs: [] })],
    [/\/api\/track-bundles\/shared/, () => ({ configured: false, bundles: [] })],
  ];
  useTelemetry.setState({
    status: { ...STATUS, session_id: null, track_name: "" },
    wsConnected: true,
  });
  return (
    <Page>
      <TracksView />
    </Page>
  );
}
