// Race Engineer controls: enable/claim voice, pick a voice, set volume/rate,
// choose verbosity and categories. Shared by the /engineer page and the
// dashboard's settings drawer, so it has to work in a narrow column.

import { useEffect, useState } from "react";
import { useLanguage, useT } from "@/i18n";
import { LANGUAGES, languageName, speechTag, type Language } from "@/i18n/languages";
import { calloutExample, CALLOUT_CATALOG } from "@/lib/calloutCatalog";
import { onVoicesChanged, voicesFor } from "@/lib/speech";
import {
  CALLOUT_CATEGORIES,
  VERBOSITY_CATEGORIES,
  type CalloutCategory,
  type Verbosity,
} from "@/lib/types";
import {
  clientId,
  spokenLanguage,
  useCalloutWording,
  useEngineer,
  type CalloutLanguage,
} from "@/store/engineer";
import { useTelemetry } from "@/store/telemetry";

const VERBOSITIES: Verbosity[] = ["minimal", "race", "coach"];

export interface VoiceGroup {
  kind: "local" | "network";
  voices: SpeechSynthesisVoice[];
}

/**
 * The voices worth offering for callouts in `language`: that language's, or
 * English's when the device has none (that is what will speak instead) —
 * on-device first, in their own group. A network-backed voice accepts
 * speak() and can then never start, which surfaces only as "no response from
 * the speech engine"; grouping is how the two are told apart. `value` is the
 * stored choice when it is on offer, else the default.
 */
export function voiceOptions(
  voices: SpeechSynthesisVoice[],
  language: Language,
  voiceURI: string,
): { groups: VoiceGroup[]; value: string } {
  const own = voicesFor(voices, speechTag(language));
  const pool = own.length ? own : voicesFor(voices, speechTag("en"));
  const offered = pool.length ? pool : voices;
  const groups = (["local", "network"] as const)
    .map((kind) => ({ kind, voices: offered.filter((v) => v.localService === (kind === "local")) }))
    .filter((g) => g.voices.length > 0);
  const value = offered.some((v) => v.voiceURI === voiceURI) ? voiceURI : "";
  return { groups, value };
}

/** Which language callouts are said in: the interface's, or another. */
export function CalloutLanguageRow({ compact = false }: { compact?: boolean }) {
  const t = useT();
  const ui = useLanguage();
  const choice = useEngineer((s) => s.calloutLanguage);
  const setChoice = useEngineer((s) => s.setCalloutLanguage);
  return (
    <label className="block">
      <span className="mb-1.5 block text-[11px] text-ink-dim">{t("engineer.calloutLanguage")}</span>
      <select
        className={`w-full rounded-[5px] border border-edge bg-panel-2 px-2.5 py-2 text-ink-soft focus:border-accent focus:outline-none ${
          compact ? "text-xs" : "text-[12.5px]"
        }`}
        value={choice}
        onChange={(e) => setChoice(e.target.value as CalloutLanguage)}
      >
        <option value="ui">{t("engineer.calloutLanguageUi", { language: languageName(ui) })}</option>
        {LANGUAGES.map((l) => (
          <option key={l.code} value={l.code}>
            {l.name}
          </option>
        ))}
      </select>
    </label>
  );
}

/**
 * Said when this device has no voice for the callout language: speech falls
 * back to English (captions stay translated) rather than handing Japanese
 * text to an English voice.
 */
export function VoiceLanguageNotice({
  voices,
  language,
}: {
  voices: SpeechSynthesisVoice[];
  language: Language;
}) {
  const t = useT();
  if (language === "en" || voices.length === 0) return null;
  if (voicesFor(voices, speechTag(language)).length > 0) return null;
  return (
    <p className="rounded-md border border-warn/40 bg-warn/10 p-2 text-[11px] text-warn">
      {t("engineer.noVoiceForLanguage", { language: languageName(language) })}
    </p>
  );
}

