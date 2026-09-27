// Live/Race view: large readouts driven by useLiveFrame's bounded sampling
// of liveFrameRef, so 30 Hz telemetry re-renders at the capped UI rate (#32)
// instead of once per animation frame. The input trace samples liveFrameRef
// on its own animation loop and never re-renders at all.

import { memo, useEffect, useMemo, useRef, useState } from "react";
import { NoTelemetryPage } from "@/components/errors";
import { lastVsPrevBest, liveDelta } from "@/components/widgets/shared";
import { computeAlerts, THRESHOLDS, type DashAlert } from "@/lib/alerts";
import { FASTEST_COLOR } from "@/lib/colors";
import { countingLaps, lapConsistency } from "@/lib/consistency";
import { useEngineerStatus } from "@/lib/engineerStatus";
import {
  formatDelta,
  formatLapTime,
  formatTimeOfDay,
  speedUnit,
  speedValue,
} from "@/lib/format";
import { openInAnalysis } from "@/lib/router";
import { projectStrategy, type StrategyProjection } from "@/lib/strategy";
import {
  AIDS_ASM,
  AIDS_HANDBRAKE,
  AIDS_REV_LIMITER,
  AIDS_TCS,
  notCountingLabel,
  type LapSummary,
  type LiveFrame,
} from "@/lib/types";
import { STALE_AFTER_MS, useLiveFrame } from "@/lib/useLiveFrame";
import { clientId } from "@/store/engineer";
import { useSettings } from "@/store/settings";
import { liveFrameRef, useTelemetry } from "@/store/telemetry";

// No frame for this long and the view hands over to the no-telemetry page.
const SILENT_AFTER_MS = 5000;

export function LiveView() {
  const { frame } = useLiveFrame(false);
  const silent = useTelemetrySilent();

  if (!frame || silent) return <NoTelemetryPage />;
  return <Dashboard frame={frame} />;
}

// True once liveFrameRef has gone SILENT_AFTER_MS without a frame. Checked
// once a second: a stalled stream stops useLiveFrame's commits, so the view
// wouldn't otherwise re-render to notice.
function useTelemetrySilent(): boolean {
  const [silent, setSilent] = useState(false);
  useEffect(() => {
    const check = () =>
      setSilent(liveFrameRef.at > 0 && performance.now() - liveFrameRef.at > SILENT_AFTER_MS);
    check();
    const id = window.setInterval(check, 1000);
    return () => window.clearInterval(id);
  }, []);
  return silent;
}

