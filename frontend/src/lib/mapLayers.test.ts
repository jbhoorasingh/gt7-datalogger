import { describe, expect, it } from "vitest";
import {
  aidPoints,
  brakeDeltaText,
  brakeMarkers,
  eventMarkers,
  hasAid,
  severityText,
  wheelsText,
} from "./mapLayers";
import {
  AIDS_ASM,
  AIDS_TCS,
  type CompareLapEntry,
  type CornerReportRow,
  type LapEvent,
} from "./types";

// A straight 100 m run along +x, sampled every 10 m.
function entry(events: LapEvent[], aids?: number[]): CompareLapEntry {
  const dist = Array.from({ length: 11 }, (_, i) => i * 10);
  return {
    series: {
      dist,
      pos_x: dist.map((d) => d),
      pos_z: dist.map(() => 5),
      ...(aids ? { aids } : {}),
    },
    peaks_valleys: { peaks: [], valleys: [] },
    events,
  };
}

function event(type: LapEvent["type"], start: number, wheels = ["fl"], severity = 0.5): LapEvent {
  return { type, start_dist: start, end_dist: start + 8, wheels, severity };
}

describe("eventMarkers", () => {
  it("puts a marker where the event began, between samples", () => {
    const [m] = eventMarkers(entry([event("lockup", 34)]));
    expect(m.x).toBeCloseTo(34, 6);
    expect(m.z).toBeCloseTo(5, 6);
    expect(m.event.type).toBe("lockup");
  });

  it("keeps only events inside the zoom window", () => {
    const e = entry([event("lockup", 20), event("wheelspin", 70)]);
    expect(eventMarkers(e, [50, 100]).map((m) => m.event.type)).toEqual(["wheelspin"]);
    expect(eventMarkers(e, null)).toHaveLength(2);
  });

  it("drops an event past the end of the series rather than pinning it to the last sample", () => {
    expect(eventMarkers(entry([event("lockup", 140)]))).toEqual([]);
  });

  it("merges per-wheel events of one type that began in the same place", () => {
    const markers = eventMarkers(
      entry([
        event("bottoming", 40, ["rr"], 0.98),
        event("bottoming", 41, ["fl"], 0.99),
        event("bottoming", 43, ["rl"], 0.985),
        event("kerb", 42, ["fl"], 0.7),
        event("bottoming", 80, ["fl"], 0.98),
      ]),
    );
    expect(markers.map((m) => [m.event.type, m.event.start_dist])).toEqual([
      ["bottoming", 40],
      ["kerb", 42],
      ["bottoming", 80],
    ]);
    expect(markers[0].event.wheels).toEqual(["fl", "rl", "rr"]);
    expect(markers[0].event.severity).toBe(0.99);
    expect(markers[0].event.end_dist).toBe(51);
  });

  it("keeps the LOWEST slip ratio when merging lockups, since lower is worse", () => {
    const [m] = eventMarkers(
      entry([event("lockup", 40, ["fl"], 0.8), event("lockup", 42, ["fr"], 0.6)]),
    );
    expect(m.event.severity).toBe(0.6);
  });

  it("does not mutate the lap's own events", () => {
    const events = [event("kerb", 40, ["fl"]), event("kerb", 41, ["fr"])];
    eventMarkers(entry(events));
    expect(events[0].wheels).toEqual(["fl"]);
  });

  it("is empty for a lap recorded without events", () => {
    const e = entry([]);
    delete e.events;
    expect(eventMarkers(e)).toEqual([]);
  });
});

