// Settings › Phone app: how the phone finds this server. mDNS does it on
// its own where multicast works; the QR code carries the same address for
// networks (and Docker bridges) where it doesn't.

import { useMemo } from "react";
import { Toggle } from "@/components/ui/Toggle";
import { useT } from "@/i18n";
import { api } from "@/lib/api";
import type { AdminStats } from "@/lib/types";
import { Dot, SectionPanel, SettingRow } from "./parts";
import { phoneUrl, qrPath } from "./qr";

export function PhoneSection({
  stats,
  busy,
  run,
}: {
  stats: AdminStats | null;
  busy: string | null;
  run: (label: string, fn: () => Promise<unknown>, done?: (r: unknown) => string) => Promise<void>;
}) {
  const t = useT();
  const url = stats?.lan_ip ? phoneUrl(stats.lan_ip, stats.http_port) : null;
  const qr = useMemo(() => (url ? qrPath(url) : null), [url]);
  const mdns = stats?.mdns ?? null;
  const enabled = stats?.mdns_enabled ?? false;
  const loopback = stats?.loopback_only ?? false;

  return (
    <SectionPanel title={t("settings.section.phone")} description={t("settings.phone.desc")}>
      <SettingRow label={t("settings.phone.announce")} help={t("settings.phone.announceHelp")}>
        <div className="flex flex-wrap items-center gap-3">
          <Toggle
            ariaLabel={t("settings.phone.announce")}
            checked={enabled}
            disabled={!stats || busy !== null}
            onCheckedChange={(on) =>
              void run(
                t("settings.phone.announce"),
                () => api.admin.updateConfig({ values: { mdns: on } }),
                () => (on ? t("settings.phone.announcing") : t("settings.phone.notAnnouncing")),
              )
            }
          />
          {stats && (
            <span className="flex items-center gap-2 font-tabular text-[11.5px] text-ink-dim">
              <Dot tone={mdns ? "good" : enabled ? "warn" : "faint"} />
              {mdns
                ? `${mdns.name.replace(/\._gt7dl\._tcp\.local\.$/, "")} · ${mdns.ip}:${mdns.port}`
                : enabled
                  ? loopback
                    ? t("settings.phone.loopbackNote")
                    : t("settings.phone.notAnnounced")
                  : t("settings.meta.off")}
            </span>
          )}
        </div>
      </SettingRow>
      <SettingRow label={t("settings.phone.byCode")} help={t("settings.phone.byCodeHelp")} last>
        {url && qr ? (
          <div className="flex flex-wrap items-center gap-4">
            <svg
              role="img"
              aria-label={t("settings.phone.qr", { url })}
              viewBox={`0 0 ${qr.size} ${qr.size}`}
              shapeRendering="crispEdges"
              className="h-40 w-40 rounded-md"
            >
              {/* Dark on light whatever the theme: phone cameras read that. */}
              <rect width={qr.size} height={qr.size} fill="#fff" />
              <path d={qr.d} fill="#000" />
            </svg>
            <div className="flex min-w-0 flex-col gap-1">
              <span className="font-tabular text-[13px]">{url}</span>
              <span className="text-[11px] text-ink-faint">
                {t("settings.phone.liveStream", { url: `ws://${stats?.lan_ip}:${stats?.http_port}/ws/live` })}
              </span>
            </div>
          </div>
        ) : (
          <span className="text-[12px] text-ink-faint">
            {!stats
              ? t("common.loading")
              : loopback
                ? t("settings.phone.loopbackHelp")
                : t("settings.phone.noLan")}
          </span>
        )}
      </SettingRow>
    </SectionPanel>
  );
}
