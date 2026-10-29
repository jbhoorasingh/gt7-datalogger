// Standalone Race Engineer page (/engineer): voice output without the
// dashboard. Often opened on a phone or in an OBS browser source, where the
// one question is "is it going to speak?" — so that answer is the hero, in
// one of four states, with the single action that changes it.

import { useEffect, useState } from "react";
import { CalloutPill } from "@/components/CalloutPill";
import { ChaseReferenceSelector } from "@/components/ChaseReferenceSelector";
import { LanguagePicker } from "@/components/LanguagePicker";
import { CalloutLanguageRow, VoiceLanguageNotice, voiceOptions } from "@/components/RaceEngineerPanel";
import { Toggle } from "@/components/ui/Toggle";
import { useLanguage, useT } from "@/i18n";
import { calloutExample, CALLOUT_CATALOG } from "@/lib/calloutCatalog";
import { formatAge, voiceState, type VoiceState } from "@/lib/engineerState";
import { onVoicesChanged } from "@/lib/speech";
import { CALLOUT_CATEGORIES, VERBOSITY_CATEGORIES, type CalloutCategory, type Verbosity } from "@/lib/types";
import { STALE_AFTER_MS } from "@/lib/useLiveFrame";
import { useNow } from "@/lib/useNow";
import { useVoiceClient } from "@/lib/useVoiceClient";
import { clientId, spokenLanguage, useCalloutWording, useEngineer } from "@/store/engineer";
import { liveFrameRef, useTelemetry } from "@/store/telemetry";

const VERBOSITIES: Verbosity[] = ["minimal", "race", "coach"];

const SETTINGS_ENGINEER = "/#/settings/race-engineer";

export function EngineerView() {
  useVoiceClient("engineer");

  useEffect(() => {
    document.body.classList.add("overlay-page");
    return () => document.body.classList.remove("overlay-page");
  }, []);

  const t = useT();
  const level = useEngineer((s) => s.serverStatus?.verbosity ?? null);
  const sessionLine = useSessionLine();

  return (
    <div className="px-4 pb-8 pt-3.5">
      <div className="mx-auto flex max-w-[1100px] flex-col gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2">
            <span className="h-[7px] w-[7px] rounded-full bg-accent shadow-[0_0_8px_var(--color-accent)]" />
            <h1 className="text-[15px] font-medium">{t("engineer.title")}</h1>
          </div>
          <span className="font-tabular text-[11px] text-ink-faint">{sessionLine}</span>
          {/* /#/live and /dash: this page has no nav, and /engineer may be a
              path where a bare hash change would go nowhere. */}
          <div className="ml-auto flex items-center gap-2">
            <LanguagePicker className="h-[30px]" />
            <a className="btn" href="/#/live">
              {t("engineer.back")}
            </a>
            <a className="btn" href="/dash">
              {t("engineer.dash")}
            </a>
          </div>
        </div>

        <Hero />

        <div className="grid items-start gap-3 [grid-template-columns:repeat(auto-fit,minmax(320px,1fr))]">
          <div className="flex min-w-0 flex-col gap-3">
            <WhatYoullHear />
            <ChaseReferenceSelector />
            <VoicePanel />
          </div>
          <CalloutFeed />
        </div>

        <span className="text-center text-[10.5px] text-ink-ghost">
          {t("engineer.ceiling.before")}
          <a className="text-ink-ghost hover:text-ink-dim hover:underline" href={SETTINGS_ENGINEER}>
            {t("engineer.settingsLink")}
          </a>
          {level ? `: ${t(`verbosity.${level}`)}` : ""}
          {t("engineer.ceiling.after")}
        </span>
      </div>
    </div>
  );
}

