// Settings (was Admin): a rail of sections, a health strip across the top,
// and one section panel at a time, chosen by #/settings/{section}. Setting
// edits are buffered and go to the server as one PUT from the pending bar;
// actions (Test, Restart, Compact, sync Connect…) still act at once.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AccessSection, probeProtection, TokenField, type Protection } from "@/components/settings/AccessSection";
import { ConnectionSection } from "@/components/settings/ConnectionSection";
import { DataSection } from "@/components/settings/DataSection";
import { EngineerSection } from "@/components/settings/EngineerSection";
import { HealthSection } from "@/components/settings/HealthSection";
import { LogsSection } from "@/components/settings/LogsSection";
import {
  filterSections,
  mergeEdits,
  pendingKeys,
  pendingPatch,
  resolveSection,
  type SectionId,
  type SettingsEdits,
} from "@/components/settings/model";
import { NotificationsSection } from "@/components/settings/NotificationsSection";
import { OverlaysSection } from "@/components/settings/OverlaysSection";
import { PendingBar } from "@/components/settings/PendingBar";
import { Dot, mb, SectionPanel, TONE_TEXT, type SectionProps, type Tone } from "@/components/settings/parts";
import { SyncSection, syncQueued } from "@/components/settings/SyncSection";
import { api, ApiError, type AdminSettingsPatch } from "@/lib/api";
import type { LayoutSummary } from "@/lib/layout";
import { openSettings } from "@/lib/router";
import type { AdminSettings, AdminStats, SyncStatus } from "@/lib/types";
import { useTelemetry } from "@/store/telemetry";
import { toast } from "@/store/toasts";
import { version } from "../../package.json";

const POLL_MS = 5000;

