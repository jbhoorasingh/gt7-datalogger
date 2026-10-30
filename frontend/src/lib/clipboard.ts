// Copy text without assuming a secure context. On a plain-HTTP LAN address
// (a tablet opening http://192.168.1.20:8000) navigator.clipboard is
// undefined, so `navigator.clipboard.writeText(...)` threw a TypeError before
// any .catch could say "Copy failed" (#190). This tries the Clipboard API,
// then the old execCommand route through a hidden textarea, and reports
// whether either worked; when neither did, the caller selects the visible
// text so Ctrl/Cmd+C is one keystroke away.

export async function copyText(text: string): Promise<boolean> {
  try {
    const clip = globalThis.navigator?.clipboard;
    if (clip?.writeText) {
      await clip.writeText(text);
      return true;
    }
  } catch {
    // refused (permissions, an unfocused document): try the old way
  }
  return legacyCopy(text);
}

function legacyCopy(text: string): boolean {
  const doc = globalThis.document;
  if (!doc?.body) return false;
  const area = doc.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  doc.body.appendChild(area);
  try {
    area.select();
    return doc.execCommand("copy");
  } catch {
    return false;
  } finally {
    doc.body.removeChild(area);
  }
}

/** Select an element's text, so the person can copy it themselves. */
export function selectText(el: Element | null | undefined): void {
  const selection = globalThis.window?.getSelection?.();
  if (!el || !selection) return;
  selection.removeAllRanges();
  selection.selectAllChildren(el);
}
