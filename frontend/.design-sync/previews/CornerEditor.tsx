// The refine view: walk a surveyed track and label its corners. The map is
// drawn from a track bundle's own border points, the list is the corners
// authored against it.
//
// WHERE THE DATA COMES FROM
// -------------------------
// CornerEditor has no data props — it fetches `/api/track-bundles/<slug>` on
// mount — so these cells serve that one path from a stubbed `fetch`, keyed by
// slug so all four can share a page.
//
// The geometry is REAL and recorded: `TRACK_OUTLINE` is the compiled survey of
// Mount Panorama, three runs, 95.9 % / 95.8 % border coverage. Its `road` is
// quads of [left pair, right pair] — exactly what `roadQuads()` emits — so the
// left and right border points and their travel headings come back out of it
// losslessly (median road width 7.1 m), which is precisely the `SurveyEdge[]`
// shape the bundle doc carries. The start/finish line and the official match
// (23 turns) are the fixture's own as well.
//
// The one thing no fixture carries is the CORNERS. `TRACK_OUTLINE` holds
// geometry only, and `TRACK_OVERVIEW`'s bundle rows carry corner *counts*, not
// positions — so the two cells that need a populated list place their apexes
// by reading curvature off the surveyed road itself (heading change over 22 m,
// local maxima at least 70 m apart), which is the same thing `detect_corners()`
// does from laps. Real road, computed positions, nobody's labels. The other
// two cells need none: six of the seven surveyed bundles in the fixture really
// are at `corners: 0`, so "surveyed, not yet labelled" is the majority state.
//
// The upstream fix is a `TRACK_BUNDLE` fixture — the `/api/track-bundles/<slug>`
// doc for one circuit that has been labelled (Spa has 19 corners and a section,
// Daytona Tri-Oval 4 and 2) — which would carry geometry and corners together
// and let this file drop both the recovery and the curvature pass.
// See .design-sync/learnings/layout.md.

import { CornerEditor } from "gt7-datalogger-frontend";
import { useEffect, useRef } from "react";
import { TRACK_OUTLINE, TRACK_OVERVIEW } from "../fixtures/tracks";
import { Surface } from "../preview-shell";

const SLUG = TRACK_OUTLINE.slug ?? "mount-panorama-motor-racing-circuit";
const row = TRACK_OVERVIEW.tracks.find((t) => t.slug === SLUG);
const OFFICIAL = row?.bundle?.official ?? null;

type Edge = { x: number; z: number; hx: number; hz: number; side: "L" | "R"; kind: "edge" };

// Each road quad is [l-a, l+a, r+a, r-a] with a = heading × half-length, so the
// two border points are the midpoints of its first and second vertex pairs and
// the heading is the first edge of the quad.
const EDGES: Edge[] = [];
const MID: { x: number; z: number; hx: number; hz: number }[] = [];
for (const q of TRACK_OUTLINE.road) {
  const lx = (q[0] + q[2]) / 2;
  const lz = (q[1] + q[3]) / 2;
  const rx = (q[4] + q[6]) / 2;
  const rz = (q[5] + q[7]) / 2;
  const n = Math.hypot(q[2] - q[0], q[3] - q[1]) || 1;
  const hx = (q[2] - q[0]) / n;
  const hz = (q[3] - q[1]) / n;
  EDGES.push({ x: lx, z: lz, hx, hz, side: "L", kind: "edge" });
  EDGES.push({ x: rx, z: rz, hx, hz, side: "R", kind: "edge" });
  MID.push({ x: (lx + rx) / 2, z: (lz + rz) / 2, hx, hz });
}

// Curvature off the surveyed road: how far the centre line has turned 22 m
// ahead of each point. Order-free — the point ahead is found by position, the
// way the survey's own geometry code works.
const AHEAD_M = 22;
const CELL_M = 40;
const grid = new Map<string, number[]>();
MID.forEach((p, k) => {
  const key = `${Math.floor(p.x / CELL_M)}:${Math.floor(p.z / CELL_M)}`;
  const bucket = grid.get(key);
  if (bucket) bucket.push(k);
  else grid.set(key, [k]);
});

function turnAt(k: number): { ang: number; dir: "L" | "R" } | null {
  const p = MID[k];
  const tx = p.x + p.hx * AHEAD_M;
  const tz = p.z + p.hz * AHEAD_M;
  let best = -1;
  let bd = 8;
  const cx = Math.floor(tx / CELL_M);
  const cz = Math.floor(tz / CELL_M);
  for (let gx = cx - 1; gx <= cx + 1; gx++) {
    for (let gz = cz - 1; gz <= cz + 1; gz++) {
      for (const m of grid.get(`${gx}:${gz}`) ?? []) {
        const d = Math.hypot(MID[m].x - tx, MID[m].z - tz);
        if (d < bd) {
          bd = d;
          best = m;
        }
      }
    }
  }
  if (best < 0) return null;
  const q = MID[best];
  const dot = Math.max(-1, Math.min(1, p.hx * q.hx + p.hz * q.hz));
  return {
    ang: (Math.acos(dot) * 180) / Math.PI,
    dir: p.hx * q.hz - p.hz * q.hx > 0 ? "R" : "L",
  };
}

