// Race Engineer controls: enable/claim voice, pick a voice, set volume/rate,
// choose verbosity and categories. Shared by the /engineer page and the
// dashboard's settings drawer, so it has to work in a narrow column.

import { useEffect, useState } from "react";
import { CALLOUT_CATALOG } from "@/lib/calloutCatalog";
import { onVoicesChanged } from "@/lib/speech";
import {
  CALLOUT_CATEGORIES,
  VERBOSITY_CATEGORIES,
  type CalloutCategory,
  type Verbosity,
} from "@/lib/types";
import { clientId, useEngineer } from "@/store/engineer";
import { useTelemetry } from "@/store/telemetry";

const VERBOSITY_HINT: Record<Verbosity, string> = {
  minimal: "critical warnings, fuel shortage, pit window, final lap",
  race: "lap times, personal bests, positions, fuel and race progress",
  coach: "everything, plus repeated lockups, wheelspin and corner feedback",
};

const CATEGORY_HINT: Record<CalloutCategory, string> = {
  system: "status messages",
  lap: "lap times",
  pace: "personal bests",
  race: "final lap, halfway",
  position: "position changes",
  fuel: "fuel range",
  strategy: "pit window, fuel shortage",
  engine: "temperatures, oil pressure",
  tires: "tire temperature and balance",
  chassis: "ride height, kerbs",
  coaching: "lockups, wheelspin, corner losses",
};

