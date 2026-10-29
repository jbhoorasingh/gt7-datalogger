// Sync (#79). The hosted service is the default, so the one thing asked for
// is its token; your own server's address sits behind "Use your own server".
// The server only ever hands back a hint of the token.
// Connect / Disconnect / Test / push act at once; the master switch and the
// per-type toggles are ordinary buffered settings. Each data type the server
// advertises is off until turned on — enabling sync enables nothing by itself.

import { useState } from "react";
import { ConfirmDialog } from "@/components/ui/Dialog";
import { Toggle } from "@/components/ui/Toggle";
import { t as translate, useT, type MessageKey } from "@/i18n";
import { api } from "@/lib/api";
import type { SyncStatus, SyncTypeStatus } from "@/lib/types";
import { toast } from "@/store/toasts";
import { Dot, INPUT_CLS, SectionPanel, type SectionProps } from "./parts";

const SYNC_STATE_LABEL: Record<SyncTypeStatus["state"], MessageKey> = {
  off: "sync.state.off",
  idle: "sync.state.idle",
  syncing: "sync.state.syncing",
  connected: "sync.state.connected",
  error: "sync.state.error",
  unsupported: "sync.state.unsupported",
};

// Which settings key carries a type's toggle. Only types this build can
// send have one; the rest render disabled with a note.
const SYNC_TOGGLE: Partial<Record<string, "sync_tracks" | "sync_sessions" | "sync_live">> = {
  tracks: "sync_tracks",
  sessions: "sync_sessions",
  live: "sync_live",
};

// What the per-type "send it now" button is called: bundles skip the settle
// window, laps skip the backoff. The live stream reconnects on its own the
// moment the car is on track; a held one is released by the same button.
const SYNC_PUSH_LABEL: Record<string, MessageKey> = {
  tracks: "sync.push.tracks",
  sessions: "sync.push.sessions",
  live: "sync.push.live",
};

// The hosted service: what a blank address means to the server
// (app/sync/connection.py DEFAULT_URL).
const HOSTED_URL = "https://sync.gt7-datalogger.com";

const STEPS: { title: MessageKey; hint: MessageKey }[] = [
  { title: "sync.step1", hint: "sync.step1.hint" },
  { title: "sync.step2", hint: "sync.step2.hint" },
  { title: "sync.step3", hint: "sync.step3.hint" },
];

function inWords(seconds: number): string {
  if (seconds < 90) return translate("sync.seconds", { n: seconds });
  return translate("sync.minutes", { n: Math.round(seconds / 60) });
}

function whenShort(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString();
}

function ago(iso: string | null | undefined): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (s < 90) return translate("engineer.age.seconds", { n: s });
  if (s < 5400) return translate("engineer.age.minutes", { n: Math.round(s / 60) });
  return whenShort(iso);
}

/** A data type's name in the interface language; an unknown one as the server says it. */
function typeName(name: string): string {
  return name === "tracks" || name === "sessions" || name === "live" ? translate(`sync.type.${name}`) : name;
}

export function syncQueued(status: SyncStatus | null): number {
  if (!status) return 0;
  return Object.values(status.types).reduce((n, t) => n + (t.active ? (t.queued ?? 0) : 0), 0);
}

