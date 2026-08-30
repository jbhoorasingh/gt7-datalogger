// Admin view: connection settings (PS IP, source), diagnostics, live log
// viewer, and data management.

import { useCallback, useEffect, useRef, useState } from "react";
import { LayoutBuilder } from "@/components/LayoutBuilder";
import { ConfirmDialog } from "@/components/ui/Dialog";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { Select } from "@/components/ui/Select";
import { api, ApiError, getAdminToken, setAdminToken } from "@/lib/api";
import { formatDuration } from "@/lib/format";
import {
  CALLOUT_CATEGORIES,
  type AdminSettings,
  type AdminStats,
  type CalloutCategory,
  type LogRecord,
  type RaceEngineerDiagnostics,
  type SpokenUnits,
  type Verbosity,
  type WebhookEvent,
} from "@/lib/types";
import { useTelemetry } from "@/store/telemetry";
import { toast } from "@/store/toasts";

const LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"] as const;

// Order matches the backend's ALL_EVENTS.
const WEBHOOK_EVENTS: { value: WebhookEvent; label: string; hint: string }[] = [
  { value: "personal_best", label: "Personal bests", hint: "a lap beats your session best" },
  { value: "session_summary", label: "Session summaries", hint: "car, laps, best time, fuel used" },
  { value: "overtake", label: "Overtakes", hint: "you gain a race position" },
  { value: "position_lost", label: "Positions lost", hint: "you drop a race position" },
  { value: "off_road", label: "Off-road excursions", hint: "3+ wheels on grass/dirt — needs packet format C" },
];

const LEVEL_COLORS: Record<string, string> = {
  DEBUG: "text-ink-faint",
  INFO: "text-ink",
  WARNING: "text-warn",
  ERROR: "text-brake",
  CRITICAL: "text-brake",
};

