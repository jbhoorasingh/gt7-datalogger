import { describe, expect, it } from "vitest";
import { CHANNEL_BY_KEY } from "./channels";
import {
  canDraw,
  gainClass,
  GAIN_COLORS,
  INPUT_COLORS,
  isMapLens,
  LENS_OPTIONS,
  lensRuns,
  lensSeries,
  SPEED_COLORS,
  speedBands,
  TIRE_BALANCE_COLORS,
  TIRE_TEMP_COLORS,
  tireBalanceClass,
  tireTempBand,
  zoneOf,
} from "./mapLens";

// A 200 m lap sampled every 10 m: full throttle, then braking, then coasting.
function lap() {
  const dist = Array.from({ length: 21 }, (_, i) => i * 10);
  return {
    dist,
    throttle: dist.map((d) => (d < 100 ? 100 : 0)),
    brake: dist.map((d) => (d >= 100 && d < 150 ? 80 : 0)),
    speed: dist.map((d) => 100 + d), // 100 … 300 km/h
  };
}

// The same lap with tire temperatures, one series per wheel.
function withTemps(fl: number[], fr: number[], rl: number[], rr: number[]) {
  return { ...lap(), tt_fl: fl, tt_fr: fr, tt_rl: rl, tt_rr: rr };
}

// One temperature all lap long.
const flat = (celsius: number) => lap().dist.map(() => celsius);

describe("zoneOf", () => {
  it("brake beats throttle, and neither pedal is coasting", () => {
    expect(zoneOf(100, 0)).toBe(2);
    expect(zoneOf(100, 5)).toBe(0);
    expect(zoneOf(0, 0)).toBe(1);
  });
});

describe("lensSeries · inputs", () => {
  it("classifies every sample and legends the three zones", () => {
    const lens = lensSeries("inputs", lap(), "metric", null)!;
    expect(lens.palette).toBe(INPUT_COLORS);
    expect(lens.keys.slice(0, 3)).toEqual([2, 2, 2]);
    expect(lens.keys[10]).toBe(0);
    expect(lens.keys[20]).toBe(1);
    expect(lens.legend.map((l) => l.label)).toEqual(["throttle", "brake", "coast"]);
  });
});

describe("lensSeries · speed", () => {
  it("splits the lap's own speed range into five bands, rounded to 5", () => {
    expect(speedBands(lap().speed, "metric")).toEqual([140, 180, 220, 260]);
    const lens = lensSeries("speed", lap(), "metric", null)!;
    expect(lens.palette).toBe(SPEED_COLORS);
    expect(lens.keys[0]).toBe(0);
    expect(lens.keys[20]).toBe(4);
    expect(lens.legend[0].label).toBe("< 140");
    expect(lens.legend[4].label).toBe("> 260 km/h");
  });

  it("legends in the user's units", () => {
    const lens = lensSeries("speed", lap(), "imperial", null)!;
    expect(lens.legend[4].label).toMatch(/mph$/);
  });

  it("has nothing to colour by without a speed channel", () => {
    const { dist, throttle, brake } = lap();
    expect(lensSeries("speed", { dist, throttle, brake }, "metric", null)).toBeNull();
  });
});

describe("lensSeries · gain / loss", () => {
  // The comparison lap loses 2 ms a metre over the first half, then holds.
  const gain = {
    label: "L9 · 1:02.000",
    dist: Array.from({ length: 21 }, (_, i) => i * 10),
    delta_ms: Array.from({ length: 21 }, (_, i) => Math.min(i * 10, 100) * 2),
  };

  it("reads the slope of the delta: losing, even, gaining", () => {
    expect(gainClass(gain, 50)).toBe(0);
    expect(gainClass(gain, 180)).toBe(1);
    const gaining = { ...gain, delta_ms: gain.delta_ms.map((v) => -v) };
    expect(gainClass(gaining, 50)).toBe(2);
  });

  it("names the compared lap in the legend", () => {
    const lens = lensSeries("gain", lap(), "metric", gain)!;
    expect(lens.palette).toBe(GAIN_COLORS);
    expect(lens.legend.map((l) => l.label)).toEqual(["L9 gains", "L9 loses", "even"]);
  });

  it("needs a comparison lap", () => {
    expect(lensSeries("gain", lap(), "metric", null)).toBeNull();
  });

  it("answers availability exactly as the renderer does", () => {
    const degenerate = { label: "L9", dist: [0], delta_ms: [0] };
    expect(canDraw("gain", lap(), "metric", degenerate)).toBe(false);
    expect(lensSeries("gain", lap(), "metric", degenerate)).toBeNull();
    expect(canDraw("gain", lap(), "metric", gain)).toBe(true);
    const { dist, throttle, brake } = lap();
    expect(canDraw("speed", { dist, throttle, brake }, "metric", null)).toBe(false);
    expect(canDraw("inputs", { dist, throttle, brake }, "metric", null)).toBe(true);
  });
});

describe("lensRuns", () => {
  it("merges consecutive samples of one class into one run", () => {
    const lens = lensSeries("inputs", lap(), "metric", null)!;
    const runs = lensRuns(lens, lap().dist);
    expect(runs).toEqual([
      { start: 0, end: 100, color: INPUT_COLORS[2] },
      { start: 100, end: 150, color: INPUT_COLORS[0] },
      { start: 150, end: 200, color: INPUT_COLORS[1] },
    ]);
  });
});

