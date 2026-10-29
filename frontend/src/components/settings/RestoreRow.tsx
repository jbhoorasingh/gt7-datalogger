import { useEffect, useRef, useState } from "react";
import { Toggle } from "@/components/ui/Toggle";
import { currentLanguage, useT, type Translate } from "@/i18n";
import { docsUrl } from "@/lib/analysisGuide";
import { api } from "@/lib/api";
import type { RestoreItem, RestoreReport, RestoreUpload } from "@/lib/types";
import { mb, SettingRow, type SectionProps } from "./parts";
import {
  defaultItems,
  hasRecordings,
  heldParts,
  machineSettings,
  restorable,
  restoreDetail,
  restoreNotes,
  restoreRequest,
  restoreSummary,
  restoreWarning,
  settingValue,
} from "./restore";

function madeOn(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString(currentLanguage(), { dateStyle: "medium" });
}

/** Which app version made the backup, and when, as far as its manifest says. */
function made(t: Translate, up: RestoreUpload): string {
  const version = up.app_version;
  const date = madeOn(up.created_at);
  if (version && date) return t("settings.data.madeByOn", { version, date });
  if (version) return t("settings.data.madeBy", { version });
  return date ? t("settings.data.madeOn", { date }) : "";
}

// Settings › Data › Restore (#161): upload a backup, tick the parts to put
// back, apply. The server checks the file and says what each part holds and
// would do here; only the ticked parts change anything.
export function RestoreRow({
  busy,
  run,
  onRestored,
}: Pick<SectionProps, "busy" | "run"> & { onRestored: () => void }) {
  const t = useT();
  const UPLOAD = t("settings.data.restoreUpload");
  const RESTORE = t("settings.data.restore");
  const [upload, setUpload] = useState<RestoreUpload | null>(null);
  const [selected, setSelected] = useState<ReadonlySet<RestoreItem>>(new Set());
  const [machine, setMachine] = useState<ReadonlySet<string>>(new Set());
  const [confirming, setConfirming] = useState(false);
  const [notes, setNotes] = useState<string[]>([]);
  const fileInput = useRef<HTMLInputElement>(null);

  function show(up: RestoreUpload | null) {
    setUpload(up);
    setSelected(new Set(up ? defaultItems(up) : []));
    setMachine(new Set());
    setConfirming(false);
  }

  // An upload made before this page was opened is still waiting on the server.
  useEffect(() => {
    let live = true;
    api.admin
      .restoreWaiting()
      .then((up) => live && show(up))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);

  function toggle<T>(set: (f: (cur: ReadonlySet<T>) => ReadonlySet<T>) => void, id: T, on: boolean) {
    set((cur) => {
      const next = new Set(cur);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
    setConfirming(false);
  }

  function pick(file: File | undefined) {
    if (!file) return;
    setNotes([]);
    void run(
      UPLOAD,
      async () => {
        const up = await api.admin.uploadRestore(file);
        show(up);
        return up;
      },
      (up) => t("settings.data.restorePick", { name: (up as RestoreUpload).name }),
    );
  }

  const request = upload ? restoreRequest(selected, machine, upload) : null;
  const replaces = !!upload && request!.items.includes("data") && hasRecordings(upload);

  function apply() {
    if (!upload || !request) return;
    if (replaces && !confirming) {
      setConfirming(true);
      return;
    }
    void run(
      RESTORE,
      async () => {
        const report = await api.admin.applyRestore(upload.id, request.items, request.machineSettings);
        show(null);
        setNotes(restoreNotes(report));
        onRestored();
        return report;
      },
      (report) => restoreSummary(report as RestoreReport),
    );
  }

  function forget() {
    if (!upload) return;
    void api.admin.forgetRestore(upload.id).catch(() => {});
    show(null);
  }

  const locked = busy !== null;

  return (
    <SettingRow
      label={RESTORE}
      help={
        <>
          {t("settings.data.restoreHelp")}{" "}
          <a
            className="text-accent hover:underline"
            href={docsUrl("guide/settings/#putting-it-back")}
            target="_blank"
            rel="noreferrer"
          >
            {t("settings.data.restoreParts")}
          </a>
        </>
      }
    >
      <div className="flex flex-col gap-2.5">
        <input
          ref={fileInput}
          type="file"
          accept=".zip,application/zip"
          className="hidden"
          aria-label={t("settings.data.restoreFile")}
          onChange={(e) => {
            pick(e.target.files?.[0]);
            e.target.value = "";
          }}
        />
        {!upload && (
          <div>
            <button className="btn" disabled={locked} onClick={() => fileInput.current?.click()}>
              {busy === UPLOAD ? t("settings.data.uploading") : t("settings.data.chooseBackup")}
            </button>
          </div>
        )}

        {upload && (
          <>
            <div className="font-tabular text-[11px] text-ink-dim">
              {[upload.name, mb(upload.bytes), made(t, upload)].filter(Boolean).join(" · ")}
            </div>
            {heldParts(upload).map((p) => {
              const can = restorable(p.id, upload);
              const on = can && selected.has(p.id);
              const refused = upload.parts[p.id].refused;
              const warning = on ? restoreWarning(p.id, upload) : "";
              const machineRows = p.id === "config" && on ? machineSettings(upload) : [];
              return (
                <div key={p.id} className="flex items-start gap-2.5">
                  <span className="mt-0.5">
                    <Toggle
                      checked={on}
                      disabled={!can || locked}
                      onCheckedChange={(v) => toggle(setSelected, p.id, v)}
                      ariaLabel={t("settings.data.restorePart", { part: t(p.label) })}
                    />
                  </span>
                  <div className="min-w-0">
                    <div className={`text-[12.5px] ${can ? "" : "text-ink-faint"}`}>{t(p.label)}</div>
                    <div className="font-tabular text-[11px] text-ink-faint">{restoreDetail(p.id, upload)}</div>
                    {refused && <div className="mt-0.5 text-[11px] text-warn">{refused}</div>}
                    {warning && <div className="mt-0.5 text-[11px] text-warn">{warning}</div>}
                    {machineRows.length > 0 && (
                      <div className="mt-1.5 flex flex-col gap-1.5">
                        {machineRows.map((s) => (
                          <div key={s.key} className="flex items-center gap-2">
                            <Toggle
                              checked={machine.has(s.key)}
                              disabled={locked}
                              onCheckedChange={(v) => toggle(setMachine, s.key, v)}
                              ariaLabel={t("settings.data.restorePart", { part: s.label })}
                            />
                            <span className="text-[11px] text-ink-dim">
                              {s.label}: <span className="font-tabular">{settingValue(s)}</span>
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
            <div className="mt-1 flex flex-wrap items-center gap-2">
              <button
                className={`btn ${confirming ? "btn-danger" : ""}`}
                disabled={locked || request!.items.length === 0}
                onClick={apply}
              >
                {busy === RESTORE
                  ? t("settings.data.restoring")
                  : confirming
                    ? t("settings.data.replaceAndRestore", { count: upload.here.sessions })
                    : RESTORE}
              </button>
              <button className="btn" disabled={locked} onClick={confirming ? () => setConfirming(false) : forget}>
                {confirming ? t("common.cancel") : t("settings.data.forget")}
              </button>
            </div>
          </>
        )}

        {notes.length > 0 && (
          <ul className="flex flex-col gap-0.5 text-[11px] text-ink-dim">
            {notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        )}
      </div>
    </SettingRow>
  );
}