export function AdminView() {
  const setStatus = useTelemetry((s) => s.setStatus);
  const [settings, setSettings] = useState<AdminSettings | null>(null);
  const [settingsError, setSettingsError] = useState<Error | null>(null);
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmingClear, setConfirmingClear] = useState(false);

  const flash = useCallback((text: string, error = false) => {
    toast(text, error ? "error" : "success");
  }, []);

  const refreshStats = useCallback(() => {
    api.admin.stats().then((s) => {
      setStats(s);
      setStatus(s.source);
    }).catch(() => {});
  }, [setStatus]);

  useEffect(() => {
    api.admin
      .settings()
      .then((s) => {
        setSettings(s);
        setSettingsError(null);
      })
      .catch((e) => {
        setSettingsError(e instanceof Error ? e : new Error("Could not load settings"));
        flash("Could not load settings", true);
      });
    refreshStats();
    const t = window.setInterval(refreshStats, 5000);
    return () => window.clearInterval(t);
  }, [refreshStats, flash]);

  async function apply(patch: Parameters<typeof api.admin.updateSettings>[0], label: string) {
    setBusy(label);
    try {
      setSettings(await api.admin.updateSettings(patch));
      flash(`${label} applied`);
      refreshStats();
    } catch (e) {
      flash(e instanceof Error ? e.message : `${label} failed`, true);
    } finally {
      setBusy(null);
    }
  }

  async function run(label: string, fn: () => Promise<unknown>, done?: (r: unknown) => string) {
    setBusy(label);
    try {
      const r = await fn();
      flash(done ? done(r) : `${label} done`);
      refreshStats();
    } catch (e) {
      flash(e instanceof Error ? e.message : `${label} failed`, true);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mx-auto flex max-w-[1200px] flex-col gap-3">
      <h2 className="text-[17px] font-medium">Admin</h2>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {/* Connection settings */}
        <Panel title="Connection" subtitle="how telemetry reaches the datalogger">
          {settings ? (
            <ConnectionForm settings={settings} busy={busy} onApply={apply} />
          ) : settingsError instanceof ApiError &&
            (settingsError.status === 401 || settingsError.status === 403) ? (
            <TokenForm error={settingsError.message} />
          ) : settingsError ? (
            <div className="p-4 text-sm text-warn">
              Backend unreachable — could not load settings
              {settingsError instanceof ApiError ? ` (HTTP ${settingsError.status})` : ""}.
            </div>
          ) : (
            <div className="p-4 text-sm text-ink-dim">Loading…</div>
          )}
        </Panel>

        {/* Diagnostics */}
        <Panel title="Diagnostics" subtitle="live health — refreshes every 5 s">
          {stats ? (
            <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 px-4 py-3.5 font-tabular text-[11.5px]">
              <Stat k="Telemetry" v={stats.source.connected ? "connected" : "no data"}
                cls={stats.source.connected ? "text-throttle" : "text-brake"} />
              <Stat k="Console" v={stats.source.console_ip || "auto-discover"} />
              <Stat k="Packets received" v={stats.source.packets_received.toLocaleString()} />
              <Stat k="Decode errors" v={String(stats.source.decode_errors)}
                cls={stats.source.decode_errors > 0 ? "text-warn" : undefined} />
              <Stat k="Packet format" v={stats.source.packet_format ?? "A"} />
              <Stat k="Frames dropped" v={String(stats.source.frames_dropped ?? 0)}
                cls={(stats.source.frames_dropped ?? 0) > 0 ? "text-warn" : undefined} />
              <Stat k="Server uptime" v={formatDuration(stats.uptime_s * 1000)} />
              <Stat k="Live clients" v={String(stats.clients)} />
              <Stat k="Sessions / laps" v={`${stats.db.sessions} / ${stats.db.laps}`} />
              <Stat k="Database size" v={`${(stats.db.size_bytes / 1048576).toFixed(1)} MB`} />
              <Stat k="Car names loaded" v={String(stats.cars_loaded)} />
              <Stat k="Recording" v={stats.source.recording ? "on" : "off"} />
            </div>
          ) : (
            <div className="p-4 text-sm text-ink-dim">Loading…</div>
          )}
          <div className="rule" />
          <div className="flex flex-wrap gap-2 px-4 py-3">
            <button
              className="btn"
              disabled={busy !== null}
              onClick={() => run("Restart source", api.admin.restartSource)}
            >
              Restart telemetry source
            </button>
            <button
              className="btn"
              disabled={busy !== null}
              onClick={() =>
                run("Car DB update", api.admin.updateCars, (r) => {
                  const res = r as { cars: number; sessions_updated: number };
                  const filled = res.sessions_updated
                    ? `, ${res.sessions_updated} session(s) updated`
                    : "";
                  return `Car database updated: ${res.cars} cars${filled}`;
                })
              }
            >
              Update car database
            </button>
          </div>
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {/* Webhook notifications */}
        <Panel title="Notifications" subtitle="webhook pings for race events">
          {settings ? (
            <WebhookForm settings={settings} busy={busy} onApply={apply} flash={flash} setBusy={setBusy} />
          ) : (
            <div className="p-4 text-sm text-ink-dim">Loading…</div>
          )}
        </Panel>

        {/* Race Engineer voice callouts */}
        <Panel title="Race Engineer" subtitle="what the voice callouts may say">
          {settings ? (
            <RaceEngineerForm settings={settings} busy={busy} onApply={apply} flash={flash} />
          ) : (
            <div className="p-4 text-sm text-ink-dim">Loading…</div>
          )}
        </Panel>
      </div>

      {/* Overlay & dashboard layout builder */}
      <Panel
        title="Overlay & dashboard builder"
        subtitle="design OBS overlays and driver dashboards"
      >
        <LayoutBuilder flash={flash} />
      </Panel>

      {/* Logs */}
      <Panel title="Logs" subtitle="live server log">
        <LogViewer />
      </Panel>

      {/* Data management */}
      <Panel title="Data management" subtitle="recorded sessions and laps">
        <div className="flex flex-wrap items-center gap-2 px-4 py-3">
          <button
            className="btn"
            disabled={busy !== null}
            onClick={() => run("Vacuum", api.admin.vacuum, () => "Database compacted")}
          >
            Compact database
          </button>
          <button
            className="btn btn-danger"
            disabled={busy !== null}
            onClick={() => setConfirmingClear(true)}
          >
            Delete all recorded data
          </button>
          <span className="text-[10.5px] text-ink-faint">
            Settings are kept. Export laps you want to keep first (Sessions view).
          </span>
        </div>
      </Panel>

      <ConfirmDialog
        open={confirmingClear}
        title="Delete ALL recorded data?"
        body="Every session and lap will be deleted. This cannot be undone — export laps you want to keep first."
        confirmLabel="Delete everything"
        danger
        onConfirm={() => {
          setConfirmingClear(false);
          run("Clear data", api.admin.clearData, () => "All sessions and laps deleted");
        }}
        onCancel={() => setConfirmingClear(false)}
      />
    </div>
  );
}

