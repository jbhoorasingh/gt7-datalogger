// The QR code as an SVG path over its module grid: one "M x y h1 v1 h-1z"
// square per dark module, so the component draws a single <path> and no
// HTML is injected.

import { encode } from "uqr";

export interface QrPath {
  /** Modules per side, quiet zone included. */
  size: number;
  d: string;
}

export function qrPath(text: string, border = 2): QrPath {
  const { data } = encode(text, { border: 0 });
  const n = data.length;
  let d = "";
  for (let y = 0; y < n; y++) {
    for (let x = 0; x < n; x++) {
      if (data[y][x]) d += `M${x + border} ${y + border}h1v1h-1z`;
    }
  }
  return { size: n + border * 2, d };
}

/** What the phone opens: the dashboard's own address on the LAN. */
export function phoneUrl(ip: string, port: number): string {
  return `http://${ip}${port === 80 ? "" : `:${port}`}/`;
}
