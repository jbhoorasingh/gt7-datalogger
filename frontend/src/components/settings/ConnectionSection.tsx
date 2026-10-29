import { useState } from "react";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { useT, type MessageKey } from "@/i18n";
import { api } from "@/lib/api";
import type { AdminSettings, AdminStats } from "@/lib/types";
import { Dot, INPUT_CLS, SectionPanel, SettingRow, type SectionProps } from "./parts";

const FORMATS: { value: AdminSettings["packet_format"]; tag: MessageKey; desc: MessageKey }[] = [
  { value: "A", tag: "settings.connection.formatA.tag", desc: "settings.connection.formatA.desc" },
  { value: "B", tag: "settings.connection.formatB.tag", desc: "settings.connection.formatB.desc" },
  { value: "~", tag: "settings.connection.formatTilde.tag", desc: "settings.connection.formatTilde.desc" },
  { value: "C", tag: "settings.connection.formatC.tag", desc: "settings.connection.formatC.desc" },
];

export function ConnectionSection({
  saved,
  draft,
  edit,
  stats,
  hz,
}: SectionProps & { stats: AdminStats | null; hz: number | null }) {
  const t = useT();
  const src = stats?.source;
  const ports = t("settings.connection.ports", {
    heartbeat: String(saved.heartbeat_port),
    telemetry: String(saved.telemetry_port),
  });
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
            r.ip === saved.ps_ip
              ? t("settings.connection.foundSaved", { ip: r.ip })
              : t("settings.connection.foundApply", { ip: r.ip }),
        });
      } else {
        setNote({
          tone: "warn",
          text: r.reason
            ? t("settings.connection.cantSearch", { reason: r.reason })
            : t("settings.connection.notFound"),
        });
      }
    } catch (e) {
      setNote({ tone: "warn", text: e instanceof Error ? e.message : t("settings.connection.searchFailed") });
    } finally {
      setFinding(false);
    }
  }

  return (
    <SectionPanel title={t("settings.section.connection")} description={t("settings.connection.desc")}>
      <SettingRow label={t("settings.label.source")} help={t("settings.connection.sourceHelp")}>
        <SegmentedControl
          ariaLabel={t("settings.label.source")}
          value={draft.source}
          onValueChange={(source) => edit({ source })}
          options={[
            { value: "udp", label: "PlayStation" },
            { value: "sim", label: t("settings.connection.simulated") },
          ]}
        />
      </SettingRow>

      <SettingRow label={t("settings.connection.console")} help={t("settings.connection.consoleHelp")} htmlFor="ps-ip">
        <div className="flex flex-col gap-1.5">
          <div className="flex gap-2">
            <input
              id="ps-ip"
              value={draft.ps_ip}
              onChange={(e) => {
                edit({ ps_ip: e.target.value.trim() });
                setNote(null);
              }}
              placeholder={t("settings.health.autoDiscover")}
              autoComplete="off"
              className={INPUT_CLS}
            />
            <button
              className="btn shrink-0"
              onClick={findConsole}
              disabled={finding || !canFind}
              title={canFind ? t("settings.connection.findTitle") : t("settings.connection.findDisabled")}
            >
              {finding ? t("settings.connection.searching") : t("settings.connection.find")}
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
                    ? t("settings.connection.receivingSim")
                    : t("settings.connection.receivingFrom", {
                        ip: src.console_ip || t("settings.connection.consoleWord"),
                      })}
                  {hz != null && ` · ${hz} Hz`}
                  {src.source !== "sim" && ` · ${ports}`}
                </>
              ) : (
                <>
                  <Dot tone="warn" />
                  {src.console_ip
                    ? t("settings.connection.noTelemetryFrom", { ip: src.console_ip })
                    : t("nav.status.noTelemetry")}{" "}
                  · {ports}
                </>
              )}
            </span>
          )}
        </div>
      </SettingRow>

      <SettingRow label={t("settings.label.packet_format")} help={t("settings.connection.formatHelp")} last>
        <div
          role="radiogroup"
          aria-label={t("settings.label.packet_format")}
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
                  <span>{t("settings.connection.formatName", { format: f.value })}</span>
                  <span className="text-[10px] text-ink-faint">{t(f.tag)}</span>
                </span>
                <span className="text-[10.5px] text-ink-dim">{t(f.desc)}</span>
              </button>
            );
          })}
        </div>
      </SettingRow>
    </SectionPanel>
  );
}
