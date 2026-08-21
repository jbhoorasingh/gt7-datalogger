// Transport for lap playback on Analysis (#59): play / pause / scrub / speed,
// owning a rAF clock that feeds the SAME onCursorDist the chart hover uses —
// so the stacked charts, the race-line dot and the Corner Detail panel all
// animate without knowing playback exists. Alongside it, a strip of readouts
// (steering wheel, pedals, gear, speed) drawn from a LiveFrame synthesized at
// the playhead — sized for the transport rather than reusing the dashboard
// widgets, and on fixed-width numerals so the row never reflows mid-lap.
//
// The clock advances in the REFERENCE lap's own time (see lib/playback.ts),
// pauses itself when the tab is hidden, and under prefers-reduced-motion
// steps the cursor a few times a second instead of every frame.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { Tip } from "@/components/ui/Tooltip";
import { formatLapTime, speedUnit, speedValue } from "@/lib/format";
import {
  advancePlayhead,
  distAtTime,
  frameAtTime,
  PLAYBACK_SPEEDS,
  playbackEnd,
  type PlaybackSeries,
} from "@/lib/playback";
import type { LapSummary } from "@/lib/types";
import { useSettings } from "@/store/settings";

// The playhead is sampled every animation frame; React state (time readout,
// scrub position, widget strip) commits at this rate, same trick as
// useLiveFrame (#32). The cursor itself goes out per frame — the consumers
// are already rAF-throttled — except under reduced motion.
const UI_UPDATE_HZ = 12;
const REDUCED_MOTION_HZ = 4;