// Car · track · lap from the live frame, sampled once a second — this page
// has no gauges, so the 15 Hz frame hook would only re-render it for nothing.
function useSessionLine(): string {
  const t = useT();
  const [line, setLine] = useState("");
  useEffect(() => {
    const tick = () => {
      const f = liveFrameRef.current;
      if (!f || performance.now() - liveFrameRef.at > STALE_AFTER_MS) {
        setLine(t("engineer.noTelemetry"));
        return;
      }
      setLine(
        [f.car_name, f.track_name, f.current_lap > 0 ? t("engineer.lapN", { n: f.current_lap }) : ""]
          .filter(Boolean)
          .join(" · "),
      );
    };
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [t]);
  return line;
}

interface StateLook {
  sub: string;
  dot: string;
  halo: string;
  ring: string;
  pulse: boolean;
}

const LOOK: Record<VoiceState, Omit<StateLook, "sub">> = {
  speaking: {
    dot: "bg-throttle shadow-[0_0_10px_var(--color-throttle)]",
    halo: "bg-throttle/14",
    ring: "color-mix(in srgb, var(--color-throttle) 35%, transparent)",
    pulse: true,
  },
  off: {
    dot: "bg-ink-faint",
    halo: "bg-panel-2",
    ring: "var(--color-hairline)",
    pulse: false,
  },
  other: {
    dot: "bg-warn shadow-[0_0_10px_var(--color-warn)]",
    halo: "bg-warn/14",
    ring: "color-mix(in srgb, var(--color-warn) 30%, transparent)",
    pulse: false,
  },
  error: {
    dot: "bg-brake shadow-[0_0_10px_var(--color-brake)]",
    halo: "bg-brake/14",
    ring: "color-mix(in srgb, var(--color-brake) 40%, transparent)",
    pulse: false,
  },
};

type Tone = "ok" | "warn" | "bad" | "none";

const TONE_DOT: Record<Tone, string> = {
  ok: "bg-throttle",
  warn: "bg-warn",
  bad: "bg-brake",
  none: "bg-ink-ghost",
};
const TONE_TEXT: Record<Tone, string> = {
  ok: "text-ink-soft",
  warn: "text-warn",
  bad: "text-brake",
  none: "text-ink-ghost",
};

function Hero() {
  const t = useT();
  const s = useEngineer();
  const wsConnected = useTelemetry((st) => st.wsConnected);
  const [testing, setTesting] = useState(false);
  const me = clientId();
  const state = voiceState({ ...s, clientId: me });
  const armed = s.enabled && s.audioReady;

  // Which page holds the voice elsewhere — the server knows only the page,
  // not a device name.
  const otherPage =
    s.serverStatus?.clients.find(
      (c) => c.client_id === s.activeClientId && c.client_id !== me,
    )?.page ?? null;

  const sub: Record<VoiceState, string> = {
    speaking: t("engineer.hero.speakingSub"),
    off: t("engineer.hero.offSub"),
    other: otherPage
      ? t("engineer.hero.otherPageSub", { page: `/${otherPage}` })
      : t("engineer.hero.otherSub"),
    error: !s.supported
      ? t("engineer.hero.unsupportedSub")
      : s.speechError
        ? t("engineer.hero.errorSubReason", { reason: s.speechError })
        : t("engineer.hero.errorSub"),
  };
  const look = LOOK[state];

  // Voice already armed in this page load only needs the speaker role back;
  // otherwise the tap must also unlock audio (enableVoice does both).
  const takeOver = () => (armed ? s.claimSpeaker() : void s.enableVoice());

  const primary =
    state === "speaking" ? (
      <HeroButton onClick={() => s.setEnabled(false)}>{t("engineer.stopHere")}</HeroButton>
    ) : state === "off" ? (
      <HeroButton primary onClick={takeOver}>
        {t("engineer.turnOn")}
      </HeroButton>
    ) : state === "other" ? (
      <HeroButton primary onClick={takeOver}>
        {t("engineer.speakHere")}
      </HeroButton>
    ) : s.supported ? (
      <HeroButton primary onClick={() => void s.enableVoice()}>
        {t("engineer.tryAgain")}
      </HeroButton>
    ) : null;

  const serverTone: Tone = wsConnected ? "ok" : "bad";
  const callouts: [string, Tone] =
    s.serverStatus == null
      ? [t("engineer.check.unknown"), "none"]
      : !s.serverStatus.enabled
        ? [t("engineer.check.offOnServer"), "warn"]
        : s.serverStatus.active
          ? [t("engineer.check.running", { count: s.receivedCount }), "ok"]
          : [t("engineer.check.idle"), "none"];
  const audio: [string, Tone] = !s.supported
    ? [t("engineer.check.unsupported"), "bad"]
    : s.speechError && s.enabled
      ? [t("engineer.check.failed", { n: s.failedCount }), "bad"]
      : !armed
        ? [t("engineer.check.needsTap"), "warn"]
        : s.spokenCount > 0
          ? [t("engineer.check.speaking"), "ok"]
          : [t("engineer.check.armed"), "ok"];
  const speaker: [string, Tone] =
    state === "speaking"
      ? [t("engineer.check.thisDevice"), "ok"]
      : s.activeClientId !== "" && s.activeClientId !== me
        ? [
            otherPage
              ? t("engineer.check.anotherPage", { page: `/${otherPage}` })
              : t("engineer.check.anotherDevice"),
            "warn",
          ]
        : [t("engineer.check.none"), "none"];

  return (
    <div
      className="panel grid overflow-hidden [grid-template-columns:repeat(auto-fit,minmax(300px,1fr))]"
      style={{ boxShadow: `0 0 0 1px ${look.ring}` }}
    >
      <div className="flex flex-col gap-3.5 p-5">
        <div className="flex items-center gap-3">
          <span
            className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-full ${look.halo}`}
          >
            <span
              className={`h-3.5 w-3.5 rounded-full ${look.dot} ${look.pulse ? "animate-pulse-dot" : ""}`}
            />
          </span>
          <div>
            <div className="text-xl font-medium">{t(`engineer.hero.${state}`)}</div>
            <div className="mt-0.5 text-xs text-ink-dim">{sub[state]}</div>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {primary}
          <button
            className="btn min-h-11 rounded-md px-4 text-[12.5px]"
            disabled={!s.supported}
            onClick={() => {
              s.testVoice();
              setTesting(true);
              window.setTimeout(() => setTesting(false), 1500);
            }}
          >
            {testing ? t("engineer.playing") : t("engineer.testVoice")}
          </button>
        </div>
        <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-2 gap-y-1.5">
          <Check
            k={t("engineer.check.server")}
            v={wsConnected ? t("engineer.check.connected") : t("engineer.check.offline")}
            tone={serverTone}
          />
          <Check k={t("engineer.check.callouts")} v={callouts[0]} tone={callouts[1]} />
          <Check k={t("engineer.check.audio")} v={audio[0]} tone={audio[1]} />
          <Check k={t("engineer.check.speaker")} v={speaker[0]} tone={speaker[1]} />
        </div>
      </div>
      <LastCallout />
    </div>
  );
}

function HeroButton({
  primary = false,
  onClick,
  children,
}: {
  primary?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      className={`btn ${primary ? "btn-primary" : ""} min-h-11 rounded-md px-5 text-[13px] font-semibold`}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

function Check({ k, v, tone }: { k: string; v: string; tone: Tone }) {
  return (
    <div className="flex min-w-0 items-center gap-2 text-[11.5px]">
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${TONE_DOT[tone]}`} />
      <span className="whitespace-nowrap text-ink-faint">{k}</span>
      <span className={`ml-auto truncate font-tabular ${TONE_TEXT[tone]}`}>{v}</span>
    </div>
  );
}