function TokenForm({ error }: { error: string }) {
  const [token, setToken] = useState(getAdminToken());
  return (
    <div className="space-y-2 p-4">
      <p className="text-sm text-warn">{error}</p>
      <label className="block text-xs text-ink-dim" htmlFor="admin-token">
        Admin token (the server&apos;s GT7_ADMIN_TOKEN)
      </label>
      <div className="flex gap-2">
        <input
          id="admin-token"
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          className="w-full rounded-md border border-edge bg-panel-2 px-3 py-1.5 font-tabular text-sm focus:border-accent focus:outline-none"
        />
        <button
          className="btn shrink-0"
          onClick={() => {
            setAdminToken(token.trim());
            window.location.reload();
          }}
        >
          Save
        </button>
      </div>
      <p className="text-[11px] text-ink-dim">
        Stored in this browser only. Live/overlay pages work without it.
      </p>
    </div>
  );
}

function ConnectionForm({
  settings,
  busy,
  onApply,
}: {
  settings: AdminSettings;
  busy: string | null;
  onApply: (patch: Parameters<typeof api.admin.updateSettings>[0], label: string) => void;
}) {
  const [ip, setIp] = useState(settings.ps_ip);
  const [token, setToken] = useState(getAdminToken());
  useEffect(() => setIp(settings.ps_ip), [settings.ps_ip]);

  return (
    <div className="space-y-4 p-4">
      <div>
        <label className="mb-1 block text-xs text-ink-dim" htmlFor="ps-ip">
          PlayStation IP address
        </label>
        <div className="flex gap-2">
          <input
            id="ps-ip"
            value={ip}
            onChange={(e) => setIp(e.target.value)}
            placeholder="e.g. 192.168.1.30 — empty = auto-discover"
            className="w-full rounded-md border border-edge bg-panel-2 px-3 py-1.5 font-tabular text-sm placeholder:text-ink-ghost focus:border-accent focus:outline-none"
            onKeyDown={(e) => {
              if (e.key === "Enter" && ip !== settings.ps_ip) onApply({ ps_ip: ip }, "Console IP");
            }}
          />
          <button
            className="btn shrink-0"
            disabled={busy !== null || ip === settings.ps_ip}
            onClick={() => onApply({ ps_ip: ip }, "Console IP")}
          >
            Apply
          </button>
        </div>
        <p className="mt-1 text-[11px] text-ink-dim">
          Applied immediately — no restart needed. Heartbeat goes to port {settings.heartbeat_port},
          telemetry arrives on {settings.telemetry_port}/udp.
        </p>
      </div>

      <div className="flex items-center gap-6">
        <div>
          <span className="mb-1 block text-xs text-ink-dim">Telemetry source</span>
          <SegmentedControl
            ariaLabel="Telemetry source"
            value={settings.source}
            disabled={busy !== null}
            onValueChange={(s) => s !== settings.source && onApply({ source: s }, "Source")}
            options={[
              { value: "udp", label: "PlayStation" },
              { value: "sim", label: "Simulated" },
            ]}
          />
        </div>
        <div>
          <span className="mb-1 block text-xs text-ink-dim">Packet format</span>
          <SegmentedControl
            ariaLabel="Packet format"
            value={settings.packet_format}
            disabled={busy !== null}
            onValueChange={(f) =>
              f !== settings.packet_format &&
              onApply({ packet_format: f as AdminSettings["packet_format"] }, "Packet format")
            }
            options={[
              { value: "A", label: "A" },
              { value: "B", label: "B" },
              { value: "~", label: "~" },
              { value: "C", label: "C" },
            ]}
          />
          <p className="mt-1 text-[11px] text-ink-dim">
            C is richest (needs GT7 v1.68+); use A for older game versions.
          </p>
        </div>
        <div>
          <span className="mb-1 block text-xs text-ink-dim">Log level</span>
          <Select
            ariaLabel="Log level"
            value={settings.log_level}
            onValueChange={(l) =>
              onApply({ log_level: l as AdminSettings["log_level"] }, "Log level")
            }
            options={LOG_LEVELS.map((l) => ({ value: l, label: l }))}
            className="px-2 py-1.5 text-xs"
          />
        </div>
      </div>

      <div>
        <label className="mb-1 block text-xs text-ink-dim" htmlFor="admin-token-field">
          Admin token — only needed if the server sets GT7_ADMIN_TOKEN
        </label>
        <div className="flex gap-2">
          <input
            id="admin-token-field"
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="empty = server is open"
            className="w-full rounded-md border border-edge bg-panel-2 px-3 py-1.5 font-tabular text-sm placeholder:text-ink-ghost focus:border-accent focus:outline-none"
          />
          <button
            className="btn shrink-0"
            disabled={busy !== null || token === getAdminToken()}
            onClick={() => {
              setAdminToken(token.trim());
              window.location.reload();
            }}
          >
            Save
          </button>
        </div>
        <p className="mt-1 text-[11px] text-ink-dim">
          Stored in this browser only; sent as X-API-Key. Live/overlay pages never need it.
        </p>
      </div>
    </div>
  );
}

