import { useState } from "react";
import { api } from "@/lib/api";
import type { AdminStats } from "@/lib/types";
import { INPUT_BASE, mb, SectionPanel, SettingRow, type SectionProps } from "./parts";

export function DataSection({
  busy,
  run,
  stats,
}: Pick<SectionProps, "busy" | "run"> & { stats: AdminStats | null }) {
  const [confirm, setConfirm] = useState("");
  const laps = stats?.db.laps;

  return (
    <SectionPanel
      title="Data"
      description={
        stats
          ? `${stats.db.sessions.toLocaleString()} sessions · ${stats.db.laps.toLocaleString()} laps · ${mb(stats.db.size_bytes)} on disk.`
          : "Recorded sessions and laps."
      }
    >
      <SettingRow label="Back up" help="Export sessions before deleting anything.">
        <div className="flex flex-wrap items-center gap-2">
          <a className="btn no-underline" href="#/sessions">
            Open Sessions
          </a>
          <span className="text-[11px] text-ink-faint">
            Each session exports as a ZIP of every lap; single laps as JSON or CSV.
          </span>
        </div>
      </SettingRow>

      <SettingRow label="Compact database" help="Reclaims space left by deleted laps.">
        <button
          className="btn"
          disabled={busy !== null}
          onClick={() => void run("Vacuum", api.admin.vacuum, () => "Database compacted")}
        >
          Compact
        </button>
      </SettingRow>

      <div className="mx-[18px] mb-[18px] mt-3.5 rounded-md border border-brake/35 p-3.5">
        <div className="text-[13px] text-brake">Delete all recorded data</div>
        <div className="mt-0.5 text-[11px] text-ink-dim">
          Every session and lap. Settings, tracks and layouts are kept. Cannot be undone.
        </div>
        <div className="mt-2.5 flex flex-wrap gap-2">
          <input
            aria-label="Type DELETE to confirm"
            autoComplete="off"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            placeholder="type DELETE to confirm"
            className={`${INPUT_BASE} w-[200px] max-w-full text-xs`}
          />
          <button
            className="btn btn-danger"
            disabled={busy !== null || confirm !== "DELETE"}
            onClick={() =>
              void run("Clear data", api.admin.clearData, () => "All sessions and laps deleted").then(() =>
                setConfirm(""),
              )
            }
          >
            {laps != null ? `Delete ${laps.toLocaleString()} lap${laps === 1 ? "" : "s"}` : "Delete everything"}
          </button>
        </div>
      </div>
    </SectionPanel>
  );
}
