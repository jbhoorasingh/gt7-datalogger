// Live/Race view: large readouts driven by useLiveFrame's bounded sampling
// of liveFrameRef, so 30 Hz telemetry re-renders at the capped UI rate (#32)
// instead of once per animation frame.

import {
  formatDelta,
  formatDuration,
  formatLapTime,
  formatTimeOfDay,
  speedUnit,
  speedValue,
} from "@/lib/format";
import { liveDelta } from "@/components/widgets/shared";
import { lapColor } from "@/lib/colors";
import { openInAnalysis } from "@/lib/router";
import { projectStrategy } from "@/lib/strategy";
import {
  AIDS_ASM,
  AIDS_HANDBRAKE,
  AIDS_REV_LIMITER,
  AIDS_TCS,
  type LapSummary,
  type LiveFrame,
} from "@/lib/types";
import { useLiveFrame } from "@/lib/useLiveFrame";
import { useSettings } from "@/store/settings";
import { useTelemetry } from "@/store/telemetry";

export function LiveView() {
  const { frame } = useLiveFrame(false);

  if (!frame) {
    return (
      <div className="flex h-full items-center justify-center text-ink-dim">
        <div className="text-center">
          <div className="mb-2 text-2xl">Waiting for telemetry…</div>
          <div className="text-sm">
            Start driving in GT7, or run the server with <code>GT7_SOURCE=sim</code> to demo.
          </div>
        </div>
      </div>
    );
  }
  return <Dashboard frame={frame} />;
}

