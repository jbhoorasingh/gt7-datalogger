import { CalloutBanner, useEngineer } from "gt7-datalogger-frontend";
import type { CalloutCategory, VoiceCallout } from "gt7-datalogger-frontend";
import { Surface } from "../preview-shell";

// The on-screen caption for the last callout — what keeps Race Engineer useful
// when the browser cannot speak. It reads the engineer store's callout history
// (not what was spoken), so each cell seeds that store, the way the WebSocket
// does on a real lap.
//
// The banner is `absolute … bottom-3` inside DashView's `relative min-h-0
// flex-1`, i.e. it lies over the widget grid, so every cell gives it that
// positioned stage at roughly the height a dashboard pane has.
//
// Wording is verbatim from src/lib/calloutCatalog.ts, priorities from the
// backend's SPECS table: 90+ is critical and paints the banner red.

function seed(callout: Omit<VoiceCallout, "created_at_ms" | "expires_at_ms" | "ttl_ms" | "interrupt">) {
  useEngineer.setState({
    captions: true,
    history: [
      {
        created_at_ms: 0,
        expires_at_ms: 12_000,
        ttl_ms: 12_000,
        interrupt: callout.priority >= 90,
        ...callout,
      },
    ],
  });
}

function Stage() {
  return (
    <Surface className="p-0" width={760}>
      {/* Height as an inline style on purpose: the compiled Tailwind carries
          only the utilities src/ uses, and h-40 is not one of them. */}
      <div className="relative" style={{ height: 168 }}>
        <CalloutBanner />
      </div>
    </Surface>
  );
}

export function LapTime() {
  // The commonest callout of all: every completed lap, against the session best.
  seed({
    id: "c-lap-1063",
    event_type: "lap_time",
    category: "lap" as CalloutCategory,
    priority: 60,
    text: "Lap time, fifty-nine point zero. Two tenths slower.",
  });
  return <Stage />;
}

export function PersonalBest() {
  seed({
    id: "c-pace-1063",
    event_type: "personal_best",
    category: "pace" as CalloutCategory,
    priority: 70,
    text: "New personal best, fifty-eight point nine six. Six tenths faster.",
  });
  return <Stage />;
}

export function Coaching() {
  // Coach verbosity, after a lap off the best — the longest text the banner has
  // to hold, and the reason it wraps to two lines at max-w-2xl.
  seed({
    id: "c-coach-6",
    event_type: "corner_time_loss",
    category: "coaching" as CalloutCategory,
    priority: 50,
    text:
      "You lost three tenths in turn six. You braked eighteen meters earlier and "
      + "carried five kilometers per hour less at the apex.",
  });
  return <Stage />;
}

export function Critical() {
  // priority >= 90: red border and fill, and it interrupted whatever was being
  // said. Bathurst in the 911 GT3 R, where the tank is the whole race.
  seed({
    id: "c-fuel-940",
    event_type: "fuel_critical",
    category: "fuel" as CalloutCategory,
    priority: 95,
    text: "Fuel critical, one point two laps remaining.",
  });
  return <Stage />;
}