function Dashboard({ frame }: { frame: LiveFrame }) {
  const units = useSettings((s) => s.units);
  const recentLaps = useTelemetry((s) => s.recentLaps);
  const sessionId = useTelemetry((s) => s.status?.session_id ?? null) ?? recentLaps[0]?.session_id;
  // The lap feed survives session boundaries (for the fuel projection); the
  // rail shows only this session's laps.
  const laps = useMemo(
    () => (sessionId != null ? recentLaps.filter((l) => l.session_id === sessionId) : []),
    [recentLaps, sessionId],
  );
  const bestLap = useMemo(() => {
    const counting = countingLaps(laps);
    return counting.length > 0
      ? counting.reduce((a, b) => (b.time_ms < a.time_ms ? b : a))
      : null;
  }, [laps]);

  const proj = projectStrategy(frame, recentLaps);
  const alerts = computeAlerts(frame, recentLaps);
  const finished = frame.total_laps > 0 && frame.current_lap > frame.total_laps;

  return (
    <div className="mx-auto flex max-w-[1440px] flex-col gap-3">
      {/* Context strip */}
      <div className="flex flex-wrap items-center gap-3.5">
        <div className="flex flex-wrap items-baseline gap-2.5 font-tabular">
          <span className="text-[15px] font-medium">{frame.car_name || "Unknown car"}</span>
          {frame.track_name && (
            <span className="whitespace-nowrap rounded-[9px] border border-accent/38 bg-accent/22 px-2.5 py-px text-[11px] font-medium text-accent-200">
              {frame.track_name}
            </span>
          )}
          <span className="text-[11px] text-ink-faint">
            {[
              sessionId != null && `session #${sessionId}`,
              finished
                ? "finished"
                : `lap ${frame.current_lap}${frame.total_laps > 0 ? `/${frame.total_laps}` : ""}`,
              frame.total_positions > 0 && `P${frame.position}/${frame.total_positions}`,
              frame.tod_ms >= 0 && `in-game ${formatTimeOfDay(frame.tod_ms)}`,
            ]
              .filter(Boolean)
              .join(" · ")}
            {frame.paused && <span className="text-warn"> · paused</span>}
            {!frame.on_track && !frame.paused && <span> · not on track</span>}
          </span>
        </div>
        {/* The two second-screen routes, reachable from the view you look at
            while driving. No layout switcher here on purpose: layouts live
            on /dash. */}
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <EngineerChip />
          <a className="btn no-underline" href="#/dash">
            Driver dash ↗
          </a>
        </div>
      </div>

      {alerts.length > 0 && <AlertStrip alerts={alerts} proj={proj} />}

      <div className="grid grid-cols-1 items-start gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(260px,320px)]">
        <div className="flex min-w-0 flex-col gap-3">
          <ShiftLights frame={frame} />

          {/* Hero: speed · gear · delta */}
          <div className="grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-3">
            <div className="panel flex flex-col items-center justify-center px-4 py-[26px]">
              <span className="font-tabular text-[clamp(56px,7vw,96px)] font-semibold leading-[0.95] tracking-[-0.02em]">
                {Math.round(speedValue(frame.speed_kmh, units))}
              </span>
              <span className="mt-2 text-[11px] uppercase tracking-[0.22em] text-ink-faint">
                {speedUnit(units)}
              </span>
            </div>
            <GearCard frame={frame} />
            <DeltaCard frame={frame} bestLap={bestLap} />
          </div>

          {/* Timing · inputs */}
          <div className="grid grid-cols-[repeat(auto-fit,minmax(240px,1fr))] gap-3">
            <TimingCard frame={frame} />
            <div className="panel flex flex-col gap-2 px-4 py-3.5">
              <div className="flex items-baseline gap-3">
                <Label>Inputs · last 8 s</Label>
                <span className="ml-auto font-tabular text-[10.5px] text-ink-faint">
                  throttle <span className="text-throttle">{Math.round(frame.throttle)}%</span>
                  {" · "}brake <span className="text-brake">{Math.round(frame.brake)}%</span>
                  {frame.boost > -0.9 && ` · boost ${frame.boost.toFixed(2)} bar`}
                </span>
              </div>
              <InputTrace />
            </div>
          </div>

          {/* Car: fuel & strategy · tires · engine */}
          <div className="grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-3">
            <FuelCard frame={frame} proj={proj} />
            <TiresCard frame={frame} />
            <CarCard frame={frame} />
          </div>
        </div>

        <LapsRail frame={frame} laps={laps} bestLap={bestLap} sessionId={sessionId} />
      </div>
    </div>
  );
}

// Race Engineer link-chip: whether the server's callouts are going anywhere,
// read from its own status broadcast (Live never speaks itself).
const EngineerChip = memo(function EngineerChip() {
  const status = useEngineerStatus((s) => s.status);
  const [me] = useState(clientId);

  let dot = "bg-ink-faint";
  let state = "status unknown";
  if (status && !status.enabled) {
    dot = "bg-ink-ghost";
    state = "callouts off";
  } else if (status?.active_client_id) {
    const speaker = status.clients.find((c) => c.client_id === status.active_client_id);
    const where =
      status.active_client_id === me
        ? "this browser"
        : speaker?.page === "dash"
          ? "a Driver dash"
          : speaker?.page === "engineer"
            ? "an Engineer page"
            : "another device";
    dot = "bg-throttle animate-pulse-dot";
    state = `speaking on ${where} · ${status.verbosity}`;
  } else if (status) {
    state = "voice off";
  }

  return (
    <a
      href="#/engineer"
      className="panel flex items-center gap-2 px-2.5 py-1 text-ink no-underline transition-shadow hover:shadow-[0_0_0_1px_var(--color-edge-bright)]"
    >
      <span className={`h-1.5 w-1.5 rounded-full ${dot}`} />
      <span className="text-[11.5px]">Race Engineer</span>
      <span className="text-[10.5px] text-ink-faint">{state}</span>
      <span className="text-[10.5px] text-accent">open ↗</span>
    </a>
  );
});

