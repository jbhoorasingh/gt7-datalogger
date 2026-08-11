// Browser speech for Race Engineer callouts: voice selection, a controlled
// queue, expiry, deduplication and a watchdog.
//
// The Web Speech API's own queue is unusable here — it is FIFO, unbounded and
// has no notion of a message going stale, so a burst of events would still be
// talking about lap 4 on lap 6. We keep at most three messages, ordered by
// priority, and drop anything past its lifetime before speaking it.
//
// Engine quirks this works around: voices load asynchronously (Safari fires
// `voiceschanged` well after load), utterances silently never fire `end` on
// some builds (hence the watchdog), and iOS stops speech on screen lock.

import type { CalloutAckStatus, CalloutCategory, VoiceCallout } from "./types";

export const MAX_QUEUE = 3;
// A callout may still interrupt speech in progress if it is at least this
// urgent — matches the backend's "critical" band.
export const INTERRUPT_PRIORITY = 90;
// How long a spoken/rejected id is remembered, so a reconnect or a duplicate
// event can't say the same thing twice.
const RECENT_RETENTION_MS = 120_000;
// How long an utterance may take to START. Chrome's default voices are often
// network-backed and can take several seconds to begin — anything tighter
// cancels speech that was merely slow, which is the worse failure.
const START_TIMEOUT_MS = 10_000;
// Once it IS speaking, how long past the estimated duration to wait for `end`.
// ~180 ms per character is far slower than any real voice, so this only fires
// when the engine has hung mid-message.
const SPEAKING_TIMEOUT_MS = 5000;
// After this many failures in a row the engine is not going to start working
// on its own, and each further attempt costs a watchdog timeout that expires
// the messages queued behind it.
const MAX_CONSECUTIVE_FAILURES = 3;
const WATCHDOG_PER_CHAR_MS = 180;
// How long to wait for the browser to populate its voice list before speaking
// anyway. Chrome usually fills it within a frame or two of page load.
const VOICES_WAIT_MS = 2000;

export interface SpeechOptions {
  voiceURI: string;
  lang: string;
  volume: number;
  rate: number;
  pitch: number;
}

export interface QueuedCallout {
  callout: VoiceCallout;
  /** Local deadline — server clocks are not trusted (see VoiceCallout.ttl_ms). */
  deadline: number;
  /** Arrival order. Sorting is stable, so without this two callouts of equal
   *  priority keep insertion order and the queue cap drops the NEWER one. */
  seq: number;
}

export function speechSupported(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window;
}

export function getVoices(): SpeechSynthesisVoice[] {
  if (!speechSupported()) return [];
  return window.speechSynthesis.getVoices();
}

/**
 * Voices are populated asynchronously in most browsers. Calls back with the
 * list now (if any) and again when it changes; returns an unsubscribe.
 */
export function onVoicesChanged(cb: (voices: SpeechSynthesisVoice[]) => void): () => void {
  if (!speechSupported()) {
    cb([]);
    return () => {};
  }
  const handler = () => cb(getVoices());
  handler();
  window.speechSynthesis.addEventListener("voiceschanged", handler);
  return () => window.speechSynthesis.removeEventListener("voiceschanged", handler);
}

/**
 * Preferred voice, then same language, then the browser's English default,
 * then whatever exists. Never assume a voice name is installed.
 */
export function pickVoice(
  voices: SpeechSynthesisVoice[],
  opts: { voiceURI: string; lang: string },
): SpeechSynthesisVoice | null {
  if (voices.length === 0) return null;
  // An explicit choice is honoured even if it is network-backed: it is the
  // user's, and the failure path below reports clearly when it never starts.
  const exact = voices.find((v) => v.voiceURI === opts.voiceURI);
  if (exact) return exact;
  // Otherwise prefer ON-DEVICE voices at every tier. Chrome mixes local
  // system voices with network-backed Google ones in a single flat list
  // (~200 entries on macOS), and a network voice ACCEPTS speak() and can
  // then never start — no error, no `onstart`, just the watchdog firing
  // "no response from the speech engine". Local voices start immediately
  // and work offline, which is what live race callouts need anyway.
  const lang = opts.lang || "en";
  const base = lang.split("-")[0];
  const preferLocal = (list: SpeechSynthesisVoice[]) =>
    list.find((v) => v.localService) ?? list[0];
  const sameLang = voices.filter((v) => v.lang === lang);
  if (sameLang.length) return preferLocal(sameLang);
  const samePrefix = voices.filter((v) => v.lang.startsWith(base));
  if (samePrefix.length) return preferLocal(samePrefix);
  const english = voices.filter((v) => v.lang.startsWith("en"));
  if (english.length) return preferLocal(english);
  return voices.find((v) => v.default) ?? voices[0];
}

