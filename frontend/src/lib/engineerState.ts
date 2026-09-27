// Pure Race Engineer display logic shared by /engineer and /dash: which of
// the four voice states this device is in, and why a callout was not heard
// here. Kept out of the store so both are testable without a speech engine.

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
 * Why this device did not say a callout, in the feed's words — or null when
 * it was spoken (or is still waiting its turn). Worked out when the ack
 * happens, so a later verbosity change does not rewrite history.
 */
export function ackNote(
  status: CalloutAckStatus,
  category: CalloutCategory,
  verbosity: Verbosity,
): string | null {
  switch (status) {
    case "spoken":
      return null;
    case "category_disabled":
      return VERBOSITY_CATEGORIES[verbosity].includes(category)
        ? "not heard — muted on this device"
        : `not heard — off at ${verbosity}`;
    case "not_active_speaker":
      return "not heard — another device speaks";
    case "disabled":
      return "not heard — voice off";
    case "expired":
      return "not heard — too late to say";
    case "duplicate":
      return "not heard — just said";
    case "interrupted":
      return "cut off";
    case "speech_error":
      return "not heard — speech failed";
  }
}

/** "4 s ago" / "3 min ago" for callout ages; both clocks are the browser's. */
export function formatAge(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s} s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ago`;
  return `${Math.floor(m / 60)} h ago`;
}
