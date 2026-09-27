// OverlayView — the stream / OBS overlay (/overlay).
//
// The whole configuration is the route, so each cell is one real overlay URL:
// the shipped default strip, the phone preset's grid, and the chroma-key page
// a webview without alpha support needs. Both presets are the app's own
// exported constants, not numbers typed out here.
//
// One caveat worth stating: the overlay's own default page is TRANSPARENT,
// because OBS composites it over the game. A transparent page on a preview
// card is a white page, which says nothing true about the design — so the
// strip cell renders the same config with `page: "dark"` (a real option, the
// one a phone or second monitor uses) and the chroma cell shows the green.
//
// Frames come from `liveFrameRef`, pinned to one moment of the app's own demo
// lap so the widgets read the same values every capture.

import {
  DEFAULT_CONFIG,
  DEMO_LAPS,
  OverlayView,
  PHONE_PRESET,
  demoFrame,
  liveFrameRef,
  useTelemetry,
  type OverlayConfig,
} from "gt7-datalogger-frontend";

const realFetch = window.fetch.bind(window);
window.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
  const url =
    typeof input === "string" ? input : input instanceof URL ? input.href : (input as Request).url;
  if (!url.includes("/api/")) return realFetch(input as RequestInfo, init);
  return Promise.resolve(new Response("no fixture", { status: 404 }));
}) as typeof window.fetch;

/** DEMO_LAPS carries no car id, so it pairs with any frame — which is what
 *  the fuel and strategy widgets need to project anything at all. */
function live(ms: number, patch: Record<string, unknown> = {}) {
  liveFrameRef.current = { ...demoFrame(ms), ...patch } as never;
  liveFrameRef.at = performance.now();
  useTelemetry.setState({ wsConnected: true, recentLaps: DEMO_LAPS });
}

function Page({ green = false }: { green?: boolean }) {
  return (
    <style>
      {`body{background:${green ? "#00ff00" : "var(--color-surface)"};color:var(--color-ink);margin:0;padding:0}
        .ds-single{height:100vh}`}
    </style>
  );
}

function Legacy({ config }: { config: OverlayConfig }) {
  return <OverlayView route={{ kind: "legacy", config }} />;
}

// --- cells ------------------------------------------------------------------

export function BottomStrip() {
  // The shipped default: gear, speed, RPM, inputs, lap times, tyres and fuel
  // in a 1920x260 bar along the bottom of the stream, at 70 % card opacity.
  live(8_000);
  return (
    <>
      <Page />
      {/* size: null fills the window rather than rendering the shipped
          1920x260 OBS canvas, which would overflow the card and clip; the
          global scale (a real overlay knob, 0.5–2) then gives the strip the
          share of the frame it has on a stream, instead of a small pill
          floating in a large empty page. */}
      <Legacy config={{ ...DEFAULT_CONFIG, page: "dark", size: null, scale: 1.6 }} />
    </>
  );
}

export function PhoneGrid() {
  // The phone preset: nine widgets two-up on a solid dark page, filling
  // whatever screen it is opened on. Scaled up (the same global knob, 0.5–2),
  // because this layout is read at arm's length on a phone or a tablet propped
  // beside the wheel — at 1:1 in a 1600px card it is a thin centred column.
  live(3_000);
  return (
    <>
      <Page />
      <Legacy config={{ ...PHONE_PRESET, scale: 1.8 }} />
    </>
  );
}

export function ChromaKey() {
  // The same strip on the chroma-key page, for streaming apps whose browser
  // source has no alpha channel. The card stays — the view forces its opacity
  // to 1 on a green page, because a translucent card lets green bleed through
  // and keys out badly.
  live(8_000);
  return (
    <>
      <Page green />
      <Legacy
        config={{
          ...DEFAULT_CONFIG,
          page: "green",
          align: "center",
          size: null,
          scale: 1.6,
        }}
      />
    </>
  );
}