function WebhookForm({
  settings,
  busy,
  onApply,
  flash,
  setBusy,
}: {
  settings: AdminSettings;
  busy: string | null;
  onApply: (patch: Parameters<typeof api.admin.updateSettings>[0], label: string) => void;
  flash: (text: string, error?: boolean) => void;
  setBusy: (b: string | null) => void;
}) {
  const [url, setUrl] = useState(settings.webhook_url);
  useEffect(() => setUrl(settings.webhook_url), [settings.webhook_url]);

  function toggleEvent(ev: WebhookEvent, on: boolean) {
    const next = WEBHOOK_EVENTS.map((e) => e.value).filter((e) =>
      e === ev ? on : settings.webhook_events.includes(e),
    );
    onApply({ webhook_events: next }, "Notification events");
  }

  return (
    <div className="space-y-3 p-4">
      <div>
        <label className="mb-1 block text-xs text-ink-dim" htmlFor="webhook-url">
          Webhook URL — where notifications are sent
        </label>
        <div className="flex gap-2">
          <input
            id="webhook-url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://discord.com/api/webhooks/… (or any HTTP endpoint)"
            className="w-full rounded-md border border-edge bg-panel-2 px-3 py-1.5 font-tabular text-sm placeholder:text-ink-ghost focus:border-accent focus:outline-none"
          />
          <button
            className="btn shrink-0"
            disabled={busy !== null || url === settings.webhook_url}
            onClick={() => onApply({ webhook_url: url }, "Webhook")}
          >
            Apply
          </button>
          <button
            className="btn shrink-0"
            disabled={busy !== null || !settings.webhook_url}
            onClick={async () => {
              setBusy("test-webhook");
              try {
                await api.admin.testWebhook();
                flash("Test notification sent");
              } catch (e) {
                flash(e instanceof Error ? e.message : "Webhook test failed", true);
              } finally {
                setBusy(null);
              }
            }}
          >
            Test
          </button>
        </div>
        <p className="mt-1 text-[11px] text-ink-dim">
          Discord webhook URLs get a rich embed; any other URL receives plain JSON. Leave
          empty to disable all notifications.
        </p>
      </div>

      <div>
        <span className="mb-1 block text-xs text-ink-dim">Notify me when…</span>
        <div className="space-y-1">
          {WEBHOOK_EVENTS.map((ev) => (
            <label
              key={ev.value}
              className="flex cursor-pointer items-baseline gap-2 text-sm"
            >
              <input
                type="checkbox"
                className="translate-y-px accent-accent"
                checked={settings.webhook_events.includes(ev.value)}
                disabled={busy !== null || !settings.webhook_url}
                onChange={(e) => toggleEvent(ev.value, e.target.checked)}
              />
              <span>{ev.label}</span>
              <span className="text-[11px] text-ink-dim">— {ev.hint}</span>
            </label>
          ))}
        </div>
        <p className="mt-1.5 text-[11px] text-ink-dim">
          Overtake / position events only fire in races where GT7 reports live positions;
          changes must hold for ~1 s so side-by-side battles don't spam.
        </p>
      </div>
    </div>
  );
}

