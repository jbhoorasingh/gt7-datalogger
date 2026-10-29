import { describe, expect, it } from "vitest";
import { timeAtDist } from "./playback";
import {
  compareSectorTimes,
  sectorDurationMs,
  timeSectors,
  type SectorTiming,
  type TimedLap,
} from "./sectorTiming";
import type { Sector } from "./types";

type Series = Parameters<typeof timeAtDist>[0];

// By default the lap time is where the clock ends, as on a lap timed from
// line to line.
const lap = (
  times: number[],
  timeMs: number | null = Math.round(times[times.length - 1] * 1000),
  dist = [0, 100, 200, 300],
): TimedLap => ({ series: { dist, t: times } as Series, timeMs });

const sector: Sector = {
  n: 2,
  name: "Sector 2",
  start_dist: 100,
  end_dist: 200,
  authored: true,
};

const reference = lap([0, 10, 20, 30]);

// A lap whose distance grid ends at `last`.
const lapEndingAt = (
  last: number,
  times = [0, 10, 20, 30],
  timeMs: number | null = 30_000,
): TimedLap => lap(times, timeMs, [0, 100, 200, last]);

const firstSector: Sector = {
  n: 1,
  name: "Sector 1",
  start_dist: 0,
  end_dist: 100,
  authored: true,
};

const finalSector: Sector = {
  n: 3,
  name: "Sector 3",
  start_dist: 200,
  end_dist: 300.1,
  authored: true,
};

const toLine = { start: false, finish: true };

describe("sectorDurationMs", () => {
  it("calculates the time between sector boundaries", () => {
    expect(sectorDurationMs(sector, reference)).toBe(10_000);
  });

  it("interpolates boundaries between recorded samples", () => {
    const partial = {
      ...sector,
      start_dist: 150,
      end_dist: 250,
    };

    expect(sectorDurationMs(partial, reference)).toBe(10_000);
  });

  it("returns null for missing or incomplete telemetry", () => {
    expect(sectorDurationMs(sector, null)).toBeNull();
    expect(sectorDurationMs(sector, lap([], null, []))).toBeNull();
    expect(sectorDurationMs(sector, lap([0, 10]))).toBeNull();
  });

  it("rejects boundaries outside the recorded distance", () => {
    expect(
      sectorDurationMs({ ...sector, end_dist: 400 }, reference),
    ).toBeNull();

    expect(
      sectorDurationMs({ ...sector, start_dist: 200 }, reference),
    ).toBeNull();
  });

  it("rejects zero-duration sectors", () => {
    expect(
      sectorDurationMs(sector, lap([0, 10, 10, 30])),
    ).toBeNull();
  });

  it("accepts a boundary rounded past the lap's last sample", () => {
    // The grid ends at 300.06 m; the boundary, rounded to 0.1 m, at 300.1 m.
    expect(sectorDurationMs(finalSector, lapEndingAt(300.06))).toBe(10_000);
    // 0.05 m is as far apart as the two roundings can put them.
    expect(sectorDurationMs(finalSector, lapEndingAt(300.05))).toBe(10_000);
    // The same at the start of the lap.
    expect(
      sectorDurationMs(firstSector, lap([0, 10, 20, 30], 30_000, [0.04, 100, 200, 300])),
    ).toBe(10_000);
  });

  it("rejects telemetry that stops short of a boundary by more than the rounding", () => {
    expect(sectorDurationMs(finalSector, lapEndingAt(300.04))).toBeNull();
    expect(sectorDurationMs(finalSector, lapEndingAt(299))).toBeNull();
    expect(
      sectorDurationMs(firstSector, lap([0, 10, 20, 30], 30_000, [0.1, 100, 200, 300])),
    ).toBeNull();
  });

  it("reads a boundary on the line at the lap's start and finish", () => {
    // However far short of the line the last sample fell.
    expect(sectorDurationMs(finalSector, lapEndingAt(299, [0, 10, 20, 29.99]), toLine)).toBe(
      10_000,
    );
    expect(
      sectorDurationMs(firstSector, lap([0.0083, 10, 20, 30]), { start: true, finish: false }),
    ).toBe(10_000);
  });

  it("times nothing on a lap whose clock does not reach its lap time", () => {
    // A stop the sampler skipped: GT7's clock ran on, the lap's own froze.
    expect(sectorDurationMs(sector, lap([0, 10, 20, 30], 60_000))).toBeNull();
    // Nor one that ran past it, or a lap without one.
    expect(sectorDurationMs(sector, lap([0, 10, 20, 30], 25_000))).toBeNull();
    expect(sectorDurationMs(sector, lap([0, 10, 20, 30], null))).toBeNull();
    // Drift inside the salvage window is the same lap.
    expect(sectorDurationMs(sector, lap([0, 10, 20, 30], 30_400))).toBe(10_000);
  });
});

