// What GET /api/health says about the server (#184), asked once per page
// load and kept: the install kind does not change while the page is open,
// and a page that loses the server (the unreachable page) still needs it to
// word its advice.

import { useEffect } from "react";
import { create } from "zustand";
import { api } from "./api";
import type { Health, InstallKind } from "./types";

export const useHealth = create<{ health: Health | null }>(() => ({ health: null }));

let pending: Promise<Health | null> | null = null;

/** Ask the server once; a failure is forgotten so the next caller asks again. */
export function loadHealth(): Promise<Health | null> {
  pending ??= api
    .health()
    .then((health) => {
      useHealth.setState({ health });
      return health;
    })
    .catch(() => {
      pending = null;
      return null;
    });
  return pending;
}

/** desktop, docker or source; null until the server has said (or if it never does). */
export function useInstallKind(): InstallKind | null {
  useEffect(() => {
    void loadHealth();
  }, []);
  return useHealth((s) => s.health?.install_kind ?? null);
}
