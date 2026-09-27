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
  const src = stats?.source;
  const dropped = src?.frames_dropped ?? 0;
  const decodeErrors = src?.decode_errors ?? 0;

  const groups: { title: string; rows: [string, string, Tone?][] }[] =
    stats && src
      ? [
          {
            title: "Telemetry",
            rows: [
              ["Status", src.connected ? "receiving" : "no data", src.connected ? "good" : "bad"],
              ["Console", src.source === "sim" ? "simulator" : src.console_ip || "auto-discover"],
              ["Packets", src.packets_received.toLocaleString()],
              ["Rate", hz != null ? `${hz} Hz` : "—"],
              ["Decode errors", String(decodeErrors), decodeErrors > 0 ? "warn" : undefined],
              ["Frames dropped", String(dropped), dropped > 0 ? "warn" : undefined],
              ["Format", src.packet_format ?? "A"],
            ],
          },
          {
            title: "Server",
            rows: [
              ["Uptime", formatDuration(stats.uptime_s * 1000)],
              ["Live clients", String(stats.clients)],
              ["Car names", String(stats.cars_loaded)],
              ...(stats.cars_generated
                ? ([["Car list from", stats.cars_generated.slice(0, 10)]] as [string, string][])
                : []),
              ["Recording", src.recording ? "on" : "off"],
            ],
          },
          {
            title: "Database",
            rows: [
              ["Sessions", stats.db.sessions.toLocaleString()],
              ["Laps", stats.db.laps.toLocaleString()],
              ["Size", mb(stats.db.size_bytes)],
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
        src.source === "sim"
          ? "The simulator is not producing frames — try restarting the telemetry source."
          : "No telemetry — check the console IP, that GT7 is in a session, and that UDP 33740 reaches this machine.",
    });
  }
  if (dropped > 0) {
    notes.push({
      tone: "warn",
      text: `${dropped.toLocaleString()} frame${dropped === 1 ? "" : "s"} dropped since the source started — usually Wi-Fi. A wired console or the 5 GHz band fixes it.`,
    });
  }
  if (decodeErrors > 0) {
    notes.push({
      tone: "warn",
      text: `${decodeErrors.toLocaleString()} packet${decodeErrors === 1 ? "" : "s"} could not be decoded — check the packet format matches your game version.`,
    });
  }

  return (
    <SectionPanel
      title="Health"
      description="Live — refreshes every 5 s."
      actions={
        <>
          <button
            className="btn"
            disabled={busy !== null}
            onClick={() => void run("Restart source", api.admin.restartSource)}
          >
            Restart telemetry source
          </button>
          <button
            className="btn"
            disabled={busy !== null}
            onClick={() =>
              void run("Car DB update", api.admin.updateCars, (r) => {
                const res = r as { cars: number; sessions_updated: number };
                const filled = res.sessions_updated ? `, ${res.sessions_updated} session(s) updated` : "";
                return `Car database updated: ${res.cars} cars${filled}`;
              })
            }
          >
            Update car database
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
        <div className="px-[18px] py-3.5 text-sm text-ink-dim">Loading…</div>
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
