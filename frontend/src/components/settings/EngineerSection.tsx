import { useEffect, useState } from "react";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { Toggle } from "@/components/ui/Toggle";
import { api } from "@/lib/api";
import {
  CALLOUT_CATEGORIES,
  type CalloutCategory,
  type RaceEngineerDiagnostics,
  type SpokenUnits,
  type Verbosity,
} from "@/lib/types";
import { SectionPanel, SettingRow, TONE_TEXT, type SectionProps, type Tone } from "./parts";

const VERBOSITY_DESC: Record<Verbosity, string> = {
  minimal: "Only what needs action: critical warnings, fuel shortage, pit window, final lap.",
  race: "Adds lap times, personal bests, positions, fuel and race progress.",
  coach: "Adds repeated lockups, wheelspin and corner feedback — chatty.",
};

export function EngineerSection({ draft, edit, busy, run }: SectionProps) {
  const [diag, setDiag] = useState<RaceEngineerDiagnostics | null>(null);

  useEffect(() => {
    const load = () => api.admin.raceEngineer().then(setDiag).catch(() => {});
    load();
    const t = window.setInterval(load, 5000);
    return () => window.clearInterval(t);
  }, []);

  const on = draft.race_engineer;
  const cats = draft.race_engineer_categories;

  function toggleCategory(category: CalloutCategory) {
    const has = cats.includes(category);
    edit({
      race_engineer_categories: CALLOUT_CATEGORIES.filter((c) =>
        c === category ? !has : cats.includes(c),
      ),
    });
  }

  const speaker = diag
    ? diag.clients.find((c) => c.is_active_speaker)?.page ?? (diag.active_client_id ? "elsewhere" : "none")
    : "—";
  const failures = diag?.acks.speech_error ?? 0;
  const stats: { k: string; v: string; tone: Tone }[] = diag
    ? [
        {
          k: "Detection",
          v: diag.active ? "running" : diag.enabled ? "idle" : "disabled",
          tone: diag.active ? "good" : "faint",
        },
        { k: "Speaking on", v: speaker, tone: "ink" },
        { k: "Emitted", v: String(diag.stats.emitted ?? 0), tone: "ink" },
        { k: "Speech failures", v: String(failures), tone: failures ? "bad" : "ink" },
      ]
    : [];

  return (
    <SectionPanel
      title="Race Engineer"
      description={
        <>
          Voice callouts, spoken in the browser on{" "}
          <a className="text-accent hover:underline" href="/dash" target="_blank" rel="noreferrer">
            /dash
          </a>{" "}
          or{" "}
          <a className="text-accent hover:underline" href="/engineer" target="_blank" rel="noreferrer">
            /engineer
          </a>{" "}
          — never on the server, so no audio hardware is needed on a Pi or in Docker.
        </>
      }
      actions={
        <label className="flex items-center gap-2.5 text-xs text-ink-dim">
          Callouts on
          <Toggle ariaLabel="Callouts on" checked={on} onCheckedChange={(v) => edit({ race_engineer: v })} />
        </label>
      }
    >
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 bg-panel-2 px-[18px] py-2.5 font-tabular">
        {stats.map((s) => (
          <span key={s.k} className="text-[11px] text-ink-faint">
            {s.k} <span className={TONE_TEXT[s.tone]}>{s.v}</span>
          </span>
        ))}
        {!diag && <span className="text-[11px] text-ink-faint">Loading diagnostics…</span>}
        <button
          className="btn ml-auto"
          disabled={busy !== null}
          onClick={() =>
            void run(
              "Test callout",
              () => api.admin.testCallout("Race engineer test callout."),
              () => "Test callout sent to connected browsers",
            )
          }
        >
          Send test callout
        </button>
      </div>
      {diag?.last_ack_reason && failures > 0 && (
        <div className="bg-brake/10 px-[18px] py-2 text-xs text-brake">
          Speech failing: {diag.last_ack_reason} — the browser is receiving callouts but cannot play them.
        </div>
      )}
      <div className="rule" />

      <SettingRow label="Maximum verbosity" help="A ceiling — each browser can choose less, never more.">
        <div className="flex flex-col items-start gap-1.5">
          <SegmentedControl
            ariaLabel="Maximum verbosity"
            value={draft.race_engineer_verbosity}
            disabled={!on}
            onValueChange={(v: Verbosity) => edit({ race_engineer_verbosity: v })}
            options={[
              { value: "minimal", label: "Minimal" },
              { value: "race", label: "Race" },
              { value: "coach", label: "Coach" },
            ]}
          />
          <span className="text-[11px] text-ink-dim">{VERBOSITY_DESC[draft.race_engineer_verbosity]}</span>
        </div>
      </SettingRow>

      <SettingRow label="Spoken units" help="For braking points and speeds inside callouts.">
        <SegmentedControl
          ariaLabel="Spoken units"
          value={draft.race_engineer_units}
          disabled={!on}
          onValueChange={(u: SpokenUnits) => edit({ race_engineer_units: u })}
          options={[
            { value: "metric", label: "Metres · km/h" },
            { value: "imperial", label: "Feet · mph" },
          ]}
        />
      </SettingRow>

      <SettingRow
        label="Categories"
        help={`What the server may say. ${cats.length} of ${CALLOUT_CATEGORIES.length} on.`}
        last
      >
        <div className="flex flex-wrap gap-1.5">
          {CALLOUT_CATEGORIES.map((c) => {
            const active = cats.includes(c);
            return (
              <button
                key={c}
                aria-pressed={active}
                disabled={!on}
                onClick={() => toggleCategory(c)}
                className={`rounded border px-2.5 py-1 text-[11.5px] capitalize transition-colors disabled:cursor-not-allowed disabled:opacity-45 ${
                  active
                    ? "border-accent/50 bg-accent/10 text-accent-300"
                    : "border-edge text-ink-faint hover:text-ink"
                }`}
              >
                {c}
              </button>
            );
          })}
        </div>
      </SettingRow>

      {diag && (
        <>
          <div className="rule" />
          <details className="px-[18px] py-3 text-[11px] text-ink-dim">
            <summary className="cursor-pointer text-ink-faint">More diagnostics</summary>
            <div className="mt-2 grid max-w-[560px] grid-cols-[1fr_auto] gap-x-6 gap-y-1 font-tabular">
              <DiagRow k="Voice-capable clients" v={diag.clients.length} />
              <DiagRow k="Suppressed (cooldown)" v={diag.stats.suppressed_cooldown ?? 0} />
              <DiagRow k="Suppressed (duplicate)" v={diag.stats.suppressed_duplicate ?? 0} />
              <DiagRow k="Suppressed (category)" v={diag.stats.suppressed_category ?? 0} />
              <DiagRow k="Spoken acks" v={diag.acks.spoken ?? 0} cls={diag.acks.spoken ? "text-throttle" : undefined} />
              <DiagRow k="Corners on reference lap" v={diag.corners} />
              <DiagRow k="Laps in fuel model" v={diag.lap_history} />
            </div>
            {diag.last_callout && (
              <div className="mt-2 rounded-md border border-edge bg-panel-2 p-2 text-xs text-ink">
                <span className="section-header">Last emitted </span>
                {diag.last_callout.text}
              </div>
            )}
          </details>
        </>
      )}
    </SectionPanel>
  );
}

function DiagRow({ k, v, cls }: { k: string; v: number; cls?: string }) {
  return (
    <>
      <span className="text-ink-faint">{k}</span>
      <span className={`text-right ${cls ?? "text-ink"}`}>{v}</span>
    </>
  );
}
