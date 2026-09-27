import { describe, expect, it } from "vitest";
import { formatDrift, formatWarming, hasTrend, stintScale, stintSpan } from "./stint";
import type { Stint, StintLap } from "./types";

function lap(number: number, seconds: number, over: Partial<StintLap> = {}): StintLap {
  return {
    id: 100 + number,
    number,
    time_ms: Math.round(seconds * 1000),
    counts: true,
    pit: false,
    stint: 1,
    exclude_reason: "",
    fuel_start: 50,
    tt_front: 80,
    tt_rear: 85,
    tt: {},
    ...over,
  };
}

describe("stintScale", () => {
  it("spans the laps that count", () => {
    const scale = stintScale([lap(1, 90.0), lap(2, 90.4), lap(3, 90.8)])!;
    expect(scale.yMin).toBeLessThan(90.0);
    expect(scale.yMax).toBeGreaterThan(90.8);
    expect(scale.yMax).toBeLessThan(92);
    expect(scale.points.map((p) => p.lap.number)).toEqual([1, 2, 3]);
    expect(scale.gaps).toEqual([]);
  });

  it("does not let one slow lap set the scale", () => {
    const laps = [90.0, 90.2, 90.4, 112.0, 90.8, 91.0].map((s, i) => lap(i + 1, s));
    const scale = stintScale(laps)!;
    expect(scale.yMax).toBeLessThan(93);
    const slow = scale.points.find((p) => p.lap.number === 4)!;
    expect(slow.clipped).toBe(true);
    expect(slow.seconds).toBe(scale.yMax);
    expect(scale.points.filter((p) => p.clipped)).toHaveLength(1);
  });

  it("keeps a whole stint of wear on the scale", () => {
    // Two tenths a lap over twenty laps: four seconds from first to last.
    const laps = Array.from({ length: 20 }, (_, i) => lap(i + 1, 90 + 0.2 * i));
    const scale = stintScale(laps)!;
    expect(scale.points.some((p) => p.clipped)).toBe(false);
    expect(scale.yMax).toBeGreaterThan(93.8);
  });

  it("makes gaps of pit laps and laps that do not count", () => {
    const scale = stintScale([
      lap(1, 90.0),
      lap(2, 70.0, { counts: false, exclude_reason: "off-track" }),
      lap(3, 130.0, { pit: true, stint: null }),
      lap(4, 90.5, { stint: 2 }),
    ])!;
    expect(scale.points.map((p) => p.lap.number)).toEqual([1, 4]);
    expect(scale.gaps.map((p) => p.lap.number)).toEqual([2, 3]);
    // A gap quicker than the window is drawn where it is; a slower one is
    // pinned to the top.
    expect(scale.gaps[0].clipped).toBe(false);
    expect(scale.gaps[1].clipped).toBe(true);
    // Neither moved the scale.
    expect(scale.yMin).toBeGreaterThan(89);
  });

  it("has nothing to draw without a lap that counts", () => {
    expect(stintScale([])).toBeNull();
    expect(stintScale([lap(1, 60, { counts: false })])).toBeNull();
  });
});

describe("hasTrend", () => {
  it("needs two laps that count", () => {
    expect(hasTrend([lap(1, 90)])).toBe(false);
    expect(hasTrend([lap(1, 90), lap(2, 91, { pit: true })])).toBe(false);
    expect(hasTrend([lap(1, 90), lap(2, 91)])).toBe(true);
  });
});

describe("formatting", () => {
  it("writes a drift with its sign", () => {
    expect(formatDrift(180)).toBe("+0.18 s/lap");
    expect(formatDrift(-42)).toBe("−0.04 s/lap");
    expect(formatDrift(1540)).toBe("+1.5 s/lap");
    expect(formatDrift(3)).toBe("±0.00 s/lap");
  });

  it("writes a temperature drift with its sign", () => {
    expect(formatWarming(1.24)).toBe("+1.2 °C/lap");
    expect(formatWarming(-0.5)).toBe("−0.5 °C/lap");
    expect(formatWarming(0.02)).toBe("±0.0 °C/lap");
  });

  it("names a stint by its laps", () => {
    const stint = { first_lap: 3, last_lap: 14 } as Stint;
    expect(stintSpan(stint)).toBe("L3–L14");
    expect(stintSpan({ ...stint, last_lap: 3 })).toBe("L3");
  });
});