describe("aidPoints", () => {
  const aids = [0, AIDS_TCS, AIDS_TCS | AIDS_ASM, AIDS_ASM, 0, 0, 0, 0, AIDS_TCS, 0, 0];

  it("returns one position per sample with the bit set", () => {
    const s = entry([], aids).series;
    expect(aidPoints(s, AIDS_TCS)).toEqual([[10, 5], [20, 5], [80, 5]]);
    expect(aidPoints(s, AIDS_ASM)).toEqual([[20, 5], [30, 5]]);
  });

  it("respects the zoom window", () => {
    expect(aidPoints(entry([], aids).series, AIDS_TCS, [50, 100])).toEqual([[80, 5]]);
  });

  it("draws nothing for a recording from before the column existed", () => {
    const s = entry([]).series;
    expect(aidPoints(s, AIDS_TCS)).toEqual([]);
    expect(hasAid(s, AIDS_TCS)).toBe(false);
  });

  it("knows which aids a lap used", () => {
    const s = entry([], [0, AIDS_ASM, 0, 0, 0, 0, 0, 0, 0, 0, 0]).series;
    expect(hasAid(s, AIDS_ASM)).toBe(true);
    expect(hasAid(s, AIDS_TCS)).toBe(false);
  });
});

describe("tooltip text", () => {
  it("names the wheels", () => {
    expect(wheelsText(["fl"])).toBe("front left");
    expect(wheelsText(["fl", "rr"])).toBe("front left, rear right");
    expect(wheelsText(["fl", "fr", "rl", "rr"])).toBe("all four wheels");
  });

  it("reads severity as what it measures for that event type", () => {
    expect(severityText(event("lockup", 0, ["fl"], 0.62))).toBe(
      "slowest wheel at 62% of road speed",
    );
    expect(severityText(event("wheelspin", 0, ["rl"], 1.34))).toBe(
      "fastest wheel at 134% of road speed",
    );
    expect(severityText(event("bottoming", 0, ["rl"], 0.99))).toBe(
      "compressed to 99% of the lap's travel",
    );
  });
});

describe("brakeMarkers", () => {
  const row = (n: number, over: Partial<CornerReportRow>): CornerReportRow => ({
    n,
    entry_speed: 180,
    min_speed: 90,
    exit_speed: 120,
    time_ms: 5000,
    ...over,
  });
  const lap = (id: string, isRef: boolean, report: CornerReportRow[]) => ({
    id,
    label: `L${id}`,
    color: "#fff",
    isRef,
    entry: { ...entry([]), corner_report: report },
  });

  it("puts each lap's marker where its brake went on", () => {
    const marks = brakeMarkers(
      [
        lap("1", true, [row(3, { brake_on: 48 })]),
        lap("2", false, [row(3, { brake_on: 34, brake_delta_m: -14 })]),
      ],
      3,
    );
    expect(marks.map((m) => [m.lapId, m.x, m.deltaM])).toEqual([
      ["1", 48, null],
      ["2", 34, -14],
    ]);
    expect(marks[1].z).toBeCloseTo(5, 6);
  });

  it("draws nothing for a lap that took the corner without braking", () => {
    const marks = brakeMarkers(
      [
        lap("1", true, [row(3, { brake_on: 48 })]),
        lap("2", false, [row(3, { brake_on: null, brake_delta_m: null })]),
      ],
      3,
    );
    expect(marks.map((m) => m.lapId)).toEqual(["1"]);
  });

  it("draws nothing for a corner the lap has no row for, or for no corner", () => {
    const laps = [lap("1", true, [row(3, { brake_on: 48 })])];
    expect(brakeMarkers(laps, 4)).toEqual([]);
    expect(brakeMarkers(laps, null)).toEqual([]);
  });

  it("reads a report from before the braking columns as no braking", () => {
    expect(brakeMarkers([lap("1", true, [row(3, {})])], 3)).toEqual([]);
  });
});

describe("brakeDeltaText", () => {
  it("says earlier or later, in whole metres", () => {
    expect(brakeDeltaText(-13.7)).toBe("14 m earlier than the reference");
    expect(brakeDeltaText(6.2)).toBe("6 m later than the reference");
    expect(brakeDeltaText(0.3)).toBe("level with the reference");
  });
});
