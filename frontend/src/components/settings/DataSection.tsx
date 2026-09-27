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
  const reclaimable = stats?.db.reclaimable_bytes ?? 0;

  return (
    <SectionPanel
      title="Data"
      description={
        stats
          ? `${stats.db.sessions.toLocaleString()} sessions · ${stats.db.laps.toLocaleString()} laps · ${mb(stats.db.size_bytes)} on disk.`
          : "Recorded sessions and laps."
      }
    >
      {/* Plain links: the server streams each ZIP as it is written, so the
          browser's own download UI shows progress on a large database. */}
      <SettingRow label="Back up" help="Every session and lap as one file.">
        <div className="flex flex-wrap gap-2">
          <a
            className="btn no-underline"
            href={api.allLapsZipUrl}
            download
            title="Each lap's JSON export in a folder per session — importable again one lap at a time"
          >
            Export all laps (JSON)
          </a>
          {/* Not "for MoTeC": i2 imports its own .ld logs, not CSV, without an add-on. */}
          <a
            className="btn no-underline"
            href={api.allLapsCsvZipUrl}
            download
            title="Each lap as the CSV its own export gives, in a folder per session"
          >
            CSV (all laps)
          </a>
        </div>
      </SettingRow>

      <SettingRow label="Compact database" help="Reclaims space left by deleted laps.">
        <div className="flex items-center gap-2.5">
          <button
            className="btn"
            disabled={busy !== null}
            onClick={() => void run("Vacuum", api.admin.vacuum, () => "Database compacted")}
          >
            Compact
          </button>
          {/* Under a megabyte is not worth a sentence. */}
          {reclaimable >= 1048576 && (
            <span className="font-tabular text-[11px] text-ink-faint">
              ~{Math.round(reclaimable / 1048576).toLocaleString()} MB reclaimable
            </span>
          )}
        </div>
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
