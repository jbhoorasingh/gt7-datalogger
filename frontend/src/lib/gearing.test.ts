import { describe, expect, it } from "vitest";
import {
  fitScale,
  gearScale,
  MAX_SCATTER_POINTS,
  plausibleTopSpeed,
  redlineSpeeds,
  sameGearing,
  scatterPoints,
  wholeGear,
} from "./gearing";
import type { LapGearing, Samples } from "./types";

// The tuned AE86 of the design fixtures (Tsukuba, lap 1063): five ratios, a
// 9,000 rpm redline, and the ratio GT7 used to send as its "top speed".
const AE86: LapGearing = { ratios: [2.512, 1.675, 1.205, 0.917, 0.737], top_speed: 2.3, rpm_alert: 9000 };
// What its own samples say, median of speed × ratio ÷ rpm.
const AE86_K = 0.02518;

// A small deterministic generator, so a noisy lap is the same lap every run.
function noise(seed: number): () => number {
  let s = seed;
  return () => {
    s = (s * 1664525 + 1013904223) % 4294967296;
    return s / 4294967296 - 0.5;
  };
}

type Row = { speed: number; rpm: number; gear: number; tire_slip: number };

function series(rows: Row[]): Samples {
  return {
    speed: rows.map((r) => r.speed),
    rpm: rows.map((r) => r.rpm),
    gear: rows.map((r) => r.gear),
    tire_slip: rows.map((r) => r.tire_slip),
  };
}

/** A lap pulling through every gear from 5,000 rpm to just under the
 *  redline, speeds off by up to ±2 %, `perGear` samples a gear. */
function sawtooth(gearing: LapGearing, k: number, perGear = 60, seed = 1): Row[] {
  const jitter = noise(seed);
  const rows: Row[] = [];
  gearing.ratios.forEach((ratio, i) => {
    for (let j = 0; j < perGear; j++) {
      const rpm = 5000 + (j / perGear) * (gearing.rpm_alert - 5200);
      rows.push({ speed: ((k * rpm) / ratio) * (1 + 0.04 * jitter()), rpm, gear: i + 1, tire_slip: 1 });
    }
  });
  return rows;
}

describe("plausibleTopSpeed", () => {
  it("takes a speed for a speed and the old ratio for what it is", () => {
    expect(plausibleTopSpeed({ ...AE86, top_speed: 290 })).toBe(true);
    expect(plausibleTopSpeed(AE86)).toBe(false);
    expect(plausibleTopSpeed({ ...AE86, top_speed: 0 })).toBe(false);
  });
});

describe("wholeGear", () => {
  it("reads a gear only when the sample sat in one", () => {
    expect(wholeGear(3, 5)).toBe(3);
    expect(wholeGear(3.004, 5)).toBe(3);
    expect(wholeGear(2.4, 5)).toBeNull(); // between two ticks either side of a shift
    expect(wholeGear(0, 5)).toBeNull(); // reverse
    expect(wholeGear(15, 5)).toBeNull(); // neutral
    expect(wholeGear(6, 5)).toBeNull();
  });
});

