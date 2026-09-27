import { StatusBar, useTelemetry } from "gt7-datalogger-frontend";
import { STATUS } from "../fixtures/app";
import { Surface } from "../preview-shell";

// The bar reads the telemetry store, so each cell puts the store in the state it
// is showing. StatusBar also calls api.status() on mount; with no server behind
// the card that rejects and is swallowed, leaving the seeded state standing.
function seed(patch: Parameters<typeof useTelemetry.setState>[0]) {
  useTelemetry.setState(patch);
}

function Bar({ view }: { view: "live" | "analysis" | "sessions" | "tracks" | "survey" | "admin" }) {
  return (
    <Surface className="p-0" width={860}>
      <StatusBar view={view} />
    </Surface>
  );
}

export function Recording() {
  seed({ status: { ...STATUS, recording: true, source: "udp", console_ip: "192.168.0.24" }, wsConnected: true });
  return <Bar view="live" />;
}

export function SimulatedSource() {
  seed({ status: { ...STATUS, recording: false, source: "sim" }, wsConnected: true });
  return <Bar view="analysis" />;
}

export function NoTelemetry() {
  // Server reachable, console silent — the readout goes amber. The commonest
  // real failure: wrong console IP, or UDP 33740 blocked by a firewall.
  seed({ status: { ...STATUS, connected: false, source: "udp", console_ip: "192.168.0.24" }, wsConnected: true });
  return <Bar view="sessions" />;
}

export function Disconnected() {
  seed({ status: { ...STATUS, connected: false }, wsConnected: false });
  return <Bar view="tracks" />;
}
