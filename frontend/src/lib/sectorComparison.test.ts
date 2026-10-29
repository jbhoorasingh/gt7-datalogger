import { describe, expect, it } from "vitest";
import { sectorComparison } from "./sectorComparison";
import type { SectorTimes } from "./types";

const data: SectorTimes = {
  session_id: 1,
  source: "track",
  sectors: [
    { n: 1, name: "S1" },
    { n: 2, name: "S2" },
    { n: 3, name: "S3" },
  ],
  laps: [
    {
      id: 101,
      number: 1,
      time_ms: 64_583,
      counts_for_best: true,
      reference: true,
      splits_ms: [24_381, 18_942, 21_260],
    },
    {
      id: 102,
      number: 2,
      time_ms: 64_300,
      counts_for_best: true,
      reference: false,
      splits_ms: [24_102, 19_254, 20_944],
    },
    {
      id: 103,
      number: 3,
      time_ms: 64_900,
      counts_for_best: true,
      reference: false,
      splits_ms: [24_720, 18_701, 21_479],
    },
    {
      id: 104,
      number: 4,
      time_ms: 60_000,
      counts_for_best: false,
      reference: false,
      splits_ms: [20_000, 18_000, 22_000],
    },
  ],
};

describe("sectorComparison", () => {
  it("uses the selected reference lap, not the API's reference flag", () => {
    const result = sectorComparison(data, 102);

    expect(result?.referenceLapNumber).toBe(2);
    expect(result?.referenceLapMs).toBe(64_300);
    expect(result?.sectors[0].referenceMs).toBe(24_102);
  });

  it("reuses the session's fastest eligible sectors", () => {
    const result = sectorComparison(data, 101);

    expect(result?.sectors.map((sector) => sector.bestMs)).toEqual([
      24_102,
      18_701,
      20_944,
    ]);
  });

  it("calculates sector deltas against the selected reference", () => {
    const result = sectorComparison(data, 101);

    expect(result?.sectors.map((sector) => sector.deltaMs)).toEqual([
      -279,
      -241,
      -316,
    ]);
  });

  it("calculates theoretical best and its full-lap delta", () => {
    const result = sectorComparison(data, 101);

    expect(result?.theoreticalBestMs).toBe(63_747);
    expect(result?.theoreticalDeltaMs).toBe(-836);
  });

  it("excludes non-counting laps from sector records", () => {
    const result = sectorComparison(data, 101);

    expect(result?.sectors[0].bestMs).toBe(24_102);
  });

  it("shows zero delta where the reference owns the best sector", () => {
    const result = sectorComparison(data, 102);

    expect(result?.sectors[0].deltaMs).toBe(0);
    expect(result?.sectors[2].deltaMs).toBe(0);
  });

  it("is level when the reference holds every best sector", () => {
    // Splits are rounded to 0.1 ms, so these add up to 64_583.1 against a
    // 64_583 lap time, and as floats to a hair more.
    const level: SectorTimes = {
      ...data,
      laps: [
        { ...data.laps[0], time_ms: 64_583, splits_ms: [24_381.4, 18_942.4, 21_259.3] },
        { ...data.laps[1], time_ms: 65_000, splits_ms: [24_500, 19_000, 21_500] },
      ],
    };

    const result = sectorComparison(level, 101);

    expect(result?.sectors.map((sector) => sector.deltaMs)).toEqual([0, 0, 0]);
    expect(result?.theoreticalDeltaMs).toBe(0);
  });

  it("rounds a sector delta under half a millisecond to level, never -0", () => {
    const close: SectorTimes = {
      ...data,
      laps: [
        { ...data.laps[0], splits_ms: [24_102.3, 18_942, 21_260] },
        { ...data.laps[1], splits_ms: [24_102.2, 19_254, 20_944] },
      ],
    };

    const result = sectorComparison(close, 101);

    expect(Object.is(result?.sectors[0].deltaMs, 0)).toBe(true);
  });

  it("does not substitute a session reference for a guest reference", () => {
    expect(sectorComparison(data, 999)).toBeNull();
    expect(sectorComparison(data, null)).toBeNull();
  });

  it("handles missing reference sector samples", () => {
    const partial: SectorTimes = {
      ...data,
      laps: data.laps.map((lap) =>
        lap.id === 101 ? { ...lap, splits_ms: null } : lap,
      ),
    };

    const result = sectorComparison(partial, 101);

    expect(result?.referenceLapMs).toBe(64_583);
    expect(result?.sectors.every((sector) => sector.deltaMs == null)).toBe(true);
    expect(result?.theoreticalBestMs).toBe(63_747);
  });

  it("does not invent theoretical times when any sector best is missing", () => {
    const partial: SectorTimes = {
      ...data,
      laps: data.laps.map((lap) => ({
        ...lap,
        splits_ms: lap.splits_ms?.slice(0, 2) ?? null,
      })),
    };

    const result = sectorComparison(partial, 101);

    expect(result?.sectors[2].bestMs).toBeNull();
    expect(result?.theoreticalBestMs).toBeNull();
    expect(result?.theoreticalDeltaMs).toBeNull();
  });

  it("returns null when no sectors are available", () => {
    expect(sectorComparison({ ...data, sectors: [] }, 101)).toBeNull();
  });
});
