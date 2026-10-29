// Race Engineer client state: browser-local voice preferences (persisted),
// live status from the server, and the speech queue that turns callouts into
// speech. Only /dash and /engineer mount the voice client — the OBS overlay
// receives the same events but must never speak (several open browser sources
// would otherwise all talk at once).

import { useCallback } from "react";
import { create } from "zustand";
import { persist } from "zustand/middleware";
import { useI18n, useLanguage, type MessageKey, type Vars } from "@/i18n";
import { enabledPhrase, wordCallout, type Worded } from "@/i18n/callouts";
import { speechTag, type Language } from "@/i18n/languages";
import {
  MAX_QUEUE,
  VoiceQueue,
  getVoices,
  speakTest,
  speechSupported,
  voicesFor,
  type QueuedCallout,
  type SpeechOptions,
} from "@/lib/speech";
import {
  CALLOUT_CATEGORIES,
  VERBOSITY_CATEGORIES,
  type CalloutAckStatus,
  type CalloutCategory,
  type RaceEngineerStatus,
  type Verbosity,
  type VoiceCallout,
} from "@/lib/types";
import { ackNote } from "@/lib/engineerState";
import { sendWs } from "@/lib/wsBus";
import { liveFrameRef } from "@/store/telemetry";

const CLIENT_ID_KEY = "gt7.clientId";
export const ENGINEER_SETTINGS_KEY = "gt7-race-engineer-settings-v1";

/** Callouts in the interface's language, or one of their own. */
export type CalloutLanguage = Language | "ui";

/** Stable per-browser identity, so a refresh keeps the active-speaker claim. */
export function clientId(): string {
  let id = localStorage.getItem(CLIENT_ID_KEY);
  if (!id) {
    id = randomId();
    localStorage.setItem(CLIENT_ID_KEY, id);
  }
  return id;
}

/**
 * `crypto.randomUUID` exists only in secure contexts, and this dashboard is
 * typically served plain-HTTP from a Pi on the LAN — where it is undefined and
 * the call throws, taking claimSpeaker/sendCapabilities down with it.
 * `getRandomValues` has no such restriction.
 */
function randomId(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40; // UUID v4 version/variant bits, so the
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // id looks the same either way
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return (
    `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-` +
    `${hex.slice(16, 20)}-${hex.slice(20)}`
  );
}

export type VoicePage = "dash" | "engineer";

/** How many callouts the feed keeps; /engineer lists them for the session. */
const HISTORY_LIMIT = 50;

/**
 * What this browser knows about a callout beyond the server's payload: when
 * it arrived (browser clock — the server's may disagree), the lap it arrived
 * on, and why it was not spoken here, if it wasn't.
 */
export interface CalloutReceipt {
  receivedAt: number;
  lap: number | null;
  status: CalloutAckStatus | null;
  /** A message id, so the feed reads in whatever language is shown now. */
  note: { key: MessageKey; vars?: Vars } | null;
}

interface EngineerState {
  // --- persisted preferences (this device only) ---
  /** The user's stored preference. Speech also needs `audioReady`. */
  enabled: boolean;
  voiceURI: string;
  /** What language callouts are said and captioned in. */
  calloutLanguage: CalloutLanguage;
  volume: number;
  rate: number;
  pitch: number;
  verbosity: Verbosity;
  categories: CalloutCategory[];
  captions: boolean;
  muteWhenHidden: boolean;
  /** Whether this device wants the speaker role when it connects. */
  wantsSpeaker: boolean;

  // --- runtime (not persisted) ---
  /**
   * A stored preference does not carry a browser's autoplay permission across
   * a restart, so speech stays off until the user clicks Enable in this page
   * load and a test utterance goes through.
   */
  audioReady: boolean;
  supported: boolean;
  page: VoicePage | null;
  activeClientId: string;
  serverStatus: RaceEngineerStatus | null;
  lastSpoken: VoiceCallout | null;
  /** Why the last utterance failed — the difference between "nothing is
   *  happening" and "the browser refused to make a sound". */
  speechError: string | null;
  /** Utterances the engine actually started, and ones that failed. */
  spokenCount: number;
  failedCount: number;
  speaking: VoiceCallout | null;
  queue: QueuedCallout[];
  history: VoiceCallout[];
  /** Keyed by callout id, for every callout still in `history`. */
  receipts: Record<string, CalloutReceipt>;
  /** Callouts received in this page load, including ones aged out of history. */
  receivedCount: number;

