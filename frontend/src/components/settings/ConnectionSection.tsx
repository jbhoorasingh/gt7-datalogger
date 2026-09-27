import { SegmentedControl } from "@/components/ui/SegmentedControl";
import type { AdminSettings, AdminStats } from "@/lib/types";
import { Dot, INPUT_CLS, SectionPanel, SettingRow, type SectionProps } from "./parts";

const FORMATS: { value: AdminSettings["packet_format"]; tag: string; desc: string }[] = [
  { value: "A", tag: "GT7 < 1.42", desc: "Core channels only." },
  { value: "B", tag: "1.42+", desc: "Adds wheel rotation, sway." },
  { value: "~", tag: "1.42+", desc: "B plus car-local velocity." },
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
  // Auto-discovery found a console the settings don't name: offer to pin it.
  const discovered = !draft.ps_ip && src?.source === "udp" && src.console_ip ? src.console_ip : "";

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
              onChange={(e) => edit({ ps_ip: e.target.value.trim() })}
              placeholder="auto-discover"
              autoComplete="off"
              className={INPUT_CLS}
            />
            {discovered && (
              <button className="btn shrink-0" onClick={() => edit({ ps_ip: discovered })}>
                Use {discovered}
              </button>
            )}
          </div>
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