describe("compareSectorTimes", () => {
  it("reports a positive delta when the comparison is slower", () => {
    const result = compareSectorTimes(
      sector,
      reference,
      lap([0, 10, 21.25, 32]),
    );

    expect(result.referenceMs).toBe(10_000);
    expect(result.selectedMs).toBe(11_250);
    expect(result.deltaMs).toBe(1_250);
    expect(result.approximate).toBe(false);
  });

  it("reports a negative delta when the comparison is faster", () => {
    const result = compareSectorTimes(
      sector,
      reference,
      lap([0, 10, 19.5, 29]),
    );

    expect(result.deltaMs).toBe(-500);
  });

  it("shows the reference duration when no comparison is selected", () => {
    const result = compareSectorTimes(sector, reference, null);

    expect(result.selectedMs).toBe(10_000);
    expect(result.referenceMs).toBe(10_000);
    expect(result.deltaMs).toBeNull();
  });

  it("does not compare laps that could not be aligned", () => {
    const result = compareSectorTimes(
      sector,
      reference,
      lap([0, 10, 22, 34]),
      false,
    );

    expect(result.referenceMs).toBe(10_000);
    expect(result.selectedMs).toBeNull();
    expect(result.deltaMs).toBeNull();
  });

  it("identifies fallback thirds as approximate", () => {
    const result = compareSectorTimes(
      { ...sector, authored: false },
      reference,
      lap([0, 10, 21, 31]),
    );

    expect(result.approximate).toBe(true);
    expect(result.selectedMs).toBe(11_000);
    expect(result.deltaMs).toBe(1_000);
  });
});

// --- through the lap, as /api/analysis/compare sends it ------------------------

const HALF_FRAME_S = 1 / 120;

/** A lap's series as the compare endpoint sends it: distances on a 5 m grid
 *  from 0 to where its last sample fell (`end`; for a compared lap, a place
 *  on the reference's axis), each with the lap's own clock at a steady
 *  `secPerM`. The first sample reads half a frame, and the resampler holds
 *  that reading over any stretch before the recording began (`joinedAt`). */
function resampled(end: number, secPerM: number, joinedAt = 0): Series {
  const dist: number[] = [];
  for (let i = 0; i * 5 < end; i++) dist.push(i * 5);
  dist.push(Math.round(end * 100) / 100);
  const t = dist.map((d) =>
    Number(
      (joinedAt > 0
        ? HALF_FRAME_S + Math.max(0, d - joinedAt) * secPerM
        : Math.max(d * secPerM, HALF_FRAME_S)
      ).toFixed(4),
    ),
  );
  return { dist, t } as Series;
}

// A 1000 m circuit with its own sectors, ending where the reference's last
// sample put the line (999.96 m, rounded to 1000.0).
const circuit: Sector[] = [
  { n: 1, name: "", start_dist: 0, end_dist: 300, authored: true },
  { n: 2, name: "", start_dist: 300, end_dist: 700, authored: true },
  { n: 3, name: "", start_dist: 700, end_dist: 1000, authored: true },
];

// The reference: a minute round, 0.06 s a metre.
const ref: TimedLap = { series: resampled(999.96, 0.06), timeMs: 60_000 };

// Compared laps a second slower, 0.061 s a metre.
const slower = (end: number, timeMs: number | null = 61_000, joinedAt = 0): TimedLap => ({
  series: resampled(end, 0.061, joinedAt),
  timeMs,
});

const at = <K extends keyof SectorTiming>(timings: Record<number, SectorTiming>, key: K) =>
  circuit.map((s) => timings[s.n][key]);

