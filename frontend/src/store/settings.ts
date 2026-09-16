import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { Units } from "@/lib/format";

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
    }),
    { name: "gt7-settings" },
  ),
);
