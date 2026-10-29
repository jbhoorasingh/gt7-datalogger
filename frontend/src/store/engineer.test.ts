import { afterEach, describe, expect, it, vi } from "vitest";

// The store persists to localStorage as it is created.
async function load() {
  vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {}, removeItem: () => {} });
  vi.resetModules();
  return import("./engineer");
}

afterEach(() => {
  vi.unstubAllGlobals();
});

const ja = { text: "ラップタイム、1分6秒7。", language: "ja" as const };
const english = "Lap time, one minute six point seven.";

describe("speakableWording", () => {
  it("speaks the translation in a voice of its language", async () => {
    const { speakableWording } = await load();
    expect(speakableWording(ja, english, [{ lang: "en-US" }, { lang: "ja-JP" }])).toEqual({
      text: ja.text,
      lang: "ja-JP",
    });
  });

  it("says the English when the device has no voice for the language", async () => {
    const { speakableWording } = await load();
    expect(speakableWording(ja, english, [{ lang: "en-US" }, { lang: "es-ES" }])).toEqual({
      text: english,
      lang: "en-US",
    });
  });

  it("matches a voice of any region, Android's underscores included", async () => {
    const { speakableWording } = await load();
    const pt = { text: "Última volta.", language: "pt-BR" as const };
    expect(speakableWording(pt, "Final lap.", [{ lang: "pt_PT" }]).text).toBe("Última volta.");
  });

  it("does not give up on a voice list that has not loaded yet", async () => {
    const { speakableWording } = await load();
    expect(speakableWording(ja, english, []).text).toBe(ja.text);
  });
});
