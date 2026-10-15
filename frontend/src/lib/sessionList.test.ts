import { describe, expect, it } from "vitest";
import {
  constantColumns,
  dateGroupLabel,
  emptyColumns,
  formatEventCounts,
  groupByDate,
  LAP_COLUMNS,
  matchesSessionFilter,
  withGearChoice,
  withoutGearChoice,
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

  it("hides nothing below two counting laps", () => {
    expect(constantColumns([lap({})]).size).toBe(0);
  });
});

describe("emptyColumns", () => {
  it("finds the columns no lap has anything for, however few laps", () => {
    expect([...emptyColumns([lap({})])]).toEqual(["pos", "gear"]);
    expect(emptyColumns([lap({}), lap({ race_position: 3, gear_judged: 2 })]).size).toBe(0);
  });

  it("counts a lap judged against no suggestion as nothing", () => {
    // The endpoint lists every lap; one GT7 suggested nothing on is judged 0.
    expect(emptyColumns([lap({ gear_judged: 0, gear_high: 0 })]).has("gear")).toBe(true);
  });
});

describe("the gear-high column", () => {
  const gear = LAP_COLUMNS.find((c) => c.id === "gear")!;

  it("prints corners a gear high over corners judged, and a dash for none", () => {
    expect(gear.text(lap({ gear_judged: 9, gear_high: 2 }))).toBe("2/9");
    expect(gear.text(lap({ gear_judged: 9, gear_high: 0 }))).toBe("0/9");
    expect(gear.text(lap({}))).toBe("–");
  });

  it("hides as the same on every lap only when every lap was judged alike", () => {
    const alike = [lap({ gear_judged: 2, gear_high: 0 }), lap({ gear_judged: 2, gear_high: 0 })];
    expect(constantColumns(alike).get("gear")).toBe("0/2");
    const apart = [alike[0], lap({ gear_judged: 2, gear_high: 1 })];
    expect(constantColumns(apart).has("gear")).toBe(false);
  });

  it("takes each lap's figures from the gear-choice answer, by lap id", () => {
    const laps = [lap({ id: 1 }), lap({ id: 2 })];
    const merged = withGearChoice(laps, {
      session_id: 1,
      reference: 1,
      laps: [{ lap_id: 2, judged: 3, gear_high: 1 }],
    });
    expect(merged[0]).toBe(laps[0]);
    expect(merged[1]).toMatchObject({ id: 2, gear_judged: 3, gear_high: 1 });
    expect(withGearChoice(laps, null)).toBe(laps);
  });

  it("is taken off again before a lap is stored back", () => {
    const [merged] = withGearChoice([lap({ id: 2 })], {
      session_id: 1,
      reference: 1,
      laps: [{ lap_id: 2, judged: 3, gear_high: 1 }],
    });
    const plain = withoutGearChoice(merged);
    expect(plain).not.toHaveProperty("gear_judged");
    expect(plain).not.toHaveProperty("gear_high");
    expect(plain.id).toBe(2);
    const untouched = lap({ id: 3 });
    expect(withoutGearChoice(untouched)).toBe(untouched);
  });
});

describe("formatEventCounts", () => {
  it("prints letters in a fixed order and a dash when clean", () => {
    expect(formatEventCounts({ kerb: 4, lockup: 2 })).toBe("2L·4K");
    expect(formatEventCounts({})).toBe("–");
    expect(formatEventCounts(undefined)).toBe("–");
  });
});