describe("lensSeries · tire temp", () => {
  it("bands at the tiles' edges: 55 °C is working, 95 °C is hot", () => {
    expect(tireTempBand(54.9)).toBe(0);
    expect(tireTempBand(55)).toBe(1);
    expect(tireTempBand(94.9)).toBe(1);
    expect(tireTempBand(95)).toBe(2);
  });

  it("colours each sample by its hottest tire", () => {
    const rearLeft = lap().dist.map((d) => (d < 100 ? 50 : 100));
    const series = withTemps(flat(60), flat(60), rearLeft, flat(40));
    const lens = lensSeries("tire_temp", series, "metric", null)!;
    expect(lens.palette).toBe(TIRE_TEMP_COLORS);
    expect(lens.keys[0]).toBe(1); // the fronts' 60 over the rears' 50 and 40
    expect(lens.keys[20]).toBe(2); // the rear left's 100
    expect(lens.legend.map((l) => l.label)).toEqual(["< 55", "55–95", "≥ 95 °C"]);
  });

  it("merges a stretch of one band into one run for the strip", () => {
    const hot = lap().dist.map((d) => (d >= 50 && d < 120 ? 96 : 70));
    const series = withTemps(hot, flat(70), flat(70), flat(70));
    const lens = lensSeries("tire_temp", series, "metric", null)!;
    expect(lensRuns(lens, series.dist)).toEqual([
      { start: 0, end: 50, color: TIRE_TEMP_COLORS[1] },
      { start: 50, end: 120, color: TIRE_TEMP_COLORS[2] },
      { start: 120, end: 200, color: TIRE_TEMP_COLORS[1] },
    ]);
  });
});

describe("lensSeries · F−R temp", () => {
  it("is even within ±3 °C, as Corner Detail's readout, and full colour from ±8", () => {
    expect(tireBalanceClass(-10)).toBe(0);
    expect(tireBalanceClass(-8)).toBe(0);
    expect(tireBalanceClass(-7.9)).toBe(1);
    expect(tireBalanceClass(-3.1)).toBe(1);
    expect(tireBalanceClass(-3)).toBe(2);
    expect(tireBalanceClass(0)).toBe(2);
    expect(tireBalanceClass(3)).toBe(2);
    expect(tireBalanceClass(3.1)).toBe(3);
    expect(tireBalanceClass(7.9)).toBe(3);
    expect(tireBalanceClass(8)).toBe(4);
  });

  it("reads hotter fronts as red and hotter rears as blue", () => {
    // Fronts 10 °C hotter over the first half of the lap, rears 5 °C hotter after.
    const front = lap().dist.map((d) => (d < 100 ? 90 : 75));
    const series = withTemps(front, front, flat(80), flat(80));
    const lens = lensSeries("tire_balance", series, "metric", null)!;
    expect(lens.palette).toBe(TIRE_BALANCE_COLORS);
    expect(lens.keys[0]).toBe(4);
    expect(TIRE_BALANCE_COLORS[4]).toBe(INPUT_COLORS[0]); // brake red
    expect(lens.keys[20]).toBe(1);
    expect(TIRE_BALANCE_COLORS[0]).toBe(INPUT_COLORS[1]); // coast blue
    expect(lens.legend.map((l) => l.label)).toEqual([
      "rears ≥ 8",
      "rears 3–8",
      "even",
      "fronts 3–8",
      "fronts ≥ 8 °C",
    ]);
  });

  it("measures the balance the way the charts' F−R channel does", () => {
    const series = withTemps(flat(91), flat(84), flat(80), flat(83));
    const balance = CHANNEL_BY_KEY.tt_balance.derive!(series, 0);
    expect(balance).toBe(6);
    const lens = lensSeries("tire_balance", series, "metric", null)!;
    expect(lens.keys[0]).toBe(tireBalanceClass(balance));
  });
});

describe("lens availability", () => {
  const gain = {
    label: "L9 · 1:02.000",
    dist: Array.from({ length: 21 }, (_, i) => i * 10),
    delta_ms: Array.from({ length: 21 }, (_, i) => i * 20),
  };

  it("needs all four tire temperatures for the tire lenses", () => {
    const full = withTemps(flat(70), flat(70), flat(70), flat(70));
    const { dist, throttle, brake, speed, tt_fl, tt_fr, tt_rl } = full;
    const threeWheels = { dist, throttle, brake, speed, tt_fl, tt_fr, tt_rl };
    const emptyRear = { ...full, tt_rr: [] };
    for (const lens of ["tire_temp", "tire_balance"] as const) {
      expect(canDraw(lens, full, "metric", null)).toBe(true);
      expect(canDraw(lens, lap(), "metric", null)).toBe(false);
      expect(canDraw(lens, threeWheels, "metric", null)).toBe(false);
      expect(canDraw(lens, emptyRear, "metric", null)).toBe(false);
      expect(lensSeries(lens, lap(), "metric", null)).toBeNull();
    }
  });

  it("answers availability exactly as the renderer does, for every lens", () => {
    const { dist, throttle, brake } = lap();
    const cases = [
      { series: withTemps(flat(70), flat(70), flat(70), flat(70)), source: gain },
      { series: lap(), source: null },
      { series: { dist, throttle, brake }, source: { ...gain, dist: [0], delta_ms: [0] } },
    ];
    for (const { series, source } of cases) {
      for (const { value } of LENS_OPTIONS) {
        const drawn = lensSeries(value, series, "metric", source) != null;
        expect(canDraw(value, series, "metric", source), value).toBe(drawn);
      }
    }
  });

  it("says why, for every lens the control can grey out", () => {
    for (const o of LENS_OPTIONS) {
      if (o.value !== "inputs") expect(o.needs?.trim(), o.value).toBeTruthy();
    }
  });
});

describe("isMapLens", () => {
  it("knows every lens the control offers, and nothing else", () => {
    for (const o of LENS_OPTIONS) expect(isMapLens(o.value)).toBe(true);
    for (const v of ["tyres", "", "Inputs", undefined, null, 42]) expect(isMapLens(v)).toBe(false);
  });
});
