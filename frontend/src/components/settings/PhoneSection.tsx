// Settings › Phone app: how the phone finds this server. mDNS does it on
// its own where multicast works; the QR code carries the same address for
// networks (and Docker bridges) where it doesn't.

import { useMemo } from "react";
import { Toggle } from "@/components/ui/Toggle";
import { api } from "@/lib/api";
import type { AdminStats } from "@/lib/types";
import { Dot, SectionPanel, SettingRow } from "./parts";
import { phoneUrl, qrPath } from "./qr";

// uvicorn's own command line binds 127.0.0.1 unless told otherwise (#151).
const LOOPBACK_NOTE = "not announced — the server listens on 127.0.0.1 only";
const LOOPBACK_HELP =
  "A phone can't reach this server: it listens on 127.0.0.1 only. Start uvicorn with --host 0.0.0.0.";

export function PhoneSection({
  stats,
  busy,
  run,
}: {
  stats: AdminStats | null;
  busy: string | null;
  run: (label: string, fn: () => Promise<unknown>, done?: (r: unknown) => string) => Promise<void>;
}) {
  const url = stats?.lan_ip ? phoneUrl(stats.lan_ip, stats.http_port) : null;
  const qr = useMemo(() => (url ? qrPath(url) : null), [url]);
  const mdns = stats?.mdns ?? null;
  const enabled = stats?.mdns_enabled ?? false;
  const loopback = stats?.loopback_only ?? false;

  return (
    <SectionPanel
      title="Phone app"
      description="The phone app finds this server on your network by itself. If it doesn't, scan the code from the app's Connect screen."
    >
      <SettingRow
        label="Announce on the network"
        help="mDNS, as _gt7dl._tcp. Multicast doesn't leave a Docker bridge network: in a container it needs host networking."
      >
        <div className="flex flex-wrap items-center gap-3">
          <Toggle
            ariaLabel="Announce on the network"
            checked={enabled}
            disabled={!stats || busy !== null}
            onCheckedChange={(on) =>
              void run(
                "Announce on the network",
                () => api.admin.updateConfig({ values: { mdns: on } }),
                () => (on ? "Announcing on the network" : "No longer announcing"),
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
                    ? LOOPBACK_NOTE
                    : "not announced — no LAN address, or multicast refused (see Logs)"
                  : "off"}
            </span>
          )}
        </div>
      </SettingRow>
      <SettingRow label="Connect by code" help="The address of this server on your network." last>
        {url && qr ? (
          <div className="flex flex-wrap items-center gap-4">
            <svg
              role="img"
              aria-label={`QR code for ${url}`}
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
                Live stream: ws://{stats?.lan_ip}:{stats?.http_port}/ws/live
              </span>
            </div>
          </div>
        ) : (
          <span className="text-[12px] text-ink-faint">
            {!stats ? "Loading…" : loopback ? LOOPBACK_HELP : "This machine has no LAN address."}
          </span>
        )}
      </SettingRow>
    </SectionPanel>
  );
}
