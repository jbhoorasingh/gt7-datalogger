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
      "Hover any chart, the map or the lap strip to move the cursor everywhere — charts, map, Corner Detail and traction circle. Drag across a chart, along the line on the map, or on the lap strip to zoom; drag the strip's edges to resize the window. ⌘/Ctrl + scroll on the map zooms about the pointer. Esc, a double-click or Full lap resets.",
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
      "The reference lap from above, coloured by the chosen lens: Inputs (green throttle, red brake, blue coasting), Speed, Gain / loss (where the compared lap gains and loses time), Tire temp (the hottest tire: cold, working, hot) or F−R temp (which axle's tires run hotter). The lens is remembered on this device and carried in the link. Other laps are drawn thin in their chart colours, brighter when zoomed in.",
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
    title: "Layers",
    body:
      "The Layers menu switches each thing the map draws besides the reference line: the surveyed road, corner numbers, the other laps with their apex speeds and brake points, and the markers — speed peaks, kerbs, events (lockup ◆, wheelspin ●, bottoming ✚, kerb strike ✖) and TCS / ASM rings. Only layers the laps can fill are listed.",
    docs: `${VIEW}#layers`,
  },
  {
    title: "Lap strip and sectors",
    body:
      "The bar under the map is the lap unrolled, coloured like the line, with the track's sectors above it and the corners below. S1…Sn zoom to a sector — the circuit's own when its boundaries are labelled on the Tracks page, thirds of the lap otherwise. A compact copy sits over the charts.",
    docs: `${VIEW}#lap-strip`,
  },
  {
    title: "Corners",
    body:
      "Numbers beside each corner on the map and under the strip — the circuit's labelled corners, or corners found on the reference lap. Click one, or press [ and ] to step through them, and everything zooms to it, from the braking zone to the exit.",
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
      "Entry, minimum and exit speed and the time through each corner, in track order or — with the sort switch — biggest loss against the reference first. Click a row to zoom to that corner.",
    docs: `${VIEW}#corner-report-card`,
  },
  {
    title: "Sector times",
    body:
      "Every lap of the session split at the track's sectors (thirds of the lap on a circuit without them), with the best of each sector and the best lap in purple. Laps that don't count toward bests are listed faint and hold no best.",
    docs: `${VIEW}#sector-times`,
  },
  {
    title: "Braking points",
    body:
      "Brake Δ in the report card is where the brake went on against the reference: −14 is 14 m earlier. Beside it, the most pedal and the length of the braking zone. Hover a row to pin both laps' brake points on the map.",
    docs: `${VIEW}#braking`,
  },
  {
    title: "Balance: body slip per corner",
    body:
      "Slip in the report card is how far the nose pointed into the corner past the car's direction of travel, in degrees. More than the reference is more rotation, towards oversteer; less is the nose pushing wide. Needs a recording with the body slip channel.",
    docs: `${VIEW}#balance`,
  },
  {
    title: "Gear choice",
    body:
      "Dashed on the Gear panel: the gear GT7 suggested, where it suggested one. Shading marks the corners taken a gear higher — still above it at the slowest point — and the Tuning panel counts the reference lap's.",
    docs: `${VIEW}#gear-choice`,
  },
  {
    title: "Stint trend",
    body:
      "Lap time and tyre temperature for every lap of the session, split into stints at pit stops, with each stint's drift as a figure such as +0.18 s/lap. GT7 sends no tyre wear: temperature and pace drift are the only proxies.",
    docs: `${VIEW}#stint-trend`,
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
    title: "Gearing",
    body:
      "The focus lap's ratios, and the sawtooth: speed against rpm, a line per gear up to the redline, and the lap's own samples climbing each line to its shift. The lines are fitted to the lap; the reference is drawn in grey when its gearbox differs.",
    docs: `${VIEW}#gearing`,
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
      "Race engineer notes for the session, the reference lap's tuning figures and class benchmark, fuel strategy, consistency across the best laps, and every lap's time against the session's median and spread.",
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
