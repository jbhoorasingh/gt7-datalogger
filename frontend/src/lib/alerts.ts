// Race-engineer alert engine shared by the alerts widget and the color
// thresholds in the engine/tire widgets. Pure function of the live frame +
// recent laps so it can run on every animation tick.

import { t } from "@/i18n";
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
        message: t("alert.fuelCritical", { laps: proj.lapsToEmpty.toFixed(1) }),
      });
    } else if (proj.lapsToEmpty < THRESHOLDS.fuelLapsWarn) {
      out.push({
        id: "fuel",
        severity: "warn",
        message: t("alert.fuelWarn", { laps: proj.lapsToEmpty.toFixed(1) }),
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
        message: t(proj.pitBeforeLap <= frame.current_lap ? "alert.pitThisLap" : "alert.pitNextLap"),
      });
    }
  }

  if (frame.water_temp > THRESHOLDS.waterCritical) {
    out.push({
      id: "water",
      severity: "critical",
      message: t("alert.waterCritical", { temp: Math.round(frame.water_temp) }),
    });
  } else if (frame.water_temp > THRESHOLDS.waterWarn) {
    out.push({
      id: "water",
      severity: "warn",
      message: t("alert.waterWarn", { temp: Math.round(frame.water_temp) }),
    });
  }

  if (frame.oil_temp > THRESHOLDS.oilTempCritical) {
    out.push({
      id: "oil-temp",
      severity: "critical",
      message: t("alert.oilCritical", { temp: Math.round(frame.oil_temp) }),
    });
  } else if (frame.oil_temp > THRESHOLDS.oilTempWarn) {
    out.push({
      id: "oil-temp",
      severity: "warn",
      message: t("alert.oilWarn", { temp: Math.round(frame.oil_temp) }),
    });
  }

  if (frame.oil_pressure >= 0 && frame.oil_pressure < THRESHOLDS.oilPressureMin && frame.rpm > 2000) {
    out.push({
      id: "oil-pressure",
      severity: "critical",
      message: t("alert.oilPressure", { pressure: frame.oil_pressure.toFixed(1) }),
    });
  }

  const hottest = Math.max(...frame.tire_temps);
  if (hottest > THRESHOLDS.tireTempWarn) {
    out.push({
      id: "tires",
      severity: "warn",
      message: t("alert.tiresHot", { temp: Math.round(hottest) }),
    });
  }

  out.sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity]);
  return out;
}