  setEnabled: (on: boolean) => void;
  setVoice: (voiceURI: string) => void;
  setCalloutLanguage: (language: CalloutLanguage) => void;
  setAudio: (patch: Partial<Pick<EngineerState, "volume" | "rate" | "pitch">>) => void;
  setVerbosity: (v: Verbosity) => void;
  toggleCategory: (c: CalloutCategory, on: boolean) => void;
  setCaptions: (on: boolean) => void;
  setMuteWhenHidden: (on: boolean) => void;
  setPage: (page: VoicePage | null) => void;
  claimSpeaker: () => void;
  releaseSpeaker: () => void;
  testVoice: () => void;
  /** Say a past callout again, here, whatever the speaker role. */
  replay: (callout: VoiceCallout) => void;
  /** Click handler for "Enable Race Engineer": arms audio, claims, registers. */
  enableVoice: () => Promise<void>;
  handleCallout: (callout: VoiceCallout) => void;
  applyServerStatus: (status: RaceEngineerStatus) => void;
  setActiveClient: (id: string) => void;
  sendCapabilities: () => void;
  isActiveSpeaker: () => boolean;
}

/** The language callouts are in on this device right now. */
export function spokenLanguage(choice: CalloutLanguage, ui: Language): Language {
  return choice === "ui" ? ui : choice;
}

function currentLanguage(s: EngineerState): Language {
  return spokenLanguage(s.calloutLanguage, useI18n.getState().language);
}

function speechOptions(s: EngineerState): SpeechOptions {
  return {
    voiceURI: s.voiceURI,
    lang: speechTag(currentLanguage(s)),
    volume: s.volume,
    rate: s.rate,
    pitch: s.pitch,
  };
}

/**
 * What to say aloud for a translation, and in which voice language. A
 * device with no voice for the language says the English instead (the
 * captions stay translated): a Japanese sentence handed to an English voice
 * comes out as noise or silence, and the panel says why it fell back. An
 * empty voice list is not proof of anything — browsers fill it late.
 */
export function speakableWording(
  worded: Worded,
  english: string,
  voices: Pick<SpeechSynthesisVoice, "lang">[],
): { text: string; lang: string } {
  const tag = speechTag(worded.language);
  if (worded.language !== "en" && voices.length > 0 && voicesFor(voices, tag).length === 0) {
    return { text: english, lang: speechTag("en") };
  }
  return { text: worded.text, lang: tag };
}

function sayCallout(s: EngineerState, callout: VoiceCallout): { text: string; lang: string } {
  return speakableWording(wordCallout(callout, currentLanguage(s)), callout.text, getVoices());
}

function sayEnabled(s: EngineerState): { text: string; lang: string } {
  return speakableWording(enabledPhrase(currentLanguage(s)), enabledPhrase("en").text, getVoices());
}

/** The single queue instance, reachable from the disconnect path. */
let voiceQueue: VoiceQueue | null = null;

export function clearVoiceQueue(): void {
  voiceQueue?.clear();
}

