import { describe, expect, it } from "vitest";
import {
  constantColumns,
  dateGroupLabel,
  formatEventCounts,
  groupByDate,
  matchesSessionFilter,
} from "./sessionList";
import type { LapSummary, SessionSummary } from "./types";

const NOW = new Date(2026, 8, 27, 15, 0); // Sun 27 Sep 2026, local time

function at(daysBack: number, hour = 12): string {
  return new Date(2026, 8, 27 - daysBack, hour).toISOString();
}

describe("dateGroupLabel", () => {
  it("names the recent days, then the month", () => {
    expect(dateGroupLabel(at(0, 1), NOW)).toBe("Today");
    expect(dateGroupLabel(at(1, 23), NOW)).toBe("Yesterday");
    expect(dateGroupLabel(at(6), NOW)).toBe("Earlier this week");
    expect(dateGroupLabel(at(10), NOW)).toBe("Earlier this month");
    expect(dateGroupLabel(at(40), NOW)).toBe(
      new Date(2026, 7, 18).toLocaleDateString(undefined, { month: "long", year: "numeric" }),
    );
  });

  it("groups consecutive sessions under one label", () => {
    const groups = groupByDate(
      [{ started_at: at(0) }, { started_at: at(0, 9) }, { started_at: at(1) }, { started_at: at(3) }],
      NOW,
    );
    expect(groups.map((g) => [g.label, g.items.length])).toEqual([
      ["Today", 2],
      ["Yesterday", 1],
      ["Earlier this week", 1],
    ]);
  });
});

describe("matchesSessionFilter", () => {
  const s = {
    car_name: "TT Cup '16",
    car_full_name: "Audi TT Cup '16",
    car_manufacturer: "Audi",
    track_name: "Deep Forest Raceway",
    car_category: "Gr.4",
    tags: ["practice", "wet"],
  } as SessionSummary;

  it("matches car, maker, circuit and tags; every word must hit", () => {
    expect(matchesSessionFilter(s, "")).toBe(true);
    expect(matchesSessionFilter(s, "audi forest")).toBe(true);
    expect(matchesSessionFilter(s, "gr.4")).toBe(true);
    expect(matchesSessionFilter(s, "audi suzuka")).toBe(false);
  });

  it("reads #word as a tag prefix only", () => {
    expect(matchesSessionFilter(s, "#prac")).toBe(true);
    expect(matchesSessionFilter(s, "#audi")).toBe(false);
  });
});

function lap(extra: Partial<LapSummary>): LapSummary {
  return {
    id: 1,
    session_id: 1,
    number: 1,
    time_ms: 90_000,
    fuel_consumed: 1.8,
    full_throttle_pct: 68,
    full_brake_pct: 3,
    coasting_pct: 1,
    tire_spin_pct: 0,
    max_speed: 222,
    min_body_height: 0,
    ...extra,
  };
}

describe("constantColumns", () => {
  it("hides what reads the same on every counting lap", () => {
    const laps = [
      lap({ full_throttle_pct: 68.2 }),
      lap({ full_throttle_pct: 69.1, max_speed: 224 }),
      // An out-lap's numbers don't make a column vary.
      lap({ counts_for_best: false, full_lap: false, fuel_consumed: 0.4 }),
    ];
    const hidden = constantColumns(laps);
    expect(hidden.get("fuel")).toBe("1.80 L");
    expect(hidden.get("brake")).toBe("3%");
    expect(hidden.has("throttle")).toBe(false);
    expect(hidden.has("speed")).toBe(false);
    expect(hidden.get("pos")).toBe("–");
  });

  it("hides nothing but an empty position column below two counting laps", () => {
    const hidden = constantColumns([lap({})]);
    expect([...hidden.keys()]).toEqual(["pos"]);
  });
});

describe("formatEventCounts", () => {
  it("prints letters in a fixed order and a dash when clean", () => {
    expect(formatEventCounts({ kerb: 4, lockup: 2 })).toBe("2L·4K");
    expect(formatEventCounts({})).toBe("–");
    expect(formatEventCounts(undefined)).toBe("–");
  });
});
