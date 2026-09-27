import { describe, expect, it } from "vitest";
import { ackNote, formatAge, voiceState, type VoiceStateInput } from "./engineerState";

const base: VoiceStateInput = {
  supported: true,
  enabled: false,
  audioReady: false,
  speechError: null,
  activeClientId: "",
  clientId: "me",
};

describe("voiceState", () => {
  it("is off until the user taps", () => {
    expect(voiceState(base)).toBe("off");
    // A stored preference alone is not armed audio.
    expect(voiceState({ ...base, enabled: true, activeClientId: "me" })).toBe("off");
  });

  it("speaks only with armed audio and the speaker role", () => {
    expect(
      voiceState({ ...base, enabled: true, audioReady: true, activeClientId: "me" }),
    ).toBe("speaking");
  });

  it("reports another device holding the voice", () => {
    expect(voiceState({ ...base, activeClientId: "phone" })).toBe("other");
    expect(
      voiceState({ ...base, enabled: true, audioReady: true, activeClientId: "phone" }),
    ).toBe("other");
  });

  it("turns to error for a device that wants to speak and can't", () => {
    expect(voiceState({ ...base, supported: false })).toBe("error");
    expect(
      voiceState({
        ...base,
        enabled: true,
        audioReady: true,
        activeClientId: "me",
        speechError: "not-allowed",
      }),
    ).toBe("error");
    // A failed test with voice off stays off.
    expect(voiceState({ ...base, speechError: "not-allowed" })).toBe("off");
  });
});

describe("ackNote", () => {
  it("says nothing for spoken callouts", () => {
    expect(ackNote("spoken", "lap", "race")).toBeNull();
  });

  it("tells verbosity from a muted category", () => {
    expect(ackNote("category_disabled", "coaching", "race")).toBe("not heard — off at race");
    expect(ackNote("category_disabled", "lap", "race")).toBe(
      "not heard — muted on this device",
    );
  });

  it("names the other device case", () => {
    expect(ackNote("not_active_speaker", "fuel", "race")).toMatch(/another device/);
  });
});

describe("formatAge", () => {
  it("scales units", () => {
    expect(formatAge(4_200)).toBe("4 s ago");
    expect(formatAge(-50)).toBe("0 s ago");
    expect(formatAge(185_000)).toBe("3 min ago");
    expect(formatAge(2 * 3_600_000)).toBe("2 h ago");
  });
});
