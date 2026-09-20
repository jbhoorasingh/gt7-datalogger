// What the Analysis view's features are, in a sentence or two each, for the
// in-app guide. The documentation site holds the long form: every entry links
// to its section there, and says nothing here that would go stale when a
// threshold changes. The channels' own descriptions live in channels.ts.

import type { ChannelDef } from "@/lib/channels";

export const DOCS_SITE = "https://jbhoorasingh.github.io/gt7-datalogger/";

/** Absolute docs URL for a site path like "guide/analysis-view/#corners". */
export function docsUrl(path: string): string {
  return DOCS_SITE + path;
}

export interface GuideFeature {
  title: string;
  body: string;
  /** Docs site path, "page/#anchor"; a test checks it resolves. */
  docs: string;
}

const VIEW = "guide/analysis-view/";

export const GUIDE_FEATURES: GuideFeature[] = [
  {
    title: "Choosing laps",
    body:
      "Click a lap chip to add or remove it; double-click one (or use the ref menu) to make it the reference everything is measured against — by default the session's quickest lap that counts. Add lap… brings in a lap from another session at the same circuit.",
    docs: `${VIEW}#selecting-laps`,
  },
  {
    title: "Time diff",
    body:
      "The top chart: each lap's gap to the reference at each point of the track, in seconds. Positive is slower; where the line climbs is where time goes.",
    docs: `${VIEW}#stacked-charts`,
  },
  {
    title: "Laps lined up by place",
    body:
      "Every lap is put on the reference lap's distance by where it was on track, so the charts compare the same point of a corner even when one lap took a wider line.",
    docs: "internals/analysis-math/#lining-laps-up-by-place-on-track",
  },
  {
    title: "Cursor and zoom",
    body:
      "Hover any chart to move the cursor everywhere — charts, map, Corner Detail and traction circle. Drag across a chart to zoom; double-click or Full resets, and S1–S3 jump to thirds of the lap.",
    docs: `${VIEW}#stacked-charts`,
  },
  {
    title: "Event shading",
    body:
      "Lockups shade Brake, wheelspin shades Throttle, bottoming and kerb strikes shade the suspension panels, and traction-control and ASM activity shade Throttle and Speed.",
    docs: `${VIEW}#stacked-charts`,
  },
  {
    title: "Playback",
    body:
      "Plays the reference lap at 0.25–4× speed on the lap's own clock, so the cursor lingers in slow corners. The strip beside it shows steering, pedals, gear and speed.",
    docs: `${VIEW}#lap-playback`,
  },
  {
    title: "Race line",
    body:
      "The reference lap from above, coloured by what the driver was doing — green throttle, red brake, blue coasting — with ▲ speed peaks and ▼ valleys. Other laps are drawn in their chart colours.",
    docs: `${VIEW}#race-line-map`,
  },
  {
    title: "Sync: Time or Position",
    body:
      "Time draws each lap's car where it was after the same lap time as the reference, so a gap shows as distance on track. Position draws them level with the reference, where the charts compare them.",
    docs: `${VIEW}#position-or-time-sync`,
  },
  {
    title: "Follow",
    body:
      "While playing, zooms the map in and pans with the reference car instead of framing the whole circuit.",
    docs: `${VIEW}#race-line-map`,
  },
  {
    title: "Events and aids on the map",
    body:
      "Events marks where each lockup ◆, wheelspin ●, bottoming ✚ and kerb strike ✖ began, in the lap's colour — hover for the wheels, click to zoom there. TCS and ASM ring every sample the aid was working.",
    docs: `${VIEW}#events-and-driver-aids`,
  },
  {
    title: "Corners",
    body:
      "Numbered circles on the map — the circuit's labelled corners, or corners found on the reference lap. Click one, or step through the strip under the map, to zoom everything to it.",
    docs: `${VIEW}#corners`,
  },
  {
    title: "Full screen and the surveyed road",
    body:
      "⤢ opens the map full screen, with scroll to zoom and drag to pan. On a surveyed circuit the road, its borders and walls are drawn under the lines; dashed amber marks stretches not surveyed yet.",
    docs: `${VIEW}#full-screen`,
  },
  {
    title: "Corner report card",
    body:
      "Entry, minimum and exit speed and the time through each corner, sorted by time lost against the reference. Click a row to zoom to that corner.",
    docs: `${VIEW}#corner-report-card`,
  },
  {
    title: "Traction circle (g-g)",
    body:
      "Lateral against longitudinal g for every moment of the lap — how much of the circle a lap uses is the reading. Needs packet B or wider; the footnote says whether the scale could be verified.",
    docs: `${VIEW}#traction-circle-g-g`,
  },
  {
    title: "Corner Detail",
    body:
      "The car from above at the cursor: tyre temperature as colour, suspension as bars, LOCK and SPIN badges. The small figures beside each wheel are the reference lap's.",
    docs: `${VIEW}#corner-detail-widget`,
  },
  {
    title: "Partial, excluded, salvaged",
    body:
      "Partial laps didn't time the whole circuit — a pit out-lap, or a race's lap 1 from the grid. Excluded laps were ruled out of bests by hand. Neither sets a best. Salvaged (⟲) laps were recovered from a stream that ended at the line, such as a replay.",
    docs: "guide/sessions-view/#excluding-a-lap-from-bests",
  },
  {
    title: "Side panels",
    body:
      "Race engineer notes for the session, the reference lap's tuning figures and class benchmark, gearing, fuel strategy, consistency across the best laps, and every lap's time against the session's median and spread.",
    docs: `${VIEW}#side-panels`,
  },
  {
    title: "Sharing a view",
    body:
      "The address bar holds the session, laps, reference and chart channels, so a copied link opens this exact comparison.",
    docs: VIEW,
  },
];

/** Where the docs say how a channel is computed: in the browser, from a
 *  column only some recordings carry, or from one every recording has. */
export function channelDocs(c: ChannelDef): string {
  const page = "internals/derived-channels/";
  if (c.derive) return `${page}#frontend-derived-channels`;
  return c.needs ? `${page}#optional-columns` : `${page}#stored-sample-columns`;
}

/** Case-insensitive match on any of the given texts. */
export function matchesQuery(query: string, ...texts: (string | undefined)[]): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return texts.some((t) => t?.toLowerCase().includes(q));
}
