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
    <div className="grid min-h-full grid-cols-1 gap-3 p-3 lg:h-full lg:grid-cols-[1fr_320px]">
      <div className="flex flex-col gap-3">
        {/* RPM bar */}
        <div className="rounded-xl bg-panel p-3">
          <div className="h-4 overflow-hidden rounded-full bg-panel-2">
            <div
              className={`h-full rounded-full transition-[width] duration-75 ${
                nearLimit ? "bg-brake" : "bg-accent"
              } ${onLimiter ? "animate-pulse" : ""}`}
              style={{ width: `${rpmPct}%` }}
            />
          </div>
          <div className="mt-1 flex justify-between font-tabular text-xs text-ink-dim">
            <span>{frame.rpm.toLocaleString()} rpm</span>
            <span>limit {frame.rpm_alert.toLocaleString()}</span>
          </div>
        </div>

        {/* Speed + gear */}
        <div className="grid flex-1 grid-cols-2 gap-3 md:grid-cols-4">
          <Panel className="col-span-2 flex flex-col items-center justify-center">
            <div className="font-tabular text-8xl font-bold leading-none">{speed}</div>
            <div className="mt-1 text-sm uppercase tracking-widest text-ink-dim">
              {speedUnit(units)}
            </div>
          </Panel>
          <Panel className="flex flex-col items-center justify-center">
            <div className="font-tabular text-8xl font-bold leading-none text-accent">
              {frame.gear === 0 ? "R" : frame.gear === 15 ? "N" : frame.gear}
            </div>
            <div className="mt-1 text-sm uppercase tracking-widest text-ink-dim">
              gear{frame.suggested_gear !== 15 ? ` → ${frame.suggested_gear}` : ""}
            </div>
          </Panel>
          <Panel className="flex flex-col justify-center gap-3 p-4">
            <InputBar label="Throttle" value={frame.throttle} color="bg-throttle" />
            <InputBar label="Brake" value={frame.brake} color="bg-brake" />
            {frame.boost > -0.9 && (
              <div className="flex justify-between font-tabular text-xs text-ink-dim">
                <span>Boost</span>
                <span>{frame.boost.toFixed(2)} bar</span>
              </div>
            )}
          </Panel>
        </div>

        {/* Lap + fuel + tires */}
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Panel className="p-4">
            <Label>Lap</Label>
            <div className="font-tabular text-3xl font-semibold">
              {finished ? (
                <span className="text-throttle">FIN</span>
              ) : (
                <>
                  {frame.current_lap}
                  {frame.total_laps > 0 && (
                    <span className="text-lg text-ink-dim">/{frame.total_laps}</span>
                  )}
                </>
              )}
            </div>
            <div className="mt-2 space-y-1 font-tabular text-sm">
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
          </Panel>
          <Panel className="p-4">
            <Label>Fuel</Label>
            <div className="font-tabular text-3xl font-semibold">{fuelPct.toFixed(1)}%</div>
            <div className="mt-2 h-2 overflow-hidden rounded-full bg-panel-2">
              <div
                className={`h-full rounded-full ${fuelPct < 15 ? "bg-brake" : "bg-warn"}`}
                style={{ width: `${Math.min(100, fuelPct)}%` }}
              />
            </div>
            <div className="mt-2 font-tabular text-xs text-ink-dim">
              {frame.fuel_level.toFixed(1)} / {frame.fuel_capacity.toFixed(0)} L
            </div>
          </Panel>
          <Panel className="p-4">
            <Label>Tires °C</Label>
            <div className="mt-1 grid grid-cols-2 gap-1.5">
              {(["FL", "FR", "RL", "RR"] as const).map((pos, i) => (
                <TireTemp key={pos} label={pos} temp={frame.tire_temps[i]} />
              ))}
            </div>
            {frame.tire_slip > 1.1 && (
              <div className="mt-2 text-xs font-semibold text-warn">TIRE SPIN</div>
            )}
          </Panel>
          <Panel className="p-4">
            <Label>Race</Label>
            <div className="font-tabular text-3xl font-semibold">
              P{frame.position}
              <span className="text-lg text-ink-dim">/{frame.total_positions}</span>
            </div>
            <div className="mt-2 space-y-1 font-tabular text-xs text-ink-dim">
              <Row
                k="Water"
                v={`${frame.water_temp.toFixed(0)}°C`}
                className={frame.water_temp >= 110 ? "text-brake" : frame.water_temp >= 100 ? "text-warn" : ""}
              />
              <Row
                k="Oil"
                v={`${frame.oil_temp.toFixed(0)}°C`}
                className={frame.oil_temp >= 130 ? "text-brake" : frame.oil_temp >= 115 ? "text-warn" : ""}
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
                  className={`rounded px-1.5 py-0.5 text-[9px] font-bold ${
                    aids & bit
                      ? "bg-warn text-black"
                      : "border border-edge text-ink-dim/60"
                  }`}
                >
                  {label}
                </span>
              ))}
            </div>
          </Panel>
        </div>

        <div className="text-center text-xs text-ink-dim">
          {frame.car_name}
          {frame.paused && <span className="ml-2 text-warn">· PAUSED</span>}
          {!frame.on_track && <span className="ml-2">· not on track</span>}
        </div>
      </div>

      {/* Strategy + recent laps. Below lg this stacks under the main panels
          instead of disappearing (#29): a tablet is the obvious second-screen
          device, and this rail holds the fuel strategy plus the only in-app
          route from Live into Analysis. The height constraints are lg-only —
          stacked, the panels size to their content and the page scrolls. */}
      <div className="flex flex-col gap-3 lg:max-h-full lg:overflow-hidden">
        <StrategyPanel frame={frame} laps={recentLaps} />
        <Panel className="flex flex-col overflow-hidden p-4 lg:min-h-0 lg:flex-1">
          <Label>Recent laps</Label>
        <div className="mt-2 max-h-72 space-y-1 overflow-y-auto font-tabular text-sm lg:max-h-none lg:flex-1">
          {recentLaps.length === 0 && (
            <div className="text-ink-dim">Completed laps appear here.</div>
          )}
          {recentLaps.map((lap) => (
            <button
              key={lap.id}
              onClick={() => openInAnalysis({ session: lap.session_id, laps: [lap.id] })}
              title="Open this lap in Analysis"
              className="flex w-full items-center justify-between rounded-md bg-panel-2/60 px-2 py-1.5 text-left transition-colors hover:bg-panel-2"
            >
              <span className="flex items-center gap-1.5 text-ink-dim">
                <span
                  className="h-2 w-2 rounded-full"
                  style={{ backgroundColor: lapColor(lap.id) }}
                />
                L{lap.number}
              </span>
              <span>{formatLapTime(lap.time_ms)}</span>
              <span className="text-xs text-ink-dim">{lap.fuel_consumed.toFixed(1)}L</span>
            </button>
          ))}
          </div>
        </Panel>
      </div>
    </div>
  );
}

