// Pure Race Engineer display logic shared by /engineer and /dash: which of
// the four voice states this device is in, and why a callout was not heard
// here. Kept out of the store so both are testable without a speech engine.

import type { MessageKey, Translate, Vars } from "@/i18n";
import {
  VERBOSITY_CATEGORIES,
  type CalloutAckStatus,
  type CalloutCategory,
  type Verbosity,
} from "./types";

/**
 * speaking — this device holds the speaker role and audio is armed.
 * off      — nothing here will talk until the user taps (browsers need that tap).
 * other    — another browser has the voice; only one device speaks at a time.
 * error    — this device wants to speak and the engine refuses.
 */
export type VoiceState = "speaking" | "off" | "other" | "error";

export interface VoiceStateInput {
  supported: boolean;
  enabled: boolean;
  audioReady: boolean;
  speechError: string | null;
  activeClientId: string;
  clientId: string;
}

export function voiceState(s: VoiceStateInput): VoiceState {
  if (!s.supported) return "error";
  // A failed Test while voice is off is reported on the test itself; the hero
  // only turns red for a device that is actually trying to speak.
  if (s.enabled && s.speechError) return "error";
  const isSpeaker = s.activeClientId !== "" && s.activeClientId === s.clientId;
  if (s.enabled && s.audioReady && isSpeaker) return "speaking";
  if (s.activeClientId !== "" && !isSpeaker) return "other";
  return "off";
}

/**
 * Why this device did not say a callout, as a message id for the feed — or
 * null when it was spoken (or is still waiting its turn). Worked out when the
 * ack happens, so a later verbosity change does not rewrite history; worded
 * when shown, so a language change does.
 */
export function ackNote(
  status: CalloutAckStatus,
  category: CalloutCategory,
  verbosity: Verbosity,
): { key: MessageKey; vars?: Vars } | null {
  switch (status) {
    case "spoken":
      return null;
    case "category_disabled":
      return VERBOSITY_CATEGORIES[verbosity].includes(category)
        ? { key: "engineer.note.muted" }
        : { key: `engineer.note.offAt.${verbosity}` };
    case "not_active_speaker":
      return { key: "engineer.note.otherDevice" };
    case "disabled":
      return { key: "engineer.note.voiceOff" };
    case "expired":
      return { key: "engineer.note.expired" };
    case "duplicate":
      return { key: "engineer.note.duplicate" };
    case "interrupted":
      return { key: "engineer.note.interrupted" };
    case "speech_error":
      return { key: "engineer.note.speechFailed" };
  }
}

/** "4 s ago" / "3 min ago" for callout ages; both clocks are the browser's. */
export function formatAge(ms: number, t: Translate): string {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return t("engineer.age.seconds", { n: s });
  const m = Math.floor(s / 60);
  if (m < 60) return t("engineer.age.minutes", { n: m });
  return t("engineer.age.hours", { n: Math.floor(m / 60) });
}
