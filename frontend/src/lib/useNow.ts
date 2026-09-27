// Wall-clock that re-renders its consumer every `everyMs` — for "12 s ago"
// labels, which would otherwise freeze at whatever they said when the last
// callout arrived.

import { useEffect, useState } from "react";

export function useNow(everyMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), everyMs);
    return () => window.clearInterval(timer);
  }, [everyMs]);
  return now;
}