function LastCallout() {
  const t = useT();
  const word = useCalloutWording();
  const latest = useEngineer((s) => s.history[0] ?? null);
  const receipt = useEngineer((s) => (latest ? s.receipts[latest.id] : undefined));
  const supported = useEngineer((s) => s.supported);
  const replay = useEngineer((s) => s.replay);
  const now = useNow();

  return (
    <div className="flex flex-col justify-center gap-2.5 bg-panel-2 p-5">
      <div className="flex items-center gap-2">
        <span className="section-header">{t("engineer.lastCallout")}</span>
        {latest && <CalloutPill category={latest.category} />}
        {receipt && (
          <span className="font-tabular text-[10.5px] text-ink-faint">
            {formatAge(now - receipt.receivedAt, t)}
          </span>
        )}
        {latest && (
          <button
            className="ml-auto text-[11px] text-accent hover:underline disabled:text-ink-ghost disabled:no-underline"
            disabled={!supported}
            onClick={() => replay(latest)}
          >
            {t("engineer.replay")}
          </button>
        )}
      </div>
      {latest ? (
        <div className="text-2xl font-medium leading-tight [text-wrap:balance]">
          {t("engineer.quoted", { text: word(latest) })}
        </div>
      ) : (
        <div className="text-[13px] text-ink-faint">{t("engineer.nothingYet")}</div>
      )}
    </div>
  );
}