export function isExpired(item: QueuedCallout, now: number): boolean {
  return now >= item.deadline;
}

/** Highest priority first, newest first within a priority, capped at MAX_QUEUE. */
export function insertByPriority(
  queue: QueuedCallout[],
  item: QueuedCallout,
): QueuedCallout[] {
  const next = [...queue, item];
  next.sort((a, b) => b.callout.priority - a.callout.priority || b.seq - a.seq);
  return next.slice(0, MAX_QUEUE);
}

export interface QueueHooks {
  enabled: () => boolean;
  activeSpeaker: () => boolean;
  categoryEnabled: (category: CalloutCategory) => boolean;
  /** Optional extra gate, e.g. "mute while the tab is hidden". */
  muted?: () => boolean;
  options: () => SpeechOptions;
  onAck: (calloutId: string, status: CalloutAckStatus, reason?: string) => void;
  onSpeaking: (callout: VoiceCallout | null) => void;
  onQueue: (queue: QueuedCallout[]) => void;
  /** An utterance actually began — proof the engine works on this device. */
  onSpeechStarted?: () => void;
  /** Why an utterance failed, in words the driver can act on. */
  onSpeechFailure?: (reason: string) => void;
}

export class VoiceQueue {
  private queue: QueuedCallout[] = [];
  private recent = new Map<string, number>();
  private speaking: VoiceCallout | null = null;
  private watchdog: number | undefined;
  private failures = 0;
  private arrived = 0;

  constructor(private hooks: QueueHooks) {}

  /** Take a callout from the server; "queued" means it will be spoken. */
  enqueue(callout: VoiceCallout): CalloutAckStatus | "queued" {
    const now = Date.now();
    this.sweep(now);
    if (!this.hooks.enabled()) return this.ack(callout, "disabled");
    if (!this.hooks.activeSpeaker()) return this.ack(callout, "not_active_speaker");
    if (!this.hooks.categoryEnabled(callout.category)) {
      return this.ack(callout, "category_disabled");
    }
    if (this.recent.has(callout.id) || this.isQueued(callout)) {
      return this.ack(callout, "duplicate");
    }
    const ttl = callout.ttl_ms > 0 ? callout.ttl_ms : 10_000;
    const item: QueuedCallout = { callout, deadline: now + ttl, seq: this.arrived++ };

    if (callout.interrupt && callout.priority >= INTERRUPT_PRIORITY) {
      // Critical: stop mid-sentence. Whatever was being said matters less
      // than "oil pressure low".
      this.cancelSpeech();
      this.queue = [item];
    } else {
      this.queue = insertByPriority(this.queue, item);
      if (!this.queue.includes(item)) {
        // Three more urgent messages are already waiting; this one would be
        // stale by the time it could be spoken.
        return this.ack(callout, "expired");
      }
    }
    this.hooks.onQueue(this.queue);
    this.pump();
    return "queued"; // the ack follows when the utterance finishes
  }

  /** Try the engine again after the user has done something about it. */
  resetFailures(): void {
    this.failures = 0;
  }

  /** Drop everything pending (disconnect, voice disabled, speaker changed). */
  clear(): void {
    this.queue = [];
    this.cancelSpeech();
    this.hooks.onQueue(this.queue);
  }

  get queued(): QueuedCallout[] {
    return this.queue;
  }

  private isQueued(callout: VoiceCallout): boolean {
    return this.queue.some(
      (q) =>
        q.callout.id === callout.id ||
        (callout.dedupe_key != null && q.callout.dedupe_key === callout.dedupe_key),
    );
  }

  private ack(
    callout: VoiceCallout,
    status: CalloutAckStatus,
    reason?: string,
  ): CalloutAckStatus {
    this.recent.set(callout.id, Date.now());
    this.hooks.onAck(callout.id, status, reason);
    return status;
  }