const ALERT_KICKER: Record<string, string> = {
  fuel: "Fuel",
  pit: "Pit window",
  water: "Water temp",
  "oil-temp": "Oil temp",
  "oil-pressure": "Oil pressure",
  tires: "Tires",
};

// computeAlerts speaks in dash-banner shorthand; here there is room for the
// sentence behind the fuel and pit alerts.
function alertText(alert: DashAlert, proj: StrategyProjection | null): string {
  if (proj && (alert.id === "fuel" || alert.id === "pit")) {
    return `Fuel for ${proj.lapsToEmpty.toFixed(1)} laps — box before lap ${proj.pitBeforeLap} at ${proj.avgFuelPerLap.toFixed(2)} L/lap.`;
  }
  return alert.message;
}

function AlertStrip({ alerts, proj }: { alerts: DashAlert[]; proj: StrategyProjection | null }) {
  // Sorted by severity, so the first alert sets the ring.
  const critical = alerts[0].severity === "critical";
  // Fuel and pit describe the same projection; say it once.
  const shown = alerts.filter((a) => !(a.id === "pit" && alerts.some((b) => b.id === "fuel")));
  return (
    <div
      className={`panel flex flex-wrap items-center gap-x-5 gap-y-1.5 px-3.5 py-2 ${
        critical
          ? "shadow-[0_0_0_1px_color-mix(in_srgb,var(--color-brake)_55%,transparent)]"
          : "shadow-[0_0_0_1px_color-mix(in_srgb,var(--color-warn)_45%,transparent)]"
      }`}
    >
      {shown.map((a) => (
        <div key={a.id} className="flex items-center gap-2.5">
          <span
            className={`text-[10px] font-semibold uppercase tracking-[0.14em] ${
              a.severity === "critical" ? "text-brake" : "text-warn"
            }`}
          >
            {ALERT_KICKER[a.id] ?? a.id}
          </span>
          <span className="font-tabular text-[12.5px]">{alertText(a, proj)}</span>
        </div>
      ))}
    </div>
  );
}

// 24-segment shift-light strip: green, then warn, then brake, filling from
// 45% of the shift point up to it. At the shift point everything lit turns
// brake; on the limiter the strip flashes.
const LEDS = 24;
const LED_START = 0.45;

function ShiftLights({ frame }: { frame: LiveFrame }) {
  const shift = Math.max(1, frame.rpm_alert);
  const onLimiter = ((frame.aids ?? 0) & AIDS_REV_LIMITER) !== 0;
  const flash = frame.rpm >= shift || onLimiter;
  return (
    <div className="panel px-4 py-3">
      <div className={`grid h-3.5 grid-cols-[repeat(24,1fr)] gap-[3px] ${onLimiter ? "animate-pulse" : ""}`}>
        {Array.from({ length: LEDS }, (_, i) => {
          const at = shift * (LED_START + ((1 - LED_START) * i) / (LEDS - 1));
          const lit = frame.rpm >= at;
          const zone = i < 14 ? "bg-throttle" : i < 20 ? "bg-warn" : "bg-brake";
          return (
            <span
              key={i}
              className={`rounded-[2px] ${lit ? (flash ? "bg-brake" : zone) : "bg-panel-2"} ${
                lit && i >= 20 ? "shadow-[0_0_6px_var(--color-brake)]" : ""
              }`}
            />
          );
        })}
      </div>
      <div className="mt-1.5 flex justify-between font-tabular text-[10.5px] text-ink-faint">
        <span>
          <span className="text-ink-soft">{frame.rpm.toLocaleString()}</span> rpm
        </span>
        <span>
          {onLimiter && <span className="text-brake">limiter · </span>}
          shift {frame.rpm_alert.toLocaleString()}
        </span>
      </div>
    </div>
  );
}

function GearCard({ frame }: { frame: LiveFrame }) {
  const suggest =
    frame.suggested_gear !== 15 && frame.suggested_gear !== frame.gear ? frame.suggested_gear : null;
  const braking = frame.brake > 5;
  return (
    <div className="panel flex flex-col items-center justify-center px-4 py-[26px]">
      <span className="font-tabular text-[clamp(56px,7vw,96px)] font-semibold leading-[0.95] text-accent">
        {frame.gear === 0 ? "R" : frame.gear === 15 ? "N" : frame.gear}
      </span>
      <span
        className={`mt-2 text-[11px] uppercase tracking-[0.22em] ${
          suggest != null && braking ? "text-warn" : "text-ink-faint"
        }`}
      >
        gear{suggest != null ? ` → ${suggest}` : ""}
      </span>
    </div>
  );
}

