// The Analysis guide is only worth having if it can't quietly rot: every
// channel explained, and every "docs ↗" link landing on a real section.

import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { channelDocs, docsUrl, GUIDE_FEATURES, matchesQuery } from "./analysisGuide";
import { CHANNEL_GROUPS, CHANNELS } from "./channels";

const DOCS = fileURLToPath(new URL("../../../docs/", import.meta.url));

// Python-Markdown's toc slugs, which is what MkDocs gives headings.
function slug(heading: string): string {
  return heading
    .toLowerCase()
    .replace(/[^\p{L}\p{N}_\s-]/gu, "")
    .trim()
    .replace(/[-\s]+/g, "-");
}

function resolves(path: string): boolean {
  const [page, anchor] = path.split("#");
  const file = `${DOCS}${page.replace(/\/$/, "")}.md`;
  if (!existsSync(file)) return false;
  if (!anchor) return true;
  const headings = readFileSync(file, "utf8")
    .split("\n")
    .filter((line) => /^#{1,6} /.test(line))
    .map((line) => slug(line.replace(/^#+ /, "")));
  return headings.includes(anchor);
}

describe("channel descriptions", () => {
  it("explain every channel, briefly", () => {
    for (const c of CHANNELS) {
      expect(c.description.trim(), c.key).not.toBe("");
      expect(c.description.length, c.key).toBeLessThanOrEqual(200);
    }
  });

  it("belong to a group the guide and picker list", () => {
    for (const c of CHANNELS) expect(CHANNEL_GROUPS).toContain(c.group);
  });
});

describe("docs links", () => {
  it("land on a real page and heading", () => {
    const paths = [...GUIDE_FEATURES.map((f) => f.docs), ...CHANNELS.map(channelDocs)];
    const broken = [...new Set(paths)].filter((p) => !resolves(p));
    expect(broken).toEqual([]);
  });

  it("are absolute", () => {
    expect(docsUrl("guide/analysis-view/#corners")).toBe(
      "https://jbhoorasingh.github.io/gt7-datalogger/guide/analysis-view/#corners",
    );
  });

  it("would catch a stale anchor", () => {
    expect(resolves("guide/analysis-view/#no-such-section")).toBe(false);
    expect(resolves("guide/no-such-page/")).toBe(false);
    expect(resolves("internals/analysis-math/#lining-laps-up-by-place-on-track")).toBe(true);
  });
});

describe("search", () => {
  it("matches any field, ignoring case", () => {
    expect(matchesQuery("  UNDERSTEER ", "Yaw rate", "…is understeer…")).toBe(true);
    expect(matchesQuery("lockup", "Speed", undefined)).toBe(false);
    expect(matchesQuery("", "anything")).toBe(true);
  });

  it("finds the channels a driver would look for", () => {
    const hits = (q: string) =>
      CHANNELS.filter((c) => matchesQuery(q, c.title, c.description, c.needs)).map((c) => c.key);
    expect(hits("understeer")).toEqual(expect.arrayContaining(["yaw_rate", "tt_balance"]));
    expect(hits("locking")).toEqual(expect.arrayContaining(["tire_slip", "slip_fl"]));
    expect(hits("packet b")).toEqual(expect.arrayContaining(["steer", "acc_lat"]));
  });
});
