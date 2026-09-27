// Whether the datalogger server can be reached at all. A closed WebSocket
// alone isn't enough to say so (the socket reconnects by itself every 2 s and
// a blip shouldn't blank the page): the server counts as unreachable only
// once /api/status fails too. While it is, the check repeats with backoff.

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";

// Wait this long after the socket closes before the first check, so a
// socket that is back within a second never shows the page.
const FIRST_CHECK_MS = 1000;
// Retry delays after each failed check; the last one repeats.
const BACKOFF_MS = [2000, 4000, 8000, 15_000, 30_000];
// While the socket is down but /api/status answers, keep an eye on it.
const HEALTHY_RECHECK_MS = 5000;

export interface Reachability {
  unreachable: boolean;
  /** Failed checks since the socket closed. */
  attempts: number;
  /** Date.now() of the next check, null when none is scheduled. */
  nextCheckAt: number | null;
  /** Date.now() the socket closed, null while it is open. */
  offlineSince: number | null;
  checking: boolean;
  retryNow: () => void;
}

interface State {
  unreachable: boolean;
  attempts: number;
  nextCheckAt: number | null;
  offlineSince: number | null;
  checking: boolean;
}

const UP: State = {
  unreachable: false,
  attempts: 0,
  nextCheckAt: null,
  offlineSince: null,
  checking: false,
};

export function useServerReachability(wsConnected: boolean, enabled = true): Reachability {
  const [state, setState] = useState<State>(UP);
  const checkNow = useRef<() => void>(() => {});

  useEffect(() => {
    if (!enabled || wsConnected) {
      setState(UP);
      return;
    }
    let alive = true;
    let timer: number | undefined;
    let attempts = 0;

    const schedule = (ms: number) => {
      window.clearTimeout(timer);
      timer = window.setTimeout(check, ms);
      setState((s) => ({ ...s, nextCheckAt: Date.now() + ms }));
    };

    const check = async () => {
      window.clearTimeout(timer);
      setState((s) => ({ ...s, checking: true, nextCheckAt: null }));
      try {
        await api.status();
        if (!alive) return;
        attempts = 0;
        setState((s) => ({ ...s, unreachable: false, attempts: 0, checking: false }));
        schedule(HEALTHY_RECHECK_MS);
      } catch {
        if (!alive) return;
        attempts += 1;
        setState((s) => ({ ...s, unreachable: true, attempts, checking: false }));
        schedule(BACKOFF_MS[Math.min(attempts, BACKOFF_MS.length) - 1]);
      }
    };

    checkNow.current = () => void check();
    setState({ ...UP, offlineSince: Date.now() });
    schedule(FIRST_CHECK_MS);
    return () => {
      alive = false;
      window.clearTimeout(timer);
      checkNow.current = () => {};
    };
  }, [wsConnected, enabled]);

  const retryNow = useCallback(() => checkNow.current(), []);
  return { ...state, retryNow };
}
