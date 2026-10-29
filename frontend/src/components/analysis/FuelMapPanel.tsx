// Relative fuel-map strategy table: laps/time remaining per fuel setting.

import { useEffect, useState } from "react";
import { useT } from "@/i18n";
import { api } from "@/lib/api";
import { formatDuration } from "@/lib/format";
import type { FuelMapResult } from "@/lib/types";

export function FuelMapPanel({ lapId }: { lapId: number }) {
  const t = useT();
  const [data, setData] = useState<FuelMapResult | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    api
      .fuelMap(lapId)
      .then((d) => {
        setData(d);
        setError(false);
      })
      .catch(() => setError(true));
  }, [lapId]);

  if (error) return <div className="p-3 text-xs text-ink-dim">{t("fuelMap.noData")}</div>;
  if (!data) return <div className="p-3 text-xs text-ink-dim">{t("common.loading")}</div>;
  if (data.rows.length === 0)
    return <div className="p-3 text-xs text-ink-dim">{t("fuelMap.noConsumption")}</div>;

  return (
    <div className="overflow-x-auto">
      <table className="w-full font-tabular text-xs">
        <thead>
          <tr className="text-left text-ink-dim">
            <th className="px-2 py-1 font-normal">{t("fuelMap.setting")}</th>
            <th className="px-2 py-1 font-normal">{t("fuelMap.perLap")}</th>
            <th className="px-2 py-1 font-normal">{t("fuelMap.lapsLeft")}</th>
            <th className="px-2 py-1 font-normal">{t("fuelMap.timeLeft")}</th>
            <th className="px-2 py-1 font-normal">{t("fuelMap.lapDelta")}</th>
          </tr>
        </thead>
        <tbody>
          {data.rows.map((r) => (
            <tr
              key={r.setting}
              className={r.setting === 0 ? "bg-panel-2 text-accent" : "text-ink"}
            >
              <td className="px-2 py-0.5">{r.setting > 0 ? `+${r.setting}` : r.setting}</td>
              <td className="px-2 py-0.5">{r.fuel_per_lap.toFixed(2)}</td>
              <td className="px-2 py-0.5">{r.laps_remaining.toFixed(1)}</td>
              <td className="px-2 py-0.5">{formatDuration(r.time_remaining_ms)}</td>
              <td className="px-2 py-0.5">
                {r.lap_time_delta_ms > 0 ? "+" : ""}
                {(r.lap_time_delta_ms / 1000).toFixed(2)}s
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="px-2 py-1.5 text-[10px] text-ink-dim">
        {t("fuelMap.footer", {
          level: data.fuel_level.toFixed(1),
          perLap: data.base_fuel_per_lap.toFixed(2),
        })}
      </div>
    </div>
  );
}