  private sweep(now: number): void {
    for (const [id, at] of this.recent) {
      if (now - at > RECENT_RETENTION_MS) this.recent.delete(id);
    }
    const kept = this.queue.filter((item) => {
      if (!isExpired(item, now)) return true;
      this.ack(item.callout, "expired");
      return false;
    });
    if (kept.length !== this.queue.length) {
      this.queue = kept;
      this.hooks.onQueue(this.queue);
    }
  }

  private cancelSpeech(): void {
    if (this.watchdog) window.clearTimeout(this.watchdog);
    this.watchdog = undefined;
    if (this.speaking) {
      // Cut off mid-sentence by a critical message or a disconnect. Not a
      // fault: remember the id so it can't come back, and say what happened.
      this.ack(this.speaking, "interrupted");
    }
    this.speaking = null;
    // Guarded: a cancel() with nothing to cancel is itself one of the ways
    // Chrome's engine ends up ignoring the next utterance.
    if (speechSupported()) cancelIfSpeaking();
    this.hooks.onSpeaking(null);
  }

  /** Speak the head of the queue if nothing is in progress. */
  private pump(): void {
    if (this.speaking || this.queue.length === 0) return;
    const now = Date.now();
    this.sweep(now);
    const item = this.queue.shift();
    if (!item) return;
    this.hooks.onQueue(this.queue);

    if (!speechSupported()) {
      // Visual-only mode: the banner still shows it, so this is not an error —
      // but the rest of the queue still has to be drained and acknowledged.
      this.ack(item.callout, "speech_error", "this browser has no speech synthesis");
      this.pump();
      return;
    }
    if (this.failures >= MAX_CONSECUTIVE_FAILURES) {
      // The engine has refused several times over. Every further attempt costs
      // a watchdog timeout, during which the messages behind it go stale — so
      // fall through to captions immediately instead of blocking the queue.
      // Test voice (or Enable) clears this.
      this.ack(item.callout, "speech_error", "speech disabled after repeated failures");
      this.pump();
      return;
    }
    if (this.hooks.muted?.()) {
      this.ack(item.callout, "disabled");
      this.pump();
      return;
    }

    const opts = this.hooks.options();
    const utterance = new SpeechSynthesisUtterance(item.callout.text);
    const voice = pickVoice(getVoices(), opts);
    if (voice) {
      utterance.voice = voice;
      utterance.lang = voice.lang;
    }
    utterance.volume = opts.volume;
    utterance.rate = opts.rate;
    utterance.pitch = opts.pitch;

    const finish = (status: CalloutAckStatus, reason?: string) => {
      if (this.speaking?.id !== item.callout.id) return; // superseded
      if (this.watchdog) window.clearTimeout(this.watchdog);
      this.watchdog = undefined;
      this.speaking = null;
      this.hooks.onSpeaking(null);
      if (status === "speech_error") {
        this.failures += 1;
        this.hooks.onSpeechFailure?.(reason ?? "unknown");
      } else if (status === "spoken") {
        this.failures = 0;
      }
      this.ack(item.callout, status, reason);
      this.pump();
    };

    utterance.onstart = () => {
      // It speaks: whatever was wrong before is over.
      this.failures = 0;
      if (this.watchdog) window.clearTimeout(this.watchdog);
      // Second phase — some engines never fire `end`, which would wedge the
      // queue for the rest of the session. Only armed once speech is really
      // under way, and sized to the message.
      this.watchdog = window.setTimeout(
        () => {
          cancelIfSpeaking();
          finish("speech_error", "the voice stopped part-way through");
        },
        SPEAKING_TIMEOUT_MS + item.callout.text.length * WATCHDOG_PER_CHAR_MS,
      );
      this.hooks.onSpeechStarted?.();
    };
    utterance.onend = () => finish("spoken");
    utterance.onerror = (event) => {
      // `canceled`/`interrupted` mean cancel() was called — by a critical
      // callout, a disconnect, or the user pressing Test voice. The engine is
      // working fine; reporting that as a speech failure would raise a false
      // alarm and count toward the fallback-to-captions streak.
      if (isDeliberateStop(event)) finish("interrupted");
      else finish("speech_error", describeSpeechError(event));
    };

    this.speaking = item.callout;
    this.hooks.onSpeaking(item.callout);
    // First phase: did it start at all? Generously, because Chrome's default
    // voices are often network-backed and take seconds to begin — a deadline
    // tight enough to catch a dead engine quickly would cancel speech that was
    // merely slow, which is worse than waiting.
    //
    // If it never starts, try ONCE more with an on-device voice before giving
    // up. Chrome accepts an utterance for a network-backed voice and can then
    // silently drop it — no start, no end, no error — and an explicitly chosen
    // voice is honoured by pickVoice precisely because it is the user's
    // choice, so the only way out of that hole is to fall back here and say
    // so. Local voices are synthesised on the machine and always start.
    const armWatchdog = (retried: boolean) => {
      this.watchdog = window.setTimeout(() => {
        cancelIfSpeaking();
        const local = retried ? null : localFallbackVoice(voice);
        if (local) {
          this.hooks.onSpeechFailure?.(
            `${voice?.name ?? "the chosen voice"} never started — trying ${local.name}`,
          );
          utterance.voice = local;
          utterance.lang = local.lang;
          armWatchdog(true);
          speak(utterance);
          return;
        }
        finish("speech_error", `no response from the speech engine (${engineDetail(voice)})`);
      }, START_TIMEOUT_MS);
    };
    armWatchdog(false);
    speak(utterance);
  }
}

