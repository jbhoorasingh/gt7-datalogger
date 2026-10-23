import { useEffect, useRef, useState } from "react";
import { Toggle } from "@/components/ui/Toggle";
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

const UPLOAD = "Upload backup";
const RESTORE = "Restore";

function madeOn(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString(undefined, { dateStyle: "medium" });
}

// Settings › Data › Restore (#161): upload a backup, tick the parts to put
// back, apply. The server checks the file and says what each part holds and
// would do here; only the ticked parts change anything.
export function RestoreRow({
  busy,
  run,
  onRestored,
}: Pick<SectionProps, "busy" | "run"> & { onRestored: () => void }) {
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
      (up) => `${(up as RestoreUpload).name}: pick the parts to restore`,
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
      label="Restore"
      help={
        <>
          Put a backup back: upload the file, then tick what to restore.{" "}
          <a
            className="text-accent hover:underline"
            href={docsUrl("guide/settings/#putting-it-back")}
            target="_blank"
            rel="noreferrer"
          >
            What each part does
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
          aria-label="Backup file to restore"
          onChange={(e) => {
            pick(e.target.files?.[0]);
            e.target.value = "";
          }}
        />
        {!upload && (
          <div>
            <button className="btn" disabled={locked} onClick={() => fileInput.current?.click()}>
              {busy === UPLOAD ? "Uploading…" : "Choose backup…"}
            </button>
          </div>
        )}

        {upload && (
          <>
            <div className="font-tabular text-[11px] text-ink-dim">
              {upload.name} · {mb(upload.bytes)}
              {upload.app_version && ` · made by ${upload.app_version}`}
              {madeOn(upload.created_at) && ` on ${madeOn(upload.created_at)}`}
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
                      ariaLabel={`Restore ${p.label}`}
                    />
                  </span>
                  <div className="min-w-0">
                    <div className={`text-[12.5px] ${can ? "" : "text-ink-faint"}`}>{p.label}</div>
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
                              ariaLabel={`Restore ${s.label}`}
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
                  ? "Restoring…"
                  : confirming
                    ? `Replace ${upload.here.sessions.toLocaleString()} sessions and restore`
                    : "Restore"}
              </button>
              <button className="btn" disabled={locked} onClick={confirming ? () => setConfirming(false) : forget}>
                {confirming ? "Cancel" : "Forget this file"}
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
