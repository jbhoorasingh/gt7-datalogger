// The server's latest Race Engineer status, for pages that show it without
// being a voice client (the Live view's link-chip). The server sends it once
// per socket connect and on every change, so it has to be caught from app
// start — a view mounted later would otherwise never see it. Deliberately
// not store/engineer.ts: that store carries the speech engine, which the main
// bundle (and the OBS overlay) must not pull in.

import { create } from "zustand";
import type { RaceEngineerStatus } from "./types";
import { subscribeWs } from "./wsBus";

export const useEngineerStatus = create<{ status: RaceEngineerStatus | null }>(() => ({
  status: null,
}));

let tracking = false;

/** Start listening; idempotent. Call once at app start. */
export function trackEngineerStatus(): void {
  if (tracking) return;
  tracking = true;
  subscribeWs((msg) => {
    if (msg.type === "race_engineer_status") useEngineerStatus.setState({ status: msg.data });
  });
}