function RaceEngineerForm({
  settings,
  busy,
  onApply,
  flash,
}: {
  settings: AdminSettings;
  busy: string | null;
  onApply: (patch: Parameters<typeof api.admin.updateSettings>[0], label: string) => void;
  flash: (text: string, error?: boolean) => void;
}) {
  const [diag, setDiag] = useState<RaceEngineerDiagnostics | null>(null);

  useEffect(() => {
    const load = () => api.admin.raceEngineer().then(setDiag).catch(() => {});
    load();
    const t = window.setInterval(load, 5000);
    return () => window.clearInterval(t);
  }, []);

  function toggleCategory(category: CalloutCategory, on: boolean) {
    const next = CALLOUT_CATEGORIES.filter((c) =>
      c === category ? on : settings.race_engineer_categories.includes(c),
    );
    onApply({ race_engineer_categories: next }, "Callout categories");
  }

  return (
    <div className="space-y-3 p-4">
      <label className="flex cursor-pointer items-baseline gap-2 text-sm">
        <input
          type="checkbox"
          className="translate-y-px accent-accent"
          checked={settings.race_engineer}
          disabled={busy !== null}
          onChange={(e) => onApply({ race_engineer: e.target.checked }, "Race Engineer")}
        />
        <span>Generate voice callouts</span>
        <span className="text-[11px] text-ink-dim">
          — detection only runs while a browser has voice enabled
        </span>
      </label>

      <div>
        <span className="mb-1 block text-xs text-ink-dim">
          Maximum verbosity — the most any device may hear
        </span>
        <div className="flex gap-1">
          {(["minimal", "race", "coach"] as Verbosity[]).map((mode) => (
            <button
              key={mode}
              disabled={busy !== null || !settings.race_engineer}
              className={`flex-1 rounded-md border px-2 py-1 text-xs capitalize ${
                settings.race_engineer_verbosity === mode
                  ? "border-accent/60 bg-accent/10 text-ink"
                  : "border-edge bg-panel-2 text-ink-dim hover:text-ink"
              }`}
              onClick={() => onApply({ race_engineer_verbosity: mode }, "Verbosity")}
            >
              {mode}
            </button>
          ))}
        </div>
        <p className="mt-1 text-[11px] text-ink-dim">
          Each browser picks its own verbosity under this one. Lowering it here
          puts those categories out of reach for every device — a driver set to
          Coach still hears nothing the server does not produce.
        </p>
      </div>

      <div>
        <span className="mb-1 block text-xs text-ink-dim">
          Spoken units — for braking points and speeds inside callouts
        </span>
        <div className="flex gap-1">
          {(["metric", "imperial"] as SpokenUnits[]).map((unit) => (
            <button
              key={unit}
              disabled={busy !== null || !settings.race_engineer}
              className={`flex-1 rounded-md border px-2 py-1 text-xs ${
                settings.race_engineer_units === unit
                  ? "border-accent/60 bg-accent/10 text-ink"
                  : "border-edge bg-panel-2 text-ink-dim hover:text-ink"
              }`}
              onClick={() => onApply({ race_engineer_units: unit }, "Spoken units")}
            >
              {unit === "metric" ? "meters / km per hour" : "feet / miles per hour"}
            </button>
          ))}
        </div>
      </div>

      <div>
        <span className="mb-1 block text-xs text-ink-dim">Categories the server emits</span>
        <div className="grid grid-cols-3 gap-x-3">
          {CALLOUT_CATEGORIES.map((category) => (
            <label key={category} className="flex cursor-pointer items-baseline gap-1.5 text-xs">
              <input
                type="checkbox"
                className="translate-y-px accent-accent"
                checked={settings.race_engineer_categories.includes(category)}
                disabled={busy !== null || !settings.race_engineer}
                onChange={(e) => toggleCategory(category, e.target.checked)}
              />
              <span className="capitalize">{category}</span>
            </label>
          ))}
        </div>
      </div>

      {diag && (
        <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 border-t border-edge pt-3 font-tabular text-sm">
          <Stat
            k="Detection"
            v={diag.active ? "running" : diag.enabled ? "idle" : "disabled"}
            cls={diag.active ? "text-throttle" : undefined}
          />
          <Stat k="Voice-capable clients" v={String(diag.clients.length)} />
          <Stat
            k="Active speaker"
            v={
              diag.clients.find((c) => c.is_active_speaker)?.page ??
              (diag.active_client_id ? "elsewhere" : "none")
            }
          />
          <Stat k="Callouts emitted" v={String(diag.stats.emitted ?? 0)} />
          <Stat k="Suppressed (cooldown)" v={String(diag.stats.suppressed_cooldown ?? 0)} />
          <Stat k="Suppressed (duplicate)" v={String(diag.stats.suppressed_duplicate ?? 0)} />
          <Stat k="Suppressed (category)" v={String(diag.stats.suppressed_category ?? 0)} />
          <Stat
            k="Spoken acks"
            v={String(diag.acks.spoken ?? 0)}
            cls={diag.acks.spoken ? "text-throttle" : undefined}
          />
          <Stat
            k="Speech failures"
            v={String(diag.acks.speech_error ?? 0)}
            cls={diag.acks.speech_error ? "text-brake" : undefined}
          />
          <Stat k="Corners on reference lap" v={String(diag.corners)} />
          <Stat k="Laps in fuel model" v={String(diag.lap_history)} />
        </div>
      )}
      {diag?.last_ack_reason && (diag.acks.speech_error ?? 0) > 0 && (
        <div className="rounded-md border border-brake/40 bg-brake/10 p-2 text-xs text-brake">
          <span className="text-[10px] uppercase tracking-widest">Speech failing </span>
          {diag.last_ack_reason} — the browser is receiving callouts but cannot play
          them.
        </div>
      )}
      {diag?.last_callout && (
        <div className="rounded-md border border-edge bg-panel-2 p-2 text-xs">
          <span className="text-[10px] uppercase tracking-widest text-ink-dim">
            Last emitted{" "}
          </span>
          {diag.last_callout.text}
        </div>
      )}

      <button
        className="btn"
        disabled={busy !== null}
        onClick={async () => {
          try {
            await api.admin.testCallout("Race engineer test callout.");
            flash("Test callout sent to connected browsers");
          } catch (e) {
            flash(e instanceof Error ? e.message : "Test callout failed", true);
          }
        }}
      >
        Send test callout
      </button>
      <p className="text-[11px] text-ink-dim">
        Voice plays in the browser, never on the server — no audio hardware is
        needed on a Raspberry Pi or in Docker. Enable it on{" "}
        <a className="text-accent hover:underline" href="/dash" target="_blank" rel="noreferrer">
          /dash
        </a>{" "}
        or on the standalone{" "}
        <a
          className="text-accent hover:underline"
          href="/engineer"
          target="_blank"
          rel="noreferrer"
        >
          /engineer
        </a>{" "}
        page.
      </p>
    </div>
  );
}