// Rolling fuel strategy from the last few laps: laps/time to empty, pit lap.
function StrategyPanel({ frame, laps }: { frame: LiveFrame; laps: LapSummary[] }) {
  const proj = projectStrategy(frame, laps);

  return (
    <Panel className="p-4">
      <Label>Race strategy</Label>
      {proj == null ? (
        <div className="mt-2 text-xs text-ink-dim">
          Complete a lap with fuel consumption to project fuel strategy.
        </div>
      ) : (
        <div className="mt-2 space-y-1 font-tabular text-sm">
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
      <div className="mt-2 border-t border-edge pt-2 text-xs text-ink-dim">
        In-game time <span className="text-ink">{formatTimeOfDay(frame.tod_ms)}</span>
        {frame.track_name && (
          <span className="ml-2">
            · <span className="text-ink">{frame.track_name}</span>
          </span>
        )}
      </div>
    </Panel>
  );
}

function Panel({ className = "", children }: { className?: string; children: React.ReactNode }) {
  return <div className={`rounded-xl bg-panel ${className}`}>{children}</div>;
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[10px] font-semibold uppercase tracking-widest text-ink-dim">
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
      <span className="text-ink-dim">{k}</span>
      <span className={accent ? "text-accent" : className}>{v}</span>
    </div>
  );
}

function InputBar({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div>
      <div className="mb-1 flex justify-between text-xs text-ink-dim">
        <span>{label}</span>
        <span className="font-tabular">{Math.round(value)}%</span>
      </div>
      <div className="h-3 overflow-hidden rounded-full bg-panel-2">
        <div className={`h-full ${color}`} style={{ width: `${value}%` }} />
      </div>
    </div>
  );
}

function TireTemp({ label, temp }: { label: string; temp: number }) {
  // Blue (cold) -> green (optimal ~70-90) -> red (hot)
  const color =
    temp < 55 ? "bg-coast/30" : temp < 95 ? "bg-throttle/30" : "bg-brake/40";
  return (
    <div className={`rounded-md px-2 py-1.5 text-center font-tabular text-sm ${color}`}>
      <span className="mr-1 text-[10px] text-ink-dim">{label}</span>
      {temp.toFixed(0)}
    </div>
  );
}
