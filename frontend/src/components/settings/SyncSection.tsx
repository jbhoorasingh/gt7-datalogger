// Sync (#79). The hosted service is the default, so the one thing asked for
// is its token; your own server's address sits behind "Use your own server".
// The server only ever hands back a hint of the token.
// Connect / Disconnect / Test / push act at once; the master switch and the
// per-type toggles are ordinary buffered settings. Each data type the server
// advertises is off until turned on — enabling sync enables nothing by itself.

import { useState } from "react";
import { ConfirmDialog } from "@/components/ui/Dialog";
import { Toggle } from "@/components/ui/Toggle";
import { api } from "@/lib/api";
import type { SyncStatus, SyncTypeStatus } from "@/lib/types";
import { toast } from "@/store/toasts";
import { Dot, INPUT_CLS, SectionPanel, type SectionProps } from "./parts";

const SYNC_STATE_LABEL: Record<SyncTypeStatus["state"], string> = {
  off: "off",
  idle: "idle",
  syncing: "syncing…",
  connected: "connected",
  error: "error",
  unsupported: "not in this logger version",
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
const SYNC_PUSH_LABEL: Record<string, string> = {
  tracks: "Sync now",
  sessions: "Flush now",
  live: "Reconnect",
};

// The hosted service: what a blank address means to the server
// (app/sync/connection.py DEFAULT_URL).
const HOSTED_URL = "https://sync.gt7-datalogger.com";

const STEPS = [
  { title: "Sign in at sync.gt7-datalogger.com", hint: "Profile → Tokens → Create token. It is shown once." },
  { title: "Paste the token below", hint: "It is tested straight away." },
  { title: "Choose what to send", hint: "Every data type starts off." },
];

function inWords(seconds: number): string {
  if (seconds < 90) return `${seconds} s`;
  return `${Math.round(seconds / 60)} min`;
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
  if (s < 90) return `${s} s ago`;
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  return whenShort(iso);
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
  const [token, setToken] = useState("");
  const [ownUrl, setOwnUrl] = useState("");
  const [ownToken, setOwnToken] = useState("");
  const [replacing, setReplacing] = useState(false);
  const [forgetting, setForgetting] = useState(false);

  const hasToken = saved.sync_token_set;
  const setup = !hasToken || replacing;
  const error = hasToken ? status?.capabilities_error ?? "" : "";
  const queued = syncQueued(status);

  async function test(label = "Test connection") {
    setBusy(label);
    try {
      const st = await api.admin.syncTest();
      setStatus(st);
      const offered = Object.keys(st.capabilities?.types ?? {});
      toast(
        `Connected to ${st.capabilities?.server || st.url}` +
          (offered.length ? ` — accepts ${offered.join(", ")}` : " — accepts no data types"),
        "success",
      );
    } catch (e) {
      reload();
      toast(e instanceof Error ? e.message : "Sync test failed", "error");
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
    if (!(await applyNow(patch, "Sync token"))) return;
    setToken("");
    setReplacing(false);
    await test("Sync token");
  }

  // Your own server: its address (or a connection string carrying both),
  // and the token when it is not already in the string.
  async function connectOwn() {
    const address = ownUrl.trim();
    if (!address) return;
    const text = ownToken.trim();
    const patch = text ? { sync_url: address, sync_token: text } : { sync_url: address };
    const s = await applyNow(patch, "Sync server");
    if (!s) return;
    setOwnUrl("");
    setOwnToken("");
    if (!s.sync_token_set) {
      toast(`Sync server set to ${s.sync_url} — now add its token`, "success");
      reload();
      return;
    }
    setReplacing(false);
    await test("Sync server");
  }

  async function push(name: string) {
    const label = SYNC_PUSH_LABEL[name] ?? "Sync now";
    setBusy(label);
    try {
      setStatus(await api.admin.syncPush(name));
      toast(
        name === "tracks"
          ? "Every eligible bundle queued"
          : name === "sessions"
            ? "Queued laps sending now"
            : "Live stream reconnecting on the next packet",
        "success",
      );
    } catch (e) {
      toast(e instanceof Error ? e.message : "Sync push failed", "error");
    } finally {
      setBusy(null);
    }
  }

  const types = status ? Object.entries(status.types) : [];
  const caps = status?.capabilities;

  return (
    <SectionPanel
      title="Sync"
      description="Contribute surveys, laps and a live position to a sync service. Off by default, per type."
      actions={
        <label className="flex items-center gap-2.5 text-xs text-ink-dim">
          Sync enabled
          <Toggle
            ariaLabel="Sync enabled"
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
              {status?.checked_at ? ` (checked ${ago(status.checked_at)})` : ""}.
              {queued > 0 && ` ${queued} queued item${queued === 1 ? "" : "s"} will send once it is fixed.`}
            </span>
            {!replacing && (
              <button className="btn btn-primary" onClick={() => setReplacing(true)}>
                Paste a new token
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
                <div className={`text-[13px] ${i === 1 ? "text-ink" : "text-ink-dim"}`}>{st.title}</div>
                <div className="mt-0.5 text-[11px] text-ink-dim">{st.hint}</div>
              </div>
            </div>
          ))}
          <div className="flex gap-2 sm:pl-[34px]">
            <input
              type="password"
              aria-label="Sync token"
              autoComplete="off"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void connectHosted();
              }}
              placeholder={
                hasToken ? `stored (${saved.sync_token_hint}) — paste a new one to replace it` : "gts_…"
              }
              className={INPUT_CLS}
            />
            <button
              className="btn btn-primary shrink-0"
              disabled={busy !== null || !token.trim()}
              onClick={() => void connectHosted()}
            >
              Connect
            </button>
            {replacing && (
              <button className="btn shrink-0" onClick={() => setReplacing(false)}>
                Cancel
              </button>
            )}
          </div>
          <span className="text-[11px] text-ink-faint sm:pl-[34px]">
            Kept apart from the address, masked here, never in a URL and never in the logs.
            Pulling shared track bundles needs none of this.
          </span>
          <details
            className="text-[11px] text-ink-dim sm:pl-[34px]"
            open={saved.sync_url !== "" && saved.sync_url !== HOSTED_URL ? true : undefined}
          >
            <summary className="cursor-pointer text-ink-faint">Use your own server</summary>
            <div className="mt-2 flex flex-col gap-1.5">
              <p>
                A server on your LAN, or anywhere other than the hosted service. A host name is
                read as https; use <span className="font-tabular">http://</span> for a LAN server.
                A connection string (<span className="font-tabular">gt7sync+http://host:port/?token=…</span>)
                carries both, so the token field can stay empty.
                {saved.sync_url && (
                  <>
                    {" "}
                    Current address: <span className="font-tabular text-ink">{saved.sync_url}</span>.
                  </>
                )}
              </p>
              <input
                aria-label="Sync server address"
                autoComplete="off"
                value={ownUrl}
                onChange={(e) => setOwnUrl(e.target.value)}
                placeholder="http://192.168.1.20:8787"
                className={INPUT_CLS}
              />
              <div className="flex gap-2">
                <input
                  type="password"
                  aria-label="Sync server token"
                  autoComplete="off"
                  value={ownToken}
                  onChange={(e) => setOwnToken(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void connectOwn();
                  }}
                  placeholder="token (leave empty for a connection string)"
                  className={INPUT_CLS}
                />
                <button
                  className="btn shrink-0"
                  disabled={busy !== null || !ownUrl.trim()}
                  onClick={() => void connectOwn()}
                >
                  Connect
                </button>
              </div>
              {saved.sync_url !== "" && saved.sync_url !== HOSTED_URL && (
                <p>Connecting with the token field above switches back to the hosted service.</p>
              )}
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
                {caps?.server || status?.url || saved.sync_url || "hosted service"}
                {caps?.version && <span className="text-ink-faint"> · v{caps.version}</span>}
              </div>
              <div className="font-tabular text-[11px] text-ink-dim">
                token {saved.sync_token_hint}
                {caps ? (
                  <>
                    {status?.checked_at && ` · checked ${ago(status.checked_at)}`} · accepts{" "}
                    {Object.keys(caps.types).join(", ") || "no data types"}
                  </>
                ) : (
                  " · not checked yet — test the connection to see what the server accepts"
                )}
              </div>
            </div>
            <button className="btn" disabled={busy !== null} onClick={() => void test()}>
              Test connection
            </button>
            <button className="btn" disabled={busy !== null} onClick={() => setForgetting(true)}>
              Disconnect…
            </button>
          </div>
          <div className="rule" />
          <div className="flex flex-col gap-2 px-[18px] py-3.5">
            <span className="text-xs text-ink-dim">What to send</span>
            {types.map(([name, t]) => (
              <SyncTypeRow
                key={name}
                name={name}
                t={t}
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
              <div className="text-xs text-ink-dim">
                No data types to send. Test the connection to ask the server what it accepts.
              </div>
            )}
            <details className="mt-1 text-[11px] text-ink-dim">
              <summary className="cursor-pointer text-ink-faint">How and when each type is sent</summary>
              <p className="mt-1.5 max-w-[640px] leading-normal">
                Nothing is sent for a type that is off. Tracks: a changed bundle uploads once it has
                been left alone for ten minutes, so a running survey goes up once, after it stops and
                the corner labelling that follows it. Sessions: each lap as soon as it is saved, the
                session with its first lap; laps queue while the service is away. Live: one socket
                while the car is on track, a few frames a second, never a backlog. Everything retries
                with backoff and never blocks recording. A type the server switches off is turned off
                here too, and says so.
              </p>
            </details>
          </div>
        </>
      )}

      <ConfirmDialog
        open={forgetting}
        title="Disconnect from the sync service?"
        body="The stored token is forgotten and sync stops until a new token is saved. The token cannot be shown again — the service only reveals it once, when it is created."
        confirmLabel="Disconnect"
        danger
        onConfirm={() => {
          setForgetting(false);
          void applyNow({ sync_token: "" }, "Sync token").then((s) => {
            if (s) {
              toast("Sync token forgotten", "success");
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
  t,
  on,
  master,
  busy,
  onToggle,
  push,
}: {
  name: string;
  t: SyncTypeStatus;
  on: boolean;
  master: boolean;
  busy: string | null;
  onToggle: (on: boolean) => void;
  push: (name: string) => void | Promise<void>;
}) {
  const key = SYNC_TOGGLE[name];
  const canToggle = master && !!key && t.supported && t.offered !== false;
  const pushLabel = SYNC_PUSH_LABEL[name];
  // A live stream is only held after a failure; otherwise it reconnects by itself.
  const canPush = t.active && (name !== "live" || !!t.error);
  return (
    <div
      className={`grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-3 rounded-md px-3 py-2.5 shadow-[0_0_0_1px_var(--color-hairline)] ${
        master ? "" : "opacity-50"
      }`}
    >
      <Toggle ariaLabel={`Sync ${name}`} checked={on} disabled={!canToggle} onCheckedChange={onToggle} />
      <div className="min-w-0">
        <div className="text-[13px] capitalize">{name}</div>
        <div className="line-clamp-2 text-[11px] text-ink-dim" title={t.description}>
          {t.description}
        </div>
        <SyncTypeMeta name={name} t={t} on={on} />
      </div>
      {pushLabel ? (
        <button
          className="btn"
          disabled={busy !== null || !canPush}
          title={name === "live" && t.active && !t.error ? "The stream reconnects by itself while the car is on track" : undefined}
          onClick={() => void push(name)}
        >
          {pushLabel}
        </button>
      ) : (
        <span />
      )}
    </div>
  );
}

// The counters under a type, once it is active. Each type has its own idea
// of "one upload": a bundle, a lap, a frame.
function SyncTypeMeta({ name, t, on }: { name: string; t: SyncTypeStatus; on: boolean }) {
  const line = "mt-[3px] flex flex-wrap gap-x-3 gap-y-0.5 font-tabular text-[11px]";
  if (!t.supported) return <div className={`${line} text-ink-faint`}>{SYNC_STATE_LABEL.unsupported}</div>;
  if (t.offered === false) return <div className={`${line} text-warn`}>the server does not accept this</div>;
  if (!on) return <div className={`${line} text-ink-faint`}>off — nothing is sent</div>;
  if (!t.active) {
    return (
      <div className={`${line} text-ink-faint`}>
        {t.offered === null ? "server not checked yet" : "waiting for sync to be applied"}
      </div>
    );
  }
  const due = t.due_in_s != null && t.due_in_s > 0 ? inWords(t.due_in_s) : "";
  const { live, sessions } = t;
  return (
    <div className={`${line} text-ink-faint`}>
      <span className={t.error ? "text-brake" : t.state === "syncing" ? "text-accent" : t.state === "connected" ? "text-throttle" : ""}>
        {SYNC_STATE_LABEL[t.state]}
        {t.error ? `: ${t.error}` : ""}
      </span>
      {name === "tracks" && (
        <>
          {t.tracks?.synced != null && <span>{t.tracks.synced} synced</span>}
          {(t.queued ?? 0) > 0 && <span className="text-accent">{t.queued} queued</span>}
          {t.tracks?.rejected ? <span className="text-brake">{t.tracks.rejected} rejected</span> : null}
          {t.tracks?.unconfirmed ? (
            <span className="text-warn">{t.tracks.unconfirmed} waiting for a confirmed layout</span>
          ) : null}
          <span>{t.uploads ?? 0} uploaded since start</span>
          {t.last_ok_at && <span>last {whenShort(t.last_ok_at)}</span>}
        </>
      )}
      {name === "sessions" && sessions && (
        <>
          <span>{t.uploads ?? 0} laps sent since start</span>
          {(t.queued ?? 0) > 0 && (
            <span className="text-accent">
              {t.queued} queued{due ? ` · retry in ${due}` : ""}
            </span>
          )}
          <span>{sessions.synced} sessions on server</span>
          {sessions.laps_rejected ? <span className="text-brake">{sessions.laps_rejected} laps refused</span> : null}
          {/* Lap analysis (#115): said only of a server that takes it. */}
          {sessions.analysis?.offered && sessions.analysis.synced > 0 && (
            <span title="The lap analysis of a session is sent once its drive has ended">
              {sessions.analysis.synced} lap analyses on server
            </span>
          )}
          {sessions.analysis?.rejected ? (
            <span className="text-brake">{sessions.analysis.rejected} lap analyses refused</span>
          ) : null}
          {sessions.closed ? <span className="text-warn">{sessions.closed} closed by the server</span> : null}
          {sessions.current && (
            <span
              title={
                sessions.current.remote_id
                  ? `Session ${sessions.current.local_id} is ${sessions.current.remote_id} on the server`
                  : "The session is announced with its first lap"
              }
            >
              this drive: {sessions.current.laps_synced} sent
              {sessions.current.laps_queued ? `, ${sessions.current.laps_queued} waiting` : ""}
              {sessions.current.closed ? ` · ${sessions.current.closed}` : ""}
            </span>
          )}
          {t.last_ok_at && <span>last lap {whenShort(t.last_ok_at)}</span>}
        </>
      )}
      {name === "live" && live && (
        <>
          <span>
            {live.streaming
              ? `streaming at ${live.hz} Hz`
              : live.connected
                ? "connected, waiting for the car"
                : t.error
                  ? due
                    ? `retry in ${due}`
                    : "not connected"
                  : "opens when the car is on track"}
          </span>
          {live.connected && <span>{live.spectators} watching</span>}
          <span>{live.frames} frames since start</span>
          {live.recording && <span className="text-warn">recording</span>}
          {live.spectate_url && (
            <a
              className="text-accent hover:underline"
              href={live.spectate_url}
              target="_blank"
              rel="noreferrer"
              title="The service's spectate page for this stream. Whether others may watch is your account's setting in the portal."
            >
              spectate page
            </a>
          )}
        </>
      )}
    </div>
  );
}