describe("timeSectors", () => {
  const thirds: Sector[] = [1, 2, 3].map((n) => ({
    n,
    name: "",
    start_dist: (n - 1) * 100,
    end_dist: n * 100,
    authored: false,
  }));

  // A 90 s lap whose first and last samples fall half a frame inside it.
  const sampled = lap([0.0083, 25, 55, 89.9917], 90_000);

  it("runs the lap's clock from 0 at the start to the lap time at the finish", () => {
    const timings = timeSectors(thirds, sampled, null);
    const times = [1, 2, 3].map((n) => timings[n].selectedMs);

    // The boundaries inside the lap are still interpolated.
    expect(times).toEqual([25_000, 30_000, 35_000]);
    expect(times.reduce((sum, ms) => sum! + ms!, 0)).toBe(90_000);
    expect([1, 2, 3].map((n) => timings[n].approximate)).toEqual([true, true, true]);
  });

  it("gives no delta between laps whose samples fell differently", () => {
    // The same lap, sampled a frame later against the line at both ends.
    const other = lap([0.0166, 25, 55, 89.9834], 90_000);
    const timings = timeSectors(thirds, sampled, other);

    expect([1, 2, 3].map((n) => timings[n].deltaMs)).toEqual([0, 0, 0]);
  });

  it("times a full lap of the circuit, summing to its lap time", () => {
    const timings = timeSectors(circuit, ref, null);

    expect(at(timings, "selectedMs")).toEqual([18_000, 24_000, 18_000]);
    expect(at(timings, "approximate")).toEqual([false, false, false]);
  });

  it("keeps the final sector of a compared lap whose last sample fell short of the reference's", () => {
    // Half a metre before the line on the reference's axis, 30 ms on its clock.
    const timings = timeSectors(circuit, ref, slower(999.5));

    expect(at(timings, "selectedMs")).toEqual([18_300, 24_400, 18_300]);
    expect(at(timings, "deltaMs")).toEqual([300, 400, 300]);
    // The deltas add up to the lap's.
    expect(at(timings, "deltaMs").reduce((sum, ms) => sum! + ms!, 0)).toBe(1_000);
  });

  it("keeps it when the compared lap's last sample lies past the reference's end", () => {
    const timings = timeSectors(circuit, ref, slower(1000.4));

    expect(at(timings, "deltaMs")).toEqual([300, 400, 300]);
  });

  it("times nothing on a lap the recording joined part way round", () => {
    // GT7's lap time covers the whole lap; the clock started at 400 m. Read
    // anyway, S1 would come out as 8 ms and S3 as 42 s.
    const timings = timeSectors(circuit, ref, slower(999.5, 61_000, 400));

    expect(at(timings, "selectedMs")).toEqual([null, null, null]);
    expect(at(timings, "deltaMs")).toEqual([null, null, null]);
    expect(at(timings, "referenceMs")).toEqual([18_000, 24_000, 18_000]);
  });

  it("times nothing on a lap whose clock froze through a stop", () => {
    const timings = timeSectors(circuit, ref, slower(999.5, 61_000 + 30_000));

    expect(at(timings, "selectedMs")).toEqual([null, null, null]);
    expect(at(timings, "deltaMs")).toEqual([null, null, null]);
  });

  it("times nothing on a lap whose telemetry stops before a boundary", () => {
    // Lined up only as far as 650 m: short of the boundary at 700 m.
    const timings = timeSectors(circuit, ref, slower(650));

    expect(at(timings, "selectedMs")).toEqual([null, null, null]);
    expect(at(timings, "deltaMs")).toEqual([null, null, null]);
  });

  it("times nothing on a lap without a lap time", () => {
    const timings = timeSectors(circuit, { ...ref, timeMs: null }, null);

    expect(at(timings, "selectedMs")).toEqual([null, null, null]);
  });

  it("compares nothing with a lap that could not be aligned", () => {
    const timings = timeSectors(circuit, ref, slower(999.5), false);

    expect(at(timings, "referenceMs")).toEqual([18_000, 24_000, 18_000]);
    expect(at(timings, "selectedMs")).toEqual([null, null, null]);
    expect(at(timings, "deltaMs")).toEqual([null, null, null]);
  });
});
