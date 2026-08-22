import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { Units } from "@/lib/format";

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
}

export const useSettings = create<SettingsState>()(
  persist(
    (set) => ({
      units: "metric",
      setUnits: (units) => set({ units }),
      mapFollow: false,
      setMapFollow: (mapFollow) => set({ mapFollow }),
    }),
    { name: "gt7-settings" },
  ),
);
