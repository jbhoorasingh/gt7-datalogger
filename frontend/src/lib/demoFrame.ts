// Synthetic telemetry for overlay placeholder mode: a plausible ~20 s
// "lap" loop (accelerate, brake for two corners, spin the rears once) so
// every widget shows realistic, moving values while designing the layout.

import type { LapSummary, LiveFrame } from "./types";

const LOOP_S = 20;

function corner(phase: number, center: number, width: number, depth: number): number {
  const d = Math.min(Math.abs(phase - center), LOOP_S - Math.abs(phase - center));
  return d < width ? depth * (1 - (d / width) ** 2) : 0;
}

export function demoFrame(nowMs: number): LiveFrame {
  const t = (nowMs / 1000) % LOOP_S;
  // Speed 230 km/h baseline with two braking zones
  const speed = Math.max(74, 232 - corner(t, 5, 2.2, 148) - corner(t, 13.5, 1.8, 96));
  const prevSpeed = Math.max(
    74,
    232 - corner((t - 0.1 + LOOP_S) % LOOP_S, 5, 2.2, 148) - corner((t - 0.1 + LOOP_S) % LOOP_S, 13.5, 1.8, 96),
  );
  const accelerating = speed >= prevSpeed;
  const braking = !accelerating && prevSpeed - speed > 1.2;
  const gear = Math.min(6, Math.max(2, Math.ceil(speed / 42)));
  const rpm = 3400 + ((speed * 2.6) % 60) * 60 + gear * 180;
  const exitSpin = t > 6.8 && t < 7.4; // wheelspin on corner exit

  return {
    on_track: true,
    paused: false,
    speed_kmh: speed,
    rpm: Math.round(Math.min(8900, rpm)),
    rpm_alert: 8600,
    gear,
    suggested_gear: braking ? Math.max(2, gear - 1) : 15,
    throttle: accelerating ? 100 : braking ? 0 : 55,
    brake: braking ? Math.min(100, (prevSpeed - speed) * 22) : 0,
    boost: 0.42,
    // Drains over a slow cycle so the strategy widget and the low-fuel /
    // pit-window alerts all get exercised while designing a layout.
    fuel_level: Math.max(1.5, 62.4 - (((nowMs / 1000) % 150) / 150) * 61),
    fuel_capacity: 100,
    current_lap: 3,
    total_laps: 5,
    best_lap_ms: 111_410,
    last_lap_ms: 111_672,
    position: 3,
    total_positions: 16,
    tire_temps: [78 + speed / 40, 79 + speed / 40, 74 + speed / 45, 75 + speed / 45] as [
      number,
      number,
      number,
      number,
    ],
    tire_slip: exitSpin ? 1.18 : 1.0,
    water_temp: 85,
    oil_temp: 102,
    oil_pressure: 5.4,
    aids: exitSpin ? 1 : 0, // TCS bit flickers with the fake wheelspin
    surface: 0x1111, // four wheels on tarmac

    car_id: 0,
    car_name: "Placeholder GT '26",
    session_best_ms: 111_410,
    prev_best_ms: 112_050,
    // Fake live delta: gains through the braking zones, drifts back on the
    // straights, so both colors and bar directions show while designing.
    delta_ms: Math.round(400 * Math.sin((t / LOOP_S) * 2 * Math.PI) - corner(t, 5, 2.2, 300)),
    lap_elapsed_ms: Math.round(t * 1000),
    pos_x: 0,
    pos_z: 0,
    tod_ms: (14 * 3600 + Math.floor(nowMs / 1000) * 4) * 1000, // fast in-game clock
    track_name: "Design Ring",
    // Saw the wheel through the two corners so the steering widget moves.
    steer_rad:
      1.1 * (corner(t, 5.6, 2.4, 1) - corner(t, 13.9, 2.0, 0.8)) *
      Math.sin((t / LOOP_S) * 4 * Math.PI),
  };
}

export const DEMO_LAPS: LapSummary[] = [
  {
    id: -1,
    session_id: -1,
    number: 2,
    time_ms: 111_672,
    fuel_consumed: 1.82,
    full_throttle_pct: 61,
    full_brake_pct: 8,
    coasting_pct: 3,
    tire_spin_pct: 2,
    max_speed: 232,
    min_body_height: 61,
  },
  {
    id: -2,
    session_id: -1,
    number: 1,
    time_ms: 112_050,
    fuel_consumed: 1.86,
    full_throttle_pct: 59,
    full_brake_pct: 9,
    coasting_pct: 4,
    tire_spin_pct: 3,
    max_speed: 229,
    min_body_height: 62,
  },
];