function WhatYoullHear() {
  const t = useT();
  const ui = useLanguage();
  const calloutLanguage = useEngineer((s) => s.calloutLanguage);
  const language = spokenLanguage(calloutLanguage, ui);
  const verbosity = useEngineer((s) => s.verbosity);
  const categories = useEngineer((s) => s.categories);
  const setVerbosity = useEngineer((s) => s.setVerbosity);
  const toggleCategory = useEngineer((s) => s.toggleCategory);
  const serverStatus = useEngineer((s) => s.serverStatus);
  // The server's own verbosity is a ceiling: it decides what is ever sent,
  // and this browser can only narrow it further.
  const serverCategories = serverStatus?.categories ?? null;
  const inMode = (c: CalloutCategory) => VERBOSITY_CATEGORIES[verbosity].includes(c);
  const onServer = (c: CalloutCategory) =>
    serverCategories == null || serverCategories.includes(c);
  const greyed = CALLOUT_CATEGORIES.filter((c) => inMode(c) && !onServer(c)).length;

  return (
    <div className="panel">
      <div className="flex items-baseline gap-2 px-4 py-2.5">
        <span className="section-header">{t("engineer.hear.title")}</span>
        <span className="text-[10.5px] text-ink-faint">{t("engineer.thisDeviceOnly")}</span>
      </div>
      <div className="rule" />
      <div className="flex flex-col gap-3 px-4 py-3.5">
        <div className="grid grid-cols-3 gap-1.5" role="radiogroup" aria-label={t("engineer.verbosity")}>
          {VERBOSITIES.map((v) => (
            <button
              key={v}
              role="radio"
              aria-checked={verbosity === v}
              className={`flex min-h-11 flex-col gap-[3px] rounded-md border px-3 py-2.5 text-left transition-colors ${
                verbosity === v
                  ? "border-accent/60 bg-accent/10"
                  : "border-edge hover:border-edge-bright"
              }`}
              onClick={() => setVerbosity(v)}
            >
              <span className={`text-[13px] capitalize ${verbosity === v ? "text-accent-300" : "text-ink"}`}>
                {t(`verbosity.${v}`)}
              </span>
              <span className="text-[10.5px] leading-snug text-ink-dim">{t(`verbosity.${v}.hint`)}</span>
            </button>
          ))}
        </div>

        <div className="flex flex-wrap gap-1.5">
          {CALLOUT_CATEGORIES.map((c) => {
            const live = inMode(c) && onServer(c);
            const picked = live && categories.includes(c);
            return (
              <button
                key={c}
                disabled={!live}
                aria-pressed={picked}
                title={
                  !onServer(c)
                    ? t("engineer.category.notSent", { hint: t(`category.${c}.hint`) })
                    : !inMode(c)
                      ? t("engineer.category.offAt", {
                          hint: t(`category.${c}.hint`),
                          verbosity: t(`verbosity.${verbosity}`),
                        })
                      : t(`category.${c}.hint`)
                }
                className={`rounded border px-2.5 py-[5px] text-[11.5px] capitalize transition-colors disabled:cursor-default disabled:opacity-40 ${
                  picked
                    ? "border-accent/50 bg-accent/10 text-accent-300"
                    : "border-edge text-ink-faint enabled:hover:text-ink"
                }`}
                onClick={() => toggleCategory(c, !categories.includes(c))}
              >
                {t(`category.${c}`)}
              </button>
            );
          })}
        </div>
        <span className="text-[11px] text-ink-faint">
          {greyed > 0 ? (
            <>
              {t("engineer.hear.greyed", {
                count: greyed,
                verbosity: serverStatus ? t(`verbosity.${serverStatus.verbosity}`) : "",
              })}{" "}
              <a className="text-accent hover:underline" href={SETTINGS_ENGINEER}>
                {t("engineer.settingsLink")}
              </a>
              {t("engineer.hear.greyedAfter")}
            </>
          ) : (
            t("engineer.hear.tapToMute")
          )}
          {verbosity === "coach" && serverStatus && !serverStatus.coaching_ready && (
            <> {t("engineer.hear.coachingWaits")}</>
          )}
        </span>

        <details className="text-[11px] text-ink-dim">
          <summary className="cursor-pointer text-ink-faint hover:text-ink-dim">
            {t("engineer.hear.samples")}
          </summary>
          <div className="mt-2 flex flex-col gap-1.5 leading-snug">
            {CALLOUT_CATEGORIES.map((c) => (
              <span key={c} className={inMode(c) && onServer(c) ? "" : "opacity-50"}>
                <b className="font-medium capitalize text-ink">{t(`category.${c}`)}</b> —{" "}
                {CALLOUT_CATALOG[c].callouts
                  .map((x) => t("engineer.quoted", { text: calloutExample(x, language) }))
                  .join(" · ")}
              </span>
            ))}
          </div>
        </details>
      </div>
    </div>
  );
}

