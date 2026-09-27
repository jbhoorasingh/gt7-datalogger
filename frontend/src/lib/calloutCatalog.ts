// What each callout category actually says, with the real wording.
//
// Category toggles are useless without this: "coaching" and "chassis" mean
// nothing until you have heard them. Mirrors the backend's SPECS table
// (backend/app/race_engineer/models.py) — a backend test fails if an event
// type exists there and not here, so the two cannot drift.

import type { CalloutCategory } from "./types";

export interface CalloutInfo {
  /** Event type, as it appears in the callout and in the diagnostics panel. */
  event: string;
  /** Verbatim example of what the driver hears. */
  example: string;
  /** When it fires. */
  when: string;
}

export interface CategoryInfo {
  summary: string;
  callouts: CalloutInfo[];
}

export const CALLOUT_CATALOG: Record<CalloutCategory, CategoryInfo> = {
  system: {
    summary: "Status messages from Race Engineer itself.",
    callouts: [
      {
        event: "engineer_enabled",
        example: "Race engineer enabled.",
        when: "you turn voice on — also the browser's audio test",
      },
      {
        event: "test",
        example: "Race engineer test callout.",
        when: "Settings › Race Engineer › Send test callout",
      },
    ],
  },
  lap: {
    summary: "Your lap time, every completed lap.",
    callouts: [
      {
        event: "lap_time",
        example: "Lap time, one minute thirty-two point five. Two tenths slower.",
        when: "every full lap, compared with your session best",
      },
    ],
  },
  pace: {
    summary: "How your pace is trending against your best lap.",
    callouts: [
      {
        event: "personal_best",
        example: "New personal best, one minute thirty-one point eight. Six tenths faster.",
        when: "a full lap beats your session best",
      },
      {
        event: "pace_drop",
        example: "Your pace is dropping, eight tenths off your best.",
        when: "three laps running average well off the best, none of them back on it",
      },
    ],
  },
  race: {
    summary: "Where you are in the race distance.",
    callouts: [
      { event: "final_lap", example: "Final lap.", when: "you start the last lap" },
      {
        event: "race_halfway",
        example: "Halfway through the race.",
        when: "you pass the midpoint of a race of four laps or more",
      },
    ],
  },
  position: {
    summary: "Places gained and lost.",
    callouts: [
      {
        event: "position_gained",
        example: "Position gained. You are now position four.",
        when: "a position change that holds for about a second",
      },
      {
        event: "position_lost",
        example: "Position lost. You are now position five.",
        when: "same, in the other direction",
      },
    ],
  },
  fuel: {
    summary: "How much further the tank will take you.",
    callouts: [
      {
        event: "fuel_remaining",
        example: "Fuel remaining, five point two laps.",
        when: "once a lap, when the tank is close to mattering",
      },
      {
        event: "fuel_low",
        example: "Fuel low, two point eight laps remaining.",
        when: "range drops under three laps",
      },
      {
        event: "fuel_critical",
        example: "Fuel critical, one point two laps remaining.",
        when: "range drops under one and a half laps — interrupts other speech",
      },
    ],
  },
  strategy: {
    summary: "Pit decisions.",
    callouts: [
      {
        event: "fuel_short",
        example: "Fuel will be short by one point two laps.",
        when: "two laps running project you cannot reach the finish",
      },
      {
        event: "pit_window_next",
        example: "Pit window opens next lap.",
        when: "the lap you must pit by comes into range",
      },
      {
        event: "pit_window_open",
        example: "Pit window is open.",
        when: "you have reached that lap",
      },
    ],
  },
  engine: {
    summary: "Mechanical warnings. The only category on at every verbosity.",
    callouts: [
      {
        event: "water_temp_high",
        example: "Water temperature high.",
        when: "above 110 °C for five seconds",
      },
      {
        event: "water_temp_critical",
        example: "Water temperature critical.",
        when: "above 120 °C — interrupts other speech",
      },
      { event: "oil_temp_high", example: "Oil temperature high.", when: "above 130 °C" },
      {
        event: "oil_temp_critical",
        example: "Oil temperature critical.",
        when: "above 140 °C — interrupts other speech",
      },
      {
        event: "oil_pressure_low",
        example: "Oil pressure low.",
        when: "under 2 bar with the engine pulling — interrupts other speech",
      },
    ],
  },
  tires: {
    summary: "Tire temperature and balance.",
    callouts: [
      {
        event: "tire_temp_high",
        example: "Tire temperatures high.",
        when: "the hottest tire stays above 110 °C",
      },
      {
        event: "tire_imbalance",
        example: "Rear tires are running eight degrees hotter than the fronts.",
        when: "after a lap, one axle or side averaging 8 °C hotter",
      },
    ],
  },
  chassis: {
    summary: "Setup feedback — things the car is doing, not you.",
    callouts: [
      {
        event: "repeated_bottoming",
        example: "The car is bottoming out at turn four.",
        when: "the floor grounds in the same place across recent laps",
      },
    ],
  },
  coaching: {
    summary:
      "Driving feedback against your best lap. Waits until several laps agree on " +
      "the track distance, so it never compares against a half-recorded lap.",
    callouts: [
      {
        event: "braking_early",
        example: "You are braking early into turn four, about fifteen meters.",
        when: "recent laps consistently brake before your best lap's marker",
      },
      {
        event: "braking_late",
        example: "You are braking late into turn four, about fifteen meters.",
        when: "the same, in the other direction",
      },
      {
        event: "repeated_lockups",
        example: "Repeated front-left lockups into turn four.",
        when: "the same wheel locks in the same place three times in recent laps",
      },
      {
        event: "repeated_wheelspin",
        example: "Repeated wheelspin on the exit of turn six.",
        when: "the same, on corner exit",
      },
      {
        event: "corner_time_loss",
        example:
          "You lost three tenths in turn six. You braked eighteen meters earlier and " +
          "carried five kilometers per hour less at the apex.",
        when: "after a lap slower than your best: where the time went, and how",
      },
    ],
  },
};
