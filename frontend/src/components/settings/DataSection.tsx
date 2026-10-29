import { useEffect, useState } from "react";
import { Toggle } from "@/components/ui/Toggle";
import { useT } from "@/i18n";
import { docsUrl } from "@/lib/analysisGuide";
import { api } from "@/lib/api";
import type { AdminStats, BackupInventory, BackupPart, PreparedBackup } from "@/lib/types";
import { BACKUP_PARTS, chosenParts, DEFAULT_PARTS, makeBackup, partDetail, tokensWarning } from "./backup";
import { INPUT_BASE, mb, SectionPanel, SettingRow, type SectionProps } from "./parts";
import { RestoreRow } from "./RestoreRow";

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
  onRestored,
}: Pick<SectionProps, "busy" | "run"> & {
  stats: AdminStats | null;
  /** A restore may have changed settings, sync and layouts: the view reloads them. */
  onRestored: () => void;
}) {
  const t = useT();
  const BACKUP = t("settings.data.backup");
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
      title={t("settings.section.data")}
      description={
        stats
          ? t("settings.data.desc", {
              counts: `${t("count.sessions", { count: stats.db.sessions })} · ${t("count.laps", { count: stats.db.laps })}`,
              size: mb(stats.db.size_bytes),
            })
          : t("settings.data.descEmpty")
      }
    >
      <SettingRow
        label={BACKUP}
        help={
          <>
            {t("settings.data.backupHelp")}{" "}
            <a
              className="text-accent hover:underline"
              href={docsUrl("guide/settings/#back-up-and-move")}
              target="_blank"
              rel="noreferrer"
            >
              {t("settings.data.restoreLink")}
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
                    ariaLabel={t("settings.data.include", { part: t(p.label) })}
                  />
                </span>
                <div className="min-w-0">
                  <div className={`text-[12.5px] ${available ? "" : "text-ink-faint"}`}>{t(p.label)}</div>
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
                  (made) => t("settings.data.backupReady", { name: (made as PreparedBackup).name }),
                )
              }
            >
              {busy === BACKUP ? t("settings.data.making") : t("settings.data.download")}
            </button>
          </div>
        </div>
      </SettingRow>

      <RestoreRow busy={busy} run={run} onRestored={onRestored} />

      {/* Plain links: the server streams each ZIP as it is written, so the
          browser's own download UI shows progress on a large database. */}
      <SettingRow
        label={t("settings.data.export")}
        help={
          <>
            {t("settings.data.exportHelp")}{" "}
            <a
              className="text-accent hover:underline"
              href={docsUrl("guide/settings/#sharing-your-data")}
              target="_blank"
              rel="noreferrer"
            >
              {t("settings.data.whatToSend")}
            </a>
          </>
        }
      >
        <div className="flex flex-wrap gap-2">
          <a
            className="btn no-underline"
            href={api.allLapsZipUrl}
            download
            title={t("settings.data.jsonTitle")}
          >
            {t("settings.data.json")}
          </a>
          {/* Not "for MoTeC": i2 imports its own .ld logs, not CSV, without an add-on. */}
          <a
            className="btn no-underline"
            href={api.allLapsCsvZipUrl}
            download
            title={t("settings.data.csvTitle")}
          >
            {t("settings.data.csv")}
          </a>
        </div>
      </SettingRow>

      <SettingRow label={t("settings.data.compact")} help={t("settings.data.compactHelp")}>
        <div className="flex items-center gap-2.5">
          <button
            className="btn"
            disabled={busy !== null}
            onClick={() => void run(t("settings.data.vacuum"), api.admin.vacuum, () => t("settings.data.compacted"))}
          >
            {t("settings.data.compactButton")}
          </button>
          {/* Under a megabyte is not worth a sentence. */}
          {reclaimable >= 1048576 && (
            <span className="font-tabular text-[11px] text-ink-faint">
              {t("settings.data.reclaimable", { mb: Math.round(reclaimable / 1048576) })}
            </span>
          )}
        </div>
      </SettingRow>

      <div className="mx-[18px] mb-[18px] mt-3.5 rounded-md border border-brake/35 p-3.5">
        <div className="text-[13px] text-brake">{t("settings.data.deleteTitle")}</div>
        <div className="mt-0.5 text-[11px] text-ink-dim">{t("settings.data.deleteHelp")}</div>
        <div className="mt-2.5 flex flex-wrap gap-2">
          <input
            aria-label={t("settings.data.confirmLabel")}
            autoComplete="off"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            placeholder={t("settings.data.confirmPlaceholder")}
            className={`${INPUT_BASE} w-[200px] max-w-full text-xs`}
          />
          <button
            className="btn btn-danger"
            disabled={busy !== null || confirm !== "DELETE"}
            onClick={() =>
              void run(t("settings.data.clear"), api.admin.clearData, () => t("settings.data.cleared")).then(() =>
                setConfirm(""),
              )
            }
          >
            {laps != null ? t("settings.data.deleteLaps", { count: laps }) : t("settings.data.deleteAll")}
          </button>
        </div>
      </div>
    </SectionPanel>
  );
}
