import { describe, expect, it } from "vitest";
import type { SetupState } from "./types";
import { classifyProblem, completionBody, detectOs, wantsWelcome } from "./welcome";

const fresh: SetupState = {
  completed: false,
  sessions: 0,
  ps_ip_saved: false,
  source: "sim",
  listening: true,
  listener_error: null,
  heartbeat_error: null,
  lan_reachable: false,
  lan_access: false,
};
const root = { pathname: "/", hash: "" };

describe("the first-run redirect", () => {
  it("takes the bare origin to the wizard while setup is not done", () => {
    for (const hash of ["", "#", "#/"]) {
      expect(wantsWelcome({ pathname: "/", hash }, fresh, "docker")).toBe(true);
    }
    expect(wantsWelcome({ pathname: "/index.html", hash: "" }, fresh, "source")).toBe(true);
  });

  it("never takes over a deep link or a second-screen page", () => {
    expect(wantsWelcome({ pathname: "/", hash: "#/live" }, fresh, "desktop")).toBe(false);
    expect(wantsWelcome({ pathname: "/", hash: "#/analysis?session=3" }, fresh, "desktop")).toBe(false);
    expect(wantsWelcome({ pathname: "/dash", hash: "" }, fresh, "desktop")).toBe(false);
    expect(wantsWelcome({ pathname: "/overlay", hash: "" }, fresh, "desktop")).toBe(false);
  });

  it("leaves a completed setup alone", () => {
    expect(wantsWelcome(root, { ...fresh, completed: true }, "desktop")).toBe(false);
  });

  it("spares an existing install with sessions, unless it is the desktop app", () => {
    const used = { ...fresh, sessions: 12 };
    expect(wantsWelcome(root, used, "docker")).toBe(false);
    expect(wantsWelcome(root, used, "source")).toBe(false);
    expect(wantsWelcome(root, used, null)).toBe(false);
    expect(wantsWelcome(root, used, "desktop")).toBe(true);
  });

  it("does nothing when the server did not answer", () => {
    expect(wantsWelcome(root, null, "desktop")).toBe(false);
  });
});

describe("detectOs", () => {
  it("reads the platform the prompt depends on", () => {
    expect(detectOs({ platform: "Win32", userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64)" })).toBe("windows");
    expect(detectOs({ platform: "MacIntel", userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5)" })).toBe("mac");
    expect(detectOs({ platform: "Linux x86_64", userAgent: "Mozilla/5.0 (X11; Linux x86_64)" })).toBe("linux");
  });

  it("does not mistake a phone for a computer", () => {
    expect(detectOs({ platform: "Linux armv8l", userAgent: "Mozilla/5.0 (Linux; Android 14)" })).toBe("other");
    expect(detectOs({ platform: "iPhone", userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)" })).toBe("other");
    expect(detectOs(undefined)).toBe("other");
  });
});

describe("classifyProblem", () => {
  const beat = (reason: string) => ({ errno: 65, reason, at: 0 });

  it("puts a listener that did not start first", () => {
    expect(
      classifyProblem(
        "UDP 33740 is in use by another program (another copy of the datalogger, its Docker container, or another telemetry tool)",
        beat("whatever"),
      ),
    ).toBe("portBusy");
    expect(classifyProblem("UDP 33740: Permission denied", null)).toBe("listenerOther");
  });

  it("maps the server's heartbeat reasons to help", () => {
    expect(
      classifyProblem(
        null,
        beat("macOS is blocking the local network for this app: allow it in System Settings › Privacy & Security › Local Network"),
      ),
    ).toBe("localNetwork");
    expect(classifyProblem(null, beat("the system refused to send to the PlayStation (a firewall?)"))).toBe("firewall");
    expect(classifyProblem(null, beat("the PlayStation refused the heartbeat: is GT7 running?"))).toBe("refused");
    expect(
      classifyProblem(null, beat("no route to the PlayStation: check its address and that both are on one network")),
    ).toBe("noRoute");
    expect(classifyProblem(null, beat("Message too long"))).toBe("heartbeatOther");
  });

  it("is nothing when nothing is wrong", () => {
    expect(classifyProblem(null, null)).toBeNull();
    expect(classifyProblem(undefined, undefined)).toBeNull();
  });
});

describe("completionBody", () => {
  it("answers the phone question only for the desktop app", () => {
    expect(completionBody("desktop", true)).toEqual({ lan_access: true });
    expect(completionBody("desktop", false)).toEqual({ lan_access: false });
    // Docker and source already serve the LAN: leave it as it is.
    expect(completionBody("docker", true)).toBeUndefined();
    expect(completionBody("source", false)).toBeUndefined();
    expect(completionBody(null, true)).toBeUndefined();
  });
});