function VoicePanel() {
  const t = useT();
  const ui = useLanguage();
  const s = useEngineer();
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);
  useEffect(() => onVoicesChanged(setVoices), []);
  const language = spokenLanguage(s.calloutLanguage, ui);
  const options = voiceOptions(voices, language, s.voiceURI);

  return (
    <div className="panel">
      <div className="px-4 py-2.5">
        <span className="section-header">{t("engineer.voice")}</span>
      </div>
      <div className="rule" />
      <div className="flex flex-col gap-3 px-4 py-3.5">
        {s.supported && voices.length === 0 && (
          <p className="rounded-md border border-warn/40 bg-warn/10 p-2 text-[11px] text-warn">
            {t("engineer.noVoices")}
          </p>
        )}
        <CalloutLanguageRow />
        <VoiceLanguageNotice voices={voices} language={language} />
        {/* Native select, not ui/Select: the on-device/network split needs
            optgroups. A network-backed voice accepts speak() and can then
            never start, which surfaces only as "no response from the speech
            engine"; grouping is how the two are told apart. */}
        <select
          aria-label={t("engineer.voice")}
          className="w-full rounded-[5px] border border-edge bg-panel-2 px-2.5 py-2 text-[12.5px] text-ink-soft focus:border-accent focus:outline-none"
          value={options.value}
          disabled={!s.supported}
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
                  {v.name} — {v.lang}
                </option>
              ))}
            </optgroup>
          ))}
        </select>

        <div className="grid grid-cols-3 gap-3.5">
          <Slider
            label={t("engineer.volume")}
            value={s.volume}
            display={`${Math.round(s.volume * 100)}%`}
            min={0}
            max={1}
            onChange={(volume) => s.setAudio({ volume })}
          />
          <Slider
            label={t("engineer.rate")}
            value={s.rate}
            display={`${s.rate.toFixed(2)}×`}
            min={0.6}
            max={1.6}
            onChange={(rate) => s.setAudio({ rate })}
          />
          <Slider
            label={t("engineer.pitch")}
            value={s.pitch}
            display={s.pitch.toFixed(2)}
            min={0.6}
            max={1.6}
            onChange={(pitch) => s.setAudio({ pitch })}
          />
        </div>

        <div className="flex flex-col gap-0.5">
          <ToggleRow
            label={t("engineer.captions")}
            hint={t("engineer.captionsHint")}
            checked={s.captions}
            onChange={s.setCaptions}
          />
          <ToggleRow
            label={t("engineer.quietHidden")}
            hint={t("engineer.quietHiddenHint")}
            checked={s.muteWhenHidden}
            onChange={s.setMuteWhenHidden}
          />
        </div>
      </div>
    </div>
  );
}

function Slider({
  label,
  value,
  display,
  min,
  max,
  onChange,
}: {
  label: string;
  value: number;
  display: string;
  min: number;
  max: number;
  onChange: (v: number) => void;
}) {
  return (
    <label className="flex min-w-0 flex-col gap-1">
      <span className="flex justify-between text-[11px] text-ink-dim">
        <span>{label}</span>
        <span className="font-tabular">{display}</span>
      </span>
      <input
        type="range"
        className="w-full"
        min={min}
        max={max}
        step={0.05}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </label>
  );
}

function ToggleRow({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string;
  hint: string;
  checked: boolean;
  onChange: (on: boolean) => void;
}) {
  return (
    <div className="flex items-center gap-3 py-1.5">
      <Toggle checked={checked} onCheckedChange={onChange} ariaLabel={label} />
      <span className="text-[12.5px]">{label}</span>
      <span className="text-[11px] text-ink-faint">{hint}</span>
    </div>
  );
}

function CalloutFeed() {
  const t = useT();
  const word = useCalloutWording();
  const history = useEngineer((s) => s.history);
  const receipts = useEngineer((s) => s.receipts);

  return (
    <div className="panel min-w-0">
      <div className="flex flex-wrap items-baseline gap-2 px-4 py-2.5">
        <span className="section-header">{t("engineer.feed.title")}</span>
        <span className="text-[10.5px] text-ink-faint">{t("engineer.feed.sub")}</span>
      </div>
      <div className="rule" />
      {history.length === 0 ? (
        <p className="px-4 py-3 text-[11px] text-ink-faint">{t("engineer.nothingYet")}</p>
      ) : (
        <div className="flex flex-col px-4 pb-2.5 pt-1">
          {history.map((callout, i) => {
            const receipt = receipts[callout.id];
            const note = receipt?.note ?? null;
            return (
              <div
                // Index as tie-breaker: a re-sent id must not collide.
                key={`${callout.id}-${i}`}
                className={`rule-row grid grid-cols-[52px_minmax(0,1fr)] gap-2.5 py-[9px] ${
                  note ? "opacity-55" : ""
                }`}
              >
                <span className="font-tabular text-[10.5px] text-ink-faint">
                  {receipt?.lap ? t("engineer.feed.lap", { n: receipt.lap }) : "—"}
                </span>
                <div className="flex flex-col gap-1">
                  <span className="flex items-center gap-1.5">
                    <CalloutPill category={callout.category} />
                    {note && <span className="text-[10px] text-ink-ghost">{t(note.key, note.vars)}</span>}
                  </span>
                  <span className="text-[12.5px] leading-snug">{word(callout)}</span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