export function PlaybackBar({
  series,
  lap,
  onCursorDist,
  onPlayingChange,
}: {
  series: PlaybackSeries;
  /** Summary of the reference lap, for the widget strip's car/lap fields. */
  lap?: LapSummary;
  onCursorDist: (dist: number | null) => void;
  /** Reported up so the view can let the playhead own the cursor over hover. */
  onPlayingChange?: (playing: boolean) => void;
}) {
  const endS = playbackEnd(series);
  const units = useSettings((s) => s.units);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [tUI, setTUI] = useState(0); // throttled mirror of tRef for the UI
  const tRef = useRef(0);
  const playingRef = useRef(false);
  playingRef.current = playing;
  const speedRef = useRef(speed);
  speedRef.current = speed;

  const reducedMotion = useMemo(
    () =>
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    [],
  );

  useEffect(() => onPlayingChange?.(playing), [playing, onPlayingChange]);

  // A different lap (or a refetch) is a different clock: rewind and stop.
  useEffect(() => {
    tRef.current = 0;
    setTUI(0);
    setPlaying(false);
  }, [series]);

  const pushCursor = useCallback(
    (tS: number) => onCursorDist(distAtTime(series, tS)),
    [series, onCursorDist],
  );

  // The clock. Wall-time deltas × the speed multiplier, so a dropped frame
  // never slows the lap down; stops (and stays) at the lap's end.
  useEffect(() => {
    if (!playing || endS <= 0) return;
    let raf = 0;
    let last = performance.now();
    let lastCommit = -Infinity;
    let lastCursor = -Infinity;
    const commitGap = 1000 / UI_UPDATE_HZ;
    const cursorGap = reducedMotion ? 1000 / REDUCED_MOTION_HZ : 0;
    const tick = (now: number) => {
      raf = requestAnimationFrame(tick);
      const { t, ended } = advancePlayhead(
        tRef.current, now - last, speedRef.current, endS,
      );
      last = now;
      tRef.current = t;
      if (now - lastCursor >= cursorGap) {
        lastCursor = now;
        pushCursor(t);
      }
      if (now - lastCommit >= commitGap || ended) {
        lastCommit = now;
        setTUI(t);
      }
      if (ended) setPlaying(false);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, endS, pushCursor, reducedMotion]);

  // A hidden tab gets no animation frames, so the lap would "jump" on return
  // — and playing to nobody is noise. Pause instead; the user resumes.
  useEffect(() => {
    const onVisibility = () => {
      if (document.hidden && playingRef.current) setPlaying(false);
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  const seek = (tS: number) => {
    tRef.current = tS;
    setTUI(tS);
    pushCursor(tS);
  };

  const toggle = () => {
    if (!playing && tRef.current >= endS) seek(0); // replay from the top
    setPlaying((p) => !p);
  };

  const frame = useMemo(() => frameAtTime(series, tUI, lap), [series, tUI, lap]);

  if (endS <= 0) return null;

  const thrH = Math.max(0.5, (frame.throttle / 100) * 36);
  const brkH = Math.max(0.5, (frame.brake / 100) * 36);
  // steer_rad is the wheel's absolute rotation as GT7 broadcasts it, so the
  // needle simply rotates by it — no lock-to-lock assumption (see
  // SteeringWidget). Null on packet A, where it sits at centre.
  const steerDeg = frame.steer_rad != null ? (frame.steer_rad * 180) / Math.PI : 0;
  const gearText = frame.gear === 0 ? "R" : frame.gear === 15 ? "N" : String(frame.gear);

  return (
    <div className="panel mb-3 flex flex-wrap items-center gap-3.5 px-3.5 py-2">
      <Tip content={playing ? "Pause" : "Play the reference lap (drives every cursor-synced panel)"}>
        <button
          onClick={toggle}
          aria-label={playing ? "Pause playback" : "Play lap"}
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-accent bg-accent/12 text-[11px] text-accent transition-colors hover:bg-accent/22"
        >
          {playing ? "❚❚" : "▶"}
        </button>
      </Tip>
      <input
        type="range"
        min={0}
        max={endS}
        step={endS / 2000}
        value={Math.min(tUI, endS)}
        onChange={(e) => seek(Number(e.target.value))}
        aria-label="Lap position"
        className="min-w-40 flex-1"
      />
      {/* Fixed width: the readout must not shift the controls beside it as
          the digits change. */}
      <span className="inline-block w-[118px] shrink-0 text-right font-tabular text-[11.5px] text-ink-muted">
        {formatLapTime(Math.round(tUI * 1000))}
        <span className="text-ink-faint"> / {formatLapTime(Math.round(endS * 1000))}</span>
      </span>
      <SegmentedControl
        ariaLabel="Playback speed"
        size="sm"
        value={String(speed)}
        onValueChange={(v) => setSpeed(Number(v))}
        options={PLAYBACK_SPEEDS.map((s) => ({ value: String(s), label: `${s}×` }))}
      />
      {/* The driver's hands at the playhead. */}
      <div className="hidden shrink-0 items-center gap-4 pl-1.5 sm:flex">
        <svg
          viewBox="0 0 34 34"
          className="h-8 w-8"
          role="img"
          aria-label={
            frame.steer_rad != null
              ? `Steering ${Math.round(steerDeg)} degrees`
              : "Steering unknown"
          }
        >
          <circle cx="17" cy="17" r="14" fill="none" stroke="var(--color-ink-ghost)" strokeWidth="2.5" />
          <line
            x1="17"
            y1="17"
            x2="17"
            y2="4"
            stroke="var(--color-accent)"
            strokeWidth="2.5"
            strokeLinecap="round"
            transform={`rotate(${steerDeg.toFixed(1)}, 17, 17)`}
          />
        </svg>
        <svg viewBox="0 0 26 40" className="h-[34px] w-[22px]" aria-hidden="true">
          <rect x="2" y="2" width="8" height="36" rx="2" fill="var(--color-panel-2)" />
          <rect x="2" y={38 - thrH} width="8" height={thrH} rx="2" fill="var(--color-throttle)" />
          <rect x="16" y="2" width="8" height="36" rx="2" fill="var(--color-panel-2)" />
          <rect x="16" y={38 - brkH} width="8" height={brkH} rx="2" fill="var(--color-brake)" />
        </svg>
        <div className="w-[30px] text-center">
          <div className="font-tabular text-[26px] font-semibold leading-none text-accent">
            {gearText}
          </div>
          <div className="text-[8px] uppercase tracking-[0.14em] text-ink-faint">gear</div>
        </div>
        <div className="w-[60px] text-center">
          <div className="font-tabular text-[26px] font-semibold leading-none">
            {Math.round(speedValue(frame.speed_kmh, units))}
          </div>
          <div className="text-[8px] uppercase tracking-[0.14em] text-ink-faint">
            {speedUnit(units)}
          </div>
        </div>
      </div>
    </div>
  );
}