describe("fitScale", () => {
  it("recovers the AE86's speed per rpm from a noisy lap", () => {
    const k = fitScale(series(sawtooth(AE86, AE86_K)), AE86)!;
    expect(Math.abs(k / AE86_K - 1)).toBeLessThan(0.01);
    const expected = [90.2, 135.3, 188.1, 247.1, 307.5];
    redlineSpeeds(AE86, k).forEach((v, i) => expect(Math.abs(v - expected[i])).toBeLessThan(1.5));
  });

  it("ignores wheelspin, shifts, neutral, reverse and crawling", () => {
    const rows = sawtooth(AE86, AE86_K);
    const clean = fitScale(series(rows), AE86)!;
    // A tenth of the lap spinning the wheels: rpm 20 % over what the road
    // speed asks for, and the slip channel says so.
    for (let i = 0; i < rows.length; i += 10) {
      rows.push({ ...rows[i], rpm: rows[i].rpm * 1.2, tire_slip: 1.2 });
    }
    rows.push(
      { speed: 120, rpm: 4000, gear: 2.4, tire_slip: 1 },
      { speed: 140, rpm: 3000, gear: 15, tire_slip: 1 },
      { speed: 40, rpm: 8000, gear: 0, tire_slip: 1 },
      { speed: 20, rpm: 8500, gear: 1, tire_slip: 1 },
      { speed: 60, rpm: 2000, gear: 1, tire_slip: 1 },
    );
    expect(fitScale(series(rows), AE86)).toBeCloseTo(clean, 6);
  });

  it("shrugs off wheelspin by the median alone without the slip channel", () => {
    const rows = sawtooth(AE86, AE86_K);
    for (let i = 0; i < rows.length; i += 10) rows.push({ ...rows[i], rpm: rows[i].rpm * 1.2 });
    const s = series(rows);
    delete s.tire_slip;
    expect(Math.abs(fitScale(s, AE86)! / AE86_K - 1)).toBeLessThan(0.01);
  });

  it("needs thirty clean samples", () => {
    const rows = sawtooth(AE86, AE86_K);
    const fourth = rows.filter((r) => r.gear === 4);
    const fifth = rows.filter((r) => r.gear === 5);
    expect(fitScale(series([...fourth.slice(0, 15), ...fifth.slice(0, 14)]), AE86)).toBeNull();
    expect(fitScale(series([...fourth.slice(0, 15), ...fifth.slice(0, 15)]), AE86)).not.toBeNull();
  });

  it("needs two gears to agree, unless the car has one", () => {
    // A lap flat out in fifth all the way round an oval.
    const fifth = sawtooth(AE86, AE86_K).filter((r) => r.gear === 5);
    expect(fitScale(series(fifth), AE86)).toBeNull();
    const ev: LapGearing = { ratios: [2.97], top_speed: 3.0, rpm_alert: 10000 };
    expect(fitScale(series(sawtooth(ev, 0.03, 60)), ev)).toBeCloseTo(0.03, 3);
  });

  it("does not let a gear barely used veto the fit", () => {
    const rows = sawtooth(AE86, AE86_K).filter((r) => r.gear > 1);
    // Three samples of first out of a hairpin, the clutch still slipping.
    rows.unshift(...[6000, 6500, 7000].map((rpm) => ({ speed: 35, rpm, gear: 1, tire_slip: 1 })));
    expect(Math.abs(fitScale(series(rows), AE86)! / AE86_K - 1)).toBeLessThan(0.01);
  });

  it("refuses a lap whose rpm does not follow its ratios", () => {
    // How the simulator makes rpm: a ramp every 60 km/h, whatever the gear.
    const sim: LapGearing = { ratios: [3.2, 2.3, 1.8, 1.4, 1.15, 0.95], top_speed: 290, rpm_alert: 9000 };
    const rows: Row[] = [];
    for (let kmh = 40; kmh < 280; kmh += 0.5) {
      const gear = Math.min(6, Math.floor(kmh / 45) + 1);
      rows.push({ speed: kmh, rpm: 2000 + ((kmh % 60) / 60) * 5500 + gear * 100, gear, tire_slip: 1 });
    }
    expect(fitScale(series(rows), sim)).toBeNull();
    expect(gearScale(series(rows), sim)).toEqual({ k: (290 * 0.95) / 9000, source: "top_speed" });
  });
});

