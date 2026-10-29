// Per-widget metadata for the grid layout system: which visual variants a
// metric supports, and which cell footprints (w x h spans) it may occupy.
// Pure data — the component map lives in widgetRegistry.tsx so this file can
// be imported by normalization code without dragging in React.

import type { MessageKey } from "@/i18n";
import type { LapSummary, LiveFrame } from "./types";
import { WIDGET_LABEL_KEYS, type WidgetId } from "./overlay";

export type WidgetGroup = "driving" | "timing" | "race" | "car" | "strategy";

export type WidgetSize = [number, number]; // [w, h] in grid cells

export interface WidgetRenderProps {
  frame: LiveFrame;
  laps: LapSummary[];
  variant: string;
  w: number;
  h: number;
  options: Record<string, unknown>;
}

export interface WidgetMeta {
  labelKey: MessageKey;
  group: WidgetGroup;
  // baseW is the content width (px) the variant was designed for at 1:1. The
  // grid zoom is capped so this width fits the cell — without it, text-heavy
  // widgets wrap and clip in tall cells. Defaults to 90.
  variants: { key: string; labelKey: MessageKey; baseW?: number }[];
  defaultVariant: string;
  sizes: WidgetSize[]; // allowed footprints; first entry = default
  // Rendered without the card chrome — the widget draws its own background
  // (or nothing at all, keeping the cell transparent for OBS).
  frameless?: boolean;
}

export const WIDGET_GROUP_LABEL_KEYS: Record<WidgetGroup, MessageKey> = {
  driving: "widget.group.driving",
  timing: "widget.group.timing",
  race: "widget.group.race",
  car: "widget.group.car",
  strategy: "widget.group.strategy",
};