/** An on-device voice to retry with, or null if `voice` already was one. */
function localFallbackVoice(
  voice: SpeechSynthesisVoice | null,
): SpeechSynthesisVoice | null {
  if (voice?.localService) return null;
  const voices = getVoices();
  const lang = voice?.lang ?? "en";
  return (
    voices.find((v) => v.localService && v.lang === lang)
    ?? voices.find((v) => v.localService && v.lang.startsWith(lang.split("-")[0]))
    ?? voices.find((v) => v.localService && v.lang.startsWith("en"))
    ?? voices.find((v) => v.localService)
    ?? null
  );
}

/**
 * Hand an utterance to the engine, working around how Chrome drops them.
 *
 * `resume()` first: Chrome leaves speechSynthesis paused after some
 * tab-visibility changes, and `speak()` on a paused engine queues the utterance
 * silently. Calling resume when it is not paused does nothing.
 *
 * Then wait for voices: Chrome (and Safari) populate `getVoices()`
 * asynchronously, and an utterance spoken before the list arrives is accepted
 * and never spoken — no start, no end, no error. Deferring costs the user
 * gesture in principle, but an utterance spoken into an empty voice list was
 * never going to make a sound anyway.
 */
function speak(utterance: SpeechSynthesisUtterance): void {
  window.speechSynthesis.resume();
  whenVoicesReady(() => window.speechSynthesis.speak(utterance));
}

function whenVoicesReady(run: () => void): void {
  if (getVoices().length > 0 || !speechSupported()) {
    run();
    return;
  }
  let done = false;
  const finish = () => {
    if (done) return;
    done = true;
    window.speechSynthesis.removeEventListener("voiceschanged", onChange);
    window.clearTimeout(timer);
    run(); // try regardless: a browser may simply never fire the event
  };
  const onChange = () => {
    if (getVoices().length > 0) finish();
  };
  window.speechSynthesis.addEventListener("voiceschanged", onChange);
  const timer = window.setTimeout(finish, VOICES_WAIT_MS);
}

/**
 * Stop whatever is speaking — but only if something is.
 *
 * `cancel()` is processed asynchronously in Chrome, so calling it immediately
 * before `speak()` can swallow the new utterance instead of the old one. That
 * looks exactly like a dead engine from here, which is why this checks first.
 */
function cancelIfSpeaking(): void {
  if (window.speechSynthesis.speaking || window.speechSynthesis.pending) {
    window.speechSynthesis.cancel();
  }
}