export function RaceEngineerPanel({ compact = false }: { compact?: boolean }) {
  const s = useEngineer();
  const wsConnected = useTelemetry((st) => st.wsConnected);
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);

  useEffect(() => onVoicesChanged(setVoices), []);

  const isSpeaker = s.activeClientId !== "" && s.activeClientId === clientId();
  const otherSpeaker = s.activeClientId !== "" && !isSpeaker;
  // The server's own verbosity/category setting is a ceiling: it decides what
  // is ever sent, and this browser can only narrow it further.
  const serverCategories = s.serverStatus?.categories ?? null;

  return (
    <div className={`space-y-3 ${compact ? "text-xs" : "text-sm"} p-3`}>
      <div className="flex flex-wrap items-center gap-2">
        {!s.enabled || !s.audioReady ? (
          <button className="btn" onClick={() => void s.enableVoice()}>
            Enable Race Engineer
          </button>
        ) : (
          <button className="btn" onClick={() => s.setEnabled(false)}>
            Disable voice
          </button>
        )}
        <button className="btn" disabled={!s.supported} onClick={() => s.testVoice()}>
          Test voice
        </button>
        {s.enabled && s.audioReady && !isSpeaker && (
          <button className="btn" onClick={() => s.claimSpeaker()}>
            Use this device for voice output
          </button>
        )}
        {isSpeaker && (
          <button className="btn" onClick={() => s.releaseSpeaker()}>
            Stop speaking here
          </button>
        )}
      </div>

      <div className="grid grid-cols-2 gap-x-4 gap-y-1 font-tabular text-[11px]">
        <Row k="Voice preference" v={s.enabled ? "enabled" : "off"} ok={s.enabled} />
        <Row
          k="Browser audio"
          v={
            !s.supported
              ? "unsupported"
              : s.speechError
                ? `${s.failedCount} failed`
                : s.spokenCount > 0
                  ? "speaking"
                  : s.audioReady
                    ? "armed"
                    : "needs a click"
          }
          ok={s.supported && (s.spokenCount > 0 || s.audioReady) && !s.speechError}
        />
        <Row
          k="Active speaker"
          v={isSpeaker ? "this device" : otherSpeaker ? "another device" : "none"}
          ok={isSpeaker}
        />
        <Row k="Connection" v={wsConnected ? "connected" : "offline"} ok={wsConnected} />
        <Row
          k="Server callouts"
          v={
            s.serverStatus == null
              ? "unknown"
              : !s.serverStatus.enabled
                ? "disabled"
                : s.serverStatus.active
                  ? "running"
                  : "idle"
          }
          ok={s.serverStatus?.active ?? false}
        />
        <Row k="Queued" v={String(s.queue.length)} />
        {s.verbosity === "coach" && (
          <Row
            k="Coaching"
            v={s.serverStatus?.coaching_ready ? "ready" : "needs a few laps"}
            ok={s.serverStatus?.coaching_ready ?? false}
          />
        )}
      </div>

      {s.speechError && (
        <p className="rounded-md border border-brake/40 bg-brake/10 p-2 text-[11px] text-brake">
          Speech failed: {s.speechError}. Callouts are still shown on screen.
        </p>
      )}
      {!s.supported && (
        <p className="rounded-md border border-warn/40 bg-warn/10 p-2 text-[11px] text-warn">
          This browser has no speech synthesis. Callouts still appear as
          on-screen captions.
        </p>
      )}
      {s.supported && voices.length === 0 && (
        <p className="rounded-md border border-warn/40 bg-warn/10 p-2 text-[11px] text-warn">
          No speech voices found. Browsers load the list a moment after the page,
          so this may clear on its own — but if it does not, the browser has no
          voices to speak with (Chrome on Linux needs a speech engine such as
          speech-dispatcher installed).
        </p>
      )}

      <label className="block">
        <span className="mb-1 block text-[11px] text-ink-dim">Voice</span>
        <select
          className="w-full rounded-md border border-edge bg-panel-2 px-2 py-1 text-xs focus:border-accent focus:outline-none"
          value={s.voiceURI}
          onChange={(e) => {
            const voice = voices.find((v) => v.voiceURI === e.target.value);
            s.setVoice(e.target.value, voice?.lang ?? s.lang);
          }}
        >
          <option value="">Browser default (on-device voice)</option>
          {/* On-device voices first and in their own group: a network-backed
              voice accepts speak() and can then never start, which surfaces
              only as "no response from the speech engine". The flat list gave
              no way to tell the two apart. */}
          {(["local", "network"] as const).map((kind) => {
            const group = voices.filter((v) => v.localService === (kind === "local"));
            if (!group.length) return null;
            return (
              <optgroup
                key={kind}
                label={kind === "local" ? "On-device (recommended)" : "Network — may not start"}
              >
                {group.map((v) => (
                  <option key={v.voiceURI} value={v.voiceURI}>
                    {v.name} ({v.lang})
                  </option>
                ))}
              </optgroup>
            );
          })}
        </select>
      </label>

      <div className="grid grid-cols-3 gap-2">
        <Slider
          label="Volume"
          value={s.volume}
          min={0}
          max={1}
          step={0.05}
          onChange={(volume) => s.setAudio({ volume })}
        />
        <Slider
          label="Rate"
          value={s.rate}
          min={0.6}
          max={1.6}
          step={0.05}
          onChange={(rate) => s.setAudio({ rate })}
        />
        <Slider
          label="Pitch"
          value={s.pitch}
          min={0.6}
          max={1.6}
          step={0.05}
          onChange={(pitch) => s.setAudio({ pitch })}
        />
      </div>

      <div>
        <span className="mb-1 block text-[11px] text-ink-dim">How much to say</span>
        <div className="flex gap-1">
          {(Object.keys(VERBOSITY_CATEGORIES) as Verbosity[]).map((mode) => (
            <button
              key={mode}
              title={VERBOSITY_HINT[mode]}
              className={`flex-1 rounded-md border px-2 py-1 text-xs capitalize ${
                s.verbosity === mode
                  ? "border-accent/60 bg-accent/10 text-ink"
                  : "border-edge bg-panel-2 text-ink-dim hover:text-ink"
              }`}
              onClick={() => s.setVerbosity(mode)}
            >
              {mode}
            </button>
          ))}
        </div>
        <p className="mt-1 text-[10px] text-ink-dim">{VERBOSITY_HINT[s.verbosity]}</p>
      </div>

      <div>
        <span className="mb-1 block text-[11px] text-ink-dim">Categories</span>
        <div className="grid grid-cols-2 gap-x-3">
          {CALLOUT_CATEGORIES.map((category) => {
            const inMode = VERBOSITY_CATEGORIES[s.verbosity].includes(category);
            const onServer = serverCategories == null || serverCategories.includes(category);
            return (
              <label
                key={category}
                className={`flex cursor-pointer items-baseline gap-1.5 text-xs ${
                  inMode && onServer ? "" : "opacity-40"
                }`}
                title={
                  !onServer
                    ? "the server never produces this category — raise its maximum "
                      + "verbosity in Admin → Race Engineer"
                    : CATEGORY_HINT[category]
                }
              >
                <input
                  type="checkbox"
                  className="translate-y-px accent-sky-400"
                  checked={s.categories.includes(category)}
                  onChange={(e) => s.toggleCategory(category, e.target.checked)}
                />
                <span className="capitalize">{category}</span>
              </label>
            );
          })}
        </div>
        <CalloutReference verbosity={s.verbosity} serverCategories={serverCategories} />
      </div>

      <div className="space-y-1">
        <label className="flex cursor-pointer items-baseline gap-2 text-xs">
          <input
            type="checkbox"
            className="translate-y-px accent-sky-400"
            checked={s.captions}
            onChange={(e) => s.setCaptions(e.target.checked)}
          />
          <span>Show callout captions on screen</span>
        </label>
        <label className="flex cursor-pointer items-baseline gap-2 text-xs">
          <input
            type="checkbox"
            className="translate-y-px accent-sky-400"
            checked={s.muteWhenHidden}
            onChange={(e) => s.setMuteWhenHidden(e.target.checked)}
          />
          <span>Stay quiet while this tab is hidden</span>
        </label>
      </div>

      {s.lastSpoken && (
        <div className="rounded-md border border-edge bg-panel-2 p-2">
          <div className="text-[10px] uppercase tracking-widest text-ink-dim">
            Last spoken
          </div>
          <div className="text-xs">{s.lastSpoken.text}</div>
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
}: {
  verbosity: Verbosity;
  serverCategories: CalloutCategory[] | null;
}) {
  return (
    <details className="mt-2 rounded-md border border-edge bg-panel-2/60">
      <summary className="cursor-pointer px-2 py-1 text-[11px] text-ink-dim hover:text-ink">
        What each category says
      </summary>
      <div className="space-y-2 px-2 pb-2">
        {CALLOUT_CATEGORIES.map((category) => {
          const info = CALLOUT_CATALOG[category];
          const inMode = VERBOSITY_CATEGORIES[verbosity].includes(category);
          const onServer = serverCategories == null || serverCategories.includes(category);
          return (
            <div key={category} className={inMode && onServer ? "" : "opacity-50"}>
              <div className="flex items-baseline gap-1.5">
                <span className="text-[11px] font-semibold capitalize">{category}</span>
                {!inMode && (
                  <span className="text-[10px] text-ink-dim">
                    — off at {verbosity} verbosity
                  </span>
                )}
                {inMode && !onServer && (
                  <span className="text-[10px] text-warn">
                    — the server never produces this: raise its maximum verbosity
                    in Admin → Race Engineer
                  </span>
                )}
              </div>
              <div className="text-[10px] text-ink-dim">{info.summary}</div>
              <ul className="mt-0.5 space-y-0.5">
                {info.callouts.map((callout) => (
                  <li key={callout.event} className="text-[10px] leading-snug">
                    <span className="text-ink">&ldquo;{callout.example}&rdquo;</span>
                    <span className="text-ink-dim"> — {callout.when}</span>
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
      <span className="text-ink-dim">{k}</span>
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
        <span className="font-tabular">{value.toFixed(2)}</span>
      </span>
      <input
        type="range"
        className="w-full accent-sky-400"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </label>
  );
}
