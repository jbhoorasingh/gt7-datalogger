import { useState } from "react";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { api } from "@/lib/api";
import type { AdminSettings, AdminStats } from "@/lib/types";
import { Dot, INPUT_CLS, SectionPanel, SettingRow, type SectionProps } from "./parts";

const NOT_FOUND =
  "Not found — the console must be on the same network and GT7 running; type its IP instead.";

const FORMATS: { value: AdminSettings["packet_format"]; tag: string; desc: string }[] = [
  { value: "A", tag: "GT7 < 1.42", desc: "Core channels only." },
  { value: "B", tag: "1.42+", desc: "Adds wheel rotation, sway." },
  { value: "~", tag: "1.42+", desc: "B plus filtered inputs & torque vectors." },
  { value: "C", tag: "1.68+ · best", desc: "Everything, incl. surface & positions." },
];

export function ConnectionSection({
  saved,
  draft,
  edit,
  stats,
  hz,
}: SectionProps & { stats: AdminStats | null; hz: number | null }) {
  const src = stats?.source;
  const ports = `heartbeat ${saved.heartbeat_port} → telemetry ${saved.telemetry_port}/udp`;
  // Find console: the server broadcasts the heartbeat and reports who
  // answered. The address only lands in the draft — Apply saves it, like any
  // other edit. It also covers pinning a console that auto-discovery found,
  // which a separate "Use <ip>" button used to offer: a live console is what
  // the search answers with.
  const [finding, setFinding] = useState(false);
  const [note, setNote] = useState<{ tone: "good" | "warn"; text: string } | null>(null);
  // The search runs on the source the server has now, not the draft's.
  const canFind = saved.source === "udp";

  async function findConsole() {
    setFinding(true);
    setNote(null);
    try {
      const r = await api.admin.discoverConsole();
      if (r.found && r.ip) {
        edit({ ps_ip: r.ip });
        setNote({
          tone: "good",
          text:
            r.ip === saved.ps_ip ? `Found at ${r.ip} — already saved.` : `Found at ${r.ip} — Apply to save it.`,
        });
      } else {
        setNote({ tone: "warn", text: r.reason ? `Can't search: ${r.reason}.` : NOT_FOUND });
      }
    } catch (e) {
      setNote({ tone: "warn", text: e instanceof Error ? e.message : "Search failed." });
    } finally {
      setFinding(false);
    }
  }

  return (
    <SectionPanel title="Connection" description="Where telemetry comes from. Applied without a restart.">
      <SettingRow
        label="Telemetry source"
        help="Simulated drives a synthetic lap for testing without a console."
      >
        <SegmentedControl
          ariaLabel="Telemetry source"
          value={draft.source}
          onValueChange={(source) => edit({ source })}
          options={[
            { value: "udp", label: "PlayStation" },
            { value: "sim", label: "Simulated" },
          ]}
        />
      </SettingRow>

      <SettingRow label="Console" help="Leave empty to auto-discover on your network." htmlFor="ps-ip">
        <div className="flex flex-col gap-1.5">
          <div className="flex gap-2">
            <input
              id="ps-ip"
              value={draft.ps_ip}
              onChange={(e) => {
                edit({ ps_ip: e.target.value.trim() });
                setNote(null);
              }}
              placeholder="auto-discover"
              autoComplete="off"
              className={INPUT_CLS}
            />
            <button
              className="btn shrink-0"
              onClick={findConsole}
              disabled={finding || !canFind}
              title={
                canFind
                  ? "Broadcast on your network and see which console answers"
                  : "Switch the source to PlayStation first"
              }
            >
              {finding ? "Searching…" : "Find console"}
            </button>
          </div>
          {note && (
            <span role="status" className="flex items-center gap-1.5 text-[11px] text-ink-dim">
              <Dot tone={note.tone} />
              {note.text}
            </span>
          )}
          {src && (
            <span className="flex items-center gap-1.5 font-tabular text-[11px] text-ink-dim">
              {src.connected ? (
                <>
                  <Dot tone="good" />
                  {src.source === "sim"
                    ? "Receiving from the simulator"
                    : `Receiving from ${src.console_ip || "console"}`}
                  {hz != null && ` · ${hz} Hz`}
                  {src.source !== "sim" && ` · ${ports}`}
                </>
              ) : (
                <>
                  <Dot tone="warn" />
                  No telemetry{src.console_ip ? ` from ${src.console_ip}` : ""} · {ports}
                </>
              )}
            </span>
          )}
        </div>
      </SettingRow>

      <SettingRow label="Packet format" help="Pick the richest one your game version sends." last>
        <div
          role="radiogroup"
          aria-label="Packet format"
          className="grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-2"
        >
          {FORMATS.map((f) => {
            const on = draft.packet_format === f.value;
            return (
              <button
                key={f.value}
                role="radio"
                aria-checked={on}
                onClick={() => edit({ packet_format: f.value })}
                className={`flex flex-col gap-[3px] rounded-md border px-[11px] py-[9px] text-left transition-colors ${
                  on ? "border-accent/60 bg-accent/8" : "border-edge hover:border-edge-bright"
                }`}
              >
                <span className={`flex justify-between text-[12.5px] ${on ? "text-accent-300" : "text-ink"}`}>
                  <span>Format {f.value}</span>
                  <span className="text-[10px] text-ink-faint">{f.tag}</span>
                </span>
                <span className="text-[10.5px] text-ink-dim">{f.desc}</span>
              </button>
            );
          })}
        </div>
      </SettingRow>
    </SectionPanel>
  );
}
