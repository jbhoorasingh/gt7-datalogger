// Per-widget metadata for the grid layout system: which visual variants a
// metric supports, and which cell footprints (w x h spans) it may occupy.
// Pure data — the component map lives in widgetRegistry.tsx so this file can
// be imported by normalization code without dragging in React.

import type { LapSummary, LiveFrame } from "./types";
import { WIDGET_LABELS, type WidgetId } from "./overlay";

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
  label: string;
  group: WidgetGroup;
  // baseW is the content width (px) the variant was designed for at 1:1. The
  // grid zoom is capped so this width fits the cell — without it, text-heavy
  // widgets wrap and clip in tall cells. Defaults to 90.
  variants: { key: string; label: string; baseW?: number }[];
  defaultVariant: string;
  sizes: WidgetSize[]; // allowed footprints; first entry = default
  // Rendered without the card chrome — the widget draws its own background
  // (or nothing at all, keeping the cell transparent for OBS).
  frameless?: boolean;
}

export const WIDGET_GROUP_LABELS: Record<WidgetGroup, string> = {
  driving: "Driving",
  timing: "Timing",
  race: "Race",
  car: "Car health",
  strategy: "Strategy",
};

export const WIDGET_META: Record<WidgetId, WidgetMeta> = {
  gear: {
    label: WIDGET_LABELS.gear,
    group: "driving",
    variants: [
      { key: "digits", label: "Digit + suggested", baseW: 64 },
      { key: "plain", label: "Digit only", baseW: 64 },
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
    label: WIDGET_LABELS.speed,
    group: "driving",
    variants: [
      { key: "digits", label: "Digits" },
      { key: "bar", label: "Bar", baseW: 150 },
      { key: "gauge", label: "Gauge", baseW: 96 },
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
    label: WIDGET_LABELS.rpm,
    group: "driving",
    variants: [
      { key: "bar", label: "Bar", baseW: 160 },
      { key: "shift-lights", label: "Shift lights", baseW: 160 },
      { key: "gauge", label: "Gauge", baseW: 96 },
      { key: "digits", label: "Digits", baseW: 100 },
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
    label: WIDGET_LABELS.inputs,
    group: "driving",
    variants: [
      { key: "bars-h", label: "Horizontal bars", baseW: 144 },
      { key: "bars-v", label: "Vertical bars", baseW: 60 },
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
    label: WIDGET_LABELS.steering,
    group: "driving",
    variants: [
      { key: "wheel", label: "Wheel + angle", baseW: 72 },
      { key: "plain", label: "Wheel only", baseW: 72 },
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
    label: WIDGET_LABELS.times,
    group: "timing",
    variants: [
      { key: "list", label: "Lap / best / last", baseW: 150 },
      { key: "last", label: "Last lap (big)", baseW: 150 },
      { key: "best", label: "Best lap (big)", baseW: 150 },
    ],
    defaultVariant: "list",
    sizes: [
      [2, 1],
      [2, 2],
      [4, 2],
    ],
  },
  delta: {
    label: WIDGET_LABELS.delta,
    group: "timing",
    variants: [
      { key: "big", label: "Big number", baseW: 110 },
      { key: "bar", label: "Centered bar", baseW: 160 },
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
    label: WIDGET_LABELS.position,
    group: "race",
    variants: [
      { key: "big", label: "Big" },
      { key: "compact", label: "Compact", baseW: 70 },
    ],
    defaultVariant: "big",
    sizes: [
      [1, 1],
      [2, 1],
      [2, 2],
    ],
  },
  tires: {
    label: WIDGET_LABELS.tires,
    group: "car",
    variants: [
      { key: "temps", label: "Temps", baseW: 72 },
      { key: "temps-slip", label: "Temps + slip", baseW: 72 },
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
    label: WIDGET_LABELS.fuel,
    group: "strategy",
    variants: [
      { key: "percent", label: "Percent", baseW: 70 },
      { key: "bar", label: "Bar", baseW: 150 },
      { key: "laps", label: "Laps remaining" },
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
    label: WIDGET_LABELS.strategy,
    group: "strategy",
    variants: [
      { key: "summary", label: "Summary", baseW: 120 },
      { key: "pit-window", label: "Pit window", baseW: 100 },
    ],
    defaultVariant: "summary",
    sizes: [
      [2, 1],
      [2, 2],
    ],
  },
  clock: {
    label: WIDGET_LABELS.clock,
    group: "race",
    variants: [{ key: "digits", label: "Digits", baseW: 80 }],
    defaultVariant: "digits",
    sizes: [
      [1, 1],
      [2, 1],
    ],
  },
  engine: {
    label: WIDGET_LABELS.engine,
    group: "car",
    variants: [
      { key: "compact", label: "Water / oil", baseW: 110 },
      { key: "detailed", label: "Detailed", baseW: 130 },
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
    label: WIDGET_LABELS.aids,
    group: "car",
    variants: [{ key: "badges", label: "Badges", baseW: 130 }],
    defaultVariant: "badges",
    sizes: [
      [2, 1],
      [1, 1],
      [4, 1],
    ],
  },
  boost: {
    label: WIDGET_LABELS.boost,
    group: "driving",
    variants: [
      { key: "digits", label: "Digits" },
      { key: "gauge", label: "Gauge", baseW: 96 },
    ],
    defaultVariant: "digits",
    sizes: [
      [1, 1],
      [2, 1],
      [2, 2],
    ],
  },
  alerts: {
    label: WIDGET_LABELS.alerts,
    group: "race",
    variants: [
      { key: "banner", label: "Banner", baseW: 220 },
      { key: "list", label: "List", baseW: 160 },
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
