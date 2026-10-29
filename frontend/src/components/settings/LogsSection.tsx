import { useEffect, useRef, useState } from "react";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { useT } from "@/i18n";
import { api } from "@/lib/api";
import type { AdminSettings, LogRecord } from "@/lib/types";
import { toast } from "@/store/toasts";
import { SectionPanel, type SectionProps } from "./parts";

const LEVEL_COLORS: Record<string, string> = {
  DEBUG: "text-ink-faint",
  INFO: "text-ink",
  WARNING: "text-warn",
  ERROR: "text-brake",
  CRITICAL: "text-brake",
};

// The viewer's filter, as the minimum level the API is asked for.
type Filter = "all" | "INFO" | "WARNING" | "ERROR";

function download(logs: LogRecord[]) {
  const text = logs.map((r) => `${r.ts} ${r.level.padEnd(8)} ${r.logger}: ${r.message}`).join("\n");
  const url = URL.createObjectURL(new Blob([`${text}\n`], { type: "text/plain" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = `gt7-datalogger-${new Date().toISOString().slice(0, 19).replace(/:/g, "")}.log`;
  a.click();
  // Revoking synchronously can cancel the download in some browsers.
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export function LogsSection({ draft, edit }: SectionProps) {
  const t = useT();
  const [logs, setLogs] = useState<LogRecord[]>([]);
  const [filter, setFilter] = useState<Filter>("all");
  const [paused, setPaused] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      if (paused) return;
      api.admin
        .logs(300, filter === "all" ? undefined : filter)
        .then((ls) => {
          if (cancelled) return;
          setLogs(ls);
          const el = scroller.current;
          if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 60) {
            requestAnimationFrame(() => el.scrollTo({ top: el.scrollHeight }));
          }
        })
        .catch(() => {});
    };
    load();
    const t = window.setInterval(load, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(t);
    };
  }, [filter, paused]);

  return (
    <SectionPanel title={t("settings.section.logs")} description={t("settings.logs.desc")}>
      <div className="flex flex-wrap items-center gap-2 px-[18px] py-2">
        <SegmentedControl
          ariaLabel={t("settings.logs.filter")}
          size="sm"
          value={filter}
          onValueChange={setFilter}
          options={[
            { value: "all", label: t("settings.logs.all") },
            { value: "INFO", label: "INFO+" },
            { value: "WARNING", label: "WARN+" },
            { value: "ERROR", label: "ERROR" },
          ]}
        />
        <button className="btn" onClick={() => setPaused((p) => !p)}>
          {paused ? t("settings.logs.resume") : t("settings.logs.pause")}
        </button>
        <button className="btn" disabled={logs.length === 0} onClick={() => download(logs)}>
          {t("settings.logs.download")}
        </button>
        <button
          className="btn"
          onClick={() =>
            api.admin
              .clearLogs()
              .then(() => setLogs([]))
              .catch((e) => toast(e instanceof Error ? e.message : t("settings.logs.clearFailed"), "error"))
          }
        >
          {t("settings.logs.clear")}
        </button>
        <span className="font-tabular text-[11px] text-ink-faint">
          {t("settings.logs.entries", { count: logs.length })}
        </span>
        <span className="ml-auto text-[11px] text-ink-dim">{t("settings.label.log_level")}</span>
        <SegmentedControl
          ariaLabel={t("settings.label.log_level")}
          size="sm"
          value={draft.log_level}
          onValueChange={(l: AdminSettings["log_level"]) => edit({ log_level: l })}
          options={[
            { value: "DEBUG", label: "DEBUG" },
            { value: "INFO", label: "INFO" },
            { value: "WARNING", label: "WARNING" },
            { value: "ERROR", label: "ERROR" },
          ]}
        />
      </div>
      <div className="rule" />
      <div ref={scroller} className="h-[340px] overflow-y-auto px-3 py-2 font-mono text-[10.5px] leading-5">
        {logs.length === 0 && <div className="p-2 text-ink-faint">{t("settings.logs.none")}</div>}
        {logs.map((r, i) => (
          <div key={`${r.ts}-${i}`} className="flex gap-2 whitespace-pre-wrap break-all px-1 hover:bg-panel-2">
            <span className="shrink-0 text-ink-faint">{r.ts.slice(11, 19)}</span>
            <span className={`w-16 shrink-0 ${LEVEL_COLORS[r.level] ?? "text-ink"}`}>{r.level}</span>
            <span className="shrink-0 text-ink-faint">{r.logger}</span>
            <span>{r.message}</span>
          </div>
        ))}
      </div>
    </SectionPanel>
  );
}
