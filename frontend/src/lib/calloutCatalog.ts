// What each callout category actually says, with the real wording.
//
// Category toggles are useless without this: "coaching" and "chassis" mean
// nothing until you have heard them. Mirrors the backend's SPECS table
// (backend/app/race_engineer/models.py) — a backend test fails if an event
// type exists there and not here, so the two cannot drift.
//
// The English example is verbatim what the server says. Other languages word
// the same sample arguments through their phrasebook, so the example is
// exactly what the driver will hear in that language; the "when" and the
// category summaries are interface text (callout.<event>.when,
// category.<name>.summary).

import { wordMessage, type CalloutArgs, type CalloutKey } from "@/i18n/callouts";
import type { Language } from "@/i18n/languages";
import type { CalloutCategory } from "./types";

/** Every event type the backend emits (models.py SPECS). */
export type CalloutEvent =
  | "engineer_enabled"
  | "test"
  | "lap_time"
  | "sector_time"
  | "personal_best"
  | "pace_drop"
  | "final_lap"
  | "race_halfway"
  | "position_gained"
  | "position_lost"
  | "fuel_remaining"
  | "fuel_low"
  | "fuel_critical"
  | "fuel_short"
  | "pit_window_next"
  | "pit_window_open"
  | "water_temp_high"
  | "water_temp_critical"
  | "oil_temp_high"
  | "oil_temp_critical"
  | "oil_pressure_low"
  | "tire_temp_high"
  | "tire_imbalance"
  | "repeated_bottoming"
  | "braking_early"
  | "braking_late"
  | "repeated_lockups"
  | "repeated_wheelspin"
  | "corner_time_loss";

export interface CalloutInfo {
  /** Event type, as it appears in the callout and in the diagnostics panel. */
  event: CalloutEvent;
  /** Verbatim English example of what the driver hears. */
  example: string;
  /** The message key and arguments that produce `example`. */
  key: CalloutKey;
  sample: CalloutArgs[CalloutKey];
}

export interface CategoryInfo {
  callouts: CalloutInfo[];
}

function info<K extends CalloutKey>(
  event: CalloutEvent,
  key: K,
  sample: CalloutArgs[K],
  example: string,
): CalloutInfo {
  return { event, key, sample, example };
}

/** The example in a language, falling back to the English wording. */
export function calloutExample(callout: CalloutInfo, language: Language): string {
  if (language === "en") return callout.example;
  return (
    wordMessage(callout.key, callout.sample as unknown as Record<string, unknown>, language) ??
    callout.example
  );
}

const T4 = { corner: 4, corner_name: "" };
const T6 = { corner: 6, corner_name: "" };

export const CALLOUT_CATALOG: Record<CalloutCategory, CategoryInfo> = {
  system: {
    callouts: [
      info("engineer_enabled", "system.enabled", {}, "Race engineer enabled."),
      info("test", "system.test", {}, "Race engineer test callout."),
    ],
  },
  lap: {
    callouts: [
      info(
        "lap_time",
        "lap.time",
        { time_ms: 92_487, delta_ms: 210 },
        "Lap time, one minute thirty-two point five. Two tenths slower.",
      ),
      info(
        "sector_time",
        "sector.time",
        {
          sector: 1,
          time_ms: 32_483,
          delta_ms: -369,
          content: "both",
          comparison: "individual",
          places: 3,
          rounding: "round",
          level: false,
        },
        "Sector one, thirty-two point four eight three seconds, point three six nine seconds faster.",
      ),
    ],
  },
  pace: {
    callouts: [
      info(
        "personal_best",
        "lap.personal_best",
        { time_ms: 91_812, gain_ms: 640 },
        "New personal best, one minute thirty-one point eight. Six tenths faster.",
      ),
      info(
        "pace_drop",
        "pace.drop",
        { gap_ms: 820, laps: 3 },
        "Your pace is dropping, eight tenths off your best.",
      ),
    ],
  },
  race: {
    callouts: [
      info("final_lap", "race.final_lap", { lap: 12 }, "Final lap."),
      info("race_halfway", "race.halfway", { lap: 6, total_laps: 12 }, "Halfway through the race."),
    ],
  },
  position: {
    callouts: [
      info(
        "position_gained",
        "position.gained",
        { position: 4, previous: 5 },
        "Position gained. You are now position four.",
      ),
      info(
        "position_lost",
        "position.lost",
        { position: 5, previous: 4 },
        "Position lost. You are now position five.",
      ),
    ],
  },
  fuel: {
    callouts: [
      info("fuel_remaining", "fuel.remaining", { laps: 5.2 }, "Fuel remaining, five point two laps."),
      info("fuel_low", "fuel.low", { laps: 2.8 }, "Fuel low, two point eight laps remaining."),
      info(
        "fuel_critical",
        "fuel.critical",
        { laps: 1.2 },
        "Fuel critical, one point two laps remaining.",
      ),
    ],
  },
  strategy: {
    callouts: [
      info("fuel_short", "fuel.shortfall", { laps: 1.2 }, "Fuel will be short by one point two laps."),
      info("pit_window_next", "strategy.pit_window_next", { lap: 9 }, "Pit window opens next lap."),
      info("pit_window_open", "strategy.pit_window_open", { lap: 9 }, "Pit window is open."),
    ],
  },
  engine: {
    callouts: [
      info("water_temp_high", "engine.water_temp_high", { celsius: 112 }, "Water temperature high."),
      info(
        "water_temp_critical",
        "engine.water_temp_critical",
        { celsius: 122 },
        "Water temperature critical.",
      ),
      info("oil_temp_high", "engine.oil_temp_high", { celsius: 132 }, "Oil temperature high."),
      info(
        "oil_temp_critical",
        "engine.oil_temp_critical",
        { celsius: 142 },
        "Oil temperature critical.",
      ),
      info("oil_pressure_low", "engine.oil_pressure_low", { bar: 1.6 }, "Oil pressure low."),
    ],
  },
  tires: {
    callouts: [
      info("tire_temp_high", "tires.temp_high", { celsius: 114 }, "Tire temperatures high."),
      info(
        "tire_imbalance",
        "tires.imbalance",
        { axis: "front_rear", hotter: "rear", degrees: 8 },
        "Rear tires are running eight degrees hotter than the fronts.",
      ),
    ],
  },
  chassis: {
    callouts: [
      info(
        "repeated_bottoming",
        "coaching.repeated_bottoming",
        { ...T4, wheel: "fl", count: 3 },
        "The car is bottoming out at turn four.",
      ),
    ],
  },
  coaching: {
    callouts: [
      info(
        "braking_early",
        "coaching.braking_early",
        { ...T4, meters: 15, units: "metric" },
        "You are braking early into turn four, about fifteen meters.",
      ),
      info(
        "braking_late",
        "coaching.braking_late",
        { ...T4, meters: 15, units: "metric" },
        "You are braking late into turn four, about fifteen meters.",
      ),
      info(
        "repeated_lockups",
        "coaching.repeated_lockups",
        { ...T4, wheel: "fl", count: 3 },
        "Repeated front-left lockups into turn four.",
      ),
      info(
        "repeated_wheelspin",
        "coaching.repeated_wheelspin",
        { ...T6, wheel: "rr", count: 3 },
        "Repeated wheelspin on the exit of turn six.",
      ),
      info(
        "corner_time_loss",
        "coaching.corner_time_loss",
        { ...T6, loss_ms: 310, brake_delta_m: -18, apex_delta_kmh: -5, units: "metric" },
        "You lost three tenths in turn six. You braked eighteen meters earlier and " +
          "carried five kilometers per hour less at the apex.",
      ),
    ],
  },
};