export function SyncSection({
  saved,
  draft,
  edit,
  busy,
  applyNow,
  status,
  setStatus,
  reload,
  setBusy,
}: SectionProps & {
  status: SyncStatus | null;
  setStatus: (s: SyncStatus) => void;
  reload: () => void;
  setBusy: (b: string | null) => void;
}) {
  const t = useT();
  const [token, setToken] = useState("");
  const [ownUrl, setOwnUrl] = useState("");
  const [ownToken, setOwnToken] = useState("");
  const [replacing, setReplacing] = useState(false);
  const [forgetting, setForgetting] = useState(false);

  const hasToken = saved.sync_token_set;
  const setup = !hasToken || replacing;
  const error = hasToken ? status?.capabilities_error ?? "" : "";
  const queued = syncQueued(status);

  async function test(label = t("sync.test")) {
    setBusy(label);
    try {
      const st = await api.admin.syncTest();
      setStatus(st);
      const offered = Object.keys(st.capabilities?.types ?? {});
      const server = st.capabilities?.server || st.url;
      toast(
        offered.length
          ? t("sync.connectedAccepts", { server, types: offered.join(", ") })
          : t("sync.connectedNone", { server }),
        "success",
      );
    } catch (e) {
      reload();
      toast(e instanceof Error ? e.message : t("sync.testFailed"), "error");
    } finally {
      setBusy(null);
    }
  }

  // The hosted service: the token alone, with the address reset to the
  // default in the same request, so a logger once pointed at a LAN server
  // comes back to the hosted one. A whole connection string pasted here still
  // works — the server splits it.
  async function connectHosted() {
    const text = token.trim();
    if (!text) return;
    const patch = text.includes("?") ? { sync_url: text } : { sync_url: "", sync_token: text };
    if (!(await applyNow(patch, t("sync.tokenLabel")))) return;
    setToken("");
    setReplacing(false);
    await test(t("sync.tokenLabel"));
  }

  // Your own server: its address (or a connection string carrying both),
  // and the token when it is not already in the string.
  async function connectOwn() {
    const address = ownUrl.trim();
    if (!address) return;
    const text = ownToken.trim();
    const patch = text ? { sync_url: address, sync_token: text } : { sync_url: address };
    const s = await applyNow(patch, t("sync.serverLabel"));
    if (!s) return;
    setOwnUrl("");
    setOwnToken("");
    if (!s.sync_token_set) {
      toast(t("sync.serverSet", { url: s.sync_url }), "success");
      reload();
      return;
    }
    setReplacing(false);
    await test(t("sync.serverLabel"));
  }

  async function push(name: string) {
    const label = t(SYNC_PUSH_LABEL[name] ?? "sync.push.tracks");
    setBusy(label);
    try {
      setStatus(await api.admin.syncPush(name));
      toast(
        name === "tracks"
          ? t("sync.pushed.tracks")
          : name === "sessions"
            ? t("sync.pushed.sessions")
            : t("sync.pushed.live"),
        "success",
      );
    } catch (e) {
      toast(e instanceof Error ? e.message : t("sync.pushFailed"), "error");
    } finally {
      setBusy(null);
    }
  }

  const types = status ? Object.entries(status.types) : [];
  const caps = status?.capabilities;

  return (
    <SectionPanel
      title={t("settings.section.sync")}
      description={t("sync.desc")}
      actions={
        <label className="flex items-center gap-2.5 text-xs text-ink-dim">
          {t("sync.enabled")}
          <Toggle
            ariaLabel={t("sync.enabled")}
            checked={draft.sync_enabled}
            disabled={!hasToken}
            onCheckedChange={(on) => edit({ sync_enabled: on })}
          />
        </label>
      }
    >
      {error && (
        <>
          <div className="flex flex-wrap items-center gap-3 bg-brake/10 px-[18px] py-2.5">
            <span className="min-w-[220px] flex-1 text-xs text-brake">
              {error}
              {status?.checked_at ? ` ${t("sync.checkedParen", { ago: ago(status.checked_at) })}` : ""}.
              {queued > 0 && ` ${t("sync.queuedWillSend", { count: queued })}`}
            </span>
            {!replacing && (
              <button className="btn btn-primary" onClick={() => setReplacing(true)}>
                {t("sync.pasteNew")}
              </button>
            )}
          </div>
          <div className="rule" />
        </>
      )}

      {setup && (
        <div className="grid gap-3.5 px-[18px] py-4">
          {STEPS.map((st, i) => (
            <div key={st.title} className="grid grid-cols-[22px_minmax(0,1fr)] gap-3">
              <span
                className={`flex h-[22px] w-[22px] items-center justify-center rounded-full border font-tabular text-[11px] ${
                  i === 1 ? "border-accent text-ink" : "border-edge text-ink-dim"
                }`}
              >
                {i + 1}
              </span>
              <div>
                <div className={`text-[13px] ${i === 1 ? "text-ink" : "text-ink-dim"}`}>{t(st.title)}</div>
                <div className="mt-0.5 text-[11px] text-ink-dim">{t(st.hint)}</div>
              </div>
            </div>
          ))}
          <div className="flex gap-2 sm:pl-[34px]">
            <input
              type="password"
              aria-label={t("sync.tokenLabel")}
              autoComplete="off"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void connectHosted();
              }}
              placeholder={hasToken ? t("sync.storedPlaceholder", { hint: saved.sync_token_hint }) : "gts_…"}
              className={INPUT_CLS}
            />
            <button
              className="btn btn-primary shrink-0"
              disabled={busy !== null || !token.trim()}
              onClick={() => void connectHosted()}
            >
              {t("sync.connect")}
            </button>
            {replacing && (
              <button className="btn shrink-0" onClick={() => setReplacing(false)}>
                {t("common.cancel")}
              </button>
            )}
          </div>
          <span className="text-[11px] text-ink-faint sm:pl-[34px]">{t("sync.tokenNote")}</span>
          <details
            className="text-[11px] text-ink-dim sm:pl-[34px]"
            open={saved.sync_url !== "" && saved.sync_url !== HOSTED_URL ? true : undefined}
          >
            <summary className="cursor-pointer text-ink-faint">{t("sync.own")}</summary>
            <div className="mt-2 flex flex-col gap-1.5">
              <p>
                {t("sync.ownHelp", { http: "http://", example: "gt7sync+http://host:port/?token=…" })}
                {saved.sync_url && (
                  <>
                    {" "}
                    {t("sync.currentAddress")} <span className="font-tabular text-ink">{saved.sync_url}</span>.
                  </>
                )}
              </p>
              <input
                aria-label={t("sync.ownAddress")}
                autoComplete="off"
                value={ownUrl}
                onChange={(e) => setOwnUrl(e.target.value)}
                placeholder="http://192.168.1.20:8787"
                className={INPUT_CLS}
              />
              <div className="flex gap-2">
                <input
                  type="password"
                  aria-label={t("sync.ownToken")}
                  autoComplete="off"
                  value={ownToken}
                  onChange={(e) => setOwnToken(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void connectOwn();
                  }}
                  placeholder={t("sync.ownTokenPlaceholder")}
                  className={INPUT_CLS}
                />
                <button
                  className="btn shrink-0"
                  disabled={busy !== null || !ownUrl.trim()}
                  onClick={() => void connectOwn()}
                >
                  {t("sync.connect")}
                </button>
              </div>
              {saved.sync_url !== "" && saved.sync_url !== HOSTED_URL && <p>{t("sync.switchBack")}</p>}
            </div>
          </details>
        </div>
      )}

      {hasToken && (
        <>
          {setup && <div className="rule" />}
          <div className="flex flex-wrap items-center gap-3 bg-panel-2 px-[18px] py-3">
            <Dot tone={error ? "bad" : caps ? "good" : "faint"} size={7} />
            <div className="min-w-[220px] flex-1">
              <div className="font-tabular text-[13px]">
                {caps?.server || status?.url || saved.sync_url || t("settings.health.hosted")}
                {caps?.version && <span className="text-ink-faint"> · v{caps.version}</span>}
              </div>
              <div className="font-tabular text-[11px] text-ink-dim">
                {t("sync.tokenHint", { hint: saved.sync_token_hint })}
                {caps ? (
                  <>
                    {status?.checked_at && ` · ${t("sync.checked", { ago: ago(status.checked_at) })}`} ·{" "}
                    {Object.keys(caps.types).length
                      ? t("sync.accepts", { types: Object.keys(caps.types).join(", ") })
                      : t("sync.acceptsNone")}
                  </>
                ) : (
                  ` · ${t("sync.notChecked")}`
                )}
              </div>
            </div>
            <button className="btn" disabled={busy !== null} onClick={() => void test()}>
              {t("sync.test")}
            </button>
            <button className="btn" disabled={busy !== null} onClick={() => setForgetting(true)}>
              {t("sync.disconnectEllipsis")}
            </button>
          </div>
          <div className="rule" />
          <div className="flex flex-col gap-2 px-[18px] py-3.5">
            <span className="text-xs text-ink-dim">{t("sync.whatToSend")}</span>
            {types.map(([name, type]) => (
              <SyncTypeRow
                key={name}
                name={name}
                type={type}
                on={SYNC_TOGGLE[name] ? draft[SYNC_TOGGLE[name]!] : false}
                master={draft.sync_enabled}
                busy={busy}
                onToggle={(on) => {
                  const key = SYNC_TOGGLE[name];
                  if (key) edit({ [key]: on });
                }}
                push={push}
              />
            ))}
            {status && types.length === 0 && (
              <div className="text-xs text-ink-dim">{t("sync.noTypes")}</div>
            )}
            <details className="mt-1 text-[11px] text-ink-dim">
              <summary className="cursor-pointer text-ink-faint">{t("sync.howTitle")}</summary>
              <p className="mt-1.5 max-w-[640px] leading-normal">{t("sync.how")}</p>
            </details>
          </div>
        </>
      )}

      <ConfirmDialog
        open={forgetting}
        title={t("sync.disconnectTitle")}
        body={t("sync.disconnectBody")}
        confirmLabel={t("sync.disconnect")}
        danger
        onConfirm={() => {
          setForgetting(false);
          void applyNow({ sync_token: "" }, t("sync.tokenLabel")).then((s) => {
            if (s) {
              toast(t("sync.forgotten"), "success");
              reload();
            }
          });
        }}
        onCancel={() => setForgetting(false)}
      />
    </SectionPanel>
  );
}