export const WIDGET_META: Record<WidgetId, WidgetMeta> = {
  gear: {
    labelKey: WIDGET_LABEL_KEYS.gear,
    group: "driving",
    variants: [
      { key: "digits", labelKey: "widget.variant.digitSuggested", baseW: 64 },
      { key: "plain", labelKey: "widget.variant.digitOnly", baseW: 64 },
    ],
    defaultVariant: "digits",
    sizes: [
      [1, 1],
      [1, 2],
      [2, 2],
      [4, 4],
    ],
  },
  speed: {
    labelKey: WIDGET_LABEL_KEYS.speed,
    group: "driving",
    variants: [
      { key: "digits", labelKey: "widget.variant.digits" },
      { key: "bar", labelKey: "widget.variant.bar", baseW: 150 },
      { key: "gauge", labelKey: "widget.variant.gauge", baseW: 96 },
    ],
    defaultVariant: "digits",
    sizes: [
      [1, 1],
      [2, 1],
      [1, 2],
      [2, 2],
      [4, 4],
    ],
  },
  rpm: {
    labelKey: WIDGET_LABEL_KEYS.rpm,
    group: "driving",
    variants: [
      { key: "bar", labelKey: "widget.variant.bar", baseW: 160 },
      { key: "shift-lights", labelKey: "widget.variant.shiftLights", baseW: 160 },
      { key: "gauge", labelKey: "widget.variant.gauge", baseW: 96 },
      { key: "digits", labelKey: "widget.variant.digits", baseW: 100 },
    ],
    defaultVariant: "bar",
    sizes: [
      [2, 1],
      [4, 1],
      [2, 2],
      [4, 2],
      [4, 4],
    ],
  },
  inputs: {
    labelKey: WIDGET_LABEL_KEYS.inputs,
    group: "driving",
    variants: [
      { key: "bars-h", labelKey: "widget.variant.barsH", baseW: 144 },
      { key: "bars-v", labelKey: "widget.variant.barsV", baseW: 60 },
    ],
    defaultVariant: "bars-h",
    sizes: [
      [2, 1],
      [4, 1],
      [1, 2],
      [2, 2],
    ],
  },
  steering: {
    labelKey: WIDGET_LABEL_KEYS.steering,
    group: "driving",
    variants: [
      { key: "wheel", labelKey: "widget.variant.wheelAngle", baseW: 72 },
      { key: "plain", labelKey: "widget.variant.wheelOnly", baseW: 72 },
    ],
    defaultVariant: "wheel",
    sizes: [
      [1, 1],
      [1, 2],
      [2, 2],
      [4, 4],
    ],
  },
  times: {
    labelKey: WIDGET_LABEL_KEYS.times,
    group: "timing",
    variants: [
      { key: "list", labelKey: "widget.variant.lapBestLast", baseW: 150 },
      { key: "last", labelKey: "widget.variant.lastBig", baseW: 150 },
      { key: "best", labelKey: "widget.variant.bestBig", baseW: 150 },
    ],
    defaultVariant: "list",
    sizes: [
      [2, 1],
      [2, 2],
      [4, 2],
    ],
  },
  delta: {
    labelKey: WIDGET_LABEL_KEYS.delta,
    group: "timing",
    variants: [
      { key: "big", labelKey: "widget.variant.bigNumber", baseW: 110 },
      { key: "bar", labelKey: "widget.variant.centeredBar", baseW: 160 },
    ],
    defaultVariant: "big",
    sizes: [
      [1, 1],
      [2, 1],
      [2, 2],
      [4, 4],
    ],
  },
  position: {
    labelKey: WIDGET_LABEL_KEYS.position,
    group: "race",
    variants: [
      { key: "big", labelKey: "widget.variant.big" },
      { key: "compact", labelKey: "widget.variant.compact", baseW: 70 },
    ],
    defaultVariant: "big",
    sizes: [
      [1, 1],
      [2, 1],
      [2, 2],
    ],
  },
  tires: {
    labelKey: WIDGET_LABEL_KEYS.tires,
    group: "car",
    variants: [
      { key: "temps", labelKey: "widget.variant.temps", baseW: 72 },
      { key: "temps-slip", labelKey: "widget.variant.tempsSlip", baseW: 72 },
    ],
    defaultVariant: "temps",
    sizes: [
      [1, 1],
      [1, 2],
      [2, 2],
      [4, 4],
    ],
  },
  fuel: {
    labelKey: WIDGET_LABEL_KEYS.fuel,
    group: "strategy",
    variants: [
      { key: "percent", labelKey: "widget.variant.percent", baseW: 70 },
      { key: "bar", labelKey: "widget.variant.bar", baseW: 150 },
      { key: "laps", labelKey: "widget.variant.lapsRemaining" },
    ],
    defaultVariant: "percent",
    sizes: [
      [1, 1],
      [1, 2],
      [2, 1],
      [2, 2],
      [4, 2],
    ],
  },
  strategy: {
    labelKey: WIDGET_LABEL_KEYS.strategy,
    group: "strategy",
    variants: [
      { key: "summary", labelKey: "widget.variant.summary", baseW: 120 },
      { key: "pit-window", labelKey: "widget.variant.pitWindow", baseW: 100 },
    ],
    defaultVariant: "summary",
    sizes: [
      [2, 1],
      [2, 2],
    ],
  },
  clock: {
    labelKey: WIDGET_LABEL_KEYS.clock,
    group: "race",
    variants: [{ key: "digits", labelKey: "widget.variant.digits", baseW: 80 }],
    defaultVariant: "digits",
    sizes: [
      [1, 1],
      [2, 1],
    ],
  },
  engine: {
    labelKey: WIDGET_LABEL_KEYS.engine,
    group: "car",
    variants: [
      { key: "compact", labelKey: "widget.variant.waterOil", baseW: 110 },
      { key: "detailed", labelKey: "widget.variant.detailed", baseW: 130 },
    ],
    defaultVariant: "compact",
    sizes: [
      [1, 1],
      [2, 1],
      [2, 2],
      [4, 2],
    ],
  },
  aids: {
    labelKey: WIDGET_LABEL_KEYS.aids,
    group: "car",
    variants: [{ key: "badges", labelKey: "widget.variant.badges", baseW: 130 }],
    defaultVariant: "badges",
    sizes: [
      [2, 1],
      [1, 1],
      [4, 1],
    ],
  },
  boost: {
    labelKey: WIDGET_LABEL_KEYS.boost,
    group: "driving",
    variants: [
      { key: "digits", labelKey: "widget.variant.digits" },
      { key: "gauge", labelKey: "widget.variant.gauge", baseW: 96 },
    ],
    defaultVariant: "digits",
    sizes: [
      [1, 1],
      [2, 1],
      [2, 2],
    ],
  },
  alerts: {
    labelKey: WIDGET_LABEL_KEYS.alerts,
    group: "race",
    variants: [
      { key: "banner", labelKey: "widget.variant.banner", baseW: 220 },
      { key: "list", labelKey: "widget.variant.list", baseW: 160 },
    ],
    defaultVariant: "banner",
    frameless: true,
    sizes: [
      [4, 1],
      [8, 1],
      [2, 1],
      [4, 2],
      [2, 2],
    ],
  },
};

export function isValidVariant(widget: WidgetId, variant: string): boolean {
  return WIDGET_META[widget].variants.some((v) => v.key === variant);
}

export function isAllowedSize(widget: WidgetId, w: number, h: number): boolean {
  return WIDGET_META[widget].sizes.some(([sw, sh]) => sw === w && sh === h);
}

export function defaultSize(widget: WidgetId): WidgetSize {
  return WIDGET_META[widget].sizes[0];
}

export function widgetBaseW(widget: WidgetId, variant: string): number {
  return WIDGET_META[widget].variants.find((v) => v.key === variant)?.baseW ?? 90;
}
