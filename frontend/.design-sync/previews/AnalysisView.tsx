// AnalysisView — the lap-comparison screen, and the deepest data dependency in
// the app: on mount it asks for the session list, that session's laps, the
// multi-lap comparison, the deviation board, the engineer's coaching notes,
// the stint trend, the circuit outline and the class benchmark, then draws a
// race line, a playback transport and a stack of synced charts over them.
//
// With nothing behind the card the whole screen is a skeleton, so the preview
// puts the datalogger's API back and answers with the payloads it really
// returned: session 228 at Tsukuba in the AE86, laps 2, 4 and 7 compared
// against lap 2 (the session best). The selection is seeded into the shared
// analysis store — the same store Sessions and Live hand a selection over in —
// which is where the view reads its initial state from, so the three laps on
// screen are the three laps the fixture's comparison was computed for.
//
// The comparison served here is COMPARE_FULL: the same three laps with every
// channel the recording holds. That is what the wide bottom rack needs — the
// per-wheel slip/temperature/suspension columns for Corner detail, the
// accelerometers for the traction circle, the aid bits for the TCS and ASM
// layers on the race line, and steering for the playback strip. The lighter
// COMPARE would leave four of those panels out.

import {
  AnalysisView,
  StatusBar,
  useAnalysisSelection,
  useTelemetry,
} from "gt7-datalogger-frontend";
import { useEffect, useRef, type ReactNode } from "react";
import { LAPS, SESSIONS, STATUS } from "../fixtures/app";
import { COACHING, DEVIATION, REF_LAP, SELECTED } from "../fixtures/analysis";
import { COMPARE_FULL, GEARING, GEARING_LAP } from "../fixtures/analysis-full";

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

const ANALYSIS_ROUTES: [RegExp, Handler][] = [
  [/\/api\/status$/, () => STATUS],
  [/\/api\/sessions\/228\/laps$/, () => LAPS],
  [/\/api\/sessions\/\d+\/laps$/, () => []],
  [/\/api\/sessions/, () => SESSIONS],
  [/\/api\/analysis\/compare/, () => COMPARE_FULL],
  [/\/api\/analysis\/deviation/, () => DEVIATION],
  [/\/api\/analysis\/coaching/, () => COACHING],
  // The class benchmark: nothing faster has been recorded at Tsukuba in this
  // class, which is what the endpoint answers with.
  [/\/api\/laps\/best/, () => null],
  // The Gearing panel asks for the reference lap's own record; the ratios and
  // redline are the AE86's, captured with the lap.
  [
    /\/api\/laps\/(\d+)/,
    (url) => {
      const id = Number(/laps\/(\d+)/.exec(url)![1]);
      const lap = LAPS.find((l) => l.id === id) ?? LAPS[0];
      return { ...lap, gearing: id === GEARING_LAP ? GEARING : null };
    },
  ],
  [/\/api\/laps/, () => LAPS],
  // Stint trend and the surveyed outline are not in the fixtures for this
  // session, and both panels are written to simply not appear without them —
  // /api/analysis/stint and /api/track-outline fall through to a 404.
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
        <StatusBar view="analysis" />
        <main
          id="ds-main"
          className="min-h-0 flex-1 overflow-y-auto px-5 pb-7 pt-3.5"
        >
          {children}
        </main>
      </div>
    </>
  );
}

function seed(selection: { sessionId: number | null; selectedLapIds: number[]; refLapId: number | null }) {
  useAnalysisSelection.setState(selection);
  useTelemetry.setState({ status: STATUS, wsConnected: true });
}

/** Scroll the app's own <main> so a named panel sits at the top of the card
 *  ("at"), or just below its bottom edge ("above" — which frames whatever
 *  comes immediately before it). Anchored on the panel's own section header
 *  rather than a pixel offset, so it lands in the same place at any card
 *  width; the analysis screen is several viewports tall and every offset in it
 *  moves when the viewport does. */
function useScrollToSection(label: string, place: "at" | "above" = "at") {
  const done = useRef(false);
  useEffect(() => {
    let frames = 0;
    let raf = 0;
    const tick = () => {
      if (done.current) return;
      const main = document.getElementById("ds-main");
      // The chart stack is the one panel with no section header of its own;
      // `.panel.px-4.py-3` is unique to it in the view.
      const head =
        label === "#charts"
          ? document.querySelector<HTMLElement>(".panel.px-4.py-3")
          : [...document.querySelectorAll<HTMLElement>(".section-header")].find(
              (el) => el.textContent?.trim() === label,
            );
      if (main && head) {
        done.current = true;
        const offset =
          head.getBoundingClientRect().top - main.getBoundingClientRect().top;
        main.scrollTop += place === "at" ? offset - 10 : offset - main.clientHeight + 24;
        return;
      }
      if (frames++ < 240) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [label, place]);
}

// --- cells ------------------------------------------------------------------

export function Comparison() {
  ROUTES = ANALYSIS_ROUTES;
  seed({ sessionId: 228, selectedLapIds: SELECTED, refLapId: REF_LAP });
  return (
    <Page>
      <AnalysisView request={{}} />
    </Page>
  );
}

export function Charts() {
  // The same comparison, scrolled to the top of the synced chart stack: nine
  // channels over distance, three real laps in their assigned series colours,
  // starting at the time-diff trace that the whole screen is read from.
  ROUTES = ANALYSIS_ROUTES;
  seed({ sessionId: 228, selectedLapIds: SELECTED, refLapId: REF_LAP });
  useScrollToSection("#charts");
  return (
    <Page>
      <AnalysisView request={{}} />
    </Page>
  );
}

export function Panels() {
  // The analysis rack: the panels the session has data for, flowing three-up.
  // Corner detail, the traction circle and the gearing table are only here
  // because the comparison carries every channel.
  ROUTES = ANALYSIS_ROUTES;
  seed({ sessionId: 228, selectedLapIds: SELECTED, refLapId: REF_LAP });
  useScrollToSection("Corner detail — cursor synced");
  return (
    <Page>
      <AnalysisView request={{}} />
    </Page>
  );
}

export function NoSessions() {
  // A fresh install. The view distinguishes this from a failed fetch: the
  // skeleton only stays up while the list is genuinely on its way.
  ROUTES = [
    [/\/api\/status$/, () => ({ ...STATUS, session_id: null, track_name: "" })],
    [/\/api\/sessions/, () => []],
  ];
  seed({ sessionId: null, selectedLapIds: [], refLapId: null });
  return (
    <Page>
      <AnalysisView request={{}} />
    </Page>
  );
}
