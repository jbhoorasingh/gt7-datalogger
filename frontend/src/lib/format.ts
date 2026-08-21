export type Units = "metric" | "imperial";

export function formatLapTime(ms: number | null | undefined): string {
  if (ms == null || ms < 0) return "–:––.–––";
  const intMs = Math.round(ms);
  const minutes = Math.floor(intMs / 60000);
  const seconds = Math.floor((intMs % 60000) / 1000);
  const millis = intMs % 1000;
  return `${minutes}:${String(seconds).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
}

export function formatDelta(ms: number): string {
  const sign = ms >= 0 ? "+" : "−";
  return `${sign}${(Math.abs(ms) / 1000).toFixed(3)}`;
}

export function formatDuration(ms: number): string {
  if (!isFinite(ms) || ms < 0) return "–";
  const h = Math.floor(ms / 3_600_000);
  const m = Math.floor((ms % 3_600_000) / 60000);
  const s = Math.floor((ms % 60000) / 1000);
  return h > 0 ? `${h}h ${m}m` : `${m}m ${s}s`;
}

export function speedValue(kmh: number, units: Units): number {
  return units === "imperial" ? kmh * 0.621371 : kmh;
}

export function speedUnit(units: Units): string {
  return units === "imperial" ? "mph" : "km/h";
}

export function formatSpeed(kmh: number, units: Units): string {
  return `${Math.round(speedValue(kmh, units))} ${speedUnit(units)}`;
}

export function formatTimeOfDay(todMs: number | null | undefined): string {
  if (todMs == null || todMs < 0) return "–";
  const dayMs = ((todMs % 86_400_000) + 86_400_000) % 86_400_000;
  const h = Math.floor(dayMs / 3_600_000);
  const m = Math.floor((dayMs % 3_600_000) / 60_000);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

export function formatTime(iso: string): string {
  if (!iso) return "–";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleString();
}

// Short-form timestamp for dense table rows: "8/21 1:09 AM". Full precision
// stays available in a tooltip via formatTime().
export function formatTimeShort(iso: string): string {
  if (!iso) return "–";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const date = d.toLocaleDateString(undefined, { month: "numeric", day: "numeric" });
  const time = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  return `${date} ${time}`;
}
