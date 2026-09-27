import { Toggle } from "@/components/ui/Toggle";
import { api } from "@/lib/api";
import type { WebhookEvent } from "@/lib/types";
import { INPUT_CLS, SectionPanel, SettingRow, type SectionProps } from "./parts";

// Order matches the backend's ALL_EVENTS.
export const WEBHOOK_EVENTS: { value: WebhookEvent; label: string; hint: string }[] = [
  { value: "personal_best", label: "Personal bests", hint: "a lap beats your session best" },
  { value: "session_summary", label: "Session summaries", hint: "car, laps, best time, fuel used" },
  { value: "overtake", label: "Overtakes", hint: "you gain a race position" },
  { value: "position_lost", label: "Positions lost", hint: "you drop a race position" },
  { value: "off_road", label: "Off-road excursions", hint: "3+ wheels on grass/dirt — needs format C" },
];

export function NotificationsSection({ saved, draft, edit, busy, run }: SectionProps) {
  const hasUrl = draft.webhook_url.trim() !== "";
  const events = draft.webhook_events;

  function toggle(ev: WebhookEvent, on: boolean) {
    edit({
      webhook_events: WEBHOOK_EVENTS.map((e) => e.value).filter((e) =>
        e === ev ? on : events.includes(e),
      ),
    });
  }

  // The test pings the server's saved URL, so an unapplied edit can't be tested yet.
  const unsaved = draft.webhook_url !== saved.webhook_url;

  return (
    <SectionPanel
      title="Notifications"
      description="Webhook pings for race events. Discord URLs get a rich embed; anything else gets JSON."
    >
      <SettingRow label="Webhook URL" help="Empty turns every notification off." htmlFor="webhook-url">
        <div className="flex gap-2">
          <input
            id="webhook-url"
            autoComplete="off"
            value={draft.webhook_url}
            onChange={(e) => edit({ webhook_url: e.target.value })}
            placeholder="https://discord.com/api/webhooks/…"
            className={INPUT_CLS}
          />
          <button
            className="btn shrink-0"
            disabled={busy !== null || !hasUrl || unsaved}
            title={unsaved ? "Apply the new URL first" : undefined}
            onClick={() => void run("Test webhook", api.admin.testWebhook, () => "Test notification sent")}
          >
            Send test
          </button>
        </div>
      </SettingRow>

      <SettingRow
        label="Notify me when…"
        help={
          hasUrl
            ? "Position events need a race that reports live positions, and a change that holds for ~1 s."
            : "Add a webhook URL to choose events."
        }
        last
      >
        <div className="flex flex-col gap-0.5">
          {WEBHOOK_EVENTS.map((ev) => (
            <label
              key={ev.value}
              className={`flex items-center gap-3 py-1.5 ${hasUrl ? "cursor-pointer" : ""}`}
            >
              <Toggle
                ariaLabel={ev.label}
                checked={hasUrl && events.includes(ev.value)}
                disabled={!hasUrl}
                onCheckedChange={(on) => toggle(ev.value, on)}
              />
              <span className={`text-[13px] ${hasUrl ? "" : "opacity-45"}`}>{ev.label}</span>
              <span className={`text-[11px] text-ink-dim ${hasUrl ? "" : "opacity-45"}`}>{ev.hint}</span>
            </label>
          ))}
        </div>
      </SettingRow>
    </SectionPanel>
  );
}
