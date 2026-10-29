// Settings (was Admin): a rail of sections, a health strip across the top,
// and one section panel at a time, chosen by #/settings/{section}. Setting
// edits are buffered and go to the server as one PUT from the pending bar;
// actions (Test, Restart, Compact, sync Connect…) still act at once.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AccessSection, probeProtection, TokenField, type Protection } from "@/components/settings/AccessSection";
import { AdvancedSection } from "@/components/settings/AdvancedSection";
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
import { PhoneSection } from "@/components/settings/PhoneSection";
import { PendingBar } from "@/components/settings/PendingBar";
import { Dot, mb, SectionPanel, TONE_TEXT, type SectionProps, type Tone } from "@/components/settings/parts";
import { SyncSection, syncQueued } from "@/components/settings/SyncSection";
import { useT } from "@/i18n";
import { api, ApiError, type AdminSettingsPatch } from "@/lib/api";
import type { LayoutSummary } from "@/lib/layout";
import { openSettings } from "@/lib/router";
import type { AdminSettings, AdminStats, SyncStatus } from "@/lib/types";
import { useTelemetry } from "@/store/telemetry";
import { toast } from "@/store/toasts";
import { version } from "../../package.json";

const POLL_MS = 5000;

export function SettingsView({ section: sectionParam }: { section?: string | null }) {
  const t = useT();
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

  const reloadSettings = useCallback(() => {
    api.admin
      .settings()
      .then((s) => {
        setSaved(s);
        setSettingsError(null);
      })
      .catch((e) => setSettingsError(e instanceof Error ? e : new Error(t("settings.loadFailed"))));
  }, [t]);

  useEffect(() => {
    reloadSettings();
    void probeProtection().then(setProtection);
  }, [reloadSettings]);

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
      toast(t("settings.applied"), "success");
      refreshStats();
      reloadSync();
    } catch (e) {
      toast(e instanceof Error ? e.message : t("settings.applyFailed"), "error");
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
        toast(e instanceof Error ? e.message : t("settings.actionFailed", { label }), "error");
        return null;
      } finally {
        setBusy(null);
      }
    },
    [t],
  );

  const run = useCallback(
    async (label: string, fn: () => Promise<unknown>, done?: (r: unknown) => string) => {
      setBusy(label);
      try {
        const r = await fn();
        toast(done ? done(r) : t("settings.actionDone", { label }), "success");
        refreshStats();
      } catch (e) {
        toast(e instanceof Error ? e.message : t("settings.actionFailed", { label }), "error");
      } finally {
        setBusy(null);
      }
    },
    [refreshStats, t],
  );

  // --- the rail and health strip read the same facts ------------------------

  const src = stats?.source;
  const queued = syncQueued(sync);
  const syncErr = saved?.sync_token_set ? sync?.capabilities_error ?? "" : "";
  const syncTypeErr = sync ? Object.values(sync.types).some((t) => t.active && t.state === "error") : false;

  const meta: Partial<Record<SectionId, [string, Tone]>> = {};
  if (src) {
    meta.connection = src.connected
      ? [src.source === "sim" ? t("settings.meta.simulated") : t("settings.meta.receiving"), "good"]
      : [t("settings.meta.noData"), "warn"];
    const dropped = src.frames_dropped ?? 0;
    meta.health =
      dropped > 0
        ? [t("settings.meta.dropped", { n: dropped }), "warn"]
        : src.decode_errors > 0
          ? [t("settings.meta.bad", { n: src.decode_errors }), "warn"]
          : [t("settings.meta.ok"), "faint"];
  }
  if (stats) {
    const size = stats.db.size_bytes / 1048576;
    meta.data = [`${size < 10 ? size.toFixed(1) : Math.round(size)} MB`, "faint"];
  }
  if (draft && saved) {
    meta.sync = !saved.sync_token_set
      ? [t("settings.meta.notSetUp"), "faint"]
      : syncErr || syncTypeErr
        ? [t("settings.meta.error"), "bad"]
        : !draft.sync_enabled
          ? [t("settings.meta.off"), "faint"]
          : queued > 0
            ? [t("settings.meta.queued", { n: queued }), "accent"]
            : [t("settings.meta.on"), "good"];
    meta["race-engineer"] = draft.race_engineer
      ? [t("settings.meta.on"), "good"]
      : [t("settings.meta.off"), "faint"];
    meta.notifications = draft.webhook_url.trim()
      ? [t("settings.meta.events", { count: draft.webhook_events.length }), "good"]
      : [t("settings.meta.off"), "faint"];
  }
  // A server on loopback only (#151) is reachable neither way.
  if (stats)
    meta.phone = stats.mdns
      ? [t("settings.meta.announced"), "good"]
      : stats.loopback_only
        ? [t("settings.meta.unreachable"), "warn"]
        : [t("settings.meta.byCode"), "faint"];
  if (layouts) meta.overlays = [t("settings.meta.saved", { n: layouts.length }), "faint"];
  if (logErrors > 0) meta.logs = [t("settings.meta.errors", { count: logErrors }), "bad"];
  if (protection)
    meta.access =
      protection === "open"
        ? [t("settings.meta.open"), "warn"]
        : [t("settings.meta.protected"), "good"];

  const rail = filterSections(query, t);

  const health: { label: string; value: string; meta: string; tone: Tone; go: SectionId }[] = [];
  if (src) {
    health.push({
      label: t("settings.health.telemetry"),
      value: src.connected ? t("nav.status.receiving") : t("nav.status.noTelemetry"),
      meta: [
        src.source === "sim" ? t("settings.health.simulator") : src.console_ip || t("settings.health.autoDiscover"),
        t("settings.health.format", { format: src.packet_format ?? "A" }),
        src.connected && hz != null ? `${hz} Hz` : "",
      ]
        .filter(Boolean)
        .join(" · "),
      tone: src.connected ? "good" : "warn",
      go: "connection",
    });
    health.push({
      label: t("settings.health.recording"),
      value: src.recording
        ? src.session_id != null
          ? t("settings.health.recordingSession", { id: String(src.session_id) })
          : t("settings.health.recordingOn")
        : t("settings.health.paused"),
      meta: src.track_name || t("chase.noCircuit"),
      tone: src.recording ? "bad" : "faint",
      go: "health",
    });
  }
  if (saved) {
    const host = sync?.capabilities?.server || sync?.url || saved.sync_url || t("settings.health.hosted");
    const label = t("settings.section.sync");
    health.push(
      !saved.sync_token_set
        ? {
            label,
            value: t("settings.health.notSetUp"),
            meta: t("settings.health.pasteToken"),
            tone: "faint",
            go: "sync",
          }
        : syncErr
          ? {
              label,
              value: /\b40[13]\b/.test(syncErr) ? t("settings.health.tokenRejected") : t("settings.health.syncError"),
              meta: syncErr,
              tone: "bad",
              go: "sync",
            }
          : {
              label,
              value: !saved.sync_enabled
                ? t("settings.health.off")
                : queued > 0
                  ? t("settings.meta.queued", { n: queued })
                  : t("settings.health.connected"),
              meta: host,
              tone: !saved.sync_enabled ? "faint" : queued > 0 ? "accent" : "good",
              go: "sync",
            },
    );
  }
  if (stats) {
    health.push({
      label: t("settings.health.storage"),
      value: mb(stats.db.size_bytes),
      meta: `${t("count.sessions", { count: stats.db.sessions })} · ${t("count.laps", { count: stats.db.laps })}`,
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
      case "phone":
        return <PhoneSection stats={stats} busy={busy} run={run} />;
      case "health":
        return <HealthSection busy={busy} run={run} stats={stats} hz={hz} />;
      case "data":
        return (
          <DataSection
            busy={busy}
            run={run}
            stats={stats}
            onRestored={() => {
              reloadSettings();
              reloadSync();
              reloadLayouts();
            }}
          />
        );
      case "access":
        return <AccessSection protection={protection} locked={locked} />;
      case "advanced":
        // Its own buffer and Apply: a change here can be any setting, so the
        // other sections' copy is reloaded afterwards rather than patched.
        return locked ? (
          <SettingsUnavailable error={settingsError} locked={locked} />
        ) : (
          <AdvancedSection
            onApplied={() => {
              writeEpoch.current++;
              setEdits({});
              reloadSettings();
              refreshStats();
              reloadSync();
              void probeProtection().then(setProtection);
            }}
          />
        );
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
        <h2 className="text-[17px] font-medium">{t("nav.settings")}</h2>
        <span className="text-[11px] text-ink-faint">{t("settings.subtitle", { version })}</span>
        <input
          type="search"
          aria-label={t("settings.find")}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            const first = rail[0]?.items[0];
            if (e.key === "Enter" && query.trim() && first) openSettings(first.id);
          }}
          placeholder={t("settings.findPlaceholder")}
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
        <nav aria-label={t("settings.sections")} className="flex flex-col gap-3.5 md:sticky md:top-3">
          {rail.map((g) => (
            <div key={g.group} className="flex flex-col gap-0.5">
              <span className="section-header px-2.5 pb-1">{t(g.group)}</span>
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
                    <span className="flex-1">{t(it.label)}</span>
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
            <span className="px-2.5 text-[11px] text-ink-faint">
              {t("settings.noMatch", { query: query.trim() })}
            </span>
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
  const t = useT();
  if (locked) {
    return (
      <SectionPanel title={t("settings.locked.title")} description={locked}>
        <div className="flex flex-col gap-1.5 px-[18px] py-3.5">
          <TokenField label={t("settings.locked.unlock")} placeholder={t("settings.locked.placeholder")} />
          <span className="text-[11px] text-ink-dim">{t("settings.locked.note")}</span>
        </div>
      </SectionPanel>
    );
  }
  return (
    <div className="panel px-[18px] py-3.5 text-sm text-ink-dim">
      {error ? (
        <span className="text-warn">
          {error instanceof ApiError
            ? t("settings.unreachableStatus", { status: String(error.status) })
            : t("settings.unreachable")}
        </span>
      ) : (
        t("common.loading")
      )}
    </div>
  );
}