export function RaceEngineerPanel({
  compact = false,
  hideStatus = false,
}: {
  compact?: boolean;
  /** /engineer renders the same rows in its own rail card instead. */
  hideStatus?: boolean;
}) {
  const t = useT();
  const ui = useLanguage();
  const word = useCalloutWording();
  const s = useEngineer();
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);

  useEffect(() => onVoicesChanged(setVoices), []);

  const isSpeaker = s.activeClientId !== "" && s.activeClientId === clientId();
  // The server's own verbosity/category setting is a ceiling: it decides what
  // is ever sent, and this browser can only narrow it further.
  const serverCategories = s.serverStatus?.categories ?? null;
  const language = spokenLanguage(s.calloutLanguage, ui);
  const options = voiceOptions(voices, language, s.voiceURI);

  return (
    <div className={`flex flex-col gap-3.5 ${compact ? "text-xs" : "text-[13px]"} p-3.5`}>
      <div className="flex flex-wrap items-center gap-2">
        {!s.enabled || !s.audioReady ? (
          <button
            className={`btn btn-primary rounded-md px-5 text-[12.5px] font-semibold ${
              compact ? "" : "min-h-[44px]"
            }`}
            onClick={() => void s.enableVoice()}
          >
            {t("engineer.enable")}
          </button>
        ) : (
          <button
            className={`btn rounded-md px-5 text-[12.5px] font-semibold ${
              compact ? "" : "min-h-[44px]"
            }`}
            onClick={() => s.setEnabled(false)}
          >
            {t("engineer.disable")}
          </button>
        )}
        <button
          className={`btn rounded-md px-4 text-[12.5px] ${compact ? "" : "min-h-[44px]"}`}
          disabled={!s.supported}
          onClick={() => s.testVoice()}
        >
          {t("engineer.testVoice")}
        </button>
        {s.enabled && s.audioReady && !isSpeaker && (
          <button className="btn" onClick={() => s.claimSpeaker()}>
            {t("engineer.useThisDevice")}
          </button>
        )}
        {isSpeaker && (
          <button className="btn" onClick={() => s.releaseSpeaker()}>
            {t("engineer.stopHere")}
          </button>
        )}
      </div>

      {!hideStatus && (
        <div className="grid grid-cols-2 gap-x-4 gap-y-1 font-tabular text-[11px]">
          <StatusRows />
        </div>
      )}

      {s.speechError && (
        <p className="rounded-md border border-brake/40 bg-brake/10 p-2 text-[11px] text-brake">
          {t("engineer.speechFailed", { reason: s.speechError })}
        </p>
      )}
      {!s.supported && (
        <p className="rounded-md border border-warn/40 bg-warn/10 p-2 text-[11px] text-warn">
          {t("engineer.unsupported")}
        </p>
      )}
      {s.supported && voices.length === 0 && (
        <p className="rounded-md border border-warn/40 bg-warn/10 p-2 text-[11px] text-warn">
          {t("engineer.noVoices")}
        </p>
      )}

      <CalloutLanguageRow compact />
      <VoiceLanguageNotice voices={voices} language={language} />

      <label className="block">
        <span className="mb-1.5 block text-[11px] text-ink-dim">{t("engineer.voice")}</span>
        <select
          className="w-full rounded-[5px] border border-edge bg-transparent px-3 py-2 text-xs text-ink-soft focus:border-accent focus:outline-none"
          value={options.value}
          onChange={(e) => s.setVoice(e.target.value)}
        >
          <option value="">{t("engineer.voiceDefault")}</option>
          {options.groups.map((group) => (
            <optgroup
              key={group.kind}
              label={group.kind === "local" ? t("engineer.voiceLocal") : t("engineer.voiceNetwork")}
            >
              {group.voices.map((v) => (
                <option key={v.voiceURI} value={v.voiceURI}>
                  {v.name} ({v.lang})
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </label>

      <div className="grid grid-cols-3 gap-2">
        <Slider
          label={t("engineer.volume")}
          value={s.volume}
          min={0}
          max={1}
          step={0.05}
          onChange={(volume) => s.setAudio({ volume })}
        />
        <Slider
          label={t("engineer.rate")}
          value={s.rate}
          min={0.6}
          max={1.6}
          step={0.05}
          onChange={(rate) => s.setAudio({ rate })}
        />
        <Slider
          label={t("engineer.pitch")}
          value={s.pitch}
          min={0.6}
          max={1.6}
          step={0.05}
          onChange={(pitch) => s.setAudio({ pitch })}
        />
      </div>

      <div>
        <span className="mb-1 block text-[11px] text-ink-dim">{t("engineer.howMuch")}</span>
        <div className="flex gap-1">
          {VERBOSITIES.map((mode) => (
            <button
              key={mode}
              title={t(`verbosity.${mode}.short`)}
              className={`flex-1 rounded-md border px-2 py-1 text-xs capitalize ${
                s.verbosity === mode
                  ? "border-accent/60 bg-accent/10 text-ink"
                  : "border-edge bg-panel-2 text-ink-dim hover:text-ink"
              }`}
              onClick={() => s.setVerbosity(mode)}
            >
              {t(`verbosity.${mode}`)}
            </button>
          ))}
        </div>
        <p className="mt-1 text-[10px] text-ink-dim">{t(`verbosity.${s.verbosity}.short`)}</p>
      </div>

      <div>
        <span className="mb-1.5 block text-[11px] text-ink-dim">{t("engineer.categories")}</span>
        <div className={`grid gap-x-3 gap-y-1 ${compact ? "grid-cols-2" : "grid-cols-2 sm:grid-cols-3"}`}>
          {CALLOUT_CATEGORIES.map((category) => {
            const inMode = VERBOSITY_CATEGORIES[s.verbosity].includes(category);
            const onServer = serverCategories == null || serverCategories.includes(category);
            return (
              <label
                key={category}
                className={`flex cursor-pointer items-baseline gap-1.5 text-xs ${
                  inMode && onServer ? "" : "opacity-40"
                }`}
                title={!onServer ? t("engineer.category.raiseCeiling") : t(`category.${category}.hint`)}
              >
                <input
                  type="checkbox"
                  className="translate-y-px accent-accent"
                  checked={s.categories.includes(category)}
                  onChange={(e) => s.toggleCategory(category, e.target.checked)}
                />
                <span className="capitalize">{t(`category.${category}`)}</span>
              </label>
            );
          })}
        </div>
        <CalloutReference verbosity={s.verbosity} serverCategories={serverCategories} language={language} />
      </div>

      <div className="space-y-1">
        <label className="flex cursor-pointer items-baseline gap-2 text-xs">
          <input
            type="checkbox"
            className="translate-y-px accent-accent"
            checked={s.captions}
            onChange={(e) => s.setCaptions(e.target.checked)}
          />
          <span>{t("engineer.showCaptions")}</span>
        </label>
        <label className="flex cursor-pointer items-baseline gap-2 text-xs">
          <input
            type="checkbox"
            className="translate-y-px accent-accent"
            checked={s.muteWhenHidden}
            onChange={(e) => s.setMuteWhenHidden(e.target.checked)}
          />
          <span>{t("engineer.stayQuietHidden")}</span>
        </label>
      </div>

      {s.lastSpoken && (
        <div className="rounded-md border border-edge bg-panel-2 p-2">
          <div className="text-[10px] uppercase tracking-widest text-ink-dim">
            {t("engineer.lastSpoken")}
          </div>
          <div className="text-xs">{word(s.lastSpoken)}</div>
        </div>
      )}
    </div>
  );
}

/**
 * What every category actually says. Folded away by default — it is reference
 * material, not a control — but a category toggle is guesswork without it.
 */
export function CalloutReference({
  verbosity,
  serverCategories,
  language,
}: {
  verbosity: Verbosity;
  serverCategories: CalloutCategory[] | null;
  language: Language;
}) {
  const t = useT();
  return (
    <details className="mt-2 rounded-md border border-edge bg-panel-2">
      <summary className="cursor-pointer px-2 py-1 text-[11px] text-ink-dim hover:text-ink">
        {t("engineer.reference.title")}
      </summary>
      <div className="space-y-2 px-2 pb-2">
        {CALLOUT_CATEGORIES.map((category) => {
          const info = CALLOUT_CATALOG[category];
          const inMode = VERBOSITY_CATEGORIES[verbosity].includes(category);
          const onServer = serverCategories == null || serverCategories.includes(category);
          return (
            <div key={category} className={inMode && onServer ? "" : "opacity-50"}>
              <div className="flex items-baseline gap-1.5">
                <span className="text-[11px] font-semibold capitalize">{t(`category.${category}`)}</span>
                {!inMode && (
                  <span className="text-[10px] text-ink-dim">
                    {t("engineer.reference.offAt", { verbosity: t(`verbosity.${verbosity}`) })}
                  </span>
                )}
                {inMode && !onServer && (
                  <span className="text-[10px] text-warn">{t("engineer.reference.notSent")}</span>
                )}
              </div>
              <div className="text-[10px] text-ink-dim">{t(`category.${category}.summary`)}</div>
              <ul className="mt-0.5 space-y-0.5">
                {info.callouts.map((callout) => (
                  <li key={callout.event} className="text-[10px] leading-snug">
                    <span className="text-ink">
                      {t("engineer.quoted", { text: calloutExample(callout, language) })}
                    </span>
                    <span className="text-ink-dim"> — {t(`callout.${callout.event}.when`)}</span>
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </div>
    </details>
  );
}

function Row({ k, v, ok }: { k: string; v: string; ok?: boolean }) {
  return (
    <>
      <span className="text-ink-faint">{k}</span>
      <span className={`text-right ${ok === undefined ? "" : ok ? "text-throttle" : "text-warn"}`}>
        {v}
      </span>
    </>
  );
}

function Slider({
  label,
  value,
  min,
  max,
  step,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
}) {
  return (
    <label className="block">
      <span className="mb-1 flex justify-between text-[11px] text-ink-dim">
        <span>{label}</span>
        <span className="font-tabular text-ink-soft">{value.toFixed(2)}</span>
      </span>
      <input
        type="range"
        className="w-full"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </label>
  );
}

/** The status rows, shared by the panel and the standalone card. */
function StatusRows() {
  const t = useT();
  const s = useEngineer();
  const wsConnected = useTelemetry((st) => st.wsConnected);
  const isSpeaker = s.activeClientId !== "" && s.activeClientId === clientId();
  const otherSpeaker = s.activeClientId !== "" && !isSpeaker;
  return (
    <>
      <Row
        k={t("engineer.status.preference")}
        v={s.enabled ? t("engineer.status.enabled") : t("engineer.status.off")}
        ok={s.enabled}
      />
      <Row
        k={t("engineer.check.audio")}
        v={
          !s.supported
            ? t("engineer.check.unsupported")
            : s.speechError
              ? t("engineer.check.failed", { n: s.failedCount })
              : s.spokenCount > 0
                ? t("engineer.check.speaking")
                : s.audioReady
                  ? t("engineer.check.armed")
                  : t("engineer.status.needsClick")
        }
        ok={s.supported && (s.spokenCount > 0 || s.audioReady) && !s.speechError}
      />
      <Row
        k={t("engineer.status.activeSpeaker")}
        v={
          isSpeaker
            ? t("engineer.check.thisDevice")
            : otherSpeaker
              ? t("engineer.check.anotherDevice")
              : t("engineer.check.none")
        }
        ok={isSpeaker}
      />
      <Row
        k={t("engineer.status.connection")}
        v={wsConnected ? t("engineer.check.connected") : t("engineer.check.offline")}
        ok={wsConnected}
      />
      <Row
        k={t("engineer.status.serverCallouts")}
        v={
          s.serverStatus == null
            ? t("engineer.check.unknown")
            : !s.serverStatus.enabled
              ? t("engineer.status.disabled")
              : s.serverStatus.active
                ? t("engineer.status.running")
                : t("engineer.check.idle")
        }
        ok={s.serverStatus?.active ?? false}
      />
      <Row k={t("engineer.status.queued")} v={String(s.queue.length)} />
      {s.verbosity === "coach" && (
        <Row
          k={t("engineer.status.coaching")}
          v={s.serverStatus?.coaching_ready ? t("engineer.status.ready") : t("engineer.status.needsLaps")}
          ok={s.serverStatus?.coaching_ready ?? false}
        />
      )}
    </>
  );
}

// The same status rows the panel shows inline, for hosts that give them
// their own card (see /engineer).
export function RaceEngineerStatus() {
  return (
    <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 font-tabular text-[11.5px]">
      <StatusRows />
    </div>
  );
}