function SyncTypeRow({
  name,
  type,
  on,
  master,
  busy,
  onToggle,
  push,
}: {
  name: string;
  type: SyncTypeStatus;
  on: boolean;
  master: boolean;
  busy: string | null;
  onToggle: (on: boolean) => void;
  push: (name: string) => void | Promise<void>;
}) {
  const t = useT();
  const key = SYNC_TOGGLE[name];
  const canToggle = master && !!key && type.supported && type.offered !== false;
  const pushLabel = SYNC_PUSH_LABEL[name];
  // A live stream is only held after a failure; otherwise it reconnects by itself.
  const canPush = type.active && (name !== "live" || !!type.error);
  return (
    <div
      className={`grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-3 rounded-md px-3 py-2.5 shadow-[0_0_0_1px_var(--color-hairline)] ${
        master ? "" : "opacity-50"
      }`}
    >
      <Toggle
        ariaLabel={t("sync.typeToggle", { name: typeName(name) })}
        checked={on}
        disabled={!canToggle}
        onCheckedChange={onToggle}
      />
      <div className="min-w-0">
        <div className="text-[13px] capitalize">{typeName(name)}</div>
        <div className="line-clamp-2 text-[11px] text-ink-dim" title={type.description}>
          {type.description}
        </div>
        <SyncTypeMeta name={name} type={type} on={on} />
      </div>
      {pushLabel ? (
        <button
          className="btn"
          disabled={busy !== null || !canPush}
          title={name === "live" && type.active && !type.error ? t("sync.liveReconnects") : undefined}
          onClick={() => void push(name)}
        >
          {t(pushLabel)}
        </button>
      ) : (
        <span />
      )}
    </div>
  );
}

