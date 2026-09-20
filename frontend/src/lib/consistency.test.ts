import { describe, expect, it } from "vitest";
import { consistencyBand, formatSpread, lapConsistency, median } from "./consistency";
import type { LapSummary } from "./types";

function lap(time_ms: number, extra: Partial<LapSummary> = {}): LapSummary {
  return { id: time_ms, session_id: 1, number: 1, time_ms, ...extra } as LapSummary;
}

describe("lapConsistency", () => {
  it("needs three counting laps before it says anything", () => {
    expect(lapConsistency([lap(90_000), lap(90_400)])).toBeNull();
    expect(lapConsistency([lap(90_000), lap(90_400), lap(90_200)])).not.toBeNull();
  });

  it("is the sample standard deviation, and that as a share of the median", () => {
    const c = lapConsistency([lap(90_000), lap(91_000), lap(92_000)])!;
    expect(c.laps).toBe(3);
    expect(c.bestMs).toBe(90_000);
    expect(c.medianMs).toBe(91_000);
    expect(c.stdMs).toBeCloseTo(1000, 6);
    expect(c.pct).toBeCloseTo((1000 / 91_000) * 100, 6);
  });

  it("leaves out laps that do not count toward bests", () => {
    // A pit out-lap forty seconds off the pace would otherwise BE the figure.
    const laps = [
      lap(90_000),
      lap(90_200),
      lap(90_400),
      lap(131_000, { counts_for_best: false, full_lap: false }),
      lap(95_000, { counts_for_best: false, best_override: false }),
    ];
    const c = lapConsistency(laps)!;
    expect(c.laps).toBe(3);
    expect(c.stdMs).toBeCloseTo(200, 6);
  });

  it("treats a lap with no verdict recorded as one that counts", () => {
    // Laps stored before the verdict existed carry no counts_for_best at all.
    expect(lapConsistency([lap(90_000), lap(90_100), lap(90_200)])!.laps).toBe(3);
  });

  it("ignores laps without a time", () => {
    expect(lapConsistency([lap(90_000), lap(90_100), lap(0), lap(-1)])).toBeNull();
  });
});

describe("median", () => {
  it("averages the middle pair of an even count", () => {
    expect(median([4, 1, 3, 2])).toBe(2.5);
    expect(median([3, 1, 2])).toBe(2);
  });
});

describe("presentation", () => {
  it("bands the percentage", () => {
    expect(consistencyBand(0.3)).toBe("tight");
    expect(consistencyBand(0.5)).toBe("steady");
    expect(consistencyBand(1.5)).toBe("loose");
  });

  it("formats the spread in seconds", () => {
    expect(formatSpread(420)).toBe("±0.42 s");
    expect(formatSpread(12_340)).toBe("±12.3 s");
  });
});
