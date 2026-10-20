import { create } from "zustand";
import { persist } from "zustand/middleware";
import { CHANNEL_BY_KEY } from "@/lib/channels";
import type { Units } from "@/lib/format";
import { DEFAULT_MAP_LAYERS, type MapLayerKey, type MapLayers } from "@/lib/mapLayers";
import { DEFAULT_LENS, isMapLens, type MapLens } from "@/lib/mapLens";

/** How the Analysis race line places the other laps' cars (#75). */
export type MapSync = "position" | "time";

interface SettingsState {
  units: Units;
  setUnits: (u: Units) => void;
  /**
   * Follow camera on the Analysis race line: while playback runs, frame a
   * stretch of track around the car instead of the whole circuit. Persisted
   * because it is a way of watching a lap, not a property of one lap.
   */
  mapFollow: boolean;
  setMapFollow: (v: boolean) => void;
  /**
   * Where the race line draws every lap other than the reference: where that
   * lap was at the reference's lap time ("time", which shows the gap as
   * distance on track), or level with the reference car ("position", where
   * the charts compare the laps). Time is the default: compared laps are
   * lined up by place, so position-synced dots sit on top of the reference's
   * and say little at circuit scale. Persisted like mapFollow.
   */
  mapSync: MapSync;
  setMapSync: (v: MapSync) => void;
  /**
   * What the race line draws besides the reference line: the surveyed road
   * and corner numbers, the other laps and their apex speeds and brake
   * points, and the markers — peaks, kerbs, chassis events (#103), TCS and
   * ASM (#104). One switch each in the map's Layers menu, remembered on
   * this device.
   */
  mapLayers: MapLayers;
  setMapLayer: (key: MapLayerKey, on: boolean) => void;
  /**
   * The lens the race line and the lap strip are coloured by (#102),
   * remembered on this device like the Layers. A link that names a lens
   * (lens=) replaces it, the way a link's ch= replaces the chart picker's
   * channels.
   */
  mapLens: MapLens;
  setMapLens: (v: MapLens) => void;
  /**
   * Analysis split view (#156): on a wide screen, four charts beside the
   * race line instead of below it — as many as stand as tall as the map —
   * their channels picked in their own header. The full chart stack stays
   * below either way. Remembered on this device like mapFollow.
   */
  splitView: boolean;
  setSplitView: (v: boolean) => void;
  splitChannels: string[];
  setSplitChannel: (index: number, key: string) => void;
}

export const DEFAULT_SPLIT_CHANNELS = ["speed", "throttle", "brake", "gear"];

/**
 * As many different channels this build knows as the split view shows: the
 * stored ones that still exist, topped up from the defaults — a choice saved
 * when it showed three keeps them and gains a fourth.
 */
function validSplitChannels(keys: unknown): string[] {
  const out: string[] = [];
  const stored = Array.isArray(keys) ? keys : [];
  for (const k of [...stored, ...DEFAULT_SPLIT_CHANNELS]) {
    if (out.length === DEFAULT_SPLIT_CHANNELS.length) break;
    if (typeof k === "string" && k in CHANNEL_BY_KEY && !out.includes(k)) out.push(k);
  }
  return out;
}

export const useSettings = create<SettingsState>()(
  persist(
    (set) => ({
      units: "metric",
      setUnits: (units) => set({ units }),
      mapFollow: false,
      setMapFollow: (mapFollow) => set({ mapFollow }),
      mapSync: "time",
      setMapSync: (mapSync) => set({ mapSync }),
      mapLayers: DEFAULT_MAP_LAYERS,
      setMapLayer: (key, on) => set((s) => ({ mapLayers: { ...s.mapLayers, [key]: on } })),
      mapLens: DEFAULT_LENS,
      setMapLens: (mapLens) => set({ mapLens }),
      splitView: false,
      setSplitView: (splitView) => set({ splitView }),
      splitChannels: DEFAULT_SPLIT_CHANNELS,
      setSplitChannel: (index, key) =>
        set((s) => ({ splitChannels: s.splitChannels.map((k, i) => (i === index ? key : k)) })),
    }),
    {
      name: "gt7-settings",
      // Version 1 widened mapLayers from three marker switches to the whole
      // Layers menu. The stored three were the old defaults for almost
      // everyone, so they are dropped rather than carried into the new set,
      // which has its own defaults; a layer the stored copy lacks always
      // falls back to the default either way.
      version: 1,
      migrate: (persisted) => {
        const state = { ...((persisted ?? {}) as Partial<SettingsState>) };
        delete state.mapLayers;
        return state as SettingsState;
      },
      merge: (persisted, current) => {
        const stored = (persisted ?? {}) as Partial<SettingsState>;
        return {
          ...current,
          ...stored,
          mapLayers: { ...DEFAULT_MAP_LAYERS, ...(stored.mapLayers ?? {}) },
          // A stored lens this build does not know (hand-edited, or one
          // since removed) would be drawn as Inputs but still written into
          // every link as lens=…, so it falls back to Inputs here.
          mapLens: isMapLens(stored.mapLens) ? stored.mapLens : DEFAULT_LENS,
          // A channel since renamed or removed would chart nothing.
          splitChannels: validSplitChannels(stored.splitChannels),
        };
      },
    },
  ),
);