export const useEngineer = create<EngineerState>()(
  persist(
    (set, get) => {
      const queue = new VoiceQueue({
        enabled: () => get().enabled && get().audioReady,
        activeSpeaker: () => get().isActiveSpeaker(),
        categoryEnabled: (c) =>
          get().categories.includes(c) &&
          VERBOSITY_CATEGORIES[get().verbosity].includes(c),
        muted: () => get().muteWhenHidden && document.hidden,
        options: () => speechOptions(get()),
        word: (callout) => sayCallout(get(), callout),
        onAck: (calloutId, status, reason) => {
          sendWs({
            type: "voice_callout_ack",
            data: {
              callout_id: calloutId,
              client_id: clientId(),
              status,
              spoken_at_ms: Date.now(),
              ...(reason ? { reason } : {}),
            },
          });
          if (status === "spoken") {
            const spoken = get().history.find((c) => c.id === calloutId) ?? null;
            if (spoken) set({ lastSpoken: spoken });
          }
          set((st) => {
            const receipt = st.receipts[calloutId];
            const callout = st.history.find((c) => c.id === calloutId);
            // First verdict wins: a re-sent id acks "duplicate" and must not
            // turn a callout that was heard into one that wasn't.
            if (!receipt || !callout || receipt.status != null) return {};
            return {
              receipts: {
                ...st.receipts,
                [calloutId]: {
                  ...receipt,
                  status,
                  note: ackNote(status, callout.category, st.verbosity),
                },
              },
            };
          });
        },
        onSpeaking: (callout) => set({ speaking: callout }),
        onQueue: (items) => set({ queue: items }),
        onSpeechStarted: () =>
          set((st) => ({
            speechError: null,
            audioReady: true,
            spokenCount: st.spokenCount + 1,
          })),
        onSpeechFailure: (reason) =>
          set((st) => ({ speechError: reason, failedCount: st.failedCount + 1 })),
      });
      voiceQueue = queue;

      return {
        enabled: false,
        voiceURI: "",
        calloutLanguage: "ui",
        volume: 1,
        rate: 1.05,
        pitch: 1,
        verbosity: "race",
        categories: [...CALLOUT_CATEGORIES],
        captions: true,
        muteWhenHidden: false,
        wantsSpeaker: true,

        audioReady: false,
        supported: speechSupported(),
        page: null,
        activeClientId: "",
        serverStatus: null,
        lastSpoken: null,
        speechError: null,
        spokenCount: 0,
        failedCount: 0,
        speaking: null,
        queue: [],
        history: [],
        receipts: {},
        receivedCount: 0,

        setEnabled: (on) => {
          set({ enabled: on });
          if (!on) {
            queue.clear();
            set({ audioReady: false });
            get().releaseSpeaker();
          }
          get().sendCapabilities();
        },
        setVoice: (voiceURI) => set({ voiceURI }),
        setCalloutLanguage: (calloutLanguage) => set({ calloutLanguage }),
        setAudio: (patch) => set(patch),
        setVerbosity: (verbosity) => set({ verbosity }),
        toggleCategory: (c, on) =>
          set((s) => ({
            categories: CALLOUT_CATEGORIES.filter((cat) =>
              cat === c ? on : s.categories.includes(cat),
            ),
          })),
        setCaptions: (captions) => set({ captions }),
        setMuteWhenHidden: (muteWhenHidden) => set({ muteWhenHidden }),
        setPage: (page) => set({ page }),

        claimSpeaker: () => {
          set({ wantsSpeaker: true });
          sendWs({ type: "claim_voice_output", data: { client_id: clientId() } });
        },
        releaseSpeaker: () => {
          set({ wantsSpeaker: false });
          queue.clear();
          sendWs({ type: "release_voice_output", data: { client_id: clientId() } });
        },

        testVoice: () => {
          set({ speechError: null });
          queue.resetFailures();
          const said = sayEnabled(get());
          speakTest(said.text, { ...speechOptions(get()), lang: said.lang }, {
            onStart: () => set({ speechError: null, audioReady: true }),
            onError: (reason) => set({ speechError: reason }),
          });
        },

        replay: (callout) => {
          const said = sayCallout(get(), callout);
          speakTest(said.text, { ...speechOptions(get()), lang: said.lang }, {
            onError: (reason) => set({ speechError: reason }),
          });
        },

        enableVoice: async () => {
          const supported = speechSupported();
          set({ supported, enabled: true });
          if (supported) {
            // The test utterance is also the browser's audio unlock, so it has
            // to happen inside the click, not after an await. Whether it is
            // ALLOWED to make a sound is only known when it starts — until
            // then "ready" would be a guess, and a wrong one is what makes a
            // silent setup impossible to diagnose.
            set({ speechError: null, audioReady: true });
            queue.resetFailures();
            const said = sayEnabled(get());
            speakTest(said.text, { ...speechOptions(get()), lang: said.lang }, {
              onStart: () => {
                set({ speechError: null });
                get().sendCapabilities();
              },
              onError: (reason) => {
                set({ speechError: reason });
                get().sendCapabilities();
              },
            });
          }
          get().sendCapabilities();
          get().claimSpeaker();
        },

        handleCallout: (callout) => {
          set((s) => {
            const history = [callout, ...s.history].slice(0, HISTORY_LIMIT);
            const receipts: Record<string, CalloutReceipt> = {};
            for (const c of history) {
              receipts[c.id] = s.receipts[c.id] ?? {
                receivedAt: Date.now(),
                lap: liveFrameRef.current?.current_lap || null,
                status: null,
                note: null,
              };
            }
            return { history, receipts, receivedCount: s.receivedCount + 1 };
          });
          queue.enqueue(callout);
        },

        applyServerStatus: (status) =>
          set({ serverStatus: status, activeClientId: status.active_client_id }),

        setActiveClient: (id) => {
          set({ activeClientId: id });
          if (id !== clientId()) queue.clear();
        },

        sendCapabilities: () => {
          const s = get();
          if (!s.page) return; // pages that never speak don't register
          sendWs({
            type: "client_capabilities",
            data: {
              client_id: clientId(),
              page: s.page,
              voice_supported: s.supported,
              voice_enabled: s.enabled && s.audioReady,
            },
          });
        },

        isActiveSpeaker: () => {
          const s = get();
          return s.activeClientId !== "" && s.activeClientId === clientId();
        },
      };
    },
    {
      name: ENGINEER_SETTINGS_KEY,
      // Runtime state (queue, speaking, server status) must never be restored
      // from disk — callouts are live events, not saved notifications.
      partialize: (s) => ({
        enabled: s.enabled,
        voiceURI: s.voiceURI,
        calloutLanguage: s.calloutLanguage,
        volume: s.volume,
        rate: s.rate,
        pitch: s.pitch,
        verbosity: s.verbosity,
        categories: s.categories,
        captions: s.captions,
        muteWhenHidden: s.muteWhenHidden,
        wantsSpeaker: s.wantsSpeaker,
      }),
    },
  ),
);

/**
 * A callout's words as this device shows them (captions, the feed, Last
 * spoken). Re-renders the caller when either language changes.
 */
export function useCalloutWording(): (callout: VoiceCallout) => string {
  const ui = useLanguage();
  const choice = useEngineer((s) => s.calloutLanguage);
  const language = spokenLanguage(choice, ui);
  return useCallback((callout: VoiceCallout) => wordCallout(callout, language).text, [language]);
}

export { MAX_QUEUE };
export type { CalloutAckStatus };
