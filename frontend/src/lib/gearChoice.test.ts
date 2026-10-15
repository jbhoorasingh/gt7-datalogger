import { describe, expect, it } from "vitest";
import { columnsForChannels } from "./channels";
import { gearHighBands, gearHighCount, NO_SUGGESTION, suggestionValues } from "./gearChoice";
import type { Corner, CornerReportRow } from "./types";

// A 300 m lap resampled every 10 m into one corner, 100–200 m: braking from
// fifth down to third by 140 m, the slowest point at 150 m, then back up.
function lap(gear: (d: number) => number = (d) => (d < 110 ? 5 : d < 140 ? 4 : d <= 150 ? 3 : 4)) {
  const dist = Array.from({ length: 31 }, (_, i) => i * 10);
  return {
    dist,
    gear: dist.map(gear),
    speed: dist.map((d) => 100 + Math.abs(d - 150)),
    suggested_gear: dist.map((d) => (d >= 60 && d < 140 ? 3 : NO_SUGGESTION)),
  };
}

function corner(n: number, entry: number, exit: number): Corner {
  return {
    n,
    apex_dist: (entry + exit) / 2,
    apex_x: 0,
    apex_z: 0,
    entry_dist: entry,
    exit_dist: exit,
    direction: "R",
    min_speed: 100,
    angle_deg: 90,
  };
}

function row(n: number, extra: Partial<CornerReportRow>): CornerReportRow {
  return { n, entry_speed: 150, min_speed: 100, exit_speed: 150, time_ms: 3000, ...extra };
}

describe("suggestionValues", () => {
  it("breaks the line wherever GT7 suggested nothing", () => {
    const values = suggestionValues({ suggested_gear: [15, 3, 3, 0, 2, 15] })!;
    expect(values).toEqual([null, 3, 3, null, 2, null]);
  });

  it("has nothing for a lap without suggestions", () => {
    expect(suggestionValues({ gear: [3, 4] })).toBeNull();
  });
});

describe("gearHighBands", () => {
  const corners = [corner(1, 100, 200)];

  it("leaves a corner the lap got down in time for unshaded", () => {
    // In fifth and fourth through the braking zone, in third by the slowest
    // point: above the suggestion for a while, as every braking lap is.
    const bands = gearHighBands(lap(), corners, [row(1, { suggested_gear: 3, gear_high: false })]);
    expect(bands).toEqual([]);
  });

  it("runs to the slowest point when the gear never came down, and not past it", () => {
    const lazy = lap((d) => (d < 110 ? 5 : 4));
    const bands = gearHighBands(lazy, corners, [row(1, { suggested_gear: 3, gear_high: true })]);
    expect(bands).toEqual([[100, 160]]);
  });

  it("rounds a resampled gear caught between two before comparing", () => {
    const halfway = lap((d) => (d < 100 ? 5 : 3.4));
    expect(gearHighBands(halfway, corners, [row(1, { suggested_gear: 3, gear_high: true })])).toEqual([]);
  });

  it("says nothing outside the window, without a suggestion, or across the line", () => {
    const high = lap(() => 6);
    const judged = row(1, { suggested_gear: 3, gear_high: true });
    expect(gearHighBands(high, corners, [row(1, { suggested_gear: null })])).toEqual([]);
    expect(gearHighBands(high, corners, [])).toEqual([]);
    expect(gearHighBands(high, [corner(1, 250, 50)], [judged])).toEqual([]);
    const [[from, to]] = gearHighBands(high, corners, [judged]);
    expect(from).toBe(100);
    expect(to).toBeLessThanOrEqual(200);
  });

  it("never counts neutral as a gear above the suggestion", () => {
    const neutral = lap(() => 15);
    expect(gearHighBands(neutral, corners, [row(1, { suggested_gear: 3, gear_high: true })])).toEqual([]);
  });
});

describe("gearHighCount", () => {
  it("counts judged corners and the ones taken a gear high", () => {
    const rows = [
      row(1, { suggested_gear: 3, gear_high: true }),
      row(2, { suggested_gear: 4, gear_high: false }),
      row(3, { suggested_gear: null, gear_high: null }),
      row(4, {}), // a server older than the columns
    ];
    expect(gearHighCount(rows)).toEqual({ high: 1, judged: 2 });
    expect(gearHighCount(undefined)).toEqual({ high: 0, judged: 0 });
  });
});

describe("the gear channel", () => {
  it("fetches the suggestion beside the gear", () => {
    expect(columnsForChannels(["gear"])).toEqual(expect.arrayContaining(["gear", "suggested_gear"]));
  });
});
