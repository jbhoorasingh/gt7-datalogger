import { Toggle } from "@/components/ui/Toggle";
import { useT } from "@/i18n";
import { api } from "@/lib/api";
import type { WebhookEvent } from "@/lib/types";
import { INPUT_CLS, SectionPanel, SettingRow, type SectionProps } from "./parts";

// Order matches the backend's ALL_EVENTS. Worded as
// settings.notify.<event> and settings.notify.<event>.hint.
export const WEBHOOK_EVENTS: WebhookEvent[] = [
  "personal_best",
  "session_summary",
  "overtake",
  "position_lost",
  "off_road",
];

export function NotificationsSection({ saved, draft, edit, busy, run }: SectionProps) {
  const t = useT();
  const hasUrl = draft.webhook_url.trim() !== "";
  const events = draft.webhook_events;

  function toggle(ev: WebhookEvent, on: boolean) {
    edit({
      webhook_events: WEBHOOK_EVENTS.filter((e) =>
        e === ev ? on : events.includes(e),
      ),
    });
  }

  // The test pings the server's saved URL, so an unapplied edit can't be tested yet.
  const unsaved = draft.webhook_url !== saved.webhook_url;

  return (
    <SectionPanel title={t("settings.section.notifications")} description={t("settings.notify.desc")}>
      <SettingRow label={t("settings.label.webhook_url")} help={t("settings.notify.urlHelp")} htmlFor="webhook-url">
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
            title={unsaved ? t("settings.notify.applyFirst") : undefined}
            onClick={() =>
              void run(t("settings.notify.testWebhook"), api.admin.testWebhook, () => t("settings.notify.testSent"))
            }
          >
            {t("settings.notify.sendTest")}
          </button>
        </div>
      </SettingRow>

      <SettingRow
        label={t("settings.notify.when")}
        help={hasUrl ? t("settings.notify.whenHelp") : t("settings.notify.addUrl")}
        last
      >
        <div className="flex flex-col gap-0.5">
          {WEBHOOK_EVENTS.map((ev) => (
            <label key={ev} className={`flex items-center gap-3 py-1.5 ${hasUrl ? "cursor-pointer" : ""}`}>
              <Toggle
                ariaLabel={t(`settings.notify.${ev}`)}
                checked={hasUrl && events.includes(ev)}
                disabled={!hasUrl}
                onCheckedChange={(on) => toggle(ev, on)}
              />
              <span className={`text-[13px] ${hasUrl ? "" : "opacity-45"}`}>{t(`settings.notify.${ev}`)}</span>
              <span className={`text-[11px] text-ink-dim ${hasUrl ? "" : "opacity-45"}`}>
                {t(`settings.notify.${ev}.hint`)}
              </span>
            </label>
          ))}
        </div>
      </SettingRow>
    </SectionPanel>
  );
}
