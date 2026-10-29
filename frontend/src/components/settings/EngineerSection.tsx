import { useEffect, useState } from "react";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { Toggle } from "@/components/ui/Toggle";
import { useT } from "@/i18n";
import { api } from "@/lib/api";
import {
  CALLOUT_CATEGORIES,
  type CalloutCategory,
  type RaceEngineerDiagnostics,
  type SpokenUnits,
  type Verbosity,
} from "@/lib/types";
import { SectionPanel, SettingRow, TONE_TEXT, type SectionProps, type Tone } from "./parts";

export function EngineerSection({ draft, edit, busy, run }: SectionProps) {
  const t = useT();
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
    ? diag.clients.find((c) => c.is_active_speaker)?.page ??
      (diag.active_client_id ? t("settings.engineer.elsewhere") : t("engineer.check.none"))
    : "—";
  const failures = diag?.acks.speech_error ?? 0;
  const stats: { k: string; v: string; tone: Tone }[] = diag
    ? [
        {
          k: t("settings.engineer.detection"),
          v: diag.active
            ? t("engineer.status.running")
            : diag.enabled
              ? t("engineer.check.idle")
              : t("engineer.status.disabled"),
          tone: diag.active ? "good" : "faint",
        },
        { k: t("settings.engineer.speakingOn"), v: speaker, tone: "ink" },
        { k: t("settings.engineer.emitted"), v: String(diag.stats.emitted ?? 0), tone: "ink" },
        { k: t("settings.engineer.failures"), v: String(failures), tone: failures ? "bad" : "ink" },
      ]
    : [];

  return (
    <SectionPanel
      title={t("engineer.title")}
      description={
        <>
          {t("settings.engineer.descBefore")}
          <a className="text-accent hover:underline" href="/dash" target="_blank" rel="noreferrer">
            /dash
          </a>
          {t("settings.engineer.descOr")}
          <a className="text-accent hover:underline" href="/engineer" target="_blank" rel="noreferrer">
            /engineer
          </a>
          {t("settings.engineer.descAfter")}
        </>
      }
      actions={
        <label className="flex items-center gap-2.5 text-xs text-ink-dim">
          {t("settings.engineer.calloutsOn")}
          <Toggle
            ariaLabel={t("settings.engineer.calloutsOn")}
            checked={on}
            onCheckedChange={(v) => edit({ race_engineer: v })}
          />
        </label>
      }
    >
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 bg-panel-2 px-[18px] py-2.5 font-tabular">
        {stats.map((s) => (
          <span key={s.k} className="text-[11px] text-ink-faint">
            {s.k} <span className={TONE_TEXT[s.tone]}>{s.v}</span>
          </span>
        ))}
        {!diag && <span className="text-[11px] text-ink-faint">{t("settings.engineer.loadingDiag")}</span>}
        <button
          className="btn ml-auto"
          disabled={busy !== null}
          onClick={() =>
            void run(
              t("settings.engineer.testCallout"),
              () => api.admin.testCallout("Race engineer test callout."),
              () => t("settings.engineer.testSent"),
            )
          }
        >
          {t("settings.engineer.sendTest")}
        </button>
      </div>
      {diag?.last_ack_reason && failures > 0 && (
        <div className="bg-brake/10 px-[18px] py-2 text-xs text-brake">
          {t("settings.engineer.speechFailing", { reason: diag.last_ack_reason })}
        </div>
      )}
      <div className="rule" />

      <SettingRow label={t("settings.engineer.maxVerbosity")} help={t("settings.engineer.maxVerbosityHelp")}>
        <div className="flex flex-col items-start gap-1.5">
          <SegmentedControl
            ariaLabel={t("settings.engineer.maxVerbosity")}
            value={draft.race_engineer_verbosity}
            disabled={!on}
            onValueChange={(v: Verbosity) => edit({ race_engineer_verbosity: v })}
            options={(["minimal", "race", "coach"] as const).map((v) => ({
              value: v,
              label: <span className="capitalize">{t(`verbosity.${v}`)}</span>,
            }))}
          />
          <span className="text-[11px] text-ink-dim">
            {t(`verbosity.${draft.race_engineer_verbosity}.server`)}
          </span>
        </div>
      </SettingRow>

      <div className="rule" />
      <div className="px-[18px] py-3">
        <div className="text-xs font-semibold text-ink">{t("settings.engineer.sectorTitle")}</div>
        <p className="mt-1 text-[11px] text-ink-dim">{t("settings.engineer.sectorDesc")}</p>
      </div>

      <SettingRow label={t("settings.engineer.sectorToggle")} help={t("settings.engineer.sectorToggleHelp")}>
        <Toggle
          ariaLabel={t("settings.label.race_engineer_sector_voice_enabled")}
          checked={draft.race_engineer_sector_voice_enabled}
          disabled={!on}
          onCheckedChange={(v) => edit({ race_engineer_sector_voice_enabled: v })}
        />
      </SettingRow>

      <SettingRow label={t("settings.engineer.decimals")} help={t("settings.engineer.decimalsHelp")}>
        <SegmentedControl
          ariaLabel={t("settings.label.race_engineer_sector_voice_decimals")}
          value={String(draft.race_engineer_sector_voice_decimals)}
          disabled={!on || !draft.race_engineer_sector_voice_enabled}
          onValueChange={(v) =>
            edit({ race_engineer_sector_voice_decimals: Number(v) as 1 | 2 | 3 })
          }
          options={[
            { value: "1", label: "1" },
            { value: "2", label: "2" },
            { value: "3", label: "3" },
          ]}
        />
      </SettingRow>

      <SettingRow label={t("settings.engineer.rounding")} help={t("settings.engineer.roundingHelp")}>
        <SegmentedControl
          ariaLabel={t("settings.label.race_engineer_sector_voice_rounding")}
          value={draft.race_engineer_sector_voice_rounding}
          disabled={!on || !draft.race_engineer_sector_voice_enabled}
          onValueChange={(v: "round" | "truncate") =>
            edit({ race_engineer_sector_voice_rounding: v })
          }
          options={[
            { value: "round", label: t("settings.engineer.round") },
            { value: "truncate", label: t("settings.engineer.truncate") },
          ]}
        />
      </SettingRow>

      <SettingRow label={t("settings.engineer.content")} help={t("settings.engineer.contentHelp")}>
        <SegmentedControl
          ariaLabel={t("settings.label.race_engineer_sector_voice_content")}
          value={draft.race_engineer_sector_voice_content}
          disabled={!on || !draft.race_engineer_sector_voice_enabled}
          onValueChange={(v: "delta" | "time" | "both") =>
            edit({ race_engineer_sector_voice_content: v })
          }
          options={[
            { value: "delta", label: t("settings.engineer.delta") },
            { value: "time", label: t("settings.engineer.time") },
            { value: "both", label: t("settings.engineer.both") },
          ]}
        />
      </SettingRow>

      <SettingRow label={t("settings.engineer.comparison")} help={t("settings.engineer.comparisonHelp")}>
        <SegmentedControl
          ariaLabel={t("settings.label.race_engineer_sector_voice_comparison")}
          value={draft.race_engineer_sector_voice_comparison}
          disabled={!on || !draft.race_engineer_sector_voice_enabled}
          onValueChange={(v: "individual" | "cumulative") =>
            edit({ race_engineer_sector_voice_comparison: v })
          }
          options={[
            { value: "individual", label: t("settings.engineer.individual") },
            { value: "cumulative", label: t("settings.engineer.cumulative") },
          ]}
        />
      </SettingRow>

      <SettingRow label={t("settings.label.race_engineer_units")} help={t("settings.engineer.unitsHelp")}>
        <SegmentedControl
          ariaLabel={t("settings.label.race_engineer_units")}
          value={draft.race_engineer_units}
          disabled={!on}
          onValueChange={(u: SpokenUnits) => edit({ race_engineer_units: u })}
          options={[
            { value: "metric", label: t("settings.engineer.metric") },
            { value: "imperial", label: t("settings.engineer.imperial") },
          ]}
        />
      </SettingRow>

      <SettingRow
        label={t("engineer.categories")}
        help={t("settings.engineer.categoriesHelp", { on: cats.length, total: CALLOUT_CATEGORIES.length })}
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
                {t(`category.${c}`)}
              </button>
            );
          })}
        </div>
      </SettingRow>

      {diag && (
        <>
          <div className="rule" />
          <details className="px-[18px] py-3 text-[11px] text-ink-dim">
            <summary className="cursor-pointer text-ink-faint">{t("settings.engineer.moreDiag")}</summary>
            <div className="mt-2 grid max-w-[560px] grid-cols-[1fr_auto] gap-x-6 gap-y-1 font-tabular">
              <DiagRow k={t("settings.engineer.diag.clients")} v={diag.clients.length} />
              <DiagRow k={t("settings.engineer.diag.cooldown")} v={diag.stats.suppressed_cooldown ?? 0} />
              <DiagRow k={t("settings.engineer.diag.duplicate")} v={diag.stats.suppressed_duplicate ?? 0} />
              <DiagRow k={t("settings.engineer.diag.category")} v={diag.stats.suppressed_category ?? 0} />
              <DiagRow
                k={t("settings.engineer.diag.spoken")}
                v={diag.acks.spoken ?? 0}
                cls={diag.acks.spoken ? "text-throttle" : undefined}
              />
              <DiagRow k={t("settings.engineer.diag.corners")} v={diag.corners} />
              <DiagRow k={t("settings.engineer.diag.fuelLaps")} v={diag.lap_history} />
            </div>
            {diag.last_callout && (
              <div className="mt-2 rounded-md border border-edge bg-panel-2 p-2 text-xs text-ink">
                <span className="section-header">{t("settings.engineer.lastEmitted")} </span>
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
