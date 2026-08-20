// Samples the 30 Hz telemetry ref on the browser's animation clock — shared
// by the overlay, the driver dashboard, and the builder canvas. Falls back to
// the synthetic demo lap when telemetry is stale and demo mode is on.

import { useEffect, useRef, useState } from "react";
import { DEMO_LAPS, demoFrame } from "./demoFrame";
import type { LapSummary, LiveFrame } from "./types";
import { liveFrameRef, useTelemetry } from "@/store/telemetry";

export const STALE_AFTER_MS = 3000;

// React commits are decoupled from the animation clock (#32): the rAF loop
// keeps sampling every frame, but state updates — each one a re-render of
// the whole consuming view — are capped at this rate. 15 Hz halves the live
// re-render rate (telemetry arrives at ~30 Hz) and cuts demo mode's from 60,
// which is what keeps a Pi or a tablet responsive; readouts and bars are
// indistinguishable at this cadence.
const UI_UPDATE_HZ = 15;

export interface LiveSample {
  frame: LiveFrame | null;
  laps: LapSummary[];
  placeholder: boolean; // true while showing demo data
}

// `enabled: false` stops the loop entirely (the last frame stays on screen)
// — for consumers that can scroll out of view, like the builder canvas.
export function useLiveFrame(demo: boolean, enabled = true): LiveSample {
  const [frame, setFrame] = useState<LiveFrame | null>(null);
  const [placeholder, setPlaceholder] = useState(false);
  const recentLaps = useTelemetry((s) => s.recentLaps);
  const raf = useRef(0);

  useEffect(() => {
    if (!enabled) return;
    const minGapMs = 1000 / UI_UPDATE_HZ;
    let lastCommit = -Infinity;
    const tick = () => {
      raf.current = requestAnimationFrame(tick);
      const now = performance.now();
      if (now - lastCommit < minGapMs) return;
      lastCommit = now;
      const live =
        liveFrameRef.current && now - liveFrameRef.at < STALE_AFTER_MS
          ? liveFrameRef.current
          : null;
      if (live) {
        setFrame(live);
        setPlaceholder(false);
      } else if (demo) {
        setFrame(demoFrame(now));
        setPlaceholder(true);
      } else {
        setFrame(liveFrameRef.current);
        setPlaceholder(false);
      }
    };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
  }, [demo, enabled]);

  return { frame, laps: placeholder ? DEMO_LAPS : recentLaps, placeholder };
}
