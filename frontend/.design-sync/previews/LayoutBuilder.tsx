// The layout builder: the whole tool that produces the LayoutConfigs
// GridRenderer draws — starting points and saved layouts, the canvas options
// and widget palette in the left rail, the drag-and-drop canvas, and the OBS /
// driver-screen URLs at the bottom.
//
// It takes one prop (`flash`) and drives itself from localStorage and the
// server, so these cells set up that environment rather than passing props:
//
//   - the DRAFT is seeded into `gt7-layout-draft`, the same key the builder
//     writes on every edit: the app's own DEFAULT_LAYOUT with `demo: true`, so
//     the canvas shows live placeholder telemetry, saved under a name so the
//     tool is in its "editing a saved layout" state rather than "unsaved
//     draft";
//   - `GET /api/layouts` and `GET /api/admin/stats` are stubbed. The stats
//     payload is the real ADMIN_STATS fixture, whose LAN address is already
//     sanitised, so the two URL rows show what the builder really renders.
//
// With the 1400 px viewport the builder is in its real `xl` two-column shape —
// rail on the left, canvas on the right — and the whole tool fits one
// screenful, so the cells differ by STATE rather than by scroll position.

import { DASH_PRESETS, DEFAULT_LAYOUT, LayoutBuilder } from "gt7-datalogger-frontend";
import { useEffect, useRef, type ReactNode } from "react";
import { ADMIN_STATS } from "../fixtures/app";
import { DarkPage } from "../preview-shell";

// The draft the builder opens on: the app's own default strip, with the
// placeholder-telemetry switch on so the canvas has something to draw.
const OBS_STRIP = { ...DEFAULT_LAYOUT, demo: true };
// `demo` is a persisted field of a layout, so a dash saved for a second screen
// can carry it. Serving it that way means loading this layout needs no edit —
// the draft stays clean, and the cell shows the saved-layout URLs rather than
// the unsaved-changes warning.
const DASH = { ...DASH_PRESETS["endurance"].layout, demo: true };

const now = "2026-09-20T18:40:00Z";
const SAVED = [
  { id: 1, name: "race-strip", kind: "overlay", config: OBS_STRIP, created_at: now, updated_at: now },
  { id: 2, name: "endurance-dash", kind: "dash", config: DASH, created_at: now, updated_at: now },
];

try {
  localStorage.setItem(
    "gt7-layout-draft",
    JSON.stringify({ layout: OBS_STRIP, id: 1, name: "race-strip", kind: "overlay" }),
  );
} catch {
  // private mode — the builder falls back to its own default draft
}

const STUB: Record<string, unknown> = {
  "/api/layouts": SAVED,
  "/api/admin/stats": ADMIN_STATS,
};

const nativeFetch = window.fetch.bind(window);
window.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
  const url = typeof input === "string" ? input : String((input as Request).url ?? input);
  const path = url.replace(/^https?:\/\/[^/]+/, "").split("?")[0];
  if (path in STUB) {
    return Promise.resolve(
      new Response(JSON.stringify(STUB[path]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
  }
  return nativeFetch(input as RequestInfo, init);
}) as typeof window.fetch;

/** Drive the builder's own controls. Steps run one React tick apart: a
 *  control read in the same tick as a layout change still sees the previous
 *  draft out of its closure. */
function useStage(steps: ((root: HTMLElement) => void)[]) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const root = box.current;
    if (!root || steps.length === 0) return;
    let tries = 0;
    const timers: number[] = [];
    const start = () => {
      if (!root.querySelector("button")) {
        if (tries++ < 60) requestAnimationFrame(start);
        return;
      }
      steps.forEach((step, i) => timers.push(window.setTimeout(() => step(root), i * 140)));
    };
    requestAnimationFrame(start);
    return () => timers.forEach(clearTimeout);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return box;
}

const press = (root: HTMLElement, text: string) =>
  [...root.querySelectorAll("button")].find((b) => b.textContent?.trim() === text)?.click();

/** The preset layouts do not ask for placeholder data; turn it on so the
 *  canvas shows readouts rather than widget names. */
const demoOn = (root: HTMLElement) => {
  const box = root.querySelector<HTMLInputElement>('input[type="checkbox"]');
  if (box && !box.checked) box.click();
};

function Builder({ boxRef }: { boxRef?: React.RefObject<HTMLDivElement | null> }) {
  // AdminView gives the builder the full width of the page; so does this.
  return (
    <div ref={boxRef}>
      <LayoutBuilder flash={() => {}} />
    </div>
  );
}

/** AdminView gives the builder the whole page, and at 1400x1000 the tool is
 *  within ~30 px of filling the capture — so the page itself is the dark
 *  ground here rather than an inset `Surface`, whose padding and body margin
 *  were enough to push the LAN URL row off the bottom. */
function Card({ children }: { children: ReactNode }) {
  return (
    <div className="font-tabular">
      <DarkPage />
      {children}
    </div>
  );
}

export function EditingSavedLayout() {
  // The whole tool on a saved overlay: starting points and the two saved
  // layouts, "Editing race-strip" with Rename and Delete live, the canvas
  // options and the full widget palette down the rail, the live canvas at 44%
  // of its true 1920x260, and the two URLs the saved layout gets.
  return (
    <Card>
      <Builder />
    </Card>
  );
}

export function LoadSavedDash() {
  // Loading the other saved layout from the server list: the kind flips to
  // Driver dash, the canvas becomes a fill-the-screen grid with live readouts
  // (the saved layout carries `demo`), and the URLs switch to /dash.
  const box = useStage([(root) => press(root, "endurance-dash")]);
  return (
    <Card>
      <Builder boxRef={box} />
    </Card>
  );
}

export function DriverDashDraft() {
  // Started again from the built-in race-engineer dashboard: eleven widgets on
  // a fill-the-screen canvas, and because the draft is now unnamed the URL
  // block falls back to "Save the layout to get a short URL".
  const box = useStage([(root) => press(root, "Race engineer dash"), demoOn]);
  return (
    <Card>
      <Builder boxRef={box} />
    </Card>
  );
}

export function GreenScreenEdit() {
  // The page control switched to chroma key: the canvas paints the real
  // #00ff00 an OBS source keys out, and because the saved layout now has an
  // unsaved edit the URL block warns that the links still serve the old one.
  const box = useStage([(root) => press(root, "Green screen")]);
  return (
    <Card>
      <Builder boxRef={box} />
    </Card>
  );
}
