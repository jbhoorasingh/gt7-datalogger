import { useT } from "@/i18n";
import { api } from "@/lib/api";
import { formatDuration } from "@/lib/format";
import type { AdminStats } from "@/lib/types";
import { mb, SectionPanel, TONE_TEXT, type SectionProps, type Tone } from "./parts";

// The server also says when its car list was generated; not in AdminStats yet.
type Stats = AdminStats & { cars_generated?: string };

export function HealthSection({
  busy,
  run,
  stats,
  hz,
}: Pick<SectionProps, "busy" | "run"> & { stats: Stats | null; hz: number | null }) {
  const t = useT();
  const src = stats?.source;
  const dropped = src?.frames_dropped ?? 0;
  const decodeErrors = src?.decode_errors ?? 0;

  const groups: { title: string; rows: [string, string, Tone?][] }[] =
    stats && src
      ? [
          {
            title: t("settings.health.telemetry"),
            rows: [
              [
                t("settings.healthPanel.status"),
                src.connected ? t("settings.meta.receiving") : t("settings.meta.noData"),
                src.connected ? "good" : "bad",
              ],
              [
                t("settings.connection.console"),
                src.source === "sim" ? t("settings.health.simulator") : src.console_ip || t("settings.health.autoDiscover"),
              ],
              [t("settings.healthPanel.packets"), src.packets_received.toLocaleString()],
              [t("settings.healthPanel.rate"), hz != null ? `${hz} Hz` : "—"],
              [t("settings.healthPanel.decodeErrors"), String(decodeErrors), decodeErrors > 0 ? "warn" : undefined],
              [t("settings.healthPanel.framesDropped"), String(dropped), dropped > 0 ? "warn" : undefined],
              [t("settings.healthPanel.format"), src.packet_format ?? "A"],
            ],
          },
          {
            title: t("settings.healthPanel.server"),
            rows: [
              [t("settings.healthPanel.uptime"), formatDuration(stats.uptime_s * 1000)],
              [t("settings.healthPanel.clients"), String(stats.clients)],
              [t("settings.healthPanel.carNames"), String(stats.cars_loaded)],
              ...(stats.cars_generated
                ? ([[t("settings.healthPanel.carListFrom"), stats.cars_generated.slice(0, 10)]] as [string, string][])
                : []),
              [t("settings.health.recording"), src.recording ? t("settings.meta.on") : t("settings.meta.off")],
            ],
          },
          {
            title: t("settings.healthPanel.database"),
            rows: [
              [t("settings.healthPanel.sessions"), stats.db.sessions.toLocaleString()],
              [t("settings.healthPanel.laps"), stats.db.laps.toLocaleString()],
              [t("settings.healthPanel.size"), mb(stats.db.size_bytes)],
            ],
          },
        ]
      : [];

  // Anomalies in words, not just a coloured number.
  const notes: { tone: Tone; text: string }[] = [];
  if (src && !src.connected) {
    notes.push({
      tone: "warn",
      text:
        src.source === "sim" ? t("settings.healthPanel.simSilent") : t("settings.healthPanel.noTelemetry"),
    });
  }
  if (dropped > 0) {
    notes.push({ tone: "warn", text: t("settings.healthPanel.dropped", { count: dropped }) });
  }
  if (decodeErrors > 0) {
    notes.push({ tone: "warn", text: t("settings.healthPanel.undecoded", { count: decodeErrors }) });
  }

  return (
    <SectionPanel
      title={t("settings.section.health")}
      description={t("settings.healthPanel.desc")}
      actions={
        <>
          <button
            className="btn"
            disabled={busy !== null}
            onClick={() => void run(t("settings.healthPanel.restartLabel"), api.admin.restartSource)}
          >
            {t("settings.healthPanel.restart")}
          </button>
          <button
            className="btn"
            disabled={busy !== null}
            onClick={() =>
              void run(t("settings.healthPanel.carsLabel"), api.admin.updateCars, (r) => {
                const res = r as { cars: number; sessions_updated: number };
                return res.sessions_updated
                  ? t("settings.healthPanel.carsUpdatedSessions", {
                      cars: res.cars,
                      sessions: res.sessions_updated,
                    })
                  : t("settings.healthPanel.carsUpdated", { cars: res.cars });
              })
            }
          >
            {t("settings.healthPanel.updateCars")}
          </button>
        </>
      }
    >
      {stats ? (
        <div className="grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))]">
          {groups.map((g) => (
            <div key={g.title} className="px-[18px] py-3.5 shadow-[inset_-1px_0_0_var(--color-hairline)]">
              <div className="section-header mb-2">{g.title}</div>
              <div className="flex flex-col gap-1.5 font-tabular text-xs">
                {g.rows.map(([k, v, tone]) => (
                  <div key={k} className="flex justify-between gap-2">
                    <span className="text-ink-faint">{k}</span>
                    <span className={`truncate ${tone ? TONE_TEXT[tone] : "text-ink"}`}>{v}</span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="px-[18px] py-3.5 text-sm text-ink-dim">{t("common.loading")}</div>
      )}
      {notes.length > 0 && (
        <>
          <div className="rule" />
          <div className="flex flex-col gap-1 px-[18px] py-2.5">
            {notes.map((n) => (
              <div key={n.text} className={`text-[11px] ${TONE_TEXT[n.tone]}`}>
                {n.text}
              </div>
            ))}
          </div>
        </>
      )}
    </SectionPanel>
  );
}