describe("gearScale", () => {
  it("prefers the lap's own fit", () => {
    const scale = gearScale(series(sawtooth(AE86, AE86_K)), { ...AE86, top_speed: 250 })!;
    expect(scale.source).toBe("fit");
    expect(scale.k).toBeCloseTo(AE86_K, 3);
  });

  it("falls back to GT7's top speed as top gear at the redline", () => {
    const scale = gearScale(null, { ...AE86, top_speed: 250 })!;
    expect(scale.source).toBe("top_speed");
    expect(redlineSpeeds(AE86, scale.k)[4]).toBeCloseTo(250, 6);
  });

  it("has nothing to draw with for an old lap the fit refuses", () => {
    expect(gearScale(null, AE86)).toBeNull();
    expect(gearScale(series(sawtooth(AE86, AE86_K, 5)), AE86)).toBeNull();
  });
});

describe("redlineSpeeds", () => {
  it("is K × redline ÷ ratio for every gear", () => {
    const speeds = redlineSpeeds(AE86, AE86_K);
    [90.2, 135.3, 188.1, 247.1, 307.5].forEach((v, i) => expect(speeds[i]).toBeCloseTo(v, 1));
  });
});

describe("scatterPoints", () => {
  it("keeps whole gears in the box, in lap order", () => {
    const points = scatterPoints(
      series([
        { speed: 50, rpm: 6000, gear: 1, tire_slip: 1 },
        { speed: 55, rpm: 6500, gear: 1.5, tire_slip: 1 },
        { speed: 60, rpm: 5000, gear: 2, tire_slip: 1 },
        { speed: 60, rpm: 1000, gear: 15, tire_slip: 1 },
        { speed: 10, rpm: 3000, gear: 0, tire_slip: 1 },
        { speed: 80, rpm: 7000, gear: 6, tire_slip: 1 },
      ]),
      5,
    );
    expect(points).toEqual([
      { speed: 50, rpm: 6000, gear: 1 },
      { speed: 60, rpm: 5000, gear: 2 },
    ]);
  });

  it("thins a long lap but keeps every shift's two sides", () => {
    // About the length of a Nürburgring lap at the 5 m compare step, shifting
    // up and down through five gears over and over. Speed creeps up with the
    // sample, so every point says which sample it came from.
    const rows: Row[] = [];
    for (let i = 0; i < 5008; i++) {
      const gear = 1 + (Math.floor(i / 40) % 5);
      rows.push({ speed: 50 + gear * 20 + i / 1000, rpm: 5000 + (i % 40) * 90, gear, tire_slip: 1 });
    }
    const points = scatterPoints(series(rows), 5);
    expect(points.length).toBeLessThanOrEqual(MAX_SCATTER_POINTS);
    expect(points.length).toBeGreaterThan(MAX_SCATTER_POINTS * 0.9);

    const index = new Map(rows.map((r, i) => [r.speed, i]));
    const kept = points.map((p) => index.get(p.speed)!);
    // In lap order.
    expect(kept).toEqual([...kept].sort((a, b) => a - b));
    const keptSet = new Set(kept);
    for (let i = 1; i < rows.length; i++) {
      if (rows[i].gear === rows[i - 1].gear) continue;
      expect(keptSet.has(i - 1)).toBe(true);
      expect(keptSet.has(i)).toBe(true);
    }
  });
});

describe("sameGearing", () => {
  it("matches the same box and nothing else", () => {
    expect(sameGearing(AE86, { ...AE86, ratios: [...AE86.ratios] })).toBe(true);
    expect(sameGearing(AE86, { ...AE86, ratios: [2.5124, 1.675, 1.205, 0.917, 0.737] })).toBe(true);
    expect(sameGearing(AE86, { ...AE86, ratios: AE86.ratios.slice(0, 4) })).toBe(false);
    expect(sameGearing(AE86, { ...AE86, ratios: [2.512, 1.685, 1.205, 0.917, 0.737] })).toBe(false);
    expect(sameGearing(AE86, { ...AE86, rpm_alert: 8500 })).toBe(false);
    // The stored top speed is no part of the gearbox.
    expect(sameGearing(AE86, { ...AE86, top_speed: 210 })).toBe(true);
  });
});