const CORNERS = (() => {
  const scored = MID.map((_, k) => ({ k, t: turnAt(k) }))
    .filter((s): s is { k: number; t: { ang: number; dir: "L" | "R" } } => (s.t?.ang ?? 0) > 18)
    .sort((a, b) => b.t.ang - a.t.ang);
  const kept: typeof scored = [];
  for (const c of scored) {
    const p = MID[c.k];
    if (kept.some((q) => Math.hypot(MID[q.k].x - p.x, MID[q.k].z - p.z) < 70)) continue;
    kept.push(c);
    if (kept.length >= 12) break;
  }
  return kept.map((c, i) => ({
    n: i + 1,
    name: "",
    direction: c.t.dir,
    apex: { x: Math.round(MID[c.k].x * 10) / 10, z: Math.round(MID[c.k].z * 10) / 10 },
    entry: null,
    exit: null,
    note: "",
  }));
})();

// A section between two of them — the optional half of the editor, and the
// only place real sectors (#22) can get a definition.
const SECTIONS = CORNERS.length >= 3
  ? [{ n: 1, name: "", start: CORNERS[0].apex, end: CORNERS[2].apex }]
  : [];

const finish = TRACK_OUTLINE.finish;

function bundle(corners: typeof CORNERS, sections: typeof SECTIONS) {
  return {
    format: "gt7-track-bundle",
    version: 5,
    meta: {
      track: TRACK_OUTLINE.track,
      runs: TRACK_OUTLINE.runs,
      source_runs: {},
      updated_at: TRACK_OUTLINE.updated_at,
      official: OFFICIAL,
    },
    edges: EDGES,
    finish_crossings: finish
      ? [
          {
            x: (finish[0] + finish[2]) / 2,
            z: (finish[1] + finish[3]) / 2,
            hx: 0,
            hz: 1,
            lap: 1,
          },
        ]
      : [],
    corners,
    sections,
  };
}

const DOCS: Record<string, unknown> = {
  "mp-labelled": bundle(CORNERS, []),
  "mp-selected": bundle(CORNERS, []),
  "mp-unlabelled": bundle([], []),
  "mp-sections": bundle(CORNERS, SECTIONS),
};

const nativeFetch = window.fetch.bind(window);
window.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
  const url = typeof input === "string" ? input : String((input as Request).url ?? input);
  const slug = /\/api\/track-bundles\/([^/?]+)/.exec(url)?.[1];
  const doc = slug && DOCS[slug];
  if (doc) {
    return Promise.resolve(
      new Response(JSON.stringify(doc), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
  }
  return nativeFetch(input as RequestInfo, init);
}) as typeof window.fetch;

/** Press the editor's own control, by its label, once it exists. */
function useLabelClick(label: string) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let tries = 0;
    const tick = () => {
      const hit = [...(box.current?.querySelectorAll("button") ?? [])].find(
        (b) => b.textContent?.trim() === label,
      );
      if (hit) hit.click();
      else if (tries++ < 60) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }, [label]);
  return box;
}

/** Select the nth corner row by clicking it, as a user would. */
function useRowClick(index: number) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let tries = 0;
    const tick = () => {
      const rows = box.current?.querySelectorAll<HTMLElement>(".rounded-lg.border.p-2");
      if (rows && rows.length > index) rows[index].click();
      else if (tries++ < 60) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }, [index]);
  return box;
}

/** The corner list is its own `max-h-[32rem]` scroller, so on a labelled track
 *  the Sections block starts below its fold. Scroll it there. */
function useScrollEnd() {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let tries = 0;
    const tick = () => {
      const list = box.current?.querySelector(".overflow-y-auto");
      if (list && list.scrollHeight > list.clientHeight) list.scrollTop = list.scrollHeight;
      else if (tries++ < 60) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }, []);
  return box;
}

// TracksView mounts the editor in `mx-auto max-w-[1200px]`. The shell's
// Surface is inline-block, so without a width of its own each cell would
// shrink-wrap to its list and give the map a different size — pin it to the
// call site's.
function Editor({ slug }: { slug: string }) {
  return (
    <div style={{ width: 1150 }}>
      <CornerEditor {...editorProps(slug)} />
    </div>
  );
}

const editorProps = (slug: string) => ({
  slug,
  trackName: TRACK_OUTLINE.track,
  official: OFFICIAL,
  onClose: () => {},
  onSaved: () => {},
});

export function Labelled() {
  // A labelled track: numbered apexes on the surveyed map, the badge counting
  // them against the official 23 turns, and the list to work down.
  return (
    <Surface>
      <Editor slug="mp-labelled" />
    </Surface>
  );
}

export function SelectedCorner() {
  // Corner 3 picked by clicking its row: orange apex on the map, and the
  // row's apex / entry / exit / reorder / remove controls open.
  const box = useRowClick(2);
  return (
    <Surface>
      <div ref={box}>
        <Editor slug="mp-selected" />
      </div>
    </Surface>
  );
}

export function PlacingApexes() {
  // A surveyed bundle nobody has labelled yet — the state six of the seven
  // surveyed bundles in the fixture are really in — with "Place corners"
  // armed: the button goes accent, the instruction changes, and the list
  // shows its empty state.
  const box = useLabelClick("Place corners");
  return (
    <Surface>
      <div ref={box}>
        <Editor slug="mp-unlabelled" />
      </div>
    </Surface>
  );
}

export function WithSections() {
  // The optional half: a section's start and end are green triangles on the
  // map, with its own row at the end of the corner list.
  const box = useScrollEnd();
  return (
    <Surface>
      <div ref={box}>
        <Editor slug="mp-sections" />
      </div>
    </Surface>
  );
}
