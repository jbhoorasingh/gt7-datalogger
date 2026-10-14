import { describe, expect, it } from "vitest";
import { phoneUrl, qrPath } from "./qr";

describe("qrPath", () => {
  it("draws a square grid with a quiet zone", () => {
    const qr = qrPath("http://192.168.1.20:8000/");
    // Version 2 (25 modules) fits this URL; two modules of border each side.
    expect(qr.size).toBe(29);
    // The top-left finder pattern starts inside the border.
    expect(qr.d.startsWith("M2 2h1v1h-1z")).toBe(true);
  });
});

describe("phoneUrl", () => {
  it("leaves out port 80 only", () => {
    expect(phoneUrl("10.0.0.5", 8000)).toBe("http://10.0.0.5:8000/");
    expect(phoneUrl("10.0.0.5", 80)).toBe("http://10.0.0.5/");
  });
});
