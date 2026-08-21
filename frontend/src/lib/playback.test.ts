// The playback clock (#59) is the only piece of Analysis that moves on its
// own; these pin the properties the transport relies on: monotone advance,
// hard stop at the lap end, speed scaling, and a scrub that hands the clock a
// position instead of fighting it.

import { describe, expect, it } from "vitest";
import {
  advancePlayhead,
  distAtTime,
  frameAtTime,
  playbackEnd,
  timeAtDist,
  type PlaybackSeries,
} from "./playback";

// A 100 m lap resampled every 10 m, driven at 20 m/s until halfway and
// 10 m/s after — so time is NOT linear in distance, which is the whole point
// of advancing the playhead on the lap's own clock.
function series(): PlaybackSeries {
  const dist = Array.from({ length: 11 }, (_, i) => i * 10);
  const t = dist.map((d) => (d <= 50 ? d / 20 : 2.5 + (d - 50) / 10));
  return {
    dist,
    t,
    speed: dist.map((d) => (d <= 50 ? 72 : 36)),
    gear: dist.map((d) => (d <= 50 ? 4 : 3)),
    throttle: dist.map(() => 100),
    brake: dist.map(() => 0),
    steer: dist.map((d) => d / 100),
  };
}

describe("advancePlayhead", () => {
  it("advances monotonically and scales with the speed multiplier", () => {
    let t = 0;
    const seen: number[] = [];
    for (let i = 0; i < 10; i++) {
      t = advancePlayhead(t, 100, 1, 60).t;
      seen.push(t);
    }
    expect(seen).toEqual(seen.slice().sort((a, b) => a - b));
    expect(t).toBeCloseTo(1.0);

    expect(advancePlayhead(0, 1000, 0.25, 60).t).toBeCloseTo(0.25);
    expect(advancePlayhead(0, 1000, 4, 60).t).toBeCloseTo(4);
  });

  it("stops exactly at the lap end and reports it", () => {
    const end = playbackEnd(series()); // 7.5 s
    expect(end).toBeCloseTo(7.5);
    const r = advancePlayhead(7.4, 1000, 2, end);
    expect(r.ended).toBe(true);
    expect(r.t).toBe(end);
    // Once at the end it stays there — no creep past the last sample.
    expect(advancePlayhead(end, 100, 1, end)).toEqual({ t: end, ended: true });
  });

  it("never runs backwards, even on a negative frame delta", () => {
    expect(advancePlayhead(3, -50, 1, 60).t).toBe(3);
  });
});

describe("distance/time mapping", () => {
  it("inverts: scrubbing to a distance resumes from that lap time", () => {
    const s = series();
    for (const dist of [0, 15, 50, 85, 100]) {
      expect(distAtTime(s, timeAtDist(s, dist))).toBeCloseTo(dist);
    }
  });

  it("advances on the lap's own clock — slow sectors play slowly", () => {
    const s = series();
    // 1 s of playback in the fast half covers 20 m; in the slow half, 10 m.
    expect(distAtTime(s, 1) - distAtTime(s, 0)).toBeCloseTo(20);
    expect(distAtTime(s, 4) - distAtTime(s, 3)).toBeCloseTo(10);
  });

  it("clamps outside the lap instead of extrapolating", () => {
    const s = series();
    expect(distAtTime(s, -1)).toBe(0);
    expect(distAtTime(s, 999)).toBe(100);
  });

  it("a scrub mid-play does not fight the clock", () => {
    const s = series();
    const end = playbackEnd(s);
    advancePlayhead(0, 1000, 1, end); // 1 s in when the user grabs the bar
    const t = timeAtDist(s, 80); // ...and drags the scrubber to 80 m
    const after = advancePlayhead(t, 500, 1, end).t;
    expect(distAtTime(s, after)).toBeGreaterThan(80); // continues from there
    expect(after).toBeCloseTo(t + 0.5);
  });
});

describe("frameAtTime", () => {
  it("synthesizes a LiveFrame the widgets can render", () => {
    const f = frameAtTime(series(), 1.25);
    expect(f.speed_kmh).toBeCloseTo(72);
    expect(f.throttle).toBe(100);
    expect(f.gear).toBe(4);
    expect(f.lap_elapsed_ms).toBe(1250);
    expect(f.steer_rad).toBeCloseTo(0.25);
  });

  it("degrades to 'no data' when a channel is absent", () => {
    const s = series();
    delete s.steer;
    const f = frameAtTime(s, 1);
    expect(f.steer_rad).toBeNull();
    expect(f.position).toBe(-1); // no race_pos channel -> no position
  });
});
