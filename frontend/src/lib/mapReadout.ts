// The race line map's hover readout (#159): every lap the map shows, read
// where the pointer meets the reference line, laid out as the chart tooltip
// lays it out — the time diff first, then one row per chart panel, one
// column per lap — so the two read alike.
//
// Every lap is resampled on the reference's distance grid (CompareLapEntry),
// so each is read at its sample nearest the distance, as the charts' own
// playhead readouts are. Nothing is interpolated between samples.

import { channelValues, formatValueFor, readoutLines, type ChannelDef } from "@/lib/channels";
import type { Units } from "@/lib/format";
import { suggestionValues } from "@/lib/gearChoice";
import { nearestIndex } from "@/lib/playback";
import type { CompareLapEntry } from "@/lib/types";

export interface ReadoutRow {
  key: string;
  label: string;
  /** One formatted value per lap, in the order given; null where it has none. */
  cells: (string | null)[];
}

// The time diff leads, as its panel leads the charts.
const DELTA_LINE = { key: "delta", titleKey: "channel.delta.title" as const };

function valueAt(
  dist: number[] | undefined,
  values: (number | null)[] | null | undefined,
  at: number,
): number | null {
  if (!dist || !values || dist.length === 0) return null;
  const v = values[nearestIndex(dist, at)];
  return v != null && Number.isFinite(v) ? v : null;
}

/** The readout for `laps` at `dist` metres along the reference. A row no lap
 *  has a value for is left out, as the chart tooltip leaves it out. */
export function mapReadout(
  laps: CompareLapEntry[],
  channels: ChannelDef[],
  dist: number,
  units: Units,
): ReadoutRow[] {
  const fmt = formatValueFor(units);
  const byKey = new Map(channels.map((def) => [def.key, def]));
  const read = (lap: CompareLapEntry, key: string): number | null => {
    if (key === "delta") {
      // The reference has no delta of its own.
      const ms = valueAt(lap.delta?.dist, lap.delta?.delta_ms, dist);
      return ms == null ? null : ms / 1000;
    }
    if (key === "suggested_gear") return valueAt(lap.series.dist, suggestionValues(lap.series), dist);
    const def = byKey.get(key);
    if (!def) return null;
    const v = valueAt(lap.series.dist, channelValues(def, lap.series), dist);
    return v != null && def.transform ? def.transform(v, units) : v;
  };
  return readoutLines([DELTA_LINE, ...channels])
    .map(({ key, label }) => ({
      key,
      label,
      cells: laps.map((lap) => {
        const v = read(lap, key);
        return v == null ? null : fmt(key, v);
      }),
    }))
    .filter((row) => row.cells.some((cell) => cell != null));
}