// The counters under a type, once it is active. Each type has its own idea
// of "one upload": a bundle, a lap, a frame.
function SyncTypeMeta({ name, type, on }: { name: string; type: SyncTypeStatus; on: boolean }) {
  const t = useT();
  const line = "mt-[3px] flex flex-wrap gap-x-3 gap-y-0.5 font-tabular text-[11px]";
  if (!type.supported) return <div className={`${line} text-ink-faint`}>{t(SYNC_STATE_LABEL.unsupported)}</div>;
  if (type.offered === false) return <div className={`${line} text-warn`}>{t("sync.meta.notAccepted")}</div>;
  if (!on) return <div className={`${line} text-ink-faint`}>{t("sync.meta.off")}</div>;
  if (!type.active) {
    return (
      <div className={`${line} text-ink-faint`}>
        {type.offered === null ? t("sync.meta.notChecked") : t("sync.meta.waitingApply")}
      </div>
    );
  }
  const due = type.due_in_s != null && type.due_in_s > 0 ? inWords(type.due_in_s) : "";
  const { live, sessions } = type;
  return (
    <div className={`${line} text-ink-faint`}>
      <span className={type.error ? "text-brake" : type.state === "syncing" ? "text-accent" : type.state === "connected" ? "text-throttle" : ""}>
        {t(SYNC_STATE_LABEL[type.state])}
        {type.error ? `: ${type.error}` : ""}
      </span>
      {name === "tracks" && (
        <>
          {type.tracks?.synced != null && <span>{t("sync.meta.synced", { n: type.tracks.synced })}</span>}
          {(type.queued ?? 0) > 0 && <span className="text-accent">{t("settings.meta.queued", { n: type.queued ?? 0 })}</span>}
          {type.tracks?.rejected ? (
            <span className="text-brake">{t("sync.meta.rejected", { n: type.tracks.rejected })}</span>
          ) : null}
          {type.tracks?.unconfirmed ? (
            <span className="text-warn">{t("sync.meta.unconfirmed", { n: type.tracks.unconfirmed })}</span>
          ) : null}
          <span>{t("sync.meta.uploaded", { n: type.uploads ?? 0 })}</span>
          {type.last_ok_at && <span>{t("sync.meta.last", { when: whenShort(type.last_ok_at) })}</span>}
        </>
      )}
      {name === "sessions" && sessions && (
        <>
          <span>{t("sync.meta.lapsSent", { n: type.uploads ?? 0 })}</span>
          {(type.queued ?? 0) > 0 && (
            <span className="text-accent">
              {t("settings.meta.queued", { n: type.queued ?? 0 })}
              {due ? ` · ${t("sync.meta.retryIn", { due })}` : ""}
            </span>
          )}
          <span>{t("sync.meta.sessionsOnServer", { n: sessions.synced })}</span>
          {sessions.laps_rejected ? (
            <span className="text-brake">{t("sync.meta.lapsRefused", { n: sessions.laps_rejected })}</span>
          ) : null}
          {/* Lap analysis (#115): said only of a server that takes it. */}
          {sessions.analysis?.offered && sessions.analysis.synced > 0 && (
            <span title={t("sync.meta.analysisTitle")}>
              {t("sync.meta.analyses", { n: sessions.analysis.synced })}
            </span>
          )}
          {sessions.analysis?.rejected ? (
            <span className="text-brake">{t("sync.meta.analysesRefused", { n: sessions.analysis.rejected })}</span>
          ) : null}
          {sessions.closed ? (
            <span className="text-warn">{t("sync.meta.closed", { n: sessions.closed })}</span>
          ) : null}
          {sessions.current && (
            <span
              title={
                sessions.current.remote_id
                  ? t("sync.meta.remote", {
                      local: String(sessions.current.local_id),
                      remote: String(sessions.current.remote_id),
                    })
                  : t("sync.meta.announced")
              }
            >
              {t("sync.meta.thisDrive", { n: sessions.current.laps_synced })}
              {sessions.current.laps_queued
                ? `, ${t("sync.meta.waiting", { n: sessions.current.laps_queued })}`
                : ""}
              {sessions.current.closed ? ` · ${sessions.current.closed}` : ""}
            </span>
          )}
          {type.last_ok_at && <span>{t("sync.meta.lastLap", { when: whenShort(type.last_ok_at) })}</span>}
        </>
      )}
      {name === "live" && live && (
        <>
          <span>
            {live.streaming
              ? t("sync.meta.streaming", { hz: live.hz })
              : live.connected
                ? t("sync.meta.waitingCar")
                : type.error
                  ? due
                    ? t("sync.meta.retryIn", { due })
                    : t("sync.meta.notConnected")
                  : t("sync.meta.opens")}
          </span>
          {live.connected && <span>{t("sync.meta.watching", { n: live.spectators })}</span>}
          <span>{t("sync.meta.frames", { n: live.frames })}</span>
          {live.recording && <span className="text-warn">{t("sync.meta.recording")}</span>}
          {live.spectate_url && (
            <a
              className="text-accent hover:underline"
              href={live.spectate_url}
              target="_blank"
              rel="noreferrer"
              title={t("sync.meta.spectateTitle")}
            >
              {t("sync.meta.spectate")}
            </a>
          )}
        </>
      )}
    </div>
  );
}
