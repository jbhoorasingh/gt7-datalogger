import { afterEach, describe, expect, it, vi } from "vitest";
import { copyText } from "./clipboard";

// A document just big enough for the textarea fallback.
function fakeDocument(copies: boolean | "throws") {
  const appended: unknown[] = [];
  const doc = {
    body: {
      appendChild: (el: unknown) => appended.push(el),
      removeChild: (el: unknown) => appended.splice(appended.indexOf(el), 1),
    },
    createElement: () => ({ value: "", style: {}, setAttribute: () => {}, select: () => {} }),
    execCommand: vi.fn(() => {
      if (copies === "throws") throw new Error("not allowed");
      return copies;
    }),
  };
  return { doc, appended };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("copyText", () => {
  it("uses the Clipboard API where there is one", async () => {
    const writeText = vi.fn(async () => {});
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    expect(await copyText("http://192.168.1.20:8000/dash")).toBe(true);
    expect(writeText).toHaveBeenCalledWith("http://192.168.1.20:8000/dash");
  });

  it("falls back to the textarea on plain HTTP, where navigator.clipboard is undefined", async () => {
    const { doc, appended } = fakeDocument(true);
    vi.stubGlobal("navigator", {});
    vi.stubGlobal("document", doc);
    expect(await copyText("x")).toBe(true);
    expect(doc.execCommand).toHaveBeenCalledWith("copy");
    expect(appended).toHaveLength(0); // cleaned up
  });

  it("falls back when the Clipboard API refuses", async () => {
    const { doc } = fakeDocument(true);
    vi.stubGlobal("navigator", { clipboard: { writeText: vi.fn(async () => Promise.reject(new Error("denied"))) } });
    vi.stubGlobal("document", doc);
    expect(await copyText("x")).toBe(true);
  });

  it("reports failure instead of throwing when nothing can copy", async () => {
    vi.stubGlobal("navigator", {});
    vi.stubGlobal("document", fakeDocument(false).doc);
    expect(await copyText("x")).toBe(false);
    vi.stubGlobal("document", fakeDocument("throws").doc);
    expect(await copyText("x")).toBe(false);
    vi.stubGlobal("document", undefined);
    expect(await copyText("x")).toBe(false);
  });
});
