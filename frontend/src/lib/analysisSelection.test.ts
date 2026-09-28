import { describe, expect, it } from "vitest";

import { openingSession, quickestCounting, resolveReference, resolveSelected } from "@/lib/analysisSelection";
import type { LapSummary, SessionSummary } from "@/lib/types";

function lap(id: number, time_ms: number, counts = true): LapSummary {
  return { id, number: id, time_ms, counts_for_best: counts } as LapSummary;
}

// Session 259: three laps, newest first. Lap 12 is the best that counts;
// lap 13 is quicker and is a pit out-lap.
const OWN = [lap(13, 60_000, false), lap(12, 65_250), lap(11, 66_666)];
const ownOnly = (laps: LapSummary[]) => (id: number) => laps.some((l) => l.id === id);
// What the view's own `keep` says of an id it has not resolved yet: it may
// turn out to be a guest, so it is not dropped.
const unresolved = () => true;

describe("quickestCounting", () => {
  it("is the quickest lap that counts towards the bests", () => {
    expect(quickestCounting(OWN)?.id).toBe(12);
  });

  it("falls back to the quickest of any kind when none counts", () => {
    expect(quickestCounting([lap(1, 70_000, false), lap(2, 69_000, false)])?.id).toBe(2);
  });

  it("has no answer for no laps", () => {
    expect(quickestCounting([])).toBeUndefined();
  });
});

describe("a session with no laps yet", () => {
  // The state every session is in until its first lap is finished.
  const rule = { laps: [], manual: false, keep: unresolved };

  it("selects nothing, whatever was selected for the session before", () => {
    // Laps 1184 and 1180 are session 256's. They pass `keep` — nothing has
    // said they are not guests — and they must still not ride along.
    expect(resolveSelected([1184, 1180], rule)).toEqual([]);
  });

  it("has no reference lap", () => {
    expect(resolveReference(1180, rule)).toBeNull();
  });

  it("starts from nothing too", () => {
    expect(resolveSelected([], rule)).toEqual([]);
    expect(resolveReference(null, rule)).toBeNull();
  });

  it("still shows laps that were asked for by a link", () => {
    // #/analysis?session=260&laps=1184&ref=1184 — another session's lap,
    // named on purpose: a guest, in a session with nothing of its own.
    const chosen = { laps: [], manual: true, keep: unresolved };
    expect(resolveSelected([1184], chosen)).toEqual([1184]);
    expect(resolveReference(1184, chosen)).toBe(1184);
  });

  it("drops a chosen lap that turned out not to exist", () => {
    const chosen = { laps: [], manual: true, keep: () => false };
    expect(resolveSelected([9999], chosen)).toEqual([]);
    expect(resolveReference(9999, chosen)).toBeNull();
  });
});

describe("a session with laps", () => {
  it("follows latest against best until somebody chooses", () => {
    const rule = { laps: OWN, manual: false, keep: ownOnly(OWN) };
    expect(resolveSelected([], rule)).toEqual([13, 12]);
    expect(resolveReference(null, rule)).toBe(12);
  });

  it("replaces another session's selection with its own", () => {
    const rule = { laps: OWN, manual: false, keep: unresolved };
    expect(resolveSelected([1184, 1180], rule)).toEqual([13, 12]);
    expect(resolveReference(1180, rule)).toBe(12);
  });

  it("selects one lap once when the latest is the best", () => {
    const one = [lap(21, 65_000)];
    expect(resolveSelected([], { laps: one, manual: false, keep: ownOnly(one) })).toEqual([21]);
  });

  it("keeps what was chosen as new laps arrive", () => {
    const rule = { laps: OWN, manual: true, keep: ownOnly(OWN) };
    expect(resolveSelected([11, 12], rule)).toEqual([11, 12]);
    expect(resolveReference(11, rule)).toBe(11);
  });

  it("drops a chosen lap that was deleted and keeps the rest", () => {
    const rule = { laps: OWN, manual: true, keep: ownOnly(OWN) };
    expect(resolveSelected([11, 99], rule)).toEqual([11]);
    expect(resolveReference(99, rule)).toBe(12);
  });

  it("falls back to latest against best when nothing chosen is left", () => {
    const rule = { laps: OWN, manual: true, keep: ownOnly(OWN) };
    expect(resolveSelected([98, 99], rule)).toEqual([13, 12]);
  });
});

describe("openingSession", () => {
  const session = (id: number, lap_count: number) => ({ id, lap_count }) as SessionSummary;
  // Newest first, as the API lists them: a session still being driven with
  // no lap yet, then two with laps.
  const LIST = [session(9, 0), session(8, 4), session(7, 2)];

  it("keeps the session it had while that session exists", () => {
    expect(openingSession(7, LIST)).toBe(7);
  });

  it("opens the newest session with laps when it had none", () => {
    expect(openingSession(null, LIST)).toBe(8);
  });

  it("does not stay on a session that has since been deleted", () => {
    expect(openingSession(3, LIST)).toBe(8);
  });

  it("falls back to the newest session when none has a lap", () => {
    expect(openingSession(3, [session(2, 0), session(1, 0)])).toBe(2);
  });

  it("is nothing when there are no sessions", () => {
    expect(openingSession(3, [])).toBeNull();
  });
});
