import { useEffect, useState } from "react";
import { Toggle } from "@/components/ui/Toggle";
import { docsUrl } from "@/lib/analysisGuide";
import { api } from "@/lib/api";
import type { AdminStats, BackupInventory, BackupPart, PreparedBackup } from "@/lib/types";
import { BACKUP_PARTS, chosenParts, DEFAULT_PARTS, makeBackup, partDetail, tokensWarning } from "./backup";
import { INPUT_BASE, mb, SectionPanel, SettingRow, type SectionProps } from "./parts";

const BACKUP = "Back up";

// The backup is made by a gated POST and fetched by the link it answers
// with: a plain link, so the browser's own download UI shows its progress.
function download(made: PreparedBackup): void {
  const a = document.createElement("a");
  a.href = made.url;
  a.download = made.name;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

export function DataSection({
  busy,
  run,
  stats,
}: Pick<SectionProps, "busy" | "run"> & { stats: AdminStats | null }) {
  const [confirm, setConfirm] = useState("");
  const [inventory, setInventory] = useState<BackupInventory | null>(null);
  const [selected, setSelected] = useState<ReadonlySet<BackupPart>>(() => new Set(DEFAULT_PARTS));
  const laps = stats?.db.laps;
  const reclaimable = stats?.db.reclaimable_bytes ?? 0;
  const parts = chosenParts(selected, inventory);

  // Again on every stats poll: a lap is not the only thing that changes it —
  // settings applied from the pending bar and layouts saved elsewhere do too.
  // Making the backup asks once more, so the switches are never the last word.
  useEffect(() => {
    let live = true;
    api.admin
      .backupInventory()
      .then((inv) => live && setInventory(inv))
      .catch(() => live && setInventory(null));
    return () => {
      live = false;
    };
  }, [stats]);

  function toggle(id: BackupPart, on: boolean) {
    setSelected((cur) => {
      const next = new Set(cur);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  return (
    <SectionPanel
      title="Data"
      description={
        stats
          ? `${stats.db.sessions.toLocaleString()} sessions · ${stats.db.laps.toLocaleString()} laps · ${mb(stats.db.size_bytes)} on disk.`
          : "Recorded sessions and laps."
      }
    >
      <SettingRow
        label={BACKUP}
        help={
          <>
            Everything this install holds, as one file: to move it to another machine, or to keep.{" "}
            <a
              className="text-accent hover:underline"
              href={docsUrl("guide/settings/#back-up-and-move")}
              target="_blank"
              rel="noreferrer"
            >
              Putting it back
            </a>
          </>
        }
      >
        <div className="flex flex-col gap-2.5">
          {BACKUP_PARTS.map((p) => {
            const available = inventory ? inventory[p.id].available : true;
            const on = available && selected.has(p.id);
            return (
              <div key={p.id} className="flex items-start gap-2.5">
                <span className="mt-0.5">
                  <Toggle
                    checked={on}
                    disabled={!available || busy !== null}
                    onCheckedChange={(v) => toggle(p.id, v)}
                    ariaLabel={`Include ${p.label}`}
                  />
                </span>
                <div className="min-w-0">
                  <div className={`text-[12.5px] ${available ? "" : "text-ink-faint"}`}>{p.label}</div>
                  <div className="font-tabular text-[11px] text-ink-faint">{partDetail(p.id, inventory)}</div>
                  {p.id === "tokens" && on && <div className="mt-0.5 text-[11px] text-warn">{tokensWarning(inventory)}</div>}
                </div>
              </div>
            );
          })}
          <div className="mt-1">
            <button
              className="btn"
              disabled={busy !== null || parts.length === 0}
              onClick={() =>
                void run(
                  BACKUP,
                  async () => {
                    const { made, inventory: now } = await makeBackup(selected, api.admin);
                    setInventory(now);
                    download(made);
                    return made;
                  },
                  (made) => `Backup ready — downloading ${(made as PreparedBackup).name}`,
                )
              }
            >
              {busy === BACKUP ? "Making backup…" : "Download backup"}
            </button>
          </div>
        </div>
      </SettingRow>

      {/* Plain links: the server streams each ZIP as it is written, so the
          browser's own download UI shows progress on a large database. */}
      <SettingRow
        label="Export laps"
        help={
          <>
            Every lap&apos;s export, in a folder per session. Asked for your data?{" "}
            <a
              className="text-accent hover:underline"
              href={docsUrl("guide/settings/#sharing-your-data")}
              target="_blank"
              rel="noreferrer"
            >
              What to send
            </a>
          </>
        }
      >
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
