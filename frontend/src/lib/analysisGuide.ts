// What the Analysis view's features are, in a sentence or two each, for the
// in-app guide. The documentation site holds the long form: every entry links
// to its section there, and says nothing here that would go stale when a
// threshold changes. The channels' own descriptions live in channels.ts.

import type { MessageKey } from "@/i18n";
import type { ChannelDef } from "@/lib/channels";

export const DOCS_SITE = "https://jbhoorasingh.github.io/gt7-datalogger/";

/** Absolute docs URL for a site path like "guide/analysis-view/#corners". */
export function docsUrl(path: string): string {
  return DOCS_SITE + path;
}

export interface GuideFeature {
  titleKey: MessageKey;
  bodyKey: MessageKey;
  /** Docs site path, "page/#anchor"; a test checks it resolves. */
  docs: string;
}

const VIEW = "guide/analysis-view/";

export const GUIDE_FEATURES: GuideFeature[] = [
  {
    titleKey: "guide.feature.choosingLaps.title",
    bodyKey: "guide.feature.choosingLaps.body",
    docs: `${VIEW}#selecting-laps`,
  },
  {
    titleKey: "guide.feature.timeDiff.title",
    bodyKey: "guide.feature.timeDiff.body",
    docs: `${VIEW}#stacked-charts`,
  },
  {
    titleKey: "guide.feature.linedUp.title",
    bodyKey: "guide.feature.linedUp.body",
    docs: "internals/analysis-math/#lining-laps-up-by-place-on-track",
  },
  {
    titleKey: "guide.feature.cursorZoom.title",
    bodyKey: "guide.feature.cursorZoom.body",
    docs: `${VIEW}#stacked-charts`,
  },
  {
    titleKey: "guide.feature.eventShading.title",
    bodyKey: "guide.feature.eventShading.body",
    docs: `${VIEW}#stacked-charts`,
  },
  {
    titleKey: "guide.feature.playback.title",
    bodyKey: "guide.feature.playback.body",
    docs: `${VIEW}#lap-playback`,
  },
  {
    titleKey: "guide.feature.raceLine.title",
    bodyKey: "guide.feature.raceLine.body",
    docs: `${VIEW}#race-line-map`,
  },
  {
    titleKey: "guide.feature.sync.title",
    bodyKey: "guide.feature.sync.body",
    docs: `${VIEW}#position-or-time-sync`,
  },
  {
    titleKey: "guide.feature.follow.title",
    bodyKey: "guide.feature.follow.body",
    docs: `${VIEW}#race-line-map`,
  },
  {
    titleKey: "guide.feature.layers.title",
    bodyKey: "guide.feature.layers.body",
    docs: `${VIEW}#layers`,
  },
  {
    titleKey: "guide.feature.lapStrip.title",
    bodyKey: "guide.feature.lapStrip.body",
    docs: `${VIEW}#lap-strip`,
  },
  {
    titleKey: "guide.feature.corners.title",
    bodyKey: "guide.feature.corners.body",
    docs: `${VIEW}#corners`,
  },
  {
    titleKey: "guide.feature.fullScreen.title",
    bodyKey: "guide.feature.fullScreen.body",
    docs: `${VIEW}#full-screen`,
  },
  {
    titleKey: "guide.feature.cornerReport.title",
    bodyKey: "guide.feature.cornerReport.body",
    docs: `${VIEW}#corner-report-card`,
  },
  {
    titleKey: "guide.feature.sectorTimes.title",
    bodyKey: "guide.feature.sectorTimes.body",
    docs: `${VIEW}#sector-times`,
  },
  {
    titleKey: "guide.feature.braking.title",
    bodyKey: "guide.feature.braking.body",
    docs: `${VIEW}#braking`,
  },
  {
    titleKey: "guide.feature.bodySlip.title",
    bodyKey: "guide.feature.bodySlip.body",
    docs: `${VIEW}#balance`,
  },
  {
    titleKey: "guide.feature.yawBalance.title",
    bodyKey: "guide.feature.yawBalance.body",
    docs: `${VIEW}#balance`,
  },
  {
    titleKey: "guide.feature.gearChoice.title",
    bodyKey: "guide.feature.gearChoice.body",
    docs: `${VIEW}#gear-choice`,
  },
  {
    titleKey: "guide.feature.stintTrend.title",
    bodyKey: "guide.feature.stintTrend.body",
    docs: `${VIEW}#stint-trend`,
  },
  {
    titleKey: "guide.feature.gg.title",
    bodyKey: "guide.feature.gg.body",
    docs: `${VIEW}#traction-circle-g-g`,
  },
  {
    titleKey: "guide.feature.cornerDetail.title",
    bodyKey: "guide.feature.cornerDetail.body",
    docs: `${VIEW}#corner-detail-widget`,
  },
  {
    titleKey: "guide.feature.gearing.title",
    bodyKey: "guide.feature.gearing.body",
    docs: `${VIEW}#gearing`,
  },
  {
    titleKey: "guide.feature.lapStatus.title",
    bodyKey: "guide.feature.lapStatus.body",
    docs: "guide/sessions-view/#excluding-a-lap-from-bests",
  },
  {
    titleKey: "guide.feature.sidePanels.title",
    bodyKey: "guide.feature.sidePanels.body",
    docs: `${VIEW}#side-panels`,
  },
  {
    titleKey: "guide.feature.sharing.title",
    bodyKey: "guide.feature.sharing.body",
    docs: VIEW,
  },
];

/** Where the docs say how a channel is computed: in the browser, from a
 *  column only some recordings carry, or from one every recording has. */
export function channelDocs(c: ChannelDef): string {
  const page = "internals/derived-channels/";
  if (c.derive) return `${page}#frontend-derived-channels`;
  return c.needsKey ? `${page}#optional-columns` : `${page}#stored-sample-columns`;
}

/** Case-insensitive match on any of the given texts. */
export function matchesQuery(query: string, ...texts: (string | undefined)[]): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return texts.some((t) => t?.toLowerCase().includes(q));
}
