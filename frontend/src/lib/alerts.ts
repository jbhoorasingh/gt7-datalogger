// Race-engineer alert engine shared by the alerts widget and the color
// thresholds in the engine/tire widgets. Pure function of the live frame +
// recent laps so it can run on every animation tick.

import { projectStrategy } from "./strategy";
import type { LapSummary, LiveFrame } from "./types";

export type AlertSeverity = "info" | "warn" | "critical";

export interface DashAlert {
  id: string;
  severity: AlertSeverity;
  message: string;
}

// Exported so widgets color their readouts with the same limits the alert
// engine fires at.
export const THRESHOLDS = {
  fuelLapsWarn: 3,
  fuelLapsCritical: 1.5,
  waterWarn: 110,
  waterCritical: 120,
  oilTempWarn: 130,
  oilTempCritical: 140,
  oilPressureMin: 2.0, // bar, only meaningful with the engine turning
  tireTempWarn: 110,
  // The tire tiles' working window, which no alert fires at: cold below
  // tireTempCold, hot from tireTempHot up. The Live and dash tiles, Corner
  // Detail and the race line's Tire temp lens all band by these two.
  tireTempCold: 55,
  tireTempHot: 95,
} as const;

const SEVERITY_ORDER: Record<AlertSeverity, number> = { critical: 0, warn: 1, info: 2 };

export function computeAlerts(frame: LiveFrame, laps: LapSummary[]): DashAlert[] {
  if (!frame.on_track || frame.paused) return [];
  const out: DashAlert[] = [];

  const proj = projectStrategy(frame, laps);
  if (proj != null) {
    if (proj.lapsToEmpty < THRESHOLDS.fuelLapsCritical) {
      out.push({
        id: "fuel",
        severity: "critical",
        message: `FUEL ${proj.lapsToEmpty.toFixed(1)} LAPS`,
      });
    } else if (proj.lapsToEmpty < THRESHOLDS.fuelLapsWarn) {
      out.push({
        id: "fuel",
        severity: "warn",
        message: `FUEL ${proj.lapsToEmpty.toFixed(1)} laps`,
      });
    }
    // Only meaningful in lapped races where running dry before the end is
    // actually possible.
    if (
      frame.total_laps > 0 &&
      proj.pitBeforeLap < frame.total_laps &&
      proj.pitBeforeLap <= frame.current_lap + 1
    ) {
      out.push({
        id: "pit",
        severity: "info",
        message: proj.pitBeforeLap <= frame.current_lap ? "PIT THIS LAP" : "PIT NEXT LAP",
      });
    }
  }

  if (frame.water_temp > THRESHOLDS.waterCritical) {
    out.push({
      id: "water",
      severity: "critical",
      message: `WATER ${Math.round(frame.water_temp)}°C`,
    });
  } else if (frame.water_temp > THRESHOLDS.waterWarn) {
    out.push({
      id: "water",
      severity: "warn",
      message: `Water ${Math.round(frame.water_temp)}°C`,
    });
  }

  if (frame.oil_temp > THRESHOLDS.oilTempCritical) {
    out.push({
      id: "oil-temp",
      severity: "critical",
      message: `OIL ${Math.round(frame.oil_temp)}°C`,
    });
  } else if (frame.oil_temp > THRESHOLDS.oilTempWarn) {
    out.push({
      id: "oil-temp",
      severity: "warn",
      message: `Oil ${Math.round(frame.oil_temp)}°C`,
    });
  }

  if (frame.oil_pressure >= 0 && frame.oil_pressure < THRESHOLDS.oilPressureMin && frame.rpm > 2000) {
    out.push({
      id: "oil-pressure",
      severity: "critical",
      message: `OIL PRESSURE ${frame.oil_pressure.toFixed(1)} bar`,
    });
  }

  const hottest = Math.max(...frame.tire_temps);
  if (hottest > THRESHOLDS.tireTempWarn) {
    out.push({
      id: "tires",
      severity: "warn",
      message: `Tires hot ${Math.round(hottest)}°C`,
    });
  }

  out.sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity]);
  return out;
}