function LogViewer() {
  const [logs, setLogs] = useState<LogRecord[]>([]);
  const [level, setLevel] = useState<string>("");
  const [paused, setPaused] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      if (paused) return;
      api.admin.logs(300, level || undefined).then((ls) => {
        if (cancelled) return;
        setLogs(ls);
        const el = scroller.current;
        if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 60) {
          requestAnimationFrame(() => el.scrollTo({ top: el.scrollHeight }));
        }
      }).catch(() => {});
    };
    load();
    const t = window.setInterval(load, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(t);
    };
  }, [level, paused]);

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 px-4 py-2">
        <Select
          ariaLabel="Log level filter"
          value={level || "all"}
          onValueChange={(v) => setLevel(v === "all" ? "" : v)}
          options={[
            { value: "all", label: "All levels" },
            ...LOG_LEVELS.map((l) => ({ value: l, label: `${l}+` })),
          ]}
          className="px-2 py-1 text-xs"
        />
        <button className="btn" onClick={() => setPaused((p) => !p)}>
          {paused ? "Resume" : "Pause"}
        </button>
        <button
          className="btn"
          onClick={() => api.admin.clearLogs().then(() => setLogs([]))}
        >
          Clear
        </button>
        <span className="ml-auto text-[11px] text-ink-dim">
          {logs.length} entries · refreshes every 2 s
        </span>
      </div>
      <div className="rule" />
      <div
        ref={scroller}
        className="h-72 overflow-y-auto px-3 py-2 font-mono text-[10.5px] leading-5"
      >
        {logs.length === 0 && <div className="p-2 text-ink-faint">No log entries.</div>}
        {logs.map((r, i) => (
          <div key={`${r.ts}-${i}`} className="flex gap-2 whitespace-pre-wrap break-all px-1 hover:bg-panel-2">
            <span className="shrink-0 text-ink-faint">{r.ts.slice(11, 19)}</span>
            <span className={`w-16 shrink-0 ${LEVEL_COLORS[r.level] ?? "text-ink"}`}>{r.level}</span>
            <span className="shrink-0 text-ink-faint">{r.logger}</span>
            <span>{r.message}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Panel({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="panel min-w-0">
      <div className="flex items-baseline gap-2 px-4 py-2.5">
        <span className="section-header">{title}</span>
        {subtitle && <span className="text-[10.5px] text-ink-faint">{subtitle}</span>}
      </div>
      <div className="rule" />
      {children}
    </div>
  );
}

function Stat({ k, v, cls }: { k: string; v: string; cls?: string }) {
  return (
    <>
      <span className="text-ink-faint">{k}</span>
      <span className={`text-right ${cls ?? ""}`}>{v}</span>
    </>
  );
}