// Green at or under the best, brake over it; the bar spans ±1.0 s.
function deltaColor(ms: number | null): string {
  return ms == null ? "text-ink-ghost" : ms <= 0 ? "text-throttle" : "text-brake";
}

function DeltaCard({ frame, bestLap }: { frame: LiveFrame; bestLap: LapSummary | null }) {
  const delta = liveDelta(frame);
  const ms = delta?.ms ?? null;
  const frac = ms == null ? 0 : Math.min(1000, Math.abs(ms)) / 2000;
  const refMs = frame.session_best_ms > 0 ? frame.session_best_ms : bestLap?.time_ms;
  const refLabel =
    bestLap && bestLap.time_ms === refMs
      ? `L${bestLap.number} ${formatLapTime(refMs)}`
      : refMs
        ? formatLapTime(refMs)
        : "no best yet";
  return (
    <div className="panel flex flex-col justify-center gap-3 px-5 py-[22px]">
      <div className="flex items-baseline justify-between gap-2">
        <Label>{delta && !delta.live ? "Δ best · last lap" : "Δ to best"}</Label>
        <span className="whitespace-nowrap font-tabular text-[10.5px] text-ink-faint">
          {refLabel}
        </span>
      </div>
      <span
        className={`whitespace-nowrap font-tabular text-[clamp(34px,4.6vw,64px)] font-semibold leading-none ${deltaColor(ms)}`}
      >
        {ms == null ? "—" : formatDelta(ms)}
      </span>
      <div className="relative h-2 overflow-hidden rounded bg-panel-2">
        <span className="absolute inset-y-0 left-1/2 w-px bg-edge-bright" />
        {ms != null && (
          <span
            className={`absolute inset-y-0 rounded ${ms <= 0 ? "bg-throttle" : "bg-brake"}`}
            style={{
              left: `${ms <= 0 ? 50 - frac * 100 : 50}%`,
              width: `${frac * 100}%`,
            }}
          />
        )}
      </div>
      <div className="flex justify-between font-tabular text-[10px] text-ink-ghost">
        <span>−1.0 s</span>
        <span>0</span>
        <span>+1.0 s</span>
      </div>
    </div>
  );
}

// Δ-to-best colouring for a completed lap: purple on (or under) the best,
// green within 0.3 s, neutral otherwise — never red for merely slower.
function lapDeltaClass(ms: number): string {
  return ms <= 0 ? "" : ms <= 300 ? "text-throttle" : "text-ink-dim";
}

function TimingCard({ frame }: { frame: LiveFrame }) {
  const lastDelta = lastVsPrevBest(frame);
  const predicted =
    frame.delta_ms != null && frame.session_best_ms > 0
      ? frame.session_best_ms + frame.delta_ms
      : null;
  return (
    <div className="panel flex flex-col gap-2.5 px-4 py-3.5">
      <Label>Current lap</Label>
      <span className="font-tabular text-4xl font-semibold leading-none">
        {formatLapTime(frame.lap_elapsed_ms)}
      </span>
      <div className="flex flex-col gap-[3px] font-tabular text-[11.5px]">
        <Row k="Last">
          {formatLapTime(frame.last_lap_ms)}
          {lastDelta != null && (
            <span
              className={`ml-1.5 ${lapDeltaClass(lastDelta)}`}
              style={lastDelta <= 0 ? { color: FASTEST_COLOR } : undefined}
            >
              {formatDelta(lastDelta)}
            </span>
          )}
        </Row>
        <Row k="Best">
          <span style={{ color: FASTEST_COLOR }}>{formatLapTime(frame.best_lap_ms)}</span>
        </Row>
        <Row k="Predicted">
          <span className={predicted == null ? "text-ink-ghost" : deltaColor(frame.delta_ms)}>
            {formatLapTime(predicted)}
          </span>
        </Row>
      </div>
    </div>
  );
}