export function SettingsView({ section: sectionParam }: { section?: string | null }) {
  const section = resolveSection(sectionParam);
  const setStatus = useTelemetry((s) => s.setStatus);

  const [saved, setSaved] = useState<AdminSettings | null>(null);
  const [settingsError, setSettingsError] = useState<Error | null>(null);
  const [edits, setEdits] = useState<SettingsEdits>({});
  const [applying, setApplying] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  const [stats, setStats] = useState<AdminStats | null>(null);
  const [hz, setHz] = useState<number | null>(null);
  const lastPackets = useRef<{ n: number; t: number } | null>(null);
  const [sync, setSync] = useState<SyncStatus | null>(null);
  const [layouts, setLayouts] = useState<LayoutSummary[] | null>(null);
  const [logErrors, setLogErrors] = useState(0);
  const [protection, setProtection] = useState<Protection>(null);
  // Bumped on every settings write, so a sync poll that set off before it
  // can't wind the saved copy back.
  const writeEpoch = useRef(0);

  const locked =
    settingsError instanceof ApiError && (settingsError.status === 401 || settingsError.status === 403)
      ? settingsError.message
      : null;

  useEffect(() => {
    api.admin
      .settings()
      .then((s) => {
        setSaved(s);
        setSettingsError(null);
      })
      .catch((e) => setSettingsError(e instanceof Error ? e : new Error("Could not load settings")));
    void probeProtection().then(setProtection);
  }, []);

  const refreshStats = useCallback(() => {
    api.admin
      .stats()
      .then((s) => {
        setStats(s);
        setStatus(s.source);
        // Packet rate from two samples: the console's actual send rate.
        const now = performance.now();
        const prev = lastPackets.current;
        lastPackets.current = { n: s.source.packets_received, t: now };
        if (prev && s.source.connected && now > prev.t && s.source.packets_received >= prev.n) {
          setHz(Math.round(((s.source.packets_received - prev.n) * 1000) / (now - prev.t)));
        } else if (!s.source.connected) {
          setHz(null);
        }
      })
      .catch(() => {});
    api.admin
      .logs(300, "ERROR")
      .then((ls) => setLogErrors(ls.length))
      .catch(() => {});
  }, [setStatus]);

  // The toggles read the polled status, not a stale snapshot: the server
  // flips a type off by itself on a 403, and a switch that stayed on next to
  // "the server no longer accepts this" would be a lie.
  const reloadSync = useCallback(() => {
    const epoch = writeEpoch.current;
    api.admin
      .sync()
      .then((st) => {
        setSync(st);
        if (epoch !== writeEpoch.current) return;
        setSaved((s) =>
          s && {
            ...s,
            sync_enabled: st.enabled,
            sync_tracks: st.types.tracks?.enabled ?? s.sync_tracks,
            sync_sessions: st.types.sessions?.enabled ?? s.sync_sessions,
            sync_live: st.types.live?.enabled ?? s.sync_live,
          },
        );
      })
      .catch(() => {});
  }, []);

  const reloadLayouts = useCallback(() => {
    api.layouts
      .list()
      .then(setLayouts)
      .catch(() => setLayouts([]));
  }, []);

  useEffect(() => {
    refreshStats();
    reloadSync();
    reloadLayouts();
    const t = window.setInterval(() => {
      refreshStats();
      reloadSync();
    }, POLL_MS);
    return () => window.clearInterval(t);
  }, [refreshStats, reloadSync, reloadLayouts]);

  const draft = useMemo(() => (saved ? { ...saved, ...edits } : null), [saved, edits]);
  const pending = useMemo(() => (saved ? pendingKeys(saved, edits) : []), [saved, edits]);

  const edit = useCallback(
    (patch: SettingsEdits) => {
      if (saved) setEdits((e) => mergeEdits(saved, e, patch));
    },
    [saved],
  );

  async function apply() {
    if (!saved) return;
    setApplying(true);
    writeEpoch.current++;
    try {
      const next = await api.admin.updateSettings(pendingPatch(saved, edits));
      setSaved(next);
      setEdits({});
      toast("Settings applied", "success");
      refreshStats();
      reloadSync();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Applying settings failed", "error");
    } finally {
      setApplying(false);
    }
  }

  const applyNow = useCallback(
    async (patch: AdminSettingsPatch, label: string): Promise<AdminSettings | null> => {
      setBusy(label);
      writeEpoch.current++;
      try {
        const next = await api.admin.updateSettings(patch);
        setSaved(next);
        // Edits still pending elsewhere stay; any now matching the server drop out.
        setEdits((e) => mergeEdits(next, e, {}));
        return next;
      } catch (e) {
        toast(e instanceof Error ? e.message : `${label} failed`, "error");
        return null;
      } finally {
        setBusy(null);
      }
    },
    [],
  );

  const run = useCallback(
    async (label: string, fn: () => Promise<unknown>, done?: (r: unknown) => string) => {
      setBusy(label);
      try {
        const r = await fn();
        toast(done ? done(r) : `${label} done`, "success");
        refreshStats();
      } catch (e) {
        toast(e instanceof Error ? e.message : `${label} failed`, "error");
      } finally {
        setBusy(null);
      }
    },
    [refreshStats],
  );

  // --- the rail and health strip read the same facts ------------------------

  const src = stats?.source;
  const queued = syncQueued(sync);
  const syncErr = saved?.sync_token_set ? sync?.capabilities_error ?? "" : "";
  const syncTypeErr = sync ? Object.values(sync.types).some((t) => t.active && t.state === "error") : false;

  const meta: Partial<Record<SectionId, [string, Tone]>> = {};
  if (src) {
    meta.connection = src.connected
      ? [src.source === "sim" ? "simulated" : "receiving", "good"]
      : ["no data", "warn"];
    const dropped = src.frames_dropped ?? 0;
    meta.health =
      dropped > 0
        ? [`${dropped.toLocaleString()} dropped`, "warn"]
        : src.decode_errors > 0
          ? [`${src.decode_errors} bad`, "warn"]
          : ["ok", "faint"];
  }
  if (stats) {
    const size = stats.db.size_bytes / 1048576;
    meta.data = [`${size < 10 ? size.toFixed(1) : Math.round(size)} MB`, "faint"];
  }
  if (draft && saved) {
    meta.sync = !saved.sync_token_set
      ? ["not set up", "faint"]
      : syncErr || syncTypeErr
        ? ["error", "bad"]
        : !draft.sync_enabled
          ? ["off", "faint"]
          : queued > 0
            ? [`${queued} queued`, "accent"]
            : ["on", "good"];
    meta["race-engineer"] = draft.race_engineer ? ["on", "good"] : ["off", "faint"];
    meta.notifications = draft.webhook_url.trim()
      ? [`${draft.webhook_events.length} event${draft.webhook_events.length === 1 ? "" : "s"}`, "good"]
      : ["off", "faint"];
  }
  if (layouts) meta.overlays = [`${layouts.length} saved`, "faint"];
  if (logErrors > 0) meta.logs = [`${logErrors} error${logErrors === 1 ? "" : "s"}`, "bad"];
  if (protection) meta.access = protection === "open" ? ["open", "warn"] : ["protected", "good"];

  const rail = filterSections(query);

  const health: { label: string; value: string; meta: string; tone: Tone; go: SectionId }[] = [];
  if (src) {
    health.push({
      label: "Telemetry",
      value: src.connected ? "Receiving" : "No telemetry",
      meta: [
        src.source === "sim" ? "simulator" : src.console_ip || "auto-discover",
        `format ${src.packet_format ?? "A"}`,
        src.connected && hz != null ? `${hz} Hz` : "",
      ]
        .filter(Boolean)
        .join(" · "),
      tone: src.connected ? "good" : "warn",
      go: "connection",
    });
    health.push({
      label: "Recording",
      value: src.recording ? `On${src.session_id != null ? ` · session #${src.session_id}` : ""}` : "Paused",
      meta: src.track_name || "circuit not identified yet",
      tone: src.recording ? "bad" : "faint",
      go: "health",
    });
  }
  if (saved) {
    const host = sync?.capabilities?.server || sync?.url || saved.sync_url || "hosted service";
    health.push(
      !saved.sync_token_set
        ? { label: "Sync", value: "Not set up", meta: "paste a connection string", tone: "faint", go: "sync" }
        : syncErr
          ? {
              label: "Sync",
              value: /\b40[13]\b/.test(syncErr) ? "Token rejected" : "Sync error",
              meta: syncErr,
              tone: "bad",
              go: "sync",
            }
          : {
              label: "Sync",
              value: !saved.sync_enabled ? "Off" : queued > 0 ? `${queued} queued` : "Connected",
              meta: host,
              tone: !saved.sync_enabled ? "faint" : queued > 0 ? "accent" : "good",
              go: "sync",
            },
    );
  }
  if (stats) {
    health.push({
      label: "Storage",
      value: mb(stats.db.size_bytes),
      meta: `${stats.db.sessions.toLocaleString()} sessions · ${stats.db.laps.toLocaleString()} laps`,
      tone: "faint",
      go: "data",
    });
  }

  const sectionProps: SectionProps | null =
    saved && draft ? { saved, draft, edit, busy, run, applyNow } : null;

  function renderSection() {
    switch (section) {
      case "overlays":
        return <OverlaysSection layouts={layouts} reload={reloadLayouts} />;
      case "health":
        return <HealthSection busy={busy} run={run} stats={stats} hz={hz} />;
      case "data":
        return <DataSection busy={busy} run={run} stats={stats} />;
      case "access":
        return <AccessSection protection={protection} locked={locked} />;
    }
    // The rest edit settings, so they wait for the server's copy.
    if (!sectionProps) return <SettingsUnavailable error={settingsError} locked={locked} />;
    switch (section) {
      case "connection":
        return <ConnectionSection {...sectionProps} stats={stats} hz={hz} />;
      case "sync":
        return (
          <SyncSection
            {...sectionProps}
            status={sync}
            setStatus={setSync}
            reload={reloadSync}
            setBusy={setBusy}
          />
        );
      case "race-engineer":
        return <EngineerSection {...sectionProps} />;
      case "notifications":
        return <NotificationsSection {...sectionProps} />;
      case "logs":
        return <LogsSection {...sectionProps} />;
    }
  }

  return (
    <div className={`mx-auto flex max-w-[1200px] flex-col gap-3 ${pending.length > 0 ? "pb-20" : ""}`}>
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-[17px] font-medium">Settings</h2>
        <span className="text-[11px] text-ink-faint">
          this installation · v{version} · changes apply when you press Apply
        </span>
        <input
          type="search"
          aria-label="Find a setting"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            const first = rail[0]?.items[0];
            if (e.key === "Enter" && query.trim() && first) openSettings(first.id);
          }}
          placeholder="Find a setting…  (e.g. token, webhook, format)"
          className="ml-auto w-[280px] max-w-full rounded-md border border-edge bg-panel-2 px-3 py-1.5 text-xs text-ink placeholder:text-ink-ghost focus:border-accent focus:outline-none"
        />
      </div>

      {health.length > 0 && (
        <div className="panel grid grid-cols-[repeat(auto-fit,minmax(200px,1fr))] overflow-hidden">
          {health.map((h) => (
            <button
              key={h.label}
              onClick={() => openSettings(h.go)}
              className="flex min-w-0 flex-col gap-1 px-4 py-3 text-left shadow-[inset_-1px_0_0_var(--color-hairline)] transition-colors hover:bg-panel-2"
            >
              <span className="section-header">{h.label}</span>
              <span className="flex items-center gap-2 font-tabular text-[15px] font-medium">
                <Dot tone={h.tone} size={7} />
                {h.value}
              </span>
              <span className="truncate font-tabular text-[11px] text-ink-faint" title={h.meta}>
                {h.meta}
              </span>
            </button>
          ))}
        </div>
      )}

      <div className="grid grid-cols-1 items-start gap-4 md:grid-cols-[minmax(180px,208px)_minmax(0,1fr)]">
        <nav aria-label="Settings sections" className="flex flex-col gap-3.5 md:sticky md:top-3">
          {rail.map((g) => (
            <div key={g.group} className="flex flex-col gap-0.5">
              <span className="section-header px-2.5 pb-1">{g.group}</span>
              {g.items.map((it) => {
                const active = it.id === section;
                const m = meta[it.id];
                return (
                  <button
                    key={it.id}
                    onClick={() => openSettings(it.id)}
                    aria-current={active ? "page" : undefined}
                    className={`flex items-center gap-2 rounded px-2.5 py-1.5 text-left text-[12.5px] transition-colors ${
                      active ? "bg-accent/16 text-accent-300" : "text-ink-muted hover:text-ink"
                    }`}
                  >
                    <span className="flex-1">{it.label}</span>
                    {m && (
                      <span className={`flex items-center gap-[5px] font-tabular text-[10.5px] ${TONE_TEXT[m[1]]}`}>
                        <Dot tone={m[1]} size={5} />
                        {m[0]}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          ))}
          {rail.length === 0 && (
            <span className="px-2.5 text-[11px] text-ink-faint">No setting matches “{query.trim()}”.</span>
          )}
        </nav>

        <section className="flex min-w-0 flex-col gap-3">{renderSection()}</section>
      </div>

      <PendingBar
        keys={pending}
        applying={applying}
        onDiscard={() => setEdits({})}
        onApply={() => void apply()}
      />
    </div>
  );
}

function SettingsUnavailable({ error, locked }: { error: Error | null; locked: string | null }) {
  if (locked) {
    return (
      <SectionPanel title="Settings are locked on this server" description={locked}>
        <div className="flex flex-col gap-1.5 px-[18px] py-3.5">
          <TokenField label="Unlock settings" placeholder="the server's GT7_ADMIN_TOKEN" />
          <span className="text-[11px] text-ink-dim">
            Stored in this browser only. Live, overlay and dash pages work without it.
          </span>
        </div>
      </SectionPanel>
    );
  }
  return (
    <div className="panel px-[18px] py-3.5 text-sm text-ink-dim">
      {error ? (
        <span className="text-warn">
          Backend unreachable — could not load settings
          {error instanceof ApiError ? ` (HTTP ${error.status})` : ""}.
        </span>
      ) : (
        "Loading…"
      )}
    </div>
  );
}
