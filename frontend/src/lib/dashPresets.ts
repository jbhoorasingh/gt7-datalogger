// Built-in driver-dashboard layouts. Full-screen (size: null) dark-page grids
// rendered by GridRenderer; the builder can clone one into a server layout
// for customization.

import type { MessageKey } from "@/i18n";
import type { LayoutConfig } from "./layout";

const BASE = {
  version: 2 as const,
  grid: { cols: 8, rows: 5, gap: 8 },
  page: "dark" as const,
  bg: 60,
  size: null,
  padX: 16,
  padY: 16,
  demo: false,
};

// Glanceable race-engineer screen: alerts up top, strategy + timing in the
// middle, car health and race context below.
const RACE_ENGINEER: LayoutConfig = {
  ...BASE,
  cells: [
    { id: "re-alerts", widget: "alerts", variant: "banner", x: 0, y: 0, w: 8, h: 1 },
    { id: "re-fuel", widget: "fuel", variant: "laps", x: 0, y: 1, w: 2, h: 2 },
    { id: "re-pit", widget: "strategy", variant: "pit-window", x: 2, y: 1, w: 2, h: 2 },
    { id: "re-delta", widget: "delta", variant: "big", x: 4, y: 1, w: 2, h: 2 },
    { id: "re-times", widget: "times", variant: "list", x: 6, y: 1, w: 2, h: 2 },
    { id: "re-tires", widget: "tires", variant: "temps-slip", x: 0, y: 3, w: 2, h: 2 },
    { id: "re-engine", widget: "engine", variant: "detailed", x: 2, y: 3, w: 2, h: 2 },
    { id: "re-pos", widget: "position", variant: "big", x: 4, y: 3, w: 2, h: 1 },
    { id: "re-clock", widget: "clock", variant: "digits", x: 4, y: 4, w: 2, h: 1 },
    { id: "re-aids", widget: "aids", variant: "badges", x: 6, y: 3, w: 2, h: 1 },
    { id: "re-speed", widget: "speed", variant: "digits", x: 6, y: 4, w: 2, h: 1 },
  ],
};

// Endurance racing: fuel is the hero, engine health gets a wide readout.
const ENDURANCE: LayoutConfig = {
  ...BASE,
  cells: [
    { id: "en-alerts", widget: "alerts", variant: "banner", x: 0, y: 0, w: 8, h: 1 },
    { id: "en-fuel", widget: "fuel", variant: "laps", x: 0, y: 1, w: 4, h: 2 },
    { id: "en-strategy", widget: "strategy", variant: "summary", x: 4, y: 1, w: 2, h: 2 },
    { id: "en-clock", widget: "clock", variant: "digits", x: 6, y: 1, w: 2, h: 1 },
    { id: "en-pos", widget: "position", variant: "compact", x: 6, y: 2, w: 2, h: 1 },
    { id: "en-engine", widget: "engine", variant: "detailed", x: 0, y: 3, w: 4, h: 2 },
    { id: "en-tires", widget: "tires", variant: "temps", x: 4, y: 3, w: 2, h: 2 },
    { id: "en-times", widget: "times", variant: "list", x: 6, y: 3, w: 2, h: 2 },
  ],
};

export const DASH_PRESETS: Record<string, { labelKey: MessageKey; layout: LayoutConfig }> = {
  "race-engineer": { labelKey: "layout.preset.raceEngineer", layout: RACE_ENGINEER },
  endurance: { labelKey: "layout.preset.endurance", layout: ENDURANCE },
};

export const DEFAULT_DASH_PRESET = "race-engineer";