// Rolling throttle/brake trace. Samples liveFrameRef on its own animation
// loop at TRACE_HZ and writes the polylines straight to the DOM, so the
// trace costs no React renders; a stalled stream simply freezes it.
const TRACE_SAMPLES = 80;
const TRACE_HZ = 10; // 80 samples = the last 8 s
const TRACE_W = 600;
const TRACE_H = 70;

function tracePoints(values: number[]): string {
  const offset = TRACE_SAMPLES - values.length;
  return values
    .map((v, i) => {
      const x = (TRACE_W * (i + offset)) / (TRACE_SAMPLES - 1);
      const y = TRACE_H - 4 - (Math.min(100, Math.max(0, v)) / 100) * (TRACE_H - 8);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

const InputTrace = memo(function InputTrace() {
  const throttleRef = useRef<SVGPolylineElement>(null);
  const brakeRef = useRef<SVGPolylineElement>(null);

  useEffect(() => {
    const throttle: number[] = [];
    const brake: number[] = [];
    let raf = 0;
    let last = -Infinity;
    const tick = () => {
      raf = requestAnimationFrame(tick);
      const now = performance.now();
      if (now - last < 1000 / TRACE_HZ) return;
      last = now;
      const f = liveFrameRef.current;
      if (!f || now - liveFrameRef.at > STALE_AFTER_MS) return;
      throttle.push(f.throttle);
      brake.push(f.brake);
      if (throttle.length > TRACE_SAMPLES) {
        throttle.shift();
        brake.shift();
      }
      throttleRef.current?.setAttribute("points", tracePoints(throttle));
      brakeRef.current?.setAttribute("points", tracePoints(brake));
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <svg
      viewBox={`0 0 ${TRACE_W} ${TRACE_H}`}
      preserveAspectRatio="none"
      className="block h-[70px] w-full"
      aria-hidden
    >
      <line
        x1={0}
        x2={TRACE_W}
        y1={TRACE_H / 2}
        y2={TRACE_H / 2}
        stroke="var(--color-hairline)"
        strokeWidth={1}
        vectorEffect="non-scaling-stroke"
      />
      <polyline
        ref={throttleRef}
        fill="none"
        stroke="var(--color-throttle)"
        strokeWidth={1.8}
        vectorEffect="non-scaling-stroke"
      />
      <polyline
        ref={brakeRef}
        fill="none"
        stroke="var(--color-brake)"
        strokeWidth={1.8}
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
});

function FuelCard({ frame, proj }: { frame: LiveFrame; proj: StrategyProjection | null }) {
  const fuelPct = (frame.fuel_level / Math.max(1, frame.fuel_capacity)) * 100;
  const low = proj != null ? proj.lapsToEmpty < THRESHOLDS.fuelLapsWarn : fuelPct < 15;
  return (
    <div className="panel px-4 py-3.5">
      <div className="flex items-baseline justify-between gap-2">
        <Label>Fuel &amp; strategy</Label>
        <span className="font-tabular text-[10.5px] text-ink-faint">
          {frame.fuel_level.toFixed(1)} / {frame.fuel_capacity.toFixed(0)} L
        </span>
      </div>
      <div className="mt-1 flex items-baseline gap-2.5">
        <span className="font-tabular text-[26px] font-semibold">
          {proj != null ? proj.lapsToEmpty.toFixed(1) : `${fuelPct.toFixed(0)}%`}
        </span>
        <span className="text-[11px] text-ink-dim">{proj != null ? "laps of fuel" : "fuel"}</span>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-[3px] bg-panel-2">
        <div
          className={`h-full rounded-[3px] ${low ? "bg-brake" : "bg-warn"}`}
          style={{ width: `${Math.min(100, fuelPct)}%` }}
        />
      </div>
      {proj == null ? (
        <div className="mt-2 text-[11px] text-ink-faint">
          Complete a lap that burns fuel to project the strategy.
        </div>
      ) : (
        <div className="mt-2 flex flex-col gap-[3px] font-tabular text-[11px]">
          <Row k="Pit before lap">{proj.pitBeforeLap}</Row>
          <Row k="Avg / lap">{proj.avgFuelPerLap.toFixed(2)} L</Row>
          {frame.total_laps > 0 &&
            (() => {
              const needed = (frame.total_laps - frame.current_lap + 1) * proj.avgFuelPerLap;
              const enough = needed <= frame.fuel_level;
              return (
                <Row k="To finish">
                  <span className={enough ? "text-throttle" : "text-brake"}>
                    {enough ? "fuel OK" : `${(needed - frame.fuel_level).toFixed(1)} L short`}
                  </span>
                </Row>
              );
            })()}
        </div>
      )}
    </div>
  );
}

function TiresCard({ frame }: { frame: LiveFrame }) {
  const [fl, fr, rl, rr] = frame.tire_temps;
  const balance = (fl + fr) / 2 - (rl + rr) / 2;
  return (
    <div className="panel px-4 py-3.5">
      <div className="flex items-baseline justify-between gap-2">
        <Label>Tires °C</Label>
        <span className="font-tabular text-[10.5px] text-ink-faint">
          F/R {balance >= 0 ? "+" : "−"}
          {Math.abs(balance).toFixed(1)} °C
        </span>
      </div>
      <div className="mt-2 grid grid-cols-2 gap-[5px]">
        {(["FL", "FR", "RL", "RR"] as const).map((pos, i) => (
          <TireTemp key={pos} label={pos} temp={frame.tire_temps[i]} />
        ))}
      </div>
      {frame.tire_slip > 1.1 && (
        <div className="mt-1.5 text-[10.5px] font-semibold text-warn">TIRE SPIN</div>
      )}
    </div>
  );
}

function CarCard({ frame }: { frame: LiveFrame }) {
  const aids = frame.aids ?? 0;
  const water =
    frame.water_temp > THRESHOLDS.waterCritical
      ? "text-brake"
      : frame.water_temp > THRESHOLDS.waterWarn
        ? "text-warn"
        : "";
  const oil =
    frame.oil_temp > THRESHOLDS.oilTempCritical
      ? "text-brake"
      : frame.oil_temp > THRESHOLDS.oilTempWarn
        ? "text-warn"
        : "";
  return (
    <div className="panel px-4 py-3.5">
      <Label>Car</Label>
      <div className="mt-2 flex flex-col gap-1 font-tabular text-[11.5px]">
        <Row k="Water">
          <span className={water}>{frame.water_temp.toFixed(0)} °C</span>
        </Row>
        <Row k="Oil">
          <span className={oil}>{frame.oil_temp.toFixed(0)} °C</span>
          {frame.oil_pressure >= 0 && ` · ${frame.oil_pressure.toFixed(1)} bar`}
        </Row>
      </div>
      {/* Driver-aid pills light while the aid is intervening */}
      <div className="mt-2.5 flex gap-1">
        {(
          [
            ["TCS", AIDS_TCS],
            ["ASM", AIDS_ASM],
            ["HB", AIDS_HANDBRAKE],
          ] as const
        ).map(([label, bit]) => (
          <span
            key={label}
            className={`rounded-[3px] px-1.5 py-px text-[8.5px] font-bold ${
              aids & bit ? "bg-warn text-surface" : "border border-edge text-ink-ghost"
            }`}
          >
            {label}
          </span>
        ))}
      </div>
    </div>
  );
}

// Laps rail: the lap in progress pinned on top, then this session's
// completed laps newest first, each a link into Analysis.
function LapsRail({
  frame,
  laps,
  bestLap,
  sessionId,
}: {
  frame: LiveFrame;
  laps: LapSummary[];
  bestLap: LapSummary | null;
  sessionId: number | undefined;
}) {
  const spread = useMemo(() => lapConsistency(laps), [laps]);
  return (
    <aside className="panel flex flex-col">
      <div className="flex items-baseline gap-2 px-4 pb-2 pt-3">
        <Label>Laps</Label>
        <span className="font-tabular text-[10.5px] text-ink-faint">
          {laps.length} done
          {spread && ` · σ ± ${(spread.stdMs / 1000).toFixed(2)} s`}
        </span>
        {sessionId != null && (
          <button
            onClick={() => openInAnalysis({ session: sessionId })}
            className="ml-auto text-[10.5px] text-accent hover:underline"
          >
            Analyze session
          </button>
        )}
      </div>
      <div className="grid grid-cols-[44px_1fr_64px] gap-2 px-4 pb-1.5 font-tabular text-[10px] text-ink-ghost">
        <span>Lap</span>
        <span>Time</span>
        <span className="text-right">Δ best</span>
      </div>
      <div className="flex max-h-[640px] flex-col gap-[3px] overflow-y-auto px-3 pb-3">
        {frame.on_track && (
          <div className="grid grid-cols-[44px_1fr_64px] items-center gap-2 rounded-[5px] px-2 py-1.5 font-tabular text-xs shadow-[inset_0_0_0_1px_color-mix(in_srgb,var(--color-accent)_40%,transparent)]">
            <span className="flex items-center gap-1.5 text-accent-300">
              <span className="h-1.5 w-1.5 animate-pulse-dot rounded-full bg-accent" />
              {`L${frame.current_lap}`}
            </span>
            <span>{formatLapTime(frame.lap_elapsed_ms)}</span>
            <span className={`text-right ${deltaColor(frame.delta_ms)}`}>
              {frame.delta_ms == null ? "—" : formatDelta(frame.delta_ms)}
            </span>
          </div>
        )}
        <CompletedLaps laps={laps} bestLap={bestLap} />
        {laps.length === 0 && (
          <div className="px-2 py-1.5 text-[11.5px] text-ink-faint">
            Completed laps appear here.
          </div>
        )}
      </div>
    </aside>
  );
}

// Memoised on the lap list, which only changes on lap events — not on every
// telemetry commit the rest of the view re-renders for.
const CompletedLaps = memo(function CompletedLaps({
  laps,
  bestLap,
}: {
  laps: LapSummary[];
  bestLap: LapSummary | null;
}) {
  return (
    <>
      {laps.map((lap) => (
        <LapRow key={lap.id} lap={lap} bestLap={bestLap} />
      ))}
    </>
  );
});

function LapRow({ lap, bestLap }: { lap: LapSummary; bestLap: LapSummary | null }) {
  const isBest = lap.id === bestLap?.id;
  const excluded = notCountingLabel(lap);
  const d = bestLap ? lap.time_ms - bestLap.time_ms : null;
  return (
    <button
      onClick={() => openInAnalysis({ session: lap.session_id, laps: [lap.id] })}
      title={`Open L${lap.number} in Analysis${excluded ? ` · ${excluded}` : ""}`}
      className="grid grid-cols-[44px_1fr_64px] items-center gap-2 rounded-[5px] bg-panel-2 px-2 py-1.5 text-left font-tabular text-xs transition-colors hover:bg-edge"
    >
      <span className="text-ink-faint">L{lap.number}</span>
      <span style={isBest ? { color: FASTEST_COLOR } : undefined}>
        {formatLapTime(lap.time_ms)}
        {isBest && (
          <span
            className="ml-1.5 rounded-[7px] border px-[5px] text-[9.5px]"
            style={{ borderColor: `color-mix(in srgb, ${FASTEST_COLOR} 50%, transparent)` }}
          >
            PB
          </span>
        )}
      </span>
      {/* Laps that don't count have no delta worth reading. */}
      {excluded || d == null ? (
        <span className="text-right text-ink-ghost">—</span>
      ) : isBest ? (
        <span className="text-right" style={{ color: FASTEST_COLOR }}>
          best
        </span>
      ) : (
        <span className={`text-right ${lapDeltaClass(d)}`}>{formatDelta(d)}</span>
      )}
    </button>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <div className="whitespace-nowrap text-[9px] font-semibold uppercase tracking-[0.16em] text-ink-faint">
      {children}
    </div>
  );
}

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-2">
      <span className="text-ink-faint">{k}</span>
      <span>{children}</span>
    </div>
  );
}

function TireTemp({ label, temp }: { label: string; temp: number }) {
  // Blue (cold) -> green (optimal ~70-90) -> red (hot). The temp colour is a
  // wash behind the number rather than the number's own colour, so the
  // reading stays legible at every temperature.
  const color =
    temp < 55 ? "bg-coast/20" : temp < 95 ? "bg-throttle/20" : "bg-brake/20";
  return (
    <div className={`rounded-[5px] px-1 py-[7px] text-center font-tabular text-sm ${color}`}>
      <span className="mr-1 text-[8.5px] text-ink-faint">{label}</span>
      {temp.toFixed(0)}
    </div>
  );
}