/** What the engine looks like right now — the detail a failure report needs. */
export function engineDetail(tried?: SpeechSynthesisVoice | null): string {
  if (!speechSupported()) return "no speech synthesis in this browser";
  const all = getVoices();
  if (all.length === 0) {
    return "no voices are installed — on Linux, Chrome needs a speech engine "
      + "(speech-dispatcher); otherwise try another browser";
  }
  const state = window.speechSynthesis.paused ? ", engine paused" : "";
  // Name the voice that was actually attempted. "199 voices available" says
  // the list is fine and nothing about the one that stayed silent, which is
  // the only part that matters when the engine accepts an utterance and drops
  // it — and whether it was on-device separates a dead voice from a dead
  // engine without anyone opening a console.
  if (tried === undefined) return `${all.length} voices available${state}`;
  const local = all.filter((v) => v.localService).length;
  const who = tried
    ? `tried ${tried.name}${tried.localService ? " (on-device)" : " (network)"}`
    : "no voice could be selected";
  return `${who}; ${all.length} voices, ${local} on-device${state}`;
}

/** Whether an error event is just our own cancel() coming back to us. */
export function isDeliberateStop(event: SpeechSynthesisErrorEvent | Event): boolean {
  const code = (event as SpeechSynthesisErrorEvent).error;
  return code === "canceled" || code === "interrupted";
}

/** The engine's own reason, which the error event carries and the spec names. */
export function describeSpeechError(event: SpeechSynthesisErrorEvent | Event): string {
  const code = (event as SpeechSynthesisErrorEvent).error;
  const explained: Record<string, string> = {
    "not-allowed": "the browser blocked audio — click Enable Race Engineer again",
    "audio-busy": "the audio device is busy",
    "audio-hardware": "no audio output device",
    "synthesis-failed": "the speech engine failed",
    "synthesis-unavailable": "no speech engine available",
    "language-unavailable": "no voice installed for this language",
    "voice-unavailable": "the selected voice is not available",
    "text-too-long": "the message was too long to speak",
    "invalid-argument": "the rate, pitch or volume was rejected",
    // Only reachable via the diagnostics record: isDeliberateStop() filters
    // these out of anything user-facing.
    canceled: "stopped before it started",
    interrupted: "interrupted by a more urgent message",
  };
  return code ? (explained[code] ?? code) : "unknown";
}

export interface SpeakHooks {
  onStart?: () => void;
  onError?: (reason: string) => void;
}

/** One-off utterance for the Test Voice / Enable buttons (bypasses the queue). */
export function speakTest(text: string, opts: SpeechOptions, hooks: SpeakHooks = {}): boolean {
  if (!speechSupported()) {
    hooks.onError?.("this browser has no speech synthesis");
    return false;
  }
  cancelIfSpeaking();
  const utterance = new SpeechSynthesisUtterance(text);
  const voice = pickVoice(getVoices(), opts);
  if (voice) {
    utterance.voice = voice;
    utterance.lang = voice.lang;
  }
  utterance.volume = opts.volume;
  utterance.rate = opts.rate;
  utterance.pitch = opts.pitch;
  // A test utterance that never starts is the whole failure this reports on —
  // but "never" has to mean never, not "not yet". Chrome's network-backed
  // voices can take several seconds to begin, and calling that a failure
  // reports a broken engine to someone whose engine is about to speak.
  // Nothing is cancelled here either: a late start is still a start.
  // ...and if it truly never starts, retry once on an on-device voice before
  // reporting a dead engine. Chrome silently drops utterances bound to
  // network-backed voices, and Test voice is exactly where someone is trying
  // to find that out.
  let settled = false;
  let timer = 0;
  let attempted = voice;
  const arm = (retried: boolean) => {
    timer = window.setTimeout(() => {
      if (settled) return;
      const local = retried ? null : localFallbackVoice(attempted);
      if (local) {
        cancelIfSpeaking();
        attempted = local;
        utterance.voice = local;
        utterance.lang = local.lang;
        arm(true);
        speak(utterance);
        return;
      }
      hooks.onError?.(`no response from the speech engine (${engineDetail(attempted)})`);
    }, START_TIMEOUT_MS);
  };
  arm(false);
  const settle = () => {
    settled = true;
    window.clearTimeout(timer);
  };
  utterance.onstart = () => {
    settle();
    hooks.onStart?.();
  };
  utterance.onend = settle;
  utterance.onerror = (event) => {
    settle();
    // Our own cancel() (a second Test press, a critical callout) is not a
    // failure of the engine.
    if (!isDeliberateStop(event)) hooks.onError?.(describeSpeechError(event));
  };
  speak(utterance);
  return true;
}
