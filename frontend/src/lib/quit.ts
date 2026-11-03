// Quit from Settings › About: remembered for this tab, so the page that
// follows says the app stopped (as asked) rather than that it vanished.

const KEY = "gt7.quitRequested";

export function markQuit(): void {
  try {
    sessionStorage.setItem(KEY, "1");
  } catch {
    // storage blocked: the unreachable page shows instead, which still helps
  }
}

export function quitRequested(): boolean {
  try {
    return sessionStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

export function clearQuit(): void {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    // nothing to clear
  }
}