function Dashboard({ frame }: { frame: LiveFrame }) {
  const units = useSettings((s) => s.units);
  const recentLaps = useTelemetry((s) => s.recentLaps);
  const speed = Math.round(speedValue(frame.speed_kmh, units));
  const rpmPct = Math.min(100, (frame.rpm / Math.max(1, frame.rpm_alert)) * 100);
  const aids = frame.aids ?? 0;
  const onLimiter = (aids & AIDS_REV_LIMITER) !== 0;
  const nearLimit = onLimiter || frame.rpm >= frame.rpm_alert * 0.95;
  const fuelPct = (frame.fuel_level / Math.max(1, frame.fuel_capacity)) * 100;
  // Live gap to the session-best lap; before a reference exists this is the
  // latest lap vs the best BEFORE it, so a new personal best shows its
  // improvement instead of +0.000.
  const delta = liveDelta(frame);
  const finished = frame.total_laps > 0 && frame.current_lap > frame.total_laps;

  return (
    <div className="mx-auto grid max-w-[1440px] grid-cols-1 items-start gap-3 lg:grid-cols-[1fr_320px]">
      <div className="flex flex-col gap-3">
        {/* RPM bar */}
        <div className="panel px-4 py-3">
          <div className="h-2.5 overflow-hidden rounded-[5px] bg-panel-2">
            <div
              className={`h-full rounded-[5px] transition-[width] duration-75 ${
                nearLimit
                  ? "bg-brake"
                  : "bg-[linear-gradient(90deg,var(--color-accent),var(--color-accent-400))]"
              } ${onLimiter ? "animate-pulse" : ""}`}
              style={{ width: `${rpmPct}%` }}
            />
          </div>
          <div className="mt-1.5 flex justify-between font-tabular text-[10.5px] text-ink-faint">
            <span>{frame.rpm.toLocaleString()} rpm</span>
            <span>limit {frame.rpm_alert.toLocaleString()}</span>
          </div>
        </div>

        {/* Speed · gear · inputs */}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-[2fr_1fr_1fr]">
          <div className="panel flex flex-col items-center justify-center px-4 py-8">
            <div className="font-tabular text-[96px] font-semibold leading-[0.95] tracking-[-0.02em]">
              {speed}
            </div>
            <div className="mt-2 text-[11px] uppercase tracking-[0.22em] text-ink-faint">
              {speedUnit(units)}
            </div>
          </div>
          <div className="panel flex flex-col items-center justify-center px-4 py-8">
            <div className="font-tabular text-[96px] font-semibold leading-[0.95] text-accent">
              {frame.gear === 0 ? "R" : frame.gear === 15 ? "N" : frame.gear}
            </div>
            <div className="mt-2 text-[11px] uppercase tracking-[0.22em] text-ink-faint">
              gear{frame.suggested_gear !== 15 ? ` → ${frame.suggested_gear}` : ""}
            </div>
          </div>
          <div className="panel flex flex-col justify-center gap-3.5 px-4 py-5">
            <InputBar label="Throttle" value={frame.throttle} color="bg-throttle" />
            <InputBar label="Brake" value={frame.brake} color="bg-brake" />
            {frame.boost > -0.9 && (
              <div className="flex justify-between text-[10.5px] text-ink-faint">
                <span>Boost</span>
                <span className="font-tabular">{frame.boost.toFixed(2)} bar</span>
              </div>
            )}
          </div>
        </div>

        {/* Lap · fuel · tires · race */}
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <div className="panel px-4 py-3.5">
            <Label>Lap</Label>
            <div className="font-tabular text-[26px] font-semibold">
              {finished ? (
                <span className="text-throttle">FIN</span>
              ) : (
                <>
                  {frame.current_lap}
                  {frame.total_laps > 0 && (
                    <span className="text-sm text-ink-faint">/{frame.total_laps}</span>
                  )}
                </>
              )}
            </div>
            <div className="mt-2 flex flex-col gap-[3px] font-tabular text-[11.5px]">
              <Row k="Last" v={formatLapTime(frame.last_lap_ms)} />
              <Row k="Best" v={formatLapTime(frame.best_lap_ms)} accent />
              {delta !== null && (
                <Row
                  k={delta.live ? "Δ best" : "Δ best (last lap)"}
                  v={formatDelta(delta.ms)}
                  className={delta.ms <= 0 ? "text-throttle" : "text-brake"}
                />
              )}
            </div>
          </div>

          <div className="panel px-4 py-3.5">
            <Label>Fuel</Label>
            <div className="font-tabular text-[26px] font-semibold">{fuelPct.toFixed(1)}%</div>
            <div className="mt-2 h-1.5 overflow-hidden rounded-[3px] bg-panel-2">
              <div
                className={`h-full rounded-[3px] ${fuelPct < 15 ? "bg-brake" : "bg-warn"}`}
                style={{ width: `${Math.min(100, fuelPct)}%` }}
              />
            </div>
            <div className="mt-2 font-tabular text-[10.5px] text-ink-faint">
              {frame.fuel_level.toFixed(1)} / {frame.fuel_capacity.toFixed(0)} L
            </div>
          </div>

          <div className="panel px-4 py-3.5">
            <Label>Tires °C</Label>
            <div className="mt-2 grid grid-cols-2 gap-[5px]">
              {(["FL", "FR", "RL", "RR"] as const).map((pos, i) => (
                <TireTemp key={pos} label={pos} temp={frame.tire_temps[i]} />
              ))}
            </div>
            {frame.tire_slip > 1.1 && (
              <div className="mt-2 text-[10.5px] font-semibold text-warn">TIRE SPIN</div>
            )}
          </div>

          <div className="panel px-4 py-3.5">
            <Label>Race</Label>
            <div className="font-tabular text-[26px] font-semibold">
              P{frame.position}
              <span className="text-sm text-ink-faint">/{frame.total_positions}</span>
            </div>
            <div className="mt-2 flex flex-col gap-[3px] font-tabular text-[10.5px] text-ink-faint">
              <Row
                k="Water"
                v={`${frame.water_temp.toFixed(0)}°C`}
                className={
                  frame.water_temp >= 110
                    ? "text-brake"
                    : frame.water_temp >= 100
                      ? "text-warn"
                      : ""
                }
              />
              <Row
                k="Oil"
                v={`${frame.oil_temp.toFixed(0)}°C`}
                className={
                  frame.oil_temp >= 130 ? "text-brake" : frame.oil_temp >= 115 ? "text-warn" : ""
                }
              />
            </div>
            {/* Driver-aid pills light while the aid is intervening */}
            <div className="mt-2 flex gap-1">
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
                    aids & bit
                      ? "bg-warn text-surface"
                      : "border border-edge text-ink-ghost"
                  }`}
                >
                  {label}
                </span>
              ))}
            </div>
          </div>
        </div>

        <div className="text-center text-[11px] text-ink-faint">
          {frame.car_name}
          {frame.track_name && ` · ${frame.track_name}`}
          {frame.paused && <span className="ml-2 text-warn">· PAUSED</span>}
          {!frame.on_track && <span className="ml-2">· not on track</span>}
        </div>
      </div>

      {/* Strategy + recent laps. Below lg this stacks under the main panels
          instead of disappearing (#29): a tablet is the obvious second-screen
          device, and this rail holds the fuel strategy plus the only in-app
          route from Live into Analysis. */}
      <div className="flex flex-col gap-3">
        <StrategyPanel frame={frame} laps={recentLaps} />
        <div className="panel flex flex-col px-4 py-3.5">
          <Label>Recent laps</Label>
          <div className="mt-2 flex max-h-[420px] flex-col gap-1 overflow-y-auto font-tabular text-xs">
            {recentLaps.length === 0 && (
              <div className="text-[11.5px] text-ink-faint">Completed laps appear here.</div>
            )}
            {recentLaps.map((lap) => (
              <button
                key={lap.id}
                onClick={() => openInAnalysis({ session: lap.session_id, laps: [lap.id] })}
                title="Open this lap in Analysis"
                className="flex w-full items-center gap-2 rounded-[5px] bg-panel-2 px-2.5 py-1.5 text-left transition-colors hover:bg-edge"
              >
                <span
                  className="h-[7px] w-[7px] shrink-0 rounded-full"
                  style={{ backgroundColor: lapColor(lap.id) }}
                />
                <span className="text-ink-faint">L{lap.number}</span>
                <span>{formatLapTime(lap.time_ms)}</span>
                <span className="ml-auto text-[10.5px] text-ink-faint">
                  {lap.fuel_consumed.toFixed(1)} L
                </span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

// Rolling fuel strategy from the last few laps: laps/time to empty, pit lap.
function StrategyPanel({ frame, laps }: { frame: LiveFrame; laps: LapSummary[] }) {
  const proj = projectStrategy(frame, laps);

  return (
    <div className="panel px-4 py-3.5">
      <Label>Race strategy</Label>
      {proj == null ? (
        <div className="mt-2 text-[11.5px] text-ink-faint">
          Complete a lap with fuel consumption to project fuel strategy.
        </div>
      ) : (
        <div className="mt-2 flex flex-col gap-1 font-tabular text-[11.5px]">
          <Row k="Fuel to empty" v={`${proj.lapsToEmpty.toFixed(1)} laps`}
            className={proj.lapsToEmpty < 2 ? "text-brake" : proj.lapsToEmpty < 4 ? "text-warn" : ""} />
          <Row k="Time to empty" v={formatDuration(proj.lapsToEmpty * proj.avgLapMs)} />
          <Row k="Pit before lap" v={String(proj.pitBeforeLap)} />
          <Row k="Avg fuel / lap" v={`${proj.avgFuelPerLap.toFixed(2)} L`} />
          {frame.total_laps > 0 &&
            (() => {
              const needed = (frame.total_laps - frame.current_lap + 1) * proj.avgFuelPerLap;
              const enough = needed <= frame.fuel_level;
              return (
                <Row
                  k="To finish"
                  v={enough ? "fuel OK" : `${(needed - frame.fuel_level).toFixed(1)} L short`}
                  className={enough ? "text-throttle" : "text-brake"}
                />
              );
            })()}
        </div>
      )}
      <div className="mt-2.5 border-t border-divider pt-2 text-[10.5px] text-ink-faint">
        In-game time <span className="text-ink-soft">{formatTimeOfDay(frame.tod_ms)}</span>
        {frame.track_name && (
          <>
            {" · "}
            <span className="text-ink-soft">{frame.track_name}</span>
          </>
        )}
      </div>
      {/* The two chrome-less second-screen routes, reachable from the one
          view you are looking at while driving. */}
      <div className="mt-1.5 text-[10.5px] text-ink-faint">
        second screen:{" "}
        <a className="text-accent underline underline-offset-[3px]" href="#/dash">
          /dash
        </a>
        {" · "}
        <a className="text-accent underline underline-offset-[3px]" href="#/engineer">
          /engineer
        </a>
      </div>
    </div>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[9px] font-semibold uppercase tracking-[0.16em] text-ink-faint">
      {children}
    </div>
  );
}

function Row({
  k,
  v,
  accent,
  className = "",
}: {
  k: string;
  v: string;
  accent?: boolean;
  className?: string;
}) {
  return (
    <div className="flex justify-between">
      <span className="text-ink-faint">{k}</span>
      <span className={accent ? "text-accent" : className}>{v}</span>
    </div>
  );
}

function InputBar({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div>
      <div className="mb-1 flex justify-between text-[10.5px] text-ink-faint">
        <span>{label}</span>
        <span className="font-tabular">{Math.round(value)}%</span>
      </div>
      <div className="h-2 overflow-hidden rounded bg-panel-2">
        <div className={`h-full rounded ${color}`} style={{ width: `${value}%` }} />
      </div>
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
    <div
      className={`rounded-[5px] px-1 py-1.5 text-center font-tabular text-[12.5px] ${color}`}
    >
      <span className="mr-1 text-[8.5px] text-ink-faint">{label}</span>
      {temp.toFixed(0)}
    </div>
  );
}
